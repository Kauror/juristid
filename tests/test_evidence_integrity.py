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
from app.matters.models import Matter
from app.matters.services import set_matter_visibility
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


# ---------------------------------------------------------------------------
# The Matter is the third input to the rule (DATA-001)
# ---------------------------------------------------------------------------
#
# Both sides of "final evidence may not be less restricted than its submission"
# are derived from the Matter's visibility. Relaxing the Matter therefore drops
# the evidence to whatever its own override says while a submission carrying its
# own RESTRICTED override stays where it is — reaching the exact state the other
# two triggers exist to prevent, without either record being written.


def _stranded_pair(matter, actor):
    """A valid restricted submission over evidence that only the Matter protects.

    Built through services only, and accepted by every check on the way: while
    the Matter is RESTRICTED the document is effectively RESTRICTED too.
    """
    _document, version = _evidence(matter, actor, title="Arvamuse tekst")
    submission = create_submission(
        matter=matter,
        title="Tundlik arvamus",
        actor=actor,
        visibility_override=Visibility.RESTRICTED,
    )
    select_final_evidence(submission=submission, version=version, actor=actor)
    submission.refresh_from_db()
    mark_submission_sent(submission=submission, actor=actor)
    submission.refresh_from_db()
    return submission, version


def test_relaxing_a_matter_over_weaker_final_evidence_is_refused(restricted_matter, specialist):
    """The regression.

    Before this round the call was accepted, and the submission's exact text
    became listable and downloadable by anyone who could see the Matter.
    """
    _submission, _version = _stranded_pair(restricted_matter, specialist)

    with pytest.raises(DomainError):
        set_matter_visibility(
            matter=restricted_matter, visibility=Visibility.NORMAL, actor=specialist
        )


def test_a_refused_relaxation_leaves_the_matter_and_its_evidence_alone(
    client, restricted_matter, specialist, other_specialist
):
    _submission, version = _stranded_pair(restricted_matter, specialist)

    with pytest.raises(DomainError):
        set_matter_visibility(
            matter=restricted_matter, visibility=Visibility.NORMAL, actor=specialist
        )

    restricted_matter.refresh_from_db()
    assert restricted_matter.visibility == Visibility.RESTRICTED

    client.force_login(other_specialist)
    assert client.get(reverse("documents:download", kwargs={"pk": version.pk})).status_code == 404


def test_the_database_refuses_relaxing_the_matter_directly(restricted_matter, specialist):
    """The service is where the sentence comes from; this is the backstop."""
    _submission, _version = _stranded_pair(restricted_matter, specialist)

    with pytest.raises(DatabaseError), transaction.atomic():
        Matter.objects.filter(pk=restricted_matter.pk).update(visibility=Visibility.NORMAL)

    restricted_matter.refresh_from_db()
    assert restricted_matter.visibility == Visibility.RESTRICTED


def test_relaxing_a_matter_whose_evidence_is_restricted_in_its_own_right_is_fine(
    restricted_matter, specialist
):
    """The ordinary way out: restrict the document, then relax the Matter."""
    document, version = _evidence(
        restricted_matter, specialist, override=Visibility.RESTRICTED, title="Arvamuse tekst"
    )
    submission = create_submission(
        matter=restricted_matter,
        title="Tundlik arvamus",
        actor=specialist,
        visibility_override=Visibility.RESTRICTED,
    )
    select_final_evidence(submission=submission, version=version, actor=specialist)
    submission.refresh_from_db()
    mark_submission_sent(submission=submission, actor=specialist)

    set_matter_visibility(matter=restricted_matter, visibility=Visibility.NORMAL, actor=specialist)

    restricted_matter.refresh_from_db()
    assert restricted_matter.visibility == Visibility.NORMAL
    document.refresh_from_db()
    assert document.visibility_override == Visibility.RESTRICTED


def test_an_unrestricted_submission_does_not_block_relaxing_its_matter(
    restricted_matter, specialist
):
    """Nothing is stranded when the submission has no restriction of its own."""
    _document, version = _evidence(restricted_matter, specialist, title="Arvamuse tekst")
    submission = create_submission(matter=restricted_matter, title="Arvamus", actor=specialist)
    select_final_evidence(submission=submission, version=version, actor=specialist)
    submission.refresh_from_db()
    mark_submission_sent(submission=submission, actor=specialist)

    set_matter_visibility(matter=restricted_matter, visibility=Visibility.NORMAL, actor=specialist)
    restricted_matter.refresh_from_db()
    assert restricted_matter.visibility == Visibility.NORMAL


def test_tightening_a_matter_is_never_blocked(normal_matter, specialist):
    """Tightening raises both sides together, and repairs the state if it was broken."""
    _document, version = _evidence(normal_matter, specialist, title="Arvamuse tekst")
    submission = create_submission(matter=normal_matter, title="Arvamus", actor=specialist)
    select_final_evidence(submission=submission, version=version, actor=specialist)
    submission.refresh_from_db()
    mark_submission_sent(submission=submission, actor=specialist)

    set_matter_visibility(matter=normal_matter, visibility=Visibility.RESTRICTED, actor=specialist)
    normal_matter.refresh_from_db()
    assert normal_matter.visibility == Visibility.RESTRICTED


def test_the_edit_form_refuses_the_change_without_saving_the_rest_of_it(
    client, restricted_matter, specialist
):
    """A refused visibility leaves nothing else from the same edit behind."""
    _submission, _version = _stranded_pair(restricted_matter, specialist)
    original_title = restricted_matter.title

    client.force_login(specialist)
    response = client.post(
        reverse("matters:matter_edit", kwargs={"pk": restricted_matter.pk}),
        {
            "title": "Uus pealkiri",
            "stage": str(restricted_matter.stage_id or ""),
            "visibility": Visibility.NORMAL,
        },
    )

    # 400 from the refusal itself, not from form validation: the sentence the
    # service raised is on the page.
    assert response.status_code == 400
    assert "vähem piiratuks" in response.content.decode()
    restricted_matter.refresh_from_db()
    assert restricted_matter.visibility == Visibility.RESTRICTED
    assert restricted_matter.title == original_title


def _relax_past_the_backstop(matter):
    """Reach the state the way it could only have been reached before this round."""
    with connection.cursor() as cursor:
        # `ALTER TABLE` refuses while this transaction still has deferred
        # foreign-key events queued from the rows built above.
        cursor.execute("SET CONSTRAINTS ALL IMMEDIATE")
        cursor.execute(
            "ALTER TABLE matters_matter DISABLE TRIGGER "
            "matters_relied_upon_evidence_stays_restricted"
        )
        cursor.execute(
            "UPDATE matters_matter SET visibility = 'NORMAL' WHERE id = %s", [str(matter.pk)]
        )
        cursor.execute(
            "ALTER TABLE matters_matter ENABLE TRIGGER "
            "matters_relied_upon_evidence_stays_restricted"
        )


def test_the_integrity_checker_names_a_submission_above_its_evidence(
    restricted_matter, specialist, capture_evidence
):
    """Detection for rows written before the trigger existed."""
    from app.documents.integrity import EVIDENCE_LESS_RESTRICTED, check_evidence

    submission, _version = _stranded_pair(restricted_matter, specialist)
    _relax_past_the_backstop(restricted_matter)

    report = check_evidence(scan_storage=False)
    found = {finding.kind: finding for finding in report.findings}
    assert EVIDENCE_LESS_RESTRICTED in found
    assert found[EVIDENCE_LESS_RESTRICTED].subject == str(submission.pk)
    assert "Tundlik arvamus" not in found[EVIDENCE_LESS_RESTRICTED].detail


def _reparent_past_the_backstop(document, matter):
    """Reach the state the way it could only have been reached before DATA-002.

    A direct write to `documents_document.matter_id` used to be enough; it is
    refused now (`submissions/migrations/0006`). What the detector is for is the
    rows written before that trigger existed, so the test has to reach them the
    same way `_relax_past_the_backstop` does.
    """
    with connection.cursor() as cursor:
        cursor.execute("SET CONSTRAINTS ALL IMMEDIATE")
        cursor.execute(
            "ALTER TABLE documents_document DISABLE TRIGGER "
            "documents_relied_upon_evidence_stays_in_matter"
        )
        cursor.execute(
            "UPDATE documents_document SET matter_id = %s WHERE id = %s",
            [str(matter.pk), str(document.pk)],
        )
        cursor.execute(
            "ALTER TABLE documents_document ENABLE TRIGGER "
            "documents_relied_upon_evidence_stays_in_matter"
        )


def test_the_integrity_checker_names_final_evidence_filed_under_another_matter(
    normal_matter, specialist, capture_evidence
):
    """The other half of the same rule.

    No service can produce this state, and since DATA-002 no direct write can
    either — but rows that reached it before that trigger existed are still out
    there, and this is what names them.
    """
    from app.documents.integrity import FOREIGN_FINAL_EVIDENCE, check_evidence

    other = factories.MatterFactory(owner=specialist)
    document, version = _evidence(normal_matter, specialist)
    submission = create_submission(matter=normal_matter, title="Arvamus", actor=specialist)
    select_final_evidence(submission=submission, version=version, actor=specialist)

    _reparent_past_the_backstop(document, other)

    report = check_evidence(scan_storage=False)
    assert FOREIGN_FINAL_EVIDENCE in {finding.kind for finding in report.findings}


def test_a_healthy_final_evidence_relationship_reports_nothing(
    normal_matter, specialist, capture_evidence
):
    from app.documents.integrity import check_evidence

    _document, version = _evidence(normal_matter, specialist)
    submission = create_submission(matter=normal_matter, title="Arvamus", actor=specialist)
    select_final_evidence(submission=submission, version=version, actor=specialist)
    submission.refresh_from_db()
    mark_submission_sent(submission=submission, actor=specialist)

    assert check_evidence(scan_storage=False).ok


def test_the_command_exits_non_zero_for_a_submission_above_its_evidence(
    restricted_matter, specialist, capture_evidence
):
    from django.core.management import call_command

    _stranded_pair(restricted_matter, specialist)
    _relax_past_the_backstop(restricted_matter)

    with pytest.raises(SystemExit) as exit_info:
        call_command("check_evidence_integrity", "--skip-storage-scan")
    assert exit_info.value.code == 1


def test_the_command_does_not_tell_an_operator_to_restore_a_visibility_fault(
    restricted_matter, specialist, capture_evidence
):
    """The two failure classes have opposite answers, and the summary says which.

    "Restore from backup" is the right sentence for bytes that are gone. It is
    the wrong one here: the bytes are present and are what was hashed, and the
    backup holds the same relationship the live database does, so a restore
    repairs nothing and costs a maintenance window. Which side to change is a
    decision about the record of what Koda sent.
    """
    from io import StringIO

    from django.core.management import call_command

    _stranded_pair(restricted_matter, specialist)
    _relax_past_the_backstop(restricted_matter)

    output = StringIO()
    with pytest.raises(SystemExit):
        call_command("check_evidence_integrity", "--skip-storage-scan", stdout=output)

    text = output.getvalue()
    assert "Restore from backup" not in text
    assert "Do not restore" in text
    assert "record of what Koda sent" in text


def test_missing_bytes_still_get_the_restore_sentence(normal_matter, capture_evidence):
    """The correction above must not have taken the storage advice away."""
    from io import StringIO

    from django.core.management import call_command

    from app.documents.services import evidence_storage

    version = capture_evidence(normal_matter, PDF, "a.pdf", MIME)
    evidence_storage().delete(version.storage_key)

    output = StringIO()
    with pytest.raises(SystemExit):
        call_command("check_evidence_integrity", "--skip-storage-scan", stdout=output)

    text = output.getvalue()
    assert "Restore from backup" in text
    assert "Do not restore" not in text
