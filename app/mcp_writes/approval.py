"""Approve and save one MCP proposal: the trusted executor (SEC-009, docs/adr/0234 §1).

Called only by the approval page (`app.mcp_writes.approval_views`) on `web`,
after the person's own authenticator code was accepted — never by the write
gateway, whose database role cannot write any table this touches except
through `web`. Kept apart from the view so that what it guarantees can be
tested without a browser:

`check` locks the proposal row, asks everything that needs no derivation
(pending, unexpired, format, write right, live connection), optionally takes
the operation's canonical locks, and derives the change afresh from the
stored arguments (`app.mcp_writes.review.derive`).

`approve_and_save` does `check` with the canonical locks, compares the binding
digest the person's form carried with the one derived now, and only then — in
the same transaction — writes the format-2 `McpWriteApproval`, runs the
operation's canonical service in a savepoint under the MCP audit provenance,
and marks the proposal EXECUTED with its receipt (or FAILED with the domain
refusal's code, the business savepoint rolled back). An unexpected exception
propagates: the caller's transaction is rolled back whole, so no approval
exists without its outcome.
"""

from __future__ import annotations

import hmac
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from django.db import transaction
from django.utils import timezone

from app.audit.provenance import mcp_write_context
from app.core.errors import DomainError
from app.mcp_access.models import GrantStatus
from app.mcp_writes import access, operations, review
from app.mcp_writes.commit import receipt
from app.mcp_writes.models import (
    CURRENT_FORMAT,
    McpWriteApproval,
    McpWriteEventType,
    McpWriteProposal,
    ProposalStatus,
)
from app.mcp_writes.operations.base import OperationRefused
from app.mcp_writes.proposals import record

#: The final status a problem leaves, under the proposal's lock.
SETTLES = {
    "expired": ProposalStatus.EXPIRED,
    "format": ProposalStatus.EXPIRED,
    "mismatch": ProposalStatus.FAILED,
    "unknown_operation": ProposalStatus.FAILED,
    "stale": ProposalStatus.STALE,
}


@dataclass
class Checked:
    """A locked proposal and why it may not be approved ("" when it may)."""

    proposal: McpWriteProposal
    problem: str = ""
    reason: str = ""
    reviewed: review.Reviewed | None = None


def problem_of(proposal: McpWriteProposal, user: Any, now: datetime) -> str:
    """Why this proposal may not be approved now (no derivation), or ""."""
    if proposal.status != ProposalStatus.PENDING:
        return "not_pending"
    if proposal.is_expired_at(now):
        return "expired"
    if proposal.format_version != CURRENT_FORMAT:
        return "format"
    if proposal.grant.status != GrantStatus.ACTIVE or not access.may_write(user, proposal.grant):
        return "no_write_right"
    if not access.family_live(proposal):
        return "disconnected"
    return ""


def check(proposal_id: Any, user: Any, *, canonical_locks: bool) -> Checked:
    """Inside a transaction: lock the proposal, check, (lock the records,) derive."""
    locked = (
        McpWriteProposal.objects.select_for_update().select_related("grant").get(pk=proposal_id)
    )
    problem = problem_of(locked, user, timezone.now())
    if problem:
        return Checked(locked, problem)
    op = operations.get(locked.operation)
    if op is None:
        return Checked(locked, "unknown_operation")
    if canonical_locks:
        try:
            op.lock(canonical=locked.canonical_arguments)
        except (OperationRefused, DomainError):
            return Checked(locked, "stale", "lock_refused")
    reviewed = review.derive(locked, user)
    if reviewed.problem:
        return Checked(locked, reviewed.problem, reviewed.reason, reviewed)
    return Checked(locked, reviewed=reviewed)


def settle(checked: Checked) -> None:
    """Under the proposal's lock: the final status a problem leaves, and its event."""
    locked = checked.proposal
    final = SETTLES.get(checked.problem)
    if final is not None:
        locked.status = final
        locked.failure_code = (
            "approval_mismatch" if final == ProposalStatus.FAILED else checked.problem
        )
        locked.save(update_fields=["status", "failure_code", "updated_at"])
    detail: dict[str, Any] = {"code": checked.problem}
    if checked.reason:
        detail["reason"] = checked.reason
    record(McpWriteEventType.APPROVAL_REFUSED, succeeded=False, proposal=locked, **detail)


def approve_and_save(
    *, proposal_id: Any, user: Any, binding: str, method: str, ip: str | None
) -> Checked:
    """The approval and the save, in one transaction, under every lock, checked again.

    Answers the outcome: a problem (settled, or ``binding_changed`` — nothing
    written but the event), or the proposal EXECUTED / FAILED."""
    with transaction.atomic():
        checked = check(proposal_id, user, canonical_locks=True)
        locked, reviewed = checked.proposal, checked.reviewed
        if checked.problem:
            settle(checked)
            return checked
        if reviewed is None or not hmac.compare_digest(str(binding), reviewed.binding):
            record(
                McpWriteEventType.APPROVAL_REFUSED,
                succeeded=False,
                proposal=locked,
                code="binding_changed",
                reason="under_lock",
            )
            checked.problem = "binding_changed"
            return checked
        now = timezone.now()
        McpWriteApproval.objects.create(
            proposal=locked,
            user=user,
            grant=locked.grant,
            arguments_digest=locked.arguments_digest,
            format_version=CURRENT_FORMAT,
            binding_digest=reviewed.binding,
            canonical_arguments=reviewed.canonical,
            state=reviewed.state,
            preview=reviewed.preview,
            approved_at=now,
            # Approved and saved at once: the approval waits for nothing.
            expires_at=now,
            method=method,
            ip_address=ip,
        )
        record(McpWriteEventType.APPROVED, proposal=locked, method=str(method))
        op = operations.REGISTRY[locked.operation]
        try:
            with transaction.atomic(), mcp_write_context(user, locked.correlation_id):
                executed = op.execute(user=user, canonical=reviewed.canonical, once_token=locked.pk)
                stored = receipt(executed)
        except OperationRefused as error:
            _failed(locked, error.code)
        except DomainError:
            _failed(locked, "domain_rule")
        else:
            locked.status = ProposalStatus.EXECUTED
            locked.executed_at = timezone.now()
            locked.result = stored
            locked.save(update_fields=["status", "executed_at", "result", "updated_at"])
            record(
                McpWriteEventType.COMMITTED,
                proposal=locked,
                record_type=stored.get("record_type", ""),
                record_id=stored.get("record_id", ""),
            )
    return checked


def _failed(locked: McpWriteProposal, code: str) -> None:
    locked.status = ProposalStatus.FAILED
    locked.failure_code = code[:32]
    locked.save(update_fields=["status", "failure_code", "updated_at"])
    record(McpWriteEventType.FAILED, succeeded=False, proposal=locked, code=code)
