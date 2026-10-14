"""«Järgmise 30 päeva jooksul» is exactly 30 calendar dates, today included.

Owner's decision (QA remediation 2026-10-14, docs/adr/0158 §4 amendment): the
first Jõustumised bucket is ``today … today + 29`` on the Europe/Tallinn
calendar — the rule «N päeva» follows on Tehtud and Muudatused (ADR 0159 §5).
It used to reach ``today + 30``, 31 dates (QA DC-3).

Precision handling is unchanged: a day is in the bucket when it falls inside;
a month, quarter, half-year or year only when the *whole* period does
(containment). These tests pin the new edge across the clock changes, the leap
day, the turn of the year and Tallinn midnight.
"""

from __future__ import annotations

import datetime
from zoneinfo import ZoneInfo

import pytest
from django.urls import reverse
from django.utils import timezone

from app.intelligence import commencements
from app.intelligence.enums import EffectiveDateKind
from app.workflow.enums import DatePrecision
from tests import factories

TALLINN = ZoneInfo("Europe/Tallinn")
UTC = datetime.UTC
D = datetime.date


def _record(start, end=None, precision=DatePrecision.EXACT):
    return factories.EffectiveDateFactory.build(
        kind=EffectiveDateKind.KNOWN_DATE,
        date_value=start,
        period_end=end or start,
        date_precision=precision,
    )


def _bucket(today, start, end=None, precision=DatePrecision.EXACT):
    return commencements.bucket_of(_record(start, end, precision), today)


@pytest.mark.parametrize(
    "today",
    [
        D(2026, 10, 14),
        D(2026, 3, 29),  # spring forward in Tallinn
        D(2026, 10, 25),  # fall back in Tallinn
        D(2026, 3, 1),
        D(2028, 2, 1),  # leap February ahead
        D(2028, 2, 29),  # the leap day itself
        D(2026, 12, 15),  # across the year end
        D(2026, 12, 31),
    ],
)
def test_the_bucket_is_exactly_thirty_dates_including_today(today):
    inside = [
        today + datetime.timedelta(days=n)
        for n in range(-1, 32)
        if _bucket(today, today + datetime.timedelta(days=n)) == commencements.BUCKET_SOON
    ]
    assert len(inside) == 30
    assert inside[0] == today
    assert inside[-1] == today + datetime.timedelta(days=29) == commencements.soon_last_day(today)
    assert _bucket(today, today + datetime.timedelta(days=30)) == commencements.BUCKET_LATER
    assert _bucket(today, today - datetime.timedelta(days=1)) == commencements.BUCKET_PAST


@pytest.mark.parametrize(
    ("today", "last"),
    [
        (D(2026, 3, 29), D(2026, 4, 27)),
        (D(2026, 10, 25), D(2026, 11, 23)),
        (D(2028, 2, 1), D(2028, 3, 1)),
        (D(2028, 2, 29), D(2028, 3, 29)),
        (D(2027, 2, 1), D(2027, 3, 2)),
        (D(2026, 12, 15), D(2027, 1, 13)),
        (D(2026, 12, 31), D(2027, 1, 29)),
    ],
)
def test_the_last_date_on_the_calendar(today, last):
    assert commencements.soon_last_day(today) == last
    assert _bucket(today, last) == commencements.BUCKET_SOON
    assert _bucket(today, last + datetime.timedelta(days=1)) == commencements.BUCKET_LATER


@pytest.mark.parametrize(
    ("today", "start", "end", "precision", "bucket"),
    [
        # A 30-day month from its first day fits exactly; a 31-day one does not.
        (D(2026, 11, 1), D(2026, 11, 1), D(2026, 11, 30), DatePrecision.MONTH, "jargmised"),
        (D(2026, 12, 1), D(2026, 12, 1), D(2026, 12, 31), DatePrecision.MONTH, "hiljem"),
        (D(2026, 12, 2), D(2026, 12, 1), D(2026, 12, 31), DatePrecision.MONTH, "hiljem"),
        # Leap February: 29 dates from the 1st fit; non-leap 28 from the 1st too.
        (D(2028, 2, 1), D(2028, 2, 1), D(2028, 2, 29), DatePrecision.MONTH, "jargmised"),
        (D(2027, 2, 1), D(2027, 2, 1), D(2027, 2, 28), DatePrecision.MONTH, "jargmised"),
        # A running month is not «within», whatever its end (ADR 0158 §4, unchanged).
        (D(2026, 10, 14), D(2026, 10, 1), D(2026, 10, 31), DatePrecision.MONTH, "hiljem"),
        # A quarter or a year is never in the 30 dates.
        (D(2026, 10, 1), D(2026, 10, 1), D(2026, 12, 31), DatePrecision.QUARTER, "hiljem"),
        (D(2027, 1, 1), D(2027, 1, 1), D(2027, 12, 31), DatePrecision.YEAR, "hiljem"),
        # Over by its end, not its start.
        (D(2026, 10, 14), D(2026, 7, 1), D(2026, 9, 30), DatePrecision.QUARTER, "moodunud"),
    ],
)
def test_precision_keeps_the_containment_rule(today, start, end, precision, bucket):
    assert _bucket(today, start, end, precision) == bucket


@pytest.mark.parametrize(
    ("instant", "tallinn_day"),
    [
        # 00:30 Tallinn on the fall-back day is still 24.10 in UTC.
        (datetime.datetime(2026, 10, 24, 21, 30, tzinfo=UTC), D(2026, 10, 25)),
        # 23:59 Tallinn the evening before is 24.10 in both.
        (datetime.datetime(2026, 10, 24, 20, 59, tzinfo=UTC), D(2026, 10, 24)),
        # 00:30 Tallinn on the spring-forward day.
        (datetime.datetime(2026, 3, 28, 22, 30, tzinfo=UTC), D(2026, 3, 29)),
        # 00:15 Tallinn on New Year's Day is still 31.12 in UTC.
        (datetime.datetime(2026, 12, 31, 22, 15, tzinfo=UTC), D(2027, 1, 1)),
        # 00:10 Tallinn on the leap day.
        (datetime.datetime(2028, 2, 28, 22, 10, tzinfo=UTC), D(2028, 2, 29)),
    ],
)
def test_today_is_the_tallinn_date_at_midnight(monkeypatch, instant, tallinn_day):
    monkeypatch.setattr(timezone, "now", lambda: instant)
    day = commencements.today()
    assert day == tallinn_day
    assert commencements.soon_last_day(day) == tallinn_day + datetime.timedelta(days=29)


@pytest.mark.django_db
def test_the_list_puts_the_thirtieth_date_in_and_the_thirty_first_later(signed_in, monkeypatch):
    today = D(2026, 10, 25)
    monkeypatch.setattr(commencements, "today", lambda: today)
    for offset, label in ((0, "täna"), (29, "kolmekümnes"), (30, "kolmekümne esimene")):
        matter = factories.MatterFactory(title=f"Jõustub {label}")
        factories.EffectiveDateFactory(
            matter=matter,
            kind=EffectiveDateKind.KNOWN_DATE,
            date_value=today + datetime.timedelta(days=offset),
            period_end=today + datetime.timedelta(days=offset),
            date_precision=DatePrecision.EXACT,
            description=label,
        )

    response = signed_in.get(
        reverse("matters:matter_list"),
        {"joustumine": "on", "olek": "koik", "vaade": "joustumised"},
    )

    buckets = {
        bucket.key: sorted(entry.record.description for entry in bucket.entries)
        for bucket in response.context["commencement_buckets"]
    }
    assert buckets["jargmised"] == ["kolmekümnes", "täna"]
    assert buckets["hiljem"] == ["kolmekümne esimene"]
    assert "Järgmise 30 päeva jooksul" in response.content.decode()
