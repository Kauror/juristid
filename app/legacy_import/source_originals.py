"""A held OneNote original, served read-only from its sealed capture (docs/adr/0208).

A page linked to several Teemad has its files held (`SKIPPED ·
SHARED_PAGE_AWAITING_REVIEW`, docs/adr/0205 §4): catalogued with their SHA-256
and size, no `Document`, and their bytes nowhere in the evidence store. They
exist only in the sealed capture package in the read-only source corpus
(`settings.HISTORICAL_SOURCE_ROOT`). A lawyer reading the page as a dossier
still needs to open one, so this module offers exactly that and nothing more:

* **The narrowest gate.** The route names a link and a resource, both by id.
  The link is read through `visible_links(viewer)` (docs/adr/0181: business
  readers read every live Teema, an administrator without a grant none); the
  resource must be on that link's page and have a held row *on that link*, so
  a file is reachable only from a page the reader may already open and only
  while it is held. A resource that any Teema has as a `Document` this reader
  may not read is refused too: the held original is never a way round a
  stricter document (ENG-047). Everything else is a 404.
* **No path from the request, the database or the page.** The package is
  found by scanning the corpus root (bounded depth, no symbolic links) for a
  `SEAL.json` whose manifest digest is the import row's own batch digest
  (`ImportBatch.source_snapshot_sha256`) and whose capture is the row's capture;
  the seal's `SHA256SUMS` and `manifest.json` are re-hashed against it once per
  process. The blob is `blobs/<sha256>` with the resource's own digest, which
  must be 64 lowercase hex characters and be listed in the sealed sums.
* **Proved before served.** Size and SHA-256 are checked on the very handle that
  is then streamed, so what leaves is the catalogued original.
* **Audited like every served file.** `DOCUMENT_DOWNLOADED`, source
  ``onenote_source_original``, success and failure alike, through
  `shared_gate.audit_detail` (the `source_xml` pattern). A failure is a 404 that
  names neither a path nor a key, never a 500.

Nothing is written but the audit row: no Document, no import row, no decision.
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import re
import threading
from dataclasses import dataclass
from pathlib import Path
from typing import Any, BinaryIO

from django.conf import settings
from django.contrib.auth.decorators import login_required
from django.http import FileResponse, Http404, HttpRequest
from django.shortcuts import get_object_or_404
from django.views.decorators.http import require_safe

from app.accounts import shared_gate
from app.audit.enums import SecurityEventType
from app.audit.services import record_security_event
from app.core.client_ip import client_ip
from app.core.http import content_disposition
from app.documents.models import Document
from app.legacy_import.source_pages import (
    LegacySourceResource,
    LegacySourceResourceImport,
    ResourceImportState,
)

logger = logging.getLogger(__name__)

#: The audit row's ``source``.
AUDIT_SOURCE = "onenote_source_original"

#: The only hold whose original is offered. A file over the evidence limit is
#: held too, but serving it is a different decision (size, streaming budget)
#: that nobody has made.
SERVED_HOLDS = frozenset({"SHARED_PAGE_AWAITING_REVIEW"})

#: The batches whose rows this module trusts to name a sealed capture.
CAPTURE_BATCH_SOURCE_SYSTEM = "ONENOTE_DESKTOP+CAPTURE"

#: How deep under the corpus root a package's `SEAL.json` may sit, and how many
#: directory entries one scan may look at. The production package is two levels
#: down (`onenote-desktop-capture-20261104/onenote-desktop-20261104-full-v2/`).
SCAN_DEPTH = 3
SCAN_ENTRY_BUDGET = 5000

#: Package subdirectories that hold content, never another package.
_NOT_PACKAGES = frozenset({"blobs", "pages"})

_SHA = re.compile(r"[0-9a-f]{64}")
_CHUNK = 1 << 20

NOT_FOUND = "Faili originaali ei leitud."


class OriginalUnavailable(Exception):
    """The original cannot be served; ``outcome`` is the audit row's reason."""

    def __init__(self, outcome: str) -> None:
        super().__init__(outcome)
        self.outcome = outcome


@dataclass(frozen=True)
class SealedPackage:
    root: Path
    capture_id: str
    manifest_sha256: str
    #: The blob digests `SHA256SUMS` lists as ``blobs/<sha>`` under their own name.
    blobs: frozenset[str]


_packages: dict[tuple[str, str, str], SealedPackage] = {}
_lock = threading.Lock()


def clear_cache() -> None:
    """Forget every package found (tests, and a corpus remounted under a process)."""
    with _lock:
        _packages.clear()


def _sha256_of(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(_CHUNK), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _seals(root: Path) -> list[Path]:
    """Every `SEAL.json` within `SCAN_DEPTH` of `root`, never through a link."""
    found: list[Path] = []
    budget = SCAN_ENTRY_BUDGET
    frontier = [(root, 0)]
    while frontier and budget > 0:
        directory, depth = frontier.pop(0)
        try:
            with os.scandir(directory) as entries:
                for entry in entries:
                    budget -= 1
                    if budget <= 0:
                        break
                    if entry.is_symlink():
                        continue
                    if entry.name == "SEAL.json" and entry.is_file(follow_symlinks=False):
                        found.append(Path(entry.path))
                    elif (
                        depth < SCAN_DEPTH
                        and entry.name not in _NOT_PACKAGES
                        and entry.is_dir(follow_symlinks=False)
                    ):
                        frontier.append((Path(entry.path), depth + 1))
        except OSError:
            continue
    return sorted(found)


def _open_package(
    seal_path: Path, *, capture_id: str, manifest_sha256: str
) -> SealedPackage | None:
    """The package `seal_path` seals, if it is the one asked for and the seal holds."""
    root = seal_path.parent
    try:
        seal = json.loads(seal_path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    if not isinstance(seal, dict) or seal.get("manifest_sha256") != manifest_sha256:
        return None
    sums, manifest = root / "SHA256SUMS", root / "manifest.json"
    try:
        if any(p.is_symlink() or not p.is_file() for p in (sums, manifest)):
            return None
        if _sha256_of(sums) != seal.get("sha256sums_sha256"):
            return None
        if _sha256_of(manifest) != manifest_sha256:
            return None
        sealed_capture = seal.get("capture_id")
        if not sealed_capture:
            # A seal that does not name its capture: the manifest it pins does.
            sealed_capture = json.loads(manifest.read_text(encoding="utf-8")).get("capture_id")
        if str(sealed_capture or "") != capture_id:
            return None
        blobs = set()
        for line in sums.read_text(encoding="utf-8").splitlines():
            digest, _, name = line.partition("  ")
            if name.startswith("blobs/") and name[6:] == digest and _SHA.fullmatch(digest):
                blobs.add(digest)
    except (OSError, ValueError):
        return None
    return SealedPackage(
        root=root, capture_id=capture_id, manifest_sha256=manifest_sha256, blobs=frozenset(blobs)
    )


def find_package(*, capture_id: str, manifest_sha256: str) -> SealedPackage | None:
    """The sealed package of `capture_id` whose manifest is `manifest_sha256`, or ``None``."""
    configured = getattr(settings, "HISTORICAL_SOURCE_ROOT", "") or ""
    if not configured or not capture_id or not _SHA.fullmatch(manifest_sha256 or ""):
        return None
    root = Path(configured)
    key = (str(root), capture_id, manifest_sha256)
    with _lock:
        cached = _packages.get(key)
    if cached is not None and (cached.root / "SEAL.json").is_file():
        return cached
    if not root.is_dir():
        return None
    for seal_path in _seals(root):
        package = _open_package(seal_path, capture_id=capture_id, manifest_sha256=manifest_sha256)
        if package is not None:
            with _lock:
                _packages[key] = package
            return package
    return None


def _capture_of(row: LegacySourceResourceImport) -> str:
    """The capture the row was applied from: its batch's, else its page's."""
    try:
        notes = json.loads(row.import_batch.notes or "{}") if row.import_batch else {}
    except ValueError:
        notes = {}
    recorded = notes.get("capture_id") if isinstance(notes, dict) else None
    return str(recorded or row.resource.source_page.capture_id or "")


def open_original(resource: LegacySourceResource, row: LegacySourceResourceImport) -> BinaryIO:
    """A handle on the proved original, positioned at its start. Raises `OriginalUnavailable`."""
    sha = resource.sha256 or ""
    if not _SHA.fullmatch(sha):
        raise OriginalUnavailable("resource_digest_invalid")
    if resource.size_bytes <= 0:
        raise OriginalUnavailable("resource_empty")
    batch = row.import_batch
    if batch is None or batch.source_system != CAPTURE_BATCH_SOURCE_SYSTEM:
        raise OriginalUnavailable("no_capture_batch")
    package = find_package(
        capture_id=_capture_of(row), manifest_sha256=batch.source_snapshot_sha256
    )
    if package is None:
        raise OriginalUnavailable("package_missing")
    if sha not in package.blobs:
        raise OriginalUnavailable("blob_not_sealed")
    path = package.root / "blobs" / sha
    try:
        if path.is_symlink() or not path.is_file():
            raise OriginalUnavailable("blob_missing")
        handle = path.open("rb")
    except OSError:
        raise OriginalUnavailable("blob_missing") from None
    try:
        if os.fstat(handle.fileno()).st_size != resource.size_bytes:
            raise OriginalUnavailable("size_mismatch")
        digest = hashlib.sha256()
        for chunk in iter(lambda: handle.read(_CHUNK), b""):
            digest.update(chunk)
        if digest.hexdigest() != sha:
            raise OriginalUnavailable("hash_mismatch")
        handle.seek(0)
    except OriginalUnavailable:
        handle.close()
        raise
    except OSError:
        handle.close()
        raise OriginalUnavailable("blob_unreadable") from None
    return handle


def held_row(link: Any, resource: LegacySourceResource) -> LegacySourceResourceImport | None:
    """`resource`'s held row on `link`, when its original is one this module serves."""
    return (
        LegacySourceResourceImport.objects.select_related("import_batch", "resource__source_page")
        .filter(
            matter_source_page=link,
            resource=resource,
            state=ResourceImportState.SKIPPED,
            error_code__in=SERVED_HOLDS,
            document__isnull=True,
        )
        .first()
    )


def withheld_elsewhere(resource: LegacySourceResource, viewer: Any) -> bool:
    """Some Teema has this resource as a Document `viewer` may not read."""
    made = set(
        LegacySourceResourceImport.objects.filter(
            resource=resource, document__isnull=False
        ).values_list("document_id", flat=True)
    )
    if not made:
        return False
    readable = set(
        Document.objects.visible_to(viewer).filter(pk__in=made).values_list("pk", flat=True)
    )
    return bool(made - readable)


# GET and HEAD only: a read-only download answers no other method. Without this,
# OPTIONS (which CSRF does not guard) served and audited the file like a GET.
@require_safe
@login_required
def source_original(request: HttpRequest, pk: Any, resource_pk: Any) -> FileResponse:
    """One held original of a page this reader may open, as an attachment."""
    from app.legacy_import.historical_views import visible_links

    link = get_object_or_404(visible_links(request.user), pk=pk)
    resource = get_object_or_404(
        LegacySourceResource.objects.select_related("source_page"),
        pk=resource_pk,
        source_page_id=link.source_page_id,
    )
    row = held_row(link, resource) if resource.size_bytes > 0 else None
    if row is None or withheld_elsewhere(resource, request.user):
        # Not held on this link (imported, empty at source, an earlier
        # capture's, another page's) or stricter elsewhere: nothing to serve,
        # nothing to audit — exactly the answer for an id that does not exist,
        # as `source_xml` answers a page without stored XML.
        raise Http404(NOT_FOUND)

    def audit(*, succeeded: bool, outcome: str) -> None:
        record_security_event(
            event_type=SecurityEventType.DOCUMENT_DOWNLOADED,
            actor=request.user if request.user.is_authenticated else None,
            subject=resource,
            ip_address=client_ip(request),
            user_agent=request.META.get("HTTP_USER_AGENT", ""),
            succeeded=succeeded,
            detail=shared_gate.audit_detail(
                request,
                source=AUDIT_SOURCE,
                matter=str(link.matter_id),
                sha256=resource.sha256,
                disposition="attachment",
                outcome=outcome,
            ),
        )

    try:
        handle = open_original(resource, row)
    except OriginalUnavailable as failure:
        logger.error(
            "held OneNote original unavailable link=%s resource=%s outcome=%s",
            link.pk,
            resource.pk,
            failure.outcome,
        )
        audit(succeeded=False, outcome=failure.outcome)
        raise Http404(NOT_FOUND) from None
    audit(succeeded=True, outcome="served")
    response = FileResponse(handle, content_type="application/octet-stream", as_attachment=True)
    response["Content-Disposition"] = content_disposition(
        "attachment", resource.original_filename or f"{resource.sha256[:12]}.bin"
    )
    response["Content-Length"] = str(resource.size_bytes)
    response["X-Content-Type-Options"] = "nosniff"
    response["Cache-Control"] = "private, no-store"
    return response
