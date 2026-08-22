"""Reconcile the current portfolio against the final approved register snapshot.

    python manage.py final_register_cutover --snapshot <sha256> --dry-run
    python manage.py final_register_cutover --snapshot <sha256> --apply

No default mode, for the reason the importer, the promotion and the historical
cutover all learned before it: the safe guess is the one people stop reading.
``--dry-run`` decides everything and writes nothing; only ``--apply`` commits,
and only for a digest recorded in ``REVIEWED_SNAPSHOT_SHA256``.

The snapshot must already have been catalogued by ``import_legacy_register``.
This command creates no Matter and reads no workbook — it reconciles against the
immutable source references that import wrote, so what it decides is reproducible
from the database alone.

The output is aggregate. Titles, ``JÄRGMISEKS`` sentences and addressees are
source content and never reach this report; the responsibility breakdown prints
the register's own first names because a source-responsibility figure is
meaningless without them, and those are already the names on every Matter page.
"""

from __future__ import annotations

from typing import Any

from django.core.management.base import BaseCommand, CommandError, CommandParser

from app.legacy_import.final_cutover import (
    ACTIONS,
    REVIEWED_SNAPSHOT_SHA256,
    UnreviewedSnapshot,
    apply_cutover_plan,
    build_cutover_plan,
    summary,
)

_ACTION_LABELS: dict[str, str] = {
    "ACTIVATE": "would become current",
    "KEEP_CURRENT": "already current (kept)",
    "RETIRE": "would leave the current set",
    "ALREADY_RETIRED": "already not current (untouched)",
    "NATIVE_SKIP": "natively created (untouched)",
    "REVIEW_REQUIRED": "review required",
}

_REVIEW_LABELS: dict[str, str] = {
    "RECORDED_CLOSURE": "carries a real recorded closure",
    "AMBIGUOUS_CONTINUATION": "continuation wording without one clear reference",
    "AUTHORED_ENTRIES": "has entries somebody wrote here",
    "OPEN_NEXT_ACTION": "has an open next action",
    "NATIVE_SUBMISSION": "has a submission made here",
}


class Command(BaseCommand):
    help = (
        "Reconcile current FULL/open Matters against the final approved register "
        "snapshot, without inventing a closure."
    )

    def add_arguments(self, parser: CommandParser) -> None:
        parser.add_argument(
            "--snapshot",
            required=True,
            help="SHA-256 of the catalogued workbook snapshot to reconcile against.",
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
            help="Commit the reconciliation. Idempotent.",
        )

    def handle(self, *args: Any, **options: Any) -> None:
        digest = str(options["snapshot"]).strip().lower()
        plan = build_cutover_plan(snapshot_sha256=digest)

        if not plan.candidates:
            raise CommandError(
                f"No catalogued register rows carry snapshot {digest[:16]}…. "
                "Run import_legacy_register for this workbook first."
            )

        self._report(summary(plan))

        if options["dry_run"]:
            self.stdout.write("")
            self.stdout.write("Dry run: nothing was written to the database.")
            if not plan.is_reviewed:
                self.stdout.write(
                    self.style.WARNING(
                        "This snapshot is analysis only. Applying it needs its digest "
                        "recorded in REVIEWED_SNAPSHOT_SHA256 (docs/adr/0021)."
                    )
                )
            return

        try:
            result = apply_cutover_plan(plan)
        except UnreviewedSnapshot as error:
            raise CommandError(str(error)) from error

        self.stdout.write("")
        self.stdout.write(self.style.SUCCESS("Applied"))
        self.stdout.write(f"  became current      {result.activated}")
        self.stdout.write(f"  left current        {result.retired}")
        self.stdout.write(f"  already current     {result.kept}")
        self.stdout.write(f"  fields refreshed    {result.refreshed}")
        self.stdout.write(f"  register state rows {result.state_rows}")
        self.stdout.write(
            "  no disposition, closure date or closing person was invented; "
            "no next action and no submission were created."
        )

    # -- output ------------------------------------------------------------

    def _report(self, figures: dict[str, Any]) -> None:
        self.stdout.write(self.style.MIGRATE_HEADING("Final register cutover"))
        self.stdout.write(f"  operation version   {figures['operation_version']}")
        self.stdout.write(f"  snapshot            {figures['snapshot_sha256'][:16]}…")
        self.stdout.write(
            f"  reviewed snapshot   {'yes' if figures['reviewed_snapshot'] else 'no'}"
        )
        self.stdout.write(f"  reviewed digests    {len(REVIEWED_SNAPSHOT_SHA256)}")
        scope = figures["current_scope_years"]
        # The scope is authority, not a filter, so it is printed beside the
        # digest rather than buried: a reader comparing this run with the last
        # one needs to see which years the approved snapshot speaks for.
        self.stdout.write(
            "  jooksva töö ulatus  "
            + (", ".join(str(year) for year in scope) if scope else "puudub (üle vaatamata)")
        )
        self.stdout.write(f"  rows examined       {figures['examined']}")
        if figures["unmatched_rows"]:
            self.stdout.write(
                self.style.WARNING(
                    f"  rows naming no Matter here: {figures['unmatched_rows']} "
                    "(import them first; this command creates nothing)"
                )
            )
        self.stdout.write("")

        self.stdout.write("Outcome")
        actions = figures["actions"]
        for name in ACTIONS:
            self.stdout.write(f"  {_ACTION_LABELS[name]:<38} {actions[name]}")

        if figures["review_reasons"]:
            self.stdout.write("")
            self.stdout.write("Review reasons")
            for reason, count in figures["review_reasons"].items():
                self.stdout.write(f"  {_REVIEW_LABELS.get(reason, reason):<38} {count}")

        self.stdout.write("")
        self.stdout.write("Current portfolio after this run")
        for sheet, count in figures["current_by_sheet"].items():
            self.stdout.write(f"  {sheet:<38} {count}")
        self.stdout.write(f"  {'TOTAL':<38} {figures['current_total']}")
        self.stdout.write("")
        self.stdout.write(
            f"  {'Arvamusi koostamisel (VÄLJA blank)':<38} {figures['drafting_total']}"
        )

        self.stdout.write("")
        self.stdout.write("Source responsibility across the current set")
        for name, count in figures["source_responsibility"].items():
            self.stdout.write(f"  {(name or '(unassigned)'):<38} {count}")
