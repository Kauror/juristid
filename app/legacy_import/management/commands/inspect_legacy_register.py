"""Look at a register snapshot without touching anything.

    python manage.py inspect_legacy_register private-data/register.xlsx \\
        --report-dir import-output/inspection

No database connection, no writes, no network. That is a hard requirement
rather than a nicety: this is the command an operator runs on the real workbook
before anything else exists, and it has to work on a laptop.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from django.core.management.base import BaseCommand, CommandError, CommandParser

from app.legacy_import.contracts import ContractError
from app.legacy_import.inspection import build_summary, inspect_workbook, write_inspection_reports


class Command(BaseCommand):
    help = "Inventory and validate a Tööd eelnõudega snapshot. Read-only, no database."

    # Django would otherwise run system checks, which reach for the database.
    requires_system_checks: list[str] = []
    requires_migrations_checks = False

    def add_arguments(self, parser: CommandParser) -> None:
        parser.add_argument("workbook", help="Path to the .xlsx snapshot.")
        parser.add_argument(
            "--report-dir",
            default="import-output/inspection",
            help="Where reports are written. Must be ignored local storage for real data.",
        )
        parser.add_argument(
            "--no-reports",
            action="store_true",
            help="Print the summary only; write nothing to disk.",
        )

    def handle(self, *args: Any, **options: Any) -> None:
        path = Path(options["workbook"])
        if not path.exists():
            raise CommandError(f"Workbook not found: {path}")

        try:
            inspection = inspect_workbook(path)
        except ContractError as error:
            raise CommandError(f"Era contract problem: {error}") from error

        summary = build_summary(inspection)
        self._print(summary)

        if options["no_reports"]:
            return

        directory = Path(options["report_dir"])
        written = write_inspection_reports(inspection, directory)
        self.stdout.write("")
        self.stdout.write(self.style.SUCCESS(f"Reports written to {directory}"))
        self.stdout.write("  aggregate (safe to publish):")
        for report in written.aggregate:
            self.stdout.write(f"    {report.name}")
        self.stdout.write("  row-level (local only, never uploaded):")
        for report in written.row_level:
            self.stdout.write(f"    {report.name}")

    def _print(self, summary: dict[str, Any]) -> None:
        source = summary["source"]
        self.stdout.write(self.style.MIGRATE_HEADING("Source"))
        self.stdout.write(f"  file          {source['file_name']}")
        self.stdout.write(f"  sha256        {source['sha256']}")
        self.stdout.write(f"  bytes         {source['byte_size']}")
        self.stdout.write(f"  parser        {source['parser_version']}")
        self.stdout.write(f"  contracts     {source['contract_versions']}")
        self.stdout.write(f"  year sheets   {source['year_sheets']}")
        if source["sheets_without_contract"]:
            self.stdout.write(
                self.style.ERROR(f"  NO CONTRACT   {source['sheets_without_contract']}")
            )

        self.stdout.write("")
        self.stdout.write(self.style.MIGRATE_HEADING("Sheets"))
        self.stdout.write(
            "  {:<6} {:>6} {:>8} {:>7} {:>7} {:>7} {:>7}".format(
                "sheet", "rows", "matters", "refs", "links", "next", "status"
            )
        )
        for sheet in summary["sheets"]:
            counts = sheet.get("counts") or {}
            self.stdout.write(
                "  {:<6} {:>6} {:>8} {:>7} {:>7} {:>7} {:>7}".format(
                    sheet["name"],
                    counts.get("rows_below_header", 0),
                    counts.get("matter_rows", 0),
                    counts.get("valid_references", 0),
                    counts.get("hyperlinks", 0),
                    counts.get("next_action_populated", 0),
                    counts.get("status_populated", 0),
                )
            )

        totals = summary["totals"]
        self.stdout.write("")
        self.stdout.write(self.style.MIGRATE_HEADING("Totals"))
        for key in sorted(totals):
            self.stdout.write(f"  {key:<28} {totals[key]}")

        self.stdout.write("")
        self.stdout.write(self.style.MIGRATE_HEADING("Row outcomes"))
        for key, value in summary["outcomes"].items():
            self.stdout.write(f"  {key:<28} {value}")

        self.stdout.write("")
        self.stdout.write(self.style.MIGRATE_HEADING("Anomalies"))
        if not summary["anomalies"]:
            self.stdout.write("  none")
        for key, value in summary["anomalies"].items():
            self.stdout.write(f"  {key:<40} {value}")

        if summary["duplicate_references"]:
            self.stdout.write("")
            self.stdout.write(
                self.style.WARNING(
                    f"  duplicate references: {len(summary['duplicate_references'])}"
                )
            )

        candidates = summary["next_action_candidates"]
        self.stdout.write("")
        self.stdout.write(self.style.MIGRATE_HEADING("Next-action candidates"))
        self.stdout.write(f"  total          {candidates['total']}")
        self.stdout.write(f"  deterministic  {candidates['deterministic']}")
        for rule, count in candidates["by_rule"].items():
            self.stdout.write(f"    {rule:<24} {count}")

        vocabulary = summary["vocabulary"]
        self.stdout.write("")
        self.stdout.write(self.style.MIGRATE_HEADING("Controlled vocabulary"))
        self.stdout.write(f"  workbook labels  {vocabulary['workbook_label_count']}")
        self.stdout.write(f"  seeded labels    {vocabulary['known_label_count']}")
        for key in (
            "labels_missing_from_seed",
            "labels_missing_from_workbook",
            "used_labels_not_in_controlled_vocabulary",
        ):
            if vocabulary[key]:
                self.stdout.write(self.style.WARNING(f"  {key}: {vocabulary[key]}"))

        if summary["structure_findings"]:
            self.stdout.write("")
            self.stdout.write(self.style.MIGRATE_HEADING("Structure findings"))
            for finding in summary["structure_findings"]:
                self.stdout.write(self.style.WARNING(f"  {finding}"))
