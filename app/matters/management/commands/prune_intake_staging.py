"""Delete `Uus teema` staging nobody will ask for again.

Two kinds of thing, and one answer for both: a session past its expiry is a
form somebody walked away from, and one a Matter has already consumed is
material that is now evidence somewhere else. Neither will ever be read again
(app/matters/intake_staging.py, docs/adr/0064). A form still open and not yet
expired is never touched.

**It cannot touch evidence.** It reads `MatterIntakeSession`, deletes objects
under the staging prefix of the held-uploads store, and deletes those rows. The
evidence store, the derivative store, `Document` and `DocumentVersion` are not
imported here and are not reachable from what is. A staging tree that holds a
symbolic link, or a row that names a key outside its own session, is refused
and reported rather than followed (`_discard_objects`), and in production the
container running this mounts the staging volume and nothing else.

**Scheduled, bounded and observable** (csf_3942255b466a4e213b958b16,
docs/adr/0177). `--loop` is what the `intake-sweeper` service in
`deploy/unraid-main/compose.yml` runs: one bounded sweep every
``INTAKE_STAGING_SWEEP_INTERVAL_SECONDS``, the same shape as the search refresh
worker — PostgreSQL decides, no broker, safe to run twice, safe to kill. A
single run prints one summary line and exits 1 if any session was refused, so
an operator running it by hand sees the problem; the loop logs the same and
carries on, and `check_intake_staging` turns red while anything stale waits.
"""

from __future__ import annotations

import logging
import signal
import time
from typing import Any

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError
from django.db import DatabaseError

logger = logging.getLogger(__name__)


def _sweep_held() -> int:
    """Held uploads past their grace period, from the same volume (docs/adr/0177)."""
    from app.documents.pending import sweep_held

    return sweep_held()


class Command(BaseCommand):
    help = "Delete expired and consumed Uus teema staging, and the files it held."

    def add_arguments(self, parser: Any) -> None:
        parser.add_argument(
            "--limit",
            type=int,
            default=None,
            help="Most sessions one run removes (default INTAKE_STAGING_SWEEP_MAX_SESSIONS).",
        )
        parser.add_argument(
            "--max-bytes",
            type=int,
            default=None,
            help="Most staged bytes one run removes (default INTAKE_STAGING_SWEEP_MAX_BYTES).",
        )
        parser.add_argument(
            "--dry-run",
            action="store_true",
            help="Report what would go, and remove nothing.",
        )
        parser.add_argument(
            "--loop",
            action="store_true",
            help="Sweep, sleep and sweep again until stopped (the intake-sweeper service).",
        )
        parser.add_argument(
            "--interval-seconds",
            type=float,
            default=None,
            help="Pause between sweeps (default INTAKE_STAGING_SWEEP_INTERVAL_SECONDS).",
        )

    def handle(self, *args: Any, **options: Any) -> None:
        from app.matters.intake_staging import sweep_stale_sessions
        from app.matters.staging import MatterIntakeSession

        limit = options["limit"]
        max_bytes = options["max_bytes"]

        if options["loop"]:
            self._loop(options)
            return

        stale = MatterIntakeSession.objects.stale()
        expired = stale.filter(consumed_at__isnull=True).count()
        consumed = stale.filter(consumed_at__isnull=False).count()
        self.stdout.write(f"Aegunud ettevalmistusi: {expired}. Juba kasutatud: {consumed}.")

        report = sweep_stale_sessions(limit=limit, max_bytes=max_bytes, dry_run=options["dry_run"])
        if options["dry_run"]:
            self.stdout.write(
                f"Kustutataks {report.sessions} ettevalmistust, {report.files} faili, "
                f"{report.bytes} baiti. Alles jääks: {report.remaining}."
            )
            self.stdout.write(self.style.WARNING("Proovikäivitus — midagi ei kustutatud."))
            return

        self.stdout.write(self._summary(report))
        self.stdout.write(f"Aegunud hoitud faile kustutatud: {_sweep_held()}.")
        if report.errors:
            for message in report.errors:
                self.stderr.write(message)
            raise CommandError(
                f"{len(report.errors)} ettevalmistust jäeti puutumata (ohtlik või vigane hoidla)."
            )

    def _summary(self, report: Any) -> str:
        return (
            f"Kustutatud {report.sessions} ettevalmistust, {report.files} faili, "
            f"{report.objects} objekti ({report.bytes} baiti). "
            f"Alles jäi: {report.remaining}. Vigu: {len(report.errors)}."
        )

    def _loop(self, options: dict[str, Any]) -> None:
        interval = options["interval_seconds"]
        if interval is None:
            interval = float(settings.INTAKE_STAGING_SWEEP_INTERVAL_SECONDS)
        stopping = {"now": False}

        def stop(signum: int, frame: Any) -> None:
            stopping["now"] = True

        previous: dict[Any, Any] = {}
        for name in ("SIGINT", "SIGTERM"):
            number = getattr(signal, name, None)
            if number is not None:
                previous[number] = signal.signal(number, stop)
        try:
            self._turns(options, interval, stopping)
        finally:
            for number, handler in previous.items():
                signal.signal(number, handler)
        self.stdout.write("Ettevalmistuste koristaja lõpetas.")

    def _turns(self, options: dict[str, Any], interval: float, stopping: dict[str, bool]) -> None:
        from app.matters.intake_staging import sweep_stale_sessions

        self.stdout.write(f"Ettevalmistuste koristaja käivitus, intervall {interval:g} s.")
        while not stopping["now"]:
            try:
                report = sweep_stale_sessions(
                    limit=options["limit"], max_bytes=options["max_bytes"]
                )
            except DatabaseError:
                # The next turn tries again; `check_intake_staging` goes red if
                # this keeps happening for longer than the stale threshold.
                logger.exception("intake staging sweep failed")
            else:
                if report.sessions or report.errors:
                    self.stdout.write(self._summary(report))
                for message in report.errors:
                    self.stderr.write(message)
            held = _sweep_held()
            if held:
                self.stdout.write(f"Aegunud hoitud faile kustutatud: {held}.")
            deadline = time.monotonic() + interval
            while not stopping["now"] and time.monotonic() < deadline:
                time.sleep(min(1.0, max(0.0, deadline - time.monotonic())))
