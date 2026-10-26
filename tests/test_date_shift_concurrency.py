"""Two people pressing «Nihuta kuupäeva» on one step at the same moment.

Real transactions and real connections (docs/adr/0143, amendment of 2026-10-26):
the Matter's row lock serialises the two saves, the second finds the step it
named superseded and is refused — one move, one new row, one audit event.
"""

from __future__ import annotations

import threading
from collections.abc import Callable
from datetime import date
from typing import Any

import pytest
from django.db import connections

from app.audit.enums import ChangeEventType
from app.audit.models import ChangeEvent
from app.core.errors import DomainError
from app.matters import date_shift
from app.workflow.enums import ActionKind, ActionStatus, DateSemantics
from app.workflow.models import NextAction
from app.workflow.services import add_planned_action, set_next_action
from tests import factories

pytestmark = pytest.mark.django_db(transaction=True, serialized_rollback=True)

TIMEOUT = 30


def _in_parallel(*targets: Callable[[], Any]) -> tuple[list[Any], list[BaseException]]:
    start = threading.Barrier(len(targets), timeout=TIMEOUT)
    results: list[Any] = []
    errors: list[BaseException] = []
    lock = threading.Lock()

    def run(target: Callable[[], Any]) -> None:
        try:
            start.wait()
            value = target()
            with lock:
                results.append(value)
        except BaseException as exc:  # pragma: no cover - reported below
            with lock:
                errors.append(exc)
        finally:
            connections.close_all()

    threads = [threading.Thread(target=run, args=(target,)) for target in targets]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=60)
    assert not any(thread.is_alive() for thread in threads), "a connection never finished"
    return results, errors


@pytest.mark.parametrize("status", ["current", "planned"])
@pytest.mark.parametrize(("first", "second"), [("nadal", "nadal"), ("paev", "kuu")])
def test_two_simultaneous_moves_of_one_step_move_it_once(first, second, status):
    owner = factories.UserFactory()
    colleague = factories.DepartmentHeadFactory()
    matter = factories.MatterFactory(owner=owner)
    action = set_next_action(
        matter=matter,
        text="Saadan arvamuse",
        kind=ActionKind.DO,
        date_semantics=DateSemantics.DEADLINE,
        target_date=date(2026, 11, 5),
        actor=owner,
    )
    if status == "planned":
        action = add_planned_action(
            matter=matter, text="Hiljem", target_date=date(2026, 11, 5), actor=owner
        )
    live_status = ActionStatus.OPEN if status == "current" else ActionStatus.PLANNED
    before = ChangeEvent.objects.filter(event_type=ChangeEventType.NEXT_ACTION_SET).count()

    def move(key: str, actor: Any) -> Callable[[], Any]:
        return lambda: date_shift.shift_action_date(
            matter=matter,
            action_id=action.pk,
            actor=actor,
            shift=key,
            source_date=date(2026, 11, 5),
        )

    results, errors = _in_parallel(move(first, owner), move(second, colleague))

    assert len(results) == 1
    assert len(errors) == 1 and isinstance(errors[0], DomainError)
    assert str(errors[0]) == date_shift.SHIFT_STALE
    live = NextAction.objects.filter(matter=matter, status=live_status).exclude(
        text="Saadan arvamuse" if status == "planned" else ""
    )
    assert live.count() == 1
    assert live.get().target_date == results[0].new_date
    after = ChangeEvent.objects.filter(event_type=ChangeEventType.NEXT_ACTION_SET).count()
    assert after == before + 1
    action.refresh_from_db()
    assert action.status == ActionStatus.SUPERSEDED
