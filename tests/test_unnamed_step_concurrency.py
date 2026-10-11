"""Two people adding a step at once take turns on the Matter's lock (AUD-004).

Real threads, real transactions, two database connections and two users, both
posting to the real view on a Matter with no step. The race used to be decided
on a read taken before the lock: both requests saw «no step», both answered 200,
and one person's step ended `SUPERSEDED` by the other's — silently. The barrier
below holds both requests at the door of the Matter's lock, after anything
either could have read beforehand, so the interleaving that lost a step is the
one exercised every run.

Now the decision is made under the lock: the first save is the current step
and the second, dated, waits in the plan; undated, it is refused. Nobody's step
is superseded. Two connections only: this machine's emulated PostgreSQL will
not start many backends at once (three is the local ceiling).
"""

from __future__ import annotations

import datetime as dt
import threading

import pytest
from django.db import connections
from django.test import Client
from django.urls import reverse
from django.utils import timezone

from app.matters import locks
from app.matters.workspace import STALE_ACTION_REFUSAL
from app.workflow.enums import ActionStatus
from app.workflow.models import NextAction
from tests import factories

pytestmark = pytest.mark.django_db(transaction=True, serialized_rollback=True)


def _hold_both_at_the_lock(monkeypatch, parties: int) -> None:
    """Every thread's first Matter lock waits until all have arrived."""
    gate = threading.Barrier(parties)
    arrived = threading.local()
    real = locks.lock_matter_for_write
    real_open = locks.lock_open_matter_for_business_write

    def _wait_once() -> None:
        if not getattr(arrived, "done", False):
            arrived.done = True
            gate.wait(timeout=20)

    def lock(matter_id):
        _wait_once()
        return real(matter_id)

    def lock_open(matter_id):
        _wait_once()
        return real_open(matter_id)

    monkeypatch.setattr(locks, "lock_matter_for_write", lock)
    monkeypatch.setattr(locks, "lock_open_matter_for_business_write", lock_open)
    from app.matters import workspace

    monkeypatch.setattr(workspace, "lock_open_matter_for_business_write", lock_open)


def _race(matter, people_and_payloads, route: str) -> list[tuple[int, str]]:
    outcomes: list[tuple[int, str]] = []

    def save(user, payload) -> None:
        try:
            client = Client()
            client.force_login(user)
            response = client.post(
                reverse(f"matters:{route}", kwargs={"pk": matter.pk}),
                payload,
                headers={"HX-Request": "true"},
            )
            outcomes.append((response.status_code, response.content.decode()))
        finally:
            connections.close_all()

    threads = [threading.Thread(target=save, args=pair) for pair in people_and_payloads]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=60)
    return outcomes


def _day(offset: int) -> str:
    return (timezone.localdate() + dt.timedelta(days=offset)).strftime("%d.%m.%Y")


@pytest.mark.parametrize("route", ["set_action", "add_planned_action"])
def test_two_simultaneous_dated_saves_leave_one_current_and_one_planned(
    monkeypatch, route, specialist, department_head
):
    matter = factories.MatterFactory(owner=specialist, stage=None)
    _hold_both_at_the_lock(monkeypatch, 2)
    date_value = (timezone.localdate() + dt.timedelta(days=4)).isoformat()
    if route == "set_action":
        date_value = _day(4)

    outcomes = _race(
        matter,
        [
            (specialist, {"text": "Samm A", "target_date": date_value}),
            (department_head, {"text": "Samm B", "target_date": date_value}),
        ],
        route,
    )

    # Both saves answer 200, exactly as the race always did — but neither
    # replaced the other.
    assert [status for status, _ in outcomes] == [200, 200]
    steps = NextAction.objects.filter(matter=matter)
    assert steps.filter(status=ActionStatus.SUPERSEDED).count() == 0
    assert steps.filter(status=ActionStatus.OPEN).count() == 1
    assert steps.filter(status=ActionStatus.PLANNED).count() == 1
    assert sorted(steps.values_list("text", flat=True)) == ["Samm A", "Samm B"]


def test_two_simultaneous_undated_saves_keep_the_first_and_refuse_the_second(
    monkeypatch, specialist, department_head
):
    matter = factories.MatterFactory(owner=specialist, stage=None)
    _hold_both_at_the_lock(monkeypatch, 2)

    outcomes = _race(
        matter,
        [(specialist, {"text": "Samm A"}), (department_head, {"text": "Samm B"})],
        "set_action",
    )

    statuses = sorted(status for status, _ in outcomes)
    assert statuses[0] == 200
    assert any(STALE_ACTION_REFUSAL in body for _, body in outcomes)
    steps = NextAction.objects.filter(matter=matter)
    assert steps.count() == 1
    assert steps.get().status == ActionStatus.OPEN
