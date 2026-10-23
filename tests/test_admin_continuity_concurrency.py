"""Two administrators removing administrators at the same moment (docs/adr/0186).

Real connections, so a real transaction per thread: three at most (the local
cluster's limit; CI's PostgreSQL is the authority). Each change is held between
reading the count and writing its own change, so without the administration
lock both would read "three" and both would succeed, leaving one usable full
administrator. With it they take turns, and the second sees the first's result.
"""

from __future__ import annotations

import threading
import time
from collections.abc import Callable
from typing import Any

import pytest
from django.db import connections

from app.accounts import admin_session, administration
from tests.admin_session import apply_admin_gate, full_admin
from tests.local_auth import person

pytestmark = pytest.mark.django_db(transaction=True, serialized_rollback=True)

THREADS = 2  # plus the test's own connection: three
TIMEOUT = 30


def run_in_thread(target: Callable[[], None]) -> threading.Thread:
    def wrapped() -> None:
        try:
            target()
        finally:
            connections.close_all()

    thread = threading.Thread(target=wrapped)
    thread.start()
    return thread


@pytest.fixture
def slow_count(monkeypatch) -> None:
    """Widen the window between the count before and the write, deterministically."""
    real = administration.usable_full_administrators

    def slow() -> list[Any]:
        result = real()
        time.sleep(0.3)
        return result

    monkeypatch.setattr(administration, "usable_full_administrators", slow)


def _race(*work: Callable[[], None]) -> tuple[list[str], list[BaseException]]:
    start = threading.Barrier(len(work), timeout=TIMEOUT)
    outcomes: list[str] = []
    failures: list[BaseException] = []
    lock = threading.Lock()

    def wrap(job: Callable[[], None]) -> Callable[[], None]:
        def run() -> None:
            try:
                start.wait()
                job()
                with lock:
                    outcomes.append("done")
            except administration.AdministrationRefused as error:
                with lock:
                    outcomes.append(str(error))
            except BaseException as error:  # pragma: no cover - reported by the assert
                with lock:
                    failures.append(error)

        return run

    threads = [run_in_thread(wrap(job)) for job in work]
    for thread in threads:
        thread.join(timeout=TIMEOUT * 2)
    assert not any(thread.is_alive() for thread in threads), "a change never finished"
    return outcomes, failures


def test_two_concurrent_removals_cannot_leave_fewer_than_two(settings, slow_count):
    apply_admin_gate(settings)
    x, _ = full_admin()
    y, _ = full_admin()
    z, _ = full_admin()
    # The actors are delegating administrators who are not themselves usable
    # (no authenticator), so only the continuity rule can stop the second one.
    p, _ = person(manage=True, delegate=True)
    q, _ = person(manage=True, delegate=True)
    del z

    outcomes, failures = _race(
        lambda: administration.deactivate_account(
            actor=admin_session._mint(p, via=admin_session.PATH), user=x
        ),
        lambda: administration.deactivate_account(
            actor=admin_session._mint(q, via=admin_session.PATH), user=y
        ),
    )

    assert failures == [], failures
    assert sorted(outcomes) == sorted(["done", administration.CONTINUITY])
    assert len(administration.usable_full_administrators()) == 2


def test_removing_each_other_at_once_leaves_both_rules_holding(settings, slow_count):
    """A removes C while B removes A: one wins; the other is stale or refused."""
    apply_admin_gate(settings)
    a, _ = full_admin()
    b, _ = full_admin()
    c, _ = full_admin()

    outcomes, failures = _race(
        lambda: administration.deactivate_account(
            actor=admin_session._mint(a, via=admin_session.PATH), user=c
        ),
        lambda: administration.deactivate_account(
            actor=admin_session._mint(b, via=admin_session.PATH), user=a
        ),
    )

    assert failures == [], failures
    assert outcomes.count("done") == 1
    refusal = next(outcome for outcome in outcomes if outcome != "done")
    assert refusal in (administration.CONTINUITY, administration.STALE_PROOF)
    assert len(administration.usable_full_administrators()) == 2
