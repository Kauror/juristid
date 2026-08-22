"""Retiring proposals, linking several Matters, and reading the letters.

The three pieces of Stage 2H.2 that change what the reconciliation *believes*,
as opposed to what it holds. Each has one rule that carries most of the weight:

* a proposal is retired only from PENDING, so an importer rerun can never
  overwrite what a person answered or what an apply already did;
* a link is a weaker claim than a Submission, and the surface that records it
  must not be able to unmake one;
* the second pass reads the letters and files nothing, because its precision on
  the real corpus has never been measured.
"""

from __future__ import annotations

import datetime

import pytest
from django.urls import reverse
from django.utils import timezone

from app.core.errors import DomainError
from app.legacy_import.opinion_archive import (
    OpinionArchiveBatch,
    OpinionArchiveItem,
    OpinionMatchCandidate,
)
from app.legacy_import.opinion_binary import OpinionArchiveBinary, OpinionArchiveMatterLink
from app.legacy_import.opinion_content_match import MATCHER_VERSION
from app.legacy_import.opinion_enums import (
    AUTOMATIC_MATCH_CLASSES,
    ArchiveLinkBasis,
    OpinionCandidateState,
    OpinionMatchClass,
    OpinionSignal,
)
from app.legacy_import.opinion_links import (
    LinkRefused,
    derive_links,
    link_matter,
    links_for,
    unlink_matter,
)
from app.legacy_import.opinion_supersede import (
    SupersessionRefused,
    supersede_candidate,
    superseded_findings,
    sweep_superseded,
)
from tests import factories

pytestmark = pytest.mark.django_db


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def batch(db):
    return OpinionArchiveBatch.objects.create(
        archive_sha256="a" * 64,
        importer_version="test/0",
        started_at=datetime.datetime(2026, 1, 1, tzinfo=datetime.UTC),
    )


@pytest.fixture
def binary(db):
    return OpinionArchiveBinary.objects.create(
        sha256="b" * 64,
        size_bytes=512,
        mime_type="application/pdf",
        storage_key="opinion-archive/bb/bb/" + "b" * 64,
        source_archive_sha256="a" * 64,
        materialized_at=timezone.now(),
    )


@pytest.fixture
def item(batch, binary):
    return OpinionArchiveItem.objects.create(
        batch=batch,
        archive_sha256="a" * 64,
        archive_relative_path="Opinions/naidis.pdf",
        original_filename="naidis.pdf",
        sha256=binary.sha256,
        size_bytes=512,
        detected_type="application/pdf",
        filename_date=datetime.date(2024, 4, 10),
        filename_recipient="Naidisministeerium",
        filename_title="Naidisarvamus",
        binary=binary,
    )


def candidate(item, *, matter=None, klass=OpinionMatchClass.UNMATCHED, state=None):
    """A proposal, always naming the run that produced it.

    `batch` is not nullable, and that is the point: a queue row a reader cannot
    trace back to a reconciliation run is a row nobody can audit a year later.
    The item's own batch is the honest answer in a fixture.
    """
    return OpinionMatchCandidate.objects.create(
        item=item,
        matter=matter,
        batch=item.batch,
        match_class=klass,
        state=state or OpinionCandidateState.PENDING,
    )


# ---------------------------------------------------------------------------
# Supersession
# ---------------------------------------------------------------------------


def test_a_reclassified_proposal_is_retired_and_says_by_what(item, normal_matter):
    old = candidate(item, klass=OpinionMatchClass.UNMATCHED)
    new = candidate(item, matter=normal_matter, klass=OpinionMatchClass.REVIEW_REQUIRED)

    report = sweep_superseded()
    old.refresh_from_db()
    assert report.superseded == 1
    assert old.state == OpinionCandidateState.SUPERSEDED
    assert old.superseded_by_id == new.pk
    assert old.supersession_reason


def test_the_evidence_on_a_retired_proposal_is_kept(item, normal_matter):
    old = candidate(item, klass=OpinionMatchClass.UNMATCHED)
    old.signals = [OpinionSignal.EXACT_SENT_DATE]
    old.explanation = "Nii arvas esimene läbimine."
    old.save()
    candidate(item, matter=normal_matter, klass=OpinionMatchClass.REVIEW_REQUIRED)

    sweep_superseded()
    old.refresh_from_db()
    assert old.signals == [OpinionSignal.EXACT_SENT_DATE]
    assert old.explanation == "Nii arvas esimene läbimine."


def test_an_applied_proposal_is_never_retired(item, normal_matter):
    applied = candidate(
        item,
        matter=normal_matter,
        klass=OpinionMatchClass.EXACT_BINARY_MATTER,
        state=OpinionCandidateState.APPLIED,
    )
    replacement = candidate(item, matter=normal_matter, klass=OpinionMatchClass.REVIEW_REQUIRED)

    with pytest.raises(SupersessionRefused):
        supersede_candidate(superseded=applied, replacement=replacement, reason="uuem tõend")
    applied.refresh_from_db()
    assert applied.state == OpinionCandidateState.APPLIED


@pytest.mark.parametrize(
    "state",
    [
        OpinionCandidateState.LINKED,
        OpinionCandidateState.REJECTED,
        OpinionCandidateState.DUPLICATE,
        OpinionCandidateState.NOT_AN_OPINION,
        OpinionCandidateState.DEFERRED,
    ],
)
def test_a_human_decision_is_never_overwritten_by_a_rerun(item, normal_matter, state):
    decided = candidate(item, matter=normal_matter, state=state)
    candidate(item, matter=normal_matter, klass=OpinionMatchClass.REVIEW_REQUIRED)

    report = sweep_superseded()
    decided.refresh_from_db()
    assert report.superseded == 0
    assert decided.state == state


def test_a_proposal_never_supersedes_itself(item):
    row = candidate(item)
    with pytest.raises(SupersessionRefused):
        supersede_candidate(superseded=row, replacement=row, reason="iseennast")


def test_a_cycle_is_refused(item, normal_matter):
    first = candidate(item, klass=OpinionMatchClass.UNMATCHED)
    second = candidate(item, matter=normal_matter, klass=OpinionMatchClass.REVIEW_REQUIRED)
    supersede_candidate(superseded=first, replacement=second, reason="uuem tõend")

    second.refresh_from_db()
    with pytest.raises(SupersessionRefused):
        supersede_candidate(superseded=second, replacement=first, reason="tagasi")


def test_retiring_the_same_pair_twice_is_the_same_answer(item, normal_matter):
    old = candidate(item, klass=OpinionMatchClass.UNMATCHED)
    new = candidate(item, matter=normal_matter, klass=OpinionMatchClass.REVIEW_REQUIRED)
    supersede_candidate(superseded=old, replacement=new, reason="uuem tõend")
    again = supersede_candidate(superseded=old, replacement=new, reason="uuem tõend")
    assert again.pk == old.pk


def test_a_retirement_without_a_reason_is_refused(item, normal_matter):
    old = candidate(item, klass=OpinionMatchClass.UNMATCHED)
    new = candidate(item, matter=normal_matter, klass=OpinionMatchClass.REVIEW_REQUIRED)
    with pytest.raises(SupersessionRefused):
        supersede_candidate(superseded=old, replacement=new, reason="   ")


def test_a_second_matter_is_not_swept_away_by_the_newer_one(item, normal_matter):
    """Two live proposals on one letter is the multi-Matter case.

    It belongs in front of a person, not resolved by whichever row is newer.
    """
    other = factories.ArchiveMatterFactory(title="Teine teema")
    first = candidate(item, matter=normal_matter, klass=OpinionMatchClass.REVIEW_REQUIRED)
    second = candidate(item, matter=other, klass=OpinionMatchClass.REVIEW_REQUIRED)

    report = sweep_superseded()
    first.refresh_from_db()
    second.refresh_from_db()
    assert report.superseded == 0
    assert first.state == OpinionCandidateState.PENDING
    assert second.state == OpinionCandidateState.PENDING


def test_a_plan_writes_nothing(item, normal_matter):
    old = candidate(item, klass=OpinionMatchClass.UNMATCHED)
    candidate(item, matter=normal_matter, klass=OpinionMatchClass.REVIEW_REQUIRED)

    report = sweep_superseded(dry_run=True)
    old.refresh_from_db()
    assert report.superseded == 1
    assert old.state == OpinionCandidateState.PENDING


def test_a_retired_proposal_is_out_of_the_default_queue(client, administrator, item, normal_matter):
    old = candidate(item, klass=OpinionMatchClass.UNMATCHED)
    candidate(item, matter=normal_matter, klass=OpinionMatchClass.REVIEW_REQUIRED)
    sweep_superseded()

    client.force_login(administrator)
    response = client.get(reverse("legacy_import:opinion_queue"))
    body = response.content.decode()
    assert str(old.pk) not in body


def test_an_inconsistent_chain_is_a_finding(item, normal_matter):
    old = candidate(item, klass=OpinionMatchClass.UNMATCHED)
    new = candidate(item, matter=normal_matter, klass=OpinionMatchClass.REVIEW_REQUIRED)
    supersede_candidate(superseded=old, replacement=new, reason="uuem tõend")

    OpinionMatchCandidate.objects.filter(pk=old.pk).update(state=OpinionCandidateState.PENDING)
    assert any("ASENDATUD" in finding for finding in superseded_findings())


# ---------------------------------------------------------------------------
# Multi-Matter links
# ---------------------------------------------------------------------------


def test_one_letter_may_concern_two_matters(binary, normal_matter, administrator):
    other = factories.ArchiveMatterFactory(title="Teine teema")
    link_matter(
        binary=binary, matter=normal_matter, basis=ArchiveLinkBasis.REVIEWED, actor=administrator
    )
    link_matter(binary=binary, matter=other, basis=ArchiveLinkBasis.REVIEWED, actor=administrator)
    assert len(links_for(binary)) == 2


def test_linking_twice_records_one_relationship(binary, normal_matter, administrator):
    _, created_first = link_matter(
        binary=binary, matter=normal_matter, basis=ArchiveLinkBasis.REVIEWED, actor=administrator
    )
    _, created_again = link_matter(
        binary=binary, matter=normal_matter, basis=ArchiveLinkBasis.REVIEWED, actor=administrator
    )
    assert created_first is True
    assert created_again is False
    assert OpinionArchiveMatterLink.objects.count() == 1


def test_a_person_confirming_a_derived_link_upgrades_it(binary, normal_matter, administrator):
    link_matter(binary=binary, matter=normal_matter, basis=ArchiveLinkBasis.EXACT_BINARY)
    link, _ = link_matter(
        binary=binary, matter=normal_matter, basis=ArchiveLinkBasis.REVIEWED, actor=administrator
    )
    assert link.basis == ArchiveLinkBasis.REVIEWED
    assert link.linked_by_id == administrator.pk


def test_a_later_derivation_never_un_reviews_a_decision(binary, normal_matter, administrator):
    link_matter(
        binary=binary, matter=normal_matter, basis=ArchiveLinkBasis.REVIEWED, actor=administrator
    )
    link, _ = link_matter(binary=binary, matter=normal_matter, basis=ArchiveLinkBasis.EXACT_BINARY)
    assert link.basis == ArchiveLinkBasis.REVIEWED


def test_a_reviewed_link_needs_a_person_behind_it(binary, normal_matter):
    with pytest.raises(LinkRefused):
        link_matter(binary=binary, matter=normal_matter, basis=ArchiveLinkBasis.REVIEWED)


def test_a_link_that_a_submission_stands_on_is_not_withdrawn_here(
    binary, normal_matter, administrator
):
    link_matter(binary=binary, matter=normal_matter, basis=ArchiveLinkBasis.APPLIED_SUBMISSION)
    with pytest.raises(LinkRefused):
        unlink_matter(binary=binary, matter=normal_matter, actor=administrator)
    assert OpinionArchiveMatterLink.objects.count() == 1


def test_a_reviewer_may_withdraw_their_own_link(binary, normal_matter, administrator):
    link_matter(
        binary=binary, matter=normal_matter, basis=ArchiveLinkBasis.REVIEWED, actor=administrator
    )
    unlink_matter(binary=binary, matter=normal_matter, actor=administrator)
    assert OpinionArchiveMatterLink.objects.count() == 0


def test_derivation_only_follows_exact_evidence(item, binary, normal_matter):
    """A REVIEW_REQUIRED candidate is not exact and must not become a link."""
    candidate(item, matter=normal_matter, klass=OpinionMatchClass.REVIEW_REQUIRED)
    derive_links()
    assert OpinionArchiveMatterLink.objects.count() == 0

    candidate(item, matter=normal_matter, klass=OpinionMatchClass.EXACT_BINARY_MATTER)
    derive_links()
    link = OpinionArchiveMatterLink.objects.get()
    assert link.basis == ArchiveLinkBasis.EXACT_BINARY
    assert link.matter_id == normal_matter.pk


def test_every_derived_class_is_one_the_apply_may_act_on(item, normal_matter):
    """The derivation reuses the automatic classes rather than a second list."""
    assert OpinionMatchClass.REVIEW_REQUIRED not in AUTOMATIC_MATCH_CLASSES
    assert OpinionMatchClass.CONFLICT not in AUTOMATIC_MATCH_CLASSES
    assert OpinionMatchClass.CONTENT_MULTI_SIGNAL not in AUTOMATIC_MATCH_CLASSES


def test_derivation_is_idempotent(item, normal_matter):
    candidate(item, matter=normal_matter, klass=OpinionMatchClass.EXACT_BINARY_MATTER)
    first = derive_links()
    second = derive_links()
    assert first.created == 1
    assert second.created == 0
    assert second.unchanged == 1


def test_the_link_screen_never_creates_a_matter(client, administrator, binary):
    from app.matters.models import Matter

    before = Matter.objects.count()
    client.force_login(administrator)
    response = client.post(
        reverse("legacy_import:opinion_archive_link", kwargs={"pk": binary.pk}),
        {"action": "link", "viide": "1999_999"},
        follow=True,
    )
    assert Matter.objects.count() == before
    assert OpinionArchiveMatterLink.objects.count() == 0
    assert "ei leitud" in response.content.decode()


def test_a_specialist_may_not_link_anything(client, specialist, binary, normal_matter):
    client.force_login(specialist)
    response = client.post(
        reverse("legacy_import:opinion_archive_link", kwargs={"pk": binary.pk}),
        {"action": "link", "viide": normal_matter.display_reference},
    )
    assert response.status_code == 403
    assert OpinionArchiveMatterLink.objects.count() == 0


def test_linking_from_the_screen_records_a_reviewed_link(
    client, administrator, binary, normal_matter
):
    client.force_login(administrator)
    client.post(
        reverse("legacy_import:opinion_archive_link", kwargs={"pk": binary.pk}),
        {"action": "link", "viide": normal_matter.display_reference, "markus": "Kuulub siia."},
        follow=True,
    )
    link = OpinionArchiveMatterLink.objects.get()
    assert link.basis == ArchiveLinkBasis.REVIEWED
    assert link.linked_by_id == administrator.pk
    assert link.note == "Kuulub siia."


def test_linking_creates_no_submission(client, administrator, binary, normal_matter):
    from app.submissions.models import Submission

    client.force_login(administrator)
    client.post(
        reverse("legacy_import:opinion_archive_link", kwargs={"pk": binary.pk}),
        {"action": "link", "viide": normal_matter.display_reference},
        follow=True,
    )
    assert Submission.objects.count() == 0


# ---------------------------------------------------------------------------
# The second pass
# ---------------------------------------------------------------------------


def register_matter(*, year, number, title, sent, counterparty):
    """A Matter as the Excel importer would have left it."""
    from app.legacy_import.parser import SOURCE_SYSTEM

    matter = factories.ArchiveMatterFactory(
        reference_year=year, reference_number=number, title=title
    )
    factories.MatterSourceReferenceFactory(
        matter=matter,
        source_system=SOURCE_SYSTEM,
        source_sheet=str(year),
        source_row_number=number,
        source_row_raw={"A": f"{year}_{number}", "B": title, "F": sent, "G": counterparty},
    )
    return matter


def give_text(binary, body):
    from app.legacy_import.opinion_binary import OpinionArchiveText
    from app.legacy_import.opinion_enums import ArchiveTextState

    OpinionArchiveText.objects.update_or_create(
        binary=binary,
        defaults={
            "state": ArchiveTextState.DONE,
            "body": body,
            "characters": len(body),
            "parser": "test",
            "parser_version": "1",
        },
    )


LETTER = (
    "Rahandusministeerium\n\n"
    "Tallinn, 10.04.2024\n\n"
    "Arvamus maksukorralduse seaduse muutmise eelnõu 512 SE kohta.\n"
)


def test_two_content_signals_propose_a_matter_for_review(item, binary):
    from app.legacy_import.opinion_content_match import apply_content_matches

    row = register_matter(
        year=2024,
        number=7,
        title="Maksukorralduse seaduse muutmine 512 SE",
        sent="2024-04-10",
        counterparty="Rahandusministeerium",
    )
    give_text(binary, LETTER)

    report = apply_content_matches()
    assert report.proposed == 1

    proposal = OpinionMatchCandidate.objects.get(match_class=OpinionMatchClass.CONTENT_MULTI_SIGNAL)
    assert proposal.matter_id == row.pk
    assert proposal.state == OpinionCandidateState.PENDING
    # Traceable to the run that produced it, like every other queue row.
    assert proposal.batch.importer_version == MATCHER_VERSION
    assert set(proposal.signals) >= {
        OpinionSignal.CONTENT_EXACT_DATE,
        OpinionSignal.CONTENT_EXACT_LAW_REFERENCE,
    }


def test_the_second_pass_never_files_anything(item, binary):
    from app.legacy_import.opinion_content_match import apply_content_matches
    from app.submissions.models import Submission

    register_matter(
        year=2024,
        number=7,
        title="Maksukorralduse seaduse muutmine 512 SE",
        sent="2024-04-10",
        counterparty="Rahandusministeerium",
    )
    give_text(binary, LETTER)
    apply_content_matches()

    assert Submission.objects.count() == 0
    assert OpinionArchiveMatterLink.objects.count() == 0


def test_one_signal_alone_proposes_nothing(item, binary):
    from app.legacy_import.opinion_content_match import apply_content_matches

    register_matter(
        year=2024,
        number=7,
        title="Hoopis teine teema",
        sent="2024-04-10",
        counterparty="Kliimaministeerium",
    )
    give_text(binary, LETTER)

    report = apply_content_matches()
    assert report.proposed == 0
    assert OpinionMatchCandidate.objects.count() == 0


def test_two_corroborated_rows_are_a_conflict_not_a_winner(item, binary):
    """No comparison between rows anywhere: two answers is the answer."""
    from app.legacy_import.opinion_content_match import apply_content_matches

    register_matter(
        year=2024,
        number=7,
        title="Maksukorralduse seaduse muutmine 512 SE",
        sent="2024-04-10",
        counterparty="Rahandusministeerium",
    )
    register_matter(
        year=2024,
        number=8,
        title="Teine kiri samast eelnõust 512 SE",
        sent="2024-04-10",
        counterparty="Rahandusministeerium",
    )
    give_text(binary, LETTER)

    report = apply_content_matches()
    assert report.proposed == 0
    assert report.conflicted == 1
    proposal = OpinionMatchCandidate.objects.get()
    assert proposal.match_class == OpinionMatchClass.CONFLICT
    assert proposal.matter_id is None


def test_a_letter_the_first_pass_already_matched_is_left_alone(item, binary, normal_matter):
    from app.legacy_import.opinion_content_match import apply_content_matches

    register_matter(
        year=2024,
        number=7,
        title="Maksukorralduse seaduse muutmine 512 SE",
        sent="2024-04-10",
        counterparty="Rahandusministeerium",
    )
    give_text(binary, LETTER)
    candidate(item, matter=normal_matter, klass=OpinionMatchClass.EXACT_BINARY_MATTER)

    report = apply_content_matches()
    assert report.already_matched == 1
    assert report.proposed == 0


def test_a_letter_with_no_extracted_text_is_skipped_rather_than_guessed_at(item, binary):
    from app.legacy_import.opinion_content_match import apply_content_matches

    register_matter(
        year=2024,
        number=7,
        title="Maksukorralduse seaduse muutmine 512 SE",
        sent="2024-04-10",
        counterparty="Rahandusministeerium",
    )
    report = apply_content_matches()
    assert report.no_text == 1
    assert report.proposed == 0


def test_planning_the_second_pass_writes_nothing(item, binary):
    from app.legacy_import.opinion_content_match import plan_content_matches

    register_matter(
        year=2024,
        number=7,
        title="Maksukorralduse seaduse muutmine 512 SE",
        sent="2024-04-10",
        counterparty="Rahandusministeerium",
    )
    give_text(binary, LETTER)

    report = plan_content_matches()
    assert report.proposed == 1
    assert OpinionMatchCandidate.objects.count() == 0


def test_a_named_month_in_the_dateline_is_read_as_a_date(item, binary):
    from app.legacy_import.opinion_content_match import read_evidence

    evidence = read_evidence("Tallinn, 10. aprill 2024\n\nArvamus 512 SE kohta.")
    assert datetime.date(2024, 4, 10) in evidence.dates


def test_an_impossible_date_is_not_a_date(item, binary):
    from app.legacy_import.opinion_content_match import read_evidence

    assert read_evidence("31.02.2024").dates == frozenset()


def test_the_reports_never_name_a_letter(item, binary):
    """Aggregates only: these are printed into CI logs and pasted into tickets."""
    from app.legacy_import.opinion_content_match import apply_content_matches

    register_matter(
        year=2024,
        number=7,
        title="Maksukorralduse seaduse muutmine 512 SE",
        sent="2024-04-10",
        counterparty="Rahandusministeerium",
    )
    give_text(binary, LETTER)
    text = apply_content_matches().as_text()

    assert "Maksukorralduse" not in text
    assert "naidis.pdf" not in text
    assert binary.sha256 not in text


def test_a_domain_refusal_is_a_domain_error(item, normal_matter, binary):
    """Both services raise from one hierarchy, so a view can catch one thing."""
    assert issubclass(SupersessionRefused, DomainError)
    assert issubclass(LinkRefused, DomainError)
