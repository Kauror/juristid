"""The MCP server's client for the write gateway (docs/adr/0189; CONTRACT §1, §5, §6).

The `mcp` process never writes. A write tool asks `mcp-writer` — a separate
process with its own database role, reachable only over the internal
`mcp_write` network — to *prepare* a proposal or to *commit* one the person
has already approved in Juristid:

    POST {MCP_WRITE_GATEWAY_URL}/v1/prepare  {"operation": str, "arguments": {...}}
    POST {MCP_WRITE_GATEWAY_URL}/v1/commit   {"proposal_id": str}

Each request carries

- the incoming MCP request's ``Authorization`` header, verbatim: the writer
  verifies the person's bearer token itself (scope, grant, account, epoch,
  ``may_write_business_content``). The token is the authority; nothing here
  vouches for the caller;
- ``X-Juristid-Gateway-Key`` (MCP_WRITE_GATEWAY_KEY), which only proves that
  the caller is the `mcp` container;
- ``X-Correlation-Id``, which ties the tool call's log line to the writer's
  proposal and audit rows.

The client follows no redirect, ignores proxy environment variables, waits at
most MCP_WRITE_CALL_TIMEOUT_SECONDS and reads at most
`MAX_RESPONSE_BYTES` of an answer. A refusal (``{"status": "refused", "code",
"message"}``) becomes `GatewayRefused` with a safe text; anything else that
goes wrong becomes `GatewayUnavailable` with a fixed one. Nothing is logged
but the operation's kind, the outcome and exception *types* — never the
token, the key, the arguments or the answer's text.
"""

from __future__ import annotations

import json
import logging
from typing import Any

import httpx2
from django.conf import settings

logger = logging.getLogger(__name__)

PREPARE_PATH = "/v1/prepare"
COMMIT_PATH = "/v1/commit"

GATEWAY_KEY_HEADER = "X-Juristid-Gateway-Key"
CORRELATION_HEADER = "X-Correlation-Id"

#: The gateway's answers are a preview or a receipt: small. Anything larger is
#: refused unread past this point.
MAX_RESPONSE_BYTES = 256 * 1024

#: A gateway message is safe English text (CONTRACT §5); it is still cut short.
MAX_MESSAGE_CHARS = 300

#: Tests put an `httpx2.MockTransport` here; production leaves it None.
TRANSPORT: httpx2.AsyncBaseTransport | None = None

#: What each refusal code tells the assistant, before the gateway's own words.
REFUSAL_TEXT: dict[str, str] = {
    "unauthorized": "Juristid did not accept this connection for changes: reconnect Juristid.",
    "insufficient_scope": (
        "This connection may not prepare changes in Juristid. Reconnect Juristid and allow "
        "changes; a colleague without write access cannot."
    ),
    "writes_disabled": "Changes through ChatGPT are switched off in Juristid at the moment.",
    "forbidden": "The user may not make this change in Juristid.",
    "not_found": "Nothing with this id or reference is available to you in Juristid.",
    "invalid_argument": "An argument was not valid for this change.",
    "missing_information": (
        "Juristid needs information the user has not given. Ask the user; do not guess."
    ),
    "domain_rule": "Juristid's rules do not allow this change.",
    "stale": (
        "The record changed after this proposal was prepared. Nothing was saved; prepare it "
        "again if the user still wants it."
    ),
    "expired": "This proposal has expired unapproved. Nothing was saved.",
    "declined": "The user declined this proposal in Juristid. Nothing was saved.",
    "approval_mismatch": "This proposal's approval does not match it. Nothing was saved.",
    "already_failed": "This proposal already failed and cannot be committed. Nothing was saved.",
    "too_large": "The change is too large for Juristid to accept.",
}
REFUSED_DEFAULT_TEXT = "Juristid refused this change. Nothing was saved."

NOT_CONFIGURED_TEXT = "Changes through ChatGPT are not configured on this Juristid server."
TIMEOUT_TEXT = (
    "Juristid took too long to answer. Nothing may have been saved: check the proposal in "
    "Juristid before trying again."
)
UNAVAILABLE_TEXT = "Juristid's change service could not be reached. Nothing was saved."
BAD_ANSWER_TEXT = "Juristid's change service gave an answer that could not be read."


class GatewayRefused(Exception):
    """The gateway refused the request; `text` is safe to show."""

    def __init__(self, code: str, text: str) -> None:
        super().__init__(text)
        self.code = code
        self.text = text


class GatewayUnavailable(Exception):
    """No usable answer; `kind` is for the log, `text` (fixed) for the assistant."""

    def __init__(self, kind: str, text: str) -> None:
        super().__init__(text)
        self.kind = kind
        self.text = text


def _one_line(value: Any) -> str:
    if not isinstance(value, str):
        return ""
    return " ".join(value.split())[:MAX_MESSAGE_CHARS]


def refusal(code: Any, message: Any) -> GatewayRefused:
    """A gateway refusal as a safe text: the code's own sentence, then the gateway's words."""
    if not isinstance(code, str) or code not in REFUSAL_TEXT:
        return GatewayRefused("refused", REFUSED_DEFAULT_TEXT)
    detail = _one_line(message)
    text = REFUSAL_TEXT[code]
    if detail and detail != text:
        text = f"{text} {detail}"
    return GatewayRefused(code, text)


async def _read_bounded(response: httpx2.Response) -> bytes:
    body = bytearray()
    async for chunk in response.aiter_bytes():
        body.extend(chunk)
        if len(body) > MAX_RESPONSE_BYTES:
            raise GatewayUnavailable("too_large", BAD_ANSWER_TEXT)
    return bytes(body)


async def _post(
    path: str, payload: dict[str, Any], *, authorization: str, correlation_id: str
) -> dict[str, Any]:
    key = str(settings.MCP_WRITE_GATEWAY_KEY or "")
    base = str(settings.MCP_WRITE_GATEWAY_URL or "").rstrip("/")
    if not key or not base:
        raise GatewayUnavailable("not_configured", NOT_CONFIGURED_TEXT)
    headers = {
        "Authorization": authorization,
        GATEWAY_KEY_HEADER: key,
        CORRELATION_HEADER: correlation_id,
        "Content-Type": "application/json",
        "Accept": "application/json",
    }
    content = json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode()
    timeout = float(settings.MCP_WRITE_CALL_TIMEOUT_SECONDS)
    try:
        async with httpx2.AsyncClient(
            transport=TRANSPORT,
            timeout=httpx2.Timeout(timeout),
            follow_redirects=False,
            trust_env=False,
        ) as client:
            async with client.stream(
                "POST", base + path, content=content, headers=headers
            ) as response:
                status_code = response.status_code
                body = await _read_bounded(response)
    except GatewayUnavailable as exc:
        logger.warning("MCP write gateway %s: %s", path, exc.kind)
        raise
    except httpx2.TimeoutException as exc:
        logger.warning("MCP write gateway %s: timeout (%s)", path, type(exc).__name__)
        raise GatewayUnavailable("timeout", TIMEOUT_TEXT) from None
    except httpx2.HTTPError as exc:
        logger.warning("MCP write gateway %s: unavailable (%s)", path, type(exc).__name__)
        raise GatewayUnavailable("unavailable", UNAVAILABLE_TEXT) from None

    try:
        answer = json.loads(body)
    except ValueError:
        answer = None
    if isinstance(answer, dict) and answer.get("status") == "refused":
        raise refusal(answer.get("code"), answer.get("message"))
    if status_code != 200 or not isinstance(answer, dict):
        kind = "unavailable" if status_code >= 500 else "bad_answer"
        logger.warning("MCP write gateway %s: HTTP %s without a usable answer", path, status_code)
        raise GatewayUnavailable(
            kind, UNAVAILABLE_TEXT if kind == "unavailable" else BAD_ANSWER_TEXT
        )
    return answer


async def prepare(
    *, authorization: str, operation: str, arguments: dict[str, Any], correlation_id: str
) -> dict[str, Any]:
    """Ask the writer to store a pending proposal; its answer, or a raised refusal."""
    return await _post(
        PREPARE_PATH,
        {"operation": operation, "arguments": arguments},
        authorization=authorization,
        correlation_id=correlation_id,
    )


async def commit(*, authorization: str, proposal_id: str, correlation_id: str) -> dict[str, Any]:
    """Ask the writer to commit an approved proposal; its answer, or a raised refusal."""
    return await _post(
        COMMIT_PATH,
        {"proposal_id": proposal_id},
        authorization=authorization,
        correlation_id=correlation_id,
    )
