"""Stand-ins for the readers (workstream A) and the token verification (workstream C).

The MCP server imports both by module name at call time
(`app.mcp_server.readers`, `app.mcp_access.verification`), so the tests put
these in `sys.modules` under those names and drive the real ASGI application
against them. Nothing here touches the database.
"""

from __future__ import annotations

import datetime as dt
import json
import sys
import threading
import time
import types
import uuid
from collections.abc import AsyncIterator, Callable
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from typing import Any

import httpx2

RESOURCE = "https://mcp.juristid.example/mcp"
TUNNEL_RESOURCE = "https://tunnel.mcp.example.net/mcp"
ORIGIN = "https://juristid.example"
HOST = "mcp.juristid.example"

GOOD_TOKEN = "good-token-0123456789"  # noqa: S105 - a stand-in
REVOKED_AT_CALL_TOKEN = "revoked-at-call-token"  # noqa: S105
NO_SCOPE_TOKEN = "no-scope-token"  # noqa: S105

READER_USER_ID = uuid.UUID("11111111-1111-4111-8111-111111111111")
REVOKED_USER_ID = uuid.UUID("22222222-2222-4222-8222-222222222222")

#: Business text the fakes put in their answers, so a test can prove it never
#: reaches a log line or an error.
SECRET_SNIPPET = "Pakendiseaduse muutmise eelnõu salajane märkus"  # noqa: S105


@dataclass(frozen=True)
class VerifiedAccess:
    token_id: uuid.UUID
    user_id: uuid.UUID
    grant_id: uuid.UUID
    client_id: str
    scopes: frozenset[str]
    resource: str
    expires_at: dt.datetime


@dataclass
class FakeUser:
    pk: uuid.UUID
    is_authenticated: bool = True


def _access(
    user_id: uuid.UUID, scopes: frozenset[str] = frozenset({"juristid.read"})
) -> VerifiedAccess:
    return VerifiedAccess(
        token_id=uuid.uuid4(),
        user_id=user_id,
        grant_id=uuid.uuid4(),
        client_id="https://chatgpt.com/oauth/client.json",
        scopes=scopes,
        resource=RESOURCE,
        expires_at=dt.datetime.now(dt.UTC) + dt.timedelta(hours=1),
    )


@dataclass
class Fakes:
    """What the fakes were asked, and how they should answer."""

    calls: list[tuple[str, Any, tuple[Any, ...], dict[str, Any]]] = field(default_factory=list)
    verified: list[str] = field(default_factory=list)
    #: tool reader name -> callable raising or returning instead of the canned answer
    behaviour: dict[str, Callable[..., Any]] = field(default_factory=dict)
    lock: threading.Lock = field(default_factory=threading.Lock)
    in_flight: int = 0
    max_in_flight: int = 0
    reader_threads: set[int] = field(default_factory=set)


CANNED: dict[str, dict[str, Any]] = {
    "search_results": {
        "results": [
            {
                "id": f"matter:{uuid.UUID(int=n)}",
                "title": f"2026_{200 + n} Pakendiseadus",
                "url": f"{ORIGIN}/teemad/{n}/",
                "snippet": SECRET_SNIPPET,
            }
            for n in range(25)
        ]
    },
    "fetch_source": {
        "id": "doc:33333333-3333-4333-8333-333333333333",
        "title": "Arvamus pakendiseaduse eelnõule",
        "text": SECRET_SNIPPET,
        "url": f"{ORIGIN}/dokumendid/3/",
        "metadata": {"kind": "DOCUMENT_FRAGMENT", "truncated": False},
        "extra": "dropped",
    },
    "search_records": {
        "results": [{"id": "entry:1", "kind": "ENTRY", "snippet": SECRET_SNIPPET}],
        "page": 1,
        "has_more": False,
    },
    "matter_snapshot": {
        "reference": "2026_225",
        "title": "Pakendiseadus",
        "next_action": {"kind": "WAIT", "label": "planned"},
        "response_deadline_of": dt.date(2026, 11, 3),
    },
    "matter_activity": {"items": [{"date": "2026-10-01", "text": SECRET_SNIPPET}], "page": 1},
    "matter_documents": {"documents": [{"title": "Eelnõu", "fetch_id": "doc:1"}], "page": 1},
    "upcoming_commencements": {"commencements": [], "planned": [], "window": ["a", "b"]},
}


def install(monkeypatch: Any) -> Fakes:
    """Put the stand-in modules where the server looks for them."""
    fakes = Fakes()

    readers = types.ModuleType("app.mcp_server.readers")

    class ReaderError(Exception):
        pass

    class NotFound(ReaderError):
        pass

    class InvalidArgument(ReaderError):
        pass

    readers.ReaderError = ReaderError  # type: ignore[attr-defined]
    readers.NotFound = NotFound  # type: ignore[attr-defined]
    readers.InvalidArgument = InvalidArgument  # type: ignore[attr-defined]

    def reader(name: str) -> Callable[..., Any]:
        def read(user: Any, *args: Any, **kwargs: Any) -> Any:
            with fakes.lock:
                fakes.calls.append((name, user, args, kwargs))
                fakes.in_flight += 1
                fakes.max_in_flight = max(fakes.max_in_flight, fakes.in_flight)
                fakes.reader_threads.add(threading.get_ident())
            try:
                if name in fakes.behaviour:
                    return fakes.behaviour[name](user, *args, **kwargs)
                return CANNED[name]
            finally:
                with fakes.lock:
                    fakes.in_flight -= 1

        return read

    for name in CANNED:
        setattr(readers, name, reader(name))

    verification = types.ModuleType("app.mcp_access.verification")
    tokens = {
        GOOD_TOKEN: _access(READER_USER_ID),
        REVOKED_AT_CALL_TOKEN: _access(REVOKED_USER_ID),
        NO_SCOPE_TOKEN: _access(READER_USER_ID, scopes=frozenset({"other"})),
    }

    def verify_access_token(raw: str, *, now: Any = None) -> VerifiedAccess | None:
        fakes.verified.append("checked")
        return tokens.get(raw)

    def reader_for(access: VerifiedAccess) -> FakeUser | None:
        if access.user_id == REVOKED_USER_ID:
            return None
        return FakeUser(pk=access.user_id)

    verification.VerifiedAccess = VerifiedAccess  # type: ignore[attr-defined]
    verification.verify_access_token = verify_access_token  # type: ignore[attr-defined]
    verification.reader_for = reader_for  # type: ignore[attr-defined]

    monkeypatch.setitem(sys.modules, "app.mcp_server.readers", readers)
    monkeypatch.setitem(sys.modules, "app.mcp_access.verification", verification)
    fakes.readers = readers  # type: ignore[attr-defined]
    return fakes


def configure(settings: Any) -> None:
    settings.MCP_ENABLED = True
    settings.JURISTID_PUBLIC_ORIGIN = ORIGIN
    settings.MCP_RESOURCE_URLS = [RESOURCE, TUNNEL_RESOURCE]
    settings.MCP_ALLOWED_HOSTS = [
        HOST,
        "tunnel.mcp.example.net",
        "mcp",
        "mcp:8001",
        "127.0.0.1",
        "127.0.0.1:*",
    ]
    settings.MCP_ALLOWED_ORIGINS = [ORIGIN, "https://chatgpt.com"]
    settings.MCP_MAX_CONCURRENT_CALLS = 4
    settings.MCP_CALL_TIMEOUT_SECONDS = 10
    settings.MCP_MAX_REQUEST_BYTES = 64 * 1024


@asynccontextmanager
async def client(app: Any, host: str = HOST) -> AsyncIterator[httpx2.AsyncClient]:
    """An HTTP client wired straight into the ASGI app, its lifespan running."""
    async with app.router.lifespan_context(app):
        transport = httpx2.ASGITransport(app=app)
        async with httpx2.AsyncClient(transport=transport, base_url=f"https://{host}") as http:
            yield http


JSON_HEADERS = {"Accept": "application/json, text/event-stream", "Content-Type": "application/json"}


async def rpc(
    http: httpx2.AsyncClient,
    method: str,
    params: dict[str, Any] | None = None,
    *,
    token: str | None = GOOD_TOKEN,
    request_id: int = 1,
    headers: dict[str, str] | None = None,
) -> httpx2.Response:
    body: dict[str, Any] = {"jsonrpc": "2.0", "id": request_id, "method": method}
    if params is not None:
        body["params"] = params
    sent = dict(JSON_HEADERS)
    if token:
        sent["Authorization"] = f"Bearer {token}"
    sent.update(headers or {})
    return await http.post("/mcp", content=json.dumps(body), headers=sent)


async def call(
    http: httpx2.AsyncClient, name: str, arguments: dict[str, Any], **kw: Any
) -> dict[str, Any]:
    response = await rpc(http, "tools/call", {"name": name, "arguments": arguments}, **kw)
    assert response.status_code == 200, response.text
    payload = response.json()
    assert "result" in payload, payload
    result: dict[str, Any] = payload["result"]
    return result


def slow(seconds: float) -> Callable[..., Any]:
    def answer(user: Any, *args: Any, **kwargs: Any) -> Any:
        time.sleep(seconds)
        return {"results": []}

    return answer
