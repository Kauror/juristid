"""The runbooks and the scripts are operational code, and are read as such.

An operator working from a runbook at eleven at night types what it says. So a
command that reaches the wrong stack, deletes a volume, or throws away somebody
else's uncommitted work is a defect in this repository, not a mistake by the
person who ran it — and it is the kind of defect that only ever shows up in
production, where it is expensive.

These tests read the fenced shell blocks out of the deployment documentation and
the scripts beside them, and check the properties that matter. Prose is not
scanned: a runbook has to be able to say "never run `docker system prune`", and
a test that could not tell that from an instruction to run it would make the
warning unwritable.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest
from django.conf import settings

ROOT = Path(settings.BASE_DIR)
DEPLOY = ROOT / "deploy"
SCRIPTS = ROOT / "scripts" / "deploy"

RUNBOOKS = sorted(DEPLOY.glob("*/*.md"))
SHELL_SCRIPTS = sorted(SCRIPTS.glob("*.sh"))

#: Commands that must never appear in a runbook's copyable blocks, and why.
#:
#: Every one of these is a command somebody reaches for while trying to fix
#: something else, and every one of them destroys data that is not the thing
#: they were fixing. They are not forbidden because they are dangerous in the
#: abstract; they are forbidden because this host runs other people's services
#: and one irreplaceable evidence tree.
FORBIDDEN_IN_RUNBOOKS = {
    "down -v": "removes volumes, and the evidence tree cannot be regenerated",
    "docker system prune": "reaches every container on a host that runs other services",
    "docker volume prune": "the same, for the volumes",
    "docker image prune -a": "deletes the images a rollback would have used",
    "git reset --hard": "destroys uncommitted work whose existence is the thing to investigate",
    "git clean": "the same, for untracked files",
    "chmod -R 777": "answers a permissions question by removing the permissions",
    "rm -rf /mnt/user/appdata": "no runbook needs this, and every typo in it is fatal",
}

_FENCED = re.compile(r"```(?:bash|sh|shell|console)\n(.*?)```", re.DOTALL)

_HEREDOC = re.compile(r"<<-?\s*[\"\']?([A-Za-z_][A-Za-z0-9_]*)[\"\']?")


def executable_lines(script: str) -> list[str]:
    """The lines a shell would run: no comments, and no heredoc bodies.

    Heredocs matter here because these scripts print instructions. The restore
    script ends by telling the operator what to run next, and a scan that could
    not tell printed text from an invocation would either flag that or force it
    to be written in a way nobody could copy.
    """
    lines: list[str] = []
    terminator: str | None = None
    for raw in script.splitlines():
        stripped = raw.strip()
        if terminator is not None:
            if stripped == terminator:
                terminator = None
            continue
        if not stripped or stripped.startswith("#"):
            continue
        opening = _HEREDOC.search(raw)
        if opening:
            terminator = opening.group(1)
        lines.append(stripped)
    return lines


def shell_blocks(markdown: str) -> list[str]:
    return _FENCED.findall(markdown)


def command_lines(markdown: str) -> list[str]:
    lines: list[str] = []
    for block in shell_blocks(markdown):
        for raw in block.splitlines():
            line = raw.strip()
            if line and not line.startswith("#"):
                lines.append(line)
    return lines


# -- the documentation -----------------------------------------------------


@pytest.mark.parametrize("runbook", RUNBOOKS, ids=lambda path: path.parent.name + "/" + path.name)
def test_no_runbook_tells_an_operator_to_run_a_destructive_command(runbook: Path) -> None:
    text = runbook.read_text(encoding="utf-8")
    for line in command_lines(text):
        for forbidden, why in FORBIDDEN_IN_RUNBOOKS.items():
            assert forbidden not in line, f"{runbook.name}: `{forbidden}` — {why}\n  {line}"


@pytest.mark.parametrize("runbook", RUNBOOKS, ids=lambda path: path.parent.name + "/" + path.name)
def test_every_compose_command_names_its_project_and_its_file(runbook: Path) -> None:
    """Implicit discovery finds whichever stack the current directory suggests.

    On this host that includes `juristid-test`, which holds the usability-test
    history and must survive every deployment untouched. Naming both leaves
    nothing for the working directory to decide.
    """
    for line in command_lines(runbook.read_text(encoding="utf-8")):
        if "docker compose" not in line:
            continue
        assert " -p " in line, f"{runbook.name}: no project\n  {line}"
        assert " -f " in line, f"{runbook.name}: no compose file\n  {line}"


def test_the_real_data_runbook_does_not_deploy_whatever_main_has_become() -> None:
    """`git pull` deploys the branch, not the commit that was reviewed.

    On a repository several people and several agents push to, those are
    routinely different things, and the difference is invisible until something
    unreviewed is serving members' material.
    """
    for name in ("README.md", "RECOVERY.md"):
        text = (DEPLOY / "unraid-main" / name).read_text(encoding="utf-8")
        for line in command_lines(text):
            assert "git pull" not in line, f"{name} still deploys by pulling:\n  {line}"


def test_the_real_data_runbook_points_at_a_recovery_procedure() -> None:
    """A backup with no written restore is a file, not a recovery plan."""
    readme = (DEPLOY / "unraid-main" / "README.md").read_text(encoding="utf-8")
    assert "RECOVERY.md" in readme
    assert (DEPLOY / "unraid-main" / "RECOVERY.md").exists()


def test_the_recovery_runbook_separates_a_local_copy_from_disaster_recovery() -> None:
    """A backup on the same disk as the thing it backs up is not disaster recovery.

    It protects against an operator mistake and a bad deployment, which is worth
    having and worth saying. It does not protect against the disk, the host, the
    filesystem or the building — and calling it DR is how a system ends up with
    a recovery plan that shares a failure boundary with the failure.
    """
    text = (DEPLOY / "unraid-main" / "RECOVERY.md").read_text(encoding="utf-8").lower()
    assert "off-host" in text
    assert "local recovery copy" in text


# -- the scripts -----------------------------------------------------------


def test_there_are_scripts_to_check() -> None:
    """Guards the guards: an empty glob would make every test below vacuous."""
    assert len(SHELL_SCRIPTS) >= 4


@pytest.mark.parametrize("script", SHELL_SCRIPTS, ids=lambda path: path.name)
def test_every_script_is_bash_with_the_unforgiving_options(script: Path) -> None:
    """`set -euo pipefail`, and `pipefail` is the one that matters here.

    Without it the exit status of `pg_dump | gzip > file` is gzip's, so a dump
    that died halfway produces a file, a zero exit status, and a backup that is
    discovered to be truncated on the day it is needed.
    """
    text = script.read_text(encoding="utf-8")
    assert text.startswith("#!/usr/bin/env bash"), script.name
    if script.name == "lib.sh":
        return  # sourced, so it inherits the caller's options
    assert "set -euo pipefail" in text, script.name


@pytest.mark.parametrize("script", SHELL_SCRIPTS, ids=lambda path: path.name)
def test_no_script_carries_windows_line_endings(script: Path) -> None:
    """Development happens on Windows and these run on Linux.

    A CRLF after the shebang makes the kernel look for an interpreter called
    `bash\\r`, and the error names a file that plainly exists.
    """
    assert b"\r\n" not in script.read_bytes(), script.name


@pytest.mark.parametrize("script", SHELL_SCRIPTS, ids=lambda path: path.name)
def test_no_script_can_reach_a_stack_it_was_not_given(script: Path) -> None:
    """Both halves: nothing runs Compose without an explicit project and file.

    Every invocation goes through `juristid_compose`, which uses the two
    variables the script was started with, and every entry point validates the
    project against an allow-list first.
    """
    text = script.read_text(encoding="utf-8")
    assert "require_known_project" in text, script.name
    for line in executable_lines(text):
        if "docker compose" not in line:
            continue
        assert '-p "$JURISTID_PROJECT"' in line, f"{script.name}: {line}"
        assert '-f "$JURISTID_COMPOSE_FILE"' in line, f"{script.name}: {line}"


@pytest.mark.parametrize("script", SHELL_SCRIPTS, ids=lambda path: path.name)
def test_no_script_destroys_anything(script: Path) -> None:
    """These scripts create and read. Deleting is somebody else's decision.

    No retention policy has been agreed (docs/open-decisions.md), so nothing
    here removes an old backup; no host-wide prune, because this host is not
    only ours; and no destructive Git, because a production checkout with
    unexpected changes in it is evidence.
    """
    text = script.read_text(encoding="utf-8")
    for forbidden in (
        "docker system prune",
        "docker volume prune",
        "docker compose down",
        "git reset",
        "git clean",
        "git checkout",
        "git stash",
        "chmod -R 777",
    ):
        for line in executable_lines(text):
            assert forbidden not in line, f"{script.name}: `{forbidden}`\n  {line}"


def test_the_backup_script_refuses_juristid_test_by_name() -> None:
    """Named, not merely unmatched.

    `juristid-test` is the one wrong project somebody actually types, because it
    is the stack they have been operating for months. A generic "unknown
    project" would be correct and would not say the thing worth saying.
    """
    lib = (SCRIPTS / "lib.sh").read_text(encoding="utf-8")
    assert "juristid-test)" in lib
    assert "refusing to touch juristid-test" in lib


def test_the_backup_script_does_not_pipe_the_dump_through_anything() -> None:
    """The pipeline is the defect, so it is gone rather than guarded.

    `pg_dump | gzip > file` needs `pipefail` to be safe. The custom format
    compresses inside pg_dump, so there is no second process whose success can
    stand in for the first one failing.
    """
    text = (SCRIPTS / "juristid-backup.sh").read_text(encoding="utf-8")
    assert "--format=custom" in text
    # Comments excluded: the header quotes the line this replaces, and the
    # quotation is the explanation.
    for line in executable_lines(text):
        assert "gzip" not in line
