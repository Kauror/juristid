"""Two people recording the next consultation round at once (owner brief §6, D2).

Real transactions on real connections — at most three at a time, the main one
and two writers — because a request, its answer and its replacement are
serialised by the Matter's row lock and by the deadline revision, and only two
transactions racing can show that neither a second current request nor an
answer to a request that was meanwhile replaced is ever written.
"""

from __future__ import annotations

import threading
from datetime import timedelta
from typing import Any

import pytest
from django.utils import timezone

from app.core.errors import DomainError
from app.matters.enums import ResponseDeadlineChange, ResponseDeadlineOutcome
from app.matters.models import Matter, MatterResponseDeadline
from app.matters.response_deadlines import (
    ACTIVE_DEADLINE_EXISTS,
    STALE_DEADLINE_REFUSAL,
    change_response_deadline,
    deadline_revision,
    request_response_deadline,
    resolve_response_deadline,
)
from app.matters.work_items import response_obligation_of
from tests import factories
from tests.test_concurrency import LOCK_WAIT_TIMEOUT, run_in_thread

pytestmark = pytest.mark.django_db(transaction=True, serialized_rollback=True)


def _day(offset: int):
    return timezone.localdate() + timedelta(days=offset)


def _race(*calls) -> list[str]:
    """Run each call on its own connection, released together; return what each said."""
    start = threading.Barrier(len(calls), timeout=LOCK_WAIT_TIMEOUT)
    outcomes: list[str] = []
    failures: list[BaseException] = []

    def runner(call):
        def run() -> None:
            try:
                start.wait()
                call()
                outcomes.append("ok")
            except DomainError as refusal:
                outcomes.append(str(refusal))
            except BaseException as exc:  # pragma: no cover - reported below
                failures.append(exc)

        return run

    threads = [run_in_thread(runner(call)) for call in calls]
    for thread in threads:
        thread.join(timeout=40)
    assert failures == [], failures
    return outcomes


def test_two_new_requests_at_once_leave_exactly_one_current_request(specialist):
    """`+ Lisa → Arvamuse tähtaeg` pressed by two colleagues after the first round.

    Whoever takes the Matter's lock first records the new request; the other
    finds it current and is refused — never a silent overwrite of one by the
    other, and never a second request in history.
    """
    matter = factories.MatterFactory(owner=specialist)
    change_response_deadline(matter=matter, deadline=_day(-60), actor=specialist)
    resolve_response_deadline(
        matter=matter,
        outcome=ResponseDeadlineOutcome.ANSWERED,
        actor=specialist,
        note="Esimesele ringile vastati e-kirjaga.",
    )
    revision = deadline_revision(Matter.objects.get(pk=matter.pk))

    def ask(deadline) -> Any:
        return lambda: request_response_deadline(
            matter=Matter.objects.get(pk=matter.pk),
            deadline=deadline,
            actor=specialist,
            expected_revision=revision,
        )

    outcomes = _race(ask(_day(10)), ask(_day(20)))

    assert outcomes.count("ok") == 1, outcomes
    (refusal,) = [outcome for outcome in outcomes if outcome != "ok"]
    assert refusal in (
        STALE_DEADLINE_REFUSAL,
        ACTIVE_DEADLINE_EXISTS.format(date=f"{_day(10).day}.{_day(10).month}.{_day(10).year}"),
        ACTIVE_DEADLINE_EXISTS.format(date=f"{_day(20).day}.{_day(20).month}.{_day(20).year}"),
    )
    matter.refresh_from_db()
    assert matter.response_deadline in (_day(10), _day(20))
    assert matter.response_requested_at is not None
    # Only the first round is history; the loser wrote nothing.
    assert MatterResponseDeadline.objects.filter(matter=matter).count() == 1
    assert response_obligation_of(matter, specialist).is_outstanding


def test_an_answer_and_a_replacement_of_the_same_request_cannot_both_land(specialist):
    """One colleague ends the request as declined while another, from a tab drawn
    at the same moment, replaces it with the draft bill's new request.

    Both carry the same revision; the second to reach the lock finds it changed
    and is refused, so the history holds one ending of that request, not two.
    """
    matter = factories.MatterFactory(owner=specialist)
    change_response_deadline(matter=matter, deadline=_day(5), actor=specialist)
    revision = deadline_revision(Matter.objects.get(pk=matter.pk))

    def decline() -> None:
        resolve_response_deadline(
            matter=Matter.objects.get(pk=matter.pk),
            outcome=ResponseDeadlineOutcome.NOT_ANSWERING,
            actor=specialist,
            expected_revision=revision,
        )

    def replace() -> None:
        change_response_deadline(
            matter=Matter.objects.get(pk=matter.pk),
            deadline=_day(40),
            actor=specialist,
            change=ResponseDeadlineChange.REPLACED,
            previous_outcome=ResponseDeadlineOutcome.SUPERSEDED,
            expected_revision=revision,
        )

    outcomes = _race(decline, replace)

    assert sorted(outcomes) == sorted(["ok", STALE_DEADLINE_REFUSAL]), outcomes
    (ended,) = MatterResponseDeadline.objects.filter(matter=matter, deadline=_day(5))
    matter.refresh_from_db()
    if ended.outcome == ResponseDeadlineOutcome.NOT_ANSWERING:
        assert matter.response_deadline is None
    else:
        assert ended.outcome == ResponseDeadlineOutcome.SUPERSEDED
        assert matter.response_deadline == _day(40)
        assert response_obligation_of(matter, specialist).is_outstanding
