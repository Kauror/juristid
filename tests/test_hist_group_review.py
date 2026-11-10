# ruff: noqa: F811 - pytest fixtures imported from the sync suites are used as arguments
"""«Vaata erinevusi» keeps the historical register's import holds apart
(docs/adr/0213 §9, amendment 2026-11-10), on synthetic data.

On a rehearsal clone, enrolling the 1,833 historical Teemad (cohort
``lovable-history``) made every one whose Lovable «hetkeseis» is «lõpetatud»
carry a permanent IMPORT_HELD ``stage`` item: the run's «vajab ülevaatamist»
went 46 → 1,869 and buried the operational work. Here a pilot of four (the
operational work) and an enrolled history cohort of three closed ARCHIVE
Teemad (their stage held by the import) run one night, and the pages are read:
the history holds sit in their own closed group after everything else, never in
a card, an operational group or a headline count; a real change on a historical
Teema stays operational; and nothing about what is pending, applied or resolved
changed. Lovable is the fake transport of tests/test_lovable_sync.py.
"""

from __future__ import annotations

import copy
import datetime as dt
from typing import Any

import pytest
from django.db import connection
from django.test.utils import CaptureQueriesContext
from django.urls import reverse

from app.legacy_import import lovable_sync, sync_admin
from app.legacy_import.lovable_pilot import build_plan
from app.legacy_import.lovable_sync_models import (
    LovableSyncRun,
    LovableSyncState,
    SyncMode,
    SyncOutcome,
    SyncTrigger,
)
from app.legacy_import.models import EvidencePilotRecord
from app.matters.models import Matter
from tests.admin_session import apply_admin_gate, choose, technical_admin, verified
from tests.refusals import refused
from tests.test_lovable_pilot import _apply, _package, pilot_on, world  # noqa: F401
from tests.test_lovable_sync import NIGHT, _row, _run, _source_topic, cohort  # noqa: F401

pytestmark = pytest.mark.django_db

HISTORY = lovable_sync.HISTORY_COHORT
HELD = "HELD_FOR_REVIEW"
HISTORICAL = ("2026_601", "2026_602", "2026_603")
GROUP = "Ajalooline register: impordil ootele jäetud väljad (3)"


def _closed(reference: str) -> dict[str, Any]:
    """A closed ARCHIVE Teema of the register whose Lovable stage is «lõpetatud»,
    which is no Juristid stage: the import holds it for review."""
    return _source_topic(
        reference,
        _row(reference, hetkeseis="lõpetatud", jargmiseks="", tegevuse_tahtaeg=None),
        status_raw="lõpetatud",
        stage="in_force",
        lifecycle="INACTIVE",
        disposition="COMPLETED",
        next_action=None,
        deadline={"treatment": "NONE"},
        field_decisions={"hetkeseis": HELD},
    )


@pytest.fixture
def night(cohort, settings):
    """The pilot of four with operational changes (a next step, an addressee, a
    sent opinion), an enrolled history cohort of three whose stage the import
    held, one of them retitled in Lovable — one COMPARE night."""
    topics = [_closed(ref) for ref in HISTORICAL]
    package = _package(*topics)
    package["snapshot"]["snapshot_sha256"] = "e" * 64
    package["snapshot"]["highest_reference"] = {"2026": 620}
    package["reserve_through"] = {"2026": 620}
    digest = build_plan(package).digest
    _apply(package)
    # The import above ran as the pilot fixture does; the pages run behind the gate.
    apply_admin_gate(settings)
    settings.REAL_DATA_ALLOWED = False
    EvidencePilotRecord.objects.filter(package_sha256=digest).update(pilot=HISTORY)
    done = lovable_sync.enroll(
        cohort=HISTORY, package_sha256=digest, expect=3, reason="Ajalugu (testmaailm)"
    )
    assert done.seeded == 3
    source = cohort["source"]
    for topic in topics:
        source.put(topic["source"]["id"], copy.deepcopy(topic["source"]["row"]), version=3)
    lovable_sync.set_mode(SyncMode.COMPARE, reason="test")
    source.edit("2026_505", jargmiseks="Ootan ministeeriumi vastust")
    source.edit("2026_501", kellele="Riigikogu")
    source.edit("2026_502", valja="2026-10-27")
    source.edit("2026_603", teema="Ajaloolise teema uus pealkiri")
    run = _run(cohort, SyncTrigger.SCHEDULED)
    return {**cohort, "run": run}


def _reader(night: dict[str, Any]) -> Any:
    return night["world"]["mari"]


def _signed(client: Any, night: dict[str, Any]) -> Any:
    admin_user, secret = technical_admin()
    verified(client, admin_user, secret)
    choose(client, _reader(night))
    return client


def test_the_synthetic_night_reproduces_the_rehearsal_shape(night):
    """The engine is unchanged: the stored counts still add the history cohort's
    held Teemad to «vajab ülevaatamist», which is what buried the work."""
    run = night["run"]
    part = run.counts["by_cohort"][HISTORY]
    assert (part["checked"], part["review"], part["import_held"]) == (3, 3, 3)
    held = run.decisions.filter(state__cohort=HISTORY, reason=lovable_sync.IMPORT_HELD)
    assert {(d.field, d.outcome) for d in held} == {("stage", SyncOutcome.REVIEW_REQUIRED)}
    assert held.count() == 3
    assert run.counts["review"] == part["review"] + run.counts["by_cohort"][
        lovable_sync.PILOT_COHORT
    ].get("review", 0)


def test_history_holds_are_their_own_group_never_operational(night):
    diff = sync_admin.differences(_reader(night))
    history = diff.history
    assert history is not None
    assert (history.total, history.teemad, history.more) == (3, 3, 0)
    assert [(s.field_label, s.count) for s in history.summary] == [("Hetkeseis", 3)]
    assert [r.reference for r in history.rows] == list(HISTORICAL)
    assert all(r.historical and r.field == "stage" for r in history.rows)
    operational = [r for _h, rows in diff.groups for r in rows] + [
        r for card in diff.step_cards for r in card.rows
    ]
    assert not any(r.historical for r in operational)
    assert diff.named == len(operational) == diff.total + sum(len(c.rows) for c in diff.step_cards)
    # The next-step card is the pilot's alone.
    assert [card.reference for card in diff.step_cards] == ["2026_505"]


def test_a_real_change_on_a_historical_teema_stays_operational(night):
    diff = sync_admin.differences(_reader(night))
    retitled = [r for r in diff.rows if r.reference == "2026_603"]
    assert [(r.field, r.historical) for r in retitled] == [("title", False)]
    assert "2026_603" in [r.reference for r in diff.history.rows]  # its stage hold only


def test_the_page_puts_the_history_group_last_and_closed(night, client):
    body = _signed(client, night).get(reverse("haldus:data_sync_differences")).content.decode()
    assert GROUP in body
    at = body.index(GROUP)
    for heading in ("Järgmise sammu parandused", "Arvamuse küsimine ja saatmine", "2026_501"):
        assert body.index(heading) < at
    opening = body.rindex("<details", 0, at)
    tag = body[opening : body.index(">", opening)]
    assert "open" not in tag.split("class=")[0] and " open" not in tag
    assert f"Nähtavaid erinevusi kokku {sync_admin.differences(_reader(night)).named}." in body


def test_the_history_list_is_capped_and_summarised(night, monkeypatch):
    monkeypatch.setattr(sync_admin, "HISTORY_ROWS_SHOWN", 2)
    history = sync_admin.differences(_reader(night)).history
    assert [r.reference for r in history.rows] == ["2026_601", "2026_602"]
    assert (history.total, history.more) == (3, 1)
    assert sum(s.count for s in history.summary) == 3


def test_page_two_of_the_operational_list_does_not_repeat_the_history_group(night, monkeypatch):
    from app.legacy_import import lovable_observability as obs

    monkeypatch.setattr(obs, "PAGE_SIZE", 1)
    first = sync_admin.differences(_reader(night), page=1)
    second = sync_admin.differences(_reader(night), page=2)
    assert first.pages > 1
    assert first.history is not None and second.history is None


def test_the_overview_counts_operational_work_and_the_history_apart(night, client):
    panel = sync_admin.lovable_panel(_reader(night))
    run = night["run"]
    every = run.decisions.filter(outcome=SyncOutcome.REVIEW_REQUIRED).count()
    assert panel.decisions[SyncOutcome.REVIEW_REQUIRED] == every - 3
    historical = panel.historical
    assert historical is not None
    assert (historical.checked, historical.review, historical.import_held) == (3, 3, 3)
    assert historical.held_decisions == 3 and historical.pending_fields == 3
    assert panel.import_held_fields == 0  # the pilot holds nothing; the history is apart
    from app.legacy_import import lovable_observability as obs

    status = obs.status(_reader(night))
    assert status.pending_review_historical == 3
    assert panel.pending_fields == status.pending_review - 3
    body = _signed(client, night).get(reverse("haldus:data_sync")).content.decode()
    assert "Ajalooline register" in body
    assert body.index("Ootel erinevusi kokku") < body.index("Ajalooline register")


def test_a_requests_run_line_takes_the_history_out_of_vajab_ulevaatamist(night):
    run = night["run"]
    counts = sync_admin.int_counts(run.counts)
    shown = sync_admin._operational_counts(counts, run)
    assert shown["review_history"] == 3
    assert shown["review"] == counts["review"] - 3
    assert LovableSyncRun.objects.get(pk=run.pk).counts["review"] == counts["review"]
    # A run without the history cohort reads exactly as before.
    assert sync_admin._operational_counts({"review": 4}, None) == {"review": 4}


def test_pending_and_resolve_are_unchanged_for_a_history_hold(night):
    reader = _reader(night)
    pending = {
        (p.reference, p.field): p
        for p in lovable_sync.pending_differences(visible_to=reader)
        if p.kind == "FIELD"
    }
    for ref in HISTORICAL:
        item = pending[(ref, "stage")]
        assert (item.outcome, item.reason, item.cohort) == (
            SyncOutcome.REVIEW_REQUIRED,
            lovable_sync.IMPORT_HELD,
            HISTORY,
        )
    row = sync_admin.difference_for(reader, "2026_601", "stage")
    assert row is not None and row.historical and row.resolvable
    with refused(sync_admin.NEEDS_REASON):
        sync_admin.resolve_difference(
            technical_admin()[0], reader, reference="2026_601", field_name="stage", note=" "
        )
    sync_admin.resolve_difference(
        technical_admin()[0], reader, reference="2026_601", field_name="stage", note="Ajalugu"
    )
    history = sync_admin.differences(reader).history
    assert [r.reference for r in history.rows] == ["2026_602", "2026_603"]


def test_an_apply_night_still_never_applies_a_history_hold(night):
    stages = {
        m.pk: m.stage for m in Matter.objects.filter(lovable_sync_state__reference__in=HISTORICAL)
    }
    lovable_sync.set_mode(SyncMode.APPLY, reason="test")
    run = _run(night, SyncTrigger.SCHEDULED, at=NIGHT + dt.timedelta(days=1))
    held = run.decisions.filter(state__cohort=HISTORY, field="stage")
    assert {(d.outcome, d.reason) for d in held} == {
        (SyncOutcome.REVIEW_REQUIRED, lovable_sync.IMPORT_HELD)
    }
    assert {
        m.pk: m.stage for m in Matter.objects.filter(lovable_sync_state__reference__in=HISTORICAL)
    } == stages
    assert sync_admin.differences(_reader(night)).history.total == 3


def test_pending_differences_reads_in_a_bounded_number_of_queries(night):
    """No per-item query: ~1,900 pending items cost what seven do."""
    with CaptureQueriesContext(connection) as few:
        items = lovable_sync.pending_differences()
    fields = [p for p in items if p.kind == "FIELD"]
    assert len(fields) >= 7
    # A second night with more differences adds rows, and no query.
    source = night["source"]
    source.edit("2026_503", teema="Uus pealkiri")
    source.edit("2026_505", kellele="Riigikogu")
    source.edit("2026_601", teema="Ajaloolise teema teine pealkiri")
    _run(night, SyncTrigger.SCHEDULED, at=NIGHT + dt.timedelta(days=1))
    with CaptureQueriesContext(connection) as again:
        later = lovable_sync.pending_differences()
    assert len([p for p in later if p.kind == "FIELD"]) > len(fields)
    assert len(again) == len(few)
    # «Since» still names the first night a difference appeared with its value.
    assert {p.since for p in later if p.cohort == HISTORY and p.field == "stage"} == {
        night["run"].started_at
    }
    assert LovableSyncState.objects.filter(cohort=HISTORY).count() == 3
