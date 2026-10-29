"""«A timestamp on a local day» has one spelling, and it is a range (2026-10-28 audit A-01/D-01).

`app.core.dates.local_day_range` is the half-open range on the timestamp that
replaced the ``__date`` cast — ``("col" AT TIME ZONE 'Europe/Tallinn')::date`` —
in Osakond's *Muudatused* (every audit-table read of the page), the Ülevaade
feed selectors, Minu asjad's month figures and «Teemades muudatusi». The cast
cannot use the column's index and was paid on every audit row on every load.

Two things are held here:

* **The same rows.** Around both 2026 daylight-saving changes (29 March, when
  the local day is 23 hours, and 25 October, when it is 25), every
  half-hour from the evening before to the morning after, the range and the
  cast select the same audit rows for every window that starts or ends on
  those days.
* **No cast left** on the hot reads: no `AT TIME ZONE` in any statement the
  change view, the feed selectors or the month figures send.
"""

from __future__ import annotations

import datetime as dt
import re
from itertools import product

import pytest
from django.db import connection
from django.test.utils import CaptureQueriesContext
from django.utils import timezone

from app.audit.enums import ChangeEventType
from app.audit.models import ChangeEvent
from app.core.dates import local_day_range
from app.matters import activity, my_work
from app.matters import department_changes as dc
from app.matters import department_dashboard as dd
from app.matters import overview as ov
from app.matters.models import Matter
from app.matters.services import add_entry, close_matter, create_matter
from app.workflow.enums import Disposition
from app.workflow.services import set_next_action

pytestmark = pytest.mark.django_db

UTC = dt.UTC

#: The evening before to the morning after each 2026 transition, in UTC.
TRANSITIONS = (
    (dt.datetime(2026, 3, 28, 18, 0, tzinfo=UTC), dt.datetime(2026, 3, 30, 4, 0, tzinfo=UTC)),
    (dt.datetime(2026, 10, 24, 18, 0, tzinfo=UTC), dt.datetime(2026, 10, 26, 4, 0, tzinfo=UTC)),
)
DAYS = (
    dt.date(2026, 3, 28),
    dt.date(2026, 3, 29),
    dt.date(2026, 3, 30),
    dt.date(2026, 10, 24),
    dt.date(2026, 10, 25),
    dt.date(2026, 10, 26),
)


def _events_around_transitions(matter: Matter) -> list[ChangeEvent]:
    moments: list[dt.datetime] = []
    for start, end in TRANSITIONS:
        moment = start
        while moment <= end:
            # Each half hour, and a second either side of it: the bounds.
            moments += [moment - dt.timedelta(seconds=1), moment, moment + dt.timedelta(seconds=1)]
            moment += dt.timedelta(minutes=30)
    return ChangeEvent.objects.bulk_create(
        ChangeEvent(
            event_type=ChangeEventType.NEXT_ACTION_SET,
            matter=matter,
            occurred_at=moment,
            summary="DST",
        )
        for moment in moments
    )


def _cast(events, first, last):
    return set(
        events.filter(occurred_at__date__gte=first, occurred_at__date__lte=last).values_list(
            "pk", flat=True
        )
    )


def test_the_change_window_is_the_same_rows_as_the_cast_across_both_dst_changes(specialist):
    matter = create_matter(title="Kellakeeramine", owner=specialist, actor=specialist)
    _events_around_transitions(matter)
    events = ChangeEvent.objects.filter(matter=matter, summary="DST")

    for first, last in product(DAYS, DAYS):
        if first > last:
            continue
        period = dd.Period(key=dd.PERIOD_CUSTOM, start=first, end=last)
        windowed = set(dc._in_window(events, period).values_list("pk", flat=True))
        assert windowed == _cast(events, first, last), (first, last)
        open_ended = set(
            events.filter(local_day_range("occurred_at", first)).values_list("pk", flat=True)
        )
        assert open_ended == set(
            events.filter(occurred_at__date__gte=first).values_list("pk", flat=True)
        ), first


def test_the_long_and_the_short_day_hold_their_own_hours(specialist):
    """25 October 2026 holds 25 hours of rows, 29 March 23 — and every one of them."""
    matter = create_matter(title="Päeva pikkus", owner=specialist, actor=specialist)
    _events_around_transitions(matter)
    events = ChangeEvent.objects.filter(matter=matter, summary="DST")

    def hours(day: dt.date) -> float:
        rows = events.filter(local_day_range("occurred_at", day, day)).order_by("occurred_at")
        first, last = rows.first(), rows.last()
        assert first is not None and last is not None
        # The first row is the local midnight itself, the last a second before the next.
        assert timezone.localtime(first.occurred_at).time() == dt.time(0, 0)
        assert timezone.localtime(last.occurred_at).time() == dt.time(23, 59, 59)
        return (last.occurred_at - first.occurred_at).total_seconds() / 3600

    assert round(hours(dt.date(2026, 10, 25))) == 25
    assert round(hours(dt.date(2026, 3, 29))) == 23


def _no_cast(captured, table: str) -> list[str]:
    reads = [q["sql"] for q in captured.captured_queries if f'"{table}"' in q["sql"]]
    assert reads, f"nothing read {table}; the check proves nothing"
    return [sql for sql in reads if "AT TIME ZONE" in sql]


def test_the_change_view_reads_the_audit_table_without_a_date_cast(
    department_head, specialist, other_specialist
):
    """D-01: every `audit_changeevent` statement of *Muudatused* is a plain range."""
    matter = create_matter(title="Muudetud", owner=specialist, actor=specialist)
    set_next_action(matter=matter, text="Samm", actor=specialist)
    close_matter(
        matter=matter,
        disposition=Disposition.COMPLETED,
        actor=other_specialist,
        follow_ups_confirmed=True,
    )
    with CaptureQueriesContext(connection) as captured:
        changes = dc.build_changes(department_head)

    assert _no_cast(captured, "audit_changeevent") == []
    windowed = [
        q["sql"]
        for q in captured.captured_queries
        if '"audit_changeevent"."occurred_at" >= ' in q["sql"]
        and '"audit_changeevent"."occurred_at" < ' in q["sql"]
    ]
    assert windowed, "the window is no longer a range on occurred_at"
    assert changes.categories


#: The one cast that is not a window: «a dated round or `Märge` that has begun»
#: compares the record's business day with the local day it was written
#: (`activity.work_sources`, ``occurred_on <= TruncDate("created_at")``) — a
#: column against a column, row by row, which no range can say.
_BEGUN = re.compile(
    r""""occurred_on" <= \(\w+\."created_at" AT TIME ZONE 'Europe/Tallinn'\)::date"""
)


def _without_begun(sql: str) -> str:
    return _BEGUN.sub("", sql)


def test_the_feed_selectors_and_the_month_figures_carry_no_date_cast(specialist, normal_matter):
    add_entry(matter=normal_matter, body="<p>Kirje</p>", author=specialist)
    today = timezone.localdate()

    with CaptureQueriesContext(connection) as feed:
        ov.activity_feed(specialist, today)
    with CaptureQueriesContext(connection) as figures:
        my_work.person_stats(specialist, specialist, today)
    with CaptureQueriesContext(connection) as between:
        list(
            activity.work_activity_between(
                Matter.objects.all(), specialist, today - dt.timedelta(days=7), today
            )
        )

    for captured in (feed, figures, between):
        casts = [
            sql
            for sql in (_without_begun(q["sql"]) for q in captured.captured_queries)
            if "AT TIME ZONE" in sql
        ]
        assert casts == [], [sql[sql.index("AT TIME ZONE") - 120 :][:200] for sql in casts]


def test_an_entry_names_its_tallinn_day_not_its_utc_day(specialist, normal_matter):
    """B-06: 23:30 UTC on 28 October is 01:30 on the 29th in Tallinn."""
    entry = add_entry(
        matter=normal_matter,
        body="<p>Hilja</p>",
        author=specialist,
        occurred_at=dt.datetime(2026, 10, 28, 23, 30, tzinfo=UTC),
    )

    assert str(entry).endswith("29.10.2026")
