"""When the nightly cycle runs, and by when each of its steps is due (ADR 0229).

One cycle a night starts at 00:00 Europe/Tallinn (summer 21:00 UTC, winter
22:00 UTC; `scripts/deploy/juristid-nightly-cycle.sh`). Its steps run in order —
the Lovable synchronisation alone first, then, **not before 01:00 Tallinn**
(`LANES_EARLIEST`, owner 25.11.2026: Lovable has the first hour alone; a Lovable
night still running at 01:00 is waited for), the registers lane (register watch,
the Riigikogu cycle, then the «Uued eelnõud» sweep) beside the Government agenda —
so each step has a latest expected finish, measured from the cycle's start, that
adds up the steps before it and the cycle's bounded lock waits (20 minutes for the
release lock before every step, 20 for the Lovable runner's lock):

* ``lovable``: 1 h 30 — both lock waits, the backup and its level-2 check, the
  run (its own 20-minute timeout) and the observation;
* ``government-agenda``: 2 h 30 — after the later of 01:00 and Lovable's end
  (at most 1 h 30), one lock wait (20 min) and a short read (40 min allowed);
* ``register-watch``: 3 h — after the later of 01:00 and Lovable's end (at most
  1 h 30), one lock wait (20 min) and the watch (≈ 12 min; 1 h 10 allowed);
* ``riigikogu``: 5 h — after the watch (3 h), one lock wait (20 min), the cycle's
  own wall-clock limit (`app.riigikogu.cycle.CYCLE_TIME_LIMIT`, 1 h 30: every
  request, retry, back-off and ledger wait of every stage stops inside it) and
  10 minutes for what the limit does not cover (start-up, the last answer's
  processing, a name lookup, the ledger lock's own 30-second limit);
* ``register-sweep``: 6 h — after the Riigikogu step (5 h), one lock wait (20 min)
  and the sweep (≈ 1 min; 40 min allowed).

These hold whether or not the Riigikogu step is installed: without it the sweep
simply finishes early. The cycle is complete by the last of them, 6 h after it
began: 03:00 UTC in summer, 04:00 UTC in winter — before the daily monitor's
04:05 UTC look (`scripts/deploy/juristid-monitor.sh`, ``NIGHTLY_COMPLETE_HOURS``).

The arithmetic is in real (UTC) time: on the night the clocks go forward 6 hours
after 00:00 is 07:00 on the wall, on the night they go back 05:00.

The health checks use these, never a clock time of their own: a first run is
late after the first cycle that could have run it plus its finish-by, and the
week's agenda is missing only after the last cycle that could have found it.
"""

from __future__ import annotations

import datetime as dt
from zoneinfo import ZoneInfo

TALLINN = ZoneInfo("Europe/Tallinn")
#: The cycle starts at midnight in Tallinn, whatever the season.
CYCLE_START = dt.time(0, 0)
#: The bounded wait for the release lock before every step (the cycle script's LOCK_WAIT).
LOCK_WAIT = dt.timedelta(minutes=20)
#: The lanes after Lovable start no earlier than this after the cycle's start
#: (01:00 Tallinn; the cycle script's LANES_AFTER). No change of offset falls in it.
LANES_EARLIEST = dt.timedelta(hours=1)

FINISH_BY: dict[str, dt.timedelta] = {
    "lovable": dt.timedelta(hours=1, minutes=30),
    "government-agenda": dt.timedelta(hours=2, minutes=30),
    "register-watch": dt.timedelta(hours=3),
    "riigikogu": dt.timedelta(hours=5),
    "register-sweep": dt.timedelta(hours=6),
}
#: The whole cycle: a cycle started longer ago than this and not completed was
#: interrupted (or overran); the host's daily monitor uses the same figure.
CYCLE_COMPLETE_BY = max(FINISH_BY.values())


def cycle_start_on(day: dt.date) -> dt.datetime:
    """The cycle of the night that begins ``day`` (its Tallinn date)."""
    return dt.datetime.combine(day, CYCLE_START, tzinfo=TALLINN)


def next_cycle_start(moment: dt.datetime) -> dt.datetime:
    """The first cycle starting at or after ``moment`` (an aware datetime)."""
    local = moment.astimezone(TALLINN)
    start = cycle_start_on(local.date())
    if start < moment:
        start = cycle_start_on(local.date() + dt.timedelta(days=1))
    return start


def after_start(start: dt.datetime, elapsed: dt.timedelta) -> dt.datetime:
    """``elapsed`` of real time after ``start``, in Tallinn time.

    An aware datetime plus a timedelta adds wall-clock time in its own zone; across
    a change of offset that is an hour off. This adds in UTC.
    """
    return (start.astimezone(dt.UTC) + elapsed).astimezone(TALLINN)


def step_due(step: str, since: dt.datetime) -> dt.datetime:
    """When ``step`` of the first cycle at or after ``since`` should have finished."""
    return after_start(next_cycle_start(since), FINISH_BY[step])
