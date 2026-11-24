"""A release's expected migrations, derived from its pinned inputs and compared exactly.

**Why (r91, 23.11.2026).** A release kit's d2 stopped before any change because a line
copied from r89's kit required exactly «1 pending migration(s):» in d1's ``plan.txt``,
and r91 had two. r87 and r88 had the same line, re-pinned by hand each time
(``grep -qxF "N pending migration(s):"``). The gate was right to stop; what was wrong
was a count written by hand. The count and the identities (``app.name``, in order) are
data: they follow from the reviewed migration files in the payload and the reviewed
plan, so the generator derives them once and the kit compares against that data.

**Two modes, one data file.**

* ``derive`` (off-host, run by the kit generator as the release commit's own copy):
  reads git — every path under ``*/migrations/*`` that changes between ``--previous``
  (the commit production runs) and ``--target`` (the release commit) — and the
  reviewed local ``migration_plan`` output (``--reviewed-plan``, read against a
  database at production's schema). The added migration files and the plan's
  pending labels must be the same set; the plan gives the order. It writes the
  expected-migrations file (``FORMAT``), which the kit carries and pins by sha256.
* ``check-plan`` (anywhere: the generator, ``refresh-pins.sh``, or d1/d2 inside the
  release image with ``python -I`` and ``--network none``): parses a live
  ``manage.py migration_plan`` output strictly and compares it with the file. It
  passes only when the database is not ahead, the pending labels are exactly the
  expected list in the expected order, and the whole plan (its additive /
  consequential classification included) is the reviewed text.
* ``shell`` prints the kit's existing variables (``EXPECTED_MIGRATIONS``,
  ``MIGRATE_ORDER``, ``EXPECTED_MIGRATION_FILES``, ``PREDECESSORS``, …) from the file, so
  a generator never writes one by hand.

Zero, one and many migrations take the same path. Every disagreement is a refusal:
exit 3 and ``REFUSED: …`` on stderr, nothing written.

**What it never does.** It never runs ``migrate``, never reads a database and never
imports Django or the project: standard library only, so it runs under ``python -I``
in the release image as well as on the generator's machine. It does not judge whether
a plan is safe (the owner does; ``migration_plan`` classifies), and it does not
replace ``tools/release_fingerprint.py``: that helper vouches for a migration's
*contents* and what Django loads; this one for *which* migrations, how many and in
what order.

**Pinned to the release commit** (``derive``): it refuses to run unless its own bytes
are ``<target>``'s ``tools/release_migrations.py``, as the fingerprint helper does.
"""

from __future__ import annotations

import argparse
import hashlib
import re
import shutil
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

HELPER_PATH = "tools/release_migrations.py"
EXIT_REFUSED = 3
FORMAT = "juristid-release-migrations: 1"

#: The exact lines `app/core/management/commands/migration_plan.py` writes (a test
#: reads that command's source, so a wording change there fails here first).
NO_PENDING = "No pending migrations."
UNKNOWN_HEADER = "The database has applied migrations this code does not have:"
ALL_ADDITIVE = (
    "Every pending migration is additive, so the release now serving "
    "keeps working against the new schema while it is replaced."
)
CONSEQUENTIAL_TAIL = (
    " pending migration(s) remove or rewrite something. "
    "Take a backup first and decide whether the release now serving survives "
    "the new schema; if it does not, the deployment needs an announced "
    "maintenance window rather than a rolling replacement."
)

_SHA = re.compile(r"[0-9a-f]{40}")
_SHA256 = re.compile(r"[0-9a-f]{64}")
_LABEL = re.compile(r"([a-z_][a-z0-9_]*)\.([0-9]{4}_[a-z0-9_]+)")
_PATH = re.compile(r"app/([a-z_][a-z0-9_]*)/migrations/([0-9]{4}_[a-z0-9_]+)\.py")
_HEADER = re.compile(r"([1-9][0-9]*) pending migration\(s\):")
_ADDITIVE = re.compile(r"  (\S+)  \(additive\)")
_CONSEQUENTIAL = re.compile(r"  (\S+)")
_OPERATION = re.compile(r"      [A-Za-z][A-Za-z0-9_]*: \S.*")


class Refused(Exception):
    """The release's migrations disagree with what was reviewed: stop, write nothing."""


# ---------------------------------------------------------------------------------------
# The plan, parsed
# ---------------------------------------------------------------------------------------


@dataclass(frozen=True)
class Pending:
    label: str
    additive: bool
    operations: tuple[str, ...]


@dataclass(frozen=True)
class Plan:
    pending: tuple[Pending, ...]
    #: The normalised text: exactly the lines `migration_plan` wrote, ``\r`` and
    #: trailing blank lines removed. Equal plans have equal text.
    text: str

    @property
    def labels(self) -> tuple[str, ...]:
        return tuple(p.label for p in self.pending)

    @property
    def sha256(self) -> str:
        return hashlib.sha256((self.text + "\n").encode("utf-8")).hexdigest()


def _label(value: str) -> str:
    if not _LABEL.fullmatch(value):
        raise Refused(f"{value!r} is not an <app>.<NNNN_name> migration label")
    return value


def parse_plan(text: str) -> Plan:
    """Parse ``manage.py migration_plan`` output, refusing anything it does not write.

    Refuses: the database ahead of the code; a count that is not the number of entries;
    a summary that does not follow from the entries; a label twice; any other line
    (a warning, colour codes, a traceback, interleaved output).
    """
    if "\x1b" in text:
        raise Refused("the plan holds terminal colour codes (run it without a TTY)")
    lines = text.replace("\r\n", "\n").replace("\r", "\n").split("\n")
    while lines and lines[-1] == "":
        lines.pop()
    if not lines:
        raise Refused("the plan is empty")
    if lines[0] == UNKNOWN_HEADER:
        ahead = [line.strip() for line in lines[1:] if _LABEL.fullmatch(line.strip())]
        raise Refused(
            "the database has applied migrations this code does not have: " + " ".join(ahead)
        )
    if lines == [NO_PENDING]:
        return Plan(pending=(), text=NO_PENDING)
    header = _HEADER.fullmatch(lines[0])
    if header is None:
        raise Refused(f"the plan does not start with a count or {NO_PENDING!r}: {lines[0]!r}")
    count = int(header.group(1))
    pending: list[Pending] = []
    index = 1
    while index < len(lines) and lines[index] != "":
        line = lines[index]
        additive = _ADDITIVE.fullmatch(line)
        if additive:
            pending.append(Pending(_label(additive.group(1)), True, ()))
            index += 1
            continue
        consequential = _CONSEQUENTIAL.fullmatch(line)
        if consequential is None:
            raise Refused(f"unexpected plan line {index + 1}: {line!r}")
        index += 1
        operations: list[str] = []
        while index < len(lines) and _OPERATION.fullmatch(lines[index]):
            operations.append(lines[index].strip())
            index += 1
        if not operations:
            raise Refused(f"{consequential.group(1)} is neither additive nor names an operation")
        pending.append(Pending(_label(consequential.group(1)), False, tuple(operations)))
    if len(pending) != count:
        raise Refused(f"the plan says {count} pending migration(s) and lists {len(pending)}")
    labels = [p.label for p in pending]
    if len(set(labels)) != len(labels):
        raise Refused(f"the plan lists a migration twice: {' '.join(labels)}")
    rest = lines[index:]
    consequential_count = sum(1 for p in pending if not p.additive)
    summary = (
        ALL_ADDITIVE if consequential_count == 0 else f"{consequential_count}{CONSEQUENTIAL_TAIL}"
    )
    if rest != ["", summary]:
        raise Refused(f"the plan does not end with its summary line ({summary[:40]}...)")
    return Plan(pending=tuple(pending), text="\n".join(lines))


# ---------------------------------------------------------------------------------------
# The expected-migrations file
# ---------------------------------------------------------------------------------------


@dataclass(frozen=True)
class Migration:
    label: str
    path: str
    blob: str
    sha256: str

    @property
    def app(self) -> str:
        return self.label.split(".", 1)[0]

    @property
    def name(self) -> str:
        return self.label.split(".", 1)[1]


@dataclass(frozen=True)
class Expected:
    previous: str
    target: str
    migrations: tuple[Migration, ...]
    #: ``app:leaf`` at ``previous`` for each app with a new migration (an app new at
    #: ``target`` has none), in the order the apps first appear in the plan.
    predecessors: tuple[str, ...]
    plan: Plan

    def render(self) -> str:
        rows = [
            FORMAT,
            f"previous: {self.previous}",
            f"target: {self.target}",
            f"count: {len(self.migrations)}",
            *(f"migration: {m.label} {m.path} {m.blob} {m.sha256}" for m in self.migrations),
            *(f"predecessor: {p}" for p in self.predecessors),
            *(f"plan: {line}".rstrip() for line in self.plan.text.split("\n")),
            f"plan_sha256: {self.plan.sha256}",
        ]
        return "\n".join(rows) + "\n"


def _key(row: str, key: str) -> str:
    prefix = f"{key}:"
    if not (row == prefix or row.startswith(prefix + " ")):
        raise Refused(f"expected-migrations file: expected {key!r}, found {row[:60]!r}")
    return row[len(prefix) + 1 :] if row != prefix else ""


def parse_expected(text: str) -> Expected:
    """Read the file `Expected.render` writes, refusing any other shape or a file whose
    parts disagree with each other (a hand edit)."""
    if "\r" in text or not text.endswith("\n"):
        raise Refused("expected-migrations file: LF line endings and a final newline only")
    rows = text[:-1].split("\n")
    if not rows or rows[0] != FORMAT:
        raise Refused(f"expected-migrations file: the first line must be {FORMAT!r}")
    previous = _key(rows[1], "previous") if len(rows) > 1 else ""
    target = _key(rows[2], "target") if len(rows) > 2 else ""
    count_text = _key(rows[3], "count") if len(rows) > 3 else ""
    if not (_SHA.fullmatch(previous) and _SHA.fullmatch(target) and count_text.isdigit()):
        raise Refused("expected-migrations file: previous/target/count malformed")
    count = int(count_text)
    index = 4
    migrations: list[Migration] = []
    for row in rows[index : index + count]:
        parts = _key(row, "migration").split(" ")
        if len(parts) != 4:
            raise Refused(f"expected-migrations file: malformed {row!r}")
        label, path, blob, sha256 = parts
        match = _PATH.fullmatch(path)
        if (
            match is None
            or f"{match.group(1)}.{match.group(2)}" != _label(label)
            or not _SHA.fullmatch(blob)
            or not _SHA256.fullmatch(sha256)
        ):
            raise Refused(f"expected-migrations file: malformed {row!r}")
        migrations.append(Migration(label, path, blob, sha256))
    if len(migrations) != count:
        raise Refused(f"expected-migrations file: count {count}, {len(migrations)} migration rows")
    index += count
    predecessors: list[str] = []
    while index < len(rows) and rows[index].startswith("predecessor:"):
        value = _key(rows[index], "predecessor")
        if not re.fullmatch(r"[a-z_][a-z0-9_]*:[0-9]{4}_[a-z0-9_]+", value):
            raise Refused(f"expected-migrations file: malformed {rows[index]!r}")
        predecessors.append(value)
        index += 1
    plan_lines: list[str] = []
    while index < len(rows) and rows[index].startswith("plan:"):
        plan_lines.append(_key(rows[index], "plan"))
        index += 1
    if index != len(rows) - 1:
        raise Refused("expected-migrations file: unexpected rows after the plan")
    plan_sha = _key(rows[index], "plan_sha256")
    plan = parse_plan("\n".join(plan_lines))
    if plan.sha256 != plan_sha:
        raise Refused("expected-migrations file: plan_sha256 is not the plan's")
    if plan.labels != tuple(m.label for m in migrations):
        raise Refused("expected-migrations file: the plan's labels are not the migration rows")
    expected = Expected(previous, target, tuple(migrations), tuple(predecessors), plan)
    if expected.render() != text:
        raise Refused("expected-migrations file: not in canonical form")
    return expected


# ---------------------------------------------------------------------------------------
# derive: the release, read from git, against the reviewed plan
# ---------------------------------------------------------------------------------------


def _git(repo: Path, *args: str) -> bytes:
    git = shutil.which("git")
    if git is None:
        raise Refused("git is not available")
    # A fixed program; refs are checked 40-character shas, paths come from git itself
    # and are checked against `_PATH` before they are used again.
    done = subprocess.run([git, "-C", str(repo), *args], capture_output=True, check=False)  # noqa: S603
    if done.returncode != 0:
        raise Refused(f"git {' '.join(args)} failed: {done.stderr.decode(errors='replace')[:200]}")
    return done.stdout


def changed_migration_paths(repo: Path, previous: str, target: str) -> list[str]:
    """The migration files ``previous`` → ``target`` adds, in path order. Anything else
    under ``*/migrations/*`` (a changed, deleted, renamed or retyped file, an
    ``__init__.py``, a non-Python file, a path outside ``app/<app>/migrations/``) is
    refused: it needs a hand review this helper cannot give."""
    raw = _git(
        repo,
        "diff",
        "--name-status",
        "--no-renames",
        "-z",
        previous,
        target,
        "--",
        "*/migrations/*",
    ).decode("utf-8")
    fields = raw.split("\0")
    if fields and fields[-1] == "":
        fields.pop()
    if len(fields) % 2:
        raise Refused("could not read git diff --name-status -z")
    added: list[str] = []
    for status, path in zip(fields[0::2], fields[1::2], strict=True):
        if status != "A":
            raise Refused(f"{path} is {status} between previous and target, not added")
        if not _PATH.fullmatch(path):
            raise Refused(f"{path} is not an app/<app>/migrations/<NNNN_name>.py file")
        added.append(path)
    return added


def _leaf(repo: Path, ref: str, app: str) -> str | None:
    """The app's highest-numbered migration at ``ref``; None for an app without one."""
    listing = _git(repo, "ls-tree", "--name-only", ref, "--", f"app/{app}/migrations/").decode()
    names = sorted(
        Path(p).stem
        for p in listing.split("\n")
        if p.endswith(".py") and re.fullmatch(r"[0-9]{4}_[a-z0-9_]+", Path(p).stem)
    )
    return names[-1] if names else None


def derive_expected(
    *, repo: Path, previous: str, target: str, reviewed_plan: str, helper: Path | None
) -> Expected:
    """The expected migrations of ``previous`` → ``target``. ``helper`` is this file, which
    must be ``target``'s own copy (None only for tests of historic commits that predate it)."""
    for value in (previous, target):
        if not _SHA.fullmatch(value):
            raise Refused(f"a commit must be a full 40-character sha, not {value!r}")
        if _git(repo, "cat-file", "-t", value).decode().strip() != "commit":
            raise Refused(f"{value} is not a commit")
    if helper is not None:
        pinned = _git(repo, "show", f"{target}:{HELPER_PATH}").replace(b"\r\n", b"\n")
        if helper.read_bytes().replace(b"\r\n", b"\n") != pinned:
            raise Refused(f"this helper is not {HELPER_PATH} at {target}")
    plan = parse_plan(reviewed_plan)
    by_label: dict[str, str] = {}
    for path in changed_migration_paths(repo, previous, target):
        app, _, rest = path.removeprefix("app/").partition("/migrations/")
        by_label[f"{app}.{rest.removesuffix('.py')}"] = path
    in_payload, in_plan = set(by_label), set(plan.labels)
    if in_payload != in_plan:
        missing = sorted(in_payload - in_plan)
        extra = sorted(in_plan - in_payload)
        raise Refused(
            "the reviewed plan and the payload's migration files disagree: "
            f"added but not pending [{' '.join(missing)}]; "
            f"pending but not added [{' '.join(extra)}]"
        )
    seen: dict[str, str] = {}
    for label in plan.labels:
        app, name = label.split(".", 1)
        if app in seen and seen[app] >= name:
            raise Refused(f"the plan orders {app}.{seen[app]} after {label}")
        seen[app] = name
    migrations: list[Migration] = []
    for label in plan.labels:
        path = by_label[label]
        entry = _git(repo, "ls-tree", target, "--", path).decode()
        if not entry.startswith("100644 blob "):
            raise Refused(f"{path} is not a regular file at {target}: {entry.strip()!r}")
        blob = entry.split()[2]
        data = _git(repo, "cat-file", "blob", blob)
        migrations.append(Migration(label, path, blob, hashlib.sha256(data).hexdigest()))
    predecessors: list[str] = []
    for app in dict.fromkeys(m.app for m in migrations):
        leaf = _leaf(repo, previous, app)
        if leaf is not None:
            first = next(m.name for m in migrations if m.app == app)
            if leaf >= first:
                raise Refused(f"{app}.{first} does not come after {app}.{leaf} at previous")
            predecessors.append(f"{app}:{leaf}")
    return Expected(previous, target, tuple(migrations), tuple(predecessors), plan)


# ---------------------------------------------------------------------------------------
# check-plan and shell
# ---------------------------------------------------------------------------------------


def check_plan(expected: Expected, live: str) -> Plan:
    """Compare a live ``migration_plan`` output with the expected file; refuse any
    difference. Labels first (the clearer message), then the whole text."""
    plan = parse_plan(live)
    want = expected.plan.labels
    if plan.labels != want:
        got = plan.labels
        missing = [label for label in want if label not in got]
        extra = [label for label in got if label not in want]
        detail = (
            f"missing [{' '.join(missing)}] extra [{' '.join(extra)}]"
            if missing or extra
            else "the same migrations in another order"
        )
        raise Refused(
            f"migration_plan lists {len(got)} pending [{' '.join(got)}], "
            f"expected {len(want)} [{' '.join(want)}]: {detail}"
        )
    if plan.text != expected.plan.text:
        raise Refused(
            "migration_plan names the expected migrations but classifies them differently "
            "from the reviewed plan (additive / operations)"
        )
    return plan


def shell_values(expected: Expected) -> list[tuple[str, str]]:
    """The kit's variables, in the formats the kits already use. Every value is built
    from checked labels and hex, so it needs no quoting beyond double quotes."""
    ms = expected.migrations
    rows = [
        ("EXPECTED_MIGRATION_COUNT", str(len(ms))),
        (
            "EXPECTED_PLAN_HEADER",
            f"{len(ms)} pending migration(s):" if ms else NO_PENDING,
        ),
        ("EXPECTED_MIGRATIONS", " ".join(sorted((m.label for m in ms), key=str.encode))),
        ("EXPECTED_MIGRATIONS_IN_ORDER", " ".join(m.label for m in ms)),
        ("MIGRATE_ORDER", " ".join(f"{m.app}:{m.name}" for m in ms)),
        ("PREDECESSORS", " ".join(expected.predecessors)),
        ("EXPECTED_MIGRATION_FILES", " ".join(sorted((m.path for m in ms), key=str.encode))),
        ("EXPECTED_MIGRATION_ROWS_BEFORE", "|".join("0" for _ in ms)),
        ("EXPECTED_MIGRATION_ROWS_AFTER", "|".join("1" for _ in ms)),
        ("EXPECTED_PLAN_SHA256", expected.plan.sha256),
    ]
    # tools/release_fingerprint.py --check-loaded vouches for an app's only leaf, so
    # only each app's last new migration can be passed to it.
    last: dict[str, Migration] = {m.app: m for m in ms}
    rows.append(
        ("CHECK_LOADED_MIGRATIONS", " ".join(f"{m.label}={m.sha256}" for m in last.values()))
    )
    for name, value in rows:
        if re.search(r'["$`\\\n]', value):
            raise Refused(f"{name} would need shell quoting")
    return rows


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="A release's expected migrations.")
    sub = parser.add_subparsers(dest="mode", required=True)
    derive = sub.add_parser("derive", help="off-host: write the expected-migrations file")
    derive.add_argument("--repo", type=Path, default=Path("."))
    derive.add_argument("--previous", required=True, help="the commit production runs")
    derive.add_argument("--target", required=True, help="the release commit")
    derive.add_argument("--reviewed-plan", type=Path, required=True)
    derive.add_argument("--out", type=Path, required=True)
    check = sub.add_parser("check-plan", help="compare a live migration_plan with the file")
    check.add_argument("--expected", type=Path, required=True)
    check.add_argument("--plan", required=True, help="a file, or - for stdin")
    shell = sub.add_parser("shell", help="print the kit's variables from the file")
    shell.add_argument("--expected", type=Path, required=True)
    args = parser.parse_args(argv)
    try:
        if args.mode == "derive":
            if args.out.exists():
                raise Refused(f"{args.out} exists; write each kit's file once")
            expected = derive_expected(
                repo=args.repo,
                previous=args.previous,
                target=args.target,
                reviewed_plan=args.reviewed_plan.read_text(encoding="utf-8"),
                helper=Path(__file__),
            )
            text = expected.render()
            parse_expected(text)  # the file this writes must read back
            args.out.write_bytes(text.encode("utf-8"))
            sha = hashlib.sha256(text.encode("utf-8")).hexdigest()
            sys.stdout.write(
                f"expected migrations: {len(expected.migrations)} "
                f"[{' '.join(expected.plan.labels)}] file sha256 {sha}\n"
            )
        elif args.mode == "check-plan":
            expected = parse_expected(args.expected.read_bytes().decode("utf-8"))
            live = (
                sys.stdin.read()
                if args.plan == "-"
                else Path(args.plan).read_bytes().decode("utf-8")
            )
            plan = check_plan(expected, live)
            sys.stdout.write(
                f"MIGRATION_PLAN_MATCH count={len(plan.pending)} "
                f"[{' '.join(plan.labels)}] plan_sha256={plan.sha256}\n"
            )
        else:
            expected = parse_expected(args.expected.read_bytes().decode("utf-8"))
            sys.stdout.write("".join(f'{k}="{v}"\n' for k, v in shell_values(expected)))
    except (Refused, OSError, UnicodeDecodeError) as error:
        # ASCII, whatever the console's code page (a Windows generator machine).
        message = str(error).encode("ascii", "backslashreplace").decode("ascii")
        sys.stderr.write(f"release_migrations: REFUSED: {message}\n")
        return EXIT_REFUSED
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
