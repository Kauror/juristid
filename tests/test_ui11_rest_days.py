"""UI-11: weekends and Estonian public holidays on every calendar (owner's brief §14).

One provider, `app.core.holidays.rest_day`, read by every grid the calendar
draws (four weeks, month, quarter, and the panel), on Minu asjad and on Osakond.
The rules are the Pühade ja tähtpäevade seadus as verified on Riigi Teataja on
2026-11-06 (https://www.riigiteataja.ee/akt/120122022005, RT I, 20.12.2022, 5).
Presentation only: nothing is disabled, moved or counted differently.
"""

from __future__ import annotations

from datetime import date, timedelta

import pytest
from django.http import QueryDict
from django.urls import reverse
from django.utils import timezone

from app.core.holidays import (
    NATIONAL_DAY,
    PUBLIC_HOLIDAY,
    easter_sunday,
    estonian_public_holidays,
    holiday_kind,
    rest_day,
)
from app.matters import calendar as cal

#: Every holiday 2025–2028, from the act read on 2026-11-06, written out by
#: hand so the test does not restate the algorithm it checks.
EXPECTED = {
    2025: {
        date(2025, 1, 1): "uusaasta",
        date(2025, 2, 24): "iseseisvuspäev",
        date(2025, 4, 18): "suur reede",
        date(2025, 4, 20): "ülestõusmispühade 1. püha",
        date(2025, 5, 1): "kevadpüha",
        date(2025, 6, 8): "nelipühade 1. püha",
        date(2025, 6, 23): "võidupüha",
        date(2025, 6, 24): "jaanipäev",
        date(2025, 8, 20): "taasiseseisvumispäev",
        date(2025, 12, 24): "jõululaupäev",
        date(2025, 12, 25): "esimene jõulupüha",
        date(2025, 12, 26): "teine jõulupüha",
    },
    2026: {
        date(2026, 1, 1): "uusaasta",
        date(2026, 2, 24): "iseseisvuspäev",
        date(2026, 4, 3): "suur reede",
        date(2026, 4, 5): "ülestõusmispühade 1. püha",
        date(2026, 5, 1): "kevadpüha",
        date(2026, 5, 24): "nelipühade 1. püha",
        date(2026, 6, 23): "võidupüha",
        date(2026, 6, 24): "jaanipäev",
        date(2026, 8, 20): "taasiseseisvumispäev",
        date(2026, 12, 24): "jõululaupäev",
        date(2026, 12, 25): "esimene jõulupüha",
        date(2026, 12, 26): "teine jõulupüha",
    },
    2027: {
        date(2027, 1, 1): "uusaasta",
        date(2027, 2, 24): "iseseisvuspäev",
        date(2027, 3, 26): "suur reede",
        date(2027, 3, 28): "ülestõusmispühade 1. püha",
        date(2027, 5, 1): "kevadpüha",
        date(2027, 5, 16): "nelipühade 1. püha",
        date(2027, 6, 23): "võidupüha",
        date(2027, 6, 24): "jaanipäev",
        date(2027, 8, 20): "taasiseseisvumispäev",
        date(2027, 12, 24): "jõululaupäev",
        date(2027, 12, 25): "esimene jõulupüha",
        date(2027, 12, 26): "teine jõulupüha",
    },
    2028: {
        date(2028, 1, 1): "uusaasta",
        date(2028, 2, 24): "iseseisvuspäev",
        date(2028, 4, 14): "suur reede",
        date(2028, 4, 16): "ülestõusmispühade 1. püha",
        date(2028, 5, 1): "kevadpüha",
        date(2028, 6, 4): "nelipühade 1. püha",
        date(2028, 6, 23): "võidupüha",
        date(2028, 6, 24): "jaanipäev",
        date(2028, 8, 20): "taasiseseisvumispäev",
        date(2028, 12, 24): "jõululaupäev",
        date(2028, 12, 25): "esimene jõulupüha",
        date(2028, 12, 26): "teine jõulupüha",
    },
}


@pytest.mark.parametrize("year", sorted(EXPECTED))
def test_every_holiday_2025_to_2028(year):
    assert estonian_public_holidays(year) == EXPECTED[year]


@pytest.mark.parametrize("year", range(2025, 2029))
def test_easter_days_are_a_friday_and_a_sunday_and_pentecost_a_sunday(year):
    easter = easter_sunday(year)
    assert easter.weekday() == 6
    assert rest_day(easter - timedelta(days=2)).holiday == "suur reede"
    assert (easter - timedelta(days=2)).weekday() == 4
    pentecost = easter + timedelta(days=49)
    assert pentecost.weekday() == 6
    assert rest_day(pentecost).holiday == "nelipühade 1. püha"


@pytest.mark.parametrize("year", range(2025, 2029))
def test_no_easter_monday_no_maundy_thursday_no_second_pentecost(year):
    """No day the act does not list: Estonia has no Easter Monday holiday."""
    easter = easter_sunday(year)
    for offset in (-3, 1, 50):
        assert rest_day(easter + timedelta(days=offset)).holiday is None


def test_jouululaupaev_is_a_rest_day_and_new_years_eve_is_not():
    """24.12 has been a riigipüha since RT I 2005, 24, 185; 31.12 is in no list."""
    christmas_eve = rest_day(date(2026, 12, 24))  # a Thursday
    assert christmas_eve.is_holiday and christmas_eve.is_rest_day
    assert christmas_eve.label == "jõululaupäev (riigipüha)"

    new_years_eve = rest_day(date(2026, 12, 31))  # a Thursday
    assert not new_years_eve.is_holiday
    assert not new_years_eve.is_rest_day
    assert new_years_eve.label == ""


@pytest.mark.parametrize(
    "day",
    [
        date(2026, 1, 6),  # kolmekuningapäev, a flag day
        date(2027, 6, 14),  # leinapäev
        date(2026, 11, 16),  # taassünni päev
        date(2026, 1, 30),  # eesti kirjanduse päev, added in 2022 as a tähtpäev
        date(2026, 2, 23),  # the shortened working day before iseseisvuspäev
    ],
)
def test_flag_days_and_shortened_days_are_ordinary_days(day):
    assert day.weekday() < 5
    assert not rest_day(day).is_rest_day


def test_a_holiday_on_a_weekend_gets_no_substitute_monday():
    # 2027: 25–26.12 are a Saturday and a Sunday; Monday 27.12 is a working day.
    assert date(2027, 12, 25).weekday() == 5
    assert not rest_day(date(2027, 12, 27)).is_rest_day
    # 2028: jaanipäev is a Saturday, võidupüha a Friday; Monday 26.06 is ordinary.
    assert not rest_day(date(2028, 6, 26)).is_rest_day


def test_the_year_boundary_belongs_to_each_own_year():
    assert rest_day(date(2026, 12, 31)).holiday is None
    assert rest_day(date(2027, 1, 1)).holiday == "uusaasta"
    assert rest_day(date(2027, 12, 31)).holiday is None
    assert rest_day(date(2028, 1, 1)).label == "uusaasta (riigipüha)"


def test_the_national_day_is_named_as_the_act_names_it():
    assert holiday_kind(date(2027, 2, 24)) == NATIONAL_DAY
    assert rest_day(date(2027, 2, 24)).label == "iseseisvuspäev (rahvuspüha)"
    assert holiday_kind(date(2027, 6, 24)) == PUBLIC_HOLIDAY
    assert holiday_kind(date(2027, 6, 25)) is None


def test_weekends_are_rest_days_without_a_name():
    saturday = rest_day(date(2026, 11, 7))
    sunday = rest_day(date(2026, 11, 8))
    monday = rest_day(date(2026, 11, 9))
    assert saturday.is_weekend and saturday.is_rest_day and saturday.label == ""
    assert sunday.is_weekend and sunday.is_rest_day
    assert not monday.is_rest_day


def test_the_provider_is_deterministic():
    assert rest_day(date(2026, 4, 3)) == rest_day(date(2026, 4, 3))


# ---------------------------------------------------------------------------
# The calendars read it
# ---------------------------------------------------------------------------


def _build(user, subject, today, **params):
    query = QueryDict(mutable=True)
    for key, value in params.items():
        query[key] = value
    return cal.build_calendar(
        user, subject, params=query, today=today, query_for=lambda changes: "?"
    )


def _cell(calendar, day):
    grids = [w for g in calendar.months for w in g.weeks] + list(calendar.weeks)
    return next(c for week in grids for c in week.days if c.day == day and c.in_period)


@pytest.mark.django_db
@pytest.mark.parametrize("department", [False, True])
@pytest.mark.parametrize(
    "params",
    [
        {"kalender": "kuu", "kalendri_aeg": "2026-12"},
        {"kalender": "kuu", "kalendri_aeg": "2026-12", "kalendri_aken": "1"},
        {"kalender": "kvartal", "kalendri_aeg": "2026-K4"},
        {"kalendri_aeg": "2026-12-21"},
    ],
)
def test_every_grid_marks_rest_days_from_the_one_provider(specialist, department, params):
    calendar = _build(specialist, None if department else specialist, date(2026, 11, 6), **params)

    christmas_eve = _cell(calendar, date(2026, 12, 24))
    assert christmas_eve.is_rest_day
    assert christmas_eve.holiday_label == "jõululaupäev (riigipüha)"
    assert christmas_eve.label.startswith("24.12.2026, jõululaupäev (riigipüha): ")
    assert christmas_eve.title.endswith("· jõululaupäev (riigipüha)")

    sunday = _cell(calendar, date(2026, 12, 27))
    assert sunday.is_rest_day and sunday.holiday_label == ""
    assert not _cell(calendar, date(2026, 12, 23)).is_rest_day


@pytest.mark.django_db
def test_a_busy_holiday_keeps_its_events_and_says_its_name(specialist):
    from tests import factories

    matter = factories.MatterFactory(owner=specialist)
    factories.NextActionFactory(
        matter=matter, target_date=date(2026, 12, 24), responsible=specialist
    )
    calendar = _build(
        specialist, specialist, date(2026, 11, 6), kalender="kuu", kalendri_aeg="2026-12"
    )

    cell = _cell(calendar, date(2026, 12, 24))
    assert cell.work_count == 1
    assert cell.label == "24.12.2026, jõululaupäev (riigipüha): 1 tähtaeg või tegevus"


@pytest.fixture
def frozen(monkeypatch):
    monkeypatch.setattr(timezone, "localdate", lambda *args, **kwargs: date(2026, 11, 6))


@pytest.mark.django_db
@pytest.mark.parametrize("page", [reverse("matters:my_work"), "/osakond/"])
def test_the_html_names_a_holiday_for_hover_keyboard_and_touch(client, specialist, frozen, page):
    client.force_login(specialist)

    body = client.get(page, {"kalender": "kuu", "kalendri_aeg": "2026-12"}).content.decode()

    # An empty holiday is a disclosure: a summary a keyboard reaches and a tap opens.
    assert '<details class="wcal__day is-rest is-holiday" data-uxpopover>' in body
    assert 'aria-label="24.12.2026, jõululaupäev (riigipüha): sündmusi ei ole"' in body
    assert 'title="jõululaupäev (riigipüha)"' in body
    assert "Neljapäev 24.12.2026 · jõululaupäev (riigipüha)" in body
    # A weekend is red and plain; Saturday and Sunday headings are red too.
    assert "wcal__day wcal__day--empty is-rest" in body
    assert body.count("wcal__head wcal__head--rest") == 2
    # An ordinary weekday says nothing.
    assert "23.12.2026, " not in body


def test_the_calendar_reads_the_shared_provider():
    """One provider: the calendar asks `app.core.holidays` and defines no rule of its own."""
    import inspect

    source = inspect.getsource(cal)
    assert "from app.core.holidays import RestDay, rest_day" in source
    assert "easter" not in source.casefold()
