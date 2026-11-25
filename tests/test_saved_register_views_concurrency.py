"""«Minu vaated» under two tabs at once (UX-007, app/matters/saved_views.py).

Real transactions in two threads, lined up on PostgreSQL's own lock report
(`wait_for_a_blocked_backend`), never on a sleep. Each case ends the same way:
one write lands, the other is refused *in words* (`DomainError`) — never an
unhandled `IntegrityError` from the one-name-per-person constraint, and never a
twenty-first view.
"""

from __future__ import annotations

import threading
from collections.abc import Callable
from typing import Any

import pytest
from django.db import transaction
from django.http import QueryDict

from app.core.errors import DomainError
from app.matters import saved_views
from app.matters.models import SavedRegisterView
from tests.test_concurrency import LOCK_WAIT_TIMEOUT, run_in_thread, wait_for_a_blocked_backend

pytestmark = pytest.mark.django_db(transaction=True, serialized_rollback=True)


def _params(query: str = "olek=koik") -> QueryDict:
    return QueryDict(query)


def _race(first: Callable[[], Any], second: Callable[[], Any]) -> list[BaseException | None]:
    """Run ``first`` holding its transaction open until ``second`` queues behind it.

    Returns what each raised (``None`` for success), first then second.
    """
    holding = threading.Event()
    release = threading.Event()
    outcomes: list[BaseException | None] = [None, None]

    def hold() -> None:
        try:
            with transaction.atomic():
                first()
                holding.set()
                release.wait(LOCK_WAIT_TIMEOUT)
        except BaseException as error:
            outcomes[0] = error
            holding.set()

    def contend() -> None:
        try:
            second()
        except BaseException as error:
            outcomes[1] = error

    holder = run_in_thread(hold)
    assert holding.wait(LOCK_WAIT_TIMEOUT)
    contender = run_in_thread(contend)
    wait_for_a_blocked_backend()
    release.set()
    holder.join(LOCK_WAIT_TIMEOUT)
    contender.join(LOCK_WAIT_TIMEOUT)
    assert not holder.is_alive() and not contender.is_alive()
    return outcomes


def test_two_tabs_saving_one_name_at_once_end_with_one_view_and_a_refusal(specialist):
    outcomes = _race(
        lambda: saved_views.save_view(user=specialist, name="Minu eelnõud", params=_params()),
        lambda: saved_views.save_view(
            user=specialist, name="minu EELNÕUD", params=_params("olek=avatud")
        ),
    )

    assert outcomes[0] is None
    assert isinstance(outcomes[1], DomainError), outcomes[1]
    assert "on sul juba olemas" in str(outcomes[1])
    assert list(
        SavedRegisterView.objects.filter(user=specialist).values_list("name", flat=True)
    ) == ["Minu eelnõud"]


def test_two_tabs_saving_the_twentieth_view_at_once_keep_twenty(specialist):
    for number in range(saved_views.MAX_VIEWS - 1):
        saved_views.save_view(
            user=specialist, name=f"Vaade {number}", params=_params(f"q=sona{number}")
        )

    outcomes = _race(
        lambda: saved_views.save_view(user=specialist, name="Kahekümnes", params=_params()),
        lambda: saved_views.save_view(
            user=specialist, name="Kahekümne esimene", params=_params("olek=avatud")
        ),
    )

    assert outcomes[0] is None
    assert isinstance(outcomes[1], DomainError), outcomes[1]
    assert f"Sul on juba {saved_views.MAX_VIEWS} vaadet" in str(outcomes[1])
    assert SavedRegisterView.objects.filter(user=specialist).count() == saved_views.MAX_VIEWS


def test_two_renames_to_one_name_at_once_end_with_one_rename_and_a_refusal(specialist):
    first = saved_views.save_view(user=specialist, name="Esimene", params=_params())
    second = saved_views.save_view(user=specialist, name="Teine", params=_params("olek=avatud"))

    outcomes = _race(
        lambda: saved_views.rename_view(view=first, name="Ühine nimi"),
        lambda: saved_views.rename_view(view=second, name="ühine NIMI"),
    )

    assert outcomes[0] is None
    assert isinstance(outcomes[1], DomainError), outcomes[1]
    assert "on sul juba olemas" in str(outcomes[1])
    first.refresh_from_db()
    second.refresh_from_db()
    assert (first.name, second.name) == ("Ühine nimi", "Teine")


def test_renaming_a_view_another_tab_just_removed_is_refused_in_words(specialist):
    view = saved_views.save_view(user=specialist, name="Kaob", params=_params())
    stale = SavedRegisterView.objects.get(pk=view.pk)

    outcomes = _race(
        lambda: saved_views.remove_view(view=view) or saved_views._lock_person(specialist),
        lambda: saved_views.rename_view(view=stale, name="Uus nimi"),
    )

    assert outcomes[0] is None
    assert isinstance(outcomes[1], DomainError), outcomes[1]
    assert "enam ei ole" in str(outcomes[1])
    assert not SavedRegisterView.objects.filter(user=specialist).exists()
