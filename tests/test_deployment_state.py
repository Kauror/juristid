"""What the deployment can be asked about itself.

Three commands an operator runs around a release, and the module underneath
them. The questions are ordinary; getting them wrong is not:

* a migration plan read *after* it was applied is a post-mortem, not a gate;
* a readiness check that passes against an unmigrated database is worse than no
  readiness check, because somebody believed it;
* a restore verified by "the site loads" has verified nothing about the twenty
  years of register behind the login page.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

import pytest
from django.core.management import call_command
from django.core.management.base import CommandError
from django.db import migrations, models

from app.core import deployment
from tests import factories

pytestmark = pytest.mark.django_db


# --------------------------------------------------------------------------
# Migrations
# --------------------------------------------------------------------------


def _migration(*operations: object) -> migrations.Migration:
    built = migrations.Migration("0001_synthetic", "core")
    built.operations = list(operations)
    return built


def test_an_additive_migration_needs_no_decision() -> None:
    planned = deployment.consequential_operations(
        _migration(
            migrations.AddField("matter", "extra", models.CharField(max_length=10, default="")),
            migrations.AddIndex("matter", models.Index(fields=["title"], name="synthetic")),
        )
    )
    assert planned == {}


@pytest.mark.parametrize(
    "operation",
    [
        migrations.RemoveField("matter", "title"),
        migrations.DeleteModel("Matter"),
        migrations.RenameField("matter", "title", "pealkiri"),
        migrations.RenameModel("Matter", "Teema"),
        migrations.RunPython(migrations.RunPython.noop),
        migrations.RunSQL("SELECT 1"),
    ],
    ids=lambda operation: type(operation).__name__,
)
def test_an_operation_that_removes_or_rewrites_is_flagged(operation: object) -> None:
    """Not a safety judgement — a prompt for one.

    Every one of these can be exactly right. What none of them is, is something
    to discover after the fact: the deployment sequence leaves the previous
    release serving while migrations run, and these are the operations that can
    stop it working mid-deployment.
    """
    planned = deployment.consequential_operations(_migration(operation))
    assert list(planned) == [type(operation).__name__]
    assert planned[type(operation).__name__]


def test_a_migrated_database_has_nothing_pending_and_nothing_unknown() -> None:
    state = deployment.migration_state()
    assert state.pending == ()
    assert state.unknown == ()
    assert state.is_consistent
    assert state.leaves, "a migrated database should report a leaf per migrated app"


def test_the_leaves_name_the_applications_they_belong_to() -> None:
    """A backup manifest records these, so a restore can be matched to its code."""
    state = deployment.migration_state()
    assert any(leaf.startswith("matters.") for leaf in state.leaves)
    assert all("." in leaf for leaf in state.leaves)


def test_the_plan_command_says_so_when_there_is_nothing_to_do(capsys) -> None:
    call_command("migration_plan")
    assert "No pending migrations." in capsys.readouterr().out


def test_the_plan_command_can_be_a_gate_without_anything_to_stop_for() -> None:
    """`--fail-on-consequential` is for an unattended script.

    With an additive plan it must stay quiet, or a deployment script that uses
    it would fail every release and be removed within a week.
    """
    call_command("migration_plan", "--fail-on-consequential")


# --------------------------------------------------------------------------
# Storage
# --------------------------------------------------------------------------


def test_every_storage_class_is_reported_with_its_recovery_class() -> None:
    """Canonical, rebuildable and source are different obligations.

    The distinction is the backup plan: one must survive, one may be deleted and
    rebuilt from the first, and one is authoritative input that must not be
    writable (docs/adr/0014, docs/adr/0022).
    """
    roots = {root.name: root for root in deployment.storage_roots()}
    assert roots["EVIDENCE_ROOT"].kind == deployment.CANONICAL
    assert roots["LEGACY_SOURCE_ROOT"].kind == deployment.CANONICAL
    assert roots["DERIVATIVE_ROOT"].kind == deployment.REBUILDABLE


def test_the_storage_roots_a_test_process_has_are_healthy() -> None:
    for root in deployment.storage_roots():
        assert root.exists, root.name
        assert root.problem == "", root.problem


def test_a_missing_storage_root_is_a_problem(settings, tmp_path: Path) -> None:
    """The failure that only shows up on restart.

    A container handed an empty directory where a bind mount should be works
    perfectly until it is replaced, at which point everything it wrote is gone.
    """
    settings.EVIDENCE_ROOT = tmp_path / "never-created"
    problems = [root.problem for root in deployment.storage_roots() if root.problem]
    assert any("does not exist" in problem for problem in problems)


def test_a_writable_source_corpus_is_a_problem(settings, tmp_path: Path) -> None:
    """Defence in depth against the importer being wrong about itself.

    The corpus is mounted `:ro` and the importer is written never to write
    there. This is the check that notices when only one of those two is still
    true — a probe of the mount, not of the code that reads it.
    """
    corpus = tmp_path / "source"
    corpus.mkdir()
    settings.HISTORICAL_SOURCE_ROOT = str(corpus)

    roots = {root.name: root for root in deployment.storage_roots()}
    assert roots["HISTORICAL_SOURCE_ROOT"].kind == deployment.SOURCE
    assert "read-only mount" in roots["HISTORICAL_SOURCE_ROOT"].problem


def test_a_deployment_with_no_corpus_is_not_asked_about_one(settings) -> None:
    """A laptop has no historical corpus, and inventing one fails everywhere."""
    settings.HISTORICAL_SOURCE_ROOT = ""
    assert "HISTORICAL_SOURCE_ROOT" not in {root.name for root in deployment.storage_roots()}


# --------------------------------------------------------------------------
# Readiness
# --------------------------------------------------------------------------


def test_a_migrated_deployment_reports_itself_ready() -> None:
    call_command("deployment_readiness", "--quiet")


def test_a_real_data_deployment_must_know_which_commit_it_is(settings) -> None:
    """The one field that used to depend on somebody remembering.

    A container that cannot say what code it is makes every later question —
    which release caused this, what am I rolling back to — unanswerable from the
    system itself.
    """
    settings.REAL_DATA_ALLOWED = True
    settings.APPLICATION_REVISION = "unknown"

    with pytest.raises(CommandError) as failure:
        call_command("deployment_readiness", "--quiet")
    assert "which commit" in str(failure.value)


def test_a_build_that_knows_its_commit_passes(settings) -> None:
    """A real-data build also needs its reference vocabulary.

    The organisations are applied here rather than assumed: policy areas arrive
    with the schema, but the public institutions are operator-seeded, and a
    deployment without them is exactly the state readiness now refuses.
    """
    from app.core.reference_data import apply_reference_plan, build_reference_plan

    apply_reference_plan(expected_sha256=build_reference_plan().digest())

    settings.REAL_DATA_ALLOWED = True
    settings.APPLICATION_REVISION = "36ea5df5b2fc434b68fe0d94d995d1a74ea7cd8f"
    call_command("deployment_readiness", "--quiet")


def test_readiness_fails_closed_on_a_broken_mount(settings, tmp_path: Path) -> None:
    settings.EVIDENCE_ROOT = tmp_path / "never-created"
    with pytest.raises(CommandError) as failure:
        call_command("deployment_readiness", "--quiet")
    assert "EVIDENCE_ROOT" in str(failure.value)


def test_readiness_reports_the_postgresql_major(capsys) -> None:
    """Which is what a restore has to match: a dump is read by a server."""
    # Imported here rather than at module scope: several tests in this file take
    # pytest-django's `settings` fixture, and a module-level import of the same
    # name would read as the fixture to anybody skimming.
    from django.conf import settings

    call_command("deployment_readiness")
    major = settings.MINIMUM_POSTGRESQL_VERSION[0]
    assert re.search(rf"PostgreSQL\s+{major}\.", capsys.readouterr().out)


# --------------------------------------------------------------------------
# Environment hygiene
# --------------------------------------------------------------------------


@pytest.mark.parametrize("value", ["1", "0", "true", "FALSE", "yes", "off", ""])
def test_a_boolean_spelled_a_way_the_reader_knows_is_not_reported(value: str) -> None:
    assert deployment.unparseable_boolean_variables({"REAL_DATA_ALLOWED": value}) == {}


@pytest.mark.parametrize("value", ["enabled", "y", "sure", "TRUE!", "2"])
def test_a_boolean_nobody_can_read_is_reported(value: str) -> None:
    """`config/env.py` reads anything it does not recognise as false.

    That is the safe direction — every flag involved is dangerous only when true
    — and it is silent: `REAL_DATA_ALLOWED=enabled` and `REAL_DATA_ALLOWED=0`
    behave identically and look nothing alike to whoever typed one of them.
    """
    reported = deployment.unparseable_boolean_variables({"REAL_DATA_ALLOWED": value})
    assert reported == {"REAL_DATA_ALLOWED": value}


def test_a_variable_that_is_not_a_flag_is_left_alone() -> None:
    """The check names flags. A secret must never appear in its output."""
    assert deployment.unparseable_boolean_variables({"DJANGO_SECRET_KEY": "hunter2"}) == {}


def test_the_system_check_warns_rather_than_refusing_to_start(monkeypatch) -> None:
    """A warning, because the fallback is already the safe one.

    Refusing to start over a typo in a flag that is off anyway would be a worse
    trade than saying so — and the value is a flag, so naming it leaks nothing.
    """
    from app.core.checks import check_runtime_safety

    monkeypatch.setenv("DEV_LOGIN_ENABLED", "maybe")
    problems = {problem.id: problem for problem in check_runtime_safety(None)}
    assert "juristid.W015" in problems
    assert "DEV_LOGIN_ENABLED" in problems["juristid.W015"].msg


# --------------------------------------------------------------------------
# The recovery fingerprint
# --------------------------------------------------------------------------


def _evidence(title: str = "Sünteetiline tõend") -> tuple[object, bytes]:
    from app.documents.services import add_evidence_version, create_document

    matter = factories.MatterFactory()
    document = create_document(matter=matter, title=title)
    content = b"%PDF-1.4 synthetic evidence for the recovery rehearsal"
    version = add_evidence_version(
        document=document,
        content=content,
        original_filename="synthetic.pdf",
        mime_type="application/pdf",
    )
    return version, content


def test_a_fingerprint_describes_the_canonical_state(tmp_path: Path) -> None:
    _evidence()
    out = tmp_path / "fingerprint.json"
    call_command("recovery_fingerprint", "--out", str(out))

    fingerprint = json.loads(out.read_text(encoding="utf-8"))
    assert fingerprint["canonical_counts"]["matters.Matter"] == 1
    assert fingerprint["evidence"]["version_count"] == 1
    assert fingerprint["evidence"]["bytes_verified"] is True
    assert len(fingerprint["evidence"]["rollup_sha256"]) == 64
    assert fingerprint["postgresql_major"] == 18


def test_a_fingerprint_carries_no_content(tmp_path: Path) -> None:
    """It is meant to be kept beside a backup and copied off the host.

    Counts, digests and schema state only — no titles, no filenames, no bytes.
    Anything else and a fingerprint would be as sensitive as the register.
    """
    _evidence(title="Konfidentsiaalne liikmete tagasiside")
    out = tmp_path / "fingerprint.json"
    call_command("recovery_fingerprint", "--out", str(out))

    text = out.read_text(encoding="utf-8")
    assert "Konfidentsiaalne" not in text
    assert "synthetic.pdf" not in text


def test_the_search_projection_is_reported_and_never_compared(tmp_path: Path) -> None:
    """A correct restore comes back with an empty projection.

    Comparing it would fail every restore that worked, which is how a check
    gets switched off (docs/adr/0014).
    """
    out = tmp_path / "fingerprint.json"
    call_command("recovery_fingerprint", "--out", str(out))
    fingerprint = json.loads(out.read_text(encoding="utf-8"))

    assert "search.SearchDocument" in fingerprint["rebuildable_counts"]
    assert "search.SearchDocument" not in fingerprint["canonical_counts"]


def test_a_pending_search_rebuild_is_not_canonical_drift(tmp_path: Path) -> None:
    """The one that made this a correction rather than a design note.

    A `SearchRebuildDebt` row means "somebody renamed a Valdkond a few seconds
    ago and the worker has not caught up". It is a healthy, self-clearing state
    that a restore verification should have no opinion about — and until the
    debt table was classified, taking a fingerprint, marking a rebuild and
    comparing produced

        canonical_counts.search.SearchRebuildDebt: 0 -> 1

    a non-zero exit and the sentence "The canonical state does not match", from
    the one command whose job is to tell an operator whether the register came
    back. A probe that cries wolf during a correct restore is worse than no
    probe (docs/adr/0041).
    """
    from app.search.freshness import mark_rebuild_owed
    from app.search.models import SearchRebuildReason

    _evidence()
    out = tmp_path / "before.json"
    call_command("recovery_fingerprint", "--out", str(out))

    mark_rebuild_owed(SearchRebuildReason.POLICY_AREA_RENAMED)

    # No CommandError: the canonical state is unchanged, because a queue is not
    # canonical state.
    call_command("recovery_fingerprint", "--compare", str(out))


def test_the_debt_is_reported_even_though_it_is_never_compared(tmp_path: Path) -> None:
    """Not compared is not the same as not looked at.

    An operator reading a fingerprint beside a backup should be able to see that
    the index owed a rebuild at the moment it was taken; what they must not get
    is that fact reported as a difference in the register.
    """
    from app.search.freshness import mark_rebuild_owed
    from app.search.models import SearchRebuildReason

    mark_rebuild_owed(SearchRebuildReason.TAG_RENAMED)
    out = tmp_path / "fingerprint.json"
    call_command("recovery_fingerprint", "--out", str(out))
    fingerprint = json.loads(out.read_text(encoding="utf-8"))

    assert fingerprint["operational_counts"]["search.SearchRebuildDebt"] == 1
    assert "search.SearchRebuildDebt" not in fingerprint["canonical_counts"]
    # And it is not a projection either — nothing rebuilds a debt row.
    assert "search.SearchRebuildDebt" not in fingerprint["rebuildable_counts"]


def test_clearing_the_debt_is_not_canonical_drift_either(tmp_path: Path) -> None:
    """The other direction, which is the one a restore actually takes.

    A dump captured with work outstanding, restored, and then converged by the
    worker, has fewer debt rows than the fingerprint beside it. That must not
    read as rows lost in the restore.
    """
    from app.search.freshness import consume_once, mark_rebuild_owed
    from app.search.models import SearchRebuildDebt, SearchRebuildReason

    _evidence()
    mark_rebuild_owed(SearchRebuildReason.ORGANISATION_RENAMED)
    out = tmp_path / "before.json"
    call_command("recovery_fingerprint", "--out", str(out))

    consume_once()
    assert not SearchRebuildDebt.objects.exists()

    call_command("recovery_fingerprint", "--compare", str(out))


def test_excluding_the_debt_did_not_stop_the_check_noticing_real_loss(tmp_path: Path) -> None:
    """The over-broad-fix guard, and the reason F1 is a classification and not a flag.

    Excluding a model from the comparison is one edit away from excluding the
    register, and a recovery check that passes over a lost Matter is worse than
    useless. So the same fixture that proves a pending rebuild is invisible
    proves an edited Matter is not.
    """
    _evidence()
    matter = factories.MatterFactory(title="Algne pealkiri")
    out = tmp_path / "before.json"
    call_command("recovery_fingerprint", "--out", str(out))

    matter.delete()

    with pytest.raises(CommandError) as failure:
        call_command("recovery_fingerprint", "--compare", str(out))
    assert "matters.Matter" in str(failure.value)


def test_a_fingerprint_taken_before_the_debt_was_classified_still_compares(
    tmp_path: Path,
) -> None:
    """Written for the deploy that introduces this, which is the awkward one.

    An operator fingerprints production, deploys, restores, compares. The file
    in their hand was written by the previous build, so it carries the debt
    table under `canonical_counts` and the new one does not — and a key present
    on one side only is a difference. Dropping operational labels from both
    sides is what makes that comparison work without a `FINGERPRINT_VERSION`
    bump that would refuse the file outright.
    """
    _evidence()
    out = tmp_path / "before.json"
    call_command("recovery_fingerprint", "--out", str(out))

    earlier = json.loads(out.read_text(encoding="utf-8"))
    earlier["canonical_counts"]["search.SearchRebuildDebt"] = 3
    earlier.pop("operational_counts", None)
    out.write_text(json.dumps(earlier), encoding="utf-8")

    call_command("recovery_fingerprint", "--compare", str(out))


def test_an_unchanged_system_matches_its_own_fingerprint(tmp_path: Path) -> None:
    _evidence()
    out = tmp_path / "fingerprint.json"
    call_command("recovery_fingerprint", "--out", str(out))
    call_command("recovery_fingerprint", "--compare", str(out))


def test_a_missing_row_fails_the_comparison(tmp_path: Path) -> None:
    _evidence()
    spare = factories.MatterFactory()
    out = tmp_path / "fingerprint.json"
    call_command("recovery_fingerprint", "--out", str(out))

    spare.delete()

    with pytest.raises(CommandError) as failure:
        call_command("recovery_fingerprint", "--compare", str(out))
    assert "canonical_counts" in str(failure.value)


def test_evidence_whose_bytes_did_not_come_back_is_caught(tmp_path: Path) -> None:
    """The failure a row count cannot see.

    A restored database with an unrestored evidence tree has every row it should
    have, and not one of the files those rows describe.
    """
    version, _ = _evidence()

    from app.documents.services import evidence_storage

    storage = evidence_storage()
    storage.delete(version.storage_key)  # type: ignore[attr-defined]

    with pytest.raises(CommandError) as failure:
        call_command("recovery_fingerprint", "--out", str(tmp_path / "after.json"))
    assert "do not match the hash recorded" in str(failure.value)


def test_evidence_whose_bytes_changed_is_caught(tmp_path: Path) -> None:
    """Immutability is enforced in the database; this proves it on the disk."""
    version, _ = _evidence()

    from django.conf import settings as django_settings

    stored = Path(django_settings.EVIDENCE_ROOT) / version.storage_key  # type: ignore[attr-defined]
    stored.write_bytes(b"something else entirely")

    with pytest.raises(CommandError) as failure:
        call_command("recovery_fingerprint", "--out", str(tmp_path / "after.json"))
    assert "do not match the hash recorded" in str(failure.value)


def test_a_fingerprint_from_a_different_version_is_refused(tmp_path: Path) -> None:
    """Two files that count different things must not be compared.

    Silently comparing them would report differences that are really a change of
    definition, which is the worst possible answer during a recovery.
    """
    out = tmp_path / "fingerprint.json"
    call_command("recovery_fingerprint", "--out", str(out))

    fingerprint = json.loads(out.read_text(encoding="utf-8"))
    fingerprint["fingerprint_version"] = 0
    out.write_text(json.dumps(fingerprint), encoding="utf-8")

    with pytest.raises(CommandError) as failure:
        call_command("recovery_fingerprint", "--compare", str(out))
    assert "do not count the same things" in str(failure.value)


def test_skipping_the_byte_pass_says_that_it_skipped_it(tmp_path: Path) -> None:
    """So nobody reads a cheap fingerprint as an expensive one."""
    _evidence()
    out = tmp_path / "fingerprint.json"
    call_command("recovery_fingerprint", "--skip-evidence-bytes", "--out", str(out))

    fingerprint = json.loads(out.read_text(encoding="utf-8"))
    assert fingerprint["evidence"]["bytes_verified"] is False
    assert fingerprint["evidence"]["objects_verified"] == 0
    assert fingerprint["evidence"]["version_count"] == 1


# --------------------------------------------------------------------------
# The state a deployment starts from
# --------------------------------------------------------------------------
#
# CI migrates from zero, and then steps back and forward again through the
# migrations a release carries, because from zero is the easy direction and the
# one a deployment never takes. Which migrations those are is a judgement, so
# the targets are written down in the workflow rather than derived — and a
# written-down list is a list that goes out of date.
#
# It did. SEARCH-001 added `search/0007` and left the targets where DATA-001 had
# put them, so the one migration that release carried into an existing database
# was never reversed in CI. The instruction to move them was already there, in
# prose, directly above the step. Prose is not a check.

#: The migration each app is stepped back to before the suite migrates forward
#: again, read out of the workflow rather than repeated here.
DEPLOYMENT_STATE_STEP = "And forwards again from the state a deployment is in"

#: Apps this step does not step back through, and the leaf each one had when
#: that was last reviewed.
#:
#: Not a claim about what production has applied — this repository cannot know
#: that. It is a record of what has already been decided about, so that changing
#: an app's leaf forces the decision to be taken again: either the release
#: carries that migration into an existing database, and the workflow steps back
#: through it, or it does not and this record moves. Both are one line; only one
#: of them is silent, and it is the one that is now impossible.
SETTLED_LEAVES = {
    "accounts": "0003_sharedgatethrottle",
    # `audit` is not here any more: 0016 widens the `event_type` choices for
    # Seotud materjalid, and the next release carries that into a database
    # that already has 0015, so CI steps the app back and applies it forwards
    # from exactly that state (docs/adr/0061).
    "core": "0001_initial",
    "documents": "0006_legacy_material_role",
    "intelligence": "0001_initial",
    # `matters` is not here any more: 0012 adds `PersonalScratchpad` and the
    # next release carries it into a database that already has 0011, so CI
    # steps the app back and applies it forwards from exactly that state.
    "organisations": "0001_initial",
    "reporting": "0001_initial",
    # A brand-new app's initial migration has no earlier state to step back
    # to: `migrate related_materials <the one before it>` names nothing, and
    # the step's own grammar cannot say `zero`. A deployment applies it from
    # nothing, which is exactly what the plain `migrate` at the end of that
    # step does — so there is nothing this app could add to it. Recorded here
    # for the same reason `intelligence` above is (docs/adr/0061).
    "related_materials": "0001_initial",
}


def _migration_names(app_label: str) -> list[str]:
    """Every numbered migration an app ships, in order, off the filesystem.

    The filesystem rather than the migration loader, because this asks what the
    repository contains rather than what a particular database has seen.
    """
    directory = Path(deployment.__file__).resolve().parent.parent / app_label / "migrations"
    return sorted(
        path.stem for path in directory.glob("[0-9][0-9][0-9][0-9]_*.py") if path.is_file()
    )


def _apps_with_migrations() -> dict[str, list[str]]:
    root = Path(deployment.__file__).resolve().parent.parent
    return {
        directory.parent.name: _migration_names(directory.parent.name)
        for directory in sorted(root.glob("*/migrations"))
        if _migration_names(directory.parent.name)
    }


def _stepped_back_targets() -> dict[str, str]:
    """`app -> migration number` as the workflow's own step names them."""
    workflow = (
        Path(deployment.__file__).resolve().parent.parent.parent
        / ".github"
        / "workflows"
        / "ci.yml"
    ).read_text(encoding="utf-8")
    assert DEPLOYMENT_STATE_STEP in workflow, (
        f"CI no longer has a step named {DEPLOYMENT_STATE_STEP!r}; this test guards that step "
        "and cannot guard one it cannot find"
    )
    body = workflow[workflow.index(DEPLOYMENT_STATE_STEP) :]
    # The step ends at the next `- name:` entry.
    end = body.find("- name:", len(DEPLOYMENT_STATE_STEP))
    return {
        match.group(1): match.group(2)
        for match in re.finditer(
            r"manage\.py migrate ([a-z_]+) (\d{4})\b", body[: end if end > 0 else None]
        )
    }


def test_ci_steps_back_through_something_at_all() -> None:
    """Guards the guard: an empty parse would make every assertion below vacuous."""
    assert len(_stepped_back_targets()) >= 2


def test_every_stepped_back_target_is_a_migration_that_exists() -> None:
    """A typo names a migration Django will refuse, and the step fails obscurely."""
    available = _apps_with_migrations()
    for app_label, number in _stepped_back_targets().items():
        assert app_label in available, f"CI steps back an app with no migrations: {app_label}"
        assert any(name.startswith(f"{number}_") for name in available[app_label]), (
            f"CI steps {app_label} back to {number}, which is not a migration this repository "
            f"has: {', '.join(available[app_label])}"
        )


def test_stepping_an_app_back_to_its_own_leaf_would_exercise_nothing() -> None:
    """A target that has caught up with its app reverses no migration at all.

    The step would still pass, and would still be listed, and would prove
    nothing — which is the same failure as leaving the app out, wearing the
    costume of the fix.
    """
    available = _apps_with_migrations()
    for app_label, number in _stepped_back_targets().items():
        leaf = available[app_label][-1]
        assert not leaf.startswith(f"{number}_"), (
            f"CI steps {app_label} back to {number}, which is already its leaf. Move the target "
            f"behind the migrations this release carries, or drop the app from the step."
        )


def test_no_app_gains_a_migration_without_a_deployment_state_decision() -> None:
    """The check SEARCH-001 did not have.

    Every app either appears in the workflow's step — in which case the
    migrations after its target are the ones a release carries, and CI reverses
    them — or its leaf is recorded in `SETTLED_LEAVES` as already decided about.
    An app that is in neither has grown a migration since anybody last thought
    about this step, which is exactly the state `search` was in.
    """
    stepped = _stepped_back_targets()
    undecided = {}
    for app_label, names in _apps_with_migrations().items():
        if app_label in stepped:
            continue
        if SETTLED_LEAVES.get(app_label) == names[-1]:
            continue
        undecided[app_label] = names[-1]

    assert not undecided, (
        "these apps' migrations are neither stepped back through by CI's "
        f"{DEPLOYMENT_STATE_STEP!r} step nor recorded as settled: "
        + ", ".join(f"{app} ({leaf})" for app, leaf in sorted(undecided.items()))
        + ". If the next release carries the migration into an existing database, add "
        "`manage.py migrate <app> <the one before it>` to that step. If it does not, move the "
        "leaf in SETTLED_LEAVES. Do not do neither."
    )


def test_settled_leaves_names_no_app_ci_already_steps_back() -> None:
    """One place per app, so the two lists cannot disagree about the same app."""
    overlap = sorted(SETTLED_LEAVES.keys() & _stepped_back_targets().keys())
    assert not overlap, (
        f"{', '.join(overlap)} is both stepped back by CI and recorded as settled; the record "
        "is for apps the step leaves alone"
    )


def test_settled_leaves_names_no_app_that_has_gone_away() -> None:
    """A record of an app nobody ships is a line that can never fail usefully."""
    available = _apps_with_migrations()
    for app_label in SETTLED_LEAVES:
        assert app_label in available, (
            f"SETTLED_LEAVES records {app_label}, which ships no migrations; remove the line"
        )
