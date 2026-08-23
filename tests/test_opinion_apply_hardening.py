"""The gates a canonical historical apply must pass, and the ones it grew.

Four defects are fixed here and each was invisible from the outside, because
each produced a record that looked correct.

**The plan was pinned to its sources and not to itself.** Identical archive and
snapshot digests do not make an identical plan: the plan is also built from the
review queue and from what is already filed, both of which move. "Rebuild the
current plan and apply whatever it now says" was indistinguishable from applying
the reviewed one.

**The reviewed route said REVIEWED_DECISION either way.** A reviewer who
approved a letter without stating a date got the register's VÄLJA column
recorded under their authority. Its own docstring said this could not happen.

**An unresolved recipient left nothing structural behind.** The one fact the
archive is certain about — who Koda wrote to, verbatim — survived only as prose
in a notes field, and the next run stopped before it ever looked again.

**The reviewed route skipped the automatic route's conflict checks.** A person
asserting whose letter this is is not asserting that nobody else has filed it.

All data is synthetic.
"""

from __future__ import annotations

import datetime
from io import StringIO

import pytest
from django.core.management import call_command
from django.core.management.base import CommandError

from app.core.enums import Visibility
from app.legacy_import.opinion_apply import apply_plan, open_batch
from app.legacy_import.opinion_apply_gate import (
    ApplyConflict,
    PlanChanged,
    plan_digest,
    require_no_conflicts,
    require_reviewed_plan,
    scan_conflicts,
)
from app.legacy_import.opinion_archive import OpinionMatchCandidate, OpinionSubmissionImport
from app.legacy_import.opinion_enums import (
    OpinionCandidateState,
    RecipientBasis,
    SentDateBasis,
)
from app.legacy_import.opinion_plan import build_plan
from app.legacy_import.parser import SOURCE_SYSTEM
from app.matters.models import Matter
from app.submissions.models import Submission, SubmissionRecipient
from tests import factories
from tests import synthetic_opinions as syn

pytestmark = pytest.mark.django_db


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


def register_matter(*, year: int, number: int, title: str, sent: str | None, counterparty: str):
    matter = factories.ArchiveMatterFactory(
        reference_year=year, reference_number=number, title=title, visibility=Visibility.NORMAL
    )
    factories.MatterSourceReferenceFactory(
        matter=matter,
        source_system=SOURCE_SYSTEM,
        source_sheet=str(year),
        source_row_number=number,
        source_row_raw={"A": f"{year}_{number}", "B": title, "F": sent or "", "G": counterparty},
    )
    return matter


def strict_pair(number: int = 21, *, sent: str | None = "2024-04-10"):
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
        marker=f"harden-{number}",
    )
    return matter, item


@pytest.fixture
def archive_path(tmp_path):
    def build(opinions):
        return syn.write_archive(tmp_path / "Opinions.zip", opinions)

    return build


def plan_for(archive):
    return build_plan(archive_path=archive, kodadash_path=None)


# ---------------------------------------------------------------------------
# 3.1 — the plan digest
# ---------------------------------------------------------------------------


def test_the_digest_is_stable_across_two_builds_of_one_unchanged_plan(archive_path):
    _matter, item = strict_pair()
    archive = archive_path([item])

    assert plan_digest(plan_for(archive)) == plan_digest(plan_for(archive))


def test_the_digest_ignores_plan_ordering(archive_path):
    """Sorted by the Submission's own identity, not by the order it was planned.

    A planner that emits the reviewed route before the automatic one, or stops
    doing so, must not change the digest of an unchanged decision.
    """
    _a, first = strict_pair(21)
    _b, second = strict_pair(22)
    forward = plan_for(archive_path([first, second]))
    backward = plan_for(archive_path([second, first]))

    assert plan_digest(forward) == plan_digest(backward)


def test_the_digest_moves_when_a_submission_would_land_on_a_different_matter(archive_path):
    _matter, item = strict_pair()
    plan = plan_for(archive_path([item]))
    before = plan_digest(plan)

    other = factories.ArchiveMatterFactory()
    plan.submissions[0].matter_id = other.pk

    assert plan_digest(plan) != before


def test_the_digest_moves_when_the_date_basis_changes(archive_path):
    """The basis is part of what would be written, so it is part of the digest."""
    _matter, item = strict_pair()
    plan = plan_for(archive_path([item]))
    before = plan_digest(plan)

    plan.submissions[0].sent_date_basis = SentDateBasis.REVIEWED_DECISION

    assert plan_digest(plan) != before


def test_apply_without_a_reviewed_digest_is_refused(archive_path):
    plan = plan_for(archive_path([strict_pair()[1]]))

    with pytest.raises(PlanChanged, match="expect-plan-sha256"):
        require_reviewed_plan(plan, "")


def test_a_stale_digest_is_refused_and_names_both(archive_path):
    plan = plan_for(archive_path([strict_pair()[1]]))

    with pytest.raises(PlanChanged, match="muutunud"):
        require_reviewed_plan(plan, "0" * 64)


def test_the_matching_digest_is_accepted_and_returned(archive_path):
    plan = plan_for(archive_path([strict_pair()[1]]))
    digest = plan_digest(plan)

    assert require_reviewed_plan(plan, digest.upper()) == digest


def test_the_apply_command_refuses_a_stale_digest(archive_path, settings, administrator):
    """The gate has to be on the route an operator actually runs."""
    settings.REAL_DATA_ALLOWED = True
    archive = archive_path([strict_pair()[1]])

    with pytest.raises(CommandError, match=r"muutunud|expect-plan-sha256"):
        call_command(
            "opinion_archive",
            "apply",
            "--opinions",
            str(archive),
            "--expect-plan-sha256",
            "0" * 64,
            stdout=StringIO(),
        )
    assert Submission.objects.count() == 0


# ---------------------------------------------------------------------------
# 3.2 — conflicts, on the reviewed route as well as the automatic one
# ---------------------------------------------------------------------------


def test_a_clean_plan_has_no_conflicts(archive_path):
    plan = plan_for(archive_path([strict_pair()[1]]))

    assert scan_conflicts(plan) == []
    require_no_conflicts(plan)


def test_the_same_letter_already_filed_on_another_matter_is_a_conflict(archive_path):
    """Not a duplicate row a constraint would catch — two true-looking claims."""
    _matter, item = strict_pair()
    archive = archive_path([item])
    batch = open_batch(plan_for(archive))
    apply_plan(plan_for(archive), batch=batch)
    assert Submission.objects.count() == 1

    # The planner correctly drops a letter that is already filed, so the shape
    # the conflict scan exists for is built explicitly: a second plan that would
    # put the same bytes on a different Matter.
    plan = plan_for(archive)
    imported = OpinionSubmissionImport.objects.get()
    other = factories.ArchiveMatterFactory()
    from app.legacy_import.opinion_plan import SubmissionPlan

    plan.submissions = [
        SubmissionPlan(
            sha256=imported.item.sha256,
            relative_path=imported.item.archive_relative_path,
            matter_id=other.pk,
            kind=imported.submission.kind,
            title=imported.submission.title,
            sent_date=datetime.date(2024, 4, 10),
            sent_date_basis=SentDateBasis.EXCEL_OUT_DATE,
            recipient_raw="Näidisministeerium",
            recipient_basis=RecipientBasis.EXCEL_ADDRESSEE,
            match_class=imported.match_class,
        )
    ]

    reasons = {conflict.reason for conflict in scan_conflicts(plan)}
    assert "ALREADY_IMPORTED_ELSEWHERE" in reasons
    with pytest.raises(ApplyConflict):
        require_no_conflicts(plan)


def test_a_rejected_candidate_blocks_its_own_plan(archive_path):
    _matter, item = strict_pair()
    archive = archive_path([item])
    batch = open_batch(plan_for(archive))
    from app.legacy_import.opinion_apply import catalogue_plan

    catalogue_plan(plan_for(archive), batch=batch)
    candidate = OpinionMatchCandidate.objects.first()
    candidate.state = OpinionCandidateState.REJECTED
    candidate.save(update_fields=["state", "updated_at"])

    plan = plan_for(archive)
    for entry in plan.submissions:
        entry.candidate_id = candidate.pk

    reasons = {conflict.reason for conflict in scan_conflicts(plan)}
    assert "CANDIDATE_REJECTED" in reasons


def test_a_candidate_claiming_applied_with_no_provenance_is_a_conflict(archive_path):
    """A broken pair is not a green light. It is the state 3.7 forbids."""
    _matter, item = strict_pair()
    archive = archive_path([item])
    from app.legacy_import.opinion_apply import catalogue_plan

    catalogue_plan(plan_for(archive), batch=open_batch(plan_for(archive)))
    candidate = OpinionMatchCandidate.objects.first()
    candidate.state = OpinionCandidateState.APPLIED
    candidate.save(update_fields=["state", "updated_at"])

    plan = plan_for(archive)
    for entry in plan.submissions:
        entry.candidate_id = candidate.pk

    reasons = {conflict.reason for conflict in scan_conflicts(plan)}
    assert "CANDIDATE_APPLIED_WITHOUT_PROVENANCE" in reasons


# ---------------------------------------------------------------------------
# 3.3 — the sent date says where it came from
# ---------------------------------------------------------------------------


def test_a_reviewer_who_stated_no_date_does_not_get_reviewed_provenance(archive_path):
    """The defect: a spreadsheet cell recorded under a person's authority."""
    matter, item = strict_pair()
    archive = archive_path([item])
    from app.legacy_import.opinion_apply import catalogue_plan

    catalogue_plan(plan_for(archive), batch=open_batch(plan_for(archive)))
    candidate = OpinionMatchCandidate.objects.first()
    candidate.matter = matter
    candidate.state = OpinionCandidateState.LINKED
    candidate.review_approves_submission = True
    candidate.reviewed_sent_date = None
    candidate.excel_sent_date = datetime.date(2024, 4, 10)
    candidate.save()

    plan = plan_for(archive)
    reviewed = [entry for entry in plan.submissions if entry.matter_id == matter.pk]

    assert reviewed, "the reviewed route should still plan this letter"
    assert reviewed[0].sent_date == datetime.date(2024, 4, 10)
    assert reviewed[0].sent_date_basis == SentDateBasis.EXCEL_OUT_DATE


def test_a_reviewer_who_stated_a_date_gets_reviewed_provenance(archive_path):
    matter, item = strict_pair(sent=None)
    archive = archive_path([item])
    from app.legacy_import.opinion_apply import catalogue_plan

    catalogue_plan(plan_for(archive), batch=open_batch(plan_for(archive)))
    candidate = OpinionMatchCandidate.objects.first()
    candidate.matter = matter
    candidate.state = OpinionCandidateState.LINKED
    candidate.review_approves_submission = True
    candidate.reviewed_sent_date = datetime.date(2024, 5, 2)
    candidate.save()

    plan = plan_for(archive)
    reviewed = [entry for entry in plan.submissions if entry.matter_id == matter.pk]

    assert reviewed[0].sent_date == datetime.date(2024, 5, 2)
    assert reviewed[0].sent_date_basis == SentDateBasis.REVIEWED_DECISION


def test_an_approved_letter_with_no_date_anywhere_is_withheld(archive_path):
    """A SENT submission whose date nobody can name is not written."""
    matter, item = strict_pair(sent=None)
    archive = archive_path([item])
    from app.legacy_import.opinion_apply import catalogue_plan

    catalogue_plan(plan_for(archive), batch=open_batch(plan_for(archive)))
    candidate = OpinionMatchCandidate.objects.first()
    candidate.matter = matter
    candidate.state = OpinionCandidateState.LINKED
    candidate.review_approves_submission = True
    candidate.reviewed_sent_date = None
    candidate.excel_sent_date = None
    candidate.save()

    plan = plan_for(archive)

    assert all(entry.sent_date is not None for entry in plan.submissions)


# ---------------------------------------------------------------------------
# 3.4 / 3.5 — the recipient survives, and resolution is retryable
# ---------------------------------------------------------------------------


def test_the_raw_recipient_is_recorded_even_when_it_resolves_to_nothing(archive_path):
    """The one fact the archive is certain about used to survive only as prose."""
    _matter, item = strict_pair()
    archive = archive_path([item])
    apply_plan(plan_for(archive), batch=open_batch(plan_for(archive)))

    imported = OpinionSubmissionImport.objects.get()

    assert imported.recipient_raw == "Näidisministeerium"
    assert SubmissionRecipient.objects.count() == 0


def test_resolution_is_retryable_once_the_reference_data_improves(archive_path):
    """The defect this closes: the second run never reached the recipient again.

    An import row existed for the occurrence, so the apply returned early — and
    adding the Organisation afterwards changed nothing, permanently.
    """
    from app.legacy_import.opinion_recipients import resolve_recipients

    _matter, item = strict_pair()
    archive = archive_path([item])
    apply_plan(plan_for(archive), batch=open_batch(plan_for(archive)))

    dry = resolve_recipients()
    assert dry.examined == 1
    assert dry.resolved == 0
    assert dry.unresolved_values == {"Näidisministeerium": 1}
    assert SubmissionRecipient.objects.count() == 0

    factories.OrganisationFactory(name="Näidisministeerium")

    report = resolve_recipients(apply=True)
    assert report.resolved == 1
    assert SubmissionRecipient.objects.count() == 1

    imported = OpinionSubmissionImport.objects.get()
    # The basis moves; the evidence does not.
    assert imported.recipient_raw == "Näidisministeerium"
    assert imported.recipient_basis == RecipientBasis.REVIEWED_MAPPING


def test_a_second_resolution_pass_is_a_no_op(archive_path):
    from app.legacy_import.opinion_recipients import resolve_recipients

    _matter, item = strict_pair()
    archive = archive_path([item])
    apply_plan(plan_for(archive), batch=open_batch(plan_for(archive)))
    factories.OrganisationFactory(name="Näidisministeerium")
    resolve_recipients(apply=True)

    again = resolve_recipients(apply=True)

    assert again.resolved == 0
    assert again.already_attached == 1
    assert SubmissionRecipient.objects.count() == 1


def test_resolution_never_creates_an_organisation(archive_path):
    from app.legacy_import.opinion_recipients import resolve_recipients
    from app.organisations.models import Organisation

    _matter, item = strict_pair()
    archive = archive_path([item])
    apply_plan(plan_for(archive), batch=open_batch(plan_for(archive)))

    before = Organisation.objects.count()
    resolve_recipients(apply=True)

    assert Organisation.objects.count() == before
    assert SubmissionRecipient.objects.count() == 0


def test_resolution_never_creates_a_submission(archive_path):
    from app.legacy_import.opinion_recipients import resolve_recipients

    _matter, item = strict_pair()
    archive = archive_path([item])
    apply_plan(plan_for(archive), batch=open_batch(plan_for(archive)))
    factories.OrganisationFactory(name="Näidisministeerium")

    before = Submission.objects.count()
    resolve_recipients(apply=True)

    assert Submission.objects.count() == before


def test_the_backfill_command_writes_nothing_without_apply(archive_path):
    _matter, item = strict_pair()
    archive = archive_path([item])
    apply_plan(plan_for(archive), batch=open_batch(plan_for(archive)))
    factories.OrganisationFactory(name="Näidisministeerium")

    out = StringIO()
    call_command("resolve_archive_recipients", stdout=out)

    assert SubmissionRecipient.objects.count() == 0
    assert "andmebaasi ei kirjutatud" in out.getvalue()


def test_the_backfill_command_lists_the_blocking_values(archive_path):
    """The report is the operator's work list, not a count of failures."""
    _matter, item = strict_pair()
    archive = archive_path([item])
    apply_plan(plan_for(archive), batch=open_batch(plan_for(archive)))

    out = StringIO()
    call_command("resolve_archive_recipients", stdout=out)

    assert "Näidisministeerium" in out.getvalue()


# ---------------------------------------------------------------------------
# 3.7 / 3.8 — idempotence and the APPLIED invariant
# ---------------------------------------------------------------------------


def test_a_second_apply_creates_no_second_submission(archive_path):
    _matter, item = strict_pair()
    archive = archive_path([item])
    apply_plan(plan_for(archive), batch=open_batch(plan_for(archive)))
    apply_plan(plan_for(archive), batch=open_batch(plan_for(archive)))

    assert Submission.objects.count() == 1
    assert OpinionSubmissionImport.objects.count() == 1


def test_applied_requires_both_the_submission_and_its_provenance(archive_path):
    """Never one without the other; `apply_plan` is one transaction."""
    _matter, item = strict_pair()
    archive = archive_path([item])
    apply_plan(plan_for(archive), batch=open_batch(plan_for(archive)))

    for candidate in OpinionMatchCandidate.objects.filter(state=OpinionCandidateState.APPLIED):
        assert candidate.submission_imports.exists()
        for imported in candidate.submission_imports.all():
            assert imported.submission_id is not None


def test_no_matter_field_is_touched_by_a_recipient_backfill(archive_path):
    from app.legacy_import.opinion_recipients import resolve_recipients

    matter, item = strict_pair()
    archive = archive_path([item])
    apply_plan(plan_for(archive), batch=open_batch(plan_for(archive)))
    factories.OrganisationFactory(name="Näidisministeerium")
    before = Matter.objects.get(pk=matter.pk).updated_at

    resolve_recipients(apply=True)

    assert Matter.objects.get(pk=matter.pk).updated_at == before
