"""Reading the OneNote desktop archive.

A pure reader. It opens files, returns dataclasses, and touches neither the
database nor the archive it is reading — the archive is source material that
outlives this importer, and an importer that can write to its own source is one
bad run away from having nothing to re-run against (Stage-2D brief 54).

The archive's shape, which this module encodes once so nothing else has to::

    onenote-desktop-archive/
        pages/<pageKey>/
            page.json                 identity, section, level, order, dates
            current.json              which capture is current, and its XML hash
            captures/<captureId>/
                page.source.xml       source evidence, exactly as OneNote gave it
                page.txt              derived readable text
                blocks.json           derived ordered blocks
                links.json            derived hyperlinks
            resources/<resourceKey>/
                resource.json         filename, kind, size, SHA-256, block position
                original/<filename>   the original bytes, unconverted

The part worth understanding is `blocks`. OneNote is a free-form canvas, so the
archive records ordering honestly rather than asserting it: blocks carry a
`sourceOrdinal`, the page records which strategy produced the order, and
`readingOrderAmbiguous` flags a page where the raw XML order and the visual
order disagreed. Files sit *in* that sequence, which is what keeps
"Ettepaneku eestikeelne variant" attached to the PDF it introduces
(Stage-2D brief 22, 31, 32).

Every file is read through `safe_paths`, relative to the archive root: a page
key, a ``captureId`` or an ``originalPath`` is a string from the archive's own
JSON, and none of them may name a file outside the archive, or reach one
through a link planted inside it (docs/adr/0178).
"""

from __future__ import annotations

import hashlib
from collections.abc import Iterator
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path, PurePath
from typing import TYPE_CHECKING

from app.legacy_import import safe_paths
from app.legacy_import.limits import WorkBudget, current_limits, load_json_document
from app.legacy_import.safe_paths import Kind, UnsafeSourcePath

if TYPE_CHECKING:
    from app.legacy_import.integrity import SourceManifest

#: Only this archive may be imported. The earlier Graph export stored one
#: page's HTML under 342 other pages and verified itself as PASS throughout, so
#: the guard is a directory name check rather than trust (Stage-2D brief 4).
EXPECTED_ARCHIVE_MARKER = "archive.json"

FILE_BLOCK_TYPES = frozenset({"FILE_ATTACHMENT", "IMAGE"})


class ArchiveError(RuntimeError):
    """The archive is not what it claims to be."""


@dataclass(frozen=True)
class ArchiveResource:
    """One file attached to one page."""

    resource_key: str
    original_filename: str
    resource_kind: str
    source_block_ordinal: int
    sha256: str
    size_bytes: int
    relative_path: str
    is_inline: bool
    download_status: str
    #: When the file was added to the OneNote page: the `creationTime` of the
    #: outline element holding it, as the desktop archive recorded it
    #: (`oneNote.blockCreated`). ``None`` when the archive has none — a floating
    #: object has no outline element — and then no date is claimed at all.
    source_added_at: datetime | None = None

    @property
    def is_captured(self) -> bool:
        return self.download_status == "CAPTURED"


@dataclass(frozen=True)
class ArchiveBlock:
    """One paragraph, list item or file, in reading order."""

    ordinal: int
    kind: str
    text: str = ""
    resource_key: str = ""
    depth: int = 0

    @property
    def is_file(self) -> bool:
        return self.kind in FILE_BLOCK_TYPES


@dataclass(frozen=True)
class ArchivePage:
    page_key: str
    page_id: str
    notebook: str
    section: str
    section_group: str
    title: str
    level: int
    page_order: int
    created_at: datetime | None
    modified_at: datetime | None
    capture_id: str
    xml_sha256: str
    xml_path: Path
    derived_text: str
    blocks: tuple[ArchiveBlock, ...]
    links: tuple[dict, ...]
    reading_order_strategy: str
    reading_order_ambiguous: bool
    resources: tuple[ArchiveResource, ...] = field(default_factory=tuple)

    @property
    def file_count(self) -> int:
        return len(self.resources)

    @property
    def file_bytes(self) -> int:
        return sum(resource.size_bytes for resource in self.resources)


def _parse_moment(raw: str | None) -> datetime | None:
    if not raw:
        return None
    try:
        return datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except ValueError:
        # A date the archive could not read is recorded as absent rather than
        # guessed. Every other field on the page is still worth importing.
        return None


class OneNoteArchive:
    """The archive on disk, opened once and read many times."""

    def __init__(
        self,
        root: Path,
        *,
        manifest: SourceManifest | None = None,
        budget: WorkBudget | None = None,
    ) -> None:
        self.root = Path(root)
        #: When given, every file read is checked against it: the bytes parsed
        #: are the bytes an operator approved (docs/adr/0184).
        self.manifest = manifest
        self.limits = current_limits()
        self.budget = budget or WorkBudget(self.limits)
        if not self.root.is_dir() or self._kind(EXPECTED_ARCHIVE_MARKER) is not Kind.FILE:
            raise ArchiveError(
                f"{self.root} does not look like a OneNote desktop archive "
                f"({EXPECTED_ARCHIVE_MARKER} is missing)."
            )
        self.pages_root = self.root / "pages"
        if self._kind("pages") is not Kind.DIRECTORY:
            raise ArchiveError(f"{self.root} has no pages/ directory.")

    # -- every read goes through these, relative to the archive root ---------

    def _kind(self, relative: str) -> Kind:
        try:
            kind = safe_paths.lookup(self.root, relative)
        except UnsafeSourcePath as error:
            raise ArchiveError(str(error)) from error
        if kind is Kind.MISSING and self.manifest is not None:
            self.manifest.refuse_absence(relative)
        return kind

    def _bytes(self, relative: str, max_bytes: int) -> bytes:
        try:
            data = safe_paths.read_bytes(
                self.root, relative, max_bytes=max_bytes, budget=self.budget
            )
        except UnsafeSourcePath as error:
            raise ArchiveError(str(error)) from error
        if self.manifest is not None:
            self.manifest.verify(relative, data)
        return data

    def _json(self, relative: str) -> dict:
        if self._kind(relative) is not Kind.FILE:
            return {}
        payload = load_json_document(
            self._bytes(relative, self.limits.max_json_bytes), what=relative, limits=self.limits
        )
        if not isinstance(payload, dict):
            raise ArchiveError(f"{relative} is not a JSON object.")
        return payload

    def _text(self, relative: str) -> str:
        if self._kind(relative) is not Kind.FILE:
            return ""
        return self._bytes(relative, self.limits.max_text_bytes).decode("utf-8")

    def _directories(self, relative: str, maximum: int) -> list[str]:
        """The subdirectory names of `relative`, counted as listed and refused past `maximum`."""
        members = safe_paths.scan_directory(
            self.root, self.root / PurePath(relative), max_members=maximum
        )
        return sorted(name for name, _path, kind in members if kind is Kind.DIRECTORY)

    @staticmethod
    def _segment(value: object, what: str) -> str:
        try:
            return safe_paths.check_segment(str(value))
        except UnsafeSourcePath as error:
            raise ArchiveError(f"{what}: {error}") from error

    @staticmethod
    def _relative(value: str, what: str) -> str:
        try:
            return safe_paths.check_relative(value)
        except UnsafeSourcePath as error:
            raise ArchiveError(f"{what}: {error}") from error

    def page_keys(self) -> list[str]:
        """Every page directory, refused past ``LEGACY_IMPORT_MAX_PAGES`` while listing."""
        return self._directories("pages", self.limits.max_pages)

    def __len__(self) -> int:
        return len(self.page_keys())

    def pages(self) -> Iterator[ArchivePage]:
        for key in self.page_keys():
            yield self.page(key)

    def page(self, page_key: str) -> ArchivePage:
        key = self._segment(page_key, "page key")
        directory = f"pages/{key}"
        identity = self._json(f"{directory}/page.json")
        current = self._json(f"{directory}/current.json")

        capture_id = current.get("captureId", "")
        if not capture_id:
            raise ArchiveError(f"{page_key}: capture {capture_id!r} is missing.")
        self._segment(capture_id, f"{page_key}: captureId")
        capture = f"{directory}/captures/{capture_id}"
        if self._kind(capture) is not Kind.DIRECTORY:
            raise ArchiveError(f"{page_key}: capture {capture_id!r} is missing.")
        # The key every later read of this page is joined to: one plain segment.
        archive_page_key = self._segment(
            identity.get("archivePageKey", page_key), f"{page_key}: archivePageKey"
        )

        blocks_payload = self._json(f"{capture}/blocks.json")
        raw_blocks = blocks_payload.get("blocks", [])
        original = identity.get("originalSource") or {}
        source = identity.get("source") or {}

        resources = tuple(self._resources(directory))
        by_ordinal = {resource.source_block_ordinal: resource for resource in resources}

        return ArchivePage(
            page_key=archive_page_key,
            page_id=source.get("pageId", ""),
            notebook=original.get("notebookName", ""),
            section=original.get("sectionName", "")
            or (identity.get("section") or {}).get("displayName", ""),
            section_group=original.get("sectionGroupPath") or "",
            title=identity.get("title", "") or original.get("pageTitle", "") or "",
            level=int(identity.get("level") or 1),
            page_order=int(identity.get("observedPageOrder") or 0),
            created_at=_parse_moment(identity.get("createdDateTime")),
            modified_at=_parse_moment(identity.get("lastModifiedDateTime")),
            capture_id=capture_id,
            xml_sha256=current.get("xmlSha256", ""),
            xml_path=self.root / PurePath(capture) / "page.source.xml",
            derived_text=self._text(f"{capture}/page.txt"),
            blocks=tuple(_blocks(raw_blocks, by_ordinal)),
            links=tuple((self._json(f"{capture}/links.json") or {}).get("links", [])),
            reading_order_strategy=str(blocks_payload.get("readingOrder") or ""),
            reading_order_ambiguous=bool(blocks_payload.get("readingOrderAmbiguous")),
            resources=resources,
        )

    def _resources(self, page_directory: str) -> Iterator[ArchiveResource]:
        root = f"{page_directory}/resources"
        if self._kind(root) is not Kind.DIRECTORY:
            return
        for directory in self._directories(root, self.limits.max_page_resources):
            name = self._segment(directory, f"{page_directory}: resource directory")
            record = f"{root}/{name}/resource.json"
            if self._kind(record) is not Kind.FILE:
                continue
            payload = self._json(record)
            # Stored with the archive's own separators; normalised here so the
            # path works on the Linux server as well as on Windows, and checked
            # after that, so a backslashed ``..`` cannot survive the translation.
            relative = (payload.get("originalPath") or "").replace("\\", "/")
            if relative:
                self._relative(relative, f"{page_directory}: originalPath")
            yield ArchiveResource(
                resource_key=payload.get("archiveResourceKey", name),
                original_filename=payload.get("originalFilename", "") or name,
                resource_kind=payload.get("resourceKind", "OTHER"),
                source_block_ordinal=int(payload.get("sourceOrdinal") or 0),
                sha256=payload.get("sha256", ""),
                size_bytes=int(payload.get("sizeBytes") or 0),
                relative_path=relative,
                is_inline=(payload.get("inlineOrFile") == "inline"),
                download_status=payload.get("downloadStatus", ""),
                source_added_at=_parse_moment((payload.get("oneNote") or {}).get("blockCreated")),
            )

    def _resource_relative(self, page_key: str, relative_path: str) -> str:
        key = self._segment(page_key, "page key")
        return f"pages/{key}/{self._relative(relative_path, f'{page_key}: resource path')}"

    def resource_path(self, page_key: str, resource: ArchiveResource) -> Path:
        """Where a resource's original bytes are, as an absolute path.

        For display only: reading goes through `read_resource`, which refuses
        what this path might reach through a link.
        """
        return self.root / PurePath(self._resource_relative(page_key, resource.relative_path))

    def read_resource(self, page_key: str, resource: ArchiveResource) -> bytes:
        return self.read_resource_at(page_key, resource.relative_path)

    def read_resource_at(self, page_key: str, relative_path: str) -> bytes:
        """A resource's bytes by the page key and path a catalogue row stored."""
        return self._bytes(
            self._resource_relative(page_key, relative_path), self.limits.max_member_bytes
        )

    def page_record(self, page_key: str) -> dict:
        """A page's own ``page.json``, whole, read through the same no-follow path.

        For a caller that needs a field `ArchivePage` does not carry. The file
        is opened again here rather than trusted from an earlier `page` call, so
        the read is checked again and cannot be redirected by a later swap.
        """
        key = self._segment(page_key, "page key")
        relative = f"pages/{key}/page.json"
        if self._kind(relative) is not Kind.FILE:
            raise ArchiveError(f"{page_key}: page.json is missing.")
        return self._json(relative)

    def read_page_xml(self, page: ArchivePage) -> bytes:
        """The page's source XML, exactly as stored, and only if it is what `current.json` claims.

        The claim (`xmlSha256`) is what the import records as the page's
        identity; bytes that do not hash to it are not that page's evidence,
        whatever else they are (docs/adr/0184).
        """
        try:
            relative = PurePath(page.xml_path).relative_to(self.root).as_posix()
        except ValueError as error:
            raise ArchiveError(f"{page.page_key}: the page XML is outside the archive.") from error
        data = self._bytes(relative, self.limits.max_member_bytes)
        claimed = (page.xml_sha256 or "").strip().lower()
        if not claimed or hashlib.sha256(data).hexdigest() != claimed:
            raise ArchiveError(
                f"{page.page_key}: page.source.xml does not hash to the xmlSha256 its "
                "current.json claims."
            )
        return data


def _blocks(raw: list[dict], by_ordinal: dict[int, ArchiveResource]) -> Iterator[ArchiveBlock]:
    """Normalise the archive's blocks and tie files to their own ordinal.

    A file block in the archive carries the ordinal its resource records, so the
    two are joined here rather than by filename — several pages attach two files
    with the same name, and matching on that would swap them.
    """
    for entry in raw:
        kind = str(entry.get("type") or "TEXT")
        ordinal = int(entry.get("sourceOrdinal") or 0)
        resource = by_ordinal.get(ordinal) if kind in FILE_BLOCK_TYPES else None
        yield ArchiveBlock(
            ordinal=ordinal,
            kind=kind,
            text=str(entry.get("text") or ""),
            resource_key=resource.resource_key if resource else "",
            depth=int(entry.get("depth") or 0),
        )
