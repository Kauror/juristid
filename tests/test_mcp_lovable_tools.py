"""The five read-only Lovable synchronisation tools (docs/adr/0201 §9).

The real ASGI application, the SDK's transport and argument validation, the
real readers and the real read service (`app.legacy_import.lovable_observability`)
over a synthetic pilot (tests/lovable_observability_support.py). Two stand-ins
only: the token verification, which maps a test token to a real account (the
OAuth path is `tests/test_mcp_e2e.py`'s), and the worker pool, replaced by one
that runs the read in the test's own thread so it sees the test's transaction
(the pool itself is `tests/test_mcp_runtime_slots.py`'s). Nothing reaches
Lovable; every title, reference and value is invented.
"""

from __future__ import annotations

import datetime as dt
import io
import json
import re
import sys
import types
import uuid
from collections.abc import Callable
from typing import Any

import anyio
import pytest
from django.core.management import call_command
from django.db import connection, connections
from django.test.utils import CaptureQueriesContext
from django.utils import timezone

from app.core.enums import Visibility
from app.legacy_import import lovable_observability as obs
from app.legacy_import.lovable_observation_models import MappingStatus
from app.legacy_import.lovable_sync_models import SyncMode
from app.mcp_server import readers, schemas
from app.mcp_server import server as mcp_server
from app.mcp_server.instructions import ESSENTIALS, INSTRUCTIONS
from app.mcp_server.runtime import ReaderRuntime
from tests import factories
from tests import mcp_server_fakes as fx
from tests.lovable_observability_support import (
    Pilot,
    control,
    narrowed_reader,
    observation_run,
    observed,
    row_counts,
    write_statements,
)

pytestmark = pytest.mark.django_db

LOVABLE_TOOLS = (
    "get_lovable_sync_status",
    "get_lovable_sync_history",
    "get_lovable_sync_matter_changes",
    "get_lovable_observed_changes",
    "get_lovable_sync_readiness",
)

#: Invented business text that must never reach a reader who may not see it.
HIDDEN_TITLE = "Varjatud teema 502"
HIDDEN_VALUE = "Salajane etapp"
UNMATCHED_VALUE = "Sidumata väärtus 900"

WRITE_WORDS = re.compile(
    r"\b(create|creates|update|updates|delete|deletes|set|sets|add|adds|send|sends|upload|"
    r"uploads|import|imports|write|writes|edit|edits|remove|removes|modify|modifies|post|posts)\b",
    re.IGNORECASE,
)


# -- the harness ---------------------------------------------------------------------


class InlineRuntime(ReaderRuntime):
    """The read in the caller's thread, on the test's own connection and transaction.

    Built outside the event loop: Django's connection there is the test's, and
    inside the loop a fresh context would open another that cannot see it."""

    def __init__(self) -> None:
        super().__init__(max_concurrent_calls=1, timeout_seconds=30)
        self._connection = connections["default"]

    async def run(self, work: Callable[[], Any]) -> Any:
        connections["default"] = self._connection
        return work()


class Harness:
    """Tokens for real accounts, and calls through the real application."""

    def __init__(self) -> None:
        self.tokens: dict[str, Any] = {}

    def token_for(self, user: Any) -> str:
        token = f"lovable-test-{uuid.uuid4().hex}"
        self.tokens[token] = fx._access(user.pk)
        return token

    def gone_token(self) -> str:
        """A token whose account no longer exists: `reader_for` answers None."""
        token = f"lovable-gone-{uuid.uuid4().hex}"
        self.tokens[token] = fx._access(uuid.uuid4())
        return token

    def _run(self, token: str, method: str, params: dict[str, Any]) -> Any:
        runtime = InlineRuntime()

        async def scenario() -> Any:
            app = mcp_server.build_app(runtime=runtime)
            async with fx.client(app) as http:
                return await fx.rpc(http, method, params, token=token)

        response = anyio.run(scenario)
        assert response.status_code == 200, response.text
        return response.json()

    def tools(self, user: Any) -> dict[str, dict[str, Any]]:
        payload = self._run(self.token_for(user), "tools/list", {})
        return {tool["name"]: tool for tool in payload["result"]["tools"]}

    def call(self, who: Any, name: str, arguments: dict[str, Any]) -> dict[str, Any]:
        token = who if isinstance(who, str) else self.token_for(who)
        payload = self._run(token, "tools/call", {"name": name, "arguments": arguments})
        result: dict[str, Any] = payload["result"]
        return result

    def ok(self, who: Any, name: str, arguments: dict[str, Any] | None = None) -> dict[str, Any]:
        result = self.call(who, name, arguments or {})
        assert result.get("isError") is not True, (name, result["content"])
        [item] = result["content"]
        assert json.loads(item["text"]) == result["structuredContent"], name
        answer: dict[str, Any] = result["structuredContent"]
        return answer

    def refused(self, who: Any, name: str, arguments: dict[str, Any]) -> str:
        result = self.call(who, name, arguments)
        assert result.get("isError") is True, (name, arguments, result)
        [item] = result["content"]
        text: str = item["text"]
        return text


@pytest.fixture
def mcp(settings, monkeypatch) -> Harness:
    fx.configure(settings)
    settings.MCP_WRITES_ENABLED = False
    # The reads run on the event loop's thread (InlineRuntime), on purpose.
    monkeypatch.setenv("DJANGO_ALLOW_ASYNC_UNSAFE", "true")
    harness = Harness()
    verification = types.ModuleType("app.mcp_access.verification")

    def verify_access_token(raw: str, *, now: Any = None) -> Any:
        return harness.tokens.get(raw)

    def reader_for(access: Any) -> Any:
        from app.accounts.models import User

        return User.objects.filter(pk=access.user_id, is_active=True).first()

    verification.verify_access_token = verify_access_token  # type: ignore[attr-defined]
    verification.reader_for = reader_for  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "app.mcp_access.verification", verification)
    return harness


@pytest.fixture
def reader() -> Any:
    return factories.UserFactory(display_name="Lugeja Näidis")


def _today() -> dt.date:
    return timezone.localdate()


def _iso(days_from_today: int) -> str:
    return (_today() + dt.timedelta(days=days_from_today)).isoformat()


@pytest.fixture
def world() -> Pilot:
    """A pilot of three Teemad (one RESTRICTED), two nights and two wider readings,
    all in the last few days — so the default window holds them whatever today is."""
    control(SyncMode.COMPARE, observe=True)
    pilot = Pilot()
    pilot.add(243, jargmiseks="Valmistada ette arvamus")
    pilot.add(501)
    pilot.add(502, visibility=Visibility.RESTRICTED, teema=HIDDEN_TITLE)
    first_night = timezone.now().replace(microsecond=0) - dt.timedelta(days=3)
    pilot.night(first_night)
    pilot.edit("2026_243", jargmiseks="Jälgida menetlust")
    pilot.edit("2026_501", hetkeseis="Valitsuses")
    pilot.edit("2026_502", hetkeseis=HIDDEN_VALUE)
    pilot.night(first_night + dt.timedelta(days=1))
    first = observation_run(first_night)
    run = observation_run(first_night + dt.timedelta(days=1), baseline=first)
    observed(run, "2026_900", fields=[("title", "Vana pealkiri", UNMATCHED_VALUE)])
    observed(
        run,
        "2026_501",
        mapping=MappingStatus.PILOT_LINKED,
        matter=pilot.matter("2026_501"),
        source_id=pilot.states["2026_501"].source_id,
        in_pilot=True,
        fields=[("stage", "Ministeeriumis", "Valitsuses")],
    )
    observed(
        run,
        "2026_502",
        mapping=MappingStatus.PILOT_LINKED,
        matter=pilot.matter("2026_502"),
        source_id=pilot.states["2026_502"].source_id,
        in_pilot=True,
        fields=[("stage", "Ministeeriumis", HIDDEN_VALUE)],
    )
    return pilot


def _dump(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False)


# -- the tool list ---------------------------------------------------------------------


def test_the_five_tools_are_listed_read_only_with_strict_inputs(mcp, reader):
    tools = mcp.tools(reader)
    assert set(LOVABLE_TOOLS) <= set(tools)
    for name in LOVABLE_TOOLS:
        tool = tools[name]
        assert tool["annotations"] == {
            "title": tool["title"],
            "readOnlyHint": True,
            "destructiveHint": False,
            "idempotentHint": True,
            "openWorldHint": False,
        }, name
        assert tool["description"].endswith("Read-only."), name
        assert "never contacted" in tool["description"], name
        for text in (tool["name"], tool["title"], tool["description"]):
            assert not WRITE_WORDS.search(text.replace("_", " ")), (name, text)
        schema = tool["inputSchema"]
        assert schema["type"] == "object" and schema["additionalProperties"] is False, name
        assert tool["outputSchema"]["type"] == "object", name
        meta = tool["_meta"]
        assert meta["securitySchemes"] == [{"type": "oauth2", "scopes": ["juristid.read"]}]
        for key in ("openai/toolInvocation/invoking", "openai/toolInvocation/invoked"):
            assert 0 < len(meta[key]) <= 64, (name, key)

    history = json.dumps(tools["get_lovable_sync_history"]["inputSchema"])
    assert r"^\\d{4}-\\d{2}-\\d{2}$" in history
    page = tools["get_lovable_observed_changes"]["inputSchema"]["properties"]["page"]
    assert '"minimum": 1' in json.dumps(page) and '"maximum": 100' in json.dumps(page)
    population = json.dumps(tools["get_lovable_sync_history"]["inputSchema"])
    assert "PILOT" in population and "ALL_LIVE" in population and "OPERATIONAL" in population
    assert tools["get_lovable_sync_matter_changes"]["inputSchema"]["required"] == ["matter"]
    assert tools["get_lovable_sync_status"]["inputSchema"].get("properties", {}) == {}
    assert tools["get_lovable_sync_readiness"]["inputSchema"].get("properties", {}) == {}


def test_the_filter_vocabularies_are_the_services_own():
    from app.legacy_import import lovable_review
    from app.legacy_import.lovable_observation_models import ObservedChangeKind

    vocabulary = schemas.LOVABLE_VOCABULARY
    assert vocabulary["field"] == tuple(lovable_review.FIELD_ORDER)
    assert vocabulary["outcome"] == tuple(lovable_review.RESULT_LABELS)
    assert vocabulary["kind"] == tuple(ObservedChangeKind.values)
    assert vocabulary["mapping"] == tuple(MappingStatus.values)
    assert schemas.LOVABLE_MAX_WINDOW_DAYS == obs.MAX_WINDOW_DAYS


def test_the_instructions_name_the_tools_and_keep_their_first_512_characters():
    assert INSTRUCTIONS.startswith(ESSENTIALS)
    for name in LOVABLE_TOOLS:
        assert f"- {name}(" in INSTRUCTIONS, name
    assert f"at most {obs.MAX_WINDOW_DAYS} days" in INSTRUCTIONS
    assert INSTRUCTIONS.endswith(" Every tool is read-only.\n")


# -- arguments ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("tool", "arguments", "expected"),
    [
        # Refused by the schema, before any reader runs: field and rule, never the value.
        ("get_lovable_sync_history", {"since": "2026/01/01"}, "since: string_pattern_mismatch"),
        ("get_lovable_sync_history", {"page": 0}, "page: greater_than_equal"),
        ("get_lovable_sync_history", {"page": 101}, "page: less_than_equal"),
        ("get_lovable_sync_history", {"page": "1"}, "page: int_type"),
        ("get_lovable_sync_history", {"page": True}, "page: int_type"),
        ("get_lovable_sync_history", {"population": "EVERYTHING"}, "population: literal_error"),
        ("get_lovable_sync_history", {"outcome": "MAYBE"}, "outcome: literal_error"),
        ("get_lovable_sync_history", {"field": "password"}, "field: literal_error"),
        ("get_lovable_sync_history", {"reference": "2026_243"}, "reference: extra_forbidden"),
        ("get_lovable_observed_changes", {"kind": "GONE"}, "kind: literal_error"),
        ("get_lovable_observed_changes", {"mapping": "ANY"}, "mapping: literal_error"),
        ("get_lovable_observed_changes", {"until": "yesterday"}, "until: string_too_short"),
        ("get_lovable_observed_changes", {"page": 10**9}, "page: less_than_equal"),
        ("get_lovable_sync_matter_changes", {}, "matter: missing"),
        ("get_lovable_sync_matter_changes", {"matter": ""}, "matter: string_too_short"),
        ("get_lovable_sync_matter_changes", {"matter": "9" * 201}, "matter: string_too_long"),
        ("get_lovable_sync_status", {"user": "marko"}, "user: extra_forbidden"),
        ("get_lovable_sync_readiness", {"sql": "select 1"}, "sql: extra_forbidden"),
    ],
)
def test_a_malformed_argument_is_refused_by_the_schema(mcp, reader, tool, arguments, expected):
    text = mcp.refused(reader, tool, arguments)
    assert text.startswith(f"Invalid arguments for {tool}: "), text
    assert expected in text, text
    for value in arguments.values():
        if isinstance(value, str) and len(value) > 3:
            assert value not in text


def test_a_window_the_service_would_quietly_change_is_refused(mcp, reader):
    limit = obs.MAX_WINDOW_DAYS
    for tool in ("get_lovable_sync_history", "get_lovable_observed_changes"):
        too_long = {"since": _iso(-(limit + 1)), "until": _iso(0)}
        assert mcp.refused(reader, tool, too_long) == (f"The window may span at most {limit} days.")
        open_ended = {"since": _iso(-(limit + 9))}
        assert mcp.refused(reader, tool, open_ended) == (
            f"The window may span at most {limit} days."
        )
        reversed_window = {"since": _iso(0), "until": _iso(-1)}
        assert mcp.refused(reader, tool, reversed_window) == "since must be on or before until."
        assert mcp.refused(reader, tool, {"since": "2026-13-01"}) == (
            "since must be an ISO date (YYYY-MM-DD)."
        )
        exact = mcp.ok(reader, tool, {"since": _iso(-limit), "until": _iso(0)})
        assert exact["page"] == 1
    assert mcp.refused(
        reader, "get_lovable_sync_history", {"population": "ALL_LIVE", "outcome": "REVIEW"}
    ) == ("outcome applies only to population PILOT.")


# -- who may ask -----------------------------------------------------------------------


def _arguments(tool: str) -> dict[str, Any]:
    return {"matter": "2026_501"} if tool == "get_lovable_sync_matter_changes" else {}


def test_a_caller_without_a_business_role_is_refused_like_every_business_tool(mcp, world):
    administrator = factories.AdministratorFactory()
    expected = mcp.refused(administrator, "get_matter", {"matter": "2026_501"})
    assert expected == mcp_server.NOT_FOUND_TEXT
    for tool in LOVABLE_TOOLS:
        text = mcp.refused(administrator, tool, _arguments(tool))
        assert text == expected, tool


def test_a_refused_account_is_unauthorized_like_every_tool(mcp, world):
    gone = mcp.gone_token()
    expected = mcp.call(gone, "search", {"query": "pakend"})
    assert expected["isError"] is True
    assert expected["content"][0]["text"] == mcp_server.UNAUTHORIZED_TEXT
    for tool in LOVABLE_TOOLS:
        result = mcp.call(gone, tool, _arguments(tool))
        assert result["isError"] is True, tool
        assert result["content"][0]["text"] == mcp_server.UNAUTHORIZED_TEXT, tool
        assert result["_meta"]["mcp/www_authenticate"], tool


def test_a_restricted_teema_is_absent_from_every_answer(mcp, world, monkeypatch):
    narrowed = narrowed_reader(monkeypatch)
    answers = {
        "status": mcp.ok(narrowed, "get_lovable_sync_status"),
        "pilot": mcp.ok(narrowed, "get_lovable_sync_history"),
        "pilot_stage": mcp.ok(narrowed, "get_lovable_sync_history", {"field": "stage"}),
        "wider": mcp.ok(narrowed, "get_lovable_sync_history", {"population": "ALL_LIVE"}),
        "observed": mcp.ok(narrowed, "get_lovable_observed_changes"),
        "readiness": mcp.ok(narrowed, "get_lovable_sync_readiness"),
        "open_teema": mcp.ok(narrowed, "get_lovable_sync_matter_changes", {"matter": "2026_501"}),
    }
    dumped = _dump(answers)
    for secret in (HIDDEN_TITLE, HIDDEN_VALUE, "2026_502", UNMATCHED_VALUE):
        assert secret not in dumped, secret
    assert "Valitsuses" in dumped  # the Teema it may open is there
    assert str(world.states["2026_502"].matter_id) not in dumped
    assert answers["pilot"]["items"][0]["hidden"] == 1
    assert answers["wider"]["items"][0]["hidden"] == 1
    assert answers["observed"]["hidden"] == 1
    refused = mcp.refused(narrowed, "get_lovable_sync_matter_changes", {"matter": "2026_502"})
    assert refused == mcp_server.NOT_FOUND_TEXT


def test_unknown_invisible_and_non_pilot_teemad_answer_alike(mcp, world, reader, monkeypatch):
    narrowed = narrowed_reader(monkeypatch)
    outsider = factories.MatterFactory(reference_year=2026, reference_number=600)
    hidden_id = world.states["2026_502"].matter_id
    answers = {
        "unknown reference": mcp.refused(
            reader, "get_lovable_sync_matter_changes", {"matter": "2026_999"}
        ),
        "unknown id": mcp.refused(
            reader, "get_lovable_sync_matter_changes", {"matter": str(uuid.uuid4())}
        ),
        "not a reference": mcp.refused(
            reader, "get_lovable_sync_matter_changes", {"matter": "pakendiseadus"}
        ),
        "non-pilot reference": mcp.refused(
            reader, "get_lovable_sync_matter_changes", {"matter": "2026_600"}
        ),
        "non-pilot id": mcp.refused(
            reader, "get_lovable_sync_matter_changes", {"matter": f"matter:{outsider.pk}"}
        ),
        "invisible reference": mcp.refused(
            narrowed, "get_lovable_sync_matter_changes", {"matter": "2026_502"}
        ),
        "invisible id": mcp.refused(
            narrowed, "get_lovable_sync_matter_changes", {"matter": str(hidden_id)}
        ),
    }
    assert set(answers.values()) == {mcp_server.NOT_FOUND_TEXT}, answers
    # The same Teema, for a reader who may open it, by reference and by either id form.
    by_reference = mcp.ok(reader, "get_lovable_sync_matter_changes", {"matter": "2026/502"})
    by_id = mcp.ok(reader, "get_lovable_sync_matter_changes", {"matter": f"matter:{hidden_id}"})
    assert by_reference == by_id
    assert by_reference["title"] == HIDDEN_TITLE


# -- the answers are the service's ---------------------------------------------------------


def _service(value: Any) -> dict[str, Any]:
    """What the reader answers with: the service's dict, links made absolute."""
    answer: dict[str, Any] = readers._absolute_links(value.as_dict())
    return answer


def _without_bounds(answer: dict[str, Any]) -> dict[str, Any]:
    assert answer.pop("truncated") is False
    assert answer.pop("omitted") == {}
    return answer


def test_every_answer_is_the_services_own(mcp, world, reader):
    status = mcp.ok(reader, "get_lovable_sync_status")
    expected = obs.status(reader)
    assert status["mode"] == expected.mode == SyncMode.COMPARE
    assert status["pending_review"] == expected.pending_review > 0
    assert status["cohort_size"] == expected.cohort_size == 3
    assert status["warnings"] == expected.warnings
    assert [i["key"] for i in status["indicators"]] == [i.key for i in expected.indicators]
    assert status["truncated"] is False and status["omitted"] == {}
    schemas.LovableSyncStatusOutput.model_validate(status)

    history = _without_bounds(mcp.ok(reader, "get_lovable_sync_history"))
    assert history.pop("has_more") is False
    assert history == _service(obs.nights(reader))
    assert {t["reference"] for t in history["items"][0]["topics"]} == {
        "2026_243",
        "2026_501",
        "2026_502",
    }
    review = mcp.ok(reader, "get_lovable_sync_history", {"outcome": "REVIEW"})
    assert (
        review["items"][0]["topics"]
        == _service(obs.nights(reader, outcome="REVIEW"))["items"][0]["topics"]
    )

    observed_page = _without_bounds(mcp.ok(reader, "get_lovable_observed_changes"))
    assert observed_page.pop("has_more") is False
    assert observed_page == _service(obs.observed_changes(reader))
    assert observed_page["totals"]["mapping"] == {"PILOT_LINKED": 2}
    # The unlinked topic is only counted (docs/adr/0201 §6, AGGREGATE).
    assert observed_page["unlinked"]["total"] == 1
    assert observed_page["unlinked"]["mapping"] == {"UNMATCHED": 1}
    assert "2026_900" not in _dump(observed_page)
    schemas.LovableObservedChangesOutput.model_validate({**observed_page, "has_more": False})
    narrowed = mcp.ok(reader, "get_lovable_observed_changes", {"mapping": "UNMATCHED"})
    assert narrowed["items"] == [] and narrowed["unlinked"]["total"] == 1
    assert UNMATCHED_VALUE not in _dump(narrowed) and "2026_900" not in _dump(narrowed)
    wider = mcp.ok(reader, "get_lovable_sync_history", {"population": "ALL_LIVE"})
    assert "2026_900" not in _dump(wider)

    changes = _without_bounds(
        mcp.ok(reader, "get_lovable_sync_matter_changes", {"matter": "2026_243"})
    )
    assert changes == _service(obs.matter_changes(reader, "2026_243"))
    assert changes["matter_url"].startswith(fx.ORIGIN + "/")
    fields = {f["field"]: f for f in changes["fields"]}
    assert fields["next_action_text"]["lovable_observed"] == "Jälgida menetlust"
    schemas.LovableSyncMatterChangesOutput.model_validate(changes)

    readiness = _without_bounds(mcp.ok(reader, "get_lovable_sync_readiness"))
    expected_readiness = _service(obs.readiness(reader))
    readiness.pop("generated_at")
    expected_readiness.pop("generated_at")
    assert readiness == expected_readiness
    schemas.LovableSyncReadinessOutput.model_validate({**readiness, "generated_at": "x"})


def test_unlinked_topics_are_listed_by_field_names_only_under_the_owner_policy(
    mcp, world, reader, settings
):
    settings.LOVABLE_UNLINKED_DETAIL = "FIELD_NAMES"
    narrowed = mcp.ok(reader, "get_lovable_observed_changes", {"mapping": "UNMATCHED"})
    [unmatched] = narrowed["items"]
    assert unmatched["reference"] == "2026_900"
    assert unmatched["title"] == "" and all(f["withheld"] for f in unmatched["fields"])
    assert UNMATCHED_VALUE not in _dump(narrowed)
    assert narrowed["unlinked"]["policy"] == "FIELD_NAMES"
    assert narrowed["unlinked"]["total"] == 0


def test_a_page_past_the_end_is_empty_rather_than_the_last_again(mcp, world, reader):
    first = mcp.ok(reader, "get_lovable_sync_history")
    assert first["pages"] == 1 and first["items"] and first["has_more"] is False
    beyond = mcp.ok(reader, "get_lovable_sync_history", {"page": 5})
    assert (beyond["page"], beyond["items"], beyond["has_more"]) == (5, [], False)
    assert beyond["total"] == first["total"]
    beyond = mcp.ok(reader, "get_lovable_observed_changes", {"page": 100})
    assert (beyond["page"], beyond["items"], beyond["total"]) == (100, [], 2)


def test_a_large_answer_is_cut_from_the_end_and_says_so(world, reader, monkeypatch, settings):
    fx.configure(settings)
    # Unlinked topics are items only under the owner-approvable FIELD_NAMES policy.
    settings.LOVABLE_UNLINKED_DETAIL = "FIELD_NAMES"
    run = observation_run(timezone.now() - dt.timedelta(hours=2))
    for number in range(30):
        observed(run, f"2026_{700 + number}", field_names=["title", "stage", "owner"])
    whole = readers.lovable_observed_changes(reader)
    assert whole["truncated"] is False and len(whole["items"]) == 33
    budget = len(json.dumps(whole, ensure_ascii=False)) // 3
    monkeypatch.setattr(readers, "LOVABLE_ANSWER_MAX_CHARS", budget)
    cut = readers.lovable_observed_changes(reader)
    assert cut["truncated"] is True
    assert cut["omitted"] == {"items": 33 - len(cut["items"])}
    assert 0 < len(cut["items"]) < 33
    assert cut["items"] == whole["items"][: len(cut["items"])]  # deterministic: a prefix
    assert len(json.dumps(cut, ensure_ascii=False)) <= budget
    assert cut["total"] == whole["total"]  # the counts still say how many exist

    history = readers.lovable_sync_history(reader, population="ALL_LIVE")
    monkeypatch.setattr(
        readers, "LOVABLE_ANSWER_MAX_CHARS", len(json.dumps(history, ensure_ascii=False)) // 2
    )
    cut_history = readers.lovable_sync_history(reader, population="ALL_LIVE")
    assert cut_history["truncated"] is True and set(cut_history["omitted"]) == {"topics"}
    # The newest reading keeps its topics; the oldest gives way first.
    assert len(cut_history["items"]) == len(history["items"])

    monkeypatch.setattr(readers, "LOVABLE_ANSWER_MAX_CHARS", 50)
    with pytest.raises(readers.ReaderError) as refused:
        readers.lovable_sync_status(reader)
    assert str(refused.value) == readers.TOO_LARGE_TEXT


def test_the_readers_refuse_like_the_others_when_called_directly(world):
    administrator = factories.AdministratorFactory()
    for call in (
        lambda: readers.lovable_sync_status(administrator),
        lambda: readers.lovable_sync_history(administrator),
        lambda: readers.lovable_observed_changes(administrator),
        lambda: readers.lovable_sync_matter_changes(administrator, "2026_501"),
        lambda: readers.lovable_sync_readiness(administrator),
    ):
        with pytest.raises(readers.NotFound):
            call()


# -- nothing is written ---------------------------------------------------------------------


def test_no_tool_writes_anything(mcp, world, reader):
    calls = [
        ("get_lovable_sync_status", {}),
        ("get_lovable_sync_history", {}),
        ("get_lovable_sync_history", {"population": "OPERATIONAL", "field": "stage"}),
        ("get_lovable_observed_changes", {"kind": "CHANGED"}),
        ("get_lovable_sync_matter_changes", {"matter": "2026_243"}),
        ("get_lovable_sync_matter_changes", {"matter": "2026_999"}),
        ("get_lovable_sync_readiness", {}),
    ]
    token = mcp.token_for(reader)
    before = row_counts()
    with CaptureQueriesContext(connection) as queries:
        for name, arguments in calls:
            mcp.call(token, name, arguments)
    assert write_statements(queries.captured_queries) == []
    assert row_counts() == before


# -- the call log ----------------------------------------------------------------------------


def test_the_call_log_names_the_tool_and_counts_never_the_content(mcp, world, reader, caplog):
    caplog.set_level("INFO", logger="app.mcp_server.calls")
    mcp.ok(reader, "get_lovable_observed_changes")
    mcp.refused(reader, "get_lovable_sync_matter_changes", {"matter": "2026_999"})
    lines = [r.getMessage() for r in caplog.records if r.name == "app.mcp_server.calls"]
    logged = [json.loads(line.split(" ", 1)[1]) for line in lines]
    assert [(e["tool"], e["outcome"], e["result_count"]) for e in logged] == [
        ("get_lovable_observed_changes", "ok", 2),
        ("get_lovable_sync_matter_changes", "not_found", None),
    ]
    assert "2026_" not in " ".join(lines)


# -- `lovable_sync report` --------------------------------------------------------------------


def test_the_report_command_prints_counts_and_codes_only(world, reader):
    before = row_counts()
    out = io.StringIO()
    with CaptureQueriesContext(connection) as queries:
        call_command("lovable_sync", "report", "--json", stdout=out)
    assert write_statements(queries.captured_queries) == []
    assert row_counts() == before
    text = out.getvalue()
    report = json.loads(text)
    status = obs.status(reader)
    assert report["status"]["mode"] == SyncMode.COMPARE
    assert report["status"]["pending_review"] == status.pending_review > 0
    assert report["status"]["cohort_size"] == 3
    assert report["status"]["warnings"] == len(status.warnings)
    assert set(report["status"]["indicators"]) == {i.key for i in status.indicators}
    readiness = obs.readiness(reader)
    assert report["readiness"]["simulation"] == readiness.simulation
    assert report["readiness"]["simulation_rows"] == 0  # asked as nobody: no row
    assert report["readiness"]["blockers"] == len(readiness.blockers)
    assert set(report["readiness"]["fields"]) == {f.field for f in readiness.fields}
    for secret in (
        HIDDEN_TITLE,
        HIDDEN_VALUE,
        "Näidisteema",
        "Valitsuses",
        "Jälgida",
        "Mari Näidis",
        UNMATCHED_VALUE,
        "2026_",
    ):
        assert secret not in text, secret
    # No sentence at all: every string is a code or an ISO moment.
    strings: list[str] = []

    def walk(node: Any) -> None:
        if isinstance(node, dict):
            for value in node.values():
                walk(value)
        elif isinstance(node, str):
            strings.append(node)

    walk(report)
    assert strings and all(
        re.fullmatch(r"[A-Z][A-Z0-9_]*|\d{4}-\d{2}-\d{2}.*", value) for value in strings
    ), strings
