"""The intake reader: the one queue consumer this product deploys.

It reads files staged on `Uus teema` while somebody is still filling the form
in, and it reads nothing else. That sentence is the whole design and it is
enforced by what this module can reach rather than by what it chooses to do:
the only queue function it imports is
`app.matters.intake_extraction.drain`, whose universe is
``MatterIntakeFile`` rows belonging to a live, unconsumed, unexpired staging
session. There is no import path from here to ``DocumentVersion``, to
`pending_versions`, or to anything that could claim a corpus row —
``tests/test_intake_reader.py`` asserts that against the module's own imports,
so a future edit that reaches for the corpus queue fails a test rather than a
production array (docs/adr/0072).

**Why that is a structural property and not a preference.** On 2026-09-10 the
canonical extraction backlog — 12 687 done, 4 093 still pending — drove the
production database's checkpoint fsyncs from under 2.5 s to 155 s, because
every small write to the parity-protected Unraid array is a read-modify-write
on a saturated USB disk. A single-row INSERT into a 264 kB audit table blocked
for two minutes and gunicorn killed the worker holding it. The queue that did
that and the queue behind somebody's open form must not be the same loop, and
the safest way to guarantee they never become the same loop again is that this
process cannot express the corpus one.

**No broker.** PostgreSQL is the queue, exactly as it is for every other
asynchronous thing here: ``SELECT … FOR UPDATE SKIP LOCKED`` makes a claim
atomic, a claim is a timestamped row state so a reader that dies leaves
evidence rather than a lock, and it is safe to run two of these. Adding Redis
or Celery to serve a table that holds single-digit rows would be a second piece
of infrastructure to run, back up and explain, for a workload of a few files a
day (AGENTS.md).

Three properties this loop is built around, the same three the corpus worker
had and for the same reasons:

* **One bad file cannot stop it.** Every failure mode ends with that staged row
  in a terminal state and the loop continuing — and the form stays usable, so a
  file nobody can read never blocks `Loo teema` (docs/adr/0072 §Failure).
* **A killed reader loses nothing.** Its claims go stale and are picked up
  again; nothing was written, so nothing is half-written.
* **It settles.** With no open `Uus teema` form anywhere in the department the
  queue is empty, and this sleeps rather than spins.
"""

from __future__ import annotations

import logging
import signal
import time
from typing import Any

from django.conf import settings
from django.core.management.base import BaseCommand

logger = logging.getLogger(__name__)

#: Staged files read per turn before the loop looks at the clock again.
#:
#: One `Uus teema` envelope is four or five files and `MAX_INTAKE_FILES` caps
#: it at twenty, so this is a whole envelope and a little room. Larger would
#: not read anything sooner — there is nothing else waiting — and smaller would
#: make a five-file drop take two turns for no reason.
BATCH = 10


class Command(BaseCommand):
    help = "Read the files staged on Uus teema, and nothing else, until stopped."

    def add_arguments(self, parser: Any) -> None:
        parser.add_argument(
            "--once",
            action="store_true",
            help="Drain what is staged now and exit, instead of waiting for more.",
        )
        parser.add_argument(
            "--limit",
            type=int,
            default=0,
            help="Stop after this many staged files (0 = no limit).",
        )
        parser.add_argument(
            "--idle-seconds",
            type=float,
            default=None,
            help="How long to wait when nothing is staged.",
        )

    def handle(self, *args: Any, **options: Any) -> None:
        # Imported here, and deliberately only this. The module-level import
        # list is what `tests/test_intake_reader.py` reads to prove this
        # process cannot reach the corpus queue, so anything added to it is a
        # decision somebody has to make on purpose.
        from app.documents.extraction.heartbeat import INTAKE_READER as mark
        from app.matters.intake_extraction import drain, pending_intake_files

        idle = options["idle_seconds"]
        if idle is None:
            idle = settings.INTAKE_READER_IDLE_SECONDS
        limit = options["limit"]
        stopping = {"now": False}

        def stop(signum: int, frame: Any) -> None:
            # Finish the file in hand, then exit. Killing mid-parse is safe —
            # nothing is committed until the settle transaction — but finishing
            # is tidier and costs at most one small file.
            stopping["now"] = True
            self.stdout.write("\nLõpetan pärast praeguse faili valmimist…")

        for name in ("SIGINT", "SIGTERM"):
            handler = getattr(signal, name, None)
            if handler is not None:
                signal.signal(handler, stop)

        waiting = pending_intake_files().count()
        self.stdout.write(f"Uus teema lugeja käivitus. Ootel: {waiting} faili.")

        processed = 0
        while not stopping["now"]:
            # Before the query, not after it. The point of the mark is that the
            # loop is turning; recording it only on the way out would make a
            # reader that is stuck *on* the query look alive.
            #
            # `touch_periodically`, not `touch`: this loop turns twice a second
            # and the mark is a file write on the container's writable layer,
            # which on the production host sits behind the parity disk this
            # whole round is about (app/documents/extraction/heartbeat.py).
            mark.touch_periodically()

            reports = drain(limit=BATCH)
            for report in reports:
                processed += 1
                self.stdout.write(
                    f"  {report.state:<16} {report.fragments:>4} osa  {report.seconds:.2f}s"
                    + (f"  [{report.error_code}]" if report.error_code else "")
                )
            if limit and processed >= limit:
                break
            if options["once"]:
                break
            if reports:
                # Something was staged this turn, so there may be more of it —
                # a person dropping five files produces five rows within a
                # second of each other. Sleeping now would put the rest of one
                # envelope behind a full idle period.
                continue
            time.sleep(idle)

        # Removed on the way out, so a stopped reader is never reported alive by
        # a mark it left behind. `--once` runs finish in milliseconds and would
        # otherwise look like a healthy daemon for the whole window.
        mark.clear()
        self.stdout.write(self.style.SUCCESS(f"Loetud {processed} faili."))
