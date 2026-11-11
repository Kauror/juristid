# ruff: noqa: F811 - pytest fixtures imported from the sync suites are used as arguments
"""The rebuilt operational cohort supersedes the pilot in place (docs/adr/0202 §6).

The pilot is the synthetic four of tests/test_lovable_sync.py; the created
Teemad are those of tests/test_lovable_operational_cohort.py. The rebuild's
REBASE provenance — a ``REBASE`` record per pilot Teema, a new canonical
`MatterSourceReference` in the package's import batch and a ``NEXT_ACTION``
record for the step after the rebase — is written here directly, in the shape
the importer's contract names, because the sync must work from that shape
alone. Every title, name and link is invented; nothing reaches Lovable.
"""

from __future__ import annotations

import copy
import io
from typing import Any

import pytest
from django.core.management import call_command

from app.core.errors import ImmutableRecordError
from app.legacy_import import lovable_observability as obs
from app.legacy_import import lovable_review, lovable_sync
from app.legacy_import.lovable_sync import OPERATIONAL_COHORT, PILOT_COHORT, SyncRefused
from app.legacy_import.lovable_sync_models import (
    LovableSyncBaselineArchive,
    LovableSyncCohort,
    LovableSyncDecision,
    LovableSyncRun,
    LovableSyncState,
    SyncOutcome,
    SyncRunStatus,
    SyncTrigger,
)
from app.legacy_import.models import EvidencePilotRecord, MatterSourceReference
from app.matters.models import Matter
from app.workflow.enums import ActionStatus
from app.workflow.models import NextAction
from tests import factories
from tests.test_lovable_operational_cohort import HELD, HOUR, active  # noqa: F401
from tests.test_lovable_pilot import pilot_on, world  # noqa: F401
from tests.test_lovable_sync import NIGHT, _decisions, _matter, _run, cohort  # noqa: F401

pytestmark = pytest.mark.django_db

DAY = HOUR * 24
REBUILD_SNAPSHOT = "e" * 64
PILOT_REFERENCES = ("2026_501", "2026_502", "2026_503", "2026_505")
#: What the reviewer held per rebased topic (Lovable columns).
REBASE_DECISIONS = {
    "2026_501": {"hetkeseis": HELD, "kellele": HELD},
    "2026_502": {},
    "2026_503": {},
    "2026_505": {"teema": "APPLIED"},
}


def _state_dump(pks: list[Any]) -> dict[str, dict[str, Any]]:
    return {
        str(row["pk"]): row
        for row in LovableSyncState.objects.filter(pk__in=pks).values(
            "pk",
            "cohort",
            "applied",
            "applied_version",
            "applied_snapshot_sha256",
            "observed",
            "observed_version",
            "observed_run",
        )
    }


def _ledger() -> tuple[list[Any], list[Any]]:
    decisions = list(
        LovableSyncDecision.objects.order_by("pk").values_list(
            "pk", "run_id", "state_id", "field", "outcome", "reason", "baseline", "source"
        )
    )
    runs = list(LovableSyncRun.objects.order_by("pk").values_list("pk", "status", "counts"))
    return decisions, runs


def _write_rebase(
    world_: dict[str, Any],
    *,
    skip: tuple[str, ...] = (),
    decisions: dict[str, dict[str, str]] | None = None,
    no_step: tuple[str, ...] = (),
) -> None:
    """The importer's REBASE output for every pilot Teema (contract of ADR 0202 §6).

    ``no_step``: Teemad whose step the rebuild did not write (a lawyer's step it
    kept), so it records no ``NEXT_ACTION`` for them."""
    held = {**REBASE_DECISIONS, **(decisions or {})}
    digest = world_["digest"]
    created = EvidencePilotRecord.objects.filter(
        pilot=OPERATIONAL_COHORT, package_sha256=digest, record_kind="MATTER"
    ).first()
    assert created is not None
    batch = MatterSourceReference.objects.get(matter_id=created.matter_id).import_batch
    rows = {row["nr"]: row for row in world_["source"].rows.values()}
    # What the rebuild wrote, by its own records: 505's title and 501's owner.
    written = {
        "2026_505": [("TITLE", "matters.Matter", _matter("2026_505").pk)],
        "2026_501": [("OWNER", "accounts.User", world_["world"]["jaan"].pk)],
    }
    for reference in PILOT_REFERENCES:
        if reference in skip:
            continue
        matter = _matter(reference)
        row = copy.deepcopy(rows[reference])
        source_id, version = row.pop("id"), row.pop("version")
        new_reference = MatterSourceReference.objects.create(
            matter=matter,
            import_batch=batch,
            source_system="LOVABLE",
            source_file_name="public.topic",
            source_snapshot_sha256=REBUILD_SNAPSHOT,
            source_sheet="public.topic:2026",
            source_row_number=matter.reference_number,
            source_row_raw={
                "lovable": {"id": source_id, "version": version},
                "row": row,
                "commencements": [],
                "field_decisions": held[reference],
                "review_notes": [],
            },
            source_title=row["teema"],
            onenote_url=row.get("onenote_link") or "",
            source_contract_version="juristid.lovable-pilot/2",
            match_method="REFERENCE_TOKEN",
            conflict_state="NONE",
        )
        # The record names its new reference; 503's names only the Teema, so the
        # package's batch is what finds its reference (the fallback).
        named = reference != "2026_503"
        EvidencePilotRecord.objects.create(
            pilot=OPERATIONAL_COHORT,
            pilot_version="2.0",
            package_sha256=digest,
            record_key=f"{reference}:rebase",
            record_kind="REBASE",
            matter=matter,
            target_model="legacy_import.MatterSourceReference" if named else "matters.Matter",
            target_id=new_reference.pk if named else matter.pk,
        )
        for kind, model, target in written.get(reference, ()):
            EvidencePilotRecord.objects.create(
                pilot=OPERATIONAL_COHORT,
                pilot_version="2.0",
                package_sha256=digest,
                record_key=f"{reference}:{kind.lower()}",
                record_kind=kind,
                matter=matter,
                target_model=model,
                target_id=target,
            )
        step = NextAction.objects.filter(matter=matter, status=ActionStatus.OPEN).first()
        if step is not None and reference not in no_step:
            EvidencePilotRecord.objects.create(
                pilot=OPERATIONAL_COHORT,
                pilot_version="2.0",
                package_sha256=digest,
                record_key=f"{reference}:step",
                record_kind="NEXT_ACTION",
                matter=matter,
                target_model="workflow.NextAction",
                target_id=step.pk,
            )


@pytest.fixture
def rebuild(active):
    """Two pilot nights (the second with a held change), then the rebuild: Lovable
    moved 501's stage and owner and 505's title, a lawyer retitled 502 before it,
    and the rebuild wrote 505's title and 501's owner (B was A) and held 501's
    stage and addressee."""
    return _rebuild_world(active)


def _rebuild_world(active: dict[str, Any], before: Any = None, **rebase: Any) -> dict[str, Any]:
    """The `rebuild` fixture's world; ``before`` runs just before the rebuild is
    written, ``rebase`` goes to `_write_rebase`."""
    source = active["source"]
    assert _run(active, SyncTrigger.SCHEDULED, at=NIGHT).status == SyncRunStatus.SUCCEEDED
    source.edit("2026_501", kellele="Ministeerium")
    assert _run(active, SyncTrigger.SCHEDULED, at=NIGHT + DAY).status == SyncRunStatus.SUCCEEDED
    source.edit("2026_501", hetkeseis="Valitsuses")
    source.edit("2026_501", vastutaja="Jaan")
    source.edit("2026_505", teema="Uus 505")
    Matter.all_objects.filter(pk=_matter("2026_502").pk).update(title="Juristi pealkiri")
    Matter.all_objects.filter(pk=_matter("2026_505").pk).update(title="Uus 505")
    Matter.all_objects.filter(pk=_matter("2026_501").pk).update(owner=active["world"]["jaan"])
    if before is not None:
        before(active)
    _write_rebase(active, **rebase)
    return active


def _supersede(world_: dict[str, Any], **kw: Any) -> lovable_sync.Enrolment:
    arguments = {
        "cohort": OPERATIONAL_COHORT,
        "package_sha256": world_["digest"],
        "expect": 7,
        "reason": "Operatiivne register ühest hetktõmmisest",
        "supersede": PILOT_COHORT,
        **kw,
    }
    return lovable_sync.enroll(**arguments)


def _pilot_pks() -> list[Any]:
    return list(LovableSyncState.objects.filter(cohort=PILOT_COHORT).values_list("pk", flat=True))


# ---------------------------------------------------------------------------
# Enrolment with --supersede
# ---------------------------------------------------------------------------


def test_the_rebuild_archives_each_pilot_baseline_and_reseeds_the_same_row(rebuild):
    pks = _pilot_pks()
    assert len(pks) == 4
    before = _state_dump(pks)
    ledger = _ledger()

    done = _supersede(rebuild)
    assert (done.seeded, done.reseeded, done.superseded) == (7, 4, PILOT_COHORT)
    assert not done.already

    archives = {str(a.state_id): a for a in LovableSyncBaselineArchive.objects.all()}
    assert set(archives) == set(before)
    for pk, old in before.items():
        archive = archives[pk]
        assert archive.applied == old["applied"]
        assert archive.observed == old["observed"]
        assert archive.observed_version == old["observed_version"]
        assert archive.observed_run_id == old["observed_run"]
        assert archive.applied_version == old["applied_version"]
        assert archive.applied_snapshot_sha256 == old["applied_snapshot_sha256"]
        assert (archive.cohort_before, archive.cohort_after) == (PILOT_COHORT, OPERATIONAL_COHORT)
        assert archive.package_sha256 == rebuild["digest"]
        assert archive.matter_id == LovableSyncState.objects.get(pk=pk).matter_id
    with pytest.raises(ImmutableRecordError, match="Arhiveeritud lähtekohta ei muudeta"):
        next(iter(archives.values())).save()

    # The same rows, re-seeded from the rebuild: A is the snapshot.
    after = _state_dump(pks)
    assert set(after) == set(before)
    assert {row["cohort"] for row in after.values()} == {OPERATIONAL_COHORT}
    assert all(row["observed"] == {} and row["observed_run"] is None for row in after.values())
    assert {row["applied_snapshot_sha256"] for row in after.values()} == {REBUILD_SNAPSHOT}
    s501 = LovableSyncState.objects.get(reference="2026_501").applied
    assert s501["stage"] == {"source": "Valitsuses", "import_held": True}
    assert s501["addressee"] == {"source": "Ministeerium", "import_held": True}
    s505 = LovableSyncState.objects.get(reference="2026_505").applied
    assert s505["title"] == {"source": "Uus 505", "target": "Uus 505"}
    # 502: the source did not move, so what the pilot import wrote stays the target
    # and the lawyer's title is still a local value.
    s502 = LovableSyncState.objects.get(reference="2026_502").applied
    old502 = next(r for r in before.values() if r["applied"]["title"]["source"].endswith("502"))
    assert s502["title"] == old502["applied"]["title"]
    # The step is the one the rebuild's NEXT_ACTION record names.
    step = NextAction.objects.get(matter=_matter("2026_505"), status=ActionStatus.OPEN)
    assert LovableSyncState.objects.get(reference="2026_505").applied["next_action"]["target"][
        "id"
    ] == str(step.pk)

    # The ledger is untouched; the pilot is retired; one cohort is expected.
    assert _ledger() == ledger
    pilot = LovableSyncCohort.objects.get(key=PILOT_COHORT)
    assert pilot.retired_at is not None and rebuild["digest"] in pilot.retired_reason
    assert pilot.expected == 4
    assert lovable_sync.expected_cohorts() == {OPERATIONAL_COHORT: 7}
    assert LovableSyncState.objects.filter(cohort=OPERATIONAL_COHORT).count() == 7


def test_a_rebuild_that_misses_one_pilot_teema_is_refused(rebuild):
    EvidencePilotRecord.objects.filter(record_kind="REBASE", record_key="2026_503:rebase").delete()
    before = _state_dump(_pilot_pks())
    with pytest.raises(SyncRefused, match="not rebased by this package") as refused:
        _supersede(rebuild, expect=6)
    assert refused.value.code == "SUPERSEDE_INCOMPLETE"
    assert not LovableSyncCohort.objects.exists()
    assert not LovableSyncBaselineArchive.objects.exists()
    assert _state_dump(_pilot_pks()) == before


def test_without_supersede_rebased_teemad_are_already_synchronised(rebuild):
    with pytest.raises(SyncRefused, match="already synchronised") as refused:
        _supersede(rebuild, supersede="")
    assert refused.value.code == "ALREADY_SYNCHRONISED"
    assert not LovableSyncCohort.objects.exists()


def test_only_the_pilot_can_be_superseded(rebuild):
    with pytest.raises(SyncRefused, match="cannot be superseded") as refused:
        _supersede(rebuild, supersede=OPERATIONAL_COHORT)
    assert refused.value.code == "UNKNOWN_SUPERSEDE"


def test_the_same_rebuild_enrolment_again_changes_nothing(rebuild):
    _supersede(rebuild)
    states = _state_dump(list(LovableSyncState.objects.values_list("pk", flat=True)))
    again = _supersede(rebuild)
    assert again.already and again.seeded == 7
    assert LovableSyncBaselineArchive.objects.count() == 4
    assert _state_dump(list(LovableSyncState.objects.values_list("pk", flat=True))) == states


def test_the_command_supersedes_and_prints_counts_only(rebuild):
    out = io.StringIO()
    call_command(
        "lovable_sync",
        "enroll",
        "--cohort",
        OPERATIONAL_COHORT,
        "--package-sha256",
        rebuild["digest"],
        "--expect",
        "7",
        "--reason",
        "ümberehitus",
        "--supersede",
        PILOT_COHORT,
        stdout=out,
    )
    assert out.getvalue().strip() == (
        "enrolled: cohort=lovable-operational expected=7 seeded=7 import_held_fields=7"
        " reseeded=4 superseded=lovable-pilot"
    )


# ---------------------------------------------------------------------------
# After the rebuild
# ---------------------------------------------------------------------------


def test_a_check_before_the_next_night_reports_nothing_spurious(rebuild):
    _supersede(rebuild)
    check = lovable_sync.run_sync(trigger=SyncTrigger.CHECK, now=lambda: NIGHT + DAY + HOUR)
    assert check.status == SyncRunStatus.SUCCEEDED, check.error
    assert (check.counts["checked"], check.counts["not_yet_read"]) == (0, 7)
    assert set(check.counts["by_cohort"]) == {OPERATIONAL_COHORT}
    assert not check.decisions.exists()


def test_the_next_night_compares_one_cohort_against_the_new_baseline(rebuild):
    _supersede(rebuild)
    night = _run(rebuild, SyncTrigger.SCHEDULED, at=NIGHT + 2 * DAY)
    assert night.status == SyncRunStatus.SUCCEEDED, night.error
    assert night.counts["checked"] == 7
    assert set(night.counts["by_cohort"]) == {OPERATIONAL_COHORT}
    found = {
        (d.state.reference, d.field, d.outcome, d.reason)
        for d in LovableSyncDecision.objects.filter(run=night).select_related("state")
    }
    rebased = {f for f in found if f[0] in PILOT_REFERENCES}
    assert rebased == {
        # Source authority (docs/adr/0213 §10): an import hold is no longer a
        # person's call; only what cannot be represented stays held.
        ("2026_501", "stage", SyncOutcome.REVIEW_REQUIRED, lovable_sync.STAGE_UNMAPPED),
        # A lawyer's value the rebuild kept is reported, never silently converged.
        ("2026_502", "title", SyncOutcome.LOCAL_TEST_CHANGE, ""),
    }
    # Every other finding is a created Teema's technical hold or first convergence.
    assert lovable_sync.IMPORT_HELD not in {f[3] for f in found - rebased}
    assert _decisions(night, "2026_505") == {}

    # The status and the report show the one rebuilt cohort.
    status = obs.status(factories.UserFactory(), now=NIGHT + 3 * DAY)
    assert set(status.cohorts) == {OPERATIONAL_COHORT} and status.per_cohort
    assert status.cohorts[OPERATIONAL_COHORT]["states"] == 7
    assert any(i.key == "cohort_lovable_operational" for i in status.indicators)
    assert any("Operatiivne register" in line for line in lovable_sync.summary_lines(night))


def test_uuendused_reads_the_nights_before_and_after_the_rebuild(rebuild):
    reader = factories.UserFactory(display_name="Lugeja Näidis")
    _supersede(rebuild)
    _run(rebuild, SyncTrigger.SCHEDULED, at=NIGHT + 2 * DAY)
    history = lovable_review.nightly_history(reader, now=NIGHT + 4 * DAY)
    after, before = history.nights[0], history.nights[1]

    # Before the rebuild: the pilot as it was, its decision on the same state row.
    [changed] = before.topics
    assert (changed.reference, changed.kind) == ("2026_501", "CHANGED")
    [addressee] = changed.fields
    assert (addressee.name, addressee.result) == ("addressee", lovable_review.REVIEW)

    # After: created Teemad enter the comparison; rebased ones are compared.
    kinds = {t.reference: t.kind for t in after.topics}
    assert {r for r, k in kinds.items() if k == lovable_review.ENROLLED} == {
        "2026_510",
        "2026_511",
        "2026_512",
    }
    assert kinds["2026_501"] == "CHANGED" and kinds["2026_505"] == "CHANGED"
    fields = {
        (t.reference, f.name): f for t in after.topics if t.kind == "CHANGED" for f in t.fields
    }
    assert fields[("2026_501", "stage")].result == lovable_review.REVIEW
    title = fields[("2026_505", "title")]
    assert (title.result, title.note) == (lovable_review.SAME, lovable_review.REBASED_NOTE)
    assert after.enrolled == 3


def test_a_lawyer_edit_between_the_apply_and_the_enrolment_stays_local(rebuild):
    """The targets are what the rebuild wrote, by its records — never Juristid's
    value read at enrolment."""
    mari = rebuild["world"]["mari"]
    Matter.all_objects.filter(pk=_matter("2026_501").pk).update(owner=mari)
    Matter.all_objects.filter(pk=_matter("2026_505").pk).update(title="Juristi 505")
    _supersede(rebuild)
    owner = LovableSyncState.objects.get(reference="2026_501").applied["owner"]
    assert owner == {"source": "Jaan", "target": str(rebuild["world"]["jaan"].pk)}
    title = LovableSyncState.objects.get(reference="2026_505").applied["title"]
    assert title == {"source": "Uus 505", "target": "Uus 505"}
    night = _run(rebuild, SyncTrigger.SCHEDULED, at=NIGHT + 2 * DAY)
    assert night.status == SyncRunStatus.SUCCEEDED, night.error
    assert _decisions(night, "2026_501")["owner"].outcome == SyncOutcome.LOCAL_TEST_CHANGE
    assert _decisions(night, "2026_505")["title"].outcome == SyncOutcome.LOCAL_TEST_CHANGE


def test_without_a_written_record_a_genuine_convergence_is_the_target(rebuild):
    """Nothing written by the rebuild, and Juristid already says what the snapshot
    says: that is a convergence, as ALREADY_CONVERGED would record it — never a
    permanent false local change."""
    EvidencePilotRecord.objects.filter(record_kind__in=("TITLE", "OWNER")).delete()
    _supersede(rebuild)
    title = LovableSyncState.objects.get(reference="2026_505").applied["title"]
    assert title == {"source": "Uus 505", "target": "Uus 505"}
    owner = LovableSyncState.objects.get(reference="2026_501").applied["owner"]
    assert owner["target"] == str(rebuild["world"]["jaan"].pk)
    night = _run(rebuild, SyncTrigger.SCHEDULED, at=NIGHT + 2 * DAY)
    assert night.status == SyncRunStatus.SUCCEEDED, night.error
    assert "title" not in _decisions(night, "2026_505")
    assert "owner" not in _decisions(night, "2026_501")


def test_without_a_written_record_a_different_local_value_keeps_the_pilot_target(rebuild):
    EvidencePilotRecord.objects.filter(record_kind__in=("TITLE", "OWNER")).delete()
    Matter.all_objects.filter(pk=_matter("2026_505").pk).update(title="Juristi 505")
    Matter.all_objects.filter(pk=_matter("2026_501").pk).update(owner=rebuild["world"]["mari"])
    _supersede(rebuild)
    title = LovableSyncState.objects.get(reference="2026_505").applied["title"]
    assert title == {"source": "Uus 505", "target": "Näidisteema 2026_505"}
    owner = LovableSyncState.objects.get(reference="2026_501").applied["owner"]
    assert owner["target"] == str(rebuild["world"]["mari"].pk)  # the pilot's own target
    night = _run(rebuild, SyncTrigger.SCHEDULED, at=NIGHT + 2 * DAY)
    assert _decisions(night, "2026_505")["title"].outcome == SyncOutcome.LOCAL_TEST_CHANGE


def _lawyer_step(world_: dict[str, Any]) -> None:
    """A lawyer replaces 505's step before the rebuild."""
    import datetime as dt

    from app.workflow.services import set_next_action

    set_next_action(
        matter=_matter("2026_505"),
        text="Juristi oma samm",
        target_date=dt.date(2026, 12, 1),
        actor=world_["world"]["jaan"],
    )


def _pilot_step_id(reference: str) -> str:
    record = EvidencePilotRecord.objects.get(
        pilot=PILOT_COHORT, matter=_matter(reference), record_kind="NEXT_ACTION"
    )
    return str(record.target_id)


def test_a_kept_lawyer_step_with_no_rebuild_record_stays_local(active):
    """C = A, B ≠ A: the rebuild keeps the lawyer's step and records no step; the
    baseline is the pilot's own step, so the lawyer's reads as a local change."""
    _rebuild_world(active, before=_lawyer_step, no_step=("2026_505",))
    _supersede(active)
    step = LovableSyncState.objects.get(reference="2026_505").applied["next_action"]
    assert step["target"]["id"] == _pilot_step_id("2026_505")
    night = _run(active, SyncTrigger.SCHEDULED, at=NIGHT + 2 * DAY)
    assert night.status == SyncRunStatus.SUCCEEDED, night.error
    found = _decisions(night, "2026_505")
    assert found["next_action_text"].outcome == SyncOutcome.LOCAL_TEST_CHANGE
    assert found["next_action_date"].outcome == SyncOutcome.LOCAL_TEST_CHANGE


def test_a_keep_local_step_with_no_rebuild_record_converges_on_lovable(active):
    """C ≠ A, B ≠ A, KEEP_LOCAL: the baseline is the pilot's step, never the
    lawyer's; under source authority (docs/adr/0213 §10) Lovable's new wording
    then replaces the lawyer's step (planned on this night's mode)."""

    def before(world_: dict[str, Any]) -> None:
        _lawyer_step(world_)
        world_["source"].edit("2026_505", jargmiseks="Lovable'i uus sõnastus")

    _rebuild_world(
        active,
        before=before,
        no_step=("2026_505",),
        decisions={"2026_505": {"teema": "APPLIED", "jargmiseks": HELD}},
    )
    _supersede(active)
    applied = LovableSyncState.objects.get(reference="2026_505").applied
    assert applied["next_action"]["target"]["id"] == _pilot_step_id("2026_505")
    assert applied["next_action_text"]["import_held"] is True
    night = _run(active, SyncTrigger.SCHEDULED, at=NIGHT + 2 * DAY)
    found = _decisions(night, "2026_505")
    for name in ("next_action_text", "next_action_date"):
        assert found[name].outcome in (SyncOutcome.ELIGIBLE, SyncOutcome.APPLIED)
        assert found[name].reason == ""


def test_a_night_reconciled_after_the_rebuild_refuses_the_enrolment(rebuild):
    from app.legacy_import.models import ImportBatch

    batch = ImportBatch.objects.get(
        pk=MatterSourceReference.objects.filter(source_snapshot_sha256=REBUILD_SNAPSHOT)
        .values_list("import_batch_id", flat=True)
        .first()
    )
    applied_at = batch.finished_at or batch.started_at
    # A check after the rebuild compares, but converges nothing: no refusal.
    check = lovable_sync.run_sync(trigger=SyncTrigger.CHECK, now=lambda: applied_at + HOUR)
    assert check.status == SyncRunStatus.SUCCEEDED, check.error
    run = _run(rebuild, at=applied_at + 2 * HOUR)
    assert run.status == SyncRunStatus.SUCCEEDED, run.error
    before = _state_dump(_pilot_pks())
    with pytest.raises(SyncRefused, match="Restore the pre-rebuild backup set") as refused:
        _supersede(rebuild)
    assert refused.value.code == "SYNC_RAN_SINCE_REBUILD"
    assert not LovableSyncCohort.objects.exists()
    assert not LovableSyncBaselineArchive.objects.exists()
    assert _state_dump(_pilot_pks()) == before


def test_a_rebased_teema_can_still_be_deleted_permanently(rebuild):
    from app.matters.deletion import permanently_delete_matter, plan_matter_deletion

    _supersede(rebuild)
    doomed = _matter("2026_502")
    state = LovableSyncState.objects.get(matter=doomed)
    others = sorted(
        str(pk)
        for pk in LovableSyncBaselineArchive.objects.exclude(state=state).values_list(
            "pk", flat=True
        )
    )
    states_before = _state_dump(
        list(LovableSyncState.objects.exclude(pk=state.pk).values_list("pk", flat=True))
    )
    assert LovableSyncBaselineArchive.objects.filter(state=state).count() == 1

    head = factories.DepartmentHeadFactory()
    permanently_delete_matter(
        matter=doomed, actor=head, confirmation=plan_matter_deletion(doomed).digest
    )
    assert not Matter.objects.filter(pk=doomed.pk).exists()
    assert not LovableSyncState.objects.filter(pk=state.pk).exists()
    assert not LovableSyncBaselineArchive.objects.filter(state_id=state.pk).exists()
    assert (
        sorted(str(pk) for pk in LovableSyncBaselineArchive.objects.values_list("pk", flat=True))
        == others
    )
    assert _state_dump(list(states_before)) == states_before
