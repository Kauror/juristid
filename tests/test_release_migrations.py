"""`tools.release_migrations`: a release kit's expected migrations, as data (r91 finding).

No database, no network. r91's d2 stopped because a line copied from r89's kit required
exactly «1 pending migration(s):» and r91 had two. These tests prove the count and the
identities come from the release's pinned inputs (git and the reviewed plan), that zero,
one and many migrations take the same path, and that every disagreement — a missing,
extra or reordered migration, a database ahead of the code, a reclassified plan, a
hand-edited file — is refused (exit 3).

The plan texts under ``tests/fixtures/release_migrations/`` are real recorded
``migration_plan`` outputs from release kits (migration labels and the command's own
wording only): r89 and r91 read locally at production's schema, r87 likewise (two
additive), r41's and r46's as the production host printed them in d1.
"""

from __future__ import annotations

import hashlib
import io
import shutil
import subprocess
import sys
from pathlib import Path

import pytest
from django.core.management import call_command

from app.core import deployment
from tools import release_migrations
from tools.release_migrations import (
    EXIT_REFUSED,
    Expected,
    Refused,
    check_plan,
    derive_expected,
    main,
    parse_expected,
    parse_plan,
    shell_values,
)

ROOT = Path(__file__).resolve().parent.parent
FIXTURES = Path(__file__).resolve().parent / "fixtures" / "release_migrations"
HELPER = Path(release_migrations.__file__)

R89 = (FIXTURES / "r89-plan.txt").read_text(encoding="utf-8")
R91 = (FIXTURES / "r91-plan.txt").read_text(encoding="utf-8")
R87 = (FIXTURES / "r87-plan.txt").read_text(encoding="utf-8")
R41_HOST = (FIXTURES / "r41-host-plan.txt").read_text(encoding="utf-8")
NONE_HOST = (FIXTURES / "r46-host-no-pending.txt").read_text(encoding="utf-8")

R91_LABELS = (
    "intelligence.0005_estimated_commencement_title",
    "register_watch.0008_agenda_ai_attempts",
)


# ---------------------------------------------------------------------------------------
# The plan grammar is the command's own output
# ---------------------------------------------------------------------------------------


def _command_output(monkeypatch, pending, unknown=()) -> str:
    """What `manage.py migration_plan` prints for a given state (its real code, no DB)."""
    state = deployment.MigrationState(
        pending=tuple(
            deployment.PlannedMigration(app_label=a, name=n, consequential=c) for a, n, c in pending
        ),
        unknown=tuple(unknown),
        leaves=(),
    )
    monkeypatch.setattr(deployment, "migration_state", lambda: state)
    out = io.StringIO()
    call_command("migration_plan", stdout=out)
    return out.getvalue()


def test_the_parser_reads_what_the_command_writes(monkeypatch):
    zero = _command_output(monkeypatch, [])
    assert parse_plan(zero).labels == ()
    one = _command_output(monkeypatch, [("legacy_import", "0033_lovable_owner_alias", {})])
    assert parse_plan(one).labels == ("legacy_import.0033_lovable_owner_alias",)
    many = _command_output(
        monkeypatch,
        [
            ("intelligence", "0005_estimated_commencement_title", {"RunPython": "arbitrary code"}),
            ("register_watch", "0008_agenda_ai_attempts", {}),
            ("matters", "0052_x", {"RemoveField": "drops a column", "AlterField": "rewrites"}),
        ],
    )
    plan = parse_plan(many)
    assert plan.labels == (*R91_LABELS, "matters.0052_x")
    assert [p.additive for p in plan.pending] == [False, True, False]
    assert plan.pending[2].operations == ("AlterField: rewrites", "RemoveField: drops a column")


def test_the_command_reporting_a_database_ahead_is_refused(monkeypatch):
    ahead = _command_output(monkeypatch, [], unknown=["matters.0099_future"])
    with pytest.raises(Refused, match=r"does not have: matters\.0099_future"):
        parse_plan(ahead)
    both = _command_output(monkeypatch, [("x", "0002_y", {})], unknown=["matters.0099_future"])
    with pytest.raises(Refused, match="does not have"):
        parse_plan(both)


@pytest.mark.parametrize(
    ("text", "labels"),
    [
        (NONE_HOST, ()),
        (R89, ("legacy_import.0033_lovable_owner_alias",)),
        (R91, R91_LABELS),
        (
            R87,
            ("audit.0043_procedural_link_removed_event", "matters.0051_procedural_link_removable"),
        ),
        (
            R41_HOST,
            (
                "legacy_import.0020_lovable_observation",
                "notifications.0003_commencement_reminder_personal_state",
            ),
        ),
    ],
    ids=["none-host", "r89", "r91", "r87", "r41-host"],
)
def test_recorded_plans_parse_to_their_labels(text, labels):
    plan = parse_plan(text)
    assert plan.labels == labels
    # CRLF (a Windows-side capture) and trailing blank lines do not change the plan.
    assert parse_plan(text.replace("\n", "\r\n") + "\r\n\n") == plan


def test_r91_keeps_its_classification():
    plan = parse_plan(R91)
    assert plan.pending[0].operations == (
        "RunPython: arbitrary code with the whole database in reach",
    )
    assert plan.pending[1].additive


@pytest.mark.parametrize(
    ("text", "message"),
    [
        ("", "empty"),
        (
            "2 pending migration(s):\n  a.0001_x  (additive)\n\n" + release_migrations.ALL_ADDITIVE,
            "says 2 pending migration",
        ),
        (R89.replace("1 pending", "2 pending"), "says 2 pending"),
        (R89 + "Traceback (most recent call last):\n", "summary"),
        ("WARNING something\n" + R89, "does not start"),
        (R89.replace("  legacy_import", "\x1b[33m  legacy_import"), "colour"),
        (R91.replace("1 pending migration(s) remove", "2 pending migration(s) remove"), "summary"),
        (R89.replace("  (additive)", ""), "neither additive"),
        (
            "1 pending migration(s):\n  Bad.Label  (additive)\n\n"
            + release_migrations.ALL_ADDITIVE,
            "not an <app>",
        ),
        (
            "2 pending migration(s):\n  a.0001_x  (additive)\n  a.0001_x  (additive)\n\n"
            + release_migrations.ALL_ADDITIVE,
            "twice",
        ),
    ],
)
def test_anything_else_is_refused(text, message):
    with pytest.raises(Refused, match=message):
        parse_plan(text)


# ---------------------------------------------------------------------------------------
# check-plan: the live plan against the expected file
# ---------------------------------------------------------------------------------------


def _expected(plan_text: str) -> Expected:
    plan = parse_plan(plan_text)
    migrations = tuple(
        release_migrations.Migration(
            label=label,
            path=f"app/{label.split('.')[0]}/migrations/{label.split('.')[1]}.py",
            blob="1" * 40,
            sha256="2" * 64,
        )
        for label in plan.labels
    )
    return Expected("a" * 40, "b" * 40, migrations, (), plan)


@pytest.mark.parametrize("text", [NONE_HOST, R89, R91, R87], ids=["zero", "r89", "r91", "r87"])
def test_the_reviewed_plan_matches_itself(text):
    expected = parse_expected(_expected(text).render())
    assert check_plan(expected, text).labels == parse_plan(text).labels


def test_r91s_two_migrations_against_r89s_one_is_refused():
    """The r91 d2 stop, as data: the r89-shaped expectation does not accept r91's plan."""
    with pytest.raises(Refused, match=r"lists 2 pending .* expected 1 .* missing \[legacy_import"):
        check_plan(_expected(R89), R91)


def test_a_missing_migration_is_refused():
    one_of_two = R91.replace("2 pending", "1 pending").replace(
        "  register_watch.0008_agenda_ai_attempts  (additive)\n", ""
    )
    with pytest.raises(Refused, match=r"missing \[register_watch\.0008"):
        check_plan(_expected(R91), one_of_two)


def test_an_extra_migration_is_refused():
    extra = R89.replace("1 pending", "2 pending").replace(
        "(additive)\n", "(additive)\n  matters.0052_unreviewed  (additive)\n", 1
    )
    with pytest.raises(Refused, match=r"extra \[matters\.0052_unreviewed\]"):
        check_plan(_expected(R89), extra)


def test_a_reordered_plan_is_refused():
    swapped = (
        R87.replace("audit.0043_procedural_link_removed_event", "TMP")
        .replace(
            "matters.0051_procedural_link_removable", "audit.0043_procedural_link_removed_event"
        )
        .replace("TMP", "matters.0051_procedural_link_removable")
    )
    with pytest.raises(Refused, match="another order"):
        check_plan(_expected(R87), swapped)


def test_pending_where_none_was_expected_is_refused():
    with pytest.raises(Refused, match="expected 0"):
        check_plan(_expected(NONE_HOST), R89)


def test_nothing_pending_where_one_was_expected_is_refused():
    with pytest.raises(Refused, match=r"lists 0 pending .* expected 1"):
        check_plan(_expected(R89), NONE_HOST)


def test_a_reclassified_migration_is_refused():
    reclassified = R89.replace("  (additive)", "\n      RunSQL: raw SQL").replace(
        release_migrations.ALL_ADDITIVE, "1" + release_migrations.CONSEQUENTIAL_TAIL
    )
    assert parse_plan(reclassified).labels == parse_plan(R89).labels
    with pytest.raises(Refused, match="classifies them differently"):
        check_plan(_expected(R89), reclassified)


# ---------------------------------------------------------------------------------------
# The expected-migrations file
# ---------------------------------------------------------------------------------------


def test_the_file_round_trips_and_refuses_hand_edits():
    text = _expected(R91).render()
    assert parse_expected(text).render() == text
    assert "count: 2\n" in text
    edits = [
        text.replace("count: 2", "count: 1"),
        text.replace("count: 2", "count: 3"),
        text.replace("plan: 2 pending", "plan: 1 pending"),
        text.replace("plan_sha256: ", "plan_sha256: 0"),
        text.replace("migration: intelligence", "migration: matters"),
        text.replace("\n", "\r\n"),
        text.rstrip("\n"),
        text + "extra: row\n",
    ]
    for edited in edits:
        with pytest.raises(Refused):
            parse_expected(edited)


def test_rows_in_another_order_than_the_plan_are_refused():
    exp = _expected(R91)
    swapped = Expected(exp.previous, exp.target, exp.migrations[::-1], (), exp.plan)
    with pytest.raises(Refused, match="labels are not the migration rows"):
        parse_expected(swapped.render())


def test_shell_values_have_the_kits_formats():
    values = dict(shell_values(_expected(R91)))
    assert values["EXPECTED_MIGRATION_COUNT"] == "2"
    assert values["EXPECTED_PLAN_HEADER"] == "2 pending migration(s):"
    assert values["EXPECTED_MIGRATIONS"] == " ".join(R91_LABELS)
    assert values["MIGRATE_ORDER"] == (
        "intelligence:0005_estimated_commencement_title register_watch:0008_agenda_ai_attempts"
    )
    assert values["EXPECTED_MIGRATION_ROWS_AFTER"] == "1|1"
    assert values["EXPECTED_MIGRATION_ROWS_BEFORE"] == "0|0"
    zero = dict(shell_values(_expected(NONE_HOST)))
    assert zero["EXPECTED_PLAN_HEADER"] == "No pending migrations."
    assert zero["EXPECTED_MIGRATION_COUNT"] == "0"
    assert zero["EXPECTED_MIGRATIONS"] == zero["MIGRATE_ORDER"] == ""
    assert zero["CHECK_LOADED_MIGRATIONS"] == ""


def test_check_loaded_gets_only_each_apps_leaf():
    """`release_fingerprint --check-loaded` vouches for an app's only leaf: two new
    migrations in one app hand it the later one."""
    text = (
        "2 pending migration(s):\n  x.0002_a  (additive)\n  x.0003_b  (additive)\n\n"
        + release_migrations.ALL_ADDITIVE
    )
    values = dict(shell_values(_expected(text)))
    assert values["CHECK_LOADED_MIGRATIONS"] == "x.0003_b=" + "2" * 64
    assert values["MIGRATE_ORDER"] == "x:0002_a x:0003_b"


# ---------------------------------------------------------------------------------------
# derive: git + the reviewed plan
# ---------------------------------------------------------------------------------------


def _plan(*labels: str) -> str:
    if not labels:
        return "No pending migrations.\n"
    body = "".join(f"  {label}  (additive)\n" for label in labels)
    return f"{len(labels)} pending migration(s):\n{body}\n{release_migrations.ALL_ADDITIVE}\n"


MIGRATION = (
    "from django.db import migrations\n\n\nclass Migration(migrations.Migration):\n    pass\n"
)


class Repo:
    """A throwaway repository: the helper, apps x and y with one migration each."""

    def __init__(self, root: Path) -> None:
        self.root = root
        self.git_exe = shutil.which("git")
        if self.git_exe is None:
            pytest.skip("git is not available")
        self.write("tools/release_migrations.py", HELPER.read_text(encoding="utf-8"))
        for app in ("x", "y"):
            self.write(f"app/{app}/migrations/__init__.py", "")
            self.write(f"app/{app}/migrations/0001_initial.py", MIGRATION)
        self.git("init", "-q")
        self.previous = self.commit()

    def write(self, path: str, text: str) -> None:
        target = self.root / path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(text, encoding="utf-8", newline="\n")

    def git(self, *args: str) -> str:
        return subprocess.run(  # noqa: S603 - git, a temporary repository, fixed arguments
            [str(self.git_exe), "-C", str(self.root), "-c", "core.autocrlf=false", *args],
            check=True,
            capture_output=True,
            text=True,
        ).stdout.strip()

    def commit(self) -> str:
        self.git("add", "-A")
        self.git("-c", "user.name=t", "-c", "user.email=t@example.invalid", "commit", "-qm", "c")
        return self.git("rev-parse", "HEAD")

    def derive(self, target: str, plan: str, helper: Path | None = HELPER) -> Expected:
        return derive_expected(
            repo=self.root, previous=self.previous, target=target, reviewed_plan=plan, helper=helper
        )


@pytest.fixture
def repo(tmp_path):
    return Repo(tmp_path / "repo")


def test_derive_zero(repo):
    repo.write("app/x/views.py", "# code only\n")
    expected = repo.derive(repo.commit(), _plan())
    assert expected.migrations == ()
    assert dict(shell_values(expected))["EXPECTED_PLAN_HEADER"] == "No pending migrations."


def test_derive_one_records_blob_sha256_and_predecessor(repo):
    repo.write("app/x/migrations/0002_b.py", MIGRATION + "# b\n")
    target = repo.commit()
    expected = repo.derive(target, _plan("x.0002_b"))
    (m,) = expected.migrations
    assert m.blob == repo.git("rev-parse", f"{target}:app/x/migrations/0002_b.py")
    assert m.sha256 == hashlib.sha256((MIGRATION + "# b\n").encode()).hexdigest()
    assert expected.predecessors == ("x:0001_initial",)


def test_derive_many_takes_the_plans_order(repo):
    repo.write("app/y/migrations/0002_b.py", MIGRATION + "# y\n")
    repo.write("app/x/migrations/0002_b.py", MIGRATION + "# x\n")
    repo.write("app/x/migrations/0003_c.py", MIGRATION + "# x3\n")
    repo.write("app/z/migrations/0001_initial.py", MIGRATION + "# new app\n")
    target = repo.commit()
    plan = _plan("y.0002_b", "x.0002_b", "z.0001_initial", "x.0003_c")
    values = dict(shell_values(repo.derive(target, plan)))
    assert values["MIGRATE_ORDER"] == "y:0002_b x:0002_b z:0001_initial x:0003_c"
    assert values["EXPECTED_MIGRATIONS"] == "x.0002_b x.0003_c y.0002_b z.0001_initial"
    assert values["PREDECESSORS"] == "y:0001_initial x:0001_initial"  # z is new
    assert values["EXPECTED_MIGRATION_FILES"].split() == sorted(
        values["EXPECTED_MIGRATION_FILES"].split()
    )


def test_derive_refuses_a_plan_that_disagrees_with_the_payload(repo):
    repo.write("app/x/migrations/0002_b.py", MIGRATION)
    target = repo.commit()
    with pytest.raises(Refused, match=r"added but not pending \[x\.0002_b\]"):
        repo.derive(target, _plan())
    with pytest.raises(Refused, match=r"pending but not added \[y\.0002_c\]"):
        repo.derive(target, _plan("x.0002_b", "y.0002_c"))


def test_derive_refuses_a_framework_migration_the_release_does_not_add(repo):
    """Django's own migrations (after a dependency upgrade) are pending without a file in
    app/: refused, a hand path (documented in the helper)."""
    repo.write("app/x/views.py", "# code only\n")
    with pytest.raises(Refused, match=r"pending but not added \[auth\.0013_user_first_name"):
        repo.derive(repo.commit(), _plan("auth.0013_user_first_name_max_length"))


def test_derive_refuses_a_plan_out_of_its_apps_order(repo):
    repo.write("app/x/migrations/0002_b.py", MIGRATION)
    repo.write("app/x/migrations/0003_c.py", MIGRATION + "#\n")
    target = repo.commit()
    with pytest.raises(Refused, match=r"orders x\.0003_c after x\.0002_b"):
        repo.derive(target, _plan("x.0003_c", "x.0002_b"))


@pytest.mark.parametrize(
    ("change", "message"),
    [
        (lambda r: r.write("app/x/migrations/0001_initial.py", MIGRATION + "#\n"), "is M"),
        (lambda r: (r.root / "app/x/migrations/0001_initial.py").unlink(), "is D"),
        (lambda r: r.write("app/x/migrations/__init__.py", "#\n"), "is M"),
        (lambda r: r.write("app/x/migrations/README.md", "x\n"), "not an app/"),
        (lambda r: r.write("other/migrations/0001_a.py", "x\n"), "not an app/"),
    ],
    ids=["changed", "deleted", "init", "non-python", "outside-app"],
)
def test_derive_refuses_anything_but_added_migration_files(repo, change, message):
    change(repo)
    with pytest.raises(Refused, match=message):
        repo.derive(repo.commit(), _plan())


def test_derive_refuses_a_helper_that_is_not_the_targets_copy(repo, tmp_path):
    target = repo.previous
    other = tmp_path / "release_migrations.py"
    other.write_text(HELPER.read_text(encoding="utf-8") + "# changed\n", encoding="utf-8")
    with pytest.raises(Refused, match=r"is not tools/release_migrations\.py at"):
        repo.derive(target, _plan(), helper=other)


def test_the_cli_derives_checks_and_prints_shell(repo, tmp_path, capsys):
    repo.write("app/x/migrations/0002_b.py", MIGRATION)
    target = repo.commit()
    reviewed = tmp_path / "plan-local.txt"
    reviewed.write_text(_plan("x.0002_b"), encoding="utf-8")
    out = tmp_path / "expected-migrations.txt"
    args = ["derive", "--repo", str(repo.root), "--previous", repo.previous, "--target", target]
    assert main([*args, "--reviewed-plan", str(reviewed), "--out", str(out)]) == 0
    # Never overwritten.
    assert main([*args, "--reviewed-plan", str(reviewed), "--out", str(out)]) == EXIT_REFUSED
    assert main(["check-plan", "--expected", str(out), "--plan", str(reviewed)]) == 0
    assert "MIGRATION_PLAN_MATCH count=1 [x.0002_b]" in capsys.readouterr().out
    live = tmp_path / "plan.txt"
    live.write_text(_plan(), encoding="utf-8")
    assert main(["check-plan", "--expected", str(out), "--plan", str(live)]) == EXIT_REFUSED
    assert "REFUSED: migration_plan lists 0 pending" in capsys.readouterr().err
    assert main(["shell", "--expected", str(out)]) == 0
    assert 'MIGRATE_ORDER="x:0002_b"\n' in capsys.readouterr().out


def test_the_helper_runs_isolated_without_the_project(tmp_path):
    """d1/d2 run it as `python -I /tmp/release_migrations.py` in the release image: no
    project, Django or working-directory import may be needed."""
    copy = tmp_path / "release_migrations.py"
    shutil.copyfile(HELPER, copy)
    expected = tmp_path / "e.txt"
    expected.write_text(_expected(R91).render(), encoding="utf-8", newline="\n")
    done = subprocess.run(  # noqa: S603 - this interpreter, a copied file, fixed arguments
        [sys.executable, "-I", str(copy), "check-plan", "--expected", str(expected), "--plan", "-"],
        input=R91,
        capture_output=True,
        text=True,
        check=False,
        cwd=tmp_path,
    )
    assert done.returncode == 0, done.stderr
    assert done.stdout.startswith("MIGRATION_PLAN_MATCH count=2")


# ---------------------------------------------------------------------------------------
# The real releases, rebuilt without their history
# ---------------------------------------------------------------------------------------
# CI checks out with fetch depth 2, so the r88..r91 commits are not there. The test rebuilds
# the three releases in a throwaway repository from the real migration files in this
# checkout (unchanged since: their blob ids must equal the ones the kits reviewed), in the
# same order: r88's base -> r89 adds legacy_import 0033 -> r90 code only -> r91 adds
# intelligence 0005 and register_watch 0008.

#: The predecessors the kits pinned (PREDECESSORS), present before each release.
REAL_BASE = (
    "app/legacy_import/migrations/0032_postipoiss_letters.py",
    "app/intelligence/migrations/0004_informational_commencement_kinds.py",
    "app/register_watch/migrations/0007_finding_files.py",
)
#: path -> the blob each kit reviewed (BLOB_legacy_import_0033, BLOB_intelligence_0005,
#: BLOB_register_watch_0008 in kit-r89 / kit-r91).
REAL_R89 = {
    "app/legacy_import/migrations/0033_lovable_owner_alias.py": (
        "cbebb22ecf061bd7ce09ad3d5fe6618fc512ab0f"
    ),
}
REAL_R91 = {
    "app/intelligence/migrations/0005_estimated_commencement_title.py": (
        "1ad387c13fbcb1dd305fb5533a856a2d4148c5b9"
    ),
    "app/register_watch/migrations/0008_agenda_ai_attempts.py": (
        "173d5434ae04944e30cfddc6ef70566d85c91664"
    ),
}


def _copy_real(repo: Repo, path: str) -> None:
    data = (ROOT / path).read_bytes().replace(b"\r\n", b"\n")
    target = repo.root / path
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(data)


def test_r89_r90_r91_derive_what_their_kits_pinned_by_hand(tmp_path):
    repo = Repo(tmp_path / "real")
    for path in REAL_BASE:
        _copy_real(repo, path)
    r88 = repo.commit()
    for path in REAL_R89:
        _copy_real(repo, path)
    r89_sha = repo.commit()
    repo.write("app/legacy_import/views.py", "# r90: code only\n")
    r90_sha = repo.commit()
    for path in REAL_R91:
        _copy_real(repo, path)
    r91_sha = repo.commit()

    def derive(previous: str, target: str, plan: str) -> Expected:
        repo.previous = previous
        return parse_expected(repo.derive(target, plan, helper=None).render())

    r89_expected = derive(r88, r89_sha, R89)
    r89 = dict(shell_values(r89_expected))
    assert r89["EXPECTED_MIGRATIONS"] == "legacy_import.0033_lovable_owner_alias"
    assert r89["MIGRATE_ORDER"] == "legacy_import:0033_lovable_owner_alias"
    assert r89["PREDECESSORS"] == "legacy_import:0032_postipoiss_letters"
    assert r89["EXPECTED_PLAN_HEADER"] == "1 pending migration(s):"
    assert [m.blob for m in r89_expected.migrations] == list(REAL_R89.values())

    r90 = dict(shell_values(derive(r89_sha, r90_sha, NONE_HOST)))
    assert r90["EXPECTED_MIGRATIONS"] == r90["EXPECTED_MIGRATION_FILES"] == ""
    assert r90["EXPECTED_PLAN_HEADER"] == "No pending migrations."

    r91_expected = derive(r90_sha, r91_sha, R91)
    r91 = dict(shell_values(r91_expected))
    assert r91["EXPECTED_MIGRATIONS"] == " ".join(R91_LABELS)
    assert r91["MIGRATE_ORDER"] == (
        "intelligence:0005_estimated_commencement_title register_watch:0008_agenda_ai_attempts"
    )
    assert r91["PREDECESSORS"] == (
        "intelligence:0004_informational_commencement_kinds register_watch:0007_finding_files"
    )
    assert r91["EXPECTED_MIGRATION_FILES"] == " ".join(REAL_R91)
    assert r91["EXPECTED_PLAN_HEADER"] == "2 pending migration(s):"  # what d2 hard-coded as 1
    assert [m.blob for m in r91_expected.migrations] == list(REAL_R91.values())
    # r89's plan is not r91's: the stop that happened, refused at derive time instead.
    repo.previous = r90_sha
    with pytest.raises(Refused, match="disagree"):
        repo.derive(r91_sha, R89, helper=None)
