"""Plan or perform a legacy register import.

    python manage.py import_legacy_register PATH --dry-run --report-dir DIR
    python manage.py import_legacy_register PATH --apply   --report-dir DIR

There is no default mode. One of ``--dry-run`` and ``--apply`` must be given,
and giving neither is an error rather than a safe guess, because the safe guess
is the one people stop reading. An import is not reversible by re-running it.

``--apply`` additionally refuses to run when the environment has not been
cleared for real data, and refuses when the plan still contains rows needing
review unless the operator says, in as many words, that they accept that.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError, CommandParser

from app.legacy_import.apply import apply_plan
from app.legacy_import.contracts import ContractError
from app.legacy_import.enums import RowOutcome
from app.legacy_import.plan_reports import build_summary, write_plan_reports
from app.legacy_import.planner import build_plan
from app.legacy_import.resolution import MappingFileError, MappingTables


class Command(BaseCommand):
    help = "Plan (--dry-run) or perform (--apply) an import of the Excel register."

    def add_arguments(self, parser: CommandParser) -> None:
        parser.add_argument("workbook", help="Path to the .xlsx snapshot.")
        mode = parser.add_mutually_exclusive_group(required=True)
        mode.add_argument(
            "--dry-run",
            action="store_true",
            help="Read the database, decide nothing, write nothing.",
        )
        mode.add_argument(
            "--apply",
            action="store_true",
            help="Perform the import. Not reversible by re-running.",
        )
        parser.add_argument("--report-dir", default="import-output/import")
        parser.add_argument(
            "--mapping-file",
            default=None,
            help="Reviewed owner/organisation/record-mode mappings (TOML or JSON).",
        )
        parser.add_argument(
            "--accept-review-rows",
            action="store_true",
            help="Apply even though some rows still need review. They are skipped, not guessed.",
        )
        parser.add_argument("--notes", default="", help="Recorded on the ImportBatch.")

    def handle(self, *args: Any, **options: Any) -> None:
        path = Path(options["workbook"])
        if not path.exists():
            raise CommandError(f"Workbook not found: {path}")

        try:
            mappings = MappingTables.load(options["mapping_file"])
        except MappingFileError as error:
            raise CommandError(str(error)) from error

        try:
            plan = build_plan(path, mappings=mappings)
        except (ContractError, MappingFileError) as error:
            raise CommandError(str(error)) from error

        summary = build_summary(plan, mode="dry-run" if options["dry_run"] else "apply")
        self._print(summary)

        if not plan.is_complete:  # pragma: no cover - defensive
            raise CommandError("Row accounting is incomplete; refusing to continue.")

        directory = Path(options["report_dir"])
        write_plan_reports(plan, directory, mode=summary["mode"])
        self.stdout.write(self.style.SUCCESS(f"\nReports written to {directory}"))

        if options["dry_run"]:
            self.stdout.write("Dry run: nothing was written to the database.")
            return

        self._guard_apply(plan, options)
        result = apply_plan(plan, notes=options["notes"])

        self.stdout.write("")
        self.stdout.write(self.style.SUCCESS("Applied"))
        self.stdout.write(f"  batch              {result.batch.pk}")
        self.stdout.write(f"  created            {result.created}")
        self.stdout.write(f"  matched            {result.matched}")
        self.stdout.write(f"  already imported   {result.already_imported}")
        self.stdout.write(f"  needing review     {result.review_required}")
        self.stdout.write(f"  reserved numbers   {result.reserved}")
        self.stdout.write(f"  blank rows skipped {result.skipped}")
        self.stdout.write(f"  reference sequence {result.references_reserved}")

    def _guard_apply(self, plan: Any, options: dict[str, Any]) -> None:
        if not getattr(settings, "REAL_DATA_ALLOWED", False):
            raise CommandError(
                "REAL_DATA_ALLOWED is off in this environment. The apply path writes "
                "business records and is not authorised here (docs/secure-pilot-gate.md)."
            )

        review = plan.outcome_counts.get(RowOutcome.REVIEW_REQUIRED.value, 0)
        if review and not options["accept_review_rows"]:
            raise CommandError(
                f"{review} rows still need review. Resolve them, or re-run with "
                "--accept-review-rows to import the rest and leave those rows out."
            )

    def _print(self, summary: dict[str, Any]) -> None:
        source = summary["source"]
        self.stdout.write(self.style.MIGRATE_HEADING(f"Mode: {summary['mode']}"))
        self.stdout.write(f"  file        {source['file_name']}")
        self.stdout.write(f"  sha256      {source['sha256']}")
        self.stdout.write(f"  importer    {source['importer_version']}")
        self.stdout.write(f"  contracts   {source['contract_versions']}")

        self.stdout.write("")
        self.stdout.write(self.style.MIGRATE_HEADING("Outcomes"))
        for key, value in summary["outcomes"].items():
            self.stdout.write(f"  {key:<24} {value}")
        self.stdout.write(
            f"  {'rows considered':<24} {summary['rows_considered']} "
            f"(accounting complete: {summary['accounting_is_complete']})"
        )

        self.stdout.write("")
        self.stdout.write(self.style.MIGRATE_HEADING("Proposed record modes"))
        for key, value in summary["proposed_record_modes"].items():
            self.stdout.write(f"  {key:<24} {value}")

        gaps = summary["mapping_gaps"]
        self.stdout.write("")
        self.stdout.write(self.style.MIGRATE_HEADING("Mapping gaps"))
        self.stdout.write(f"  owners                   {gaps['owners']}")
        self.stdout.write(f"  organisations            {gaps['organisations']}")
        self.stdout.write(f"  statuses                 {gaps['statuses']}")

        self.stdout.write("")
        self.stdout.write(self.style.MIGRATE_HEADING("OneNote"))
        self.stdout.write(f"  links retained           {summary['onenote_links_retained']}")
        self.stdout.write(f"  content status           {summary['onenote_content_status']}")

        candidates = summary["next_action_candidates"]
        self.stdout.write("")
        self.stdout.write(self.style.MIGRATE_HEADING("Next-action candidates"))
        self.stdout.write(f"  total                    {candidates['total']}")
        self.stdout.write(f"  deterministic            {candidates['deterministic']}")
        self.stdout.write(f"  would create NextAction  {candidates['would_create_next_action']}")

        self.stdout.write("")
        self.stdout.write(self.style.MIGRATE_HEADING("Anomalies"))
        for key, value in summary["anomalies"].items():
            self.stdout.write(f"  {key:<40} {value}")

        if summary["structure_findings"]:
            self.stdout.write("")
            for finding in summary["structure_findings"]:
                self.stdout.write(self.style.WARNING(f"  {finding}"))
