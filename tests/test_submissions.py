"""Outbound written advocacy.

The rule under test throughout: a submission is SENT only together with the
exact evidence of what went out. Everything else here follows from that.
"""

from __future__ import annotations

import pytest
from django.db import DatabaseError, IntegrityError, transaction
from django.utils import timezone

from app.audit.enums import ChangeEventType
from app.audit.models import ChangeEvent
from app.core.enums import Visibility
from app.core.errors import DomainError
from app.documents.services import add_evidence_version, create_document
from app.submissions.enums import SubmissionKind, SubmissionStatus
from app.submissions.models import Submission
from app.submissions.services import (
    attach_final_evidence,
    create_submission,
    mark_submission_sent,
    select_final_evidence,
    supersede_submission,
    withdraw_submission,
)
from tests import factories

pytestmark = pytest.mark.django_db

PDF = b"%PDF-1.4 synthetic final opinion"
PLAIN = "application/pdf"


def _draft(matter, actor=None, **kwargs):
    return create_submission(
        matter=matter, title=kwargs.pop("title", "Koja arvamus"), actor=actor, **kwargs
    )


def _with_evidence(submission, actor=None):
    attach_final_evidence(
        submission=submission,
        content=PDF,
        original_filename="arvamus.pdf",
        mime_type=PLAIN,
        actor=actor,
    )
    submission.refresh_from_db()
    return submission


# -- creation ---------------------------------------------------------------


def test_a_submission_starts_as_a_draft(normal_matter, specialist):
    submission = _draft(normal_matter, specialist)
    assert submission.status == SubmissionStatus.DRAFT
    assert submission.sent_at is None
    assert submission.final_version is None
    assert ChangeEvent.objects.filter(event_type=ChangeEventType.SUBMISSION_CREATED).exists()


def test_one_matter_can_carry_many_submissions(normal_matter, specialist):
    """The reason there is no Matter-level opinion_sent_date."""
    first = _with_evidence(_draft(normal_matter, specialist, title="Arvamus eelnõule"), specialist)
    mark_submission_sent(submission=first, actor=specialist)

    second = _draft(
        normal_matter,
        specialist,
        title="Täiendav arvamus",
        kind=SubmissionKind.SUPPLEMENTARY_OPINION,
    )
    _with_evidence(second, specialist)
    mark_submission_sent(submission=second, actor=specialist)

    sent = Submission.objects.filter(matter=normal_matter).sent()
    assert sent.count() == 2
    assert {item.kind for item in sent} == {
        SubmissionKind.FORMAL_OPINION,
        SubmissionKind.SUPPLEMENTARY_OPINION,
    }


def test_matter_has_no_canonical_opinion_sent_date(normal_matter):
    """A single column could only ever record one of several submissions."""
    field_names = {field.name for field in normal_matter._meta.get_fields()}
    assert "opinion_sent_date" not in field_names


def test_an_empty_title_is_refused(normal_matter, specialist):
    with pytest.raises(DomainError):
        create_submission(matter=normal_matter, title="  ", actor=specialist)


def test_recipients_and_joint_submitters_are_separate(normal_matter, specialist):
    ministry = factories.OrganisationFactory(name="Näidisministeerium")
    partner = factories.OrganisationFactory(name="Partnerliit")

    submission = create_submission(
        matter=normal_matter,
        title="Ühispöördumine",
        kind=SubmissionKind.JOINT_LETTER,
        actor=specialist,
        recipients=[ministry],
        joint_submitters=[partner],
    )
    assert list(submission.recipients.all()) == [ministry]
    assert list(submission.joint_submitters.all()) == [partner]


# -- sending ----------------------------------------------------------------


def test_sending_requires_final_evidence(normal_matter, specialist):
    submission = _draft(normal_matter, specialist)
    with pytest.raises(DomainError):
        mark_submission_sent(submission=submission, actor=specialist)

    submission.refresh_from_db()
    assert submission.status == SubmissionStatus.DRAFT
    assert submission.sent_at is None


def test_sending_sets_the_timestamp_and_records_the_event(normal_matter, specialist):
    submission = _with_evidence(_draft(normal_matter, specialist), specialist)
    sent = mark_submission_sent(submission=submission, actor=specialist)

    assert sent.status == SubmissionStatus.SENT
    assert sent.sent_at is not None
    assert sent.sent_by == specialist
    assert sent.final_version is not None

    event = ChangeEvent.objects.get(event_type=ChangeEventType.SUBMISSION_SENT)
    assert event.matter_id == normal_matter.id
    assert event.payload["final_version"] == str(sent.final_version_id)


def test_the_database_refuses_a_sent_row_without_evidence(normal_matter):
    """Belt and braces: the service checks, and so does the table."""
    submission = factories.SubmissionFactory(matter=normal_matter)
    with pytest.raises(IntegrityError), transaction.atomic():
        Submission.objects.filter(pk=submission.pk).update(
            status=SubmissionStatus.SENT, sent_at=timezone.now(), final_version=None
        )


def test_the_database_refuses_a_sent_row_without_a_timestamp(normal_matter, specialist):
    submission = _with_evidence(_draft(normal_matter, specialist), specialist)
    with pytest.raises(IntegrityError), transaction.atomic():
        Submission.objects.filter(pk=submission.pk).update(
            status=SubmissionStatus.SENT, sent_at=None
        )


def test_a_submission_cannot_be_sent_twice(normal_matter, specialist):
    submission = _with_evidence(_draft(normal_matter, specialist), specialist)
    mark_submission_sent(submission=submission, actor=specialist)
    submission.refresh_from_db()
    with pytest.raises(DomainError):
        mark_submission_sent(submission=submission, actor=specialist)


def test_final_evidence_cannot_be_swapped_once_captured(normal_matter, specialist):
    """Replacing what was relied upon would rewrite history."""
    submission = _with_evidence(_draft(normal_matter, specialist), specialist)
    with pytest.raises(DomainError):
        attach_final_evidence(
            submission=submission,
            content=b"%PDF-1.4 second file",
            original_filename="teine.pdf",
            mime_type=PLAIN,
            actor=specialist,
        )


def test_final_evidence_bytes_are_immutable(normal_matter, specialist):
    submission = _with_evidence(_draft(normal_matter, specialist), specialist)
    version = submission.final_version

    from app.documents.models import DocumentVersion

    with pytest.raises(DatabaseError), transaction.atomic():
        DocumentVersion.objects.filter(pk=version.pk).update(sha256="0" * 64)


def test_existing_evidence_in_the_matter_can_be_selected(normal_matter, specialist):
    document = create_document(matter=normal_matter, title="Saadetud kiri", created_by=specialist)
    version = add_evidence_version(
        document=document,
        content=PDF,
        original_filename="kiri.pdf",
        mime_type=PLAIN,
        uploaded_by=specialist,
    )
    submission = _draft(normal_matter, specialist)
    select_final_evidence(submission=submission, version=version, actor=specialist)

    submission.refresh_from_db()
    assert submission.final_version == version


def test_evidence_from_another_matter_is_refused(normal_matter, specialist):
    other_matter = factories.MatterFactory(owner=specialist)
    document = create_document(matter=other_matter, title="Võõras", created_by=specialist)
    version = add_evidence_version(
        document=document,
        content=PDF,
        original_filename="vale.pdf",
        mime_type=PLAIN,
        uploaded_by=specialist,
    )
    submission = _draft(normal_matter, specialist)
    with pytest.raises(DomainError):
        select_final_evidence(submission=submission, version=version, actor=specialist)


# -- later transitions ------------------------------------------------------


def test_withdrawing_keeps_the_evidence(normal_matter, specialist):
    submission = _with_evidence(_draft(normal_matter, specialist), specialist)
    mark_submission_sent(submission=submission, actor=specialist)
    submission.refresh_from_db()

    withdraw_submission(submission=submission, actor=specialist, reason="Asendati uuega")
    submission.refresh_from_db()

    assert submission.status == SubmissionStatus.WITHDRAWN
    assert submission.final_version is not None
    assert submission.sent_at is not None
    assert ChangeEvent.objects.filter(event_type=ChangeEventType.SUBMISSION_WITHDRAWN).exists()


def test_a_draft_cannot_be_withdrawn(normal_matter, specialist):
    submission = _draft(normal_matter, specialist)
    with pytest.raises(DomainError):
        withdraw_submission(submission=submission, actor=specialist)


def test_superseding_is_recorded(normal_matter, specialist):
    submission = _draft(normal_matter, specialist)
    supersede_submission(submission=submission, actor=specialist)
    submission.refresh_from_db()
    assert submission.status == SubmissionStatus.SUPERSEDED


# -- visibility -------------------------------------------------------------


def test_submissions_inherit_matter_visibility(restricted_matter, specialist, other_specialist):
    _draft(restricted_matter, specialist)
    assert Submission.objects.visible_to(other_specialist).count() == 0
    assert Submission.objects.visible_to(specialist).count() == 1


def test_a_submission_can_be_more_restrictive_than_its_matter(
    normal_matter, specialist, other_specialist
):
    _draft(normal_matter, specialist, title="Avalik")
    create_submission(
        matter=normal_matter,
        title="Tundlik",
        actor=specialist,
        visibility_override=Visibility.RESTRICTED,
    )
    assert Submission.objects.visible_to(other_specialist).count() == 1
    assert Submission.objects.visible_to(specialist).count() == 2


def test_department_head_sees_restricted_submissions(
    restricted_matter, specialist, department_head
):
    _draft(restricted_matter, specialist)
    assert Submission.objects.visible_to(department_head).count() == 1


def test_administrator_alone_does_not(restricted_matter, specialist, administrator):
    _draft(restricted_matter, specialist)
    assert Submission.objects.visible_to(administrator).count() == 0
