"""Is anything owed to the search index, and has it been owed too long?

    python manage.py check_search_freshness

The container healthcheck for the search refresh worker, and the one-line
question an operator has. Exit 0 means the projection is converged or converging;
exit 1 means an obligation has been outstanding past
``SEARCH_REBUILD_DEBT_STALE_SECONDS``, or a rebuild has failed.

**This probe measures convergence, not liveness**, and that is a deliberate
departure from `check_extraction_worker`, which reads a heartbeat file. The
distinction matters in one direction only: a stopped search worker with nothing
owed reports healthy here, where a heartbeat probe would report the truth
earlier. That is the trade taken on purpose. Nothing is wrong while nothing is
owed — the index is complete and current — and the moment a rename arrives this
goes red within the threshold, before anybody has had time to search for the new
name and not find it. In exchange the probe needs no writable path, no shared
volume and no second definition of "recently".

Read-only, like `check_search_integrity`. It reports the debt; it never consumes
it. `run_search_refresh_worker` is the only thing that pays it off, and keeping
observation and repair apart is what makes this command usable to ask whether
something is wrong.
"""

from __future__ import annotations

from typing import Any

from django.conf import settings
from django.core.management.base import BaseCommand


class Command(BaseCommand):
    help = "Exit non-zero if the search index has owed a rebuild for too long."

    def add_arguments(self, parser: Any) -> None:
        parser.add_argument(
            "--quiet",
            action="store_true",
            help="Say nothing on success. The healthcheck does not read stdout.",
        )
        parser.add_argument(
            "--max-seconds",
            type=int,
            default=None,
            help=(
                "How long an obligation may be outstanding before this fails "
                "(default SEARCH_REBUILD_DEBT_STALE_SECONDS)."
            ),
        )

    def handle(self, *args: Any, **options: Any) -> None:
        from app.search.freshness import status

        # `is None`, not `or`: `--max-seconds 0` means "anything owed is a
        # fault", which is how the test suite pins the failing branch, and a
        # falsy-check would silently substitute the default for it.
        limit = options["max_seconds"]
        if limit is None:
            limit = settings.SEARCH_REBUILD_DEBT_STALE_SECONDS
        state = status()

        if state.is_clear:
            if not options["quiet"]:
                self.stdout.write(self.style.SUCCESS("Otsinguindeks on ajakohane."))
            return

        age = int(state.seconds_owed())
        summary = ", ".join(
            f"{reason} x {count}" for reason, count in sorted(state.reasons.items())
        )

        # A failed attempt is a problem the moment it happens: the worker tried,
        # the rebuild raised, and waiting is not going to fix that.
        if state.failed_attempts:
            self.stderr.write(
                f"Otsinguindeksi taastamine on ebaõnnestunud {state.failed_attempts} korda "
                f"({summary}). Viimane viga: {state.last_error or 'teadmata'}"
            )
            raise SystemExit(1)

        if age >= limit:
            self.stderr.write(
                f"Otsinguindeks on {age}s võlgu ({summary}), lubatud on {limit}s. "
                "Kas `run_search_refresh_worker` töötab?"
            )
            raise SystemExit(1)

        if not options["quiet"]:
            self.stdout.write(f"Otsinguindeks on {age}s võlgu ({summary}); taastamine on ootel.")
