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

from collections.abc import Callable
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
    resolve_source_organisations,
)
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
    """
    if not uploads:
        raise DomainError("Vali vähemalt üks fail.")

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
    return IntakeResult(matter=matter, documents=len(uploads))
