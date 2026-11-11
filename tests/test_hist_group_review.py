# ruff: noqa: F811 - pytest fixtures imported from the sync suites are used as arguments
"""«Vaata erinevusi» keeps the historical register's own holds apart
(docs/adr/0213 §9, amendment 2026-11-10), on synthetic data.

Since the owner decision of 11.11 the historical register follows Lovable too
(§10): «lõpetatud» on a closed Teema converges, so the group that remains is the
former staff Lovable names as owners who are no Juristid users
(`HISTORY_OWNER_UNRESOLVED`). The fixture's import held the owner, as the
backfill does for a name with no account.

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
    """A closed ARCHIVE Teema of the register whose Lovable stage is «lõpetatud»
    and whose owner is former staff with no Juristid account: the import held
    the owner for review."""
    return _source_topic(
        reference,
        _row(
            reference,
            hetkeseis="lõpetatud",
            jargmiseks="",
            tegevuse_tahtaeg=None,
            vastutaja="Endine Töötaja",
        ),
        status_raw="lõpetatud",
        stage="in_force",
        lifecycle="INACTIVE",
        disposition="COMPLETED",
        next_action=None,
        deadline={"treatment": "NONE"},
        field_decisions={"vastutaja": HELD},
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
    assert (part["checked"], part["review"], part["import_held"]) == (3, 3, 0)
    held = run.decisions.filter(state__cohort=HISTORY, reason=lovable_sync.HISTORY_OWNER_UNRESOLVED)
    assert {(d.field, d.outcome) for d in held} == {("owner", SyncOutcome.REVIEW_REQUIRED)}
    assert held.count() == 3
    assert run.counts["review"] == part["review"] + run.counts["by_cohort"][
        lovable_sync.PILOT_COHORT
    ].get("review", 0)


def test_history_holds_are_their_own_group_never_operational(night):
    diff = sync_admin.differences(_reader(night))
    history = diff.history
    assert history is not None
    assert (history.total, history.teemad, history.pages, history.hidden) == (3, 3, 1, 0)
    assert [(s.field_label, s.count) for s in history.summary] == [("Vastutaja", 3)]
    assert [r.reference for r in history.rows] == list(HISTORICAL)
    assert all(r.historical and r.field == "owner" for r in history.rows)
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
    assert "2026_603" in [r.reference for r in diff.history.rows]  # its owner hold only


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


def test_every_history_hold_is_reachable_by_its_own_pages(night, monkeypatch, client):
    monkeypatch.setattr(sync_admin, "HISTORY_ROWS_SHOWN", 2)
    history = sync_admin.differences(_reader(night)).history
    assert [r.reference for r in history.rows] == ["2026_601", "2026_602"]
    assert (history.total, history.page, history.pages, history.opened) == (3, 1, 2, False)
    assert sum(s.count for s in history.summary) == 3
    last = sync_admin.differences(_reader(night), history_page=2).history
    assert [r.reference for r in last.rows] == ["2026_603"] and last.opened
    signed = _signed(client, night)
    body = signed.get(reverse("haldus:data_sync_differences")).content.decode()
    assert "ajalugu=2#ajalooline" in body
    body = signed.get(reverse("haldus:data_sync_differences") + "?ajalugu=2").content.decode()
    at = body.index('id="ajalooline"')
    assert body[at : body.index(">", at)].endswith(" open")
    assert "2026_603" in body[at:]


def test_the_history_group_stays_beside_every_operational_page(night, monkeypatch):
    from app.legacy_import import lovable_observability as obs

    monkeypatch.setattr(obs, "PAGE_SIZE", 1)
    first = sync_admin.differences(_reader(night), page=1)
    second = sync_admin.differences(_reader(night), page=2)
    assert first.pages > 1
    assert second.history is not None and second.history.total == 3
    assert not any(r.historical for r in second.rows)


def test_the_overview_counts_operational_work_and_the_history_apart(night, client):
    panel = sync_admin.lovable_panel(_reader(night))
    run = night["run"]
    every = run.decisions.filter(outcome=SyncOutcome.REVIEW_REQUIRED).count()
    assert panel.decisions[SyncOutcome.REVIEW_REQUIRED] == every - 3
    historical = panel.historical
    assert historical is not None
    assert (historical.checked, historical.review, historical.import_held) == (3, 3, 0)
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
    # Every one waits for review only on its owner hold: 603's real title change
    # is a planned write (ELIGIBLE on this COMPARE night), not a review item
    # (docs/adr/0213 §10), so all three count as the history's (per Teema).
    assert sync_admin.historical_review_teemad([run.pk]) == {run.pk: 3}
    counts = sync_admin.int_counts(run.counts)
    shown = sync_admin._operational_counts(counts, 3)
    assert (shown["review"], shown["review_history"]) == (counts["review"] - 3, 3)
    assert LovableSyncRun.objects.get(pk=run.pk).counts["review"] == counts["review"]
    # A run without the history cohort reads exactly as before.
    assert sync_admin._operational_counts({"review": 4}, 0) == {"review": 4}


def test_pending_and_resolve_are_unchanged_for_a_history_hold(night):
    reader = _reader(night)
    pending = {
        (p.reference, p.field): p
        for p in lovable_sync.pending_differences(visible_to=reader)
        if p.kind == "FIELD"
    }
    for ref in HISTORICAL:
        item = pending[(ref, "owner")]
        assert (item.outcome, item.reason, item.cohort) == (
            SyncOutcome.REVIEW_REQUIRED,
            lovable_sync.HISTORY_OWNER_UNRESOLVED,
            HISTORY,
        )
    row = sync_admin.difference_for(reader, "2026_601", "owner")
    assert row is not None and row.historical and row.resolvable
    with refused(sync_admin.NEEDS_REASON):
        sync_admin.resolve_difference(
            technical_admin()[0], reader, reference="2026_601", field_name="owner", note=" "
        )
    sync_admin.resolve_difference(
        technical_admin()[0], reader, reference="2026_601", field_name="owner", note="Ajalugu"
    )
    history = sync_admin.differences(reader).history
    assert [r.reference for r in history.rows] == ["2026_602", "2026_603"]


def test_an_apply_night_still_never_applies_a_history_hold(night):
    owners = {
        m.pk: m.owner_id
        for m in Matter.objects.filter(lovable_sync_state__reference__in=HISTORICAL)
    }
    lovable_sync.set_mode(SyncMode.APPLY, reason="test")
    run = _run(night, SyncTrigger.SCHEDULED, at=NIGHT + dt.timedelta(days=1))
    held = run.decisions.filter(state__cohort=HISTORY, field="owner")
    assert {(d.outcome, d.reason) for d in held} == {
        (SyncOutcome.REVIEW_REQUIRED, lovable_sync.HISTORY_OWNER_UNRESOLVED)
    }
    assert {
        m.pk: m.owner_id
        for m in Matter.objects.filter(lovable_sync_state__reference__in=HISTORICAL)
    } == owners
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
    assert {p.since for p in later if p.cohort == HISTORY and p.field == "owner"} == {
        night["run"].started_at
    }
    assert LovableSyncState.objects.filter(cohort=HISTORY).count() == 3


# ---------------------------------------------------------------------------
# Independent review of #610 (2026-11-10): adversarial cases
# ---------------------------------------------------------------------------


def _where(diff: Any, reference: str) -> dict[str, list[str]]:
    operational = [r for _h, rows in diff.groups for r in rows] + [
        r for card in diff.step_cards for r in card.rows
    ]
    held = diff.history.rows if diff.history else []
    return {
        "operational": sorted(r.field for r in operational if r.reference == reference),
        "history": sorted(r.field for r in held if r.reference == reference),
    }


def test_reopened_stage_in_lovable_on_a_historical_teema(night):
    """Lovable moves a closed historical topic from «lõpetatud» back to
    «Riigikogus» (inside its operational scope): a real change, planned on this
    COMPARE night as the reopening (docs/adr/0213 §10) — operational work, while
    its owner hold stays in the history group."""
    night["source"].edit("2026_602", hetkeseis="Riigikogus")
    run = _run(night, SyncTrigger.SCHEDULED, at=NIGHT + dt.timedelta(days=1))
    d = run.decisions.get(state__reference="2026_602", field="stage")
    assert (d.outcome, d.reason) == (SyncOutcome.ELIGIBLE, "")
    where = _where(sync_admin.differences(_reader(night)), "2026_602")
    assert where == {"operational": ["stage"], "history": ["owner"]}


def test_source_missing_on_a_historical_teema_is_operational(night):
    source = night["source"]
    gone = next(k for k, r in source.rows.items() if r["nr"] == "2026_601")
    del source.rows[gone]
    _run(night, SyncTrigger.SCHEDULED, at=NIGHT + dt.timedelta(days=1))
    where = _where(sync_admin.differences(_reader(night)), "2026_601")
    assert where["operational"], where


def test_other_status_pages_show_the_operational_figure(night, client):
    from app.legacy_import import lovable_observability as obs

    status = obs.status(_reader(night))
    assert status.pending_review_operational == status.pending_review - 3
    signed = _signed(client, night)
    body = signed.get(reverse("core:release_notes_lovable")).content.decode()
    assert f"Ülevaatust ootab {status.pending_review_operational} välja." in body
    assert "ajaloolises registris 3 impordil ootele jäetud välja" in body
    body = signed.get(reverse("haldus:lovable")).content.decode()
    assert f"<dd>{status.pending_review_operational}</dd>" in body
    assert "Lisaks 3 ajaloolise registri impordil ootele jäetud välja" in body


def test_haldus_lovable_lists_a_capped_number_of_changes(night, client, monkeypatch):
    from app.haldus import views

    monkeypatch.setattr(views, "LOVABLE_ROWS_SHOWN", 1)
    body = _signed(client, night).get(reverse("haldus:lovable")).content.decode()
    assert "Näidatud esimesed 1 viite järjekorras; veel" in body


@pytest.fixture
def many_history(cohort, settings):
    """25 enrolled history Teemad, each import-held, on an APPLY night."""
    refs = [f"2026_{n}" for n in range(601, 626)]
    topics = [_closed(r) for r in refs]
    package = _package(*topics)
    package["snapshot"]["snapshot_sha256"] = "f" * 64
    package["snapshot"]["highest_reference"] = {"2026": 640}
    package["reserve_through"] = {"2026": 640}
    digest = build_plan(package).digest
    _apply(package)
    apply_admin_gate(settings)
    settings.REAL_DATA_ALLOWED = False
    EvidencePilotRecord.objects.filter(package_sha256=digest).update(pilot=HISTORY)
    done = lovable_sync.enroll(
        cohort=HISTORY, package_sha256=digest, expect=25, reason="Ajalugu (testmaailm)"
    )
    assert done.seeded == 25
    for topic in topics:
        cohort["source"].put(
            topic["source"]["id"], copy.deepcopy(topic["source"]["row"]), version=3
        )
    lovable_sync.set_mode(SyncMode.APPLY, reason="test")
    return cohort


def test_breakers_ignore_import_holds_and_since_stays_the_first_night(many_history):
    first = _run(many_history, SyncTrigger.SCHEDULED)
    second = _run(many_history, SyncTrigger.SCHEDULED, at=NIGHT + dt.timedelta(days=1))
    for run in (first, second):
        held = run.decisions.filter(
            state__cohort=HISTORY, reason=lovable_sync.HISTORY_OWNER_UNRESOLVED
        )
        assert held.count() == 25
        assert not run.decisions.filter(reason=lovable_sync.BREAKER).exists()
    sinces = {p.since for p in lovable_sync.pending_differences() if p.cohort == HISTORY}
    assert sinces == {first.started_at}
    assert sync_admin.differences(many_history["world"]["mari"]).history.total == 25


def test_the_pages_read_in_as_many_queries_for_25_history_holds_as_for_5(many_history):
    """No per-item query on any page that counts or lists the holds."""
    _run(many_history, SyncTrigger.SCHEDULED)
    user = many_history["world"]["mari"]
    from app.legacy_import import lovable_observability as obs

    reads = (
        lambda: sync_admin.differences(user),
        lambda: sync_admin.lovable_panel(user),
        lambda: obs.status(user),
        lambda: lovable_sync.pending_differences(visible_to=user),
    )

    def counted() -> list[int]:
        out = []
        for read in reads:
            with CaptureQueriesContext(connection) as queries:
                read()
            out.append(len(queries))
        return out

    many = counted()
    assert sync_admin.differences(user).history.total == 25
    actor = technical_admin()[0]
    for n in range(601, 621):
        sync_admin.resolve_difference(
            actor, user, reference=f"2026_{n}", field_name="owner", note="Ajalugu"
        )
    assert sync_admin.differences(user).history.total == 5
    assert counted() == many
