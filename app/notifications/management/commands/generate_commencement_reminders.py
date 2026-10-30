"""Generate and retire «Kontrolli jõustumist» reminders and deadline notifications.

(docs/adr/0188, docs/adr/0198.) One worker, the `notifications` Compose
service, does both: each turn reconciles the commencement checks, then the
personal deadline notifications, each under its own advisory lock. The name is
kept because the Compose service and the healthcheck call it.

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
from dataclasses import dataclass
from datetime import date
from typing import Any

from django.core.management.base import BaseCommand, CommandError
from django.db import DatabaseError
from django.utils import timezone

logger = logging.getLogger(__name__)

#: Between two loop turns. Ten minutes: a date recorded in the morning is asked
#: about before lunch, and the Tallinn midnight roll-over is picked up within the
#: same interval. A setting is not worth its configuration surface here.
DEFAULT_INTERVAL_SECONDS = 600.0


def _reconcile_all(today: date | None, *, dry_run: bool) -> list[Any]:
    from app.notifications.deadlines import reconcile_deadline_notifications
    from app.notifications.services import reconcile_commencement_reminders

    return [
        reconcile_commencement_reminders(today, dry_run=dry_run),
        reconcile_deadline_notifications(today, dry_run=dry_run),
    ]


def _each_turn() -> list[Any]:
    """One loop turn. Each reconcile is its own transaction: one failing does not
    stop the other, and the next turn tries again."""
    from app.notifications.deadlines import reconcile_deadline_notifications
    from app.notifications.services import reconcile_commencement_reminders

    results: list[Any] = []
    for name, run in (
        ("commencement reminder", reconcile_commencement_reminders),
        ("deadline notification", reconcile_deadline_notifications),
    ):
        try:
            results.append(run())
        except DatabaseError:
            logger.exception("%s reconcile failed", name)
    # The daily Muudatused photograph rides the same worker (docs/adr/0159 §7,
    # docs/adr/0198 §8): once per Tallinn day from 03:00, never twice, under its
    # own lock — so no host cron and no second service.
    try:
        results.append(_capture_snapshot())
    except DatabaseError:
        logger.exception("daily snapshot capture failed")
    return results


@dataclass(frozen=True)
class _SnapshotTurn:
    """The capture's line in the loop's output: said only on the turn it photographs."""

    changed: bool

    @property
    def summary(self) -> str:
        return f"Operatiivne hetktõmmis {timezone.localdate().isoformat()}: tehtud."


def _capture_snapshot() -> _SnapshotTurn:
    from app.reporting.selectors.snapshots import capture_daily_if_missing

    return _SnapshotTurn(changed=capture_daily_if_missing())


class Command(BaseCommand):
    help = "Create due commencement checks and deadline notifications; retire obsolete ones."

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
        if options["loop"]:
            self._loop(float(options["interval_seconds"]))
            return

        results = _reconcile_all(options["today"], dry_run=options["check"])
        if options["check"]:
            if any(result.changed for result in results):
                for result in results:
                    self.stderr.write(result.summary)
                raise CommandError("Teavituste genereerimine on tegemata.")
            if not options["quiet"]:
                for result in results:
                    self.stdout.write(self.style.SUCCESS(result.summary))
            return
        if not options["quiet"]:
            for result in results:
                self.stdout.write(result.summary)

    def _loop(self, interval: float) -> None:
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
                for result in _each_turn():
                    if result.changed:
                        self.stdout.write(result.summary)
                deadline = time.monotonic() + interval
                while not stopping["now"] and time.monotonic() < deadline:
                    time.sleep(min(1.0, max(0.0, deadline - time.monotonic())))
        finally:
            for number, handler in previous.items():
                signal.signal(number, handler)
        self.stdout.write("Jõustumise meeldetuletuste genereerija lõpetas.")
