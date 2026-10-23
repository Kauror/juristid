"""The write gateway's refusals and the JSON rules every stored value follows.

A refusal is *returned*, never raised through a transaction: the status a
proposal moves to and the event that records why must survive the refusal
(the rule `app.mcp_access.services` follows for the same reason).

Every message is a fixed English sentence, or an operation's own
`OperationRefused` message, which `app.mcp_writes.operations.base` already
holds to «never repeats confidential record text it was not given». No
message quotes a caller's argument value.
"""

from __future__ import annotations

import datetime as dt
import decimal
import json
import uuid
from dataclasses import dataclass, field
from typing import Any

#: HTTP status per refusal code (CONTRACT §5, plus the two the gateway itself
#: needs: an internal failure and a timeout, both retryable).
STATUS: dict[str, int] = {
    "unauthorized": 401,
    "insufficient_scope": 403,
    "writes_disabled": 403,
    "forbidden": 403,
    "not_found": 404,
    "invalid_argument": 400,
    "missing_information": 422,
    "domain_rule": 422,
    "stale": 409,
    "expired": 410,
    "declined": 409,
    "approval_mismatch": 409,
    "already_failed": 409,
    "too_large": 413,
    "method_not_allowed": 405,
    "internal_error": 500,
    "timeout": 504,
}

MESSAGES: dict[str, str] = {
    "unauthorized": "Authentication required: reconnect Juristid and try again.",
    "insufficient_scope": (
        "This connection may only read. Reconnect Juristid and allow preparing changes."
    ),
    "writes_disabled": "Changes through the AI connection are switched off in Juristid.",
    "forbidden": "This account may not make changes through Juristid's AI connection.",
    "not_found": "No such prepared change is available to you.",
    "invalid_argument": "An argument was not valid for this operation.",
    "missing_information": "Information the change needs was not given.",
    "domain_rule": "A Juristid rule refused this change.",
    "stale": (
        "The record changed after this change was prepared. Nothing was saved; "
        "prepare the change again."
    ),
    "expired": "This prepared change has expired. Nothing was saved; prepare it again.",
    "declined": "The person declined this change in Juristid. Nothing was saved.",
    "approval_mismatch": (
        "The approval does not match this prepared change. Nothing was saved; prepare it again."
    ),
    "already_failed": "This prepared change already failed. Nothing was saved; prepare it again.",
    "too_large": "The request is too large.",
    "method_not_allowed": "Method not allowed.",
    "internal_error": "Juristid could not complete this call. Try again.",
    "timeout": "Juristid took too long to answer. Try again.",
}

#: A refusal message is cut to this many characters.
MAX_MESSAGE = 300


@dataclass(frozen=True)
class Refusal:
    code: str
    message: str = ""
    extra: dict[str, Any] = field(default_factory=dict)

    @property
    def status(self) -> int:
        return STATUS.get(self.code, 400)

    def as_json(self) -> dict[str, Any]:
        text = (self.message or MESSAGES.get(self.code, "Refused."))[:MAX_MESSAGE]
        return {"status": "refused", "code": self.code, "message": text, **self.extra}


def refusal(code: str, message: str = "", **extra: Any) -> Refusal:
    return Refusal(code=code, message=message, extra=extra)


def _default(value: Any) -> Any:
    if isinstance(value, dt.datetime | dt.date | dt.time):
        return value.isoformat()
    if isinstance(value, uuid.UUID | decimal.Decimal):
        return str(value)
    if isinstance(value, set | frozenset | tuple):
        return sorted(value, key=str) if isinstance(value, set | frozenset) else list(value)
    raise TypeError(f"{type(value).__name__} is not JSON-safe")


def jsonable(value: Any) -> Any:
    """``value`` as JSON would give it back: what a JSONField stores and returns.

    Applied to every value that is stored and compared later — canonical
    arguments, state, preview, receipt — on both sides of the comparison, so a
    date an operation returns compares equal to the string it was stored as.
    """
    return json.loads(json.dumps(value, default=_default, allow_nan=False))


def canonical_json(value: Any) -> str:
    """Sorted keys, no whitespace, no NaN: one text for one value."""
    return json.dumps(
        jsonable(value), sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False
    )
