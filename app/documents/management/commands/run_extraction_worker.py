"""The corpus extraction worker. **Not a deployed service.**

An operator tool since docs/adr/0069, and the demotion is the point of this
paragraph. This loop parses canonical ``DocumentVersion`` rows — the historical
archive, the imported corpus, anything an operator deliberately wants text out
of — and on 2026-09-10 running it against a 16 000-file backlog drove the
production array's checkpoint fsyncs from under 2.5 s to 155 s and made a
single-row INSERT block for two minutes. Corpus-wide extraction is no longer
part of the minimal product, so nothing starts this on `docker compose up -d`:
it is absent from both stacks' Compose files, and what *is* deployed is
`run_intake_reader`, whose universe is one open form
(app/matters/management/commands/run_intake_reader.py).

It is kept, rather than deleted, because the capability is still occasionally
wanted — rebuilding text for the opinions archive, reading a batch of imported
historical material — and because deleting it would take the parser stack's
only end-to-end exercise with it. Run it deliberately, off working hours, and
watch the array:

    docker compose -p juristid-main -f compose.yml run --rm \
        web python manage.py run_extraction_worker --once --limit 50

No Redis, no Celery, no broker. ``SELECT ... FOR UPDATE SKIP LOCKED`` makes
claiming a job atomic, so two workers never take the same file and neither
queues behind the other. The claim is a row state with a timestamp, so a worker
that dies leaves evidence of what it was doing rather than a lock nobody can
clear.

Three properties this loop is built around:

* **One bad file cannot stop it.** Every failure mode ends with that version in
  a terminal state and the loop continuing.
* **A killed worker loses nothing.** Its claims go stale and are picked up
  again; the derivative it was building was never promoted, so the previous one
  is still serving.
* **It is safe to run twice.** Nothing here assumes it is the only worker.

**It never touches `Uus teema` staging.** It used to drain both queues, which
is how one saturated array became one stalled form. `MatterIntakeFile` is the
intake reader's and nothing here can reach it.
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
        from app.documents.extraction.heartbeat import EXTRACTION_WORKER as mark
        from app.documents.extraction.orchestrator import (
            claim_version,
            extract_document_version,
            pending_versions,
        )

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

        # Said once, at the top, because this loop is now started by hand and
        # the number it is about to work through is the number that decides
        # whether starting it during working hours was a good idea.
        waiting = pending_versions().count()
        self.stdout.write(
            f"Korpuse töötleja käivitus. Ootel: {waiting} faili. "
            "See ei ole püsiteenus — vt docs/adr/0069."
        )

        processed = 0
        while not stopping["now"]:
            # Before the query, not after it. The point of the mark is that the
            # loop is turning; recording it only on the way out would make a
            # worker that is stuck *on* the query look alive.
            #
            # Throttled like the reader's, and for the same reason at a smaller
            # scale: this loop turns once per document, and during the
            # 2026-09-10 backlog drain that was 900 an hour of file writes onto
            # the array it was already saturating.
            mark.touch_periodically()

            candidate = pending_versions().first()
            if candidate is None:
                if options["once"]:
                    break
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
        mark.clear()
        self.stdout.write(self.style.SUCCESS(f"Töödeldud {processed} faili."))
