"""Prepare: validate a client's proposed change and store it for its person (CONTRACT §5).

`prepare` is what `POST /v1/prepare` runs, in a worker thread of the write
gateway, after the gateway has verified the bearer token and asked
`app.mcp_access` that its account may write (`access.writer_for`). It

1. finds the operation in `REGISTRY` (an unknown name is refused without
   being repeated);
2. validates the arguments with the operation's own pydantic model — a size
   bound first; a refusal names the fields and what is wrong with them, never
   a value;
3. calls the operation's ``prepare`` inside a savepoint that is always rolled
   back, so whatever an operation might write while previewing (it must write
   nothing) cannot escape, `on_commit` callbacks included;
4. stores the proposal — PENDING for `MCP_WRITE_PROPOSAL_SECONDS` — with the
   canonical arguments, their digest, the state, the preview and a fresh
   correlation id, and one `PREPARED` event, in one transaction;
5. answers the gateway's response, with the approval page's absolute address.

A refused preparation stores no proposal and writes one `PREPARE_REFUSED`
event naming the code (and, for invalid arguments, the field names).
"""

from __future__ import annotations

import dataclasses
import hashlib
import json
import logging
import uuid
from datetime import timedelta
from typing import Any

from django.conf import settings
from django.db import transaction
from django.urls import reverse
from django.utils import timezone
from pydantic import ValidationError

from app.core.errors import DomainError
from app.mcp_writes import operations
from app.mcp_writes.access import family_id
from app.mcp_writes.models import McpWriteEvent, McpWriteEventType, McpWriteProposal
from app.mcp_writes.operations.base import OperationRefused, Preview
from app.mcp_writes.refusals import Refusal, canonical_json, jsonable, refusal

logger = logging.getLogger("app.mcp_writes")

#: At most this many invalid fields are named in one refusal.
MAX_NAMED_FIELDS = 10


# -- the digest -----------------------------------------------------------------


def arguments_digest(
    *, operation: str, canonical_arguments: Any, user_id: Any, grant_id: Any
) -> str:
    """SHA-256 over the canonical JSON of what is being approved, and by whom.

    The operation, its canonical arguments, the user and the grant: an
    approval of one is an approval of exactly that, and a stored row edited
    afterwards no longer matches the digest its approval recorded.
    """
    payload = {
        "operation": operation,
        "canonical_arguments": canonical_arguments,
        "user": str(user_id),
        "grant": str(grant_id),
    }
    return hashlib.sha256(canonical_json(payload).encode("utf-8")).hexdigest()


def digest_of(proposal: McpWriteProposal) -> str:
    """The digest recomputed from what the proposal row holds now."""
    return arguments_digest(
        operation=proposal.operation,
        canonical_arguments=proposal.canonical_arguments,
        user_id=proposal.user_id,
        grant_id=proposal.grant_id,
    )


# -- the events -----------------------------------------------------------------


def record(
    event_type: str,
    *,
    succeeded: bool = True,
    proposal: McpWriteProposal | None = None,
    user: Any = None,
    grant_id: Any = None,
    client_id: str = "",
    operation: str = "",
    correlation_id: uuid.UUID | None = None,
    **detail: Any,
) -> McpWriteEvent:
    """Append one event. ``detail`` holds codes and ids only."""
    if proposal is not None:
        user = user if user is not None else proposal.user
        grant_id = grant_id if grant_id is not None else proposal.grant_id
        client_id = client_id or proposal.client_id
        operation = operation or proposal.operation
        correlation_id = correlation_id or proposal.correlation_id
    return McpWriteEvent.objects.create(
        event_type=event_type,
        succeeded=succeeded,
        proposal=proposal,
        user=user,
        grant_id=grant_id,
        client_id=(client_id or "")[:500],
        operation=(operation or "")[:64],
        correlation_id=correlation_id,
        detail=jsonable(detail),
    )


# -- the shapes the gateway answers ---------------------------------------------


def approval_url(proposal_id: Any) -> str:
    """The approval page's absolute address, from settings — never from a request."""
    origin = (getattr(settings, "JURISTID_PUBLIC_ORIGIN", "") or "").rstrip("/")
    return f"{origin}{reverse('mcp_writes:approve', kwargs={'proposal_id': proposal_id})}"


def preview_json(preview: Preview) -> dict[str, Any]:
    return dict(jsonable(dataclasses.asdict(preview)))


def proposal_answer(proposal: McpWriteProposal) -> dict[str, Any]:
    op = operations.get(proposal.operation)
    return {
        "status": "prepared",
        "proposal": {
            "id": str(proposal.pk),
            "operation": proposal.operation,
            "label": str(op.label) if op is not None else proposal.operation,
            "expires_at": proposal.expires_at.isoformat(),
            "approval_url": approval_url(proposal.pk),
            "preview": proposal.preview,
        },
    }


# -- validation -------------------------------------------------------------------


def _field_name(location: tuple[Any, ...]) -> str:
    return ".".join(str(part) for part in location) or "arguments"


def invalid_arguments(error: ValidationError) -> Refusal:
    """Name the fields and the kind of problem; never the value that was sent."""
    problems = []
    for item in error.errors(include_url=False, include_input=False, include_context=False):
        problems.append(
            f"{_field_name(tuple(item.get('loc') or ()))} ({item.get('type', 'invalid')})"
        )
    named = sorted(set(problems))[:MAX_NAMED_FIELDS]
    return refusal(
        "invalid_argument",
        "Invalid arguments: " + "; ".join(named) + ".",
        fields=[name.split(" ", 1)[0] for name in named],
    )


def _too_large(raw_arguments: Any) -> bool:
    limit = int(getattr(settings, "MCP_WRITE_MAX_REQUEST_BYTES", 64 * 1024))
    try:
        size = len(json.dumps(raw_arguments, ensure_ascii=False).encode("utf-8"))
    except (TypeError, ValueError):
        return True
    return size > limit


# -- prepare ----------------------------------------------------------------------


def _refused(answer: Refusal, *, user: Any, access: Any, operation: str, **detail: Any) -> Refusal:
    record(
        McpWriteEventType.PREPARE_REFUSED,
        succeeded=False,
        user=user,
        grant_id=access.grant_id,
        client_id=access.client_id,
        operation=operation,
        code=answer.code,
        **detail,
    )
    return answer


def prepare(
    *, access: Any, user: Any, operation_name: Any, raw_arguments: Any
) -> dict[str, Any] | Refusal:
    """Validate, preview and store one proposed change. Writes no business row.

    ``access`` is the bearer token's `VerifiedAccess`; ``user`` the account
    `writer_for` answered for it. Neither comes from the request body.
    """
    op = operations.get(operation_name) if isinstance(operation_name, str) else None
    if op is None:
        return _refused(
            refusal("invalid_argument", "Unknown operation."),
            user=user,
            access=access,
            operation="",
            reason="unknown_operation",
        )
    name = op.name
    if not isinstance(raw_arguments, dict):
        return _refused(
            refusal("invalid_argument", "The arguments must be a JSON object."),
            user=user,
            access=access,
            operation=name,
            reason="not_an_object",
        )
    if _too_large(raw_arguments):
        return _refused(refusal("too_large"), user=user, access=access, operation=name)
    try:
        arguments = op.arguments.model_validate(raw_arguments)
    except ValidationError as error:
        answer = invalid_arguments(error)
        return _refused(
            answer, user=user, access=access, operation=name, fields=answer.extra["fields"]
        )

    with transaction.atomic():
        try:
            # A savepoint that is always rolled back: `prepare` writes
            # nothing, and if it ever did, nothing of it would survive —
            # not a row, not an `on_commit` callback.
            with transaction.atomic():
                prepared = op.prepare(user=user, arguments=arguments)
                transaction.set_rollback(True)
        except OperationRefused as error:
            return _refused(
                refusal(error.code, error.message), user=user, access=access, operation=name
            )
        except DomainError:
            return _refused(refusal("domain_rule"), user=user, access=access, operation=name)

        canonical = jsonable(prepared.canonical)
        state = jsonable(prepared.state)
        preview = preview_json(prepared.preview)
        now = timezone.now()
        proposal = McpWriteProposal.objects.create(
            id=uuid.uuid4(),
            user=user,
            grant_id=access.grant_id,
            token_family_id=family_id(access),
            client_id=(access.client_id or "")[:500],
            operation=name,
            canonical_arguments=canonical,
            arguments_digest=arguments_digest(
                operation=name,
                canonical_arguments=canonical,
                user_id=user.pk,
                grant_id=access.grant_id,
            ),
            target_matter_id=prepared.target_matter_id,
            target_record_type=(prepared.target_record_type or "")[:100],
            target_record_id=(prepared.target_record_id or "")[:100],
            state=state,
            preview=preview,
            expires_at=now + timedelta(seconds=int(settings.MCP_WRITE_PROPOSAL_SECONDS)),
            correlation_id=uuid.uuid4(),
        )
        record(McpWriteEventType.PREPARED, proposal=proposal)
    return proposal_answer(proposal)
