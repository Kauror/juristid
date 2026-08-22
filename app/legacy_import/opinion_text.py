"""Reading text out of archive binaries, under the extraction policy that exists.

The archive would be far more useful with its bodies searchable, and that is not
by itself a reason to open 767 real PDFs with a parser.

The rule in force says an unscanned file is not processed where
``REAL_DATA_ALLOWED`` is on, and that nothing anywhere turns a `PENDING` scan
state into `CLEAN`, because replacing a missing control with a lie about one is
worse than not having the control (docs/adr/0014). The opinions archive is real
Koda correspondence, and the malware scanner that would clear it is a Secure
Pilot Gate deliverable that does not exist yet.

So this module obeys that rule rather than carving an exception out of it:

* where real data is allowed, every binary is marked ``BLOCKED`` — not
  ``FAILED``, not silently left ``PENDING``, and above all not extracted. The
  archive is fully **searchable by metadata** either way, which is what makes
  obeying the rule affordable (brief 24);
* where it is not — CI, a developer machine, the synthetic corpus — extraction
  runs, so the pipeline that will one day process the real archive is exercised
  end to end rather than written and never executed.

There is a case for a narrow trusted-source exception: these are the Chamber's
own outgoing letters, pinned by SHA-256, parsed by a PDF-only parser with no
network and no execution. It is a real argument and it is a **policy decision**,
not an implementation detail, so it is written down as an open decision instead
of being taken here.

Native text only. No OCR, deliberately: OCR over 767 files is expensive, the
existing engine is a shared resource, and a scanned letter with no text layer is
a fact worth recording rather than a gap worth filling at that price (brief 23).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from django.conf import settings
from django.db import transaction
from django.utils import timezone

from app.documents.services import evidence_storage
from app.legacy_import.opinion_binary import OpinionArchiveBinary, OpinionArchiveText
from app.legacy_import.opinion_enums import ArchiveTextState

#: How much of one letter is worth holding for search. The corpus is opinions,
#: not filings: the longest in the measured snapshot is a few dozen pages, and a
#: cap keeps one malformed file from putting a megabyte of repeated glyphs into
#: a search vector.
MAX_BODY_CHARACTERS = 400_000

PARSER_NAME = "opinion-archive-pdf"
PARSER_VERSION = "1"


@dataclass
class TextReport:
    """Aggregates. Never a filename, never a line of a letter."""

    considered: int = 0
    extracted: int = 0
    no_text_layer: int = 0
    blocked: int = 0
    failed: int = 0
    skipped_up_to_date: int = 0
    findings: list[str] = field(default_factory=list)

    def as_text(self) -> str:
        rows = [
            ("vaadatud", self.considered),
            ("tekst eraldatud", self.extracted),
            ("tekstikihti ei ole", self.no_text_layer),
            ("turvapoliitika blokeeris", self.blocked),
            ("ebaõnnestus", self.failed),
            ("juba ajakohane", self.skipped_up_to_date),
        ]
        lines = [f"  {label:<32} {value:>12}" for label, value in rows]
        lines.extend(f"  leid: {finding}" for finding in self.findings)
        return "\n".join(lines)


def extraction_is_permitted() -> bool:
    """Whether a parser may open archive bytes in this environment.

    The same test `app.documents.extraction.orchestrator` applies to an
    unscanned upload, asked in one line so the two cannot drift: an archive
    binary has no scan verdict, and a file with no verdict is not opened where
    real data lives.
    """
    return not settings.REAL_DATA_ALLOWED


def extract_all(*, force: bool = False) -> TextReport:
    """Read every materialised binary that does not already have current text.

    Derived and rebuildable throughout: this only ever writes
    `OpinionArchiveText`, and a failure here cannot touch the bytes it read.
    """
    report = TextReport()
    permitted = extraction_is_permitted()
    storage = evidence_storage()

    binaries = OpinionArchiveBinary.objects.select_related("text").order_by("pk")
    for binary in binaries.iterator():
        report.considered += 1
        existing = getattr(binary, "text", None)
        if existing is not None and not force and _is_current(existing, permitted):
            report.skipped_up_to_date += 1
            continue
        if not permitted:
            _record(
                binary,
                state=ArchiveTextState.BLOCKED,
                note=("Skaneerimata fail reaalandmete keskkonnas — sisu ei avata (docs/adr/0014)."),
            )
            report.blocked += 1
            continue
        _extract_one(binary, storage, report)
    return report


def _is_current(text: OpinionArchiveText, permitted: bool) -> bool:
    """Whether an existing row already reflects this parser and this policy.

    A row that says BLOCKED stops being current the moment extraction becomes
    permitted, which is what makes turning the policy on a re-run rather than a
    migration.
    """
    if text.state == ArchiveTextState.BLOCKED:
        return not permitted
    return text.parser == PARSER_NAME and text.parser_version == PARSER_VERSION


def _extract_one(binary: OpinionArchiveBinary, storage: Any, report: TextReport) -> None:
    from app.documents.enums import DerivativeKind
    from app.documents.extraction.base import SourceFile
    from app.documents.extraction.errors import ExtractionFailed
    from app.documents.extraction.pdf import PdfParser

    try:
        with storage.open(binary.storage_key, "rb") as handle:
            content = handle.read()
    except FileNotFoundError:
        report.failed += 1
        report.findings.append(f"baidi {binary.sha256[:12]}… objekti ei ole salvestuses")
        _record(binary, state=ArchiveTextState.FAILED, note="Salvestusest ei leitud objekti.")
        return

    source = SourceFile(
        content=content,
        filename=f"{binary.sha256}.pdf",
        mime_type=binary.mime_type or "application/pdf",
    )
    try:
        parsed = PdfParser().parse(source)
    except ExtractionFailed as error:
        report.failed += 1
        _record(binary, state=ArchiveTextState.FAILED, note=str(error))
        return
    except Exception as error:
        report.failed += 1
        report.findings.append(f"baidil {binary.sha256[:12]}… tekkis parseri viga")
        _record(binary, state=ArchiveTextState.FAILED, note=f"{type(error).__name__}")
        return

    # A parse yields derivatives, each carrying fragments; the extracted-text
    # one is what this wants, and a PDF produces exactly one of it.
    fragments = [
        fragment
        for derivative in parsed.derivatives
        if derivative.kind == DerivativeKind.EXTRACTED_TEXT
        for fragment in derivative.fragments
    ]
    body = "\n\n".join(fragment.text for fragment in fragments if fragment.text).strip()
    truncated = body[:MAX_BODY_CHARACTERS]
    pages = len(fragments) or None

    if not truncated:
        # A photographed letter is not a failure and not an absence of the file.
        # Recording it as its own state is what lets a coverage report say how
        # much of the archive is scanned rather than how much is broken.
        report.no_text_layer += 1
        _record(
            binary,
            state=ArchiveTextState.NO_TEXT_LAYER,
            page_count=pages,
            note="PDF-il ei ole tekstikihti; sisu on tõenäoliselt skaneeritud.",
        )
        return

    report.extracted += 1
    _record(
        binary,
        state=ArchiveTextState.DONE,
        body=truncated,
        page_count=pages,
        note="Lühendatud." if len(body) > MAX_BODY_CHARACTERS else "",
    )


@transaction.atomic
def _record(
    binary: OpinionArchiveBinary,
    *,
    state: str,
    body: str = "",
    page_count: int | None = None,
    note: str = "",
) -> OpinionArchiveText:
    """One text row per binary, replaced in place by a re-extraction."""
    text, _ = OpinionArchiveText.objects.update_or_create(
        binary=binary,
        defaults={
            "state": state,
            "body": body,
            "page_count": page_count,
            "characters": len(body),
            "parser": PARSER_NAME,
            "parser_version": PARSER_VERSION,
            "extracted_at": timezone.now(),
            "note": note,
        },
    )
    return text
