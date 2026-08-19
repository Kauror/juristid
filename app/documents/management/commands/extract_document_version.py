"""Extract one exact version, by id.

The debugging tool. When a file failed and somebody wants to know why, this
runs the same code path the worker runs, on one row, in the foreground, with the
report printed rather than logged.

``--force`` re-claims a version that is already DONE, which is how a parser fix
is verified against the file that motivated it without rebuilding anything else.
"""

from __future__ import annotations

from typing import Any

from django.core.management.base import BaseCommand, CommandError


class Command(BaseCommand):
    help = "Run text extraction for one DocumentVersion."

    def add_arguments(self, parser: Any) -> None:
        parser.add_argument("version_id", help="DocumentVersion UUID.")
        parser.add_argument(
            "--force",
            action="store_true",
            help="Process it even if it is already DONE, FAILED or NOT_APPLICABLE.",
        )

    def handle(self, *args: Any, **options: Any) -> None:
        from app.documents.extraction.orchestrator import claim_version, extract_document_version
        from app.documents.models import DocumentVersion

        version = DocumentVersion.objects.filter(pk=options["version_id"]).first()
        if version is None:
            raise CommandError(f"No DocumentVersion {options['version_id']}.")

        claimed = claim_version(version.pk, force=options["force"])
        if claimed is None:
            raise CommandError(
                f"Version {version.pk} is {version.extraction_state} and is not claimable. "
                "Use --force to process it anyway."
            )

        report = extract_document_version(claimed)
        self.stdout.write(f"  fail:        {claimed.original_filename}")
        self.stdout.write(f"  MIME:        {claimed.mime_type}")
        self.stdout.write(f"  olek:        {report.state}")
        self.stdout.write(f"  tuletisi:    {report.derivatives}")
        self.stdout.write(f"  tekstiosi:   {report.fragments}")
        self.stdout.write(f"  manuseid:    {report.attachments}")
        self.stdout.write(f"  kestus:      {report.seconds:.2f}s")
        if report.error_code:
            self.stdout.write(self.style.ERROR(f"  vea kood:    {report.error_code}"))
        if report.note:
            self.stdout.write(f"  märkus:      {report.note}")
