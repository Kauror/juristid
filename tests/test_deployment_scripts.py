"""The deployment scripts, run.

Reading a script proves it says the right thing. Running it proves it does the
right thing, and the parts worth running here are the refusals — the branches
that decide whether a command reaches the stack it was aimed at or a different
one. Those branches execute before anything touches Docker, so they can be
exercised on a laptop with no Docker, no PostgreSQL and nothing deployed.

What is deliberately not here: the successful path. A backup that works is
proved by the `recovery` job in CI, which runs these same scripts against a
disposable PostgreSQL 18, destroys it, and restores it. That needs Docker, so it
lives there rather than in a test that would skip itself on every developer
machine — and a check that skips itself is a check that never runs.
"""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest
from django.conf import settings

ROOT = Path(settings.BASE_DIR)
SCRIPTS = ROOT / "scripts" / "deploy"
COMPOSE = ROOT / "deploy" / "unraid-main" / "compose.yml"

BACKUP = SCRIPTS / "juristid-backup.sh"
VERIFY = SCRIPTS / "juristid-verify-backup.sh"
RESTORE = SCRIPTS / "juristid-restore.sh"
PREFLIGHT = SCRIPTS / "juristid-deploy-preflight.sh"

#: Not skipped when absent. Bash is present on the Linux runner and in the Git
#: for Windows toolchain the development machine uses, so a missing one is a
#: broken environment rather than a reason to pass quietly.
BASH = shutil.which("bash") or "bash"


def run(script: Path, *arguments: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(  # noqa: S603 - a fixed interpreter and a repository path
        [BASH, str(script), *arguments],
        cwd=ROOT,
        capture_output=True,
        text=True,
        timeout=120,
    )


# -- the wrong stack -------------------------------------------------------


def _arguments_for(script: Path, project: str) -> list[str]:
    """The minimum each script needs to reach its project check.

    Written out per script rather than passed as one superset: an unknown
    argument stops these scripts, which is the behaviour a separate test below
    relies on.
    """
    common = ["--project", project, "--compose-file", str(COMPOSE)]
    if script == BACKUP:
        return [*common, "--data-root", str(ROOT), "--backup-root", str(ROOT)]
    if script == VERIFY:
        return [*common, "--set", str(ROOT)]
    if script == RESTORE:
        return [*common, "--set", str(ROOT), "--database-only"]
    return [*common, "--repo", str(ROOT), "--target", "0" * 40]


@pytest.mark.parametrize("script", [BACKUP, VERIFY, RESTORE, PREFLIGHT], ids=lambda p: p.name)
def test_no_script_will_operate_on_juristid_test(script: Path) -> None:
    """The one wrong project somebody actually types.

    `juristid-test` is the stack the department has been using for months, so it
    is the name that comes to hand — and it holds usability-test history that
    must survive every deployment untouched. It is refused by name rather than
    merely failing to match, because the message is the point.
    """
    result = run(script, *_arguments_for(script, "juristid-test"))
    assert result.returncode != 0
    assert "juristid-test" in result.stderr
    assert "refusing" in result.stderr


@pytest.mark.parametrize("script", [BACKUP, VERIFY, RESTORE, PREFLIGHT], ids=lambda p: p.name)
def test_an_unrecognised_project_is_refused(script: Path) -> None:
    """An allow-list, not a deny-list.

    A deny-list protects against the names somebody thought of. This host runs
    more than twenty containers, and the list of things not to break is not
    knowable in advance.
    """
    result = run(script, *_arguments_for(script, "juristid"))
    assert result.returncode != 0
    assert "unknown Compose project" in result.stderr


# -- refusing to start rather than half-finishing --------------------------


def test_the_backup_refuses_a_destination_that_does_not_exist() -> None:
    """Better than creating it: a mistyped path would silently become a new
    backup root, and the real one would sit there quietly not being written to.
    """
    result = run(
        BACKUP,
        "--compose-file",
        str(COMPOSE),
        "--data-root",
        str(ROOT),
        "--backup-root",
        str(ROOT / "no-such-backup-root"),
    )
    assert result.returncode != 0
    assert "backup root not found" in result.stderr


def test_the_backup_refuses_a_data_root_with_no_evidence_tree(tmp_path: Path) -> None:
    """A missing bind mount looks exactly like a tree with nothing in it.

    If the check were only "did rsync succeed", a backup taken while the
    evidence mount was absent would succeed, record nothing, and be
    indistinguishable from a real one.
    """
    backups = tmp_path / "backups"
    backups.mkdir()
    result = run(
        BACKUP,
        "--compose-file",
        str(COMPOSE),
        "--data-root",
        str(tmp_path),
        "--backup-root",
        str(backups),
    )
    assert result.returncode != 0
    assert "evidence tree not found" in result.stderr


def test_the_verifier_refuses_a_set_that_is_not_one(tmp_path: Path) -> None:
    result = run(VERIFY, "--set", str(tmp_path), "--level", "1")
    assert result.returncode != 0
    assert "database dump not found" in result.stderr


def test_level_three_is_not_something_a_script_can_claim(tmp_path: Path) -> None:
    """The deepest level is a restore into a disposable database.

    Offering it as a flag on a file check would let somebody believe they had
    run it. It is the CI rehearsal, and the refusal says so.
    """
    result = run(VERIFY, "--set", str(tmp_path), "--level", "3")
    assert result.returncode != 0
    assert "RECOVERY.md" in result.stderr


# -- deploying a commit rather than a branch -------------------------------


@pytest.mark.parametrize("target", ["main", "HEAD", "abc1234", "origin/main", "v1.0"])
def test_the_preflight_refuses_anything_that_is_not_a_full_commit(target: str) -> None:
    """A branch is whatever it has become; an abbreviation can match twice.

    Both resolve silently, and both resolve at deploy time rather than at review
    time, which is the whole problem.
    """
    result = run(
        PREFLIGHT,
        "--repo",
        str(ROOT),
        "--target",
        target,
        "--compose-file",
        str(COMPOSE),
    )
    assert result.returncode != 0
    assert "full 40-character commit id" in result.stderr


def test_the_preflight_accepts_a_full_commit_id_shaped_target(tmp_path: Path) -> None:
    """It gets past the format check and fails later, on facts about the host.

    Worth asserting separately: a format check that rejected everything would
    pass the test above for the wrong reason.

    Pointed at a directory that is not a checkout, so the run stops at that and
    never fetches — a test has no business reaching the network or touching the
    repository it is running inside.
    """
    result = run(
        PREFLIGHT,
        "--repo",
        str(tmp_path),
        "--target",
        "0" * 40,
        "--compose-file",
        str(COMPOSE),
    )
    assert "full 40-character commit id" not in result.stderr
    assert "the target is a full commit id" in result.stdout
    assert "is not a Git checkout" in result.stderr


# -- ordinary usability ----------------------------------------------------


@pytest.mark.parametrize("script", [BACKUP, VERIFY, RESTORE, PREFLIGHT], ids=lambda p: p.name)
def test_every_script_explains_itself(script: Path) -> None:
    result = run(script, "--help")
    assert result.returncode == 0
    assert "Usage:" in result.stdout


@pytest.mark.parametrize("script", [BACKUP, VERIFY, RESTORE, PREFLIGHT], ids=lambda p: p.name)
def test_an_unknown_argument_stops_the_script(script: Path) -> None:
    """Rather than being ignored.

    A typo in `--backup-root` that silently fell through would produce a backup
    somewhere nobody looks, which is worse than no backup because it is believed.
    """
    result = run(script, "--backup-root-typo", str(ROOT))
    assert result.returncode != 0
    assert "unknown argument" in result.stderr
