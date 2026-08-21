"""Whether the extraction worker's loop is still turning.

A worker has no port, so the image's HTTP healthcheck cannot describe it: for
28 hours the rehearsal's extractor reported `unhealthy` because nothing answers
`/healthz` in a container running a queue consumer. A container that is always
red is worse than one with no probe at all — a container that *becomes*
genuinely unhealthy then looks exactly the same.

So the worker leaves a mark each time round its loop, and the probe reads it.
This says something the process table cannot: not "the process exists" but "the
loop is turning". A parser wedged on a malformed file leaves the process alive
and the queue stopped, and that is the failure this catches.

The staleness threshold is `EXTRACTION_STALE_CLAIM_MINUTES`, reused rather than
reinvented. That setting already means "a claim this old belongs to a worker
that died", so a second number for the same judgement could only disagree with
the first. It is also why a slow file cannot raise a false alarm: nothing is
allowed to hold a claim longer than that in the first place.
"""

from __future__ import annotations

import os
import time
from pathlib import Path

from django.conf import settings


def path() -> Path:
    return Path(settings.EXTRACTION_WORKER_HEARTBEAT_PATH)


def touch() -> None:
    """Mark the loop as having turned. Never raises.

    A worker that cannot write its heartbeat should keep extracting: the probe
    is an observation of the work, not a precondition for it. The container
    goes red, which is the correct outcome and a much smaller problem than a
    queue that stopped because a temporary directory was not writable.
    """
    target = path()
    try:
        target.parent.mkdir(parents=True, exist_ok=True)
        target.touch()
        os.utime(target, None)
    except OSError:
        pass


def age_seconds() -> float | None:
    """Seconds since the loop last turned, or None if it never has."""
    target = path()
    try:
        return max(0.0, time.time() - target.stat().st_mtime)
    except OSError:
        return None


def threshold_seconds() -> int:
    return int(settings.EXTRACTION_STALE_CLAIM_MINUTES) * 60


def is_alive() -> bool:
    age = age_seconds()
    return age is not None and age < threshold_seconds()


def clear() -> None:
    """Forget the mark, so a stopped worker is never reported alive.

    Called when the loop exits. Without it a `--once` run — seconds of work —
    would leave a fresh heartbeat that made the container look like a healthy
    daemon for the next half hour.
    """
    try:
        path().unlink(missing_ok=True)
    except OSError:
        pass
