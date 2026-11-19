"""Quality audit 2026-11, package G: the Lovable and OneNote pages' cost.

* QA26-PERF-01 — «Valmisolek» (Uuendused → Lovable, Haldus → Lovable) planned
  every synchronised Teema with ~10 queries each: 24 s and 24,400 statements on
  production's 2,370 Teemad. It now reads Juristid's values in bulk.
* QA26-PERF-02 — Uuendused → Lovable compared every stored night again on every
  view (4.6 s). A night pair's difference is now worked out once per worker.
* QA26-PERF-03 — Haldus → OneNote «ootel» read every held page's text and blocks
  to print one page of 25 (6 s). The list now leaves them unread.
"""

from __future__ import annotations

import datetime as dt

import pytest
from django.db import connection
from django.test.utils import CaptureQueriesContext

from app.legacy_import import lovable_observability as obs
from app.legacy_import import lovable_sync
from app.legacy_import.lovable_sync_models import LovableSyncRun, LovableSyncState, SyncMode
from tests import factories
from tests.lovable_observability_support import Pilot, control, night_at
from tests.test_ls2_c_onenote_holds import NEW_BYTES, _later, world  # noqa: F401  (fixture)

pytestmark = pytest.mark.django_db

LATER = night_at(3) + dt.timedelta(hours=2)


@pytest.fixture
def reader():
    return factories.UserFactory(display_name="Lugeja Näidis")


def _world(size: int, base: int) -> Pilot:
    pilot = Pilot()
    for number in range(base + 1, base + size + 1):
        pilot.add(number)
    pilot.night(night_at(0))
    for number in range(base + 1, base + size + 1):
        pilot.edit(f"2026_{number}", hetkeseis="Valitsuses", jargmiseks=f"Uus {number}")
    pilot.night(night_at(1))
    return pilot


def _queries(call) -> int:
    with CaptureQueriesContext(connection) as queries:
        call()
    return len(queries.captured_queries)


def test_readiness_asks_nothing_per_teema(reader):
    """PERF-01: the plan rows of 20 Teemad cost what those of 3 cost."""
    control(SyncMode.COMPARE)
    _world(3, 800)
    small_answer = obs.readiness(reader, now=LATER)
    small = _queries(lambda: obs.readiness(reader, now=LATER))
    LovableSyncState.objects.all().delete()
    LovableSyncRun.objects.all().delete()
    _world(20, 900)
    large_answer = obs.readiness(reader, now=LATER)
    large = _queries(lambda: obs.readiness(reader, now=LATER))
    # The simulation really planned every Teema (not a vacuous constant).
    assert sum(small_answer.simulation.values()) > 0
    assert sum(large_answer.simulation.values()) > sum(small_answer.simulation.values())
    assert large - small <= 3, (small, large)


def test_conditional_values_bulk_is_each_teemas_own(reader):
    """The bulk reader gives every Teema its own values, never a neighbour's."""
    from app.intelligence.enums import EffectiveDateKind, FactStatus
    from app.intelligence.models import MatterEffectiveDate, MatterImportantDate
    from app.matters.models import MatterResponseDeadline

    control(SyncMode.COMPARE)
    pilot = _world(4, 700)
    states = list(LovableSyncState.objects.select_related("matter").order_by("reference"))
    first, second, third, _fourth = (s.matter for s in states)
    day = night_at(3).date()
    MatterEffectiveDate.objects.create(
        matter=first,
        kind=EffectiveDateKind.KNOWN_DATE,
        date_value=day,
        period_end=day,
        status=FactStatus.ACTIVE,
    )
    MatterImportantDate.objects.create(
        matter=second,
        kind="PLANNED_COMMENCEMENT",
        date_value=night_at(4).date(),
        period_end=night_at(4).date(),
        title="Kavandatud jõustumine",
        status=FactStatus.ACTIVE,
        source_text=lovable_sync.LOVABLE_COMMENCEMENT_MARK,
    )
    MatterResponseDeadline.objects.create(
        matter=third, deadline=night_at(5).date(), outcome="ANSWERED"
    )
    third.superseded_by = second
    third.save(update_fields=["superseded_by"])
    del pilot
    matters = [s.matter for s in LovableSyncState.objects.select_related("matter")]
    bulk = lovable_sync.conditional_values_bulk(matters)
    for matter in matters:
        assert bulk[matter.pk] == lovable_sync.conditional_values(matter), matter.pk
    assert bulk[first.pk]["commencements"]
    assert bulk[second.pk]["lovable_commencements"]
    assert not bulk[first.pk]["lovable_commencements"]
    assert bulk[third.pk]["request_history"] == 1 and bulk[first.pk]["request_history"] == 0
    assert bulk[third.pk][lovable_sync.CONTINUATION] == [
        str(LovableSyncState.objects.get(matter=second).source_id)
    ]
    assert bulk[first.pk][lovable_sync.CONTINUATION] == []


# -- QA26-PERF-03: the OneNote «ootel» list ---------------------------------------------

#: Columns a list of held items never prints (`onenote_coverage._LIST_DEFERRED_*`).
_HEAVY = ('"derived_text"', '"blocks"', '"links"', '"context"', '"error_detail"', '"detail"')


def test_the_holds_list_reads_no_page_text_or_context(world, reader):  # noqa: F811
    """PERF-03: a page of held items never loads the pages' text, blocks or the
    readings' context — not when listed, not when its row is printed."""
    from app.legacy_import import onenote_coverage as cov
    from app.legacy_import.onenote_coverage_models import HoldScope

    _later(world, "cap-2", NEW_BYTES)
    with CaptureQueriesContext(connection) as queries:
        items, _hidden, counts = cov.held_items(viewer=reader)
        before = len(queries.captured_queries)
        # What the list template prints of each item.
        printed = [
            (
                i.page.source_section,
                i.page.title,
                i.resource.original_filename,
                [t.reference for t in i.teemad],
                i.category_label,
                i.reason_label,
                i.final,
            )
            for i in items
        ]
    assert {i.scope for i in items} == {
        HoldScope.SHARED_FILE,
        HoldScope.HELD_IMPORT,
        HoldScope.HELD_REVISION,
    }
    assert len(printed) == len(items) and sum(counts.values()) == len(items)
    assert len(queries.captured_queries) == before, "printing a row read more"
    read = [q["sql"] for q in queries.captured_queries]
    assert not [c for c in _HEAVY if any(c in sql for sql in read)], read
    # One item's own page still has everything.
    one = cov.held_item(next(i.key for i in items if i.revision is not None), viewer=reader)
    assert one.context_available is not None and one.detail is not None


# -- QA26-PERF-02: Uuendused → Lovable compares a stored night once --------------------


def test_a_second_view_neither_reads_nor_compares_the_stored_nights(reader, monkeypatch):
    """PERF-02: the landing page's numbers come from nights already compared; the
    second view reads no snapshot document and canonicalises no topic."""
    from app.legacy_import import lovable_review

    control(SyncMode.COMPARE)
    pilot = _world(3, 600)
    pilot.night(night_at(2))
    first = obs.status(reader, now=LATER)
    calls = {"canonical": 0}
    real = lovable_review.canonical

    def counting(bundle):
        calls["canonical"] += 1
        return real(bundle)

    monkeypatch.setattr(lovable_review, "canonical", counting)
    with CaptureQueriesContext(connection) as queries:
        second = obs.status(reader, now=LATER)
    assert calls["canonical"] == 0
    assert not [q["sql"] for q in queries.captured_queries if '"document"' in q["sql"]]
    assert second.as_dict() == first.as_dict()
    # A new night is a new pair: compared once, and its change is seen.
    pilot.edit("2026_601", jargmiseks="Veel uuem")
    pilot.night(night_at(3))
    third = obs.status(reader, now=LATER + dt.timedelta(days=1))
    assert calls["canonical"] > 0
    assert third.as_dict() != second.as_dict()


def test_the_landing_page_reads_only_the_decision_columns_it_compares(reader):
    """PERF-02 after r77: every decision row's baseline/current JSON and its run's
    counts, and every Teema's observing run's counts, were decoded on each view
    (54,000 JSON documents on production). The comparison reads none of them.
    (The Teemad's own applied/observed values ARE read: the pending fields need them.)"""
    control(SyncMode.COMPARE)
    pilot = _world(3, 500)
    pilot.night(night_at(2))
    first = obs.status(reader, now=LATER)
    with CaptureQueriesContext(connection) as queries:
        second = obs.status(reader, now=LATER)
        nights = obs.nights(reader, now=LATER)
    read = [q["sql"] for q in queries.captured_queries]
    decision_reads = [sql for sql in read if 'FROM "legacy_import_lovablesyncdecision"' in sql]
    state_reads = [sql for sql in read if 'FROM "legacy_import_lovablesyncstate"' in sql]
    assert decision_reads
    named = [
        column
        for column in (
            '"legacy_import_lovablesyncdecision"."baseline"',
            '"legacy_import_lovablesyncdecision"."current"',
            '"legacy_import_lovablesyncrun"."counts"',
        )
        if any(column in sql for sql in decision_reads)
    ] + [
        "state join: run counts"
        for sql in state_reads
        if '"legacy_import_lovablesyncrun"."counts"' in sql
    ]
    assert named == [], named
    assert second.as_dict() == first.as_dict()
    assert nights.items and any(r.topics for r in nights.items)


def test_a_night_lists_its_first_topics_and_says_how_many_more(reader, monkeypatch):
    """PERF-02 after r77: «Öised võrdlused» was 1.2 MB because a cohort night listed
    ~1,850 topics. It lists `NIGHT_TOPICS_SHOWN`, says how many more, keeps counts whole."""
    from django.test import Client
    from django.urls import reverse

    from app.core import views as core_views

    control(SyncMode.COMPARE)
    pilot = _world(3, 400)
    pilot.night(night_at(2))
    monkeypatch.setattr(core_views, "_lovable_now", lambda: LATER)
    monkeypatch.setattr(core_views, "NIGHT_TOPICS_SHOWN", 2)
    client = Client()
    client.force_login(reader)
    body = client.get(reverse("core:release_notes_lovable"), {"vaade": "oised"}).content.decode()
    sections = body.count('<section class="lovnight__topic"')
    assert sections == 2 * body.count("Näidatud 2 teemat 3-st")
    assert "Näidatud 2 teemat 3-st. Ülejäänud 1 leiad" in body
    assert "<dd>3</dd>" in body  # «Muudetud teemasid» still counts all three
