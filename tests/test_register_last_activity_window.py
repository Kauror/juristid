"""`?viimane_alates` / `?viimane_kuni` — the last substantive activity, at its precision.

The window asks about the very fact «Viimane tegevus» names (`activity.activity_of`)
and keeps four other things apart from it (docs/adr/0134, docs/adr/0200 §3):

* a period (`Kaasamine`, `Märge`, `Väline seisukoht` known to a month, quarter,
  half-year or year) is in the window only when its **whole** period is — its
  anchor, the first day, is not the day it happened;
* the native-record ``updated_at`` fallback prints something true in the column
  but is not work, so it is in no window;
* a technical edit after old work moves nothing (that is ``?muudetud_*``);
* the *latest* activity decides, not any activity in the range
  (`work_activity_between`).

Every date is in 2024, so nothing here moves with the calendar.
"""

from __future__ import annotations

import io
from datetime import date, datetime, time
from typing import Any
from zoneinfo import ZoneInfo

import openpyxl
import pytest
from django.urls import reverse
from django.utils import timezone

from app.matters import activity, register_selection
from app.matters.enums import EngagementKind
from app.matters.models import Matter
from app.matters.selectors import matter_list_queryset
from app.matters.services import (
    add_engagement,
    record_external_position,
    record_procedural_development,
)
from app.workflow.dates import period_bounds
from app.workflow.enums import DatePrecision
from tests import factories

pytestmark = pytest.mark.django_db

REGISTER = reverse("matters:matter_list")
TABLE = reverse("matters:export_table")
TALLINN = ZoneInfo("Europe/Tallinn")


def _d(value: date) -> str:
    return value.strftime("%d.%m.%Y")


def _window(start: date | None = None, end: date | None = None) -> dict[str, str]:
    params = {"olek": "koik"}
    if start:
        params["viimane_alates"] = _d(start)
    if end:
        params["viimane_kuni"] = _d(end)
    return params


def _titles(client, params) -> set[str]:
    response = client.get(REGISTER, params)
    assert response.status_code == 200
    assert "page" in response.context, "the window was refused"
    return {matter.title for matter in response.context["page"].object_list}


def _entry(matter, author, on: date, hour: int = 10):
    factories.EntryFactory(
        matter=matter,
        author=author,
        kind="MEETING",
        occurred_at=datetime.combine(on, time(hour), tzinfo=TALLINN),
    )


def _engagement(matter, actor, on: date, precision=DatePrecision.EXACT.value):
    add_engagement(
        matter=matter,
        kind=EngagementKind.EMAIL_CAMPAIGN,
        title="Küsitlus",
        occurred_on=on,
        occurred_on_precision=precision,
        actor=actor,
    )


def _marge(matter, actor, on: date, precision=DatePrecision.EXACT.value):
    record_procedural_development(
        matter=matter,
        title="Ministeerium saatis eelnõu",
        occurred_on=on,
        occurred_on_precision=precision,
        actor=actor,
    )


def _position(matter, actor, on: date, precision=DatePrecision.EXACT.value):
    record_external_position(
        matter=matter,
        organisation=factories.OrganisationFactory(),
        url="https://www.koda.ee/naide",
        stated_on=on,
        stated_on_precision=precision,
        actor=actor,
    )


def _touch(matter) -> None:
    """A technical modification today — what an edit of the title would leave."""
    Matter.objects.filter(pk=matter.pk).update(updated_at=timezone.now())


@pytest.fixture
def world(specialist):
    """One Matter per situation the window has to tell apart."""

    def matter(title: str) -> Any:
        return factories.MatterFactory(title=title, owner=specialist)

    day = matter("Päev")
    _entry(day, specialist, date(2024, 6, 15))

    month = matter("Kuu")
    _entry(month, specialist, date(2024, 3, 1))
    _engagement(month, specialist, date(2024, 6, 1), DatePrecision.MONTH.value)

    quarter = matter("Kvartal")
    _marge(quarter, specialist, date(2024, 4, 1), DatePrecision.QUARTER.value)

    year = matter("Aasta")
    _position(year, specialist, date(2024, 1, 1), DatePrecision.YEAR.value)

    native_only = matter("Ainult kirje muutmine")  # no work at all, `updated_at` today
    _touch(native_only)

    edited = matter("Vana töö, uus parandus")
    _entry(edited, specialist, date(2024, 6, 12))
    _touch(edited)

    moved_on = matter("Hiljem edasi")
    _entry(moved_on, specialist, date(2024, 6, 14))
    _entry(moved_on, specialist, date(2024, 6, 25))

    # A day and a month on the same anchor: the day is what happened
    # (BASIS_PRECEDENCE), so the row is a day.
    day_ties_month = matter("Päev võrdub kuuga")
    _entry(day_ties_month, specialist, date(2024, 6, 1))
    _engagement(day_ties_month, specialist, date(2024, 6, 1), DatePrecision.MONTH.value)

    # Two periods on one anchor: `Kaasamine` precedes `Märge`, so the month is
    # what «Viimane tegevus» names, even beside an exact `Märge` that day.
    month_ties_marge = matter("Kuu võrdub märkega")
    _engagement(month_ties_marge, specialist, date(2024, 6, 1), DatePrecision.MONTH.value)
    _marge(month_ties_marge, specialist, date(2024, 6, 1))

    return specialist


WINDOWS = [
    (date(2024, 6, 10), date(2024, 6, 20)),
    (date(2024, 6, 1), date(2024, 6, 30)),
    (date(2024, 6, 1), date(2024, 6, 15)),
    (date(2024, 6, 2), date(2024, 6, 30)),
    (date(2024, 4, 1), date(2024, 6, 30)),
    (date(2024, 4, 1), date(2024, 5, 31)),
    (date(2024, 1, 1), date(2024, 12, 31)),
    (date(2024, 1, 1), date(2024, 12, 30)),
    (date(2024, 6, 1), date(2024, 6, 1)),
    (date(2024, 6, 1), None),
    (date(2024, 6, 2), None),
    (None, date(2024, 6, 30)),
    (None, date(2024, 6, 29)),
    (None, date(2024, 12, 31)),
    (timezone.localdate(), None),
]


@pytest.mark.parametrize(("start", "end"), WINDOWS)
def test_the_window_is_the_column_s_own_fact_at_its_precision(
    signed_in, world, start: date | None, end: date | None
):
    """Held row by row against `activity_of`, the column's reading.

    The oracle: a Matter is in the window when its last activity is a
    qualifying fact (not ``NATIVE_RECORD``) whose whole period — one day for a
    day — lies between ``start`` and ``end``.
    """
    expected = set()
    for row in activity.annotate_last_activity(matter_list_queryset(world), world):
        fact = activity.activity_of(row)
        if fact is None or fact.basis == activity.ActivityBasis.NATIVE_RECORD:
            continue
        first, last = period_bounds(fact.occurred_on, fact.date_precision)
        if (start is None or first >= start) and (end is None or last <= end):
            expected.add(row.title)

    assert _titles(signed_in, _window(start, end)) == expected


def test_a_day_inside_the_window_matches(signed_in, world):
    shown = _titles(signed_in, _window(date(2024, 6, 10), date(2024, 6, 20)))

    assert "Päev" in shown
    assert "Vana töö, uus parandus" in shown


def test_a_month_is_not_its_first_day(signed_in, world):
    first_days = _titles(signed_in, _window(date(2024, 6, 1), date(2024, 6, 15)))
    whole_month = _titles(signed_in, _window(date(2024, 6, 1), date(2024, 6, 30)))
    the_first = _titles(signed_in, _window(date(2024, 6, 1), date(2024, 6, 1)))

    assert "Kuu" not in first_days
    assert "Kuu" not in the_first
    assert "Kuu" in whole_month
    # An open end holds a period that lies wholly on its side of the edge.
    assert "Kuu" in _titles(signed_in, _window(date(2024, 6, 1)))
    assert "Kuu" not in _titles(signed_in, _window(date(2024, 6, 2)))
    assert "Kuu" in _titles(signed_in, _window(end=date(2024, 6, 30)))
    assert "Kuu" not in _titles(signed_in, _window(end=date(2024, 6, 29)))


def test_a_quarter_and_a_year_need_their_whole_period(signed_in, world):
    assert "Kvartal" in _titles(signed_in, _window(date(2024, 4, 1), date(2024, 6, 30)))
    assert "Kvartal" not in _titles(signed_in, _window(date(2024, 4, 1), date(2024, 5, 31)))
    assert "Aasta" in _titles(signed_in, _window(date(2024, 1, 1), date(2024, 12, 31)))
    assert "Aasta" not in _titles(signed_in, _window(date(2024, 1, 1), date(2024, 12, 30)))


def test_a_day_tied_with_a_month_is_a_day_and_two_periods_follow_precedence(signed_in, world):
    first_day = _titles(signed_in, _window(date(2024, 6, 1), date(2024, 6, 1)))

    assert "Päev võrdub kuuga" in first_day
    assert "Kuu võrdub märkega" not in first_day
    assert "Kuu võrdub märkega" in _titles(signed_in, _window(date(2024, 6, 1), date(2024, 6, 30)))


def test_a_record_with_no_substantive_activity_is_in_no_window(signed_in, world):
    native = Matter.objects.get(title="Ainult kirje muutmine")
    fact = activity.activity_for_matter(native, world)
    today = timezone.localdate()

    # The column still prints something true for it — unchanged.
    assert fact is not None and fact.basis == activity.ActivityBasis.NATIVE_RECORD
    assert "Ainult kirje muutmine" not in _titles(signed_in, _window(today, today))
    assert "Ainult kirje muutmine" not in _titles(signed_in, _window(end=today))
    # …and it is the modification window that finds it.
    assert "Ainult kirje muutmine" in _titles(
        signed_in, {"olek": "koik", "muudetud_alates": _d(today)}
    )


def test_old_work_then_a_recent_technical_edit_is_still_old_work(signed_in, world):
    today = timezone.localdate()

    assert "Vana töö, uus parandus" not in _titles(signed_in, _window(today))
    assert "Vana töö, uus parandus" in _titles(signed_in, _window(end=date(2024, 6, 12)))


def test_the_latest_activity_decides_not_any_activity_in_the_range(signed_in, world):
    shown = _titles(signed_in, _window(date(2024, 6, 10), date(2024, 6, 20)))

    assert "Hiljem edasi" not in shown
    assert "Hiljem edasi" in _titles(signed_in, _window(date(2024, 6, 21), date(2024, 6, 30)))


def test_window_sort_and_table_export_give_one_population(signed_in, world):
    params = {
        **_window(date(2024, 1, 1), date(2024, 12, 31)),
        "jarjestus": register_selection.ACTIVITY_SORT_NEWEST,
    }

    response = signed_in.get(REGISTER, params)
    shown = [matter.title for matter in response.context["page"].object_list]
    export = signed_in.get(TABLE, params)
    assert export.status_code == 200
    sheet = openpyxl.load_workbook(io.BytesIO(export.content))["Teemad"]
    exported = [row[0].value for row in sheet.iter_rows(min_row=2)]

    assert exported == shown
    assert set(shown) == {
        "Päev",
        "Kuu",
        "Kvartal",
        "Aasta",
        "Vana töö, uus parandus",
        "Hiljem edasi",
        "Päev võrdub kuuga",
        "Kuu võrdub märkega",
    }
    # Newest first, on the column's own anchor: 25.06 before 15.06 before 12.06.
    assert shown.index("Hiljem edasi") < shown.index("Päev") < shown.index("Vana töö, uus parandus")
