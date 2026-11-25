"""When the nightly cycle runs, and by when each of its steps is due (ADR 0229).

One cycle a night starts at 00:00 Europe/Tallinn (summer 21:00 UTC, winter
22:00 UTC; `scripts/deploy/juristid-nightly-cycle.sh`). Its steps run in order —
the Lovable synchronisation alone first, then the registers lane (register watch,
then the «Uued eelnõud» sweep) beside the Government agenda — so each step has a
latest expected finish, measured from the cycle's start, that adds up the steps
before it and the cycle's bounded lock waits (20 minutes for the release lock
before every step, 20 for the Lovable runner's lock):

* ``lovable``: 1 h 30 — both lock waits, the backup and its level-2 check, the
  run (its own 20-minute timeout) and the observation;
* ``government-agenda``: 2 h 30 — after Lovable, one lock wait and a short read;
* ``register-watch``: 3 h — after Lovable, one lock wait and the watch (≈ 12 min);
* ``register-sweep``: 4 h — after the watch, one lock wait and the sweep.

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

FINISH_BY: dict[str, dt.timedelta] = {
    "lovable": dt.timedelta(hours=1, minutes=30),
    "government-agenda": dt.timedelta(hours=2, minutes=30),
    "register-watch": dt.timedelta(hours=3),
    "register-sweep": dt.timedelta(hours=4),
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


def step_due(step: str, since: dt.datetime) -> dt.datetime:
    """When ``step`` of the first cycle at or after ``since`` should have finished."""
    return next_cycle_start(since) + FINISH_BY[step]
