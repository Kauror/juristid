"""Look at MCP writes from the host: `manage.py mcp_writes list|show`.

The procedure around it is docs/mcp/APPROVAL-RUNBOOK.md.

    manage.py mcp_writes list [--user <upn>] [--status PENDING|…] [--limit 50]
    manage.py mcp_writes show <proposal id>

Read-only. Prints ids, the operation and its label, statuses, times, failure
codes, the target Matter's id and reference, and each event's type, outcome
and codes — **never** the arguments, the preview's text, a receipt's saved
values, a token or a code. To see what a change actually saved, open the
record in Juristid (its history names «ChatGPT kaudu, isiklikult kinnitatud»)
or look up the business audit rows by the correlation id printed here.
"""

from __future__ import annotations

import uuid
from typing import Any

from django.core.management.base import BaseCommand, CommandError
from django.utils import timezone

from app.accounts.models import User
from app.mcp_writes import operations
from app.mcp_writes.models import (
    McpWriteApproval,
    McpWriteEvent,
    McpWriteProposal,
    ProposalStatus,
)

DEFAULT_LIMIT = 50


def _label(operation: str) -> str:
    op = operations.get(operation)
    return str(op.label) if op is not None else "(tundmatu toiming)"


def _when(moment: Any) -> str:
    return timezone.localtime(moment).strftime("%Y-%m-%d %H:%M:%S") if moment else "-"


def _status(proposal: McpWriteProposal) -> str:
    if proposal.status == ProposalStatus.PENDING and proposal.is_expired_at(timezone.now()):
        return "PENDING (aegunud)"
    return str(proposal.status)


def _safe_detail(detail: Any) -> str:
    """Codes and ids only, as they were written; anything else is left out."""
    if not isinstance(detail, dict):
        return ""
    parts = []
    for key in sorted(detail):
        value = detail[key]
        if isinstance(value, str | int | bool) or value is None:
            parts.append(f"{key}={value}")
        elif isinstance(value, list) and all(isinstance(item, str) for item in value):
            parts.append(f"{key}={','.join(value)}")
    return " ".join(parts)


class Command(BaseCommand):
    help = "List MCP write proposals or show one with its events (read-only, no content)."

    def add_arguments(self, parser: Any) -> None:
        sub = parser.add_subparsers(dest="action", required=True)
        listing = sub.add_parser("list", help="Recent proposals, newest first.")
        listing.add_argument("--user", default="", help="Only this account (its UPN).")
        listing.add_argument(
            "--status", default="", choices=["", *ProposalStatus.values], help="Only this status."
        )
        listing.add_argument("--limit", type=int, default=DEFAULT_LIMIT)
        show = sub.add_parser("show", help="One proposal and its events.")
        show.add_argument("proposal_id")

    def handle(self, *args: Any, **options: Any) -> None:
        if options["action"] == "list":
            self._list(options)
        else:
            self._show(options["proposal_id"])

    def _list(self, options: dict[str, Any]) -> None:
        queryset = McpWriteProposal.objects.order_by("-created_at")
        if options["user"]:
            user = User.objects.filter(upn__iexact=options["user"].strip()).first()
            if user is None:
                raise CommandError("No account with that UPN.")
            queryset = queryset.filter(user=user)
        if options["status"]:
            queryset = queryset.filter(status=options["status"])
        limit = max(1, min(int(options["limit"]), 1000))
        rows = list(queryset[:limit])
        if not rows:
            self.stdout.write("No MCP write proposals.")
            return
        for proposal in rows:
            self.stdout.write(
                f"{proposal.pk}  {_when(proposal.created_at)}  {_status(proposal):<18} "
                f"{proposal.operation} ({_label(proposal.operation)})  user={proposal.user_id}"
                + (f"  failure={proposal.failure_code}" if proposal.failure_code else "")
            )

    def _show(self, raw: str) -> None:
        try:
            pid = uuid.UUID(str(raw))
        except ValueError:
            raise CommandError("Not a proposal id.") from None
        proposal = McpWriteProposal.objects.select_related("user").filter(pk=pid).first()
        if proposal is None:
            raise CommandError("No such proposal.")
        result = proposal.result if isinstance(proposal.result, dict) else {}
        preview = proposal.preview if isinstance(proposal.preview, dict) else {}
        target = preview.get("target") if isinstance(preview.get("target"), dict) else {}
        lines = [
            f"Proposal      {proposal.pk}",
            f"Operation     {proposal.operation} ({_label(proposal.operation)})",
            f"Status        {_status(proposal)}"
            + (f" (failure {proposal.failure_code})" if proposal.failure_code else ""),
            f"User          {proposal.user_id} ({proposal.user.upn})",
            f"Grant         {proposal.grant_id}",
            f"Client        {proposal.client_id}",
            f"Token family  {proposal.token_family_id or '-'}",
            f"Correlation   {proposal.correlation_id}",
            f"Target matter {proposal.target_matter_id or '-'} "
            f"{(target or {}).get('reference', '') if target else ''}".rstrip(),
            f"Target record {proposal.target_record_type or '-'} "
            f"{proposal.target_record_id}".rstrip(),
            f"Created       {_when(proposal.created_at)}",
            f"Expires       {_when(proposal.expires_at)}",
            f"Executed      {_when(proposal.executed_at)}",
            f"Saved record  {result.get('record_type', '-')} "
            f"{result.get('record_id', '')}".rstrip(),
        ]
        approval = McpWriteApproval.objects.filter(proposal=proposal).first()
        if approval is not None:
            lines.append(
                f"Approved      {_when(approval.approved_at)} via {approval.method}, "
                f"valid until {_when(approval.expires_at)}"
            )
        lines.append("Events:")
        for event in McpWriteEvent.objects.filter(proposal=proposal).order_by("created_at"):
            outcome = "ok" if event.succeeded else "refused"
            lines.append(
                f"  {_when(event.created_at)}  {event.event_type:<16} {outcome:<7} "
                f"user={event.user_id or '-'} {_safe_detail(event.detail)}".rstrip()
            )
        self.stdout.write("\n".join(lines))
