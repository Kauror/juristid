"""A colleague changes Juristid from ChatGPT, end to end, with nothing faked (docs/adr/0189).

The whole chain of MCP Phase 2, each piece the production code:

* the operator's ``mcp_access grant`` / ``grant-write`` commands;
* enrolment and the OAuth consent asking for ``juristid.read juristid.write``,
  through the authorization server's services, and the code exchange;
* the MCP application (`app.mcp_server.server.build_app`) over httpx2's
  `ASGITransport`, its write tools and their gateway client
  (`app.mcp_server.write_client`), whose transport is pointed at
* the write gateway (`app.mcp_writes.gateway.build_app`) in-process — the one
  substitution, standing in for the `mcp_write` network;
* Juristid's approval page, walked with the test `Client` as a browser walks it
  (the shared gate, the person's own persona, their authenticator code);
* the ten canonical operations and the canonical services under them;
* the read tools, which must show every change afterwards.

Only the TOTP clock is pinned, so a code at a known step is a known code.
Transactional, because the MCP server and the gateway work in worker threads
with connections of their own.
"""

from __future__ import annotations

import io
from datetime import timedelta
from typing import Any
from urllib.parse import parse_qs, urlsplit

import httpx2
import pytest
from django.core.management import call_command
from django.http import QueryDict
from django.test import Client
from django.utils import timezone

from app.audit.enums import AuthMethod
from app.audit.models import ChangeEvent
from app.matters.models import Entry, Matter, MatterResponseDeadline
from app.mcp_access import credentials, oauth, services
from app.mcp_server import write_client
from app.mcp_server.runtime import ReaderRuntime
from app.mcp_writes import gateway
from app.mcp_writes.models import McpWriteProposal, ProposalStatus
from tests import factories
from tests import mcp_eval_world as w
from tests.gate import PASSWORD, apply_shared_gate


def local_today() -> Any:
    return timezone.localdate()


def _ok(mcp: w.Mcp, name: str, arguments: dict[str, Any]) -> dict[str, Any]:
    """A successful call's structuredContent (write results carry an instruction in the text)."""
    result = mcp.call(name, arguments)
    assert not result.get("isError"), result
    content: dict[str, Any] = result["structuredContent"]
    return content


pytestmark = pytest.mark.django_db(transaction=True, serialized_rollback=True)

GATE = "/konto/varav/"
SWITCH_PERSONA = "/konto/kasutaja/vaheta/"
GATEWAY_KEY = "g" * 48
READ_WRITE = "juristid.read juristid.write"
NOTE = "Ministeerium lubas saata eelnõu muudetud versiooni novembris."


@pytest.fixture(autouse=True)
def deployment(settings, monkeypatch):
    apply_shared_gate(settings)
    w.apply_mcp_settings(
        settings,
        MCP_WRITES_ENABLED=True,
        MCP_WRITE_GATEWAY_KEY=GATEWAY_KEY,
        MCP_WRITE_GATEWAY_URL="http://mcp-writer:8002",
        MCP_WRITE_LISTEN_PORT=8002,
        MCP_WRITE_MAX_CONCURRENT_CALLS=2,
        MCP_WRITE_CALL_TIMEOUT_SECONDS=60,
    )
    app = gateway.build_app(runtime=ReaderRuntime(max_concurrent_calls=2, timeout_seconds=60))
    monkeypatch.setattr(write_client, "TRANSPORT", httpx2.ASGITransport(app=app))


def _connect(user: Any, moment: Any, *, write: bool = True) -> tuple[str, str]:
    """Grant (and grant write), enrol and authorize asking for read+write; the token and secret.

    The enrolment spends the step before ``moment``, the authorization the step
    of ``moment``; the approvals below use later steps.
    """
    out = io.StringIO()
    call_command("mcp_access", "grant", "--user", user.upn, stdout=out)
    [link] = [line for line in out.getvalue().splitlines() if line.startswith("https://")]
    if write:
        call_command("mcp_access", "grant-write", "--user", user.upn, stdout=io.StringIO())
    enrolment = credentials.new_enrolment(services.account_label(user))
    assert services.confirm_enrolment(
        proof=credentials.link_proof(parse_qs(urlsplit(link).query)["kood"][0]),
        user=user,
        sealed=enrolment.sealed,
        code=w.code_at(enrolment.secret, moment, -1),
        ip_address=None,
    )
    query = QueryDict(mutable=True)
    query.update(w.authorize_params(scope=READ_WRITE))
    request = oauth.parse_authorization(query)
    assert isinstance(request, services.AuthorizationRequest), request
    outcome = services.authorize(
        user=user, request=request, totp_code=w.code_at(enrolment.secret, moment), ip_address=None
    )
    assert outcome.status == "ok", outcome
    tokens = services.exchange_code(
        client_id=w.CHATGPT_CLIENT,
        code=outcome.code,
        redirect_uri=w.CHATGPT_REDIRECT,
        code_verifier=w.PKCE_VERIFIER,
        resource=w.TUNNEL_RESOURCE,
        ip_address=None,
    )
    assert isinstance(tokens, services.IssuedTokens), tokens
    return tokens.access_token, enrolment.secret


def _as(client: Client, user: Any) -> None:
    assert client.post(GATE, {"password": PASSWORD}).status_code == 302
    assert client.post(SWITCH_PERSONA, {"user_id": str(user.pk)}).status_code == 302


def _path(url: str) -> str:
    parts = urlsplit(url)
    assert f"{parts.scheme}://{parts.netloc}" == w.ORIGIN, url
    return parts.path


def _prepare(mcp: w.Mcp, operation: str, arguments: dict[str, Any]) -> dict[str, Any]:
    answer = _ok(mcp, f"prepare_{operation}", arguments)
    assert answer["status"] == "prepared", answer
    assert answer["approval_url"].startswith(f"{w.ORIGIN}/mcp-ligipaas/muudatus/")
    return answer


def _approve(client: Client, prepared: dict[str, Any], code: str | None) -> str:
    page = client.get(_path(prepared["approval_url"]))
    assert page.status_code == 200, page.content.decode()[:600]
    form = {"otsus": "kinnita"}
    if code is not None:
        form["kood"] = code
    answered = client.post(_path(prepared["approval_url"]), form)
    text = answered.content.decode()
    assert answered.status_code == 200, text[:800]
    return text


def _commit(mcp: w.Mcp, prepared: dict[str, Any]) -> dict[str, Any]:
    return _ok(mcp, "commit_prepared_action", {"proposal_id": prepared["proposal_id"]})


def _ai_rows(user: Any) -> list[ChangeEvent]:
    return list(ChangeEvent.objects.filter(auth_method=AuthMethod.MCP).order_by("created_at"))


# -- the whole round, the brief's acceptance list ------------------------------------------


def test_a_lawyer_prepares_approves_and_commits_each_change_and_reads_it_back(client, monkeypatch):
    moment = w.freeze_totp(monkeypatch)
    mari = factories.UserFactory(upn="mari.maasikas@example.invalid", display_name="Mari Maasikas")
    marko = factories.DepartmentHeadFactory(
        upn="marko.mets@example.invalid", display_name="Marko Mets"
    )
    matter = factories.MatterFactory(owner=mari, title="Pakendiseaduse muutmine")
    matter.refresh_from_db()
    reference = matter.display_reference
    token, secret = _connect(mari, moment)
    mcp = w.Mcp(token, host=w.TUNNEL_CLIENT_HOST)
    _as(client, mari)

    tools = {tool["name"]: tool for tool in mcp.rpc("tools/list", {}).json()["result"]["tools"]}
    assert w.TOOL_NAMES <= set(tools)
    assert tools["commit_prepared_action"]["annotations"]["destructiveHint"] is True
    assert tools["prepare_add_matter_note"]["annotations"]["readOnlyHint"] is False

    # 3. A note: prepared, nothing saved; committed only after the person approves.
    yesterday = (local_today() - timedelta(days=1)).isoformat()
    entries_before = Entry.objects.filter(matter=matter).count()
    note = _prepare(
        mcp, "add_matter_note", {"matter": reference, "text": NOTE, "occurred_on": yesterday}
    )
    assert Entry.objects.filter(matter=matter).count() == entries_before
    waiting = _commit(mcp, note)
    assert waiting["status"] == "awaiting_approval"
    assert Entry.objects.filter(matter=matter).count() == entries_before

    approved = _approve(client, note, w.code_at(secret, moment, 1))
    assert "Kinnitatud" in approved
    done = _commit(mcp, note)
    assert done["status"] == "executed" and done["replayed"] is False
    assert Entry.objects.filter(matter=matter).count() == entries_before + 1
    # 2. Committing again (a retry after a timeout) saves nothing more.
    again = _commit(mcp, note)
    assert again["status"] == "executed" and again["replayed"] is True
    assert again["record_id"] == done["record_id"]
    assert Entry.objects.filter(matter=matter).count() == entries_before + 1
    saved = Entry.objects.get(pk=done["record_id"])
    assert saved.author_id == mari.pk and "novembris" in saved.body

    # The audit row names the person, through ChatGPT, individually confirmed.
    row = _ai_rows(mari)[-1]
    assert row.auth_method == AuthMethod.MCP
    assert row.authenticated_principal_id == mari.pk
    assert row.acting_persona_id is None

    # 12. The returned link opens in the browser.
    assert client.get(_path(done["url"])).status_code == 200

    # 4. A meeting — approved with the code typed a moment ago (same gate session).
    meeting = _prepare(
        mcp,
        "record_meeting",
        {"matter": reference, "summary": "Kohtusime ministeeriumiga.", "held_on": yesterday},
    )
    _approve(client, meeting, None)
    assert _commit(mcp, meeting)["status"] == "executed"

    # 6. A WAIT step with a review date (not an opinion deadline).
    review = (local_today() + timedelta(days=20)).isoformat()
    wait = _prepare(
        mcp,
        "set_next_action",
        {
            "matter": reference,
            "mode": "new",
            "text": "Ootame Rahandusministeeriumi vastust",
            "kind": "wait",
            "date": review,
            "date_precision": "exact",
            "date_meaning": "review_on",
            "waiting_for": "Rahandusministeerium",
        },
    )
    _approve(client, wait, None)
    assert _commit(mcp, wait)["status"] == "executed"
    matter.refresh_from_db()
    assert matter.response_deadline is None

    # 8. A newly requested opinion deadline, then 9. the same request moved.
    first_deadline = (local_today() + timedelta(days=30)).isoformat()
    requested = _prepare(
        mcp,
        "response_deadline",
        {"matter": reference, "mode": "new_request", "deadline": first_deadline},
    )
    _approve(client, requested, None)
    assert _commit(mcp, requested)["status"] == "executed"
    rounds = MatterResponseDeadline.objects.filter(matter=matter).count()
    moved_to = (local_today() + timedelta(days=40)).isoformat()
    moved = _prepare(
        mcp, "response_deadline", {"matter": reference, "mode": "move", "deadline": moved_to}
    )
    _approve(client, moved, None)
    assert _commit(mcp, moved)["status"] == "executed"
    matter.refresh_from_db()
    assert matter.response_deadline.isoformat() == moved_to
    assert MatterResponseDeadline.objects.filter(matter=matter).count() >= rounds

    # 10. An important date at month precision stays a month.
    dated = _prepare(
        mcp,
        "add_important_date",
        {
            "matter": reference,
            "title": "Eeldatav jõustumine",
            "date": (local_today() + timedelta(days=120)).strftime("%Y-%m"),
            "date_precision": "month",
            "kind": "estimated_commencement",
        },
    )
    _approve(client, dated, None)
    assert _commit(mcp, dated)["status"] == "executed"

    # 5. The department head hands the Matter to Marko: Mari may assign, so may she.
    assign = _prepare(
        mcp,
        "assign_matter",
        {"matter": reference, "action": "assign", "responsible_person": "Marko Mets"},
    )
    _approve(client, assign, None)
    assert _commit(mcp, assign)["status"] == "executed"
    matter.refresh_from_db()
    assert matter.owner_id == marko.pk

    # 1. One new Matter, not two, however often the commit is repeated.
    matters_before = Matter.objects.count()
    created = _prepare(mcp, "create_matter", {"title": "Töölepingu seaduse muudatused"})
    _approve(client, created, None)
    first = _commit(mcp, created)
    second = _commit(mcp, created)
    assert first["status"] == second["status"] == "executed"
    assert second["replayed"] is True
    assert Matter.objects.count() == matters_before + 1
    new_matter = Matter.objects.get(title="Töölepingu seaduse muudatused")
    assert new_matter.response_deadline is None

    # 11. Every change is visible through the read tools.
    snapshot = mcp.ok("get_matter", {"matter": reference})
    assert snapshot["owner"] == "Marko Mets"
    activity = mcp.ok("get_matter_activity", {"matter": reference})
    assert any("novembris" in (item.get("text") or "") for item in activity["items"])
    assert mcp.ok("get_matter", {"matter": new_matter.display_reference})["title"]

    executed = McpWriteProposal.objects.filter(user=mari, status=ProposalStatus.EXECUTED)
    assert executed.count() == 8


# -- the boundaries that only the whole chain can show ------------------------------------


def test_a_read_only_connection_is_asked_to_step_up_and_nothing_is_prepared(monkeypatch):
    moment = w.freeze_totp(monkeypatch)
    liis = factories.UserFactory(upn="liis.kask@example.invalid")
    matter = factories.MatterFactory(owner=liis)
    matter.refresh_from_db()
    token, _secret = _connect(liis, moment, write=False)
    mcp = w.Mcp(token, host=w.TUNNEL_CLIENT_HOST)
    result = mcp.call(
        "prepare_add_matter_note",
        {"matter": matter.display_reference, "text": NOTE, "occurred_on": "2026-10-01"},
    )
    assert result["isError"] is True
    [challenge] = result["_meta"]["mcp/www_authenticate"]
    assert 'error="insufficient_scope"' in challenge and READ_WRITE in challenge
    assert not McpWriteProposal.objects.exists()


def test_an_administrator_without_a_business_role_never_gets_write():
    admin = factories.AdministratorFactory(upn="haldur@example.invalid")
    with pytest.raises(Exception):  # noqa: B017 - the command's own refusal, any shape
        call_command("mcp_access", "grant", "--user", admin.upn, stdout=io.StringIO())
    with pytest.raises(Exception):  # noqa: B017
        call_command("mcp_access", "grant-write", "--user", admin.upn, stdout=io.StringIO())


def test_revoke_write_between_approval_and_commit_stops_the_change(client, monkeypatch):
    moment = w.freeze_totp(monkeypatch)
    mari = factories.UserFactory(upn="mari.maasikas@example.invalid")
    matter = factories.MatterFactory(owner=mari)
    matter.refresh_from_db()
    token, secret = _connect(mari, moment)
    mcp = w.Mcp(token, host=w.TUNNEL_CLIENT_HOST)
    _as(client, mari)
    before = Entry.objects.filter(matter=matter).count()
    note = _prepare(
        mcp,
        "add_matter_note",
        {"matter": matter.display_reference, "text": NOTE, "occurred_on": "2026-10-01"},
    )
    _approve(client, note, w.code_at(secret, moment, 1))
    call_command(
        "mcp_access", "revoke-write", "--user", mari.upn, "--reason", "test", stdout=io.StringIO()
    )
    result = mcp.call("commit_prepared_action", {"proposal_id": note["proposal_id"]})
    assert result["isError"] is True
    assert Entry.objects.filter(matter=matter).count() == before


def test_another_persons_token_cannot_commit_or_see_a_proposal(client, monkeypatch):
    moment = w.freeze_totp(monkeypatch)
    mari = factories.UserFactory(upn="mari.maasikas@example.invalid")
    liis = factories.UserFactory(upn="liis.kask@example.invalid")
    matter = factories.MatterFactory(owner=mari)
    matter.refresh_from_db()
    mari_token, secret = _connect(mari, moment)
    liis_token, _liis_secret = _connect(liis, moment)
    _as(client, mari)
    note = _prepare(
        w.Mcp(mari_token, host=w.TUNNEL_CLIENT_HOST),
        "add_matter_note",
        {"matter": matter.display_reference, "text": NOTE, "occurred_on": "2026-10-01"},
    )
    _approve(client, note, w.code_at(secret, moment, 1))
    before = Entry.objects.filter(matter=matter).count()
    stolen = w.Mcp(liis_token, host=w.TUNNEL_CLIENT_HOST).call(
        "commit_prepared_action", {"proposal_id": note["proposal_id"]}
    )
    assert stolen["isError"] is True
    assert Entry.objects.filter(matter=matter).count() == before
    # Liis's persona cannot open Mari's approval page either.
    other = Client()
    _as(other, liis)
    assert "Kinnita" not in other.get(_path(note["approval_url"])).content.decode()


def test_an_approval_is_refused_for_a_stale_matter_and_nothing_is_saved(client, monkeypatch):
    moment = w.freeze_totp(monkeypatch)
    mari = factories.UserFactory(upn="mari.maasikas@example.invalid")
    other = factories.UserFactory(upn="teine@example.invalid", display_name="Teine Kolleeg")
    matter = factories.MatterFactory(owner=mari)
    matter.refresh_from_db()
    token, secret = _connect(mari, moment)
    mcp = w.Mcp(token, host=w.TUNNEL_CLIENT_HOST)
    _as(client, mari)
    assign = _prepare(
        mcp,
        "assign_matter",
        {"matter": matter.display_reference, "action": "assign", "responsible_person": "Teine"},
    )
    _approve(client, assign, w.code_at(secret, moment, 1))
    # Meanwhile somebody hands the Matter on in the browser.
    from app.matters.services import assign_matter

    assign_matter(matter=Matter.objects.get(pk=matter.pk), owner=other, actor=mari)
    result = mcp.call("commit_prepared_action", {"proposal_id": assign["proposal_id"]})
    assert result["isError"] is True
    assert McpWriteProposal.objects.get(pk=assign["proposal_id"]).status == ProposalStatus.STALE
    assert not ChangeEvent.objects.filter(auth_method=AuthMethod.MCP).exists()
    assert timezone.now()  # the clock was never needed beyond the TOTP pin


# -- one correlation id from the tool call to the audit log (audit 2026-10-28 C-07) --------


def test_one_correlation_id_ties_tool_call_proposal_approval_commit_and_audit(
    client, monkeypatch, caplog
):
    import json
    import logging

    from app.mcp_writes.models import McpWriteApproval, McpWriteEvent, McpWriteEventType

    caplog.set_level(logging.INFO, logger="app.mcp_server.calls")
    moment = w.freeze_totp(monkeypatch)
    mari = factories.UserFactory(upn="mari.maasikas@example.invalid")
    matter = factories.MatterFactory(owner=mari)
    matter.refresh_from_db()
    token, secret = _connect(mari, moment)
    mcp = w.Mcp(token, host=w.TUNNEL_CLIENT_HOST)
    _as(client, mari)

    note = _prepare(
        mcp,
        "add_matter_note",
        {"matter": matter.display_reference, "text": NOTE, "occurred_on": "2026-10-01"},
    )
    _approve(client, note, w.code_at(secret, moment, 1))
    _commit(mcp, note)

    calls = [
        json.loads(r.getMessage().split(" ", 1)[1])
        for r in caplog.records
        if r.getMessage().startswith("mcp_tool_call ")
    ]
    prepare_call, commit_call = calls[-2], calls[-1]
    assert (prepare_call["tool"], commit_call["tool"]) == (
        "prepare_add_matter_note",
        "commit_prepared_action",
    )
    # The tool log carries no proposal id (only ids of its own call); the
    # proposal and its events are found by the call's correlation id.
    assert "proposal_id" not in prepare_call
    proposal = McpWriteProposal.objects.get(pk=note["proposal_id"])
    # The prepare tool call's own id is the proposal's.
    assert str(proposal.correlation_id) == prepare_call["correlation_id"]
    events = {e.event_type: e for e in McpWriteEvent.objects.filter(proposal=proposal)}
    for kind in (
        McpWriteEventType.PREPARED,
        McpWriteEventType.APPROVED,
        McpWriteEventType.COMMITTED,
    ):
        assert events[kind].correlation_id == proposal.correlation_id, kind
    # The commit tool call is found from the commit event, and the business rows
    # the commit wrote carry the proposal's id.
    assert (
        events[McpWriteEventType.COMMITTED].detail["call_correlation_id"]
        == (commit_call["correlation_id"])
    )
    assert McpWriteApproval.objects.get(proposal=proposal).user == mari
    business = [row for row in _ai_rows(mari) if row.matter_id == matter.pk]
    assert business and {row.correlation_id for row in business} == {proposal.correlation_id}
