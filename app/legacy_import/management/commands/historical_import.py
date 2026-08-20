"""The historical corpus importer.

    manage.py historical_import inspect
    manage.py historical_import plan
    manage.py historical_import dry-run
    manage.py historical_import apply
    manage.py historical_import materialise [--limit N]
    manage.py historical_import status
    manage.py historical_import verify

The phases are separate commands rather than flags on one, because they have
genuinely different consequences and a flag is easy to mistype. `plan` reads and
writes nothing. `dry-run` executes the real plan against the real schema and
rolls it back. `apply` commits. `materialise` streams 4.14 GiB of originals and
is resumable.

**The apply gate is not advisory.** Source hashes must match, the audit baseline
must reconcile, and the plan must carry no fatal finding. If any of those fails,
the command stops rather than importing what it can — a partially imported
historical corpus looks exactly like a complete one to everybody who reads it
later (Stage-2D brief 46).
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction
from django.utils import timezone

IMPORTER_VERSION = "historical-corpus/1.0.0"


class Command(BaseCommand):
    help = "Import the historical Excel + OneNote corpus."

    def add_arguments(self, parser: Any) -> None:
        parser.add_argument(
            "phase",
            choices=["inspect", "plan", "dry-run", "apply", "materialise", "status", "verify"],
        )
        parser.add_argument("--excel", type=Path, help="Tööd eelnõudega.xlsx")
        parser.add_argument("--archive", type=Path, help="onenote-desktop-archive/")
        parser.add_argument("--audit", type=Path, help="migration-audit/")
        parser.add_argument(
            "--expect-excel-sha256", default="", help="Refuse to run against anything else."
        )
        parser.add_argument("--expect-manifest-sha256", default="")
        parser.add_argument(
            "--limit", type=int, default=None, help="materialise: stop after this many files."
        )
        parser.add_argument("--report", type=Path, help="Write a JSON summary here.")

    # -- entry point -------------------------------------------------------

    def handle(self, *args: Any, **options: Any) -> None:
        phase = options["phase"]
        if phase == "status":
            return self._status()
        if phase == "verify":
            return self._verify()
        if phase == "materialise":
            return self._materialise(options)

        plan = self._plan(options)
        if phase in {"inspect", "plan"}:
            self.stdout.write(plan.as_text())
            self.stdout.write("")
            for finding in plan.findings:
                self.stdout.write(f"  {finding}")
            if options.get("report"):
                self._write_report(options["report"], plan.summary())
            return None

        self._require_gate(plan)
        if phase == "dry-run":
            return self._dry_run(plan, options)
        return self._apply(plan, options)

    # -- phases ------------------------------------------------------------

    def _plan(self, options: dict) -> Any:
        from app.legacy_import.historical_plan import PlanError, build_plan

        excel, archive, audit = self._paths(options)
        try:
            return build_plan(
                excel_path=excel,
                archive_root=archive,
                audit_root=audit,
                expected_excel_sha256=options["expect_excel_sha256"],
                expected_manifest_sha256=options["expect_manifest_sha256"],
            )
        except PlanError as error:
            raise CommandError(str(error)) from error

    def _require_gate(self, plan: Any) -> None:
        """Everything that must be true before the corpus may be written."""
        if not settings.REAL_DATA_ALLOWED:
            raise CommandError(
                "REAL_DATA_ALLOWED is off in this environment. The historical corpus is real "
                "Koda material and may only be imported where the deployment says so."
            )
        fatal = [finding for finding in plan.findings if "reconciles" not in finding]
        if fatal:
            raise CommandError(
                "The plan does not reconcile with the audit baseline:\n  "
                + "\n  ".join(fatal)
                + "\nRefusing to import. A partially reconciled corpus is "
                "indistinguishable from a complete one once it is in."
            )
        if plan.warnings:
            self.stdout.write(
                self.style.WARNING(f"{len(plan.warnings)} warning(s); none is fatal.")
            )

    def _dry_run(self, plan: Any, options: dict) -> None:
        """The real plan, against the real schema, rolled back.

        Not a simulation — the same functions apply runs. A dry run that
        exercised different code would agree with apply right up until the day
        it mattered (Stage-2D brief 45).
        """
        from app.legacy_import.historical_apply import apply_structure, open_batch
        from app.legacy_import.onenote_archive import OneNoteArchive

        archive = OneNoteArchive(plan.archive_root)
        self.stdout.write("Dry run — every write below is rolled back.\n")

        class _Rollback(Exception):
            pass

        carrier: dict[str, Any] = {}
        try:
            with transaction.atomic():
                batch = open_batch(plan, importer_version=IMPORTER_VERSION)
                # Stashed outside the transaction's scope before the rollback,
                # so the counts survive the exception that discards the rows
                # they describe.
                carrier["report"] = apply_structure(plan, batch=batch, archive=archive)
                raise _Rollback
        except _Rollback:
            pass

        report = carrier["report"]
        self.stdout.write(report.as_text())
        self.stdout.write("")
        self.stdout.write(self.style.SUCCESS("Rolled back. Nothing was written."))
        if options.get("report"):
            self._write_report(options["report"], {"dry_run": report.__dict__ | {"batch_id": None}})

    def _apply(self, plan: Any, options: dict) -> None:
        from app.legacy_import.historical_apply import apply_structure, open_batch
        from app.legacy_import.onenote_archive import OneNoteArchive

        archive = OneNoteArchive(plan.archive_root)
        batch = open_batch(plan, importer_version=IMPORTER_VERSION)
        self.stdout.write(f"Import batch {batch.pk}\n")

        report = apply_structure(plan, batch=batch, archive=archive)
        batch.finished_at = timezone.now()
        batch.created_matter_count = report.onenote_matters_created
        batch.matched_count = report.exact_links_created
        batch.unmatched_count = len(report.exact_links_unmatched)
        batch.save()

        self.stdout.write(report.as_text())
        if report.exact_links_unmatched:
            self.stdout.write(
                self.style.WARNING(
                    f"\n{len(report.exact_links_unmatched)} exact link(s) name no Matter in the "
                    "register. Run the Excel import first if that is unexpected."
                )
            )
        self.stdout.write(
            self.style.SUCCESS(
                "\nStructure imported. Run `historical_import materialise` to copy the "
                "originals; the corpus is readable in the meantime."
            )
        )
        if options.get("report"):
            self._write_report(options["report"], report.__dict__ | {"batch_id": str(batch.pk)})

    def _materialise(self, options: dict) -> None:
        from app.legacy_import.historical_apply import (
            materialise_resources,
            pending_materialisations,
        )
        from app.legacy_import.onenote_archive import OneNoteArchive

        archive_root = options.get("archive") or self._configured("archive")
        if archive_root is None:
            raise CommandError("No archive path. Pass --archive or set HISTORICAL_SOURCE_ROOT.")
        archive = OneNoteArchive(Path(archive_root))
        waiting = len(pending_materialisations())
        limit = options.get("limit")
        self.stdout.write(f"{waiting} file(s) waiting. Copying {limit or 'all'}.")

        report = materialise_resources(archive=archive, limit=limit)
        self.stdout.write(report.as_text())
        remaining = len(pending_materialisations())
        if remaining:
            self.stdout.write(f"\n{remaining} still waiting. Re-run to continue.")
        else:
            self.stdout.write(self.style.SUCCESS("\nEvery file is in."))

    def _status(self) -> None:
        from app.documents.models import DocumentVersion
        from app.legacy_import.historical_apply import pending_materialisations
        from app.legacy_import.source_pages import (
            HistoricalMatchCandidate,
            LegacySourcePage,
            LegacySourceResource,
            LegacySourceResourceImport,
            MatterSourcePage,
        )
        from app.matters.enums import MatterOrigin
        from app.matters.models import Matter

        rows = [
            ("Excel Matters", Matter.objects.filter(origin=MatterOrigin.LEGACY_IMPORT).count()),
            (
                "OneNote-only Matters",
                Matter.objects.filter(origin=MatterOrigin.LEGACY_ONENOTE).count(),
            ),
            ("source pages", LegacySourcePage.objects.count()),
            ("Matter ↔ page links", MatterSourcePage.objects.count()),
            ("catalogued resources", LegacySourceResource.objects.count()),
            ("materialised documents", LegacySourceResourceImport.objects.count()),
            ("still to materialise", len(pending_materialisations())),
            ("pending review", HistoricalMatchCandidate.objects.filter(state="PENDING").count()),
            (
                "awaiting extraction",
                DocumentVersion.objects.filter(extraction_state="PENDING").count(),
            ),
        ]
        for label, value in rows:
            self.stdout.write(f"  {label:<24} {value:>8,}")

    def _verify(self) -> None:
        """Check what the import claims against what the database holds."""
        from app.legacy_import.source_pages import (
            LegacySourcePage,
            LegacySourceResourceImport,
            MatterSourcePage,
            ResourceImportState,
            SourceMatchMethod,
        )
        from app.matters.enums import MatterOrigin
        from app.matters.models import Matter

        problems: list[str] = []

        from django.db.models import Count

        duplicates = (
            LegacySourcePage.objects.values("source_page_id")
            .annotate(n=Count("pk"))
            .filter(n__gt=1)
            .count()
        )
        if duplicates:
            problems.append(f"{duplicates} source page(s) imported more than once")

        for matter in Matter.objects.filter(origin=MatterOrigin.LEGACY_ONENOTE):
            if matter.reference_year is not None or matter.reference_number is not None:
                problems.append(f"{matter.pk}: OneNote-only Matter carries a register reference")
            primaries = matter.source_pages.filter(
                match_method=SourceMatchMethod.ONENOTE_ONLY_MATTER
            ).count()
            if primaries != 1:
                problems.append(f"{matter.pk}: {primaries} primary source pages, expected 1")

        mismatched = 0
        for record in LegacySourceResourceImport.objects.filter(
            state=ResourceImportState.IMPORTED
        ).select_related("resource", "document_version"):
            if record.document_version and record.document_version.sha256 != record.resource.sha256:
                mismatched += 1
        if mismatched:
            problems.append(f"{mismatched} document(s) whose SHA-256 differs from the archive")

        orphan_links = MatterSourcePage.objects.filter(source_page__isnull=True).count()
        if orphan_links:
            problems.append(f"{orphan_links} link(s) with no source page")

        if problems:
            for problem in problems:
                self.stdout.write(self.style.ERROR(f"  {problem}"))
            raise CommandError(f"{len(problems)} verification problem(s).")
        self.stdout.write(self.style.SUCCESS("  every check passed"))

    # -- helpers -----------------------------------------------------------

    def _paths(self, options: dict) -> tuple[Path, Path, Path]:
        resolved: dict[str, Path] = {}
        for label in ("excel", "archive", "audit"):
            candidate = options.get(label) or self._configured(label)
            if candidate is None:
                raise CommandError(
                    f"No {label} path. Pass --{label} or set HISTORICAL_SOURCE_ROOT."
                )
            path = Path(candidate)
            if not path.exists():
                raise CommandError(f"{label}: {path} does not exist.")
            resolved[label] = path
        return resolved["excel"], resolved["archive"], resolved["audit"]

    def _configured(self, what: str) -> Path | None:
        """The conventional layout under HISTORICAL_SOURCE_ROOT.

        Set once in the deployment's environment so an operator types a phase
        name and nothing else — a long path retyped at 2am is a path that ends
        up pointing at the wrong archive.
        """
        root = getattr(settings, "HISTORICAL_SOURCE_ROOT", "")
        if not root:
            return None
        base = Path(root)
        return {
            "excel": base / "excel" / "Tööd eelnõudega.xlsx",
            "archive": base / "onenote-desktop-archive",
            "audit": base / "migration-audit",
        }[what]

    def _write_report(self, path: Path, payload: dict) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2, default=str), encoding="utf-8"
        )
        self.stdout.write(f"\nReport written to {path}")
