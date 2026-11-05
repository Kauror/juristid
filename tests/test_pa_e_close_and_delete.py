"""Agent E (independent verifier): QA-20 deletion and QA-03 cycle refusal.

QA-20: a deleted predecessor must no longer block deleting its successor, while
any *live* predecessor — whoever owns it, however restricted — still must.

QA-03: `close_matter` refuses a successor whose forward chain leads back, and
must not refuse (or deadlock) a legitimate closure. The cycle walk locks the
successor's forward chain in ascending order and then, for a step that changed
while it waited, *late-locks* outside that order. The last test here is the
three-transaction schedule that late lock opens to a deadlock: a legitimate,
acyclic closure and an ordinary ordered pair-lock writer. Before the change
`close_matter` locked only its ordered pair, so it could not take part in one.

Synthetic data only.
"""

from __future__ import annotations

import threading
from itertools import pairwise
from typing import Any

import pytest
from django.db import OperationalError, transaction
from django.utils import timezone

from app.core.enums import Visibility
from app.matters.deletion import (
    BLOCKED_BY_RELATED_MATTER,
    delete_matter,
    deletion_may_be_offered,
    plan_matter_deletion,
)
from app.matters.locks import lock_matters_in_order
from app.matters.models import Matter
from app.matters.services import (
    CONTINUATION_CHAIN_CHANGED_REFUSAL,
    CONTINUATION_CHAIN_TOO_LONG_REFUSAL,
    CONTINUATION_CYCLE_REFUSAL,
    CYCLE_CHECK_HOPS,
    close_matter,
)
from app.workflow.enums import Disposition
from tests import factories
from tests.refusals import refused
from tests.test_evidence_concurrency import TIMEOUT, Runner, wait_until_blocked


def _fresh(matter: Matter) -> Matter:
    return Matter.all_objects.get(pk=matter.pk)


def _close(matter: Matter, successor: Matter, actor: Any) -> Matter:
    return close_matter(
        matter=_fresh(matter),
        disposition=Disposition.SUPERSEDED,
        actor=actor,
        successor=_fresh(successor),
    )


def _continues(earlier: Matter, later: Matter) -> None:
    Matter.all_objects.filter(pk=earlier.pk).update(
        superseded_by=later,
        is_open=False,
        closed_at=timezone.now(),
        disposition=Disposition.SUPERSEDED,
    )


def _codes(matter: Matter) -> list[str]:
    return [b.code for b in plan_matter_deletion(Matter.objects.get(pk=matter.pk)).blockers]


# -- QA-20 ---------------------------------------------------------------------------


@pytest.mark.django_db
def test_e_a_live_restricted_predecessor_of_another_owner_still_blocks(specialist):
    """The blocker is unscoped: a predecessor the deleting person cannot even see
    is a live record pointing here, and must still block (no tombstone pointer)."""
    other = factories.UserFactory()
    hidden = factories.MatterFactory(owner=other, visibility=Visibility.RESTRICTED)
    successor = factories.MatterFactory(owner=specialist)
    _continues(hidden, successor)
    assert BLOCKED_BY_RELATED_MATTER in _codes(successor)
    assert deletion_may_be_offered(Matter.objects.get(pk=successor.pk)) is False


@pytest.mark.django_db
def test_e_deleted_predecessors_neither_block_nor_count(specialist):
    live = factories.MatterFactory(owner=specialist)
    gone1 = factories.MatterFactory(owner=specialist)
    gone2 = factories.MatterFactory(owner=specialist)
    successor = factories.MatterFactory(owner=specialist)
    for predecessor in (live, gone1, gone2):
        _continues(predecessor, successor)
    for predecessor in (gone1, gone2):
        Matter.all_objects.filter(pk=predecessor.pk).update(deleted_at=timezone.now())
    plan = plan_matter_deletion(Matter.objects.get(pk=successor.pk))
    blockers = [b for b in plan.blockers if b.code == BLOCKED_BY_RELATED_MATTER]
    assert len(blockers) == 1
    # Whatever the blocker says, it does not count the two tombstones.
    text = " ".join(str(value) for value in vars(blockers[0]).values())
    assert "3" not in text, text
    # Delete the live one through the real service: now the successor goes.
    _close_free = Matter.objects.get(pk=live.pk)
    delete_matter(matter=_close_free, actor=specialist)
    assert BLOCKED_BY_RELATED_MATTER not in _codes(successor)
    assert deletion_may_be_offered(Matter.objects.get(pk=successor.pk)) is True
    delete_matter(matter=Matter.objects.get(pk=successor.pk), actor=specialist)
    assert _fresh(successor).deleted_at is not None
    # The tombstones keep their edge (history), now pointing at a tombstone.
    assert {_fresh(m).superseded_by_id for m in (live, gone1, gone2)} == {successor.pk}


# -- QA-03: refusals ----------------------------------------------------------------


@pytest.mark.django_db
def test_e_a_cycle_through_a_restricted_stage_is_still_refused_and_names_nothing(specialist):
    """Unscoped on purpose: the closing person cannot read the middle stage."""
    other = factories.UserFactory()
    a = factories.MatterFactory(owner=specialist, title="PAE TSÜKKEL A")
    hidden = factories.MatterFactory(
        owner=other, visibility=Visibility.RESTRICTED, title="PAE SALATSÜKKEL"
    )
    b = factories.MatterFactory(owner=specialist, title="PAE TSÜKKEL B")
    _continues(b, hidden)
    _continues(hidden, a)
    with refused(CONTINUATION_CYCLE_REFUSAL):
        _close(a, b, specialist)
    assert "SALATSÜKKEL" not in CONTINUATION_CYCLE_REFUSAL
    assert _fresh(a).is_open and _fresh(a).superseded_by_id is None


@pytest.mark.django_db
def test_e_a_cycle_through_a_tombstone_is_refused(specialist):
    a = factories.MatterFactory(owner=specialist)
    gone = factories.MatterFactory(owner=specialist)
    b = factories.MatterFactory(owner=specialist)
    _continues(b, gone)
    _continues(gone, a)
    Matter.all_objects.filter(pk=gone.pk).update(deleted_at=timezone.now())
    with refused(CONTINUATION_CYCLE_REFUSAL):
        _close(a, b, specialist)


@pytest.mark.django_db
def test_e_a_chain_at_the_check_bound_is_refused_as_too_long_not_closed(specialist):
    stages = [factories.MatterFactory(owner=specialist) for _ in range(CYCLE_CHECK_HOPS + 2)]
    for earlier, later in pairwise(stages):
        Matter.all_objects.filter(pk=earlier.pk).update(superseded_by=later)
    closing = factories.MatterFactory(owner=specialist)
    with refused(CONTINUATION_CHAIN_TOO_LONG_REFUSAL):
        _close(closing, stages[0], specialist)
    assert _fresh(closing).is_open


@pytest.mark.django_db
def test_e_a_long_but_legitimate_chain_below_the_bound_still_closes(specialist):
    stages = [factories.MatterFactory(owner=specialist) for _ in range(40)]
    for earlier, later in pairwise(stages):
        Matter.all_objects.filter(pk=earlier.pk).update(superseded_by=later)
    closing = factories.MatterFactory(owner=specialist)
    _close(closing, stages[0], specialist)
    assert _fresh(closing).superseded_by_id == stages[0].pk


@pytest.mark.django_db
def test_e_naming_your_own_predecessor_as_successor_is_refused(specialist):
    p = factories.MatterFactory(owner=specialist)
    m = factories.MatterFactory(owner=specialist)
    _close(p, m, specialist)
    with refused(CONTINUATION_CYCLE_REFUSAL):
        _close(m, p, specialist)


# -- QA-03: concurrency -------------------------------------------------------------


def _ordered(owner: Any, n: int) -> list[Matter]:
    made = [factories.MatterFactory(owner=owner) for _ in range(n)]
    return sorted(made, key=lambda m: m.pk.int)


@pytest.mark.django_db(transaction=True, serialized_rollback=True)
def test_e_two_legitimate_closures_onto_one_chain_both_succeed(specialist):
    """p1 -> s and p2 -> s at once, s -> e existing: both lock {p, s, e}; no refusal."""
    p1, p2, s, e = (factories.MatterFactory(owner=specialist) for _ in range(4))
    _close(s, e, specialist)
    start = threading.Barrier(2)

    def closer(p: Matter) -> Any:
        def run() -> None:
            start.wait(TIMEOUT)
            _close(p, s, specialist)

        return run

    one, two = Runner(closer(p1)).start(), Runner(closer(p2)).start()
    assert one.join() is None
    assert two.join() is None
    assert {_fresh(p1).superseded_by_id, _fresh(p2).superseded_by_id} == {s.pk}


@pytest.mark.django_db(transaction=True, serialized_rollback=True)
def test_e_the_late_lock_never_deadlocks_a_legitimate_closure_with_an_ordered_writer(specialist):
    """Three transactions, ids b < d < a; no cycle anywhere.

    T1 closes a as b and holds its transaction (rows a, b locked).
    T2 closes d as a: its unlocked pre-read sees a open, so it locks {d, a} in
       order — takes d, waits for a.
    T3 is any ordered pair writer on (b, d) — `lock_matters_in_order(b, d)` —
       queued on b behind T1.
    T1 commits. T3 gets b and waits for d (T2). T2 gets a, sees the new edge
       a -> b, and late-locks b, out of the global order: it waits for T3.

    Found by agent E on a3e305a9: PostgreSQL detected a deadlock and aborted one.
    Fixed at integration (E-1): the late lock is taken with NOWAIT, so T2 never
    waits out of order. T3 holds b, so T2 cannot both finish and avoid waiting;
    it is refused as «changed» with nothing written, and T3 finishes. A retry of
    T2 afterwards reads the whole chain, locks it in order and closes.
    """
    b, d, a = _ordered(specialist, 3)
    t1_locked = threading.Event()
    release_t1 = threading.Event()

    def t1() -> None:
        with transaction.atomic():
            _close(a, b, specialist)
            t1_locked.set()
            assert release_t1.wait(TIMEOUT)

    def t2() -> None:
        assert t1_locked.wait(TIMEOUT)
        _close(d, a, specialist)

    def t3() -> None:
        with transaction.atomic():
            lock_matters_in_order(b.pk, d.pk)

    one = Runner(t1).start()
    assert t1_locked.wait(TIMEOUT)
    two = Runner(t2).start()
    wait_until_blocked(backends=1)  # T2 holds d, waits for a
    three = Runner(t3).start()
    wait_until_blocked(backends=2)  # T3 waits for b
    release_t1.set()
    errors = [one.join(), two.join(), three.join()]

    deadlocks = [e for e in errors if isinstance(e, OperationalError) and "deadlock" in str(e)]
    assert not deadlocks, f"deadlock between a legitimate closure and an ordered writer: {errors}"
    assert errors[0] is None and errors[2] is None, errors
    assert errors[1] is None or str(errors[1]) == CONTINUATION_CHAIN_CHANGED_REFUSAL, errors
    if errors[1] is not None:
        _close(d, a, specialist)  # the retry: no cycle, so it closes
    assert Matter.all_objects.get(pk=d.pk).superseded_by_id == a.pk
