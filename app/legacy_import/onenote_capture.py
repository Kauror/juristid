"""Add a sealed OneNote desktop capture to the existing Lovable Teemad (docs/adr/0205).

The capture is the owner-approved desktop evidence run of 2026-11-04: every page
a Lovable topic links to exactly, read through the OneNote desktop client, each
file copied byte for byte with its block's creation time and its context. It is
sealed outside the repository (``SEAL.json`` → ``SHA256SUMS`` → every file) and
read here only through that seal.

What this module may write, and nothing else:

* ``LegacySourcePage`` — one per page identity, keyed ``p-<page GUID>``. A page
  that already has a row (the October evidence pilot) is reused **unchanged**.
* ``MatterSourcePage`` — the Lovable Teema's link to that page
  (``LOVABLE_EXACT_PAGE_GUID``), also for a page that holds no file.
* ``LegacySourceResource`` — one per file occurrence on the page, keyed by the
  file block's object ID, with ``source_added_at`` = that block's creation time.
* ``Document`` + ``DocumentVersion`` through ``create_document`` and
  ``add_evidence_version`` — the import path (docs/adr/0076 §3), with no actor and
  outside any composer operation, so the Teema chronology files them on their
  OneNote day (docs/adr/0092 §6). Each writes its ordinary change event.
* ``LegacySourceResourceImport`` — exactly one per (Teema link, occurrence):
  IMPORTED with its document, or SKIPPED with the reason it is held.
* ``ImportBatch`` — one per apply run that writes anything.

It never touches a Matter, a Lovable reference or sync state, an existing source
row, or the guarded Excel-era write paths (docs/adr/0203). It never creates a
Matter. A page shared by several Teemad is catalogued once and linked to each,
but its files are held for review: a shared page does not prove that every file
on it belongs to every Teema (owner decision D4).
"""

from __future__ import annotations

import hashlib
import json
import re
import urllib.parse
from collections import Counter
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from pathlib import Path, PurePosixPath
from typing import Any

from django.conf import settings
from django.core.files.base import ContentFile
from django.db import transaction
from django.db.models import F
from django.db.models.functions import Lower
from django.utils import timezone

from app.legacy_import.integrity import IntegrityError, canonical_sha256, require_digest

#: The sealed package format this module reads.
SCHEMA = "juristid.onenote-desktop-capture/2"
IMPORTER_VERSION = "onenote-capture/1"
CONTRACT_VERSION = "adr-0205/1"
OPERATOR_INTENT = "onenote-capture-import"
BATCH_SOURCE_SYSTEM = "ONENOTE_DESKTOP+CAPTURE"
REGISTER_COHORT = "lovable-register"
SOURCE_ADDED_BASIS = "ONENOTE_BLOCK_CREATED"

#: Why an occurrence is catalogued but not made into a Document for a Teema.
HOLD_SHARED_PAGE = "SHARED_PAGE_AWAITING_REVIEW"
HOLD_EMPTY_FILE = "EMPTY_FILE"
HOLD_TOO_LARGE = "LARGER_THAN_EVIDENCE_LIMIT"
HOLD_EARLIER_CAPTURE = "EARLIER_CAPTURE_NOT_IMPORTED"

#: A later capture of a page an earlier capture already wrote (amendment 2026-11-05).
#:
#: *Recorded* — the new block identity is catalogued and its (Teema, occurrence) row
#: says why no Document was made:
#:
#: * a proven pure move: same page GUID, an earlier row on this Teema's link with the
#:   same block creation time and filename (unique both ways on the page) and the
#:   same SHA-256 and size. The earlier row's decision stands; nothing is copied.
SKIP_CONTINUES = "CONTINUES_EARLIER_BLOCK"
#: * the same (creation time, filename) but other bytes: a move and an edit at once
#:   proves nothing, so a person decides.
HOLD_MOVED_AND_CHANGED = "MOVED_BLOCK_BYTES_CHANGED"
#: * the (creation time, filename) names more than one earlier or later block.
HOLD_AMBIGUOUS = "AMBIGUOUS_EARLIER_BLOCK"
#: * same bytes and filename as an earlier file of this page, without a creation time
#:   to prove it (a floating file) or at another time: held, never matched.
HOLD_RESEMBLES = "RESEMBLES_EARLIER_FILE_UNPROVEN"
#: * the same bytes, filename and creation time as an earlier capture's file on
#:   another page of this Teema (a copied page gets a new GUID and keeps its times).
HOLD_SAME_FILE_ELSEWHERE = "SAME_FILE_ON_ANOTHER_PAGE_OF_TEEMA"
#:
#: *Revisions* (live-sync round 2026-11, implementing amendment proposals P1/P2):
#: the occurrence keeps an earlier capture's identity (same block object ID) but
#: this capture reads it differently. The earlier resource row, its import row
#: and the immutable ``DocumentVersion`` are kept exactly; this capture's reading
#: is recorded beside them as a ``OneNoteResourceRevision``
#: (``onenote_coverage_models``), one per (Teema link, occurrence, capture):
#:
#: * other bytes, where the earlier reading became a Document: the next
#:   ``DocumentVersion`` of that Document (disposition ``VERSION``);
VERSION_CHANGED_BYTES = "CHANGED_BYTES_NEW_VERSION"
#: * other bytes that cannot become a version (the earlier file was held, the
#:   Document was removed since, the new bytes are empty or too large, or the page
#:   is shared): held with the reason (disposition ``REVISE``);
HOLD_BYTES_CHANGED = "BYTES_CHANGED_UNDER_SAME_BLOCK"
HOLD_DOCUMENT_REMOVED = "DOCUMENT_REMOVED_SINCE_IMPORT"
#: * the same bytes under another creation time: held for a person (``REVISE``);
HOLD_TIME_CHANGED = "BLOCK_TIME_CHANGED_UNDER_SAME_BLOCK"
#: * the same bytes with another structural comment: recorded beside the earlier
#:   one; the Document's provenance note is kept (``REVISE``).
HOLD_COMMENT_CHANGED = "COMMENT_CHANGED_SINCE_IMPORT"
#: Retired with P1/P2: no later-capture change is left only in the plan.
PLAN_ONLY_HOLDS: frozenset[str] = frozenset()

#: Item dispositions. ``VERSION`` and ``REVISE`` are written as revisions.
PROMOTE, HOLD, VERSION, REVISE = "PROMOTE", "HOLD", "VERSION", "REVISE"
REVISING = frozenset({VERSION, REVISE})

#: How a later capture reads each (Teema, occurrence) against what Juristid holds
#: (owner brief §8 D3). Counted in the plan; the decisions are the items.
CLASS_SAME = "SAME"
CLASS_SAME_BLOCK_NEW_ID = "SAME_BLOCK_NEW_ID"
CLASS_NEW_RESOURCE = "NEW_RESOURCE"
CLASS_NEW_PAGE = "NEW_PAGE"
CLASS_CHANGED_BYTES = "CHANGED_BYTES"
CLASS_CHANGED_TIME = "CHANGED_TIME"
CLASS_CHANGED_COMMENT = "CHANGED_COMMENT"
CLASS_HELD_FOR_REVIEW = "HELD_FOR_REVIEW"

#: The only context bases that are the file's own comment (owner decision D9).
STRUCTURAL_BASES = frozenset({"ATTACHED_NOTE", "CONTAINER_HEADING"})

#: A block modified this long after its creation may hold newer bytes (the
#: Phase 1 contract, docs/onenote-ingress-phase1.md §9.2).
CONTENT_DRIFT = timedelta(hours=1)

_GUID = re.compile(r"^[0-9A-Fa-f]{8}-[0-9A-Fa-f]{4}-[0-9A-Fa-f]{4}-[0-9A-Fa-f]{4}-[0-9A-Fa-f]{12}$")
_SHA = re.compile(r"^[0-9a-f]{64}$")
_REFERENCE = re.compile(r"\b(?:19|20)\d{2}[ _-]\d{1,4}\b")
_MAX_FILE_BYTES = 200 * 1024 * 1024
_PAGE_FILES = ("pre.xml", "post.xml", "receipt.json")

#: The capture's block kinds, as the source-page view reads them
#: (``historical_views._rendered_blocks``): a file block names its resource.
_RENDERED_KIND = {"FILE": "FILE_ATTACHMENT", "IMAGE": "IMAGE_NOT_CAPTURED"}


class CaptureError(Exception):
    """The capture, the plan or the database is not in a state this may write from."""


# -- reading the sealed capture ---------------------------------------------------


@dataclass(frozen=True)
class Occurrence:
    occurrence_key: str
    page_guid: str
    block_object_id: str
    block_ordinal: int | None
    ordinal: int
    floating: bool
    filename: str
    size: int
    sha256: str
    copied_at: datetime | None
    block_created: datetime | None
    block_modified: datetime | None
    object_modified: datetime | None
    context: tuple[dict[str, Any], ...]

    @property
    def structural_comment(self) -> list[tuple[str, str]]:
        """(basis, verbatim text) of ``ATTACHED_NOTE`` / ``CONTAINER_HEADING`` only.

        The basis decides, not the record's own ``attributed`` flag: a record of
        any other basis never becomes the file's comment, however it is flagged.
        """
        return [
            (record["basis"], "\n".join(record["text"]))
            for record in self.context
            if record.get("basis") in STRUCTURAL_BASES and record.get("text")
        ]

    @property
    def content_may_be_newer(self) -> bool:
        return bool(
            self.block_created
            and self.block_modified
            and self.block_modified - self.block_created > CONTENT_DRIFT
        )


@dataclass(frozen=True)
class PageMatter:
    matter_id: str
    lovable_source_id: str
    reference: str


@dataclass(frozen=True)
class CapturePage:
    tag: str
    page_guid: str
    page_object_id: str
    title: str
    notebook: str
    section: str
    section_groups: tuple[str, ...]
    parent_object_id: str
    level: int
    archived: bool
    page_created: datetime | None
    page_modified: datetime | None
    onenote_hyperlink: str
    blocks: tuple[dict[str, Any], ...]
    matters: tuple[PageMatter, ...]
    occurrences: tuple[Occurrence, ...]

    @property
    def page_key(self) -> str:
        return f"p-{self.page_guid.lower()}"

    @property
    def shared(self) -> bool:
        return len(self.matters) > 1


@dataclass
class Capture:
    root: Path
    capture_id: str
    package: str
    manifest_sha256: str
    sums_sha256: str
    counts: dict[str, int]
    pages: list[CapturePage]
    listed: dict[str, str] = field(default_factory=dict)

    @property
    def occurrences(self) -> list[Occurrence]:
        return [occurrence for page in self.pages for occurrence in page.occurrences]

    def blob_path(self, sha256: str) -> Path:
        if not _SHA.match(sha256):
            raise CaptureError(f"not a SHA-256: {sha256!r}")
        return self.root / "blobs" / sha256

    def read_blob(self, occurrence: Occurrence) -> bytes:
        path = self.blob_path(occurrence.sha256)
        if path.is_symlink() or not path.is_file():
            raise CaptureError(f"blob for {occurrence.occurrence_key} is missing or not a file")
        content = path.read_bytes()
        if len(content) != occurrence.size:
            raise CaptureError(f"blob for {occurrence.occurrence_key} has the wrong size")
        if hashlib.sha256(content).hexdigest() != occurrence.sha256:
            raise CaptureError(f"blob for {occurrence.occurrence_key} does not hash to its name")
        return content

    def read_page_file(self, page: CapturePage, name: str) -> bytes:
        """A page file, refused unless it hashes to what ``SHA256SUMS`` pins."""
        if name not in _PAGE_FILES:
            raise CaptureError(f"not a page file: {name!r}")
        relative = f"pages/{page.tag}/{name}"
        path = self.root / relative
        if path.is_symlink() or not path.is_file():
            raise CaptureError(f"page file {relative} is missing")
        content = path.read_bytes()
        if hashlib.sha256(content).hexdigest() != self.listed.get(relative):
            raise CaptureError(f"page file {relative} does not match SHA256SUMS")
        return content


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _time(value: Any) -> datetime | None:
    if not value:
        return None
    moment = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    if moment.tzinfo is None:
        raise CaptureError(f"a naive source time has no basis: {value!r}")
    return moment


def _safe_relative(name: str) -> PurePosixPath:
    path = PurePosixPath(name)
    if path.is_absolute() or ".." in path.parts or not path.parts or "\\" in name:
        raise CaptureError(f"unsafe path in SHA256SUMS: {name!r}")
    return path


def load_capture(root: Path, *, verify_all_bytes: bool = False) -> Capture:
    """Open a sealed capture, refusing anything the seal does not vouch for.

    The seal pins ``SHA256SUMS``, which pins every file. ``verify_all_bytes``
    re-hashes every listed file (``validate``); otherwise each page file and
    each blob is re-hashed when it is read.
    """
    root = Path(root)
    try:
        seal = json.loads((root / "SEAL.json").read_text(encoding="utf-8"))
    except (OSError, ValueError) as error:
        raise CaptureError(f"no readable SEAL.json in {root}") from error
    sums_path, manifest_path = root / "SHA256SUMS", root / "manifest.json"
    if _sha256_file(sums_path) != seal.get("sha256sums_sha256"):
        raise CaptureError("SHA256SUMS does not match the seal")
    manifest_sha = _sha256_file(manifest_path)
    if manifest_sha != seal.get("manifest_sha256"):
        raise CaptureError("manifest.json does not match the seal")
    listed: dict[str, str] = {}
    for line in sums_path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        digest, _, name = line.partition("  ")
        if not _SHA.match(digest):
            raise CaptureError(f"malformed SHA256SUMS line: {line[:80]!r}")
        listed[str(_safe_relative(name))] = digest
    if listed.get("manifest.json") != manifest_sha:
        raise CaptureError("SHA256SUMS does not list the sealed manifest")
    on_disk = set()
    for path in root.rglob("*"):
        relative = path.relative_to(root).as_posix()
        if path.is_symlink():
            raise CaptureError(f"a symbolic link in the capture: {relative}")
        if path.is_file() and relative not in {"SHA256SUMS", "SEAL.json"}:
            on_disk.add(relative)
    if on_disk != set(listed):
        missing, extra = sorted(set(listed) - on_disk), sorted(on_disk - set(listed))
        raise CaptureError(
            f"capture tree differs from SHA256SUMS: missing {missing[:3]}, extra {extra[:3]}"
        )
    if verify_all_bytes:
        for name, digest in listed.items():
            if _sha256_file(root / name) != digest:
                raise CaptureError(f"{name} does not match SHA256SUMS")
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    if manifest.get("schema") != SCHEMA:
        raise CaptureError(f"unsupported capture schema {manifest.get('schema')!r}")
    pages = [_page(raw, listed) for raw in manifest["pages"]]
    occurrences_by_page: dict[str, list[Occurrence]] = {}
    for raw in manifest["occurrences"]:
        occurrence = _occurrence(raw)
        if f"blobs/{occurrence.sha256}" not in listed:
            raise CaptureError(f"{occurrence.occurrence_key}: its blob is not sealed")
        occurrences_by_page.setdefault(raw["page_tag"], []).append(occurrence)
    pages = [_with_occurrences(page, occurrences_by_page.pop(page.tag, [])) for page in pages]
    if occurrences_by_page:
        raise CaptureError(f"occurrences name unknown pages: {sorted(occurrences_by_page)[:3]}")
    capture = Capture(
        root=root,
        capture_id=str(manifest["capture_id"]),
        package=str(manifest.get("package", "")),
        manifest_sha256=manifest_sha,
        sums_sha256=seal["sha256sums_sha256"],
        counts=dict(manifest["counts"]),
        pages=pages,
        listed=listed,
    )
    _check_counts(capture)
    return capture


def _page(raw: dict[str, Any], listed: dict[str, str]) -> CapturePage:
    guid = str(raw["page_guid"])
    if not _GUID.match(guid):
        raise CaptureError(f"page {raw.get('tag')!r}: not a GUID {guid!r}")
    for name in _PAGE_FILES:
        if f"pages/{raw['tag']}/{name}" not in listed:
            raise CaptureError(f"page {raw['tag']}: {name} is not sealed")
    matters = tuple(
        PageMatter(str(m["matter_id"]), str(m["lovable_source_id"]), str(m["reference"]))
        for m in raw["matters"]
    )
    # A page no register link names (a section inventory, owner brief §10) has an
    # empty list: it is inventoried and never written, so it never becomes a Teema.
    if len({m.matter_id for m in matters}) != len(matters):
        raise CaptureError(f"page {raw['tag']}: Teema list repeated")
    return CapturePage(
        tag=str(raw["tag"]),
        page_guid=guid.upper(),
        page_object_id=str(raw["page_object_id"]),
        title=str(raw.get("title") or ""),
        notebook=str(raw.get("notebook") or ""),
        section=str(raw.get("section") or ""),
        section_groups=tuple(raw.get("section_groups") or ()),
        parent_object_id=str(raw.get("parent_object_id") or ""),
        level=int(raw.get("level") or 1),
        archived=bool(raw.get("archived")),
        page_created=_time(raw.get("page_created")),
        page_modified=_time(raw.get("page_modified")),
        onenote_hyperlink=str(raw.get("onenote_hyperlink") or ""),
        blocks=tuple(raw.get("blocks") or ()),
        matters=matters,
        occurrences=(),
    )


def _occurrence(raw: dict[str, Any]) -> Occurrence:
    sha = str(raw["sha256"]).lower()
    size = int(raw["size"])
    if not _SHA.match(sha) or size < 0 or size > _MAX_FILE_BYTES:
        raise CaptureError(f"{raw.get('occurrence_key')}: bad hash or size")
    block_id = str(raw["block_object_id"])
    if not block_id or len(block_id) > 100:
        raise CaptureError(f"{raw.get('occurrence_key')}: block object id unusable as a key")
    floating = bool(raw.get("floating"))
    created = _time(raw.get("block_created"))
    if floating and created is not None:
        raise CaptureError(
            f"{raw.get('occurrence_key')}: a floating file cannot carry a block time"
        )
    return Occurrence(
        occurrence_key=str(raw["occurrence_key"]),
        page_guid=str(raw["page_guid"]).upper(),
        block_object_id=block_id,
        block_ordinal=raw.get("block_ordinal"),
        ordinal=int(raw["ordinal"]),
        floating=floating,
        filename=str(raw.get("filename") or ""),
        size=size,
        sha256=sha,
        copied_at=_time(raw.get("copied_at")),
        block_created=created,
        block_modified=_time(raw.get("block_modified")),
        object_modified=_time(raw.get("object_modified")),
        context=tuple(raw.get("context") or ()),
    )


def _with_occurrences(page: CapturePage, occurrences: list[Occurrence]) -> CapturePage:
    keys = [o.block_object_id for o in occurrences]
    if len(set(keys)) != len(keys):
        raise CaptureError(f"page {page.tag}: one block object id carries two files")
    if any(o.page_guid != page.page_guid for o in occurrences):
        raise CaptureError(f"page {page.tag}: an occurrence names another page")
    return CapturePage(
        **{**page.__dict__, "occurrences": tuple(sorted(occurrences, key=lambda o: o.ordinal))}
    )


def _check_counts(capture: Capture) -> None:
    occurrences = capture.occurrences
    actual = {
        "pages": len(capture.pages),
        "occurrences": len(occurrences),
        "distinct_blobs": len({o.sha256 for o in occurrences}),
        "dated": sum(1 for o in occurrences if o.block_created),
        "floating": sum(1 for o in occurrences if o.floating),
        "structural_comment_files": sum(1 for o in occurrences if o.structural_comment),
        "topics": sum(len(p.matters) for p in capture.pages),
        "shared_pages": sum(1 for p in capture.pages if p.shared),
    }
    wrong = {k: (capture.counts.get(k), v) for k, v in actual.items() if capture.counts.get(k) != v}
    if wrong:
        raise CaptureError(f"manifest counts do not reconcile with its own rows: {wrong}")
    guids = [p.page_guid for p in capture.pages]
    if len(set(guids)) != len(guids):
        raise CaptureError("two captured pages share one page GUID")


def rendered_blocks(page: CapturePage) -> list[dict[str, Any]]:
    """The page's blocks in the shape the source-page view renders.

    A file block becomes ``FILE_ATTACHMENT`` naming its resource (the block's
    object ID, which is the resource key). An inline image was not captured as
    a file, so it names nothing. Every captured field is kept beside.
    """
    files = {o.block_object_id for o in page.occurrences}
    out = []
    placed = set()
    for block in page.blocks:
        kind = str(block.get("kind") or "TEXT")
        row = dict(block)
        row["source_kind"] = kind
        object_id = str(block.get("object_id") or "")
        if kind == "FILE" and object_id not in files:
            row["kind"], row["resource_key"] = "FILE_NOT_CAPTURED", ""  # never a nameless file
        else:
            row["kind"] = _RENDERED_KIND.get(kind, kind)
            row["resource_key"] = object_id if kind == "FILE" else ""
        if row["resource_key"]:
            placed.add(object_id)
        out.append(row)
    # A captured file with no block of its own is still on its page, after the rest.
    last = max((int(b.get("ordinal") or 0) for b in page.blocks), default=0)
    for offset, o in enumerate(x for x in page.occurrences if x.block_object_id not in placed):
        out.append(
            {
                "ordinal": last + 1 + offset,
                "kind": "FILE_ATTACHMENT",
                "text": "",
                "depth": 0,
                "resource_key": o.block_object_id,
                "source_kind": "FILE",
                "object_id": o.block_object_id,
            }
        )
    return out


# -- planning (reads only) -----------------------------------------------------------


@dataclass(frozen=True)
class PlannedItem:
    """One (Teema, occurrence) decision. The unit every count reconciles to."""

    matter_id: str
    page_key: str
    resource_key: str
    sha256: str
    disposition: str  # PROMOTE | HOLD | VERSION | REVISE
    reason: str = ""


@dataclass
class Plan:
    capture: Capture
    items: list[PlannedItem] = field(default_factory=list)
    problems: list[str] = field(default_factory=list)
    known_pages: list[str] = field(default_factory=list)  # page keys that already have a row
    earlier_resources: dict[str, int] = field(default_factory=dict)  # page key -> pilot rows
    matter_refs: dict[str, str] = field(default_factory=dict)
    recorded: set[tuple[str, str, str]] = field(default_factory=set)
    linked: set[tuple[str, str]] = field(default_factory=set)
    progress: Counter[str] = field(default_factory=Counter)
    #: (page key, this capture's block ID) -> the earlier block ID it continues or
    #: resembles (amendment 2026-11-05). Part of the decisions.
    earlier_blocks: dict[tuple[str, str], str] = field(default_factory=dict)
    #: Counts only, never decisions: what a later capture sees that it does not write.
    observations: Counter[str] = field(default_factory=Counter)
    #: (Teema, page key, block ID) that this capture already recorded a revision for.
    revised: set[tuple[str, str, str]] = field(default_factory=set)
    #: Page key -> (text changed, title/place changed) against the latest earlier
    #: reading of a page an earlier *capture* wrote. Part of the decisions.
    page_changes: dict[str, tuple[bool, bool]] = field(default_factory=dict)
    #: Page keys whose revision this capture already recorded (progress).
    page_revised: set[str] = field(default_factory=set)
    #: Per (Teema, occurrence) and per page: how this capture reads against Juristid.
    classes: Counter[str] = field(default_factory=Counter)
    #: (page key, block ID) -> the earlier Document a VERSION item adds to.
    version_of: dict[tuple[str, str, str], Any] = field(default_factory=dict)

    @property
    def digest(self) -> str:
        """The decisions only — independent of how far an apply has got."""
        decisions: dict[str, Any] = {
            "manifest_sha256": self.capture.manifest_sha256,
            "sha256sums_sha256": self.capture.sums_sha256,
            "importer": IMPORTER_VERSION,
            "items": [item.__dict__ for item in self.items],
            "links": sorted(
                (pm.matter_id, page.page_key) for page in self.capture.pages for pm in page.matters
            ),
            "known_pages": sorted(self.known_pages),
            "problems": self.problems,
        }
        # Only a later capture has these; a first capture's digest is what it always was.
        if self.earlier_blocks:
            decisions["earlier_blocks"] = sorted(
                [*key, earlier] for key, earlier in self.earlier_blocks.items()
            )
        if self.page_changes:
            decisions["page_changes"] = sorted(
                [key, text, place] for key, (text, place) in self.page_changes.items()
            )
        return canonical_sha256(decisions)

    def item_done(self, item: PlannedItem) -> bool:
        key = (item.matter_id, item.page_key, item.resource_key)
        return key in (self.revised if item.disposition in REVISING else self.recorded)

    def page_items(self, page: CapturePage) -> list[PlannedItem]:
        index = self.__dict__.get("_by_page")
        if index is None or index[0] != len(self.items):
            grouped: dict[str, list[PlannedItem]] = {}
            for item in self.items:
                grouped.setdefault(item.page_key, []).append(item)
            index = self.__dict__["_by_page"] = (len(self.items), grouped)
        return index[1].get(page.page_key, [])

    def page_done(self, page: CapturePage) -> bool:
        """Every Teema linked, every (Teema, occurrence) recorded, a changed page revised."""
        for pm in page.matters:
            if (pm.matter_id, page.page_key) not in self.linked:
                return False
        if page.page_key in self.page_changes and page.page_key not in self.page_revised:
            return False
        return all(self.item_done(item) for item in self.page_items(page))

    def summary(self) -> dict[str, Any]:
        promote = [i for i in self.items if i.disposition == PROMOTE]
        hold = [i for i in self.items if i.disposition == HOLD]
        revise = [i for i in self.items if i.disposition in REVISING]
        occurrences = self.capture.occurrences
        index = {
            (page.page_key, o.block_object_id): o
            for page in self.capture.pages
            for o in page.occurrences
        }

        def occ(item: PlannedItem) -> Occurrence:
            return index[(item.page_key, item.resource_key)]

        return {
            "capture_id": self.capture.capture_id,
            "manifest_sha256": self.capture.manifest_sha256,
            "plan_sha256": self.digest,
            "problems": len(self.problems),
            "pages": len(self.capture.pages),
            "pages_single_teema": sum(1 for p in self.capture.pages if not p.shared),
            "pages_shared": sum(1 for p in self.capture.pages if p.shared),
            "pages_without_files": sum(1 for p in self.capture.pages if not p.occurrences),
            "pages_already_known": len(self.known_pages),
            "pages_done": sum(1 for p in self.capture.pages if self.page_done(p)),
            "earlier_capture_resources_on_known_pages": sum(self.earlier_resources.values()),
            "occurrences": len(occurrences),
            "distinct_sha256": len({o.sha256 for o in occurrences}),
            "teema_page_links": sum(len(p.matters) for p in self.capture.pages),
            "teemad": len({pm.matter_id for p in self.capture.pages for pm in p.matters}),
            "items": len(self.items),
            "promote": len(promote),
            "promote_bytes": sum(occ(i).size for i in promote),
            "promote_dated": sum(1 for i in promote if occ(i).block_created),
            "promote_undated": sum(1 for i in promote if not occ(i).block_created),
            "promote_with_structural_comment": sum(1 for i in promote if occ(i).structural_comment),
            "hold": len(hold),
            "hold_by_reason": dict(Counter(i.reason for i in hold)),
            "progress": dict(self.progress),
            # A later capture (amendment 2026-11-05): what is left to write, what is
            # held without a row to hold it in, and what is seen but never written.
            "pages_pending": sum(1 for p in self.capture.pages if not self.page_done(p)),
            "items_already_recorded": sum(1 for i in self.items if self.item_done(i)),
            "plan_only_holds": dict(Counter(i.reason for i in hold if i.reason in PLAN_ONLY_HOLDS)),
            # Revisions (P1/P2): new versions of existing Documents, and recorded holds.
            "new_versions": sum(1 for i in revise if i.disposition == VERSION),
            "new_version_bytes": sum(occ(i).size for i in revise if i.disposition == VERSION),
            "revisions_by_reason": dict(Counter(i.reason for i in revise)),
            "pages_changed": len(self.page_changes),
            "pages_text_changed": sum(1 for t, _p in self.page_changes.values() if t),
            "pages_moved_or_renamed": sum(1 for _t, p in self.page_changes.values() if p),
            "pages_unlinked": sum(1 for p in self.capture.pages if not p.matters),
            "changes": dict(self.classes),
            "earlier_files_not_in_this_capture": self.observations["earlier_gone"],
            "pages_text_changed_since_stored_row": self.observations["page_xml_changed"],
            "observed_link_differs": self.observations["observed_link_differs"],
        }


_GUID_TEXT = r"[0-9A-Fa-f]{8}-[0-9A-Fa-f]{4}-[0-9A-Fa-f]{4}-[0-9A-Fa-f]{4}-[0-9A-Fa-f]{12}"
#: The page form of ``wd=target(Section.one|SECTION/Title|PAGE/)``. The page is the
#: last ``|GUID/`` before the target closes (or the value ends), because a title
#: may itself hold ``|`` and ``/`` and some stored links carry text after the
#: closing parenthesis; a section-only target (``Section.one|SECTION/``) has no
#: page and does not match.
_TARGET_PAGE = re.compile(rf"^.+?\.one\|[^|/]+/.*\|(?P<page>{_GUID_TEXT})/?(?:\).*)?$", re.DOTALL)
_CLIENT_PAGE = re.compile(rf"(?:^|&)page-id=\{{?(?P<page>{_GUID_TEXT})\}}?", re.IGNORECASE)


def register_page_guids(link: str) -> frozenset[str]:
    """The OneNote page GUIDs a register link names, upper-cased; empty when it names none.

    Read only where a link names a *page*: the last GUID of ``wd=target(…)`` on
    a ``Doc.aspx`` or ``OneNote.aspx`` web link, and ``page-id`` in the
    fragment of an ``onenote:`` client link. ``sourcedoc`` (the notebook, the
    same on every link), ``OneNote.aspx?id=`` (a notebook path) and
    ``wdsectionfileid`` (a section file) are never read: taking one of them for
    a page is what made the retired register-link reconciliation collapse every
    Teema onto one identity (owner decision D5, docs/adr/0204). A link whose
    web and client halves disagree names two pages, and the caller refuses it.
    """
    found: set[str] = set()
    for piece in (link or "").split():
        if piece.lower().startswith("onenote:"):
            fragment = urllib.parse.urlsplit(piece[len("onenote:") :]).fragment
            found.update(m.group("page").upper() for m in _CLIENT_PAGE.finditer(fragment))
            continue
        try:
            query = urllib.parse.urlsplit(piece).query
        except ValueError:
            continue
        for key, values in urllib.parse.parse_qs(query, keep_blank_values=True).items():
            if key.lower() != "wd":
                continue
            for value in values:
                for _pass in range(3):  # a target may arrive encoded once or twice more
                    if value.startswith("target("):
                        break
                    value = urllib.parse.unquote(value)
                if value.startswith("target("):
                    match = _TARGET_PAGE.match(value[len("target(") :])
                    if match:
                        found.add(match.group("page").upper())
    return frozenset(found)


def _stored_onenote_link(matter_id: str, state: Any) -> str:
    """The Teema's own OneNote link as the register stored it (applied, else the reference)."""
    from app.legacy_import.models import MatterSourceReference

    applied = (state.applied or {}).get("onenote_link") if state is not None else None
    if isinstance(applied, dict):
        # The register's own shape (``lovable_sync``): the Lovable value it last
        # applied (``source``) and what Juristid wrote (``target``). Both are read,
        # so the two must name the same page; ``str()`` of the dict happened to work
        # for a web link and dropped a client half (amendment 2026-11-05, case 8).
        halves = [str(applied.get(k) or "").strip() for k in ("source", "target")]
        if any(halves):
            return " ".join(h for h in halves if h)
    elif applied:
        return str(applied)
    reference = (
        MatterSourceReference.objects.filter(matter_id=matter_id, source_system="LOVABLE")
        .exclude(onenote_url="")
        .order_by("-created_at", "-id")
        .first()
    )
    return reference.onenote_url if reference else ""


def build_plan(capture: Capture) -> Plan:
    """Decide every (Teema, occurrence), and read what the database already holds."""
    from app.legacy_import.lovable_sync_models import LovableSyncState
    from app.legacy_import.onenote_coverage_models import OneNoteResourceRevision
    from app.legacy_import.source_pages import (
        LegacySourcePage,
        LegacySourceResource,
        LegacySourceResourceImport,
        MatterSourcePage,
        SourceSystem,
    )
    from app.matters.models import Matter

    plan = Plan(capture=capture)
    limit = int(getattr(settings, "MAX_EVIDENCE_UPLOAD_BYTES", 100 * 1024 * 1024))

    # Every (page, Teema) pair is checked — never one entry per topic. A topic names
    # exactly one Teema and a Teema exactly one topic, across the whole capture.
    pairs = [(page, pm) for page in capture.pages for pm in page.matters]
    by_topic: dict[str, set[str]] = {}
    by_matter: dict[str, set[str]] = {}
    for _page, pm in pairs:
        by_topic.setdefault(pm.lovable_source_id, set()).add(pm.matter_id)
        by_matter.setdefault(pm.matter_id, set()).add(pm.lovable_source_id)
    for topic, matters in sorted(by_topic.items()):
        if len(matters) > 1:
            plan.problems.append(f"Lovable topic {topic} names {len(matters)} Teemad")
    for matter_id, topics in sorted(by_matter.items()):
        if len(topics) > 1:
            plan.problems.append(f"Teema {matter_id} is named by {len(topics)} Lovable topics")
    states = {
        str(s.source_id): s for s in LovableSyncState.objects.filter(source_id__in=list(by_topic))
    }
    live = {
        str(pk) for pk in Matter.objects.filter(pk__in=list(by_matter)).values_list("pk", flat=True)
    }
    for page, pm in pairs:
        state = states.get(pm.lovable_source_id)
        label = f"{pm.reference} (page {page.tag})"
        if state is None:
            plan.problems.append(f"{label}: Lovable topic {pm.lovable_source_id} has no sync state")
            continue
        if str(state.matter_id) != pm.matter_id:
            plan.problems.append(f"{label}: the Lovable topic maps to another Teema")
            continue
        if state.cohort != REGISTER_COHORT:
            plan.problems.append(f"{label}: not in the {REGISTER_COHORT} cohort")
        if pm.matter_id not in live:
            plan.problems.append(f"{label}: the Teema is not live")
        link = _stored_onenote_link(pm.matter_id, state)
        # The *applied* link is the register's accepted record; an observation
        # (COMPARE, never applied) is not, so it is reported and never followed.
        observed = (state.observed or {}).get("onenote_link")
        named = register_page_guids(link)
        if isinstance(observed, str) and observed and register_page_guids(observed) != named:
            plan.observations["observed_link_differs"] += 1
        # Exactly this page, read where a link names a page — never a substring
        # match, which every link passes for the notebook GUID it carries (D5).
        if named != {page.page_guid.upper()}:
            unapplied = (
                " (Lovable's observed link names it, but that change is not applied)"
                if isinstance(observed, str)
                and register_page_guids(observed) == {page.page_guid.upper()}
                else ""
            )
            plan.problems.append(
                f"{label}: the Teema's stored OneNote link does not name exactly this page"
                + unapplied
            )
        plan.matter_refs[pm.matter_id] = pm.reference

    keys = [p.page_key for p in capture.pages]
    existing = {
        row.lk: row
        for row in LegacySourcePage.objects.filter(source_system=SourceSystem.ONENOTE_DESKTOP)
        .annotate(lk=Lower("page_key"), batch_system=F("import_batch__source_system"))
        .filter(lk__in=keys)
        .only(
            "pk",
            "page_key",
            "capture_id",
            "source_xml_sha256",
            "title",
            "source_section",
            "source_section_group",
            "blocks",
        )
    }
    earlier = _earlier_capture_rows(capture, list(by_matter))
    notes, removed = _earlier_notes(capture, earlier)
    page_readings, own_page_revisions = _page_readings(capture, [r.pk for r in existing.values()])
    for page in capture.pages:
        row = existing.get(page.page_key)
        ours = {o.block_object_id: o for o in page.occurrences}
        # An earlier resource row under this capture's block ID is the same
        # occurrence identity. A row this capture wrote is progress, never
        # "earlier", so the decisions (and the digest) do not move as batches
        # complete. A disagreeing earlier row used to refuse the whole capture; it
        # is now that occurrence's revision (amendment 2026-11-05, case 3; P1/P2).
        same_block: dict[str, Any] = {}
        rows: list[Any] = []
        readings: dict[Any, Reading] = {}
        if row is not None:
            rows = list(LegacySourceResource.objects.filter(source_page=row))
            written_here = _written_by_this_capture(capture, rows)
            same_block = {
                r.resource_key: r
                for r in rows
                if r.resource_key in ours and r.pk not in written_here
            }
            readings = _resource_readings(capture, list(same_block.values()))
        new_page = row is None or row.capture_id == capture.capture_id[:100]
        # A row this capture wrote is progress, not prior knowledge: only an earlier
        # capture's row is "known", so the digest does not move as batches complete.
        if row is not None and not new_page:
            plan.known_pages.append(page.page_key)
            plan.earlier_resources[page.page_key] = sum(
                1 for r in rows if r.resource_key not in ours
            )
            if row.source_xml_sha256 and row.source_xml_sha256 != capture.listed.get(
                f"pages/{page.tag}/post.xml"
            ):
                plan.observations["page_xml_changed"] += 1  # the row is kept (0205 §3)
            # A page an earlier *capture* wrote whose text, title or place changed
            # gets a page revision (P3). An October-pilot row is another reader's
            # shape: counted above, never compared.
            if row.batch_system == BATCH_SOURCE_SYSTEM:
                text_sig, place = page_readings.get(row.pk) or (
                    text_signature(row.blocks or []),
                    (row.title, row.source_section, row.source_section_group),
                )
                text_changed = text_sig != text_signature(rendered_blocks(page))
                place_changed = place != _place(page)
                if text_changed or place_changed:
                    plan.page_changes[page.page_key] = (text_changed, place_changed)
                    plan.classes["CHANGED_TEXT"] += int(text_changed)
                    plan.classes["PAGE_MOVED_OR_RENAMED"] += int(place_changed)
                    if row.pk in own_page_revisions:
                        plan.page_revised.add(page.page_key)
        if row is None:
            plan.classes["NEW_PAGE_PAGES"] += 1
        for pm in page.matters:
            decided = _later_capture_decisions(
                plan,
                page,
                same_block,
                earlier.get(pm.matter_id, []),
                notes,
                removed=removed,
                readings=readings,
                limit=limit,
                matter_id=pm.matter_id,
            )
            for occurrence in page.occurrences:
                base = (pm.matter_id, page.page_key, occurrence.block_object_id, occurrence.sha256)
                disposition, reason, klass = decided.get(occurrence.block_object_id, ("", "", ""))
                if disposition:
                    item = PlannedItem(*base, disposition, reason)
                elif page.shared:
                    item = PlannedItem(*base, HOLD, HOLD_SHARED_PAGE)
                elif occurrence.size == 0:
                    item = PlannedItem(*base, HOLD, HOLD_EMPTY_FILE)
                elif occurrence.size > limit:
                    item = PlannedItem(*base, HOLD, HOLD_TOO_LARGE)
                else:
                    item = PlannedItem(*base, PROMOTE)
                plan.items.append(item)
                plan.classes[klass or (CLASS_NEW_PAGE if new_page else CLASS_NEW_RESOURCE)] += 1

    matter_ids = list(by_matter)
    plan.linked = {
        (str(m), k)
        for m, k in MatterSourcePage.objects.filter(matter_id__in=matter_ids)
        .annotate(lk=Lower("source_page__page_key"))
        .filter(lk__in=keys)
        .values_list("matter_id", "lk")
    }
    done = (
        LegacySourceResourceImport.objects.filter(matter_source_page__matter_id__in=matter_ids)
        .annotate(lk=Lower("resource__source_page__page_key"))
        .filter(lk__in=keys)
        .values_list("matter_source_page__matter_id", "lk", "resource__resource_key", "state")
    )
    recorded = {(str(m), k, r): s for m, k, r, s in done}
    plan.recorded = set(recorded)
    plan.revised = {
        (str(m), k, r)
        for m, k, r in OneNoteResourceRevision.objects.filter(
            manifest_sha256=capture.manifest_sha256,
            matter_source_page__matter_id__in=matter_ids,
        )
        .annotate(lk=Lower("resource__source_page__page_key"))
        .filter(lk__in=keys)
        .values_list("matter_source_page__matter_id", "lk", "resource__resource_key")
    }
    for item in plan.items:
        key = (item.matter_id, item.page_key, item.resource_key)
        if item.disposition in REVISING:
            plan.progress["revised" if key in plan.revised else "revision_PENDING"] += 1
            continue
        recorded_state = recorded.get(key)
        plan.progress["recorded_" + (recorded_state or "PENDING")] += 1
    return plan


def _place(page: CapturePage) -> tuple[str, str, str]:
    """Where a page sits, in the page row's own columns (title, section, section group)."""
    return (page.title, page.section[:300], " / ".join(page.section_groups)[:400])


def text_signature(blocks: list[dict[str, Any]]) -> str:
    """The page's reading for change detection: each non-file block's kind, text,
    creation time and depth, in order.

    Never a block's object ID (a move re-issues every one, D2) and never its
    modification time (navigation touches text blocks, Phase 1 §9c). File blocks
    are the occurrences' business, compared one by one.
    """
    rows = []
    for block in blocks:
        kind = str(block.get("source_kind") or block.get("kind") or "")
        if kind in {"FILE", "FILE_ATTACHMENT", "FILE_NOT_CAPTURED"}:
            continue
        rows.append(
            [
                kind,
                str(block.get("text") or ""),
                str(block.get("created") or ""),
                block.get("depth"),
            ]
        )
    return canonical_sha256(rows)


def _page_readings(
    capture: Capture, page_ids: list[Any]
) -> tuple[dict[Any, tuple[str, tuple[str, str, str]]], set[Any]]:
    """Per page row: the latest *earlier* capture revision's reading; and the rows
    this capture already revised (progress, never an earlier reading)."""
    from app.legacy_import.onenote_coverage_models import OneNotePageRevision

    latest: dict[Any, tuple[str, tuple[str, str, str]]] = {}
    own: set[Any] = set()
    for page_id, manifest, sig, title, section, group in (
        OneNotePageRevision.objects.filter(page_id__in=page_ids)
        .order_by("created_at", "pk")
        .values_list(
            "page_id",
            "manifest_sha256",
            "text_signature",
            "title",
            "source_section",
            "source_section_group",
        )
    ):
        if manifest == capture.manifest_sha256:
            own.add(page_id)
        else:
            latest[page_id] = (sig, (title, section, group))
    return latest, own


@dataclass(frozen=True)
class Reading:
    """The latest earlier capture's reading of one resource (a revision row)."""

    sha256: str
    size_bytes: int
    source_added_at: datetime | None
    context: tuple[dict[str, Any], ...]


def _resource_readings(capture: Capture, resources: list[Any]) -> dict[Any, Reading]:
    """Per resource: the latest revision an *earlier* capture recorded, if any."""
    from app.legacy_import.onenote_coverage_models import OneNoteResourceRevision

    if not resources:
        return {}
    out: dict[Any, Reading] = {}
    for resource_id, sha, size, added, context in (
        OneNoteResourceRevision.objects.filter(resource__in=resources)
        .exclude(manifest_sha256=capture.manifest_sha256)
        .order_by("created_at", "pk")
        .values_list("resource_id", "sha256", "size_bytes", "source_added_at", "context")
    ):
        out[resource_id] = Reading(sha, size, added, tuple(context or ()))
    return out


@dataclass(frozen=True)
class EarlierRow:
    """One (Teema, occurrence) decision an *earlier* capture recorded (amendment 2026-11-05)."""

    page_key: str
    resource_key: str
    filename: str
    sha256: str
    size: int
    added_at: datetime | None
    state: str
    error_code: str
    document_id: Any


def _earlier_capture_rows(capture: Capture, matter_ids: list[str]) -> dict[str, list[EarlierRow]]:
    """Per Teema, the rows an earlier OneNote *capture* import recorded on its links.

    Only this importer's batches of another sealed manifest count. The October
    evidence pilot's rows (another importer, other resource keys) are never a
    continuity candidate, so a first capture plans exactly as it always did.
    """
    from app.legacy_import.source_pages import LegacySourceResourceImport

    out: dict[str, list[EarlierRow]] = {}
    rows = (
        LegacySourceResourceImport.objects.filter(
            matter_source_page__matter_id__in=matter_ids,
            import_batch__source_system=BATCH_SOURCE_SYSTEM,
        )
        .exclude(import_batch__source_snapshot_sha256=capture.manifest_sha256)
        .annotate(lk=Lower("resource__source_page__page_key"))
        .values_list(
            "matter_source_page__matter_id",
            "lk",
            "resource__resource_key",
            "resource__original_filename",
            "resource__sha256",
            "resource__size_bytes",
            "resource__source_added_at",
            "state",
            "error_code",
            "document_id",
        )
        .order_by("pk")
        .iterator(chunk_size=2000)
    )
    for matter_id, *values in rows:
        out.setdefault(str(matter_id), []).append(EarlierRow(*values))
    return out


def _earlier_notes(
    capture: Capture, earlier: dict[str, list[EarlierRow]]
) -> tuple[dict[Any, str], set[Any]]:
    """The provenance notes of earlier Documents whose block this capture names again,
    and which of those Documents a person has removed since."""
    from app.documents.models import Document

    blocks = {(p.page_key, o.block_object_id) for p in capture.pages for o in p.occurrences}
    ids = {
        e.document_id
        for rows in earlier.values()
        for e in rows
        if e.document_id and (e.page_key, e.resource_key) in blocks
    }
    if not ids:
        return {}, set()
    notes: dict[Any, str] = {}
    removed: set[Any] = set()
    # The plain manager: a removed Document is still there, and must be seen as removed.
    for pk, note, removed_at in Document.objects.filter(pk__in=ids).values_list(
        "pk", "provenance_note", "removed_at"
    ):
        notes[pk] = note
        if removed_at is not None:
            removed.add(pk)
    return notes, removed


def _written_by_this_capture(capture: Capture, resources: list[Any]) -> set[Any]:
    """The resource rows this capture's own batches wrote: every import row of theirs is ours."""
    from app.legacy_import.source_pages import LegacySourceResourceImport

    if not resources:
        return set()
    manifests: dict[Any, set[str]] = {}
    for resource_id, manifest in LegacySourceResourceImport.objects.filter(
        resource__in=resources
    ).values_list("resource_id", "import_batch__source_snapshot_sha256"):
        manifests.setdefault(resource_id, set()).add(manifest or "")
    return {pk for pk, seen in manifests.items() if seen == {capture.manifest_sha256}}


def _comment_line(basis: str, text: str) -> str:
    label = "Märkus OneNote'is" if basis == "ATTACHED_NOTE" else "Pealkiri OneNote'is"
    return f"{label}: {text}"


def _comment_changed(note: str, occurrence: Occurrence) -> bool:
    """Whether a Document's note carries other structural comments than this capture has."""
    lines = [_comment_line(basis, text) for basis, text in occurrence.structural_comment]
    recorded = note.count("\nMärkus OneNote'is: ") + note.count("\nPealkiri OneNote'is: ")
    return recorded != len(lines) or any(f"\n{line}" not in note for line in lines)


def structural_comment_of(context: Any) -> list[tuple[str, str]]:
    """(basis, verbatim text) of a stored context list, as ``Occurrence.structural_comment``."""
    return [
        (record["basis"], "\n".join(record["text"]))
        for record in context or ()
        if isinstance(record, dict)
        and record.get("basis") in STRUCTURAL_BASES
        and record.get("text")
    ]


Decision = tuple[str, str, str]  # (disposition or "", reason, class)


def _changed_bytes(
    plan: Plan,
    page: CapturePage,
    o: Occurrence,
    e: EarlierRow | None,
    *,
    removed: set[Any],
    limit: int,
    matter_id: str,
) -> Decision:
    """Other bytes under the same block identity: the next version, or a recorded hold.

    A version only where the earlier reading became a Document of *this* Teema
    that is still on its file, and only from bytes an evidence version may hold.
    Never on a shared page (D4): the earlier file was held there, and so is this.
    """
    from app.legacy_import.source_pages import ResourceImportState

    if page.shared:
        return REVISE, HOLD_SHARED_PAGE, CLASS_CHANGED_BYTES
    if e is None or e.state != ResourceImportState.IMPORTED or not e.document_id:
        return REVISE, HOLD_BYTES_CHANGED, CLASS_CHANGED_BYTES
    if e.document_id in removed:
        return REVISE, HOLD_DOCUMENT_REMOVED, CLASS_CHANGED_BYTES
    if o.size == 0:
        return REVISE, HOLD_EMPTY_FILE, CLASS_CHANGED_BYTES
    if o.size > limit:
        return REVISE, HOLD_TOO_LARGE, CLASS_CHANGED_BYTES
    plan.version_of[(matter_id, page.page_key, o.block_object_id)] = e.document_id
    return VERSION, VERSION_CHANGED_BYTES, CLASS_CHANGED_BYTES


def _later_capture_decisions(
    plan: Plan,
    page: CapturePage,
    same_block: dict[str, Any],
    earlier: list[EarlierRow],
    notes: dict[Any, str],
    *,
    removed: set[Any],
    readings: dict[Any, Reading],
    limit: int,
    matter_id: str,
) -> dict[str, Decision]:
    """Block ID -> (disposition, reason, class) for this capture's occurrences that an
    earlier capture decides; class only, for one this capture reads unchanged.

    For one (Teema, page). An occurrence not named here is decided as a first
    capture decides it. Identity is the block object ID; the *earlier reading* of
    it is the latest earlier capture's revision of the resource, else the resource
    row itself. Against it:

    * other bytes → the next ``DocumentVersion`` of the same Document, or a
      recorded hold (``_changed_bytes``);
    * another creation time → a recorded hold;
    * another structural comment → recorded beside the earlier one.

    Without the block ID, continuity is claimed only for a *pure move*:

    * the same page, since the page key is the page GUID;
    * exactly one earlier row on this Teema's link, and exactly one block of
      this capture, with that (creation time, filename);
    * an unchanged SHA-256 and size.

    Anything weaker that still resembles an earlier file is held for a person.
    It is never matched and never made into a second Document. A floating file
    has no creation time, so it is never continued at all.
    """
    from app.legacy_import.source_pages import ResourceImportState

    decided: dict[str, Decision] = {}
    by_key = {e.resource_key: e for e in earlier if e.page_key == page.page_key}
    for o in page.occurrences:
        r = same_block.get(o.block_object_id)
        if r is None:
            continue
        latest = readings.get(r.pk)
        sha, size, added = (
            (latest.sha256, latest.size_bytes, latest.source_added_at)
            if latest is not None
            else (r.sha256, r.size_bytes, r.source_added_at)
        )
        e = by_key.get(o.block_object_id)
        if sha != o.sha256 or size != o.size:
            decided[o.block_object_id] = _changed_bytes(
                plan, page, o, e, removed=removed, limit=limit, matter_id=matter_id
            )
        elif added != o.block_created:
            decided[o.block_object_id] = (REVISE, HOLD_TIME_CHANGED, CLASS_CHANGED_TIME)
        else:
            if latest is not None:
                changed = structural_comment_of(latest.context) != o.structural_comment
            else:
                note = notes.get(e.document_id) if e is not None and e.document_id else None
                changed = bool(
                    e is not None
                    and e.state == ResourceImportState.IMPORTED
                    and note is not None
                    and _comment_changed(note, o)
                )
            if changed:
                decided[o.block_object_id] = (REVISE, HOLD_COMMENT_CHANGED, CLASS_CHANGED_COMMENT)
            else:
                # Read unchanged: the same file for a Teema an earlier capture
                # decided; for any other Teema on the page it is new to that Teema.
                decided[o.block_object_id] = ("", "", CLASS_SAME if e is not None else "")
    if not earlier:
        return decided
    now = {o.block_object_id for o in page.occurrences}
    # An earlier block this capture no longer names. A row that itself continued
    # an older block is an alias of a row still on the page, never a candidate.
    gone = [
        e for e in by_key.values() if e.resource_key not in now and e.error_code != SKIP_CONTINUES
    ]
    new = [o for o in page.occurrences if o.block_object_id not in same_block]
    claimed: set[str] = set()
    for o in new:
        name = o.filename[:500]
        if o.block_created is not None:
            old = [e for e in gone if e.added_at == o.block_created and e.filename == name]
            twins = [
                x for x in new if x.block_created == o.block_created and x.filename == o.filename
            ]
            if len(old) == 1 and len(twins) == 1:
                e = old[0]
                pure = e.sha256 == o.sha256 and e.size == o.size
                decided[o.block_object_id] = (
                    (HOLD, SKIP_CONTINUES, CLASS_SAME_BLOCK_NEW_ID)
                    if pure
                    else (HOLD, HOLD_MOVED_AND_CHANGED, CLASS_HELD_FOR_REVIEW)
                )
                plan.earlier_blocks[(page.page_key, o.block_object_id)] = e.resource_key
                claimed.add(e.resource_key)
                continue
            if old:
                decided[o.block_object_id] = (HOLD, HOLD_AMBIGUOUS, CLASS_HELD_FOR_REVIEW)
                claimed.update(e.resource_key for e in old)
                continue
        similar = [e for e in gone if e.sha256 == o.sha256 and e.filename == name]
        if similar:
            decided[o.block_object_id] = (HOLD, HOLD_RESEMBLES, CLASS_HELD_FOR_REVIEW)
            if len(similar) == 1:
                plan.earlier_blocks[(page.page_key, o.block_object_id)] = similar[0].resource_key
            claimed.update(e.resource_key for e in similar)
            continue
        if any(
            e.page_key != page.page_key
            and e.sha256 == o.sha256
            and e.filename == name
            and e.added_at == o.block_created
            for e in earlier
        ):
            decided[o.block_object_id] = (HOLD, HOLD_SAME_FILE_ELSEWHERE, CLASS_HELD_FOR_REVIEW)
    removed_here = sum(1 for e in gone if e.resource_key not in claimed)
    plan.observations["earlier_gone"] += removed_here
    plan.classes["REMOVED"] += removed_here
    return decided


# -- applying ------------------------------------------------------------------------


@dataclass
class ApplyReport:
    batch_id: str = ""
    pages_written: int = 0
    pages_created: int = 0
    pages_reused: int = 0
    links_created: int = 0
    resources_created: int = 0
    documents_created: int = 0
    document_bytes: int = 0
    held: Counter[str] = field(default_factory=Counter)
    already_recorded: int = 0
    earlier_capture_rows: int = 0
    matters: set[str] = field(default_factory=set)
    pages_done_before: int = 0
    #: A later capture (P1–P3): new versions of existing Documents, recorded
    #: readings that made none, and page revisions.
    versions_created: int = 0
    version_bytes: int = 0
    revisions: Counter[str] = field(default_factory=Counter)
    page_revisions_created: int = 0
    #: Page key -> what was written for it, for the coverage marks.
    page_outcomes: dict[str, str] = field(default_factory=dict)

    def merge(self, other: ApplyReport) -> None:
        for name in (
            "versions_created",
            "version_bytes",
            "page_revisions_created",
            "pages_written",
            "pages_created",
            "pages_reused",
            "links_created",
            "resources_created",
            "documents_created",
            "document_bytes",
            "already_recorded",
            "earlier_capture_rows",
        ):
            setattr(self, name, getattr(self, name) + getattr(other, name))
        self.held.update(other.held)
        self.revisions.update(other.revisions)
        self.matters |= other.matters
        self.page_outcomes.update(other.page_outcomes)

    def as_dict(self) -> dict[str, Any]:
        out = dict(self.__dict__)
        out["held"] = dict(self.held)
        out["revisions"] = dict(self.revisions)
        out["matters"] = sorted(self.matters)
        return out


def refuse_unless_authorised(
    plan: Plan,
    *,
    operator_intent: str,
    expect_manifest_sha256: str,
    expect_plan_sha256: str,
    backup_set: str,
) -> str:
    if not getattr(settings, "ONENOTE_CAPTURE_IMPORT_ENABLED", False):
        raise CaptureError(
            "The OneNote capture import is off here (JURISTID_ONENOTE_CAPTURE_IMPORT). "
            "It runs only on the one-off container that applies it."
        )
    if not getattr(settings, "REAL_DATA_ALLOWED", False):
        raise CaptureError(
            "REAL_DATA_ALLOWED is off; real source material may not enter this environment."
        )
    if operator_intent != OPERATOR_INTENT:
        raise CaptureError(f"Type --operator-intent {OPERATOR_INTENT} to apply the capture.")
    if (expect_manifest_sha256 or "").strip().lower() != plan.capture.manifest_sha256:
        raise CaptureError(
            f"The sealed manifest is {plan.capture.manifest_sha256}; "
            "pass exactly that as --expect-manifest-sha256."
        )
    if not backup_set.strip():
        raise CaptureError("Name the verified backup set taken before this run (--backup-set).")
    if plan.problems:
        raise CaptureError(
            "The plan has problems; nothing was written:\n  " + "\n  ".join(plan.problems[:20])
        )
    try:
        return require_digest(
            plan.digest,
            expect_plan_sha256,
            flag="--expect-plan-sha256",
            what="The OneNote capture plan",
        )
    except IntegrityError as error:
        raise CaptureError(str(error)) from error


def select_pages(
    plan: Plan,
    *,
    matters: list[str] | None = None,
    limit: int | None = None,
    include_shared: bool = False,
) -> list[CapturePage]:
    """The next pages to write, in a deterministic order, finished pages skipped.

    Order: by the first Teema reference, then page key. ``matters`` picks pages
    of those Teemad (a shared page only with ``include_shared``); otherwise
    ``include_shared`` chooses between the single-Teema and the shared pages.
    """
    chosen = []
    # A page no register link names is inventoried, never written (no Teema to link).
    pages = sorted(
        (p for p in plan.capture.pages if p.matters),
        key=lambda p: (min(m.reference for m in p.matters), p.page_key),
    )
    for page in pages:
        ids = {m.matter_id for m in page.matters}
        if matters is not None:
            if not ids & set(matters) or (page.shared and not include_shared):
                continue
        elif page.shared != include_shared:
            continue
        if plan.page_done(page):
            continue
        chosen.append(page)
    return chosen if limit is None else chosen[:limit]


def apply(
    plan: Plan,
    pages: list[CapturePage],
    *,
    operator_intent: str,
    expect_manifest_sha256: str,
    expect_plan_sha256: str,
    backup_set: str,
    label: str,
    actor: Any = None,
) -> ApplyReport:
    """Write the chosen pages. One transaction per page; a finished page is never rewritten.

    The first failure (an interruption included) stops the run: the page that
    failed rolls back whole, the pages before it stay, and the batch is marked
    FAILED with the reason. Counts reach the report only once a page commits.
    Search rows are written by the ordinary per-row handlers as each page
    commits, the way a person's upload is indexed.
    """
    approved = refuse_unless_authorised(
        plan,
        operator_intent=operator_intent,
        expect_manifest_sha256=expect_manifest_sha256,
        expect_plan_sha256=expect_plan_sha256,
        backup_set=backup_set,
    )
    from app.legacy_import.models import ImportBatch, ReconciliationStatus
    from app.search.indexing import indexable_matters, refresh_matters

    report = ApplyReport()
    batch: ImportBatch | None = None
    by_page: dict[str, list[PlannedItem]] = {}
    for item in plan.items:
        by_page.setdefault(item.page_key, []).append(item)

    def open_batch() -> ImportBatch:
        return ImportBatch.objects.create(
            source_system=BATCH_SOURCE_SYSTEM,
            source_file_name=plan.capture.package[:400],
            source_snapshot_sha256=plan.capture.manifest_sha256,
            importer_version=IMPORTER_VERSION,
            contract_version=CONTRACT_VERSION,
            started_at=timezone.now(),
            reconciliation_status=ReconciliationStatus.RUNNING,
            notes=json.dumps(
                {
                    "label": label,
                    "plan_sha256": approved,
                    "backup_set": backup_set.strip(),
                    "capture_id": plan.capture.capture_id,
                    "sha256sums_sha256": plan.capture.sums_sha256,
                },
                sort_keys=True,
            ),
        )

    failure: BaseException | None = None
    refresh_failure: Exception | None = None
    current: CapturePage | None = None
    try:
        for page in pages:
            current = page
            if _page_complete(
                plan.capture, page, by_page.get(page.page_key, []), plan.page_changes
            ):
                report.pages_done_before += 1
                continue
            if batch is None:
                batch = open_batch()
                report.batch_id = str(batch.pk)
            page_report = ApplyReport()
            _write_page(
                plan.capture,
                page,
                by_page.get(page.page_key, []),
                batch=batch,
                report=page_report,
                earlier=plan.earlier_blocks,
                page_change=plan.page_changes.get(page.page_key),
                version_of=plan.version_of,
            )
            report.merge(page_report)  # only after the page's transaction committed
        current = None
    except BaseException as error:  # an interruption is a failure too; re-raised below
        failure = error
    finally:
        if batch is not None:
            batch.finished_at = timezone.now()
            batch.matched_count = report.documents_created + report.versions_created
            batch.unmatched_count = sum(report.held.values())
            batch.source_row_count = (
                report.documents_created
                + report.versions_created
                + sum(report.held.values())
                + sum(report.revisions.values())
            )
            batch.reconciliation_status = (
                ReconciliationStatus.FAILED
                if failure
                else ReconciliationStatus.COMPLETED_WITH_GAPS
                if report.held
                else ReconciliationStatus.COMPLETED
            )
            notes = json.loads(batch.notes)
            notes["report"] = report.as_dict()
            if failure:
                notes["failure"] = f"{type(failure).__name__}: {str(failure)[:1500]}"
                notes["note"] = (
                    "The failed page rolled back; evidence objects it stored before failing are "
                    "orphaned (check_evidence_integrity lists them; never prune without the owner)."
                )
            batch.notes = json.dumps(notes, sort_keys=True)
            batch.save(
                update_fields=[
                    "finished_at",
                    "matched_count",
                    "unmatched_count",
                    "source_row_count",
                    "reconciliation_status",
                    "notes",
                    "updated_at",
                ]
            )
        if report.matters:
            try:
                refresh_matters(indexable_matters().filter(pk__in=list(report.matters)))
            except Exception as refresh_error:  # never hides the run's own failure
                refresh_failure = refresh_error
        # Progress marks (onenote_coverage): each committed page, and the page that failed.
        outcomes = dict(report.page_outcomes)
        if failure is not None and current is not None:
            outcomes[current.page_key] = "FAILED"
        if outcomes:
            try:
                from app.legacy_import.onenote_coverage import record_capture_marks

                record_capture_marks(
                    plan.capture, outcomes, actor=actor, reason=f"apply: {label}"[:300]
                )
            except Exception as mark_error:  # never hides the run's own failure
                refresh_failure = refresh_failure or mark_error
    if failure is not None:
        if isinstance(failure, KeyboardInterrupt | SystemExit):
            raise failure
        raise CaptureError(f"stopped at a page that rolled back whole: {failure}") from failure
    if refresh_failure is not None:
        raise CaptureError(
            "every chosen page committed, but refreshing the Teemad's search rows failed "
            f"({refresh_failure}); run rebuild_search_index for them"
        ) from refresh_failure
    return report


def _page_complete(
    capture: Capture,
    page: CapturePage,
    items: list[PlannedItem],
    page_changes: dict[str, tuple[bool, bool]] | None = None,
) -> bool:
    """Whether the database already holds every row this page's items call for."""
    from app.legacy_import.onenote_coverage_models import (
        OneNotePageRevision,
        OneNoteResourceRevision,
    )
    from app.legacy_import.source_pages import LegacySourceResourceImport, MatterSourcePage

    ids = [m.matter_id for m in page.matters]
    links = (
        MatterSourcePage.objects.filter(matter_id__in=ids)
        .annotate(lk=Lower("source_page__page_key"))
        .filter(lk=page.page_key)
        .count()
    )
    if links != len(ids):
        return False
    if (
        page_changes
        and page.page_key in page_changes
        and not OneNotePageRevision.objects.filter(manifest_sha256=capture.manifest_sha256)
        .annotate(lk=Lower("page__page_key"))
        .filter(lk=page.page_key)
        .exists()
    ):
        return False
    blocks = [o.block_object_id for o in page.occurrences]
    rows = {
        (str(m), r)
        for m, r in LegacySourceResourceImport.objects.filter(
            matter_source_page__matter_id__in=ids, resource__resource_key__in=blocks
        )
        .annotate(lk=Lower("resource__source_page__page_key"))
        .filter(lk=page.page_key)
        .values_list("matter_source_page__matter_id", "resource__resource_key")
    }
    revised = {
        (str(m), r)
        for m, r in OneNoteResourceRevision.objects.filter(
            manifest_sha256=capture.manifest_sha256,
            matter_source_page__matter_id__in=ids,
            resource__resource_key__in=blocks,
        )
        .annotate(lk=Lower("resource__source_page__page_key"))
        .filter(lk=page.page_key)
        .values_list("matter_source_page__matter_id", "resource__resource_key")
    }
    return all(
        (i.matter_id, i.resource_key) in (revised if i.disposition in REVISING else rows)
        for i in items
    )


def _title_for(occurrence: Occurrence) -> str:
    name = (occurrence.filename or "").strip()
    return (name or f"OneNote'i fail {occurrence.ordinal}")[:400]


def _write_page(
    capture: Capture,
    page: CapturePage,
    items: list[PlannedItem],
    *,
    batch: Any,
    report: ApplyReport,
    earlier: dict[tuple[str, str], str] | None = None,
    page_change: tuple[bool, bool] | None = None,
    version_of: dict[tuple[str, str, str], Any] | None = None,
) -> None:
    from app.documents.enums import ExtractionState
    from app.documents.services import add_evidence_version, create_document
    from app.legacy_import.historical_apply import legacy_source_storage
    from app.legacy_import.historical_materials import (
        extraction_note_for,
        is_extractable,
        mime_type_for,
        role_for,
    )
    from app.legacy_import.onenote_coverage_models import OneNotePageRevision
    from app.legacy_import.source_pages import (
        LegacySourcePage,
        LegacySourceResource,
        LegacySourceResourceImport,
        MatterSourcePage,
        ResourceImportState,
        SourceMatchClass,
        SourceMatchMethod,
        SourcePageRole,
        SourceRelationshipKind,
        SourceSystem,
    )
    from app.matters.locks import lock_matters_in_order

    post_xml = capture.read_page_file(page, "post.xml")
    with transaction.atomic():
        locked = lock_matters_in_order(*[pm.matter_id for pm in page.matters])
        matters = {str(pk): m for pk, m in locked.items()}
        if any(
            matters.get(pm.matter_id) is None or matters[pm.matter_id].deleted_at
            for pm in page.matters
        ):
            raise CaptureError(f"page {page.tag}: a Teema is gone or removed since the plan")
        record = LegacySourcePage.objects.filter(
            source_system=SourceSystem.ONENOTE_DESKTOP, page_key__iexact=page.page_key
        ).first()
        now = timezone.now()
        if record is None:
            key = f"{page.page_key}/{capture.capture_id}/page.source.xml"
            storage = legacy_source_storage()
            if not storage.exists(key):
                storage.save(key, ContentFile(post_xml))
            text = "\n".join(b["text"] for b in page.blocks if b.get("text"))
            record = LegacySourcePage.objects.create(
                source_system=SourceSystem.ONENOTE_DESKTOP,
                source_page_id="{" + page.page_guid + "}",
                page_key=page.page_key,
                source_notebook=page.notebook[:200],
                source_section=page.section[:300],
                source_section_group=" / ".join(page.section_groups)[:400],
                source_parent_page=_parent_title(capture, page)[:400],
                title=page.title,
                page_level=page.level,
                page_role=SourcePageRole.UNCLEAR if page.shared else SourcePageRole.MATTER_LIKE,
                role_reason=(
                    f"Lovable'i link täpse lehe GUID-iga; seotud teemasid: {len(page.matters)}"
                )[:2000],
                source_created_at=page.page_created,
                source_modified_at=page.page_modified,
                capture_id=capture.capture_id[:100],
                source_xml_storage_key=key,
                source_xml_sha256=hashlib.sha256(post_xml).hexdigest(),
                derived_text=text,
                blocks=rendered_blocks(page),
                links=[],
                onenote_hyperlink=page.onenote_hyperlink,
                reference_tokens=" ".join(sorted(set(_REFERENCE.findall(page.title))))[:300],
                text_characters=len(text),
                block_count=len(page.blocks),
                file_count=len(page.occurrences),
                file_bytes=sum(o.size for o in page.occurrences),
                first_imported_at=now,
                latest_imported_at=now,
                import_batch=batch,
            )
            report.pages_created += 1
        else:
            report.pages_reused += 1  # an earlier capture's row: kept exactly as it is
            if (
                page_change is not None
                and not OneNotePageRevision.objects.filter(
                    page=record, manifest_sha256=capture.manifest_sha256
                ).exists()
            ):
                # The page's text, title or place changed since its latest reading:
                # this capture's reading is kept beside the row, which stays (P3).
                key = f"{page.page_key}/{capture.capture_id}/page.source.xml"
                storage = legacy_source_storage()
                if not storage.exists(key):
                    storage.save(key, ContentFile(post_xml))
                OneNotePageRevision.objects.create(
                    page=record,
                    import_batch=batch,
                    capture_id=capture.capture_id[:100],
                    manifest_sha256=capture.manifest_sha256,
                    source_xml_storage_key=key,
                    source_xml_sha256=hashlib.sha256(post_xml).hexdigest(),
                    text_signature=text_signature(rendered_blocks(page)),
                    text_changed=page_change[0],
                    location_changed=page_change[1],
                    title=page.title,
                    source_section=page.section[:300],
                    source_section_group=" / ".join(page.section_groups)[:400],
                    source_modified_at=page.page_modified,
                    derived_text="\n".join(b["text"] for b in page.blocks if b.get("text")),
                    blocks=rendered_blocks(page),
                )
                report.page_revisions_created += 1
        resources = {
            r.resource_key: r for r in LegacySourceResource.objects.filter(source_page=record)
        }
        # The plan held these: an earlier row under this block ID that this capture
        # contradicts. It is kept exactly as it is; nothing is overwritten.
        contradicted = {i.resource_key for i in items if i.disposition in REVISING}
        for occurrence in page.occurrences:
            existing = resources.get(occurrence.block_object_id)
            if existing is not None:
                if occurrence.block_object_id in contradicted:
                    continue
                if existing.sha256 != occurrence.sha256 or existing.size_bytes != occurrence.size:
                    raise CaptureError(
                        f"{page.tag}/{occurrence.block_object_id}: "
                        "existing row disagrees with the seal"
                    )
                continue
            resources[occurrence.block_object_id] = LegacySourceResource.objects.create(
                source_page=record,
                resource_key=occurrence.block_object_id,
                original_filename=occurrence.filename[:500],
                source_block_ordinal=occurrence.block_ordinal or 0,
                sha256=occurrence.sha256,
                size_bytes=occurrence.size,
                archive_relative_path=f"{capture.package}/blobs/{occurrence.sha256}",
                source_added_at=occurrence.block_created,
                source_added_basis=SOURCE_ADDED_BASIS if occurrence.block_created else "",
            )
            report.resources_created += 1
        ours = {o.block_object_id for o in page.occurrences}
        by_key = {o.block_object_id: o for o in page.occurrences}
        for pm in page.matters:
            matter = matters[pm.matter_id]
            link, created = MatterSourcePage.objects.get_or_create(
                matter=matter,
                source_page=record,
                defaults={
                    "relationship_kind": SourceRelationshipKind.PRIMARY,
                    "match_method": SourceMatchMethod.LOVABLE_EXACT_PAGE_GUID,
                    "match_class": SourceMatchClass.EXACT,
                    "source_audit_reference": f"{capture.capture_id}/{page.tag}"[:200],
                },
            )
            report.links_created += int(created)
            done = set(
                LegacySourceResourceImport.objects.filter(matter_source_page=link).values_list(
                    "resource__resource_key", flat=True
                )
            )
            for item in [i for i in items if i.matter_id == pm.matter_id]:
                if item.disposition in REVISING:
                    _write_revision(
                        capture,
                        page,
                        item,
                        link=link,
                        resource=resources[item.resource_key],
                        occurrence=by_key[item.resource_key],
                        batch=batch,
                        report=report,
                        document_id=(version_of or {}).get(
                            (item.matter_id, item.page_key, item.resource_key)
                        ),
                    )
                    continue
                if item.resource_key in done:
                    report.already_recorded += 1
                    continue
                occurrence, resource = by_key[item.resource_key], resources[item.resource_key]
                if item.disposition == HOLD:
                    LegacySourceResourceImport.objects.create(
                        matter_source_page=link,
                        resource=resource,
                        import_batch=batch,
                        state=ResourceImportState.SKIPPED,
                        error_code=item.reason,
                        error_detail=_hold_detail(
                            item.reason,
                            page,
                            earlier=(earlier or {}).get((page.page_key, item.resource_key), ""),
                        ),
                    )
                    report.held[item.reason] += 1
                    continue
                content = capture.read_blob(occurrence)
                extractable = is_extractable(Path(occurrence.filename).suffix.lower())
                document = create_document(
                    matter=matter,
                    title=_title_for(occurrence),
                    role=role_for(occurrence.filename),
                    provenance_note=provenance_note(capture, page, occurrence),
                )
                version = add_evidence_version(
                    document=document,
                    content=content,
                    original_filename=(occurrence.filename or "").strip()[:500]
                    or f"onenote-{occurrence.ordinal}.bin",
                    mime_type=mime_type_for(occurrence.filename),
                    expected_sha256=occurrence.sha256,
                    acquired_at=occurrence.copied_at,
                    source_path=f"{capture.package}/blobs/{occurrence.sha256}",
                    source_identifier=f"{record.page_key}/{resource.resource_key}"[:400],
                    extraction_state=ExtractionState.PENDING
                    if extractable
                    else ExtractionState.NOT_APPLICABLE,
                    extraction_note=""
                    if extractable
                    else extraction_note_for(occurrence.filename)[:300],
                )
                if version.sha256 != occurrence.sha256 or version.size_bytes != occurrence.size:
                    raise CaptureError(
                        f"{page.tag}/{item.resource_key}: stored bytes differ from the seal"
                    )
                LegacySourceResourceImport.objects.create(
                    matter_source_page=link,
                    resource=resource,
                    document=document,
                    document_version=version,
                    import_batch=batch,
                    state=ResourceImportState.IMPORTED,
                )
                report.documents_created += 1
                report.document_bytes += version.size_bytes
            # An earlier capture's rows on a reused page: never imported for this Teema,
            # recorded so that every (link, resource) pair is accounted for.
            for key, resource in resources.items():
                if key in ours or key in done:
                    continue
                LegacySourceResourceImport.objects.create(
                    matter_source_page=link,
                    resource=resource,
                    import_batch=batch,
                    state=ResourceImportState.SKIPPED,
                    error_code=HOLD_EARLIER_CAPTURE,
                    error_detail=(
                        "Varasema hõive rida; selle teema failid tulevad hõivest "
                        + capture.capture_id
                    ),
                )
                report.earlier_capture_rows += 1
            report.matters.add(pm.matter_id)
        report.pages_written += 1
        # What this page now waits on (the coverage marks, onenote_coverage): a shared
        # page or a file held for a person's decision awaits association.
        review = {HOLD_SHARED_PAGE, *REVIEW_HOLDS}
        report.page_outcomes[page.page_key] = (
            "AWAITING_ASSOCIATION"
            if page.shared or any(i.reason in review for i in items)
            else "IMPORTED"
        )


#: Holds a person decides (as opposed to a file that cannot be evidence at all).
REVIEW_HOLDS = frozenset(
    {
        HOLD_MOVED_AND_CHANGED,
        HOLD_AMBIGUOUS,
        HOLD_RESEMBLES,
        HOLD_SAME_FILE_ELSEWHERE,
        HOLD_BYTES_CHANGED,
        HOLD_TIME_CHANGED,
        HOLD_DOCUMENT_REMOVED,
    }
)


def _write_revision(
    capture: Capture,
    page: CapturePage,
    item: PlannedItem,
    *,
    link: Any,
    resource: Any,
    occurrence: Occurrence,
    batch: Any,
    report: ApplyReport,
    document_id: Any,
) -> None:
    """Record this capture's reading of an occurrence the Teema link already holds.

    ``VERSION``: the bytes become the next ``DocumentVersion`` of the earlier
    Document, through ``add_evidence_version`` (the earlier version, its bytes,
    hash and storage key are never touched). ``REVISE``: the reading is recorded
    with its reason and nothing else changes. Either way exactly one row per
    (link, occurrence, capture), so a repeated apply records nothing.
    """
    from app.documents.enums import ExtractionState
    from app.documents.models import Document
    from app.documents.services import add_evidence_version
    from app.legacy_import.historical_materials import (
        extraction_note_for,
        is_extractable,
        mime_type_for,
    )
    from app.legacy_import.onenote_coverage_models import (
        OneNoteResourceRevision,
        RevisionKind,
        RevisionState,
    )

    if OneNoteResourceRevision.objects.filter(
        matter_source_page=link, resource=resource, manifest_sha256=capture.manifest_sha256
    ).exists():
        report.already_recorded += 1
        return
    kind = {
        HOLD_TIME_CHANGED: RevisionKind.CHANGED_TIME,
        HOLD_COMMENT_CHANGED: RevisionKind.CHANGED_COMMENT,
    }.get(item.reason, RevisionKind.CHANGED_BYTES)
    document = version = None
    if item.disposition == VERSION:
        document = Document.objects.get(pk=document_id)
        if document.matter_id != link.matter_id or document.removed_at is not None:
            raise CaptureError(
                f"{page.tag}/{item.resource_key}: the Document is not on this Teema's file"
            )
        extractable = is_extractable(Path(occurrence.filename).suffix.lower())
        version = add_evidence_version(
            document=document,
            content=capture.read_blob(occurrence),
            original_filename=(occurrence.filename or "").strip()[:500]
            or f"onenote-{occurrence.ordinal}.bin",
            mime_type=mime_type_for(occurrence.filename),
            expected_sha256=occurrence.sha256,
            acquired_at=occurrence.copied_at,
            source_path=f"{capture.package}/blobs/{occurrence.sha256}",
            source_identifier=(
                f"{page.page_key}/{resource.resource_key}@{capture.capture_id}"[:400]
            ),
            extraction_state=ExtractionState.PENDING
            if extractable
            else ExtractionState.NOT_APPLICABLE,
            extraction_note="" if extractable else extraction_note_for(occurrence.filename)[:300],
        )
        if version.sha256 != occurrence.sha256 or version.size_bytes != occurrence.size:
            raise CaptureError(f"{page.tag}/{item.resource_key}: stored bytes differ from the seal")
        state = RevisionState.VERSIONED
        report.versions_created += 1
        report.version_bytes += version.size_bytes
    elif item.reason == HOLD_COMMENT_CHANGED:
        state = RevisionState.RECORDED
    else:
        state = RevisionState.HELD
    OneNoteResourceRevision.objects.create(
        matter_source_page=link,
        resource=resource,
        import_batch=batch,
        capture_id=capture.capture_id[:100],
        manifest_sha256=capture.manifest_sha256,
        kind=kind,
        state=state,
        reason=item.reason,
        detail=_hold_detail(item.reason, page),
        sha256=occurrence.sha256,
        size_bytes=occurrence.size,
        source_added_at=occurrence.block_created,
        block_modified_at=occurrence.block_modified or occurrence.object_modified,
        archive_relative_path=f"{capture.package}/blobs/{occurrence.sha256}",
        context=list(occurrence.context),
        document=document if version is not None else None,
        document_version=version,
    )
    report.revisions[item.reason] += 1


def _parent_title(capture: Capture, page: CapturePage) -> str:
    if not page.parent_object_id:
        return ""
    parent = next((p for p in capture.pages if p.page_object_id == page.parent_object_id), None)
    return parent.title if parent else ""


def _hold_detail(reason: str, page: CapturePage, *, earlier: str = "") -> str:
    block = f" (varasem plokk {earlier})" if earlier else ""
    later = {
        SKIP_CONTINUES: "Sama fail mis varasema hõive plokis{b}: sama lehe GUID, loomise aeg, "
        "failinimi ja SHA-256. Plokk on teisaldatud; varasem otsus kehtib, teist dokumenti ei "
        "looda.",
        HOLD_MOVED_AND_CHANGED: "Varasema faili{b} loomise aeg ja nimi, kuid teised baidid: "
        "teisaldus ja muutmine korraga ei tõenda järjepidevust. Vajab ülevaatust.",
        HOLD_AMBIGUOUS: "Sama loomise aja ja nimega faile on lehel mitu; millise varasema "
        "failiga see sama on, otsustab inimene.",
        HOLD_RESEMBLES: "Samad baidid ja nimi kui varasemal failil{b}, kuid loomise aeg seda "
        "ei tõenda. Vajab ülevaatust; teist dokumenti ei looda.",
        HOLD_SAME_FILE_ELSEWHERE: "Sama fail (baidid, nimi, loomise aeg) on selle teema teisel "
        "lehel juba olemas; leht võib olla koopia. Vajab ülevaatust.",
        VERSION_CHANGED_BYTES: "Selle ploki baidid on varasemast hõivest erinevad: uued baidid "
        "on sama dokumendi järgmine versioon; varasem versioon on muutmata.",
        HOLD_BYTES_CHANGED: "Selle ploki baidid on varasemast hõivest erinevad, kuid varasem "
        "fail ei ole selle teema dokument; uued baidid on lähtekorpuses ja ootavad ülevaatust.",
        HOLD_DOCUMENT_REMOVED: "Selle ploki baidid on varasemast hõivest erinevad, kuid "
        "dokument on teemalt eemaldatud; uut versiooni ei lisata. Vajab ülevaatust.",
        HOLD_TIME_CHANGED: "Selle ploki loomise aeg erineb varasemast hõivest; varasem kirje "
        "on muutmata. Vajab ülevaatust.",
        HOLD_COMMENT_CHANGED: "Faili märkus OneNote'is erineb varasemast hõivest; uus märkus "
        "on salvestatud varasema kõrvale, dokumendi päritolumärkus on muutmata.",
    }
    if reason in later:
        return later[reason].format(b=block)
    if reason == HOLD_SHARED_PAGE:
        refs = ", ".join(sorted(m.reference for m in page.matters))
        return (
            f"Leht on seotud mitme teemaga ({refs}); faili kuuluvus vajab ülevaatust. "
            "Originaal on lähtekorpuses alles."
        )
    if reason == HOLD_EMPTY_FILE:
        return "OneNote'i fail on 0-baidine; tühja tõendit ei looda. Kirje on lähtekorpuses alles."
    if reason == HOLD_TOO_LARGE:
        return "Fail on suurem kui tõendi ülempiir; originaal on lähtekorpuses alles."
    return reason


def _tallinn(moment: datetime) -> str:
    return timezone.localtime(moment).strftime("%d.%m.%Y %H:%M")


def provenance_note(capture: Capture, page: CapturePage, occurrence: Occurrence) -> str:
    """What the document page says about where this file came from. Verbatim source text only."""
    location = " → ".join([*page.section_groups, page.section, page.title[:160]])
    lines = [f"OneNote: {location}"]
    if occurrence.block_created:
        lines.append(f"Lisatud OneNote'i: {_tallinn(occurrence.block_created)}")
        if page.page_created and occurrence.block_created < page.page_created:
            lines.append("Lisatud enne praeguse lehe loomist (sisu on teisaldatud).")
        if occurrence.content_may_be_newer and occurrence.block_modified:
            lines.append(
                "Faili sisu võib olla hiljem uuendatud "
                f"(ploki viimane muutmine {_tallinn(occurrence.block_modified)})."
            )
    else:
        lines.append("Lisamise kuupäev teadmata.")
        if occurrence.object_modified:
            lines.append(
                f"OneNote'i objekti viimane muutmine: {_tallinn(occurrence.object_modified)}"
            )
    for basis, text in occurrence.structural_comment:
        lines.append(_comment_line(basis, text))
    where = f", plokk {occurrence.block_ordinal}" if occurrence.block_ordinal is not None else ""
    lines.append(f"Allikas: {capture.capture_id}{where}")
    return "\n".join(lines)


# -- verifying (reads only) ----------------------------------------------------------


def verify(
    capture: Capture, *, check_bytes: bool = False, require_complete: bool = False
) -> dict[str, Any]:
    """Read back every row this capture produced for its Teemad and reconcile it with the seal.

    Rows on the captured pages that belong to other Matters (an earlier
    capture's, on Teemad since retired) are counted, not judged. Items not yet
    applied are counted; with ``require_complete`` each is a problem.
    """
    from app.documents.filenames import canonical_filename
    from app.documents.models import Document
    from app.documents.services import evidence_storage
    from app.legacy_import.onenote_coverage_models import (
        OneNoteResourceRevision,
        RevisionState,
    )
    from app.legacy_import.source_pages import (
        LegacySourcePage,
        LegacySourceResourceImport,
        MatterSourcePage,
        ResourceImportState,
    )

    problems: list[str] = []
    counts: Counter[str] = Counter()
    keys = [p.page_key for p in capture.pages]
    matter_ids = sorted({pm.matter_id for p in capture.pages for pm in p.matters})
    expected = {}
    for page in capture.pages:
        for pm in page.matters:
            for o in page.occurrences:
                expected[(pm.matter_id, page.page_key, o.block_object_id)] = (page, o)
    # **Bounded memory** (2026-11-05). Every row used to arrive with its whole
    # source page joined in — the page's text, blocks and links, copied once per
    # row — plus the whole Document and the whole link, and the queryset cached
    # all of them: the production run over 9,783 decisions was killed at 2 GB.
    # The page key is now read once per page, each row brings only the columns
    # checked below, and the rows are streamed. Nothing checked is dropped.
    page_keys = dict(
        LegacySourcePage.objects.annotate(lk=Lower("page_key"))
        .filter(lk__in=keys)
        .values_list("pk", "lk")
    )
    rows = (
        LegacySourceResourceImport.objects.filter(resource__source_page_id__in=list(page_keys))
        .select_related(
            "matter_source_page", "resource", "document", "document_version", "import_batch"
        )
        .only(
            "pk",
            "state",
            "error_code",
            "document_id",
            "document_version_id",
            "import_batch__source_system",
            "import_batch__source_snapshot_sha256",
            "matter_source_page__matter_id",
            "resource__resource_key",
            "resource__source_page_id",
            "resource__sha256",
            "resource__size_bytes",
            "resource__source_added_at",
            "document__matter_id",
            "document__created_by_id",
            "document_version__document_id",
            "document_version__sha256",
            "document_version__size_bytes",
            "document_version__original_filename",
            "document_version__uploaded_by_id",
            "document_version__storage_key",
        )
        .order_by("pk")
        .iterator(chunk_size=500)
    )
    seen = set()
    storage = evidence_storage() if check_bytes else None
    wanted = set(matter_ids)
    # Later readings (P1/P2): this capture's own revision of an occurrence, else the
    # latest any capture recorded. The occurrence is reconciled with that reading;
    # the earlier import row is reconciled with the reading it was made from.
    revisions = {
        (
            str(r.matter_source_page.matter_id),
            page_keys[r.resource.source_page_id],
            r.resource.resource_key,
        ): r
        for r in OneNoteResourceRevision.objects.filter(
            manifest_sha256=capture.manifest_sha256,
            resource__source_page_id__in=list(page_keys),
            matter_source_page__matter_id__in=matter_ids,
        ).select_related("matter_source_page", "resource", "document", "document_version")
    }
    latest: dict[Any, Any] = {}
    for r in (
        OneNoteResourceRevision.objects.filter(resource__source_page_id__in=list(page_keys))
        .only("resource_id", "sha256", "size_bytes", "source_added_at")
        .order_by("created_at", "pk")
    ):
        latest[r.resource_id] = r
    for row in rows:
        matter_id = str(row.matter_source_page.matter_id)
        if matter_id not in wanted:
            counts["rows_of_other_matters_on_captured_pages"] += 1
            continue
        key = (matter_id, page_keys[row.resource.source_page_id], row.resource.resource_key)
        if key not in expected:
            if row.error_code == HOLD_EARLIER_CAPTURE and row.state == ResourceImportState.SKIPPED:
                counts["earlier_capture_rows"] += 1
                continue
            # A row an earlier sealed capture recorded for a block this one no longer
            # names (moved, re-issued or removed): that capture's, counted, not judged
            # (amendment 2026-11-05). Any other unexpected row is still a problem.
            batch = row.import_batch
            if (
                batch is not None
                and batch.source_system == BATCH_SOURCE_SYSTEM
                and batch.source_snapshot_sha256 != capture.manifest_sha256
            ):
                counts["earlier_capture_rows"] += 1
                continue
            problems.append(f"unexpected import row {row.pk}")
            continue
        if key in seen:
            problems.append(f"two import rows for {key}")
        seen.add(key)
        page, o = expected[key]
        counts["state_" + row.state] += 1
        resource = row.resource
        later = revisions.get(key) or latest.get(resource.pk)
        reading = later if later is not None else resource
        if reading.sha256 != o.sha256 or reading.size_bytes != o.size:
            problems.append(f"{page.tag}/{o.block_object_id}: resource hash/size differ")
        if reading.source_added_at != o.block_created:
            problems.append(
                f"{page.tag}/{o.block_object_id}: source_added_at differs from the block time"
            )
        # The earlier version is judged against the reading it was made from.
        made_from = (resource.sha256, resource.size_bytes) if later else (o.sha256, o.size)
        if later is not None:
            counts["read_again_by_a_later_capture"] += 1
        if row.state == ResourceImportState.IMPORTED:
            version = row.document_version
            if version is None or row.document is None or version.document_id != row.document_id:
                problems.append(f"{page.tag}/{o.block_object_id}: imported without its document")
                continue
            if row.document.matter_id != row.matter_source_page.matter_id:
                problems.append(f"{page.tag}/{o.block_object_id}: document on another Teema")
            if (version.sha256, version.size_bytes) != made_from:
                problems.append(f"{page.tag}/{o.block_object_id}: stored version differs")
            wanted_name = (
                canonical_filename((o.filename or "").strip()[:500]) or version.original_filename
            )
            if later is None and version.original_filename != wanted_name:
                problems.append(f"{page.tag}/{o.block_object_id}: filename differs")
            if version.uploaded_by_id is not None or row.document.created_by_id is not None:
                problems.append(f"{page.tag}/{o.block_object_id}: an actor was recorded")
            if page.shared or o.size == 0:
                problems.append(f"{page.tag}/{o.block_object_id}: a held item was promoted")
            counts["documents"] += 1
            counts["document_bytes"] += version.size_bytes
            counts["dated" if o.block_created else "undated"] += 1
            if storage is not None:
                with storage.open(version.storage_key, "rb") as handle:
                    digest = hashlib.sha256()
                    while chunk := handle.read(1 << 20):
                        digest.update(chunk)
                    if digest.hexdigest() != made_from[0]:
                        problems.append(f"{page.tag}/{o.block_object_id}: stored bytes do not hash")
                counts["bytes_rehashed"] += 1
        else:
            counts["held_" + (row.error_code or "UNKNOWN")] += 1
            if row.document_id or row.document_version_id:
                problems.append(f"{page.tag}/{o.block_object_id}: a held row carries a document")
    for key, rev in revisions.items():
        if key not in expected:
            problems.append(f"unexpected revision {rev.pk}")
            continue
        page, o = expected[key]
        counts["revision_" + rev.state] += 1
        if key not in seen:  # a Teema linked after the earlier capture: the revision is its row
            seen.add(key)
            if rev.sha256 != o.sha256 or rev.size_bytes != o.size:
                problems.append(f"{page.tag}/{o.block_object_id}: resource hash/size differ")
        if rev.state != RevisionState.VERSIONED:
            if rev.document_id or rev.document_version_id:
                problems.append(f"{page.tag}/{o.block_object_id}: a held revision has a document")
            continue
        version = rev.document_version
        if version is None or rev.document is None or version.document_id != rev.document_id:
            problems.append(f"{page.tag}/{o.block_object_id}: versioned without its document")
            continue
        if rev.document.matter_id != rev.matter_source_page.matter_id:
            problems.append(f"{page.tag}/{o.block_object_id}: version on another Teema")
        if version.sha256 != o.sha256 or version.size_bytes != o.size:
            problems.append(f"{page.tag}/{o.block_object_id}: stored new version differs")
        if version.uploaded_by_id is not None:
            problems.append(f"{page.tag}/{o.block_object_id}: an actor was recorded")
        counts["new_versions"] += 1
        counts["new_version_bytes"] += version.size_bytes
        if storage is not None:
            with storage.open(version.storage_key, "rb") as handle:
                digest = hashlib.sha256()
                while chunk := handle.read(1 << 20):
                    digest.update(chunk)
                if digest.hexdigest() != o.sha256:
                    problems.append(f"{page.tag}/{o.block_object_id}: stored bytes do not hash")
            counts["bytes_rehashed"] += 1
    linked = {
        (str(m), k)
        for m, k in MatterSourcePage.objects.filter(matter_id__in=matter_ids)
        .annotate(lk=Lower("source_page__page_key"))
        .filter(lk__in=keys)
        .values_list("matter_id", "lk")
    }
    for (matter_id, page_key, resource_key), (page, _o) in expected.items():
        if (matter_id, page_key) in linked and (matter_id, page_key, resource_key) not in seen:
            problems.append(
                f"{page.tag}/{resource_key}: page applied but this occurrence has no row"
            )
    pending_items = len(expected) - len(seen)
    pending_links = sum(
        1
        for page in capture.pages
        for pm in page.matters
        if (pm.matter_id, page.page_key) not in linked
    )
    if require_complete and (pending_items or pending_links):
        problems.append(
            f"not complete: {pending_items} items and {pending_links} page links not applied"
        )
    foreign = (
        Document.objects.filter(matter_id__in=matter_ids)
        .filter(legacy_imports__isnull=True)
        .count()
    )
    counts["other_documents_on_these_teemad"] = foreign
    counts["applied_teema_page_links"] = len(linked)
    counts["expected_teema_page_links"] = sum(len(p.matters) for p in capture.pages)
    counts["expected_items"] = len(expected)
    counts["recorded_items"] = len(seen)
    counts["pending_items"] = pending_items
    return {
        "problems": problems,
        "counts": dict(counts),
        "manifest_sha256": capture.manifest_sha256,
    }


#: The sync-state columns that are the register's own record of a Teema. ``observed``
#: and its version/run move with every nightly comparison and are not compared.
_SYNC_FIELDS = ("matter_id", "source_id", "reference", "cohort", "applied", "applied_version")


def business_digest(matter_ids: list[str]) -> dict[str, str]:
    """Per Teema: a digest of every business row the import must leave untouched."""
    from django.forms.models import model_to_dict

    from app.legacy_import.lovable_sync_models import LovableSyncState
    from app.legacy_import.models import MatterSourceReference
    from app.matters.models import Matter

    def row(obj: Any) -> dict[str, Any]:
        values = model_to_dict(obj)
        values["updated_at"] = getattr(obj, "updated_at", None)
        return {k: str(v) for k, v in sorted(values.items())}

    states = {
        str(s["matter_id"]): {k: str(s.get(k)) for k in _SYNC_FIELDS}
        for s in LovableSyncState.objects.filter(matter_id__in=matter_ids).values(*_SYNC_FIELDS)
    }
    out = {}
    for matter in Matter.all_objects.filter(pk__in=matter_ids).order_by("pk"):
        refs = MatterSourceReference.objects.filter(matter=matter).order_by("pk")
        out[str(matter.pk)] = canonical_sha256(
            {
                "matter": row(matter),
                "sync": states.get(str(matter.pk)),
                "refs": [row(r) for r in refs],
            }
        )
    return out


# -- the request API (Agent C's «OneNote'i impordi kontroll» / «Impordi …») ----------


@dataclass
class CaptureCheck:
    """What a verification of one sealed capture found. Reads only, unless recorded."""

    path: str
    ok: bool
    checked_at: datetime
    capture_id: str = ""
    manifest_sha256: str = ""
    plan_sha256: str = ""
    problems: list[str] = field(default_factory=list)
    summary: dict[str, Any] = field(default_factory=dict)
    #: What the database already holds against this capture (``verify``); a capture
    #: not yet applied reads as pending here, never as a failure.
    readback: dict[str, Any] = field(default_factory=dict)
    #: Page key -> the state this check establishes for it (``PageState`` values).
    page_states: dict[str, str] = field(default_factory=dict)

    def as_dict(self) -> dict[str, Any]:
        out = dict(self.__dict__)
        out["checked_at"] = self.checked_at.isoformat()
        out["page_state_counts"] = dict(Counter(self.page_states.values()))
        return out


def _page_problem(plan: Plan, page: CapturePage) -> bool:
    marker = f"(page {page.tag})"
    return any(marker in problem for problem in plan.problems)


def verify_capture(path: str | Path, *, record: bool = False, actor: Any = None) -> CaptureCheck:
    """Re-hash the whole sealed capture, plan it against Juristid and read back what is held.

    ``ok`` means the seal holds and the plan has no problems: the plan digest
    (``plan_sha256``) is then what an administrator approves for the import.
    Writes nothing — with ``record``, only the per-page progress marks
    (``VERIFIED`` for a page with material to import, ``NO_CHANGES`` for a page
    the database already holds exactly, ``AWAITING_ASSOCIATION`` for a page no
    register link names, ``FAILED`` for a page a problem names).
    """
    now = timezone.now()
    try:
        capture = load_capture(Path(path), verify_all_bytes=True)
    except (CaptureError, OSError, ValueError, KeyError) as error:
        return CaptureCheck(path=str(path), ok=False, checked_at=now, problems=[str(error)])
    plan = build_plan(capture)
    readback = verify(capture)
    states: dict[str, str] = {}
    for page in capture.pages:
        if _page_problem(plan, page):
            states[page.page_key] = "FAILED"
        elif not page.matters:
            states[page.page_key] = "AWAITING_ASSOCIATION"
        elif plan.page_done(page):
            states[page.page_key] = "NO_CHANGES"
        else:
            states[page.page_key] = "VERIFIED"
    check = CaptureCheck(
        path=str(path),
        ok=not plan.problems,
        checked_at=now,
        capture_id=capture.capture_id,
        manifest_sha256=capture.manifest_sha256,
        plan_sha256=plan.digest,
        problems=list(plan.problems),
        summary=plan.summary(),
        readback={"problems": readback["problems"], "counts": readback["counts"]},
        page_states=states,
    )
    if record:
        from app.legacy_import.onenote_coverage import record_capture_marks

        record_capture_marks(capture, states, actor=actor, reason="verify_capture")
    return check


def apply_capture(
    path: str | Path,
    *,
    approved_plan_sha256: str,
    actor: Any,
    backup_set: str | None = None,
    label: str = "",
) -> ApplyReport:
    """Apply a verified capture exactly as approved: every pending page, shared ones too.

    The existing gated path (``apply``) with every gate kept: the one-off
    container's settings (``JURISTID_ONENOTE_CAPTURE_IMPORT``,
    ``REAL_DATA_ALLOWED``), the sealed manifest, and a named backup set —
    ``backup_set`` or, for the host's request runner,
    ``JURISTID_ONENOTE_BACKUP_SET``, which the runner sets after taking the
    backup. Refused unless the plan computed *now* is exactly the approved one: a
    capture or a database that moved since the approval is planned again, never
    applied from an old preview. Pages the database already holds get a
    ``NO_CHANGES`` mark.
    """
    import os

    approved = (approved_plan_sha256 or "").strip().lower()
    capture = load_capture(Path(path), verify_all_bytes=True)
    plan = build_plan(capture)
    if not approved or plan.digest != approved:
        raise CaptureError(
            "Refused: the capture's plan is not the approved one "
            f"(now {plan.digest}); verify it again and approve the new plan."
        )
    pages = select_pages(plan) + select_pages(plan, include_shared=True)
    who = getattr(actor, "pk", None)
    report = apply(
        plan,
        pages,
        operator_intent=OPERATOR_INTENT,
        expect_manifest_sha256=capture.manifest_sha256,
        expect_plan_sha256=approved,
        backup_set=backup_set
        if backup_set is not None
        else os.environ.get("JURISTID_ONENOTE_BACKUP_SET", ""),
        label=(label or f"request by {who or 'operator'}")[:200],
        actor=actor,
    )
    chosen = {p.page_key for p in pages}
    untouched = {
        p.page_key: "NO_CHANGES" for p in capture.pages if p.page_key not in chosen and p.matters
    }
    if untouched:
        from app.legacy_import.onenote_coverage import record_capture_marks

        record_capture_marks(capture, untouched, actor=actor, reason="apply_capture: already held")
    return report


# -- one section at a time (owner brief §10) -----------------------------------------

#: How a section inventory reads a page. None of them creates a Teema.
SECTION_EXACT = "EXACT"  # the capture names its Teemad and the register names exactly them
SECTION_REGISTER_NOT_IN_CAPTURE = "REGISTER_NAMES_PAGE_CAPTURE_DOES_NOT"
SECTION_CAPTURE_NOT_IN_REGISTER = "CAPTURE_NAMES_TEEMA_REGISTER_DOES_NOT"
SECTION_ARCHIVE_UNLINKED = "CATALOGUED_PAGE_UNLINKED"
SECTION_NO_MATCH = "NO_EXACT_MATCH"
NOT_A_MATTER_ROLES = frozenset(
    {"CATEGORY_OR_CONTAINER", "INDEX_OR_LIST", "BACKGROUND", "EMPTY_OR_PLACEHOLDER"}
)


def section_plan(capture: Capture, section: str) -> dict[str, Any]:
    """Inventory one section's pages in a capture and match them only by exact identity.

    A page is matched to a Teema only through the register's exact page GUID
    (``onenote_coverage.register_page_map``) or a page row Juristid already holds
    under that GUID — never a title, a reference in the text, a filename or a
    hash. Containers, indexes, background and empty pages, and pages several
    Teemad share, are listed as what they are, never as matters. No Teema is
    created and no register number is made up: an unmatched page is an exception
    for a person. The writes stay the existing gated ``apply`` of the whole
    capture's plan.
    """
    from app.legacy_import.onenote_coverage import register_page_map
    from app.legacy_import.source_pages import LegacySourcePage, MatterSourcePage, SourceSystem

    pages = [p for p in capture.pages if p.section == section]
    if not pages:
        raise CaptureError(f"the capture holds no page of section {section!r}")
    plan = build_plan(capture)
    register = register_page_map()
    keys = [p.page_key for p in pages]
    rows = {
        row.lk: row
        for row in LegacySourcePage.objects.filter(source_system=SourceSystem.ONENOTE_DESKTOP)
        .annotate(lk=Lower("page_key"))
        .filter(lk__in=keys)
    }
    linked: dict[str, set[str]] = {}
    for key, matter_id in MatterSourcePage.objects.filter(
        source_page__in=list(rows.values()), matter__deleted_at__isnull=True
    ).values_list("source_page__page_key", "matter_id"):
        linked.setdefault(key.lower(), set()).add(str(matter_id))
    parents = {p.parent_object_id for p in capture.pages if p.parent_object_id}
    out_rows = []
    exceptions = []
    counts: Counter[str] = Counter()
    for page in pages:
        row = rows.get(page.page_key)
        named = {t.matter_id for t in register.pages.get(page.page_guid, [])}
        in_capture = {pm.matter_id for pm in page.matters}
        held = linked.get(page.page_key, set())
        if row is not None and row.page_role in NOT_A_MATTER_ROLES:
            role = row.page_role
        elif page.page_object_id in parents:
            role = "CONTAINER_CANDIDATE"
        elif len(named | in_capture | held) > 1:
            role = "SHARED_DOSSIER"
        elif not page.occurrences and not any(b.get("text") for b in page.blocks):
            role = "EMPTY_CANDIDATE"
        else:
            role = "PAGE"
        if in_capture and in_capture == named:
            match = SECTION_EXACT
        elif named - in_capture:
            match = SECTION_REGISTER_NOT_IN_CAPTURE
        elif in_capture - named:
            match = SECTION_CAPTURE_NOT_IN_REGISTER
        elif held:
            match = SECTION_EXACT  # an exact link Juristid already holds
        elif row is not None:
            match = SECTION_ARCHIVE_UNLINKED
        else:
            match = SECTION_NO_MATCH
        pending = [i for i in plan.page_items(page) if not plan.item_done(i)]
        counts["match_" + match] += 1
        counts["role_" + role] += 1
        counts["occurrences"] += len(page.occurrences)
        counts["pending_items"] += len(pending)
        if match != SECTION_EXACT or role != "PAGE":
            exceptions.append(
                {
                    "page_guid": page.page_guid,
                    "match": match,
                    "role": role,
                    "action": (
                        "NOT_A_MATTER"
                        if role in NOT_A_MATTER_ROLES or role == "CONTAINER_CANDIDATE"
                        else "AWAITING_ASSOCIATION"
                    ),
                }
            )
        out_rows.append(
            {
                "page_guid": page.page_guid,
                "page_key": page.page_key,
                "title": page.title,
                "level": page.level,
                "catalogued": row is not None,
                "role": role,
                "match": match,
                "teemad_register": sorted(named),
                "teemad_capture": sorted(in_capture),
                "teemad_linked": sorted(held),
                "occurrences": len(page.occurrences),
                "pending_items": len(pending),
                "pending_by_disposition": dict(Counter(i.disposition for i in pending)),
                "done": plan.page_done(page),
            }
        )
    return {
        "section": section,
        "capture_id": capture.capture_id,
        "manifest_sha256": capture.manifest_sha256,
        "plan_sha256": plan.digest,
        "plan_problems": len(plan.problems),
        "pages": len(pages),
        "counts": dict(counts),
        "teemad_created": 0,
        "exceptions": exceptions,
        "rows": out_rows,
    }
