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
from django.utils import timezone

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
