"""What the analyser reads: the current versions' live derivatives, and nothing else.

The analyser never opens a file. It reads what the extraction system already
made of each document — the ACTIVE ``EXTRACTED_TEXT`` or ``OCR_TEXT``
derivative's fragments, and the ``EMAIL_METADATA`` record for a message — for
the document's *current* version only. That is the whole of the security
boundary this feature sits behind: the malware gate and the extraction gate
are the extraction worker's (`app.documents.extraction.orchestrator`), and a
document that has not passed them has no derivative to read, so the analyser
has nothing to say about it and says so (docs/adr/0014, docs/adr/0060).

Authorization is applied here, once, before anything is read:
``Document.objects.visible_to(viewer)`` is the same queryset every document
route uses, and a version, derivative or fragment is reached only through a
Document that queryset returned. A restricted annex on a normal Matter is
therefore absent from the analysis for anybody who may not open it — not
present-but-hidden, absent.

**Bounded.** One query lists the documents, one fetches their live
derivatives with the fragments prefetched. Neither grows with the number of
fragments, and nothing here loads another Matter.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

from django.db.models import Prefetch

from app.documents.enums import (
    DerivativeKind,
    DerivativeStatus,
    DocumentRole,
    ExtractionState,
    LocatorKind,
    TextSource,
)
from app.documents.models import Document, DocumentDerivative, DocumentTextFragment
from app.matters.intake_suggestions.types import SourceKind

#: How much of one document the rules read, in characters of extracted text.
#:
#: Roughly 150 pages. The deadline a covering letter states is on its first
#: page and a draft's own heading is on its first page, so a ceiling this far
#: out never costs the value the feature exists for; what it bounds is the
#: regex work a 900-page consolidated annex would otherwise cause on every
#: open of the review page. A document that hits it is marked ``truncated``
#: and the panel says so rather than pretending the rest was read.
MAX_CHARACTERS_PER_DOCUMENT = 400_000


@dataclass(frozen=True)
class TextBlock:
    """One fragment of extracted text, with the locator it was stored under."""

    ordinal: int
    text: str
    locator_label: str
    from_ocr: bool = False
    #: The email header summary the message parser writes as its first
    #: fragment. Read as a header, not as prose (email_common.py).
    is_email_header: bool = False

    @property
    def source_kind(self) -> str:
        if self.is_email_header:
            return SourceKind.EMAIL_HEADER
        return SourceKind.OCR_TEXT if self.from_ocr else SourceKind.NATIVE_TEXT


@dataclass(frozen=True)
class SourceDocument:
    """One document as the analyser sees it: identity, state, text, headers."""

    document_id: Any
    version_id: Any
    filename: str
    role: str
    extraction_state: str
    extraction_note: str = ""
    blocks: tuple[TextBlock, ...] = ()
    #: The structured record the message parser stored, or ``None`` when the
    #: document is not a message (or its metadata is not available yet).
    email: Mapping[str, object] | None = None
    truncated: bool = False

    @property
    def is_email(self) -> bool:
        return self.role == DocumentRole.ORIGINAL_EMAIL or self.email is not None

    @property
    def analysed(self) -> bool:
        """Whether anything was there to read."""
        return bool(self.blocks) or self.email is not None

    @property
    def from_ocr(self) -> bool:
        return any(block.from_ocr for block in self.blocks)

    @property
    def prose_blocks(self) -> tuple[TextBlock, ...]:
        """The document's own text: every block but a message's header summary."""
        return tuple(block for block in self.blocks if not block.is_email_header)

    @property
    def text(self) -> str:
        return "\n\n".join(block.text for block in self.prose_blocks)

    def email_value(self, key: str) -> str:
        """One scalar header, or ``""`` — absent headers stay absent."""
        if self.email is None:
            return ""
        value = self.email.get(key)
        if value is None or isinstance(value, list | dict):
            return ""
        return str(value).strip()


@dataclass(frozen=True)
class AnalysisInput:
    documents: tuple[SourceDocument, ...]

    @property
    def analysed(self) -> tuple[SourceDocument, ...]:
        return tuple(document for document in self.documents if document.analysed)


#: The derivative kinds the analyser consumes. Previews and thumbnails are
#: pictures, and the search projection is somebody else's copy of this text.
_TEXT_KINDS = (DerivativeKind.EXTRACTED_TEXT, DerivativeKind.OCR_TEXT)
_READ_KINDS = (*_TEXT_KINDS, DerivativeKind.EMAIL_METADATA)


def build_analysis_input(
    matter: Any,
    viewer: Any,
    *,
    character_limit: int = MAX_CHARACTERS_PER_DOCUMENT,
) -> AnalysisInput:
    """The visible documents of one Matter, with their live derived content.

    ``viewer`` decides what is read. The Matter itself is the caller's to have
    authorised (every view reaches it through ``get_visible_matter``); the
    documents are authorised again here because a child may be more
    restricted than its parent and only ``visible_to`` knows (docs/adr/0038).
    """
    documents = list(
        Document.objects.filter(matter=matter)
        .visible_to(viewer)
        .select_related("current_version")
        .order_by("created_at", "id")
    )
    version_ids = [
        document.current_version_id for document in documents if document.current_version_id
    ]
    derivatives = (
        DocumentDerivative.objects.filter(
            version_id__in=version_ids,
            status=DerivativeStatus.ACTIVE,
            kind__in=_READ_KINDS,
        )
        .order_by("version_id", "kind")
        .prefetch_related(
            Prefetch("fragments", queryset=DocumentTextFragment.objects.order_by("ordinal"))
        )
        if version_ids
        else []
    )
    by_version: dict[Any, dict[str, DocumentDerivative]] = {}
    for derivative in derivatives:
        by_version.setdefault(derivative.version_id, {})[derivative.kind] = derivative

    sources: list[SourceDocument] = []
    for document in documents:
        version = document.current_version
        if version is None:
            continue
        found = by_version.get(version.pk, {})
        text_derivative = found.get(DerivativeKind.EXTRACTED_TEXT) or found.get(
            DerivativeKind.OCR_TEXT
        )
        email_derivative = found.get(DerivativeKind.EMAIL_METADATA)
        blocks, truncated = _blocks_of(text_derivative, character_limit)
        sources.append(
            SourceDocument(
                document_id=document.pk,
                version_id=version.pk,
                filename=version.original_filename or document.title,
                role=document.role,
                extraction_state=version.extraction_state,
                extraction_note=version.extraction_note,
                blocks=blocks,
                email=dict(email_derivative.metadata or {}) if email_derivative else None,
                truncated=truncated,
            )
        )
    return AnalysisInput(documents=tuple(sources))


def _blocks_of(
    derivative: DocumentDerivative | None, character_limit: int
) -> tuple[tuple[TextBlock, ...], bool]:
    if derivative is None:
        return (), False
    blocks: list[TextBlock] = []
    used = 0
    truncated = False
    for fragment in derivative.fragments.all():
        text = fragment.text or ""
        if not text:
            continue
        remaining = character_limit - used
        if remaining <= 0:
            truncated = True
            break
        if len(text) > remaining:
            text = text[:remaining]
            truncated = True
        used += len(text)
        locator = fragment.locator if isinstance(fragment.locator, dict) else {}
        blocks.append(
            TextBlock(
                ordinal=fragment.ordinal,
                text=text,
                locator_label=fragment.locator_label,
                from_ocr=fragment.text_source == TextSource.OCR,
                is_email_header=(
                    fragment.locator_kind == LocatorKind.SECTION
                    and locator.get("part") == "headers"
                ),
            )
        )
        if truncated:
            break
    return tuple(blocks), truncated


def is_done(document: SourceDocument) -> bool:
    return document.extraction_state == ExtractionState.DONE
