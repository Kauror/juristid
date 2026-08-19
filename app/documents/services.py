"""Evidence capture.

The only supported way to create evidence is ``add_evidence_version``. It
allocates the version number under a row lock on the logical Document, writes
the bytes to a key that cannot collide, and removes the stored object again if
the surrounding database work does not survive.

Ordering matters: the bytes are written **before** the row that describes them.
The alternative — row first, bytes on commit — can leave a DocumentVersion
claiming evidence that does not exist, which is an integrity hole. An orphaned
blob is recoverable and detectable; a row pointing at nothing is neither.
"""

from __future__ import annotations

import hashlib
import logging
import posixpath
import uuid
from typing import Any

from django.conf import settings
from django.core.files.base import ContentFile
from django.core.files.storage import storages
from django.db import transaction
from django.db.models import Max
from django.utils import timezone

from app.audit.enums import ChangeEventType
from app.audit.services import record_change_event
from app.core.errors import DomainError
from app.core.ids import uuid7
from app.documents.enums import DocumentRole, MalwareScanState
from app.documents.models import Document, DocumentVersion
from app.matters.models import Matter

logger = logging.getLogger(__name__)

# Business formats the department actually exchanges. Anything else is refused
# rather than stored and hoped about (master specification 15.6).
ALLOWED_EVIDENCE_MIME_TYPES: frozenset[str] = frozenset(
    {
        "application/pdf",
        "application/msword",
        "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        "application/vnd.ms-excel",
        "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        "application/vnd.ms-outlook",
        "application/vnd.openxmlformats-officedocument.presentationml.presentation",
        "message/rfc822",
        "text/plain",
        "text/csv",
        "image/png",
        "image/jpeg",
        "application/zip",
    }
)


def evidence_storage() -> Any:
    return storages[settings.EVIDENCE_STORAGE_ALIAS]


def evidence_prefix(document: Document) -> str:
    return posixpath.join(str(document.matter_id), str(document.id))


def _storage_key(document: Document, version_number: int, version_id: uuid.UUID) -> str:
    """A key that two concurrent writers cannot produce twice.

    The version number comes from a locked allocation and the version id is a
    fresh UUIDv7, so the key is unique even if a retry re-uses a number.
    """
    return posixpath.join(evidence_prefix(document), f"{version_number:04d}-{version_id}")


@transaction.atomic
def create_document(
    *,
    matter: Matter,
    title: str,
    role: str = DocumentRole.OTHER,
    created_by: Any = None,
    visibility_override: str = "",
    **extra: Any,
) -> Document:
    if not title.strip():
        raise DomainError("A document requires a title.")

    document = Document(
        matter=matter,
        title=title.strip(),
        role=role,
        created_by=created_by,
        visibility_override=visibility_override,
        **extra,
    )
    document.save()

    record_change_event(
        event_type=ChangeEventType.DOCUMENT_CREATED,
        matter=matter,
        actor=created_by,
        obj=document,
        summary=document.title[:200],
        payload={"role": document.role},
    )
    return document


@transaction.atomic
def add_evidence_version(
    *,
    document: Document,
    content: bytes,
    original_filename: str,
    mime_type: str,
    uploaded_by: Any = None,
    acquired_at: Any = None,
    source_path: str = "",
    source_url: str = "",
    source_identifier: str = "",
    sharepoint_item_version: str = "",
    malware_scan_state: str = MalwareScanState.PENDING,
    make_current: bool = True,
) -> DocumentVersion:
    """Store one immutable binary as the next version of ``document``."""
    if mime_type not in ALLOWED_EVIDENCE_MIME_TYPES:
        raise DomainError(f"MIME type {mime_type!r} is not an accepted evidence format.")
    if len(content) == 0:
        raise DomainError("Refusing to store an empty evidence file.")
    if len(content) > settings.MAX_EVIDENCE_UPLOAD_BYTES:
        raise DomainError(
            f"Evidence file exceeds the {settings.MAX_EVIDENCE_UPLOAD_BYTES} byte limit."
        )

    # Serialise version allocation for this logical document. Concurrent
    # callers queue here rather than racing to the same version number.
    locked = Document.objects.select_for_update().get(pk=document.pk)

    next_number = (locked.versions.aggregate(highest=Max("version_number"))["highest"] or 0) + 1
    version_id = uuid7()
    key = _storage_key(locked, next_number, version_id)

    storage = evidence_storage()
    if storage.exists(key):
        raise DomainError(f"Refusing to overwrite an existing stored object at {key!r}.")

    digest = hashlib.sha256(content).hexdigest()
    stored_key = storage.save(key, ContentFile(content))

    try:
        version = DocumentVersion.objects.create(
            id=version_id,
            document=locked,
            version_number=next_number,
            storage_key=stored_key,
            original_filename=original_filename[:400],
            mime_type=mime_type,
            size_bytes=len(content),
            sha256=digest,
            uploaded_by=uploaded_by,
            acquired_at=acquired_at or timezone.now(),
            source_path=source_path,
            source_url=source_url,
            source_identifier=source_identifier,
            sharepoint_item_version=sharepoint_item_version,
            malware_scan_state=malware_scan_state,
        )

        if make_current:
            locked.current_version = version
            locked.save(update_fields=["current_version", "updated_at"])

        record_change_event(
            event_type=ChangeEventType.EVIDENCE_VERSION_ADDED,
            matter=locked.matter,
            actor=uploaded_by,
            obj=version,
            summary=original_filename[:200],
            payload={
                "document": str(locked.id),
                "version": next_number,
                "sha256": digest,
                "size_bytes": len(content),
            },
        )
    except BaseException:
        # The bytes were written but the record describing them will not
        # survive, so the object must not survive either.
        _discard_stored_object(storage, stored_key)
        raise

    # Residual case: a caller that wraps this call in a larger transaction and
    # rolls back *after* it returns leaves the stored object behind. Django has
    # no rollback hook to catch that, and deferring the write to commit would
    # trade an orphaned object for a version row pointing at missing bytes,
    # which is worse. `manage.py prune_orphaned_evidence` finds and removes it.

    # Keep the in-memory object the caller passed in consistent with the row.
    if make_current:
        document.current_version = version
    return version


def _discard_stored_object(storage: Any, key: str) -> None:
    """Best-effort removal of a stored object whose record did not survive.

    A failure to delete is logged and leaves an orphan, rather than raising an
    exception that would mask the original error. `prune_orphaned_evidence`
    finds those.
    """
    try:
        storage.delete(key)
    except Exception:
        logger.exception("Could not remove orphaned evidence object %s", key)


@transaction.atomic
def set_legal_hold(
    *, document: Document, on: bool, reason: str = "", actor: Any = None
) -> Document:
    if on and not reason.strip():
        raise DomainError("A legal hold requires a written reason.")

    document.legal_hold = on
    document.legal_hold_reason = reason.strip() if on else ""
    document.legal_hold_set_at = timezone.now() if on else None
    document.legal_hold_set_by = actor if on else None
    document.save(
        update_fields=[
            "legal_hold",
            "legal_hold_reason",
            "legal_hold_set_at",
            "legal_hold_set_by",
            "updated_at",
        ]
    )
    return document
