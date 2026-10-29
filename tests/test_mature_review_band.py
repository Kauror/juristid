"""A review that has come round is this week's work; an expectation is not a review.

Owner's decision 1 of 2026-10-29 (2026-10-28 audit B-02, docs/adr/0195):

* a genuinely due review whose stated period has started — MONITOR / REVIEW_ON
  «oktoober 2026» on 29.10.2026 — belongs in **Sel nädalal**, not *Hiljem*,
  although the month has not ended. It was ripe on every other surface
  (`Ülevaatamiseks`, *Vajab sekkumist*, the `REVIEW_DUE` statistic, the row's
  «ülevaatamise aeg on käes») while the timeline filed it under *Hiljem* and
  the strip's «tegevust sel nädalal» left it out;
* an ``EXPECTED_AROUND`` expectation — WAIT, outside response «IV kvartal
  2026» — is **not** an instruction to review: it is not promoted because its
  period began, it is never an overdue deadline, and it comes round only on its
  period's last day.

Scenario D of the remediation brief is held across the readers that answer it:
the band, the strip's week figure, `WORK_RIPE` (`?too=ulevaatamiseks`),
`WORK_NEEDS_ATTENTION`, `WORK_OVERDUE`, the register's `?tegevus=ulevaatus`,
the calendar's row status and the week figure's explanation.
"""

from __future__ import annotations

import datetime

import pytest
from django.utils import timezone

from app.matters import calendar as cal
from app.matters import my_work, seis_help
from app.matters import work_items as wi
from app.matters.models import Matter
from app.matters.selectors import REVIEW_DUE, filter_by_next_action
from app.workflow.enums import ActionKind, DatePrecision, DateSemantics
from app.workflow.services import set_next_action
from tests import factories

pytestmark = pytest.mark.django_db

TODAY = datetime.date(2026, 10, 29)  # a Thursday; the week ends on 1.11


@pytest.fixture(autouse=True)
def _frozen_day(monkeypatch):
    monkeypatch.setattr(timezone, "localdate", lambda *args, **kwargs: TODAY)


def _step(owner, title, *, kind, semantics, precision, stored):
    matter = factories.MatterFactory(owner=owner, title=title)
    set_next_action(
        matter=matter,
        text=title,
        kind=kind,
        date_semantics=semantics,
        target_date=stored,
        date_precision=precision,
        responsible=owner,
        actor=owner,
    )
    return matter


def _item(user, matter, today=TODAY):
    [item] = [i for i in wi.work_items(user, today=today) if i.matter_id == matter.pk]
    return item


def _band(item, today=TODAY):
    return wi.band_of(item, today, wi.end_of_iso_week(today), None)


# ---------------------------------------------------------------------------
# The band, precision by precision, across period boundaries
# ---------------------------------------------------------------------------

#: ``(precision, stored, today, band)`` for a MONITOR / REVIEW_ON step. The
#: days a wrong rule gets wrong: the day before the period opens (not ripe:
#: *Hiljem*), its first day, a day inside it, its last day, the day after.
REVIEW_BANDS = [
    # Exact: its own day is this week's; before that a far day is *Hiljem*.
    (DatePrecision.EXACT, datetime.date(2026, 12, 20), datetime.date(2026, 10, 29), wi.BAND_LATER),
    (DatePrecision.EXACT, datetime.date(2026, 10, 29), datetime.date(2026, 10, 29), wi.BAND_WEEK),
    (DatePrecision.EXACT, datetime.date(2026, 10, 20), datetime.date(2026, 10, 29), wi.BAND_WEEK),
    # «november 2026»: not before it opens; from 1 November on.
    (DatePrecision.MONTH, datetime.date(2026, 11, 1), datetime.date(2026, 10, 31), wi.BAND_LATER),
    (DatePrecision.MONTH, datetime.date(2026, 11, 1), datetime.date(2026, 11, 1), wi.BAND_WEEK),
    (DatePrecision.MONTH, datetime.date(2026, 11, 1), datetime.date(2026, 11, 17), wi.BAND_WEEK),
    (DatePrecision.MONTH, datetime.date(2026, 11, 1), datetime.date(2026, 11, 30), wi.BAND_WEEK),
    (DatePrecision.MONTH, datetime.date(2026, 11, 1), datetime.date(2026, 12, 1), wi.BAND_WEEK),
    # «I kvartal 2027».
    (DatePrecision.QUARTER, datetime.date(2027, 1, 1), datetime.date(2026, 12, 31), wi.BAND_LATER),
    (DatePrecision.QUARTER, datetime.date(2027, 1, 1), datetime.date(2027, 1, 1), wi.BAND_WEEK),
    (DatePrecision.QUARTER, datetime.date(2027, 1, 1), datetime.date(2027, 2, 15), wi.BAND_WEEK),
    (DatePrecision.QUARTER, datetime.date(2027, 1, 1), datetime.date(2027, 3, 31), wi.BAND_WEEK),
    # «2027».
    (DatePrecision.YEAR, datetime.date(2027, 1, 1), datetime.date(2026, 12, 31), wi.BAND_LATER),
    (DatePrecision.YEAR, datetime.date(2027, 1, 1), datetime.date(2027, 1, 1), wi.BAND_WEEK),
    (DatePrecision.YEAR, datetime.date(2027, 1, 1), datetime.date(2027, 6, 30), wi.BAND_WEEK),
    (DatePrecision.YEAR, datetime.date(2027, 1, 1), datetime.date(2028, 1, 1), wi.BAND_WEEK),
]

#: The same days for a WAIT / EXPECTED_AROUND step: *Hiljem* until the
#: period's last day, then this week's — and never overdue.
EXPECTATION_BANDS = [
    (DatePrecision.EXACT, datetime.date(2026, 10, 29), datetime.date(2026, 10, 29), wi.BAND_WEEK),
    (DatePrecision.MONTH, datetime.date(2026, 11, 1), datetime.date(2026, 11, 1), wi.BAND_LATER),
    (DatePrecision.MONTH, datetime.date(2026, 11, 1), datetime.date(2026, 11, 29), wi.BAND_LATER),
    (DatePrecision.MONTH, datetime.date(2026, 11, 1), datetime.date(2026, 11, 30), wi.BAND_WEEK),
    (DatePrecision.MONTH, datetime.date(2026, 11, 1), datetime.date(2026, 12, 1), wi.BAND_WEEK),
    (DatePrecision.QUARTER, datetime.date(2027, 1, 1), datetime.date(2027, 1, 1), wi.BAND_LATER),
    (DatePrecision.QUARTER, datetime.date(2027, 1, 1), datetime.date(2027, 3, 30), wi.BAND_LATER),
    (DatePrecision.QUARTER, datetime.date(2027, 1, 1), datetime.date(2027, 3, 31), wi.BAND_WEEK),
    (DatePrecision.YEAR, datetime.date(2027, 1, 1), datetime.date(2027, 6, 30), wi.BAND_LATER),
    (DatePrecision.YEAR, datetime.date(2027, 1, 1), datetime.date(2027, 12, 31), wi.BAND_WEEK),
    (DatePrecision.YEAR, datetime.date(2027, 1, 1), datetime.date(2028, 1, 1), wi.BAND_WEEK),
]


@pytest.mark.parametrize(("precision", "stored", "today", "band"), REVIEW_BANDS)
def test_a_review_is_in_the_week_band_exactly_when_it_has_come_round(
    specialist, precision, stored, today, band
):
    matter = _step(
        specialist,
        "Ülevaatus",
        kind=ActionKind.MONITOR,
        semantics=DateSemantics.REVIEW_ON,
        precision=precision,
        stored=stored,
    )
    item = _item(specialist, matter, today)

    assert _band(item, today) == band
    # The band and the canonical review rule are one answer.
    assert (band == wi.BAND_WEEK) is (item.is_review_ripe or item.when == today)
    assert item.is_overdue is False


@pytest.mark.parametrize(("precision", "stored", "today", "band"), EXPECTATION_BANDS)
def test_an_expectation_is_not_promoted_because_its_period_began(
    specialist, precision, stored, today, band
):
    matter = _step(
        specialist,
        "Ootame vastust",
        kind=ActionKind.WAIT,
        semantics=DateSemantics.EXPECTED_AROUND,
        precision=precision,
        stored=stored,
    )
    item = _item(specialist, matter, today)

    assert _band(item, today) == band
    assert item.is_review_ripe is (band == wi.BAND_WEEK)
    assert item.is_overdue is False


def test_every_ripe_row_is_in_the_week_or_the_overdue_band(specialist):
    """`WORK_RIPE` ⊆ *Sel nädalal* ∪ *Üle tähtaja* — the audit's parity, on a mixed desk."""
    for index, (precision, stored, *_rest) in enumerate(REVIEW_BANDS + EXPECTATION_BANDS):
        _step(
            specialist,
            f"Rida {index}",
            kind=ActionKind.MONITOR if index % 2 else ActionKind.WAIT,
            semantics=DateSemantics.REVIEW_ON
            if index < len(REVIEW_BANDS)
            else (DateSemantics.EXPECTED_AROUND),
            precision=precision,
            stored=stored,
        )
    items = wi.work_items(specialist, today=TODAY)
    bands = {
        item.object_id: band.key for band in wi.band_items(items, TODAY) for item in band.items
    }

    ripe = wi.work_population_items(items, wi.WORK_RIPE, TODAY)
    assert ripe
    assert {bands[item.object_id] for item in ripe} <= {wi.BAND_WEEK, wi.BAND_OVERDUE}


# ---------------------------------------------------------------------------
# Scenario D, on every reader
# ---------------------------------------------------------------------------


@pytest.fixture
def scenario_d(specialist):
    review = _step(
        specialist,
        "Jälgin: oktoober 2026",
        kind=ActionKind.MONITOR,
        semantics=DateSemantics.REVIEW_ON,
        precision=DatePrecision.MONTH,
        stored=datetime.date(2026, 10, 1),
    )
    expected = _step(
        specialist,
        "Ootame ministeeriumi vastust: IV kvartal 2026",
        kind=ActionKind.WAIT,
        semantics=DateSemantics.EXPECTED_AROUND,
        precision=DatePrecision.QUARTER,
        stored=datetime.date(2026, 10, 1),
    )
    return review, expected


def test_scenario_d_the_review_is_current_work_and_the_expectation_is_not(specialist, scenario_d):
    review, expected = scenario_d
    review_item, expected_item = _item(specialist, review), _item(specialist, expected)

    # The band.
    assert _band(review_item) == wi.BAND_WEEK
    assert _band(expected_item) == wi.BAND_LATER
    # Neither is late; only the review is ripe.
    assert (review_item.is_overdue, expected_item.is_overdue) == (False, False)
    assert (review_item.is_review_ripe, expected_item.is_review_ripe) == (True, False)
    # The calendar's row status.
    assert cal._work_status(review_item) == "ülevaatamise aeg on käes"
    assert cal._work_status(expected_item) == ""


def test_scenario_d_counters_and_filters(specialist, department_head, scenario_d):
    review, _expected = scenario_d
    items = wi.work_items(specialist, today=TODAY)

    def ids(key):
        return wi.work_population_ids(specialist, key, today=TODAY, items=items)

    assert ids(wi.WORK_RIPE) == {review.pk}
    assert ids(wi.WORK_NEEDS_ATTENTION) == {review.pk}
    assert ids(wi.WORK_OVERDUE) == set()
    register = set(
        filter_by_next_action(
            Matter.objects.visible_to(specialist), specialist, REVIEW_DUE, TODAY
        ).values_list("pk", flat=True)
    )
    assert register == {review.pk}
    # The manager reads the same populations on the colleague's desk.
    desk = wi.work_items(department_head, today=TODAY, responsible=specialist)
    assert wi.work_population_ids(
        department_head, wi.WORK_NEEDS_ATTENTION, today=TODAY, items=desk, responsible=specialist
    ) == {review.pk}


def test_scenario_d_on_minu_asjad(specialist, scenario_d):
    review, expected = scenario_d

    page = my_work.build_my_work(specialist, TODAY)

    band_of_matter = {item.matter_id: band.key for band in page.bands for item in band.items}
    assert band_of_matter[review.pk] == wi.BAND_WEEK
    assert band_of_matter[expected.pk] == wi.BAND_LATER
    # The strip's «tegevust sel nädalal» is the week band's own total.
    week = next(figure for figure in page.seis if figure.key == "week")
    assert week.value == page.week == 1
    assert next(figure for figure in page.seis if figure.key == "overdue").value == 0


def test_the_week_figure_explains_both_rules():
    text = " ".join(seis_help.MY_WEEK_HELP.paragraphs)

    assert "alates selle perioodi esimesest päevast" in text
    assert "alles perioodi viimasel päeval" in text
    assert "ei ole tähtaeg" in text
