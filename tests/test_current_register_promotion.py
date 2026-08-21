"""Activating one reviewed register year as current work.

The conservative import is right about history and wrong about the year the
department is working in right now. These tests hold both halves: that the
current year becomes current work, and that everything the source says
otherwise — a closure, a native Matter, an unsettled row — survives being run
over.

The load-bearing assertions are the negative ones. Promotion must not invent a
next action out of `JÄRGMISEKS` free text, a submission out of a `VÄLJA` date,
or a closure timestamp the register never held, because each of those would be
indistinguishable from a real one afterwards.
"""

from __future__ import annotations

import pytest

from app.audit.enums import ChangeEventType
from app.audit.models import ChangeEvent
from app.legacy_import.current_register import (
    REVIEWED_CURRENT_YEARS,
    Classification,
    UnreviewedYear,
    apply_promotion_plan,
    build_promotion_plan,
    summary,
)
from app.matters.enums import MatterOrigin, RecordMode
from app.matters.models import Matter
from app.search.models import SearchDocument
from app.submissions.models import Submission
from app.workflow.models import NextAction
from tests.synthetic_portfolio import (
    ALREADY_FULL,
    ARCHIVE_YEAR,
    BARE,
    CLOSED,
    CURRENT_YEAR,
    HISTORICAL,
    NATIVE,
    OWNED_CANDIDATE,
    RESTRICTED,
    UNASSIGNED,
    build_portfolio,
)

pytestmark = pytest.mark.django_db


def classifications(plan) -> dict[str, str]:
    return {candidate.matter.title: candidate.classification for candidate in plan.candidates}


# =========================================================================
# Classification
# =========================================================================


def test_a_genuine_current_row_is_a_promotion_candidate() -> None:
    build_portfolio()
    assert classifications(build_promotion_plan(year=CURRENT_YEAR))[OWNED_CANDIDATE] == (
        Classification.PROMOTE
    )


def test_a_row_the_ledger_proposed_as_a_full_candidate_is_promoted() -> None:
    build_portfolio()
    candidate = next(
        c
        for c in build_promotion_plan(year=CURRENT_YEAR).candidates
        if c.matter.title == OWNED_CANDIDATE
    )
    assert candidate.full_candidate_ledger
    assert candidate.promotes


def test_an_unassigned_current_row_is_still_current_work() -> None:
    """Having no owner is a state to show, not a reason to hide the file."""
    build_portfolio()
    assert classifications(build_promotion_plan(year=CURRENT_YEAR))[UNASSIGNED] == (
        Classification.PROMOTE
    )


def test_an_explicitly_closed_row_is_not_promoted() -> None:
    """A closed FULL Matter needs a closure timestamp the register never had."""
    build_portfolio()
    assert classifications(build_promotion_plan(year=CURRENT_YEAR))[CLOSED] == (
        Classification.EXPLICITLY_CLOSED
    )


def test_a_row_with_nothing_but_a_title_is_not_activated() -> None:
    build_portfolio()
    assert classifications(build_promotion_plan(year=CURRENT_YEAR))[BARE] == (
        Classification.INSUFFICIENT_SOURCE
    )


def test_a_native_matter_wearing_the_same_number_is_never_overwritten() -> None:
    build_portfolio()
    assert classifications(build_promotion_plan(year=CURRENT_YEAR))[NATIVE] == (
        Classification.NATIVE_SKIP
    )


def test_a_matter_that_is_already_full_is_left_alone() -> None:
    build_portfolio()
    assert classifications(build_promotion_plan(year=CURRENT_YEAR))[ALREADY_FULL] == (
        Classification.ALREADY_FULL
    )


def test_an_older_archive_year_is_not_touched_by_the_current_year_operation() -> None:
    portfolio = build_portfolio()
    plan = build_promotion_plan(year=CURRENT_YEAR)
    assert HISTORICAL not in classifications(plan)

    apply_promotion_plan(plan)
    assert portfolio.matter(HISTORICAL).record_mode == RecordMode.ARCHIVE


def test_a_onenote_only_matter_is_never_promoted() -> None:
    """OneNote provenance and a register row together is a reconciliation."""
    portfolio = build_portfolio()
    matter = portfolio.matter(OWNED_CANDIDATE)
    Matter.objects.filter(pk=matter.pk).update(origin=MatterOrigin.LEGACY_ONENOTE)

    assert classifications(build_promotion_plan(year=CURRENT_YEAR))[OWNED_CANDIDATE] == (
        Classification.REVIEW_REQUIRED
    )


def test_a_conflicted_source_reference_goes_to_review_not_to_promotion() -> None:
    from app.legacy_import.models import ConflictState, MatterSourceReference

    portfolio = build_portfolio()
    matter = portfolio.matter(OWNED_CANDIDATE)
    MatterSourceReference.objects.filter(matter=matter).update(
        conflict_state=ConflictState.CONFLICTING_EVIDENCE
    )

    assert classifications(build_promotion_plan(year=CURRENT_YEAR))[OWNED_CANDIDATE] == (
        Classification.CONFLICT
    )


# =========================================================================
# Applying
# =========================================================================


def test_applying_makes_the_candidate_a_full_open_matter() -> None:
    portfolio = build_portfolio()
    apply_promotion_plan(build_promotion_plan(year=CURRENT_YEAR))

    matter = portfolio.matter(OWNED_CANDIDATE)
    assert matter.record_mode == RecordMode.FULL
    assert matter.is_open is True
    assert matter.origin == MatterOrigin.PROMOTED_LEGACY


def test_promotion_preserves_everything_the_register_already_said() -> None:
    portfolio = build_portfolio()
    before = portfolio.matter(OWNED_CANDIDATE)
    reference = (before.reference_year, before.reference_number)
    source_reference_count = before.source_references.count()

    apply_promotion_plan(build_promotion_plan(year=CURRENT_YEAR))

    after = portfolio.matter(OWNED_CANDIDATE)
    assert (after.reference_year, after.reference_number) == reference
    assert after.stage_id == before.stage_id
    assert after.received_date == before.received_date
    assert after.response_deadline == before.response_deadline
    assert after.addressee_organisation_id == before.addressee_organisation_id
    assert after.source_references.count() == source_reference_count


def test_promotion_fabricates_no_next_action_no_submission_and_no_closure() -> None:
    """The source says `Ootame ministeeriumi vastust`. It stays free text.

    Turning every `JÄRGMISEKS` string into a DO with a deadline is exactly the
    conflation the register suffered from: the same column holds a thing to do,
    a thing to wait for and somebody else's expected timing, and this product
    exists partly to keep those apart (Stage-2F brief 19).
    """
    portfolio = build_portfolio()
    apply_promotion_plan(build_promotion_plan(year=CURRENT_YEAR))

    matter = portfolio.matter(OWNED_CANDIDATE)
    assert not NextAction.objects.filter(matter=matter).exists()
    assert not Submission.objects.filter(matter=matter).exists()
    assert matter.closed_at is None
    assert matter.disposition == ""


def test_a_closed_archive_row_keeps_its_closure_and_gains_no_date() -> None:
    portfolio = build_portfolio()
    apply_promotion_plan(build_promotion_plan(year=CURRENT_YEAR))

    closed = portfolio.matter(CLOSED)
    assert closed.record_mode == RecordMode.ARCHIVE
    assert closed.is_open is False
    assert closed.closed_at is None, "a closure date the register never held is not invented"


def test_a_native_matter_survives_the_operation_unchanged() -> None:
    portfolio = build_portfolio()
    before = portfolio.matter(NATIVE)
    apply_promotion_plan(build_promotion_plan(year=CURRENT_YEAR))

    after = portfolio.matter(NATIVE)
    assert after.origin == MatterOrigin.NATIVE
    assert after.owner_id == before.owner_id


def test_running_it_twice_promotes_nothing_the_second_time() -> None:
    build_portfolio()
    first = apply_promotion_plan(build_promotion_plan(year=CURRENT_YEAR))
    assert first.promoted > 0

    second = apply_promotion_plan(build_promotion_plan(year=CURRENT_YEAR))
    assert second.promoted == 0


def test_every_promotion_is_audited_with_its_source_and_its_rule() -> None:
    portfolio = build_portfolio()
    apply_promotion_plan(build_promotion_plan(year=CURRENT_YEAR))

    event = ChangeEvent.objects.filter(
        matter=portfolio.matter(OWNED_CANDIDATE),
        event_type=ChangeEventType.MATTER_PROMOTED,
    ).latest("created_at")

    assert event.payload["operation"] == "promote_current_register"
    assert event.payload["source_year"] == CURRENT_YEAR
    assert event.payload["from_record_mode"] == RecordMode.ARCHIVE.value
    assert event.payload["to_record_mode"] == RecordMode.FULL.value
    assert event.payload["source_references"]


def test_the_search_projection_reflects_the_promoted_matters() -> None:
    portfolio = build_portfolio()
    apply_promotion_plan(build_promotion_plan(year=CURRENT_YEAR))

    matter = portfolio.matter(OWNED_CANDIDATE)
    assert SearchDocument.objects.filter(matter=matter, source_kind="MATTER").exists()


# =========================================================================
# Other years
# =========================================================================


def test_the_reviewed_years_are_exactly_the_current_one() -> None:
    """The list is the decision. Extending it is a reviewed change with a diff."""
    assert REVIEWED_CURRENT_YEARS == (CURRENT_YEAR,)


def test_an_unreviewed_year_can_be_analysed() -> None:
    build_portfolio()
    figures = summary(build_promotion_plan(year=ARCHIVE_YEAR))
    assert figures["reviewed_year"] is False
    assert figures["source_matters"] == 1


def test_an_unreviewed_year_cannot_be_applied() -> None:
    """The 2026 decision is about 2026 and must not leak backwards."""
    build_portfolio()
    plan = build_promotion_plan(year=ARCHIVE_YEAR)
    with pytest.raises(UnreviewedYear):
        apply_promotion_plan(plan)


def test_refusing_an_unreviewed_year_promotes_nothing() -> None:
    portfolio = build_portfolio()
    plan = build_promotion_plan(year=ARCHIVE_YEAR)
    with pytest.raises(UnreviewedYear):
        apply_promotion_plan(plan)
    assert portfolio.matter(HISTORICAL).record_mode == RecordMode.ARCHIVE


# =========================================================================
# Reporting
# =========================================================================


def test_the_dry_run_report_is_aggregate_and_carries_no_source_content() -> None:
    build_portfolio()
    rendered = str(summary(build_promotion_plan(year=CURRENT_YEAR)))
    for forbidden in (OWNED_CANDIDATE, RESTRICTED, "Sandra", "Ootame ministeeriumi vastust"):
        assert forbidden not in rendered


def test_the_report_counts_the_data_quality_of_what_it_would_activate() -> None:
    build_portfolio()
    figures = summary(build_promotion_plan(year=CURRENT_YEAR))

    of_which = figures["of_which"]
    assert of_which["owner_populated"] + of_which["owner_unresolved"] == figures["would_promote"]
    assert (
        of_which["with_response_deadline"] + of_which["without_response_deadline"]
        == figures["would_promote"]
    )
