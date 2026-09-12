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
from collections.abc import Sequence
from typing import Any

from django.conf import settings
from django.core.files.base import ContentFile
from django.core.files.storage import storages
from django.db import transaction
from django.db.models import Max
from django.utils import timezone

from app.audit.enums import ChangeEventType
from app.audit.services import record_change_event
from app.core.enums import validate_visibility_override
from app.core.errors import DomainError
from app.core.ids import uuid7
from app.documents.enums import DocumentRole, ExtractionState
from app.documents.models import Document, DocumentVersion
from app.matters.locks import lock_open_matter_for_business_write
from app.matters.models import Matter

logger = logging.getLogger(__name__)

# Business formats the department actually exchanges. Anything else is refused
# rather than stored and hoped about (master specification 15.6).
#
# Two lists guard two different doors, and conflating them would be a mistake in
# one direction or the other. `app/documents/uploads.py` decides what a *browser*
# may push at us and stays narrow. This one decides what the evidence store will
# hold, and the historical corpus adds formats to it — signed containers, Office
# templates, legacy word processing — that arrived from an archive whose every
# byte was hashed before this code ran. Storing them is not the same as
# accepting them from a stranger (docs/adr/0015).
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
        # -- the historical corpus ----------------------------------------
        # Preserved exactly, parsed by nothing. ASiC-E and BDoc especially:
        # unpacking a signed container to index the document inside it would
        # mean presenting the extract as the evidence, which inverts the one
        # relationship this system is built on (Stage-2D brief 24).
        "application/vnd.etsi.asic-e+zip",
        "application/x-ddoc",
        "application/vnd.openxmlformats-officedocument.spreadsheetml.template",
        "application/vnd.openxmlformats-officedocument.wordprocessingml.template",
        "application/rtf",
        "text/html",
        "application/xml",
        "application/vnd.oasis.opendocument.text",
        "application/vnd.oasis.opendocument.spreadsheet",
        "image/gif",
        "image/tiff",
        "image/bmp",
        "image/webp",
        "video/mp4",
        "application/x-mspublisher",
        "application/vnd.visio",
        "application/onenote",
        "application/octet-stream",
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
        raise DomainError("Dokument vajab pealkirja.")
    if role not in DocumentRole.values:
        raise DomainError(f"Tundmatu dokumendi roll {role!r}.")
    try:
        validate_visibility_override(visibility_override)
    except ValueError as error:
        raise DomainError(str(error)) from error

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


#: The URL schemes a working reference may point at. The same two the
#: engagement link accepts, and for the same reason: anything else is either
#: unopenable or an attempt to smuggle a script into somebody's browser.
WORKING_REFERENCE_SCHEMES = ("http", "https")


@transaction.atomic
def link_working_document(
    *,
    matter: Matter,
    title: str,
    web_url: str,
    created_by: Any = None,
    item_id: str = "",
    site_path: str = "",
) -> Document:
    """Point at a living working file, without pretending it is evidence.

    A `Document` with `role=WORKING_DOCUMENT` and a SharePoint URL, which is
    exactly what the model has always been able to hold — the only thing that
    was missing was a way for a person to create one. Nothing about it is
    evidence: no bytes are captured, no checksum exists, `current_version` stays
    null, and `has_working_document` is what the Dokumendid tab reads to render
    it dashed and marked as living rather than solid and authoritative
    (master specification 8.6, 15.3; Teema redesign §23.3).

    `item_id` is the SharePoint item identifier when somebody has it. It is
    optional because the ordinary way a lawyer gets one of these links is by
    copying it out of the browser, and demanding an internal identifier they
    would have to go and look up is how a control stops being used. What it is
    *not* is decoration: `has_working_document` reads `sharepoint_item_id`, so a
    row created without one is stamped with a deterministic placeholder derived
    from the URL rather than left to read as an evidence document with no file.
    """
    from urllib.parse import urlsplit

    clean_title = (title or "").strip()
    if not clean_title:
        raise DomainError("Töödokument vajab nime.")

    url = (web_url or "").strip()
    parts = urlsplit(url)
    if parts.scheme.lower() not in WORKING_REFERENCE_SCHEMES:
        raise DomainError("Viide peab algama http:// või https:// aadressiga.")
    if not parts.netloc:
        raise DomainError("Viide peab sisaldama veebiaadressi.")

    identifier = (item_id or "").strip() or f"url:{hashlib.sha256(url.encode()).hexdigest()[:32]}"

    return create_document(
        matter=matter,
        title=clean_title[:400],
        role=DocumentRole.WORKING_DOCUMENT,
        created_by=created_by,
        sharepoint_web_url=url[:1000],
        sharepoint_item_id=identifier[:200],
        sharepoint_site_id=(site_path or "").strip()[:200],
        sharepoint_observed_at=timezone.now(),
    )


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
    extraction_state: str = ExtractionState.PENDING,
    make_current: bool = True,
) -> DocumentVersion:
    """Store one immutable binary as the next version of ``document``.

    ``extraction_state`` is the caller's when the caller already knows. Every
    ordinary upload leaves it ``PENDING`` — nothing has read the bytes — but a
    file promoted out of `Uus teema` was read while the form was open, and
    saying so is what keeps a corpus run from reading it a second time
    (``INTAKE_READ``; app/matters/intake_staging.py, docs/adr/0072).

    There is no ``malware_scan_state`` parameter. The column still exists on
    the model and still defaults to ``PENDING``, and nothing anywhere reads it
    (app/documents/enums.py, docs/adr/0072).
    """
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
            extraction_state=extraction_state,
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


# ---------------------------------------------------------------------------
# Evidence for one exact record
# ---------------------------------------------------------------------------

#: The keyword each linkable record is named by, keyed on its model label. One
#: mapping so a caller may hand this layer a record and nothing else: the
#: alternative is every caller knowing which column its own fact lives in, which
#: is how one of them eventually files an engagement under `entry`
#: (app/documents/links.py, docs/adr/0075 §6).
LINK_FIELD_BY_MODEL: dict[str, str] = {
    "matters.Entry": "entry",
    "matters.MatterEngagement": "engagement",
    "intelligence.MatterImportantDate": "important_date",
    "intelligence.MatterEffectiveDate": "effective_date",
    "intelligence.MatterWorkVictory": "work_victory",
}


@transaction.atomic
def link_document_to_record(*, document: Document, record: Any, actor: Any = None) -> Any:
    """State that ``document`` supports ``record``. The one way a link is made.

    **The cross-Matter refusal lives here**, because it is the one place that
    can see both ends. A ``CHECK`` constraint sees a single row and cannot
    follow a foreign key, so the database can guarantee that a link names
    exactly one record and cannot guarantee that the record and the document
    belong to the same file. Refused rather than repaired: a link written to
    the wrong Matter is a disclosure, and quietly moving one end to match the
    other would be the application deciding which of the two the person meant
    (docs/adr/0075 §6).

    Idempotent on purpose. A retried save that reaches here twice with the same
    pair gets one row, because the uniqueness constraint says one document
    supports one record once.
    """
    from app.documents.links import DocumentLink

    label = type(record)._meta.label
    field = LINK_FIELD_BY_MODEL.get(label)
    if field is None:
        raise DomainError(f"Dokumenti ei saa siduda kirjega {label!r}.")

    record_matter = getattr(record, "matter_id", None)
    if record_matter is None or record_matter != document.matter_id:
        raise DomainError("Dokumendi ja kirje teema peavad olema samad.")

    link, _created = DocumentLink.objects.get_or_create(
        document=document,
        defaults={"created_by": actor},
        **{field: record},
    )
    return link


@transaction.atomic
def capture_supporting_evidence(
    *,
    matter: Matter,
    record: Any,
    uploads: Sequence[Any],
    actor: Any = None,
    role: str = DocumentRole.OTHER,
) -> list[Document]:
    """Capture every uploaded file as evidence and tie each to ``record``.

    Three canonical acts per file and one explicit relationship, in one
    transaction with whatever wrote ``record``: the ``Document``, its immutable
    ``DocumentVersion``, and the ``DocumentLink`` that says which fact these
    bytes are the evidence for.

    **All or none.** The second of three files being refused must not leave a
    fact standing that claims evidence and holds one file — so nothing here is
    caught. Validation raises ``UploadRejected``, capture raises ``DomainError``,
    and either one unwinds the caller's transaction along with the record it was
    writing (docs/adr/0075 §8).

    ``role`` stays ``OTHER`` for every caller on the Teema workspace. The button
    a file arrived through is not a business role — a PDF attached to a work
    victory is not a new kind of document — and inventing one to record where it
    came from is what the link exists to avoid (brief §23).
    """
    # Imported here rather than at module scope: `app.documents.uploads` reads
    # `ALLOWED_EVIDENCE_MIME_TYPES` from this module, so the two may not import
    # each other on the way in.
    from app.documents.uploads import read_upload

    captured: list[Document] = []
    for upload in uploads:
        accepted = read_upload(upload)
        document = create_document(
            matter=matter,
            title=accepted.filename,
            role=role,
            created_by=actor,
        )
        add_evidence_version(
            document=document,
            content=accepted.content,
            original_filename=accepted.filename,
            mime_type=accepted.mime_type,
            uploaded_by=actor,
        )
        link_document_to_record(document=document, record=record, actor=actor)
        captured.append(document)
    return captured


# ---------------------------------------------------------------------------
# The interactive boundary
# ---------------------------------------------------------------------------
#
# Everything above this line is a *primitive*: the supported way to bring bytes
# into the evidence store, whoever is bringing them and whatever state the
# Matter is in. That generality is load-bearing. `app.legacy_import.opinion_apply`
# files real historical letters onto archive Matters, `historical_apply` puts
# OneNote material there, `email_intake` lands what arrived in the mailbox, and
# the intake staging commits what somebody dropped on a Matter weeks ago. Most
# of those Matters are closed — the register is full of finished work — and a
# leaf that refused a closed Matter would break the import rather than protect
# anything (R2-02, and the same reasoning `record_engagement` states for
# `add_engagement`).
#
# The two functions below are what a *person* posts to. They are the same acts
# with one question asked first, and the question is asked under the Matter's
# row lock rather than off the instance the view arrived with, because a page
# is not a boundary: a browser that had Dokumendid open before somebody else
# closed the Matter still has the upload panel on it, and its POST reaches a
# server with no memory of which page it came from.


@transaction.atomic
def capture_evidence_on_open_matter(
    *,
    matter: Matter,
    title: str,
    role: str,
    content: bytes,
    original_filename: str,
    mime_type: str,
    actor: Any = None,
) -> Document:
    """`↑ Lae dokument` — a new file on a Matter that is still open.

    One transaction over the two primitives, so a refusal leaves neither a
    ``Document`` without its bytes nor a stored object without its row. The
    lock is taken before either of them runs, which is what makes «a refusal
    leaves nothing behind» true rather than probable.

    `Matter → Document` is a prefix of the one lock order
    (`app/matters/locks.py`): this takes the Matter's row and
    ``add_evidence_version`` takes the Document's, in that order and never the
    other way round.
    """
    locked = lock_open_matter_for_business_write(matter.pk)
    document = create_document(
        matter=locked,
        title=title,
        role=role,
        created_by=actor,
    )
    add_evidence_version(
        document=document,
        content=content,
        original_filename=original_filename,
        mime_type=mime_type,
        uploaded_by=actor,
    )
    document.refresh_from_db()
    return document


@transaction.atomic
def add_version_on_open_matter(
    *,
    document: Document,
    content: bytes,
    original_filename: str,
    mime_type: str,
    uploaded_by: Any = None,
) -> DocumentVersion:
    """A further version of an existing document, filed by a person.

    A new version is new evidence — new bytes, a new checksum, a new row that
    the Matter's file list will show — so it is the same act as an upload as
    far as closure is concerned, and it is guarded the same way. The importer's
    own re-versioning keeps ``add_evidence_version``.

    The Matter is read from the document rather than taken as an argument: the
    caller resolved the document through the reader's visibility scope, and a
    Matter passed alongside it would be a second answer to the question of
    which file this belongs to.
    """
    lock_open_matter_for_business_write(document.matter_id)
    return add_evidence_version(
        document=document,
        content=content,
        original_filename=original_filename,
        mime_type=mime_type,
        uploaded_by=uploaded_by,
    )
