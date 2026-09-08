"""Staging a file on `Uus teema`, and promoting it when the Teema is created.

Four operations and nothing else: **stage** validated bytes so the extractor
may read them, **remove** one the person took back off, **promote** what is
left into real evidence when `Loo teema` succeeds, and **sweep** what nobody
came back for. There is no editing, no versioning, no sharing and no listing
across people — a staged file is one person's unfinished form, and every
capability this module does not have is a capability it cannot leak
(docs/adr/0064).

Two properties are load-bearing.

**Nothing here is a business write.** Staging creates no Matter, no Document,
no DocumentVersion, no ChangeEvent, no search row and no Organisation. That is
not a convention this module tries to honour; it is a consequence of what it
imports, which is `app.documents.uploads` for the validator and a storage
backend for the bytes. `promote_intake_files` is the one function that writes
anything canonical, it is called from inside the Matter's own transaction, and
it does its writing through the ordinary services — the same
``create_document`` and ``add_evidence_version`` that a file uploaded a month
later goes through.

**The bytes that reach evidence are the bytes that arrived.** They are read
back from staging at promotion and hashed again, and a mismatch refuses the
save rather than storing something that is nearly the letter a ministry sent.
Nothing anywhere reconstructs a document from its extracted text, and the
extracted text is not carried across at all.
"""

from __future__ import annotations

import hashlib
import logging
import uuid
from dataclasses import dataclass
from datetime import timedelta
from typing import Any

from django.conf import settings
from django.core.files.base import ContentFile
from django.core.files.storage import storages
from django.db import transaction
from django.db.models import Max
from django.utils import timezone

from app.core.errors import DomainError
from app.core.ids import uuid7
from app.documents.enums import DocumentRole
from app.documents.services import add_evidence_version, create_document
from app.documents.uploads import AcceptedUpload
from app.matters.intake import MAX_INTAKE_FILES, role_for
from app.matters.staging import MatterIntakeFile, MatterIntakeSession

logger = logging.getLogger(__name__)

#: Everything staged sits under this prefix inside the held-uploads storage.
#:
#: The same storage class, deliberately: both are a form's unsaved working
#: state, neither describes anything, and both are cheap to lose (docs/adr/0014,
#: `app.documents.pending`). One deployment concern rather than two, one
#: directory to leave out of a backup rather than two.
#:
#: The prefix is not cosmetic. ``pending._sweep`` deletes objects at the *root*
#: of that store by age, and it lists names rather than walking the tree — so
#: staged bytes sit one directory down, out of its reach, and are swept by
#: :func:`sweep_stale_sessions` from the rows that describe them instead. A test
#: holds that boundary (`tests/test_intake_staging.py`).
STORAGE_PREFIX = "intake"


def staging_storage() -> Any:
    return storages[settings.PENDING_UPLOAD_STORAGE_ALIAS]


def _expiry() -> Any:
    return timezone.now() + timedelta(hours=settings.PENDING_UPLOAD_GRACE_HOURS)


# ---------------------------------------------------------------------------
# Reading
# ---------------------------------------------------------------------------


def _as_uuid(value: Any) -> uuid.UUID | None:
    """A caller's identifier, or ``None`` if it is not one.

    Parsed here rather than left to the queryset. Handing a malformed string to
    a UUID column raises, and a 500 is a different answer from a 404 — which is
    exactly the difference a caller probing for what exists would read
    (`app.core.decorators`, task §23).
    """
    try:
        return uuid.UUID(str(value))
    except (ValueError, AttributeError, TypeError):
        return None


def get_session(*, owner: Any, session_id: Any) -> MatterIntakeSession | None:
    """One usable staging session belonging to this person, or ``None``.

    Fail-closed and silent about which of the four reasons applies: an
    identifier that is not one, a session that is somebody else's, one that has
    expired and one that a Matter has already consumed all answer the same way.
    The caller turns ``None`` into the project's 404, so a guessed identifier
    learns nothing — not even whether it named a row.
    """
    parsed = _as_uuid(session_id)
    if parsed is None:
        return None
    return MatterIntakeSession.objects.owned_by(owner).usable().filter(pk=parsed).first()


def live_files(session: MatterIntakeSession) -> list[MatterIntakeFile]:
    """The files still on the form, in the order they were offered."""
    return list(session.files.live().in_order())


# ---------------------------------------------------------------------------
# Staging
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class StagingResult:
    session: MatterIntakeSession
    staged: tuple[MatterIntakeFile, ...]
    #: The first refusal, in words meant for the person who chose the file.
    #: Every file is read, so one bad choice among four names itself
    #: immediately rather than one save at a time (`_read_new_matter_files`).
    refusal: str = ""


@transaction.atomic
def stage_uploads(
    *, owner: Any, uploads: list[AcceptedUpload], session: MatterIntakeSession | None = None
) -> StagingResult:
    """Keep these validated files so the extractor may read them.

    ``uploads`` have already been through ``read_upload`` — size, extension
    allowlist and content signature — and this function does not repeat that
    work or weaken it. What it adds is the checksum, the ordinal and the row
    the extraction worker claims.

    The bytes are written before the row that describes them, which is the
    ordering ``add_evidence_version`` uses and for the same reason: a row
    pointing at an object that is not there is undetectable, and an object no
    row points at is swept.
    """
    session = session or MatterIntakeSession.objects.create(owner=owner, expires_at=_expiry())

    existing = session.files.count()
    next_ordinal = (session.files.aggregate(top=Max("ordinal"))["top"] or 0) + 1
    storage = staging_storage()
    staged: list[MatterIntakeFile] = []
    refusal = ""

    for upload in uploads:
        if existing + len(staged) >= MAX_INTAKE_FILES:
            # The same ceiling `validate_uploads` enforces, applied to the
            # running total rather than to one request, because staging accepts
            # files a few at a time. Said rather than silently dropped: a file
            # that vanished without a word is the defect the held-upload work
            # was about.
            if not refusal:
                refusal = f"Korraga saab lisada kuni {MAX_INTAKE_FILES} faili."
            break

        key = storage.save(f"{STORAGE_PREFIX}/{session.pk}/{uuid7()}", ContentFile(upload.content))
        staged.append(
            MatterIntakeFile.objects.create(
                session=session,
                ordinal=next_ordinal + len(staged),
                storage_key=key,
                original_filename=upload.filename,
                mime_type=upload.mime_type,
                size_bytes=len(upload.content),
                sha256=hashlib.sha256(upload.content).hexdigest(),
                role=role_for(upload.filename),
            )
        )

    return StagingResult(session=session, staged=tuple(staged), refusal=refusal)


def remove_file(*, session: MatterIntakeSession, file_id: Any) -> bool:
    """Take one file back off the form. Returns whether anything was there.

    Stamped rather than deleted, and the bytes stay until the session is swept.
    That is what keeps the browser and the server agreeing: a row that is
    ``removed`` is absent from the list, absent from the analysis and absent
    from what `Loo teema` promotes, and there is no window in which it is
    absent from one of the three and present in another.
    """
    parsed = _as_uuid(file_id)
    if parsed is None:
        return False
    updated = (
        session.files.live()
        .filter(pk=parsed)
        .update(removed_at=timezone.now(), updated_at=timezone.now())
    )
    return bool(updated)


# ---------------------------------------------------------------------------
# Promotion
# ---------------------------------------------------------------------------


def promote_intake_files(
    *, session: MatterIntakeSession, matter: Any, actor: Any
) -> list[MatterIntakeFile]:
    """Every file still on the form becomes one Document with one version.

    Called from inside `matter_create`'s transaction, so a refusal anywhere
    after it takes the Documents with it and there is never a Matter carrying
    half an envelope.

    **One file, one Document**, exactly as `Saabunud` does it and through the
    same two services. The filename, the MIME type and the role are the ones
    recorded at upload; the checksum is recomputed from the bytes read back and
    compared, so the version that is written is provably the file the browser
    sent.

    **Nothing derived is carried across.** The staged text stays in staging and
    the new version is queued for extraction like any other, which is the
    deliberate half of this design and is argued in docs/adr/0064: the
    canonical publish path also writes attachment Documents, derivative
    binaries and the search projection, and reusing a staged parse would mean
    either duplicating that against a second source of truth or promoting a
    derivative that later readers assume is complete. One extra parse of a file
    the department uploads once is the cheaper mistake.
    """
    storage = staging_storage()
    promoted: list[MatterIntakeFile] = []

    for staged in session.files.live().in_order():
        try:
            with storage.open(staged.storage_key, "rb") as handle:
                content = handle.read()
        except (FileNotFoundError, OSError) as error:
            # The bytes are the point of the whole feature, so this refuses the
            # save rather than creating a Matter that is missing a document
            # nobody will notice is missing. The person still has the file.
            raise DomainError(
                f"Faili „{staged.original_filename}” ei õnnestunud lugeda. Vali see uuesti."
            ) from error

        digest = hashlib.sha256(content).hexdigest()
        if digest != staged.sha256:
            # Not reachable by any ordinary route — nothing rewrites a staged
            # object — which is exactly why it is checked. Silent corruption of
            # evidence is the one failure this system may not have.
            raise DomainError(
                f"Faili „{staged.original_filename}” sisu on muutunud. Vali see uuesti."
            )

        document = create_document(
            matter=matter,
            title=staged.original_filename,
            role=staged.role or DocumentRole.INCOMING_AUTHORITY,
            created_by=actor,
        )
        add_evidence_version(
            document=document,
            content=content,
            original_filename=staged.original_filename,
            mime_type=staged.mime_type,
            uploaded_by=actor,
        )
        promoted.append(staged)

    return promoted


def consume_session(session: MatterIntakeSession) -> None:
    """The Teema exists, so the staging is over: stamp it and drop the bytes.

    Called after the commit rather than inside it. Deleting a staged object is
    not part of the business operation, and a storage backend having a bad
    minute must not take a written Matter with it — the sweeper will find
    anything left behind.
    """
    MatterIntakeSession.objects.filter(pk=session.pk, consumed_at__isnull=True).update(
        consumed_at=timezone.now(), updated_at=timezone.now()
    )
    _discard_objects(session)


def _discard_objects(session: MatterIntakeSession) -> int:
    """Delete this session's stored bytes, best effort. Rows are untouched."""
    storage = staging_storage()
    removed = 0
    for key in session.files.exclude(storage_key="").values_list("storage_key", flat=True):
        try:
            storage.delete(key)
            removed += 1
        except OSError:  # pragma: no cover - best effort
            logger.warning("could not delete staged intake object %s", key, exc_info=True)
    return removed


# ---------------------------------------------------------------------------
# Lifecycle
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class SweepReport:
    sessions: int
    files: int
    objects: int


def sweep_stale_sessions(*, limit: int = 500) -> SweepReport:
    """Delete staging nobody will ask for again: expired, or already consumed.

    Rows drive this, not a directory listing, and that is the difference
    between this and ``pending._sweep``. A held upload is a bare object with no
    row anywhere, so age on disk is the only fact available about it; a staged
    file has a row that says who owns it, when it expires and whether a Matter
    took it. Deciding from the row means the sweeper can never delete an object
    a live session is still pointing at, whatever the clock on the filesystem
    says.

    Bytes first, then rows. An object outliving its row is a few kilobytes in a
    store that is not backed up; a row outliving its object is a form that
    offers somebody a file which is no longer there.

    Deliberately *not* scheduled by this change. It is a management command an
    operator runs, and `deploy/unraid-main/RECOVERY.md` says so — putting a
    deletion loop into a production timer is a separate decision from writing
    one (task §25).
    """
    stale = list(MatterIntakeSession.objects.stale().order_by("expires_at")[:limit])
    if not stale:
        return SweepReport(sessions=0, files=0, objects=0)

    objects = 0
    files = 0
    for session in stale:
        objects += _discard_objects(session)
        files += session.files.count()

    MatterIntakeSession.objects.filter(pk__in=[session.pk for session in stale]).delete()
    logger.info("swept %d stale intake sessions (%d files, %d objects)", len(stale), files, objects)
    return SweepReport(sessions=len(stale), files=files, objects=objects)
