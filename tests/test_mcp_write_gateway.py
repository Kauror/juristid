"""The write gateway over HTTP: Host, size, key, JSON, token — in that order (docs/adr/0189).

`app.mcp_writes.gateway.build_app()` driven over httpx2's `ASGITransport`, the
way the `mcp` process's client will call it. The checks that need no
database run without one; the chain through the token, prepare and commit is
transactional, because the gateway does its database work in worker threads
with connections of their own and must see what the test wrote.
"""

from __future__ import annotations

import asyncio
import io
import json
import logging
from datetime import timedelta
from typing import Any

import httpx2
import pytest
from django.core.management import call_command
from django.core.management.base import CommandError
from django.utils import timezone

from app.mcp_server.runtime import ReaderRuntime
from app.mcp_writes import gateway
from app.mcp_writes.models import (
    ApprovalMethod,
    McpWriteApproval,
    McpWriteProposal,
    ProposalStatus,
)
from tests import factories
from tests import test_mcp_write_proposals_support as support

HOST = "mcp-writer:8002"

#: The gateway's worker threads open connections of their own; what the test
#: wrote must be committed for them to see it. Seeded reference data is put
#: back afterwards, so later tests find the world they expect.
transactional = pytest.mark.django_db(transaction=True, serialized_rollback=True)
SECRET_ARGUMENT = "SALAJANE-MÄRKUS-4417"  # noqa: S105 - record text, not a credential


@pytest.fixture(autouse=True)
def writes_on(settings):
    support.enable(settings)
    settings.MCP_WRITE_LISTEN_PORT = 8002
    settings.MCP_WRITE_MAX_REQUEST_BYTES = 64 * 1024


def call(
    method: str,
    path: str,
    *,
    body: Any = None,
    raw: bytes | None = None,
    key: str | None = support.GATEWAY_KEY,
    token: str | None = "x" * 43,
    host: str = HOST,
    headers: dict[str, str] | None = None,
) -> httpx2.Response:
    sent = {"Content-Type": "application/json"}
    if key is not None:
        sent["X-Juristid-Gateway-Key"] = key
    if token is not None:
        sent["Authorization"] = f"Bearer {token}"
    sent.update(headers or {})
    content = raw if raw is not None else (json.dumps(body).encode() if body is not None else b"")
    app = gateway.build_app(runtime=ReaderRuntime(max_concurrent_calls=2, timeout_seconds=20))

    async def go() -> httpx2.Response:
        transport = httpx2.ASGITransport(app=app)
        async with httpx2.AsyncClient(transport=transport, base_url=f"http://{host}") as http:
            return await http.request(method, path, content=content, headers=sent)

    return asyncio.run(go())


def refused(response: httpx2.Response, status: int, code: str) -> dict[str, Any]:
    assert response.status_code == status, response.text
    payload = response.json()
    assert payload["status"] == "refused" and payload["code"] == code, payload
    assert response.headers["cache-control"].startswith("no-store")
    return payload


# -- before any database work -------------------------------------------------------------


def test_healthz_answers_without_key_or_token():
    response = call("GET", "/healthz", key=None, token=None)
    assert response.status_code == 200 and response.json() == {"status": "ok"}


@pytest.mark.parametrize("host", ["evil.example.test", "mcp-writer:9999", "mcp", "mcp:8001"])
def test_a_host_off_the_list_is_refused_first(host):
    response = call("POST", "/v1/prepare", body={}, key=None, host=host)
    assert response.status_code == 421


@pytest.mark.parametrize(
    "host", ["mcp-writer", "mcp-writer:8002", "localhost:8002", "127.0.0.1:8002"]
)
def test_the_listed_hosts_reach_the_gateway(host):
    assert call("GET", "/healthz", host=host).status_code == 200


def test_a_missing_key_is_401_and_a_wrong_key_403():
    refused(call("POST", "/v1/prepare", body={}, key=None), 401, "unauthorized")
    refused(call("POST", "/v1/prepare", body={}, key="w" * 40), 403, "forbidden")
    refused(call("POST", "/v1/commit", body={}, key=support.GATEWAY_KEY[:-1]), 403, "forbidden")


@pytest.mark.parametrize(
    "configure",
    [
        {"MCP_WRITES_ENABLED": False},
        {"MCP_WRITE_GATEWAY_KEY": ""},
        {"MCP_WRITE_GATEWAY_KEY": "k" * 31},
        {"MCP_ENABLED": False},
    ],
)
def test_writes_off_or_no_usable_key_refuses_every_call(settings, configure):
    for name, value in configure.items():
        setattr(settings, name, value)
    key = settings.MCP_WRITE_GATEWAY_KEY or support.GATEWAY_KEY
    refused(call("POST", "/v1/prepare", body={}, key=key), 403, "writes_disabled")
    refused(call("POST", "/v1/commit", body={}, key=key), 403, "writes_disabled")


def test_an_oversized_body_is_413(settings):
    settings.MCP_WRITE_MAX_REQUEST_BYTES = 1024
    big = {"operation": "x", "arguments": {"text": "y" * 2000}}
    refused(call("POST", "/v1/prepare", body=big), 413, "too_large")
    # Even before the key is looked at, when the length is declared.
    refused(call("POST", "/v1/prepare", body=big, key=None), 413, "too_large")


@pytest.mark.parametrize(
    "raw", [b"{not json", b"[1, 2]", b'"text"', b"", '{"a": "\xff"}'.encode("latin-1")]
)
def test_a_malformed_body_gets_a_safe_error(raw):
    payload = refused(call("POST", "/v1/prepare", raw=raw), 400, "invalid_argument")
    assert "not json" not in payload["message"]


def test_a_request_without_a_bearer_token_is_401():
    response = call("POST", "/v1/prepare", body={"operation": "x", "arguments": {}}, token=None)
    refused(response, 401, "unauthorized")
    assert response.headers["www-authenticate"].startswith("Bearer")


def test_a_user_or_persona_in_the_body_is_refused():
    body = {"operation": "x", "arguments": {}, "user_id": "someone"}
    refused(call("POST", "/v1/prepare", body=body), 400, "invalid_argument")
    refused(
        call("POST", "/v1/commit", body={"proposal_id": "x", "persona": "y"}),
        400,
        "invalid_argument",
    )


def test_other_methods_and_paths_are_refused():
    refused(call("GET", "/v1/prepare"), 405, "method_not_allowed")
    refused(call("POST", "/v1/delete", body={}), 404, "not_found")


# -- the token and the work ---------------------------------------------------------------


@pytest.fixture
def chain(monkeypatch):
    op = support.register(monkeypatch)
    support.install_access(monkeypatch)
    user = factories.UserFactory()
    matter = factories.MatterFactory(owner=user, title="Vana pealkiri")
    grant = support.active_grant(user)
    token = support.token_for(user, grant)
    return {"op": op, "user": user, "matter": matter, "grant": grant, "token": token}


def _approve(proposal_id: str) -> None:
    proposal = McpWriteProposal.objects.get(pk=proposal_id)
    now = timezone.now()
    McpWriteApproval.objects.create(
        proposal=proposal,
        user=proposal.user,
        grant=proposal.grant,
        arguments_digest=proposal.arguments_digest,
        approved_at=now,
        expires_at=now + timedelta(minutes=10),
        method=ApprovalMethod.TOTP,
    )
    McpWriteProposal.objects.filter(pk=proposal.pk).update(status=ProposalStatus.APPROVED)


@transactional
def test_prepare_approve_commit_over_http_and_nothing_secret_in_the_log(chain, caplog):
    caplog.set_level(logging.INFO, logger="app.mcp_writes")
    arguments = {"matter": str(chain["matter"].pk), "title": SECRET_ARGUMENT}

    prepared = call(
        "POST",
        "/v1/prepare",
        body={"operation": "test_rename_matter", "arguments": arguments},
        token=chain["token"],
    )
    assert prepared.status_code == 200, prepared.text
    proposal = prepared.json()["proposal"]
    assert proposal["approval_url"].endswith(f"/mcp-ligipaas/muudatus/{proposal['id']}/")
    assert prepared.headers["cache-control"].startswith("no-store")

    waiting = call("POST", "/v1/commit", body={"proposal_id": proposal["id"]}, token=chain["token"])
    assert waiting.status_code == 200 and waiting.json()["status"] == "awaiting_approval"

    _approve(proposal["id"])
    done = call("POST", "/v1/commit", body={"proposal_id": proposal["id"]}, token=chain["token"])
    assert done.status_code == 200 and done.json()["status"] == "executed"
    assert done.json()["replayed"] is False
    again = call("POST", "/v1/commit", body={"proposal_id": proposal["id"]}, token=chain["token"])
    assert again.json()["replayed"] is True and again.json()["result"] == done.json()["result"]
    chain["matter"].refresh_from_db()
    assert chain["matter"].title == SECRET_ARGUMENT and chain["op"].executions == 1

    lines = [
        record.getMessage() for record in caplog.records if record.name.startswith("app.mcp_writes")
    ]
    assert len([line for line in lines if line.startswith("mcp-writer ")]) == 4
    text = "\n".join(lines)
    assert chain["token"] not in text and support.GATEWAY_KEY not in text
    assert SECRET_ARGUMENT not in text and "Vana pealkiri" not in text
    assert "op=test_rename_matter" in text and f"user={chain['user'].pk}" in text


@transactional
def test_an_unknown_token_is_401(chain):
    response = call("POST", "/v1/prepare", body={"operation": "x", "arguments": {}}, token="y" * 43)
    refused(response, 401, "unauthorized")


@transactional
def test_a_read_only_token_gets_insufficient_scope(chain):
    reader_token = support.token_for(chain["user"], chain["grant"], scopes="juristid.read")
    body = {"operation": "test_rename_matter", "arguments": {}}
    refused(call("POST", "/v1/prepare", body=body, token=reader_token), 403, "insufficient_scope")
    refused(
        call("POST", "/v1/commit", body={"proposal_id": "x"}, token=reader_token),
        403,
        "insufficient_scope",
    )
    assert not McpWriteProposal.objects.exists()


@transactional
def test_a_write_token_whose_account_may_not_write_is_forbidden(chain, settings):
    administrator = factories.AdministratorFactory()
    grant = support.active_grant(administrator)
    token = support.token_for(administrator, grant)
    body = {"operation": "test_rename_matter", "arguments": {}}
    # An administrator cannot even read through MCP; the token fails verification.
    refused(call("POST", "/v1/prepare", body=body, token=token), 401, "unauthorized")


@transactional
def test_an_unknown_operation_is_refused_without_echo(chain):
    response = call(
        "POST",
        "/v1/prepare",
        body={"operation": SECRET_ARGUMENT, "arguments": {}},
        token=chain["token"],
    )
    payload = refused(response, 400, "invalid_argument")
    assert SECRET_ARGUMENT not in json.dumps(payload)


@transactional
def test_a_timeout_is_a_safe_504(chain, monkeypatch):
    def slow(**kwargs):
        import time

        time.sleep(2)
        raise AssertionError("not reached in time")

    monkeypatch.setattr(gateway.proposals, "prepare", slow)
    app = gateway.build_app(runtime=ReaderRuntime(max_concurrent_calls=1, timeout_seconds=0.5))

    async def go() -> httpx2.Response:
        transport = httpx2.ASGITransport(app=app)
        async with httpx2.AsyncClient(transport=transport, base_url=f"http://{HOST}") as http:
            return await http.post(
                "/v1/prepare",
                content=json.dumps({"operation": "test_rename_matter", "arguments": {}}),
                headers={
                    "X-Juristid-Gateway-Key": support.GATEWAY_KEY,
                    "Authorization": f"Bearer {chain['token']}",
                },
            )

    refused(asyncio.run(go()), 504, "timeout")


# -- the commands ---------------------------------------------------------------------------


def test_the_gateway_refuses_to_start_unless_writes_role_and_key_are_right(settings):
    from app.mcp_writes.management.commands.run_mcp_write_gateway import start_refusal

    settings.JURISTID_PROCESS_ROLE = "web"
    assert "mcp_writer" in start_refusal()
    settings.JURISTID_PROCESS_ROLE = settings.MCP_WRITER_PROCESS_ROLE
    assert start_refusal() == ""
    settings.MCP_WRITE_GATEWAY_KEY = "short"
    assert "32 characters" in start_refusal()
    settings.MCP_WRITES_ENABLED = False
    assert "MCP_WRITES_ENABLED" in start_refusal()
    with pytest.raises(CommandError, match="MCP_WRITES_ENABLED"):
        call_command("run_mcp_write_gateway", skip_checks=True)


def test_the_healthcheck_fails_when_nothing_listens(settings, capsys):
    settings.MCP_WRITE_LISTEN_PORT = 1
    with pytest.raises(SystemExit) as stopped:
        call_command("check_mcp_write_gateway", "--quiet")
    assert stopped.value.code == 1
    assert "did not answer" in capsys.readouterr().err


@pytest.mark.parametrize("raw", [b"[" * 30000, b'{"a":' * 40 + b"1" + b"}" * 40])
def test_a_deeply_nested_body_is_a_safe_400(raw):
    refused(call("POST", "/v1/prepare", raw=raw), 400, "invalid_argument")


def test_brackets_inside_strings_do_not_count_as_nesting():
    assert not gateway.too_deep(b'{"text": "' + b"[" * 100 + b'\\" ]"}')
    assert gateway.too_deep(b"[" * 33)
    assert not gateway.too_deep(b"[" * 32 + b"]" * 32)


@pytest.mark.django_db
@pytest.mark.parametrize("real_data", [True, False])
def test_the_gateway_does_not_start_on_a_write_capable_owner_role(settings, monkeypatch, real_data):
    """Audit 2026-10-28 C-04: E050 is an error in the `mcp_writer` role whether
    or not REAL_DATA_ALLOWED is set, so a gateway connected as the test
    database's owner (it can write every table) never reaches uvicorn."""
    import uvicorn
    from django.core.management.base import SystemCheckError

    settings.JURISTID_PROCESS_ROLE = settings.MCP_WRITER_PROCESS_ROLE
    settings.REAL_DATA_ALLOWED = real_data
    served: list[Any] = []
    monkeypatch.setattr(uvicorn.Server, "run", lambda self, sockets=None: served.append(self))
    with pytest.raises(SystemCheckError, match=r"juristid\.E050"):
        call_command("run_mcp_write_gateway", stdout=io.StringIO(), stderr=io.StringIO())
    assert served == []


# -- X-Correlation-Id (audit 2026-10-28 C-07) ----------------------------------------------


def _prepare_with(chain: Any, correlation: str | None) -> httpx2.Response:
    headers = {} if correlation is None else {"X-Correlation-Id": correlation}
    return call(
        "POST",
        "/v1/prepare",
        body={
            "operation": "test_rename_matter",
            "arguments": {"matter": str(chain["matter"].pk), "title": "Uus"},
        },
        token=chain["token"],
        headers=headers,
    )


@transactional
def test_a_valid_correlation_id_follows_the_proposal_through_commit(chain, caplog):
    import uuid

    from app.mcp_writes.models import McpWriteEvent, McpWriteEventType

    caplog.set_level(logging.INFO, logger="app.mcp_writes")
    first, second = str(uuid.uuid4()), str(uuid.uuid4())
    prepared = _prepare_with(chain, first)
    proposal_id = prepared.json()["proposal"]["id"]
    proposal = McpWriteProposal.objects.get(pk=proposal_id)
    assert str(proposal.correlation_id) == first
    _approve(proposal_id)
    done = call(
        "POST",
        "/v1/commit",
        body={"proposal_id": proposal_id},
        token=chain["token"],
        headers={"X-Correlation-Id": second},
    )
    assert done.json()["status"] == "executed"
    events = {e.event_type: e for e in McpWriteEvent.objects.filter(proposal=proposal)}
    assert str(events[McpWriteEventType.PREPARED].correlation_id) == first
    committed = events[McpWriteEventType.COMMITTED]
    assert str(committed.correlation_id) == first
    assert committed.detail["call_correlation_id"] == second
    lines = [r.getMessage() for r in caplog.records if r.getMessage().startswith("mcp-writer ")]
    assert f"corr={first}" in lines[0] and f"corr={second}" in lines[1]
    assert "(replaced)" not in "\n".join(lines)


@transactional
@pytest.mark.parametrize(
    "bad",
    [
        None,
        "",
        "not-a-uuid",
        "00000000-0000-0000-0000-000000000000",
        "{12345678-1234-1234-1234-123456789abc}",
        "12345678123412341234123456789abc",
        "x" * 200,
        " 12345678-1234-1234-1234-123456789abc; DROP",
    ],
)
def test_a_missing_or_malformed_correlation_id_is_replaced(chain, caplog, bad):
    caplog.set_level(logging.INFO, logger="app.mcp_writes")
    prepared = _prepare_with(chain, bad)
    proposal = McpWriteProposal.objects.get(pk=prepared.json()["proposal"]["id"])
    assert proposal.correlation_id.int != 0 and str(proposal.correlation_id) != bad
    line = next(r.getMessage() for r in caplog.records if r.getMessage().startswith("mcp-writer "))
    assert f"corr={proposal.correlation_id} (replaced)" in line


@transactional
def test_a_reused_correlation_id_never_joins_two_proposals(chain):
    import uuid

    shared = str(uuid.uuid4())
    one = _prepare_with(chain, shared).json()["proposal"]["id"]
    two = _prepare_with(chain, shared).json()["proposal"]["id"]
    ids = set(
        McpWriteProposal.objects.filter(pk__in=[one, two]).values_list("correlation_id", flat=True)
    )
    assert len(ids) == 2 and uuid.UUID(shared) in ids


@transactional
def test_a_correlation_id_naming_another_proposal_reaches_nothing(chain):
    """The id is a label: a commit carrying someone else's proposal's correlation
    id still commits only what its own proposal_id names, and only if approved."""
    prepared = _prepare_with(chain, None).json()["proposal"]["id"]
    target = McpWriteProposal.objects.get(pk=prepared)
    waiting = call(
        "POST",
        "/v1/commit",
        body={"proposal_id": prepared},
        token=chain["token"],
        headers={"X-Correlation-Id": str(target.correlation_id)},
    )
    assert waiting.json()["status"] == "awaiting_approval"
    other = factories.UserFactory()
    stranger = support.token_for(other, support.active_grant(other))
    refused(
        call(
            "POST",
            "/v1/commit",
            body={"proposal_id": prepared},
            token=stranger,
            headers={"X-Correlation-Id": str(target.correlation_id)},
        ),
        404,
        "not_found",
    )
    target.refresh_from_db()
    assert target.status == ProposalStatus.PENDING
