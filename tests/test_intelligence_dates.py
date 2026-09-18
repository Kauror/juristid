"""The period domain: what a precision means, and how it sorts.

No database. These are the rules every structured fact rests on, and getting one
of them wrong produces a *confident* wrong answer — a quarter printed as a day,
a half-year that reads as past in July — rather than an error.
"""

from __future__ import annotations

from datetime import date

import pytest

from app.workflow.dates import (
    MAX_YEAR,
    MIN_YEAR,
    InvalidPeriod,
    bounds_for,
    format_at_precision,
    half_year_bounds,
    is_approximate,
    period_bounds,
    period_starts_after,
    quarter_bounds,
)
from app.workflow.enums import DatePrecision
from app.workflow.models import NextAction

# -- rendering --------------------------------------------------------------


@pytest.mark.parametrize(
    ("value", "precision", "expected"),
    [
        (date(2026, 9, 27), DatePrecision.EXACT, "27.9.2026"),
        (date(2026, 9, 1), DatePrecision.MONTH, "september 2026"),
        (date(2026, 1, 1), DatePrecision.QUARTER, "I kvartal 2026"),
        (date(2026, 4, 1), DatePrecision.QUARTER, "II kvartal 2026"),
        (date(2026, 7, 1), DatePrecision.QUARTER, "III kvartal 2026"),
        (date(2026, 10, 1), DatePrecision.QUARTER, "IV kvartal 2026"),
        (date(2027, 1, 1), DatePrecision.HALF_YEAR, "I poolaasta 2027"),
        (date(2027, 7, 1), DatePrecision.HALF_YEAR, "II poolaasta 2027"),
        (date(2028, 1, 1), DatePrecision.YEAR, "2028"),
        # INFERRED records *where the value came from*, not that the day is
        # uncertain, so it renders as the day it is.
        (date(2026, 9, 27), DatePrecision.INFERRED, "27.9.2026"),
    ],
)
def test_a_date_is_written_at_the_precision_it_was_known_to(value, precision, expected):
    assert format_at_precision(value, precision) == expected


def test_a_missing_date_renders_as_nothing_rather_than_a_placeholder():
    assert format_at_precision(None, DatePrecision.EXACT) == ""


def test_an_approximate_date_never_prints_its_anchor_day():
    """The whole reason the anchor exists is that it is not the fact."""
    rendered = format_at_precision(date(2026, 4, 1), DatePrecision.QUARTER)
    assert "01.04" not in rendered
    assert rendered == "II kvartal 2026"


@pytest.mark.parametrize(
    ("precision", "approximate"),
    [
        (DatePrecision.EXACT, False),
        (DatePrecision.INFERRED, False),
        (DatePrecision.MONTH, True),
        (DatePrecision.QUARTER, True),
        (DatePrecision.HALF_YEAR, True),
        (DatePrecision.YEAR, True),
    ],
)
def test_which_precisions_count_as_approximate(precision, approximate):
    assert is_approximate(precision) is approximate


def test_next_action_still_renders_through_the_shared_module():
    """The refactor must not have changed what `Järgmiseks` prints."""
    action = NextAction(target_date=date(2026, 4, 1), date_precision=DatePrecision.QUARTER)
    assert action.display_date == "II kvartal 2026"
    assert action.is_approximate is True


# -- normalisation ----------------------------------------------------------


@pytest.mark.parametrize(
    ("kwargs", "precision", "expected"),
    [
        ({"exact_date": date(2026, 9, 27)}, DatePrecision.EXACT, (date(2026, 9, 27),) * 2),
        ({"year": 2026, "month": 2}, DatePrecision.MONTH, (date(2026, 2, 1), date(2026, 2, 28))),
        ({"year": 2028, "month": 2}, DatePrecision.MONTH, (date(2028, 2, 1), date(2028, 2, 29))),
        (
            {"year": 2026, "quarter": 2},
            DatePrecision.QUARTER,
            (date(2026, 4, 1), date(2026, 6, 30)),
        ),
        (
            {"year": 2026, "quarter": 4},
            DatePrecision.QUARTER,
            (date(2026, 10, 1), date(2026, 12, 31)),
        ),
        (
            {"year": 2027, "half": 2},
            DatePrecision.HALF_YEAR,
            (date(2027, 7, 1), date(2027, 12, 31)),
        ),
        ({"year": 2028}, DatePrecision.YEAR, (date(2028, 1, 1), date(2028, 12, 31))),
    ],
)
def test_a_chosen_period_normalises_to_its_first_and_last_day(kwargs, precision, expected):
    assert bounds_for(precision, **kwargs) == expected


@pytest.mark.parametrize(
    ("precision", "kwargs"),
    [
        (DatePrecision.QUARTER, {"year": 2026, "quarter": 5}),
        (DatePrecision.QUARTER, {"year": 2026, "quarter": 0}),
        (DatePrecision.HALF_YEAR, {"year": 2026, "half": 3}),
        (DatePrecision.MONTH, {"year": 2026, "month": 13}),
        (DatePrecision.YEAR, {"year": 20226}),
        (DatePrecision.YEAR, {"year": MIN_YEAR - 1}),
        (DatePrecision.YEAR, {"year": MAX_YEAR + 1}),
        (DatePrecision.EXACT, {}),
        (DatePrecision.QUARTER, {"year": 2026}),
        (DatePrecision.MONTH, {"quarter": 2}),
    ],
)
def test_an_impossible_period_is_refused_rather_than_coerced(precision, kwargs):
    with pytest.raises(InvalidPeriod):
        bounds_for(precision, **kwargs)


def test_bounds_round_trip_through_the_stored_anchor():
    """`period_bounds` must reproduce what `bounds_for` produced.

    This is the property the service-layer guard rests on: a stored anchor plus
    its precision has to be enough to recover the period, or a row could claim a
    precision its dates do not describe.
    """
    for precision, kwargs in (
        (DatePrecision.EXACT, {"exact_date": date(2026, 9, 27)}),
        (DatePrecision.MONTH, {"year": 2026, "month": 9}),
        (DatePrecision.QUARTER, {"year": 2026, "quarter": 3}),
        (DatePrecision.HALF_YEAR, {"year": 2026, "half": 2}),
        (DatePrecision.YEAR, {"year": 2026}),
    ):
        start, end = bounds_for(precision, **kwargs)
        assert period_bounds(start, precision) == (start, end)


# -- ordering ---------------------------------------------------------------


def test_the_more_precise_event_sorts_first_among_equal_starts():
    """The documented rule: order by period start, then by period end.

    II poolaasta 2026, III kvartal 2026 and 01.07.2026 all begin on 1 July. The
    end date is what separates them, and it puts the narrowest period first.
    """
    half = half_year_bounds(2026, 2)
    third_quarter = quarter_bounds(2026, 3)
    exact = bounds_for(DatePrecision.EXACT, exact_date=date(2026, 7, 1))
    assert half[0] == third_quarter[0] == exact[0]

    ordered = sorted([half, third_quarter, exact])
    assert ordered == [exact, third_quarter, half]


def test_a_wider_period_sorts_earlier_when_it_starts_earlier():
    """The year 2026 begins on 1 January, so it leads the year it covers.

    Worth stating, because "more precise first" is only the tie-break. A record
    filed to the whole of 2026 is an expectation about the start of 2026 as much
    as its end, and the ordering does not pretend otherwise.
    """
    year = bounds_for(DatePrecision.YEAR, year=2026)
    third_quarter = quarter_bounds(2026, 3)

    assert sorted([third_quarter, year]) == [year, third_quarter]


def test_quarters_sort_in_calendar_order():
    assert quarter_bounds(2027, 1) < quarter_bounds(2027, 2) < quarter_bounds(2027, 3)


def test_a_half_year_sits_at_the_start_of_the_period_it_represents():
    """Documented and deliberate: the anchor is the period's *first* day.

    II poolaasta 2027 therefore sorts with July, and — because the comparison
    that decides "has this passed" uses the period's *end* — it is not past
    until the last day of December.
    """
    start, end = half_year_bounds(2027, 2)
    assert (start, end) == (date(2027, 7, 1), date(2027, 12, 31))
    assert start >= date(2027, 7, 1)
    assert end < date(2028, 1, 1)


# -- is the whole period still ahead of us ----------------------------------
#
# The mirror of the lateness rule: that one asks whether a period has wholly
# *ended*, `period_starts_after` whether it has wholly *begun*. It exists
# because `Menetluse areng` records something that has happened, and a surface
# refusing a future step has to be able to tell «30.09.2026» — which is plainly
# still to come — from «september 2026», which covers today and says nothing of
# the kind.


@pytest.mark.parametrize(
    ("value", "precision", "expected"),
    [
        # A day, on both sides of today and on it.
        (date(2026, 9, 30), DatePrecision.EXACT, True),
        (date(2026, 9, 19), DatePrecision.EXACT, True),
        (date(2026, 9, 18), DatePrecision.EXACT, False),
        (date(2026, 9, 17), DatePrecision.EXACT, False),
        # A month: october is ahead, september is the one we are in.
        (date(2026, 10, 1), DatePrecision.MONTH, True),
        (date(2026, 9, 1), DatePrecision.MONTH, False),
        (date(2026, 8, 1), DatePrecision.MONTH, False),
        # A quarter.
        (date(2026, 10, 1), DatePrecision.QUARTER, True),
        (date(2026, 7, 1), DatePrecision.QUARTER, False),
        # A half-year. Still stored, still compared, and not offered as a chip
        # (docs/adr/0079 §7) — which is exactly why it has to be in this table.
        (date(2027, 7, 1), DatePrecision.HALF_YEAR, True),
        (date(2026, 7, 1), DatePrecision.HALF_YEAR, False),
        (date(2026, 1, 1), DatePrecision.HALF_YEAR, False),
        # A year.
        (date(2027, 1, 1), DatePrecision.YEAR, True),
        (date(2026, 1, 1), DatePrecision.YEAR, False),
        # `INFERRED` is a day, not vagueness, and behaves as `EXACT` here as
        # everywhere else in this module (docs/adr/0079 §8).
        (date(2026, 9, 30), DatePrecision.INFERRED, True),
        (date(2026, 9, 17), DatePrecision.INFERRED, False),
    ],
)
def test_a_period_is_ahead_only_when_its_first_day_is(value, precision, expected):
    assert period_starts_after(value, precision, day=date(2026, 9, 18)) is expected


def test_an_unknown_date_is_not_in_the_future():
    """Unknown is a fact the product keeps, not a value to resolve against today.

    Answering `True` would make «kuupäev teadmata» a refusable answer, and an
    undated development is the case that made `MatterProceduralDevelopment` a
    record rather than an `Entry` (docs/adr/0091 §5.2).
    """
    for precision in DatePrecision.values:
        assert period_starts_after(None, precision, day=date(2026, 9, 18)) is False


def test_a_broad_period_is_never_resolved_to_a_day_to_answer_the_question():
    """*2026* read on 18 September is not the future, and *2027* is.

    The whole reason the comparison is on the period's **start**: rejecting
    «2026» because its anchor is 1 January would be right, and rejecting it
    because somebody resolved it to 31 December would be a different rule that
    happens to agree on this example and not on the next one.
    """
    today = date(2026, 9, 18)
    assert period_starts_after(date(2026, 1, 1), DatePrecision.YEAR, day=today) is False
    assert period_starts_after(date(2027, 1, 1), DatePrecision.YEAR, day=today) is True


def test_the_answer_is_read_from_the_period_and_not_from_the_stored_number():
    """A mid-period anchor still answers for the period it stands for.

    `period_bounds` normalises, so a row stored as `2026-10-17` + `MONTH` — which
    the composer cannot produce and an import could — is read as *oktoober 2026*
    and begins on 1 October, not on the 17th.
    """
    assert (
        period_starts_after(date(2026, 9, 24), DatePrecision.MONTH, day=date(2026, 9, 18)) is False
    )
    assert (
        period_starts_after(date(2026, 11, 24), DatePrecision.MONTH, day=date(2026, 9, 18)) is True
    )
