"""The MCP write gateway (`mcp-writer`): the internal HTTP API `mcp` calls (CONTRACT §1, §5).

    GET  /healthz       {"status": "ok"} — no key, no token, no database
    POST /v1/prepare    {"operation": str, "arguments": {...}}
    POST /v1/commit     {"proposal_id": str}

Served by `manage.py run_mcp_write_gateway` on the internal `mcp_write`
network only. Every request, outermost first:

1. **Host** must be on the allow-list (`localhost`, `127.0.0.1`,
   `mcp-writer`, each bare or with the listen port) — 421 otherwise;
2. a declared body over `MCP_WRITE_MAX_REQUEST_BYTES` is refused (413) before
   anything else is read;
3. **the gateway key** (``X-Juristid-Gateway-Key``), compared in constant
   time. If the process holds no key, a key shorter than 32 characters, or
   `MCP_WRITES_ENABLED` is off, *every* call is refused (``writes_disabled``)
   — a gateway that cannot tell its caller apart answers nobody;
4. the body is read up to the limit (413 beyond it) and parsed as one JSON
   object (``invalid_argument`` otherwise, without repeating it);
5. **the bearer token** the `mcp` process forwarded verbatim is verified *here*
   — `verify_access_token`, then the write scope, then `writer_for` — in a
   worker thread, with the work itself, under `MCP_WRITE_CALL_TIMEOUT_SECONDS`
   and at most `MCP_WRITE_MAX_CONCURRENT_CALLS` at a time
   (`app.mcp_server.runtime.ReaderRuntime`). The gateway key proves only that
   the caller is the `mcp` container; it is never a substitute for the token.

No user id, persona, e-mail or role is ever read from the body: the principal
is the token's.

Answers are JSON with ``Cache-Control: no-store``. A refusal is
``{"status": "refused", "code": …, "message": …}`` with a 4xx (or 500/504 for
an internal failure or a timeout), its message a fixed sentence that never
quotes the request.

**Logging.** One line per call (`app.mcp_writes.gateway.calls`): path,
operation (only a registered name), user id, outcome code, HTTP status and
duration. Never the token, the key, the arguments or any text.
"""

from __future__ import annotations

import hmac
import json
import logging
import time
from collections.abc import Callable
from typing import Any

from django.conf import settings
from starlette.applications import Starlette
from starlette.datastructures import Headers
from starlette.requests import Request
from starlette.responses import JSONResponse, PlainTextResponse, Response
from starlette.routing import Route
from starlette.types import ASGIApp, Receive, Scope, Send

from app.mcp_server.runtime import CallTimedOut, ReaderRuntime
from app.mcp_writes import access as write_access
from app.mcp_writes import commit as commits
from app.mcp_writes import operations, proposals
from app.mcp_writes.refusals import Refusal, refusal

logger = logging.getLogger(__name__)
call_log = logging.getLogger("app.mcp_writes.gateway.calls")

HEALTH_PATH = "/healthz"
PREPARE_PATH = "/v1/prepare"
COMMIT_PATH = "/v1/commit"
KEY_HEADER = "x-juristid-gateway-key"
#: The shortest gateway key the process accepts at all.
MIN_KEY_LENGTH = 32

NO_STORE = {"Cache-Control": "no-store", "Pragma": "no-cache"}


def allowed_hosts() -> frozenset[str]:
    port = int(settings.MCP_WRITE_LISTEN_PORT)
    names = ("localhost", "127.0.0.1", "mcp-writer")
    return frozenset({*names, *(f"{name}:{port}" for name in names)})


def configured_key() -> str:
    return str(getattr(settings, "MCP_WRITE_GATEWAY_KEY", "") or "")


def gateway_usable() -> bool:
    """Writes on and a usable key held: otherwise every call is refused."""
    return write_access.writes_enabled() and len(configured_key()) >= MIN_KEY_LENGTH


def max_body() -> int:
    return int(getattr(settings, "MCP_WRITE_MAX_REQUEST_BYTES", 64 * 1024))


class AllowedHostsMiddleware:
    """Refuse a request whose Host header is not on the list (421), before anything reads it."""

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] == "http":
            host = Headers(scope=scope).get("host", "").strip().lower()
            if host not in allowed_hosts():
                await PlainTextResponse("Invalid Host header", status_code=421)(
                    scope, receive, send
                )
                return
        await self.app(scope, receive, send)


def _answer(payload: dict[str, Any], status: int = 200) -> Response:
    return JSONResponse(payload, status_code=status, headers=NO_STORE)


def _refused(answer: Refusal) -> Response:
    headers = dict(NO_STORE)
    if answer.code == "unauthorized":
        headers["WWW-Authenticate"] = 'Bearer realm="juristid"'
    return JSONResponse(answer.as_json(), status_code=answer.status, headers=headers)


async def _read_body(request: Request) -> bytes | None:
    """The body, or None when it is longer than the limit."""
    limit = max_body()
    body = bytearray()
    async for chunk in request.stream():
        body.extend(chunk)
        if len(body) > limit:
            return None
    return bytes(body)


def _declared_too_large(request: Request) -> bool:
    raw = request.headers.get("content-length", "")
    if not raw:
        return False
    try:
        return int(raw) > max_body()
    except ValueError:
        return True


#: The deepest nesting of objects and arrays a body may have. Every
#: operation's arguments are a flat object; this leaves generous room.
MAX_JSON_DEPTH = 32


def too_deep(body: bytes, limit: int = MAX_JSON_DEPTH) -> bool:
    """Whether the body nests ``[`` / ``{`` deeper than ``limit``, outside strings.

    Scanned before parsing, without recursion, so a body of thirty thousand
    ``[`` never reaches the parser's recursion limit.
    """
    depth = 0
    in_string = False
    escaped = False
    for byte in body:
        if in_string:
            if escaped:
                escaped = False
            elif byte == 0x5C:  # backslash
                escaped = True
            elif byte == 0x22:  # quote
                in_string = False
        elif byte == 0x22:
            in_string = True
        elif byte in (0x5B, 0x7B):
            depth += 1
            if depth > limit:
                return True
        elif byte in (0x5D, 0x7D):
            depth -= 1
    return False


def _key_refusal(request: Request) -> Refusal | None:
    if not gateway_usable():
        return refusal("writes_disabled")
    presented = request.headers.get(KEY_HEADER, "")
    if not presented:
        return refusal("unauthorized", "Gateway key required.")
    if not hmac.compare_digest(presented.encode("utf-8"), configured_key().encode("utf-8")):
        return refusal("forbidden", "Gateway key not accepted.")
    return None


def _bearer(request: Request) -> str:
    raw = request.headers.get("authorization", "")
    scheme, _, token = raw.partition(" ")
    if scheme.lower() != "bearer":
        return ""
    return token.strip()


def _as_the_writer(
    raw_token: str, work: Callable[[Any, Any], dict[str, Any] | Refusal]
) -> Callable[[], tuple[dict[str, Any] | Refusal, Any]]:
    """In the worker thread: verify the token, ask for the writer, run ``work``."""

    def run() -> tuple[dict[str, Any] | Refusal, Any]:
        verified = write_access.verify_access_token(raw_token)
        if verified is None:
            return refusal("unauthorized"), None
        if write_access.write_scope() not in set(getattr(verified, "scopes", ()) or ()):
            return refusal("insufficient_scope"), verified.user_id
        writer = write_access.writer_for(verified)
        if writer is None or writer.pk != verified.user_id:
            return refusal("forbidden"), verified.user_id
        return work(verified, writer), verified.user_id

    return run


def _log(path: str, operation: str, user_id: Any, code: str, status: int, started: float) -> None:
    call_log.info(
        "mcp-writer %s op=%s user=%s outcome=%s status=%s ms=%d",
        path,
        operation or "-",
        user_id or "-",
        code,
        status,
        int((time.monotonic() - started) * 1000),
    )


def build_app(*, runtime: ReaderRuntime | None = None) -> Starlette:
    """The ASGI application `run_mcp_write_gateway` serves. One per process (or test)."""
    runtime = runtime or ReaderRuntime(
        max_concurrent_calls=int(settings.MCP_WRITE_MAX_CONCURRENT_CALLS),
        timeout_seconds=float(settings.MCP_WRITE_CALL_TIMEOUT_SECONDS),
    )

    async def healthz(request: Request) -> Response:
        """Liveness: the process answers. No key, no token, no database, no data."""
        return _answer({"status": "ok"})

    async def handle(request: Request, path: str) -> Response:
        started = time.monotonic()
        operation_label = ""
        user_id: Any = None

        def done(response: Response, code: str) -> Response:
            _log(path, operation_label, user_id, code, response.status_code, started)
            return response

        if _declared_too_large(request):
            return done(_refused(refusal("too_large")), "too_large")
        key_problem = _key_refusal(request)
        if key_problem is not None:
            return done(_refused(key_problem), key_problem.code)
        body = await _read_body(request)
        if body is None:
            return done(_refused(refusal("too_large")), "too_large")
        if too_deep(body):
            answer = refusal("invalid_argument", "The body is nested too deeply.")
            return done(_refused(answer), answer.code)
        try:
            payload = json.loads(body.decode("utf-8")) if body else None
        except (UnicodeDecodeError, ValueError, RecursionError):
            payload = None
        if not isinstance(payload, dict):
            answer = refusal("invalid_argument", "The body must be one JSON object.")
            return done(_refused(answer), answer.code)
        raw_token = _bearer(request)
        if not raw_token:
            return done(_refused(refusal("unauthorized")), "unauthorized")

        if path == PREPARE_PATH:
            name = payload.get("operation")
            if isinstance(name, str) and operations.get(name) is not None:
                operation_label = name
            unexpected = set(payload) - {"operation", "arguments"}
            if unexpected:
                answer = refusal("invalid_argument", "Unexpected fields in the request.")
                return done(_refused(answer), answer.code)

            def work(verified: Any, writer: Any) -> dict[str, Any] | Refusal:
                return proposals.prepare(
                    access=verified,
                    user=writer,
                    operation_name=name,
                    raw_arguments=payload.get("arguments"),
                )
        else:
            unexpected = set(payload) - {"proposal_id"}
            if unexpected:
                answer = refusal("invalid_argument", "Unexpected fields in the request.")
                return done(_refused(answer), answer.code)
            proposal_id = payload.get("proposal_id")

            def work(verified: Any, writer: Any) -> dict[str, Any] | Refusal:
                return commits.commit(access=verified, user=writer, proposal_id=proposal_id)

        try:
            result, user_id = await runtime.run(_as_the_writer(raw_token, work))
        except CallTimedOut:
            return done(_refused(refusal("timeout")), "timeout")
        except Exception as error:
            logger.error("MCP write gateway call failed: %s", type(error).__name__)
            return done(_refused(refusal("internal_error")), "internal_error")
        if isinstance(result, Refusal):
            return done(_refused(result), result.code)
        return done(_answer(result), str(result.get("status", "ok")))

    async def prepare_endpoint(request: Request) -> Response:
        return await handle(request, PREPARE_PATH)

    async def commit_endpoint(request: Request) -> Response:
        return await handle(request, COMMIT_PATH)

    async def not_found(request: Request, exc: Exception) -> Response:
        return _refused(refusal("not_found", "No such endpoint."))

    async def not_allowed(request: Request, exc: Exception) -> Response:
        return _refused(refusal("method_not_allowed"))

    routes = [
        Route(HEALTH_PATH, healthz, methods=["GET"]),
        Route(PREPARE_PATH, prepare_endpoint, methods=["POST"]),
        Route(COMMIT_PATH, commit_endpoint, methods=["POST"]),
    ]
    app = Starlette(
        debug=False,
        routes=routes,
        exception_handlers={404: not_found, 405: not_allowed},
    )
    app.add_middleware(AllowedHostsMiddleware)
    return app
