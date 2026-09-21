"""The five smaller findings of the 2026-09-21 round, at the seams that own them.

Each is a small rule and each was reported because the product was silent where
it knew the answer:

* an opinion could be corrected but never given the paper that arrived a week
  later, because the position panel took files only at capture (QA-021);
* `Arvamus välja` was the one chronology row with no `Muuda` at all, on the
  record where a wrong date or recipient matters most (QA-023);
* the register answered `0 teemat` while the chips above it counted the matches
  one filter away (QA-019);
* a date far in the past saved without comment, where a future one is refused
  (QA-015 — asserted here as the rule it is *not*: nothing is rejected);
* two files of one name rendered as two identical rows (QA-016).
"""

from __future__ import annotations

import datetime

import pytest
from django.urls import reverse

from app.audit.enums import ChangeEventType
from app.audit.models import ChangeEvent
from app.matters.models import MatterExternalPosition
from app.submissions.enums import SentAtPrecision, SubmissionKind, SubmissionStatus
from app.submissions.services import (
    NOT_A_RECORDED_SEND,
    SentOpinionConflict,
    correct_sent_opinion,
    sent_opinion_revision,
)
from tests import factories

pytestmark = pytest.mark.django_db


def _sent(matter, specialist, organisation, capture_evidence, *, when=None, summary=""):
    """A recorded send, through the door a person actually uses.

    `register_sent_opinion` rather than a factory row:
    `submissions_sent_requires_timestamp_and_evidence` is a check constraint
    and not a convention, so a fixture that set the status directly would be
    testing a row the product cannot produce — and the send event this service
    writes is what puts `Arvamus välja` on the chronology, which is the surface
    these tests are about.
    """
    from app.documents.enums import DocumentRole
    from app.submissions.services import register_sent_opinion

    version = capture_evidence(
        matter,
        b"%PDF-1.4 synthetic evidence",
        "koja-arvamus.pdf",
        "application/pdf",
        title="Koja arvamus eelnõule",
        role=DocumentRole.KODA_SUBMISSION_FINAL,
    )
    submission = register_sent_opinion(
        document=version.document,
        version=version,
        title="Koja arvamus eelnõule",
        recipients=[organisation],
        sent_at=when or datetime.datetime(2026, 5, 14, 0, 0, tzinfo=datetime.UTC),
        sent_at_precision=SentAtPrecision.DATE,
        summary=summary or "Toetame eelnõu.",
        actor=specialist,
    )
    submission.refresh_from_db()
    return submission


# ---------------------------------------------------------------------------
# QA-023 — a recorded send can be corrected
# ---------------------------------------------------------------------------


def test_the_stated_facts_of_a_send_can_be_corrected(
    normal_matter, specialist, organisation, capture_evidence
):
    submission = _sent(normal_matter, specialist, organisation, capture_evidence)
    other = factories.OrganisationFactory()

    correct_sent_opinion(
        submission=submission,
        sent_at=datetime.datetime(2026, 5, 15, 0, 0, tzinfo=datetime.UTC),
        sent_at_precision=SentAtPrecision.DATE,
        summary="Toetame eelnõu pikema üleminekuajaga.",
        kind=SubmissionKind.FORMAL_OPINION,
        addressees=[other],
        actor=specialist,
    )

    submission.refresh_from_db()
    assert submission.sent_at.date() == datetime.date(2026, 5, 15)
    assert submission.summary == "Toetame eelnõu pikema üleminekuajaga."
    assert [row.organisation for row in submission.recipient_rows.all()] == [other]


def test_a_correction_touches_neither_the_evidence_nor_the_status(
    normal_matter, specialist, organisation, capture_evidence
):
    """The two invariants the brief names, asserted rather than assumed."""
    submission = _sent(normal_matter, specialist, organisation, capture_evidence)
    evidence = submission.final_version_id
    sender = submission.sent_by_id

    correct_sent_opinion(
        submission=submission,
        sent_at=datetime.datetime(2026, 5, 15, 0, 0, tzinfo=datetime.UTC),
        sent_at_precision=SentAtPrecision.DATE,
        summary="Parandatud kokkuvõte.",
        kind=SubmissionKind.FORMAL_OPINION,
        actor=specialist,
    )

    submission.refresh_from_db()
    assert submission.final_version_id == evidence
    assert submission.status == SubmissionStatus.SENT
    # Who sent the letter is not who corrected the record.
    assert submission.sent_by_id == sender


def test_a_correction_keeps_the_teadmiseks_recipients(
    normal_matter, specialist, organisation, capture_evidence
):
    """`set_recipients` replaces the whole set, so the other half must be handed back."""
    from app.submissions.enums import RecipientRole
    from app.submissions.services import set_recipients

    submission = _sent(normal_matter, specialist, organisation, capture_evidence)
    committee = factories.OrganisationFactory()
    set_recipients(
        submission=submission,
        addressees=[organisation],
        for_information=[committee],
        actor=specialist,
    )
    submission.refresh_from_db()

    correct_sent_opinion(
        submission=submission,
        sent_at=submission.sent_at,
        sent_at_precision=submission.sent_at_precision,
        summary=submission.summary,
        kind=submission.kind,
        addressees=[organisation],
        actor=specialist,
    )

    copied = submission.recipient_rows.filter(role=RecipientRole.FOR_INFORMATION)
    assert [row.organisation for row in copied] == [committee]


def test_the_correction_writes_its_own_audit_event(
    normal_matter, specialist, organisation, capture_evidence
):
    submission = _sent(normal_matter, specialist, organisation, capture_evidence)

    correct_sent_opinion(
        submission=submission,
        sent_at=datetime.datetime(2026, 5, 15, 0, 0, tzinfo=datetime.UTC),
        sent_at_precision=SentAtPrecision.DATE,
        summary=submission.summary,
        kind=submission.kind,
        actor=specialist,
    )

    event = ChangeEvent.objects.get(
        event_type=ChangeEventType.SUBMISSION_CORRECTED, matter=normal_matter
    )
    assert event.payload["from"]["sent_at"].startswith("2026-05-14")
    assert event.payload["to"]["sent_at"].startswith("2026-05-15")
    # And it is not a second send: the one `register_sent_opinion` wrote is
    # still the only one on the file.
    assert (
        ChangeEvent.objects.filter(
            event_type=ChangeEventType.SUBMISSION_SENT, matter=normal_matter
        ).count()
        == 1
    )


def test_a_save_that_changes_nothing_writes_no_audit_row(
    normal_matter, specialist, organisation, capture_evidence
):
    submission = _sent(normal_matter, specialist, organisation, capture_evidence)

    correct_sent_opinion(
        submission=submission,
        sent_at=submission.sent_at,
        sent_at_precision=submission.sent_at_precision,
        summary=submission.summary,
        kind=submission.kind,
        actor=specialist,
    )

    assert not ChangeEvent.objects.filter(event_type=ChangeEventType.SUBMISSION_CORRECTED).exists()


def test_a_draft_is_refused(normal_matter, specialist):
    from app.core.errors import DomainError

    draft = factories.SubmissionFactory(matter=normal_matter, status=SubmissionStatus.DRAFT)

    with pytest.raises(DomainError) as refusal:
        correct_sent_opinion(
            submission=draft,
            sent_at=datetime.datetime(2026, 5, 15, 0, 0, tzinfo=datetime.UTC),
            sent_at_precision=SentAtPrecision.DATE,
            summary="",
            kind=SubmissionKind.FORMAL_OPINION,
            actor=specialist,
        )

    assert str(refusal.value) == NOT_A_RECORDED_SEND


def test_a_stale_second_tab_writes_nothing(
    normal_matter, specialist, organisation, capture_evidence
):
    submission = _sent(normal_matter, specialist, organisation, capture_evidence)
    stale = sent_opinion_revision(submission)
    correct_sent_opinion(
        submission=submission,
        sent_at=datetime.datetime(2026, 5, 15, 0, 0, tzinfo=datetime.UTC),
        sent_at_precision=SentAtPrecision.DATE,
        summary=submission.summary,
        kind=submission.kind,
        actor=specialist,
    )

    with pytest.raises(SentOpinionConflict):
        correct_sent_opinion(
            submission=submission,
            sent_at=datetime.datetime(2026, 1, 1, 0, 0, tzinfo=datetime.UTC),
            sent_at_precision=SentAtPrecision.DATE,
            summary=submission.summary,
            kind=submission.kind,
            actor=specialist,
            expected_revision=stale,
        )

    submission.refresh_from_db()
    assert submission.sent_at.date() == datetime.date(2026, 5, 15)


def test_the_row_offers_muuda(client, normal_matter, specialist, organisation, capture_evidence):
    submission = _sent(normal_matter, specialist, organisation, capture_evidence)
    client.force_login(specialist)

    body = client.get(
        reverse("matters:matter_detail", kwargs={"pk": normal_matter.pk})
    ).content.decode()

    assert f"koja-arvamus-{submission.pk}-muuda" in body


def test_a_reader_is_offered_nothing_and_reaches_nothing(
    client, normal_matter, specialist, organisation, reader, capture_evidence
):
    submission = _sent(normal_matter, specialist, organisation, capture_evidence)
    client.force_login(reader)

    body = client.get(
        reverse("matters:matter_detail", kwargs={"pk": normal_matter.pk})
    ).content.decode()
    assert f"koja-arvamus-{submission.pk}-muuda" not in body

    response = client.post(
        reverse(
            "matters:update_sent_opinion",
            kwargs={"pk": normal_matter.pk, "submission_id": submission.pk},
        )
    )
    assert response.status_code == 404


# ---------------------------------------------------------------------------
# QA-021 — a position takes a paper that arrives later
# ---------------------------------------------------------------------------


def _position(matter, specialist, organisation):
    from app.matters.enums import ExternalPositionProvenance

    return MatterExternalPosition.objects.create(
        matter=matter,
        organisation=organisation,
        provenance=ExternalPositionProvenance.RECEIVED,
        summary="Toetab pikemat üleminekuaega.",
        created_by=specialist,
    )


def test_a_paper_can_be_attached_to_a_position_after_the_fact(
    normal_matter, specialist, organisation
):
    from django.core.files.uploadedfile import SimpleUploadedFile

    from app.matters.workspace import add_external_position_evidence

    position = _position(normal_matter, specialist, organisation)
    result = add_external_position_evidence(
        position=position,
        author=specialist,
        uploads=[SimpleUploadedFile("seisukoht.pdf", b"%PDF-1.4 Liidu seisukoht.")],
    )

    assert len(result.documents) == 1
    assert position.document_links.count() == 1
    assert ChangeEvent.objects.filter(
        event_type=ChangeEventType.EXTERNAL_POSITION_DOCUMENT_LINKED, matter=normal_matter
    ).exists()


def test_attaching_a_paper_changes_nothing_the_record_says(normal_matter, specialist, organisation):
    """`Muuda` corrects; this adds. A capture that edited would blur both."""
    from django.core.files.uploadedfile import SimpleUploadedFile

    from app.matters.workspace import add_external_position_evidence

    position = _position(normal_matter, specialist, organisation)
    add_external_position_evidence(
        position=position,
        author=specialist,
        uploads=[SimpleUploadedFile("seisukoht.pdf", b"%PDF-1.4 Liidu seisukoht.")],
    )

    position.refresh_from_db()
    assert position.summary == "Toetab pikemat üleminekuaega."
    assert position.organisation_id == organisation.pk


def test_the_position_row_offers_lisa_fail(client, normal_matter, specialist, organisation):
    position = _position(normal_matter, specialist, organisation)
    client.force_login(specialist)

    body = client.get(
        reverse("matters:matter_detail", kwargs={"pk": normal_matter.pk})
    ).content.decode()

    assert f"valine-seisukoht-{position.pk}-toend" in body


# ---------------------------------------------------------------------------
# QA-019 — the register says where the matches are
# ---------------------------------------------------------------------------


def _register(client, **params):
    return client.get(reverse("matters:matter_list"), params).content.decode()


def test_an_empty_filtered_register_says_the_matches_are_elsewhere(signed_in, specialist):
    """The chips already counted them; the empty state was the dead end."""
    from app.matters.services import close_matter
    from app.workflow.enums import Disposition

    matter = factories.MatterFactory(owner=specialist, title="QA hiline pakendiseadus")
    close_matter(matter=matter, disposition=Disposition.COMPLETED, actor=specialist)

    body = _register(signed_in, q="hiline")

    assert "Vasteid leidub ka teistes olekutes." in body
    assert "olek=koik" in body


def test_a_register_with_rows_says_nothing_of_the_kind(signed_in, specialist):
    factories.MatterFactory(owner=specialist, title="QA avatud pakendiseadus")

    body = _register(signed_in, q="pakendiseadus")

    assert "Vasteid leidub ka teistes olekutes." not in body


def test_the_widest_filter_offers_no_further_one(signed_in, specialist):
    factories.MatterFactory(owner=specialist, title="QA pakendiseadus")

    body = _register(signed_in, q="seda-sõna-ei-ole", olek="koik")

    assert "Vasteid leidub ka teistes olekutes." not in body


def test_the_hint_is_counted_after_authorization(client, specialist, reader):
    """«There is something you cannot see» is a disclosure, so it is not said."""
    from app.core.enums import Visibility
    from app.matters.services import close_matter
    from app.workflow.enums import Disposition

    matter = factories.MatterFactory(
        owner=specialist, title="QA salajane pakendiseadus", visibility=Visibility.RESTRICTED
    )
    close_matter(matter=matter, disposition=Disposition.COMPLETED, actor=specialist)
    client.force_login(reader)

    body = _register(client, q="salajane")

    assert "Vasteid leidub ka teistes olekutes." not in body


# ---------------------------------------------------------------------------
# QA-015 — an old date is flagged, never refused
# ---------------------------------------------------------------------------


def test_a_genuinely_historical_date_still_saves(normal_matter, specialist):
    """The half of QA-015 that must not become a policy.

    The warning is a client-side check at input time. The server keeps taking
    old dates, because the register archive holds them and a hard cutoff would
    block exactly the material the migration exists for.
    """
    from app.matters.workspace import add_procedural_development
    from app.workflow.enums import DatePrecision

    result = add_procedural_development(
        matter=normal_matter,
        author=specialist,
        title="Eelnõu algatati",
        occurred_on=datetime.date(1996, 3, 14),
        occurred_on_precision=DatePrecision.EXACT.value,
    )

    assert result.record.occurred_on == datetime.date(1996, 3, 14)


# ---------------------------------------------------------------------------
# QA-016 — two files of one name are distinguishable
# ---------------------------------------------------------------------------


def test_same_named_documents_are_marked_apart(specialist):
    from app.matters.views import _mark_duplicate_names

    matter = factories.MatterFactory(owner=specialist)
    first = factories.DocumentFactory(matter=matter, title="dup.txt")
    second = factories.DocumentFactory(matter=matter, title="dup.txt")
    alone = factories.DocumentFactory(matter=matter, title="ainus.txt")

    _mark_duplicate_names([first, second, alone])

    assert getattr(first, "duplicate_hint", "")
    assert getattr(second, "duplicate_hint", "")
    # A name that appears once carries nothing extra.
    assert not getattr(alone, "duplicate_hint", "")


def test_the_mark_exposes_nothing_internal(specialist):
    from app.matters.views import _mark_duplicate_names

    matter = factories.MatterFactory(owner=specialist)
    rows = [
        factories.DocumentFactory(matter=matter, title="dup.txt"),
        factories.DocumentFactory(matter=matter, title="dup.txt"),
    ]

    _mark_duplicate_names(rows)

    for document in rows:
        hint = document.duplicate_hint
        assert str(document.pk) not in hint
        assert "/" not in hint


def test_two_files_of_one_name_under_one_row_are_told_apart(normal_matter, specialist):
    """The inline list, which is the other place a name was the only label."""
    from django.core.files.uploadedfile import SimpleUploadedFile

    from app.matters.timeline import matter_timeline
    from app.matters.workspace import add_matter_note

    add_matter_note(
        matter=normal_matter,
        author=specialist,
        body="<p>Kaks lisa.</p>",
        uploads=[
            SimpleUploadedFile("lisa.pdf", b"%PDF-1.4 esimene"),
            SimpleUploadedFile("lisa.pdf", b"%PDF-1.4 teine, tunduvalt pikem sisu siin"),
        ],
    )

    page, _more = matter_timeline(matter=normal_matter, user=specialist)
    files = [file for item in page for file in item.files]

    assert len(files) == 2
    assert {file.label for file in files} == {"lisa.pdf"}
    # Different bytes, different sizes, and the row says so.
    assert files[0].detail and files[1].detail
    assert files[0].detail != files[1].detail


def test_a_single_file_carries_no_detail(normal_matter, specialist):
    from django.core.files.uploadedfile import SimpleUploadedFile

    from app.matters.timeline import matter_timeline
    from app.matters.workspace import add_matter_note

    add_matter_note(
        matter=normal_matter,
        author=specialist,
        body="<p>Üks lisa.</p>",
        uploads=[SimpleUploadedFile("lisa.pdf", b"%PDF-1.4 ainus")],
    )

    page, _more = matter_timeline(matter=normal_matter, user=specialist)
    files = [file for item in page for file in item.files]

    assert len(files) == 1
    assert files[0].detail == ""
