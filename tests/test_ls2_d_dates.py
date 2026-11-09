# ruff: noqa: F811 - pytest fixtures imported from the sync suite are used as arguments
"""Lovable sync nights across month and year ends, on clocks the test holds (LS2-D).

The suite's nights used to be dated absolutely (`NIGHT` = 28.10.2026) while
the readings were stamped with the wall clock and the pages looked back 31 days
from the wall clock, so the suite started failing once the calendar moved on.
Every moment here is the test's own: the night's clock (`_run(at=…)`; the
reading and the states it seeds take the running night's moment,
`tests.test_lovable_review.page_clock`) and the page's clock (`now=`).
Nothing depends on the day the suite runs.
"""

from __future__ import annotations

import datetime as dt

import pytest

from app.legacy_import import lovable_review, lovable_sync
from app.legacy_import.lovable_sync_models import LovableSyncSnapshot, SyncMode, SyncTrigger
from tests.test_lovable_pilot import pilot_on, world  # noqa: F401
from tests.test_lovable_review import page_clock  # noqa: F401 - autouse: the nights' clocks
from tests.test_lovable_sync import TALLINN, _run, cohort  # noqa: F401

pytestmark = pytest.mark.django_db

SCHEDULED = SyncTrigger.SCHEDULED


def _tallinn(y: int, m: int, d: int, hh: int = 0, mm: int = 15) -> dt.datetime:
    return dt.datetime(y, m, d, hh, mm, tzinfo=TALLINN)


@pytest.mark.parametrize(
    ("utc", "due", "key"),
    [
        # 30.11 → 1.12 (winter, UTC+2): 22:15Z is 00:15 on the 1st.
        (dt.datetime(2026, 11, 30, 21, 15, tzinfo=dt.UTC), False, "2026-11-30"),
        (dt.datetime(2026, 11, 30, 22, 15, tzinfo=dt.UTC), True, "2026-12-01"),
        # 31.12 → 1.1: the night belongs to the new year.
        (dt.datetime(2026, 12, 31, 21, 15, tzinfo=dt.UTC), False, "2026-12-31"),
        (dt.datetime(2026, 12, 31, 22, 15, tzinfo=dt.UTC), True, "2027-01-01"),
        # The night the clocks go back (25.10.2026, 04:00 → 03:00): 00:15 is still
        # summer time (UTC+3), so the 21:15Z firing is the due one.
        (dt.datetime(2026, 10, 24, 21, 15, tzinfo=dt.UTC), True, "2026-10-25"),
        (dt.datetime(2026, 10, 24, 22, 15, tzinfo=dt.UTC), False, "2026-10-25"),
    ],
)
def test_the_due_firing_and_its_key_follow_the_tallinn_day(utc, due, key):
    lovable_sync.set_mode(SyncMode.COMPARE, reason="t")
    answer = lovable_sync.due(trigger=SCHEDULED, now=utc)
    assert answer.startswith("DUE") is due, answer
    assert lovable_sync.run_key_for(SCHEDULED, utc) == key


@pytest.mark.parametrize(
    "nights",
    [
        [_tallinn(2026, 11, 30), _tallinn(2026, 12, 1)],
        [_tallinn(2026, 12, 31), _tallinn(2027, 1, 1)],
    ],
    ids=["30.11-1.12", "31.12-1.1"],
)
def test_nights_across_a_month_or_year_end_are_both_shown_in_order(cohort, nights):
    reader = cohort["world"]["mari"]
    runs = [_run(cohort, SCHEDULED, at=moment) for moment in nights]
    # The reading carries the night's own moment, never the wall clock.
    for run, moment in zip(runs, nights, strict=True):
        assert LovableSyncSnapshot.objects.get(run=run).taken_at == moment
        assert run.snapshot_taken_at == moment
    page_clock = nights[-1] + dt.timedelta(hours=12)
    history = lovable_review.nightly_history(reader, now=page_clock)
    assert [n.run.run_key for n in history.nights] == [
        nights[1].date().isoformat(),
        nights[0].date().isoformat(),
    ]
    assert history.nights[1].comparison == "BASELINE"
    assert history.nights[0].comparison == "COMPARED"


def test_a_night_older_than_the_window_leaves_the_page_by_the_pages_clock_only(cohort):
    reader = cohort["world"]["mari"]
    night = _tallinn(2026, 12, 31)
    _run(cohort, SCHEDULED, at=night)
    days = lovable_review.SNAPSHOT_RETENTION_DAYS
    inside = night + dt.timedelta(days=days) - dt.timedelta(minutes=1)
    outside = night + dt.timedelta(days=days) + dt.timedelta(minutes=1)
    assert len(lovable_review.nightly_history(reader, now=inside).nights) == 1
    assert lovable_review.nightly_history(reader, now=outside).nights == []
