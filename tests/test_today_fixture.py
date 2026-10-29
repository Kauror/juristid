"""The opt-in `today` fixture (tests/conftest.py, audit 2026-10-28 E-05).

What it promises: a fixed default, a day of the test's choosing through
indirect parametrisation, and no frozen clock for anybody who did not ask.
Shown against a real rule that already takes `today` explicitly
(`app.matters.calendar.default_anchor`), at the boundaries `date.today()`
would only reach by luck.
"""

from __future__ import annotations

from datetime import date

import pytest

from app.matters import calendar
from tests.conftest import TEST_TODAY


def test_the_default_is_the_fixed_day(today: date) -> None:
    assert today == TEST_TODAY
    assert today.weekday() == 2, "a Wednesday: room on both sides of the week"


@pytest.mark.parametrize(
    ("today", "quarter_start"),
    [
        (date(2026, 9, 30), date(2026, 7, 1)),
        (date(2026, 10, 1), date(2026, 10, 1)),
        (date(2026, 12, 31), date(2026, 10, 1)),
        (date(2027, 1, 1), date(2027, 1, 1)),
    ],
    indirect=["today"],
)
def test_a_boundary_is_chosen_not_waited_for(today: date, quarter_start: date) -> None:
    assert calendar.default_anchor(calendar.VIEW_QUARTER, today) == quarter_start
