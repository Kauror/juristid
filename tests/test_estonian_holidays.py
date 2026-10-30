"""Estonian public holidays and the date boxes' informational hint (docs/adr/0197)."""

from __future__ import annotations

import json
from datetime import date
from pathlib import Path

import pytest
from django.conf import settings
from django.urls import reverse

from app.core.holidays import (
    date_notice,
    easter_sunday,
    estonian_public_holidays,
    holiday_name,
)
from tests import factories


@pytest.mark.parametrize(
    ("year", "easter"),
    [
        (1990, date(1990, 4, 15)),
        (2000, date(2000, 4, 23)),
        (2019, date(2019, 4, 21)),
        (2024, date(2024, 3, 31)),
        (2025, date(2025, 4, 20)),
        (2026, date(2026, 4, 5)),
        (2027, date(2027, 3, 28)),
        (2038, date(2038, 4, 25)),
        (2100, date(2100, 3, 28)),
    ],
)
def test_easter_sunday(year, easter):
    assert easter_sunday(year) == easter


def test_a_year_has_the_twelve_statutory_holidays_in_date_order():
    assert estonian_public_holidays(2027) == {
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
    }
    assert list(estonian_public_holidays(2027)) == sorted(estonian_public_holidays(2027))


@pytest.mark.parametrize(
    ("day", "name"),
    [
        (date(2024, 3, 29), "suur reede"),
        (date(2024, 3, 31), "ülestõusmispühade 1. püha"),
        (date(2024, 5, 19), "nelipühade 1. püha"),
        (date(2025, 4, 18), "suur reede"),
        (date(2025, 6, 8), "nelipühade 1. püha"),
        (date(2026, 4, 3), "suur reede"),
        (date(2026, 5, 24), "nelipühade 1. püha"),
        (date(2026, 8, 20), "taasiseseisvumispäev"),
    ],
)
def test_movable_and_fixed_holidays_by_name(day, name):
    assert holiday_name(day) == name


def test_an_ordinary_day_is_no_holiday():
    assert holiday_name(date(2027, 6, 22)) is None
    # Easter Monday is not a riigipüha in Estonia.
    assert holiday_name(date(2027, 3, 29)) is None


@pytest.mark.parametrize(
    ("day", "message"),
    [
        (date(2027, 6, 24), "24.06.2027 valitud kuupäev on riigipüha (jaanipäev)."),
        (date(2027, 6, 26), "26.06.2027 valitud kuupäev on laupäev."),
        (date(2027, 6, 27), "27.06.2027 valitud kuupäev on pühapäev."),
        (date(2027, 3, 26), "26.03.2027 valitud kuupäev on riigipüha (suur reede)."),
        (date(2027, 2, 24), "24.02.2027 valitud kuupäev on riigipüha (iseseisvuspäev)."),
    ],
)
def test_the_notice_is_the_exact_sentence(day, message):
    assert date_notice(day) == message


def test_a_holiday_on_a_weekend_says_only_the_holiday():
    # 24.06.2028 is a Saturday; Easter Sunday is always a Sunday.
    assert date(2028, 6, 24).weekday() == 5
    assert date_notice(date(2028, 6, 24)) == (
        "24.06.2028 valitud kuupäev on riigipüha (jaanipäev)."
    )
    assert date_notice(date(2027, 3, 28)) == (
        "28.03.2027 valitud kuupäev on riigipüha (ülestõusmispühade 1. püha)."
    )


def test_a_weekday_and_no_date_say_nothing():
    assert date_notice(date(2027, 6, 25)) is None  # Friday
    assert date_notice(None) is None


@pytest.mark.parametrize(
    "day",
    [date(2027, 2, 23), date(2027, 6, 22), date(2027, 12, 23), date(2027, 12, 31)],
)
def test_shortened_working_days_on_weekdays_say_nothing(day):
    assert day.weekday() < 5
    assert date_notice(day) is None


def test_a_shortened_day_on_a_weekend_is_just_a_weekend():
    # 23.12.2028 and 31.12.2028 are a Saturday and a Sunday.
    assert date_notice(date(2028, 12, 23)) == "23.12.2028 valitud kuupäev on laupäev."
    assert date_notice(date(2028, 12, 31)) == "31.12.2028 valitud kuupäev on pühapäev."


# ---- The endpoint the browser reads ---------------------------------------


@pytest.mark.django_db
@pytest.mark.parametrize("factory", [factories.ReaderFactory, factories.UserFactory])
def test_every_signed_in_reader_gets_a_years_holidays(client, factory):
    client.force_login(factory())
    response = client.get(reverse("core:public_holidays", kwargs={"year": 2027}))

    assert response.status_code == 200
    assert response["Content-Type"].startswith("application/json")
    assert response["Cache-Control"] == "private, max-age=86400"
    body = json.loads(response.content)
    assert body["year"] == 2027
    assert body["holidays"]["2027-06-24"] == "jaanipäev"
    assert body["holidays"]["2027-03-26"] == "suur reede"
    assert body["holidays"] == {
        day.isoformat(): name for day, name in estonian_public_holidays(2027).items()
    }


@pytest.mark.django_db
@pytest.mark.parametrize("year", [1989, 2101, 1, 40000])
def test_a_year_outside_the_date_range_is_not_found(client, year):
    client.force_login(factories.ReaderFactory())
    assert client.get(reverse("core:public_holidays", kwargs={"year": year})).status_code == 404


@pytest.mark.django_db
@pytest.mark.parametrize("year", [1990, 2100])
def test_the_range_edges_answer(client, year):
    client.force_login(factories.ReaderFactory())
    assert client.get(reverse("core:public_holidays", kwargs={"year": year})).status_code == 200


@pytest.mark.django_db
def test_the_endpoint_takes_no_post(client):
    client.force_login(factories.ReaderFactory())
    assert client.post(reverse("core:public_holidays", kwargs={"year": 2027})).status_code == 405


@pytest.mark.django_db
def test_an_anonymous_caller_is_sent_to_sign_in(client, settings):
    settings.AUTH_MODE = "none"
    response = client.get(reverse("core:public_holidays", kwargs={"year": 2027}))
    assert response.status_code == 302
    assert response["Location"].startswith(reverse(settings.LOGIN_URL))


def test_the_browser_asks_the_route_this_module_answers():
    """`static/js/app.js` writes the path and the year range out; they must stay
    the route's and the date field's, or every holiday hint goes quiet."""
    from app.workflow.dates import EARLIEST_DAY, LATEST_DAY

    script = (Path(settings.BASE_DIR) / "static" / "js" / "app.js").read_text(encoding="utf-8")
    prefix = reverse("core:public_holidays", kwargs={"year": 2027}).removesuffix("2027/")
    assert f'var HOLIDAYS_URL = "{prefix}";' in script
    assert f"var NOTICE_FIRST_YEAR = {EARLIEST_DAY.year};" in script
    assert f"var NOTICE_LAST_YEAR = {LATEST_DAY.year};" in script
    # The browser computes no holiday of its own.
    assert "easter" not in script.lower()
