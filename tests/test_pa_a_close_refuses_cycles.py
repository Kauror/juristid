"""A closure may not make the continuation history lead back to itself (QA-03, docs/adr/0207).

`Matter.superseded_by` is single-valued, so a Matter's forward history is one
path. The database refuses only a self-reference; `close_matter` refused only
that too. Naming as `Järglane` a Matter whose own continuation already leads
back — directly or through further stages — to the Matter being closed made a
cycle that every reader then saw as «Ajalugu jõuab tagasi juba näidatud
teemani».

`close_matter` is the only runtime writer of `superseded_by` (the workspace's
«Lõpeta teema» never names a successor; `reopen_matter` only clears one). It now
walks forward from the proposed successor under row locks and refuses when the
walk reaches the closing Matter. A refused closure writes nothing.

**Lock order.** The forward chain is read once without locks, then the closing
Matter, the successor and that chain are locked together in the global
ascending-id order (`lock_matters_in_order`), and the chain is re-walked on the
locked rows; a step that changed in between is locked as it is reached. Two
closures that would together close a cycle therefore share at least one locked
row, take turns, and the second re-reads the first's committed edge.
"""

from __future__ import annotations

import threading
import time
from typing import Any

import pytest
from django.db import connection, transaction

from app.audit.enums import ChangeEventType
from app.audit.models import ChangeEvent
from app.core.errors import DomainError
from app.matters.models import Matter
from app.matters.services import CONTINUATION_CYCLE_REFUSAL, close_matter
from app.workflow.enums import Disposition
from tests import factories
from tests.refusals import refused
from tests.test_evidence_concurrency import TIMEOUT, Runner


def _fresh(matter: Matter) -> Matter:
    return Matter.all_objects.get(pk=matter.pk)


def _close(matter: Matter, successor: Matter, actor: Any) -> Matter:
    return close_matter(
        matter=_fresh(matter),
        disposition=Disposition.SUPERSEDED,
        actor=actor,
        successor=_fresh(successor),
    )


def _state(matter: Matter) -> tuple[Any, ...]:
    row = _fresh(matter)
    closures = ChangeEvent.objects.filter(
        matter_id=matter.pk, event_type=ChangeEventType.MATTER_CLOSED
    ).count()
    return (
        row.is_open,
        row.disposition,
        row.disposition_reason,
        row.closed_at,
        row.closed_by_id,
        row.superseded_by_id,
        row.updated_at,
        closures,
        ChangeEvent.objects.filter(matter_id=matter.pk).count(),
    )


def _teemad(owner: Any, n: int) -> list[Matter]:
    return [factories.MatterFactory(owner=owner) for _ in range(n)]


@pytest.mark.django_db
def test_a_matter_cannot_continue_as_itself(specialist):
    (a,) = _teemad(specialist, 1)
    before = _state(a)
    with refused("Teema ei saa jätkuda iseenda all."):
        _close(a, a, specialist)
    assert _state(a) == before


@pytest.mark.django_db
def test_a_two_stage_cycle_is_refused_and_writes_nothing(specialist):
    a, b = _teemad(specialist, 2)
    _close(b, a, specialist)  # b continues as a
    before = _state(a)

    with refused(CONTINUATION_CYCLE_REFUSAL):
        _close(a, b, specialist)  # a would continue as b: a -> b -> a

    assert _state(a) == before
    assert _fresh(a).is_open and _fresh(a).superseded_by_id is None


@pytest.mark.django_db
def test_a_multi_stage_cycle_is_refused(specialist):
    m1, m2, m3, m4, m5 = _teemad(specialist, 5)
    _close(m1, m2, specialist)
    _close(m2, m3, specialist)
    _close(m3, m4, specialist)
    _close(m4, m5, specialist)
    before = _state(m5)

    for start in (m1, m2, m3, m4):
        with refused(CONTINUATION_CYCLE_REFUSAL):
            _close(m5, start, specialist)
    assert _state(m5) == before


@pytest.mark.django_db
def test_a_linear_chain_is_still_allowed(specialist):
    m1, m2, m3, m4 = _teemad(specialist, 4)
    _close(m1, m2, specialist)
    _close(m2, m3, specialist)
    _close(m3, m4, specialist)
    assert [_fresh(m).superseded_by_id for m in (m1, m2, m3)] == [m2.pk, m3.pk, m4.pk]
    assert _fresh(m4).is_open


@pytest.mark.django_db
def test_converging_predecessors_are_still_allowed(specialist):
    p1, p2, p3, target, later = _teemad(specialist, 5)
    for predecessor in (p1, p2, p3):
        _close(predecessor, target, specialist)
    _close(target, later, specialist)
    assert {_fresh(p).superseded_by_id for p in (p1, p2, p3)} == {target.pk}
    assert _fresh(target).superseded_by_id == later.pk


@pytest.mark.django_db
def test_a_loop_elsewhere_in_the_data_does_not_block_an_unrelated_closure(specialist):
    """A cycle the import refused and somebody wrote by hand is not this closure's to refuse."""
    a, b, c, closing = _teemad(specialist, 4)
    # c continues into the two-stage loop a <-> b, which does not pass through `closing`.
    Matter.all_objects.filter(pk=a.pk).update(superseded_by=b)
    Matter.all_objects.filter(pk=b.pk).update(superseded_by=a)
    Matter.all_objects.filter(pk=c.pk).update(superseded_by=a)
    _close(closing, c, specialist)
    assert _fresh(closing).superseded_by_id == c.pk


# ---------------------------------------------------------------------------
# Two closures that would together close a cycle take turns
# ---------------------------------------------------------------------------


def _waiting_or_done(done: threading.Event) -> bool:
    deadline = time.monotonic() + TIMEOUT
    while time.monotonic() < deadline:
        if done.is_set():
            return False
        with connection.cursor() as cursor:
            cursor.execute("SELECT pg_stat_clear_snapshot()")
            cursor.execute(
                """
                SELECT count(*) FROM pg_stat_activity
                 WHERE datname = current_database()
                   AND wait_event_type = 'Lock'
                   AND pid <> pg_backend_pid()
                """
            )
            if cursor.fetchone()[0] > 0:
                return True
        time.sleep(0.02)
    raise AssertionError("the second closure neither waited nor finished")


@pytest.mark.django_db(transaction=True, serialized_rollback=True)
def test_two_closures_with_no_common_pair_cannot_together_close_a_cycle(specialist):
    """a -> b and c -> d exist. T1 closes b as c while T2 closes d as a.

    Each closure's own pair ({b, c} and {a, d}) is disjoint from the other's, so
    locking only the pair let both pass and commit a -> b -> c -> d -> a. With the
    forward chain locked too, T2 waits for T1 and then sees b -> c -> d.
    """
    a, b, c, d = _teemad(specialist, 4)
    _close(a, b, specialist)
    _close(c, d, specialist)

    first_wrote = threading.Event()
    release = threading.Event()
    second_done = threading.Event()

    def first() -> None:
        with transaction.atomic():
            _close(b, c, specialist)
            first_wrote.set()
            assert release.wait(TIMEOUT)

    def second() -> None:
        try:
            assert first_wrote.wait(TIMEOUT)
            _close(d, a, specialist)
        finally:
            second_done.set()

    one = Runner(first).start()
    two = Runner(second).start()
    try:
        assert first_wrote.wait(TIMEOUT)
        second_waited = _waiting_or_done(second_done)
    finally:
        release.set()
    first_error, second_error = one.join(), two.join()

    assert first_error is None
    assert second_waited, "the second closure did not wait for the first"
    assert isinstance(second_error, DomainError)
    assert str(second_error) == CONTINUATION_CYCLE_REFUSAL
    assert _fresh(d).is_open and _fresh(d).superseded_by_id is None
    assert _fresh(b).superseded_by_id == c.pk
