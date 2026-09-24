"""Workflow transitions are decided on the current row (ENG-072, ENG-075).

**ENG-072.** `complete_next_action`, `cancel_next_action` and
`acknowledge_review` checked `status` on the instance the view had fetched —
before the transaction, without a lock. Racing another transition on the same
action, the late one still saw OPEN, so:

* complete vs replace ended COMPLETED with `replaced_by` set;
* complete vs closure wrote NEXT_ACTION_COMPLETED after MATTER_CLOSED;
* complete twice wrote two completion events;
* review vs replace wrote NEXT_ACTION_REVIEWED on a SUPERSEDED action.

**ENG-075.** `assign_matter` took no Matter lock: it retired notices and then
saved, the reverse of `delete_matter`'s order, so it could deadlock with a
deletion or commit an owner, a live notice and a MATTER_ASSIGNED event onto a
tombstone. Its audit `from` was the owner the request started with.

Each race is forced: the first transaction does its whole write and then stays
open until `pg_stat_activity` shows the second one blocked on a lock. The second
was handed a copy of the row taken before the first began — exactly what a view
holds — so whatever it decides, it decides after the first has committed.
"""

from __future__ import annotations

import threading
from collections.abc import Callable
from typing import Any

import pytest
from django.db import transaction

from app.audit.enums import ChangeEventType
from app.audit.models import ChangeEvent
from app.core.errors import DomainError
from app.matters.deletion import delete_matter
from app.matters.models import Matter, MatterAssignmentNotice
from app.matters.services import ASSIGNMENT_ON_DELETED_MATTER, assign_matter, close_matter
from app.workflow.enums import ActionKind, ActionStatus, DateSemantics, Disposition
from app.workflow.models import NextAction
from app.workflow.services import (
    acknowledge_review,
    cancel_next_action,
    complete_next_action,
    set_next_action,
)
from tests import factories
from tests.test_evidence_concurrency import TIMEOUT, Runner, wait_until_blocked

pytestmark = pytest.mark.django_db(transaction=True, serialized_rollback=True)

TERMINAL_EVENTS = {
    ChangeEventType.NEXT_ACTION_COMPLETED,
    ChangeEventType.NEXT_ACTION_CANCELLED,
}


def _first_then_second(
    first: Callable[[], Any], second: Callable[[], Any]
) -> tuple[BaseException | None, BaseException | None]:
    """``first`` commits only once ``second`` is observed waiting on a lock."""
    wrote = threading.Event()
    release = threading.Event()

    def run_first() -> None:
        with transaction.atomic():
            first()
            wrote.set()
            assert release.wait(TIMEOUT)

    def run_second() -> None:
        assert wrote.wait(TIMEOUT)
        second()

    one = Runner(run_first).start()
    two = Runner(run_second).start()
    try:
        wait_until_blocked(backends=1)
    finally:
        release.set()
    return one.join(), two.join()


def _step(matter: Matter, actor: Any, *, text: str = "Koosta vastus", kind: str = ActionKind.DO):
    return set_next_action(
        matter=matter,
        text=text,
        kind=kind,
        date_semantics=DateSemantics.DEADLINE if kind == ActionKind.DO else DateSemantics.REVIEW_ON,
        target_date=None,
        actor=actor,
    )


def _stale(action: NextAction) -> NextAction:
    """What a view holds: the row as it was when the request began."""
    return NextAction.objects.get(pk=action.pk)


def _events_about(action: NextAction) -> list[str]:
    return list(
        ChangeEvent.objects.filter(object_id=action.pk)
        .order_by("occurred_at", "id")
        .values_list("event_type", flat=True)
    )


def assert_the_workflow_is_consistent(matter: Matter) -> None:
    """The invariants a race must not be able to break."""
    assert NextAction.objects.filter(matter=matter, status=ActionStatus.OPEN).count() <= 1
    for action in NextAction.objects.filter(matter=matter):
        if action.replaced_by_id is not None:
            assert action.status == ActionStatus.SUPERSEDED, (
                f"{action.status} action carries replaced_by"
            )
        terminal = [event for event in _events_about(action) if event in TERMINAL_EVENTS]
        assert len(terminal) <= 1, f"two terminal events on one action: {terminal}"


# ---------------------------------------------------------------------------
# ENG-072 — terminal transitions
# ---------------------------------------------------------------------------


def test_complete_against_replace_cannot_leave_a_completed_superseded_action(specialist):
    matter = factories.MatterFactory(owner=specialist)
    action = _step(matter, specialist)
    held = _stale(action)

    replace_error, complete_error = _first_then_second(
        lambda: _step(Matter.objects.get(pk=matter.pk), specialist, text="Uus samm"),
        lambda: complete_next_action(action=held, actor=specialist),
    )

    assert replace_error is None
    assert isinstance(complete_error, DomainError)
    assert NextAction.objects.get(pk=action.pk).status == ActionStatus.SUPERSEDED
    assert ChangeEventType.NEXT_ACTION_COMPLETED not in _events_about(action)
    assert_the_workflow_is_consistent(matter)


def test_complete_against_closure_cannot_complete_after_the_matter_closed(specialist):
    matter = factories.MatterFactory(owner=specialist)
    action = _step(matter, specialist)
    held = _stale(action)

    close_error, complete_error = _first_then_second(
        lambda: close_matter(
            matter=Matter.objects.get(pk=matter.pk),
            disposition=Disposition.COMPLETED,
            actor=specialist,
        ),
        lambda: complete_next_action(action=held, actor=specialist),
    )

    assert close_error is None
    assert isinstance(complete_error, DomainError)
    assert NextAction.objects.get(pk=action.pk).status == ActionStatus.CANCELLED
    events = list(
        ChangeEvent.objects.filter(matter=matter)
        .order_by("occurred_at", "id")
        .values_list("event_type", flat=True)
    )
    assert ChangeEventType.NEXT_ACTION_COMPLETED not in events
    assert events[-1] == ChangeEventType.MATTER_CLOSED
    assert_the_workflow_is_consistent(matter)


def test_completing_twice_records_one_completion(specialist):
    matter = factories.MatterFactory(owner=specialist)
    action = _step(matter, specialist)
    first_copy, second_copy = _stale(action), _stale(action)

    first_error, second_error = _first_then_second(
        lambda: complete_next_action(action=first_copy, actor=specialist),
        lambda: complete_next_action(action=second_copy, actor=specialist),
    )

    assert first_error is None
    assert isinstance(second_error, DomainError)
    assert _events_about(action).count(ChangeEventType.NEXT_ACTION_COMPLETED) == 1
    assert_the_workflow_is_consistent(matter)


def test_a_review_cannot_be_recorded_on_a_superseded_action(specialist):
    matter = factories.MatterFactory(owner=specialist)
    action = _step(matter, specialist, text="Jälgin menetlust", kind=ActionKind.MONITOR)
    held = _stale(action)

    replace_error, review_error = _first_then_second(
        lambda: _step(Matter.objects.get(pk=matter.pk), specialist, text="Uus samm"),
        lambda: acknowledge_review(action=held, actor=specialist),
    )

    assert replace_error is None
    assert isinstance(review_error, DomainError)
    assert NextAction.objects.get(pk=action.pk).status == ActionStatus.SUPERSEDED
    assert ChangeEventType.NEXT_ACTION_REVIEWED not in _events_about(action)
    assert_the_workflow_is_consistent(matter)


def test_cancel_against_complete_leaves_one_terminal_state(specialist):
    matter = factories.MatterFactory(owner=specialist)
    action = _step(matter, specialist)
    for_cancel, for_complete = _stale(action), _stale(action)

    cancel_error, complete_error = _first_then_second(
        lambda: cancel_next_action(action=for_cancel, actor=specialist, reason="Pole vaja"),
        lambda: complete_next_action(action=for_complete, actor=specialist),
    )

    assert cancel_error is None
    assert isinstance(complete_error, DomainError)
    assert NextAction.objects.get(pk=action.pk).status == ActionStatus.CANCELLED
    assert_the_workflow_is_consistent(matter)


def test_an_uncontested_transition_still_works(specialist):
    matter = factories.MatterFactory(owner=specialist)
    action = _step(matter, specialist)
    done = complete_next_action(action=_stale(action), actor=specialist)
    assert done.status == ActionStatus.COMPLETED
    assert _events_about(action).count(ChangeEventType.NEXT_ACTION_COMPLETED) == 1


# ---------------------------------------------------------------------------
# ENG-075 — owner assignment
# ---------------------------------------------------------------------------


def _live_notices(matter: Matter) -> int:
    return MatterAssignmentNotice.objects.filter(
        matter_id=matter.pk, viewed_at__isnull=True, superseded_at__isnull=True
    ).count()


def test_an_assignment_after_a_deletion_is_refused_and_writes_nothing(specialist, department_head):
    matter = factories.MatterFactory(owner=None)
    held = Matter.objects.get(pk=matter.pk)

    delete_error, assign_error = _first_then_second(
        lambda: delete_matter(matter=Matter.objects.get(pk=matter.pk), actor=specialist),
        lambda: assign_matter(matter=held, owner=department_head, actor=specialist),
    )

    assert delete_error is None
    assert isinstance(assign_error, DomainError)
    assert str(assign_error) == ASSIGNMENT_ON_DELETED_MATTER
    tombstone = Matter.all_objects.get(pk=matter.pk)
    assert tombstone.deleted_at is not None
    assert tombstone.owner_id is None
    assert _live_notices(tombstone) == 0
    events = list(
        ChangeEvent.objects.filter(matter=tombstone)
        .order_by("occurred_at", "id")
        .values_list("event_type", flat=True)
    )
    assert (
        ChangeEventType.MATTER_ASSIGNED
        not in events[events.index(ChangeEventType.MATTER_DELETED) :]
    )


def test_a_deletion_after_an_assignment_takes_the_notice_with_it(specialist, department_head):
    """The other order: the assignment commits first, the deletion then waits for
    it on the Matter row instead of deadlocking over the notices."""
    matter = factories.MatterFactory(owner=specialist)

    assign_error, delete_error = _first_then_second(
        lambda: assign_matter(
            matter=Matter.objects.get(pk=matter.pk), owner=department_head, actor=specialist
        ),
        lambda: delete_matter(matter=Matter.objects.get(pk=matter.pk), actor=specialist),
    )

    assert assign_error is None and delete_error is None
    tombstone = Matter.all_objects.get(pk=matter.pk)
    assert tombstone.deleted_at is not None
    assert _live_notices(tombstone) == 0


def test_two_assignments_take_turns_and_the_audit_chain_is_true(
    specialist, other_specialist, department_head
):
    matter = factories.MatterFactory(owner=specialist)
    held = Matter.objects.get(pk=matter.pk)  # still says: owner = specialist

    first_error, second_error = _first_then_second(
        lambda: assign_matter(
            matter=Matter.objects.get(pk=matter.pk), owner=other_specialist, actor=specialist
        ),
        lambda: assign_matter(matter=held, owner=department_head, actor=specialist),
    )

    assert first_error is None and second_error is None
    assert Matter.objects.get(pk=matter.pk).owner == department_head
    chain = [
        (event.payload["from_name"], event.payload["to_name"])
        for event in ChangeEvent.objects.filter(
            matter=matter, event_type=ChangeEventType.MATTER_ASSIGNED
        ).order_by("occurred_at", "id")
    ]
    assert chain[-2:] == [
        (specialist.display_name, other_specialist.display_name),
        (other_specialist.display_name, department_head.display_name),
    ]
    assert _live_notices(matter) == 1


def test_a_closed_matter_still_changes_hands(specialist, department_head):
    """Correcting who owned a finished file stays possible; only a deleted one
    is refused."""
    matter = factories.MatterFactory(owner=specialist)
    close_matter(matter=matter, disposition=Disposition.COMPLETED, actor=specialist)

    assign_matter(matter=Matter.objects.get(pk=matter.pk), owner=department_head, actor=specialist)

    assert Matter.objects.get(pk=matter.pk).owner == department_head
