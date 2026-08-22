"""Turning an email's attachments into evidence of their own.

An attachment is a file somebody sent. It belongs in the Matter as a Document,
findable and downloadable on its own terms — a lawyer looking for the annex
should not have to know it arrived stapled to a covering message.

What makes that safe is the link back. Every attachment Document records, in a
row rather than a sentence, exactly which message DocumentVersion it came out
of. "Which original email did this PDF come from" has to be answerable years
later, when the parser that knew has been replaced twice and the provenance note
says *saabus e-kirjaga* (Stage-2B brief 25).

Three things this module refuses to do:

* **Guess the business meaning.** An attachment gets `EMAIL_ATTACHMENT` and
  nothing more specific. Mail arrives from ministries, members, associations and
  colleagues alike, and calling every attachment an incoming official document
  would file half of them wrongly (Stage-2B brief 26).
* **Promote inline resources.** A signature logo is recorded in the message's
  metadata derivative and does not become a Document. Nine of them per forwarded
  thread would bury the two annexes that matter (Stage-2B brief 27).
* **Store an attachment twice.** Re-running extraction on the same message must
  not double the Matter's document count, so an attachment already linked to
  this exact parent version at this exact ordinal is left alone.
* **Follow a message inside a message forever.** A nested `.eml` is preserved
  whole as its own evidence rather than expanded, which is right — but the
  Document it becomes is itself a message, so the worker extracts *it* next and
  finds the message inside that one. The parser's own depth limit cannot see
  this, because each of those passes is a fresh parse that starts at depth zero;
  the nesting only exists in the chain of `EmailAttachmentLink` rows, and that
  is where it has to be counted (`_nesting_depth`).
"""

from __future__ import annotations

import logging
import posixpath
from typing import Any

from django.conf import settings

from app.documents.derivatives import AttachmentDisposition, EmailAttachmentLink
from app.documents.enums import DocumentRole, MalwareScanState
from app.documents.extraction.base import ParsedAttachment
from app.documents.models import DocumentVersion
from app.documents.services import add_evidence_version, create_document
from app.documents.uploads import EXTENSION_MIME_TYPES

logger = logging.getLogger(__name__)

#: Attachment types that are themselves messages, and therefore the ones whose
#: chain has to be bounded. A PDF is a leaf: it becomes one Document and stops.
#: A message becomes a Document the worker will open, find another message in,
#: and repeat — so a 100 MB file of nothing but envelopes could otherwise
#: manufacture hundreds of thousands of Documents and evidence blobs, one worker
#: pass at a time, with every individual pass looking entirely reasonable.
NESTED_MESSAGE_MIME_TYPES: frozenset[str] = frozenset(
    {"message/rfc822", "application/vnd.ms-outlook"}
)


def register_email_attachments(
    *, parent_version: DocumentVersion, attachments: tuple[ParsedAttachment, ...]
) -> int:
    """Create a Document per real attachment. Returns how many were new.

    Runs inside the orchestrator's publish transaction, so a failure part way
    leaves neither half-created documents nor a message marked extracted.
    """
    existing = set(
        EmailAttachmentLink.objects.filter(parent_version=parent_version).values_list(
            "ordinal", flat=True
        )
    )
    matter = parent_version.document.matter
    depth = _nesting_depth(parent_version)
    at_depth_limit = depth >= settings.EXTRACTION_MAX_EMAIL_DEPTH
    created = 0

    for ordinal, attachment in enumerate(attachments, start=1):
        if ordinal in existing:
            continue
        if attachment.inline:
            # Counted on the message's EMAIL_METADATA derivative and otherwise
            # left alone. It is part of how the message draws itself, not
            # something anybody sent, and it gets no Document and no link row.
            continue

        mime_type = _storable_mime_type(attachment)
        if mime_type is None:
            # The message is still evidence and still extracted; one attachment
            # in a format the evidence store does not accept is reported and
            # skipped rather than failing the whole intake.
            logger.info(
                "attachment skipped version=%s ordinal=%d reason=unsupported_type",
                parent_version.pk,
                ordinal,
            )
            continue

        if at_depth_limit and mime_type in NESTED_MESSAGE_MIME_TYPES:
            # The chain stops here. Only messages are refused: a PDF three
            # envelopes deep is still somebody's annex and still worth having,
            # and it cannot extend the chain because nothing opens it looking
            # for more messages. Said out loud so the ceiling is visible to an
            # operator rather than being a silently shorter thread.
            logger.warning(
                "nested message not stored version=%s ordinal=%d depth=%d limit=%d",
                parent_version.pk,
                ordinal,
                depth,
                settings.EXTRACTION_MAX_EMAIL_DEPTH,
            )
            continue

        document = create_document(
            matter=matter,
            title=attachment.filename or f"Manus {ordinal}",
            role=DocumentRole.EMAIL_ATTACHMENT,
            created_by=parent_version.uploaded_by,
            visibility_override=parent_version.document.visibility_override,
            provenance_note=(f"Manus e-kirjast {parent_version.original_filename} (#{ordinal})."),
        )
        version = add_evidence_version(
            document=document,
            content=attachment.content,
            original_filename=attachment.filename or f"manus-{ordinal}",
            mime_type=mime_type,
            uploaded_by=parent_version.uploaded_by,
            acquired_at=parent_version.acquired_at,
            source_identifier=str(parent_version.pk),
            # Never inherited from the parent. The message having been scanned
            # says nothing about a file that was inside it, and copying a CLEAN
            # verdict onto unscanned bytes would be exactly the fake control
            # this codebase refuses elsewhere (Stage-2B brief 32).
            malware_scan_state=MalwareScanState.PENDING,
        )
        EmailAttachmentLink.objects.create(
            parent_version=parent_version,
            attachment_version=version,
            ordinal=ordinal,
            declared_filename=attachment.filename[:400],
            content_id=attachment.content_id[:200],
            disposition=AttachmentDisposition.ATTACHMENT,
        )
        created += 1

    return created


def _nesting_depth(version: DocumentVersion) -> int:
    """How many messages this binary already arrived inside.

    Walks the `EmailAttachmentLink` chain upward. Bounded by the limit itself
    plus one — the walk stops as soon as it has counted enough to refuse, so a
    corpus that somehow already contains a deep chain cannot make this query
    expensive.

    Zero for a message somebody uploaded directly, which is the ordinary case
    and costs one indexed lookup that misses.
    """
    limit = settings.EXTRACTION_MAX_EMAIL_DEPTH
    depth = 0
    current = version.pk
    while depth <= limit:
        parent = (
            EmailAttachmentLink.objects.filter(attachment_version_id=current)
            .values_list("parent_version_id", flat=True)
            .first()
        )
        if parent is None:
            return depth
        depth += 1
        current = parent
    return depth


def _storable_mime_type(attachment: ParsedAttachment) -> str | None:
    """What the evidence store should record for this attachment.

    The message's own claim about an attachment's type is not trusted: it is
    attacker-controlled in exactly the same way a browser's Content-Type is, and
    the upload path already refuses to believe that one. The extension decides,
    against the same allowlist, and a type outside it means the attachment is
    not stored at all.
    """
    extension = posixpath.splitext(attachment.filename.lower())[1]
    declared = EXTENSION_MIME_TYPES.get(extension)
    if declared is not None:
        return declared
    return None


def parent_email_of(version: DocumentVersion) -> EmailAttachmentLink | None:
    """The message this exact binary arrived in, if it arrived in one."""
    return (
        EmailAttachmentLink.objects.filter(attachment_version=version)
        .select_related("parent_version", "parent_version__document")
        .first()
    )


def attachments_of(version: DocumentVersion) -> Any:
    return (
        EmailAttachmentLink.objects.filter(
            parent_version=version, disposition=AttachmentDisposition.ATTACHMENT
        )
        .select_related("attachment_version", "attachment_version__document")
        .order_by("ordinal")
    )
