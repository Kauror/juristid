# ruff: noqa: F811 - pytest fixtures imported from the sync suites are used as arguments
"""The register replacement's enrolment (docs/adr/0203 §3).

The world is the rebuilt operational cohort of
tests/test_lovable_operational_rebuild_sync.py (enrolled, with baseline
archives, after two pilot nights and one operational night). The replacement
is simulated as the importer's contract describes it: every earlier Teema
retired through the real `delete_matter` (a tombstone; its states, decisions
and archives go with it), its reference released, and the register created as
new Teemad with ``lovable-register`` provenance — some under the same Lovable
ids the retired Teemad had. Synthetic throughout; nothing reaches Lovable.
"""

from __future__ import annotations

import copy
import io
import json
from typing import Any

import pytest
from django.core.management import call_command

from app.legacy_import import lovable_observability as obs
from app.legacy_import import lovable_review, lovable_sync
from app.legacy_import.lovable_pilot import build_plan
from app.legacy_import.lovable_sync import (
    OPERATIONAL_COHORT,
    PILOT_COHORT,
    REGISTER_COHORT,
    SyncRefused,
)
from app.legacy_import.lovable_sync_models import (
    LovableSyncBaselineArchive,
    LovableSyncCohort,
    LovableSyncState,
    SyncOutcome,
    SyncRunStatus,
    SyncTrigger,
)
from app.legacy_import.models import EvidencePilotRecord, ImportBatch, MatterSourceReference
from app.matters.models import Matter
from tests import factories
from tests.test_lovable_operational_cohort import HELD, HOUR, active  # noqa: F401
from tests.test_lovable_operational_rebuild_sync import _supersede, rebuild  # noqa: F401
from tests.test_lovable_pilot import _apply, _package, pilot_on, world  # noqa: F401
from tests.test_lovable_sync import (  # noqa: F401
    NIGHT,
    _decisions,
    _row,
    _run,
    _source_topic,
    cohort,
)

pytestmark = pytest.mark.django_db

DAY = HOUR * 24
REPLACES = [OPERATIONAL_COHORT, PILOT_COHORT]


def _uuid(rows: dict[str, dict[str, Any]], reference: str) -> str:
    return next(r["id"] for r in rows.values() if r["nr"] == reference)


def _replace(world_: dict[str, Any], *, keep: tuple[str, ...] = ()) -> dict[str, Any]:
    """The replacement: retire every synchronised Teema (except ``keep``),
    release its reference, create the register. 601 and 602 are Lovable's topics
    the retired 501 and 502 were; 610 is new."""
    from app.matters.deletion import delete_matter

    source = world_["source"]
    old_ids = {r["nr"]: r["id"] for r in source.rows.values()}
    for state in list(LovableSyncState.objects.select_related("matter")):
        if state.reference in keep:
            continue
        delete_matter(matter=state.matter, actor=None)
        Matter.all_objects.filter(pk=state.matter_id).update(
            reference_year=None, reference_number=None
        )
    topics = [
        _source_topic("2026_601", _row("2026_601"), field_decisions={"hetkeseis": HELD}),
        _source_topic("2026_602", _row("2026_602")),
        _source_topic("2026_610", _row("2026_610")),
    ]
    topics[0]["source"]["id"] = old_ids["2026_501"]
    topics[1]["source"]["id"] = old_ids["2026_502"]
    package = _package(*topics)
    package["snapshot"]["snapshot_sha256"] = "f" * 64
    package["snapshot"]["highest_reference"] = {"2026": 620}
    package["reserve_through"] = {"2026": 620}
    digest = build_plan(package).digest
    _apply(package)
    EvidencePilotRecord.objects.filter(package_sha256=digest).update(pilot=REGISTER_COHORT)
    for topic in topics:
        source.put(topic["source"]["id"], copy.deepcopy(topic["source"]["row"]), version=7)
    return {**world_, "register_digest": digest}


def _enroll(world_: dict[str, Any], **kw: Any) -> lovable_sync.Enrolment:
    arguments = {
        "cohort": REGISTER_COHORT,
        "package_sha256": world_["register_digest"],
        "expect": 3,
        "reason": "Lovable'i register asendab kõik varasemad",
        "replaces": REPLACES,
        **kw,
    }
    return lovable_sync.enroll(**arguments)


@pytest.fixture
def replaced(rebuild):
    """The rebuilt operational cohort, one night over it, then the replacement."""
    _supersede(rebuild)
    night = _run(rebuild, SyncTrigger.SCHEDULED, at=NIGHT + 2 * DAY)
    assert night.status == SyncRunStatus.SUCCEEDED, night.error
    assert LovableSyncBaselineArchive.objects.count() == 4
    return _replace(rebuild)


def test_the_deletion_walk_takes_the_old_states_with_their_teemad(replaced):
    # What `delete_matter` does to the synchronisation's rows today: gone.
    assert not LovableSyncState.objects.exists()
    assert not LovableSyncBaselineArchive.objects.exists()


def test_the_register_replaces_every_earlier_cohort(replaced):
    done = _enroll(replaced)
    assert (done.cohort, done.expected, done.seeded, done.already) == (REGISTER_COHORT, 3, 3, False)
    assert done.import_held_fields == 1
    rows = {row.key: row for row in LovableSyncCohort.objects.all()}
    assert set(rows) == {PILOT_COHORT, OPERATIONAL_COHORT, REGISTER_COHORT}
    assert rows[OPERATIONAL_COHORT].retired_at is not None
    assert replaced["register_digest"] in rows[OPERATIONAL_COHORT].retired_reason
    assert rows[PILOT_COHORT].retired_at is not None  # retired by the rebuild, kept so
    assert rows[REGISTER_COHORT].retired_at is None
    assert lovable_sync.expected_cohorts() == {REGISTER_COHORT: 3}
    assert set(LovableSyncState.objects.values_list("cohort", flat=True)) == {REGISTER_COHORT}
    held = LovableSyncState.objects.get(reference="2026_601").applied["stage"]
    assert held.get("import_held") is True

    again = _enroll(replaced)
    assert again.already and LovableSyncState.objects.count() == 3


def test_the_first_night_compares_the_register_and_shows_it_entering(replaced):
    reader = factories.UserFactory(display_name="Lugeja Näidis")
    _enroll(replaced)
    # Kontrolli muudatusi before the next night: nothing read yet, nothing fails.
    check = lovable_sync.run_sync(trigger=SyncTrigger.CHECK, now=lambda: NIGHT + 2 * DAY + HOUR)
    assert check.status == SyncRunStatus.SUCCEEDED, check.error
    assert (check.counts["checked"], check.counts["not_yet_read"]) == (0, 3)

    night = _run(replaced, SyncTrigger.SCHEDULED, at=NIGHT + 3 * DAY)
    assert night.status == SyncRunStatus.SUCCEEDED, night.error
    assert night.counts["checked"] == 3 and set(night.counts["by_cohort"]) == {REGISTER_COHORT}
    found = {(d.state.reference, d.field, d.outcome, d.reason) for d in night.decisions.all()}
    assert found == {("2026_601", "stage", SyncOutcome.REVIEW_REQUIRED, lovable_sync.IMPORT_HELD)}
    assert _decisions(night, "2026_610") == {}

    history = lovable_review.nightly_history(reader, now=NIGHT + 5 * DAY)
    newest, before = history.nights[0], history.nights[1]
    # The register enters the comparison — 601 and 602 too, though Lovable's
    # topics were in the earlier reading under the retired Teemad.
    assert {t.reference: t.kind for t in newest.topics} == {
        "2026_601": lovable_review.ENROLLED,
        "2026_602": lovable_review.ENROLLED,
        "2026_610": lovable_review.ENROLLED,
    }
    assert newest.changed_topics == 0 and newest.attention == 0
    # The night before the replacement still renders: its Teemad are gone, their
    # changes are counted, never attributed to the register.
    assert before.topics == []
    assert before.run.status == SyncRunStatus.SUCCEEDED
    readings = obs._pilot_readings(reader, NIGHT + 5 * DAY, full=True)
    assert len(readings) == len(history.nights)

    # Status, report and MCP: one cohort, its numbers.
    status = obs.status(reader, now=NIGHT + 5 * DAY)
    assert set(status.cohorts) == {REGISTER_COHORT} and status.per_cohort
    assert status.cohorts[REGISTER_COHORT]["expected"] == 3
    assert status.cohorts[REGISTER_COHORT]["states"] == 3
    assert status.cohort_size == 3
    out = io.StringIO()
    call_command("lovable_sync", "report", "--json", stdout=out)
    report = json.loads(out.getvalue())
    assert report["status"]["cohorts"][REGISTER_COHORT]["checked"] == 3


def test_a_night_before_the_replacement_counts_changes_it_cannot_attribute(rebuild):
    """A source change on a night whose Teemad were replaced since is counted."""
    reader = factories.UserFactory()
    _supersede(rebuild)
    rebuild["source"].edit("2026_503", kellele="Ministeerium")
    _run(rebuild, SyncTrigger.SCHEDULED, at=NIGHT + 2 * DAY)
    world_ = _replace(rebuild)
    _enroll(world_)
    night = next(
        n
        for n in lovable_review.nightly_history(reader, now=NIGHT + 5 * DAY).nights
        if n.run.started_at == NIGHT + 2 * DAY
    )
    assert night.topics == [] and night.gone >= 1
    assert "enam ei sünkroonita" in night.message


@pytest.mark.parametrize(
    ("change", "code"),
    [
        ({"replaces": []}, "REPLACES_REQUIRED"),
        ({"replaces": [PILOT_COHORT]}, "REPLACES_INCOMPLETE"),
        ({"replaces": ["lovable-somewhere"]}, "UNKNOWN_REPLACES"),
        ({"expect": 4}, "COUNT_MISMATCH"),
        ({"supersede": PILOT_COHORT}, "SUPERSEDE_AND_REPLACE"),
    ],
)
def test_the_register_enrolment_refuses_a_wrong_request(replaced, change, code):
    with pytest.raises(SyncRefused) as refused:
        _enroll(replaced, **change)
    assert refused.value.code == code
    assert not LovableSyncCohort.objects.filter(key=REGISTER_COHORT).exists()
    assert LovableSyncCohort.objects.get(key=OPERATIONAL_COHORT).retired_at is None


def test_a_live_state_of_a_replaced_cohort_refuses(rebuild):
    _supersede(rebuild)
    world_ = _replace(rebuild, keep=("2026_503",))
    with pytest.raises(SyncRefused, match="on a live Teema") as refused:
        _enroll(world_)
    assert refused.value.code == "LIVE_STATE_REMAINS"
    assert not LovableSyncCohort.objects.filter(key=REGISTER_COHORT).exists()


def test_a_run_since_the_replacement_refuses(replaced):
    batch = ImportBatch.objects.get(
        pk=MatterSourceReference.objects.filter(source_snapshot_sha256="f" * 64)
        .values_list("import_batch_id", flat=True)
        .first()
    )
    applied_at = batch.finished_at or batch.started_at
    # The operational cohort is still the only active one: a run after the
    # replacement fails its size check, but it ran.
    lovable_sync.run_sync(
        trigger=SyncTrigger.MANUAL,
        transport=replaced["source"],
        now=lambda: applied_at + HOUR,
        pause=lambda _s: None,
    )
    with pytest.raises(SyncRefused, match="Restore the pre-replacement backup set") as refused:
        _enroll(replaced)
    assert refused.value.code == "SYNC_RAN_SINCE_REBUILD"
    assert not LovableSyncCohort.objects.filter(key=REGISTER_COHORT).exists()


def test_a_state_left_on_a_tombstone_refuses_and_nothing_is_written(replaced):
    """The deletion walk removes states today; one left behind with a register
    id must stop the enrolment, never be deleted by it."""
    tombstone = Matter.all_objects.filter(deleted_at__isnull=False).first()
    assert tombstone is not None
    register_id = MatterSourceReference.objects.get(
        source_snapshot_sha256="f" * 64, source_title="Näidisteema 2026_601"
    ).source_row_raw["lovable"]["id"]
    LovableSyncState.objects.create(
        matter=tombstone,
        source_id=register_id,
        reference="2026_501",
        cohort=OPERATIONAL_COHORT,
        applied={},
    )
    cohorts_before = list(LovableSyncCohort.objects.order_by("key").values())
    with pytest.raises(SyncRefused, match="2026_501") as refused:
        _enroll(replaced)
    assert refused.value.code == "STALE_STATE_ON_TOMBSTONE"
    assert "Näidisteema" not in str(refused.value)
    assert list(LovableSyncCohort.objects.order_by("key").values()) == cohorts_before
    assert list(LovableSyncState.objects.values_list("reference", flat=True)) == ["2026_501"]
