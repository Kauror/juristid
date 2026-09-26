"""Two people naming one new institution at the same moment get one row (ENG-045).

Look-then-insert without a lock: both transactions looked, both found nothing,
both inserted — two rows, after which the name was refused as ambiguous for
everybody until an operator merged them. The create decision is now taken under
a transaction-scoped advisory lock keyed by the organisation key and checked
again once the lock is held.

These run against real PostgreSQL with two real transactions in two threads,
three connections in all (the test's own and one per thread).

**How the interleaving is forced, without a sleep.** A barrier alone lines the
two threads up at the start, and then whichever is faster usually finishes
before the other has looked — so the race would be real and the test would
pass anyway. Instead every lookup waits, once it has read, until the other
thread has made the same lookup *or* is blocked on a lock this thread holds.
Without the lock both threads read "nothing" before either writes, which is the
race exactly. With it the second thread queues on the lock, the first is
released by PostgreSQL itself reporting the wait, and the second reads the
first one's row. Neither outcome depends on timing.
"""

from __future__ import annotations

import threading
import time
from collections.abc import Callable
from typing import Any

import pytest
from django.db import connection, connections, transaction
from django.test import Client
from django.urls import reverse

from app.organisations import services
from app.organisations.models import Organisation, OrganisationType
from tests import factories

# Real transactions; `serialized_rollback=True` is the suite's isolation
# contract for them (tests/reference_baseline.py).
pytestmark = pytest.mark.django_db(transaction=True, serialized_rollback=True)

WAIT_SECONDS = 15


def someone_waits_on_me() -> bool:
    """Whether a backend in this database is queued on a lock this one holds."""
    with connection.cursor() as cursor:
        cursor.execute(
            "SELECT EXISTS (SELECT 1 FROM pg_stat_activity "
            "WHERE datname = current_database() "
            "AND pg_backend_pid() = ANY(pg_blocking_pids(pid)))"
        )
        return bool(cursor.fetchone()[0])


class Lockstep:
    """`find_matches`, made to wait after reading until the race is set up."""

    def __init__(self, real: Callable[[str], Any]) -> None:
        self.real = real
        self.guard = threading.Lock()
        self.calls: dict[int, int] = {}
        self.finished: set[int] = set()

    def register(self) -> None:
        with self.guard:
            self.calls[threading.get_ident()] = 0

    def finish(self) -> None:
        with self.guard:
            self.finished.add(threading.get_ident())

    def __call__(self, name: str) -> Any:
        result = self.real(name)
        me = threading.get_ident()
        with self.guard:
            self.calls[me] = self.calls.get(me, 0) + 1
            mine = self.calls[me]
        deadline = time.monotonic() + WAIT_SECONDS
        while time.monotonic() < deadline:
            with self.guard:
                others = {ident: count for ident, count in self.calls.items() if ident != me}
                caught_up = all(
                    count >= mine or ident in self.finished for ident, count in others.items()
                )
            if caught_up or someone_waits_on_me():
                break
            time.sleep(0.005)
        return result


def run_together(lockstep: Lockstep, *targets: Callable[[], Any]) -> list[Any]:
    """Each target in its own thread and its own transaction; results in order."""
    barrier = threading.Barrier(len(targets))
    results: list[Any] = [None] * len(targets)
    errors: list[BaseException] = []

    def wrapped(index: int, target: Callable[[], Any]) -> Callable[[], None]:
        def run() -> None:
            lockstep.register()
            try:
                barrier.wait(timeout=WAIT_SECONDS)
                results[index] = target()
            except BaseException as error:  # reported after both threads join
                errors.append(error)
            finally:
                lockstep.finish()
                connections.close_all()

        return run

    threads = [
        threading.Thread(target=wrapped(index, target)) for index, target in enumerate(targets)
    ]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=WAIT_SECONDS * 3)
    assert not errors, errors
    return results


@pytest.fixture
def lockstep(monkeypatch) -> Lockstep:
    stepped = Lockstep(services.find_matches)
    monkeypatch.setattr(services, "find_matches", stepped)
    return stepped


def resolve_in_a_transaction(name: str) -> Callable[[], Any]:
    def target() -> Any:
        with transaction.atomic():
            organisation = services.resolve_organisation_name(name=name)
        return organisation.pk if organisation is not None else None

    return target


@pytest.mark.parametrize(
    ("first", "second"),
    [
        ("Riigikogu uus erikomisjon", "Riigikogu uus erikomisjon"),
        ("Riigikogu uus erikomisjon", "riigikogu  uus\u200b erikomisjon"),
        ("Riigikogu uus\u00ad erikomisjon", "\ufeffRiigikogu uus erikomisjon"),
    ],
    ids=["same-spelling", "zero-width-space", "soft-hyphen-and-bom"],
)
def test_two_simultaneous_resolutions_of_one_new_name_leave_one_institution(
    lockstep, first, second
):
    first_pk, second_pk = run_together(
        lockstep, resolve_in_a_transaction(first), resolve_in_a_transaction(second)
    )

    assert Organisation.objects.count() == 1
    assert first_pk == second_pk == Organisation.objects.get().pk


def test_two_simultaneous_quick_creates_leave_one_institution(lockstep):
    """Through the panel itself: both clicks answered, neither with a 500."""
    person = factories.UserFactory()
    clients = [Client(), Client()]
    for client in clients:
        client.force_login(person)

    def click(client: Client) -> Callable[[], Any]:
        def target() -> Any:
            return client.post(
                reverse("organisations:quick_create"),
                {"name": "Näidis Sihtasutus", "target": "source_organisations"},
            )

        return target

    responses = run_together(lockstep, click(clients[0]), click(clients[1]))

    assert [response.status_code for response in responses] == [200, 200]
    assert Organisation.objects.count() == 1
    reused = ["oli juba olemas" in response.content.decode() for response in responses]
    assert sorted(reused) == [False, True]


def test_an_existing_name_is_reused_by_both_without_waiting(lockstep):
    """The ordinary case takes no lock at all: both read the one row and go."""
    existing = Organisation.objects.create(
        name="Näidisamet", organisation_type=OrganisationType.AUTHORITY
    )

    pks = run_together(
        lockstep,
        resolve_in_a_transaction("Näidisamet"),
        resolve_in_a_transaction("näidis\u200bamet"),
    )

    assert pks == [existing.pk, existing.pk]
    assert Organisation.objects.count() == 1
