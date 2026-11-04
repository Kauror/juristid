"""What text belongs to an attachment, and how sure we can be.

A OneNote page is a working history. Its text is kept whole and verbatim
(the page capture is the source), and each attachment occurrence gets a
*context record*: the exact source text that structurally relates to it, the
blocks it came from and the basis of the relation. Nothing is summarised and
nothing is attributed by guess.

OneNote has no caption field on an attached file (`one:InsertedFile` carries
only `pathCache`, `pathSource`, `preferredName`), so an "explicit caption" can
only be structural. The bases, strongest first:

``ATTACHED_NOTE``           text in an outline element nested *under* the file's element
``CONTAINER_HEADING``       the file sits nested under a text element (its outline parent)
``ADJACENT_SINGLE_FILE``    text directly before/after a run holding only this file, with
                            no unfetched object beside it — a reviewable candidate only
``ADJACENT_SHARED``         text beside a run of several files (or unknown objects):
                            context for all of them, attributed to none
``PAGE_NARRATIVE``          everything else; reachable from every file via the page capture

Reading order is reliable only within one outline: separate outlines on a page
are positioned freely, so text from another outline is never "adjacent".

Each capture produces fresh context records. A changed comment with unchanged
bytes is a new context record, not a new document version; the previous record
stays.
"""

from __future__ import annotations

import xml.etree.ElementTree as ET
from collections.abc import Sequence
from dataclasses import dataclass
from enum import StrEnum

NS = "{http://schemas.microsoft.com/office/onenote/2013/onenote}"


#: ``Block.outline`` for an object placed directly on the page canvas.
FLOATING = -1


class BlockKind(StrEnum):
    TEXT = "TEXT"
    FILE = "FILE"
    IMAGE = "IMAGE"
    TABLE = "TABLE"
    BLANK = "BLANK"
    UNFETCHED_OBJECT = "UNFETCHED_OBJECT"  # empty one:OE: could be a file not yet downloaded


@dataclass(frozen=True)
class Block:
    ordinal: int  # document order across the page
    kind: BlockKind
    outline: int  # index of its one:Outline
    depth: int
    parent: int | None  # ordinal of the outline element it is nested under
    text: str = ""  # verbatim plain text (formatting stays in the page capture)
    object_id: str = ""
    created: str = ""  # one:OE/@creationTime, verbatim
    modified: str = ""
    author_ref: str = ""  # opaque authorResolutionID; never displayed as a name


class ContextBasis(StrEnum):
    ATTACHED_NOTE = "ATTACHED_NOTE"
    CONTAINER_HEADING = "CONTAINER_HEADING"
    ADJACENT_SINGLE_FILE = "ADJACENT_SINGLE_FILE"
    ADJACENT_SHARED = "ADJACENT_SHARED"
    PAGE_NARRATIVE = "PAGE_NARRATIVE"


#: Bases strong enough to show the text *as this file's comment*.
ATTRIBUTED = frozenset({ContextBasis.ATTACHED_NOTE, ContextBasis.CONTAINER_HEADING})


@dataclass(frozen=True)
class ContextRecord:
    occurrence: str  # the file block's object id
    capture_id: str
    basis: ContextBasis
    block_ordinals: tuple[int, ...]
    text: tuple[str, ...]  # verbatim, one per block

    @property
    def attributed(self) -> bool:
        return self.basis in ATTRIBUTED


def _kind(oe: ET.Element) -> tuple[BlockKind, str]:
    if oe.find(f"{NS}InsertedFile") is not None:
        return BlockKind.FILE, ""
    text_node = oe.find(f"{NS}T")
    if text_node is not None:
        text = "".join(text_node.itertext())
        return (BlockKind.TEXT, text) if text.strip() else (BlockKind.BLANK, "")
    if oe.find(f"{NS}Image") is not None:
        return BlockKind.IMAGE, ""
    if oe.find(f"{NS}Table") is not None:
        return BlockKind.TABLE, ""
    own = [child.tag for child in oe if child.tag != f"{NS}OEChildren"]
    # D1 pilot: a file not yet fetched leaves an empty one:OE — or, in a bulleted
    # list, an OE holding only its one:List marker. Both may hide a file.
    if not own or own == [f"{NS}List"]:
        return BlockKind.UNFETCHED_OBJECT, ""
    return BlockKind.BLANK, ""


def blocks_from_desktop_xml(page_xml: str) -> list[Block]:
    """Ordered blocks with outline, depth and parent, from ``GetPageContent`` XML.

    Text is taken verbatim from ``one:T``; OneNote stores it as an HTML
    fragment, so tags are stripped only here, for matching — the capture keeps
    the original.
    """
    import re

    root = ET.fromstring(page_xml)  # noqa: S314
    blocks: list[Block] = []

    def walk(container: ET.Element, outline: int, depth: int, parent: int | None) -> None:
        for oe in container.findall(f"{NS}OE"):
            kind, text = _kind(oe)
            ordinal = len(blocks)
            blocks.append(
                Block(
                    ordinal=ordinal,
                    kind=kind,
                    outline=outline,
                    depth=depth,
                    parent=parent,
                    text=re.sub(r"<[^>]+>", "", text),
                    object_id=oe.get("objectID", ""),
                    created=oe.get("creationTime", ""),
                    modified=oe.get("lastModifiedTime", ""),
                    author_ref=oe.get("authorResolutionID", ""),
                )
            )
            for children in oe.findall(f"{NS}OEChildren"):
                walk(children, outline, depth + 1, ordinal)

    for index, outline in enumerate(root.findall(f"{NS}Outline")):
        for children in outline.findall(f"{NS}OEChildren"):
            walk(children, index, 1, None)
    # Floating objects sit on the page canvas outside any outline. They have no
    # outline element, hence no creationTime (ADR 0092 §6) and no reading-order
    # position; the D1 pilot found one that was invisible until the page was
    # opened. The element's own lastModifiedTime is kept; it is not an addition date.
    for element in root:
        if element.tag in {f"{NS}InsertedFile", f"{NS}Image"}:
            blocks.append(
                Block(
                    ordinal=len(blocks),
                    kind=BlockKind.FILE if element.tag == f"{NS}InsertedFile" else BlockKind.IMAGE,
                    outline=FLOATING,
                    depth=0,
                    parent=None,
                    object_id=element.get("objectID", ""),
                    created="",
                    modified=element.get("lastModifiedTime", ""),
                )
            )
    return blocks


def context_for(
    blocks: Sequence[Block], file_ordinal: int, *, capture_id: str
) -> list[ContextRecord]:
    """Every context record for one file block, strongest first. Never invents text."""
    target = blocks[file_ordinal]
    if target.kind is not BlockKind.FILE:
        raise ValueError("not a file block")
    records: list[ContextRecord] = []
    floating = target.outline == FLOATING

    def record(basis: ContextBasis, found: Sequence[Block]) -> None:
        if found:
            records.append(
                ContextRecord(
                    target.object_id,
                    capture_id,
                    basis,
                    tuple(b.ordinal for b in found),
                    tuple(b.text for b in found),
                )
            )

    if floating:
        # No outline, no position: only the page narrative relates to it.
        record(ContextBasis.PAGE_NARRATIVE, [b for b in blocks if b.kind is BlockKind.TEXT])
        return records
    record(
        ContextBasis.ATTACHED_NOTE,
        [b for b in blocks if b.parent == target.ordinal and b.kind is BlockKind.TEXT],
    )
    parent = blocks[target.parent] if target.parent is not None else None
    if parent is not None and parent.kind is BlockKind.TEXT:
        record(ContextBasis.CONTAINER_HEADING, [parent])

    siblings = [b for b in blocks if b.outline == target.outline and b.parent == target.parent]
    index = next(i for i, b in enumerate(siblings) if b.ordinal == target.ordinal)
    runny = {BlockKind.FILE, BlockKind.BLANK, BlockKind.UNFETCHED_OBJECT, BlockKind.IMAGE}
    lo = index
    while lo > 0 and siblings[lo - 1].kind in runny:
        lo -= 1
    hi = index
    while hi + 1 < len(siblings) and siblings[hi + 1].kind in runny:
        hi += 1
    run = siblings[lo : hi + 1]
    neighbours = [
        b
        for b in (
            siblings[lo - 1] if lo > 0 else None,
            siblings[hi + 1] if hi + 1 < len(siblings) else None,
        )
        if b is not None and b.kind is BlockKind.TEXT
    ]
    others = [
        b
        for b in run
        if b.ordinal != target.ordinal
        and b.kind in {BlockKind.FILE, BlockKind.UNFETCHED_OBJECT, BlockKind.IMAGE}
    ]
    record(
        ContextBasis.ADJACENT_SHARED if others else ContextBasis.ADJACENT_SINGLE_FILE, neighbours
    )

    used = {o for r in records for o in r.block_ordinals}
    record(
        ContextBasis.PAGE_NARRATIVE,
        [b for b in blocks if b.kind is BlockKind.TEXT and b.ordinal not in used],
    )
    return records


class ContextChange(StrEnum):
    UNCHANGED = "UNCHANGED"
    TEXT_CHANGED = "TEXT_CHANGED"
    BASIS_CHANGED = "BASIS_CHANGED"
    NEW = "NEW"


def compare_context(previous: ContextRecord | None, current: ContextRecord) -> ContextChange:
    """Compare one basis across two captures. A change appends; nothing is overwritten."""
    if previous is None:
        return ContextChange.NEW
    if previous.basis is not current.basis:
        return ContextChange.BASIS_CHANGED
    return ContextChange.UNCHANGED if previous.text == current.text else ContextChange.TEXT_CHANGED


def searchable_context(
    records: Sequence[ContextRecord], *, document_visible: bool
) -> tuple[str, ...]:
    """Context text a search projection may index for one document.

    Only for a document the reader may already see (ADR 0005/0137 decide that,
    before this is called); and only attributed text — nearby or shared text
    belongs to the page, whose own visibility governs it, never to every file
    beside it.
    """
    if not document_visible:
        return ()
    return tuple(text for record in records if record.attributed for text in record.text)
