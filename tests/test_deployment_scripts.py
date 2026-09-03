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

from tests.test_deployment_runbooks import command_lines

ROOT = Path(settings.BASE_DIR)
SCRIPTS = ROOT / "scripts" / "deploy"
COMPOSE = ROOT / "deploy" / "unraid-main" / "compose.yml"

BACKUP = SCRIPTS / "juristid-backup.sh"
VERIFY = SCRIPTS / "juristid-verify-backup.sh"
RESTORE = SCRIPTS / "juristid-restore.sh"
AGE = SCRIPTS / "juristid-check-backup-age.sh"
PREFLIGHT = SCRIPTS / "juristid-deploy-preflight.sh"
LIB = SCRIPTS / "lib.sh"

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


# -- the sequence the preflight prints -------------------------------------
#
# What an operator copies, rendered by the shell that renders it in production
# rather than approximated in Python. The sequence lives in `deployment_plan` in
# `lib.sh` precisely so it can be called here: reaching it through the preflight
# itself would mean getting past a `git fetch`, a Docker daemon and a real host,
# and a check that cannot run is a check that does not.


def render_plan(target: str) -> list[str]:
    """The printed deployment sequence, one command per line."""
    script = (
        f'. "{LIB}"\n'
        f'deployment_plan juristid-main deploy/unraid-main/compose.yml /srv/repo "{target}"\n'
    )
    result = subprocess.run(  # noqa: S603 - a fixed interpreter and a generated literal
        [BASH, "-c", script], cwd=ROOT, capture_output=True, text=True, timeout=60
    )
    assert result.returncode == 0, result.stderr
    return [line.strip() for line in result.stdout.splitlines() if line.strip()]


TARGET = "a" * 40

#: The tag the release workflow saves the image under and `docker load` restores
#: on the host: the image name Compose resolves, and the first twelve characters
#: of the commit. Named once so the plan, the README and this file cannot drift.
RELEASE_IMAGE = f"juristid-main-web:{TARGET[:12]}"


def commands(lines: list[str]) -> list[str]:
    """The rendered plan without its comment lines."""
    return [line for line in lines if not line.startswith("#")]


def test_the_printed_sequence_never_builds_an_image_on_the_host() -> None:
    """The one command the production host must never be told to run.

    The release image is built off the host by `release-image.yml` for one
    reviewed commit and `docker load`ed here; the host's Docker storage sits
    behind a USB parity disk and BuildKit has died mid-build on it. The plan
    used to print `docker compose … build`, contradicting that workflow, and
    this is the assertion that keeps the contradiction from coming back. It
    reads what the operator sees, not the shell source.
    """
    for line in commands(render_plan(TARGET)):
        assert not line.startswith("docker build"), line
        assert not (line.startswith("docker compose") and line.endswith(" build")), (
            f"the printed plan builds an image on the production host\n  {line}"
        )
        assert "--build" not in line.replace("--no-build", ""), (
            f"the printed plan builds an image on the production host\n  {line}"
        )


def test_the_printed_replacement_says_no_build_on_the_command_itself() -> None:
    """`up -d --no-build`, not `up -d`.

    `compose.yml` keeps a `build:` stanza for CI. A plain `up -d` that found
    the release tag missing — a typo in the export, a load that did not happen
    — would build on the host from whatever the checkout holds. `--no-build`
    turns that into an error naming the missing image, and puts the contract
    on the command rather than in the operator's memory.
    """
    lines = commands(render_plan(TARGET))
    replacements = [line for line in lines if line.startswith("docker compose") and " up " in line]
    assert len(replacements) == 1, replacements
    assert replacements[0].endswith(" up -d --no-build"), replacements[0]


def test_the_printed_sequence_proves_the_loaded_image_is_the_target() -> None:
    """Before the first command that resolves it — and no invented path.

    The artifact lives wherever the operator transferred it, so the plan does
    not print a `docker load < /some/path`; the README owns verification and
    loading. What the plan does carry is the check that the tag Compose is
    about to resolve holds this commit, asked of the image itself the way the
    release workflow asks it, and placed before `run --rm web` — which would
    build the image if the tag were missing.
    """
    lines = commands(render_plan(TARGET))
    proof = [line for line in lines if line.startswith("docker run") and "/app/GIT_SHA" in line]
    assert len(proof) == 1, proof
    assert RELEASE_IMAGE in proof[0], proof[0]
    assert "--rm" in proof[0], proof[0]

    resolves = [
        index
        for index, line in enumerate(lines)
        if line.startswith("docker compose") and (" run " in line or " up " in line)
    ]
    assert resolves, "the printed sequence resolves no image at all"
    assert lines.index(proof[0]) < min(resolves), (
        "the identity of the loaded image is checked after a command that already resolved it"
    )
    assert not any("docker load" in line for line in lines), (
        "the plan prints a docker load with a path it cannot know"
    )


def test_the_printed_sequence_establishes_the_identity_before_it_is_needed() -> None:
    """Exported, in full, before the first command that resolves the image.

    The generated sequence used to prefix `build` and `up -d` with the two
    variables and leave them off `migrate`. Each line was separately correct;
    the schema change was the one that could resolve
    `juristid-main-web:local` — Compose's fallback, and the tag a hand-built
    image overwrites. With no build on the host the first consumer is the
    identity check, then `migration_plan`; every one of them comes after both
    exports.
    """
    lines = commands(render_plan(TARGET))

    assert f"export JURISTID_GIT_SHA={TARGET}" in lines
    assert f"export JURISTID_IMAGE_TAG={TARGET[:12]}" in lines

    identity = max(
        index
        for index, line in enumerate(lines)
        if line.startswith(("export JURISTID_GIT_SHA=", "export JURISTID_IMAGE_TAG="))
    )
    for needle in (
        "/app/GIT_SHA",
        "manage.py migration_plan",
        "manage.py migrate",
        "up -d --no-build",
        "manage.py deployment_readiness",
    ):
        matching = [index for index, line in enumerate(lines) if line.endswith(needle)]
        assert matching, f"the printed sequence has no {needle} step"
        assert min(matching) > identity, (
            f"{needle} is printed before the identity it needs, so it can resolve a different "
            "image from the rest of the release"
        )


def test_the_printed_sequence_reads_the_plan_from_the_target_image() -> None:
    """`run --rm web`, never `exec -T web`.

    `exec` enters the container that is still running the previous release.
    Application source is baked into the image and nothing bind-mounts the
    checkout over it, so the new migrations are not in there — and the answer it
    gives is "No pending migrations.", for a release that carries several.
    """
    for line in render_plan(TARGET):
        if "manage.py migration_plan" not in line:
            continue
        assert "run --rm web" in line, line
        assert "exec" not in line, line
        break
    else:  # pragma: no cover - the assertion below reports it
        pytest.fail("the printed sequence no longer reads a migration plan")


def test_the_printed_migrate_and_replacement_share_the_release_identity() -> None:
    """No line re-establishes the identity for itself.

    An inline `VAR=value command` prefix is separately correct and separately
    forgettable, which is exactly how `migrate` came to differ from the commands
    around it. One export, one shell, one image.
    """
    for line in commands(render_plan(TARGET)):
        if line.startswith("export "):
            continue
        for variable in ("JURISTID_GIT_SHA=", "JURISTID_IMAGE_TAG="):
            assert variable not in line, f"the identity is set per command again\n  {line}"


def test_the_printed_sequence_keeps_the_backup_against_the_migration() -> None:
    """identity check → plan → backup → migrate → replace → readiness.

    Loading an image writes nothing and changes no database, so it belongs
    before the backup. The backup's value is that nothing is written between
    it and the first command that changes the schema, so nothing may come
    between them; and readiness is asked of the running replacement, so it
    comes after `up`.
    """
    lines = commands(render_plan(TARGET))

    def first(needle: str) -> int:
        found = [index for index, line in enumerate(lines) if needle in line]
        assert found, f"the printed sequence no longer has {needle}"
        return found[0]

    assert first("/app/GIT_SHA") < first("migration_plan")
    assert first("migration_plan") < first("juristid-backup.sh")
    assert first("juristid-backup.sh") + 1 == first("manage.py migrate"), (
        "something is printed between the backup and the migration"
    )
    assert first("manage.py migrate") < first("up -d --no-build")
    assert first("up -d --no-build") < first("deployment_readiness")


def test_the_printed_post_flight_check_enters_the_running_container() -> None:
    """Because by then the running container *is* the new image.

    Here so that correcting the plan by replacing every `exec` with `run` fails
    rather than passes: readiness has to be asked of the process actually
    serving, not of a one-off container that need not be the same image.
    """
    readiness = [line for line in render_plan(TARGET) if "deployment_readiness" in line]
    assert readiness, "the printed sequence no longer ends with a readiness check"
    for line in readiness:
        assert "exec -T web" in line, line


def test_the_printed_sequence_reaches_no_other_stack() -> None:
    """Every Compose line in it still names the project and the file.

    `juristid-test` is on the same host with the usability-test history in it,
    and implicit discovery finds whichever stack the working directory suggests.
    """
    compose = [line for line in commands(render_plan(TARGET)) if line.startswith("docker compose")]
    assert len(compose) >= 4, compose
    for line in compose:
        assert "-p juristid-main" in line, line
        assert "-f deploy/unraid-main/compose.yml" in line, line


def test_the_preflight_explains_the_plan_in_terms_of_a_loaded_image() -> None:
    """The prose after the plan is what an operator reads while deciding.

    It used to say the exports come "before the build" and that `run --rm`
    starts "the image just built" — an explanation of a sequence that no longer
    exists, printed under the one that does. Read as source rather than run:
    reaching that text through the script means a Docker daemon and a real
    checkout.
    """
    text = PREFLIGHT.read_text(encoding="utf-8")
    for stale in ("before the build", "image just built", "the build,"):
        assert stale not in text, f"the preflight still explains a build on the host: {stale!r}"
    for current in ("release-image.yml", "docker-loaded", "--no-build", "/app/GIT_SHA"):
        assert current in text, f"the preflight's explanation no longer mentions {current!r}"


# -- the production runbook cannot regress to an on-host build ------------
#
# Narrow on purpose. `docker build` is exactly right in `release-image.yml`,
# `docker compose up --build` is exactly right in a development checkout, and
# CI builds a disposable image on every push. What is forbidden is the
# real-data host being *told* to build, and the place it would be told is the
# copyable blocks of the two runbooks in deploy/unraid-main/. Prose is not
# scanned: a runbook has to be able to say "never `docker compose build`".

PRODUCTION_RUNBOOKS = sorted((ROOT / "deploy" / "unraid-main").glob("*.md"))


@pytest.mark.parametrize("runbook", PRODUCTION_RUNBOOKS, ids=lambda path: path.name)
def test_the_production_runbook_never_tells_the_host_to_build(runbook: Path) -> None:
    for line in command_lines(runbook.read_text(encoding="utf-8")):
        assert not line.startswith("docker build"), f"{runbook.name}: builds on the host\n  {line}"
        if "docker compose" not in line:
            continue
        assert not line.rstrip().endswith(" build"), (
            f"{runbook.name}: tells the production host to build the image. The release image "
            f"is built off-host by release-image.yml and docker-loaded here.\n  {line}"
        )
        assert "--build" not in line.replace("--no-build", ""), (
            f"{runbook.name}: `up --build` builds on the production host\n  {line}"
        )


@pytest.mark.parametrize("runbook", PRODUCTION_RUNBOOKS, ids=lambda path: path.name)
def test_every_production_up_that_starts_the_application_says_no_build(runbook: Path) -> None:
    """Wherever the release procedure starts or replaces the application image.

    `up -d db` and `up -d tunnel` run pinned upstream images that no `build:`
    stanza can touch, so they are left alone. Any `up` that is unqualified or
    names an application service resolves `juristid-main-web:<tag>`, and that
    is where a missing tag becomes a build unless the command forbids it.
    """
    import yaml

    compose = yaml.safe_load((ROOT / "deploy" / "unraid-main" / "compose.yml").read_text())
    web_image = compose["services"]["web"]["image"]
    application = {
        name for name, service in compose["services"].items() if service.get("image") == web_image
    }
    assert {"web", "extractor", "searchindex"} <= application

    examined = 0
    for line in command_lines(runbook.read_text(encoding="utf-8")):
        if "docker compose" not in line or " up " not in line:
            continue
        tail = line.split(" up ", 1)[1].split()
        named = [token for token in tail if not token.startswith("-")]
        if named and not application & set(named):
            continue
        examined += 1
        assert "--no-build" in tail, (
            f"{runbook.name}: starts the application image without forbidding a build\n  {line}"
        )
    assert examined, f"{runbook.name}: no line starts the application at all"


def test_the_production_runbook_documents_the_release_artifact_contract() -> None:
    """The three files the workflow uploads, the digest checked before the load,
    and the load itself — in the README, once, in copyable form.
    """
    text = (ROOT / "deploy" / "unraid-main" / "README.md").read_text(encoding="utf-8")
    workflow = (ROOT / ".github" / "workflows" / "release-image.yml").read_text(encoding="utf-8")

    assert "release-image.yml" in text
    for artifact in (
        "juristid-main-web-<sha12>.tar.gz",
        "juristid-main-web-<sha12>.tar.gz.sha256",
        "release-manifest-<sha12>.txt",
    ):
        assert artifact in text, f"the README does not name {artifact}"
    # The names the README documents are the names the workflow writes.
    assert 'archive="juristid-main-web-$SHORT.tar.gz"' in workflow
    assert '"$archive.sha256"' in workflow
    assert '"release-manifest-$SHORT.txt"' in workflow

    lines = command_lines(text)
    verify = [index for index, line in enumerate(lines) if line.startswith("sha256sum -c ")]
    load = [index for index, line in enumerate(lines) if line.startswith("docker load < ")]
    assert len(verify) == 1 and len(load) == 1, (verify, load)
    assert verify[0] < load[0], "the digest is checked after the image is loaded"
    assert "${JURISTID_IMAGE_TAG}" in lines[load[0]], (
        "the load names an invented path rather than the release tag"
    )


# -- ordinary usability ----------------------------------------------------


@pytest.mark.parametrize("script", [BACKUP, VERIFY, RESTORE, PREFLIGHT, AGE], ids=lambda p: p.name)
def test_every_script_explains_itself(script: Path) -> None:
    result = run(script, "--help")
    assert result.returncode == 0
    assert "Usage:" in result.stdout


@pytest.mark.parametrize("script", [BACKUP, VERIFY, RESTORE, PREFLIGHT, AGE], ids=lambda p: p.name)
def test_an_unknown_argument_stops_the_script(script: Path) -> None:
    """Rather than being ignored.

    A typo in `--backup-root` that silently fell through would produce a backup
    somewhere nobody looks, which is worse than no backup because it is believed.
    """
    result = run(script, "--backup-root-typo", str(ROOT))
    assert result.returncode != 0
    assert "unknown argument" in result.stderr
