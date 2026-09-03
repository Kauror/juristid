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
import shutil
import subprocess
from pathlib import Path

import pytest
from django.conf import settings

ROOT = Path(settings.BASE_DIR)
DEPLOY = ROOT / "deploy"
SCRIPTS = ROOT / "scripts" / "deploy"

RUNBOOKS = sorted(DEPLOY.glob("*/*.md"))
SHELL_SCRIPTS = sorted(SCRIPTS.glob("*.sh"))

#: Not skipped when absent: bash is present on the Linux runner and in the Git
#: for Windows toolchain, so a missing one is a broken environment.
BASH = shutil.which("bash") or "bash"

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


def _application_stacks() -> list[Path]:
    """Stack directories that deploy the application, not just a database.

    `deploy/recovery-rehearsal/compose.yml` is a lone `db` service for restoring
    a dump into, so it has nothing to keep in step and is not a counter-example.
    Its `compose.real-data.yml` sibling *does* define `web`, and is deliberately
    not picked up here: this glob reads `compose.yml` only, and a rehearsal is
    not a deployment. Its own contract is `test_deployment_recovery_rehearsal`.
    """
    import yaml

    stacks = []
    for path in sorted(DEPLOY.glob("*/compose.yml")):
        compose = yaml.safe_load(path.read_text(encoding="utf-8"))
        if "web" in compose.get("services", {}):
            stacks.append(path.parent)
    return stacks


def _services_running_the_application_image(compose: dict) -> set[str]:
    """Every service built from the same image as `web`.

    Read off the file rather than listed here, so a fifth application service
    is covered on the day it is added rather than on the day somebody remembers
    this test exists.
    """
    services = compose.get("services", {})
    web = services.get("web", {})
    reference = web.get("image") or web.get("build")
    if reference is None:
        return set()
    return {
        name
        for name, definition in services.items()
        if (definition.get("image") or definition.get("build")) == reference
    }


def _named_services(line: str) -> list[str]:
    """The service names a `docker compose … up -d …` line brings up.

    Everything after `up`, minus flags and their arguments. An empty list means
    the line was unqualified, which is the shape that needs no checking at all:
    Compose starts whatever the file defines.
    """
    tail = line.split(" up ", 1)[1].split()
    names: list[str] = []
    skip = False
    for token in tail:
        if skip:
            skip = False
            continue
        if token in ("--profile", "--scale", "--timeout", "--wait-timeout"):
            skip = True
            continue
        if token.startswith("-"):
            continue
        names.append(token)
    return names


def test_there_are_application_stacks_to_check() -> None:
    """Guards the guard below: an empty parametrisation would pass in silence."""
    assert len(_application_stacks()) >= 2


@pytest.mark.parametrize("stack", _application_stacks(), ids=lambda path: path.name)
def test_no_runbook_redeploys_only_some_of_the_application_services(stack: Path) -> None:
    """`up -d web` leaves every other service on the image it replaced.

    That was survivable while `web` and `extractor` were the only two — a stale
    extractor still drains its queue. It stopped being survivable when
    `searchindex` arrived: a stack redeployed with `up -d web` does not start the
    search refresh worker *at all*, so the durable debt SEARCH-001 introduced
    accumulates with nothing consuming it, and the projection goes back to
    converging only when a human runs a rebuild — which is the defect ADR 0041
    was written to close, reintroduced by the runbook rather than by the code.

    The rule is narrow on purpose. It only fires on a line that names `web`
    explicitly, so an instruction to restart one worker on its own is still
    writable, and an unqualified `up -d` — which is what a redeploy should say —
    passes without the test having an opinion about which services exist.
    """
    import yaml

    compose = yaml.safe_load((stack / "compose.yml").read_text(encoding="utf-8"))
    application = _services_running_the_application_image(compose)
    # Guards the guard: a stack whose services stopped sharing an image would
    # make every assertion below vacuous.
    assert len(application) >= 2, f"{stack.name}: nothing to keep in step"

    examined = 0
    for runbook in sorted(stack.glob("*.md")):
        for line in command_lines(runbook.read_text(encoding="utf-8")):
            if "docker compose" not in line or " up " not in line:
                continue
            named = _named_services(line)
            if "web" not in named:
                continue
            examined += 1
            missing = sorted(application - set(named))
            assert not missing, (
                f"{runbook.name}: this deploys `web` and leaves "
                f"{', '.join(missing)} on the previous build — or, for a service "
                f"the previous deployment did not have, not running at all. Say "
                f"`up -d` and let Compose start what the file defines.\n  {line}"
            )
    assert examined, f"{stack.name}: no runbook line deploys the application"


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


def test_the_recovery_runbook_records_the_real_set_rehearsal() -> None:
    """A restore rehearsal against a real set is worth exactly what it recorded.

    "We tested the backups once" decays into "the backups are tested", and the
    difference is which set, which PostgreSQL, and whether the evidence bytes
    were read or merely counted. The runbook has to carry the identifiers so a
    later reader can tell what was actually proved from what was assumed.
    """
    text = (DEPLOY / "unraid-main" / "RECOVERY.md").read_text(encoding="utf-8")
    assert "20260902T182850Z" in text, "the tested set is not named"
    assert "03.09.2026" in text, "the rehearsal has no date"
    assert "PostgreSQL 18" in text
    assert "53m46s" in text, "the measured duration is not recorded"
    assert "deep-hash verified" in text, "evidence byte verification is not recorded"


def test_the_recovery_runbook_keeps_the_rehearsals_limits_beside_its_result() -> None:
    """The limits are the half that gets dropped when a summary is written.

    A same-host restore says nothing about losing the host; a rehearsal with
    generated secrets says nothing about recovering the real ones; and the set
    that was tested described the *previous* release. Each of those is a way for
    this result to be read as broader than it is, so each has to survive in the
    same file as the result.
    """
    text = (DEPLOY / "unraid-main" / "RECOVERY.md").read_text(encoding="utf-8")
    assert "What is not yet fixed" in text
    for finding in ("DR0", "DR1-A", "DR1-B", "DR1-C"):
        assert finding in text, f"{finding} is not recorded"
    lowered = text.lower()
    for limit in (
        "same-host, same-disk",
        "no off-host recovery was proved",
        "secrets and tunnel recovery were not proved",
        "source corpus was not independently recovered",
        "one release behind the running application",
    ):
        assert limit in lowered, f"the runbook no longer states: {limit}"


def test_the_real_data_rehearsal_is_not_confused_with_the_synthetic_one() -> None:
    """Two files, one directory, one filename fragment apart.

    The synthetic stack publishes a host port. Pointing a production set at it
    would put the register on the host's network, so the runbook that sends an
    operator to a rehearsal has to send them to the right file and say why the
    other one is not it.
    """
    text = (DEPLOY / "unraid-main" / "RECOVERY.md").read_text(encoding="utf-8")
    assert "compose.real-data.yml" in text
    assert "publishes a host port" in text, "the runbook does not say why not the other one"
    for line in command_lines(text):
        if "juristid-recovery-rehearsal" not in line:
            continue
        assert "compose.real-data.yml" in line, (
            "a rehearsal command points at the synthetic stack:\n  " + line
        )


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

    A script that touches no stack at all is not asked to validate a project it
    was never given. `juristid-check-backup-age.sh` reads directory names and
    starts nothing; requiring an allow-list check there would be requiring a
    guard over a door that does not exist, and a `--project` flag nothing uses
    is a flag somebody eventually believes means something.
    """
    text = script.read_text(encoding="utf-8")
    lines = executable_lines(text)
    touches_a_stack = any("docker compose" in line or "juristid_compose" in line for line in lines)
    if touches_a_stack:
        assert "require_known_project" in text, script.name
    for line in lines:
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


# -- the shell trap that inverts a safety check ----------------------------


def test_the_pipefail_grep_inversion_is_real() -> None:
    """The mechanism these scripts must avoid, demonstrated rather than asserted.

    `grep -q` exits the instant it matches. Whatever is writing into it then dies
    on SIGPIPE, and `set -o pipefail` reports the *pipeline* by its worst member —
    so `writer | grep -q pattern` returns **failure when the pattern is present**,
    provided the input is long enough that the writer is still writing.

    Which makes it a bug that hides: it needs a match near the start of an input
    big enough to fill a pipe buffer, so every small example works perfectly.
    The deployment preflight had it on its most important check, where the
    inverted answer was the reassuring one — "no service publishes a host port",
    said precisely when a service did.
    """
    early_match = (
        "big=$(echo needle; for i in $(seq 1 50000); do echo padding padding padding; done)"
    )
    script = f"""
        set -euo pipefail
        {early_match}
        printf '%s\n' "$big" | grep -q needle && echo PIPED_FOUND || echo PIPED_MISSED
        grep -q needle <<<"$big" && echo STRING_FOUND || echo STRING_MISSED
    """
    result = subprocess.run(  # noqa: S603 - a fixed interpreter and a literal script
        [BASH, "-c", script], capture_output=True, text=True, timeout=120
    )
    assert "PIPED_MISSED" in result.stdout, (
        "the pipeline no longer inverts; if this shell has changed, the rule below "
        "may be able to relax"
    )
    assert "STRING_FOUND" in result.stdout


@pytest.mark.parametrize("script", SHELL_SCRIPTS, ids=lambda path: path.name)
def test_no_script_pipes_into_a_quiet_grep(script: Path) -> None:
    """Because the answer would be wrong in the direction nobody checks.

    A here-string has no upstream process to kill, so the exit status is grep's
    own. Comments are exempt: the scripts explain this trap where they used to
    fall into it.
    """
    for line in executable_lines(script.read_text(encoding="utf-8")):
        assert not re.search(r"\|\s*grep\s+(-\w*q|--quiet)", line), (
            f"{script.name}: piping into `grep -q` returns failure when the pattern "
            f"matches\n  {line}"
        )


# ---------------------------------------------------------------------------
# The evidence integrity check, once it exists beside the recovery tooling
#
# `check_evidence_integrity` arrived on the evidence branch and the recovery
# tooling arrived on the deployment branch, so nothing until integration made
# them agree. Two things have to stay true, and both are cheap to assert.
# ---------------------------------------------------------------------------


def test_the_recovery_runbook_verifies_the_restored_evidence_store() -> None:
    """A fingerprint comparison cannot see an object no row refers to.

    It compares what was recorded, and an orphan was never recorded. A restore
    that reassembled the database and the evidence tree from different points
    in time is exactly the failure that leaves one, so the runbook has to run
    the check that looks for it.
    """
    text = (DEPLOY / "unraid-main" / "RECOVERY.md").read_text(encoding="utf-8")
    assert "check_evidence_integrity" in text
    assert any(
        "check_evidence_integrity" in line
        for block in shell_blocks(text)
        for line in block.splitlines()
    ), "named in prose but not in a block an operator can copy"


def test_nothing_schedules_a_full_checksum_pass() -> None:
    """`--verify-sha` reads every stored byte.

    On this corpus that is a maintenance window, not a health check, and a
    health check that takes a maintenance window is a health check somebody
    switches off. It stays an explicit, deliberate operation: no runbook block,
    no script, no CI step may imply it.

    Prose is exempt, and so are the heredocs the scripts print instructions
    from, for the same reason the destructive-command scan exempts them: the
    documentation has to be able to say "not this flag, and here is why".
    """

    def runnable(path: Path) -> list[str]:
        text = path.read_text(encoding="utf-8")
        if path.suffix == ".md":
            return [line for block in shell_blocks(text) for line in block.splitlines()]
        if path.suffix == ".sh":
            return executable_lines(text)
        # YAML and the Dockerfile: everything a comment marker does not disown.
        return [
            line.strip()
            for line in text.splitlines()
            if line.strip() and not line.strip().startswith("#")
        ]

    candidates = [
        *RUNBOOKS,
        *SHELL_SCRIPTS,
        ROOT / ".github" / "workflows" / "ci.yml",
        DEPLOY / "unraid-main" / "compose.yml",
        DEPLOY / "unraid-test" / "compose.yml",
        DEPLOY / "recovery-rehearsal" / "compose.yml",
        DEPLOY / "recovery-rehearsal" / "compose.real-data.yml",
        DEPLOY / "recovery-rehearsal" / "compose.real-data.source.yml",
        ROOT / "docker-compose.yml",
        ROOT / "Dockerfile",
    ]
    for path in candidates:
        if not path.exists():
            continue
        for line in runnable(path):
            assert "--verify-sha" not in line, (
                f"{path.name}: a full checksum pass must be asked for by hand\n  {line}"
            )


# ---------------------------------------------------------------------------
# The decision register must not still be asking for what has arrived
# ---------------------------------------------------------------------------


def test_the_decision_register_does_not_call_the_tokens_provisional() -> None:
    """A settled decision listed as blocking asks somebody for what they sent.

    `docs/open-decisions.md` said "every colour in `static/css/tokens.css` is a
    marked placeholder" for as long as it took the CVI package to arrive, be
    mapped, and ship — while `tokens.css` itself opened with **CVI-MAPPED …
    not placeholders**. The same sentence had gone stale in `README.md`, and
    fixing one and not the other is how a claim survives.

    This is the cheap half of the check: the two files cannot both be right, so
    the register is asserted against the stylesheet rather than against a date.
    """
    root = Path(__file__).resolve().parent.parent
    register = (root / "docs" / "open-decisions.md").read_text(encoding="utf-8")
    tokens = (root / "static" / "css" / "tokens.css").read_text(encoding="utf-8")

    if "CVI-MAPPED" not in tokens:
        pytest.skip("tokens.css no longer claims to be CVI-mapped; check the register by hand")

    assert "is a marked placeholder" not in register, (
        "docs/open-decisions.md still calls the design tokens provisional while "
        "static/css/tokens.css says CVI-MAPPED. One of the two is wrong, and the "
        "register is the one that asks a person for something."
    )


def test_the_decision_register_does_not_call_the_stage_vocabulary_empty() -> None:
    """`StageVocabulary` was seeded from the reviewed workbook in
    `workflow/0004_seed_stage_vocabulary`. A register still calling it empty
    asks the department head to transcribe eleven labels they already
    transcribed."""
    root = Path(__file__).resolve().parent.parent
    register = (root / "docs" / "open-decisions.md").read_text(encoding="utf-8")
    seed = root / "app" / "workflow" / "migrations" / "0004_seed_stage_vocabulary.py"

    assert seed.exists(), "the seed migration moved; re-check the register by hand"
    assert "`StageVocabulary` and `LegacyStatusMapping` are empty" not in register, (
        "docs/open-decisions.md still calls the stage vocabulary empty, but "
        f"{seed.relative_to(root)} seeds it"
    )
