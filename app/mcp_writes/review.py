"""The trusted review of an MCP proposal: what the person sees is derived here (SEC-009).

The write gateway (`mcp-writer`) is reachable, through `mcp`, from the AI
client's side. Everything it stores in a proposal row — the canonical
arguments, the state, the preview — is therefore *writer-controlled* and is
never shown to the person or executed as stored (docs/adr/0234).

`derive` is what the approval page (`web`, the trusted component) runs
instead. From the proposal's validated ``arguments`` it calls the operation's
own ``prepare`` again — in a savepoint that is always rolled back, as the
gateway does — under the person's own visibility, and gets the canonical
arguments, the state and the preview *afresh*. Then:

* the fresh canonical arguments and target must equal the stored ones, or the
  proposal does not say what it was prepared to say (``mismatch`` — a row that
  was tampered with, or an operation that no longer resolves the same way);
* the fresh state must equal the stored state, or the record moved on
  (``stale``);
* a refusal from ``prepare`` now is ``stale`` too: the change can no longer be
  made as prepared.

`binding_digest` is the SHA-256 of everything the approval is bound to:
the format, the proposal's id, operation, arguments, the fresh canonical
arguments, the target Matter and record, the user, the grant, the client, the
token family, the fresh state, the fresh preview and the proposal's expiry.
The page puts it in the form it shows; the POST must carry the same value, and
it is computed again under the proposal's lock and the canonical locks before
anything is approved or executed. Any difference between display, submission
and execution therefore means «review again», never «approve something else».
"""

from __future__ import annotations

import dataclasses
import hashlib
from dataclasses import dataclass
from typing import Any

from django.db import transaction
from pydantic import ValidationError

from app.core.errors import DomainError
from app.mcp_writes import operations
from app.mcp_writes.models import CURRENT_FORMAT, McpWriteProposal
from app.mcp_writes.operations.base import OperationRefused, Prepared
from app.mcp_writes.refusals import canonical_json, jsonable


@dataclass(frozen=True)
class Reviewed:
    """The change as trusted code derives it now, or why it cannot be approved."""

    #: "" when the change can be approved; else ``format``, ``unknown_operation``,
    #: ``mismatch`` or ``stale``.
    problem: str
    canonical: dict[str, Any] = dataclasses.field(default_factory=dict)
    state: dict[str, Any] = dataclasses.field(default_factory=dict)
    preview: dict[str, Any] = dataclasses.field(default_factory=dict)
    binding: str = ""
    #: For the event: which comparison failed (codes only).
    reason: str = ""


def binding_digest(
    proposal: McpWriteProposal,
    *,
    canonical: Any,
    state: Any,
    preview: Any,
) -> str:
    """SHA-256 over the canonical JSON of everything one approval is bound to."""
    payload = {
        "format": CURRENT_FORMAT,
        "proposal": str(proposal.pk),
        "operation": proposal.operation,
        "arguments": proposal.arguments,
        "canonical_arguments": canonical,
        "target": {
            "matter": str(proposal.target_matter_id or ""),
            "record_type": proposal.target_record_type,
            "record_id": proposal.target_record_id,
        },
        "user": str(proposal.user_id),
        "grant": str(proposal.grant_id),
        "client": proposal.client_id,
        "token_family": str(proposal.token_family_id or ""),
        "state": state,
        "preview": preview,
        "proposal_expires_at": proposal.expires_at.isoformat(),
    }
    return hashlib.sha256(canonical_json(payload).encode("utf-8")).hexdigest()


def _prepared_again(proposal: McpWriteProposal, user: Any) -> Prepared | str:
    op = operations.get(proposal.operation)
    if op is None:
        return "unknown_operation"
    if not isinstance(proposal.arguments, dict):
        return "mismatch"
    try:
        arguments = op.arguments.model_validate(proposal.arguments)
    except ValidationError:
        return "mismatch"
    try:
        # Writes nothing, and nothing it might write survives (as at prepare).
        with transaction.atomic():
            prepared = op.prepare(user=user, arguments=arguments)
            transaction.set_rollback(True)
    except (OperationRefused, DomainError):
        return "stale"
    return prepared


def derive(proposal: McpWriteProposal, user: Any) -> Reviewed:
    """The proposal's change as the approval page may show and execute it, derived afresh.

    ``user`` is the person on the page (already checked to be the proposal's
    own). Call it again under the proposal's lock and the operation's
    canonical locks before approving: only that answer is acted on.
    """
    if proposal.format_version != CURRENT_FORMAT:
        return Reviewed(problem="format", reason="format_version")
    prepared = _prepared_again(proposal, user)
    if isinstance(prepared, str):
        return Reviewed(problem=prepared, reason=f"prepare_{prepared}")
    from app.mcp_writes.proposals import preview_json

    canonical = jsonable(prepared.canonical)
    state = jsonable(prepared.state)
    preview = preview_json(prepared.preview)
    if canonical != proposal.canonical_arguments:
        return Reviewed(problem="mismatch", reason="canonical_arguments")
    if (
        str(prepared.target_matter_id or "") != str(proposal.target_matter_id or "")
        or (prepared.target_record_type or "")[:100] != proposal.target_record_type
        or (prepared.target_record_id or "")[:100] != proposal.target_record_id
    ):
        return Reviewed(problem="mismatch", reason="target")
    if state != proposal.state:
        return Reviewed(problem="stale", reason="state_changed")
    return Reviewed(
        problem="",
        canonical=canonical,
        state=state,
        preview=preview,
        binding=binding_digest(proposal, canonical=canonical, state=state, preview=preview),
    )
