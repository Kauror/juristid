"""The write gateway's cumulative budgets (SEC-014, docs/adr/0234 §4).

`app.mcp_writes.budgets` through the gateway's own entry points
(`proposals.prepare`, `commit.commit`), with the stand-in rename operation and
synthetic accounts only. Limits are lowered per test so that a handful of calls
exhausts them; the defaults and their rationale are the ADR's.
"""

from __future__ import annotations

import threading
import uuid
from dataclasses import replace
from datetime import timedelta
from io import StringIO
from types import SimpleNamespace

import pytest
from django.core.management import call_command
from django.db import connection
from django.utils import timezone

from app.mcp_server import write_client
from app.mcp_writes import approval, budgets, proposals, review
from app.mcp_writes.commit import commit
from app.mcp_writes.models import (
    ApprovalMethod,
    McpWriteCounter,
    McpWriteEvent,
    McpWriteEventType,
    McpWriteProposal,
    ProposalStatus,
)
from app.mcp_writes.refusals import Refusal
from tests import factories
from tests import test_mcp_write_proposals_support as support

pytestmark = pytest.mark.django_db


@pytest.fixture(autouse=True)
def writes_on(settings):
    support.enable(settings)


@pytest.fixture
def op(monkeypatch):
    return support.register(monkeypatch)


@pytest.fixture
def world(op, monkeypatch):
    support.install_access(monkeypatch)
    user = factories.UserFactory()
    matter = factories.MatterFactory(owner=user, title="Algne")
    grant, _raw, access = support.writer(user)
    return {"user": user, "matter": matter, "grant": grant, "access": access}


def prepare(world, title: str = "Uus", *, access=None, arguments=None):
    return proposals.prepare(
        access=access or world["access"],
        user=world["user"],
        operation_name="test_rename_matter",
        raw_arguments=arguments
        if arguments is not None
        else {"matter": str(world["matter"].pk), "title": title},
    )


def limited(answer) -> bool:
    return isinstance(answer, Refusal) and answer.code == "rate_limited"


def events(event_type: str) -> list[McpWriteEvent]:
    return list(McpWriteEvent.objects.filter(event_type=event_type).order_by("created_at"))


def later(monkeypatch, minutes: int) -> None:
    """Move the budgets' clock forward (only theirs: expiries are not what is tested)."""
    moment = timezone.now() + timedelta(minutes=minutes)
    monkeypatch.setattr(budgets, "timezone", SimpleNamespace(now=lambda: moment))


# -- ordinary use ----------------------------------------------------------------------------


def test_a_normal_session_is_never_limited_and_is_counted_per_principal(world):
    answers = [prepare(world, f"Pealkiri {n}") for n in range(5)]
    assert all(isinstance(answer, dict) for answer in answers)
    keys = set(McpWriteCounter.objects.filter(bucket="prepare").values_list("principal", flat=True))
    access = world["access"]
    assert keys == {
        f"user:{world['user'].pk}",
        f"grant:{world['grant'].pk}",
        f"family:{access.family_id}",
        budgets.client_key(access.client_id),
        "global",
    }
    assert not any(access.client_id in key for key in keys)  # never the client id itself
    row = McpWriteCounter.objects.get(
        principal=f"user:{world['user'].pk}", bucket="prepare", period="10m"
    )
    assert row.count == 5


# -- exhaustion, recovery and what is said ----------------------------------------------------


def test_repeated_proposals_run_out_with_retry_guidance_and_store_nothing(world, settings):
    settings.MCP_WRITE_PREPARE_PER_10M = 3
    assert all(isinstance(prepare(world, f"P{n}"), dict) for n in range(3))

    answer = prepare(world, "Neljas")

    assert limited(answer) and answer.status == 429
    body = answer.as_json()
    assert 0 < body["retry_after_seconds"] <= 600 and body["budget"] == "prepare"
    assert body["scope"] in {"user", "grant", "family", "client"}
    assert "Nothing was saved" in body["message"] and "minute" in body["message"]
    assert McpWriteProposal.objects.count() == 3
    refused = events(McpWriteEventType.PREPARE_REFUSED)[-1]
    assert refused.detail["code"] == "rate_limited" and refused.detail["reason"] == "prepare"


def test_a_refreshed_or_new_token_of_the_same_person_shares_the_budget(world, settings):
    settings.MCP_WRITE_PREPARE_PER_10M = 2
    assert isinstance(prepare(world, "Üks"), dict)
    refreshed = support.verified(
        support.token_for(world["user"], world["grant"], family_id=world["access"].family_id)
    )
    assert isinstance(prepare(world, "Kaks", access=refreshed), dict)
    new_authorization = support.verified(support.token_for(world["user"], world["grant"]))
    assert new_authorization.family_id != world["access"].family_id
    assert limited(prepare(world, "Kolm", access=new_authorization))


def test_the_budget_comes_back_when_the_window_ends(world, settings, monkeypatch):
    settings.MCP_WRITE_PREPARE_PER_10M = 1
    assert isinstance(prepare(world, "Üks"), dict)
    assert limited(prepare(world, "Kaks"))
    later(monkeypatch, 11)
    assert isinstance(prepare(world, "Kolm"), dict)


def test_the_daily_budget_holds_across_ten_minute_windows(world, settings, monkeypatch):
    settings.MCP_WRITE_PREPARE_PER_DAY = 2
    assert isinstance(prepare(world, "Üks"), dict)
    later(monkeypatch, 11)
    assert isinstance(prepare(world, "Kaks"), dict)
    later(monkeypatch, 22)
    answer = prepare(world, "Kolm")
    assert limited(answer) and answer.as_json()["retry_after_seconds"] > 600


def test_the_deployment_wide_budget_counts_everybody(world, settings, op):
    settings.MCP_WRITE_PREPARE_GLOBAL_PER_10M = 2
    other = factories.UserFactory()
    matter = factories.MatterFactory(owner=other)
    _grant, _raw, access = support.writer(other)
    assert isinstance(prepare(world, "Üks"), dict)
    assert isinstance(
        proposals.prepare(
            access=access,
            user=other,
            operation_name=op.name,
            raw_arguments={"matter": str(matter.pk), "title": "Teine"},
        ),
        dict,
    )
    answer = prepare(world, "Kolm")
    assert limited(answer) and answer.as_json()["scope"] == "global"


def test_outstanding_proposals_are_capped_and_freed_by_a_decision(world, settings):
    settings.MCP_WRITE_MAX_OUTSTANDING = 2
    first = prepare(world, "Üks")
    assert isinstance(first, dict) and isinstance(prepare(world, "Kaks"), dict)
    answer = prepare(world, "Kolm")
    assert limited(answer) and answer.as_json()["budget"] == "outstanding"
    McpWriteProposal.objects.filter(pk=first["proposal"]["id"]).update(
        status=ProposalStatus.DECLINED, failure_code="declined"
    )
    assert isinstance(prepare(world, "Neli"), dict)


def test_stored_bytes_are_capped_per_day(world, settings):
    assert isinstance(prepare(world, "x" * 150), dict)
    stored = McpWriteCounter.objects.get(
        principal=f"user:{world['user'].pk}", bucket="stored_bytes"
    ).count
    assert stored > 600  # arguments, canonical arguments, state and preview
    settings.MCP_WRITE_STORED_BYTES_PER_DAY = stored + 100
    answer = prepare(world, "y" * 150)
    assert limited(answer) and answer.as_json()["budget"] == "stored_bytes"
    assert McpWriteProposal.objects.count() == 1


# -- repeated refusals ------------------------------------------------------------------------


def test_repeated_invalid_requests_stop_the_principal_until_the_window_ends(
    world, settings, monkeypatch
):
    settings.MCP_WRITE_REFUSED_PER_10M = 4
    for _ in range(4):
        answer = prepare(world, arguments={"matter": "x"})  # no title: invalid
        assert isinstance(answer, Refusal) and answer.code == "invalid_argument"

    valid = prepare(world, "Nüüd õige")

    assert limited(valid) and valid.as_json()["budget"] == "refused"
    later(monkeypatch, 11)
    assert isinstance(prepare(world, "Hiljem õige"), dict)


def test_repeated_identical_refusals_are_aggregated_not_dropped(world, settings):
    settings.MCP_WRITE_EVENTS_PER_WINDOW = 3
    for _ in range(25):
        answer = commit(access=world["access"], user=world["user"], proposal_id=str(uuid.uuid4()))
        assert isinstance(answer, Refusal) and answer.code == "not_found"

    written = events(McpWriteEventType.COMMIT_REFUSED)
    assert len(written) == 4  # three in full, one marked aggregated, then counted only
    assert "occurrence" not in written[0].detail and written[0].detail["code"] == "not_found"
    assert written[0].detail["reason"] == "no_such_proposal"
    assert written[-1].detail["aggregated"] is True and written[-1].detail["occurrence"] == 4
    counter = McpWriteCounter.objects.get(
        bucket="event:COMMIT_REFUSED:not_found:no_such_proposal", period="10m"
    )
    assert counter.count == 25  # the exact total stays observable


def test_a_replayed_receipt_is_counted_and_aggregated_but_never_refused(world, settings):
    settings.MCP_WRITE_EVENTS_PER_WINDOW = 2
    settings.MCP_WRITE_COMMIT_PER_10M = 3
    answer = prepare(world)
    proposal = McpWriteProposal.objects.get(pk=answer["proposal"]["id"])
    approval.approve_and_save(
        proposal_id=proposal.pk,
        user=world["user"],
        binding=review.derive(proposal, world["user"]).binding,
        method=ApprovalMethod.TOTP,
        ip=None,
    )
    receipts = [
        commit(access=world["access"], user=world["user"], proposal_id=str(proposal.pk))
        for _ in range(12)
    ]
    assert all(receipt["status"] == "executed" for receipt in receipts)  # past the budget too
    assert [receipt["replayed"] for receipt in receipts] == [False] + [True] * 11
    assert len(events(McpWriteEventType.RECEIPT_DELIVERED)) == 1
    assert len(events(McpWriteEventType.COMMIT_REPLAYED)) == 3
    # Polls of an unapproved proposal are budgeted, and say how long to wait.
    waiting = prepare(world, "Teine")
    polls = [
        commit(access=world["access"], user=world["user"], proposal_id=waiting["proposal"]["id"])
        for _ in range(3)
    ]
    assert any(limited(poll) for poll in polls)


def test_a_rate_limited_answer_reaches_the_assistant_as_nothing_saved():
    answer = write_client.refusal("rate_limited", "Try again in about 4 minutes.", action="commit")
    assert isinstance(answer, write_client.GatewayRefused)
    assert "Nothing was saved" in answer.text and "4 minutes" in answer.text


# -- concurrency -------------------------------------------------------------------------------


@pytest.mark.django_db(transaction=True, serialized_rollback=True)
def test_two_prepares_at_once_cannot_both_take_the_last_slot(world, settings):
    """Two threads, two connections (three with the test's own): the counter is one upsert."""
    settings.MCP_WRITE_PREPARE_PER_10M = 1
    barrier = threading.Barrier(2)
    answers: list[object] = []
    errors: list[BaseException] = []

    def run(title: str) -> None:
        try:
            barrier.wait(timeout=10)
            answers.append(prepare(world, title))
        except BaseException as error:  # pragma: no cover - surfaced below
            errors.append(error)
        finally:
            connection.close()

    threads = [threading.Thread(target=run, args=(f"T{n}",)) for n in range(2)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=30)

    assert errors == []
    assert sorted(isinstance(answer, dict) for answer in answers) == [False, True]
    assert McpWriteProposal.objects.count() == 1
    assert (
        McpWriteCounter.objects.get(
            principal=f"user:{world['user'].pk}", bucket="prepare", period="10m"
        ).count
        == 2
    )


@pytest.mark.django_db(transaction=True, serialized_rollback=True)
def test_two_prepares_at_once_cannot_exceed_the_outstanding_cap(world, settings):
    settings.MCP_WRITE_MAX_OUTSTANDING = 1
    barrier = threading.Barrier(2)
    answers: list[object] = []

    def run(title: str) -> None:
        try:
            barrier.wait(timeout=10)
            answers.append(prepare(world, title))
        finally:
            connection.close()

    threads = [threading.Thread(target=run, args=(f"T{n}",)) for n in range(2)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=30)
    assert sorted(isinstance(answer, dict) for answer in answers) == [False, True]
    assert McpWriteProposal.objects.filter(status=ProposalStatus.PENDING).count() == 1


# -- diagnostics ---------------------------------------------------------------------------------


def test_the_command_and_haldus_show_counts_without_client_details(world, settings):
    settings.MCP_WRITE_EVENTS_PER_WINDOW = 1
    for _ in range(5):
        commit(access=world["access"], user=world["user"], proposal_id=str(uuid.uuid4()))
    prepare(world)

    out = StringIO()
    call_command("mcp_writes", "budgets", stdout=out)
    text = out.getvalue()
    assert "MCP_WRITE_PREPARE_PER_10M = 30" in text and "Proposals waiting for approval: 1" in text
    assert "Repeated refusals counted beyond" in text
    assert world["access"].client_id not in text

    summary = budgets.summary()
    assert summary is not None
    assert summary["prepared_today"] == 1 and summary["outstanding"] == 1
    assert summary["refused_last_day"] == 5 and summary["suppressed_last_day"] == 3
    assert world["access"].client_id not in str(summary)


def test_haldus_shows_no_section_while_writes_are_off(settings):
    settings.MCP_WRITES_ENABLED = False
    assert budgets.summary() is None


def test_a_commit_of_another_principal_still_counts_against_the_caller(world, settings):
    """Asking about somebody else's proposal is a refused call of the caller's own."""
    settings.MCP_WRITE_REFUSED_PER_10M = 2
    other = factories.UserFactory()
    other_matter = factories.MatterFactory(owner=other)
    _grant, _raw, other_access = support.writer(other)
    theirs = proposals.prepare(
        access=other_access,
        user=other,
        operation_name="test_rename_matter",
        raw_arguments={"matter": str(other_matter.pk), "title": "Nende"},
    )
    for _ in range(2):
        answer = commit(
            access=world["access"], user=world["user"], proposal_id=theirs["proposal"]["id"]
        )
        assert isinstance(answer, Refusal) and answer.code == "not_found"
    assert limited(prepare(world, "Minu"))
    # The other person is not affected by the caller's refusals.
    assert isinstance(
        commit(access=other_access, user=other, proposal_id=theirs["proposal"]["id"]), dict
    )
    assert replace(other_access) == other_access
