"""`step_launch.launch` — the docs/adr/0168 §3 sequence, without a view (JARCH-001).

`add_website_overview`, `add_engagement_compact` and `add_koda_opinion` each
used to spell out «lock the step → save the record → fulfil the step» on their
own. These tests call the one helper directly, so any canonical form that uses
it gets the same guarantees:

* no step named: the record is saved and no step changes;
* a step named: the record finishes exactly that step, in the same transaction;
* a step of another operation, or one no longer ahead, is refused **before**
  the save runs — nothing is written;
* a record the step did not ask for (``fulfils`` says no) leaves the step open;
* a refused save leaves the step as it was.
"""

from __future__ import annotations

import pytest

from app.audit.enums import ChangeEventType
from app.audit.models import ChangeEvent
from app.core.errors import DomainError
from app.matters import step_launch, workspace
from app.matters.models import MatterEngagement
from app.workflow.enums import PlanStepOperation, PlanStepState
from app.workflow.models import MatterPlanStep
from app.workflow.plan import (
    STEP_NOT_OPEN,
    STEP_WRONG_OPERATION,
    plan_revision,
    plan_steps_of,
    seed_standard_plan,
    skip_plan_step,
)
from tests import factories
from tests.follow_ups import day
from tests.refusals import refused

pytestmark = pytest.mark.django_db

CONSULT = "consult-members"
SAVE_REFUSED = "Sünteetiline keeldumine salvestamisel."


@pytest.fixture
def planned(specialist):
    matter = factories.MatterFactory(owner=specialist)
    seed_standard_plan(matter=matter, actor=specialist)
    return matter


def _step(matter, key=CONSULT) -> MatterPlanStep:
    return MatterPlanStep.objects.get(matter=matter, template_step_key=key)


def _completions(step) -> int:
    return ChangeEvent.objects.filter(
        event_type=ChangeEventType.PLAN_STEP_COMPLETED, object_id=step.pk
    ).count()


def _round_save(matter, actor, calls=None):
    """`Alusta kaasamist`'s own service, counting how often it ran."""

    def save():
        if calls is not None:
            calls.append(1)
        return workspace.add_matter_engagement(
            matter=matter, author=actor, audience="Liikmed", occurred_on=day(0)
        )

    return save


def _launch(matter, actor, step_id, save, **extra):
    return step_launch.launch(
        matter=matter,
        step_id=step_id,
        operation=PlanStepOperation.ENGAGEMENT,
        actor=actor,
        save=save,
        **extra,
    )


def test_without_a_step_the_record_is_saved_and_no_step_changes(planned, specialist):
    states = {step.pk: step.state for step in plan_steps_of(planned)}

    result = _launch(planned, specialist, None, _round_save(planned, specialist))

    assert MatterEngagement.objects.filter(pk=result.record.pk, matter=planned).exists()
    assert {step.pk: step.state for step in plan_steps_of(planned)} == states


def test_the_named_step_is_finished_by_the_record(planned, specialist):
    step = _step(planned)

    result = _launch(planned, specialist, step.pk, _round_save(planned, specialist))

    step.refresh_from_db()
    assert step.state == PlanStepState.COMPLETED
    assert step.completed_by_id == specialist.pk
    assert step.fulfilled_by_operation == PlanStepOperation.ENGAGEMENT
    assert step.fulfilled_by_record == result.record.pk
    assert _completions(step) == 1


def test_a_step_of_another_operation_is_refused_before_the_save(planned, specialist):
    step = _step(planned)
    calls: list[int] = []

    with refused(STEP_WRONG_OPERATION):
        step_launch.launch(
            matter=planned,
            step_id=step.pk,
            operation=PlanStepOperation.SUBMISSION,
            actor=specialist,
            save=_round_save(planned, specialist, calls),
        )

    assert calls == []
    assert not MatterEngagement.objects.filter(matter=planned).exists()
    step.refresh_from_db()
    assert step.state != PlanStepState.COMPLETED


def test_a_step_no_longer_ahead_is_refused_before_the_save(planned, specialist):
    step = _step(planned)
    skip_plan_step(
        step=step, actor=specialist, expected_revision=plan_revision(plan_steps_of(planned))
    )
    calls: list[int] = []

    with refused(STEP_NOT_OPEN):
        _launch(planned, specialist, step.pk, _round_save(planned, specialist, calls))

    assert calls == []
    assert not MatterEngagement.objects.filter(matter=planned).exists()


def test_a_second_launch_from_the_same_step_writes_nothing(planned, specialist):
    step = _step(planned)
    _launch(planned, specialist, step.pk, _round_save(planned, specialist))
    calls: list[int] = []

    with refused(STEP_NOT_OPEN):
        _launch(planned, specialist, step.pk, _round_save(planned, specialist, calls))

    assert calls == []
    assert MatterEngagement.objects.filter(matter=planned).count() == 1
    assert _completions(step) == 1


def test_a_record_the_step_did_not_ask_for_leaves_it_open(planned, specialist):
    step = _step(planned)
    before = step.state

    result = _launch(
        planned,
        specialist,
        step.pk,
        _round_save(planned, specialist),
        fulfils=lambda record: False,
    )

    step.refresh_from_db()
    assert MatterEngagement.objects.filter(pk=result.record.pk).exists()
    assert step.state == before
    assert _completions(step) == 0


def test_a_refused_save_leaves_the_step_as_it_was(planned, specialist):
    step = _step(planned)
    before = step.state

    def refuse():
        workspace.add_matter_engagement(
            matter=planned, author=specialist, audience="Liikmed", occurred_on=day(0)
        )
        raise DomainError(SAVE_REFUSED)

    with refused(SAVE_REFUSED):
        _launch(planned, specialist, step.pk, refuse)

    step.refresh_from_db()
    assert step.state == before
    assert not MatterEngagement.objects.filter(matter=planned).exists()
