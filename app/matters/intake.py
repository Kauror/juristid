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

from dataclasses import dataclass
from datetime import date
from typing import Any

from django.db import transaction

from app.core.enums import Visibility
from app.core.errors import DomainError
from app.documents.enums import DocumentRole
from app.documents.services import add_evidence_version, create_document
from app.documents.uploads import AcceptedUpload, read_upload
from app.matters.entry_enums import EntryKind
from app.matters.models import Matter
from app.matters.services import add_entry, create_matter

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
    return [read_upload(handle) for handle in files]


@transaction.atomic
def register_incoming(
    *,
    uploads: list[AcceptedUpload],
    title: str = "",
    actor: Any = None,
    owner: Any = None,
    source_organisations: Any = None,
    received_date: date | None = None,
    response_deadline: date | None = None,
    stage: Any = None,
    track: str = "",
    visibility: str = Visibility.NORMAL,
    brief_summary: str = "",
    handover_note: str = "",
) -> IntakeResult:
    """Create the Matter and capture every arriving file as evidence.

    Call :func:`validate_uploads` first: this function assumes the bytes are
    already accepted, and does the writing in one transaction so a failure part
    way leaves no Matter behind.

    No procedural stage is invented. A file arriving says something has been
    received; it says nothing about where the external process stands, and
    guessing would put a wrong Hetkeseis on the file from its first minute.
    """
    if not uploads:
        raise DomainError("Vali vähemalt üks fail.")

    resolved_title = (title or "").strip() or title_from_filename(uploads[0].filename)

    matter = create_matter(
        title=resolved_title,
        actor=actor,
        owner=owner,
        stage=stage,
        track=track or "",
        source_organisations=source_organisations,
        received_date=received_date,
        response_deadline=response_deadline,
        visibility=visibility,
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

    for upload in uploads:
        document = create_document(
            matter=matter,
            title=upload.filename,
            role=role_for(upload.filename),
            created_by=actor,
        )
        add_evidence_version(
            document=document,
            content=upload.content,
            original_filename=upload.filename,
            mime_type=upload.mime_type,
            uploaded_by=actor,
        )

    return IntakeResult(matter=matter, documents=len(uploads))
