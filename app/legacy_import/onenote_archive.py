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
"""

from __future__ import annotations

import json
from collections.abc import Iterator
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

#: Only this archive may be imported. The earlier Graph export stored one
#: page's HTML under 342 other pages and verified itself as PASS throughout, so
#: the guard is a directory name check rather than trust (Stage-2D brief 4).
EXPECTED_ARCHIVE_MARKER = "archive.json"

TEXT_BLOCK_TYPES = frozenset({"TEXT", "LIST_ITEM", "TABLE"})
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

    def __init__(self, root: Path) -> None:
        self.root = Path(root)
        if not (self.root / EXPECTED_ARCHIVE_MARKER).is_file():
            raise ArchiveError(
                f"{self.root} does not look like a OneNote desktop archive "
                f"({EXPECTED_ARCHIVE_MARKER} is missing)."
            )
        self.pages_root = self.root / "pages"
        if not self.pages_root.is_dir():
            raise ArchiveError(f"{self.root} has no pages/ directory.")

    def page_keys(self) -> list[str]:
        return sorted(p.name for p in self.pages_root.iterdir() if p.is_dir())

    def __len__(self) -> int:
        return len(self.page_keys())

    def pages(self) -> Iterator[ArchivePage]:
        for key in self.page_keys():
            yield self.page(key)

    def page(self, page_key: str) -> ArchivePage:
        directory = self.pages_root / page_key
        identity = _read_json(directory / "page.json")
        current = _read_json(directory / "current.json")

        capture_id = current.get("captureId", "")
        capture = directory / "captures" / capture_id
        if not capture.is_dir():
            raise ArchiveError(f"{page_key}: capture {capture_id!r} is missing.")

        blocks_payload = _read_json(capture / "blocks.json")
        raw_blocks = blocks_payload.get("blocks", [])
        original = identity.get("originalSource") or {}
        source = identity.get("source") or {}

        resources = tuple(self._resources(directory))
        by_ordinal = {resource.source_block_ordinal: resource for resource in resources}

        return ArchivePage(
            page_key=identity.get("archivePageKey", page_key),
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
            xml_path=capture / "page.source.xml",
            derived_text=_read_text(capture / "page.txt"),
            blocks=tuple(_blocks(raw_blocks, by_ordinal)),
            links=tuple((_read_json(capture / "links.json") or {}).get("links", [])),
            reading_order_strategy=str(blocks_payload.get("readingOrder") or ""),
            reading_order_ambiguous=bool(blocks_payload.get("readingOrderAmbiguous")),
            resources=resources,
        )

    def _resources(self, page_directory: Path) -> Iterator[ArchiveResource]:
        root = page_directory / "resources"
        if not root.is_dir():
            return
        for directory in sorted(root.iterdir()):
            record = directory / "resource.json"
            if not record.is_file():
                continue
            payload = _read_json(record)
            relative = payload.get("originalPath") or ""
            yield ArchiveResource(
                resource_key=payload.get("archiveResourceKey", directory.name),
                original_filename=payload.get("originalFilename", "") or directory.name,
                resource_kind=payload.get("resourceKind", "OTHER"),
                source_block_ordinal=int(payload.get("sourceOrdinal") or 0),
                sha256=payload.get("sha256", ""),
                size_bytes=int(payload.get("sizeBytes") or 0),
                # Stored with the archive's own separators; normalised here so
                # the path works on the Linux server as well as on Windows.
                relative_path=relative.replace("\\", "/"),
                is_inline=(payload.get("inlineOrFile") == "inline"),
                download_status=payload.get("downloadStatus", ""),
            )

    def resource_path(self, page_key: str, resource: ArchiveResource) -> Path:
        """Where a resource's original bytes are, as an absolute path."""
        return self.pages_root / page_key / Path(resource.relative_path)

    def read_resource(self, page_key: str, resource: ArchiveResource) -> bytes:
        return self.resource_path(page_key, resource).read_bytes()

    def read_page_xml(self, page: ArchivePage) -> bytes:
        return page.xml_path.read_bytes()


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


def _read_json(path: Path) -> dict:
    if not path.is_file():
        return {}
    return json.loads(path.read_text(encoding="utf-8"))


def _read_text(path: Path) -> str:
    if not path.is_file():
        return ""
    return path.read_text(encoding="utf-8")
