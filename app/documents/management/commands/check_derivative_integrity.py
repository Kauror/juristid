"""Report derivative rows whose object is gone, and objects no row names.

The derivative half of `check_evidence_integrity`, kept apart because the two
stores make different promises: evidence is canonical and its loss is a
restore, a derivative is rebuildable and its loss is a rebuild. Read-only.
Exits non-zero when anything is found, so it can sit in a cron line beside the
evidence check (app/documents/derivative_integrity.py).
"""

from __future__ import annotations

from typing import Any

from django.core.management.base import BaseCommand

from app.documents.derivative_integrity import (
    MISSING_DERIVATIVE_OBJECT,
    ORPHAN_DERIVATIVE_OBJECT,
    check_derivatives,
)


class Command(BaseCommand):
    help = "Check DocumentDerivative rows against the derivative store, both ways. Read-only."

    def add_arguments(self, parser: Any) -> None:
        parser.add_argument(
            "--skip-storage-scan",
            action="store_true",
            help="Check rows only; do not walk the store for objects nothing names.",
        )
        parser.add_argument(
            "--sample",
            type=int,
            default=20,
            help="Show at most N subjects per finding class (0 = all).",
        )

    def handle(self, *args: Any, **options: Any) -> None:
        report = check_derivatives(scan_storage=not options["skip_storage_scan"])
        self.stdout.write(
            f"Derivatives checked:   {report.objects_checked.get('DocumentDerivative', 0)}"
        )
        if not options["skip_storage_scan"]:
            self.stdout.write(f"Stored objects seen:   {report.objects_seen}")

        if report.ok:
            self.stdout.write(self.style.SUCCESS("No derivative integrity problems found."))
            return

        sample = options["sample"]
        grouped = report.by_kind()
        for kind in sorted(grouped):
            findings = grouped[kind]
            self.stdout.write(self.style.ERROR(f"\n{kind}: {len(findings)}"))
            shown = findings if sample <= 0 else findings[:sample]
            for finding in shown:
                self.stdout.write(f"  {finding.subject}\t{finding.detail}")
            if len(findings) > len(shown):
                self.stdout.write(f"  … and {len(findings) - len(shown)} more")

        if MISSING_DERIVATIVE_OBJECT in grouped:
            self.stdout.write(
                "\nMissing objects are rebuilt, not restored: "
                "rebuild_document_derivatives --version-id <id> for the versions named."
            )
        if ORPHAN_DERIVATIVE_OBJECT in grouped:
            self.stdout.write(
                "\nObjects no row names: review with prune_orphaned_derivatives, and "
                "remove with prune_orphaned_derivatives --delete."
            )
        raise SystemExit(1)
