"""Throw away derived content and build it again from the originals.

This command is the proof that derivatives are disposable. It deletes
`DocumentDerivative`, `DocumentTextFragment` and every stored preview for the
selected versions, sets them back to PENDING, and re-extracts — and the result
should be indistinguishable from the first time. A test asserts exactly that
(Stage-2B brief 47, 68).

What it does **not** touch: `DocumentVersion`, its bytes, its checksum, or the
`EmailAttachmentLink` rows recording which message an attachment arrived in.
Those are evidence and provenance; only the parser's opinions are rebuilt.

Scope is required. There is no bare form that rebuilds the whole archive,
because a command whose default is "reprocess everything" is one typo away from
an afternoon of OCR (Stage-2B brief 48).
"""

from __future__ import annotations

from typing import Any

from django.core.management.base import BaseCommand, CommandError


class Command(BaseCommand):
    help = "Delete and regenerate derived content for the selected document versions."

    def add_arguments(self, parser: Any) -> None:
        parser.add_argument("--matter", help="Matter reference, e.g. 2026_42.")
        parser.add_argument("--document", help="Document UUID.")
        # Not `--version`: every Django management command already has one, and
        # argparse raises at parse time rather than at definition time, so the
        # collision only appeared when somebody ran the command. It did.
        parser.add_argument("--version-id", dest="version_id", help="DocumentVersion UUID.")
        parser.add_argument(
            "--all",
            action="store_true",
            help="Every version in the database. Required to be explicit; there is no default.",
        )
        parser.add_argument(
            "--dry-run",
            action="store_true",
            help="Report what would be rebuilt and change nothing.",
        )

    def handle(self, *args: Any, **options: Any) -> None:
        from app.documents.extraction.orchestrator import (
            claim_version,
            discard_derivatives,
            extract_document_version,
        )
        from app.documents.models import DocumentVersion
        from app.matters.models import Matter

        versions = DocumentVersion.objects.select_related("document", "document__matter")
        selectors = 0

        if options["version_id"]:
            versions = versions.filter(pk=options["version_id"])
            selectors += 1
        if options["document"]:
            versions = versions.filter(document_id=options["document"])
            selectors += 1
        if options["matter"]:
            parsed = Matter.parse_reference(options["matter"])
            if parsed is None:
                raise CommandError(f"{options['matter']!r} is not a Matter reference.")
            year, number = parsed
            versions = versions.filter(
                document__matter__reference_year=year,
                document__matter__reference_number=number,
            )
            selectors += 1
        if options["all"]:
            selectors += 1

        if selectors == 0:
            raise CommandError(
                "Choose what to rebuild: --version-id, --document, --matter or --all."
            )

        total = versions.count()
        if options["dry_run"]:
            self.stdout.write(f"Ehitaks uuesti {total} versiooni tuletised. Midagi ei muudetud.")
            return

        rebuilt = 0
        states: dict[str, int] = {}
        for version in versions.iterator():
            discard_derivatives(version)
            claimed = claim_version(version.pk, force=True)
            if claimed is None:  # pragma: no cover - forced claims do not fail
                continue
            report = extract_document_version(claimed)
            states[report.state] = states.get(report.state, 0) + 1
            rebuilt += 1

        summary = ", ".join(f"{state}: {count}" for state, count in sorted(states.items()))
        self.stdout.write(self.style.SUCCESS(f"Uuesti ehitatud {rebuilt} versiooni. {summary}"))
