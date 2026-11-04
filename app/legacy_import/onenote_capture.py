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
    if not matters or len({m.matter_id for m in matters}) != len(matters):
        raise CaptureError(f"page {raw['tag']}: Teema list empty or repeated")
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
    disposition: str  # PROMOTE | HOLD
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

    @property
    def digest(self) -> str:
        """The decisions only — independent of how far an apply has got."""
        return canonical_sha256(
            {
                "manifest_sha256": self.capture.manifest_sha256,
                "sha256sums_sha256": self.capture.sums_sha256,
                "importer": IMPORTER_VERSION,
                "items": [item.__dict__ for item in self.items],
                "links": sorted(
                    (pm.matter_id, page.page_key)
                    for page in self.capture.pages
                    for pm in page.matters
                ),
                "known_pages": sorted(self.known_pages),
                "problems": self.problems,
            }
        )

    def page_done(self, page: CapturePage) -> bool:
        """Every Teema linked, every (Teema, occurrence) recorded."""
        for pm in page.matters:
            if (pm.matter_id, page.page_key) not in self.linked:
                return False
            for o in page.occurrences:
                if (pm.matter_id, page.page_key, o.block_object_id) not in self.recorded:
                    return False
        return True

    def summary(self) -> dict[str, Any]:
        promote = [i for i in self.items if i.disposition == "PROMOTE"]
        hold = [i for i in self.items if i.disposition == "HOLD"]
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
    if applied:
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
        # Exactly this page, read where a link names a page — never a substring
        # match, which every link passes for the notebook GUID it carries (D5).
        if register_page_guids(link) != {page.page_guid.upper()}:
            plan.problems.append(
                f"{label}: the Teema's stored OneNote link does not name exactly this page"
            )
        plan.matter_refs[pm.matter_id] = pm.reference

    keys = [p.page_key for p in capture.pages]
    existing = {
        row.lk: row
        for row in LegacySourcePage.objects.filter(source_system=SourceSystem.ONENOTE_DESKTOP)
        .annotate(lk=Lower("page_key"))
        .filter(lk__in=keys)
        .only("pk", "page_key", "capture_id")
    }
    for page in capture.pages:
        row = existing.get(page.page_key)
        # A row this capture wrote is progress, not prior knowledge: only an earlier
        # capture's row is "known", so the digest does not move as batches complete.
        if row is not None and row.capture_id != capture.capture_id[:100]:
            plan.known_pages.append(page.page_key)
            ours = {o.block_object_id: o for o in page.occurrences}
            rows = list(LegacySourceResource.objects.filter(source_page=row))
            plan.earlier_resources[page.page_key] = sum(
                1 for r in rows if r.resource_key not in ours
            )
            for r in rows:
                o = ours.get(r.resource_key)
                if o is not None and (
                    r.sha256 != o.sha256
                    or r.size_bytes != o.size
                    or r.source_added_at != o.block_created
                ):
                    plan.problems.append(
                        f"page {page.tag}: existing resource row {r.resource_key} "
                        "disagrees with the seal"
                    )
        for pm in page.matters:
            for occurrence in page.occurrences:
                base = (pm.matter_id, page.page_key, occurrence.block_object_id, occurrence.sha256)
                if page.shared:
                    item = PlannedItem(*base, "HOLD", HOLD_SHARED_PAGE)
                elif occurrence.size == 0:
                    item = PlannedItem(*base, "HOLD", HOLD_EMPTY_FILE)
                elif occurrence.size > limit:
                    item = PlannedItem(*base, "HOLD", HOLD_TOO_LARGE)
                else:
                    item = PlannedItem(*base, "PROMOTE")
                plan.items.append(item)

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
    for item in plan.items:
        recorded_state = recorded.get((item.matter_id, item.page_key, item.resource_key))
        plan.progress["recorded_" + (recorded_state or "PENDING")] += 1
    return plan


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

    def merge(self, other: ApplyReport) -> None:
        for name in (
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
        self.matters |= other.matters

    def as_dict(self) -> dict[str, Any]:
        out = dict(self.__dict__)
        out["held"] = dict(self.held)
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
    pages = sorted(
        plan.capture.pages, key=lambda p: (min(m.reference for m in p.matters), p.page_key)
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
    try:
        for page in pages:
            if _page_complete(page, by_page.get(page.page_key, [])):
                report.pages_done_before += 1
                continue
            if batch is None:
                batch = open_batch()
                report.batch_id = str(batch.pk)
            page_report = ApplyReport()
            _write_page(
                plan.capture, page, by_page.get(page.page_key, []), batch=batch, report=page_report
            )
            report.merge(page_report)  # only after the page's transaction committed
    except BaseException as error:  # an interruption is a failure too; re-raised below
        failure = error
    finally:
        if batch is not None:
            batch.finished_at = timezone.now()
            batch.matched_count = report.documents_created
            batch.unmatched_count = sum(report.held.values())
            batch.source_row_count = report.documents_created + sum(report.held.values())
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


def _page_complete(page: CapturePage, items: list[PlannedItem]) -> bool:
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
    recorded = (
        LegacySourceResourceImport.objects.filter(
            matter_source_page__matter_id__in=ids,
            resource__resource_key__in=[o.block_object_id for o in page.occurrences],
        )
        .annotate(lk=Lower("resource__source_page__page_key"))
        .filter(lk=page.page_key)
        .count()
    )
    return recorded == len(items)


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
        resources = {
            r.resource_key: r for r in LegacySourceResource.objects.filter(source_page=record)
        }
        for occurrence in page.occurrences:
            existing = resources.get(occurrence.block_object_id)
            if existing is not None:
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
                if item.resource_key in done:
                    report.already_recorded += 1
                    continue
                occurrence, resource = by_key[item.resource_key], resources[item.resource_key]
                if item.disposition == "HOLD":
                    LegacySourceResourceImport.objects.create(
                        matter_source_page=link,
                        resource=resource,
                        import_batch=batch,
                        state=ResourceImportState.SKIPPED,
                        error_code=item.reason,
                        error_detail=_hold_detail(item.reason, page),
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


def _parent_title(capture: Capture, page: CapturePage) -> str:
    if not page.parent_object_id:
        return ""
    parent = next((p for p in capture.pages if p.page_object_id == page.parent_object_id), None)
    return parent.title if parent else ""


def _hold_detail(reason: str, page: CapturePage) -> str:
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
        label = "Märkus OneNote'is" if basis == "ATTACHED_NOTE" else "Pealkiri OneNote'is"
        lines.append(f"{label}: {text}")
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
        .select_related("matter_source_page", "resource", "document", "document_version")
        .only(
            "pk",
            "state",
            "error_code",
            "document_id",
            "document_version_id",
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
            problems.append(f"unexpected import row {row.pk}")
            continue
        if key in seen:
            problems.append(f"two import rows for {key}")
        seen.add(key)
        page, o = expected[key]
        counts["state_" + row.state] += 1
        resource = row.resource
        if resource.sha256 != o.sha256 or resource.size_bytes != o.size:
            problems.append(f"{page.tag}/{o.block_object_id}: resource hash/size differ")
        if resource.source_added_at != o.block_created:
            problems.append(
                f"{page.tag}/{o.block_object_id}: source_added_at differs from the block time"
            )
        if row.state == ResourceImportState.IMPORTED:
            version = row.document_version
            if version is None or row.document is None or version.document_id != row.document_id:
                problems.append(f"{page.tag}/{o.block_object_id}: imported without its document")
                continue
            if row.document.matter_id != row.matter_source_page.matter_id:
                problems.append(f"{page.tag}/{o.block_object_id}: document on another Teema")
            if version.sha256 != o.sha256 or version.size_bytes != o.size:
                problems.append(f"{page.tag}/{o.block_object_id}: stored version differs")
            wanted_name = (
                canonical_filename((o.filename or "").strip()[:500]) or version.original_filename
            )
            if version.original_filename != wanted_name:
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
                    if digest.hexdigest() != o.sha256:
                        problems.append(f"{page.tag}/{o.block_object_id}: stored bytes do not hash")
                counts["bytes_rehashed"] += 1
        else:
            counts["held_" + (row.error_code or "UNKNOWN")] += 1
            if row.document_id or row.document_version_id:
                problems.append(f"{page.tag}/{o.block_object_id}: a held row carries a document")
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
