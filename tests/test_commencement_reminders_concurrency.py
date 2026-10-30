"""Two generators, or a generator and a person, at the same moment (docs/adr/0188, brief C5–C6).

Real transactions and real PostgreSQL connections, because the guarantee is a
database guarantee: the advisory lock serialises the runs and the unique
constraint backs it. Two worker connections at most besides the test's own.
"""

from __future__ import annotations

import threading
from collections.abc import Callable
from datetime import date, timedelta
from typing import Any

import pytest
from django.db import connections

from app.intelligence.enums import EffectiveDateKind
from app.notifications import services
from app.notifications.enums import ReminderStage, ReminderStatus
from app.notifications.models import CommencementReminder
from app.workflow.enums import DatePrecision
from tests import factories

pytestmark = pytest.mark.django_db(transaction=True, serialized_rollback=True)

TODAY = date(2026, 11, 2)
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


def _commencements(count: int) -> list[Any]:
    owner = factories.UserFactory()
    return [
        factories.EffectiveDateFactory(
            matter=factories.MatterFactory(owner=owner),
            kind=EffectiveDateKind.KNOWN_DATE,
            # Inside the on-the-day prompt's window: the seven-day stage is no
            # longer generated (owner decision D1, docs/adr/0198).
            date_value=TODAY - timedelta(days=index % 7),
            period_end=TODAY - timedelta(days=index % 7),
            date_precision=DatePrecision.EXACT,
        )
        for index in range(count)
    ]


def test_two_generators_at_once_create_each_prompt_exactly_once():
    records = _commencements(6)

    results, errors = _in_parallel(
        lambda: services.reconcile_commencement_reminders(TODAY),
        lambda: services.reconcile_commencement_reminders(TODAY),
    )

    assert errors == []
    assert sorted(result.created for result in results) == [0, len(records)]
    assert CommencementReminder.objects.count() == len(records)
    assert set(CommencementReminder.objects.values_list("stage", flat=True)) == {
        ReminderStage.ON_DATE
    }


def test_an_answer_racing_the_on_the_day_run_leaves_nothing_open():
    """The person checks on the morning of the day while the generator owes the second prompt.

    Whichever commits first, the other sees it: either the answer closes both
    stages, or the generator finds the answer and owes nothing.
    """
    (record,) = _commencements(1)
    assert record.date_value is not None
    # A seven-day prompt written before owner decision D1 (docs/adr/0198).
    first = CommencementReminder.objects.create(
        effective_date=record,
        source_date=record.date_value,
        stage=ReminderStage.SEVEN_DAYS,
        due_on=record.date_value - timedelta(days=7),
    )
    owner = record.matter.owner

    _, errors = _in_parallel(
        lambda: services.reconcile_commencement_reminders(record.date_value),
        lambda: services.acknowledge_reminder(reminder=first, actor=owner),
    )

    assert errors == []
    assert not CommencementReminder.objects.filter(
        effective_date=record, status=ReminderStatus.OPEN
    ).exists()


def test_two_deadline_generators_at_once_deliver_each_interval_exactly_once():
    """docs/adr/0198 §4: the advisory lock (24606) and the unique constraint."""
    from app.notifications.deadlines import reconcile_deadline_notifications
    from app.notifications.models import DeadlineNotification

    owner = factories.UserFactory()
    for index in range(6):
        factories.MatterFactory(owner=owner, response_deadline=TODAY + timedelta(days=1 + index))

    results, errors = _in_parallel(
        lambda: reconcile_deadline_notifications(TODAY),
        lambda: reconcile_deadline_notifications(TODAY),
    )

    assert errors == []
    created = sorted(result.created for result in results)
    assert created[0] == 0
    assert DeadlineNotification.objects.count() == created[1] == 6
