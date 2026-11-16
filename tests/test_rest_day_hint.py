"""The day-off hint beside a Minu asjad date (owner, 2026-11-16).

`app.core.holidays.rest_day_hint` names the day off and the last working day
before it; a riigipüha wins over the weekend it falls on. `WorkItem.rest_day_hint`
shows it only for an exact day today or ahead.
"""

from __future__ import annotations

from datetime import date, timedelta

import pytest

from app.core.holidays import (
    easter_sunday,
    is_working_day,
    previous_working_day,
    rest_day_hint,
)
from app.matters.work_items import WorkItem
from app.workflow.enums import DatePrecision

# --- is_working_day -------------------------------------------------------


@pytest.mark.parametrize(
    ("day", "working"),
    [
        (date(2026, 11, 20), True),  # Friday
        (date(2026, 11, 23), True),  # Monday
        (date(2026, 11, 21), False),  # Saturday
        (date(2026, 11, 22), False),  # Sunday
        (date(2026, 12, 24), False),  # jõululaupäev, a Thursday
        (date(2026, 2, 24), False),  # iseseisvuspäev, a Tuesday
        (date(2026, 4, 3), False),  # suur reede
        (date(2026, 4, 6), True),  # Easter Monday is not a riigipüha in Estonia
    ],
)
def test_is_working_day(day, working):
    assert is_working_day(day) is working


def test_easter_2026_is_5_april():
    assert easter_sunday(2026) == date(2026, 4, 5)


# --- previous_working_day -------------------------------------------------


def test_previous_working_day_skips_the_weekend():
    assert previous_working_day(date(2026, 11, 23)) == date(2026, 11, 20)


def test_previous_working_day_skips_the_christmas_run():
    # 24, 25, 26 December 2026 (Thu–Sat) and Sunday 27: back to Wednesday 23.
    assert previous_working_day(date(2026, 12, 27)) == date(2026, 12, 23)


def test_previous_working_day_skips_good_friday_and_easter_weekend():
    # Easter Sunday 5 April 2026: Saturday 4 and suur reede 3 April are off.
    assert previous_working_day(date(2026, 4, 6)) == date(2026, 4, 2)


def test_previous_working_day_is_strictly_before():
    assert previous_working_day(date(2026, 11, 20)) == date(2026, 11, 19)


# --- rest_day_hint --------------------------------------------------------


def test_a_working_day_has_no_hint():
    assert rest_day_hint(date(2026, 11, 20)) == ""


def test_saturday():
    assert rest_day_hint(date(2026, 11, 21)) == "Laupäev. Eelmine tööpäev on reede 20.11."


def test_sunday():
    assert rest_day_hint(date(2026, 11, 22)) == "Pühapäev. Eelmine tööpäev on reede 20.11."


def test_joululaupaev():
    assert (
        rest_day_hint(date(2026, 12, 24))
        == "Riigipüha (jõululaupäev). Eelmine tööpäev on kolmapäev 23.12."
    )


def test_a_holiday_on_a_weekend_names_the_holiday():
    # 26 December 2026 is a Saturday; the holiday wins.
    assert (
        rest_day_hint(date(2026, 12, 26))
        == "Riigipüha (teine jõulupüha). Eelmine tööpäev on kolmapäev 23.12."
    )


def test_suur_reede():
    assert (
        rest_day_hint(date(2026, 4, 3))
        == "Riigipüha (suur reede). Eelmine tööpäev on neljapäev 02.04."
    )


def test_easter_sunday_reaches_back_past_good_friday():
    assert (
        rest_day_hint(date(2026, 4, 5))
        == "Riigipüha (ülestõusmispühade 1. püha). Eelmine tööpäev on neljapäev 02.04."
    )


def test_the_monday_after_easter_is_a_working_day():
    assert rest_day_hint(date(2026, 4, 6)) == ""


def test_iseseisvuspaev():
    assert (
        rest_day_hint(date(2026, 2, 24))
        == "Riigipüha (iseseisvuspäev). Eelmine tööpäev on esmaspäev 23.02."
    )


def test_new_year_reaches_back_into_the_old_year():
    # 1 January 2028 is a Saturday; 31 December 2027 is a Friday.
    assert rest_day_hint(date(2028, 1, 1)) == (
        "Riigipüha (uusaasta). Eelmine tööpäev on reede 31.12."
    )


# --- WorkItem.rest_day_hint ----------------------------------------------

TODAY = date(2026, 11, 16)  # a Monday


def _item(when, *, period_end=None, precision=DatePrecision.EXACT, today=TODAY) -> WorkItem:
    return WorkItem(
        source_type="next_action",
        object_id=1,
        matter=None,  # type: ignore[arg-type]
        responsible=None,
        action_kind="DO",
        date_semantics="DEADLINE",
        when=when,
        period_end=period_end,
        date_precision=precision,
        display_date="",
        meaning="",
        text="Samm",
        is_overdue=False,
        is_review_ripe=False,
        today=today,
    )


def test_work_item_on_a_saturday_ahead_has_the_hint():
    assert _item(date(2026, 11, 21)).rest_day_hint == "Laupäev. Eelmine tööpäev on reede 20.11."


def test_work_item_on_a_rest_day_today_has_the_hint():
    sunday = date(2026, 11, 22)
    assert _item(sunday, today=sunday).rest_day_hint.startswith("Pühapäev.")


def test_work_item_on_a_working_day_has_none():
    assert _item(date(2026, 11, 20)).rest_day_hint == ""


def test_work_item_in_the_past_has_none():
    assert _item(TODAY - timedelta(days=2)).rest_day_hint == ""  # Saturday 14.11


def test_work_item_with_an_approximate_date_has_none():
    # A period starting on Sunday 29.11.2026 names no day.
    item = _item(
        date(2026, 11, 29),
        period_end=date(2026, 11, 30),
        precision=DatePrecision.MONTH,
    )
    assert item.is_approximate
    assert item.rest_day_hint == ""


def test_work_item_without_a_date_has_none():
    assert _item(None).rest_day_hint == ""
