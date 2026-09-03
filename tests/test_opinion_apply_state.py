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
import io

import pytest
from django.core.management import call_command
from django.core.management.base import CommandError
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
from app.legacy_import.opinion_enums import OpinionCandidateState, SentDateBasis
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


# =========================================================================
# A person's decision outranks the next automatic run
# =========================================================================
#
# The automatic pass rebuilds its proposals from the sources every time and
# knows nothing, by construction, about who has looked at the queue since. So a
# reviewer who rejected a file, called it a duplicate, said it was not an
# opinion, deferred it, or linked it to a Matter without asserting it was sent
# had their answer overwritten by the next `opinion_archive apply` — either by
# creating the canonical SENT Submission they had refused, or by flipping the
# row itself back to APPLIED.
#
# Both directions are covered below, because they fail independently: the
# planner decides whether a Submission is written, and the apply decides what
# the candidate row ends up saying.


def decided_states():
    """Every terminal state a reviewer can put a row into from `/haldus/`."""
    return [
        OpinionCandidateState.REJECTED,
        OpinionCandidateState.DUPLICATE,
        OpinionCandidateState.NOT_AN_OPINION,
        OpinionCandidateState.DEFERRED,
    ]


@pytest.mark.parametrize("decision", decided_states())
def test_a_decided_candidate_is_never_applied_by_a_later_run(archive_path, administrator, decision):
    """The shape that loses a decision: the Submission is gone, the row is not.

    Somebody applied the archive, saw the result was wrong, deleted the
    Submission — which takes the import row with it — and recorded why in the
    queue. Nothing in the sources changed, so the next run proposes exactly the
    same automatic match, and before this fix it filed it again.
    """
    matter, item = strict_pair(number=201)
    archive = archive_path([item])
    first = plan_for(archive)
    apply_plan(first, batch=open_batch(first))

    Submission.objects.filter(matter=matter).delete()
    assert OpinionSubmissionImport.objects.count() == 0, "deleting the Submission clears the import"
    OpinionMatchCandidate.objects.filter(matter=matter).update(
        state=decision,
        decided_by=administrator,
        decided_at=timezone.now(),
        decision_note="Vaadatud üle ja tagasi lükatud.",
    )

    second = plan_for(archive)
    report = apply_plan(second, batch=open_batch(second))

    assert report.submissions_created == 0
    assert Submission.objects.filter(matter=matter).count() == 0
    assert OpinionSubmissionImport.objects.count() == 0
    candidate = OpinionMatchCandidate.objects.get(matter=matter)
    assert candidate.state == decision
    assert candidate.decided_by == administrator
    assert candidate.decision_note == "Vaadatud üle ja tagasi lükatud."


@pytest.mark.parametrize("decision", decided_states())
def test_a_decision_taken_after_the_apply_is_not_flipped_back(
    archive_path, administrator, decision
):
    """The other shape: the Submission survives, so the rerun stops early.

    It still reached `_mark_candidate_applied` with the import row's candidate,
    which is how a REJECTED row silently became APPLIED again without anything
    else in the database moving.
    """
    matter, item = strict_pair(number=202)
    archive = archive_path([item])
    first = plan_for(archive)
    apply_plan(first, batch=open_batch(first))

    OpinionMatchCandidate.objects.filter(matter=matter).update(
        state=decision, decided_by=administrator, decided_at=timezone.now()
    )
    before = counts()

    second = plan_for(archive)
    apply_plan(second, batch=open_batch(second))

    assert counts() == before
    assert OpinionMatchCandidate.objects.get(matter=matter).state == decision


def test_a_matter_linked_without_approving_the_sending_is_not_applied(archive_path, administrator):
    """Seo teemaga is deliberately the answer that stops short of SENT.

    A reviewer who can say which Matter a letter belongs to should not have to
    claim it went out in order to record that. An automatic run that then claims
    it for them makes the distinction the queue offers meaningless (brief 26).
    """
    matter, item = strict_pair(number=203)
    archive = archive_path([item])
    first = plan_for(archive)
    apply_plan(first, batch=open_batch(first))
    Submission.objects.filter(matter=matter).delete()

    OpinionMatchCandidate.objects.filter(matter=matter).update(
        state=OpinionCandidateState.LINKED,
        review_approves_submission=False,
        decided_by=administrator,
        decided_at=timezone.now(),
    )

    second = plan_for(archive)
    apply_plan(second, batch=open_batch(second))

    assert Submission.objects.filter(matter=matter).count() == 0
    assert OpinionMatchCandidate.objects.get(matter=matter).state == OpinionCandidateState.LINKED


def test_the_plan_says_out_loud_that_a_decision_withheld_the_file(archive_path, administrator):
    """A file that silently stops appearing reads as a file that stopped matching."""
    matter, item = strict_pair(number=204)
    archive = archive_path([item])
    first = plan_for(archive)
    apply_plan(first, batch=open_batch(first))
    Submission.objects.filter(matter=matter).delete()
    OpinionMatchCandidate.objects.filter(matter=matter).update(
        state=OpinionCandidateState.REJECTED, decided_by=administrator, decided_at=timezone.now()
    )

    second = plan_for(archive)

    assert second.submissions == []
    assert any("ülevaataja" in warning.lower() for warning in second.warnings)


def test_a_decision_on_one_bundle_file_does_not_release_the_others(archive_path, administrator):
    """Withholding a letter-plus-annex bundle does not soften beside a decision.

    Tempting shortcut, and wrong: if the decided file simply dropped out of the
    automatic pass before the same-day rule ran, rejecting the annex would file
    the letter — turning "this one is the annex" into a canonical SENT record
    for a file nobody approved. Which of the two is the letter is still a
    judgement, and one rejection does not make it (brief 41, 70).
    """
    matter, letter = strict_pair(number=205)
    annex = syn.opinion(
        date="2024-04-10",
        recipient="Näidisministeerium",
        title="Arvamus näidisregistri seaduse muutmise kohta Lisa 1",
        marker="bundle-205b",
    )
    archive = archive_path([letter, annex])
    first = plan_for(archive)
    apply_plan(first, batch=open_batch(first))
    assert Submission.objects.filter(matter=matter).count() == 0

    OpinionMatchCandidate.objects.filter(item__sha256=annex.sha256).update(
        state=OpinionCandidateState.NOT_AN_OPINION,
        decided_by=administrator,
        decided_at=timezone.now(),
    )

    second = plan_for(archive)
    apply_plan(second, batch=open_batch(second))

    assert Submission.objects.filter(matter=matter).count() == 0
    assert OpinionSubmissionImport.objects.count() == 0
    assert OpinionCandidateState.APPLIED not in states()


def test_confirming_the_sending_is_what_resolves_a_bundle(archive_path, administrator):
    """The route that does file it, and the one file it files.

    The counterpart to the test above: the queue is not a dead end for a
    bundle. A reviewer naming the letter and approving its sending gets exactly
    one canonical record, and the annex stays evidence.
    """
    _matter, letter = strict_pair(number=208)
    annex = syn.opinion(
        date="2024-04-10",
        recipient="Näidisministeerium",
        title="Arvamus näidisregistri seaduse muutmise kohta Lisa 1",
        marker="bundle-208b",
    )
    archive = archive_path([letter, annex])
    first = plan_for(archive)
    apply_plan(first, batch=open_batch(first))

    OpinionMatchCandidate.objects.filter(item__sha256=letter.sha256).update(
        state=OpinionCandidateState.LINKED,
        review_approves_submission=True,
        reviewed_sent_date=datetime.date(2024, 4, 10),
        decided_by=administrator,
        decided_at=timezone.now(),
    )

    second = plan_for(archive)
    apply_plan(second, batch=open_batch(second))

    record = OpinionSubmissionImport.objects.get()
    assert record.item.sha256 == letter.sha256
    assert record.sent_date_basis == SentDateBasis.REVIEWED_DECISION
    assert (
        OpinionMatchCandidate.objects.get(pk=record.candidate_id).state
        == OpinionCandidateState.APPLIED
    )
    assert OpinionCandidateState.APPLIED not in list(
        OpinionMatchCandidate.objects.filter(item__sha256=annex.sha256).values_list(
            "state", flat=True
        )
    ), "the annex is evidence, not a second sent action"


def test_a_reviewers_date_wins_over_the_automatic_one_for_the_same_file(
    archive_path, administrator, monkeypatch
):
    """Both routes propose the same occurrence; the person's must be written.

    The register has a `VÄLJA` all along, so this file is automatic material —
    but the first import cannot store the evidence, so nothing is filed and the
    row lands in the queue. A reviewer confirms the sending with a date they can
    defend. The storage comes back, and now *both* routes propose the same file.
    Planning the automatic one first wrote the register's date over the
    reviewer's and left their row LINKED for ever.
    """
    from app.legacy_import import opinion_apply

    matter, item = strict_pair(number=206)
    archive = archive_path([item])

    monkeypatch.setattr(opinion_apply, "_final_version_for", lambda *a, **k: (None, False))
    first = plan_for(archive)
    apply_plan(first, batch=open_batch(first))
    assert OpinionSubmissionImport.objects.count() == 0
    monkeypatch.undo()

    OpinionMatchCandidate.objects.filter(matter=matter).update(
        state=OpinionCandidateState.LINKED,
        review_approves_submission=True,
        reviewed_sent_date=datetime.date(2024, 3, 1),
        decided_by=administrator,
        decided_at=timezone.now(),
    )

    second = plan_for(archive)
    apply_plan(second, batch=open_batch(second))

    submission = Submission.objects.get(matter=matter)
    # Read in local time, as the rest of the suite does: a date-only historical
    # value is anchored at *Tallinn* midnight, so the UTC date is the day before.
    assert timezone.localtime(submission.sent_at).date() == datetime.date(2024, 3, 1), (
        "the reviewer's date, not the register's"
    )
    record = OpinionSubmissionImport.objects.get()
    assert record.sent_date_basis == SentDateBasis.REVIEWED_DECISION
    candidate = OpinionMatchCandidate.objects.get(pk=record.candidate_id)
    assert candidate.state == OpinionCandidateState.APPLIED
    assert candidate.review_approves_submission is True


@pytest.mark.parametrize("decision", decided_states())
def test_the_applied_transition_itself_refuses_a_decided_row(archive_path, decision):
    """Belt and braces, and worth having separately.

    The planner now keeps a decided row from ever reaching the writer, so the
    end-to-end tests above would still pass if this guard were removed. It is
    the guard that has to hold when a future caller reaches
    `_mark_candidate_applied` by a route the planner does not police.
    """
    from app.legacy_import import opinion_apply

    matter, item = strict_pair(number=207)
    plan = plan_for(archive_path([item]))
    apply_plan(plan, batch=open_batch(plan))
    candidate = OpinionMatchCandidate.objects.get(matter=matter)
    OpinionMatchCandidate.objects.filter(pk=candidate.pk).update(state=decision)

    opinion_apply._mark_candidate_applied(candidate.pk)

    assert OpinionMatchCandidate.objects.get(pk=candidate.pk).state == decision


# =========================================================================
# What the operator surfaces say
# =========================================================================


def test_status_counts_pending_applied_and_decided_separately(archive_path, administrator):
    """`ülevaatust ootel` must mean work, not everything ever catalogued.

    Three files, three outcomes: one filed automatically, one still unmatched
    and genuinely waiting, one a person has answered. Before the split, all
    three sat in the same number and the operator could not tell a backlog from
    a finished import.
    """
    _matter, applied = strict_pair(number=211)
    waiting = syn.opinion(
        date="2024-07-02", recipient="Tundmatu asutus", title="Arvamus", marker="status-212"
    )
    answered = syn.opinion(
        date="2024-07-03", recipient="Teadmata asutus", title="Arvamus", marker="status-213"
    )
    plan = plan_for(archive_path([applied, waiting, answered]))
    apply_plan(plan, batch=open_batch(plan))

    OpinionMatchCandidate.objects.filter(item__sha256=answered.sha256).update(
        state=OpinionCandidateState.NOT_AN_OPINION,
        decided_by=administrator,
        decided_at=timezone.now(),
    )

    output = io.StringIO()
    call_command("opinion_archive", "status", stdout=output)
    tally = {
        label.strip(): int(value.replace(",", ""))
        for label, value in (
            line.rsplit(None, 1) for line in output.getvalue().strip().splitlines()
        )
    }

    assert tally["sidumiskandidaate"] == 3
    assert tally["ülevaatust ootel"] == 1, "only the unmatched file is still work"
    assert tally["rakendatud"] == 1
    assert tally["ülevaataja otsustatud"] == 1


def test_verify_accepts_a_consistent_provenance_chain(archive_path):
    _matter, item = strict_pair(number=221)
    plan = plan_for(archive_path([item]))
    apply_plan(plan, batch=open_batch(plan))

    output = io.StringIO()
    call_command("opinion_archive", "verify", stdout=output)

    assert "kõik kontrollid läbitud" in output.getvalue()


def test_verify_reports_an_import_naming_a_candidate_from_another_matter(archive_path):
    """One row must not say candidate A, Submission for Matter B."""
    _matter, item = strict_pair(number=222)
    other = register_matter(
        year=2024, number=223, title="Muu teema", sent="2024-09-09", counterparty="Muu asutus"
    )
    plan = plan_for(archive_path([item]))
    apply_plan(plan, batch=open_batch(plan))

    record = OpinionSubmissionImport.objects.get()
    OpinionMatchCandidate.objects.filter(pk=record.candidate_id).update(matter=other)

    with pytest.raises(CommandError):
        call_command("opinion_archive", "verify", stdout=io.StringIO())


def test_verify_reports_an_applied_candidate_that_produced_nothing(archive_path):
    """APPLIED is a claim about a Submission, so an unbacked one is a defect."""
    _matter, item = strict_pair(number=224)
    plan = plan_for(archive_path([item]))
    apply_plan(plan, batch=open_batch(plan))

    OpinionSubmissionImport.objects.update(candidate=None)

    with pytest.raises(CommandError):
        call_command("opinion_archive", "verify", stdout=io.StringIO())


# =========================================================================
# The archive projection an apply owns for what it wrote
# =========================================================================


def test_the_bulk_candidate_update_stays_visible_in_the_archive_projection(
    archive_path, evidence_root
):
    """`_mark_candidate_applied` is a `QuerySet.update()`, and no signal sees one.

    The sequence an operator actually performs: catalogue, materialise, index,
    then apply. `apply_plan` suspends the per-row handlers because it touches
    the same binary several times, and pays for exactly the binaries its batch
    catalogued — so the row has to converge without anybody rebuilding the
    corpus. Both halves are asserted: the projection agrees with canon, and the
    two letters it did not touch were not rewritten to make it so.
    """
    from app.legacy_import.opinion_apply import catalogue_plan
    from app.legacy_import.opinion_materialize import materialize
    from app.legacy_import.opinion_search import (
        archive_index_findings,
        rebuild_archive_index,
        refresh_archive_binaries,
    )
    from app.legacy_import.opinion_search_models import OpinionArchiveSearchDocument

    _matter, item = strict_pair(number=231)
    path = archive_path([item])
    plan = plan_for(path)
    catalogue_plan(plan, batch=open_batch(plan))
    materialize(archive_path=path, expected_archive_sha256=plan.archive_sha256)
    rebuild_archive_index()

    row = OpinionArchiveSearchDocument.objects.get()
    assert row.review_state == OpinionCandidateState.PENDING
    assert archive_index_findings() == []

    apply_plan(plan_for(path), batch=open_batch(plan))

    row.refresh_from_db()
    assert OpinionMatchCandidate.objects.get().state == OpinionCandidateState.APPLIED
    assert row.review_state == OpinionCandidateState.APPLIED
    assert archive_index_findings() == []
    # Already converged, so the bounded refresh finds nothing left to write.
    assert refresh_archive_binaries([row.binary_id]) == 0


def test_a_workbook_catalogued_after_materialisation_reaches_the_projection(
    archive_path, evidence_root, tmp_path
):
    """The metadata staling path, through the sequence that produces it.

    `catalogue_plan` deliberately does not suspend archive indexing, and does
    not need to: it writes one row at a time through the model, so the per-row
    handlers cover it. What it *can* do is write a reading against an occurrence
    that is already materialised and already projected — which is exactly what
    happens when the KodaDash workbook arrives after the archive did, one
    `catalogue` run later.

    Before the metadata handler, `external_id` never reached `identifiers` on
    that run, the letter stayed unfindable by its KodaDash handle, and
    `verify` reported a clean corpus throughout.
    """
    from app.legacy_import.opinion_apply import catalogue_plan
    from app.legacy_import.opinion_archive import OpinionArchiveMetadata
    from app.legacy_import.opinion_materialize import materialize
    from app.legacy_import.opinion_search import (
        archive_index_findings,
        rebuild_archive_index,
        refresh_archive_binaries,
    )
    from app.legacy_import.opinion_search_models import OpinionArchiveSearchDocument

    _matter, item = strict_pair(number=232)
    path = archive_path([item])
    plan = plan_for(path)
    catalogue_plan(plan, batch=open_batch(plan))
    materialize(archive_path=path, expected_archive_sha256=plan.archive_sha256)

    # Materialisation leaves the letter indexed, which is what makes the next
    # catalogue run a write against an *already projected* occurrence.
    row = OpinionArchiveSearchDocument.objects.get()
    assert OpinionArchiveMetadata.objects.count() == 0
    assert "KD-KATALOOG" not in row.identifiers
    assert archive_index_findings() == []

    book = syn.write_kodadash_workbook(
        tmp_path / "kd.xlsx",
        [
            {
                "content_id": "KD-KATALOOG",
                "file_sha256": item.sha256,
                "recipient_raw": "Näidisministeerium",
                "title": "KodaDashi lugem",
            }
        ],
    )
    later = build_plan(archive_path=path, kodadash_path=book)
    catalogue_plan(later, batch=open_batch(later))

    # No rebuild_archive_index() here, deliberately.
    assert OpinionArchiveMetadata.objects.count() == 1
    row.refresh_from_db()
    assert "KD-KATALOOG" in row.identifiers
    assert archive_index_findings() == []
    # It landed where a rebuild would have, and nothing was left owing.
    assert rebuild_archive_index().written == 0
    assert refresh_archive_binaries([row.binary_id]) == 0
    # And it created no Submission: this is still a catalogue.
    assert Submission.objects.count() == 0


def test_an_apply_converges_the_metadata_it_wrote_under_suspension(
    archive_path, evidence_root, tmp_path
):
    """The bulk half, for the relation this round added.

    `apply_plan` suspends the per-row handlers and pays one bounded refresh for
    the binaries its batch catalogued, which is what makes this worth asserting
    apart from the catalogue above: inside the apply the metadata handler pays
    nothing at all, and the register handle reaches `identifiers` only because
    the refresh recomputes the whole row. A refresh narrowed to the candidate
    columns would leave it out and nothing would report it.
    """
    from app.legacy_import.opinion_apply import catalogue_plan
    from app.legacy_import.opinion_archive import OpinionArchiveMetadata
    from app.legacy_import.opinion_materialize import materialize
    from app.legacy_import.opinion_search import (
        archive_index_findings,
        rebuild_archive_index,
        refresh_archive_binaries,
    )
    from app.legacy_import.opinion_search_models import OpinionArchiveSearchDocument

    _matter, item = strict_pair(number=233)
    path = archive_path([item])
    base = plan_for(path)
    catalogue_plan(base, batch=open_batch(base))
    materialize(archive_path=path, expected_archive_sha256=base.archive_sha256)
    row = OpinionArchiveSearchDocument.objects.get()
    assert "KD-APPLY" not in row.identifiers

    book = syn.write_kodadash_workbook(
        tmp_path / "kd.xlsx",
        [
            {
                "content_id": "KD-APPLY",
                "file_sha256": item.sha256,
                "recipient_raw": "Näidisministeerium",
                "title": "KodaDashi lugem",
            }
        ],
    )
    plan = build_plan(archive_path=path, kodadash_path=book)
    apply_plan(plan, batch=open_batch(plan))

    assert OpinionArchiveMetadata.objects.count() == 1
    row.refresh_from_db()
    assert "KD-APPLY" in row.identifiers
    assert row.has_submission is True
    assert row.review_state == OpinionCandidateState.APPLIED
    assert archive_index_findings() == []
    # Bounded, and already converged: no rebuild of the corpus is required.
    assert rebuild_archive_index().written == 0
    assert refresh_archive_binaries([row.binary_id]) == 0
