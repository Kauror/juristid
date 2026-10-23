"""Commit: only an approved, unexpired, untampered, current proposal runs — once (ADR 0189).

`app.mcp_writes.commit.commit` with the stand-in rename operation
(`tests/test_mcp_write_proposals_support.py`). The approval rows are written
here the way the approval page writes them; the page itself is
`tests/test_mcp_write_approval.py`.
"""

from __future__ import annotations

import threading
import uuid
from dataclasses import replace
from datetime import timedelta

import pytest
from django.db import connection
from django.utils import timezone

from app.audit.enums import ChangeEventType
from app.audit.models import ChangeEvent
from app.mcp_access.models import GrantStatus
from app.mcp_writes import proposals
from app.mcp_writes.commit import commit
from app.mcp_writes.models import (
    ApprovalMethod,
    McpWriteApproval,
    McpWriteEvent,
    McpWriteEventType,
    McpWriteProposal,
    ProposalStatus,
)
from app.mcp_writes.refusals import Refusal
from tests import factories
from tests import test_mcp_write_proposals_support as support

ORIGINAL = "Pakendiseaduse muutmine"
RENAMED = "Pakendiseaduse muutmise eelnõu"


@pytest.fixture(autouse=True)
def writes_on(settings):
    support.enable(settings)


@pytest.fixture
def op(monkeypatch):
    return support.register(monkeypatch)


@pytest.fixture
def stand_ins(monkeypatch):
    return support.install_access(monkeypatch)


@pytest.fixture
def world(db, op, stand_ins):
    user = factories.UserFactory()
    matter = factories.MatterFactory(owner=user, title=ORIGINAL)
    grant, raw, access = support.writer(user)
    return {"user": user, "matter": matter, "grant": grant, "raw": raw, "access": access}


def prepared(world) -> McpWriteProposal:
    answer = proposals.prepare(
        access=world["access"],
        user=world["user"],
        operation_name="test_rename_matter",
        raw_arguments={"matter": str(world["matter"].pk), "title": RENAMED},
    )
    assert isinstance(answer, dict), answer
    return McpWriteProposal.objects.get(pk=answer["proposal"]["id"])


def approve(proposal: McpWriteProposal, *, digest: str | None = None, seconds: int = 600):
    """What the approval page writes after the person's code was accepted."""
    now = timezone.now()
    McpWriteApproval.objects.create(
        proposal=proposal,
        user=proposal.user,
        grant=proposal.grant,
        arguments_digest=digest or proposal.arguments_digest,
        approved_at=now,
        expires_at=now + timedelta(seconds=seconds),
        method=ApprovalMethod.TOTP,
    )
    McpWriteProposal.objects.filter(pk=proposal.pk).update(status=ProposalStatus.APPROVED)
    proposal.refresh_from_db()
    return proposal


def _commit(world, proposal, **overrides):
    access = replace(world["access"], **overrides) if overrides else world["access"]
    return commit(access=access, user=world["user"], proposal_id=str(proposal.pk))


def title_of(world) -> str:
    world["matter"].refresh_from_db()
    return world["matter"].title


def renames() -> int:
    return ChangeEvent.objects.filter(event_type=ChangeEventType.MATTER_TITLE_CHANGED).count()


def _events(proposal) -> list[str]:
    return list(
        McpWriteEvent.objects.filter(proposal=proposal)
        .order_by("created_at")
        .values_list("event_type", flat=True)
    )


# -- the happy path and its replay -------------------------------------------------


@pytest.mark.django_db
def test_an_approved_proposal_executes_through_the_canonical_service(world, op):
    proposal = approve(prepared(world))

    answer = _commit(world, proposal)

    assert answer["status"] == "executed" and answer["replayed"] is False
    result = answer["result"]
    assert result["record_type"] == "matter" and result["record_id"] == str(world["matter"].pk)
    assert result["url"].startswith(support.access_support.ORIGIN + "/teemad/")
    assert result["saved"] == {"title": RENAMED}
    assert title_of(world) == RENAMED and renames() == 1 and op.executions == 1
    proposal.refresh_from_db()
    assert proposal.status == ProposalStatus.EXECUTED and proposal.executed_at is not None
    assert _events(proposal)[-1] == McpWriteEventType.COMMITTED


@pytest.mark.django_db
def test_a_replayed_commit_returns_the_same_receipt_and_executes_once(world, op):
    proposal = approve(prepared(world))
    first = _commit(world, proposal)
    second = _commit(world, proposal)

    assert second["status"] == "executed" and second["replayed"] is True
    assert second["result"] == first["result"]
    assert op.executions == 1 and renames() == 1
    assert _events(proposal)[-2:] == [
        McpWriteEventType.COMMITTED,
        McpWriteEventType.COMMIT_REPLAYED,
    ]


# -- nothing without an approval ------------------------------------------------------


@pytest.mark.django_db
def test_without_an_approval_nothing_changes_and_the_client_is_sent_to_the_page(world, op):
    proposal = prepared(world)

    answer = _commit(world, proposal)

    assert answer["status"] == "awaiting_approval"
    assert answer["approval_url"].endswith(f"/mcp-ligipaas/muudatus/{proposal.pk}/")
    assert title_of(world) == ORIGINAL and op.executions == 0
    proposal.refresh_from_db()
    assert proposal.status == ProposalStatus.PENDING


@pytest.mark.django_db
def test_an_approved_status_without_an_approval_row_executes_nothing(world, op):
    proposal = prepared(world)
    McpWriteProposal.objects.filter(pk=proposal.pk).update(status=ProposalStatus.APPROVED)

    answer = _commit(world, proposal)

    assert isinstance(answer, Refusal) and answer.code == "approval_mismatch"
    assert title_of(world) == ORIGINAL and op.executions == 0


@pytest.mark.django_db
def test_an_expired_proposal_is_refused_and_marked_expired(world, op):
    proposal = prepared(world)
    McpWriteProposal.objects.filter(pk=proposal.pk).update(
        expires_at=timezone.now() - timedelta(seconds=1)
    )

    answer = _commit(world, proposal)

    assert isinstance(answer, Refusal) and answer.code == "expired" and answer.status == 410
    proposal.refresh_from_db()
    assert proposal.status == ProposalStatus.EXPIRED
    assert _events(proposal)[-1] == McpWriteEventType.EXPIRED
    assert title_of(world) == ORIGINAL
    assert _commit(world, proposal).code == "expired"


@pytest.mark.django_db
def test_an_expired_approval_is_refused(world, op):
    proposal = approve(prepared(world), seconds=-1)

    answer = _commit(world, proposal)

    assert isinstance(answer, Refusal) and answer.code == "expired"
    assert title_of(world) == ORIGINAL and op.executions == 0


@pytest.mark.django_db
def test_an_approval_keeps_the_proposal_alive_past_its_own_expiry(world, op):
    """A proposal waits `MCP_WRITE_PROPOSAL_SECONDS` for approval; then the approval counts."""
    proposal = approve(prepared(world))
    McpWriteProposal.objects.filter(pk=proposal.pk).update(
        expires_at=timezone.now() - timedelta(seconds=1)
    )
    assert _commit(world, proposal)["status"] == "executed"


@pytest.mark.django_db
def test_a_declined_proposal_is_refused(world, op):
    proposal = prepared(world)
    McpWriteProposal.objects.filter(pk=proposal.pk).update(status=ProposalStatus.DECLINED)
    answer = _commit(world, proposal)
    assert isinstance(answer, Refusal) and answer.code == "declined"
    assert title_of(world) == ORIGINAL


# -- tampering ----------------------------------------------------------------------


@pytest.mark.django_db
def test_canonical_arguments_edited_after_approval_are_refused_by_the_digest(world, op):
    proposal = approve(prepared(world))
    McpWriteProposal.objects.filter(pk=proposal.pk).update(
        canonical_arguments={"matter_id": str(world["matter"].pk), "title": "Kellegi teise tekst"}
    )

    answer = _commit(world, proposal)

    assert isinstance(answer, Refusal) and answer.code == "approval_mismatch"
    assert title_of(world) == ORIGINAL and op.executions == 0
    proposal.refresh_from_db()
    assert proposal.status == ProposalStatus.FAILED
    assert proposal.failure_code == "approval_mismatch"


@pytest.mark.django_db
def test_a_digest_rewritten_on_the_proposal_too_still_fails_the_recomputation(world, op):
    proposal = approve(prepared(world))
    McpWriteProposal.objects.filter(pk=proposal.pk).update(
        canonical_arguments={"matter_id": str(world["matter"].pk), "title": "Kellegi teise tekst"},
        arguments_digest="0" * 64,
    )
    answer = _commit(world, proposal)
    assert isinstance(answer, Refusal) and answer.code == "approval_mismatch"
    assert title_of(world) == ORIGINAL


@pytest.mark.django_db
def test_an_approval_of_other_arguments_approves_nothing(world, op):
    proposal = approve(prepared(world), digest="f" * 64)
    answer = _commit(world, proposal)
    assert isinstance(answer, Refusal) and answer.code == "approval_mismatch"
    assert title_of(world) == ORIGINAL and op.executions == 0


# -- staleness -------------------------------------------------------------------------


@pytest.mark.django_db
def test_a_record_that_moved_on_after_approval_makes_the_proposal_stale(world, op):
    proposal = approve(prepared(world))
    world["matter"].title = "Keegi muutis vahepeal"
    world["matter"].save(update_fields=["title"])

    answer = _commit(world, proposal)

    assert isinstance(answer, Refusal) and answer.code == "stale" and answer.status == 409
    assert title_of(world) == "Keegi muutis vahepeal" and op.executions == 0
    proposal.refresh_from_db()
    assert proposal.status == ProposalStatus.STALE
    assert _commit(world, proposal).code == "stale"


# -- failures leave nothing partial ---------------------------------------------------


@pytest.mark.django_db
def test_an_interrupted_execute_leaves_nothing_and_can_be_retried(world, op):
    proposal = approve(prepared(world))
    op.fail_after_write = RuntimeError("katkes")

    answer = _commit(world, proposal)

    assert isinstance(answer, Refusal) and answer.code == "internal_error"
    assert "katkes" not in str(answer.as_json())
    assert title_of(world) == ORIGINAL and renames() == 0
    proposal.refresh_from_db()
    assert proposal.status == ProposalStatus.APPROVED
    failed = McpWriteEvent.objects.filter(proposal=proposal, event_type=McpWriteEventType.FAILED)
    assert failed.get().detail["retryable"] is True

    op.fail_after_write = None
    again = _commit(world, proposal)
    assert again["status"] == "executed" and title_of(world) == RENAMED and renames() == 1


@pytest.mark.django_db
def test_a_domain_refusal_in_execute_rolls_back_and_fails_the_proposal(world, op):
    proposal = approve(prepared(world))
    op.refuse_after_write = True

    answer = _commit(world, proposal)

    assert isinstance(answer, Refusal) and answer.code == "domain_rule"
    assert answer.message == "The Matter is closed."
    assert title_of(world) == ORIGINAL and renames() == 0
    proposal.refresh_from_db()
    assert proposal.status == ProposalStatus.FAILED and proposal.failure_code == "domain_rule"
    op.refuse_after_write = False
    assert _commit(world, proposal).code == "already_failed"
    assert title_of(world) == ORIGINAL


# -- the principal ----------------------------------------------------------------------


@pytest.mark.django_db
def test_another_users_token_is_told_nothing_exists(world, op):
    proposal = approve(prepared(world))
    stranger = factories.UserFactory()
    _grant, _raw, stranger_access = support.writer(stranger)

    answer = commit(access=stranger_access, user=stranger, proposal_id=str(proposal.pk))
    unknown = commit(access=stranger_access, user=stranger, proposal_id=str(uuid.uuid4()))

    assert isinstance(answer, Refusal) and answer.as_json() == unknown.as_json()
    assert answer.code == "not_found" and answer.status == 404
    assert title_of(world) == ORIGINAL and op.executions == 0
    proposal.refresh_from_db()
    assert proposal.status == ProposalStatus.APPROVED
    refused = McpWriteEvent.objects.filter(proposal=proposal, user=stranger).get()
    assert refused.detail == {"code": "not_found", "reason": "principal_mismatch"}


@pytest.mark.django_db
@pytest.mark.parametrize(
    "overrides",
    [{"client_id": "https://other.example.test/client.json"}, {"family_id": uuid.uuid4()}],
)
def test_the_same_person_through_another_client_or_token_family_is_told_nothing(
    world, op, overrides
):
    proposal = approve(prepared(world))
    answer = _commit(world, proposal, **overrides)
    assert isinstance(answer, Refusal) and answer.code == "not_found"
    assert title_of(world) == ORIGINAL


@pytest.mark.django_db
def test_a_malformed_proposal_id_is_not_found(world):
    for raw in ("", "not-a-uuid", "x" * 500, None, 7):
        answer = commit(access=world["access"], user=world["user"], proposal_id=raw)
        assert isinstance(answer, Refusal) and answer.code == "not_found"


# -- rights withdrawn between approval and commit -----------------------------------


@pytest.mark.django_db
def test_revoke_write_between_approval_and_commit_is_honoured(world, op, stand_ins):
    proposal = approve(prepared(world))
    stand_ins.write_revoked.add(world["grant"].pk)

    answer = _commit(world, proposal)

    assert isinstance(answer, Refusal) and answer.code == "forbidden"
    assert title_of(world) == ORIGINAL and op.executions == 0


@pytest.mark.django_db
def test_a_revoked_grant_between_approval_and_commit_is_honoured(world, op):
    proposal = approve(prepared(world))
    world["grant"].status = GrantStatus.REVOKED
    world["grant"].save(update_fields=["status"])
    answer = _commit(world, proposal)
    assert isinstance(answer, Refusal) and answer.code == "forbidden"
    assert title_of(world) == ORIGINAL


@pytest.mark.django_db
def test_a_disabled_account_between_approval_and_commit_is_honoured(world, op):
    proposal = approve(prepared(world))
    world["user"].is_active = False
    world["user"].save(update_fields=["is_active"])
    answer = _commit(world, proposal)
    assert isinstance(answer, Refusal) and answer.code == "forbidden"
    assert title_of(world) == ORIGINAL


@pytest.mark.django_db
def test_with_writes_switched_off_nothing_commits(world, op, settings):
    proposal = approve(prepared(world))
    settings.MCP_WRITES_ENABLED = False
    answer = _commit(world, proposal)
    assert isinstance(answer, Refusal) and answer.code == "writes_disabled"
    assert title_of(world) == ORIGINAL


# -- two commits at once ------------------------------------------------------------------


@pytest.mark.django_db(transaction=True, serialized_rollback=True)
def test_two_concurrent_commits_execute_once(world, op):
    """Two threads, two connections (three with the test's own): one executes, one replays."""
    proposal = approve(prepared(world))
    barrier = threading.Barrier(2)
    answers: list[object] = []
    errors: list[BaseException] = []

    def commit_in_a_thread() -> None:
        try:
            barrier.wait(timeout=10)
            answers.append(_commit(world, proposal))
        except BaseException as error:  # pragma: no cover - surfaced below
            errors.append(error)
        finally:
            connection.close()

    threads = [threading.Thread(target=commit_in_a_thread) for _ in range(2)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=30)

    assert errors == []
    assert sorted(answer["replayed"] for answer in answers) == [False, True]  # type: ignore[index]
    assert renames() == 1 and op.executions == 1 and title_of(world) == RENAMED
