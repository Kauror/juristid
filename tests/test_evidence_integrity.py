"""Final evidence must be the right file, and no more visible than its submission.

A submission's final version is the system's answer to "what exactly did Koda
send". Evidence from another Matter makes that answer wrong; evidence that is
less restricted than the submission makes the restriction cosmetic, because the
exact text would be listed and downloadable by people who cannot see the
submission at all.

Both rules are checked in the service and backed by database triggers, and both
halves are tested independently.
"""

from __future__ import annotations

import pytest
from django.db import DatabaseError, connection, transaction
from django.urls import reverse
from django.utils import timezone

from app.core.enums import Visibility
from app.core.errors import DomainError
from app.documents.enums import DocumentRole
from app.documents.models import Document
from app.documents.services import add_evidence_version, create_document
from app.submissions.enums import SubmissionStatus
from app.submissions.models import Submission
from app.submissions.services import (
    attach_final_evidence,
    create_submission,
    mark_submission_sent,
    select_final_evidence,
)
from tests import factories

pytestmark = pytest.mark.django_db

PDF = b"%PDF-1.4 synthetic final opinion"
MIME = "application/pdf"


def _evidence(matter, actor, *, override: str = "", title: str = "Tõend"):
    document = create_document(
        matter=matter,
        title=title,
        role=DocumentRole.KODA_SUBMISSION_FINAL,
        created_by=actor,
        visibility_override=override,
    )
    version = add_evidence_version(
        document=document,
        content=PDF,
        original_filename="fail.pdf",
        mime_type=MIME,
        uploaded_by=actor,
    )
    return document, version


# ---------------------------------------------------------------------------
# Same Matter
# ---------------------------------------------------------------------------


def test_evidence_from_the_same_matter_is_accepted(normal_matter, specialist):
    submission = create_submission(matter=normal_matter, title="Arvamus", actor=specialist)
    _document, version = _evidence(normal_matter, specialist)

    select_final_evidence(submission=submission, version=version, actor=specialist)
    submission.refresh_from_db()
    assert submission.final_version == version


def test_selecting_another_matters_version_is_refused(normal_matter, specialist):
    other = factories.MatterFactory(owner=specialist)
    submission = create_submission(matter=normal_matter, title="Arvamus", actor=specialist)
    _document, version = _evidence(other, specialist)

    with pytest.raises(DomainError):
        select_final_evidence(submission=submission, version=version, actor=specialist)


def test_attaching_to_another_matters_document_is_refused(normal_matter, specialist):
    """The document argument is the path a caller could get wrong quietly."""
    other = factories.MatterFactory(owner=specialist)
    foreign_document = create_document(matter=other, title="Võõras", created_by=specialist)
    submission = create_submission(matter=normal_matter, title="Arvamus", actor=specialist)

    with pytest.raises(DomainError):
        attach_final_evidence(
            submission=submission,
            content=PDF,
            original_filename="fail.pdf",
            mime_type=MIME,
            actor=specialist,
            document=foreign_document,
        )
    submission.refresh_from_db()
    assert submission.final_version is None


def test_the_database_refuses_foreign_evidence_written_directly(normal_matter, specialist):
    """A bulk update bypasses every service check; the trigger does not."""
    other = factories.MatterFactory(owner=specialist)
    submission = create_submission(matter=normal_matter, title="Arvamus", actor=specialist)
    _document, foreign_version = _evidence(other, specialist)

    with pytest.raises(DatabaseError), transaction.atomic():
        Submission.objects.filter(pk=submission.pk).update(final_version=foreign_version)


def test_the_database_refuses_a_sent_submission_with_foreign_evidence(normal_matter, specialist):
    other = factories.MatterFactory(owner=specialist)
    _document, foreign_version = _evidence(other, specialist)

    with pytest.raises(DatabaseError), transaction.atomic():
        Submission.objects.create(
            matter=normal_matter,
            title="Otse loodud",
            status=SubmissionStatus.SENT,
            sent_at=timezone.now(),
            final_version=foreign_version,
        )


# ---------------------------------------------------------------------------
# Not less restricted than the submission
# ---------------------------------------------------------------------------


def test_evidence_created_for_a_restricted_submission_inherits_the_restriction(
    normal_matter, specialist
):
    """The common path must be safe without the caller remembering anything."""
    submission = create_submission(
        matter=normal_matter,
        title="Tundlik arvamus",
        actor=specialist,
        visibility_override=Visibility.RESTRICTED,
    )
    version = attach_final_evidence(
        submission=submission,
        content=PDF,
        original_filename="tundlik.pdf",
        mime_type=MIME,
        actor=specialist,
    )
    assert version.document.visibility_override == Visibility.RESTRICTED
    assert version.document.effective_visibility == Visibility.RESTRICTED


def test_selecting_less_restricted_evidence_is_refused(normal_matter, specialist):
    submission = create_submission(
        matter=normal_matter,
        title="Tundlik arvamus",
        actor=specialist,
        visibility_override=Visibility.RESTRICTED,
    )
    _document, open_version = _evidence(normal_matter, specialist, title="Avalik tõend")

    with pytest.raises(DomainError):
        select_final_evidence(submission=submission, version=open_version, actor=specialist)


def test_attaching_to_a_less_restricted_document_is_refused(normal_matter, specialist):
    open_document = create_document(matter=normal_matter, title="Avalik", created_by=specialist)
    submission = create_submission(
        matter=normal_matter,
        title="Tundlik arvamus",
        actor=specialist,
        visibility_override=Visibility.RESTRICTED,
    )
    with pytest.raises(DomainError):
        attach_final_evidence(
            submission=submission,
            content=PDF,
            original_filename="fail.pdf",
            mime_type=MIME,
            actor=specialist,
            document=open_document,
        )


def test_equally_restricted_evidence_is_accepted(normal_matter, specialist):
    submission = create_submission(
        matter=normal_matter,
        title="Tundlik arvamus",
        actor=specialist,
        visibility_override=Visibility.RESTRICTED,
    )
    _document, version = _evidence(
        normal_matter, specialist, override=Visibility.RESTRICTED, title="Tundlik tõend"
    )
    select_final_evidence(submission=submission, version=version, actor=specialist)
    submission.refresh_from_db()
    assert submission.final_version == version


def test_more_restricted_evidence_than_the_submission_is_fine(normal_matter, specialist):
    """Tightening is always allowed; only relaxing breaks the rule."""
    submission = create_submission(matter=normal_matter, title="Tavaline", actor=specialist)
    _document, version = _evidence(
        normal_matter, specialist, override=Visibility.RESTRICTED, title="Tundlik tõend"
    )
    select_final_evidence(submission=submission, version=version, actor=specialist)
    submission.refresh_from_db()
    assert submission.final_version == version


def test_sending_revalidates_the_rule_independently_of_the_trigger(normal_matter, specialist):
    """The service check must stand on its own, not lean on the trigger.

    Reaching this state through the ORM is impossible — the trigger refuses it,
    which is what the neighbouring test proves. So the trigger is disabled for
    the duration of this transaction, the invalid state is created, and the
    service is asked to send. Belt and braces, each verified without the other.
    """
    submission = create_submission(matter=normal_matter, title="Arvamus", actor=specialist)
    _document, version = _evidence(normal_matter, specialist)
    select_final_evidence(submission=submission, version=version, actor=specialist)

    with connection.cursor() as cursor:
        cursor.execute("SET CONSTRAINTS ALL IMMEDIATE")
        cursor.execute(
            "ALTER TABLE submissions_submission "
            "DISABLE TRIGGER submissions_final_evidence_integrity"
        )
    Submission.objects.filter(pk=submission.pk).update(visibility_override=Visibility.RESTRICTED)
    submission.refresh_from_db()

    with pytest.raises(DomainError):
        mark_submission_sent(submission=submission, actor=specialist)

    submission.refresh_from_db()
    assert submission.status == SubmissionStatus.DRAFT
    assert submission.sent_at is None


def test_the_database_refuses_relaxing_relied_upon_evidence(normal_matter, specialist):
    """Without this the rule could be undone after the fact by one UPDATE."""
    submission = create_submission(
        matter=normal_matter,
        title="Tundlik arvamus",
        actor=specialist,
        visibility_override=Visibility.RESTRICTED,
    )
    version = attach_final_evidence(
        submission=submission,
        content=PDF,
        original_filename="tundlik.pdf",
        mime_type=MIME,
        actor=specialist,
    )
    submission.refresh_from_db()
    mark_submission_sent(submission=submission, actor=specialist)

    with pytest.raises(DatabaseError), transaction.atomic():
        Document.objects.filter(pk=version.document_id).update(visibility_override="")


def test_the_database_refuses_restricting_a_submission_past_its_evidence(normal_matter, specialist):
    submission = create_submission(matter=normal_matter, title="Arvamus", actor=specialist)
    _document, version = _evidence(normal_matter, specialist)
    select_final_evidence(submission=submission, version=version, actor=specialist)

    with pytest.raises(DatabaseError), transaction.atomic():
        Submission.objects.filter(pk=submission.pk).update(
            visibility_override=Visibility.RESTRICTED
        )


# ---------------------------------------------------------------------------
# The leak this is all preventing
# ---------------------------------------------------------------------------


def test_a_restricted_submissions_evidence_does_not_leak(
    client, normal_matter, specialist, other_specialist
):
    """The Matter is visible to everyone; the submission and its text are not."""
    submission = create_submission(
        matter=normal_matter,
        title="Tundlik arvamus",
        actor=specialist,
        visibility_override=Visibility.RESTRICTED,
    )
    version = attach_final_evidence(
        submission=submission,
        content=PDF,
        original_filename="tundlik.pdf",
        mime_type=MIME,
        actor=specialist,
    )
    submission.refresh_from_db()
    mark_submission_sent(submission=submission, actor=specialist)

    client.force_login(other_specialist)

    # The Matter itself is readable.
    detail = client.get(reverse("matters:matter_detail", kwargs={"pk": normal_matter.pk}))
    assert detail.status_code == 200

    # The submission is not listed on the position tab.
    position = client.get(reverse("matters:matter_position", kwargs={"pk": normal_matter.pk}))
    assert position.status_code == 200
    assert "Tundlik arvamus" not in position.content.decode()

    # Its evidence is not listed among documents.
    documents = client.get(reverse("matters:matter_documents", kwargs={"pk": normal_matter.pk}))
    assert documents.status_code == 200
    assert "tundlik.pdf" not in documents.content.decode()

    # And it cannot be downloaded by guessing the URL.
    download = client.get(reverse("documents:download", kwargs={"pk": version.pk}))
    assert download.status_code == 404

    # The owner reaches all three.
    client.force_login(specialist)
    assert client.get(reverse("documents:download", kwargs={"pk": version.pk})).status_code == 200
