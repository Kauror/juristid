"""Named use cases for outbound written advocacy.

The rule that matters: a Submission becomes SENT only together with the exact
binary that was sent. Both halves happen in one transaction, so there is no
window in which the system claims Koda sent an opinion it cannot produce.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from django.db import transaction
from django.utils import timezone

from app.audit.enums import ChangeEventType
from app.audit.services import record_change_event
from app.core.enums import Visibility, most_restrictive, validate_visibility_override
from app.core.errors import DomainError
from app.documents.enums import DocumentRole
from app.documents.models import Document, DocumentVersion
from app.documents.services import add_evidence_version, create_document
from app.submissions.enums import SubmissionKind, SubmissionStatus
from app.submissions.models import Submission


@transaction.atomic
def create_submission(
    *,
    matter: Any,
    title: str,
    kind: str = SubmissionKind.FORMAL_OPINION,
    actor: Any = None,
    recipients: list[Any] | None = None,
    joint_submitters: list[Any] | None = None,
    channel: str = "",
    reference: str = "",
    notes: str = "",
    visibility_override: str = "",
) -> Submission:
    """Start a submission. It begins as a draft; sending is a separate act."""
    title = title.strip()
    if not title:
        raise DomainError("Arvamus vajab pealkirja.")
    if kind not in SubmissionKind.values:
        raise DomainError(f"Tundmatu arvamuse liik {kind!r}.")
    try:
        validate_visibility_override(visibility_override)
    except ValueError as error:
        raise DomainError(str(error)) from error

    submission = Submission.objects.create(
        matter=matter,
        title=title,
        kind=kind,
        status=SubmissionStatus.DRAFT,
        channel=channel.strip(),
        reference=reference.strip(),
        notes=notes,
        created_by=actor,
        visibility_override=visibility_override,
    )
    if recipients:
        submission.recipients.set(recipients)
    if joint_submitters:
        submission.joint_submitters.set(joint_submitters)

    record_change_event(
        event_type=ChangeEventType.SUBMISSION_CREATED,
        matter=matter,
        actor=actor,
        obj=submission,
        summary=title[:200],
        payload={"kind": kind},
    )
    return submission


def _effective(record: Any) -> str:
    """The visibility that actually applies, derived not stored."""
    return most_restrictive(
        record.matter.visibility, record.visibility_override or Visibility.NORMAL
    )


def check_evidence_is_usable(*, submission: Submission, version: DocumentVersion) -> None:
    """The two rules that make a piece of evidence usable as a final text.

    Both are checked wherever evidence is attached, selected or sent, and both
    have a database backstop, because a submission pointing at the wrong file is
    a claim about what Koda argued that cannot be verified.

    1. **Same Matter.** Evidence from another file is not evidence of this one.
    2. **Not less restricted than the submission.** A restricted submission
       whose final text sits on a normal document would be readable, and
       downloadable, by people who cannot see the submission itself — the
       restriction would be cosmetic.
    """
    document = version.document
    if document.matter_id != submission.matter_id:
        raise DomainError("Tõend peab kuuluma sama teema juurde.")

    if most_restrictive(_effective(document), _effective(submission)) != _effective(document):
        raise DomainError(
            "Lõplik tõend ei tohi olla vähem piiratud kui arvamus ise. "
            "Piira dokumenti või loo tõend arvamuse piiranguga."
        )


@transaction.atomic
def attach_final_evidence(
    *,
    submission: Submission,
    content: bytes,
    original_filename: str,
    mime_type: str,
    actor: Any = None,
    document: Document | None = None,
) -> DocumentVersion:
    """Capture the exact binary that is being sent.

    A submission that already has final evidence is not re-pointed at a
    different file: the previously captured version is what was relied upon, and
    quietly replacing it would rewrite history. Withdraw and supersede instead.
    """
    if submission.status != SubmissionStatus.DRAFT:
        raise DomainError("Lõplikku tõendit saab lisada ainult koostatavale arvamusele.")
    if submission.final_version_id is not None:
        raise DomainError("Sellel arvamusel on juba lõplik tõend.")

    if document is None:
        document = create_document(
            matter=submission.matter,
            title=submission.title[:400],
            role=DocumentRole.KODA_SUBMISSION_FINAL,
            created_by=actor,
            # Evidence created for a restricted submission inherits that
            # restriction rather than relying on the caller to remember.
            visibility_override=submission.visibility_override,
        )
    elif document.matter_id != submission.matter_id:
        raise DomainError("Tõend peab kuuluma sama teema juurde.")

    version = add_evidence_version(
        document=document,
        content=content,
        original_filename=original_filename,
        mime_type=mime_type,
        uploaded_by=actor,
    )

    check_evidence_is_usable(submission=submission, version=version)

    submission.final_version = version
    submission.save(update_fields=["final_version", "updated_at"])
    return version


@transaction.atomic
def select_final_evidence(
    *, submission: Submission, version: DocumentVersion, actor: Any = None
) -> Submission:
    """Point a draft at an evidence version that is already in the Matter."""
    if submission.status != SubmissionStatus.DRAFT:
        raise DomainError("Lõplikku tõendit saab valida ainult koostatavale arvamusele.")
    check_evidence_is_usable(submission=submission, version=version)

    submission.final_version = version
    submission.save(update_fields=["final_version", "updated_at"])
    return submission


@transaction.atomic
def mark_submission_sent(
    *,
    submission: Submission,
    actor: Any = None,
    sent_at: datetime | None = None,
    channel: str = "",
    reference: str = "",
) -> Submission:
    """Record that this exact text went out, with its evidence.

    The check-and-write happens under a row lock so two people pressing send at
    once cannot produce two different sent timestamps for one submission.
    """
    locked = Submission.objects.select_for_update().get(pk=submission.pk)

    if locked.status == SubmissionStatus.SENT:
        raise DomainError("Arvamus on juba saadetud.")
    if locked.status != SubmissionStatus.DRAFT:
        raise DomainError("Ainult koostatava arvamuse saab saadetuks märkida.")
    final_version = locked.final_version
    if final_version is None:
        raise DomainError(
            "Saadetud arvamus vajab täpset lõplikku tõendit. Lisa või vali saadetud fail."
        )
    # Re-checked at the moment of sending: the document could have been
    # re-pointed or relaxed between drafting and this call.
    check_evidence_is_usable(submission=locked, version=final_version)

    locked.status = SubmissionStatus.SENT
    locked.sent_at = sent_at or timezone.now()
    locked.sent_by = actor
    if channel.strip():
        locked.channel = channel.strip()
    if reference.strip():
        locked.reference = reference.strip()
    locked.save(
        update_fields=["status", "sent_at", "sent_by", "channel", "reference", "updated_at"]
    )

    record_change_event(
        event_type=ChangeEventType.SUBMISSION_SENT,
        matter=locked.matter,
        actor=actor,
        obj=locked,
        summary=locked.title[:200],
        payload={
            "kind": locked.kind,
            "sent_at": locked.sent_at.isoformat(),
            "final_version": str(locked.final_version_id),
            "recipients": [organisation.name for organisation in locked.recipients.all()],
        },
    )

    submission.refresh_from_db()
    return submission


@transaction.atomic
def withdraw_submission(
    *, submission: Submission, actor: Any = None, reason: str = ""
) -> Submission:
    """Withdraw a sent submission. The evidence of what was sent stays."""
    if submission.status != SubmissionStatus.SENT:
        raise DomainError("Tagasi võtta saab ainult saadetud arvamust.")

    submission.status = SubmissionStatus.WITHDRAWN
    submission.save(update_fields=["status", "updated_at"])

    record_change_event(
        event_type=ChangeEventType.SUBMISSION_WITHDRAWN,
        matter=submission.matter,
        actor=actor,
        obj=submission,
        summary=submission.title[:200],
        payload={"reason": reason[:500]},
    )
    return submission


@transaction.atomic
def supersede_submission(*, submission: Submission, actor: Any = None) -> Submission:
    if submission.status not in {SubmissionStatus.SENT, SubmissionStatus.DRAFT}:
        raise DomainError("Seda arvamust ei saa asendatuks märkida.")

    submission.status = SubmissionStatus.SUPERSEDED
    submission.save(update_fields=["status", "updated_at"])

    record_change_event(
        event_type=ChangeEventType.SUBMISSION_SUPERSEDED,
        matter=submission.matter,
        actor=actor,
        obj=submission,
        summary=submission.title[:200],
    )
    return submission
