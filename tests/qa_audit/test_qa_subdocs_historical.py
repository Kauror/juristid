"""Independent QA 2026-10-14 — subdocs area: historical records (ADR 0012, 0015, 0019, 0020, 0146 §9).

A register-archive row with a historical opinion filed the way the archive apply
files one (below the interactive wrappers). Provenance is kept, nothing modern
is invented, and no interactive act turns history into today's work. Probes
against main 81a7d6e4.
"""  # noqa: E501

from __future__ import annotations

import datetime as dt

import pytest
from django.urls import reverse
from django.utils import timezone

from app.audit.enums import ChangeEventType
from app.audit.models import ChangeEvent
from app.documents.enums import DocumentRole
from app.documents.services import add_evidence_version, create_document
from app.legacy_import.models import MatterSourceReference
from app.submissions.enums import SentAtPrecision, SubmissionStatus
from app.submissions.services import (
    correct_sent_opinion,
    register_sent_opinion,
    sent_opinion_revision,
)
from app.workflow.models import NextAction, OpinionFollowUp
from tests import factories
from tests.follow_ups import PDF

pytestmark = pytest.mark.django_db

SENT_2019 = dt.date(2019, 3, 14)


@pytest.fixture
def archive_opinion(specialist, organisation):
    matter = factories.ArchiveMatterFactory(title="Ajalooline eelnõu 2019", is_open=False)
    reference = factories.MatterSourceReferenceFactory(matter=matter, source_row_number=77)
    document = create_document(
        matter=matter, title="Koja arvamus 2019", role=DocumentRole.KODA_SUBMISSION_FINAL
    )
    version = add_evidence_version(
        document=document,
        content=PDF + b"2019",
        original_filename="arvamus_2019.pdf",
        mime_type="application/pdf",
    )
    submission = register_sent_opinion(
        document=document,
        version=version,
        title="Koja arvamus 2019",
        recipients=[organisation],
        sent_at=timezone.make_aware(dt.datetime.combine(SENT_2019, dt.time.min)),
        sent_at_precision=SentAtPrecision.DATE,
    )
    return matter, reference, submission


def test_a_historical_send_schedules_no_check(archive_opinion):
    _, _, submission = archive_opinion
    assert submission.status == SubmissionStatus.SENT
    assert not OpinionFollowUp.objects.filter(submission=submission).exists()
    assert not NextAction.objects.filter(matter=submission.matter).exists()


def test_historical_record_reads_for_specialist_and_reader_without_inventing_dates(
    client, specialist, reader, archive_opinion
):
    matter, _, _ = archive_opinion
    today = timezone.localdate()
    today_et = f"{today.day}.{today.month}.{today.year}"
    for persona in (specialist, reader):
        client.force_login(persona)
        page = client.get(reverse("matters:matter_detail", kwargs={"pk": matter.pk}))
        assert page.status_code == 200
        body = page.content.decode()
        assert "14.3.2019" in body or "14.03.2019" in body
        # Nothing on a 2019 archive row happened today. The writer's collapsed
        # `+ Saabus` editor used to pre-fill today in its input (DC-03); since
        # D5 (docs/adr/0166 §5) it starts empty here, so no hit is allowed at all.
        assert today_et not in body


def test_closed_historical_record_refuses_new_business_content(
    signed_in, specialist, organisation, archive_opinion
):
    from django.core.files.uploadedfile import SimpleUploadedFile

    from app.documents.models import Document
    from app.matters.models import MatterEngagement
    from app.submissions.models import Submission

    matter, _, _ = archive_opinion
    documents_before = Document.objects.filter(matter=matter).count()

    signed_in.post(
        reverse("documents:upload_evidence", kwargs={"matter_id": matter.pk}),
        {
            "role": DocumentRole.INCOMING_AUTHORITY,
            "upload": SimpleUploadedFile("uus.pdf", PDF, content_type="application/pdf"),
        },
    )
    signed_in.post(
        reverse("matters:add_engagement_compact", kwargs={"pk": matter.pk}),
        {"audience": "Liikmed", "occurred_on": "", "feedback_deadline": ""},
        headers={"HX-Request": "true"},
    )
    signed_in.post(
        reverse("matters:add_koda_opinion", kwargs={"pk": matter.pk}),
        {
            "upload": SimpleUploadedFile("a.pdf", PDF + b"n", content_type="application/pdf"),
            "recipients": [str(organisation.pk)],
            "sent_on": "1.10.2026",
            "summary": "x",
        },
        headers={"HX-Request": "true"},
    )

    assert Document.objects.filter(matter=matter).count() == documents_before
    assert not MatterEngagement.objects.filter(matter=matter).exists()
    assert Submission.objects.filter(matter=matter).count() == 1


def test_correcting_a_historical_send_keeps_import_provenance_and_schedules_nothing(
    specialist, archive_opinion
):
    matter, reference, submission = archive_opinion
    before = MatterSourceReference.objects.filter(pk=reference.pk).values().get()

    correct_sent_opinion(
        submission=submission,
        sent_at=timezone.make_aware(dt.datetime.combine(dt.date(2019, 3, 15), dt.time.min)),
        sent_at_precision=SentAtPrecision.DATE,
        summary="Parandatud",
        kind=submission.kind,
        actor=specialist,
        expected_revision=sent_opinion_revision(submission),
    )

    assert MatterSourceReference.objects.filter(pk=reference.pk).values().get() == before
    assert not OpinionFollowUp.objects.filter(submission=submission).exists()
    event = ChangeEvent.objects.get(matter=matter, event_type=ChangeEventType.SUBMISSION_CORRECTED)
    assert event.payload["from"]["sent_at"].startswith("2019-03-1")
