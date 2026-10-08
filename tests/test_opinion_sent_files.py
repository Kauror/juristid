"""A `Koja arvamus` may go out as several files (docs/adr/0144 §5).

One Submission, several exact binaries: the first file is the opinion's
`final_version` with every guarantee it always had, the rest are its further
sent files. Asserted here:

* **the record** — one file reads exactly as before; two files are one
  Submission, one «Arvamus välja», two letter documents and one further-file
  row, with display titles kept apart from the immutable original filenames;
* **the transaction** — a refused second file refuses the whole save;
* **the deadline** — a two-file opinion answers the current request, and a
  later request is not answered by it;
* **the read surfaces** — Dokumendid shows every file with its send, the rail
  draws one line per opinion, «Registreeri saatmine» offers none of them;
* **evidence protection** — a further file cannot be revised, reclassified,
  removed, filed as a working document, moved or relaxed while the send stands;
* **visibility** — a reader who may not see the opinion is shown none of it.
"""

from __future__ import annotations

import datetime as dt

import pytest
from django.core.files.uploadedfile import SimpleUploadedFile
from django.db import IntegrityError, transaction
from django.urls import reverse
from django.utils import timezone

from app.audit.enums import ChangeEventType
from app.audit.models import ChangeEvent
from app.core.enums import Visibility
from app.core.errors import DomainError
from app.documents.enums import DocumentRole
from app.documents.models import Document
from app.documents.services import (
    OPINION_EVIDENCE_IS_NOT_A_WORKING_DOCUMENT,
    SENT_OPINION_EVIDENCE,
    link_document_to_record,
    new_version_refusal,
    removal_refusal,
    role_change_refusal,
)
from app.documents.uploads import UploadRejected
from app.matters.enums import ResponseDeadlineOutcome
from app.matters.models import MatterResponseDeadline
from app.matters.response_deadlines import deadline_revision, request_response_deadline
from app.matters.workspace import SENT_OPINION_NEEDS_A_FILE, add_matter_koda_opinion
from app.submissions.models import Submission, SubmissionSentFile
from app.submissions.opinions import opinion_rail, unregistered_opinion_documents
from app.submissions.services import SENT_FILES_ONLY_BEFORE_SENDING, bind_further_sent_files
from tests import factories
from tests.refusals import refused
from tests.synthetic_containers import signed_container

pytestmark = pytest.mark.django_db


def _pdf(name: str) -> SimpleUploadedFile:
    return SimpleUploadedFile(name, f"%PDF-1.4 {name}".encode(), content_type="application/pdf")


def _asice(name: str = "koda_opinion.asice") -> SimpleUploadedFile:
    return SimpleUploadedFile(
        name, signed_container(), content_type="application/vnd.etsi.asic-e+zip"
    )


def _broken(name: str = "katki.pdf") -> SimpleUploadedFile:
    """Bytes that do not match the extension — refused at the door."""
    return SimpleUploadedFile(name, b"PK\x03\x04 not a pdf", content_type="application/pdf")


@pytest.fixture
def matter(specialist):
    return factories.MatterFactory(owner=specialist, stage=None)


def _send(matter, author, organisation, files, **kwargs):
    return add_matter_koda_opinion(
        matter=matter,
        author=author,
        uploads=files,
        recipients=[organisation],
        sent_on=timezone.localdate(),
        **kwargs,
    )


# ---------------------------------------------------------------------------
# A. The record
# ---------------------------------------------------------------------------


def test_one_file_reads_exactly_as_before(matter, specialist, organisation):
    submission = _send(matter, specialist, organisation, [_asice()]).record

    assert submission.final_version.original_filename == "koda_opinion.asice"
    assert not SubmissionSentFile.objects.filter(submission=submission).exists()
    event = ChangeEvent.objects.get(
        event_type=ChangeEventType.SUBMISSION_SENT, object_id=submission.pk
    )
    assert "sent_files" not in event.payload


def test_the_single_file_shape_still_works(matter, specialist, organisation):
    """Every earlier caller passes ``upload=``; it is the same one-file send."""
    submission = add_matter_koda_opinion(
        matter=matter,
        author=specialist,
        upload=_pdf("arvamus.pdf"),
        recipients=[organisation],
        sent_on=timezone.localdate(),
    ).record
    assert submission.final_version.original_filename == "arvamus.pdf"


def test_two_files_are_one_opinion(matter, specialist, organisation):
    container = _asice()
    annex = _pdf("Selgitus.pdf")
    annex.display_title = "Selgitav lisa ministeeriumile"

    result = _send(matter, specialist, organisation, [container, annex])
    submission = result.record

    assert Submission.objects.filter(matter=matter).count() == 1
    assert submission.final_version.original_filename == "koda_opinion.asice"
    row = SubmissionSentFile.objects.get(submission=submission)
    assert row.position == 1
    assert row.version.original_filename == "Selgitus.pdf"
    assert row.version.document.title == "Selgitav lisa ministeeriumile"
    assert row.version.document.role == DocumentRole.KODA_SUBMISSION_FINAL
    assert {document.pk for document in result.documents} == {
        submission.final_version.document_id,
        row.version.document_id,
    }
    event = ChangeEvent.objects.get(
        event_type=ChangeEventType.SUBMISSION_SENT, object_id=submission.pk
    )
    assert event.payload["sent_files"] == [str(row.version_id)]
    assert (
        ChangeEvent.objects.filter(
            event_type=ChangeEventType.SUBMISSION_SENT, matter=matter
        ).count()
        == 1
    )


def test_three_files_keep_their_order(matter, specialist, organisation):
    submission = _send(
        matter, specialist, organisation, [_pdf("a.pdf"), _pdf("b.pdf"), _pdf("c.pdf")]
    ).record
    names = [
        row.version.original_filename
        for row in SubmissionSentFile.objects.filter(submission=submission).order_by("position")
    ]
    assert names == ["b.pdf", "c.pdf"]


def test_no_file_is_refused(matter, specialist, organisation):
    with pytest.raises(DomainError, match=SENT_OPINION_NEEDS_A_FILE):
        _send(matter, specialist, organisation, [])
    assert not Submission.objects.filter(matter=matter).exists()


def test_a_refused_second_file_refuses_everything(matter, specialist, organisation):
    with pytest.raises(UploadRejected):
        _send(matter, specialist, organisation, [_asice(), _broken()])

    assert not Submission.objects.filter(matter=matter).exists()
    assert not Document.objects.filter(matter=matter).exists()


def test_further_files_cannot_be_added_after_the_send(matter, specialist, organisation):
    submission = _send(matter, specialist, organisation, [_asice()]).record
    other = _send(matter, specialist, organisation, [_pdf("teine.pdf")]).record

    with pytest.raises(DomainError, match=SENT_FILES_ONLY_BEFORE_SENDING):
        bind_further_sent_files(submission=submission, versions=[other.final_version])


# ---------------------------------------------------------------------------
# B. The deadline
# ---------------------------------------------------------------------------


def test_a_two_file_opinion_answers_the_current_request_and_not_the_next(
    matter, specialist, organisation
):
    request_response_deadline(
        matter=matter, deadline=timezone.localdate() + dt.timedelta(days=5), actor=specialist
    )
    matter.refresh_from_db()

    submission = _send(
        matter,
        specialist,
        organisation,
        [_asice(), _pdf("lisa.pdf")],
        answers_deadline=deadline_revision(matter),
    ).record

    matter.refresh_from_db()
    assert matter.response_deadline is None
    answered = MatterResponseDeadline.objects.get(matter=matter)
    assert answered.outcome == ResponseDeadlineOutcome.ANSWERED
    assert answered.submission == submission

    later = timezone.localdate() + dt.timedelta(days=30)
    request_response_deadline(matter=matter, deadline=later, actor=specialist)
    matter.refresh_from_db()
    assert matter.response_deadline == later
    assert MatterResponseDeadline.objects.filter(matter=matter).count() == 1


# ---------------------------------------------------------------------------
# C. What the pages show
# ---------------------------------------------------------------------------


def test_dokumendid_shows_every_sent_file_with_its_send(
    signed_in, matter, specialist, organisation
):
    _send(matter, specialist, organisation, [_asice(), _pdf("Selgitus.pdf")])

    body = signed_in.get(
        reverse("matters:matter_documents", kwargs={"pk": matter.pk})
    ).content.decode()

    assert "koda_opinion.asice" in body
    assert "Selgitus.pdf" in body
    assert body.count("doctable__sent") >= 2


def test_the_rail_draws_one_line_per_opinion(matter, specialist, organisation):
    _send(matter, specialist, organisation, [_asice(), _pdf("Selgitus.pdf")])

    lines = opinion_rail(matter, viewer=specialist)

    assert len(lines) == 1
    assert lines[0].document.current_version.original_filename == "koda_opinion.asice"
    assert [document.current_version.original_filename for document in lines[0].further] == [
        "Selgitus.pdf"
    ]


def test_the_rail_page_names_both_files(signed_in, matter, specialist, organisation):
    _send(matter, specialist, organisation, [_asice(), _pdf("Selgitus.pdf")])

    body = signed_in.get(
        reverse("matters:matter_detail", kwargs={"pk": matter.pk})
    ).content.decode()
    rail = body[body.index('id="koja-arvamus"') :]
    rail = rail[: rail.index("</div>\n", rail.index("railcard__row"))]
    assert "koda_opinion.asice" in rail and "Selgitus.pdf" in rail


def test_no_sent_file_is_offered_for_registration(matter, specialist, organisation):
    _send(matter, specialist, organisation, [_asice(), _pdf("Selgitus.pdf")])
    assert unregistered_opinion_documents(matter, viewer=specialist) == []


def test_the_chronology_keeps_one_row_with_every_sent_file(
    signed_in, matter, specialist, organisation
):
    _send(matter, specialist, organisation, [_asice(), _pdf("Selgitus.pdf")])

    body = signed_in.get(
        reverse("matters:matter_detail", kwargs={"pk": matter.pk})
    ).content.decode()
    kaik = body[body.index('id="ajajoon"') :]
    assert kaik.count("Arvamus välja") == 1
    assert "koda_opinion.asice" in kaik and "Selgitus.pdf" in kaik


def test_the_panel_posts_several_files_as_one_opinion(signed_in, matter, organisation):
    response = signed_in.post(
        reverse("matters:add_koda_opinion", kwargs={"pk": matter.pk}),
        {
            "upload": [_asice(), _pdf("Selgitus.pdf")],
            "upload__pealkiri": ["Koja arvamus (allkirjastatud)", "Selgitav lisa"],
            "recipients": [str(organisation.pk)],
            "sent_on": timezone.localdate().strftime("%d.%m.%Y"),
        },
        headers={"HX-Request": "true"},
    )

    assert response.status_code == 200, response.content.decode()[:600]
    submission = Submission.objects.get(matter=matter)
    assert submission.final_version.document.title == "Koja arvamus (allkirjastatud)"
    assert submission.final_version.original_filename == "koda_opinion.asice"
    row = SubmissionSentFile.objects.get(submission=submission)
    assert row.version.document.title == "Selgitav lisa"
    assert row.version.original_filename == "Selgitus.pdf"


def test_the_panel_still_requires_a_file(signed_in, matter, organisation):
    response = signed_in.post(
        reverse("matters:add_koda_opinion", kwargs={"pk": matter.pk}),
        {
            "recipients": [str(organisation.pk)],
            "sent_on": timezone.localdate().strftime("%d.%m.%Y"),
        },
        headers={"HX-Request": "true"},
    )
    assert response.status_code == 400
    assert "Lisa fail, mis välja saadeti." in response.content.decode()
    assert not Submission.objects.filter(matter=matter).exists()


# ---------------------------------------------------------------------------
# D. Evidence protection
# ---------------------------------------------------------------------------


def _further_document(matter, specialist, organisation):
    submission = _send(matter, specialist, organisation, [_asice(), _pdf("Selgitus.pdf")]).record
    return submission, SubmissionSentFile.objects.get(submission=submission).version.document


def test_a_further_file_is_protected_like_the_letter(matter, specialist, organisation):
    _, document = _further_document(matter, specialist, organisation)

    assert new_version_refusal(document) == SENT_OPINION_EVIDENCE
    assert role_change_refusal(document) == SENT_OPINION_EVIDENCE
    assert removal_refusal(document) == SENT_OPINION_EVIDENCE


def test_a_further_file_cannot_be_filed_as_a_working_document(matter, specialist, organisation):
    submission, document = _further_document(matter, specialist, organisation)
    Document.objects.filter(pk=document.pk).update(role=DocumentRole.OTHER)
    document.refresh_from_db()

    with refused(OPINION_EVIDENCE_IS_NOT_A_WORKING_DOCUMENT):
        link_document_to_record(document=document, record=submission, actor=specialist)


def test_the_database_refuses_moving_a_further_file_to_another_matter(
    matter, specialist, organisation
):
    _, document = _further_document(matter, specialist, organisation)
    elsewhere = factories.MatterFactory(owner=specialist)

    with pytest.raises(IntegrityError), transaction.atomic():
        Document.objects.filter(pk=document.pk).update(matter=elsewhere)


def test_the_database_refuses_a_further_file_from_another_matter(matter, specialist, organisation):
    submission = _send(matter, specialist, organisation, [_asice()]).record
    elsewhere = factories.MatterFactory(owner=specialist)
    foreign = _send(elsewhere, specialist, organisation, [_pdf("muu.pdf")]).record

    with pytest.raises(IntegrityError), transaction.atomic():
        SubmissionSentFile.objects.create(
            submission=submission, version=foreign.final_version, position=1
        )


def test_the_database_refuses_relaxing_a_further_file_of_a_restricted_opinion(
    matter, specialist, organisation
):
    submission, document = _further_document(matter, specialist, organisation)
    Document.objects.filter(pk__in=[document.pk, submission.final_version.document_id]).update(
        visibility_override=Visibility.RESTRICTED
    )
    Submission.objects.filter(pk=submission.pk).update(visibility_override=Visibility.RESTRICTED)

    with pytest.raises(IntegrityError), transaction.atomic():
        Document.objects.filter(pk=document.pk).update(visibility_override=Visibility.NORMAL)


def test_the_database_refuses_restricting_an_opinion_above_its_further_file(
    matter, specialist, organisation
):
    submission, _document = _further_document(matter, specialist, organisation)
    Document.objects.filter(pk=submission.final_version.document_id).update(
        visibility_override=Visibility.RESTRICTED
    )

    with pytest.raises(IntegrityError), transaction.atomic():
        Submission.objects.filter(pk=submission.pk).update(
            visibility_override=Visibility.RESTRICTED
        )


# ---------------------------------------------------------------------------
# E. Visibility
# ---------------------------------------------------------------------------


def test_a_reader_is_shown_no_file_of_a_restricted_opinion(
    client, matter, specialist, reader, organisation
):
    submission, document = _further_document(matter, specialist, organisation)
    Document.objects.filter(pk__in=[document.pk, submission.final_version.document_id]).update(
        visibility_override=Visibility.RESTRICTED
    )
    Submission.objects.filter(pk=submission.pk).update(visibility_override=Visibility.RESTRICTED)

    assert opinion_rail(matter, viewer=reader) == []
    client.force_login(reader)
    body = client.get(
        reverse("matters:matter_documents", kwargs={"pk": matter.pk})
    ).content.decode()
    assert "Selgitus.pdf" not in body
    assert "koda_opinion.asice" not in body
