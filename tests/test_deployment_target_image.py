"""Which image a deployment command reaches, and why it is never a detail.

Application source is `COPY`ed into the image, and the production stack
bind-mounts no source into `/app` — only evidence, derivatives, the OneNote
source and the read-only corpus. So the code inside a running container is the
code of the image it was started from, and moving the deployment checkout to the
reviewed commit changes nothing inside it.

That gives two commands with two different meanings:

``exec``
    enters the container that is **already running**, which during a deployment
    still holds the **previously deployed** image.

``run --rm``
    starts a one-off container from the image the Compose file currently
    resolves — after a build with the identity exported, the **target** image.

Neither is safer than the other. The rule is that the command has to be aimed at
whichever image the question is about, and the runbook had it backwards in the
one place where the wrong answer is the reassuring one:

* ``exec -T web python manage.py migration_plan`` read the *old* release's
  migration graph, so it could answer "No pending migrations." for a release
  carrying several — in the voice of a safety check, at the moment before an
  operator decides not to take a maintenance window; and
* the generated ``migrate`` step did not carry the deployment identity, so it
  could resolve ``juristid-main-web:local`` — Compose's fallback tag, and the
  one a hand-built image overwrites — making a schema change on behalf of a
  build nobody reviewed.

These tests are the contract for the corrected sequence. They assert safety
properties rather than comparing files: a runbook has to stay writable.

The complementary check lives in CI's compose smoke job, where Docker exists —
`scripts/ci/assert_deployment_identity.py` reads `docker compose config` and
proves the resolution itself rather than re-implementing `${VAR:-default}`.
"""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path

import pytest
from django.conf import settings

from tests.test_deployment_runbooks import RUNBOOKS, command_lines

MAIN = Path(settings.BASE_DIR) / "deploy" / "unraid-main"
PREFLIGHT = Path(settings.BASE_DIR) / "scripts" / "deploy" / "juristid-deploy-preflight.sh"

#: The deployment's identity: exported once, before anything is built, and read
#: by every command after it. Together they resolve `juristid-main-web:<sha12>`.
IDENTITY = ("JURISTID_GIT_SHA", "JURISTID_IMAGE_TAG")

#: What Compose resolves when `JURISTID_IMAGE_TAG` is unset. Named in the
#: runbooks because it is the concrete thing that goes wrong, and named here so
#: that renaming it in one place fails rather than drifts.
FALLBACK_IMAGE = "juristid-main-web:local"

DEPLOYMENT_SECTION = ("## Deploying a new build", "## Backup, restore, disaster recovery")
FAILED_MIGRATION_SECTION = ("### The migration failed partway", "### A data migration")
AUDIT_SECTION = ("### 7. Release-specific pre-migration audits", "### 8. Back up")


def section(text: str, heading: str, until: str) -> str:
    """The part of a runbook between two headings, both of which must exist."""
    assert heading in text, f"the runbook no longer has a section headed {heading!r}"
    start = text.index(heading)
    assert until in text[start:], f"nothing follows {heading!r}; the section is unbounded"
    return text[start : text.index(until, start)]


@pytest.fixture(scope="module")
def readme() -> str:
    return (MAIN / "README.md").read_text(encoding="utf-8")


@pytest.fixture(scope="module")
def recovery() -> str:
    return (MAIN / "RECOVERY.md").read_text(encoding="utf-8")


def sole(lines: list[str], matches: Callable[[str], bool], what: str) -> int:
    """The index of the one command that matches, and a failure if there is not one.

    Exactly one, not the first one: a sequence that grew a second `migrate` is a
    sequence nobody has read end to end, and taking the first would hide it.
    """
    found = [index for index, line in enumerate(lines) if matches(line)]
    assert len(found) == 1, (
        f"expected exactly one {what} command in the sequence, found {len(found)}"
    )
    return found[0]


# -- the migration plan ----------------------------------------------------


@pytest.mark.parametrize("runbook", RUNBOOKS, ids=lambda path: path.parent.name + "/" + path.name)
def test_no_runbook_reads_a_migration_plan_out_of_the_running_container(runbook: Path) -> None:
    """The step exists to say what the *target* release will ask of the schema.

    Asked through `exec`, it asks the release that is still serving, whose
    migration graph does not contain the new migrations at all — so it cannot
    call them pending, and "No pending migrations." is the answer it gives for a
    release full of them.
    """
    for line in command_lines(runbook.read_text(encoding="utf-8")):
        if "manage.py migration_plan" not in line:
            continue
        assert "run --rm web" in line, (
            f"{runbook.name}: a migration plan must be read from the target image\n  {line}"
        )
        assert "exec" not in line, (
            f"{runbook.name}: `exec` reads the migration graph of the release already running, "
            f"which cannot contain the migrations being deployed\n  {line}"
        )


def test_the_preflight_does_not_print_a_plan_read_from_the_old_image() -> None:
    """The generated sequence is what an operator actually copies.

    Read as text as well as run (`tests/test_deployment_scripts.py` renders it),
    because a `migration_plan` that reappeared beside `exec` anywhere in this
    file would be one paste away from production.
    """
    text = PREFLIGHT.read_text(encoding="utf-8") + (
        Path(settings.BASE_DIR) / "scripts" / "deploy" / "lib.sh"
    ).read_text(encoding="utf-8")
    for line in text.splitlines():
        stripped = line.strip()
        if "manage.py migration_plan" not in stripped or stripped.startswith("#"):
            continue
        assert "run --rm web" in stripped, (
            f"the printed plan reads it from the wrong image\n  {stripped}"
        )


# -- the deployment identity -----------------------------------------------


def test_the_sequence_names_the_release_before_it_builds_anything(readme: str) -> None:
    """Both variables, exported once, before the first command that reads them.

    The alternative — prefixing individual commands — is what produced the
    second defect: `build` and `up -d` carried the identity and `migrate` did
    not, so a schema change could resolve the fallback image while the release
    around it resolved the reviewed one.
    """
    lines = command_lines(section(readme, *DEPLOYMENT_SECTION))

    for variable in IDENTITY:
        exports = [line for line in lines if line.startswith(f"export {variable}=")]
        assert len(exports) == 1, f"{variable} is exported {len(exports)} times, not once"

    last_export = max(
        index
        for index, line in enumerate(lines)
        if any(line.startswith(f"export {variable}=") for variable in IDENTITY)
    )

    for what, matches in (
        ("build", lambda line: "docker compose" in line and line.rstrip().endswith("build")),
        ("migration_plan", lambda line: "manage.py migration_plan" in line),
        ("migrate", lambda line: line.rstrip().endswith("manage.py migrate")),
        ("up -d", lambda line: "docker compose" in line and line.rstrip().endswith("up -d")),
    ):
        assert sole(lines, matches, what) > last_export, (
            f"the {what} step runs before the release identity is exported, so it can resolve "
            f"{FALLBACK_IMAGE} instead of the reviewed image"
        )


def test_nothing_in_the_sequence_re_establishes_the_identity_per_command(readme: str) -> None:
    """One shell, one identity.

    Inline `VAR=value command` prefixes are how build, migrate and up drifted
    apart in the first place: each one is separately correct and separately
    forgettable. Exported once, a command cannot be the one that missed out.
    """
    for line in command_lines(section(readme, *DEPLOYMENT_SECTION)):
        if line.startswith("export "):
            continue
        for variable in IDENTITY:
            assert f"{variable}=" not in line, (
                f"the identity is set again on one command rather than exported for all of "
                f"them, which is the shape that let `migrate` differ\n  {line}"
            )


# -- the order -------------------------------------------------------------


def test_the_sequence_stays_in_the_one_safe_order(readme: str) -> None:
    """build → plan → backup → migrate → up, and each `<` is load-bearing.

    *Build before plan* is the correction: the plan is a question about the
    target image, so the target image has to exist to be asked. A build writes
    no business data, mutates no database and replaces nothing, so moving it
    earlier moved nothing dangerous earlier.

    *Backup after the plan and immediately before migrate* is what deliberately
    did **not** move. The backup is the copy a failed migration is restored to,
    and everything written between it and the failure is lost in that restore —
    so it belongs against the first command that changes the database, not
    against the first command of the deployment.
    """
    lines = command_lines(section(readme, *DEPLOYMENT_SECTION))

    def first(needle: str, what: str) -> int:
        found = [index for index, line in enumerate(lines) if needle in line]
        assert found, f"the deployment sequence no longer has a {what} step"
        return found[0]

    build = first("compose.yml build", "build")
    plan = first("manage.py migration_plan", "migration plan")
    backup = first("juristid-backup.sh", "backup")
    migrate = first("manage.py migrate", "migrate")
    replace = first("compose.yml up -d", "replacement")

    assert build < plan, "the plan is read from the target image, so the image must exist first"
    assert plan < backup, "a plan read after the backup cannot inform the decision to take one"
    assert backup < migrate, "the backup is the copy a failed migration is restored to"
    assert migrate < replace, "the schema moves before the process that expects it"

    between = lines[backup + 1 : migrate]
    assert not between, (
        "something now runs between the backup and the migration. The backup's whole value is "
        f"that nothing is written between it and the schema change:\n  {between}"
    )


def test_the_post_flight_check_still_enters_the_running_container(readme: str) -> None:
    """Guards against fixing this by replacing every `exec` with `run`.

    `deployment_readiness` asks about the process now serving — by then the new
    image — so entering it is exactly right. A one-off container would answer
    about an image that need not be the one gunicorn is running, which is the
    same mistake pointed the other way.
    """
    readiness = [
        line
        for line in command_lines(section(readme, *DEPLOYMENT_SECTION))
        if "manage.py deployment_readiness" in line
    ]
    assert readiness, "the deployment no longer checks readiness afterwards"
    for line in readiness:
        assert "exec" in line, (
            f"readiness asks about the running process, so it enters it\n  {line}"
        )


# -- the release-specific pre-migration audit ------------------------------


def test_the_pre_migration_audit_is_target_image_and_not_unconditional(readme: str) -> None:
    """A target-image audit can ask about the schema *before* it moves.

    That is the whole reason it is worth sequencing: a finding about rows a new
    constraint would reject is cheap to act on while the constraint is not
    installed yet, and expensive afterwards. It has to be the new release's
    check, so `exec` into the old image cannot serve — the old image does not
    have it.

    And it stays conditional. A check for a constraint the target release does
    not contain is a command that either is not in that image or answers a
    question nobody asked, so the runbook describes the shape and the release
    note says whether it applies.
    """
    text = section(readme, *AUDIT_SECTION)

    assert "Most releases have none" in text, "the step reads as unconditional"
    assert "Do not make it unconditional" in text
    assert "release note" in text

    lowered = text.lower()
    assert "no repair" in lowered or "not repair" in lowered or "repairs anything" in lowered, (
        "the step must say plainly that it does not repair what it finds"
    )
    assert "stop" in lowered, "a finding has to stop the deployment, not annotate it"

    for line in command_lines(text):
        assert "run --rm web" in line, (
            f"a pre-migration audit asks the new release's question, so it needs the new "
            f"release's image\n  {line}"
        )
        assert "--skip-storage-scan" in line, (
            f"the pre-migration pass is the cheap relational one; walking the evidence store "
            f"is a maintenance window, not a deployment step\n  {line}"
        )


def test_the_pre_migration_audit_does_not_assume_an_unmerged_release(readme: str) -> None:
    """Main's runbook has to be executable with nothing else merged.

    So the step may describe the shape of a release-specific audit, and may show
    the command, but must not name a migration, a service or a flag that only
    exists on a branch.

    The list is what is *not* on main, so it shrinks as things merge. DATA-001
    and DATA-002 landed (`submissions/0005` and `0006`), and naming them here is
    now naming main — which is why the step may say that a production instance
    which has not yet crossed them owes this audit. `searchindex` and
    `check_search_freshness` are SEARCH-001's, still unmerged, and stay out:
    that branch reconciles its own deployment wording when it lands.
    """
    text = section(readme, *AUDIT_SECTION)
    for absent in ("searchindex", "check_search_freshness"):
        assert absent not in text, (
            f"the runbook names {absent!r}, which is not on main. A runbook that needs an "
            "unmerged branch to be correct is a runbook nobody can follow today."
        )


# -- diagnosing a failed migration -----------------------------------------


def test_the_failed_migration_diagnostic_carries_its_own_identity(recovery: str) -> None:
    """A `run --rm` in a fresh shell resolves whatever the environment says.

    After a failed deployment the operator is frequently in a new session, and
    `run --rm web` with nothing exported resolves the fallback image — some
    other build entirely, answering confidently about this one. So the section
    has to re-establish both variables in the block an operator copies.
    """
    lines = command_lines(section(recovery, *FAILED_MIGRATION_SECTION))

    assert any("manage.py migration_plan" in line for line in lines), (
        "the failed-migration section no longer inspects the migration state"
    )
    for variable in IDENTITY:
        assert any(line.startswith(f"export {variable}=") for line in lines), (
            f"the diagnostic starts a one-off container without exporting {variable}, so it can "
            f"resolve {FALLBACK_IMAGE}"
        )


def test_the_failed_migration_diagnostic_does_not_promise_all_or_nothing(recovery: str) -> None:
    """A release is several migrations, and `migrate` applies them in turn.

    An earlier one can commit while a later one fails, so "this migration is
    still pending" is not "nothing changed". The old text read one line of the
    report and concluded about the whole release; the correction has to keep
    naming the partly-applied case, which is the one that is easy to miss and
    expensive to be wrong about.
    """
    text = section(recovery, *FAILED_MIGRATION_SECTION)
    assert "partly applied" in text
    assert "Some are pending and some are not" in text


# -- one mental model ------------------------------------------------------


def test_both_runbooks_name_the_thing_that_goes_wrong(readme: str, recovery: str) -> None:
    """The rule is stated where each file needs it, in the same terms.

    A reader has one of these open, not both, so the concrete failure — a
    command resolving the fallback tag — has to be named in each.
    """
    for name, text in (("README.md", readme), ("RECOVERY.md", recovery)):
        assert FALLBACK_IMAGE in text, (
            f"{name} no longer names the fallback tag, which is the concrete thing that goes "
            "wrong when the identity is missing"
        )
