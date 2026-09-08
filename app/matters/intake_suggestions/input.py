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

**Bounded, and bounded at the loading rather than at the reading.** The
visible documents are listed first. Their live derivative *rows* are read
next — kind, status and the ``character_count`` the extraction worker already
stored, plus the ``metadata`` column a message's headers live in, which is a
small JSON object rather than a text load. That count is what `_plan` admits
material on, so the budget is spent before any text is fetched, and the
fragments of exactly the admitted derivatives are the last thing read.

Deciding admission from a stored count is the whole point: text the budget
excludes stays in the database instead of being loaded and then discarded.
Prefetching every fragment and reading the first few would bound the rule work
and nothing else.

So the shape is at most three queries, and it is flat — it does not grow with
the number of fragments, the number of documents, or how much material a
Matter has collected, and nothing here loads another Matter. Fewer than three
when there is nothing to ask for: a Matter with no visible document never
reaches the derivative query, and one whose documents admit no text never
reaches the fragment query.

What the budget left out is not silently dropped. A document read in part is
``truncated`` and one not read at all is ``skipped_for_budget`` — kept apart
because they are different facts — and the panel says so rather than letting a
short answer read as «there was nothing in there».
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

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
from app.matters.staging import MatterIntakeFile, MatterIntakeSession

#: How much of one document the rules read, in characters of extracted text.
#:
#: About thirty pages, and the number comes from measurement rather than from
#: taste. The rule vocabulary is 227 signals, and a signal that does not match
#: reads every character it is given, so one full pass costs roughly fifteen to
#: twenty-five seconds per megabyte. The previous ceiling of 400 000 was
#: therefore six to ten seconds of synchronous work for a single document, and
#: it would also swallow the whole Matter budget below and leave nothing for
#: the covering letter beside it.
#:
#: Nothing the rules look for lives past this point. A title is read from the
#: first 2 500 characters of a document, a letterhead from the first 900, and a
#: ministry states its deadline in the opening paragraphs; area and track
#: vocabulary is dense enough that thirty pages classify as well as three
#: hundred. A document that hits the ceiling is marked ``truncated`` and the
#: panel says so rather than pretending the rest was read.
MAX_CHARACTERS_PER_DOCUMENT = 80_000

#: How much extracted text one Matter's analysis may read in total.
#:
#: The per-document ceiling alone bounds nothing: incoming intake accepts
#: twenty files at once and a Matter accrues more later, so twenty large
#: documents would be twenty times the work on every open of the review page.
#: This is the bound that makes one GET's cost independent of how much material
#: a Matter has collected.
#:
#: 200 000 characters is about eighty pages across the whole Matter, which
#: covers the envelope this feature exists for — a covering message, a covering
#: letter, a draft and an explanatory memorandum — and measures at roughly two
#: seconds of rule work on ordinary prose. It is deliberately a character count
#: and not a clock: a limit in seconds would make the same Matter produce
#: different suggestions on a loaded runner than on an idle one, and the
#: analysis has to be reproducible before it is fast.
MAX_TOTAL_ANALYSIS_CHARACTERS = 200_000

#: How many documents may contribute text to one analysis.
#:
#: Twenty is `app.matters.intake.MAX_INTAKE_FILES` — one complete intake
#: envelope. The character budget usually binds first; this bounds the case
#: where a Matter has collected many small documents, so that the work stays
#: proportional to a reading rather than to a filing cabinet.
MAX_TEXT_DOCUMENTS_ANALYSED = 20

#: Which material is read first when a Matter holds more than the budget.
#:
#: Deliberately the roles intake and the extraction worker assign, in the order
#: the product cares about them, and nothing cleverer. A message carries the
#: sender, the subject and the sent time in structured headers; the documents
#: intake captured are the covering letter and the draft; an attachment is
#: whatever arrived stapled behind them; everything else — a member's feedback,
#: a working document, historical material — is not the incoming envelope this
#: feature reads (`app.documents.enums.DocumentRole`).
DOCUMENT_PRIORITY: dict[str, int] = {
    DocumentRole.ORIGINAL_EMAIL: 0,
    DocumentRole.INCOMING_AUTHORITY: 1,
    DocumentRole.EMAIL_ATTACHMENT: 2,
}

#: Where a role the list does not name is read: after all of them, in the order
#: the documents were captured.
DEFAULT_PRIORITY = 3


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
    #: This document's own text ran past :data:`MAX_CHARACTERS_PER_DOCUMENT`,
    #: or past what was left of the Matter's budget when it was reached.
    truncated: bool = False
    #: The Matter's budget was already spent when this document's turn came, so
    #: none of its text was read. A different fact from ``truncated`` and kept
    #: apart from it: one document was read in part, the other not at all.
    skipped_for_budget: bool = False

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

    @property
    def skipped_for_budget(self) -> tuple[SourceDocument, ...]:
        return tuple(document for document in self.documents if document.skipped_for_budget)

    @property
    def partial(self) -> bool:
        """Whether readable material was left unread because of the budget."""
        return bool(self.skipped_for_budget) or any(
            document.truncated for document in self.documents
        )


#: The derivative kinds the analyser consumes. Previews and thumbnails are
#: pictures, and the search projection is somebody else's copy of this text.
_TEXT_KINDS = (DerivativeKind.EXTRACTED_TEXT, DerivativeKind.OCR_TEXT)
_READ_KINDS = (*_TEXT_KINDS, DerivativeKind.EMAIL_METADATA)


def build_analysis_input(
    matter: Any,
    viewer: Any,
    *,
    character_limit: int = MAX_CHARACTERS_PER_DOCUMENT,
    total_limit: int = MAX_TOTAL_ANALYSIS_CHARACTERS,
    document_limit: int = MAX_TEXT_DOCUMENTS_ANALYSED,
) -> AnalysisInput:
    """The visible documents of one Matter, with their live derived content.

    ``viewer`` decides what is read. The Matter itself is the caller's to have
    authorised (every view reaches it through ``get_visible_matter``); the
    documents are authorised again here because a child may be more
    restricted than its parent and only ``visible_to`` knows (docs/adr/0038).
    Authorisation happens *before* the budget, so a document this viewer may
    not see spends none of their budget and changes none of their suggestions.

    **The budget bounds the loading, not only the reading.** Three queries,
    whatever the Matter holds: the visible documents, then the live
    derivatives' own rows, then the fragments of the documents the budget
    actually admits. Deciding what to read from ``character_count`` — which the
    extraction worker already stores on the derivative — is what keeps the
    unread text in the database instead of in this process's memory. Loading
    every fragment and then reading the first few would bound the regex work
    and nothing else.
    """
    documents = [
        document
        for document in Document.objects.filter(matter=matter)
        .visible_to(viewer)
        .select_related("current_version")
        .order_by("created_at", "id")
        if document.current_version_id
    ]
    version_ids = [document.current_version_id for document in documents]

    # The derivative rows only: kind, status and the character count the worker
    # recorded. `metadata` comes with them because a message's headers live in
    # that column and cost one small JSON object, not a text load.
    headers: dict[Any, dict[str, DocumentDerivative]] = {}
    if version_ids:
        for derivative in DocumentDerivative.objects.filter(
            version_id__in=version_ids,
            status=DerivativeStatus.ACTIVE,
            kind__in=_READ_KINDS,
        ).order_by("version_id", "kind"):
            headers.setdefault(derivative.version_id, {})[derivative.kind] = derivative

    admitted, skipped = _plan(
        documents,
        headers,
        character_limit=character_limit,
        total_limit=total_limit,
        document_limit=document_limit,
    )

    # The fragments of exactly the admitted documents, ordered. One query, and
    # its size is bounded by the plan above rather than by the Matter.
    fragments: dict[Any, list[DocumentTextFragment]] = {}
    if admitted:
        for fragment in DocumentTextFragment.objects.filter(
            derivative_id__in=list(admitted.values())
        ).order_by("derivative_id", "ordinal"):
            fragments.setdefault(fragment.derivative_id, []).append(fragment)

    # Spend the budget in the order the plan admitted, not in the order the
    # documents were captured. Assembling in capture order would let the
    # annexes at the front of a Matter exhaust the allowance before the
    # covering letter behind them was reached, which is the priority the plan
    # exists to express. The documents are returned in capture order below.
    read: dict[Any, tuple[tuple[TextBlock, ...], bool]] = {}
    used = 0
    for document_id, derivative_id in admitted.items():
        allowance = min(character_limit, max(total_limit - used, 0))
        blocks, truncated = _blocks_of(fragments.get(derivative_id, []), allowance)
        used += sum(len(block.text) for block in blocks)
        read[document_id] = (blocks, truncated)

    sources: list[SourceDocument] = []
    for document in documents:
        version = document.current_version
        found = headers.get(version.pk, {})
        email_derivative = found.get(DerivativeKind.EMAIL_METADATA)
        blocks, truncated = read.get(document.pk, ((), False))
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
                skipped_for_budget=document.pk in skipped,
            )
        )
    return AnalysisInput(documents=tuple(sources))


def _plan(
    documents: list[Any],
    headers: dict[Any, dict[str, DocumentDerivative]],
    *,
    character_limit: int,
    total_limit: int,
    document_limit: int,
) -> tuple[dict[Any, Any], set[Any]]:
    """Which documents' text is loaded, and which are left for want of budget.

    Returns the admitted ``{document id: derivative id}`` and the ids of the
    documents whose text was not read at all. Pure planning: it reads the
    stored ``character_count`` and loads nothing.

    The order is :data:`DOCUMENT_PRIORITY` and then the order the documents
    were captured, so the same Matter always admits the same documents. A
    message is admitted first because its headers are the most valuable thing
    the analyser has and its body is short; a covering letter next, because
    that is where a ministry states a deadline.
    """
    ranked = sorted(
        documents,
        key=lambda document: (
            DOCUMENT_PRIORITY.get(document.role, DEFAULT_PRIORITY),
            document.created_at,
            str(document.pk),
        ),
    )
    offered: list[tuple[Any, Any, int]] = []
    for document in ranked:
        found = headers.get(document.current_version_id, {})
        text_derivative = found.get(DerivativeKind.EXTRACTED_TEXT) or found.get(
            DerivativeKind.OCR_TEXT
        )
        if text_derivative is None:
            # Nothing to read. Not a budget decision, so not reported as one:
            # the document's extraction state already says why.
            continue
        offered.append((document.pk, text_derivative.pk, text_derivative.character_count or 0))
    return _admit(
        offered,
        character_limit=character_limit,
        total_limit=total_limit,
        document_limit=document_limit,
    )


def _admit(
    offered: list[tuple[Any, Any, int]],
    *,
    character_limit: int,
    total_limit: int,
    document_limit: int,
) -> tuple[dict[Any, Any], set[Any]]:
    """Spend the budget over material already in rank order. Loads nothing.

    ``offered`` is ``(key, payload, recorded character count)`` for everything
    that has text to read, most valuable first. The arithmetic is here rather
    than in each caller because it is the whole of what "bounded" means on this
    surface, and two copies of it would be two answers to how much work one
    request may do (docs/adr/0060, docs/adr/0064).
    """
    admitted: dict[Any, Any] = {}
    skipped: set[Any] = set()
    planned = 0
    for key, payload, character_count in offered:
        if len(admitted) >= document_limit or planned >= total_limit:
            skipped.add(key)
            continue
        admitted[key] = payload
        # A source whose recorded size is missing or zero is planned at the
        # per-document ceiling rather than as free. Both writers always record
        # the count, so this is the defensive reading: a stale or absent number
        # must make the plan smaller, never larger, or the count it is standing
        # in for could admit an unbounded fragment load.
        planned += min(character_count or character_limit, character_limit)
    return admitted, skipped


def _blocks_of(rows: list[Any], character_limit: int) -> tuple[tuple[TextBlock, ...], bool]:
    """Fragments into blocks, truncated at ``character_limit``.

    Takes ``DocumentTextFragment`` rows from the Matter surface and
    :class:`_StoredFragment` from the staged one. Deliberately one function:
    where a document is cut, how a locator becomes a label and what marks a
    message's header summary must not differ by which surface asked
    (docs/adr/0064).
    """
    if not rows or character_limit <= 0:
        return (), bool(rows)
    blocks: list[TextBlock] = []
    used = 0
    truncated = False
    for fragment in rows:
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


@dataclass(frozen=True)
class _StoredFragment:
    """A staged file's fragment, in the shape :func:`_blocks_of` reads.

    The canonical path hands that function ``DocumentTextFragment`` rows; a
    staged file has JSON on its own row instead, because a derivative table
    hangs off a ``DocumentVersion`` that does not exist yet. The truncation,
    the locator and the OCR flag must behave identically either way, so the
    JSON is put into this shape and the same function reads it.
    """

    ordinal: int
    text: str
    locator_kind: str
    locator: dict[str, Any]
    locator_label: str
    text_source: str


def build_intake_analysis_input(
    session: MatterIntakeSession,
    *,
    character_limit: int = MAX_CHARACTERS_PER_DOCUMENT,
    total_limit: int = MAX_TOTAL_ANALYSIS_CHARACTERS,
    document_limit: int = MAX_TEXT_DOCUMENTS_ANALYSED,
) -> AnalysisInput:
    """The same input, read from files staged on `Uus teema` instead.

    Everything after this function is shared with the existing Matter surface:
    the same budget, the same priority order, the same ``AnalysisInput``, the
    same ``analyse``. What differs is only where the text was found — a staged
    file carries its own, because the derivative tables belong to a
    ``DocumentVersion`` and there is no Matter yet to hang one on
    (docs/adr/0064).

    **Authorisation is the session, and it is the caller's.** A staged file
    has no visibility of its own: it is not a record about anything, it is one
    person's unfinished form, and ``MatterIntakeSession.objects.owned_by`` is
    what decides who may reach it. The ``visible_to`` gate on the Matter
    surface has no counterpart here because there is no parent to inherit from
    (`app.matters.intake_staging.get_session`).

    **Two queries, and the budget still bounds the loading.** The rows are read
    first with their text deferred — the plan needs only the recorded character
    count — and the text of exactly the admitted files is the second query.
    Files the budget does not reach stay in the database rather than being
    loaded and then ignored, which is the property the Matter surface has and
    the reason ``text_character_count`` is a column.
    """
    staged = list(session.files.live().in_order().defer("text"))

    ranked = sorted(
        staged,
        key=lambda file: (
            DOCUMENT_PRIORITY.get(file.role, DEFAULT_PRIORITY),
            file.ordinal,
            str(file.pk),
        ),
    )
    admitted, skipped = _admit(
        [
            (file.pk, file.pk, file.text_character_count)
            for file in ranked
            if file.text_character_count
        ],
        character_limit=character_limit,
        total_limit=total_limit,
        document_limit=document_limit,
    )

    stored: dict[Any, list[Any]] = {}
    if admitted:
        for pk, rows in MatterIntakeFile.objects.filter(pk__in=list(admitted)).values_list(
            "pk", "text"
        ):
            stored[pk] = rows if isinstance(rows, list) else []

    # Spend the allowance in the order the plan admitted, not in the order the
    # files were chosen: an annex picked first must not exhaust the budget
    # before the covering letter behind it is reached.
    read: dict[Any, tuple[tuple[TextBlock, ...], bool]] = {}
    used = 0
    for file_pk in admitted:
        allowance = min(character_limit, max(total_limit - used, 0))
        blocks, truncated = _blocks_of(_stored_fragments(stored.get(file_pk, [])), allowance)
        used += sum(len(block.text) for block in blocks)
        read[file_pk] = (blocks, truncated)

    sources: list[SourceDocument] = []
    for file in staged:
        blocks, truncated = read.get(file.pk, ((), False))
        sources.append(
            SourceDocument(
                # A staged file has no version of its own, and inventing an
                # identifier for one would be inventing a fact. Both provenance
                # fields carry the staged row, which is exactly what the excerpt
                # under a suggestion came from.
                document_id=file.pk,
                version_id=file.pk,
                filename=file.original_filename,
                role=file.role,
                extraction_state=file.extraction_state,
                extraction_note=file.extraction_note,
                blocks=blocks,
                email=dict(file.email_metadata) if file.email_metadata else None,
                truncated=truncated,
                skipped_for_budget=file.pk in skipped,
            )
        )
    return AnalysisInput(documents=tuple(sources))


def _stored_fragments(rows: list[Any]) -> list[Any]:
    """A staged file's JSON, in the shape the shared block reader takes."""
    fragments: list[Any] = []
    for ordinal, row in enumerate(rows, start=1):
        if not isinstance(row, dict):  # pragma: no cover - defensive
            continue
        locator = row.get("locator")
        fragments.append(
            _StoredFragment(
                ordinal=ordinal,
                text=str(row.get("text") or ""),
                locator_kind=str(row.get("locator_kind") or LocatorKind.NONE),
                locator=locator if isinstance(locator, dict) else {},
                locator_label=str(row.get("locator_label") or ""),
                text_source=str(row.get("text_source") or TextSource.NATIVE),
            )
        )
    return fragments


def is_done(document: SourceDocument) -> bool:
    return document.extraction_state == ExtractionState.DONE
