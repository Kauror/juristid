"""Reconciling the current portfolio against the final approved snapshot.

The load-bearing assertions are the negative ones. This operation moves Matters
in and out of the department's live work on the strength of a spreadsheet, so
what it must *not* do matters more than what it does: no invented closure, no
`NextAction` from free text, no `Submission` from a date, no native Matter
touched, and no silent overwrite of a decision a person already made.
"""

from __future__ import annotations

import pytest

from app.audit.enums import ChangeEventType
from app.audit.models import ChangeEvent
from app.legacy_import.current_state import CurrentRegisterState, RegisterCurrency
from app.legacy_import.final_cutover import (
    Action,
    ReviewReason,
    UnreviewedSnapshot,
    apply_cutover_plan,
    build_cutover_plan,
    summary,
)
from app.legacy_import.models import MatterSourceReference
from app.matters.enums import MatterOrigin, RecordMode
from app.matters.models import Entry, Matter
from app.search.models import SearchDocument, SearchSourceKind
from app.submissions.models import Submission
from app.workflow.models import NextAction
from tests import factories
from tests.synthetic_cutover import (
    AMBIGUOUS_ROW,
    BARE_REFERENCE_ROW,
    CARRY_OVER_LIVE,
    CARRY_OVER_REOPENABLE,
    CONFLICT_ACTION,
    CONFLICT_ENTRY,
    CONFLICT_SUBMISSION,
    CURRENT_DRAFTING,
    CURRENT_OTHER_STATUS,
    CURRENT_SENT,
    EARLIER_SNAPSHOT,
    FINAL_SNAPSHOT,
    NATIVE_ROW,
    OWNER_UNKNOWN,
    PADDING_ROW,
    REAL_CLOSURE,
    RETIRING_IN_FORCE,
    RETIRING_NO_PLANS,
    SUPERSEDED_ROW,
    build_world,
)

pytestmark = pytest.mark.django_db


@pytest.fixture
def reviewed(monkeypatch: pytest.MonkeyPatch) -> None:
    """Approve the synthetic snapshot, the way a code change approves a real one."""
    monkeypatch.setattr(
        "app.legacy_import.final_cutover.REVIEWED_SNAPSHOT_SHA256",
        (FINAL_SNAPSHOT,),
    )


@pytest.fixture
def world():
    return build_world()


def plan_for(snapshot: str = FINAL_SNAPSHOT):
    return build_cutover_plan(snapshot_sha256=snapshot)


def actions(plan) -> dict[str, str]:
    return {candidate.matter.title: candidate.action for candidate in plan.candidates}


def currencies(plan) -> dict[str, str]:
    return {candidate.matter.title: candidate.currency for candidate in plan.candidates}


# =========================================================================
# What the snapshot says
# =========================================================================


def test_a_pre_numbered_blank_row_is_not_a_candidate(world) -> None:
    """105 such rows exist in the approved snapshot. None is work."""
    assert PADDING_ROW not in actions(plan_for())


def test_a_terminal_status_leaves_the_current_set(world) -> None:
    outcome = actions(plan_for())
    assert outcome[RETIRING_IN_FORCE] == Action.RETIRE
    assert outcome[RETIRING_NO_PLANS] == Action.RETIRE


def test_a_live_status_stays_current(world) -> None:
    outcome = actions(plan_for())
    assert outcome[CURRENT_DRAFTING] == Action.KEEP_CURRENT
    assert outcome[CURRENT_SENT] == Action.KEEP_CURRENT


def test_the_status_muu_stays_current(world) -> None:
    assert actions(plan_for())[CURRENT_OTHER_STATUS] == Action.KEEP_CURRENT


def test_a_sent_opinion_does_not_leave_the_current_set(world) -> None:
    """`VÄLJA` says the opinion went out, never that the Matter finished.

    This Matter is before the Riigikogu with its opinion already sent, which is
    the ordinary shape of the work and the row the commonest misreading of this
    register gets wrong.
    """
    plan = plan_for()
    assert currencies(plan)[CURRENT_SENT] == RegisterCurrency.CURRENT
    assert actions(plan)[CURRENT_SENT] == Action.KEEP_CURRENT


# =========================================================================
# Continuation
# =========================================================================


def test_an_explicit_continuation_leaves_the_current_set(world) -> None:
    plan = plan_for()
    assert currencies(plan)[SUPERSEDED_ROW] == RegisterCurrency.SUPERSEDED
    assert actions(plan)[SUPERSEDED_ROW] == Action.RETIRE


def test_a_bare_cross_reference_does_not_supersede(world) -> None:
    assert actions(plan_for())[BARE_REFERENCE_ROW] == Action.KEEP_CURRENT


def test_an_ambiguous_continuation_goes_to_review(world) -> None:
    plan = plan_for()
    candidate = next(c for c in plan.candidates if c.matter.title == AMBIGUOUS_ROW)
    assert candidate.action == Action.REVIEW_REQUIRED
    assert candidate.review_reason == ReviewReason.AMBIGUOUS_CONTINUATION


def test_an_ambiguous_row_is_left_exactly_as_it_was(world, reviewed) -> None:
    before = world.refresh(AMBIGUOUS_ROW)
    apply_cutover_plan(plan_for())
    after = world.refresh(AMBIGUOUS_ROW)
    assert (after.record_mode, after.is_open) == (before.record_mode, before.is_open)


# =========================================================================
# Carry-over and reactivation
# =========================================================================


def test_live_carry_over_work_becomes_current(world, reviewed) -> None:
    """A proceeding does not end because a calendar year did.

    The year-only rule had archived every 2025 row; 60 of them are live.
    """
    assert actions(plan_for())[CARRY_OVER_LIVE] == Action.ACTIVATE
    apply_cutover_plan(plan_for())
    matter = world.refresh(CARRY_OVER_LIVE)
    assert matter.record_mode == RecordMode.FULL
    assert matter.is_open


def test_a_matter_the_historical_cutover_retired_can_be_reactivated(world, reviewed) -> None:
    """ARCHIVE *and closed*, and reopened before promotion.

    Stage 2I's default invented no disposition and no timestamp, which is
    exactly what makes it recognisable and safe to reverse.
    """
    apply_cutover_plan(plan_for())
    matter = world.refresh(CARRY_OVER_REOPENABLE)
    assert matter.record_mode == RecordMode.FULL
    assert matter.is_open
    assert matter.disposition == ""
    assert matter.closed_at is None


def test_a_real_recorded_closure_is_never_reversed(world, reviewed) -> None:
    """Somebody wrote a disposition there. The register does not undo that."""
    plan = plan_for()
    candidate = next(c for c in plan.candidates if c.matter.title == REAL_CLOSURE)
    assert candidate.action == Action.REVIEW_REQUIRED
    assert candidate.review_reason == ReviewReason.RECORDED_CLOSURE

    apply_cutover_plan(plan)
    matter = world.refresh(REAL_CLOSURE)
    assert not matter.is_open
    assert matter.disposition


# =========================================================================
# Retirement invents nothing
# =========================================================================


def test_leaving_the_current_set_writes_no_closure_facts(world, reviewed) -> None:
    """The shape ADR 0020 established, reached from better evidence.

    ARCHIVE and closed, with no disposition, no timestamp and no closing
    person: *the register no longer lists this as current; the exact closure
    fact is unknown*.
    """
    apply_cutover_plan(plan_for())
    matter = world.refresh(RETIRING_IN_FORCE)

    assert matter.record_mode == RecordMode.ARCHIVE
    assert not matter.is_open
    assert matter.disposition == ""
    assert matter.closed_at is None
    assert matter.closed_by is None


def test_retirement_is_not_recorded_as_an_ordinary_closure(world, reviewed) -> None:
    """Its own event type, so nothing later reads it as a professional decision."""
    apply_cutover_plan(plan_for())
    matter = world.refresh(RETIRING_IN_FORCE)

    kinds = set(ChangeEvent.objects.filter(matter=matter).values_list("event_type", flat=True))
    assert ChangeEventType.MATTER_REGISTER_CUTOVER_RETIRED in kinds
    assert ChangeEventType.MATTER_CLOSED not in kinds


# =========================================================================
# Native work, and work somebody has already done here
# =========================================================================


def test_a_native_matter_is_never_touched(world, reviewed) -> None:
    before = world.refresh(NATIVE_ROW)
    assert actions(plan_for())[NATIVE_ROW] == Action.NATIVE_SKIP

    apply_cutover_plan(plan_for())
    after = world.refresh(NATIVE_ROW)
    assert after.origin == MatterOrigin.NATIVE
    assert (after.record_mode, after.is_open) == (before.record_mode, before.is_open)


def test_authored_entries_hold_back_a_retirement(world, reviewed) -> None:
    """No importer in this codebase writes an Entry, so one is always a person's."""
    from app.matters.services import add_entry

    add_entry(
        matter=world[CONFLICT_ENTRY],
        body="<p>Sünteetiline märkus, kirjutatud siin.</p>",
        author=world.people.sandra,
    )

    candidate = next(c for c in plan_for().candidates if c.matter.title == CONFLICT_ENTRY)
    assert candidate.action == Action.REVIEW_REQUIRED
    assert candidate.review_reason == ReviewReason.AUTHORED_ENTRIES

    apply_cutover_plan(plan_for())
    assert world.refresh(CONFLICT_ENTRY).is_open


def test_an_open_next_action_holds_back_a_retirement(world, reviewed) -> None:
    from app.workflow.services import set_next_action

    set_next_action(
        matter=world[CONFLICT_ACTION],
        text="Sünteetiline järgmine samm.",
        actor=world.people.sandra,
        target_date=None,
        kind="WAIT",
        date_semantics="REVIEW_ON",
    )

    candidate = next(c for c in plan_for().candidates if c.matter.title == CONFLICT_ACTION)
    assert candidate.action == Action.REVIEW_REQUIRED
    assert candidate.review_reason == ReviewReason.OPEN_NEXT_ACTION


def test_a_submission_made_here_holds_back_a_retirement(world, reviewed) -> None:
    """A native submission, as distinct from one the opinion archive rebuilt."""
    from app.submissions.services import create_submission

    create_submission(
        matter=world[CONFLICT_SUBMISSION],
        title="Sünteetiline arvamus",
        actor=world.people.sandra,
    )

    candidate = next(c for c in plan_for().candidates if c.matter.title == CONFLICT_SUBMISSION)
    assert candidate.action == Action.REVIEW_REQUIRED
    assert candidate.review_reason == ReviewReason.NATIVE_SUBMISSION


# =========================================================================
# Nothing is fabricated
# =========================================================================


def test_no_next_action_is_created_from_the_source_instruction(world, reviewed) -> None:
    """134 current Matters carry one in the approved snapshot. None becomes a row."""
    apply_cutover_plan(plan_for())
    assert not NextAction.objects.exists()


def test_no_submission_is_created_from_a_send_date(world, reviewed) -> None:
    """A SENT submission needs evidence. A date is not evidence (ADR 0011)."""
    apply_cutover_plan(plan_for())
    assert not Submission.objects.exists()


def test_no_entry_is_created_by_the_operation(world, reviewed) -> None:
    apply_cutover_plan(plan_for())
    assert not Entry.objects.exists()


# =========================================================================
# Source evidence
# =========================================================================


def test_the_operation_writes_no_source_reference(world, reviewed) -> None:
    """It reconciles against evidence the importer wrote; it creates none."""
    before = MatterSourceReference.objects.count()
    apply_cutover_plan(plan_for())
    assert MatterSourceReference.objects.count() == before


def test_an_earlier_snapshot_survives_untouched(world, reviewed) -> None:
    """A newer workbook is new evidence, never an edit to the old (ADR 0012)."""
    earlier = list(
        MatterSourceReference.objects.filter(source_snapshot_sha256=EARLIER_SNAPSHOT).values_list(
            "pk", "source_row_raw"
        )
    )
    assert earlier

    apply_cutover_plan(plan_for())

    after = list(
        MatterSourceReference.objects.filter(source_snapshot_sha256=EARLIER_SNAPSHOT).values_list(
            "pk", "source_row_raw"
        )
    )
    assert after == earlier


def test_only_the_named_snapshot_is_reconciled(world) -> None:
    """The earlier workbook's rows are not silently folded into the answer."""
    titles = set(actions(plan_for(EARLIER_SNAPSHOT)))
    assert titles == {CURRENT_DRAFTING}


# =========================================================================
# The derived state table
# =========================================================================


def test_the_derived_state_records_the_snapshot_it_came_from(world, reviewed) -> None:
    apply_cutover_plan(plan_for())
    state = CurrentRegisterState.objects.get(matter=world[CURRENT_DRAFTING])
    assert state.source_snapshot_sha256 == FINAL_SNAPSHOT
    assert state.currency == RegisterCurrency.CURRENT


def test_the_send_date_is_preserved_and_is_not_a_submission(world, reviewed) -> None:
    apply_cutover_plan(plan_for())
    state = CurrentRegisterState.objects.get(matter=world[CURRENT_SENT])
    assert state.opinion_sent_date is not None
    assert not state.is_drafting
    assert not Submission.objects.exists()


def test_a_blank_send_column_makes_a_matter_drafting(world, reviewed) -> None:
    apply_cutover_plan(plan_for())
    state = CurrentRegisterState.objects.get(matter=world[CURRENT_DRAFTING])
    assert state.opinion_sent_date is None
    assert state.is_drafting


def test_the_source_instruction_is_preserved_verbatim(world, reviewed) -> None:
    apply_cutover_plan(plan_for())
    state = CurrentRegisterState.objects.get(matter=world[SUPERSEDED_ROW])
    assert "2026_1" in state.next_action_text
    assert state.continues_under_reference == "2026_1"


def test_an_unresolved_owner_stays_unresolved(world, reviewed) -> None:
    """The source names somebody with no account, and none is invented.

    The raw name is kept as source responsibility — the register's one
    certainty — while the canonical owner stays empty. Supplying an account
    later resolves it with no re-import.
    """
    apply_cutover_plan(plan_for())

    state = CurrentRegisterState.objects.get(matter=world[CURRENT_OTHER_STATUS])
    assert state.owner_raw == OWNER_UNKNOWN
    assert not state.owner_resolved
    assert world.refresh(CURRENT_OTHER_STATUS).owner is None


def test_a_resolvable_name_is_marked_resolved(world, reviewed) -> None:
    """The other half of the same rule, so "unresolved" cannot pass by default."""
    apply_cutover_plan(plan_for())
    state = CurrentRegisterState.objects.get(matter=world[CARRY_OVER_LIVE])
    assert state.owner_resolved


# =========================================================================
# Field refresh
# =========================================================================


def test_the_stage_is_refreshed_from_the_source_status(world, reviewed) -> None:
    apply_cutover_plan(plan_for())
    matter = world.refresh(CURRENT_DRAFTING)
    assert matter.stage is not None
    assert matter.stage.key == "consultation"


def test_a_resolvable_owner_is_written_to_the_matter(world, reviewed) -> None:
    apply_cutover_plan(plan_for())
    assert world.refresh(CARRY_OVER_LIVE).owner == world.people.sandra


def test_the_refresh_never_touches_a_native_matter(world, reviewed) -> None:
    before = world.refresh(NATIVE_ROW)
    apply_cutover_plan(plan_for())
    after = world.refresh(NATIVE_ROW)
    assert after.stage_id == before.stage_id
    assert after.owner_id == before.owner_id


def test_the_title_is_never_overwritten(world, reviewed) -> None:
    """Both wordings may be right, and the title is what people navigate by."""
    before = {m.pk: m.title for m in Matter.objects.all()}
    apply_cutover_plan(plan_for())
    after = {m.pk: m.title for m in Matter.objects.all()}
    assert after == before


# =========================================================================
# Idempotency
# =========================================================================


def test_a_second_run_changes_nothing(world, reviewed) -> None:
    apply_cutover_plan(plan_for())
    first = {(m.pk, m.record_mode, m.is_open, m.stage_id, m.owner_id) for m in Matter.objects.all()}

    second = apply_cutover_plan(plan_for())
    after = {(m.pk, m.record_mode, m.is_open, m.stage_id, m.owner_id) for m in Matter.objects.all()}

    assert after == first
    assert second.activated == 0
    assert second.retired == 0
    assert second.refreshed == 0


def test_a_second_run_writes_no_further_audit_events(world, reviewed) -> None:
    apply_cutover_plan(plan_for())
    before = ChangeEvent.objects.count()
    apply_cutover_plan(plan_for())
    assert ChangeEvent.objects.count() == before


def test_a_second_run_leaves_one_state_row_per_matter(world, reviewed) -> None:
    apply_cutover_plan(plan_for())
    first = CurrentRegisterState.objects.count()
    apply_cutover_plan(plan_for())

    assert CurrentRegisterState.objects.count() == first
    matters = CurrentRegisterState.objects.values_list("matter_id", flat=True)
    assert len(set(matters)) == len(matters)


# =========================================================================
# Guards
# =========================================================================


def test_an_unreviewed_snapshot_cannot_be_applied(world) -> None:
    """Retiring a department's portfolio is not a command-line argument."""
    with pytest.raises(UnreviewedSnapshot):
        apply_cutover_plan(plan_for())


def test_an_unreviewed_snapshot_can_still_be_analysed(world) -> None:
    plan = plan_for()
    assert not plan.is_reviewed
    assert plan.candidates
    assert summary(plan)["current_total"] > 0


# =========================================================================
# Search
# =========================================================================


def test_a_reactivated_matter_stays_findable(world, reviewed) -> None:
    apply_cutover_plan(plan_for())
    matter = world.refresh(CARRY_OVER_REOPENABLE)
    assert SearchDocument.objects.filter(
        matter=matter, source_kind=SearchSourceKind.MATTER
    ).exists()


def test_a_retired_matter_stays_findable(world, reviewed) -> None:
    """Search is derived, and history stays searchable. It simply now describes
    a Matter that is no longer current."""
    apply_cutover_plan(plan_for())
    matter = world.refresh(RETIRING_IN_FORCE)
    assert SearchDocument.objects.filter(
        matter=matter, source_kind=SearchSourceKind.MATTER
    ).exists()


# =========================================================================
# The aggregate report
# =========================================================================


def test_the_summary_reports_the_resulting_portfolio(world, reviewed) -> None:
    figures = summary(plan_for())
    assert figures["current_total"] == len(plan_for().current_after)
    assert set(figures["current_by_sheet"]) <= {"2025", "2026"}
    assert figures["drafting_total"] >= 1


def test_the_summary_carries_no_source_text(world, reviewed) -> None:
    """Titles and instructions stay on the source reference, never in a report."""
    rendered = repr(summary(plan_for()))
    assert CURRENT_DRAFTING not in rendered
    assert "Jätkub" not in rendered


def test_source_responsibility_keeps_a_name_with_no_account(world, reviewed) -> None:
    """Reporting an unresolvable name as *Määramata* would discard what the
    register is certain about."""
    responsibility = summary(plan_for())["source_responsibility"]
    assert responsibility
    assert any(name for name in responsibility if name)


# =========================================================================
# A restricted Matter is still a Matter
# =========================================================================


def test_the_operation_does_not_read_through_visibility(world, reviewed) -> None:
    """This is an operator command, not a reader's page.

    It runs over every Matter the snapshot names, restricted ones included —
    which is right, because retiring only the visible half would leave the
    portfolio in a state that depends on who ran the command. What is scoped is
    every *reading* surface built on top of it.
    """
    restricted = factories.MatterFactory(
        title="Sünteetiline piiratud registrikirje",
        visibility="RESTRICTED",
        record_mode=RecordMode.FULL,
        origin=MatterOrigin.LEGACY_IMPORT,
        reference_year=2026,
        reference_number=77,
    )
    from tests.synthetic_cutover import CURRENT_SHEET
    from tests.synthetic_portfolio import add_source_reference

    add_source_reference(
        world.register,
        restricted,
        year=CURRENT_SHEET,
        status_cell="jõustunud",
        snapshot=FINAL_SNAPSHOT,
    )

    apply_cutover_plan(plan_for())
    assert not Matter.objects.get(pk=restricted.pk).is_open
