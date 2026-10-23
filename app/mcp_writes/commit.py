"""Commit: execute one approved proposal, once, through the canonical services (CONTRACT §5).

`commit` is what `POST /v1/commit` runs, in a worker thread of the write
gateway, after the gateway has verified the bearer token and asked
`app.mcp_access` that its account may write. In **one transaction**:

1. the proposal row is locked (``SELECT … FOR UPDATE``) — two commits of one
   proposal take turns, and the second sees what the first did;
2. **identity**: the token's user, grant, client and token family must be the
   proposal's. Anything else is answered exactly like a proposal that does not
   exist (``not_found``): nobody learns about somebody else's proposal;
3. an EXECUTED proposal answers its stored receipt again (``replayed``,
   `COMMIT_REPLAYED`) — the commit is idempotent and the change ran once;
4. a DECLINED / FAILED / STALE / EXPIRED one is refused with its code; a
   PENDING one past its expiry becomes EXPIRED; a PENDING one inside it
   answers ``awaiting_approval`` with the approval link;
5. the **approval**: present, its user, grant and digest the proposal's, the
   digest recomputed from the stored canonical arguments equal to both, and
   the approval itself unexpired (`MCP_WRITE_APPROVAL_SECONDS`);
6. the account is asked again (`writer_for`) inside the transaction, so a
   revoke-write, a revoked grant or a switched-off account between the
   approval and the commit is honoured;
7. `op.lock`, then `op.state` under the lock, compared with the stored state —
   a difference is STALE;
8. a savepoint around `op.execute(once_token=proposal.id)`, under the MCP audit
   provenance (`app.audit.provenance.mcp_write_context`), so every business
   audit row it writes names the approved principal and the proposal's
   correlation id;
9. EXECUTED with the receipt and a `COMMITTED` event.

**Refusals are returned from inside the transaction, never raised through
it**, so the status a proposal moves to (FAILED, STALE, EXPIRED) and the event
that says why survive even though the business savepoint rolled back.
Nothing partial survives: a domain refusal or any exception inside `execute`
rolls back the savepoint, and only the proposal's own status and the event
remain. A domain refusal is final (FAILED). An unexpected error is not the
person's decision and not the proposal's fault: the proposal stays APPROVED
and may be committed again inside the approval's window; the event records
``internal_error``.
"""

from __future__ import annotations

import logging
import uuid
from typing import Any

from django.db import transaction
from django.utils import timezone

from app.audit.provenance import mcp_write_context
from app.core.errors import DomainError
from app.mcp_writes import operations
from app.mcp_writes.access import family_id, writer_for, writes_enabled
from app.mcp_writes.models import (
    McpWriteApproval,
    McpWriteEventType,
    McpWriteProposal,
    ProposalStatus,
)
from app.mcp_writes.operations.base import Executed, OperationRefused
from app.mcp_writes.proposals import approval_url, digest_of, record
from app.mcp_writes.refusals import Refusal, jsonable, refusal

logger = logging.getLogger("app.mcp_writes")

#: Statuses a refused commit answers with their own code.
_FINAL_CODES = {
    ProposalStatus.DECLINED: "declined",
    ProposalStatus.FAILED: "already_failed",
    ProposalStatus.STALE: "stale",
    ProposalStatus.EXPIRED: "expired",
}


def _parse_id(raw: Any) -> uuid.UUID | None:
    if not isinstance(raw, str) or len(raw) > 64:
        return None
    try:
        return uuid.UUID(raw)
    except ValueError:
        return None


def same_principal(proposal: McpWriteProposal, access: Any, user: Any) -> bool:
    """The token's user, grant, client and family are the proposal's own."""
    return (
        user is not None
        and proposal.user_id == user.pk
        and proposal.user_id == access.user_id
        and proposal.grant_id == access.grant_id
        and proposal.client_id == (access.client_id or "")[:500]
        and proposal.token_family_id == family_id(access)
    )


def receipt(executed: Executed) -> dict[str, Any]:
    from app.mcp_access import config

    url = executed.url or ""
    if url.startswith("/"):
        url = f"{config.issuer()}{url}"
    return dict(
        jsonable(
            {
                "record_type": executed.record_type,
                "record_id": executed.record_id,
                "matter_id": executed.matter_id,
                "matter_reference": executed.matter_reference,
                "url": url,
                "saved": executed.saved,
            }
        )
    )


def executed_answer(proposal: McpWriteProposal, *, replayed: bool) -> dict[str, Any]:
    return {
        "status": "executed",
        "replayed": replayed,
        "proposal_id": str(proposal.pk),
        "result": proposal.result,
    }


def _finish(
    proposal: McpWriteProposal,
    status: str,
    code: str,
    *,
    event: str,
    message: str = "",
    **detail: Any,
) -> Refusal:
    """Move the proposal to a final status, record why, and answer the refusal."""
    proposal.status = status
    proposal.failure_code = code[:32]
    proposal.save(update_fields=["status", "failure_code", "updated_at"])
    record(event, succeeded=False, proposal=proposal, code=code, **detail)
    return refusal(code, message)


def _refuse(
    proposal: McpWriteProposal | None, code: str, *, user: Any, access: Any, **detail: Any
) -> Refusal:
    """Refuse without changing the proposal; record it."""
    record(
        McpWriteEventType.COMMIT_REFUSED,
        succeeded=False,
        proposal=proposal,
        user=user if proposal is None else None,
        grant_id=access.grant_id if proposal is None else None,
        client_id=access.client_id if proposal is None else "",
        code=code,
        **detail,
    )
    return refusal(code)


def _approval_holds(proposal: McpWriteProposal, approval: McpWriteApproval) -> bool:
    recomputed = digest_of(proposal)
    return (
        approval.user_id == proposal.user_id
        and approval.grant_id == proposal.grant_id
        and approval.arguments_digest == proposal.arguments_digest
        and recomputed == proposal.arguments_digest
    )


def commit(*, access: Any, user: Any, proposal_id: Any) -> dict[str, Any] | Refusal:
    """Execute an approved proposal once; see the module docstring for every step."""
    pid = _parse_id(proposal_id)
    if pid is None:
        return refusal("not_found")
    if not writes_enabled():
        return refusal("writes_disabled")
    with transaction.atomic():
        proposal = McpWriteProposal.objects.select_for_update().filter(pk=pid).first()
        if proposal is None:
            return _refuse(None, "not_found", user=user, access=access, reason="no_such_proposal")
        if not same_principal(proposal, access, user):
            # Recorded against the proposal for the operator; the caller is
            # told exactly what a missing proposal tells it.
            record(
                McpWriteEventType.COMMIT_REFUSED,
                succeeded=False,
                proposal=proposal,
                user=user,
                grant_id=access.grant_id,
                client_id=access.client_id,
                code="not_found",
                reason="principal_mismatch",
            )
            return refusal("not_found")

        if proposal.status == ProposalStatus.EXECUTED:
            record(McpWriteEventType.COMMIT_REPLAYED, proposal=proposal)
            return executed_answer(proposal, replayed=True)
        if proposal.status in _FINAL_CODES:
            return _refuse(
                proposal, _FINAL_CODES[ProposalStatus(proposal.status)], user=user, access=access
            )

        now = timezone.now()
        approval = McpWriteApproval.objects.filter(proposal=proposal).first()
        if approval is None:
            if proposal.status == ProposalStatus.PENDING and proposal.is_expired_at(now):
                return _finish(
                    proposal,
                    ProposalStatus.EXPIRED,
                    "expired",
                    event=McpWriteEventType.EXPIRED,
                    reason="not_approved_in_time",
                )
            if proposal.status == ProposalStatus.PENDING:
                record(
                    McpWriteEventType.COMMIT_REFUSED,
                    succeeded=False,
                    proposal=proposal,
                    code="awaiting_approval",
                )
                return {
                    "status": "awaiting_approval",
                    "proposal_id": str(proposal.pk),
                    "approval_url": approval_url(proposal.pk),
                    "expires_at": proposal.expires_at.isoformat(),
                }
            # APPROVED with no approval row: the row was tampered with.
            return _finish(
                proposal,
                ProposalStatus.FAILED,
                "approval_mismatch",
                event=McpWriteEventType.COMMIT_REFUSED,
                reason="no_approval_row",
            )
        if not _approval_holds(proposal, approval):
            return _finish(
                proposal,
                ProposalStatus.FAILED,
                "approval_mismatch",
                event=McpWriteEventType.COMMIT_REFUSED,
                reason="digest_or_principal",
            )
        if approval.is_expired_at(now):
            return _finish(
                proposal,
                ProposalStatus.EXPIRED,
                "expired",
                event=McpWriteEventType.EXPIRED,
                reason="approval_expired",
            )

        # The account is asked again here, inside the transaction: a
        # revoke-write, a revoked grant, a lost business role or a
        # switched-off account since the approval ends it now.
        writer = writer_for(access)
        if writer is None or writer.pk != proposal.user_id:
            return _refuse(
                proposal, "forbidden", user=user, access=access, reason="no_longer_writer"
            )

        op = operations.get(proposal.operation)
        if op is None:
            return _finish(
                proposal,
                ProposalStatus.FAILED,
                "domain_rule",
                event=McpWriteEventType.FAILED,
                reason="unknown_operation",
            )
        canonical = proposal.canonical_arguments
        try:
            op.lock(canonical=canonical)
            current = jsonable(op.state(user=writer, canonical=canonical))
        except (OperationRefused, DomainError) as error:
            code = getattr(error, "code", "domain_rule")
            return _finish(
                proposal,
                ProposalStatus.STALE,
                "stale",
                event=McpWriteEventType.COMMIT_REFUSED,
                reason=f"state_{code}",
            )
        if current != proposal.state:
            return _finish(
                proposal,
                ProposalStatus.STALE,
                "stale",
                event=McpWriteEventType.COMMIT_REFUSED,
                reason="state_changed",
            )

        try:
            with transaction.atomic(), mcp_write_context(writer, proposal.correlation_id):
                executed = op.execute(user=writer, canonical=canonical, once_token=proposal.pk)
                stored = receipt(executed)
        except OperationRefused as error:
            return _finish(
                proposal,
                ProposalStatus.FAILED,
                error.code,
                event=McpWriteEventType.FAILED,
                message=error.message,
            )
        except DomainError:
            return _finish(
                proposal, ProposalStatus.FAILED, "domain_rule", event=McpWriteEventType.FAILED
            )
        except Exception as error:
            # Not a refusal: nothing was saved (the savepoint is gone) and the
            # approval still stands, so the client may try again inside its
            # window. The class only — a message could quote data.
            logger.error(
                "MCP write %s (%s) failed in execute: %s",
                proposal.pk,
                proposal.operation,
                type(error).__name__,
            )
            record(
                McpWriteEventType.FAILED,
                succeeded=False,
                proposal=proposal,
                code="internal_error",
                retryable=True,
                error=type(error).__name__,
            )
            return refusal("internal_error")

        proposal.status = ProposalStatus.EXECUTED
        proposal.executed_at = timezone.now()
        proposal.result = stored
        proposal.failure_code = ""
        proposal.save(
            update_fields=["status", "executed_at", "result", "failure_code", "updated_at"]
        )
        record(
            McpWriteEventType.COMMITTED,
            proposal=proposal,
            record_type=stored.get("record_type", ""),
            record_id=stored.get("record_id", ""),
        )
    return executed_answer(proposal, replayed=False)
