"""The nightly cycle's finish-by figures (ADR 0229, `app.core.nightly_schedule`).

Pure arithmetic: no database. Summer, winter and both nights the clocks change.
"""

from __future__ import annotations

import datetime as dt
import re
from pathlib import Path

import pytest

from app.core import nightly_schedule as ns

UTC = dt.UTC
REPO = Path(__file__).resolve().parent.parent
H = dt.timedelta(hours=1)
M = dt.timedelta(minutes=1)


def test_the_finish_by_chain_adds_up_exactly_as_the_docstring_says():
    by = ns.FINISH_BY
    wait = ns.LOCK_WAIT
    assert wait == 20 * M
    assert by["lovable"] == 90 * M
    assert by["government-agenda"] == by["lovable"] + wait + 40 * M == 150 * M
    assert by["register-watch"] == by["lovable"] + wait + 70 * M == 3 * H
    assert by["riigikogu"] == by["register-watch"] + wait + 90 * M + 10 * M == 5 * H
    assert by["register-sweep"] == by["riigikogu"] + wait + 40 * M == 6 * H
    assert ns.CYCLE_COMPLETE_BY == by["register-sweep"] == 6 * H
    # The registers lane in order; the agenda lane beside it.
    assert by["lovable"] < by["register-watch"] < by["riigikogu"] < by["register-sweep"]
    assert by["lovable"] < by["government-agenda"] < by["register-sweep"]


def test_the_riigikogu_finish_by_is_built_on_the_cycles_own_limit():
    from app.riigikogu import cycle

    assert ns.FINISH_BY["riigikogu"] - ns.FINISH_BY["register-watch"] - ns.LOCK_WAIT == (
        cycle.CYCLE_TIME_LIMIT + 10 * M
    )


# (since, step, due in UTC): the first cycle at or after `since`, plus real time.
CASES = [
    # Winter: 00:00 Tallinn = 22:00Z.
    (dt.datetime(2026, 11, 25, 12, 0, tzinfo=UTC), "register-watch", (2026, 11, 26, 1, 0)),
    (dt.datetime(2026, 11, 25, 12, 0, tzinfo=UTC), "riigikogu", (2026, 11, 26, 3, 0)),
    (dt.datetime(2026, 11, 25, 12, 0, tzinfo=UTC), "register-sweep", (2026, 11, 26, 4, 0)),
    # Summer: 00:00 Tallinn = 21:00Z.
    (dt.datetime(2026, 7, 1, 12, 0, tzinfo=UTC), "riigikogu", (2026, 7, 2, 2, 0)),
    (dt.datetime(2026, 7, 1, 12, 0, tzinfo=UTC), "register-sweep", (2026, 7, 2, 3, 0)),
    # Clocks forward 29.03.2026 01:00Z: the cycle starts at 22:00Z on the 28th
    # (UTC+2); six real hours later is 04:00Z (07:00 on the wall), not 03:00Z.
    (dt.datetime(2026, 3, 28, 12, 0, tzinfo=UTC), "riigikogu", (2026, 3, 29, 3, 0)),
    (dt.datetime(2026, 3, 28, 12, 0, tzinfo=UTC), "register-sweep", (2026, 3, 29, 4, 0)),
    (dt.datetime(2026, 3, 28, 12, 0, tzinfo=UTC), "register-watch", (2026, 3, 29, 1, 0)),
    # Clocks back 25.10.2026 01:00Z: the cycle starts at 21:00Z on the 24th
    # (UTC+3); six real hours later is 03:00Z (05:00 on the wall), not 04:00Z.
    (dt.datetime(2026, 10, 24, 12, 0, tzinfo=UTC), "riigikogu", (2026, 10, 25, 2, 0)),
    (dt.datetime(2026, 10, 24, 12, 0, tzinfo=UTC), "register-sweep", (2026, 10, 25, 3, 0)),
    # The first night after each change: back to plain winter / summer.
    (dt.datetime(2026, 10, 25, 12, 0, tzinfo=UTC), "register-sweep", (2026, 10, 26, 4, 0)),
    (dt.datetime(2026, 3, 29, 12, 0, tzinfo=UTC), "register-sweep", (2026, 3, 30, 3, 0)),
]


@pytest.mark.parametrize(("since", "step", "due"), CASES)
def test_a_step_is_due_its_finish_by_in_real_time_after_the_cycle(since, step, due):
    got = ns.step_due(step, since)
    assert got.astimezone(UTC) == dt.datetime(*due, tzinfo=UTC)
    start = ns.next_cycle_start(since)
    # In UTC: two datetimes in one zone subtract as wall-clock times.
    assert got.astimezone(UTC) - start.astimezone(UTC) == ns.FINISH_BY[step]


def _last_sunday(year: int, month: int) -> dt.date:
    day = dt.date(year, month, 31)
    return day - dt.timedelta(days=(day.weekday() + 1) % 7)


def _daily_monitor_utc() -> dt.time:
    """The daily tier's cron time on the host (`5 4 * * *`, UTC)."""
    text = (REPO / "deploy/unraid-main/cron/juristid.cron.example").read_text(encoding="utf-8")
    line = next(row for row in text.splitlines() if "--tier daily" in row)
    minute, hour = line.split()[:2]
    return dt.time(int(hour), int(minute))


def test_every_night_is_due_complete_before_the_daily_monitor_looks():
    look = _daily_monitor_utc()
    nights = []
    for year in range(2026, 2031):
        for month in (3, 10):
            change = _last_sunday(year, month)
            nights += [change - dt.timedelta(days=1), change, change + dt.timedelta(days=1)]
        nights += [dt.date(year, 1, 15), dt.date(year, 7, 2)]
    for night in nights:
        start = ns.cycle_start_on(night)
        complete = ns.after_start(start, ns.CYCLE_COMPLETE_BY).astimezone(UTC)
        monitor = dt.datetime.combine(complete.date(), look, tzinfo=UTC)
        assert complete <= monitor, night
        # And the monitor at that moment judges this night (now − CYCLE_COMPLETE_BY
        # is in it), not the one before.
        judged = (monitor - ns.CYCLE_COMPLETE_BY).astimezone(ns.TALLINN).date()
        assert judged == night, night


def test_the_host_monitor_uses_the_same_cycle_completion():
    text = (REPO / "scripts/deploy/juristid-monitor.sh").read_text(encoding="utf-8")
    hours = int(re.search(r"^NIGHTLY_COMPLETE_HOURS=(\d+)$", text, re.MULTILINE).group(1))
    assert dt.timedelta(hours=hours) == ns.CYCLE_COMPLETE_BY


def test_the_lanes_after_lovable_fit_their_finish_by_from_one_oclock():
    """Owner, 25.11.2026: the lanes start no earlier than 01:00. Their finish-by
    still holds from the later of 01:00 and Lovable's own finish-by."""
    from app.core import nightly_schedule as ns

    assert ns.LANES_EARLIEST == dt.timedelta(hours=1)
    assert ns.LANES_EARLIEST < ns.FINISH_BY["lovable"]
    lanes_from = max(ns.LANES_EARLIEST, ns.FINISH_BY["lovable"])
    for step in ("government-agenda", "register-watch"):
        assert ns.FINISH_BY[step] >= lanes_from + ns.LOCK_WAIT, step
    assert ns.CYCLE_COMPLETE_BY == dt.timedelta(hours=6)
    script = (
        Path(__file__).resolve().parents[1] / "scripts" / "deploy" / "juristid-nightly-cycle.sh"
    ).read_text(encoding="utf-8")
    assert "LANES_AFTER=${JURISTID_NIGHTLY_LANES_AFTER:-3600}" in script
