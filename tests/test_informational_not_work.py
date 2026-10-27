"""A planned or estimated commencement is not personal work (ADR 0120, amendment of 2026-10-27).

The reference is the Lovable pilot's 2025_218 as Minu asjad showed it on
27 October 2026: an `ESTIMATED_COMMENCEMENT` of 1 January 2026 sat at the top of
*Sel nädalal* with «299 p» beside it, ahead of the file's real `MONITOR` review
on 2 December. The work model carried the estimate as a milestone, and a past
milestone that is not overdue is "come round" — so it was placed with this
week's work.

The fix is in the population, not the page: `work_items.important_deadlines`
leaves the informational kinds out, so every band, header figure, register
population and calendar work dot built on it agrees. The record itself is
untouched — it stays on its Teema, and the Minu asjad calendar shows it under
*Jõustumised*, named as the estimate it is and never as a recorded
commencement. Synthetic data only.
"""

from __future__ import annotations

from datetime import date

import pytest
from django.urls import reverse

from app.core.enums import Visibility
from app.intelligence.enums import ImportantDateKind
from app.intelligence.models import MatterImportantDate
from app.intelligence.services import add_important_date
from app.matters import calendar as cal
from app.matters import work_items as wi
from app.matters.my_work import build_my_work
from app.workflow.enums import ActionKind, DateSemantics
from tests import factories

pytestmark = pytest.mark.django_db

TODAY = date(2026, 10, 27)
ESTIMATED = ImportantDateKind.ESTIMATED_COMMENCEMENT
PLANNED = ImportantDateKind.PLANNED_COMMENCEMENT


def _date(matter, day, *, kind, title="Eeldatav jõustumine"):
    return add_important_date(
        matter=matter, title=title, date_value=day, period_end=day, kind=kind, actor=matter.owner
    )


def _step(matter, day, *, kind=ActionKind.MONITOR, text="Jälgin menetlust"):
    semantics = DateSemantics.DEADLINE if kind == ActionKind.DO else DateSemantics.REVIEW_ON
    return factories.NextActionFactory(
        matter=matter,
        text=text,
        kind=kind,
        date_semantics=semantics,
        target_date=day,
        responsible=matter.owner,
    )


def _reference_matter(owner):
    """The 2025_218 shape: an old estimate beside a genuine future review."""
    matter = factories.MatterFactory(owner=owner, title="Näidisseaduse muutmise eelnõu")
    estimate = _date(matter, date(2026, 1, 1), kind=ESTIMATED)
    step = _step(matter, date(2026, 12, 2))
    return matter, estimate, step


def _bands(work):
    return {band.key: band for band in work.bands}


def _band_keys(work, matter):
    return {band.key for band in work.bands for item in band.items if item.matter_id == matter.pk}


def _calendar(user, subject=None, **params):
    from django.http import QueryDict

    query = QueryDict(mutable=True)
    for key, value in params.items():
        query[key] = value
    return cal.build_calendar(
        user,
        subject or user,
        params=query,
        today=TODAY,
        query_for=lambda changes: "?" + "&".join(f"{k}={v}" for k, v in changes.items() if v),
    )


# ---------------------------------------------------------------------------
# 1–4. The work population
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("kind", [ESTIMATED, PLANNED])
def test_a_months_old_informational_date_is_in_no_work_band(specialist, kind):
    matter = factories.MatterFactory(owner=specialist)
    _date(matter, date(2026, 1, 1), kind=kind)

    work = build_my_work(specialist, today=TODAY)

    assert _band_keys(work, matter) == set()
    assert not any(item.matter_id == matter.pk for item in work.items)
    assert work.overdue == 0 and work.week == 0


@pytest.mark.parametrize("kind", [ESTIMATED, PLANNED])
def test_a_future_informational_date_is_in_no_work_band_either(specialist, kind):
    matter = factories.MatterFactory(owner=specialist)
    _date(matter, date(2026, 10, 29), kind=kind)  # this week
    _date(matter, date(2026, 11, 20), kind=kind, title="Teine hinnang")  # next 30 days

    work = build_my_work(specialist, today=TODAY)

    assert _band_keys(work, matter) == set()
    assert work.beyond_horizon == 0


def test_the_reference_matter_shows_its_real_review_and_not_its_estimate(specialist):
    matter, estimate, step = _reference_matter(specialist)

    work = build_my_work(specialist, today=TODAY)
    rows = [item for band in work.bands for item in band.items if item.matter_id == matter.pk]

    # One row: the 2 December review, in a future band — not «299 p» this week.
    assert [(row.source_type, row.object_id) for row in rows] == [(wi.SOURCE_NEXT_ACTION, step.pk)]
    assert _band_keys(work, matter) == {wi.BAND_LATER}
    assert wi.BAND_WEEK not in _bands(work)
    # The estimate is untouched on the Teema.
    estimate.refresh_from_db()
    assert (estimate.kind, estimate.date_value) == (ESTIMATED, date(2026, 1, 1))


def test_the_estimate_stays_on_its_teema(client, specialist):
    matter = _reference_matter(specialist)[0]
    client.force_login(specialist)

    body = client.get(reverse("matters:matter_detail", kwargs={"pk": matter.pk})).content.decode()

    assert "Eeldatav jõustumine" in body
    assert MatterImportantDate.objects.filter(matter=matter, kind=ESTIMATED).count() == 1


# ---------------------------------------------------------------------------
# 5–6. Genuine work keeps its meaning and its order
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "kind", [ImportantDateKind.OTHER, ImportantDateKind.TRANSPOSITION_DEADLINE]
)
def test_a_genuine_overdue_milestone_is_still_overdue(specialist, kind):
    matter = factories.MatterFactory(owner=specialist)
    _date(matter, date(2026, 10, 1), kind=kind, title="Lubatud vastus")

    work = build_my_work(specialist, today=TODAY)

    assert _band_keys(work, matter) == {wi.BAND_OVERDUE}
    assert work.overdue == 1
    assert matter.pk in wi.work_population_ids(
        specialist, wi.WORK_OVERDUE, today=TODAY, responsible=specialist
    )


def test_past_reviews_and_overdue_do_keep_their_semantics_and_order(specialist):
    reviewed = factories.MatterFactory(owner=specialist, title="Ülevaatus")
    _step(reviewed, date(2026, 10, 20), kind=ActionKind.WAIT, text="Ootan ministeeriumi")
    watched = factories.MatterFactory(owner=specialist, title="Jälgimine")
    _step(watched, date(2026, 10, 22), kind=ActionKind.MONITOR)
    late = factories.MatterFactory(owner=specialist, title="Hiline")
    _step(late, date(2026, 10, 15), kind=ActionKind.DO, text="Koostan arvamuse")
    noise = factories.MatterFactory(owner=specialist, title="Hinnang")
    _date(noise, date(2026, 1, 1), kind=ESTIMATED)

    bands = _bands(build_my_work(specialist, today=TODAY))

    assert [item.matter_id for item in bands[wi.BAND_OVERDUE].items] == [late.pk]
    # Ripe reviews still lead *Sel nädalal*, oldest first, and nothing else does.
    week = [item.matter_id for item in bands[wi.BAND_WEEK].items]
    assert week == [reviewed.pk, watched.pk]
    assert all(item.is_review_ripe for item in bands[wi.BAND_WEEK].items)


# ---------------------------------------------------------------------------
# 7. Counts, bands and the register agree
# ---------------------------------------------------------------------------


def test_header_counts_bands_and_register_populations_agree(specialist):
    _reference_matter(specialist)
    only_estimate = factories.MatterFactory(owner=specialist)
    _date(only_estimate, date(2026, 3, 1), kind=PLANNED)
    real = factories.MatterFactory(owner=specialist)
    _step(real, date(2026, 10, 10), kind=ActionKind.DO)

    work = build_my_work(specialist, today=TODAY)
    bands = _bands(work)

    assert work.overdue == bands[wi.BAND_OVERDUE].total == 1
    assert work.week == 0
    for key in (wi.WORK_OVERDUE, wi.WORK_RIPE, wi.WORK_DEADLINE_THIS_WEEK):
        population = wi.work_population_ids(specialist, key, today=TODAY, responsible=specialist)
        assert only_estimate.pk not in population
    # It *does* need attention — it has no next step at all, and an estimate
    # never stands in for one (docs/adr/0120, amendment of 2026-10-19).
    assert only_estimate.pk in wi.work_population_ids(
        specialist, wi.WORK_NEEDS_ATTENTION, today=TODAY, responsible=specialist
    )
    assert wi.work_population_ids(
        specialist, wi.WORK_OVERDUE, today=TODAY, responsible=specialist
    ) == {real.pk}
    # The register list behind the figure holds the same Matter.
    from django.test import Client

    client = Client()
    client.force_login(specialist)
    listed = client.get(reverse("matters:matter_list"), {"too": wi.WORK_OVERDUE}).content.decode()
    assert real.title in listed and only_estimate.title not in listed


# ---------------------------------------------------------------------------
# 8. The calendar
# ---------------------------------------------------------------------------


def test_the_calendar_shows_an_estimate_under_jõustumised_never_as_work(specialist):
    matter = factories.MatterFactory(owner=specialist)
    record = _date(matter, date(2026, 11, 3), kind=ESTIMATED)
    passed = _date(matter, date(2026, 10, 20), kind=PLANNED, title="Eelnõus kavandatud")
    factories.EffectiveDateFactory(matter=matter, date_value=date(2026, 11, 3))

    calendar = _calendar(specialist)
    mine = {e.key: e for e in calendar.events if e.matter.pk == matter.pk}

    estimate = mine[f"{cal.ESTIMATE_KEY_PREFIX}{record.pk}"]
    assert estimate.category == cal.CATEGORY_COMMENCEMENT and estimate.is_estimate
    assert estimate.meaning == "Eeldatav jõustumine (allika hinnang)"
    assert not estimate.is_work and not estimate.is_overdue and estimate.status == ""
    # A past plan never reads as commenced.
    old = mine[f"{cal.ESTIMATE_KEY_PREFIX}{passed.pk}"]
    assert old.meaning == "Eelnõus kavandatud jõustumine" and old.status == cal.ESTIMATE_PASSED
    # Once each: no work dot for the same record.
    assert not any(
        e.is_work and e.item is not None and e.item.source_type == wi.SOURCE_IMPORTANT_DEADLINE
        for e in calendar.events
    )
    # 3 November: one recorded commencement and one estimate, counted apart.
    cell = next(day for week in calendar.weeks for day in week.days if day.day == date(2026, 11, 3))
    assert (cell.work_count, cell.commencement_count, cell.estimate_count) == (0, 1, 1)
    assert "1 jõustumine, 1 hinnanguline jõustumine" in cell.label


@pytest.mark.parametrize("view", [cal.VIEW_WEEKS, cal.VIEW_MONTH, cal.VIEW_QUARTER, cal.VIEW_YEAR])
def test_every_calendar_view_places_the_estimate_once_and_counts_no_work(specialist, view):
    matter = factories.MatterFactory(owner=specialist)
    record = _date(matter, date(2026, 11, 3), kind=ESTIMATED)

    params = {"kalender": view}
    if view == cal.VIEW_MONTH:
        params[cal.PERIOD_PARAM] = "2026-11"
    calendar = _calendar(specialist, **params)

    keys = [e.key for e in calendar.events]
    assert keys.count(f"{cal.ESTIMATE_KEY_PREFIX}{record.pk}") == 1
    if view == cal.VIEW_YEAR:
        november = calendar.year_months[10]
        assert (november.work_count, november.commencement_count, november.estimate_count) == (
            0,
            0,
            1,
        )


# ---------------------------------------------------------------------------
# 9–10. A colleague's desk and restricted records
# ---------------------------------------------------------------------------


def test_a_colleagues_desk_reads_the_same_population(specialist, department_head):
    matter, _estimate, step = _reference_matter(specialist)

    work = build_my_work(department_head, subject=specialist, today=TODAY)

    rows = [item for band in work.bands for item in band.items if item.matter_id == matter.pk]
    assert [row.object_id for row in rows] == [step.pk]
    assert work.week == 0
    events = _calendar(department_head, subject=specialist).events
    assert [e.is_estimate for e in events if e.matter.pk == matter.pk] == []  # Jan: not shown


def test_a_restricted_estimate_follows_the_readers_scope(specialist, reader, administrator):
    matter = factories.MatterFactory(owner=specialist, visibility=Visibility.RESTRICTED)
    _date(matter, date(2026, 11, 3), kind=ESTIMATED, title="piiratud hinnang")

    assert [e.text for e in _calendar(reader, subject=specialist).events] == ["piiratud hinnang"]
    assert _calendar(administrator, subject=specialist).events == []
    assert all(
        tile.total == 0
        for tile in _calendar(administrator, subject=specialist, kalender="aasta").year_months
    )
