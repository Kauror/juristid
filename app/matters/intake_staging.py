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
import os
import uuid
from dataclasses import dataclass, field
from datetime import timedelta
from pathlib import Path
from typing import Any

from django.conf import settings
from django.core.files.base import ContentFile
from django.core.files.storage import storages
from django.db import transaction
from django.db.models import Exists, Max, OuterRef, Q, Sum
from django.db.models.functions import Coalesce, Least
from django.utils import timezone

from app.core.errors import DomainError
from app.core.ids import uuid7
from app.documents import pending
from app.documents.enums import ExtractionState
from app.documents.uploads import AcceptedUpload
from app.matters.intake import MAX_INTAKE_FILES, file_incoming, role_for
from app.matters.intake_suggestions.availability import create_form_suggestions_offered
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
#: :func:`sweep_stale_sessions`, one selected session prefix at a time. A test
#: holds that boundary (`tests/test_intake_staging.py`).
STORAGE_PREFIX = "intake"


def staging_storage() -> Any:
    return storages[settings.PENDING_UPLOAD_STORAGE_ALIAS]


#: The advisory-lock namespace for staging quotas, in the two-integer form
#: `app.search.indexing` (24601), `app.organisations.services` (24602) and
#: `app.matters.duplicate_intake` (24603) use. One key: the quotas are checked
#: and the bytes written under a single deployment-wide lock (docs/adr/0177).
STAGING_QUOTA_LOCK = pending.STORAGE_QUOTA_LOCK

#: What the person is told when staging is full. The global sentence is one
#: sentence for three causes — other people's forms, the deployment's ceiling
#: and the disk itself — because which of them it was is the operator's to know
#: (logged), not something a refusal should describe to whoever asks.
OWNER_SESSIONS_FULL = (
    "Sul on juba {limit} pooleli «Uus teema» vormi, kuhu on faile lisatud. "
    "Lõpeta mõni neist või oota, kuni see aegub, ja lisa fail siis uuesti."
)
OWNER_BYTES_FULL = (
    "Sinu pooleli vormidele lisatud failide maht on täis (kuni {limit_mb} MB). "
    "Lõpeta pooleli teema või proovi mõne tunni pärast uuesti."
)
STAGING_FULL = (
    "Failide ajutine hoidla on praegu täis. Proovi hiljem uuesti; kui see kordub, "
    "anna teada süsteemi haldajale."
)


@dataclass(frozen=True)
class StagingUsage:
    """What staging holds now, for one person and for everybody."""

    owner_active_sessions: int
    owner_bytes: int
    active_sessions: int
    bytes: int


def _holding_files() -> Any:
    return Exists(MatterIntakeFile.objects.filter(session=OuterRef("pk")))


def staging_usage(*, owner: Any, excluding: Any = None) -> StagingUsage:
    """Counted from the rows, which is what makes the answer transactional.

    *Active* is a form that can still be used and already holds a file: an
    empty render is a row and no bytes (`open_session` makes one per render),
    and counting those would refuse somebody for having reloaded a page.
    *Bytes* are every staged file of a form not yet consumed — taken back off
    the form and expired ones included, because their bytes stay on the volume
    until the sweep removes them. A consumed form's bytes are deleted after its
    commit (`consume_session`), so they are not counted.

    ``excluding`` is the form the caller is staging into, so that a form is not
    counted against itself when it becomes active.
    """
    active = MatterIntakeSession.objects.usable().filter(_holding_files())
    if excluding is not None:
        active = active.exclude(pk=excluding.pk)
    unconsumed = MatterIntakeFile.objects.filter(session__consumed_at__isnull=True)

    def total(files: Any) -> int:
        return int(files.aggregate(total=Coalesce(Sum("size_bytes"), 0))["total"])

    return StagingUsage(
        owner_active_sessions=active.filter(owner=owner).count(),
        owner_bytes=total(unconsumed.filter(session__owner=owner)),
        active_sessions=active.count(),
        bytes=total(unconsumed),
    )


def staging_free_bytes(storage: Any = None) -> int | None:
    """Free space on the staging volume, or ``None`` for a non-filesystem store."""
    return pending.free_bytes(storage or staging_storage())


def quota_refusal(*, owner: Any, session: MatterIntakeSession | None, incoming_bytes: int) -> str:
    """The refusal for staging ``incoming_bytes`` more into ``session``, or ``""``.

    Authoritative only under `STAGING_QUOTA_LOCK`, which `stage_uploads` holds;
    the upload view also asks it once, unlocked, with the sizes the request
    *declared*, so a request that cannot fit is answered before its files are
    read (csf_3942255b466a4e213b958b16).
    """
    if incoming_bytes <= 0:
        return ""
    usage = staging_usage(owner=owner, excluding=session)
    becomes_active = session is None or not session.files.exists()

    if becomes_active and usage.owner_active_sessions >= (
        settings.INTAKE_STAGING_MAX_ACTIVE_SESSIONS_PER_OWNER
    ):
        return OWNER_SESSIONS_FULL.format(
            limit=settings.INTAKE_STAGING_MAX_ACTIVE_SESSIONS_PER_OWNER
        )
    if usage.owner_bytes + incoming_bytes > settings.INTAKE_STAGING_MAX_BYTES_PER_OWNER:
        return OWNER_BYTES_FULL.format(
            limit_mb=settings.INTAKE_STAGING_MAX_BYTES_PER_OWNER // (1024 * 1024)
        )

    reason = ""
    if becomes_active and usage.active_sessions >= settings.INTAKE_STAGING_MAX_ACTIVE_SESSIONS:
        reason = f"active sessions {usage.active_sessions}"
    elif usage.bytes + incoming_bytes > settings.INTAKE_STAGING_MAX_BYTES:
        reason = f"staged bytes {usage.bytes} + {incoming_bytes}"
    else:
        free = staging_free_bytes()
        if free is not None and free - incoming_bytes < settings.INTAKE_STAGING_MIN_FREE_BYTES:
            reason = f"free bytes {free} - {incoming_bytes}"
    if reason:
        logger.warning("intake staging refused at the deployment limit: %s", reason)
        return STAGING_FULL
    return ""


def _lock_staging_quotas() -> None:
    # The same lock `app.documents.pending.hold` takes: both write to one volume.
    pending.lock_storage_quota()


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


def open_session(*, owner: Any) -> MatterIntakeSession:
    """A fresh session for a freshly rendered `Uus teema` form.

    Every render gets one, not only a render where somebody staged a file,
    because the session is also the form's **one-time submission token**
    (ENG-074): `Loo teema` consumes it in the transaction that creates the
    Teema, so the same form posted twice — a retried request, a replayed POST —
    finds it consumed and is answered with the Teema the first one created. A
    new form is a new session, so creating a second Teema on purpose, even with
    the same words, is always possible: this is idempotency per form, never
    deduplication by content.

    A render that is never submitted leaves a row that expires and is swept
    with every other stale session (`sweep_stale_sessions`). It holds no form
    text — an owner, an expiry, and later a consumption stamp and the Teema.
    """
    return MatterIntakeSession.objects.create(owner=owner, expires_at=_expiry())


@dataclass(frozen=True)
class CreateClaim:
    """What `Loo teema` learned from the form token it was posted with.

    ``session`` is set when the token named a session of this person's:
    either just claimed by this request (``replayed`` false) or already
    consumed by an earlier submission of the same form (``replayed`` true).
    ``None`` is a POST that named no session of theirs at all.
    """

    session: MatterIntakeSession | None
    replayed: bool = False


def claim_for_create(*, owner: Any, session_id: Any) -> CreateClaim:
    """Consume this form's session, atomically, or report that it already was.

    Must run inside the transaction that creates the Teema, and before anything
    else is written — in particular before a reference number is allocated. One
    conditional ``UPDATE ... WHERE consumed_at IS NULL``: two submissions of the
    same form race on that row, the second waits for the first to commit, finds
    the condition false and claims nothing, and so writes nothing either. If the
    first rolls back, its claim goes with it and the second proceeds. No
    read-then-write, and nothing held in the web session.

    Expiry does not stop a claim. An expired session no longer offers its staged
    files — that is `get_session`'s rule, unchanged — but it is still this
    form's token, and a form left open past the grace period must still be
    saveable exactly once.
    """
    parsed = _as_uuid(session_id)
    if parsed is None:
        return CreateClaim(session=None)
    now = timezone.now()
    mine = MatterIntakeSession.objects.owned_by(owner).filter(pk=parsed)
    if mine.filter(consumed_at__isnull=True).update(consumed_at=now, updated_at=now):
        return CreateClaim(session=mine.get())
    existing = mine.first()
    if existing is not None and existing.consumed_at is not None:
        return CreateClaim(session=existing, replayed=True)
    return CreateClaim(session=None)


def record_created_matter(*, session: MatterIntakeSession, matter: Any) -> None:
    """Remember which Teema this form created, for a repeated submission to find."""
    MatterIntakeSession.objects.filter(pk=session.pk).update(matter=matter)


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

    **The session row is locked first** (``FOR NO KEY UPDATE``), and the
    count and the next ordinal are read under that lock. Two uploads into one
    form — a second tab, a retry — used to read the same ``max(ordinal)``, and
    the loser hit the unique constraint as a 500 with its bytes already written
    (ENG-087). Now the second waits for the first and numbers after it.

    The bytes are written before the row that describes them, which is the
    ordering ``add_evidence_version`` uses and for the same reason: a row
    pointing at an object that is not there is undetectable. If anything here
    fails after a write, every object this call wrote is discarded before the
    error propagates. What still escapes — a rollback *after* this returns —
    sits under the session's own prefix, and the sweeper removes that prefix
    whole (:func:`_discard_objects`).

    **Quotas before bytes** (csf_3942255b466a4e213b958b16, docs/adr/0177). The
    twenty-file cap is per form, and a form is one render, so it bounded
    nothing on its own. Every stager first takes one deployment-wide advisory
    lock (`STAGING_QUOTA_LOCK`), then the session row; under both it counts what
    staging already holds — per person and in total — checks the free space on
    the volume, and only then writes. The rows this transaction inserts are the
    reservation: the next stager waits on the lock until they are committed and
    counts them, and a write that fails takes them with it on rollback, its
    bytes discarded below. A request that does not fit writes nothing and is
    answered with a sentence, like the twenty-file cap.
    """
    _lock_staging_quotas()
    session = session or MatterIntakeSession.objects.create(owner=owner, expires_at=_expiry())
    MatterIntakeSession.objects.select_for_update(no_key=True).filter(pk=session.pk).exists()

    existing = session.files.count()
    next_ordinal = (session.files.aggregate(top=Max("ordinal"))["top"] or 0) + 1
    refusal = ""
    room = max(0, MAX_INTAKE_FILES - existing)
    if len(uploads) > room:
        # The same ceiling `validate_uploads` enforces, applied to the running
        # total rather than to one request, because staging accepts files a few
        # at a time. Said rather than silently dropped: a file that vanished
        # without a word is the defect the held-upload work was about.
        refusal = f"Korraga saab lisada kuni {MAX_INTAKE_FILES} faili."
        uploads = uploads[:room]
    if uploads:
        over = quota_refusal(
            owner=owner,
            session=session,
            incoming_bytes=sum(len(upload.content) for upload in uploads),
        )
        if over:
            return StagingResult(
                session=session, staged=(), refusal=" ".join(filter(None, (refusal, over)))
            )

    storage = staging_storage()
    staged: list[MatterIntakeFile] = []
    written: list[str] = []
    try:
        _stage_each(session, uploads, next_ordinal, storage, staged, written)
    except BaseException:
        for key in written:
            _discard_key(storage, key)
        raise

    return StagingResult(session=session, staged=tuple(staged), refusal=refusal)


def _stage_each(
    session: MatterIntakeSession,
    uploads: list[AcceptedUpload],
    next_ordinal: int,
    storage: Any,
    staged: list[MatterIntakeFile],
    written: list[str],
) -> None:
    """Write each file and its row; ``written`` records every key before it lands.

    Before, not after: a save that fails half-way leaves a partial object under
    the name it was given and returns nothing, and the caller can only discard
    what it was told about.
    """
    for upload in uploads:
        name = f"{STORAGE_PREFIX}/{session.pk}/{uuid7()}"
        written.append(name)
        key = storage.save(name, ContentFile(upload.content))
        if key != name:  # pragma: no cover - a uuid7 name is never taken
            written.append(key)
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


#: What the intake reader's verdict on a staged file becomes on the canonical
#: version promoted from it **while the form offers what the reader found**.
#: Anything not named here — ``PENDING`` and ``PROCESSING``, the two states
#: meaning "nobody has read this yet" — falls through to ``PENDING``, which is
#: the only honest reading of a binary the reader never reached
#: (docs/adr/0072).
#:
#: ``DONE`` is the one row that depends on the form, and
#: :func:`promoted_extraction_state` is where that is decided (D-6, AUD-025).
PROMOTED_EXTRACTION_STATE: dict[str, str] = {
    ExtractionState.DONE: ExtractionState.INTAKE_READ,
    ExtractionState.NOT_APPLICABLE: ExtractionState.NOT_APPLICABLE,
    ExtractionState.FAILED: ExtractionState.FAILED,
}


def promoted_extraction_state(staged_state: str, *, suggestions_offered: bool | None = None) -> str:
    """The canonical version's extraction state, from the staged file's.

    **``INTAKE_READ`` only when the reading reached the form.** That state says
    *read on the form; the answers went onto it; nothing owed* — and it is
    terminal, so `pending_versions` never offers the version again and no text
    or search row is ever made from it. Both halves are acceptable only because
    the first half is true. With the suggestions withdrawn (docs/adr/0088) the
    reader still reads the staged file, but nothing it found is shown to
    anybody: no panel, no pre-fill. Recording ``INTAKE_READ`` then would claim a
    reading the person never received *and* would make the document
    unsearchable by content for good, on the strength of that claim. So a
    staged ``DONE`` is promoted ``PENDING`` instead — not extracted canonically
    yet, owed to the ordinary worker like any other upload (AUD-025, D-6).

    ``suggestions_offered`` is the caller's when it has already asked; ``None``
    asks the same predicate the create form asks
    (`create_form_suggestions_offered`), at promotion time. ``FAILED`` and
    ``NOT_APPLICABLE`` are verdicts about the bytes rather than about the form,
    and carry across either way.
    """
    promoted = PROMOTED_EXTRACTION_STATE.get(staged_state, ExtractionState.PENDING)
    if promoted == ExtractionState.INTAKE_READ:
        if suggestions_offered is None:
            suggestions_offered = create_form_suggestions_offered()
        if not suggestions_offered:
            return ExtractionState.PENDING
    return promoted


def promoted_extraction_note(staged: MatterIntakeFile) -> str:
    """The reason a promoted version carries, from the staged file's.

    Only for ``FAILED``. A canonical version that is FAILED and silent about why
    is exactly what data-quality tooling cannot explain, and the reader *did*
    say why — on the staged row that is thrown away with the session (DX-4).
    The other states need no reason carried: ``INTAKE_READ`` and ``PENDING``
    are not failures, and ``NOT_APPLICABLE`` is the format's, not this file's.
    """
    if promoted_extraction_state(staged.extraction_state) != ExtractionState.FAILED:
        return ""
    return staged.extraction_note


def promote_intake_files(
    *,
    session: MatterIntakeSession,
    matter: Any,
    actor: Any,
    titles: dict[str, str] | None = None,
    role: str = "",
    filed: list[Any] | None = None,
) -> list[MatterIntakeFile]:
    """Every file still on the form becomes one Document with one version.

    ``role`` is `file_incoming`'s override (empty: the classifier's), and
    ``filed``, when given, receives each `Document` in the order it was filed —
    what `Uus teema`'s sent proposal stands its send on.

    ``titles`` maps a staged file's id to the display title typed on its row
    (`intake_title__<id>`); a file without one keeps its filename as the title.

    Called from inside `matter_create`'s transaction, so a refusal anywhere
    after it takes the Documents with it and there is never a Matter carrying
    half an envelope.

    **One file, one Document**, exactly as `Saabunud` does it and through the
    same two services. The filename, the MIME type and the role are the ones
    recorded at upload; the checksum is recomputed from the bytes read back and
    compared, so the version that is written is provably the file the browser
    sent.

    **The bytes are carried across and the reading is not — but the *fact* of
    the reading is.** docs/adr/0064 promoted a staged file at ``PENDING`` so
    the ordinary extraction worker would parse it again from scratch, and gave
    a good argument for it: reusing a staged parse would mean either
    duplicating the canonical publish path against a second source of truth or
    promoting a derivative that later readers assume is complete.

    That argument still holds and this does not contradict it. Nothing derived
    is carried across — no derivative row, no text fragment, no search
    projection, no attachment Document — and the staged text is still thrown
    away with the session. What changed in docs/adr/0072 is the *other* half:
    the second parse is not performed either. A file read while somebody was
    looking at the form has done its job, the answers went onto the form, and
    re-reading it the moment a Matter exists is the corpus write amplification
    that stalled production on 2026-09-10 for a result nobody asked for.

    So the promoted version records what is true of it, and the intake reader's
    verdict maps straight onto the canonical column:

    ==================  ====================  =======================================
    staged              promoted              what it says
    ==================  ====================  =======================================
    ``DONE``            ``INTAKE_READ``       read once, on the form; nothing owed —
                                              **only while the form offers the
                                              reading** (otherwise ``PENDING``)
    ``NOT_APPLICABLE``  ``NOT_APPLICABLE``    no parser opens this format, ever
    ``FAILED``          ``FAILED``            a parser opened these exact bytes and
                                              could not read them
    ``PENDING`` /       ``PENDING``           nothing has read it — promoted before
    ``PROCESSING``                            the reader reached it
    ==================  ====================  =======================================

    "Nothing owed" is true only when the answers did go onto the form. With the
    suggestions withdrawn (docs/adr/0088) a staged ``DONE`` was read for nobody,
    so it is promoted ``PENDING`` like a file nothing has read — the honest
    state, and the one that keeps it searchable once the ordinary worker runs
    (`promoted_extraction_state`, AUD-025, docs/adr/0072 amendment 2026-10-10).
    The predicate is asked once per promotion, so every file of one press gets
    the same answer.

    Only ``PENDING`` is offered to `pending_versions`, and it is the only
    state that is honestly still pending. An operator who wants text out of a
    promoted file anyway runs `extract_document_version` on it deliberately;
    what is refused is the automatic re-read.

    **The original bytes are untouched by any of this.** The checksum is
    recomputed from what is read back and compared before a version row exists,
    so the evidence is provably the file the browser sent whatever the reader
    made of it.
    """
    storage = staging_storage()
    promoted: list[MatterIntakeFile] = []
    offered = create_form_suggestions_offered()

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

        # The role is `role_for`'s (inside `file_incoming`), asked again of the
        # same name rather than read back from the staged row. The staged copy
        # is the same answer, kept for the analyser's ranking; asking the rule
        # itself leaves no second rule behind — the fallback this replaced
        # filed a row with a blank role as `INCOMING_AUTHORITY` whatever it
        # was — and keeps every path that turns an incoming file into a
        # Document on one classifier (ENG-066).
        document = file_incoming(
            matter=matter,
            filename=staged.original_filename,
            content=content,
            mime_type=staged.mime_type,
            actor=actor,
            extraction_state=promoted_extraction_state(
                staged.extraction_state, suggestions_offered=offered
            ),
            extraction_note=promoted_extraction_note(staged),
            title=(titles or {}).get(str(staged.pk), ""),
            role=role,
        )
        if filed is not None:
            filed.append(document)
        promoted.append(staged)

    return promoted


def without_staged_copies(
    uploads: list[AcceptedUpload], staged: list[MatterIntakeFile]
) -> list[AcceptedUpload]:
    """The chosen files this session has not already staged.

    A form posts its session from the first render (ENG-074), and the browser
    empties the file input only once the stage answer arrives. A `Loo teema`
    pressed while that upload is in flight therefore carries both: the session,
    into which the server has by then staged the files, and the same files
    again in the input. Promoting one and attaching the other filed every file
    twice. The page now waits for the upload before it submits; this is the
    server's half, for a browser that did not wait — a request that hung past
    the page's limit, or a stage answer that never came back after it had
    committed.

    **Narrow on purpose.** A chosen file is dropped only when a file staged
    into *this same session* has the same name and the same bytes, and each
    staged file cancels at most one chosen file. It is not deduplication by
    content: two different names with identical bytes are two files, and so
    is a file chosen again after a refusal — that one arrives as a held
    upload, which is never passed through here (`matter_create`). Both halves
    of this overlap come from one form, one press and one set of bytes, so it
    is one file sent twice rather than two a person chose.
    """
    remaining: dict[tuple[str, str], int] = {}
    for item in staged:
        key = (item.original_filename, item.sha256)
        remaining[key] = remaining.get(key, 0) + 1
    kept: list[AcceptedUpload] = []
    for upload in uploads:
        key = (upload.filename, hashlib.sha256(upload.content).hexdigest())
        if remaining.get(key):
            remaining[key] -= 1
            continue
        kept.append(upload)
    return kept


#: The columns the reader writes from a staged file's contents — everything on
#: the row that is *about what the file says* rather than about the bytes. One
#: definition, so `consume_session` and the reader's sweep cannot disagree about
#: what "the extracted text is gone" means. ``extraction_state`` and
#: ``extraction_note`` stay: they say what happened, not what was read.
_EXTRACTED_PAYLOAD_CLEARED: dict[str, Any] = {
    "text": [],
    "text_character_count": 0,
    "email_metadata": None,
}


def _still_holding_text() -> Q:
    """Rows whose extracted payload has not been cleared yet."""
    return ~Q(text=[]) | Q(text_character_count__gt=0) | Q(email_metadata__isnull=False)


def consume_session(session: MatterIntakeSession) -> None:
    """The Teema exists, so the staging is over: stamp it, drop the bytes and the text.

    Called after the commit rather than inside it. Deleting a staged object is
    not part of the business operation, and a storage backend having a bad
    minute must not take a written Matter with it — the sweeper will find
    anything left behind.

    **The extracted text goes too.** The reader writes a file's whole text — up
    to millions of characters of somebody's incoming correspondence — and a
    message's parsed headers onto the staged row, and once the Teema exists no
    caller will ever read them again: promotion carries the bytes and not the
    reading, and the form they were for is gone. Deleting only the bytes left
    that copy in the database until an operator ran `prune_intake_staging`,
    which nothing scheduled then (AUD-025; docs/adr/0177 schedules it). The
    rows themselves stay for the sweeper, so a repeated submission still finds
    a closed door rather than an empty one.
    """
    now = timezone.now()
    MatterIntakeSession.objects.filter(pk=session.pk, consumed_at__isnull=True).update(
        consumed_at=now, updated_at=now
    )
    MatterIntakeFile.objects.filter(session_id=session.pk).filter(_still_holding_text()).update(
        **_EXTRACTED_PAYLOAD_CLEARED, updated_at=now
    )
    for message in _discard_objects(session).errors:
        logger.error("consumed intake staging left in place: %s", message)


def clear_expired_intake_text() -> int:
    """Blank the extracted text of every session no form can use any more.

    The other half of `consume_session`'s rule: a form that was opened, read and
    abandoned is never consumed, so its text would otherwise sit in the database
    until somebody ran `prune_intake_staging`. Called by the always-running
    intake reader at a bounded cadence (`run_intake_reader`), which makes the
    text's lifetime the session's own rather than an operator's memory.

    **One ``UPDATE``, and only of text.** Rows, bytes, stamps and states are left
    exactly as they were — deleting is `sweep_stale_sessions`' decision, run by
    the `intake-sweeper` service (docs/adr/0177). The scope is the sessions `stale()` already names
    — expired, or consumed but somehow still holding text because the
    after-commit clear in `consume_session` never ran — and within them only
    rows that still hold something, so a turn with nothing to clear writes
    nothing: on the production array a write is the cost (docs/adr/0072).

    An expired session's files are offered to nothing: `get_session` refuses
    it, so neither the panel nor promotion reads them again, and the reader's
    own queue already excludes it (`pending_intake_files`). Returns the number
    of rows cleared.
    """
    holding = MatterIntakeFile.objects.filter(
        session__in=MatterIntakeSession.objects.stale()
    ).filter(_still_holding_text())
    # Asked before written, so the usual answer — nothing to clear — is one
    # read and no statement a write-counting test or the array would see.
    if not holding.exists():
        return 0
    return holding.update(**_EXTRACTED_PAYLOAD_CLEARED, updated_at=timezone.now())


@dataclass
class Discarded:
    """What deleting one session's staged bytes did, and what it refused to do."""

    objects: int = 0
    bytes: int = 0
    #: Each a sentence for an operator. Non-empty means nothing of this session
    #: was deleted: an unsafe session is left exactly as found, for a person.
    errors: list[str] = field(default_factory=list)


def _own_key(key: str, prefix: str) -> bool:
    """A key directly under this session's prefix, and nothing cleverer."""
    head, _, name = key.rpartition("/")
    return head == prefix and name not in ("", ".", "..") and "\\" not in name


def _discard_objects(session: MatterIntakeSession) -> Discarded:
    """Delete this session's stored bytes. Rows are untouched.

    Everything under the session's own prefix, not only the keys its rows
    name. An object whose row never committed — a write whose transaction
    rolled back afterwards — is named by no row, and following rows alone left
    it in the store for good (ENG-087). The prefix is this one session's and
    nobody else's: the scope is exactly the sessions the caller already chose,
    never a scan of the whole staging area.

    **Refuses rather than follows** (csf_3942255b466a4e213b958b16). Now that a
    timer runs this, a staging tree somebody tampered with must not become a
    way to delete something else. A row naming a key outside its own prefix,
    a session or ``intake`` directory that is a symbolic link or resolves
    elsewhere, and an entry in the session's directory that is a link or not a
    regular file are each reported, and the whole session is left untouched:
    nothing is followed, nothing is deleted through a link, and the evidence
    store, which this module never opens for writing, stays out of reach.
    """
    storage = staging_storage()
    prefix = f"{STORAGE_PREFIX}/{session.pk}"
    result = Discarded()
    keys = set(session.files.exclude(storage_key="").values_list("storage_key", flat=True))
    for key in sorted(keys):
        if not _own_key(key, prefix):
            result.errors.append(f"{session.pk}: a row names {key!r}, outside {prefix}/")
    if result.errors:
        return result

    location = getattr(storage, "location", None)
    if not location:  # pragma: no cover - every configured store is a filesystem
        try:
            _directories, names = storage.listdir(prefix)
        except FileNotFoundError:
            names = []
        keys.update(f"{prefix}/{name}" for name in names)
        for key in sorted(keys):
            if _own_key(key, prefix) and _discard_key(storage, key):
                result.objects += 1
        return result

    return _discard_directory(Path(os.path.realpath(location)), session, result)


def _discard_directory(root: Path, session: MatterIntakeSession, result: Discarded) -> Discarded:
    """The filesystem half of `_discard_objects`: one flat directory, no links."""
    intake = root / STORAGE_PREFIX
    directory = intake / str(session.pk)
    for path in (intake, directory):
        if path.is_symlink():
            result.errors.append(f"{session.pk}: {path} is a symbolic link; not followed")
            return result
    if not os.path.lexists(directory):
        return result
    if not directory.is_dir() or os.path.realpath(directory) != str(directory):
        result.errors.append(f"{session.pk}: {directory} is not a directory under {root}")
        return result

    with os.scandir(directory) as listing:
        entries = list(listing)
    for entry in entries:
        if entry.is_symlink():
            result.errors.append(f"{session.pk}: {entry.path} is a symbolic link; not followed")
        elif not entry.is_file(follow_symlinks=False):
            result.errors.append(f"{session.pk}: {entry.path} is not a regular file")
    if result.errors:
        return result

    for entry in sorted(entries, key=lambda item: item.name):
        try:
            size = entry.stat(follow_symlinks=False).st_size
            os.unlink(entry.path)
        except FileNotFoundError:
            continue
        except OSError as error:
            result.errors.append(f"{session.pk}: could not delete {entry.path}: {error}")
            continue
        result.objects += 1
        result.bytes += size
    if not result.errors:
        try:
            os.rmdir(directory)
        except OSError:  # pragma: no cover - best effort; the rows still go
            logger.warning("could not remove staging directory %s", directory, exc_info=True)
    return result


def _discard_key(storage: Any, key: str) -> bool:
    try:
        if not storage.exists(key):
            return False
        storage.delete(key)
        return True
    except OSError:  # pragma: no cover - best effort
        logger.warning("could not delete staged intake object %s", key, exc_info=True)
        return False


# ---------------------------------------------------------------------------
# Lifecycle
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class SweepReport:
    sessions: int
    files: int
    objects: int
    bytes: int = 0
    #: Sessions found unsafe or failing, left in place, one sentence each.
    errors: tuple[str, ...] = ()
    #: Stale sessions still waiting after this run: the bound, made visible.
    remaining: int = 0


def sweep_stale_sessions(
    *, limit: int | None = None, max_bytes: int | None = None, dry_run: bool = False
) -> SweepReport:
    """Delete staging nobody will ask for again: expired, or already consumed.

    Rows drive this, not a directory listing, and that is the difference
    between this and ``pending._sweep``. A held upload is a bare object with no
    row anywhere, so age on disk is the only fact available about it; a staged
    file has a row that says who owns it, when it expires and whether a Matter
    took it. Deciding from the row means the sweeper can never delete an object
    a live session is still pointing at, whatever the clock on the filesystem
    says: an unfinished form survives until its own expiry.

    Bytes first, then rows. An object outliving its row is a few kilobytes in a
    store that is not backed up; a row outliving its object is a form that
    offers somebody a file which is no longer there.

    **Bounded, idempotent and scheduled** (docs/adr/0177). One run takes at most
    ``limit`` sessions and stops before the staged bytes it has taken would pass
    ``max_bytes`` (it always takes one, so a single large session cannot stall
    it). Each session is locked, re-checked as stale and deleted in its own
    short transaction, skipping one a request holds: `stage_uploads` locks the
    session row first, so a sweep and an upload into the same form take turns.
    A second run over the same state finds nothing and deletes nothing. A
    session `_discard_objects` refuses is left whole and reported in
    ``errors``. The `intake-sweeper` service runs this every
    ``INTAKE_STAGING_SWEEP_INTERVAL_SECONDS`` (`prune_intake_staging --loop`).
    """
    if limit is None:
        limit = settings.INTAKE_STAGING_SWEEP_MAX_SESSIONS
    if max_bytes is None:
        max_bytes = settings.INTAKE_STAGING_SWEEP_MAX_BYTES
    candidates = list(
        MatterIntakeSession.objects.stale()
        .annotate(staged_bytes=Coalesce(Sum("files__size_bytes"), 0))
        .order_by("expires_at", "pk")
        .values_list("pk", "staged_bytes")[: max(0, limit)]
    )
    chosen: list[Any] = []
    planned = 0
    for pk, staged_bytes in candidates:
        if chosen and planned + staged_bytes > max_bytes:
            break
        chosen.append(pk)
        planned += staged_bytes

    if dry_run:
        return SweepReport(
            sessions=len(chosen),
            files=MatterIntakeFile.objects.filter(session_id__in=chosen).count(),
            objects=0,
            bytes=planned,
            remaining=MatterIntakeSession.objects.stale().count() - len(chosen),
        )

    sessions = files = objects = removed_bytes = 0
    errors: list[str] = []
    for pk in chosen:
        with transaction.atomic():
            session = (
                MatterIntakeSession.objects.stale()
                .select_for_update(skip_locked=True)
                .filter(pk=pk)
                .first()
            )
            if session is None:
                # Already gone (another sweep), or in a request's hands now.
                continue
            discarded = _discard_objects(session)
            if discarded.errors:
                errors.extend(discarded.errors)
                for message in discarded.errors:
                    logger.error("intake staging sweep refused: %s", message)
                continue
            files += session.files.count()
            session.delete()
        sessions += 1
        objects += discarded.objects
        removed_bytes += discarded.bytes

    remaining = MatterIntakeSession.objects.stale().count()
    logger.info(
        "swept %d stale intake sessions (%d files, %d objects, %d bytes); %d remain, %d refused",
        sessions,
        files,
        objects,
        removed_bytes,
        remaining,
        len(errors),
    )
    return SweepReport(
        sessions=sessions,
        files=files,
        objects=objects,
        bytes=removed_bytes,
        errors=tuple(errors),
        remaining=remaining,
    )


def oldest_stale_seconds() -> float | None:
    """How long the longest-waiting stale session has been stale, or ``None``.

    Stale since its consumption or its expiry, whichever came first. This is
    what `check_intake_staging` measures: convergence, like
    `check_search_freshness`. A sweeper with nothing to do is healthy, and one
    that has stopped, or keeps refusing an unsafe session, goes red once
    anything has waited too long.
    """
    since = (
        MatterIntakeSession.objects.stale()
        .annotate(since=Least("consumed_at", "expires_at"))
        .order_by("since")
        .values_list("since", flat=True)
        .first()
    )
    if since is None:
        return None
    return max(0.0, (timezone.now() - since).total_seconds())
