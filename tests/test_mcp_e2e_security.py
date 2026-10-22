"""Hostile input at the real MCP endpoint, over a real database (docs/adr/0185 §1, §2, §5).

`tests/test_mcp_server.py` holds the transport's contract with stand-in readers
and `tests/test_mcp_readers*.py` the readers' with no transport. This suite
sends what an attacker — or a confused model — would send, through both at
once and as a real colleague's token, and checks three things every time: the
answer is a refusal that carries no business text, nothing in the database
changed, and nothing else was disclosed.

Read-only, so one committed world serves the module (`tests/mcp_eval_world.py`).
"""

from __future__ import annotations

import json
import re
import uuid
from typing import Any

import pytest

from app.mcp_server import server as mcp_server
from tests import mcp_eval_world as w

pytestmark = pytest.mark.django_db

#: Business text from the world: none of it may appear in a refusal.
BUSINESS_TEXT = ("Kliimakindla", "Pakendiseaduse", "Marko", w.WAITING_FOR, "Tulumaksuseaduse")


@pytest.fixture(scope="module")
def world(django_db_setup, django_db_blocker, tmp_path_factory):
    with (
        pytest.MonkeyPatch.context() as patch,
        w.committed_world(django_db_blocker, tmp_path_factory.mktemp("mcp-sec"), patch) as built,
    ):
        yield built


@pytest.fixture
def mcp(world) -> w.Mcp:
    return world.mcp("marko")


@pytest.fixture
def unchanged():
    """Every model's row count, before and after the test."""
    before = w.row_counts()
    yield
    assert w.row_counts() == before


def _refusal(result: dict[str, Any], sent: str = "") -> str:
    text = w.error_text(result)
    assert "structuredContent" not in result or result["structuredContent"] is None
    for business in BUSINESS_TEXT:
        assert business not in text, business
    assert "Traceback" not in text
    if len(sent) > 3:
        assert sent not in text
    return text


# -- crafted source ids --------------------------------------------------------------------


def _crafted_ids(world: w.EvalWorld) -> list[str]:
    matter = world.matters["wait"]
    entry = world.records["wait_recent_entry"]
    document = world.records["long_document"]
    link = world.records["historical_page"]
    return [
        # Another family's real uuid under a prefix: a name, never a permission.
        f"doc:{matter.pk}",
        f"entry:{document.pk}",
        f"hist:{entry.pk}",
        f"sub:{link.pk}",
        f"frag:{document.pk}",
        f"eff:{matter.pk}",
        f"matter:{entry.pk}",
        # Spellings the strict parser refuses rather than normalises.
        f"matter:{str(matter.pk).upper()}",
        f"matter:{matter.pk.hex}",
        f"MATTER:{matter.pk}",
        f" doc:{document.pk}@-1",
        f"doc:{document.pk}@1e3",
        f"doc:{document.pk}@99999999999",
        f"doc:{document.pk}@",
        # Prefixes that are not ours.
        f"user:{world.users['marko'].pk}",
        f"token:{uuid.uuid4()}",
        f"grant:{uuid.uuid4()}",
        # SQL- and path-shaped.
        "matter:' OR '1'='1",
        "doc:00000000-0000-0000-0000-000000000000; DROP TABLE matters_matter; --",
        "entry:../../../../etc/passwd",
        "hist:%00",
        # Unicode look-alikes and noise.
        f"dоc:{document.pk}",  # Cyrillic «о»
        "matter:１２３",
        "matter:‮" + str(matter.pk),
        "matter:🙂",
        "\x00",
    ]


def test_a_crafted_id_is_refused_and_discloses_nothing(world, mcp, unchanged):
    known = {
        mcp_server.NOT_FOUND_TEXT,
        "Unknown id prefix.",
        "The id does not name a record.",
        "The continuation is not a number.",
        "The continuation is out of range.",
        "The continuation is past the end of this text.",
        "The id is empty or too long.",
    }
    crafted = _crafted_ids(world)
    results = mcp.batch([("fetch", {"id": source_id}) for source_id in crafted])
    for source_id, result in zip(crafted, results, strict=True):
        text = _refusal(result, source_id)
        assert text in known, (source_id, text)
    # Another family's real record answers exactly like a record that does not exist.
    assert w.error_text(results[0]) == mcp_server.NOT_FOUND_TEXT


def test_a_ten_thousand_character_id_is_refused_by_the_schema(mcp, unchanged):
    for name, arguments in (
        ("fetch", {"id": "matter:" + "a" * 10_000}),
        ("get_matter", {"matter": "2026_" + "1" * 10_000}),
        ("search_records", {"query": "pakend", "matter": "x" * 10_000}),
    ):
        text = _refusal(mcp.call(name, arguments))
        assert text.startswith(f"Invalid arguments for {name}:"), text
        assert "too_long" in text


@pytest.mark.parametrize(
    "matter",
    [
        "2026_1' OR '1'='1",
        "2026_1; DROP TABLE matters_matter",
        "2026/1",
        "teema 1",
        "*",
        "%",
        "2026_0",
        "9999_2000000001",
        "0000_1",
        str(uuid.uuid4()),
    ],
)
def test_a_crafted_matter_reference_is_refused(mcp, matter, unchanged):
    text = _refusal(mcp.call("get_matter", {"matter": matter}), matter)
    assert text in (
        mcp_server.NOT_FOUND_TEXT,
        "matter must be a Matter id or a reference such as 2026_225.",
    ), text


# -- pagination -----------------------------------------------------------------------------


@pytest.mark.parametrize("page", [0, -1, 11, 10**9, 1.5, "1", True, None])
def test_a_page_outside_one_to_ten_is_refused_by_every_paged_tool(world, mcp, page):
    reference = world.reference("wait")
    for name, arguments in (
        ("search_records", {"query": "eelnõu"}),
        ("get_matter_activity", {"matter": reference}),
        ("get_matter_documents", {"matter": reference}),
        ("get_upcoming_commencements", {}),
    ):
        result = mcp.call(name, {**arguments, "page": page})
        if page is None:
            # An explicit null is «the first page», not an error.
            w.structured(result, name)
            continue
        text = _refusal(result)
        assert text.startswith(f"Invalid arguments for {name}: page"), (name, page, text)


def test_the_last_page_is_empty_rather_than_the_first_again(world, mcp):
    first = mcp.ok("search_records", {"query": "eelnõu"})
    tenth = mcp.ok("search_records", {"query": "eelnõu", "page": 10})
    assert first["results"] and first["total"] < 9 * 20
    assert (
        tenth["results"] == [] and tenth["has_more"] is False and tenth["total"] == first["total"]
    )
    activity = mcp.ok("get_matter_activity", {"matter": world.reference("wait"), "page": 10})
    assert activity["items"] == [] and activity["has_more"] is False
    documents = mcp.ok("get_matter_documents", {"matter": world.reference("wait"), "page": 10})
    assert documents["documents"] == [] and documents["has_more"] is False


# -- size -----------------------------------------------------------------------------------


def test_a_query_over_five_hundred_characters_is_refused(mcp, unchanged):
    exactly = "pakendiseadus " * 35 + "x" * 10
    assert len(exactly) == 500
    w.structured(mcp.call("search", {"query": exactly}), "search")
    for name in ("search", "search_records"):
        text = _refusal(mcp.call(name, {"query": exactly + "x"}))
        assert (
            text
            == f"Invalid arguments for {name}: query: string_too_long. See the tool's input schema."
        )


def test_a_hundred_kilobyte_body_is_refused_before_anything_reads_it(world, unchanged):
    body = json.dumps(
        {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "tools/call",
            "params": {"name": "search", "arguments": {"query": "x" * 100_000}},
        }
    )
    for token in (world.token("marko"), None, "not-a-token"):
        response = w.Mcp(token).raw(body)
        assert response.status_code == 413, (token, response.status_code)
        assert "Kliima" not in response.text


# -- instructions inside the records ----------------------------------------------------------


def test_an_instruction_inside_a_record_is_returned_verbatim_and_does_nothing(
    world, mcp, unchanged
):
    entry = world.records["injection_entry"]
    document = world.records["injection_document"]
    page = world.records["injection_page"]

    found = mcp.ok("search_records", {"query": "ignoreeri varasemaid juhiseid"})
    found_ids = {row["id"] for row in found["results"]}
    assert {f"entry:{entry.pk}", f"hist:{page.pk}"} <= found_ids
    assert any(source_id.startswith("frag:") for source_id in found_ids)

    for source_id in (f"entry:{entry.pk}", f"doc:{document.pk}", f"hist:{page.pk}"):
        answer = mcp.ok("fetch", {"id": source_id})
        assert w.INJECTION in answer["text"], source_id
        assert answer["url"].startswith(w.ORIGIN + "/")
    activity = mcp.ok("get_matter_activity", {"matter": world.reference("injection")})
    assert any(w.INJECTION in item["text"] for item in activity["items"])
    # Nothing the text asked for happened: no tool by that name, no second call.
    assert "delete_matter" not in {
        tool["name"] for tool in mcp.rpc("tools/list", {}).json()["result"]["tools"]
    }


def test_the_instructions_tell_the_client_that_text_is_data(world, mcp):
    hello = mcp.rpc(
        "initialize",
        {
            "protocolVersion": w.PROTOCOL_VERSION,
            "capabilities": {},
            "clientInfo": {"name": "t", "version": "1"},
        },
    ).json()["result"]
    assert "never instructions" in hello["instructions"][:512]


# -- methods nobody should call --------------------------------------------------------------


@pytest.mark.parametrize(
    "method",
    [
        "sampling/createMessage",
        "roots/list",
        "elicitation/create",
        "resources/write",
        "tools/create",
        "admin/shutdown",
    ],
)
def test_a_method_the_server_does_not_have_is_a_json_rpc_error(mcp, method, unchanged):
    response = mcp.rpc(method, {})
    assert response.status_code == 200
    assert response.json()["error"]["code"] == -32601


@pytest.mark.parametrize(
    ("method", "params", "key"),
    [
        ("resources/list", {}, "resources"),
        ("resources/templates/list", {}, "resourceTemplates"),
        ("prompts/list", {}, "prompts"),
    ],
)
def test_resources_and_prompts_are_empty(mcp, method, params, key):
    response = mcp.rpc(method, params)
    assert response.status_code == 200
    assert response.json()["result"][key] == []


@pytest.mark.parametrize(
    ("method", "params"),
    [
        ("resources/read", {"uri": "file:///etc/passwd"}),
        ("resources/read", {"uri": "https://juristid.test/teemad/"}),
        ("prompts/get", {"name": "system"}),
        ("resources/subscribe", {"uri": "file:///etc/passwd"}),
    ],
)
def test_reading_a_resource_or_a_prompt_finds_nothing(mcp, method, params, unchanged):
    response = mcp.rpc(method, params)
    assert response.status_code == 200
    payload = response.json()
    assert "error" in payload, payload
    assert "root:" not in response.text


def test_an_unknown_tool_is_an_error_naming_no_record(mcp, unchanged):
    for name in ("delete_matter", "update_matter", "sql", "search ", "SEARCH", "get_my_work"):
        text = _refusal(mcp.call(name, {"matter": "2026_1"}))
        assert "Unknown tool" in text, (name, text)


def test_every_method_needs_the_token(world, unchanged):
    anonymous = w.Mcp(None)
    for method in ("tools/list", "tools/call", "resources/list", "prompts/list", "ping"):
        response = anonymous.rpc(method, {})
        assert response.status_code == 401, method
        assert response.json() == {
            "error": "invalid_token",
            "error_description": "Authentication required",
        }


def test_a_json_rpc_batch_is_not_a_way_around_anything(world, mcp, unchanged):
    batch = json.dumps(
        [
            {"jsonrpc": "2.0", "id": 1, "method": "tools/list"},
            {
                "jsonrpc": "2.0",
                "id": 2,
                "method": "tools/call",
                "params": {"name": "get_matter", "arguments": {"matter": world.reference("wait")}},
            },
        ]
    )
    response = mcp.raw(batch)
    assert response.status_code in (200, 400), response.status_code
    if response.status_code == 200:
        assert "error" in response.json() or w.WAITING_FOR not in response.text
    assert w.Mcp(None).raw(batch).status_code == 401


# -- concurrency ----------------------------------------------------------------------------


def test_three_calls_at_once_each_get_their_own_correct_answer(world, mcp, unchanged):
    calls: list[tuple[str, dict[str, Any]]] = [
        ("get_matter", {"matter": world.reference("wait")}),
        ("search", {"query": "pakendiseadus"}),
        ("fetch", {"id": f"doc:{world.records['long_document'].pk}"}),
    ]
    one_by_one = [
        w.structured(result, name)
        for (name, _), result in zip(calls, mcp.batch(calls), strict=True)
    ]
    for _ in range(2):
        together = mcp.concurrently(calls)
        assert [w.structured(result) for result in together] == one_by_one
    # Three colleagues at once, each answered as themselves.
    tokens = [world.token("marko"), world.token("kati"), world.token("former")]
    answers = []
    for token in tokens:
        answers.append(w.Mcp(token).rpc("tools/list", {}).status_code)
    assert answers == [200, 200, 401]


# -- the tool list itself ---------------------------------------------------------------------

WRITE_WORDS = re.compile(
    r"\b(create|creates|update|updates|delete|deletes|set|sets|add|adds|send|sends|upload|"
    r"uploads|import|imports|write|writes|edit|edits|remove|removes|modify|modifies|post|posts)\b",
    re.IGNORECASE,
)


def test_no_tool_suggests_it_writes(mcp):
    tools = mcp.rpc("tools/list", {}).json()["result"]["tools"]
    assert {tool["name"] for tool in tools} == w.TOOL_NAMES
    for tool in tools:
        for text in (tool["name"], tool.get("title") or "", tool["description"]):
            assert not WRITE_WORDS.search(text.replace("_", " ")), (tool["name"], text)
        annotations = tool["annotations"]
        assert annotations["readOnlyHint"] is True
        assert annotations["destructiveHint"] is False
        properties = tool["inputSchema"].get("properties", {})
        assert not WRITE_WORDS.search(" ".join(properties)), tool["name"]


# -- dates at the edge of the calendar -----------------------------------------------------


@pytest.mark.parametrize(
    ("tool", "arguments"),
    [
        ("get_upcoming_commencements", {"from": "9999-12-31"}),
        ("get_upcoming_commencements", {"from": "9999-12-31", "to": "9999-12-31"}),
        ("get_upcoming_commencements", {"from": "0001-01-01", "to": "0001-01-01"}),
        ("get_matter_activity", {"since": "9999-12-31"}),
        ("get_matter_activity", {"until": "0001-01-01"}),
    ],
)
def test_a_date_at_the_end_of_the_calendar_is_an_answer_or_an_invalid_argument(
    world, mcp, tool, arguments
):
    """Never the generic failure: that is what an unhandled exception looks like."""
    if tool == "get_matter_activity":
        arguments = {"matter": world.reference("wait"), **arguments}
    result = mcp.call(tool, arguments)
    if result.get("isError"):
        text = _refusal(result)
        assert text != mcp_server.FAILED_TEXT, (tool, arguments)
    else:
        w.structured(result, tool)
