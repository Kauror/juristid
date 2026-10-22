"""The MCP server's protocol and tool contracts (docs/adr/0185 §2, §5, §6).

Drives the real ASGI application in-process — the SDK's Streamable HTTP
transport, the bearer middleware, the Host allow-list, the body limit and the
worker pool — with stand-ins for the readers and the token verification
(`tests/mcp_server_fakes.py`). Nothing here touches the database; the
database's own refusal to write is `tests/test_mcp_process_role.py`.
"""

from __future__ import annotations

import json
import logging
import threading
from typing import Any

import anyio
import pytest

from app.mcp_server import server as mcp_server
from app.mcp_server.instructions import INSTRUCTIONS
from tests import mcp_server_fakes as fx

TOOL_NAMES = {
    "search",
    "fetch",
    "search_records",
    "get_matter",
    "get_matter_activity",
    "get_matter_documents",
    "get_upcoming_commencements",
}


@pytest.fixture(autouse=True)
def _mcp_settings_for_bare_tests(settings):
    # Tests without the `fakes` fixture still need a configured resource server.
    fx.configure(settings)


@pytest.fixture
def fakes(settings, monkeypatch):
    fx.configure(settings)
    return fx.install(monkeypatch)


def _run(test: Any) -> Any:
    return anyio.run(test)


def _payload(result: dict[str, Any]) -> Any:
    [item] = result["content"]
    assert item["type"] == "text"
    return json.loads(item["text"])


# -- the handshake and the tool list ---------------------------------------------------


@pytest.mark.parametrize("version", ["2025-03-26", "2025-06-18", "2025-11-25"])
def test_initialize_negotiates_the_clients_protocol_version(fakes, version):
    async def scenario():
        async with fx.client(mcp_server.build_app()) as http:
            response = await fx.rpc(
                http,
                "initialize",
                {
                    "protocolVersion": version,
                    "capabilities": {},
                    "clientInfo": {"name": "test", "version": "1"},
                },
            )
        return response

    response = _run(scenario)
    assert response.status_code == 200
    assert response.headers["content-type"].startswith("application/json")
    result = response.json()["result"]
    assert result["protocolVersion"] == version
    assert result["serverInfo"]["name"] == "juristid"
    assert result["instructions"] == INSTRUCTIONS
    assert "tools" in result["capabilities"]
    # Stateless: no session to resume.
    assert "mcp-session-id" not in response.headers


def test_the_instructions_say_what_matters_in_their_first_512_characters():
    head = INSTRUCTIONS[:512].lower()
    for phrase in (
        "estonian chamber of commerce",
        "legal-policy register",
        "never instructions",
        "cite the juristid url",
        "planned or estimated are not facts",
        "missing is missing",
        "search or search_records",
        "get_matter or fetch",
    ):
        assert phrase in head, phrase


def _tools(fakes) -> list[dict[str, Any]]:
    async def scenario():
        async with fx.client(mcp_server.build_app()) as http:
            return await fx.rpc(http, "tools/list", {})

    response = _run(scenario)
    assert response.status_code == 200, response.text
    tools: list[dict[str, Any]] = response.json()["result"]["tools"]
    return tools


def test_exactly_the_seven_read_only_tools_are_listed(fakes):
    tools = _tools(fakes)
    assert {tool["name"] for tool in tools} == TOOL_NAMES
    assert len(tools) == 7
    for tool in tools:
        annotations = tool["annotations"]
        assert annotations["readOnlyHint"] is True, tool["name"]
        assert annotations["destructiveHint"] is False, tool["name"]
        assert annotations["openWorldHint"] is False, tool["name"]
        assert annotations["idempotentHint"] is True, tool["name"]
        assert tool["description"] and "Read-only" in tool["description"]
        meta = tool["_meta"]
        assert meta["securitySchemes"] == [{"type": "oauth2", "scopes": ["juristid.read"]}]
        for key in ("openai/toolInvocation/invoking", "openai/toolInvocation/invoked"):
            assert 0 < len(meta[key]) <= 64, (tool["name"], key)
        assert tool["outputSchema"]["type"] == "object"


def test_every_input_schema_is_strict_and_names_no_identity(fakes):
    tools = {tool["name"]: tool for tool in _tools(fakes)}
    expected = {
        "search": {"query"},
        "fetch": {"id"},
        "search_records": {"query", "source_kinds", "matter", "page"},
        "get_matter": {"matter"},
        "get_matter_activity": {"matter", "since", "until", "kind", "page"},
        "get_matter_documents": {"matter", "page"},
        "get_upcoming_commencements": {"from", "to", "page"},
    }
    for name, properties in expected.items():
        schema = tools[name]["inputSchema"]
        assert schema["type"] == "object"
        assert schema["additionalProperties"] is False, name
        assert set(schema["properties"]) == properties, name
        for forbidden in ("user", "user_id", "persona", "email", "role", "sql", "path", "url"):
            assert forbidden not in schema["properties"], (name, forbidden)

    def flat(schema: dict[str, Any]) -> str:
        return json.dumps(schema)

    assert '"maxLength": 500' in flat(tools["search"]["inputSchema"])
    assert tools["search"]["inputSchema"]["required"] == ["query"]
    assert '"maxLength": 200' in flat(tools["fetch"]["inputSchema"])
    assert '"maxLength": 200' in flat(tools["get_matter"]["inputSchema"])
    for name in ("search_records", "get_matter_activity", "get_matter_documents"):
        page = flat(tools[name]["inputSchema"]["properties"]["page"])
        assert '"minimum": 1' in page and '"maximum": 10' in page, name
    dates = flat(tools["get_upcoming_commencements"]["inputSchema"])
    assert r"^\\d{4}-\\d{2}-\\d{2}$" in dates
    kinds = flat(tools["search_records"]["inputSchema"]["properties"]["source_kinds"])
    assert "SUBMISSION" in kinds and "DOCUMENT_FRAGMENT" in kinds


def test_search_and_fetch_publish_the_company_knowledge_output_schemas(fakes):
    tools = {tool["name"]: tool for tool in _tools(fakes)}
    search = tools["search"]["outputSchema"]
    assert search["required"] == ["results"]
    item = search["$defs"]["SearchResult"]
    assert set(item["properties"]) == {"id", "title", "url"}
    assert set(item["required"]) == {"id", "title", "url"}
    fetch = tools["fetch"]["outputSchema"]
    assert set(fetch["properties"]) == {"id", "title", "text", "url", "metadata"}
    assert set(fetch["required"]) == {"id", "title", "text", "url", "metadata"}


# -- the tools, each happy path --------------------------------------------------------


def _call(name: str, arguments: dict[str, Any], **kw: Any) -> dict[str, Any]:
    async def scenario():
        async with fx.client(mcp_server.build_app()) as http:
            return await fx.call(http, name, arguments, **kw)

    result: dict[str, Any] = _run(scenario)
    return result


def test_search_returns_the_company_knowledge_shape_twice(fakes):
    result = _call("search", {"query": "pakendiseadus"})
    assert result.get("isError") is not True
    structured = result["structuredContent"]
    assert structured == _payload(result)
    assert len(structured["results"]) == 20
    assert all(set(row) == {"id", "title", "url"} for row in structured["results"])
    [(name, user, args, kwargs)] = fakes.calls
    assert name == "search_results"
    assert user.pk == fx.READER_USER_ID
    assert args == ("pakendiseadus",)
    assert kwargs == {"limit": 20}


def test_fetch_returns_the_company_knowledge_shape_twice(fakes):
    result = _call("fetch", {"id": "doc:33333333-3333-4333-8333-333333333333"})
    structured = result["structuredContent"]
    assert structured == _payload(result)
    assert set(structured) == {"id", "title", "text", "url", "metadata"}
    assert structured["metadata"] == {"kind": "DOCUMENT_FRAGMENT", "truncated": False}
    assert fakes.calls[0][2] == ("doc:33333333-3333-4333-8333-333333333333",)


@pytest.mark.parametrize(
    ("tool", "arguments", "reader", "args", "kwargs"),
    [
        (
            "search_records",
            {"query": "pakend", "source_kinds": ["SUBMISSION"], "matter": "2026_225", "page": 2},
            "search_records",
            ("pakend",),
            {"source_kinds": ["SUBMISSION"], "matter": "2026_225", "page": 2},
        ),
        (
            "search_records",
            {"query": "pakend"},
            "search_records",
            ("pakend",),
            {"source_kinds": None, "matter": None, "page": 1},
        ),
        ("get_matter", {"matter": "2026_225"}, "matter_snapshot", ("2026_225",), {}),
        (
            "get_matter_activity",
            {"matter": "2026_225", "since": "2026-01-01", "until": "2026-10-01", "kind": "events"},
            "matter_activity",
            ("2026_225",),
            {"since": "2026-01-01", "until": "2026-10-01", "kind": "events", "page": 1},
        ),
        (
            "get_matter_activity",
            {"matter": "2026_225"},
            "matter_activity",
            ("2026_225",),
            {"since": None, "until": None, "kind": "all", "page": 1},
        ),
        (
            "get_matter_documents",
            {"matter": "2026_225", "page": 3},
            "matter_documents",
            ("2026_225",),
            {"page": 3},
        ),
        (
            "get_upcoming_commencements",
            {"from": "2026-10-22", "to": "2026-11-21"},
            "upcoming_commencements",
            (),
            {"start": "2026-10-22", "end": "2026-11-21", "page": 1},
        ),
        (
            "get_upcoming_commencements",
            {},
            "upcoming_commencements",
            (),
            {"start": None, "end": None, "page": 1},
        ),
    ],
)
def test_each_richer_tool_calls_its_reader_and_answers_twice(
    fakes, tool, arguments, reader, args, kwargs
):
    result = _call(tool, arguments)
    assert result.get("isError") is not True, result
    assert result["structuredContent"] == _payload(result)
    [(name, user, called_args, called_kwargs)] = fakes.calls
    assert (name, called_args, called_kwargs) == (reader, args, kwargs)
    assert user.pk == fx.READER_USER_ID


def test_dates_in_a_readers_answer_become_iso_strings_in_both_copies(fakes):
    result = _call("get_matter", {"matter": "2026_225"})
    assert result["structuredContent"]["response_deadline_of"] == "2026-11-03"
    assert _payload(result)["response_deadline_of"] == "2026-11-03"


# -- arguments the schema refuses ------------------------------------------------------


@pytest.mark.parametrize(
    ("tool", "arguments", "field"),
    [
        ("search", {"query": "x" * 501}, "query"),
        ("search", {"query": ""}, "query"),
        ("search", {}, "query"),
        ("search", {"query": "pakend", "user": "somebody@koda.ee"}, "user"),
        ("search", {"query": "pakend", "persona": "Kaur"}, "persona"),
        ("fetch", {"id": "d" * 201}, "id"),
        ("get_matter", {"matter": "m" * 201}, "matter"),
        ("search_records", {"query": "pakend", "page": 0}, "page"),
        ("search_records", {"query": "pakend", "page": 11}, "page"),
        ("search_records", {"query": "pakend", "page": "2"}, "page"),
        ("search_records", {"query": "pakend", "source_kinds": ["EVERYTHING"]}, "source_kinds"),
        ("get_matter_activity", {"matter": "2026_225", "since": "1.10.2026"}, "since"),
        ("get_matter_activity", {"matter": "2026_225", "kind": "drafts"}, "kind"),
        ("get_upcoming_commencements", {"from": "2026-10-22T00:00"}, "from"),
        ("get_upcoming_commencements", {"start": "2026-10-22"}, "start"),
    ],
)
def test_schema_violations_are_errors_that_do_not_echo_the_input(fakes, tool, arguments, field):
    result = _call(tool, arguments)
    assert result["isError"] is True
    [item] = result["content"]
    text = item["text"]
    assert field in text
    assert "Traceback" not in text
    for value in arguments.values():
        if isinstance(value, str) and len(value) > 3:
            assert value not in text
    assert "structuredContent" not in result or result["structuredContent"] is None
    assert fakes.calls == []


# -- reader refusals and failures ------------------------------------------------------


def test_not_found_answers_like_a_missing_record(fakes):
    def refuse(user, *args, **kwargs):
        raise fakes.readers.NotFound(f"no {fx.SECRET_SNIPPET}")

    fakes.behaviour["fetch_source"] = refuse
    result = _call("fetch", {"id": "doc:33333333-3333-4333-8333-333333333333"})
    assert result["isError"] is True
    assert result["content"][0]["text"] == mcp_server.NOT_FOUND_TEXT
    assert fx.SECRET_SNIPPET not in json.dumps(result)


def test_invalid_argument_carries_the_readers_safe_message(fakes):
    def refuse(user, *args, **kwargs):
        raise fakes.readers.InvalidArgument("The window may be at most 400 days.")

    fakes.behaviour["upcoming_commencements"] = refuse
    result = _call("get_upcoming_commencements", {"from": "2026-01-01", "to": "2027-12-31"})
    assert result["isError"] is True
    assert result["content"][0]["text"] == "The window may be at most 400 days."


def test_an_unexpected_failure_says_nothing_about_the_data(fakes, caplog):
    def crash(user, *args, **kwargs):
        raise RuntimeError(fx.SECRET_SNIPPET)

    fakes.behaviour["matter_snapshot"] = crash
    with caplog.at_level(logging.DEBUG):
        result = _call("get_matter", {"matter": "2026_225"})
    assert result["isError"] is True
    assert result["content"][0]["text"] == mcp_server.FAILED_TEXT
    assert fx.SECRET_SNIPPET not in json.dumps(result)
    assert fx.SECRET_SNIPPET not in caplog.text
    assert "RuntimeError" in caplog.text


def test_an_account_refused_at_call_time_gets_an_authentication_challenge(fakes):
    result = _call("search", {"query": "pakend"}, token=fx.REVOKED_AT_CALL_TOKEN)
    assert result["isError"] is True
    assert result["content"][0]["text"] == mcp_server.UNAUTHORIZED_TEXT
    [challenge] = result["_meta"]["mcp/www_authenticate"]
    assert challenge.startswith("Bearer ")
    assert (
        'resource_metadata="https://mcp.juristid.example/.well-known/oauth-protected-resource/mcp"'
        in challenge
    )
    assert 'error="invalid_token"' in challenge
    assert "structuredContent" not in result or result["structuredContent"] is None
    # The reader was never reached.
    assert fakes.calls == []


# -- what the transport refuses --------------------------------------------------------


def _request(method: str, path: str, **kw: Any) -> Any:
    host = kw.pop("host", fx.HOST)

    async def scenario():
        async with fx.client(mcp_server.build_app(), host=host) as http:
            return await http.request(method, path, **kw)

    return _run(scenario)


def _rpc(method: str, params: dict[str, Any] | None = None, **kw: Any) -> Any:
    host = kw.pop("host", fx.HOST)

    async def scenario():
        async with fx.client(mcp_server.build_app(), host=host) as http:
            return await fx.rpc(http, method, params, **kw)

    return _run(scenario)


def _assert_challenge(response: Any, *, resource_path: str = "/mcp") -> None:
    assert response.status_code == 401
    header = response.headers["www-authenticate"]
    assert header.startswith("Bearer ")
    assert (
        f'resource_metadata="https://{fx.HOST}/.well-known/oauth-protected-resource{resource_path}"'
        in header
    )
    assert 'scope="juristid.read"' in header
    assert response.json() == {
        "error": "invalid_token",
        "error_description": "Authentication required",
    }


def test_a_request_without_a_token_is_challenged_and_given_nothing(fakes):
    response = _rpc("tools/call", {"name": "search", "arguments": {"query": "x"}}, token=None)
    _assert_challenge(response)
    assert fakes.calls == []


def test_tool_discovery_without_a_token_is_challenged(fakes):
    response = _rpc("tools/list", {}, token=None)
    _assert_challenge(response)
    assert "search" not in response.text


def test_an_invalid_token_is_challenged(fakes):
    response = _rpc("tools/list", {}, token="not-a-token")  # noqa: S106
    _assert_challenge(response)
    assert fakes.verified == ["checked"]


def test_an_oversized_token_is_not_even_looked_up(fakes):
    response = _rpc("tools/list", {}, token="t" * 600)
    _assert_challenge(response)
    assert fakes.verified == []


def test_a_token_without_the_scope_is_refused(fakes):
    response = _rpc("tools/list", {}, token=fx.NO_SCOPE_TOKEN)
    assert response.status_code == 403
    assert 'error="insufficient_scope"' in response.headers["www-authenticate"]


def test_an_unknown_method_is_a_json_rpc_error(fakes):
    response = _rpc("resources/write", {})
    assert response.status_code == 200
    error = response.json()["error"]
    assert error["code"] == -32601


def test_a_write_shaped_tool_does_not_exist(fakes):
    result = _call("delete_matter", {"matter": "2026_225"})
    assert result["isError"] is True
    assert "Unknown tool" in result["content"][0]["text"]
    assert fakes.calls == []


def test_an_oversized_body_is_refused_before_anything_reads_it(fakes, settings):
    settings.MCP_MAX_REQUEST_BYTES = 2048
    for token in (fx.GOOD_TOKEN, None):
        response = _rpc(
            "tools/call", {"name": "search", "arguments": {"query": "x" * 4000}}, token=token
        )
        assert response.status_code == 413
    assert fakes.calls == []
    assert fakes.verified == []


@pytest.mark.parametrize("path", ["/mcp", "/healthz", "/.well-known/oauth-protected-resource/mcp"])
def test_a_host_not_on_the_list_is_refused(fakes, path):
    method = "POST" if path == "/mcp" else "GET"
    response = _request(
        method, path, host="evil.example", headers={"Authorization": f"Bearer {fx.GOOD_TOKEN}"}
    )
    assert response.status_code == 421
    assert fakes.verified == []


def test_a_browser_origin_not_on_the_list_is_refused(fakes):
    response = _rpc("tools/list", {}, headers={"Origin": "https://evil.example"})
    assert response.status_code == 403
    assert "search" not in response.text


def test_the_tunnel_clients_service_name_is_allowed(fakes):
    response = _rpc("tools/list", {}, host="mcp:8001")
    assert response.status_code == 200


@pytest.mark.parametrize("method", ["GET", "DELETE", "PUT"])
def test_only_post_reaches_the_mcp_endpoint(fakes, method):
    # Stateless: a standalone GET stream would hang open with nothing to say.
    response = _request(
        method,
        "/mcp",
        headers={"Authorization": f"Bearer {fx.GOOD_TOKEN}", "Accept": "text/event-stream"},
    )
    assert response.status_code == 405
    assert response.headers["allow"] == "POST"


def test_healthz_answers_without_a_token_and_says_nothing_else():
    response = _request("GET", "/healthz", host="127.0.0.1:8001")
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}
    assert response.headers["cache-control"] == "no-store"


# -- protected resource metadata (RFC 9728) --------------------------------------------


@pytest.mark.parametrize(
    "path", ["/.well-known/oauth-protected-resource/mcp", "/.well-known/oauth-protected-resource"]
)
def test_the_protected_resource_metadata_names_the_issuer_and_the_scope(fakes, path):
    response = _request("GET", path)
    assert response.status_code == 200
    assert response.json() == {
        "resource": fx.RESOURCE,
        "authorization_servers": [fx.ORIGIN],
        "scopes_supported": ["juristid.read"],
        "bearer_methods_supported": ["header"],
        "resource_name": "Juristid",
    }


def test_the_metadata_names_the_resource_the_client_dialled(fakes):
    response = _request(
        "GET", "/.well-known/oauth-protected-resource/mcp", host="tunnel.mcp.example.net"
    )
    assert response.json()["resource"] == fx.TUNNEL_RESOURCE
    challenged = _rpc("tools/list", {}, token=None, host="tunnel.mcp.example.net")
    assert (
        'resource_metadata="https://tunnel.mcp.example.net/.well-known/oauth-protected-resource/mcp"'
        in challenged.headers["www-authenticate"]
    )


# -- the worker pool -------------------------------------------------------------------


def test_parallel_calls_respect_the_concurrency_limit(fakes, settings):
    settings.MCP_MAX_CONCURRENT_CALLS = 2
    fakes.behaviour["search_results"] = fx.slow(0.3)
    results: list[dict[str, Any]] = []

    async def scenario():
        async with fx.client(mcp_server.build_app()) as http:

            async def one(n: int) -> None:
                results.append(await fx.call(http, "search", {"query": f"q{n}"}, request_id=n))

            async with anyio.create_task_group() as group:
                for n in range(5):
                    group.start_soon(one, n)

    _run(scenario)
    assert len(results) == 5
    assert all(result.get("isError") is not True for result in results)
    assert fakes.max_in_flight == 2


def test_a_call_past_the_timeout_fails_safely(fakes, settings):
    settings.MCP_CALL_TIMEOUT_SECONDS = 1
    release = threading.Event()

    def stuck(user, *args, **kwargs):
        release.wait(10)
        return {"reference": fx.SECRET_SNIPPET}

    fakes.behaviour["matter_snapshot"] = stuck
    try:
        result = _call("get_matter", {"matter": "2026_225"})
    finally:
        release.set()
    assert result["isError"] is True
    assert "took too long" in result["content"][0]["text"]
    assert fx.SECRET_SNIPPET not in json.dumps(result)


def test_readers_never_run_on_the_event_loop_thread(fakes):
    loop_threads: set[int] = set()

    async def scenario():
        loop_threads.add(threading.get_ident())
        async with fx.client(mcp_server.build_app()) as http:
            await fx.call(http, "search", {"query": "pakend"})

    _run(scenario)
    assert fakes.reader_threads
    assert not fakes.reader_threads & loop_threads


# -- logging ---------------------------------------------------------------------------


def test_one_log_line_per_call_with_no_query_id_snippet_or_token(fakes, caplog):
    async def scenario():
        async with fx.client(mcp_server.build_app()) as http:
            await fx.call(http, "search", {"query": "salajane päring"})
            await fx.call(http, "fetch", {"id": "doc:33333333-3333-4333-8333-333333333333"})

    with caplog.at_level(logging.DEBUG):
        _run(scenario)

    lines = [r for r in caplog.records if r.name == "app.mcp_server.calls"]
    assert len(lines) == 2
    records = [json.loads(r.getMessage().split(" ", 1)[1]) for r in lines]
    assert [r["tool"] for r in records] == ["search", "fetch"]
    for record in records:
        assert set(record) == {"tool", "user_id", "outcome", "duration_ms", "result_count"}
        assert record["user_id"] == str(fx.READER_USER_ID)
        assert record["outcome"] == "ok"
        assert isinstance(record["duration_ms"], int)
    assert records[0]["result_count"] == 20
    everything = caplog.text
    for secret in (
        "salajane päring",
        "33333333-3333-4333-8333-333333333333",
        fx.GOOD_TOKEN,
        fx.SECRET_SNIPPET,
    ):
        assert secret not in everything


# -- the official SDK client against the app -------------------------------------------


@pytest.mark.parametrize("mode", ["legacy", "auto"])
def test_the_sdk_client_lists_and_calls_the_tools(fakes, mode):
    import httpx2
    from mcp import Client
    from mcp.client.streamable_http import streamable_http_client

    seen: dict[str, Any] = {}

    async def scenario():
        app = mcp_server.build_app()
        async with app.router.lifespan_context(app):
            http = httpx2.AsyncClient(
                transport=httpx2.ASGITransport(app=app),
                headers={"Authorization": f"Bearer {fx.GOOD_TOKEN}"},
                timeout=30,
            )
            async with http:
                transport = streamable_http_client(f"https://{fx.HOST}/mcp", http_client=http)
                async with Client(transport, mode=mode) as client:
                    seen["version"] = client.protocol_version
                    seen["instructions"] = client.instructions
                    tools = await client.list_tools()
                    seen["tools"] = {tool.name for tool in tools.tools}
                    result = await client.call_tool("search", {"query": "pakend"})
                    seen["structured"] = result.structured_content
                    seen["error"] = result.is_error

    _run(scenario)
    print(f"negotiated protocol version ({mode}): {seen['version']}")
    assert seen["tools"] == TOOL_NAMES
    assert seen["error"] is False
    assert len(seen["structured"]["results"]) == 20
    assert seen["instructions"] == INSTRUCTIONS
    if mode == "legacy":
        assert seen["version"] == "2025-11-25"
