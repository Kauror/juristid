"""The nightly Lovable synchronisation (docs/adr/0192), on synthetic data.

The cohort is built the way production's was: a synthetic pilot package applied
through `lovable_pilot` (tests/test_lovable_pilot.py), so A — the baseline — is
read from the same immutable provenance the real run reads. Lovable is a fake
transport serving invented rows. Every title, name and link is invented.
"""

from __future__ import annotations

import copy
import datetime as dt
from typing import Any

import pytest
from django.db import connections

from app.audit.models import ChangeEvent
from app.documents.models import Document
from app.intelligence.models import MatterImportantDate
from app.legacy_import import lovable_source, lovable_sync
from app.legacy_import.lovable_source import SourceError
from app.legacy_import.lovable_sync import SyncRefused, run_key_for, run_sync
from app.legacy_import.lovable_sync_models import (
    LovableSyncControl,
    LovableSyncDecision,
    LovableSyncRun,
    LovableSyncState,
    SyncMode,
    SyncOutcome,
    SyncRunStatus,
    SyncTrigger,
)
from app.legacy_import.models import MatterSourceReference
from app.matters.models import Matter
from app.submissions.models import Submission
from app.workflow.enums import ActionStatus
from app.workflow.models import NextAction
from tests import factories
from tests.test_lovable_pilot import URL, _apply, _package, _topic, pilot_on, world  # noqa: F401

TALLINN = lovable_sync.TALLINN
NIGHT = dt.datetime(2026, 10, 28, 0, 15, tzinfo=TALLINN)
NEW_URL = "https://example.invalid/sites/naidis/_layouts/Doc.aspx?sourcedoc={BBBB}"


def _row(reference: str, **values: Any) -> dict[str, Any]:
    return {
        "nr": reference,
        "aasta": int(reference[:4]),
        "teema": f"Näidisteema {reference}",
        "vastutaja": "Mari",
        "uus_vastutaja": "",
        "hetkeseis": "Riigikogus",
        "jargmiseks": "ootan 2. lugemist",
        "tegevuse_tahtaeg": "2026-11-15",
        "arvamuse_tahtaeg": None,
        "valja": "2026-01-20",
        "valja_kuupaev": None,
        "kellele": "",
        "koja_ettepanek": False,
        "oigusakt": "",
        "sisse": "2026-01-05",
        "onenote_link": URL,
        "operatiivne_ulatus": True,
        **values,
    }


def _source_topic(reference: str, row: dict[str, Any], **overrides: Any) -> dict[str, Any]:
    topic = _topic(reference, **overrides)
    topic["source"]["row"] = row
    return topic


def _do_step(date: str = "2026-11-20") -> dict[str, Any]:
    return {
        "review": "READY",
        "kind": "DO",
        "date_semantics": "DEADLINE",
        "target_date": date,
        "date_precision": "EXACT",
        "text": "Saadan märkused",
        "source_text": "Saadan märkused",
        "treatment": "SOURCE_NEXT_STEP",
        "reason": "DO",
    }


@pytest.fixture
def cohort(world, pilot_on, monkeypatch):  # noqa: F811
    """Four pilot Teemad: a WAIT (501), a DO (505) owned by Jaan, a drafting
    step (502, OPINION_DRAFTING) and a finished file (503)."""
    rows = {
        "2026_501": _row("2026_501"),
        "2026_502": _row(
            "2026_502",
            vastutaja="Jaan",
            jargmiseks="Koostan arvamust.",
            tegevuse_tahtaeg="2026-11-12",
        ),
        "2026_503": _row("2026_503", hetkeseis="jõustunud", jargmiseks="", tegevuse_tahtaeg=None),
        "2026_505": _row(
            "2026_505",
            vastutaja="Jaan",
            jargmiseks="Saadan märkused",
            tegevuse_tahtaeg="2026-11-20",
        ),
    }
    full = _package()
    by_ref = {t["reference"]: t for t in full["topics"]}
    topics = [
        _source_topic("2026_501", rows["2026_501"]),
        {**by_ref["2026_502"], "source": {**by_ref["2026_502"]["source"], "row": rows["2026_502"]}},
        {**by_ref["2026_503"], "source": {**by_ref["2026_503"]["source"], "row": rows["2026_503"]}},
        _source_topic("2026_505", rows["2026_505"], owner_raw="Jaan", next_action=_do_step()),
    ]
    package = _package(*topics)
    package["snapshot"]["highest_reference"] = {"2026": 520}
    _apply(package)
    monkeypatch.setattr(lovable_sync, "EXPECTED_COHORT", 4)
    lovable_sync.set_mode(SyncMode.APPLY, reason="test")
    source = FakeLovable()
    for topic in topics:
        source.put(topic["source"]["id"], copy.deepcopy(topic["source"]["row"]), version=3)
    return {"source": source, "world": world, "topics": topics}


class FakeLovable:
    """Lovable's PostgREST as the sync reads it: the allowlisted tables only."""

    def __init__(self) -> None:
        self.rows: dict[str, dict[str, Any]] = {}
        self.commencements: list[dict[str, Any]] = []
        self.fail: Exception | None = None
        self.on_select: Any = None

    def put(self, topic_id: str, row: dict[str, Any], *, version: int) -> None:
        self.rows[topic_id] = {**row, "id": topic_id, "version": version}

    def edit(self, reference: str, **values: Any) -> None:
        for row in self.rows.values():
            if row["nr"] == reference:
                row.update(values)
                row["version"] += 1
                return
        raise KeyError(reference)

    def select(
        self, table: str, columns: Any, *, filters: dict[str, str], order: str
    ) -> list[dict[str, Any]]:
        if self.fail is not None:
            raise self.fail
        if self.on_select is not None:
            self.on_select(table)
        wanted = None
        for value in filters.values():
            wanted = set(value[4:-1].split(","))
        if table == "topic":
            rows = [r for r in self.rows.values() if wanted is None or r["id"] in wanted]
            return [{c: r.get(c) for c in columns} for r in sorted(rows, key=lambda r: r["id"])]
        if table == "topic_commencement":
            return [dict(r) for r in self.commencements if r["topic_id"] in (wanted or set())]
        return []


def _run(
    cohort: dict[str, Any],
    trigger: str = SyncTrigger.MANUAL,
    *,
    at: dt.datetime | None = None,
    **kw: Any,
) -> LovableSyncRun:
    moment = at or NIGHT
    return run_sync(
        trigger=trigger,
        transport=cohort["source"],
        # A set taken five minutes before the run, as the host script takes one.
        backup_set=kw.pop(
            "backup_set",
            (moment - dt.timedelta(minutes=5)).astimezone(dt.UTC).strftime("sets/%Y%m%dT%H%M%SZ"),
        ),
        now=lambda: moment,
        pause=lambda _s: None,
        **kw,
    )


def _matter(reference: str) -> Matter:
    year, number = reference.split("_")
    return Matter.all_objects.get(reference_year=int(year), reference_number=int(number))


def _decisions(run: LovableSyncRun, reference: str) -> dict[str, LovableSyncDecision]:
    return {d.field: d for d in run.decisions.filter(state__reference=reference)}


def _writes() -> int:
    return ChangeEvent.objects.count()


# ---------------------------------------------------------------------------
# Nothing changed
# ---------------------------------------------------------------------------


def test_unchanged_source_writes_nothing(cohort):
    before = _writes()
    run = _run(cohort)
    assert run.status == SyncRunStatus.SUCCEEDED, run.error
    assert run.counts["checked"] == 4
    assert run.counts["unchanged"] == 4
    assert run.counts["updated"] == 0
    assert _writes() == before
    assert LovableSyncState.objects.count() == 4


def test_the_thirteen_earlier_teemad_are_outside_the_cohort(cohort):
    other = factories.MatterFactory(reference_year=2025, reference_number=7, title="Varasem teema")
    MatterSourceReference.objects.create(
        matter=other,
        import_batch=factories.ImportBatchFactory(source_system="LOVABLE"),
        source_system="LOVABLE",
        source_file_name="public.topic",
        source_snapshot_sha256="b" * 64,
        source_sheet="public.topic:2025",
        source_row_number=7,
        source_row_raw={"lovable": {"id": "00000000-0000-0000-0000-000000009999"}},
        match_method="REFERENCE_TOKEN",
    )
    _run(cohort)
    assert not LovableSyncState.objects.filter(matter=other).exists()


# ---------------------------------------------------------------------------
# Safe automatic updates
# ---------------------------------------------------------------------------


def test_a_title_correction_is_applied_with_sync_provenance(cohort):
    cohort["source"].edit("2026_501", teema="Näidisteema 2026_501 (parandatud)")
    run = _run(cohort)
    matter = _matter("2026_501")
    assert matter.title == "Näidisteema 2026_501 (parandatud)"
    event = ChangeEvent.objects.filter(matter=matter, event_type="MATTER_TITLE_CHANGED").get()
    assert event.actor is None
    assert event.payload["provenance_source"] == "LOVABLE_SYNC"
    assert event.payload["sync_run"] == str(run.pk)
    assert event.payload["previous"] == "Näidisteema 2026_501"
    assert run.counts["updated"] == 1 and run.verified


def test_an_owner_change_hands_the_file_and_its_step_over(cohort):
    jaan = cohort["world"]["jaan"]
    cohort["source"].edit("2026_501", uus_vastutaja="Jaan")
    _run(cohort)
    matter = _matter("2026_501")
    assert matter.owner == jaan
    step = NextAction.objects.get(matter=matter, status=ActionStatus.OPEN)
    assert step.responsible == jaan
    event = ChangeEvent.objects.get(matter=matter, event_type="MATTER_ASSIGNED")
    assert event.actor is None and event.payload["provenance_source"] == "LOVABLE_SYNC"


def test_an_unresolved_owner_is_held(cohort):
    cohort["source"].edit("2026_501", vastutaja="Tundmatu Isik")
    run = _run(cohort)
    decision = _decisions(run, "2026_501")["owner"]
    assert decision.outcome == SyncOutcome.REVIEW_REQUIRED
    assert decision.reason == lovable_sync.UNRESOLVED_OWNER
    assert _matter("2026_501").owner == cohort["world"]["mari"]


def test_an_owner_who_may_not_hold_work_is_held(cohort):
    jaan = cohort["world"]["jaan"]
    jaan.is_active = False
    jaan.save(update_fields=["is_active"])
    cohort["source"].edit("2026_501", uus_vastutaja="Jaan")
    run = _run(cohort)
    assert _decisions(run, "2026_501")["owner"].outcome == SyncOutcome.REVIEW_REQUIRED


def test_a_monitor_or_wait_review_day_moves_and_keeps_everything_else(cohort):
    matter = _matter("2026_501")
    before = NextAction.objects.get(matter=matter, status=ActionStatus.OPEN)
    cohort["source"].edit("2026_501", tegevuse_tahtaeg="2026-11-29")
    _run(cohort)
    after = NextAction.objects.get(matter=matter, status=ActionStatus.OPEN)
    assert after.pk != before.pk
    assert after.target_date == dt.date(2026, 11, 29)
    assert (after.text, after.kind, after.date_semantics, after.responsible_id) == (
        before.text,
        before.kind,
        before.date_semantics,
        before.responsible_id,
    )
    before.refresh_from_db()
    assert before.status == ActionStatus.SUPERSEDED
    assert NextAction.objects.filter(matter=matter, status=ActionStatus.OPEN).count() == 1


def test_a_do_deadline_moves(cohort):
    cohort["source"].edit("2026_505", tegevuse_tahtaeg="2026-12-01")
    _run(cohort)
    step = NextAction.objects.get(matter=_matter("2026_505"), status=ActionStatus.OPEN)
    assert (step.kind, step.date_semantics, step.target_date) == (
        "DO",
        "DEADLINE",
        dt.date(2026, 12, 1),
    )


def test_a_day_moved_into_the_past_is_held_not_made_overdue(cohort):
    cohort["source"].edit("2026_505", tegevuse_tahtaeg="2026-10-01")
    run = _run(cohort)
    decision = _decisions(run, "2026_505")["next_action_date"]
    assert (decision.outcome, decision.reason) == (
        SyncOutcome.REVIEW_REQUIRED,
        lovable_sync.DATE_PAST,
    )


def test_the_drafting_step_is_not_moved_by_the_source_day(cohort):
    cohort["source"].edit("2026_502", tegevuse_tahtaeg="2026-11-30")
    run = _run(cohort)
    decision = _decisions(run, "2026_502")["next_action_date"]
    assert decision.reason == lovable_sync.NOT_SOURCE_STEP


def test_a_new_onenote_link_is_a_new_observation_beside_the_old(cohort):
    matter = _matter("2026_501")
    original = MatterSourceReference.objects.get(matter=matter)
    raw_before = copy.deepcopy(original.source_row_raw)
    cohort["source"].edit("2026_501", onenote_link=NEW_URL)
    _run(cohort)
    references = MatterSourceReference.objects.filter(matter=matter).order_by("created_at")
    assert [r.onenote_url for r in references] == [URL, NEW_URL]
    original.refresh_from_db()
    assert original.source_row_raw == raw_before
    from app.matters.views import _lovable_source_links

    assert _lovable_source_links(matter) == [{"url": NEW_URL}]


def test_a_cleared_onenote_link_is_held(cohort):
    cohort["source"].edit("2026_501", onenote_link="")
    run = _run(cohort)
    assert _decisions(run, "2026_501")["onenote_link"].outcome == SyncOutcome.REVIEW_REQUIRED


# ---------------------------------------------------------------------------
# Held for review
# ---------------------------------------------------------------------------


def test_new_next_step_wording_is_held_with_its_day(cohort):
    matter = _matter("2026_501")
    before = NextAction.objects.get(matter=matter, status=ActionStatus.OPEN)
    cohort["source"].edit("2026_501", jargmiseks="Koostan arvamuse", tegevuse_tahtaeg="2026-11-25")
    run = _run(cohort)
    found = _decisions(run, "2026_501")
    assert found["next_action_text"].outcome == SyncOutcome.REVIEW_REQUIRED
    assert found["next_action_date"].reason == lovable_sync.WITH_TEXT
    assert NextAction.objects.get(matter=matter, status=ActionStatus.OPEN).pk == before.pk


def test_a_held_change_stays_pending_the_next_night(cohort):
    cohort["source"].edit("2026_501", hetkeseis="Valitsuses")
    first = _run(cohort)
    second = _run(cohort, at=NIGHT + dt.timedelta(days=1))
    for run in (first, second):
        assert _decisions(run, "2026_501")["stage"].outcome == SyncOutcome.REVIEW_REQUIRED
    state = LovableSyncState.objects.get(reference="2026_501")
    assert state.applied["stage"]["source"] == "Riigikogus"
    assert state.observed["stage"] == "Valitsuses"


@pytest.mark.parametrize(
    ("values", "field"),
    [
        ({"arvamuse_tahtaeg": "2026-12-01"}, "opinion_deadline"),
        ({"valja": "saadetud", "valja_kuupaev": "2026-10-27"}, "sent"),
        ({"kellele": "Näidisministeerium"}, "addressee"),
        ({"koja_ettepanek": True}, "koda_initiative"),
        ({"sisse": "2026-02-01"}, "received"),
        ({"operatiivne_ulatus": False}, "operational_scope"),
    ],
)
def test_held_fields_write_nothing(cohort, values, field):
    before = _writes()
    cohort["source"].edit("2026_501", **values)
    run = _run(cohort)
    assert _decisions(run, "2026_501")[field].outcome == SyncOutcome.REVIEW_REQUIRED
    assert _writes() == before
    assert not Submission.objects.exists()
    assert not Document.objects.exists()


def test_a_changed_commencement_estimate_is_held_and_never_becomes_work(cohort):
    from app.matters.work_items import important_deadlines

    source = cohort["source"]
    topic_id = cohort["topics"][0]["source"]["id"]
    source.commencements.append(
        {
            "id": "c1",
            "topic_id": topic_id,
            "liik": "Eeldatava kuupäevaga jõustumine",
            "kuupaev": "2026-01-01",
            "tapsustus": "",
            "position": 0,
        }
    )
    source.edit("2026_501")
    before = MatterImportantDate.objects.count()
    run = _run(cohort)
    assert _decisions(run, "2026_501")["commencements"].outcome == SyncOutcome.REVIEW_REQUIRED
    assert MatterImportantDate.objects.count() == before
    mari = cohort["world"]["mari"]
    assert not [
        i
        for i in important_deadlines(mari)
        if getattr(i, "matter_id", None) == _matter("2026_502").pk
        and i.kind.endswith("COMMENCEMENT")
    ]


def test_a_past_estimate_from_the_pilot_stays_out_of_work_after_a_sync(cohort):
    """The #558 regression, after a synchronisation run (docs/adr/0120 amendment)."""
    from app.matters.work_items import important_deadlines

    cohort["source"].edit("2026_502", teema="Näidisteema 2026_502 uus")
    _run(cohort)
    matter = _matter("2026_502")
    assert MatterImportantDate.objects.filter(matter=matter, kind="ESTIMATED_COMMENCEMENT").exists()
    jaan = cohort["world"]["jaan"]
    kinds = {getattr(i, "kind", "") for i in important_deadlines(jaan)}
    assert not kinds & {"ESTIMATED_COMMENCEMENT", "PLANNED_COMMENCEMENT"}


def test_a_closed_teema_is_not_written(cohort):
    cohort["source"].edit("2026_503", teema="Uus pealkiri")
    run = _run(cohort)
    decision = _decisions(run, "2026_503")["title"]
    assert (decision.outcome, decision.reason) == (
        SyncOutcome.REVIEW_REQUIRED,
        lovable_sync.MATTER_CLOSED,
    )
    assert _matter("2026_503").title != "Uus pealkiri"
    assert not _matter("2026_503").is_open


def test_a_deleted_source_row_stops_that_teema_only(cohort):
    topic_id = cohort["topics"][0]["source"]["id"]
    del cohort["source"].rows[topic_id]
    cohort["source"].edit("2026_505", teema="Näidisteema 2026_505 uus")
    run = _run(cohort)
    assert _decisions(run, "2026_501")["*"].outcome == SyncOutcome.SOURCE_MISSING
    assert run.counts["missing"] == 1
    assert _matter("2026_501").deleted_at is None
    assert _matter("2026_505").title == "Näidisteema 2026_505 uus"


def test_a_changed_identifier_stops_that_teema(cohort):
    cohort["source"].edit("2026_501", nr="2026_599")
    run = _run(cohort)
    assert _decisions(run, "2026_501")["*"].outcome == SyncOutcome.IDENTIFIER_CHANGED


def test_too_many_missing_rows_stop_the_whole_run(cohort, monkeypatch):
    monkeypatch.setattr(lovable_sync, "MAX_MISSING", 0)
    del cohort["source"].rows[cohort["topics"][0]["source"]["id"]]
    cohort["source"].edit("2026_505", teema="ei tohi")
    run = _run(cohort)
    assert run.status == SyncRunStatus.FAILED and run.error.startswith("COHORT_MISSING")
    assert _matter("2026_505").title != "ei tohi"


# ---------------------------------------------------------------------------
# Three-way: local edits, conflicts, convergence
# ---------------------------------------------------------------------------


def test_a_local_test_edit_is_kept(cohort):
    from app.matters.services import set_matter_title

    lawyer = cohort["world"]["mari"]
    set_matter_title(matter=_matter("2026_501"), value="Juristi oma pealkiri", actor=lawyer)
    run = _run(cohort)
    assert _decisions(run, "2026_501")["title"].outcome == SyncOutcome.LOCAL_TEST_CHANGE
    assert _matter("2026_501").title == "Juristi oma pealkiri"


def test_a_local_and_a_source_change_conflict_and_nothing_is_overwritten(cohort):
    from app.matters.services import set_matter_title

    set_matter_title(
        matter=_matter("2026_501"), value="Juristi oma pealkiri", actor=cohort["world"]["mari"]
    )
    cohort["source"].edit("2026_501", teema="Lovable uus pealkiri")
    run = _run(cohort)
    assert _decisions(run, "2026_501")["title"].outcome == SyncOutcome.CONFLICT
    assert _matter("2026_501").title == "Juristi oma pealkiri"
    assert run.counts["conflicts"] == 1


def test_a_local_change_that_matches_the_source_converges_without_a_write(cohort):
    from app.matters.services import set_matter_title

    set_matter_title(
        matter=_matter("2026_501"), value="Sama uus pealkiri", actor=cohort["world"]["mari"]
    )
    cohort["source"].edit("2026_501", teema="Sama  uus pealkiri")
    before = _writes()
    run = _run(cohort)
    assert _decisions(run, "2026_501")["title"].outcome == SyncOutcome.ALREADY_CONVERGED
    assert _writes() == before
    assert (
        LovableSyncState.objects.get(reference="2026_501").applied["title"]["source"]
        == "Sama uus pealkiri"
    )


def test_an_edited_step_turns_a_date_change_into_a_conflict(cohort):
    from app.matters.workspace import change_current_action

    matter = _matter("2026_505")
    step = NextAction.objects.get(matter=matter, status=ActionStatus.OPEN)
    change_current_action(
        matter=matter,
        actor=cohort["world"]["jaan"],
        action_id=step.pk,
        text="Saadan märkused ja helistan",
        kind=step.kind,
        date_semantics=step.date_semantics,
        target_date=step.target_date,
    )
    cohort["source"].edit("2026_505", tegevuse_tahtaeg="2026-12-01")
    run = _run(cohort)
    assert _decisions(run, "2026_505")["next_action_date"].outcome == SyncOutcome.CONFLICT
    assert (
        NextAction.objects.get(matter=matter, status=ActionStatus.OPEN).text
        == "Saadan märkused ja helistan"
    )


def test_an_edit_made_after_planning_is_caught_under_the_lock(cohort, monkeypatch):
    """A lawyer's save between the plan and the write turns the write into a conflict."""
    from app.matters.services import set_matter_title

    cohort["source"].edit("2026_501", teema="Lovable uus pealkiri")
    real_apply = lovable_sync.apply_matter

    def edit_then_apply(plan, **kw):
        if plan.state.reference == "2026_501":
            set_matter_title(
                matter=_matter("2026_501"),
                value="Vahepealne muudatus",
                actor=cohort["world"]["mari"],
            )
        return real_apply(plan, **kw)

    monkeypatch.setattr(lovable_sync, "apply_matter", edit_then_apply)
    run = _run(cohort)
    assert _matter("2026_501").title == "Vahepealne muudatus"
    assert _decisions(run, "2026_501")["title"].outcome == SyncOutcome.CONFLICT


# ---------------------------------------------------------------------------
# Idempotency, failure, concurrency
# ---------------------------------------------------------------------------


def test_the_same_snapshot_twice_writes_once(cohort):
    cohort["source"].edit("2026_501", teema="Uus", tegevuse_tahtaeg="2026-11-29")
    _run(cohort)
    writes = _writes()
    open_steps = NextAction.objects.filter(status=ActionStatus.OPEN).count()
    second = _run(cohort, at=NIGHT + dt.timedelta(minutes=5))
    assert second.counts["updated"] == 0
    assert _writes() == writes
    assert NextAction.objects.filter(status=ActionStatus.OPEN).count() == open_steps
    assert LovableSyncDecision.objects.filter(outcome=SyncOutcome.APPLIED).count() == 2


def test_the_nightly_run_happens_once_per_tallinn_date(cohort):
    _run(cohort, SyncTrigger.SCHEDULED)
    with pytest.raises(SyncRefused) as refused:
        _run(cohort, SyncTrigger.SCHEDULED, at=NIGHT + dt.timedelta(hours=1))
    assert refused.value.code == "DUPLICATE"


@pytest.mark.parametrize(
    ("utc", "key"),
    [
        # Summer time ends on 25.10.2026: 00:15 is 21:15 UTC before it, 22:15 after.
        (dt.datetime(2026, 10, 24, 21, 15, tzinfo=dt.UTC), "2026-10-25"),
        (dt.datetime(2026, 10, 25, 22, 15, tzinfo=dt.UTC), "2026-10-26"),
        (dt.datetime(2027, 3, 27, 22, 15, tzinfo=dt.UTC), "2027-03-28"),
        (dt.datetime(2027, 3, 28, 21, 15, tzinfo=dt.UTC), "2027-03-29"),
    ],
)
def test_the_run_key_is_the_tallinn_date_across_summer_time(utc, key):
    assert run_key_for(SyncTrigger.SCHEDULED, utc) == key


def test_a_failed_read_is_a_failed_run_with_nothing_written(cohort):
    cohort["source"].edit("2026_501", teema="ei tohi")
    cohort["source"].fail = SourceError("UNREACHABLE", "Lovable could not be reached.")
    run = _run(cohort)
    assert run.status == SyncRunStatus.FAILED and run.error.startswith("UNREACHABLE")
    assert _matter("2026_501").title != "ei tohi"
    assert not run.verified


def test_a_source_that_keeps_changing_while_read_fails_the_run(cohort):
    source = cohort["source"]
    source.on_select = lambda table: table == "topic" and source.edit("2026_501")
    run = _run(cohort)
    assert run.status == SyncRunStatus.FAILED and run.error.startswith("INCONSISTENT")


def test_a_source_that_settles_is_read_again(cohort):
    source = cohort["source"]
    calls = {"n": 0}

    def once(table):
        calls["n"] += 1
        if calls["n"] == 2:
            source.edit("2026_501")

    source.on_select = once
    run = _run(cohort)
    assert run.status == SyncRunStatus.SUCCEEDED
    assert run.snapshot.document["attempts"] == 2


def test_a_crash_inside_one_teema_rolls_that_teema_back(cohort, monkeypatch):
    cohort["source"].edit("2026_501", teema="Uus 501", tegevuse_tahtaeg="2026-11-29")
    cohort["source"].edit("2026_505", teema="Uus 505")
    import app.workflow.services as workflow_services

    def boom(**_kw):
        raise RuntimeError("crash")

    monkeypatch.setattr(workflow_services, "set_next_action", boom)
    run = _run(cohort)
    assert _matter("2026_501").title == "Näidisteema 2026_501"  # rolled back with the step
    assert (
        LovableSyncState.objects.get(reference="2026_501").applied["title"]["source"]
        == "Näidisteema 2026_501"
    )
    assert _decisions(run, "2026_501")["*"].outcome == SyncOutcome.FAILED
    assert _matter("2026_505").title == "Uus 505"
    assert run.counts["failed"] == 1
    assert run.status == SyncRunStatus.FAILED  # one Teema not written is not a success


def test_a_second_run_is_refused_while_one_holds_the_lock(cohort):
    other = connections["default"].copy()
    try:
        with other.cursor() as cursor:
            cursor.execute(
                "SELECT pg_advisory_lock(%s, %s)",
                [lovable_sync.LOCK_NAMESPACE, lovable_sync.LOCK_KEY],
            )
        with pytest.raises(SyncRefused) as refused:
            _run(cohort)
        assert refused.value.code == "LOCKED"
    finally:
        other.close()


def test_a_breaker_holds_a_night_with_too_many_changes(cohort, monkeypatch):
    monkeypatch.setattr(lovable_sync, "MAX_CHANGED_MATTERS", 1)
    cohort["source"].edit("2026_501", teema="Uus 501")
    cohort["source"].edit("2026_505", teema="Uus 505")
    run = _run(cohort)
    assert run.status == SyncRunStatus.REVIEW_ONLY and run.error == lovable_sync.BREAKER
    assert run.counts["eligible_unapplied"] == 2
    assert _matter("2026_501").title == "Näidisteema 2026_501"


def test_no_verified_backup_means_no_writes(cohort):
    cohort["source"].edit("2026_501", teema="Uus 501")
    run = _run(cohort, backup_set="")
    assert run.status == SyncRunStatus.REVIEW_ONLY and run.error == lovable_sync.NO_BACKUP
    assert _matter("2026_501").title == "Näidisteema 2026_501"


def test_compare_mode_reports_and_writes_nothing(cohort):
    lovable_sync.set_mode(SyncMode.COMPARE, reason="first nights")
    cohort["source"].edit("2026_501", teema="Uus 501")
    before = _writes()
    run = _run(cohort)
    assert run.status == SyncRunStatus.SUCCEEDED
    assert _decisions(run, "2026_501")["title"].outcome == SyncOutcome.ELIGIBLE
    assert _writes() == before


# ---------------------------------------------------------------------------
# Pause and cutover
# ---------------------------------------------------------------------------


def test_a_paused_job_refuses_and_resumes(cohort):
    lovable_sync.set_mode(SyncMode.OFF, reason="paused")
    with pytest.raises(SyncRefused) as refused:
        _run(cohort)
    assert refused.value.code == "PAUSED"
    lovable_sync.set_mode(SyncMode.APPLY, reason="resumed")
    assert _run(cohort).status == SyncRunStatus.SUCCEEDED


def test_cutover_disables_the_job_for_good(cohort):
    lovable_sync.cut_over(reason="Juristid is the source of truth")
    with pytest.raises(SyncRefused) as refused:
        _run(cohort)
    assert refused.value.code == "CUT_OVER"
    with pytest.raises(SyncRefused):
        lovable_sync.set_mode(SyncMode.APPLY, reason="again")
    row = LovableSyncControl.objects.get()
    row.cut_over = False
    with pytest.raises(Exception, match="tagasi"):
        row.save()


def test_check_compares_the_stored_snapshot_without_the_source(cohort):
    cohort["source"].edit("2026_501", teema="Uus 501")
    lovable_sync.set_mode(SyncMode.COMPARE, reason="t")
    _run(cohort)
    check = run_sync(trigger=SyncTrigger.CHECK, now=lambda: NIGHT + dt.timedelta(hours=8))
    assert check.mode == SyncMode.COMPARE
    assert _decisions(check, "2026_501")["title"].outcome == SyncOutcome.ELIGIBLE


def test_an_operator_resolution_clears_a_held_change(cohort):
    cohort["source"].edit("2026_501", hetkeseis="Valitsuses")
    _run(cohort)
    lovable_sync.resolve_field(
        reference="2026_501", field_name="stage", note="etapp muudetud käsitsi"
    )
    run = _run(cohort, at=NIGHT + dt.timedelta(days=1))
    assert "stage" not in _decisions(run, "2026_501")


def test_search_is_refreshed_for_the_written_teema(cohort, monkeypatch):
    import app.search.indexing as indexing

    seen: list[Any] = []
    monkeypatch.setattr(indexing, "refresh_matters", lambda qs, **kw: seen.extend(qs) or len(seen))
    cohort["source"].edit("2026_501", teema="Uus 501")
    _run(cohort)
    assert [m.pk for m in seen] == [_matter("2026_501").pk]


def test_the_summary_says_counts_and_names_a_failure():
    run = LovableSyncRun(
        started_at=NIGHT,
        status=SyncRunStatus.SUCCEEDED,
        mode=SyncMode.APPLY,
        counts={"checked": 100, "unchanged": 94, "updated": 3, "local_only": 1, "review": 2},
    )
    lines = lovable_sync.summary_lines(run)
    assert lines[0] == "Lovable sünkroonimine — 28.10.2026"
    assert "Kontrollitud: 100 teemat" in lines and "Uuendatud: 3" in lines
    run.status, run.error = SyncRunStatus.FAILED, "UNREACHABLE: x"
    assert lovable_sync.summary_lines(run)[1] == "EBAÕNNESTUS: UNREACHABLE"


# ---------------------------------------------------------------------------
# The source reader
# ---------------------------------------------------------------------------


def _credentials(tmp_path, **values: str):
    data = {
        "LOVABLE_SYNC_URL": "https://naidis.supabase.co",
        "LOVABLE_SYNC_PUBLISHABLE_KEY": "sb_publishable_naidis",
        "LOVABLE_SYNC_EMAIL": "sync@example.invalid",
        "LOVABLE_SYNC_PASSWORD": "naidis-salasona",
        **values,
    }
    path = tmp_path / "lovable-sync.env"
    path.write_text("\n".join(f"{k}={v}" for k, v in data.items()), encoding="utf-8")
    return path


def test_credentials_must_be_https_and_publishable(tmp_path):
    with pytest.raises(SourceError, match="https"):
        lovable_source.load_config(_credentials(tmp_path, LOVABLE_SYNC_URL="http://x"))
    with pytest.raises(SourceError) as refused:
        lovable_source.load_config(
            _credentials(tmp_path, LOVABLE_SYNC_PUBLISHABLE_KEY="sb_secret_x")
        )
    assert refused.value.code == "PRIVILEGED_KEY"
    config = lovable_source.load_config(_credentials(tmp_path))
    assert "naidis-salasona" not in repr(config)


def _jwt(role: str) -> str:
    import base64
    import json

    body = (
        base64.urlsafe_b64encode(json.dumps({"role": role, "sub": "u1"}).encode())
        .decode()
        .rstrip("=")
    )
    return f"x.{body}.y"


def test_only_an_ordinary_signed_in_token_reads():
    lovable_source.refuse_privileged_token(_jwt("authenticated"))
    for role in ("service_role", "anon", "supabase_admin"):
        with pytest.raises(SourceError):
            lovable_source.refuse_privileged_token(_jwt(role))


class _Response:
    def __init__(self, body: Any, status: int = 200) -> None:
        import json

        self.raw = json.dumps(body).encode()

    def read(self, _n: int) -> bytes:
        return self.raw

    def __enter__(self):
        return self

    def __exit__(self, *_a):
        return False


def test_the_reader_reads_only_allowlisted_tables(tmp_path):
    seen: list[str] = []

    def opener(request, timeout):
        seen.append(f"{request.get_method()} {request.full_url}")
        if "/auth/v1/token" in request.full_url:
            return _Response({"access_token": _jwt("authenticated")})
        return _Response([])

    transport = lovable_source.HttpTransport(
        lovable_source.load_config(_credentials(tmp_path)), opener=opener
    )
    with pytest.raises(SourceError) as refused:
        transport.select("user_profiles", ["user_id"], filters={}, order="user_id")
    assert refused.value.code == "NOT_ALLOWLISTED"
    with pytest.raises(SourceError):
        transport.select("topic", ["markmed"], filters={}, order="id")
    transport.select("topic", ["id", "nr"], filters={}, order="id")
    assert all(
        line.startswith(("GET ", "POST https://naidis.supabase.co/auth")) for line in seen
    )


def test_a_service_role_sign_in_stops_before_any_read(tmp_path):
    def opener(request, timeout):
        return _Response({"access_token": _jwt("service_role")})

    transport = lovable_source.HttpTransport(
        lovable_source.load_config(_credentials(tmp_path)), opener=opener
    )
    with pytest.raises(SourceError) as refused:
        transport.select("topic", ["id"], filters={}, order="id")
    assert refused.value.code == "PRIVILEGED_TOKEN"


def test_the_access_probe_fails_an_identity_that_can_write(tmp_path):
    import urllib.error

    def opener(request, timeout, *, writes_allowed: bool):
        url = request.full_url
        if "/auth/v1/token" in url:
            return _Response({"access_token": _jwt("authenticated")})
        if "/rpc/can_access_admin_view" in url:
            return _Response(False)
        if "/rpc/" in url or request.get_method() in ("PATCH", "DELETE"):
            if writes_allowed:
                return _Response([{"id": "x"}])
            raise urllib.error.HTTPError(url, 403, "denied", {}, None)
        if "topic_change" in url or "deleted_at" in url:
            return _Response([])
        if "topic?" in url:
            return _Response(
                [{"id": "00000000-0000-0000-0000-000000000001", "nr": "2026_1", "version": 1}]
            )
        return _Response([])

    config = lovable_source.load_config(_credentials(tmp_path))
    good = lovable_source.access_probe(
        config, opener=lambda r, t: opener(r, t, writes_allowed=False)
    )
    assert good.passed, [c for c in good.checks if not c[1]]
    bad = lovable_source.access_probe(config, opener=lambda r, t: opener(r, t, writes_allowed=True))
    assert not bad.passed


# ---------------------------------------------------------------------------
# Haldus → Lovable sünkroonimine
# ---------------------------------------------------------------------------


@pytest.fixture
def haldus(settings, cohort):
    from tests.admin_session import apply_admin_gate, technical_admin, verified

    apply_admin_gate(settings)
    # The cohort was applied with the pilot's real-data switch on; a synthetic
    # administrator may not sign in under it, and the page needs none of it.
    settings.REAL_DATA_ALLOWED = False
    admin, secret = technical_admin()
    return {"admin": admin, "secret": secret, "verified": verified}


def test_the_page_shows_the_night_in_counts(client, haldus, cohort):
    from django.urls import reverse

    cohort["source"].edit("2026_501", hetkeseis="Valitsuses")
    _run(cohort)
    haldus["verified"](client, haldus["admin"], haldus["secret"])
    page = client.get(reverse("haldus:lovable"))
    body = page.content.decode()
    assert page.status_code == 200
    assert "Kontrollitud: 4 teemat" in body
    assert "Vajab ülevaatamist: 1" in body
    # No persona: no Teema is visible, so the change is counted, never shown.
    assert "Valitsuses" not in body and "Riigikogus" not in body
    assert "1 muudatus teemadel, mida sinu valitud isik ei näe" in body


def test_a_business_persona_sees_the_wording_of_what_waits(client, haldus, cohort):
    """Shown through `visible_to`: a department worker reads every Teema
    (docs/adr/0181), so the wording is there; with no persona it is not
    (`test_the_page_shows_the_night_in_counts`)."""
    from django.urls import reverse

    from tests.admin_session import choose

    cohort["source"].edit("2026_501", hetkeseis="Valitsuses")
    _run(cohort)
    haldus["verified"](client, haldus["admin"], haldus["secret"])
    choose(client, cohort["world"]["mari"])
    body = client.get(reverse("haldus:lovable")).content.decode()
    assert "Valitsuses" in body and "Riigikogus" in body and "Hetkeseis" in body
    assert "mida sinu valitud isik ei näe" not in body


def test_check_changes_records_a_report_only_run(client, haldus, cohort):
    from django.urls import reverse

    lovable_sync.set_mode(SyncMode.COMPARE, reason="t")
    _run(cohort)
    haldus["verified"](client, haldus["admin"], haldus["secret"])
    before = _writes()
    response = client.post(reverse("haldus:lovable_check"))
    assert response.status_code == 302
    assert LovableSyncRun.objects.filter(trigger=SyncTrigger.CHECK).count() == 1
    assert _writes() == before
    assert client.get(reverse("haldus:lovable_check")).status_code == 405


def test_the_page_is_for_the_technical_administrator_only(client, settings, cohort):
    from django.urls import reverse

    from app.accounts.enums import UserRole
    from tests.admin_session import apply_admin_gate, full_admin, verified

    apply_admin_gate(settings)
    settings.REAL_DATA_ALLOWED = False
    admin, secret = full_admin(role=UserRole.DEPARTMENT_HEAD)
    verified(client, admin, secret)
    assert client.get(reverse("haldus:lovable")).status_code == 404
    assert client.post(reverse("haldus:lovable_check")).status_code == 404


def test_a_failed_night_is_never_shown_as_current_success(cohort):
    first = _run(cohort, SyncTrigger.SCHEDULED)
    assert lovable_sync.sync_health(now=NIGHT + dt.timedelta(hours=1)).ok
    cohort["source"].fail = SourceError("UNREACHABLE", "x")
    _run(cohort, SyncTrigger.SCHEDULED, at=NIGHT + dt.timedelta(days=1))
    health = lovable_sync.sync_health(now=NIGHT + dt.timedelta(days=1, hours=1))
    assert not health.ok and "failed" in health.detail
    assert health.last_success == first
    stale = lovable_sync.sync_health(now=NIGHT + dt.timedelta(days=3))
    assert not stale.ok


@pytest.mark.parametrize(
    ("utc", "expected"),
    [
        (dt.datetime(2026, 10, 24, 21, 15, tzinfo=dt.UTC), "DUE APPLY"),  # summer: 00:15
        (dt.datetime(2026, 10, 24, 22, 15, tzinfo=dt.UTC), "NOT_DUE NOT_MIDNIGHT_IN_TALLINN"),
        (dt.datetime(2026, 10, 27, 21, 15, tzinfo=dt.UTC), "NOT_DUE NOT_MIDNIGHT_IN_TALLINN"),
        (dt.datetime(2026, 10, 27, 22, 15, tzinfo=dt.UTC), "DUE APPLY"),  # winter: 00:15
    ],
)
def test_exactly_one_of_the_two_utc_firings_is_due(db, utc, expected):
    lovable_sync.set_mode(SyncMode.APPLY, reason="t")
    assert lovable_sync.due(trigger=SyncTrigger.SCHEDULED, now=utc) == expected


def test_due_says_paused_ran_and_cut_over(cohort):
    winter = dt.datetime(2026, 10, 27, 22, 15, tzinfo=dt.UTC)
    _run(cohort, SyncTrigger.SCHEDULED, at=winter)
    assert lovable_sync.due(trigger=SyncTrigger.SCHEDULED, now=winter) == "NOT_DUE ALREADY_RAN"
    lovable_sync.set_mode(SyncMode.COMPARE, reason="t")
    assert lovable_sync.due(trigger=SyncTrigger.MANUAL, now=winter) == "DUE COMPARE"
    lovable_sync.set_mode(SyncMode.OFF, reason="t")
    assert lovable_sync.due(trigger=SyncTrigger.MANUAL, now=winter) == "NOT_DUE PAUSED"
    lovable_sync.cut_over(reason="t")
    assert lovable_sync.due(trigger=SyncTrigger.MANUAL, now=winter) == "NOT_DUE CUT_OVER"


def test_credentials_can_come_from_the_environment(monkeypatch):
    for key, value in {
        "LOVABLE_SYNC_URL": "https://naidis.supabase.co",
        "LOVABLE_SYNC_PUBLISHABLE_KEY": "sb_publishable_naidis",
        "LOVABLE_SYNC_EMAIL": "sync@example.invalid",
        "LOVABLE_SYNC_PASSWORD": "naidis-salasona",
    }.items():
        monkeypatch.setenv(key, value)
    assert lovable_source.load_config().email == "sync@example.invalid"
    monkeypatch.delenv("LOVABLE_SYNC_PASSWORD")
    with pytest.raises(SourceError) as refused:
        lovable_source.load_config()
    assert refused.value.code == "CREDENTIALS_INCOMPLETE"


def test_run_needs_the_one_off_switches(db, settings):
    from django.core.management import CommandError, call_command

    settings.LOVABLE_SYNC_ENABLED = False
    with pytest.raises(CommandError, match="JURISTID_LOVABLE_SYNC"):
        call_command("lovable_sync", "run", "--trigger", "scheduled")


# ---------------------------------------------------------------------------
# The independent review's findings (2026-10-27), each pinned
# ---------------------------------------------------------------------------


def test_a_day_the_lawyer_reviewed_in_place_is_not_the_baseline(cohort):
    """«Vaatasin üle» moves the pilot's step's day in place, keeping its id: the
    baseline is the day the pilot wrote, so the lawyer's day is a local change and
    a later source day conflicts instead of replacing it."""
    from app.workflow.services import acknowledge_review

    matter = _matter("2026_501")
    step = NextAction.objects.get(matter=matter, status=ActionStatus.OPEN)
    acknowledge_review(
        action=step, actor=cohort["world"]["mari"], next_review_date=dt.date(2026, 11, 20)
    )
    cohort["source"].edit("2026_501", tegevuse_tahtaeg="2026-11-25")
    run = _run(cohort)
    assert _decisions(run, "2026_501")["next_action_date"].outcome == SyncOutcome.CONFLICT
    step.refresh_from_db()
    assert NextAction.objects.get(matter=matter, status=ActionStatus.OPEN).target_date == dt.date(
        2026, 11, 20
    )


def test_a_resolved_wording_does_not_let_the_source_move_the_lawyers_own_step(cohort):
    from app.matters.workspace import change_current_action

    matter = _matter("2026_505")
    step = NextAction.objects.get(matter=matter, status=ActionStatus.OPEN)
    change_current_action(
        matter=matter,
        actor=cohort["world"]["jaan"],
        action_id=step.pk,
        text="Minu oma sõnastus",
        kind=step.kind,
        date_semantics=step.date_semantics,
        target_date=step.target_date,
    )
    cohort["source"].edit("2026_505", jargmiseks="Lovable uus sõnastus")
    _run(cohort)
    lovable_sync.resolve_field(reference="2026_505", field_name="next_action_text", note="jääb")
    cohort["source"].edit("2026_505", tegevuse_tahtaeg="2026-12-03")
    run = _run(cohort, at=NIGHT + dt.timedelta(days=1))
    decision = _decisions(run, "2026_505")["next_action_date"]
    assert decision.outcome == SyncOutcome.REVIEW_REQUIRED
    assert decision.reason == lovable_sync.NOT_SOURCE_STEP
    assert (
        NextAction.objects.get(matter=matter, status=ActionStatus.OPEN).target_date
        == step.target_date
    )


def test_converged_wording_becomes_the_baseline_and_a_later_day_moves(cohort):
    from app.matters.workspace import change_current_action

    matter = _matter("2026_505")
    step = NextAction.objects.get(matter=matter, status=ActionStatus.OPEN)
    change_current_action(
        matter=matter,
        actor=cohort["world"]["jaan"],
        action_id=step.pk,
        text="Ootan ministeeriumi vastust",
        kind="WAIT",
        date_semantics="REVIEW_ON",
        target_date=dt.date(2026, 11, 22),
    )
    cohort["source"].edit(
        "2026_505", jargmiseks="Ootan ministeeriumi vastust", tegevuse_tahtaeg="2026-11-22"
    )
    first = _run(cohort)
    assert (
        _decisions(first, "2026_505")["next_action_text"].outcome == SyncOutcome.ALREADY_CONVERGED
    )
    cohort["source"].edit("2026_505", tegevuse_tahtaeg="2026-11-29")
    second = _run(cohort, at=NIGHT + dt.timedelta(days=1))
    assert "next_action_text" not in _decisions(second, "2026_505")
    after = NextAction.objects.get(matter=matter, status=ActionStatus.OPEN)
    assert (after.text, after.kind, after.target_date) == (
        "Ootan ministeeriumi vastust",
        "WAIT",
        dt.date(2026, 11, 29),
    )


def test_a_value_that_comes_back_is_applied_again_without_jamming(cohort):
    for night, day in enumerate(["2026-11-29", "2026-11-15", "2026-11-29"]):
        cohort["source"].edit("2026_501", tegevuse_tahtaeg=day)
        run = _run(cohort, at=NIGHT + dt.timedelta(days=night))
        assert run.status == SyncRunStatus.SUCCEEDED, run.error
        assert _decisions(run, "2026_501") == {} or "*" not in _decisions(run, "2026_501")
    step = NextAction.objects.get(matter=_matter("2026_501"), status=ActionStatus.OPEN)
    assert step.target_date == dt.date(2026, 11, 29)


def test_a_run_with_a_failed_teema_is_not_a_success(cohort, monkeypatch):
    import app.workflow.services as workflow_services

    cohort["source"].edit("2026_501", tegevuse_tahtaeg="2026-11-29")
    monkeypatch.setattr(workflow_services, "set_next_action", lambda **_kw: 1 / 0)
    run = _run(cohort)
    assert run.status == SyncRunStatus.FAILED
    assert run.error.startswith(lovable_sync.PARTIAL)
    assert not lovable_sync.sync_health(now=NIGHT + dt.timedelta(hours=1)).ok


def test_standing_conflicts_do_not_hold_every_later_night(cohort, monkeypatch):
    from app.matters.services import set_matter_title

    monkeypatch.setattr(lovable_sync, "MAX_NEW_CONFLICTS", 0)
    set_matter_title(matter=_matter("2026_501"), value="Juristi oma", actor=cohort["world"]["mari"])
    cohort["source"].edit("2026_501", teema="Lovable oma")
    first = _run(cohort)
    assert first.status == SyncRunStatus.REVIEW_ONLY  # a new conflict: the night is held
    cohort["source"].edit("2026_505", teema="Uus 505")
    second = _run(cohort, at=NIGHT + dt.timedelta(days=1))
    assert second.status == SyncRunStatus.SUCCEEDED, second.error
    assert _matter("2026_505").title == "Uus 505"


def test_a_closed_teema_gets_no_new_link(cohort):
    cohort["source"].edit("2026_503", onenote_link=NEW_URL)
    run = _run(cohort)
    assert _decisions(run, "2026_503")["onenote_link"].reason == lovable_sync.MATTER_CLOSED
    assert not MatterSourceReference.objects.filter(onenote_url=NEW_URL).exists()


def test_a_closed_teema_already_matching_the_source_converges(cohort):
    from app.matters.services import set_matter_title

    set_matter_title(matter=_matter("2026_503"), value="Uus nimi", actor=cohort["world"]["mari"])
    cohort["source"].edit("2026_503", teema="Uus nimi")
    run = _run(cohort)
    assert _decisions(run, "2026_503")["title"].outcome == SyncOutcome.ALREADY_CONVERGED


def test_check_changes_nothing_but_its_own_findings(cohort):
    lovable_sync.set_mode(SyncMode.COMPARE, reason="t")
    cohort["source"].edit("2026_501", hetkeseis="Valitsuses")
    _run(cohort)
    states = list(
        LovableSyncState.objects.order_by("reference").values("applied", "observed", "observed_run")
    )
    snapshots = list(lovable_sync.LovableSyncSnapshot.objects.values_list("pk", flat=True))
    check = run_sync(trigger=SyncTrigger.CHECK, now=lambda: NIGHT + dt.timedelta(days=40))
    assert check.status == SyncRunStatus.SUCCEEDED
    assert (
        list(
            LovableSyncState.objects.order_by("reference").values(
                "applied", "observed", "observed_run"
            )
        )
        == states
    )
    assert list(lovable_sync.LovableSyncSnapshot.objects.values_list("pk", flat=True)) == snapshots


def test_check_seeds_nothing(cohort):
    check = run_sync(trigger=SyncTrigger.CHECK, now=lambda: NIGHT)
    assert check.status == SyncRunStatus.FAILED and check.error.startswith("COHORT_SIZE")
    assert not LovableSyncState.objects.exists()


@pytest.mark.parametrize(
    ("backup_set", "fresh"),
    [
        ("sets/20261027T221000Z", True),  # 5 minutes before 00:15 Tallinn (22:15 UTC)
        ("sets/20261027T180000Z", False),  # hours old
        ("sets/20200101T000000Z", False),  # invented
        ("sets/20261027T230000Z", False),  # after the run started
        ("../etc", False),
    ],
)
def test_only_a_fresh_backup_set_opens_the_mutating_stage(backup_set, fresh):
    started = dt.datetime(2026, 10, 27, 22, 15, tzinfo=dt.UTC)
    assert (lovable_sync.fresh_backup_set(backup_set, started) == backup_set) is fresh


def test_a_stale_backup_set_writes_nothing(cohort):
    cohort["source"].edit("2026_501", teema="Uus 501")
    run = _run(cohort, backup_set="sets/20261020T000000Z")
    assert run.status == SyncRunStatus.REVIEW_ONLY and run.error == lovable_sync.NO_BACKUP
    assert _matter("2026_501").title == "Näidisteema 2026_501"


def test_a_resolved_owner_is_not_learnt_as_a_name(cohort):
    cohort["source"].edit("2026_501", uus_vastutaja="Jaan")
    lovable_sync.set_mode(SyncMode.COMPARE, reason="t")
    _run(cohort)
    lovable_sync.resolve_field(reference="2026_501", field_name="owner", note="Mari jääb")
    resolver = lovable_sync.OwnerResolver(LovableSyncState.objects.all())
    assert resolver.resolve("Jaan") == (str(cohort["world"]["jaan"].pk), "")
