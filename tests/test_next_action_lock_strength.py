"""Setting a next step locks the Matter at the strength every writer uses (SVC-04).

`set_next_action` and `establish_opinion_preparation_action` took the Matter —
and the open step — at plain `FOR UPDATE`, while every other Matter writer takes
`FOR NO KEY UPDATE` (`app.matters.locks.lock_matter_for_write`). Every composer
locks the Matter at the weaker mode first and then calls `set_next_action`,
which *upgraded* the lock for the rest of the transaction. After the upgrade
every `FOR KEY SHARE` on the row waits until commit: each insert of a row that
references the Matter, and the search rebuild's own at COMMIT. That is the
ENG-027 shape — a write that then reaches the rebuild gate closes a cycle and
PostgreSQL kills one side.

Every race is forced, never hoped for: a side is held at a precise point until
`pg_stat_activity` shows the other backend blocked on a lock or finished
(`tests/test_matter_write_serialization.py`). Nothing is asserted from elapsed
time. The exclusion the lock exists for is proven alongside: two step-setters
still take turns, and a step-setter and a closure never interleave into a
closed Matter with an open step.
"""

from __future__ import annotations

import threading
import time
from collections.abc import Callable
from datetime import date, timedelta
from typing import Any

import pytest
from django.db import transaction
from django.utils import timezone

from app.audit.enums import ChangeEventType
from app.audit.models import ChangeEvent
from app.core.errors import DomainError
from app.matters.models import Entry, Matter
from app.matters.services import close_matter
from app.search.indexing import refresh_matters
from app.workflow.enums import ActionStatus, Disposition
from app.workflow.models import NextAction
from app.workflow.services import establish_opinion_preparation_action, set_next_action
from tests import factories
from tests.test_evidence_concurrency import TIMEOUT, Runner
from tests.test_matter_write_serialization import (
    _a_backend_is_waiting_on_a_lock,
    _race_against_a_rebuild,
    assert_the_index_is_sound,
)

pytestmark = pytest.mark.django_db(transaction=True, serialized_rollback=True)


def _step(matter: Matter, actor: Any, text: str = "Saadan arvamuse") -> NextAction:
    return set_next_action(
        matter=Matter.objects.get(pk=matter.pk),
        text=text,
        target_date=timezone.localdate() + timedelta(days=7),
        actor=actor,
    )


# ---------------------------------------------------------------------------
# The cost the upgrade had
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("setter", ["set_next_action", "establish_opinion_preparation_action"])
def test_setting_a_step_and_then_writing_an_indexed_row_does_not_deadlock_a_rebuild(
    specialist, monkeypatch, setter
):
    """The composer shape SVC-04 named: a step, then an indexed write, one transaction.

    The writer is held at the rebuild gate — after the step's lock, before its
    refresh — until the rebuild is blocked on the Matter row (the bug: `FOR
    UPDATE` blocks the rebuild's `FOR KEY SHARE`) or has committed (the fix).
    """
    matter = factories.MatterFactory(owner=specialist)

    def write() -> None:
        if setter == "set_next_action":
            _step(matter, specialist)
        else:
            establish_opinion_preparation_action(
                matter=Matter.objects.get(pk=matter.pk),
                prepare_by=timezone.localdate() + timedelta(days=14),
                actor=specialist,
            )
        refresh_matters(Matter.objects.filter(pk=matter.pk))

    writer_error, rebuild_error = _race_against_a_rebuild(monkeypatch, write)

    assert writer_error is None, f"the step lost: {writer_error!r}"
    assert rebuild_error is None, f"the rebuild lost: {rebuild_error!r}"
    assert NextAction.objects.filter(matter=matter, status=ActionStatus.OPEN).count() == 1
    assert_the_index_is_sound()


def test_a_row_referencing_the_matter_is_not_held_behind_a_step_being_set(specialist):
    """`FOR KEY SHARE` — any insert of a child row — no longer waits on a step.

    The step-setter holds its transaction open after `set_next_action` until the
    other backend either blocks on a lock (the old `FOR UPDATE`) or finishes. The
    inserter records whether it was ever seen waiting.
    """
    matter = factories.MatterFactory(owner=specialist)
    stepped = threading.Event()
    inserted = threading.Event()
    seen_waiting: dict[str, bool] = {}

    def set_and_hold() -> None:
        with transaction.atomic():
            _step(matter, specialist)
            stepped.set()
            deadline_hit = not _hold_until(inserted)
            seen_waiting["waited"] = _a_backend_is_waiting_on_a_lock() or deadline_hit

    def insert_child() -> None:
        assert stepped.wait(TIMEOUT)
        try:
            factories.EntryFactory(
                matter=Matter.objects.get(pk=matter.pk), body="<p>Samal ajal.</p>"
            )
        finally:
            inserted.set()

    holder = Runner(set_and_hold).start()
    inserter = Runner(insert_child).start()
    assert inserter.join() is None and holder.join() is None

    assert seen_waiting["waited"] is False, "the child insert queued behind the step's lock"
    assert Entry.objects.filter(matter=matter).count() == 1


def _hold_until(done: threading.Event) -> bool:
    """Hold until ``done``, or until another backend is seen blocked. True when done."""
    deadline = time.monotonic() + TIMEOUT
    while time.monotonic() < deadline:
        if done.is_set():
            return True
        if _a_backend_is_waiting_on_a_lock():
            return False
        time.sleep(0.02)
    return False


# ---------------------------------------------------------------------------
# What the lock is for, unchanged
# ---------------------------------------------------------------------------


def _race(*callables: Callable[[], Any]) -> list[BaseException | None]:
    start = threading.Barrier(len(callables), timeout=TIMEOUT)
    outcomes: list[BaseException | None] = [None] * len(callables)

    def wrap(index: int, call: Callable[[], Any]) -> Callable[[], None]:
        def run() -> None:
            start.wait()
            try:
                with transaction.atomic():
                    call()
            except BaseException as error:  # recorded, not swallowed
                outcomes[index] = error

        return run

    runners = [Runner(wrap(i, c)).start() for i, c in enumerate(callables)]
    assert all(r.join() is None for r in runners)
    return outcomes


def test_two_step_setters_still_take_turns(specialist, other_specialist):
    """One open step, a navigable chain, two SET events — never two open steps."""
    matter = factories.MatterFactory(owner=specialist)

    outcomes = _race(
        lambda: _step(matter, specialist, "Esimene samm"),
        lambda: _step(matter, other_specialist, "Teine samm"),
    )

    assert outcomes == [None, None]
    open_steps = NextAction.objects.filter(matter=matter, status=ActionStatus.OPEN)
    assert open_steps.count() == 1
    superseded = NextAction.objects.get(matter=matter, status=ActionStatus.SUPERSEDED)
    assert superseded.replaced_by == open_steps.get()
    assert (
        ChangeEvent.objects.filter(
            matter=matter, event_type=ChangeEventType.NEXT_ACTION_SET
        ).count()
        == 2
    )


def test_a_step_and_a_closure_never_leave_a_closed_matter_with_an_open_step(specialist):
    """Whichever commits first decides; the other sees the committed row."""
    matter = factories.MatterFactory(owner=specialist)

    outcomes = _race(
        lambda: _step(matter, specialist),
        lambda: close_matter(
            follow_ups_confirmed=True,
            matter=Matter.objects.get(pk=matter.pk),
            disposition=Disposition.COMPLETED,
            actor=specialist,
        ),
    )

    stored = Matter.objects.get(pk=matter.pk)
    assert not stored.is_open
    assert not NextAction.objects.filter(matter=matter, status=ActionStatus.OPEN).exists()
    # Either the step came first and the closure ended it, or the closure came
    # first and the step was refused on the locked, closed row.
    step_error, close_error = outcomes
    assert close_error is None
    assert step_error is None or isinstance(step_error, DomainError)


def test_a_preparation_step_is_still_established_once(specialist):
    """The idempotency `establish_opinion_preparation_action` promises, raced."""
    matter = factories.MatterFactory(owner=specialist)
    day = date.today() + timedelta(days=10)

    def establish() -> None:
        establish_opinion_preparation_action(
            matter=Matter.objects.get(pk=matter.pk), prepare_by=day, actor=specialist
        )

    assert _race(establish, establish) == [None, None]
    assert NextAction.objects.filter(matter=matter, status=ActionStatus.OPEN).count() == 1
    assert (
        ChangeEvent.objects.filter(
            matter=matter, event_type=ChangeEventType.NEXT_ACTION_SET
        ).count()
        == 1
    )
