"""Incoming material becomes a Matter.

The register's real starting point is a file, not a title: a ministry sends a
covering letter, a draft, an explanatory memorandum and two annexes, and only
then does Koda open a file. Until now the product made the document secondary —
create a Matter, find the Documents tab, expand a disclosure labelled "Tõend",
upload one file at a time — which is backwards for the work that arrives most
often.

Two properties this module exists to guarantee.

**Nothing is written until everything validates.** Every upload is read and
checked *before* any business state is created, so a rejected fifth file cannot
leave behind a Matter with four documents and a confused owner. A half-made
Matter is worse than a failed intake: it looks like real work and quietly isn't.

**One file, one document.** Several arriving files are several Documents, each
with its own immutable version, filename, MIME type and checksum. Concatenating
or zipping them would destroy exactly the per-file provenance that makes this
evidence rather than an attachment.
"""

from __future__ import annotations

import uuid
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import date
from typing import Any

from django.db import transaction
from django.utils import timezone

from app.audit.operations import composer_operation
from app.core.enums import Visibility
from app.core.errors import DomainError
from app.documents.enums import DocumentRole, ExtractionState
from app.documents.models import Document
from app.documents.services import EvidenceContent, add_evidence_version, create_document
from app.documents.uploads import AcceptedUpload, check_upload_batch, read_upload
from app.matters.entry_enums import EntryKind
from app.matters.enums import MatterDataClass
from app.matters.models import Matter
from app.matters.services import (
    add_entry,
    close_matter_for_terminal_stage,
    create_matter,
    resolve_addressee,
    resolve_source_organisations,
    save_personal_note,
)
from app.workflow.enums import Track
from app.workflow.stage_flow import is_terminal as is_terminal_stage

#: Extensions whose contents are an email rather than a document. The file is
#: captured exactly as it arrived either way; the role records what it *is*, so
#: that Stage 2B can find the emails to parse without guessing from filenames.
#: Parsing itself is deliberately not attempted here.
EMAIL_EXTENSIONS = (".msg", ".eml")

MAX_INTAKE_FILES = 20


def role_for(filename: str) -> str:
    lowered = (filename or "").lower()
    if lowered.endswith(EMAIL_EXTENSIONS):
        return DocumentRole.ORIGINAL_EMAIL
    return DocumentRole.INCOMING_AUTHORITY


def file_incoming(
    *,
    matter: Matter,
    filename: str,
    content: EvidenceContent,
    mime_type: str,
    actor: Any,
    extraction_state: str = ExtractionState.PENDING,
    extraction_note: str = "",
    title: str = "",
    role: str = "",
    expected_sha256: str = "",
) -> Document:
    """One incoming file as one Document with one immutable version.

    ``title`` is the display title typed for it before the save; the filename
    is the title otherwise, and the version's `original_filename` always.

    The one sequence every incoming path files through — Saabunud, `Uus teema`'s
    direct and held files, and a staged file promoted at save. Through the
    ordinary services, so a file arriving with a new Matter is subject to the
    same evidence rules as one uploaded later: same storage, same checksum, same
    immutability trigger, same scan state. Nothing is inferred from the
    filename but the role, and the role is :func:`role_for`'s, so an `.eml` is
    «Algne e-kiri» however it reached the Teema (ENG-066: the three copies this
    replaces had each needed that fix separately).

    ``role`` overrides the classifier for one deliberate case: `Uus teema`'s
    «Märgi saadetud Koja arvamuseks», where the person has just said these
    files are the Chamber's own sent letter — filed as `Koja arvamus`, the role
    `+ Koja arvamus` gives the same bytes (docs/adr/0151, amendment of
    2026-10-12). Left empty, the role is :func:`role_for`'s, as always.

    ``extraction_state`` is the staging area's: a file it has already read
    arrives with that reading's state (`promoted_extraction_state`), and a
    file it failed to read arrives with the reader's reason in
    ``extraction_note`` (DX-4).
    """
    from app.documents.uploads import clean_display_title

    document = create_document(
        matter=matter,
        title=clean_display_title(title) or filename,
        role=role or role_for(filename),
        created_by=actor,
    )
    add_evidence_version(
        document=document,
        content=content,
        original_filename=filename,
        mime_type=mime_type,
        uploaded_by=actor,
        extraction_state=extraction_state,
        extraction_note=extraction_note,
        expected_sha256=expected_sha256,
    )
    return document


def title_from_filename(filename: str) -> str:
    """A first title from the first file, so the field is never blank.

    Deliberately mechanical: the extension is dropped and separators become
    spaces. It does not try to read legal meaning out of `eelnou_v3_FINAL.pdf`,
    because a confident wrong title is harder to notice than an ugly one, and
    the field stays editable.
    """
    name = (filename or "").rsplit("/", 1)[-1].rsplit("\\", 1)[-1]
    stem = name.rsplit(".", 1)[0] if "." in name else name
    cleaned = " ".join(stem.replace("_", " ").replace("-", " ").split())
    return cleaned or name or "Saabunud materjal"


@dataclass(frozen=True)
class IntakeResult:
    matter: Matter
    documents: int


class IncomingFormAlreadySubmitted(Exception):
    """A `Saabunud → Lisa` form that has already filed its Teema, posted again.

    Raised inside `register_incoming`'s transaction before anything is written,
    so the replay leaves nothing behind. ``session`` is the form's consumed
    token, which names the Teema the first submission created (ENG-074,
    docs/adr/0112). Not a `DomainError`: nothing was refused — what the person
    wanted already exists.
    """

    def __init__(self, session: Any) -> None:
        super().__init__("incoming form already submitted")
        self.session = session


def validate_uploads(files: list[Any]) -> list[AcceptedUpload]:
    """Read and check every file up front.

    Raises before anything is written. ``read_upload`` is the existing
    validator — size, extension allowlist and content signature — and is reused
    rather than reimplemented, because a second upload-validation stack is a
    second thing to get wrong.
    """
    if not files:
        raise DomainError("Vali vähemalt üks fail.")
    if len(files) > MAX_INTAKE_FILES:
        raise DomainError(f"Korraga saab lisada kuni {MAX_INTAKE_FILES} faili.")
    # The aggregate policy, from the sizes the upload handler counted, before
    # any file is opened (docs/adr/0182).
    check_upload_batch([int(getattr(handle, "size", 0) or 0) for handle in files])
    return [read_upload(handle) for handle in files]


def read_incoming_files(files: list[Any]) -> tuple[list[AcceptedUpload], list[str]]:
    """Read every newly chosen file, keeping the good ones and naming the bad.

    The `Saabunud → Lisa` twin of `views._read_new_matter_files`: the same
    `read_upload` validator, but every file is read and each refusal names its
    file, so the good half of a batch can be held across the refusal
    (`app.documents.pending`) instead of being dropped with it. The batch
    limits are checked first, from the sizes the upload handler counted, before
    any file is opened (docs/adr/0182); over them, nothing is read.
    """
    chosen = [handle for handle in files if handle]
    if len(chosen) > MAX_INTAKE_FILES:
        return [], [f"Korraga saab lisada kuni {MAX_INTAKE_FILES} faili."]
    try:
        check_upload_batch([int(getattr(handle, "size", 0) or 0) for handle in chosen])
    except DomainError as error:
        return [], [str(error)]
    accepted: list[AcceptedUpload] = []
    refusals: list[str] = []
    for handle in chosen:
        try:
            accepted.append(read_upload(handle))
        except DomainError as error:
            refusals.append(f"{handle.name} — {error}")
    return accepted, refusals


def check_incoming_batch(uploads: list[AcceptedUpload]) -> None:
    """Held and newly chosen files together are one save, under one policy."""
    if not uploads:
        raise DomainError("Vali vähemalt üks fail.")
    if len(uploads) > MAX_INTAKE_FILES:
        raise DomainError(f"Korraga saab lisada kuni {MAX_INTAKE_FILES} faili.")
    check_upload_batch([upload.size for upload in uploads])


#: A step the caller hands to :func:`file_new_matter`, run on the new Matter.
MatterStep = Callable[[Matter], object]


@transaction.atomic
def file_new_matter(
    *,
    title: str,
    actor: Any = None,
    owner: Any = None,
    stage: Any = None,
    track: str = "",
    source_organisations: Any = None,
    sender_name: str = "",
    received_date: date | None = None,
    response_deadline: date | None = None,
    policy_areas: Any = None,
    policy_area_other: str = "",
    legal_instruments: Any = None,
    legal_instrument_other: str = "",
    visibility: str = Visibility.NORMAL,
    data_class: str = MatterDataClass.REAL,
    brief_summary: str = "",
    handover_note: str = "",
    attach_files: MatterStep | None = None,
    after_files: MatterStep | None = None,
    seed_plan: bool = False,
) -> Matter:
    """A person files a new Teema: the one creation sequence both doors share.

    `Uus teema` (`matter_create`) and `Saabunud` (`intake`) used to orchestrate
    the same creation twice, once in a view and once here, and every shared rule
    had to be kept in step by hand — the deadline recorded as a request, the
    files grouped into one operation, the plan seeded, a terminal `Hetkeseis`
    closing the file (AUD-010). The doors still differ in what they *ask*; what
    a new Teema *is* is decided here, once, in one transaction, in this order:

    1. the senders are resolved, so a typed institution and the Teema that names
       it are one write and a refusal below takes both
       (`resolve_source_organisations`, §6);
    2. the Matter is created with exactly what the door gave it. **The received
       date is stored as given**: a person who cleared it has said it is not
       known, and `None` stays `None` — no door invents today (D-3; «never
       manufacture historical certainty», AGENTS.md);
    3. the handover note («Märkmed vastutajale»), its own timeline row;
    4. the files, through the caller's ``attach_files``, inside **one**
       `composer_operation`, so `Teema käik` reads «lisas 3 dokumenti» once
       (docs/adr/0121 §6);
    5. the caller's ``after_files`` — `Uus teema`'s scratch note and links —
       in the same transaction, so a refusal there takes the Teema with it;
    6. a terminal `Hetkeseis` closes the file, last, so everything above is
       written on an open Matter (docs/adr/0131 §10); otherwise the standard
       plan is seeded where ``seed_plan`` asks for it (docs/adr/0133 §5, 0141).

    No first `NextAction` is written from the deadline (docs/adr/0094, 0133 §8):
    the deadline is an obligation on the Matter, not its current task.

    Visibility, the data class and the `Koja algatus` track are the caller's
    decisions and arrive already made; `create_matter` refuses what is not
    allowed — TEST on a non-native record, incoming dates on `Koja algatus`,
    a second `Õigusakt` (docs/adr/0150, 0151).
    """
    senders = resolve_source_organisations(
        chosen=source_organisations, typed_name=sender_name or ""
    )

    matter = create_matter(
        title=title,
        actor=actor,
        owner=owner,
        stage=stage,
        track=track or "",
        source_organisations=senders,
        received_date=received_date,
        response_deadline=response_deadline,
        # A deadline typed by a person filing new work is a request recorded
        # now: it is answered only by an answer that names it
        # (`app/matters/response_deadlines.py`). Computed here, once, for both
        # doors.
        response_requested_at=timezone.now() if response_deadline else None,
        policy_areas=list(policy_areas or []),
        policy_area_other=policy_area_other or "",
        # Handed to the service as part of the creation rather than written on
        # afterwards: one MATTER_CREATED, and no instant at which the Teema
        # carries a classification nobody chose (task §17, §19).
        legal_instruments=list(legal_instruments or []),
        legal_instrument_other=legal_instrument_other or "",
        visibility=visibility,
        data_class=data_class,
        brief_summary=(brief_summary or "").strip(),
    )

    # «Märkmed vastutajale» — what the person filing this wants whoever picks it
    # up to know. Recorded as the Matter's first timeline entry rather than as a
    # column of its own: it is a dated, attributed statement about the file, it
    # is visible to exactly the people who may see the file, and that is what an
    # `Entry` already is. No new field, and no new visibility question
    # (docs/design-v2-compatibility.md, DS-09).
    note = (handover_note or "").strip()
    if note:
        add_entry(matter=matter, body=note, author=actor, kind=EntryKind.NOTE)

    # **Every file this press files is one operation** (docs/adr/0121 §6): one
    # `operation_id`, so `Teema käik` reads «lisas 7 dokumenti» once instead of
    # «lisas dokumendi» seven times. Only the files — `Teema loodud`, the note
    # above and the extras below are separate facts with rows of their own, and
    # a grouped note would swallow the files into «lisas märkuse». Each document
    # keeps its own `DOCUMENT_CREATED` and `EVIDENCE_VERSION_ADDED`; the
    # grouping is the chronology's reading, never a merged record.
    if attach_files is not None:
        with composer_operation():
            attach_files(matter)

    if after_files is not None:
        after_files(matter)

    # **A Teema filed as «Jõustunud» or «Rohkem ei tegele» is filed closed**
    # (docs/adr/0131 §10), last, so the files and links above are written on an
    # open Matter — and it gets no plan: importers, refreshes and a Teema filed
    # already closed never get one (docs/adr/0133 §5). A Matter created a moment
    # ago has no follow-up to confirm, so the closure asks nothing.
    if is_terminal_stage(getattr(stage, "key", None)):
        close_matter_for_terminal_stage(matter=matter, actor=actor)
    elif seed_plan:
        from app.workflow.plan import seed_standard_plan

        seed_standard_plan(matter=matter, actor=actor)

    return matter


@transaction.atomic
def register_incoming(
    *,
    uploads: list[AcceptedUpload],
    title: str = "",
    actor: Any = None,
    owner: Any = None,
    source_organisations: Any = None,
    sender_name: str = "",
    received_date: date | None = None,
    response_deadline: date | None = None,
    stage: Any = None,
    track: str = "",
    visibility: str = Visibility.NORMAL,
    brief_summary: str = "",
    handover_note: str = "",
    seed_plan: bool = False,
    form_token: Any = None,
) -> IntakeResult:
    """Create the Matter and capture every arriving file as evidence.

    Call :func:`validate_uploads` first: this function assumes the bytes are
    already accepted, and does the writing in one transaction so a failure part
    way leaves no Matter behind.

    No procedural stage is invented. A file arriving says something has been
    received; it says nothing about where the external process stands, and
    guessing would put a wrong Hetkeseis on the file from its first minute.

    ``sender_name`` is a body somebody typed because the catalogue does not hold
    it. It is resolved *here* rather than in the view, so that creating the
    institution and creating the Matter are the same transaction: a rejected
    file or any other refusal below takes the new institution with it, and
    Saabunud cannot leave an organisation behind that no Matter names
    (`app.matters.services.resolve_source_organisations`).

    ``seed_plan`` is the explicit seam for `Tööplaan` (docs/adr/0133 §5): the
    `Saabunud` page passes it, because a person filing material that has just
    arrived is starting real work, and the faint standard plan is copied onto
    the new Matter in this same transaction. Nothing infers it from the Matter's
    origin or mode — a caller recording history leaves it off, and gets no plan.

    ``form_token`` is the drawn form's one-time token (`intake`, a
    `MatterIntakeSession` from `intake_staging.open_session`), exactly as on
    `Uus teema` (ENG-074, docs/adr/0112): claimed here first, before any row is
    written and before a reference number is allocated, so a replayed, retried
    or parallel POST of the same form files nothing and raises
    `IncomingFormAlreadySubmitted` naming the Teema the first one created. A
    call naming no session of the actor's carries no token and files as before.
    """
    if not uploads:
        raise DomainError("Vali vähemalt üks fail.")

    # Imported here: `intake_staging` builds on this module.
    from app.matters import intake_staging

    claim = intake_staging.claim_for_create(owner=actor, session_id=form_token)
    if claim.replayed:
        raise IncomingFormAlreadySubmitted(claim.session)

    def attach_uploads(matter: Matter) -> None:
        for upload in uploads:
            file_incoming(
                matter=matter,
                filename=upload.filename,
                content=upload.body,
                mime_type=upload.mime_type,
                actor=actor,
            )

    # A thin door over the one creation sequence (AUD-010): what is Saabunud's
    # own is only the files-first title and the files themselves. The received
    # date goes through exactly as given — `None` when it was cleared (D-3).
    matter = file_new_matter(
        title=(title or "").strip() or title_from_filename(uploads[0].filename),
        actor=actor,
        owner=owner,
        stage=stage,
        track=track,
        source_organisations=source_organisations,
        sender_name=sender_name,
        received_date=received_date,
        response_deadline=response_deadline,
        visibility=visibility,
        brief_summary=brief_summary,
        handover_note=handover_note,
        attach_files=attach_uploads,
        seed_plan=seed_plan,
    )
    # Remembered in the same transaction, so a repeated submission of this
    # form finds the Teema this one created.
    if claim.session is not None:
        intake_staging.record_created_matter(session=claim.session, matter=matter)
    return IntakeResult(matter=matter, documents=len(uploads))


class CreateFormAlreadySubmitted(Exception):
    """An `Uus teema` form that has already filed its Teema, posted again.

    Raised inside `create_from_form`'s transaction before anything is written,
    so the replay leaves nothing behind. ``session`` is the form's consumed
    token, naming the Teema the first submission created (ENG-074,
    docs/adr/0112). Not a `DomainError`: nothing was refused.
    """

    def __init__(self, session: Any) -> None:
        super().__init__("form already submitted")
        self.session = session


@dataclass(frozen=True)
class CreatedFromForm:
    """What one successful `Uus teema` save filed."""

    matter: Matter
    #: Files filed with it: the promoted staged ones plus the held and chosen
    #: ones left after `without_staged_copies`.
    files: int


#: Refused when a ticked similar Matter is not one this reader may link.
SIMILAR_LINK_NOT_FOUND = "Valitud sarnast teemat ei leitud. Värskenda lehte ja proovi uuesti."


@transaction.atomic
def create_from_form(
    *,
    actor: Any,
    data: Mapping[str, Any],
    data_class: str,
    form_token: Any,
    earlier_tokens: list[str],
    confirmed: bool,
    intake_session: Any,
    staged_titles: Mapping[str, str],
    resumed: list[Any],
    chosen: list[Any],
    link_targets: list[str],
    from_candidate: Any = None,
) -> CreatedFromForm:
    """`Uus teema`'s save: the whole creation sequence, in one transaction (HR-13).

    **The caller authorises.** Like `file_new_matter`, this checks no permission of its
    own: the view's `business_write_required` (and `for_create`) gate it.

    The view collects what the page posted (the validated form, the uploads
    already read, the staging session) and renders what this refuses; the
    order of the writes is decided here. In this transaction, in this order:

    1. **This form, exactly once** (ENG-074). The form's session is its one-time
       token, consumed first, before any row is written or any reference
       number allocated, by one conditional UPDATE. A second submission of the
       same form waits for the first on that row, finds it consumed and raises
       `CreateFormAlreadySubmitted`, writing nothing. A POST naming no session
       of this person's carries no token and is not a replay of anything.
    2. **Not the same Teema again by accident** (QA-COORD-01, docs/adr/0166
       §2): a Teema this person just filed, or filed from one of this form's
       earlier tokens, under the same title raises `RecentDuplicateUnconfirmed`
       unless ``confirmed`` («Loo siiski uus»). Refused here, so the claim
       above rolls back with it and the token stays usable. The check holds a
       per-person, per-title lock until this transaction ends, so two tabs
       pressed together take turns (JQA-006).
    3. `file_new_matter`, the one creation sequence the doors share (AUD-010):
       senders, the Matter, then **the files** as one `composer_operation`
       (the token first records the Teema it created, then the staged files
       are promoted, then the held and chosen ones are filed, and «Märgi
       saadetud Koja arvamuseks» records the send on exactly those
       documents), then **the extras** only `Uus teema` asks (the private
       note, the linked Teemad), then a terminal `Hetkeseis` or the plan.
    4. The register-watch candidate the form was opened from, if any, is
       closed by this Teema (docs/adr/0218 §4).

    Any `DomainError` (an ambiguous sender, a duplicate, a link that cannot be
    made) rolls back everything above, the claim included; so does anything
    else, such as an evidence store going away, which propagates. What happens
    to the uploads after a refusal, and releasing the held and staged files
    after the commit, are the caller's: they belong to the request.

    `Saabunud` (`register_incoming`) and MCP (`mcp_writes`) are other doors over
    `file_new_matter` and deliberately do not run this: neither asks for
    links, a note, a sent proposal or a candidate; `Saabunud` has no
    recent-duplicate check (docs/adr/0166 §2 is `Uus teema`'s) and MCP has its
    own once-token (docs/adr/0189).
    """
    # Imported here: `intake_staging` builds on this module, and the others
    # build on the services this module is part of.
    from app.matters import duplicate_intake, intake_staging, workspace
    from app.register_watch import candidates as topic_candidates

    claim = intake_staging.claim_for_create(owner=actor, session_id=form_token)
    if claim.replayed:
        raise CreateFormAlreadySubmitted(claim.session)
    duplicate_intake.refuse_unconfirmed_duplicate(
        actor=actor,
        title=data["title"],
        earlier_tokens=earlier_tokens,
        confirmed=confirmed,
    )

    # «Märgi saadetud Koja arvamuseks» (docs/adr/0151, amendment of
    # 2026-10-12): the files this press files are the Chamber's sent proposal,
    # filed as `Koja arvamus` and recorded as sent in the same operation. The
    # form has already refused it on any other `Õigusakt`; the service refuses
    # it without a file.
    sends_proposal = bool(data.get("koda_sent"))
    filed_role = DocumentRole.KODA_SUBMISSION_FINAL if sends_proposal else ""
    filed: list[Any] = []
    promoted: list[Any] = []
    uploads: list[Any] = [*resumed, *chosen]

    def attach_files(matter: Matter) -> None:
        nonlocal promoted, uploads
        # Remembered first, so a repeated submission of this form finds the
        # Teema the first one created (ENG-074).
        if claim.session is not None:
            intake_staging.record_created_matter(session=claim.session, matter=matter)
        # Staged first, because they were chosen first: each becomes one
        # Document with one immutable version from the bytes the browser sent,
        # checked against the checksum recorded when they arrived.
        if intake_session is not None:
            promoted = intake_staging.promote_intake_files(
                session=intake_session,
                matter=matter,
                actor=actor,
                role=filed_role,
                filed=filed,
                titles=dict(staged_titles),
            )
            # A press while the upload was still in flight posts the session
            # *and* the files it had just staged. Promoted once is filed once;
            # held uploads are never compared (`without_staged_copies`).
            uploads = [*resumed, *intake_staging.without_staged_copies(chosen, promoted)]
        for upload in uploads:
            filed.append(attach_form_upload(matter, upload, actor=actor, role=filed_role))
        # The send, on exactly those documents, in the order they were offered:
        # one `Submission`, the first file its letter and the rest its further
        # sent files. Inside this step, so `Teema käik` reads it as one act.
        if sends_proposal:
            workspace.record_sent_proposal_on_new_matter(
                matter=matter,
                documents=filed,
                author=actor,
                recipients=named_addressees(
                    data.get("sent_recipients"), data.get("sent_recipient_name")
                ),
                sent_on=data.get("sent_on"),
            )

    def after_files(matter: Matter) -> None:
        # The private scratch pad, through its own service, and only when
        # something was typed: an empty note would be a row recording that
        # somebody wrote nothing.
        note = (data.get("notes") or "").strip()
        if note:
            save_personal_note(matter=matter, author=actor, body=note)
        # The similar Matters somebody ticked and the ones chosen by hand
        # (docs/adr/0150 §3): one list, through the one relation service, in
        # this transaction; a link that cannot be made takes the Teema with it.
        link_ticked_similar_matters(matter=matter, ticked=link_targets, actor=actor)

    matter = file_new_matter(
        title=data["title"],
        actor=actor,
        owner=data.get("owner"),
        # Blank by decision: `Uus teema` no longer asks «Millest teema räägib»
        # (docs/adr/0130 §2).
        brief_summary="",
        stage=data.get("stage"),
        # Derived in one case only: `Õigusakt` «Koja ettepanek või pöördumine»
        # is a Teema the Chamber started, `Koja algatus` (docs/adr/0151
        # amendment of 2026-10-12, `initiative_on_create`).
        track=Track.KODA_INITIATIVE if data.get("koda_initiative") else "",
        # Only the sender side: `Uus teema` no longer asks Adressaat
        # (docs/adr/0090 §5).
        source_organisations=data.get("source_organisations"),
        sender_name=data.get("sender_name") or "",
        # As the box was left: a cleared date stays unknown (D-3).
        received_date=data.get("received_date"),
        response_deadline=data.get("response_deadline"),
        policy_areas=list(data.get("policy_areas") or []),
        policy_area_other=data.get("policy_area_other") or "",
        legal_instruments=list(data.get("legal_instruments") or []),
        legal_instrument_other=data.get("legal_instrument_other") or "",
        # Decided here, never read from the form (brief 21).
        visibility=Visibility.NORMAL,
        # The form turns its one checkbox into the stored vocabulary; the
        # service refuses TEST on anything not created here.
        data_class=data_class,
        attach_files=attach_files,
        after_files=after_files,
        # No standard plan on a Teema filed already sent: its next work is the
        # thirty-day check (docs/adr/0151).
        seed_plan=not sends_proposal,
    )
    if from_candidate is not None:
        # Its register addresses become the Teema's `Menetluse link`s, in this
        # transaction; decided by someone else meanwhile, it is left as it is
        # and the Teema still stands.
        topic_candidates.adopt_if_new(from_candidate, matter, actor=actor)
    return CreatedFromForm(matter=matter, files=len(uploads) + len(promoted))


def attach_form_upload(matter: Matter, upload: Any, *, actor: Any, role: str = "") -> Document:
    """One `Uus teema` upload as one Document with one immutable version.

    Through `file_incoming`, so a file arriving with a new Matter meets the same
    evidence rules as one uploaded later, and its role is `role_for`'s answer
    unless the save names one: an `.eml` is «Algne e-kiri» however it reached
    the Teema (ENG-066). The display title typed on a held upload is kept.

    Looked up from this module at call time: it is the seam the creation tests
    replace to fail one file of several (tests/test_matter_create_form.py).
    """
    return file_incoming(
        matter=matter,
        filename=upload.filename,
        content=upload.body,
        mime_type=upload.mime_type,
        actor=actor,
        title=getattr(upload, "display_title", ""),
        role=role,
    )


def named_addressees(chosen: Any, typed: Any) -> list[Any]:
    """The addressees a save names: the ones ticked, then the one typed.

    `resolve_addressee` decides what a typed name means (reuse an exact or alias
    match, create only a genuinely new body, refuse a spelling that names two)
    inside the caller's transaction (docs/adr/0073). De-duplicated, so a typed
    name that resolves to a ticked body is one addressee.
    """
    named_list = list(chosen or [])
    name = (typed or "").strip()
    if not name:
        return named_list
    named = resolve_addressee(chosen=None, typed_name=name)
    if named is not None and not any(row.pk == named.pk for row in named_list):
        named_list.append(named)
    return named_list


def link_ticked_similar_matters(*, matter: Any, ticked: list[str], actor: Any) -> None:
    """Link the new Matter to each ticked `Sarnased teemad` card, once each.

    Through `link_related_matters`, the canonical relation service, so the pair
    is stored once and audited on both files. Duplicates in the post collapse;
    the new Matter itself is never a target. A value that is not a Matter this
    reader may see refuses the whole save rather than linking the rest.
    """
    from app.related_materials.services import link_related_matters

    wanted: list[uuid.UUID] = []
    for value in ticked:
        try:
            pk = uuid.UUID(str(value))
        except ValueError:
            raise DomainError(SIMILAR_LINK_NOT_FOUND) from None
        if pk != matter.pk and pk not in wanted:
            wanted.append(pk)
    if not wanted:
        return
    # Only Matters this reader may open, and only of the new Matter's own data
    # class: a REAL Teema is never linked to synthetic TEST work, which neither
    # the suggestions nor the search ever offer (docs/adr/0150 §3).
    others = {
        other.pk: other
        for other in Matter.objects.visible_to(actor).filter(
            pk__in=wanted, data_class=matter.data_class
        )
    }
    if len(others) != len(wanted):
        raise DomainError(SIMILAR_LINK_NOT_FOUND)
    for pk in wanted:
        link_related_matters(matter=matter, other=others[pk], actor=actor)
