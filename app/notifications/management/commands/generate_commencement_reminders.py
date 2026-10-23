"""Generate and retire «Kontrolli jõustumist» reminders (docs/adr/0188).

    python manage.py generate_commencement_reminders            # one run, one summary line
    python manage.py generate_commencement_reminders --check    # exit 1 if a run is owed
    python manage.py generate_commencement_reminders --loop     # the `notifications` service

One run reconciles the reminder table with the commencements for today's
Europe/Tallinn date: it creates the prompts that are due, replaces an unanswered
seven-day prompt by the on-the-day one, and retires the prompts whose
commencement moved, ended or lost its day. It is idempotent and safe to run
twice or from two places at once — an advisory lock serialises runs and a unique
constraint makes a second row for one prompt impossible
(`app.notifications.services.reconcile_commencement_reminders`).

``--loop`` is the same shape as the `intake-sweeper` and `searchindex` workers:
PostgreSQL decides, no broker, one bounded run every ``--interval-seconds``,
safe to kill. A database error is logged and the next turn tries again.

``--check`` is the healthcheck: it plans a run without writing and exits 1 when
the plan is not empty — a prompt is due and was not generated, or a retired
commencement still has an open prompt. Convergence, not liveness, like
`check_search_freshness`: a stopped worker with nothing owed is green.
"""

from __future__ import annotations

import logging
import signal
import time
from datetime import date
from typing import Any

from django.core.management.base import BaseCommand, CommandError
from django.db import DatabaseError

logger = logging.getLogger(__name__)

#: Between two loop turns. Ten minutes: a date recorded in the morning is asked
#: about before lunch, and the Tallinn midnight roll-over is picked up within the
#: same interval. A setting is not worth its configuration surface here.
DEFAULT_INTERVAL_SECONDS = 600.0


class Command(BaseCommand):
    help = "Create due «Kontrolli jõustumist» reminders and retire obsolete ones."

    def add_arguments(self, parser: Any) -> None:
        parser.add_argument(
            "--today",
            type=date.fromisoformat,
            default=None,
            help="Reconcile for this ISO date instead of today's Tallinn date (operator check).",
        )
        parser.add_argument(
            "--check",
            action="store_true",
            help="Write nothing; exit 1 if a run would change anything (the healthcheck).",
        )
        parser.add_argument("--quiet", action="store_true", help="Say nothing on success.")
        parser.add_argument(
            "--loop",
            action="store_true",
            help="Reconcile, sleep and reconcile again until stopped (the notifications service).",
        )
        parser.add_argument(
            "--interval-seconds",
            type=float,
            default=DEFAULT_INTERVAL_SECONDS,
            help=f"Pause between loop turns (default {DEFAULT_INTERVAL_SECONDS:g}).",
        )

    def handle(self, *args: Any, **options: Any) -> None:
        from app.notifications.services import reconcile_commencement_reminders

        if options["loop"]:
            self._loop(float(options["interval_seconds"]))
            return

        result = reconcile_commencement_reminders(options["today"], dry_run=options["check"])
        if options["check"]:
            if result.changed:
                self.stderr.write(result.summary)
                raise CommandError("Jõustumise meeldetuletuste genereerimine on tegemata.")
            if not options["quiet"]:
                self.stdout.write(self.style.SUCCESS(result.summary))
            return
        if not options["quiet"]:
            self.stdout.write(result.summary)

    def _loop(self, interval: float) -> None:
        from app.notifications.services import reconcile_commencement_reminders

        stopping = {"now": False}

        def stop(signum: int, frame: Any) -> None:
            stopping["now"] = True

        previous: dict[Any, Any] = {}
        for name in ("SIGINT", "SIGTERM"):
            number = getattr(signal, name, None)
            if number is not None:
                previous[number] = signal.signal(number, stop)
        self.stdout.write(
            f"Jõustumise meeldetuletuste genereerija käivitus, intervall {interval:g} s."
        )
        try:
            while not stopping["now"]:
                try:
                    result = reconcile_commencement_reminders()
                except DatabaseError:
                    logger.exception("commencement reminder reconcile failed")
                else:
                    if result.changed:
                        self.stdout.write(result.summary)
                deadline = time.monotonic() + interval
                while not stopping["now"] and time.monotonic() < deadline:
                    time.sleep(min(1.0, max(0.0, deadline - time.monotonic())))
        finally:
            for number, handler in previous.items():
                signal.signal(number, handler)
        self.stdout.write("Jõustumise meeldetuletuste genereerija lõpetas.")
