"""A colleague's work committed while a confirmed closure waits for the lock is not ended.

QA-WORKFLOW-02 (docs/adr/0152 §7), with two real transactions: B holds the
Matter's lock and plans a step; A's closure, confirmed with the digest of the
warning A saw before B's step existed, queues behind it. When A gets the row,
the digest is computed again against what B committed — it no longer matches,
and the closure is refused with the renewed warning. B's step stays planned.
"""

from __future__ import annotations

import threading

import pytest
from django.db import transaction

from app.matters.closure_work import LIVE_WORK_CHANGED, live_work_at_closure
from app.matters.models import Matter
from app.matters.services import close_matter
from app.workflow.enums import ActionStatus
from app.workflow.follow_ups import FollowUpClosureUnconfirmed
from app.workflow.models import NextAction
from app.workflow.services import add_planned_action, set_next_action
from tests import factories
from tests.follow_ups import day
from tests.test_concurrency import LOCK_WAIT_TIMEOUT, run_in_thread, wait_for_a_blocked_backend

pytestmark = pytest.mark.django_db(transaction=True, serialized_rollback=True)


def test_work_committed_while_the_closure_waits_is_not_ended(specialist, other_specialist):
    matter = factories.MatterFactory(owner=specialist)
    set_next_action(matter=matter, text="Praegune samm", actor=specialist)
    digest = live_work_at_closure(matter).digest  # what A's warning showed
    holder_ready = threading.Event()
    closer_started = threading.Event()
    failures: list[BaseException] = []
    outcomes: list[str] = []

    def hold_then_plan() -> None:
        try:
            with transaction.atomic():
                Matter.objects.select_for_update().get(pk=matter.pk)
                holder_ready.set()
                closer_started.wait(timeout=LOCK_WAIT_TIMEOUT)
                wait_for_a_blocked_backend()
                add_planned_action(
                    matter=matter,
                    text="Kolleegi plaan",
                    target_date=day(7),
                    actor=other_specialist,
                )
            outcomes.append("planned")
        except BaseException as exc:
            failures.append(exc)

    def close() -> None:
        try:
            holder_ready.wait(timeout=LOCK_WAIT_TIMEOUT)
            closer_started.set()
            close_matter(
                matter=matter,
                disposition="COMPLETED",
                actor=specialist,
                follow_ups_confirmed=digest,
            )
            outcomes.append("closed")
        except FollowUpClosureUnconfirmed as refusal:
            outcomes.append("re-asked" if LIVE_WORK_CHANGED in str(refusal) else "refused")
        except BaseException as exc:
            failures.append(exc)

    threads = [run_in_thread(hold_then_plan), run_in_thread(close)]
    for thread in threads:
        thread.join(timeout=40)

    assert failures == [], failures
    assert sorted(outcomes) == ["planned", "re-asked"], outcomes
    matter.refresh_from_db()
    assert matter.is_open
    assert NextAction.objects.get(matter=matter, text="Kolleegi plaan").status == (
        ActionStatus.PLANNED
    )
