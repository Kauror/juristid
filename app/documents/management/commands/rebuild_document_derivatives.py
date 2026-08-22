"""Build derived content again from the originals, then drop what it replaced.

This command is the proof that derivatives are disposable. It re-extracts the
selected versions and the result should be indistinguishable from the first
time. A test asserts exactly that (Stage-2B brief 47, 68).

**The order is extract, then delete.** It used to be the other way around, and
that was a hole in the one guarantee the rest of this subsystem is built to
keep. Deleting first means the window between the delete and a successful parse
is a window in which the file has no searchable text at all — and a parser
regression, a missing OCR language, a full disk, or a `--all` run against a
corpus one bad commit later turns that window into a permanent state for
however many files the run reached. Everything else here already refuses that
order: `_write_derivative` builds the new representation before demoting the
old, and `_record_failure` deliberately leaves an existing ACTIVE derivative
alone. So does this now. A version whose rebuild did not reach DONE keeps
exactly what it had, and is named in the summary.

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
        from app.documents.enums import ExtractionState
        from app.documents.extraction.orchestrator import (
            claim_version,
            discard_inactive_derivatives,
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
        kept = 0
        states: dict[str, int] = {}
        for version in versions.iterator():
            claimed = claim_version(version.pk, force=True)
            if claimed is None:  # pragma: no cover - forced claims do not fail
                continue
            report = extract_document_version(claimed)
            states[report.state] = states.get(report.state, 0) + 1
            if report.state == ExtractionState.DONE:
                # The new representation is live; the one it replaced, and the
                # failure rows of any earlier attempt, are now safe to remove.
                discard_inactive_derivatives(claimed)
                rebuilt += 1
            else:
                # Nothing was promoted, so nothing is dropped. The previous
                # representation is still what search reads — degraded, and
                # recoverable by fixing whatever failed and running again.
                kept += 1

        summary = ", ".join(f"{state}: {count}" for state, count in sorted(states.items()))
        self.stdout.write(self.style.SUCCESS(f"Uuesti ehitatud {rebuilt} versiooni. {summary}"))
        if kept:
            # Said out loud. A rebuild that quietly left a tenth of the corpus
            # on the previous parser's output reads as a clean run otherwise.
            self.stdout.write(
                self.style.WARNING(
                    f"{kept} versiooni ei jõudnud valmis; neil jäi kehtima varasem "
                    "tuletis. Otsing töötab, kuid sisu on vana."
                )
            )
