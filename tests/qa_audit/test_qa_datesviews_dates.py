"""QA audit 2026-10-14 (area `datesviews`) — date and time-zone boundaries.

Independent probes at source 81a7d6e4:

* the ADR 0146 follow-up check's day from a send timestamp at Tallinn midnight
  on both 2026 clock-change nights and across a leap February;
* the ISO week (ADR 0149, Monday–Sunday) around 29 February 2028;
* lateness — the SQL population (`NextAction.objects.overdue`) and the row
  answer (`is_overdue`) agree for every precision on leap-year and year-end
  days, with a stored anchor that is not the period's first day too;
* `q` (search text) narrows the Jõustumised list and its coverage figure.
"""

from __future__ import annotations

import datetime as dt
from zoneinfo import ZoneInfo

import pytest

from app.intelligence import commencements
from app.intelligence.enums import EffectiveDateKind
from app.matters import work_items as wi
from app.workflow.enums import ActionKind, ActionStatus, DatePrecision, DateSemantics
from app.workflow.follow_ups import first_check_day, sent_on_of
from app.workflow.models import NextAction
from tests import factories

TALLINN = ZoneInfo("Europe/Tallinn")
UTC = ZoneInfo("UTC")


@pytest.mark.parametrize(
    ("instant_utc", "sent_on", "due"),
    [
        # 25.10.2026 00:30 EEST (UTC+3) is 24.10 21:30 UTC.
        (
            dt.datetime(2026, 10, 24, 21, 30, tzinfo=UTC),
            dt.date(2026, 10, 25),
            dt.date(2026, 11, 24),
        ),
        # 29.03.2026 00:30 EET (UTC+2) is 28.03 22:30 UTC.
        (dt.datetime(2026, 3, 28, 22, 30, tzinfo=UTC), dt.date(2026, 3, 29), dt.date(2026, 4, 28)),
        # 31.12.2026 23:59 EET is still 31.12 in UTC; 1.1.2027 00:10 is not.
        (
            dt.datetime(2026, 12, 31, 21, 59, tzinfo=UTC),
            dt.date(2026, 12, 31),
            dt.date(2027, 1, 30),
        ),
        (dt.datetime(2026, 12, 31, 22, 10, tzinfo=UTC), dt.date(2027, 1, 1), dt.date(2027, 1, 31)),
        # Leap February: 30 January 2028 + 30 days is 29 February.
        (dt.datetime(2028, 1, 30, 10, 0, tzinfo=UTC), dt.date(2028, 1, 30), dt.date(2028, 2, 29)),
        (dt.datetime(2027, 1, 30, 10, 0, tzinfo=UTC), dt.date(2027, 1, 30), dt.date(2027, 3, 1)),
    ],
)
def test_the_follow_up_day_is_thirty_tallinn_dates_after_the_send(instant_utc, sent_on, due):
    assert sent_on_of(instant_utc) == sent_on
    assert first_check_day(sent_on) == due


@pytest.mark.parametrize(
    ("today", "monday", "sunday"),
    [
        (dt.date(2028, 2, 29), dt.date(2028, 2, 28), dt.date(2028, 3, 5)),
        (dt.date(2028, 3, 5), dt.date(2028, 2, 28), dt.date(2028, 3, 5)),
        (dt.date(2028, 3, 6), dt.date(2028, 3, 6), dt.date(2028, 3, 12)),
        (dt.date(2026, 10, 25), dt.date(2026, 10, 19), dt.date(2026, 10, 25)),
        (dt.date(2027, 1, 1), dt.date(2026, 12, 28), dt.date(2027, 1, 3)),
    ],
)
def test_the_iso_week_is_monday_to_sunday(today, monday, sunday):
    assert wi.start_of_iso_week(today) == monday
    assert wi.end_of_iso_week(today) == sunday
    assert (wi.end_of_iso_week(today) - wi.start_of_iso_week(today)).days == 6


BOUNDARY_DAYS = [
    dt.date(2028, 2, 28),
    dt.date(2028, 2, 29),
    dt.date(2028, 3, 1),
    dt.date(2027, 12, 31),
    dt.date(2028, 1, 1),
    dt.date(2028, 6, 30),
    dt.date(2028, 7, 1),
]

ANCHORS = [
    (DatePrecision.EXACT, dt.date(2028, 2, 29)),
    (DatePrecision.INFERRED, dt.date(2028, 2, 28)),
    (DatePrecision.MONTH, dt.date(2028, 2, 1)),
    # Not normalised to the first day: still the February period.
    (DatePrecision.MONTH, dt.date(2028, 2, 15)),
    (DatePrecision.QUARTER, dt.date(2027, 10, 1)),
    (DatePrecision.QUARTER, dt.date(2028, 1, 1)),
    (DatePrecision.HALF_YEAR, dt.date(2028, 1, 1)),
    (DatePrecision.YEAR, dt.date(2027, 1, 1)),
]


@pytest.mark.django_db
def test_sql_and_row_lateness_agree_on_leap_and_year_end_days(specialist):
    actions = []
    for precision, anchor in ANCHORS:
        matter = factories.MatterFactory(owner=specialist)
        actions.append(
            NextAction.objects.create(
                matter=matter,
                text=f"{precision} {anchor}",
                kind=ActionKind.DO,
                date_semantics=DateSemantics.DEADLINE,
                target_date=anchor,
                date_precision=precision,
                status=ActionStatus.OPEN,
                responsible=specialist,
            )
        )
    for today in BOUNDARY_DAYS:
        in_sql = set(
            NextAction.objects.filter(pk__in=[a.pk for a in actions])
            .overdue(today)
            .values_list("pk", flat=True)
        )
        in_python = {a.pk for a in actions if a.is_overdue(today)}
        assert in_sql == in_python, today

    feb = next(a for a in actions if a.date_precision == DatePrecision.MONTH)
    assert not feb.is_overdue(dt.date(2028, 2, 29))
    assert feb.is_overdue(dt.date(2028, 3, 1))


@pytest.mark.django_db
def test_search_text_narrows_the_commencement_list_and_its_coverage(signed_in, monkeypatch):
    from django.urls import reverse

    from app.search.indexing import refresh_matter

    monkeypatch.setattr(commencements, "today", lambda: dt.date(2026, 10, 14))
    wanted = factories.MatterFactory(title="Pakendiaktsiisi seadus QA")
    other = factories.MatterFactory(title="Metsaseadus QA")
    bare = factories.MatterFactory(title="Pakendiregister ilma jõustumiseta QA")
    for matter, text in ((wanted, "pakend"), (other, "mets")):
        factories.EffectiveDateFactory(
            matter=matter,
            kind=EffectiveDateKind.KNOWN_DATE,
            date_value=dt.date(2027, 1, 1),
            period_end=dt.date(2027, 1, 1),
            description=text,
        )
    for matter in (wanted, other, bare):
        refresh_matter(matter)

    response = signed_in.get(
        reverse("matters:matter_list"),
        {"joustumine": "on", "vaade": "joustumised", "q": "pakend"},
    )

    rows = [
        e.record.description
        for bucket in response.context["commencement_buckets"]
        for e in bucket.entries
    ]
    assert rows == ["pakend"]
    assert response.context["commencement_coverage"]["missing"] == 1
