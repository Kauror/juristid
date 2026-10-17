"""Two `Uus teema` tabs pressed together file one Teema (JQA-006, docs/adr/0166 §2).

The «filed once» net reads committed rows. Each form's token claim locks only
that form's own row, so two forms with different tokens and the same title,
saved at the same moment, could each look, each find nothing the other had
committed yet, and each file a Teema with its own reference number. The check
now runs under a transaction-scoped advisory lock keyed by the person and the
normalised title (`duplicate_intake.hold_title`).

These run against real PostgreSQL with two real requests in two threads, three
connections in all (the test's own and one per thread).

**How the interleaving is forced, without a sleep** — the pattern of
`tests/test_organisation_identity_concurrency.py`. Every duplicate check waits,
once it has read, until the other request has made the same check *or* is
blocked on a lock this request holds (`pg_blocking_pids`). Without the lock
both requests read "nothing" before either commits, which is the race exactly.
With it the second request queues on the lock, the first is released by
PostgreSQL itself reporting the wait, and the second reads the first one's
Teema. Neither outcome depends on timing.
"""

from __future__ import annotations

import re
import threading
import time
from collections.abc import Callable
from typing import Any

import pytest
from django.db import connection, connections
from django.test import Client
from django.urls import reverse

from app.matters import duplicate_intake
from app.matters.models import Matter

# Real transactions; `serialized_rollback=True` is the suite's isolation
# contract for them (tests/reference_baseline.py).
pytestmark = pytest.mark.django_db(transaction=True, serialized_rollback=True)

CREATE = reverse("matters:matter_create")

#: The give-up bound only. Nothing is asserted from elapsed time.
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
    """`recent_duplicate`, made to wait after reading until the race is set up."""

    def __init__(self, real: Callable[..., Any]) -> None:
        self.real = real
        self.guard = threading.Lock()
        self.calls: dict[int, int] = {}
        self.finished: set[int] = set()
        self.released_by_lock_wait = 0

    def register(self) -> None:
        with self.guard:
            self.calls[threading.get_ident()] = 0

    def finish(self) -> None:
        with self.guard:
            self.finished.add(threading.get_ident())

    def __call__(self, **kwargs: Any) -> Any:
        result = self.real(**kwargs)
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
            if caught_up:
                break
            if someone_waits_on_me():
                with self.guard:
                    self.released_by_lock_wait += 1
                break
            # A polling interval, not a synchronisation primitive: the loop
            # exits on observed state, never on elapsed time.
            time.sleep(0.005)
        return result


@pytest.fixture
def lockstep(monkeypatch) -> Lockstep:
    stepped = Lockstep(duplicate_intake.recent_duplicate)
    monkeypatch.setattr(duplicate_intake, "recent_duplicate", stepped)
    return stepped


def _client(user: Any) -> Client:
    client = Client()
    client.force_login(user)
    return client


def _token(client: Client) -> str:
    page = client.get(CREATE).content.decode()
    found = re.search(r'name="intake" value="([^"]+)"', page)
    assert found
    return found.group(1)


def press_together(lockstep: Lockstep, *presses: Callable[[], Any]) -> list[Any]:
    """Each press in its own thread, released together; responses in order."""
    barrier = threading.Barrier(len(presses))
    results: list[Any] = [None] * len(presses)
    errors: list[BaseException] = []

    def wrapped(index: int, press: Callable[[], Any]) -> Callable[[], None]:
        def run() -> None:
            lockstep.register()
            try:
                barrier.wait(timeout=WAIT_SECONDS)
                results[index] = press()
            except BaseException as error:  # reported after both threads join
                errors.append(error)
            finally:
                lockstep.finish()
                connections.close_all()

        return run

    threads = [threading.Thread(target=wrapped(i, press)) for i, press in enumerate(presses)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=WAIT_SECONDS * 3)
    assert not any(thread.is_alive() for thread in threads), "a request never finished"
    assert not errors, errors
    return results


def _press(user: Any, title: str, token: str) -> Callable[[], Any]:
    def press() -> Any:
        return _client(user).post(CREATE, {"title": title, "owner": str(user.pk), "intake": token})

    return press


def test_two_tabs_pressed_together_file_one_teema_and_the_second_is_asked(specialist, lockstep):
    """The second tab waits for the first, then finds its Teema and asks."""
    title = "Kaks kaarti korraga"
    tabs = [_client(specialist), _client(specialist)]
    tokens = [_token(tabs[0]), _token(tabs[1])]
    assert tokens[0] != tokens[1]

    responses = press_together(
        lockstep,
        _press(specialist, title, tokens[0]),
        # Case and spacing do not make the title different.
        _press(specialist, "  kaks KAARTI  korraga ", tokens[1]),
    )

    created = [
        matter
        for matter in Matter.objects.filter(title__icontains="kaarti")
        if duplicate_intake.normalised_title(matter.title) == "kaks kaarti korraga"
    ]
    assert len(created) == 1, "two tabs pressed together filed the same Teema twice"
    assert sorted(response.status_code for response in responses) == [302, 400]
    (asked,) = [response for response in responses if response.status_code == 400]
    body = asked.content.decode()
    assert duplicate_intake.RECENT_DUPLICATE.rstrip(".") in body
    assert reverse("matters:matter_detail", kwargs={"pk": created[0].pk}) in body
    assert f'name="{duplicate_intake.CONFIRM_FIELD}"' in body
    # The interleaving was forced by the lock wait, not by one request
    # happening to finish first.
    assert lockstep.released_by_lock_wait >= 1


def test_the_asked_tab_still_files_a_deliberate_second_teema(specialist, lockstep):
    """«Loo siiski uus» after the race: the refused token files exactly once."""
    title = "Kaks kaarti teadlikult"
    tokens = [_token(_client(specialist)), _token(_client(specialist))]
    responses = press_together(
        lockstep, _press(specialist, title, tokens[0]), _press(specialist, title, tokens[1])
    )
    statuses = [response.status_code for response in responses]
    assert sorted(statuses) == [302, 400]
    asked = statuses.index(400)

    confirmed = _client(specialist).post(
        CREATE,
        {
            "title": title,
            "owner": str(specialist.pk),
            "intake": tokens[asked],
            duplicate_intake.CONFIRM_FIELD: "1",
        },
    )

    assert confirmed.status_code == 302
    created = list(Matter.objects.filter(title=title))
    assert len(created) == 2
    assert len({matter.reference_number for matter in created}) == 2


def test_colleagues_pressing_the_same_title_together_do_not_wait_for_each_other(
    specialist, other_specialist, lockstep
):
    """The lock is per person: no global title uniqueness (0166 §2)."""
    title = "Ühine pealkiri korraga"
    responses = press_together(
        lockstep,
        _press(specialist, title, _token(_client(specialist))),
        _press(other_specialist, title, _token(_client(other_specialist))),
    )

    assert [response.status_code for response in responses] == [302, 302]
    assert Matter.objects.filter(title=title).count() == 2
    assert lockstep.released_by_lock_wait == 0
