"""Is `Uus teema` staging being swept, and does it still have room?

    python manage.py check_intake_staging

The healthcheck of the `intake-sweeper` service and the operator's one-line
question (docs/adr/0177). Exit 0 means staging is converging and has room; exit
1 means a stale session has waited longer than
``INTAKE_STAGING_SWEEP_STALE_SECONDS`` — the sweeper has stopped, keeps failing,
or keeps refusing a session it judged unsafe — or staging is at its
deployment-wide byte ceiling, or the volume has less than
``INTAKE_STAGING_MIN_FREE_BYTES`` free, in which case every new file is being
refused.

Convergence, not liveness, as `check_search_freshness`: a stopped sweeper with
nothing stale is green, and correctly so. Read-only: it deletes nothing.
"""

from __future__ import annotations

from typing import Any

from django.conf import settings
from django.core.management.base import BaseCommand


class Command(BaseCommand):
    help = "Exit non-zero if stale Uus teema staging waits too long or staging is full."

    def add_arguments(self, parser: Any) -> None:
        parser.add_argument(
            "--quiet",
            action="store_true",
            help="Say nothing on success. The healthcheck does not read stdout.",
        )

    def handle(self, *args: Any, **options: Any) -> None:
        from app.matters.intake_staging import (
            oldest_stale_seconds,
            staging_free_bytes,
            staging_usage,
        )

        usage = staging_usage(owner=None)
        free = staging_free_bytes()
        waited = oldest_stale_seconds()
        problems: list[str] = []

        if waited is not None and waited > settings.INTAKE_STAGING_SWEEP_STALE_SECONDS:
            problems.append(
                f"Aegunud või kasutatud ettevalmistus on oodanud koristamist {int(waited)} s "
                f"(lubatud {settings.INTAKE_STAGING_SWEEP_STALE_SECONDS} s)."
            )
        if usage.bytes >= settings.INTAKE_STAGING_MAX_BYTES:
            problems.append(
                f"Ettevalmistuste maht {usage.bytes} baiti on jõudnud piirini "
                f"{settings.INTAKE_STAGING_MAX_BYTES}."
            )
        if free is not None and free < settings.INTAKE_STAGING_MIN_FREE_BYTES:
            problems.append(
                f"Hoidlas on vaba {free} baiti, vähem kui {settings.INTAKE_STAGING_MIN_FREE_BYTES}."
            )

        if problems:
            for problem in problems:
                self.stderr.write(problem)
            raise SystemExit(1)
        if not options["quiet"]:
            self.stdout.write(
                self.style.SUCCESS(
                    f"Ettevalmistused korras: {usage.active_sessions} aktiivset vormi, "
                    f"{usage.bytes} baiti, vaba {free if free is not None else '—'} baiti."
                )
            )
