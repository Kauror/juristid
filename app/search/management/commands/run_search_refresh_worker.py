"""The search freshness worker: PostgreSQL is the queue, again.

    python manage.py run_search_refresh_worker

The counterpart to `run_extraction_worker`, built the same way and for the same
reason. A high-fanout canonical change — renaming an Organisation, editing its
aliases, renaming a Tag or a PolicyArea — records a durable obligation in its own
transaction (`app/search/freshness.py`). This loop is what pays the obligation
off without a human noticing it exists.

**Why a loop and not a cron entry.** The deployment host has no scheduler this
project may safely use: `deploy/unraid-test/README.md` says so plainly, and the
same is true of the main stack. A Compose service running a management command
from the application image is what the master specification names for exactly
this job (§24.3, "search projection maintenance/rebuild support"), and it is the
shape the extraction worker already proved on this host.

**Why the whole corpus rather than a fanout queue.** A full rebuild is measured
in seconds at production scale — 1.7s for 2,946 rows on the deployed host, 4.4s
for 3,155 rows on a developer laptop — and it is atomic, so readers keep the
previous complete index for its whole duration. A queue of per-Matter refresh
jobs would be a second projection path to keep in step with the first, in
exchange for saving a few seconds of a machine's time a few times a month. The
coarse answer is the correct one here, and it converges automatically on
searchable text this module has never heard of, which a hand-maintained fanout
map cannot.

Four properties, matching the extraction worker's:

* **A killed worker loses nothing.** The obligation is a committed row, and it
  is deleted only after the rebuild it paid for has committed.
* **A failed rebuild loses nothing.** `rebuild_all` is one transaction, so the
  previous complete index survives, and the debt stays outstanding with the
  failure recorded on it.
* **It is safe to run twice.** Two workers claiming the same rows perform two
  rebuilds and the second is a no-op against an index that is already current;
  the advisory lock in `app/search/indexing.py` keeps them from overlapping.
* **A database restart does not need a container restart.** Every pass begins by
  dropping a connection the previous one broke (`freshness.worker_pass`).
  Without that the process survives the outage and never works again, which is
  the one failure mode `restart: unless-stopped` cannot see: it watches for a
  process that exited, and this one is still running.
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
    help = "Rebuild the search projection whenever a canonical change owes one."

    def add_arguments(self, parser: Any) -> None:
        parser.add_argument(
            "--once",
            action="store_true",
            help="Pay off what is currently owed and exit, instead of waiting for more.",
        )
        parser.add_argument(
            "--idle-seconds",
            type=int,
            default=None,
            help="How long to wait when nothing is owed.",
        )
        parser.add_argument(
            "--limit",
            type=int,
            default=0,
            help="Stop after this many rebuilds (0 = no limit).",
        )

    def handle(self, *args: Any, **options: Any) -> None:
        from app.search.freshness import worker_pass

        idle = options["idle_seconds"]
        if idle is None:
            idle = settings.SEARCH_REFRESH_WORKER_IDLE_SECONDS
        limit = options["limit"]
        stopping = {"now": False}

        def stop(signum: int, frame: Any) -> None:
            # Finish the rebuild in hand. Killing mid-rebuild is safe — the
            # transaction rolls back and the previous index is still complete —
            # but finishing costs seconds and saves a redundant pass.
            stopping["now"] = True
            self.stdout.write("\nLõpetan pärast praeguse indeksi valmimist…")

        for name in ("SIGINT", "SIGTERM"):
            handler = getattr(signal, name, None)
            if handler is not None:
                signal.signal(handler, stop)

        rebuilds = 0
        while not stopping["now"]:
            try:
                outcome = worker_pass()
            except Exception as error:
                # The attempt is already recorded on the debt rows, which is what
                # `check_search_freshness` reads; this is the operator's copy
                # with the traceback attached. The two differ on purpose: the
                # debt column is sanitised because a probe prints it, and this
                # log line is for a developer reading the container's own
                # output (`app/search/freshness.describe_failure`).
                logger.exception("Otsinguindeksi taastamine ebaõnnestus.")
                self.stderr.write(self.style.ERROR(f"  taastamine ebaõnnestus: {error}"))
                if options["once"]:
                    raise SystemExit(1) from error
                # Idle rather than immediate, and that is the whole of this
                # worker's retry policy. A rebuild that fails for an application
                # reason fails again in ten seconds' time; ten seconds is slow
                # enough that a permanently broken rebuild costs a log line every
                # ten seconds rather than a busy loop, and quick enough that a
                # transient outage converges as soon as it ends. Six users and a
                # rebuild measured in seconds do not need a backoff curve, and a
                # backoff curve would need its own tests and its own reset rule.
                time.sleep(idle)
                continue

            if not outcome.rebuilt:
                if options["once"]:
                    break
                time.sleep(idle)
                continue

            rebuilds += 1
            result = outcome.result
            if result is not None:
                self.stdout.write(
                    f"  taastatud {result.documents} rida {result.seconds:.2f}s "
                    f"({outcome.cleared} võlga tasutud)"
                )
            if limit and rebuilds >= limit:
                break
            if options["once"]:
                # Anything marked while that rebuild ran is still outstanding.
                # `--once` means "pay off what was owed when I started", and the
                # pass above has already claimed and cleared exactly that.
                break

        self.stdout.write(self.style.SUCCESS(f"Tehtud {rebuilds} täisindeksit."))
