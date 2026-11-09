"""The captured versions of one OneNote page, as its source page reads them.

A page row (`LegacySourcePage`) is the first capture's record, and it is never
rewritten. A later sealed capture whose reading of the page's text, title or
place differs is kept beside it as a `OneNotePageRevision` (docs/adr/0213 §6).
Both are evidence; the source page shows the **latest** by default and keeps
every earlier one a click away (`?hoive=`), so a later capture is never stored
but invisible (live-sync round 2, agent B).

What changed between two readings is said only where the source proves it:
blocks are compared by their OneNote object ID, and only when every text block
of both readings carries one, no ID repeats, and the readings share at least one
(a moved page re-issues every ID, D2). Otherwise the page says the text changed
and that the change cannot be shown block by block — never a diff invented from
positions or similar wording.

Files follow the same windows. A file version a later capture brought
(`OneNoteResourceRevision`, `VERSIONED`) belongs to the reading that was the
latest when it was imported: the capture writes its page reading first and its
files after, in one transaction (`onenote_capture._write_page`), so a reading's
window runs from its own creation to the next reading's. The first capture's
version, its bytes and its import row are untouched; the reading only chooses
which version its file line opens.

Reads only. Nothing here writes, and nothing decides who may read: the caller
has already opened the page through `historical_views.visible_links`.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field, replace
from datetime import datetime
from typing import Any

from django.http import Http404

from app.legacy_import import historical_reading as reading

#: The GET parameter that names a reading, and its value for the first record.
QUERY_PARAM = "hoive"
ORIGINAL = "algne"

_FILE_KINDS = frozenset({"FILE", "FILE_ATTACHMENT", "FILE_NOT_CAPTURED", "IMAGE"})


@dataclass(frozen=True)
class PageReading:
    """One captured version of the page: the first record, or a later revision."""

    #: 1 for the page row, then 2, 3, … in the order the captures were imported.
    number: int
    #: The `?hoive=` value that names it: `ORIGINAL`, or the revision's id.
    token: str
    #: The `OneNotePageRevision`, or ``None`` for the page row.
    revision: Any
    title: str
    source_section: str
    source_section_group: str
    source_modified_at: datetime | None
    #: When Juristid stored this reading (the import of its sealed capture).
    #: Provenance: never an event date.
    imported_at: datetime | None
    capture_id: str
    source_xml_sha256: str
    blocks: list[dict[str, Any]]
    #: The page's own link records. Only the first capture's row carries them;
    #: their block ordinals are that capture's, so a revision has none.
    links: list[Any]
    text_changed: bool = False
    location_changed: bool = False
    #: Set by `page_readings`: the next reading's creation, the end of this
    #: reading's window for file changes; ``None`` for the latest.
    until: datetime | None = None
    #: The start of this reading's window (the revision's creation; ``None`` for
    #: the first record, whose window starts with the page).
    since: datetime | None = None
    latest: bool = False

    @property
    def is_original(self) -> bool:
        return self.revision is None

    @property
    def display_location(self) -> str:
        parts = [self.source_section_group, self.source_section]
        return " → ".join(part for part in parts if part)


def page_readings(page: Any) -> list[PageReading]:
    """The page row, then its revisions oldest first. One query (the revisions)."""
    from app.legacy_import.onenote_coverage_models import OneNotePageRevision

    out = [
        PageReading(
            number=1,
            token=ORIGINAL,
            revision=None,
            title=page.title or "",
            source_section=page.source_section or "",
            source_section_group=page.source_section_group or "",
            source_modified_at=page.source_modified_at,
            imported_at=page.first_imported_at,
            capture_id=page.capture_id or "",
            source_xml_sha256=page.source_xml_sha256 or "",
            blocks=[block for block in page.blocks or [] if isinstance(block, dict)],
            links=list(page.links or []),
        )
    ]
    revisions = OneNotePageRevision.objects.filter(page=page).order_by("created_at", "pk")
    for number, revision in enumerate(revisions, start=2):
        out.append(
            PageReading(
                number=number,
                token=str(revision.pk),
                revision=revision,
                title=revision.title or page.title or "",
                source_section=revision.source_section or "",
                source_section_group=revision.source_section_group or "",
                source_modified_at=revision.source_modified_at,
                imported_at=revision.created_at,
                capture_id=revision.capture_id or "",
                source_xml_sha256=revision.source_xml_sha256 or "",
                blocks=[block for block in revision.blocks or [] if isinstance(block, dict)],
                links=[],
                text_changed=revision.text_changed,
                location_changed=revision.location_changed,
                since=revision.created_at,
            )
        )
    return [
        replace(
            item,
            until=out[index + 1].since if index + 1 < len(out) else None,
            latest=index + 1 == len(out),
        )
        for index, item in enumerate(out)
    ]


def select_reading(readings: list[PageReading], token: str | None) -> PageReading:
    """The reading ``token`` names; the latest without one. Anything else is a 404.

    A value that is not `ORIGINAL` or a UUID of one of *this page's* revisions
    names nothing here — another page's revision id included.
    """
    if token is None:
        return readings[-1]
    token = token.strip()
    if token == ORIGINAL:
        return readings[0]
    try:
        wanted = str(uuid.UUID(token))
    except ValueError:
        raise Http404("Sellist lehe versiooni ei ole.") from None
    for item in readings:
        if item.token == wanted:
            return item
    raise Http404("Sellist lehe versiooni ei ole.")


# -- what changed, where the source proves it ---------------------------------------------


@dataclass(frozen=True)
class BlockChanges:
    """Text blocks added, changed and removed, matched by OneNote object ID."""

    #: Ordinals in the later reading.
    added: frozenset[int] = frozenset()
    changed: frozenset[int] = frozenset()
    #: The earlier reading's text of each block the later one no longer has.
    removed: tuple[str, ...] = ()

    @property
    def empty(self) -> bool:
        return not (self.added or self.changed or self.removed)


def _text_blocks(blocks: list[dict[str, Any]]) -> dict[str, dict[str, Any]] | None:
    """The text-bearing blocks by object ID, or ``None`` when identity is not proven."""
    out: dict[str, dict[str, Any]] = {}
    for block in blocks:
        kind = str(block.get("source_kind") or block.get("kind") or "")
        if kind in _FILE_KINDS or kind == "TITLE":
            continue
        if not reading.display_text(block).strip():
            continue
        object_id = str(block.get("object_id") or "").strip()
        if not object_id or object_id in out:
            return None
        out[object_id] = block
    return out


def block_changes(
    earlier: list[dict[str, Any]], later: list[dict[str, Any]]
) -> BlockChanges | None:
    """The text blocks ``later`` added, changed and removed against ``earlier``.

    ``None`` when the comparison would be a guess: a text block without an object
    ID or with a repeated one on either side, or no ID in common (a moved page,
    or an earlier capture shape that kept none).
    """
    before, after = _text_blocks(earlier), _text_blocks(later)
    if before is None or after is None:
        return None
    if before and after and not (before.keys() & after.keys()):
        return None
    if not before and not after:
        return BlockChanges()

    def ordinal(block: dict[str, Any]) -> int | None:
        value = block.get("ordinal")
        return value if isinstance(value, int) and not isinstance(value, bool) else None

    added = {ordinal(block) for key, block in after.items() if key not in before}
    changed = {
        ordinal(block)
        for key, block in after.items()
        if key in before
        and reading.display_text(block).strip() != reading.display_text(before[key]).strip()
    }
    removed = tuple(
        reading.display_text(block).strip() for key, block in before.items() if key not in after
    )
    return BlockChanges(
        added=frozenset(value for value in added if value is not None),
        changed=frozenset(value for value in changed if value is not None),
        removed=removed,
    )


# -- the files of one reading --------------------------------------------------------------


@dataclass(frozen=True)
class FileVersion:
    """The version a reading's file line opens, when a later capture brought it."""

    version: Any
    #: The block's modification time in OneNote, when it is plausible: not before
    #: the block's creation and not after the import. Otherwise ``None``: the
    #: page then claims no day for the change (never the import day).
    modified_at: datetime | None
    #: Brought within this reading's own window (a change of this version).
    in_window: bool


@dataclass
class ReadingFiles:
    """What one reading shows of the link's files, decided before the template."""

    #: Resource keys this reading may list: the files the page had by its end.
    known: set[str] = field(default_factory=set)
    #: Resource keys first catalogued within this reading's window (not the first).
    added: set[str] = field(default_factory=set)
    #: Resource key → the later version this reading opens.
    versions: dict[str, FileVersion] = field(default_factory=dict)
    #: Later readings of a file held or recorded for a person within the window.
    held: int = 0


def plausible_modification(revision: Any) -> datetime | None:
    """A file version's OneNote modification time, if it can be the change's day.

    The block's modification time is when the bytes were last replaced, at the
    latest (D2). It is shown only when it is not earlier than the block's
    creation in the same capture and not later than the import that stored the
    version: a time outside that span is a clock the page will not repeat.
    """
    modified = revision.block_modified_at
    if modified is None:
        return None
    created = revision.source_added_at
    if created is not None and modified < created:
        return None
    stored = getattr(revision, "created_at", None)
    if stored is not None and modified > stored:
        return None
    return modified


def reading_files(
    item: PageReading, resources: dict[str, Any], revisions: list[Any]
) -> ReadingFiles:
    """The files ``item`` shows, from the page's catalogue and the link's revisions.

    ``resources`` are the page's `LegacySourceResource` rows by key; ``revisions``
    this link's `OneNoteResourceRevision` rows, oldest first.
    """
    from app.legacy_import.onenote_coverage_models import RevisionState

    out = ReadingFiles()
    for key, resource in resources.items():
        created = resource.created_at
        if item.until is not None and created is not None and created >= item.until:
            continue  # a later capture's file: not on the page yet
        out.known.add(key)
        if item.since is not None and created is not None and created >= item.since:
            out.added.add(key)
    for revision in revisions:
        created = revision.created_at
        if item.until is not None and created >= item.until:
            continue
        in_window = item.since is None or created >= item.since
        key = revision.resource.resource_key
        if revision.state == RevisionState.VERSIONED and revision.document_version_id:
            out.versions[key] = FileVersion(
                version=revision.document_version,
                modified_at=plausible_modification(revision),
                in_window=in_window and not item.is_original,
            )
        elif in_window and not item.is_original and revision.state == RevisionState.HELD:
            out.held += 1
    return out
