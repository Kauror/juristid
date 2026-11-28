"""The global daily storage allowance under real concurrency (r96 final, docs/adr/0234 §4).

Two different users prepare at the same moment, close to the deployment-wide
`MCP_WRITE_STORED_BYTES_GLOBAL_PER_DAY`. Their per-user locks are different, so
only the global counter can serialise them. Real PostgreSQL transactions on
real threads (two connections plus the test's own: three); synthetic accounts.

The overlap is forced, not hoped for: one thread is parked after its quota check
and before its proposal row is written (the point where the earlier code had
checked the global allowance but not yet made its claim durable), while the
other runs to the end.
"""

from __future__ import annotations

import threading
from typing import Any

import pytest
from django.db import connection

from app.mcp_writes import budgets, proposals
from app.mcp_writes.models import (
    McpWriteCounter,
    McpWriteEventType,
    McpWriteProposal,
    ProposalStatus,
)
from app.mcp_writes.refusals import Refusal
from tests import factories
from tests import test_mcp_write_proposals_support as support

pytestmark = pytest.mark.django_db(transaction=True, serialized_rollback=True)


@pytest.fixture(autouse=True)
def writes_on(settings):
    support.enable(settings)


@pytest.fixture
def op(monkeypatch):
    return support.register(monkeypatch)


def person(name: str) -> dict[str, Any]:
    user = factories.UserFactory()
    matter = factories.MatterFactory(owner=user, title="Algne")
    _grant, _raw, access = support.writer(user)
    return {"name": name, "user": user, "matter": matter, "access": access}


def prepare(who: dict[str, Any]) -> Any:
    return proposals.prepare(
        access=who["access"],
        user=who["user"],
        operation_name="test_rename_matter",
        raw_arguments={"matter": str(who["matter"].pk), "title": "Uus nimi"},
    )


def global_bytes() -> int:
    row = McpWriteCounter.objects.filter(principal="global", bucket="stored_bytes").first()
    return int(row.count) if row else 0


def user_bytes(who: dict[str, Any]) -> int:
    row = McpWriteCounter.objects.filter(
        principal=f"user:{who['user'].pk}", bucket="stored_bytes"
    ).first()
    return int(row.count) if row else 0


def reset_storage_counters() -> None:
    McpWriteCounter.objects.filter(bucket="stored_bytes").delete()


@pytest.fixture
def world(op, monkeypatch, settings):
    """Three people; one proposal's size measured; the global allowance = 1.5 proposals."""
    support.install_access(monkeypatch)
    people = {name: person(name) for name in ("a", "b", "c")}
    measured = prepare(people["c"])
    assert isinstance(measured, dict)
    size = global_bytes()
    assert size > 400
    McpWriteProposal.objects.filter(user=people["c"]["user"]).update(
        status=ProposalStatus.DECLINED, failure_code="declined"
    )
    reset_storage_counters()
    settings.MCP_WRITE_STORED_BYTES_GLOBAL_PER_DAY = int(size * 1.5)
    return {"people": people, "size": size}


def is_limit(answer: Any) -> bool:
    return (
        isinstance(answer, Refusal)
        and answer.code == "rate_limited"
        and answer.as_json().get("budget") == "stored_bytes"
        and answer.as_json().get("scope") == "global"
    )


def run_together(*people: dict[str, Any], park: str | None = None, monkeypatch=None):
    """Prepare for each person on its own thread; optionally park ``park`` before its insert."""
    answers: dict[str, Any] = {}
    errors: list[BaseException] = []
    parked = threading.Event()
    go = threading.Event()
    if park is not None:
        assert monkeypatch is not None
        original = proposals.arguments_digest

        def digest(**kwargs: Any) -> str:
            if threading.current_thread().name == park:
                parked.set()
                assert go.wait(timeout=20)
            return original(**kwargs)

        monkeypatch.setattr(proposals, "arguments_digest", digest)

    def run(who: dict[str, Any]) -> None:
        try:
            answers[who["name"]] = prepare(who)
        except BaseException as error:  # surfaced by the assertions
            errors.append(error)
        finally:
            connection.close()

    threads = {
        who["name"]: threading.Thread(target=run, args=(who,), name=who["name"]) for who in people
    }
    if park is None:
        for thread in threads.values():
            thread.start()
    else:
        threads[park].start()
        assert parked.wait(timeout=20)
        others = [t for n, t in threads.items() if n != park]
        for thread in others:
            thread.start()
        for thread in others:
            thread.join(timeout=2)  # the other one may legitimately be waiting for the park
        go.set()
    for thread in threads.values():
        thread.join(timeout=40)
        assert not thread.is_alive(), "a thread hung: a deadlock or a lock never released"
    return answers, errors


def test_two_users_overlapping_near_the_global_limit_cannot_both_pass(world, monkeypatch):
    a, b = world["people"]["a"], world["people"]["b"]
    answers, errors = run_together(a, b, park="a", monkeypatch=monkeypatch)

    assert errors == []
    outcomes = sorted(isinstance(answer, dict) for answer in answers.values())
    assert outcomes == [False, True], answers
    refused = next(answer for answer in answers.values() if not isinstance(answer, dict))
    assert is_limit(refused)
    assert "Nothing was saved" in refused.as_json()["message"] or refused.as_json()["budget"]
    # the limit held, and the counters are exactly what was committed
    assert global_bytes() <= int(world["size"] * 1.5)
    assert (
        McpWriteProposal.objects.filter(
            user__in=[a["user"], b["user"]], status=ProposalStatus.PENDING
        ).count()
        == 1
    )
    assert global_bytes() == user_bytes(a) + user_bytes(b) == world["size"]
    # the refused user consumed nothing
    loser = a if user_bytes(a) == 0 else b
    assert user_bytes(loser) == 0


def test_the_refused_request_is_the_later_one_and_its_retry_guidance_is_the_existing_one(
    world, monkeypatch
):
    a, b = world["people"]["a"], world["people"]["b"]
    answers, errors = run_together(a, b, park="a", monkeypatch=monkeypatch)
    assert errors == []
    refused = next(answer for answer in answers.values() if not isinstance(answer, dict))
    body = refused.as_json()
    assert body["retry_after_seconds"] >= 1 and body["scope"] == "global"
    assert "stored_bytes" in body["message"]


def test_twenty_unsynchronised_rounds_never_exceed_the_limit_nor_deadlock(world):
    a, b = world["people"]["a"], world["people"]["b"]
    for round_number in range(20):
        # fresh windows: only the global allowance is on trial
        McpWriteCounter.objects.all().delete()
        McpWriteProposal.objects.filter(
            user__in=[a["user"], b["user"]], status=ProposalStatus.PENDING
        ).update(status=ProposalStatus.DECLINED, failure_code="declined")
        before = McpWriteProposal.objects.filter(user__in=[a["user"], b["user"]]).count()
        answers, errors = run_together(a, b)
        assert errors == [], (round_number, errors)
        assert sorted(isinstance(x, dict) for x in answers.values()) == [False, True], (
            round_number,
            answers,
        )
        assert global_bytes() == world["size"] == user_bytes(a) + user_bytes(b)
        after = McpWriteProposal.objects.filter(user__in=[a["user"], b["user"]]).count()
        assert after - before == 1


def test_concurrent_requests_below_the_limit_all_succeed(world, settings):
    settings.MCP_WRITE_STORED_BYTES_GLOBAL_PER_DAY = world["size"] * 10
    a, b = world["people"]["a"], world["people"]["b"]
    answers, errors = run_together(a, b)
    assert errors == []
    assert all(isinstance(answer, dict) for answer in answers.values())
    assert global_bytes() == 2 * world["size"] == user_bytes(a) + user_bytes(b)


def test_the_same_user_path_still_works_and_is_still_serialised(world):
    a = world["people"]["a"]
    answers, errors = run_together(a, dict(a, name="a2"))
    assert errors == []
    # one user, two overlapping requests, room for one: exactly one is kept
    assert sorted(isinstance(x, dict) for x in answers.values()) == [False, True]
    assert global_bytes() == user_bytes(a) == world["size"]


def test_a_refusal_consumes_nothing_and_an_exact_fit_is_allowed(world, settings):
    a, b = world["people"]["a"], world["people"]["b"]
    settings.MCP_WRITE_STORED_BYTES_GLOBAL_PER_DAY = world["size"] * 2  # exactly two
    assert isinstance(prepare(a), dict)
    McpWriteProposal.objects.filter(user=a["user"]).update(
        status=ProposalStatus.DECLINED, failure_code="declined"
    )
    assert isinstance(prepare(b), dict)  # the exact fit
    assert global_bytes() == 2 * world["size"]
    McpWriteProposal.objects.filter(user=b["user"]).update(
        status=ProposalStatus.DECLINED, failure_code="declined"
    )
    answer = prepare(a)
    assert is_limit(answer)
    assert global_bytes() == 2 * world["size"] == user_bytes(a) + user_bytes(b)
    assert McpWriteProposal.objects.count() == 3  # c's measured one, a's, b's


def test_a_rolled_back_preparation_leaves_the_counters_untouched(world, monkeypatch):
    a = world["people"]["a"]

    real_record = proposals.record

    def boom(event_type: Any, *args: Any, **kwargs: Any) -> Any:
        if event_type == McpWriteEventType.PREPARED:
            raise RuntimeError("interrupted after the quota was claimed")
        return real_record(event_type, *args, **kwargs)

    monkeypatch.setattr(proposals, "record", boom)
    answer = prepare(a)
    monkeypatch.undo()
    assert isinstance(answer, Refusal) and answer.code == "internal_error"
    assert global_bytes() == 0 and user_bytes(a) == 0
    assert McpWriteProposal.objects.filter(user=a["user"]).count() == 0


def test_the_allowance_of_a_rolled_back_claim_is_free_for_the_next_user(world, monkeypatch):
    """A parked claim that then fails must not leave the next person refused."""
    a, b = world["people"]["a"], world["people"]["b"]
    gate = threading.Event()

    def digest(**kwargs: Any) -> str:
        if threading.current_thread().name == "a":
            gate.wait(timeout=20)
            raise RuntimeError("a's preparation breaks after its claim")
        return real(**kwargs)

    real = proposals.arguments_digest
    monkeypatch.setattr(proposals, "arguments_digest", digest)
    answers: dict[str, Any] = {}

    def run(who: dict[str, Any]) -> None:
        try:
            answers[who["name"]] = prepare(who)
        finally:
            connection.close()

    first = threading.Thread(target=run, args=(a,), name="a")
    second = threading.Thread(target=run, args=(b,), name="b")
    first.start()
    second.start()
    second.join(timeout=2)  # b waits on a's claim (or has not started to)
    gate.set()
    first.join(timeout=30)
    second.join(timeout=30)
    assert isinstance(answers["a"], Refusal) and answers["a"].code == "internal_error"
    assert isinstance(answers["b"], dict), answers
    assert global_bytes() == user_bytes(b) == world["size"] and user_bytes(a) == 0


def test_the_ordering_rule_is_user_lock_then_global_row():
    """The documented rule, kept where a reader of the module will find it."""
    assert "user lock" in (budgets.__doc__ or "").lower()
    assert "global" in (budgets.__doc__ or "").lower()
