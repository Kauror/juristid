"""What the apply records about itself once a Submission actually exists.

A candidate that produced a canonical Submission is finished work. Leaving it
PENDING put it straight back into `/haldus/arvamuste-ulevaatus/` and into the
`opinion_archive status` "ülevaatust ootel" count, so the queue overstated what
a person still had to look at — and the import row could not say which candidate
justified it.

The interesting tests here are the ones that must *not* move: a review-required
row, an unmatched one, a withheld same-day bundle and a failed evidence write
all have to stay pending, because APPLIED means "this produced a Submission",
not "the plan expected it to".

All data is synthetic. No real archive file, register row or name appears.
"""

from __future__ import annotations

import datetime

import pytest
from django.urls import reverse
from django.utils import timezone

from app.core.enums import Visibility
from app.documents.enums import DocumentRole
from app.documents.models import Document, DocumentVersion
from app.documents.services import add_evidence_version, create_document
from app.legacy_import.opinion_apply import apply_plan, open_batch
from app.legacy_import.opinion_archive import (
    OpinionMatchCandidate,
    OpinionSubmissionImport,
)
from app.legacy_import.opinion_enums import OpinionCandidateState
from app.legacy_import.opinion_plan import build_plan
from app.legacy_import.parser import SOURCE_SYSTEM
from app.legacy_import.source_pages import (
    LegacySourcePage,
    LegacySourceResource,
    MatterSourcePage,
    SourceMatchMethod,
)
from app.matters.models import Matter
from app.submissions.enums import SubmissionKind, SubmissionStatus
from app.submissions.models import Submission, SubmissionRecipient
from tests import factories
from tests import synthetic_opinions as syn

pytestmark = pytest.mark.django_db


def register_matter(*, year: int, number: int, title: str, sent: str | None, counterparty: str):
    """A Matter as the Excel importer would have left it."""
    matter = factories.ArchiveMatterFactory(
        reference_year=year, reference_number=number, title=title, visibility=Visibility.NORMAL
    )
    factories.MatterSourceReferenceFactory(
        matter=matter,
        source_system=SOURCE_SYSTEM,
        source_sheet=str(year),
        source_row_number=number,
        source_row_raw={
            "A": f"{year}_{number}",
            "B": title,
            "F": sent or "",
            "G": counterparty,
        },
    )
    return matter


@pytest.fixture
def archive_path(tmp_path):
    def build(opinions):
        return syn.write_archive(tmp_path / "Opinions.zip", opinions)

    return build


def plan_for(archive):
    return build_plan(archive_path=archive, kodadash_path=None)


def strict_pair(number: int = 21, *, sent: str | None = "2024-04-10"):
    """A register row and an archive file that match on three exact signals."""
    matter = register_matter(
        year=2024,
        number=number,
        title="Näidisregistri seaduse muutmise seadus",
        sent=sent,
        counterparty="Näidisministeerium",
    )
    item = syn.opinion(
        date="2024-04-10",
        recipient="Näidisministeerium",
        title="Arvamus näidisregistri seaduse muutmise kohta",
        marker=f"state-{number}",
    )
    return matter, item


def attach_to_onenote(matters, data: bytes) -> None:
    """Put these exact bytes on a OneNote page the given Matters claim.

    Gives an occurrence an exact-binary Matter without a register sent date,
    which is the shape a reviewer is asked to decide about.
    """
    page = LegacySourcePage.objects.create(
        source_page_id=f"page-{syn.sha256(data)[:12]}",
        page_key=f"key-{syn.sha256(data)[:12]}",
        source_notebook="oigus",
        source_section="ARHIIV",
        title="Leht",
        capture_id="capture-1",
        first_imported_at=datetime.datetime(2026, 1, 1, tzinfo=datetime.UTC),
        latest_imported_at=datetime.datetime(2026, 1, 1, tzinfo=datetime.UTC),
    )
    LegacySourceResource.objects.create(
        source_page=page,
        resource_key="resource-1",
        original_filename="lisatud.pdf",
        source_block_ordinal=3,
        sha256=syn.sha256(data),
        size_bytes=len(data),
        archive_relative_path="pages/x/resource-1.pdf",
    )
    for matter in matters:
        MatterSourcePage.objects.create(
            matter=matter, source_page=page, match_method=SourceMatchMethod.EXCEL_EXACT_PAGE_ID
        )


def states():
    return list(OpinionMatchCandidate.objects.values_list("state", flat=True))


def counts():
    return (
        Submission.objects.count(),
        Document.objects.count(),
        DocumentVersion.objects.count(),
        SubmissionRecipient.objects.count(),
        OpinionSubmissionImport.objects.count(),
    )


# =========================================================================
# A successful automatic application
# =========================================================================


def test_an_automatic_match_leaves_its_candidate_applied(archive_path):
    matter, item = strict_pair()
    plan = plan_for(archive_path([item]))
    apply_plan(plan, batch=open_batch(plan))

    assert OpinionMatchCandidate.objects.get(matter=matter).state == OpinionCandidateState.APPLIED


def test_the_import_row_names_the_candidate_that_justified_it(archive_path):
    matter, item = strict_pair(number=22)
    plan = plan_for(archive_path([item]))
    apply_plan(plan, batch=open_batch(plan))

    record = OpinionSubmissionImport.objects.get()
    candidate = OpinionMatchCandidate.objects.get(matter=matter)
    assert record.candidate_id == candidate.pk
    # One reconstruction decision, so the class on both sides has to agree.
    assert record.match_class == candidate.match_class


def test_an_applied_candidate_is_no_longer_pending(archive_path):
    """The authoritative state, not a template that hides the row."""
    _matter, item = strict_pair(number=23)
    plan = plan_for(archive_path([item]))
    apply_plan(plan, batch=open_batch(plan))

    assert OpinionMatchCandidate.objects.filter(state=OpinionCandidateState.PENDING).count() == 0


def test_an_applied_candidate_leaves_the_review_queue(client, archive_path, administrator):
    _matter, item = strict_pair(number=24)
    plan = plan_for(archive_path([item]))
    apply_plan(plan, batch=open_batch(plan))

    client.force_login(administrator)
    response = client.get(reverse("legacy_import:opinion_queue"))

    assert response.status_code == 200
    assert list(response.context["candidates"]) == []


def test_an_automatic_application_records_no_human_reviewer(archive_path):
    """The system is not a person and must not be filed as one."""
    matter, item = strict_pair(number=25)
    plan = plan_for(archive_path([item]))
    apply_plan(plan, batch=open_batch(plan))

    candidate = OpinionMatchCandidate.objects.get(matter=matter)
    assert candidate.state == OpinionCandidateState.APPLIED
    assert candidate.decided_by is None
    assert candidate.decided_at is None
    assert candidate.decision_note == ""


# =========================================================================
# Everything that must stay pending
# =========================================================================


def test_a_review_required_candidate_stays_pending(archive_path):
    """Date and addressee are two signals. Two is not identity."""
    register_matter(
        year=2024, number=31, title="Esimene", sent="2024-05-06", counterparty="Näidisministeerium"
    )
    register_matter(
        year=2024, number=32, title="Teine", sent="2024-05-06", counterparty="Näidisministeerium"
    )
    item = syn.opinion(
        date="2024-05-06", recipient="Näidisministeerium", title="Arvamus", marker="review-31"
    )
    plan = plan_for(archive_path([item]))
    apply_plan(plan, batch=open_batch(plan))

    assert OpinionSubmissionImport.objects.count() == 0
    assert OpinionCandidateState.APPLIED not in states()


def test_an_unmatched_candidate_stays_pending(archive_path):
    item = syn.opinion(
        date="2024-06-01", recipient="Tundmatu asutus", title="Arvamus", marker="unmatched-1"
    )
    plan = plan_for(archive_path([item]))
    apply_plan(plan, batch=open_batch(plan))

    assert OpinionSubmissionImport.objects.count() == 0
    assert OpinionCandidateState.APPLIED not in states()


def test_a_matter_with_no_defensible_date_stays_pending(archive_path):
    """No sent date, no canonical Submission — so nothing was applied.

    Without a `VÄLJA` the register row cannot supply the third signal either,
    so the occurrence ends up UNMATCHED and its candidate carries no Matter at
    all. Assert on the candidate for the *item*, which is the row that exists.
    """
    matter, item = strict_pair(number=33, sent=None)
    plan = plan_for(archive_path([item]))
    apply_plan(plan, batch=open_batch(plan))

    assert Submission.objects.filter(matter=matter).count() == 0
    assert OpinionSubmissionImport.objects.count() == 0
    candidate = OpinionMatchCandidate.objects.get(item__sha256=item.sha256)
    assert candidate.state == OpinionCandidateState.PENDING


def test_a_same_day_bundle_stays_review_work(archive_path):
    """One letter plus its annex is one sent action, withheld for a person."""
    matter, first = strict_pair(number=41)
    second = syn.opinion(
        date="2024-04-10",
        recipient="Näidisministeerium",
        title="Arvamus näidisregistri seaduse muutmise kohta Lisa 1",
        marker="bundle-41b",
    )
    plan = plan_for(archive_path([first, second]))
    apply_plan(plan, batch=open_batch(plan))

    assert Submission.objects.filter(matter=matter).count() == 0
    assert OpinionCandidateState.APPLIED not in states()


def test_a_failed_evidence_write_never_marks_the_candidate_applied(archive_path, monkeypatch):
    """Otherwise: gone from the queue and absent from the archive at once."""
    from app.legacy_import import opinion_apply

    matter, item = strict_pair(number=51)
    monkeypatch.setattr(opinion_apply, "_final_version_for", lambda *a, **k: (None, False))

    plan = plan_for(archive_path([item]))
    report = apply_plan(plan, batch=open_batch(plan))

    assert report.submissions_created == 0
    assert Submission.objects.filter(matter=matter).count() == 0
    assert OpinionSubmissionImport.objects.count() == 0
    assert OpinionMatchCandidate.objects.get(matter=matter).state == OpinionCandidateState.PENDING


# =========================================================================
# Linking, rerunning and repairing
# =========================================================================


def test_linking_an_existing_submission_also_applies_the_candidate(archive_path, specialist):
    """The bytes are already the final evidence; provenance attaches to them."""
    matter, item = strict_pair(number=61)
    document = create_document(
        matter=matter, title="Olemasolev", role=DocumentRole.KODA_SUBMISSION_FINAL
    )
    version = add_evidence_version(
        document=document,
        content=item.data,
        original_filename=item.name.rsplit("/", 1)[-1],
        mime_type="application/pdf",
        acquired_at=timezone.now(),
        uploaded_by=specialist,
    )
    Submission.objects.create(
        matter=matter,
        kind=SubmissionKind.FORMAL_OPINION,
        title="Olemasolev arvamus",
        status=SubmissionStatus.SENT,
        sent_at=timezone.now(),
        final_version=version,
    )

    plan = plan_for(archive_path([item]))
    report = apply_plan(plan, batch=open_batch(plan))

    assert report.submissions_created == 0
    assert Submission.objects.filter(matter=matter).count() == 1
    record = OpinionSubmissionImport.objects.get()
    assert record.candidate_id is not None
    assert (
        OpinionMatchCandidate.objects.get(pk=record.candidate_id).state
        == OpinionCandidateState.APPLIED
    )


def test_applying_twice_writes_nothing_the_second_time(archive_path):
    matter, item = strict_pair(number=71)
    archive = archive_path([item])

    first = plan_for(archive)
    apply_plan(first, batch=open_batch(first))
    before = counts()

    second = plan_for(archive)
    report = apply_plan(second, batch=open_batch(second))

    assert report.submissions_created == 0
    assert counts() == before
    assert Submission.objects.filter(matter=matter).count() == 1


def test_the_candidate_link_survives_a_complete_rerun(archive_path):
    matter, item = strict_pair(number=91)
    archive = archive_path([item])
    first = plan_for(archive)
    apply_plan(first, batch=open_batch(first))
    candidate_id = OpinionSubmissionImport.objects.get().candidate_id

    second = plan_for(archive)
    apply_plan(second, batch=open_batch(second))

    assert OpinionSubmissionImport.objects.get().candidate_id == candidate_id
    assert OpinionMatchCandidate.objects.get(matter=matter).state == OpinionCandidateState.APPLIED


def test_a_rerun_repairs_bookkeeping_left_by_an_earlier_build(archive_path):
    """The shape a pre-fix or partial deployment leaves behind.

    A canonical Submission exists, its import row exists, and the candidate is
    still sitting in the queue claiming somebody must look at it. A rerun has to
    finish the record without writing a second anything.
    """
    matter, item = strict_pair(number=81)
    archive = archive_path([item])
    first = plan_for(archive)
    apply_plan(first, batch=open_batch(first))

    # Wind the bookkeeping back to the broken shape.
    OpinionSubmissionImport.objects.update(candidate=None)
    OpinionMatchCandidate.objects.update(state=OpinionCandidateState.PENDING)
    before = counts()

    second = plan_for(archive)
    apply_plan(second, batch=open_batch(second))

    assert counts() == before, "the repair must create nothing"
    candidate = OpinionMatchCandidate.objects.get(matter=matter)
    assert OpinionSubmissionImport.objects.get().candidate_id == candidate.pk
    assert candidate.state == OpinionCandidateState.APPLIED


def test_a_reviewed_candidate_keeps_its_reviewer_when_it_becomes_applied(
    archive_path, administrator
):
    """APPLIED is a workflow state, not a decision. The person stays the person."""
    matter, item = strict_pair(number=101, sent=None)
    # The exact binary gives the candidate its Matter; the missing VÄLJA is
    # what stops it being applied automatically, which is exactly the row a
    # reviewer is asked to decide about.
    attach_to_onenote([matter], item.data)
    archive = archive_path([item])

    # Catalogue the archive so the candidate row exists; no date, so nothing
    # is applied automatically.
    first = plan_for(archive)
    apply_plan(first, batch=open_batch(first))
    assert OpinionSubmissionImport.objects.count() == 0

    decided = timezone.now()
    OpinionMatchCandidate.objects.filter(matter=matter).update(
        state=OpinionCandidateState.LINKED,
        review_approves_submission=True,
        reviewed_sent_date=datetime.date(2024, 4, 10),
        decided_by=administrator,
        decided_at=decided,
        decision_note="Kontrollitud käsitsi.",
    )

    second = plan_for(archive)
    apply_plan(second, batch=open_batch(second))

    candidate = OpinionMatchCandidate.objects.get(matter=matter)
    assert candidate.state == OpinionCandidateState.APPLIED
    assert candidate.decided_by == administrator
    assert candidate.decided_at == decided
    assert candidate.decision_note == "Kontrollitud käsitsi."
    assert candidate.reviewed_sent_date == datetime.date(2024, 4, 10)
    assert candidate.review_approves_submission is True
    assert OpinionSubmissionImport.objects.get().candidate_id == candidate.pk


def test_no_matter_is_reopened_or_promoted_by_the_apply(archive_path):
    """Bookkeeping only. The historical lifecycle is somebody else's business."""
    matter, item = strict_pair(number=121)
    Matter.objects.filter(pk=matter.pk).update(is_open=False)

    plan = plan_for(archive_path([item]))
    apply_plan(plan, batch=open_batch(plan))

    matter.refresh_from_db()
    assert matter.is_open is False
    assert matter.record_mode == "ARCHIVE"
