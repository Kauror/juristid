"""Brute-force attempts take turns: at most the limit is ever verified (ENG-070).

Two doors, one defect. The shared gate read its lockout without a lock, checked
the password, and only then counted the failure under the row lock; the test
stack's dev-login PIN did the same with a read-then-write counter in Django's
database cache. Attempts fired together therefore all read "not locked" and all
had their credential checked before the one that armed the lockout was counted.
A burst of parallel connections got max(limit, parallelism) guesses per window.

The property under test is the one an attacker cares about: **how many
credentials get checked**. Every test counts the verifications themselves by
wrapping the function that performs them, rather than inferring them from
counters or statuses.

The two ``..._waits_for_the_one_being_verified`` tests are the deterministic
statements of the fix. They hold one attempt *inside* its verification and let a
second attempt from the same client start. Fixed, the second queues behind the
first one's lock and then finds the lockout it armed; unfixed, it reads the old
counter and has its own credential checked. Neither outcome depends on how the
threads happen to be scheduled. The burst tests state the resulting arithmetic.

Real transactions and real PostgreSQL connections, because the guarantees are
database guarantees. Three connections at most besides the test's own:
PostgreSQL serves each from its own process, and three overlap exactly as well
as eight (tests/test_concurrency.py says why more is a hazard, not a proof).
"""

from __future__ import annotations

import threading
import time
from collections import Counter
from collections.abc import Callable
from datetime import timedelta
from typing import Any

import pytest
from django.core.cache import cache
from django.core.management import call_command
from django.db import connection, connections
from django.test import Client, RequestFactory
from django.urls import reverse
from django.utils import timezone

from app.accounts import shared_gate, views
from app.accounts.models import SharedGateThrottle
from app.audit.enums import SecurityEventType
from app.audit.models import SecurityAuditEvent
from tests import factories
from tests.gate import PASSWORD, apply_shared_gate

pytestmark = pytest.mark.django_db(transaction=True, serialized_rollback=True)

LOCK_WAIT_TIMEOUT = 15

#: The limit both doors ship with, stated rather than inherited.
LIMIT = 5

#: A burst: three connections, each firing three wrong credentials back to
#: back, released together. Nine attempts against a limit of five.
CONNECTIONS = 3
ATTEMPTS_PER_CONNECTION = 3

PIN = "4821"

#: The deployments' cache backend, under a table name no deployment uses.
CACHE_TABLE = "test_authentication_attempt_cache"


# -- harness ----------------------------------------------------------------


class CountingCheck:
    """Stands in for a credential check: counts every call, can hold the first.

    Holding the first call open is how the tests put one attempt *inside* its
    verification for as long as they need, without sleeping and without
    guessing how long a hash takes.
    """

    def __init__(self, real: Callable[[str], bool]) -> None:
        self.real = real
        self.hold_first = False
        self.calls = 0
        self.first_started = threading.Event()
        self.first_finished = threading.Event()
        self.later_started = threading.Event()
        self.release = threading.Event()
        self._lock = threading.Lock()

    def __call__(self, supplied: str) -> bool:
        with self._lock:
            self.calls += 1
            first = self.calls == 1
        if not first:
            self.later_started.set()
            return self.real(supplied)
        self.first_started.set()
        try:
            if self.hold_first:
                self.release.wait(timeout=LOCK_WAIT_TIMEOUT)
            return self.real(supplied)
        finally:
            self.first_finished.set()


def run_in_thread(target: Callable[[], None]) -> threading.Thread:
    def wrapped() -> None:
        try:
            target()
        finally:
            connections.close_all()

    thread = threading.Thread(target=wrapped)
    thread.start()
    return thread


def a_backend_here_is_waiting_on_a_lock() -> bool:
    """Whether a connection to this test database is queued behind a lock.

    Scoped to this database, because a development cluster is shared with other
    suites and their blocked backends are not ours.
    """
    with connection.cursor() as cursor:
        cursor.execute(
            "SELECT count(*) FROM pg_stat_activity "
            "WHERE datname = current_database() AND cardinality(pg_blocking_pids(pid)) > 0"
        )
        return cursor.fetchone()[0] > 0


def wait_until(condition: Callable[[], bool]) -> bool:
    """A synchronisation aid, never an assertion: it only lines attempts up."""
    deadline = time.monotonic() + LOCK_WAIT_TIMEOUT
    while time.monotonic() < deadline:
        if condition():
            return True
        time.sleep(0.01)
    return False


def hold_one_and_start_another(
    check: CountingCheck, first: Callable[[], Any], second: Callable[[], Any]
) -> tuple[dict[str, Any], list[BaseException]]:
    """Hold `first` inside its credential check while `second` begins.

    Released once the second attempt is either queued on a lock (the fix) or
    checking its own credential (the defect). Which of the two happened is left
    to the caller's assertions about what the attempts left behind.
    """
    check.hold_first = True
    results: dict[str, Any] = {}
    errors: list[BaseException] = []

    def run_first() -> None:
        try:
            results["first"] = first()
        except BaseException as exc:  # pragma: no cover - reported by the caller
            errors.append(exc)
        finally:
            check.release.set()

    def run_second() -> None:
        try:
            if not check.first_started.wait(timeout=LOCK_WAIT_TIMEOUT):
                raise AssertionError("the first attempt never reached its credential check")
            results["second"] = second()
        except BaseException as exc:  # pragma: no cover - reported by the caller
            errors.append(exc)

    threads = [run_in_thread(run_first), run_in_thread(run_second)]
    check.first_started.wait(timeout=LOCK_WAIT_TIMEOUT)
    wait_until(lambda: check.later_started.is_set() or a_backend_here_is_waiting_on_a_lock())
    check.release.set()
    for thread in threads:
        thread.join(timeout=40)
    assert not any(thread.is_alive() for thread in threads), "an attempt never finished"
    return results, errors


def burst(post: Callable[[], Any]) -> tuple[list[int], list[BaseException]]:
    """Nine attempts from three connections released together."""
    start = threading.Barrier(CONNECTIONS, timeout=LOCK_WAIT_TIMEOUT)
    statuses: list[int] = []
    errors: list[BaseException] = []
    lock = threading.Lock()

    def one_connection() -> None:
        try:
            start.wait()
            for _ in range(ATTEMPTS_PER_CONNECTION):
                status = post().status_code
                with lock:
                    statuses.append(status)
        except BaseException as exc:  # pragma: no cover - reported by the caller
            errors.append(exc)

    threads = [run_in_thread(one_connection) for _ in range(CONNECTIONS)]
    for thread in threads:
        thread.join(timeout=60)
    assert not any(thread.is_alive() for thread in threads), "a connection never finished"
    return statuses, errors


def refusal_reasons() -> Counter[str]:
    return Counter(
        event.detail.get("reason")
        for event in SecurityAuditEvent.objects.filter(
            event_type=SecurityEventType.AUTHENTICATION_FAILED
        )
    )


# -- the shared gate ----------------------------------------------------------


@pytest.fixture
def password_check(settings, monkeypatch) -> CountingCheck:
    apply_shared_gate(settings, PASSWORD)
    settings.SHARED_GATE_MAX_ATTEMPTS = LIMIT
    check = CountingCheck(shared_gate.verify_password)
    monkeypatch.setattr(shared_gate, "verify_password", check)
    return check


def gate_post(address: str, password: str) -> Any:
    return Client().post(
        reverse("accounts:shared_gate"), {"password": password}, HTTP_CF_CONNECTING_IP=address
    )


def gate_key(address: str) -> str:
    return shared_gate.client_key(RequestFactory().post("/", HTTP_CF_CONNECTING_IP=address))


def test_a_gate_attempt_waits_for_the_one_being_verified(password_check):
    """The deterministic statement of the shared-gate fix.

    One wrong password left before the lockout. The first attempt is held
    inside its password check; the second starts meanwhile. The first one's
    failure arms the lockout, so the second must be refused *without its
    password being checked* — which it can only be if it waited for the first.
    """
    address = "203.0.113.70"
    SharedGateThrottle.objects.create(client_key=gate_key(address), failures=LIMIT - 1)

    results, errors = hold_one_and_start_another(
        password_check,
        lambda: gate_post(address, "vale-esimene").status_code,
        lambda: gate_post(address, "vale-teine").status_code,
    )

    assert errors == [], errors
    assert password_check.calls == 1, (
        f"{password_check.calls} passwords were checked: the second attempt read the lockout "
        "before the first attempt's failure was counted, and was verified past the limit"
    )
    # The first attempt is the limit-th: verified, counted, and it armed the
    # lockout. The second found it armed.
    assert results == {"first": 429, "second": 429}
    assert refusal_reasons() == Counter({"bad_password": 1, "locked_out": 1})
    record = SharedGateThrottle.objects.get(client_key=gate_key(address))
    assert record.lockout_cycles == 1
    assert record.failures == 0


def test_a_burst_of_wrong_passwords_gets_exactly_the_limit_checked(password_check):
    address = "203.0.113.71"

    statuses, errors = burst(lambda: gate_post(address, "vale"))

    assert errors == [], errors
    total = CONNECTIONS * ATTEMPTS_PER_CONNECTION
    assert password_check.calls == LIMIT, (
        f"{password_check.calls} of {total} parallel wrong passwords were checked "
        f"against a limit of {LIMIT}"
    )
    # Every refusal is a page, never a 500 or a deadlock: the limit-1 plain
    # misses, then the miss that armed the lockout and everything after it.
    assert Counter(statuses) == Counter({400: LIMIT - 1, 429: total - LIMIT + 1})
    assert refusal_reasons() == Counter({"bad_password": LIMIT, "locked_out": total - LIMIT})
    record = SharedGateThrottle.objects.get(client_key=gate_key(address))
    assert record.lockout_cycles == 1
    assert record.failures == 0
    assert record.locked_until is not None and record.locked_until > timezone.now()

    # The correct password is refused while the lockout stands, and is not
    # even checked: being right does not reset a client that is locked out.
    assert gate_post(address, PASSWORD).status_code == 429
    assert password_check.calls == LIMIT
    assert SharedGateThrottle.objects.filter(client_key=gate_key(address)).exists()

    # Another client is nobody's hostage.
    assert gate_post("198.51.100.71", PASSWORD).status_code == 302

    # When the lockout runs out the door opens as before, and the state clears.
    SharedGateThrottle.objects.filter(client_key=gate_key(address)).update(
        locked_until=timezone.now() - timedelta(seconds=1)
    )
    assert gate_post(address, PASSWORD).status_code == 302
    assert not SharedGateThrottle.objects.filter(client_key=gate_key(address)).exists()


def test_one_clients_password_check_does_not_hold_up_another_client(password_check):
    """The lock is one client's row. Held through a hash, it stalls nobody else.

    The first client is held inside its password check — under its row lock,
    for as long as the test likes. A different client must get through, its own
    password verified, while that is still going on.
    """
    password_check.hold_first = True
    errors: list[BaseException] = []

    def held_client() -> None:
        try:
            gate_post("203.0.113.72", "vale")
        except BaseException as exc:  # pragma: no cover - reported by the assert
            errors.append(exc)
        finally:
            password_check.release.set()

    thread = run_in_thread(held_client)
    try:
        assert password_check.first_started.wait(timeout=LOCK_WAIT_TIMEOUT)
        response = gate_post("198.51.100.72", PASSWORD)
        still_verifying = not password_check.first_finished.is_set()
    finally:
        password_check.release.set()
        thread.join(timeout=40)

    assert errors == [], errors
    assert response.status_code == 302
    assert still_verifying, "the other client only got through once the first had finished"
    assert password_check.calls == 2


# -- the dev-login PIN ---------------------------------------------------------


@pytest.fixture
def database_cache(settings):
    """The cache the test stack actually runs, not the test settings' one.

    The counter's lost increments lived in `DatabaseCache.incr`, a get followed
    by a set. The in-process cache the rest of the suite uses increments under a
    Python lock and cannot show them.
    """
    settings.CACHES = {
        "default": {
            "BACKEND": "django.core.cache.backends.db.DatabaseCache",
            "LOCATION": CACHE_TABLE,
        }
    }
    call_command("createcachetable", verbosity=0)
    yield
    with connection.cursor() as cursor:
        cursor.execute(f"DROP TABLE IF EXISTS {CACHE_TABLE}")


@pytest.fixture
def pin_check(settings, monkeypatch, database_cache) -> CountingCheck:
    settings.DEV_LOGIN_ENABLED = True
    settings.DEV_LOGIN_PIN = PIN
    settings.DEV_LOGIN_PIN_MAX_ATTEMPTS = LIMIT
    settings.DEV_LOGIN_PIN_LOCKOUT_SECONDS = 300
    check = CountingCheck(views._pin_is_correct)
    monkeypatch.setattr(views, "_pin_is_correct", check)
    return check


@pytest.fixture
def persona():
    return factories.UserFactory(is_synthetic=True)


def pin_post(persona: Any, address: str, pin: str) -> Any:
    return Client().post(
        reverse("accounts:dev_login"),
        {"user_id": str(persona.pk), "pin": pin},
        HTTP_CF_CONNECTING_IP=address,
    )


def pin_counter(address: str) -> Any:
    return cache.get(f"dev-login-pin-attempts:{address}")


def test_a_pin_attempt_waits_for_the_one_being_compared(pin_check, persona):
    """The deterministic statement of the dev-login fix, the same shape."""
    address = "203.0.113.80"
    cache.set(f"dev-login-pin-attempts:{address}", LIMIT - 1, timeout=300)

    results, errors = hold_one_and_start_another(
        pin_check,
        lambda: pin_post(persona, address, "0001").status_code,
        lambda: pin_post(persona, address, "0002").status_code,
    )

    assert errors == [], errors
    assert pin_check.calls == 1, (
        f"{pin_check.calls} PINs were compared: the second attempt read the counter before "
        "the first attempt's miss was counted, and was compared past the limit"
    )
    # The limit-th miss is still compared and answered as a miss; the lockout
    # it arms refuses the next one.
    assert results == {"first": 400, "second": 429}
    assert refusal_reasons() == Counter({"bad_pin": 1, "locked_out": 1})
    assert pin_counter(address) == LIMIT


def test_a_burst_of_wrong_pins_gets_exactly_the_limit_compared(pin_check, persona):
    address = "203.0.113.81"

    statuses, errors = burst(lambda: pin_post(persona, address, "0000"))

    assert errors == [], errors
    total = CONNECTIONS * ATTEMPTS_PER_CONNECTION
    assert pin_check.calls == LIMIT, (
        f"{pin_check.calls} of {total} parallel wrong PINs were compared "
        f"against a limit of {LIMIT}"
    )
    assert Counter(statuses) == Counter({400: LIMIT, 429: total - LIMIT})
    assert refusal_reasons() == Counter({"bad_pin": LIMIT, "locked_out": total - LIMIT})
    # No increment lost, none invented.
    assert pin_counter(address) == LIMIT

    # The right PIN waits the window out like any other, uncompared.
    assert pin_post(persona, address, PIN).status_code == 429
    assert pin_check.calls == LIMIT

    # A different caller is unaffected.
    assert pin_post(persona, "198.51.100.81", PIN).status_code == 302

    # When the window expires the counter goes with it, and the PIN works.
    with connection.cursor() as cursor:
        cursor.execute(
            f"UPDATE {CACHE_TABLE} SET expires = %s WHERE cache_key LIKE %s",
            [timezone.now() - timedelta(seconds=1), f"%dev-login-pin-attempts:{address}"],
        )
        assert cursor.rowcount == 1
    assert pin_post(persona, address, PIN).status_code == 302
    assert pin_counter(address) is None


def test_one_callers_pin_check_does_not_hold_up_another_caller(pin_check, persona):
    pin_check.hold_first = True
    errors: list[BaseException] = []

    def held_caller() -> None:
        try:
            pin_post(persona, "203.0.113.82", "0000")
        except BaseException as exc:  # pragma: no cover - reported by the assert
            errors.append(exc)
        finally:
            pin_check.release.set()

    thread = run_in_thread(held_caller)
    try:
        assert pin_check.first_started.wait(timeout=LOCK_WAIT_TIMEOUT)
        response = pin_post(persona, "198.51.100.82", PIN)
        still_comparing = not pin_check.first_finished.is_set()
    finally:
        pin_check.release.set()
        thread.join(timeout=40)

    assert errors == [], errors
    assert response.status_code == 302
    assert still_comparing, "the other caller only got through once the first had finished"
    assert pin_check.calls == 2
