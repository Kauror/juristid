"""Protocol details the QA evaluation found (EVAL-005, EVAL-006, EVAL-007).

* EVAL-005: the server has tools and nothing else (docs/adr/0185 §1), so
  ``initialize`` advertises ``tools`` only — not ``prompts`` or ``resources``,
  whose list methods stay answerable and empty.
* EVAL-006: a refused request does not echo the request body back (the
  no-echo rule the argument validation already follows).
* EVAL-007: the call log's ``result_count`` is counted per tool, explicitly —
  not "the first list in the answer", which for `get_matter` was its
  alternate titles.
"""

from __future__ import annotations

import json
import logging
from typing import Any

import anyio
import pytest

from app.mcp_server import server as mcp_server
from tests import mcp_server_fakes as fx

MARKER = "ZQ4711"  # short: pydantic shortens a long echoed value


@pytest.fixture
def fakes(settings, monkeypatch):
    fx.configure(settings)
    return fx.install(monkeypatch)


def _post(body: str, token: str | None = fx.GOOD_TOKEN) -> Any:
    async def scenario():
        async with fx.client(mcp_server.build_app()) as http:
            headers = dict(fx.JSON_HEADERS)
            if token:
                headers["Authorization"] = f"Bearer {token}"
            return await http.post("/mcp", content=body, headers=headers)

    return anyio.run(scenario)


def _rpc(method: str, params: dict[str, Any] | None = None) -> Any:
    async def scenario():
        async with fx.client(mcp_server.build_app()) as http:
            return await fx.rpc(http, method, params)

    return anyio.run(scenario)


# -- EVAL-005 ------------------------------------------------------------------------


def test_initialize_advertises_tools_and_nothing_else(fakes):
    response = _rpc(
        "initialize",
        {
            "protocolVersion": "2025-06-18",
            "capabilities": {},
            "clientInfo": {"name": "t", "version": "1"},
        },
    )
    capabilities = response.json()["result"]["capabilities"]
    assert "tools" in capabilities
    for name in ("prompts", "resources", "logging", "completions"):
        assert name not in capabilities, name


@pytest.mark.parametrize(
    ("method", "key"),
    [("resources/list", "resources"), ("prompts/list", "prompts")],
)
def test_the_unadvertised_lists_still_answer_empty(fakes, method, key):
    response = _rpc(method, {})
    assert response.status_code == 200
    assert response.json()["result"][key] == []


# -- EVAL-006 ------------------------------------------------------------------------


@pytest.mark.parametrize(
    "body",
    [
        json.dumps(
            [
                {"jsonrpc": "2.0", "id": 1, "method": "tools/list"},
                {
                    "jsonrpc": "2.0",
                    "id": 2,
                    "method": "tools/call",
                    "params": {"name": "search", "arguments": {"query": MARKER}},
                },
            ]
        ),
        json.dumps([{"jsonrpc": "2.0", "id": 1, "method": "tools/list", "params": {"q": MARKER}}]),
        '{"jsonrpc": "2.0", "id": 1, "method": "tools/call", "params": {"query": "' + MARKER,
        json.dumps({"jsonrpc": "2.0", "id": 1, "method": 7, "params": {"q": MARKER}}),
        json.dumps({"id": 1, "method": "tools/call", "params": {"q": MARKER}}),
    ],
)
def test_a_refused_body_is_not_echoed_back(fakes, body):
    response = _post(body)
    assert MARKER not in response.text
    assert fakes.calls == []


# -- EVAL-007 ------------------------------------------------------------------------


def _logged_count(caplog: Any) -> Any:
    [record] = [r for r in caplog.records if r.name == "app.mcp_server.calls"]
    return json.loads(record.getMessage().split(" ", 1)[1])["result_count"]


@pytest.mark.parametrize(
    ("tool", "reader", "arguments", "answer", "expected"),
    [
        (
            "get_matter",
            "matter_snapshot",
            {"matter": "2026_225"},
            {"alternate_titles": ["a", "b", "c"], "planned_actions": [], "title": "x"},
            1,
        ),
        (
            "get_matter_activity",
            "matter_activity",
            {"matter": "2026_225"},
            {"matter": {"x": 1}, "items": [{}, {}]},
            2,
        ),
        (
            "get_matter_documents",
            "matter_documents",
            {"matter": "2026_225"},
            {"matter": {}, "documents": [{}, {}, {}]},
            3,
        ),
        (
            "get_upcoming_commencements",
            "upcoming_commencements",
            {},
            {"window": ["a", "b"], "commencements": [{}], "informational": [{}, {}]},
            3,
        ),
        (
            "search_records",
            "search_records",
            {"query": "x"},
            {"facets": [1, 2, 3, 4], "results": [{}]},
            1,
        ),
    ],
)
def test_result_count_is_counted_for_each_tool(
    fakes, caplog, tool, reader, arguments, answer, expected
):
    fakes.behaviour[reader] = lambda *args, **kwargs: answer
    caplog.set_level(logging.INFO, logger="app.mcp_server.calls")

    async def scenario():
        async with fx.client(mcp_server.build_app()) as http:
            return await fx.call(http, tool, arguments)

    result = anyio.run(scenario)
    assert result.get("isError") is not True
    assert _logged_count(caplog) == expected
