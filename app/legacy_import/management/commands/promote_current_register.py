"""Review, and then activate, one register year as current work.

    python manage.py promote_current_register --year 2026 --dry-run
    python manage.py promote_current_register --year 2026 --apply

    python manage.py promote_current_register --year 2025 --dry-run   # analysis only

No default mode. ``--dry-run`` reads and decides nothing; ``--apply`` performs
the promotion and is idempotent, so running it twice promotes nothing the second
time.

``--apply`` refuses for any year outside ``REVIEWED_CURRENT_YEARS``. That is not
a safety flag somebody can pass: the decision that a register year represents
current work belongs to the department, and the way it is recorded is a reviewed
change to that tuple.

The output is aggregate throughout. No matter title, owner name or source cell
is printed, so the run can be pasted into a message or attached to a review.
"""

from __future__ import annotations

from typing import Any

from django.core.management.base import BaseCommand, CommandError, CommandParser

from app.legacy_import.contracts import ContractError
from app.legacy_import.current_register import (
    REVIEWED_CURRENT_YEARS,
    UnreviewedYear,
    apply_promotion_plan,
    build_promotion_plan,
    summary,
)

_CLASSIFICATION_LABELS: dict[str, str] = {
    "PROMOTE": "would promote",
    "ALREADY_FULL": "already FULL",
    "EXPLICITLY_CLOSED": "explicitly closed",
    "NATIVE_SKIP": "native skips",
    "CONFLICT": "conflicts",
    "INSUFFICIENT_SOURCE": "insufficient source",
    "REVIEW_REQUIRED": "review required",
}

_FIELD_LABELS: dict[str, str] = {
    "owner_populated": "owner populated",
    "owner_unresolved": "owner unresolved",
    "stage_populated": "stage populated",
    "stage_unresolved": "stage unresolved",
    "with_source_next_action": "with source JÄRGMISEKS",
    "without_source_next_action": "without source JÄRGMISEKS",
    "with_response_deadline": "with response deadline",
    "without_response_deadline": "without response deadline",
}

_SIGNAL_LABELS: dict[str, str] = {
    "explicit_closure": "explicit closure",
    "mapped_stage": "mapped stage",
    "nonblank_next_action": "nonblank JÄRGMISEKS",
    "deterministic_owner": "deterministic owner",
    "full_candidate_ledger": "ledger proposed FULL candidate",
    "already_full": "already FULL",
}


class Command(BaseCommand):
    help = "Review or activate one Excel register year as current Juristid work."

    def add_arguments(self, parser: CommandParser) -> None:
        parser.add_argument(
            "--year",
            type=int,
            required=True,
            help="The register year sheet to examine, for example 2026.",
        )
        mode = parser.add_mutually_exclusive_group(required=True)
        mode.add_argument(
            "--dry-run",
            action="store_true",
            help="Classify every candidate and write nothing.",
        )
        mode.add_argument(
            "--apply",
            action="store_true",
            help="Promote the eligible candidates. Idempotent; safe to repeat.",
        )

    def handle(self, *args: Any, **options: Any) -> None:
        year = options["year"]
        try:
            plan = build_promotion_plan(year=year)
        except ContractError as error:
            raise CommandError(str(error)) from error

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
            result = apply_promotion_plan(plan)
        except UnreviewedYear as error:
            raise CommandError(str(error)) from error

        self.stdout.write("")
        self.stdout.write(self.style.SUCCESS("Applied"))
        self.stdout.write(f"  promoted          {result.promoted}")
        self.stdout.write(f"  candidates seen   {result.examined}")
        self.stdout.write("")
        # No snapshot is written here, and none is backfilled. The operational
        # history starts when somebody looked; a row manufactured for a past
        # date would look exactly like a real one (Stage-2F brief 24).
        self.stdout.write(
            "The next capture_operational_snapshot run records the new operational state. "
            "No snapshot was written or rewritten by this command."
        )

    def _report(self, figures: dict[str, Any]) -> None:
        self.stdout.write(self.style.MIGRATE_HEADING(f"Current register {figures['year']}"))
        self.stdout.write(f"  operation version {figures['operation_version']}")
        reviewed = "yes" if figures["reviewed_year"] else "no (analysis only)"
        self.stdout.write(f"  reviewed year     {reviewed}")
        self.stdout.write(
            f"  reviewed years    {', '.join(str(y) for y in REVIEWED_CURRENT_YEARS)}"
        )
        self.stdout.write(f"  source matters    {figures['source_matters']}")

        self.stdout.write("")
        self.stdout.write(self.style.MIGRATE_LABEL("Classification"))
        for name, count in figures["classifications"].items():
            self.stdout.write(f"  {_CLASSIFICATION_LABELS[name]:<34} {count}")

        self.stdout.write("")
        self.stdout.write(self.style.MIGRATE_LABEL("Of the promotable population"))
        for key, count in figures["of_which"].items():
            self.stdout.write(f"  {_FIELD_LABELS[key]:<34} {count}")

        self.stdout.write("")
        self.stdout.write(self.style.MIGRATE_LABEL("Source signals across the year"))
        for key, count in figures["source_signals"].items():
            self.stdout.write(f"  {_SIGNAL_LABELS[key]:<34} {count}")
