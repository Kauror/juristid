"""Issuing one-time links from several connections at once (docs/adr/0180 §5).

csf_42d9970ac23d6f55f4b19163: `tokens.issue` was invalidate-then-insert with
nothing making two issues for one person take turns. Each invalidated the links
committed before it, none saw the others' uncommitted ones, and every one of
them inserted — several live links in several mailboxes, the thing
"superseded" exists to prevent. Now the account's row is locked first and a
partial unique index holds the rule.

Real connections, so a real database transaction per thread: three at most
(more crashes the emulated local cluster; CI's PostgreSQL is the authority).
The window between invalidating and inserting is widened deterministically by
slowing the random-selector call that sits inside it, so the race is not left
to scheduling luck.
"""

from __future__ import annotations

import threading
import time
from collections.abc import Callable
from typing import Any

import pytest
from django.db import connections
from django.utils import timezone

from app.accounts import tokens
from app.accounts.enums import CredentialTokenPurpose
from app.accounts.models import AccountCredentialToken
from tests.local_auth import apply_local_password, person

pytestmark = pytest.mark.django_db(transaction=True, serialized_rollback=True)

RESET = CredentialTokenPurpose.PASSWORD_RESET
CONNECTIONS = 3
LOCK_WAIT_TIMEOUT = 20


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
def slow_selector(monkeypatch) -> None:
    """Hold each issue between its invalidation and its insert for a moment."""
    real = tokens.secrets.token_hex

    def slow(nbytes: int | None = None) -> str:
        time.sleep(0.4)
        return real(nbytes)

    monkeypatch.setattr(tokens.secrets, "token_hex", slow)


def test_parallel_issues_leave_exactly_one_usable_link(settings, slow_selector):
    """Fails on 4818203e: all three inserted, three live links."""
    apply_local_password(settings)
    user, _ = person()
    start = threading.Barrier(CONNECTIONS, timeout=LOCK_WAIT_TIMEOUT)
    issued: list[Any] = []
    failures: list[BaseException] = []
    lock = threading.Lock()

    def one_issue() -> None:
        try:
            start.wait()
            link = tokens.issue(user=user, purpose=RESET)
            with lock:
                issued.append(link)
        except BaseException as exc:  # pragma: no cover - reported by the assert
            with lock:
                failures.append(exc)

    threads = [run_in_thread(one_issue) for _ in range(CONNECTIONS)]
    for thread in threads:
        thread.join(timeout=60)

    assert not any(thread.is_alive() for thread in threads), "an issue never finished"
    assert failures == [], failures
    assert len(issued) == CONNECTIONS
    live = AccountCredentialToken.objects.filter(
        user=user, purpose=RESET, used_at__isnull=True, invalidated_at__isnull=True
    )
    assert live.count() == 1
    usable = [
        link
        for link in issued
        if tokens.peek(tokens.proof_of(link.token), purpose=RESET) is not None
    ]
    assert len(usable) == 1
    assert usable[0].record.pk == live.get().pk
    assert all(
        row.invalidation_reason == "superseded"
        for row in AccountCredentialToken.objects.filter(user=user).exclude(pk=live.get().pk)
    )
    assert live.get().expires_at > timezone.now()
