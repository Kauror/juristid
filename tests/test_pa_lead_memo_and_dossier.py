"""Lead integration fixes after the independent review (agent E), synthetic data only.

* E-1: the QA-03 late lock never waits out of order (``NOWAIT``): a held row
  refuses the closure as «changed» instead of deadlocking; a free one is taken.
* The QA-10 memo is dropped on the successor instance a closure names, so the
  same instance re-read in that request sees its new earlier stage.
"""

from __future__ import annotations

from typing import Any

import pytest

from app.matters.continuations import continuations_for, historical_predecessors
from app.matters.models import Matter
from app.matters.services import (
    CONTINUATION_CHAIN_CHANGED_REFUSAL,
    _lock_now_or_refuse,
    close_matter,
)
from app.workflow.enums import Disposition
from tests import factories
from tests.refusals import refused

pytestmark = pytest.mark.django_db


def _teema(owner: Any) -> Matter:
    return Matter.all_objects.get(pk=factories.MatterFactory(owner=owner).pk)


def test_the_successor_instance_a_closure_names_forgets_its_memoised_history(specialist):
    m = _teema(specialist)
    p = _teema(specialist)
    assert historical_predecessors(m, specialist).stages == ()
    assert continuations_for(m, specialist).predecessors == ()
    close_matter(matter=p, disposition=Disposition.SUPERSEDED, actor=specialist, successor=m)
    assert [s.matter.pk for s in historical_predecessors(m, specialist).stages] == [p.pk]
    assert [x.pk for x in continuations_for(m, specialist).predecessors] == [p.pk]


def test_a_free_late_step_is_locked_at_once(specialist):
    step = _teema(specialist)
    assert _lock_now_or_refuse(step.pk).pk == step.pk


@pytest.mark.django_db(transaction=True, serialized_rollback=True)
def test_a_late_step_somebody_holds_refuses_without_waiting(specialist):
    import threading

    from django.db import connections, transaction

    from tests.test_evidence_concurrency import TIMEOUT, Runner

    step = _teema(specialist)
    held = threading.Event()
    release = threading.Event()

    def holder() -> None:
        with transaction.atomic():
            Matter.all_objects.select_for_update(no_key=True).get(pk=step.pk)
            held.set()
            assert release.wait(TIMEOUT)

    runner = Runner(holder).start()
    try:
        assert held.wait(TIMEOUT)
        with transaction.atomic():
            with refused(CONTINUATION_CHAIN_CHANGED_REFUSAL):
                _lock_now_or_refuse(step.pk)
            # The savepoint kept the outer transaction usable.
            assert Matter.all_objects.filter(pk=step.pk).exists()
    finally:
        release.set()
        assert runner.join() is None
        connections.close_all()
