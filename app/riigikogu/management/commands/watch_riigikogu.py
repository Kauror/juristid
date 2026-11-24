"""The nightly Riigikogu cycle (ADR 0222 §5).

    manage.py watch_riigikogu [--stage INVITATIONS ...] [--today YYYY-MM-DD]

Reads only when ``JURISTID_RIIGIKOGU_MONITORING`` and
``JURISTID_REGISTER_WATCH_FETCH`` are both on; otherwise every stage is recorded
as DISABLED and nothing is fetched. Not scheduled by this code: the proposed
place in the coordinated nightly sequence is documented in ADR 0222 §5 and in
``deploy/unraid-main/cron/juristid.cron.example`` as a comment, for the owner to
install with a release.

Exit 0 when the cycle ran (a stage may have failed: it is in its own record and
in Haldus); exit 1 when another cycle is running.
"""

from __future__ import annotations

import datetime as dt
from typing import Any

from django.core.management.base import BaseCommand, CommandError

from app.core.errors import DomainError
from app.riigikogu import cycle
from app.riigikogu.enums import Stage


class Command(BaseCommand):
    help = "Loeb Riigikogu avaandmetest kutsed, tähtajad, päevakorrad, protokollid ja stenogrammid."

    def add_arguments(self, parser: Any) -> None:
        parser.add_argument("--stage", action="append", default=[], choices=Stage.values)
        parser.add_argument("--today", default="")

    def handle(self, *args: Any, **options: Any) -> None:
        today = dt.date.fromisoformat(options["today"]) if options["today"] else None
        try:
            runs = cycle.run(today=today, only=set(options["stage"]) or None)
        except DomainError as error:
            raise CommandError(str(error)) from error
        for record in runs:
            self.stdout.write(
                f"{record.stage:<12} {record.status:<10} päringuid {record.requests:>4} "
                f"429: {record.rate_limited} {record.counts} vigu {len(record.errors)}"
            )
