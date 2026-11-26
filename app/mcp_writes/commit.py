"""Commit: report what became of one proposal to the client that prepared it (ADR 0234).

`commit` is what `POST /v1/commit` runs, in a worker thread of the write
gateway, after the gateway has verified the bearer token and asked
`app.mcp_access` that its account may write.

**Since format 2 (SEC-009) the gateway executes nothing.** A change is saved
by Juristid's approval page (`app.mcp_writes.approval_views`), in the same
transaction as the person's approval, from the change the page itself derived
and showed (`app.mcp_writes.review`). The gateway's database role holds no
write privilege on any business table and cannot update a proposal at all
(`app.mcp_writes.db_role`), so nothing a compromised gateway does can turn an
unapproved proposal into a saved change. What `commit_prepared_action` keeps
is its interface and its answers:

1. the principal's commit budget is charged (`app.mcp_writes.budgets`); a
   call that finds the caller's own saved proposal is counted but never
   refused — the receipt of a saved change is always available;
2. **identity**: the token's user, grant, client and token family must be the
   proposal's. Anything else is answered exactly like a proposal that does not
   exist (``not_found``): nobody learns about somebody else's proposal;
3. an EXECUTED proposal answers its stored receipt: the first time with
   ``replayed`` false (`RECEIPT_DELIVERED`), afterwards with ``replayed`` true
   (`COMMIT_REPLAYED`, aggregated) — the change ran once, at approval;
4. a DECLINED / FAILED / STALE / EXPIRED one is refused with its code; a
   format-1 one (an older release's) is ``expired``; a PENDING one past its
   expiry is ``expired``; a PENDING one inside it answers
   ``awaiting_approval`` with the approval link.

Nothing here writes a proposal: statuses are moved only by the approval page.
"""

from __future__ import annotations

import logging
import uuid
from typing import Any

from django.db import connection, transaction
from django.utils import timezone

from app.mcp_writes import budgets
from app.mcp_writes.access import family_id, writes_enabled
from app.mcp_writes.models import (
    CURRENT_FORMAT,
    McpWriteEvent,
    McpWriteEventType,
    McpWriteProposal,
    ProposalStatus,
)
from app.mcp_writes.operations.base import Executed
from app.mcp_writes.proposals import approval_url, record, record_refusal
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
    """The receipt the approval page stores with an executed proposal."""
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


def _refuse(
    proposal: McpWriteProposal | None, code: str, *, user: Any, access: Any, **detail: Any
) -> Refusal:
    """Refuse; record it (aggregated). The proposal is never changed here."""
    record_refusal(
        McpWriteEventType.COMMIT_REFUSED,
        proposal=proposal,
        user=user if proposal is None else None,
        grant_id=access.grant_id if proposal is None else None,
        client_id=access.client_id if proposal is None else "",
        code=code,
        **detail,
    )
    return refusal(code)


def _deliver(proposal: McpWriteProposal) -> dict[str, Any]:
    """The receipt of a saved change: first delivery, or a replay."""
    with transaction.atomic():
        with connection.cursor() as cursor:
            # Two first fetches at once take turns: exactly one is «not replayed».
            cursor.execute(
                "SELECT pg_advisory_xact_lock(hashtextextended(%s, 0))",
                [f"mcp_writes.receipt:{proposal.pk}"],
            )
        delivered = McpWriteEvent.objects.filter(
            proposal=proposal, event_type=McpWriteEventType.RECEIPT_DELIVERED
        ).exists()
        if delivered:
            record_refusal(
                McpWriteEventType.COMMIT_REPLAYED,
                succeeded=True,
                counts_as_refused=False,
                proposal=proposal,
            )
        else:
            record(McpWriteEventType.RECEIPT_DELIVERED, proposal=proposal)
    return executed_answer(proposal, replayed=delivered)


def commit(*, access: Any, user: Any, proposal_id: Any) -> dict[str, Any] | Refusal:
    """Report a proposal's outcome to its own client; see the module docstring for every step."""
    pid = _parse_id(proposal_id)
    if pid is None:
        return refusal("not_found")
    if not writes_enabled():
        return refusal("writes_disabled")
    proposal = McpWriteProposal.objects.filter(pk=pid).first()
    own = proposal is not None and same_principal(proposal, access, user)

    if own and proposal is not None and proposal.status == ProposalStatus.EXECUTED:
        # Never refused: the change is saved and its receipt is the client's.
        budgets.count_only("commit", access=access, user=user)
        return _deliver(proposal)

    limited = budgets.admit("commit", access=access, user=user)
    if limited is not None:
        record_refusal(
            McpWriteEventType.COMMIT_REFUSED,
            counts_as_refused=False,
            user=user,
            grant_id=access.grant_id,
            client_id=access.client_id,
            code="rate_limited",
            reason=str(limited.extra.get("budget", "")),
        )
        return limited

    with transaction.atomic():
        if proposal is None:
            return _refuse(None, "not_found", user=user, access=access, reason="no_such_proposal")
        if not own:
            # Recorded against the proposal for the operator; the caller is
            # told exactly what a missing proposal tells it.
            record_refusal(
                McpWriteEventType.COMMIT_REFUSED,
                proposal=proposal,
                user=user,
                grant_id=access.grant_id,
                client_id=access.client_id,
                code="not_found",
                reason="principal_mismatch",
            )
            return refusal("not_found")
        if proposal.status in _FINAL_CODES:
            return _refuse(
                proposal, _FINAL_CODES[ProposalStatus(proposal.status)], user=user, access=access
            )
        if proposal.format_version != CURRENT_FORMAT:
            # Prepared under an older release's rules: never approved under these.
            return _refuse(proposal, "expired", user=user, access=access, reason="format")
        if proposal.status == ProposalStatus.APPROVED:
            # Format 2 approves and saves in one transaction: an APPROVED one
            # was not approved by this release's page.
            return _refuse(
                proposal, "approval_mismatch", user=user, access=access, reason="approved_unsaved"
            )
        if proposal.is_expired_at(timezone.now()):
            return _refuse(
                proposal, "expired", user=user, access=access, reason="not_approved_in_time"
            )
        record_refusal(
            McpWriteEventType.COMMIT_REFUSED,
            counts_as_refused=False,
            proposal=proposal,
            code="awaiting_approval",
        )
        return {
            "status": "awaiting_approval",
            "proposal_id": str(proposal.pk),
            "approval_url": approval_url(proposal.pk),
            "expires_at": proposal.expires_at.isoformat(),
        }
