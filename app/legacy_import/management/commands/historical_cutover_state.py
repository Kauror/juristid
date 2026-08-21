"""Retire pre-cutover register rows from current work, inventing nothing.

    python manage.py historical_cutover_state --cutover-year 2026 --dry-run
    python manage.py historical_cutover_state --cutover-year 2026 --apply

No default mode, for the reason the importer and the promotion already learned:
the safe guess is the one people stop reading. ``--dry-run`` decides everything
and writes nothing; only ``--apply`` commits, and only for a cutover year
recorded in ``REVIEWED_HISTORICAL_CUTOVER_YEARS``.

The output is aggregate. A register row's title, owner and source cells are
source content and never reach this report — the per-year table is counts.
"""

from __future__ import annotations

from typing import Any

from django.core.management.base import BaseCommand, CommandError, CommandParser

from app.legacy_import.historical_cutover import (
    CLASSIFICATIONS,
    REVIEWED_HISTORICAL_CUTOVER_YEARS,
    Classification,
    UnreviewedCutoverYear,
    apply_cutover_plan,
    build_cutover_plan,
    summary,
)

_LABELS: dict[str, str] = {
    Classification.WOULD_CLOSE_HISTORICAL: "would become historical",
    Classification.ALREADY_CLOSED: "already closed (untouched)",
    Classification.CURRENT_EXCEPTION: "current exception (untouched)",
    Classification.REVIEW_REQUIRED: "review required",
    Classification.EXCLUDED: "excluded",
}

_REVIEW_LABELS: dict[str, str] = {
    "OPEN_NEXT_ACTION": "has an open next action",
    "UNEXPECTED_ORIGIN": "unexpected origin",
    "MULTIPLE_SOURCE_YEARS": "appears in several register years",
}


class Command(BaseCommand):
    help = (
        "Default pre-cutover imported register Matters to historical, without inventing a closure."
    )

    def add_arguments(self, parser: CommandParser) -> None:
        parser.add_argument(
            "--cutover-year",
            type=int,
            required=True,
            help="The first year treated as current. Everything before it defaults to historical.",
        )
        mode = parser.add_mutually_exclusive_group(required=True)
        mode.add_argument(
            "--dry-run",
            action="store_true",
            help="Read the database, decide everything, write nothing.",
        )
        mode.add_argument(
            "--apply",
            action="store_true",
            help="Commit the historical default. Idempotent.",
        )

    def handle(self, *args: Any, **options: Any) -> None:
        year = options["cutover_year"]
        plan = build_cutover_plan(cutover_year=year)
        self._report(summary(plan))

        if options["dry_run"]:
            self.stdout.write("")
            self.stdout.write("Dry run: nothing was written to the database.")
            if not plan.is_reviewed_year:
                self.stdout.write(
                    self.style.WARNING(
                        f"{year} is analysis only. Applying it needs a recorded department "
                        "decision (docs/open-decisions.md)."
                    )
                )
            return

        try:
            result = apply_cutover_plan(plan)
        except UnreviewedCutoverYear as error:
            raise CommandError(str(error)) from error

        self.stdout.write("")
        self.stdout.write(self.style.SUCCESS("Applied"))
        self.stdout.write(f"  became historical  {result.closed}")
        self.stdout.write(f"  matters examined   {result.examined}")
        self.stdout.write("  no disposition, no closure date and no closing person were invented.")

    # -- output ------------------------------------------------------------

    def _report(self, figures: dict[str, Any]) -> None:
        self.stdout.write(
            self.style.MIGRATE_HEADING(f"Historical cutover {figures['cutover_year']}")
        )
        self.stdout.write(f"  operation version   {figures['operation_version']}")
        self.stdout.write(f"  reviewed year       {'yes' if figures['reviewed_year'] else 'no'}")
        self.stdout.write(
            f"  reviewed years      {', '.join(str(y) for y in REVIEWED_HISTORICAL_CUTOVER_YEARS)}"
        )
        self.stdout.write(f"  pre-cutover matters {figures['pre_cutover_matters']}")
        self.stdout.write("")

        self.stdout.write("Classification")
        counts = figures["classifications"]
        for name in CLASSIFICATIONS:
            self.stdout.write(f"  {_LABELS[name]:<34} {counts[name]}")

        if figures["review_reasons"]:
            self.stdout.write("")
            self.stdout.write("Review reasons")
            for reason, count in figures["review_reasons"].items():
                self.stdout.write(f"  {_REVIEW_LABELS.get(reason, reason):<34} {count}")

        by_year = figures["by_source_year"]
        if by_year:
            self.stdout.write("")
            self.stdout.write("By source year")
            self.stdout.write(
                f"  {'year':<6}{'historical':>12}{'already':>10}{'current':>10}{'review':>9}"
            )
            for year, row in by_year.items():
                self.stdout.write(
                    f"  {year:<6}"
                    f"{row[Classification.WOULD_CLOSE_HISTORICAL]:>12}"
                    f"{row[Classification.ALREADY_CLOSED]:>10}"
                    f"{row[Classification.CURRENT_EXCEPTION]:>10}"
                    f"{row[Classification.REVIEW_REQUIRED]:>9}"
                )
        self.stdout.write("")
        self.stdout.write(f"  would become historical {figures['would_close']}")
