"""Approve-and-save, and what the gateway's commit reports (ADR 0189, amended by 0234).

Since format 2 a change is saved by the approval page's service
(`app.mcp_writes.approval.approve_and_save`, on `web`) in the same transaction
as the person's approval, from the change it derived itself; the gateway's
`app.mcp_writes.commit.commit` executes nothing and reports the stored
receipt. With the stand-in rename operation
(`tests/test_mcp_write_proposals_support.py`); the page itself is
`tests/test_mcp_write_approval.py`. This module is also run as the writer role
(tests/test_mcp_writer_role_runs_commit.py): `commit` must work with a role
that cannot write a business table or update a proposal.
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
from app.mcp_access.models import GrantStatus, McpAccessGrant, McpToken
from app.mcp_writes import approval, proposals, review
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
NO_BINDING = "0" * 64


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


def approve(proposal: McpWriteProposal, *, binding: str | None = None) -> approval.Checked:
    """What the approval page does after the person's code was accepted: approve and save."""
    if binding is None:
        binding = review.derive(proposal, proposal.user).binding
    return approval.approve_and_save(
        proposal_id=proposal.pk,
        user=proposal.user,
        binding=binding,
        method=ApprovalMethod.TOTP,
        ip="127.0.0.1",
    )


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


def forged(world, **fields) -> McpWriteProposal:
    """A proposal row inserted directly — what a gateway could insert (PENDING, by the trigger)."""
    source = prepared(world)
    values = {
        field.attname: getattr(source, field.attname)
        for field in McpWriteProposal._meta.concrete_fields
        if field.attname not in ("id", "created_at", "updated_at", "correlation_id")
    }
    values.update(fields)
    return McpWriteProposal.objects.create(id=uuid.uuid4(), correlation_id=uuid.uuid4(), **values)


# -- the happy path and the receipt --------------------------------------------------


@pytest.mark.django_db
def test_an_approval_saves_through_the_canonical_service_and_commit_reports_it(world, op):
    proposal = prepared(world)

    outcome = approve(proposal)

    assert outcome.problem == ""
    proposal.refresh_from_db()
    assert proposal.status == ProposalStatus.EXECUTED and proposal.executed_at is not None
    assert title_of(world) == RENAMED and renames() == 1 and op.executions == 1
    recorded = McpWriteApproval.objects.get(proposal=proposal)
    assert outcome.reviewed is not None
    assert recorded.format_version == 2 and recorded.binding_digest == outcome.reviewed.binding
    assert recorded.canonical_arguments == proposal.canonical_arguments
    assert recorded.preview["summary"].startswith("Muuda teema")
    event = ChangeEvent.objects.get(event_type=ChangeEventType.MATTER_TITLE_CHANGED)
    assert event.correlation_id == proposal.correlation_id

    answer = _commit(world, proposal)

    assert answer["status"] == "executed" and answer["replayed"] is False
    assert answer["result"]["record_type"] == "matter"
    assert _events(proposal) == [
        McpWriteEventType.PREPARED,
        McpWriteEventType.APPROVED,
        McpWriteEventType.COMMITTED,
        McpWriteEventType.RECEIPT_DELIVERED,
    ]


@pytest.mark.django_db
def test_a_repeated_commit_returns_the_same_receipt_and_saves_nothing_again(world, op):
    proposal = prepared(world)
    approve(proposal)
    first = _commit(world, proposal)
    second = _commit(world, proposal)
    assert first["result"] == second["result"]
    assert (first["replayed"], second["replayed"]) == (False, True)
    assert renames() == 1 and op.executions == 1
    assert McpWriteEvent.objects.filter(
        proposal=proposal, event_type=McpWriteEventType.COMMIT_REPLAYED
    ).exists()


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
def test_an_approved_status_from_an_older_release_executes_nothing(world, op):
    proposal = prepared(world)
    McpWriteProposal.objects.filter(pk=proposal.pk).update(status=ProposalStatus.APPROVED)
    answer = _commit(world, proposal)
    assert isinstance(answer, Refusal) and answer.code == "approval_mismatch"
    assert title_of(world) == ORIGINAL and op.executions == 0


@pytest.mark.django_db
def test_a_format_one_proposal_is_expired_and_never_approved(world, op):
    proposal = forged(world, format_version=1)
    answer = _commit(world, proposal)
    assert isinstance(answer, Refusal) and answer.code == "expired"
    assert approve(proposal, binding=NO_BINDING).problem == "format"
    proposal.refresh_from_db()
    assert proposal.status == ProposalStatus.EXPIRED
    assert title_of(world) == ORIGINAL and not McpWriteApproval.objects.exists()


@pytest.mark.django_db
def test_an_expired_proposal_is_refused_and_never_approved(world, op, settings):
    settings.MCP_WRITE_PROPOSAL_SECONDS = 0
    proposal = prepared(world)
    answer = _commit(world, proposal)
    assert isinstance(answer, Refusal) and answer.code == "expired"
    proposal.refresh_from_db()
    assert proposal.status == ProposalStatus.PENDING  # the gateway moves nothing
    assert approve(proposal, binding=NO_BINDING).problem == "expired"
    proposal.refresh_from_db()
    assert proposal.status == ProposalStatus.EXPIRED and title_of(world) == ORIGINAL


@pytest.mark.django_db
def test_a_declined_proposal_is_refused(world, op):
    proposal = prepared(world)
    McpWriteProposal.objects.filter(pk=proposal.pk).update(
        status=ProposalStatus.DECLINED, failure_code="declined"
    )
    answer = _commit(world, proposal)
    assert isinstance(answer, Refusal) and answer.code == "declined"
    assert title_of(world) == ORIGINAL


# -- what the approval is bound to ----------------------------------------------------------


@pytest.mark.django_db
def test_a_binding_from_another_preview_approves_nothing(world, op):
    proposal = prepared(world)
    assert approve(proposal, binding="f" * 64).problem == "binding_changed"
    proposal.refresh_from_db()
    assert proposal.status == ProposalStatus.PENDING
    assert not McpWriteApproval.objects.exists() and title_of(world) == ORIGINAL


@pytest.mark.django_db
def test_the_binding_covers_every_bound_field(world, op):
    proposal = prepared(world)
    reviewed = review.derive(proposal, world["user"])
    base = reviewed.binding
    shown = {
        "canonical": reviewed.canonical,
        "state": reviewed.state,
        "preview": reviewed.preview,
    }
    for name, value in (
        ("preview", {**reviewed.preview, "summary": "muu"}),
        ("state", {"title": "muu"}),
        ("canonical", {**reviewed.canonical, "title": "muu"}),
    ):
        assert review.binding_digest(proposal, **{**shown, name: value}) != base, name
    for attribute, value in (
        ("client_id", "other"),
        ("token_family_id", uuid.uuid4()),
        ("grant_id", uuid.uuid4()),
        ("user_id", uuid.uuid4()),
        ("target_matter_id", uuid.uuid4()),
        ("target_record_id", "other"),
        ("expires_at", proposal.expires_at + timedelta(seconds=1)),
        ("operation", "other"),
        ("arguments", {"title": "muu"}),
        ("id", uuid.uuid4()),
    ):
        changed = McpWriteProposal.objects.get(pk=proposal.pk)
        setattr(changed, attribute, value)
        assert review.binding_digest(changed, **shown) != base, attribute


@pytest.mark.django_db
def test_stored_canonical_arguments_that_the_arguments_do_not_give_fail_the_proposal(world, op):
    """A gateway-inserted row whose canonical arguments say something else: never approved."""
    proposal = forged(
        world, canonical_arguments={"matter_id": str(world["matter"].pk), "title": "Muu"}
    )
    assert review.derive(proposal, world["user"]).problem == "mismatch"
    assert approve(proposal, binding=NO_BINDING).problem == "mismatch"
    proposal.refresh_from_db()
    assert proposal.status == ProposalStatus.FAILED and proposal.failure_code == "approval_mismatch"
    assert title_of(world) == ORIGINAL and op.executions == 0


@pytest.mark.django_db
def test_a_stored_target_the_arguments_do_not_give_fails_the_proposal(world, op):
    other = factories.MatterFactory(owner=world["user"])
    proposal = forged(world, target_matter_id=other.pk, target_record_id=str(other.pk))
    assert approve(proposal, binding=NO_BINDING).problem == "mismatch"
    assert title_of(world) == ORIGINAL and op.executions == 0


@pytest.mark.django_db
def test_a_record_that_moved_on_before_approval_makes_the_proposal_stale(world, op):
    proposal = prepared(world)
    binding = review.derive(proposal, world["user"]).binding
    world["matter"].title = "Keegi muutis"
    world["matter"].save(update_fields=["title"])
    assert approve(proposal, binding=binding).problem == "stale"
    proposal.refresh_from_db()
    assert proposal.status == ProposalStatus.STALE and op.executions == 0


# -- failures while saving ------------------------------------------------------------------


@pytest.mark.django_db
def test_an_interrupted_save_leaves_nothing_and_can_be_approved_again(world, op):
    proposal = prepared(world)
    op.fail_after_write = RuntimeError("connection lost")
    with pytest.raises(RuntimeError, match="connection lost"):
        approve(proposal)
    proposal.refresh_from_db()
    assert proposal.status == ProposalStatus.PENDING
    assert not McpWriteApproval.objects.exists() and title_of(world) == ORIGINAL

    op.fail_after_write = None
    assert approve(proposal).problem == ""
    proposal.refresh_from_db()
    assert proposal.status == ProposalStatus.EXECUTED and title_of(world) == RENAMED


@pytest.mark.django_db
def test_a_domain_refusal_while_saving_rolls_back_and_fails_the_proposal(world, op):
    proposal = prepared(world)
    op.refuse_after_write = True
    approve(proposal)
    proposal.refresh_from_db()
    assert proposal.status == ProposalStatus.FAILED and proposal.failure_code == "domain_rule"
    assert title_of(world) == ORIGINAL and renames() == 0
    assert McpWriteApproval.objects.filter(proposal=proposal).exists()  # the person approved
    answer = _commit(world, proposal)
    assert isinstance(answer, Refusal) and answer.code == "already_failed"


# -- whose proposal -------------------------------------------------------------------------


@pytest.mark.django_db
def test_another_users_token_is_told_nothing_exists(world, op):
    proposal = prepared(world)
    approve(proposal)
    stranger = factories.UserFactory()
    _grant, _raw, access = support.writer(stranger)
    answer = commit(access=access, user=stranger, proposal_id=str(proposal.pk))
    missing = commit(access=access, user=stranger, proposal_id=str(uuid.uuid4()))
    assert isinstance(answer, Refusal) and answer.code == "not_found"
    assert isinstance(missing, Refusal) and answer.as_json() == missing.as_json()


@pytest.mark.django_db
@pytest.mark.parametrize("field", ["client_id", "family_id"])
def test_the_same_person_through_another_client_or_token_family_is_told_nothing(world, op, field):
    proposal = prepared(world)
    approve(proposal)
    override = {field: "other-client" if field == "client_id" else uuid.uuid4()}
    answer = _commit(world, proposal, **override)
    assert isinstance(answer, Refusal) and answer.code == "not_found"


@pytest.mark.django_db
def test_a_malformed_proposal_id_is_not_found(world):
    for raw in ("", "nope", "x" * 100, None, 7):
        answer = commit(access=world["access"], user=world["user"], proposal_id=raw)
        assert isinstance(answer, Refusal) and answer.code == "not_found"


# -- what changed between prepare and approval ----------------------------------------------


@pytest.mark.django_db
def test_revoke_write_before_approval_is_honoured(world, op, stand_ins):
    proposal = prepared(world)
    stand_ins.write_revoked.add(world["grant"].pk)
    assert approve(proposal, binding=NO_BINDING).problem == "no_write_right"
    assert title_of(world) == ORIGINAL and not McpWriteApproval.objects.exists()


@pytest.mark.django_db
def test_a_revoked_grant_before_approval_is_honoured(world, op):
    proposal = prepared(world)
    McpAccessGrant.objects.filter(pk=world["grant"].pk).update(status=GrantStatus.REVOKED)
    assert approve(proposal, binding=NO_BINDING).problem == "no_write_right"
    assert title_of(world) == ORIGINAL


@pytest.mark.django_db
def test_a_disabled_account_before_approval_is_honoured(world, op):
    proposal = prepared(world)
    type(world["user"]).objects.filter(pk=world["user"].pk).update(is_active=False)
    proposal.user.refresh_from_db()
    assert approve(proposal, binding=NO_BINDING).problem == "no_write_right"
    assert title_of(world) == ORIGINAL


@pytest.mark.django_db
def test_a_revoked_token_family_approves_nothing_it_prepared(world, op):
    proposal = prepared(world)
    McpToken.objects.filter(family_id=proposal.token_family_id).update(
        revoked_at=timezone.now(), revoke_reason="refresh_reused"
    )
    assert approve(proposal, binding=NO_BINDING).problem == "disconnected"
    proposal.refresh_from_db()
    assert proposal.status == ProposalStatus.PENDING and title_of(world) == ORIGINAL


@pytest.mark.django_db
def test_with_writes_switched_off_nothing_is_reported_or_saved(world, op, settings):
    proposal = prepared(world)
    settings.MCP_WRITES_ENABLED = False
    answer = _commit(world, proposal)
    assert isinstance(answer, Refusal) and answer.code == "writes_disabled"
    assert approve(proposal, binding=NO_BINDING).problem == "no_write_right"
    assert title_of(world) == ORIGINAL


# -- two at once ----------------------------------------------------------------------------


def _twice(work) -> tuple[list[object], list[BaseException]]:
    """Run ``work`` in two threads at once (two connections; three with the test's own)."""
    barrier = threading.Barrier(2)
    results: list[object] = []
    errors: list[BaseException] = []

    def run() -> None:
        try:
            barrier.wait(timeout=10)
            results.append(work())
        except BaseException as error:  # pragma: no cover - surfaced below
            errors.append(error)
        finally:
            connection.close()

    threads = [threading.Thread(target=run) for _ in range(2)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=30)
    return results, errors


@pytest.mark.django_db(transaction=True, serialized_rollback=True)
def test_two_concurrent_approvals_save_once(world, op):
    proposal = prepared(world)
    binding = review.derive(proposal, world["user"]).binding
    outcomes, errors = _twice(lambda: approve(proposal, binding=binding))
    assert errors == []
    assert sorted(outcome.problem for outcome in outcomes) == ["", "not_pending"]  # type: ignore[attr-defined]
    assert renames() == 1 and op.executions == 1 and title_of(world) == RENAMED
    assert McpWriteApproval.objects.filter(proposal=proposal).count() == 1


@pytest.mark.django_db(transaction=True, serialized_rollback=True)
def test_two_concurrent_receipt_fetches_report_one_first_delivery(world, op):
    proposal = prepared(world)
    approve(proposal)
    answers, errors = _twice(lambda: _commit(world, proposal))
    assert errors == []
    assert sorted(answer["replayed"] for answer in answers) == [False, True]  # type: ignore[index]
    assert renames() == 1 and op.executions == 1
