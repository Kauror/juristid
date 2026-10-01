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
from app.core.web_addresses import normalize_web_address
from app.documents.enums import DocumentRole, ExtractionState
from app.documents.filenames import canonical_filename
from app.documents.limits import WORKING_DOCUMENT_URL_MAX_LENGTH
from app.documents.models import Document, DocumentVersion
from app.matters.locks import lock_open_matter_for_business_write
from app.matters.models import Matter

logger = logging.getLogger(__name__)

#: Roles `↑ Lae dokument` refuses outright, not merely leaves off its menu.
#:
#: `KODA_SUBMISSION_FINAL` — «Arvamus». A file uploaded under it was an opinion
#: nobody had said was sent, and the one control that could say so was the
#: `Dokumendid` block this set of roles was narrowed alongside. The role itself
#: is untouched: stored rows keep it, the `Roll` filter still finds them, and
#: `Koja arvamus` and the archive apply still write it — each together with the
#: send it is the evidence of (docs/adr/0061, amendment of 2026-09-27).
UPLOAD_REFUSED_ROLES: frozenset[str] = frozenset({DocumentRole.KODA_SUBMISSION_FINAL})

OPINION_UPLOAD_REFUSAL = (
    "Koja arvamus lisatakse teema lehel: Lisa teemale → Koja arvamus. "
    "Seal salvestatakse fail koos saatmise kuupäeva ja adressaatidega."
)

#: Roles a person may not file a *new* upload as, however many the model holds.
#:
#: `OUTCOME_EVIDENCE` — «Tulemuse tõend» — is a claim about what happened to a
#: proposal after Koda wrote about it, and a file is almost never that at the
#: moment somebody is uploading it. On the menu it read as a plausible tenth
#: option beside nine descriptions of what a file *is*, and picking it filed a
#: document under an assertion nobody had made.
#:
#: An exclusion from a menu and nothing else. The value stays in
#: :class:`~app.documents.enums.DocumentRole`, documents already carrying it
#: stay valid and render their stored label everywhere they always did, the
#: `Roll` filter still offers it so those documents remain findable, and no
#: migration is involved. Here rather than in the Dokumendid view since
#: docs/adr/0120, because `Muuda liiki` offers the same vocabulary the upload
#: does and one set is what keeps the two from drifting.
UPLOAD_ROLES_NOT_OFFERED: frozenset[str] = frozenset({DocumentRole.OUTCOME_EVIDENCE})

# -- Correcting a document (docs/adr/0120, UQ-12) ----------------------------
#
# What a person may do about a file once it is on the Matter, and — stated once,
# for the page that offers the act and for the service that performs it — what
# refuses each act. The evidence architecture is not touched by any of them: no
# byte is rewritten, no `DocumentVersion` is removed, and a letter that went out
# stays exactly the letter that went out.

#: A sent opinion's letter. The standing opinion is what Koda said; its file is
#: the proof of the exact words, and the way to undo a mistaken one is the
#: domain's own — withdrawing it — which is named so the reader knows what to do.
SENT_OPINION_EVIDENCE = (
    "See fail on saadetud Koja arvamuse tõend: just see fail läks välja ja jääb "
    "arvamuse juurde. Kui arvamus saadeti ekslikult või vale failiga, võta arvamus "
    "kõigepealt tagasi (Teema käik → arvamuse rida → Võta tagasi); õige arvamus "
    "registreeritakse uuena."
)
#: A superseded opinion's letter, or the chosen text of one still being prepared.
KEPT_OPINION_EVIDENCE = (
    "See fail on Koja arvamuse tõend (asendatud või koostamisel arvamus) ja jääb "
    "selle arvamuse juurde."
)
#: A withdrawn opinion's letter may leave the list, but it is not revised or
#: reclassified: it is still the record of what went out before the withdrawal.
WITHDRAWN_OPINION_EVIDENCE = (
    "See fail on tagasi võetud Koja arvamuse tõend. Saadetud faili ei parandata ega "
    "liigitata ümber; õige arvamus registreeritakse uuena."
)
DOCUMENT_UNDER_LEGAL_HOLD = "Dokumendile on seatud säilitamiskohustus, seega ei saa seda eemaldada."
DOCUMENT_ROLE_NOT_OFFERED = "Seda liiki ei saa dokumendile valida."
#: The confirmation a removal answers with, for the view's message.
DOCUMENT_REMOVED_MESSAGE = "Dokument on teema dokumentide hulgast eemaldatud."


def offered_document_roles(current: str = "") -> list[tuple[str, str]]:
    """The roles a person may file a document as — on upload, or by correcting it.

    `Muuda liiki` and `↑ Lae dokument` offer one vocabulary, so a role a person
    could upload under is a role they can correct to and nothing else is. The
    document's own stored role is kept in the list even when it is no longer
    offered (`Tulemuse tõend`, `Arvamus`), so the select can say what the file
    is now rather than silently showing something else.
    """
    return [
        (value, label)
        for value, label in DocumentRole.choices
        if value == current
        or (value not in UPLOAD_ROLES_NOT_OFFERED and value not in UPLOAD_REFUSED_ROLES)
    ]


def opinion_evidence_statuses(document: Document) -> set[str]:
    """The statuses of every opinion standing on one of this document's versions.

    `Submission.final_version` — the exact bytes a send stands on — is the one
    tie between a file and a letter the evidence architecture keeps (ADR 0040,
    DATA-001). Read from the plain manager and unscoped: this decides whether an
    act on the file may happen at all, never what a reader is shown.
    """
    from app.submissions.models import Submission

    return set(
        Submission.objects.filter(final_version__document=document).values_list("status", flat=True)
    )


def _opinion_refusal(statuses: set[str], *, withdrawn_blocks: bool) -> str:
    from app.submissions.enums import SubmissionStatus

    if SubmissionStatus.SENT in statuses:
        return SENT_OPINION_EVIDENCE
    if statuses & {SubmissionStatus.SUPERSEDED, SubmissionStatus.DRAFT}:
        return KEPT_OPINION_EVIDENCE
    if withdrawn_blocks and SubmissionStatus.WITHDRAWN in statuses:
        return WITHDRAWN_OPINION_EVIDENCE
    return ""


def new_version_refusal(document: Document, statuses: set[str] | None = None) -> str:
    """Why `Lisa uus versioon` may not add a version to this file, or "".

    A sent letter is not revised after the fact: a second version would become
    the file's *current* bytes, so the Dokumendid row badged `Arvamus` would
    open a text that never went out while the opinion itself still stands on
    the first. The opinion's own record never moves — `final_version` pins the
    exact version — but the page would contradict it.

    ``statuses`` lets the page that asks all three questions read the opinions
    once (`opinion_evidence_statuses`).
    """
    if statuses is None:
        statuses = opinion_evidence_statuses(document)
    return _opinion_refusal(statuses, withdrawn_blocks=True)


def role_change_refusal(document: Document, statuses: set[str] | None = None) -> str:
    """Why `Muuda liiki` may not reclassify this file, or ""."""
    if statuses is None:
        statuses = opinion_evidence_statuses(document)
    return _opinion_refusal(statuses, withdrawn_blocks=True)


def removal_refusal(document: Document, statuses: set[str] | None = None) -> str:
    """Why `Eemalda` may not take this file off the Matter, or "".

    **A standing opinion's letter stays** — sent, superseded, or the chosen text
    of one being prepared. A sent one names the way out: withdraw the opinion,
    after which its letter may leave the list like any mistaken upload, the
    withdrawn `Submission` keeping its `final_version` pointer and the bytes
    staying in the evidence store (`PROTECT`). **A legal hold** outlives a
    person's decision that a file does not belong, the rule deleting a Matter
    applies to its documents (`app.matters.deletion`).
    """
    if statuses is None:
        statuses = opinion_evidence_statuses(document)
    refusal = _opinion_refusal(statuses, withdrawn_blocks=False)
    if refusal:
        return refusal
    if document.legal_hold:
        return DOCUMENT_UNDER_LEGAL_HOLD
    return ""


def _locked_document(document: Document) -> Document:
    """The document's own row, re-read under a lock after the Matter's.

    `Matter → Document` is the one lock order (`app/matters/locks.py`); the
    refusals above are then asked of the row as it is now rather than as the
    page that posted saw it.
    """
    return Document._base_manager.select_for_update(no_key=True).get(pk=document.pk)


# Business formats the department actually exchanges. Anything else is refused
# rather than stored and hoped about (master specification 15.6).
#
# Two lists guard two different doors, and conflating them would be a mistake in
# one direction or the other. `app/documents/uploads.py` decides what a *browser*
# may push at us and stays narrow. This one decides what the evidence store will
# hold, and the historical corpus adds formats to it — Office templates, legacy
# word processing, the old DigiDoc envelope — that arrived from an archive whose
# every byte was hashed before this code ran. Storing them is not the same as
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
        # ASiC-E, as `.asice` and as `.bdoc`: through both doors since
        # docs/adr/0126, because the Chamber sends and receives its letters in
        # them. Preserved exactly and parsed by nothing: unpacking a signed
        # container to index the document inside it would mean presenting the
        # extract as the evidence, which inverts the one relationship this
        # system is built on (Stage-2D brief 24).
        "application/vnd.etsi.asic-e+zip",
        # -- the historical corpus ----------------------------------------
        # Preserved exactly, parsed by nothing.
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
    clean_title = (title or "").strip()
    if not clean_title:
        raise DomainError("Töödokument vajab nime.")

    # The product's one web-address rule (docs/adr/0121 §5): `https://` is
    # added to an address pasted without one, a scheme somebody typed is kept,
    # and anything that is not an http(s) host is refused in this box's words.
    # The length is this column's own check below, worded with the measured
    # length, so the shared rule is asked for none.
    url = normalize_web_address(
        web_url,
        max_length=None,
        not_a_url="Viide peab sisaldama veebiaadressi.",
        not_web_scheme="Viide peab algama http:// või https:// aadressiga.",
    )
    if not url:
        raise DomainError("Viide peab sisaldama veebiaadressi.")
    # Refuse, never shorten. This used to be `url[:1000]` on the way into the
    # column, which stored a broken link under a success message and threw away
    # the only copy of the part it cut (app/documents/limits.py).
    if len(url) > WORKING_DOCUMENT_URL_MAX_LENGTH:
        raise DomainError(
            f"Viide on liiga pikk: {len(url)} märki, lubatud on {WORKING_DOCUMENT_URL_MAX_LENGTH}."
        )

    identifier = (item_id or "").strip() or f"url:{hashlib.sha256(url.encode()).hexdigest()[:32]}"

    return create_document(
        matter=matter,
        title=clean_title[:400],
        role=DocumentRole.WORKING_DOCUMENT,
        created_by=created_by,
        sharepoint_web_url=url,
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
    #
    # `FOR NO KEY UPDATE` (ENG-027), which still makes two uploads take turns.
    # Saving the new current version fires the document's search refresh, and
    # when an earlier version has ACTIVE extracted text that refresh takes the
    # rebuild gate's shared side. A rebuild holds the gate exclusively and needs
    # `FOR KEY SHARE` on this row at COMMIT for the fragments it re-inserted;
    # plain `FOR UPDATE` blocked it and the two deadlocked (app/matters/locks.py).
    locked = Document.objects.select_for_update(no_key=True).get(pk=document.pk)

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
            original_filename=canonical_filename(original_filename),
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
    "matters.MatterExternalPosition": "external_position",
    "matters.MatterProceduralDevelopment": "procedural_development",
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

    **All or none — for the bytes as well as the rows.** The second of three
    files being refused must not leave a fact standing that claims evidence and
    holds one file, so nothing here is caught: ``UploadRejected`` and
    ``DomainError`` unwind the caller's transaction along with the record it was
    writing (docs/adr/0075 §8). And every file is validated *before the first
    one is written*. Validating inside the writing loop rolled the rows back but
    left the earlier files' bytes in the evidence store with no row naming them
    — an orphan per refused mixed selection, which `check_evidence_integrity`
    reports as damage (ENG-086). The refusal names every unusable file at once,
    the way `Uus teema` does, rather than one per attempt.

    ``role`` stays ``OTHER`` for every caller on the Teema workspace. The button
    a file arrived through is not a business role — a PDF attached to a work
    victory is not a new kind of document — and inventing one to record where it
    came from is what the link exists to avoid (brief §23).
    """
    # Imported here rather than at module scope: `app.documents.uploads` reads
    # `ALLOWED_EVIDENCE_MIME_TYPES` from this module, so the two may not import
    # each other on the way in.
    from app.documents.uploads import UploadRejected, read_upload

    accepted_files = []
    refusals: list[str] = []
    for upload in uploads:
        try:
            accepted_files.append(read_upload(upload))
        except UploadRejected as error:
            # Named only when there is more than one file to tell apart.
            name = getattr(upload, "name", "") if len(uploads) > 1 else ""
            refusals.append(f"{name} — {error}" if name else str(error))
    if refusals:
        raise UploadRejected(" ".join(refusals))

    captured: list[Document] = []
    for accepted in accepted_files:
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

    **Not the Chamber's opinion.** A file filed here as `Arvamus` said Koda held
    it and nothing about a send, and the only thing that could finish it was
    `Registreeri saatmine` on `Dokumendid` — which is retired as a way in. So the
    role is refused before anything is locked or written, and the refusal names
    the door that records an opinion properly: `Lisa teemale → Koja arvamus`,
    which stores the bytes and the canonical send in one transaction. Refused
    here rather than only left off the menu, because a browser holding the old
    page still posts the old value (docs/adr/0061, amendment of 2026-09-27).
    """
    if role in UPLOAD_REFUSED_ROLES:
        raise DomainError(OPINION_UPLOAD_REFUSAL)
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

    **`Lisa uus versioon` on Dokumendid** (docs/adr/0120, UQ-12). The corrected
    file becomes the document's current version and every earlier one stays,
    immutable, in its version history — one document, not a second row with
    the same name. Refused for an opinion's letter (`new_version_refusal`) and
    for a file taken off the Matter, both under the locks.
    """
    lock_open_matter_for_business_write(document.matter_id)
    locked = _locked_document(document)
    if locked.is_removed:
        raise DomainError(DOCUMENT_ALREADY_REMOVED)
    refusal = new_version_refusal(locked)
    if refusal:
        raise DomainError(refusal)
    return add_evidence_version(
        document=document,
        content=content,
        original_filename=original_filename,
        mime_type=mime_type,
        uploaded_by=uploaded_by,
    )


DOCUMENT_ALREADY_REMOVED = "See dokument on teemalt juba eemaldatud."


@transaction.atomic
def change_document_role(*, document: Document, role: str, actor: Any = None) -> Document:
    """`Muuda liiki` — what the file *is*, corrected (docs/adr/0120, UQ-12).

    Metadata only: `Document.role` changes and nothing about a version does —
    the bytes, the checksum and the version history are exactly what they were.
    The vocabulary is the upload's (`offered_document_roles`), so `Arvamus`
    stays reachable only through `Koja arvamus`, which records the send with
    it; an opinion's own letter is not reclassified (`role_change_refusal`).

    Audited as `DOCUMENT_ROLE_CHANGED`, old and new value in the payload. The
    same role again writes nothing and returns the row unchanged, so a double
    submit is not a second event. The search row follows on the save
    (`app.search.signals.refresh_on_document_change`).
    """
    matter = lock_open_matter_for_business_write(document.matter_id)
    locked = _locked_document(document)
    if locked.is_removed:
        raise DomainError(DOCUMENT_ALREADY_REMOVED)
    if role not in {value for value, _label in offered_document_roles(locked.role)}:
        if role in UPLOAD_REFUSED_ROLES:
            raise DomainError(OPINION_UPLOAD_REFUSAL)
        raise DomainError(DOCUMENT_ROLE_NOT_OFFERED)
    if role == locked.role:
        return locked
    refusal = role_change_refusal(locked)
    if refusal:
        raise DomainError(refusal)
    previous = locked.role
    locked.role = role
    locked.save(update_fields=["role", "updated_at"])
    record_change_event(
        event_type=ChangeEventType.DOCUMENT_ROLE_CHANGED,
        matter=matter,
        actor=actor,
        obj=locked,
        summary=locked.title[:200],
        payload={"from": previous, "to": role},
    )
    return locked


@transaction.atomic
def remove_document(*, document: Document, actor: Any = None) -> Document:
    """`Eemalda dokument` — a mistaken upload comes off the Matter (docs/adr/0120).

    ADR 0102's removal, extended to the file itself: `removed_at` and
    `removed_by` are set and nothing is destroyed. Every `DocumentVersion`
    stays, and so do its bytes in the immutable store; `DocumentLink` rows stay
    and stop reading (`DocumentLink.visible_to`); the `ChangeEvent`s that said
    the file arrived stay in `Kõik muudatused`, and `DOCUMENT_REMOVED` joins
    them. What changes is every business read: `Document.objects.visible_to`
    drops the row, so the Dokumendid list, the count on its tab, the files under
    `Teema käik` rows, downloads through the product and search all stop
    offering it — the search rows are withdrawn by the save's own signal.

    Refused, under the Matter's and then the document's lock, for the reasons
    `removal_refusal` states — above all while an opinion stands on it. On a
    closed Matter the lock itself refuses, as it does every change to the file
    (docs/adr/0076). Removing a removed document is not an error and writes
    nothing.
    """
    matter = lock_open_matter_for_business_write(document.matter_id)
    locked = _locked_document(document)
    if locked.is_removed:
        return locked
    refusal = removal_refusal(locked)
    if refusal:
        raise DomainError(refusal)
    locked.removed_at = timezone.now()
    locked.removed_by = actor if getattr(actor, "pk", None) is not None else None
    locked.save(update_fields=["removed_at", "removed_by", "updated_at"])
    record_change_event(
        event_type=ChangeEventType.DOCUMENT_REMOVED,
        matter=matter,
        actor=actor,
        obj=locked,
        summary=locked.title[:200],
        payload={
            "role": locked.role,
            "versions": locked.versions.count(),
            "current_sha256": locked.current_version.sha256 if locked.current_version else "",
        },
    )
    return locked
