"""The extraction worker: PostgreSQL is the queue.

No Redis, no Celery, no broker. At this scale — six lawyers, a few thousand
matters, a few files a day — a job queue would be a second piece of
infrastructure to run, back up, monitor and explain, in exchange for capabilities
none of the work needs (AGENTS.md, Stage-2B brief 31).

What PostgreSQL gives instead is the part that actually matters:
``SELECT ... FOR UPDATE SKIP LOCKED`` makes claiming a job atomic, so two
workers never take the same file and neither queues behind the other. The claim
is a row state with a timestamp, so a worker that dies leaves evidence of what
it was doing rather than a lock nobody can clear.

Three properties this loop is built around:

* **One bad file cannot stop it.** Every failure mode ends with that version in
  a terminal state and the loop continuing.
* **A killed worker loses nothing.** Its claims go stale and are picked up
  again; the derivative it was building was never promoted, so the previous one
  is still serving.
* **It is safe to run twice.** Nothing here assumes it is the only worker.
"""

from __future__ import annotations

import logging
import signal
import time
from typing import Any

from django.conf import settings
from django.core.management.base import BaseCommand

logger = logging.getLogger(__name__)


class Command(BaseCommand):
    help = "Process pending document extractions until stopped."

    def add_arguments(self, parser: Any) -> None:
        parser.add_argument(
            "--once",
            action="store_true",
            help="Drain the queue once and exit, instead of waiting for more work.",
        )
        parser.add_argument(
            "--limit",
            type=int,
            default=0,
            help="Stop after this many versions (0 = no limit).",
        )
        parser.add_argument(
            "--idle-seconds",
            type=int,
            default=None,
            help="How long to wait when the queue is empty.",
        )

    def handle(self, *args: Any, **options: Any) -> None:
        from app.documents.extraction import heartbeat
        from app.documents.extraction.orchestrator import (
            awaiting_scanner,
            claim_version,
            extract_document_version,
            pending_versions,
        )
        from app.matters.intake_extraction import drain as drain_intake

        idle = options["idle_seconds"] or settings.EXTRACTION_WORKER_IDLE_SECONDS
        limit = options["limit"]
        stopping = {"now": False}

        def stop(signum: int, frame: Any) -> None:
            # Finish the file in hand, then exit. Killing mid-parse is safe —
            # nothing is committed until the publish transaction — but finishing
            # is tidier and costs at most one document.
            stopping["now"] = True
            self.stdout.write("\nLõpetan pärast praeguse faili valmimist…")

        for name in ("SIGINT", "SIGTERM"):
            handler = getattr(signal, name, None)
            if handler is not None:
                signal.signal(handler, stop)

        # Said once, at the top, because "Töödeldud 0 faili" is the same output
        # for "nothing to do" and "nothing may be done in this environment", and
        # only one of those is fine.
        blocked = awaiting_scanner().count()
        if blocked:
            self.stdout.write(
                self.style.WARNING(
                    f"{blocked} faili ootab pahavarakontrolli ja neid ei töödelda "
                    "selles keskkonnas. Sisu otsingusse ei jõua enne, kui skanner "
                    "on olemas."
                )
            )

        processed = 0
        #: Staged files read per turn before the loop looks at evidence again.
        #: One `Uus teema` envelope is four or five files; more than that in a
        #: single turn would be a form nobody is waiting on.
        INTAKE_BATCH = 5
        while not stopping["now"]:
            # Before the query, not after it. The point of the mark is that the
            # loop is turning; recording it only on the way out would make a
            # worker that is stuck *on* the query look alive.
            heartbeat.touch()

            # The staged queue first, and it is not a preference about
            # importance. A file staged on `Uus teema` has somebody sitting in
            # front of the form waiting for it, and it is one small file; an
            # evidence backlog is a hundred files nobody is watching. Draining
            # the second before the first would make the feature useless
            # exactly when the queue is long (app/matters/intake_extraction.py,
            # docs/adr/0064).
            #
            # Bounded per turn so it can never starve the evidence queue: a
            # session holds at most one intake envelope, and after that this
            # falls through to the work below.
            staged = drain_intake(limit=INTAKE_BATCH)
            for staged_report in staged:
                processed += 1
                self.stdout.write(
                    f"  {staged_report.state:<16} {'(uus teema)':<48} "
                    f"{staged_report.fragments:>4} osa  {staged_report.seconds:.1f}s"
                    + (f"  [{staged_report.error_code}]" if staged_report.error_code else "")
                )
            if limit and processed >= limit:
                break

            candidate = pending_versions().first()
            if candidate is None:
                if options["once"]:
                    break
                if staged:
                    # Something was read this turn, so there may be more of it.
                    # Sleeping now would make the next staged file wait a full
                    # idle period behind an empty evidence queue.
                    continue
                time.sleep(idle)
                continue

            claimed = claim_version(candidate.pk)
            if claimed is None:
                # Another worker took it between the read and the claim. Not an
                # error — it is the normal outcome of two workers racing, and
                # the right response is to look for the next one.
                continue

            report = extract_document_version(claimed)
            processed += 1
            self.stdout.write(
                f"  {report.state:<16} {claimed.original_filename[:48]:<48} "
                f"{report.fragments:>4} osa  {report.seconds:.1f}s"
                + (f"  [{report.error_code}]" if report.error_code else "")
            )
            if limit and processed >= limit:
                break

        # Removed on the way out, so a stopped worker is never reported alive by
        # a mark it left behind. `--once` runs are the common case here: they
        # finish in seconds and would otherwise look like a healthy daemon.
        heartbeat.clear()
        self.stdout.write(self.style.SUCCESS(f"Töödeldud {processed} faili."))
