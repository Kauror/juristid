"""Concurrency regressions, run against real PostgreSQL.

These use threads and real transactions because the guarantees under test are
database guarantees. None of them wait a fixed amount of time: each blocks until
PostgreSQL itself reports the condition it is waiting for, so the tests are
deterministic rather than timing-dependent.
"""

from __future__ import annotations

import threading
from datetime import timedelta

import pytest
from django.db import connection, connections, transaction
from django.test import RequestFactory
from django.utils import timezone

from app.accounts import shared_gate
from app.accounts.models import SharedGateThrottle
from app.core.errors import DomainError
from app.matters.models import Entry, EntryRevision, Matter
from app.matters.services import add_entry, close_matter, edit_entry
from app.workflow.enums import ActionKind, ActionStatus, DateSemantics
from app.workflow.models import NextAction
from app.workflow.services import set_next_action
from tests import factories

pytestmark = pytest.mark.django_db(transaction=True)

LOCK_WAIT_TIMEOUT = 15


def wait_for_a_blocked_backend(expected: int = 1) -> bool:
    """Wait until PostgreSQL reports a backend queued on a lock.

    Used to line the two transactions up so the interleaving actually happens,
    and deliberately *not* asserted on: whether the second transaction was
    observed mid-block is a timing detail, while the invariant the test exists
    for is what the two transactions leave behind. Making the observation itself
    a pass condition is what made this flaky.
    """
    deadline = timezone.now() + timedelta(seconds=LOCK_WAIT_TIMEOUT)
    while timezone.now() < deadline:
        with connection.cursor() as cursor:
            cursor.execute(
                "SELECT count(*) FROM pg_stat_activity WHERE cardinality(pg_blocking_pids(pid)) > 0"
            )
            if cursor.fetchone()[0] >= expected:
                return True
    return False


def run_in_thread(target) -> threading.Thread:
    def wrapped() -> None:
        try:
            target()
        finally:
            connections.close_all()

    thread = threading.Thread(target=wrapped)
    thread.start()
    return thread


# ---------------------------------------------------------------------------
# A closed Matter can never keep an open instruction
# ---------------------------------------------------------------------------


def test_closing_while_setting_an_action_cannot_leave_both(specialist):
    """The Matter row is the boundary both operations serialise on.

    Whichever transaction reaches it first wins: a closure that lands first
    makes the other call refuse, and a next action that lands first is cancelled
    by the closure. Neither ordering can end with a closed Matter that still
    carries an open `Järgmiseks`.
    """
    matter = factories.MatterFactory(owner=specialist)
    holder_ready = threading.Event()
    closer_started = threading.Event()
    failures: list[BaseException] = []
    outcomes: list[str] = []

    def hold_then_set() -> None:
        """Take the Matter lock, let the closer queue behind it, then commit."""
        try:
            with transaction.atomic():
                Matter.objects.select_for_update().get(pk=matter.pk)
                holder_ready.set()
                closer_started.wait(timeout=LOCK_WAIT_TIMEOUT)
                wait_for_a_blocked_backend()
                set_next_action(
                    matter=matter,
                    text="Tegevus võidujooksus",
                    kind=ActionKind.WAIT,
                    date_semantics=DateSemantics.REVIEW_ON,
                    actor=specialist,
                )
            outcomes.append("action-set")
        except DomainError:
            outcomes.append("action-refused")
        except BaseException as exc:
            failures.append(exc)

    def close() -> None:
        try:
            holder_ready.wait(timeout=LOCK_WAIT_TIMEOUT)
            closer_started.set()
            close_matter(matter=matter, disposition="COMPLETED", actor=specialist)
            outcomes.append("closed")
        except DomainError:
            outcomes.append("close-refused")
        except BaseException as exc:
            failures.append(exc)

    threads = [run_in_thread(hold_then_set), run_in_thread(close)]
    for thread in threads:
        thread.join(timeout=40)

    assert failures == [], failures

    matter.refresh_from_db()
    open_actions = NextAction.objects.filter(matter=matter, status=ActionStatus.OPEN)

    # The invariant, whichever way the race resolved. If the Matter row were
    # not the boundary, both transactions could commit and leave a closed
    # Matter carrying a live instruction — which is exactly what this asserts
    # cannot happen.
    assert not (matter.is_open is False and open_actions.exists()), outcomes
    if not matter.is_open:
        assert open_actions.count() == 0

    # Both operations resolved, and they resolved consistently: the closure
    # always lands, and the action either landed before it or was refused after.
    assert "closed" in outcomes, outcomes
    assert {"action-set", "action-refused"} & set(outcomes), outcomes


def test_a_closure_that_lands_first_makes_the_later_action_refuse(specialist):
    """The ordering the lock is there to produce, verified end to end."""
    matter = factories.MatterFactory(owner=specialist)
    close_matter(matter=matter, disposition="COMPLETED", actor=specialist)

    with pytest.raises(DomainError):
        set_next_action(
            matter=matter,
            text="Liiga hilja",
            kind=ActionKind.WAIT,
            actor=specialist,
        )
    assert NextAction.objects.filter(matter=matter, status=ActionStatus.OPEN).count() == 0


# ---------------------------------------------------------------------------
# Simultaneous entry edits keep every superseded wording
# ---------------------------------------------------------------------------


def test_two_simultaneous_edits_keep_both_revisions(specialist):
    """Neither edit may be lost, and neither revision number may collide.

    The second writer waits for the first, then edits whatever is current by
    then — which is the honest outcome for a record two people touched at once.
    """
    matter = factories.MatterFactory(owner=specialist)
    entry = add_entry(matter=matter, body="<p>Algne sõnastus</p>", author=specialist)

    holder_ready = threading.Event()
    second_started = threading.Event()
    failures: list[BaseException] = []

    def hold_then_edit() -> None:
        try:
            with transaction.atomic():
                Entry.objects.select_for_update().get(pk=entry.pk)
                holder_ready.set()
                second_started.wait(timeout=LOCK_WAIT_TIMEOUT)
                wait_for_a_blocked_backend()
                edit_entry(entry=entry, body="<p>Esimene muudatus</p>", actor=specialist)
        except BaseException as exc:
            failures.append(exc)

    def second_edit() -> None:
        try:
            holder_ready.wait(timeout=LOCK_WAIT_TIMEOUT)
            second_started.set()
            edit_entry(entry=entry, body="<p>Teine muudatus</p>", actor=specialist)
        except BaseException as exc:
            failures.append(exc)

    threads = [run_in_thread(hold_then_edit), run_in_thread(second_edit)]
    for thread in threads:
        thread.join(timeout=40)

    assert failures == [], failures

    entry.refresh_from_db()
    revisions = list(EntryRevision.objects.filter(entry=entry).order_by("revision_number"))

    assert entry.edit_count == 2
    assert [revision.revision_number for revision in revisions] == [1, 2]

    # Nothing was silently overwritten: the original wording and the first edit
    # both survive as revisions, and the entry now holds the second.
    preserved = " ".join(revision.body for revision in revisions)
    assert "Algne sõnastus" in preserved
    assert "Esimene muudatus" in preserved
    assert "Teine muudatus" in entry.body


def test_sequential_edits_number_revisions_without_gaps(specialist):
    matter = factories.MatterFactory(owner=specialist)
    entry = add_entry(matter=matter, body="<p>Versioon 0</p>", author=specialist)

    for index in range(1, 4):
        edit_entry(entry=entry, body=f"<p>Versioon {index}</p>", actor=specialist)

    entry.refresh_from_db()
    numbers = list(
        EntryRevision.objects.filter(entry=entry)
        .order_by("revision_number")
        .values_list("revision_number", flat=True)
    )
    assert numbers == [1, 2, 3]
    assert entry.edit_count == 3


# ---------------------------------------------------------------------------
# The shared gate counts every failed attempt, however they arrive
# ---------------------------------------------------------------------------
#
# SEC-01. `register_failure` read the counter into Python, incremented it and
# saved it back, with nothing making two writers take turns. Attempts fired in
# parallel therefore recorded **one** failure and no lockout at all: each of
# them read `failures = 0` and each of them wrote `1`.
#
# That is not a slow counter, it is an absent control. The escalating lockout is
# the only thing standing between a shared password and unlimited guessing, and
# an attacker who opens twenty connections instead of sending twenty requests in
# a row never reaches the threshold that arms it.
#
# The fix serialises the writers on the one row they are all counting, which is
# also the boundary that keeps the control per-client: a lockout for one client
# key must never be reachable by, or block, another (Stage-2D auth brief 9).

#: The threshold these tests run against, and how many attempts arrive at once.
#:
#: Deliberately small. PostgreSQL serves each connection from its own process,
#: so a test that opens eight of them measures the host's process limits as much
#: as the application's locking, and on an emulated x64 build it will eventually
#: measure them the hard way. Three overlapping attempts prove the property
#: exactly as well as eight: what matters is that they overlap at all.
GATE_ATTEMPTS = 2
GATE_PARALLEL_FAILURES = 3

THROTTLE_TABLE = "accounts_sharedgatethrottle"


def gate_request(address: str):
    """A request the throttle can derive a client key from, and nothing more."""
    return RequestFactory().post("/konto/varav/", HTTP_CF_CONNECTING_IP=address)


@pytest.fixture
def gate_limits(settings):
    settings.SHARED_GATE_MAX_ATTEMPTS = GATE_ATTEMPTS
    settings.SHARED_GATE_LOCKOUT_SECONDS = 300
    settings.SHARED_GATE_MAX_LOCKOUT_SECONDS = 3600
    return settings


def wait_for_a_queued_throttle_writer() -> bool:
    """Wait until somebody is queued behind a lock on the throttle table.

    A synchronisation aid, not an assertion: it lines the two transactions up so
    the interleaving under test actually happens. It deliberately does **not**
    distinguish the defect from the fix — both queue here, and which one is
    running is decided by what they read, not by whether they wait.

    Narrower than :func:`wait_for_a_blocked_backend` because the development
    cluster is shared with other suites, and a backend blocked while holding a
    lock on this one relation cannot be somebody else's Matter write.

    Both halves of the condition are needed, and the reason is how PostgreSQL
    waits for a row: ``SELECT … FOR UPDATE`` takes a granted relation-level lock
    first and then waits on the *holder's* ``transactionid``, so the waiter never
    appears as an ungranted lock on the table. What identifies it is the pair —
    blocked, and holding a lock on this relation.
    """
    deadline = timezone.now() + timedelta(seconds=LOCK_WAIT_TIMEOUT)
    while timezone.now() < deadline:
        with connection.cursor() as cursor:
            cursor.execute(
                "SELECT count(*) FROM pg_stat_activity a "
                "WHERE cardinality(pg_blocking_pids(a.pid)) > 0 AND EXISTS ("
                "  SELECT 1 FROM pg_locks l JOIN pg_class c ON c.oid = l.relation"
                "  WHERE l.pid = a.pid AND c.relname = %s)",
                [THROTTLE_TABLE],
            )
            if cursor.fetchone()[0] > 0:
                return True
    return False


#: What the concurrent writer parks in `failures` before it commits. Any value
#: the attempt cannot have arrived at by itself will do; three is simply legible
#: in a failure message.
CONCURRENT_COUNT = 3


def test_a_failed_attempt_counts_on_top_of_a_concurrent_write_not_over_it(gate_limits):
    """The deterministic statement of the fix: the **read** happens under the lock.

    Blocking is not the property. The unfixed code blocked too — its `SELECT`
    passed straight through under MVCC, it read a stale counter, and only then
    did its `UPDATE` queue behind the holder. It waited and *still* lost the
    count, which is why "did it appear in pg_locks" cannot tell the two apart.

    What tells them apart is what the attempt counts on top of. A writer parks a
    known value in the row and holds it; the attempt starts while that lock is
    held. Fixed, the attempt waits, re-reads, and lands on `CONCURRENT_COUNT + 1`.
    Unfixed, it wrote the `1` it computed from a row it had read before the
    holder ever committed, and the holder's write is gone.

    A barrier alone would not prove this. Connections released together stagger
    enough on a slow host to take turns by luck, and a test that only sometimes
    reproduces a lost update is not a regression guard.
    """
    # A threshold this test cannot reach, so the assertion is about the counter
    # rather than about a lockout resetting it.
    gate_limits.SHARED_GATE_MAX_ATTEMPTS = 10

    request = gate_request("203.0.113.11")
    key = shared_gate.client_key(request)
    SharedGateThrottle.objects.create(client_key=key)

    held = threading.Event()
    attempted = threading.Event()
    failures: list[BaseException] = []

    def hold_and_write() -> None:
        """Take the row, let the attempt queue behind it, then commit a value."""
        try:
            with transaction.atomic():
                row = SharedGateThrottle.objects.select_for_update().get(client_key=key)
                held.set()
                wait_for_a_queued_throttle_writer()
                row.failures = CONCURRENT_COUNT
                row.save(update_fields=["failures", "updated_at"])
        except BaseException as exc:  # pragma: no cover - reported by the assert
            failures.append(exc)

    def one_failure() -> None:
        try:
            held.wait(timeout=LOCK_WAIT_TIMEOUT)
            shared_gate.record_failure(request)
        except BaseException as exc:  # pragma: no cover - reported by the assert
            failures.append(exc)
        finally:
            attempted.set()

    threads = [run_in_thread(hold_and_write), run_in_thread(one_failure)]
    for thread in threads:
        thread.join(timeout=40)

    assert failures == [], failures
    assert attempted.is_set()

    record = SharedGateThrottle.objects.get(client_key=key)
    assert record.failures == CONCURRENT_COUNT + 1, (
        f"the attempt recorded {record.failures} rather than {CONCURRENT_COUNT + 1}: "
        "it counted from a row it read before the concurrent write committed, so "
        "that write was lost and failures can be lost the same way"
    )


def test_parallel_wrong_passwords_are_every_one_of_them_counted(gate_limits):
    """The behaviour that follows, stated as arithmetic.

    Released from a barrier so the attempts overlap. The result is deterministic
    *because* they serialise: three increments with a reset at the second leave
    one, one completed lockout cycle, and exactly one attempt that earned a wait.
    """
    request = gate_request("203.0.113.12")
    key = shared_gate.client_key(request)
    start = threading.Barrier(GATE_PARALLEL_FAILURES, timeout=LOCK_WAIT_TIMEOUT)
    waits: list[int] = []
    failures: list[BaseException] = []
    lock = threading.Lock()

    def one_failure() -> None:
        try:
            start.wait()
            seconds = shared_gate.record_failure(request)
            with lock:
                waits.append(seconds)
        except BaseException as exc:  # pragma: no cover - reported by the assert
            failures.append(exc)

    threads = [run_in_thread(one_failure) for _ in range(GATE_PARALLEL_FAILURES)]
    for thread in threads:
        thread.join(timeout=40)

    assert failures == [], failures

    record = SharedGateThrottle.objects.get(client_key=key)
    assert len(waits) == GATE_PARALLEL_FAILURES
    # The attempts up to the threshold armed the lockout and reset the counter;
    # the rest were counted after it.
    assert record.failures == GATE_PARALLEL_FAILURES - GATE_ATTEMPTS
    assert record.lockout_cycles == 1
    assert record.locked_until is not None
    # One attempt, and only one, is told to wait: the one that crossed the
    # threshold. Before the fix every attempt returned 0.
    assert sorted(waits) == [0] * (GATE_PARALLEL_FAILURES - 1) + [300]


def test_a_lockout_cannot_be_outrun_by_firing_attempts_in_parallel(gate_limits):
    """The product invariant, stated without arithmetic.

    Exactly `max_attempts` wrong passwords lock the client out. Whether they
    arrive one after another or all at once is the attacker's choice, and it
    must not be the difference between a lockout and none.
    """
    request = gate_request("203.0.113.13")
    start = threading.Barrier(GATE_ATTEMPTS, timeout=LOCK_WAIT_TIMEOUT)
    failures: list[BaseException] = []

    def one_failure() -> None:
        try:
            start.wait()
            shared_gate.record_failure(request)
        except BaseException as exc:  # pragma: no cover - reported by the assert
            failures.append(exc)

    threads = [run_in_thread(one_failure) for _ in range(GATE_ATTEMPTS)]
    for thread in threads:
        thread.join(timeout=40)

    assert failures == [], failures
    assert shared_gate.lockout_seconds_remaining(request) > 0


def test_one_clients_lockout_does_not_stand_in_another_clients_way(gate_limits):
    """Per client, still. The row is the boundary; the table is not.

    A table-wide or global lock would turn this control into the
    denial-of-service primitive the design explicitly refuses: one attacker
    could stall every other client's authentication. So this holds one client's
    row open and requires that a different client's attempt completes anyway.
    If it could not, the second thread would still be waiting at the join.
    """
    held = threading.Event()
    other_finished = threading.Event()
    failures: list[BaseException] = []

    first = gate_request("203.0.113.14")
    second = gate_request("198.51.100.14")
    first_key = shared_gate.client_key(first)
    second_key = shared_gate.client_key(second)
    SharedGateThrottle.objects.create(client_key=first_key)

    def hold_the_first_clients_row() -> None:
        try:
            with transaction.atomic():
                SharedGateThrottle.objects.select_for_update().get(client_key=first_key)
                held.set()
                # Released only once the other client is through, so the whole
                # of that client's attempt happens under this lock.
                other_finished.wait(timeout=LOCK_WAIT_TIMEOUT)
        except BaseException as exc:  # pragma: no cover - reported by the assert
            failures.append(exc)

    def the_other_client_tries() -> None:
        try:
            held.wait(timeout=LOCK_WAIT_TIMEOUT)
            shared_gate.record_failure(second)
        except BaseException as exc:  # pragma: no cover - reported by the assert
            failures.append(exc)
        finally:
            other_finished.set()

    threads = [
        run_in_thread(hold_the_first_clients_row),
        run_in_thread(the_other_client_tries),
    ]
    for thread in threads:
        thread.join(timeout=40)

    assert failures == [], failures
    assert other_finished.is_set(), "the second client never got through the first client's lock"
    assert SharedGateThrottle.objects.get(client_key=second_key).failures == 1
    # And the held client was not charged for somebody else's attempt.
    assert SharedGateThrottle.objects.get(client_key=first_key).failures == 0


def test_a_first_failure_from_two_connections_at_once_raises_nothing(gate_limits):
    """No row exists yet, so both attempts race to create it.

    The insert is where a careless fix breaks: two transactions both find
    nothing, both try to create, and one meets the unique constraint. Neither
    attempt may be lost, and neither may surface as an error to a person who
    simply mistyped a password.
    """
    request = gate_request("203.0.113.15")
    start = threading.Barrier(GATE_ATTEMPTS, timeout=LOCK_WAIT_TIMEOUT)
    failures: list[BaseException] = []

    def one_failure() -> None:
        try:
            start.wait()
            shared_gate.record_failure(request)
        except BaseException as exc:  # pragma: no cover - reported by the assert
            failures.append(exc)

    threads = [run_in_thread(one_failure) for _ in range(GATE_ATTEMPTS)]
    for thread in threads:
        thread.join(timeout=40)

    assert failures == [], failures
    record = SharedGateThrottle.objects.get(client_key=shared_gate.client_key(request))
    # Both attempts landed: two is the threshold, so the counter reset and a
    # cycle was recorded rather than the count simply standing at two.
    assert record.lockout_cycles == 1


def test_a_row_deleted_by_a_successful_sign_in_does_not_break_the_attempt(gate_limits):
    """The window the locking read opened, closed by fault injection.

    `record_failure` now reads the row twice: once to create it if absent, once
    to hold it. Between those two statements a *correct* password from the same
    client can delete it — that is `record_success` doing exactly what it exists
    for — and the locking read would then find nothing.

    The window is a few microseconds wide and cannot be hit reliably by racing
    threads, so it is produced directly: the create is made to delete the row
    behind itself, once, which is precisely the interleaving. What must not
    happen is an exception on the sign-in page for somebody who mistyped.
    """
    request = gate_request("203.0.113.16")
    key = shared_gate.client_key(request)
    original = SharedGateThrottle.objects.get_or_create
    struck: list[int] = []

    def delete_it_behind_us(**kwargs):
        result = original(**kwargs)
        if not struck:
            struck.append(1)
            SharedGateThrottle.objects.filter(client_key=key).delete()
        return result

    SharedGateThrottle.objects.get_or_create = delete_it_behind_us
    try:
        wait = shared_gate.record_failure(request)
    finally:
        SharedGateThrottle.objects.get_or_create = original

    assert struck == [1], "the injected delete never ran; the test proved nothing"
    assert wait == 0
    # The attempt was counted on a fresh row rather than lost or raised.
    assert SharedGateThrottle.objects.get(client_key=key).failures == 1
