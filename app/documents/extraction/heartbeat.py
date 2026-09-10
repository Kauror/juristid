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
the first.

**What this probe cannot distinguish, and by construction never will.** Nothing
bounds how long one parse may take, so a file that legitimately needs longer
than the threshold — a 500-page scan, which is 500 separate OCR runs — looks
from here exactly like a worker wedged on a malformed one. That is not an
oversight to be engineered away: "the loop has not turned for half an hour" is
the whole signal, and a worker that kept marking itself alive from inside a
parse would be reporting health it cannot observe, which is the failure this
module was written to replace. The probe therefore errs towards a false alarm on
a very slow file rather than towards silence on a stuck one, and an operator
seeing the container go red while `check_evidence_integrity` reports a fresh
claim on a large document is looking at the former.

**Two loops, two marks.** Since docs/adr/0069 there are two queue consumers in
this codebase — the `Uus teema` intake reader, which is the deployed one, and
the corpus extraction worker, which an operator starts by hand — and they must
never share a file. A single mark would let a corpus run somebody kicked off
report the intake reader alive while the reader was dead, and the intake
reader's probe is the one a container is judged by.

So a :class:`Heartbeat` is named, and each worker holds its own.
"""

from __future__ import annotations

import os
import time
from dataclasses import dataclass
from pathlib import Path

from django.conf import settings


@dataclass(frozen=True)
class Heartbeat:
    """One worker's mark, and the window it is judged against.

    ``path_setting`` and ``threshold_setting`` are setting *names* rather than
    values, read at call time: a module-level constant would freeze whatever
    the settings held at import, which `override_settings` in a test and an
    environment variable in a container both need not to be true.
    """

    path_setting: str
    threshold_setting: str

    def path(self) -> Path:
        return Path(getattr(settings, self.path_setting))

    def touch(self) -> None:
        """Mark the loop as having turned. Never raises.

        A worker that cannot write its heartbeat should keep working: the probe
        is an observation of the work, not a precondition for it. The container
        goes red, which is the correct outcome and a much smaller problem than
        a queue that stopped because a temporary directory was not writable.
        """
        target = self.path()
        try:
            target.parent.mkdir(parents=True, exist_ok=True)
            target.touch()
            os.utime(target, None)
        except OSError:
            pass

    def age_seconds(self) -> float | None:
        """Seconds since the loop last turned, or None if it never has."""
        target = self.path()
        try:
            return max(0.0, time.time() - target.stat().st_mtime)
        except OSError:
            return None

    def threshold_seconds(self) -> int:
        return int(getattr(settings, self.threshold_setting)) * 60

    def is_alive(self) -> bool:
        age = self.age_seconds()
        return age is not None and age < self.threshold_seconds()

    def clear(self) -> None:
        """Forget the mark, so a stopped worker is never reported alive.

        Called when the loop exits. Without it a `--once` run — seconds of work
        — would leave a fresh heartbeat that made the container look like a
        healthy daemon for the rest of the window.
        """
        try:
            self.path().unlink(missing_ok=True)
        except OSError:
            pass


#: The deployed one. Its window is the reader's own stale-claim minutes, which
#: is five rather than thirty: a staged file is small and bounded, so a loop
#: that has not turned in five minutes is a reader that is not coming back, and
#: the person at the form should not wait half an hour to be told so.
INTAKE_READER = Heartbeat(
    path_setting="INTAKE_READER_HEARTBEAT_PATH",
    threshold_setting="INTAKE_READER_STALE_CLAIM_MINUTES",
)

#: The corpus extraction worker's. Not a deployed service since
#: docs/adr/0069 — an operator runs it deliberately — but it keeps its mark so
#: that a long backfill can still be watched while it runs.
EXTRACTION_WORKER = Heartbeat(
    path_setting="EXTRACTION_WORKER_HEARTBEAT_PATH",
    threshold_setting="EXTRACTION_STALE_CLAIM_MINUTES",
)
