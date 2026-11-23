"""The fingerprint differences a migration release may show, from its reviewed migration.

A release kit generator runs it from the reviewed release commit itself:

    git -C <repo> show <TARGET>:tools/release_fingerprint.py > rf.py
    python rf.py --repo <repo> --ref <TARGET> --app legacy_import \\
        --migration 0033_lovable_owner_alias --previous-leaf 0032_postipoiss_letters

and gets the lines, C-sorted, ready for the kit's ``FP_EXPECTED_LINES``.

**Why (r89, 23.11.2026).** A release kit's d2 compares `recovery_fingerprint` before and
after the swap, and its ``fingerprint_verdict`` stops unless the differences are exactly
``FP_EXPECTED_LINES``. r89 created one new, empty table; the fingerprint reported
``canonical_counts.legacy_import.LovableOwnerAlias: None -> 0`` beside the two
migration-leaf lines, but the kit's hand-written list held only the leaves, so d2 stopped
after a correct swap. r84 had listed its new models by hand; r89's generator forgot. The
verdict was right to stop.

**What this does, and what it never does.** It writes the expected list from the reviewed
migration file itself: the two leaf lines of a one-migration release, and ``None -> 0`` for
each model that file's ``CreateModel`` operations create — nothing else. It never touches
the verdict, which stays an exact comparison: a new model that already holds rows
(``None -> 1``), a model the migration does not create, any other count that moved, a
missing expected line or any other leaf still stops the release. Every line it emits is
checked against those two exact shapes (`check_lines`).

**Unsupported migrations fail before deployment.** The list is only right for a migration
that changes the schema and creates empty tables. A migration with any operation outside
`SUPPORTED_OPERATIONS` — a data migration (``RunPython``, ``RunSQL``), a deleted, renamed
or retabled model, a removed or renamed field, ``SeparateDatabaseAndState`` — or one this
reader cannot parse (no ``operations`` list, an unnamed ``CreateModel``, a model created
twice) is refused here, when the kit is generated, never accepted silently to stop after
the swap.

**Pinned to the reviewed commit.** With ``--ref``, the migration is read from that commit
(``git show <ref>:app/<app>/migrations/<name>.py``), and the helper refuses to run unless
its own bytes are the commit's ``tools/release_fingerprint.py``: the code CI tested
(tests/test_release_fingerprint.py) and the blob the kit pins come from one commit.
"""

from __future__ import annotations

import argparse
import re
import shutil
import subprocess
import sys
from pathlib import Path

#: Operations that change only the schema and create nothing but empty tables, so a
#: fingerprint can move only by the leaves and «None -> 0» per created model.
SUPPORTED_OPERATIONS = frozenset(
    {
        "CreateModel",
        "AddField",
        "AlterField",
        "AddIndex",
        "RemoveIndex",
        "AddConstraint",
        "RemoveConstraint",
        "AlterModelOptions",
        "AlterModelManagers",
    }
)
#: Words that mean data or raw SQL wherever they appear, however they were imported.
_DATA_WORDS = re.compile(r"\b(RunPython|RunSQL|SeparateDatabaseAndState)\b")
_CREATE = re.compile(r"migrations\.CreateModel\(\s*name\s*=\s*['\"]([A-Za-z_][A-Za-z0-9_]*)['\"]")
_OPERATION = re.compile(r"migrations\.([A-Z][A-Za-z]*)\(")
_APP = re.compile(r"[a-z_][a-z0-9_]*")
_MIGRATION = re.compile(r"[0-9]{4}_[a-z0-9_]+")
_SHA = re.compile(r"[0-9a-f]{40}")
#: The only two shapes an expected line may take.
_LINE = re.compile(
    r"canonical_counts\.[a-z_][a-z0-9_]*\.[A-Za-z_][A-Za-z0-9_]*: None -> 0"
    r"|migration_leaves: (newly present|no longer present): "
    r"[a-z_][a-z0-9_]*\.[0-9]{4}_[a-z0-9_]+"
)
HELPER_PATH = "tools/release_fingerprint.py"


def check_supported(text: str) -> list[str]:
    """The migration's operations, in file order, or refuse one this cannot vouch for."""
    if "class Migration(migrations.Migration)" not in text or "operations = [" not in text:
        raise ValueError("not a migration file with an operations list")
    data = sorted(set(_DATA_WORDS.findall(text)))
    if data:
        raise ValueError(f"unsupported: a data or raw SQL operation ({', '.join(data)})")
    operations = _OPERATION.findall(text)
    unsupported = sorted(set(operations) - SUPPORTED_OPERATIONS)
    if unsupported:
        raise ValueError(f"unsupported operation(s): {', '.join(unsupported)}")
    return operations


def created_models(text: str) -> list[str]:
    """The model names a migration's ``CreateModel`` operations create, in file order.

    Refuses one it cannot name (it would otherwise be left out silently, and the release
    would stop after the swap again) and a model created twice.
    """
    names = _CREATE.findall(text)
    creates = [op for op in _OPERATION.findall(text) if op == "CreateModel"]
    if len(names) != len(creates):
        raise ValueError(
            f"{len(creates)} CreateModel operation(s) but {len(names)} readable name(s)"
        )
    if len(set(names)) != len(names):
        raise ValueError(f"a model is created twice: {names}")
    return names


def new_model_lines(app: str, text: str) -> list[str]:
    """``canonical_counts.<app>.<Model>: None -> 0`` for each model the migration creates.

    That is how ``recovery_fingerprint --compare`` words a model absent before and empty
    after.
    """
    if not _APP.fullmatch(app):
        raise ValueError(f"not an app label: {app!r}")
    return [f"canonical_counts.{app}.{name}: None -> 0" for name in created_models(text)]


def leaf_lines(app: str, migration: str, previous_leaf: str) -> list[str]:
    """The two migration-leaf lines of a release that adds one migration to ``app``."""
    if not _APP.fullmatch(app):
        raise ValueError(f"not an app label: {app!r}")
    for value in (migration, previous_leaf):
        if not _MIGRATION.fullmatch(value):
            raise ValueError(f"not a migration name: {value!r}")
    if migration == previous_leaf:
        raise ValueError("the new migration cannot be its own previous leaf")
    return [
        f"migration_leaves: newly present: {app}.{migration}",
        f"migration_leaves: no longer present: {app}.{previous_leaf}",
    ]


def check_lines(lines: list[str]) -> list[str]:
    """Refuse any line that is not one of the two exact shapes, and any duplicate."""
    for line in lines:
        if not _LINE.fullmatch(line):
            raise ValueError(f"not an allowed expected line: {line!r}")
    if len(set(lines)) != len(lines):
        raise ValueError("a line is listed twice")
    return lines


def expected_lines(*, app: str, migration: str, previous_leaf: str, text: str) -> list[str]:
    """Every line the release's fingerprint must show, C-sorted (``LC_ALL=C sort``)."""
    check_supported(text)
    lines = leaf_lines(app, migration, previous_leaf) + new_model_lines(app, text)
    return sorted(check_lines(lines), key=lambda s: s.encode("utf-8"))


def _git_show(repo: Path, ref: str, path: str) -> bytes:
    git = shutil.which("git")
    if git is None:
        raise ValueError("git is not available")
    # A fixed program and arguments: the ref is a checked 40-character sha, the path built
    # from checked labels.
    done = subprocess.run(  # noqa: S603
        [git, "-C", str(repo), "show", f"{ref}:{path}"], capture_output=True, check=False
    )
    if done.returncode != 0:
        raise ValueError(f"{path} is not in commit {ref}")
    return done.stdout


def pinned_migration(*, repo: Path, ref: str, app: str, migration: str, helper: Path) -> str:
    """The migration's text at ``ref``, after proving ``helper`` (this file, as run) is
    byte-identical to ``ref``'s own copy: the helper and the migration from one commit."""
    if not _SHA.fullmatch(ref):
        raise ValueError(f"--ref must be a full 40-character commit, not {ref!r}")
    if not (_APP.fullmatch(app) and _MIGRATION.fullmatch(migration)):
        raise ValueError(f"not a migration: {app!r} {migration!r}")
    pinned = _git_show(repo, ref, HELPER_PATH).replace(b"\r\n", b"\n")
    if helper.read_bytes().replace(b"\r\n", b"\n") != pinned:
        raise ValueError(f"this helper is not {HELPER_PATH} at {ref}")
    return _git_show(repo, ref, f"app/{app}/migrations/{migration}.py").decode("utf-8")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="A release kit's expected fingerprint lines.")
    parser.add_argument("--app", required=True)
    parser.add_argument("--migration", required=True, help="e.g. 0033_lovable_owner_alias")
    parser.add_argument("--previous-leaf", required=True, help="e.g. 0032_postipoiss_letters")
    source = parser.add_mutually_exclusive_group(required=True)
    source.add_argument("--ref", help="the reviewed release commit (full sha); needs --repo")
    source.add_argument("--migration-file", type=Path, help="a local file (tests, review)")
    parser.add_argument("--repo", type=Path, default=Path("."))
    args = parser.parse_args(argv)
    try:
        if args.ref:
            text = pinned_migration(
                repo=args.repo,
                ref=args.ref,
                app=args.app,
                migration=args.migration,
                helper=Path(__file__),
            )
        else:
            if args.migration_file.stem != args.migration:
                raise ValueError(
                    f"--migration-file is {args.migration_file.name}, not {args.migration}.py"
                )
            text = args.migration_file.read_text(encoding="utf-8")
        lines = expected_lines(
            app=args.app, migration=args.migration, previous_leaf=args.previous_leaf, text=text
        )
    except ValueError as error:
        sys.stderr.write(f"release_fingerprint: REFUSED: {error}\n")
        return 2
    sys.stdout.write("\n".join(lines) + "\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
