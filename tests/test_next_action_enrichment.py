"""Planning and applying structured actions from the register's ``JÄRGMISEKS``.

The parser is tested one sentence at a time in
``tests/test_register_next_action_parser.py``. What is tested here is
everything the parser is not allowed to decide: who is eligible, whose decision
outranks the source, what the digests protect, and what a second run does.

Every sentence, title and reference below is invented.
"""

from __future__ import annotations

import datetime as dt

import pytest
from django.utils import timezone

from app.audit.enums import ChangeEventType
from app.audit.models import ChangeEvent
from app.legacy_import.current_state import CurrentRegisterState, RegisterCurrency
from app.legacy_import.next_action_enrichment import (
    ENRICHMENT_SOURCE,
    MixedSnapshot,
    Outcome,
    PlanChanged,
    UnknownSnapshot,
    apply_plan,
    build_plan,
    protected_rows,
    summary,
)
from app.matters.enums import MatterDataClass, RecordMode
from app.workflow.enums import (
    ActionKind,
    ActionStatus,
    DatePrecision,
    DateSemantics,
    Disposition,
)
from app.workflow.models import NextAction
from app.workflow.services import complete_next_action, set_next_action
from tests import factories

pytestmark = pytest.mark.django_db

SNAPSHOT = "a" * 64
OTHER_SNAPSHOT = "b" * 64

WAIT_QUARTER = "Ootan eelnõud 2027. aasta 2. kvartalis"
DEADLINE = "Esitada arvamus hiljemalt 15.09.2099"
UNREADABLE = "Menetlus jätkub Riigikogus"
STALE = "Ootan eelnõud 2019. aasta 2. kvartalis"


def _state(
    *,
    text: str,
    matter=None,
    currency: str = RegisterCurrency.CURRENT,
    snapshot: str = SNAPSHOT,
    row: int = 1,
    **matter_kwargs,
) -> CurrentRegisterState:
    """One derived register row, the way the cutover writes them."""
    matter = matter or factories.MatterFactory(**matter_kwargs)
    reference = factories.MatterSourceReferenceFactory(
        matter=matter,
        source_sheet="2026",
        source_row_number=row,
        source_snapshot_sha256=snapshot,
    )
    return CurrentRegisterState.objects.create(
        matter=matter,
        source_reference=reference,
        source_snapshot_sha256=snapshot,
        source_sheet="2026",
        source_row_number=row,
        currency=currency,
        next_action_text=text,
        observed_at=timezone.now(),
    )


def _outcome_for(plan, matter_id) -> str:
    return next(item.outcome for item in plan.proposals if item.matter_id == matter_id)


# -- the snapshot pin -------------------------------------------------------


def test_a_snapshot_nobody_catalogued_is_refused():
    _state(text=WAIT_QUARTER, row=1)
    with pytest.raises(UnknownSnapshot):
        build_plan(snapshot_sha256=OTHER_SNAPSHOT)


def test_derived_state_from_two_workbooks_fails_closed():
    """Half a plan from an older snapshot is worse than no plan.

    ``CurrentRegisterState`` is rebuilt wholesale by the register cutover, so
    two digests in the table means two cutovers left it describing two
    workbooks. No digest computed over that could say which half it spoke for.
    """
    _state(text=WAIT_QUARTER, row=1, snapshot=SNAPSHOT)
    _state(text=WAIT_QUARTER, row=2, snapshot=OTHER_SNAPSHOT)
    with pytest.raises(MixedSnapshot):
        build_plan(snapshot_sha256=SNAPSHOT)


# -- population -------------------------------------------------------------


def test_a_current_open_full_row_with_a_readable_instruction_is_auto():
    state = _state(text=WAIT_QUARTER, row=1)
    plan = build_plan(snapshot_sha256=SNAPSHOT)

    (proposal,) = plan.auto
    assert proposal.matter_id == state.matter_id
    assert proposal.kind == ActionKind.WAIT
    assert proposal.date_semantics == DateSemantics.EXPECTED_AROUND
    assert proposal.target_date == dt.date(2027, 4, 1)
    assert proposal.date_precision == DatePrecision.QUARTER


@pytest.mark.parametrize(
    ("kwargs", "expected"),
    [
        ({"currency": RegisterCurrency.RETIRED}, Outcome.SKIP_NOT_CURRENT),
        ({"currency": RegisterCurrency.REVIEW_REQUIRED}, Outcome.SKIP_NOT_CURRENT),
        ({"record_mode": RecordMode.ARCHIVE}, Outcome.SKIP_ARCHIVE_RECORD),
        (
            {
                "is_open": False,
                "closed_at": dt.datetime(2026, 1, 5, tzinfo=dt.UTC),
                "disposition": Disposition.COMPLETED,
            },
            Outcome.SKIP_CLOSED,
        ),
        ({"data_class": MatterDataClass.TEST}, Outcome.SKIP_TEST_DATA),
    ],
)
def test_the_population_is_narrow(kwargs, expected):
    """Everything outside current open full business work is named, not dropped.

    A closed Matter cannot take an open instruction and the service would refuse
    it; a retired row's instruction is a note about finished work; a development
    record is not the department's history.
    """
    fields = dict(kwargs)
    currency = fields.pop("currency", RegisterCurrency.CURRENT)
    state = _state(text=WAIT_QUARTER, row=1, currency=currency, **fields)
    plan = build_plan(snapshot_sha256=SNAPSHOT)
    assert _outcome_for(plan, state.matter_id) == expected
    assert plan.auto == ()


def test_a_blank_cell_is_counted_separately_from_a_rejection():
    """Nothing was written, so nothing was refused."""
    state = _state(text="   ", row=1)
    plan = build_plan(snapshot_sha256=SNAPSHOT)
    assert _outcome_for(plan, state.matter_id) == Outcome.SKIP_EMPTY
    assert summary(plan)["source_instructions"] == 0


def test_an_unreadable_instruction_is_review_required_with_a_named_reason():
    state = _state(text=UNREADABLE, row=1)
    plan = build_plan(snapshot_sha256=SNAPSHOT)

    assert _outcome_for(plan, state.matter_id) == Outcome.REVIEW_REQUIRED
    assert summary(plan)["review_reasons"] == {"NO_KIND": 1}


def test_an_instruction_whose_period_has_passed_is_not_created_today():
    """Understood, and about a quarter in 2019.

    Creating it now would put a six-year-old instruction on somebody's list as
    though it were new work.
    """
    state = _state(text=STALE, row=1)
    plan = build_plan(snapshot_sha256=SNAPSHOT)
    assert _outcome_for(plan, state.matter_id) == Outcome.STALE_SOURCE
    assert plan.auto == ()


# -- human work wins --------------------------------------------------------


@pytest.mark.parametrize("status", [ActionStatus.CANCELLED, ActionStatus.SUPERSEDED])
def test_any_prior_action_history_stops_the_enrichment(status, specialist):
    """Not "any open one". Any at all.

    A cancelled or superseded action proves somebody has already worked this
    file through the structured workflow. Reviving an Excel sentence over their
    decision is the one failure this operation must not have.
    """
    matter = factories.MatterFactory(owner=specialist)
    factories.NextActionFactory(matter=matter, target_date=dt.date(2026, 1, 1), status=status)
    state = _state(text=WAIT_QUARTER, row=1, matter=matter)

    plan = build_plan(snapshot_sha256=SNAPSHOT)
    assert _outcome_for(plan, state.matter_id) == Outcome.SKIP_EXISTING_ACTION_HISTORY
    assert plan.auto == ()


def test_a_completed_action_protects_the_matter_too(specialist):
    matter = factories.MatterFactory(owner=specialist)
    action = set_next_action(
        matter=matter,
        text="Käsitsi tehtud otsus",
        actor=specialist,
        target_date=dt.date(2099, 1, 1),
    )
    complete_next_action(action=action, actor=specialist)
    state = _state(text=WAIT_QUARTER, row=1, matter=matter)

    plan = build_plan(snapshot_sha256=SNAPSHOT)
    assert _outcome_for(plan, state.matter_id) == Outcome.SKIP_EXISTING_ACTION_HISTORY


def test_a_hand_made_open_action_is_left_exactly_as_it_was(specialist):
    """Production carries at least one of these. It must come through untouched."""
    matter = factories.MatterFactory(owner=specialist)
    action = set_next_action(
        matter=matter,
        text="Inimese kirjutatud tegevus",
        actor=specialist,
        target_date=dt.date(2099, 3, 1),
    )
    _state(text=WAIT_QUARTER, row=1, matter=matter)

    plan = build_plan(snapshot_sha256=SNAPSHOT)
    apply_plan(plan, expect_plan_sha256=plan.digest)

    action.refresh_from_db()
    assert action.status == ActionStatus.OPEN
    assert action.text == "Inimese kirjutatud tegevus"
    assert NextAction.objects.filter(matter=matter).count() == 1


# -- applying ---------------------------------------------------------------


def test_apply_creates_the_action_and_keeps_the_source_verbatim(specialist):
    matter = factories.MatterFactory(owner=specialist)
    state = _state(text=WAIT_QUARTER, row=1, matter=matter)

    plan = build_plan(snapshot_sha256=SNAPSHOT)
    result = apply_plan(plan, expect_plan_sha256=plan.digest)

    assert result.created == 1
    action = NextAction.objects.get(matter=matter)
    assert action.kind == ActionKind.WAIT
    assert action.date_semantics == DateSemantics.EXPECTED_AROUND
    assert action.target_date == dt.date(2027, 4, 1)
    assert action.date_precision == DatePrecision.QUARTER
    # The evidence travels with the interpretation.
    assert action.source_text == WAIT_QUARTER
    assert action.text == WAIT_QUARTER
    # And the register's own cell is untouched: the two are different claims
    # and the first stays true.
    state.refresh_from_db()
    assert state.next_action_text == WAIT_QUARTER


def test_responsibility_falls_to_the_matter_owner(specialist):
    matter = factories.MatterFactory(owner=specialist)
    _state(text=WAIT_QUARTER, row=1, matter=matter)
    plan = build_plan(snapshot_sha256=SNAPSHOT)
    apply_plan(plan, expect_plan_sha256=plan.digest)

    assert NextAction.objects.get(matter=matter).responsible == specialist


def test_an_ownerless_matter_gets_an_ownerless_action_and_is_reported():
    """No User is invented. The gap is a number in the report instead."""
    matter = factories.MatterFactory(owner=None)
    _state(text=WAIT_QUARTER, row=1, matter=matter)

    plan = build_plan(snapshot_sha256=SNAPSHOT)
    assert summary(plan)["auto_without_responsible"] == 1

    apply_plan(plan, expect_plan_sha256=plan.digest)
    assert NextAction.objects.get(matter=matter).responsible is None


def test_the_action_is_attributed_to_nobody_and_carries_its_provenance(specialist):
    """A machine read this. Naming whoever ran the command would be a lie.

    What did decide it is recorded instead — the source, the workbook digest,
    the immutable reference and the parser version — on the ordinary
    ``NEXT_ACTION_SET`` event rather than a second one.
    """
    matter = factories.MatterFactory(owner=specialist)
    state = _state(text=WAIT_QUARTER, row=1, matter=matter)

    plan = build_plan(snapshot_sha256=SNAPSHOT)
    apply_plan(plan, expect_plan_sha256=plan.digest)

    action = NextAction.objects.get(matter=matter)
    assert action.created_by is None

    event = ChangeEvent.objects.get(matter=matter, event_type=ChangeEventType.NEXT_ACTION_SET)
    assert event.actor is None
    provenance = event.payload["provenance"]
    assert provenance["source"] == ENRICHMENT_SOURCE
    assert provenance["source_snapshot_sha256"] == SNAPSHOT
    assert provenance["source_reference_id"] == str(state.source_reference_id)
    assert provenance["parser_version"] == plan.parser_version


def test_a_manual_next_action_records_no_provenance(specialist, normal_matter):
    """The optional argument must not change what the ordinary path writes."""
    set_next_action(
        matter=normal_matter, text="Käsitsi", actor=specialist, target_date=dt.date(2099, 1, 1)
    )
    event = ChangeEvent.objects.get(
        matter=normal_matter, event_type=ChangeEventType.NEXT_ACTION_SET
    )
    assert "provenance" not in event.payload


# -- the digests ------------------------------------------------------------


def test_the_plan_digest_is_stable_across_identical_runs():
    _state(text=WAIT_QUARTER, row=1)
    _state(text=DEADLINE, row=2)
    assert (
        build_plan(snapshot_sha256=SNAPSHOT).digest == build_plan(snapshot_sha256=SNAPSHOT).digest
    )


def test_a_wrong_digest_writes_nothing():
    _state(text=WAIT_QUARTER, row=1)
    plan = build_plan(snapshot_sha256=SNAPSHOT)
    with pytest.raises(PlanChanged):
        apply_plan(plan, expect_plan_sha256="0" * 64)
    assert NextAction.objects.count() == 0


def test_a_source_sentence_changed_after_planning_aborts_the_whole_run():
    """One moved row stops everything, rather than most of it being written.

    A partial apply against a digest the operator approved would leave a state
    that neither the plan nor the database describes.
    """
    first = _state(text=WAIT_QUARTER, row=1)
    _state(text=DEADLINE, row=2)
    plan = build_plan(snapshot_sha256=SNAPSHOT)
    approved = plan.digest

    CurrentRegisterState.objects.filter(pk=first.pk).update(
        next_action_text="Ootan hoopis midagi muud 2028. aastal"
    )

    with pytest.raises(PlanChanged):
        apply_plan(build_plan(snapshot_sha256=SNAPSHOT), expect_plan_sha256=approved)
    assert NextAction.objects.count() == 0


def test_an_action_appearing_between_plan_and_apply_stops_the_run(specialist):
    """Somebody worked the file. Their decision stands and the run refuses."""
    matter = factories.MatterFactory(owner=specialist)
    _state(text=WAIT_QUARTER, row=1, matter=matter)
    plan = build_plan(snapshot_sha256=SNAPSHOT)
    approved = plan.digest

    set_next_action(
        matter=matter,
        text="Vahepeal tehtud otsus",
        actor=specialist,
        target_date=dt.date(2099, 5, 1),
    )

    with pytest.raises(PlanChanged):
        apply_plan(plan, expect_plan_sha256=approved)
    assert NextAction.objects.filter(matter=matter).count() == 1
    assert NextAction.objects.get(matter=matter).text == "Vahepeal tehtud otsus"


def test_a_second_apply_creates_nothing_and_raises_no_second_event(specialist):
    """Idempotency, and it falls out of the precedence rule rather than a flag.

    Once an enrichment action exists the Matter has action history, so the next
    plan classifies it exactly as it classifies a hand-made one.
    """
    matter = factories.MatterFactory(owner=specialist)
    state = _state(text=WAIT_QUARTER, row=1, matter=matter)

    first = build_plan(snapshot_sha256=SNAPSHOT)
    apply_plan(first, expect_plan_sha256=first.digest)

    second = build_plan(snapshot_sha256=SNAPSHOT)
    assert _outcome_for(second, state.matter_id) == Outcome.SKIP_EXISTING_ACTION_HISTORY
    assert second.auto == ()

    result = apply_plan(second, expect_plan_sha256=second.digest)
    assert result.created == 0
    assert NextAction.objects.filter(matter=matter).count() == 1
    assert (
        ChangeEvent.objects.filter(
            matter=matter, event_type=ChangeEventType.NEXT_ACTION_SET
        ).count()
        == 1
    )


# -- reporting --------------------------------------------------------------


def test_the_report_carries_no_source_text(specialist):
    """A file somebody may email must not hold the register's own prose."""
    matter = factories.MatterFactory(owner=specialist, title="Sünteetiline pakendieelnõu")
    _state(text=WAIT_QUARTER, row=1, matter=matter)
    plan = build_plan(snapshot_sha256=SNAPSHOT)

    encoded = repr(summary(plan)) + repr(protected_rows(plan))
    assert WAIT_QUARTER not in encoded
    assert "Sünteetiline pakendieelnõu" not in encoded
    # The sentence's identity is there instead.
    assert plan.auto[0].source_text_sha256 in encoded


# -- the command ------------------------------------------------------------


def test_the_plan_command_writes_nothing_and_prints_its_digest(specialist):
    """The digest is the only thing ``apply`` will accept, so it has to be
    printed where the operator reading the report can see it."""
    from io import StringIO

    from django.core.management import call_command

    matter = factories.MatterFactory(owner=specialist)
    _state(text=WAIT_QUARTER, row=1, matter=matter)
    plan = build_plan(snapshot_sha256=SNAPSHOT)

    out = StringIO()
    call_command(
        "register_next_action_enrichment",
        "plan",
        "--expect-snapshot-sha256",
        SNAPSHOT,
        stdout=out,
    )
    printed = out.getvalue()

    assert plan.digest in printed
    assert WAIT_QUARTER not in printed
    assert NextAction.objects.count() == 0


def test_the_apply_command_refuses_without_a_reviewed_digest(specialist):
    from django.core.management import CommandError, call_command

    matter = factories.MatterFactory(owner=specialist)
    _state(text=WAIT_QUARTER, row=1, matter=matter)

    with pytest.raises(CommandError):
        call_command(
            "register_next_action_enrichment",
            "apply",
            "--expect-snapshot-sha256",
            SNAPSHOT,
        )
    assert NextAction.objects.count() == 0
