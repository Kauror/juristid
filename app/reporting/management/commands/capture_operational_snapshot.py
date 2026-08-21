"""Photograph today's operational portfolio.

    python manage.py capture_operational_snapshot

Writes one row per open FULL Matter for one date. Running it twice on the same
day refreshes those rows rather than duplicating them — the unique constraint on
``(snapshot_date, matter)`` makes that a property of the schema, so a cron that
fires twice after a restart cannot corrupt a trend.

**It does not backfill.** ``--date`` exists for tests and for a day somebody
noticed the job had not run *while the state is still that day's*. Pointing it
at last March would write today's portfolio under March's date, which is not a
photograph of March — it is today's picture with a false caption, and nothing on
the resulting chart would look wrong (Stage-2E brief 52).

**No scheduler is installed by this branch.** Once Stage 2E is merged and
deployed, this wants a once-daily run early in the morning, before the working
day changes anything:

    0 3 * * *  docker compose -p juristid-main -f <compose> exec -T web \\
                 python manage.py capture_operational_snapshot

Until then the trend simply has no history, which is the honest state of a
feature that has not been running.
"""

from __future__ import annotations

from datetime import date
from typing import Any

from django.core.management.base import BaseCommand, CommandError, CommandParser
from django.utils import timezone

from app.reporting.selectors.snapshots import capture


class Command(BaseCommand):
    help = "Record one day's operational Matter snapshot. Idempotent per date."

    def add_arguments(self, parser: CommandParser) -> None:
        # Deliberately not `--version`: Django already defines that on every
        # command, and argparse only raises when the parser is built — so a
        # clash passes every test that does not actually invoke the command.
        parser.add_argument(
            "--date",
            dest="on",
            default="",
            help=(
                "ISO date to record under. Defaults to today. For tests and for "
                "a same-day rerun; this is not a backfill."
            ),
        )

    def handle(self, *args: Any, **options: Any) -> None:
        raw = str(options.get("on") or "")
        if raw:
            try:
                on = date.fromisoformat(raw)
            except ValueError as exc:
                raise CommandError(f"Kuupäev ei ole ISO-vormingus: {raw!r}") from exc
        else:
            on = timezone.localdate()

        created, updated = capture(on=on)
        total = created + updated

        self.stdout.write(
            self.style.SUCCESS(
                f"{on:%d.%m.%Y}: {total} aktiivset teemat "
                f"({created} uut kirjet, {updated} värskendatud)."
            )
        )
        if not total:
            # "0 rows" is the same output for "nothing to record" and "the
            # population query is wrong", and only one of those is fine.
            self.stdout.write(
                "Avatud täielikke teemasid ei ole. Hetktõmmis on tühi ja see on korrektne."
            )
