"""A colleague connects ChatGPT to Juristid, end to end over HTTP, with nothing faked.

docs/adr/0185 §3–§5. The authorization server is the Django application,
driven with the test `Client` exactly as a browser and ChatGPT's backend would
drive it; the resource server is the real Starlette application from
`app.mcp_server.server.build_app()`, driven over httpx2's `ASGITransport`. The
readers, the token verification, the TOTP check and the database are the
production code. The only things pinned are the TOTP clock (so a code at a
known step is a known code) and, for `private_key_jwt`, the JWKS a client
publishes (a locally generated RSA key; no test reaches a network).

The deployment is `https://juristid.test`, with two resources: its own
`/mcp` and the Secure MCP Tunnel URL OpenAI rewrites discovery to.

Transactional, because the MCP server reads in worker threads with
connections of their own and must see what the authorization server wrote.
"""

from __future__ import annotations

import io
import re
from datetime import timedelta
from typing import Any
from urllib.parse import parse_qs, urlencode, urlsplit

import pytest
from django.core.management import call_command
from django.test import Client
from django.utils import timezone

from app.mcp_access import oauth
from app.mcp_access.models import GrantStatus, McpAccessGrant, McpToken, TokenKind
from app.mcp_access.verification import verify_access_token
from app.mcp_access.views import GENERIC_REFUSAL
from tests import factories
from tests import mcp_access_support as support
from tests import mcp_eval_world as w
from tests.gate import PASSWORD, apply_shared_gate

pytestmark = pytest.mark.django_db(transaction=True, serialized_rollback=True)

GATE = "/konto/varav/"
SWITCH_PERSONA = "/konto/kasutaja/vaheta/"
ENROL = "/mcp-ligipaas/liitu/"
AUTHORIZE = "/oauth/authorize"
TOKEN = "/oauth/token"  # noqa: S105 - a path, not a secret
REVOKE = "/oauth/revoke"
FORM = "application/x-www-form-urlencoded"


@pytest.fixture(autouse=True)
def deployment(settings):
    apply_shared_gate(settings)
    w.apply_mcp_settings(settings)


def _as(client: Client, user: Any) -> None:
    """Pass the shared gate and act as ``user``'s persona."""
    assert client.post(GATE, {"password": PASSWORD}).status_code == 302
    assert client.post(SWITCH_PERSONA, {"user_id": str(user.pk)}).status_code == 302


def _machine(path: str, params: dict[str, str]) -> Any:
    """A request from the client's backend: no cookie, no gate, a form body."""
    return Client().post(path, data=urlencode(params), content_type=FORM)


def _grant(upn: str) -> str:
    """`manage.py mcp_access grant`, and the enrolment link it prints."""
    out = io.StringIO()
    call_command("mcp_access", "grant", "--user", upn, stdout=out)
    [link] = [line for line in out.getvalue().splitlines() if line.startswith("https://")]
    return link


def _enrol(client: Client, link: str, moment: Any, *, steps: int = 0) -> str:
    """Follow the link in the persona's session, read the key off the page, confirm it."""
    parts = urlsplit(link)
    assert f"{parts.scheme}://{parts.netloc}" == w.ORIGIN
    arrived = client.get(f"{parts.path}?{parts.query}")
    assert (arrived.status_code, arrived["Location"]) == (302, ENROL)
    page = client.get(ENROL)
    assert page.status_code == 200
    [secret] = re.findall(r'class="mono acctsecret">([A-Z2-7]+)<', page.content.decode())
    done = client.post(ENROL, {"kood": w.code_at(secret, moment, steps)})
    assert done.status_code == 200, done.content.decode()[:500]
    assert "Autentija on seadistatud" in done.content.decode()
    return secret


def _authorize(client: Client, code: str, **overrides: Any) -> dict[str, list[str]]:
    """Open the consent page as ChatGPT sends the browser there, then «Luba» with ``code``."""
    query = urlencode(w.authorize_params(**overrides))
    page = client.get(f"{AUTHORIZE}?{query}")
    assert page.status_code == 200, page.content.decode()[:500]
    assert "ChatGPT" in page.content.decode()
    answered = client.post(f"{AUTHORIZE}?{query}", {"otsus": "luba", "kood": code})
    assert answered.status_code == 302, answered.content.decode()[:800]
    location = urlsplit(answered["Location"])
    assert f"{location.scheme}://{location.netloc}{location.path}" == w.CHATGPT_REDIRECT
    return parse_qs(location.query)


@pytest.fixture(autouse=True)
def chatgpt_keys(monkeypatch) -> support.FakeClientKeys:
    """ChatGPT's signing key, generated here, and the JWKS it would publish.

    The default ChatGPT client authenticates at /oauth/token with
    private_key_jwt only (MCPSEC-08), so every machine request signs an
    assertion unless a test says otherwise.
    """
    keys = support.FakeClientKeys(kid="chatgpt-test-1")
    keys.install(monkeypatch)
    KEYS["chatgpt"] = keys
    return keys


#: The current test's key, for the helpers below (set by `chatgpt_keys`).
KEYS: dict[str, support.FakeClientKeys] = {}


def _signed() -> dict[str, str]:
    """`private_key_jwt` client authentication as ChatGPT's backend sends it (RFC 7523)."""
    return {
        "client_id": w.CHATGPT_CLIENT,
        "client_assertion_type": oauth.CLIENT_ASSERTION_TYPE,
        "client_assertion": KEYS["chatgpt"].assertion(
            iss=w.CHATGPT_CLIENT, sub=w.CHATGPT_CLIENT, aud=f"{w.ORIGIN}{TOKEN}"
        ),
    }


PUBLIC_CLIENT_AUTH = {"client_id": w.CHATGPT_CLIENT}


def _exchange_response(code: str, *, resource: str = w.TUNNEL_RESOURCE, **auth: str) -> Any:
    return _machine(
        TOKEN,
        {
            "grant_type": "authorization_code",
            "code": code,
            "redirect_uri": w.CHATGPT_REDIRECT,
            "code_verifier": w.PKCE_VERIFIER,
            "resource": resource,
            **(auth or _signed()),
        },
    )


def _exchange(code: str, *, resource: str = w.TUNNEL_RESOURCE, **auth: str) -> dict[str, Any]:
    response = _exchange_response(code, resource=resource, **auth)
    assert response.status_code == 200, response.content
    assert response["Cache-Control"] == "no-store"
    body: dict[str, Any] = response.json()
    return body


def _refresh(refresh_token: str, **auth: str) -> Any:
    return _machine(
        TOKEN,
        {
            "grant_type": "refresh_token",
            "refresh_token": refresh_token,
            "resource": w.TUNNEL_RESOURCE,
            **(auth or _signed()),
        },
    )


def _out_of_grace(user: Any) -> None:
    """Move every rotation of ``user``'s refresh tokens past the 30-second retry grace."""
    McpToken.objects.filter(user=user, replaced_by__isnull=False).update(
        revoked_at=timezone.now() - timedelta(minutes=5)
    )


def _tools_listed(token: str) -> int:
    return w.Mcp(token, host=w.TUNNEL_CLIENT_HOST).rpc("tools/list", {}).status_code


# -- the whole connection --------------------------------------------------------------


def test_a_colleague_connects_chatgpt_reads_everything_and_is_disconnected(client, monkeypatch):
    moment = w.freeze_totp(monkeypatch)
    world = w.build_world(moment)
    mari = factories.UserFactory(upn="mari.maasikas@example.invalid", display_name="Mari Maasikas")

    # The operator grants the account and hands Mari her one-time link.
    link = _grant("MARI.MAASIKAS@example.invalid")
    assert link.startswith(f"{w.ORIGIN}{ENROL}?kood=")
    assert McpAccessGrant.objects.get(user=mari).status == GrantStatus.PENDING_ENROLMENT

    # Mari opens it behind the gate, as herself, and enrols her authenticator.
    _as(client, mari)
    secret = _enrol(client, link, moment)
    assert McpAccessGrant.objects.get(user=mari).status == GrantStatus.ACTIVE

    # ChatGPT discovers both halves.
    discovered = Client().get("/.well-known/oauth-authorization-server")
    assert discovered.status_code == 200
    metadata = discovered.json()
    assert metadata["issuer"] == w.ORIGIN
    assert metadata["authorization_endpoint"] == f"{w.ORIGIN}{AUTHORIZE}"
    assert metadata["token_endpoint"] == f"{w.ORIGIN}{TOKEN}"
    assert metadata["revocation_endpoint"] == f"{w.ORIGIN}{REVOKE}"
    assert metadata["code_challenge_methods_supported"] == ["S256"]
    assert metadata["authorization_response_iss_parameter_supported"] is True
    resource = w.Mcp(None, host=w.TUNNEL_CLIENT_HOST).get(
        "/.well-known/oauth-protected-resource/mcp"
    )
    assert resource.status_code == 200
    assert resource.json()["authorization_servers"] == [w.ORIGIN]
    unauthenticated = w.Mcp(None, host="api.openai.com").rpc("tools/list", {})
    assert unauthenticated.status_code == 401
    assert (
        'resource_metadata="https://api.openai.com/.well-known/oauth-protected-resource/v1/mcp/'
        'tunnel_abc"' in unauthenticated.headers["www-authenticate"]
    )

    # Consent: the next step's code, because the enrolment spent this one.
    granted = _authorize(client, w.code_at(secret, moment, 1))
    assert granted["state"] == ["chatgpt-olek-7f3a"]
    assert granted["iss"] == [w.ORIGIN]
    [code] = granted["code"]

    # As a public client (`none`) the ChatGPT client is refused now (MCPSEC-08),
    # and the refusal does not spend the code.
    unsigned = _exchange_response(code, **PUBLIC_CLIENT_AUTH)
    assert (unsigned.status_code, unsigned.json()["error"]) == (401, "invalid_client")
    # ChatGPT's backend exchanges it with a signed assertion (private_key_jwt + PKCE).
    pair = _exchange(code)
    assert pair["token_type"] == "Bearer"  # noqa: S105
    assert pair["expires_in"] == 3600
    assert pair["scope"] == "juristid.read"
    first_access, first_refresh = pair["access_token"], pair["refresh_token"]

    # The MCP side, through the tunnel client's address.
    mcp = w.Mcp(first_access, host=w.TUNNEL_CLIENT_HOST)
    hello = mcp.rpc(
        "initialize",
        {
            "protocolVersion": w.PROTOCOL_VERSION,
            "capabilities": {},
            "clientInfo": {"name": "openai-mcp", "version": "1.0.0"},
        },
    )
    assert hello.status_code == 200
    assert hello.json()["result"]["serverInfo"]["name"] == "juristid"
    tools = mcp.rpc("tools/list", {}).json()["result"]["tools"]
    assert {tool["name"] for tool in tools} == w.TOOL_NAMES
    assert all(tool["annotations"]["readOnlyHint"] is True for tool in tools)

    wait = world.reference("wait")
    answers = dict(
        zip(
            sorted(w.TOOL_NAMES),
            mcp.batch(
                [
                    ("fetch", {"id": world.matter_id("wait")}),
                    ("get_matter", {"matter": wait}),
                    ("get_matter_activity", {"matter": wait}),
                    ("get_matter_documents", {"matter": world.reference("packaging_waste")}),
                    ("get_upcoming_commencements", {}),
                    ("list_matters", {"responsible": "Marko"}),
                    ("search", {"query": "pakendiseadus"}),
                    ("search_records", {"query": "Kliimaministeerium"}),
                ]
            ),
            strict=True,
        )
    )
    facts = {name: w.structured(result, name) for name, result in answers.items()}
    assert w.WAITING_FOR in facts["fetch"]["text"]
    assert facts["get_matter"]["owner"] == "Marko Mets"
    assert facts["get_matter_activity"]["items"][0]["kind"] == "entry"
    assert facts["get_matter_documents"]["documents"][0]["fragment_count"] == 45
    assert len(facts["get_upcoming_commencements"]["commencements"]) == 1
    assert world.matter_id("wait") in [r["id"] for r in facts["list_matters"]["matters"]]
    assert world.matter_id("packaging") in [r["id"] for r in facts["search"]["results"]]
    assert world.matter_id("wait") in [r["id"] for r in facts["search_records"]["results"]]

    # Refresh: a new pair; the old access token stands until it expires.
    assert _refresh(first_refresh, **PUBLIC_CLIENT_AUTH).status_code == 401
    rotated = _refresh(first_refresh)
    assert rotated.status_code == 200, rotated.content
    second = rotated.json()
    assert second["refresh_token"] != first_refresh
    assert _tools_listed(first_access) == 200
    assert _tools_listed(second["access_token"]) == 200

    # A retried rotation inside the grace window gets the same successor pair.
    retried = _refresh(first_refresh)
    assert retried.status_code == 200, retried.content
    assert retried.json()["refresh_token"] == second["refresh_token"]
    assert retried.json()["access_token"] == second["access_token"]

    # Outside it, the rotated token coming back means somebody holds a copy: the family ends.
    _out_of_grace(mari)
    replayed = _refresh(first_refresh)
    assert (replayed.status_code, replayed.json()["error"]) == (400, "invalid_grant")
    assert _tools_listed(first_access) == 401
    assert _tools_listed(second["access_token"]) == 401
    assert _refresh(second["refresh_token"]).status_code == 400

    # Connect again, with a fresh code at a later step.
    later = w.freeze_totp(monkeypatch, moment + timedelta(seconds=90))
    granted = _authorize(client, w.code_at(secret, later), state="teine-olek")
    assert granted["state"] == ["teine-olek"]
    signed = _exchange(granted["code"][0])
    assert _tools_listed(signed["access_token"]) == 200
    assertion = _signed()
    renewed_response = _refresh(signed["refresh_token"], **assertion)
    assert renewed_response.status_code == 200, renewed_response.content
    renewed = renewed_response.json()
    # A spent assertion is not accepted twice, and the refusal spends nothing.
    replayed_assertion = _refresh(renewed["refresh_token"], **assertion)
    assert (replayed_assertion.status_code, replayed_assertion.json()["error"]) == (
        401,
        "invalid_client",
    )

    # ChatGPT revokes one access token (RFC 7009): that token ends, nothing else.
    revoked = _machine(REVOKE, {"token": renewed["access_token"], **_signed()})
    assert revoked.status_code == 200
    assert _tools_listed(renewed["access_token"]) == 401
    assert _tools_listed(signed["access_token"]) == 200

    # The operator ends Mari's access: the grant and every token, at once.
    out = io.StringIO()
    call_command(
        "mcp_access", "revoke", "--user", mari.upn, "--reason", "Lahkus osakonnast", stdout=out
    )
    assert McpAccessGrant.objects.get(user=mari).status == GrantStatus.REVOKED
    assert _tools_listed(signed["access_token"]) == 401
    called = w.Mcp(signed["access_token"]).rpc(
        "tools/call", {"name": "get_matter", "arguments": {"matter": wait}}
    )
    assert called.status_code == 401
    assert world.matters["wait"].title not in called.text
    assert _refresh(renewed["refresh_token"]).status_code == 400
    assert not McpToken.objects.filter(user=mari, revoked_at=None).exists()
    # Marko, whom nobody revoked, is still connected.
    assert _tools_listed(world.token("marko")) == 200


# -- what a token is refused for -----------------------------------------------------------


@pytest.fixture
def connected(monkeypatch):
    """Two colleagues connected through the services, and one matter to ask about."""
    moment = w.freeze_totp(monkeypatch)
    matter = factories.MatterFactory(title="Äriseadustiku muutmise eelnõu")
    users = {
        "public": factories.UserFactory(upn="avalik@example.invalid"),
        "tunnel": factories.UserFactory(upn="tunnel@example.invalid"),
    }
    connections = {
        "public": w.connect(users["public"], moment, resource=w.RESOURCE),
        "tunnel": w.connect(users["tunnel"], moment, resource=w.TUNNEL_RESOURCE),
    }
    return {"matter": matter, "users": users, "connections": connections}


def _answers(token: str, matter: Any) -> int:
    response = w.Mcp(token).rpc(
        "tools/call",
        {"name": "get_matter", "arguments": {"matter": matter.display_reference}},
    )
    if response.status_code != 200:
        return response.status_code
    return 200 if response.json()["result"].get("isError") is not True else 500


def test_a_token_is_accepted_only_for_a_resource_still_configured(connected, settings):
    matter = connected["matter"]
    public, tunnel = connected["connections"]["public"], connected["connections"]["tunnel"]
    assert _answers(public.access, matter) == 200
    assert _answers(tunnel.access, matter) == 200

    # The public endpoint is withdrawn: only the tunnel remains a resource.
    settings.MCP_RESOURCE_URLS = [w.TUNNEL_RESOURCE]
    assert _answers(public.access, matter) == 401
    assert _answers(tunnel.access, matter) == 200
    refreshed = _refresh(public.refresh)
    assert refreshed.status_code == 400

    # And the other way round.
    settings.MCP_RESOURCE_URLS = [w.RESOURCE]
    assert _answers(tunnel.access, matter) == 401


def test_an_expired_access_token_is_refused(connected):
    matter = connected["matter"]
    tunnel = connected["connections"]["tunnel"]
    assert _answers(tunnel.access, matter) == 200
    McpToken.objects.filter(user=connected["users"]["tunnel"], kind=TokenKind.ACCESS).update(
        expires_at=timezone.now() - timedelta(seconds=1)
    )
    assert _answers(tunnel.access, matter) == 401
    # The refresh token is still good, and is what a client uses next.
    assert _refresh(tunnel.refresh).status_code == 200


def test_an_account_that_changes_is_refused_at_its_next_call(connected):
    from app.accounts.enums import UserRole

    matter = connected["matter"]
    deactivated, demoted = connected["users"]["public"], connected["users"]["tunnel"]
    public, tunnel = connected["connections"]["public"], connected["connections"]["tunnel"]
    assert _answers(public.access, matter) == 200
    assert _answers(tunnel.access, matter) == 200

    deactivated.is_active = False
    deactivated.save(update_fields=["is_active"])
    demoted.role = UserRole.ADMINISTRATOR
    demoted.save(update_fields=["role"])

    assert _answers(public.access, matter) == 401
    assert _answers(tunnel.access, matter) == 401
    assert _refresh(tunnel.refresh).status_code == 400


def test_mcp_switched_off_answers_nothing(connected, settings, client):
    tunnel = connected["connections"]["tunnel"]
    settings.MCP_ENABLED = False
    _as(client, connected["users"]["tunnel"])
    for path in (
        "/.well-known/oauth-authorization-server",
        f"{AUTHORIZE}?{urlencode(w.authorize_params())}",
        ENROL,
    ):
        assert client.get(path).status_code == 404, path
    for path in (TOKEN, REVOKE):
        assert client.post(path, data="", content_type=FORM).status_code == 404, path
    assert verify_access_token(tunnel.access) is None
    assert _answers(tunnel.access, connected["matter"]) == 401


# -- who may consent ------------------------------------------------------------------------


class TestConsent:
    """The authorization step itself, in the browser. No MCP call reads what these
    write, so they need no committed rows: the class's own mark (the closest one)
    keeps them in an ordinary rolled-back transaction.
    """

    pytestmark = pytest.mark.django_db(transaction=False)

    def test_another_personas_code_cannot_authorize(self, client, monkeypatch):
        moment = w.freeze_totp(monkeypatch)
        jaan, mari = factories.UserFactory(), factories.UserFactory()
        jaans = w.connect(jaan, moment)
        maris = w.connect(mari, moment)
        _as(client, mari)
        query = urlencode(w.authorize_params())
        refused = client.post(
            f"{AUTHORIZE}?{query}", {"otsus": "luba", "kood": w.code_at(jaans.secret, moment, 1)}
        )
        assert refused.status_code == 400
        assert refused.context["error"] == GENERIC_REFUSAL
        assert "Location" not in refused
        granted = _authorize(client, w.code_at(maris.secret, moment, 1))
        assert granted["code"]

    def test_a_persona_without_a_grant_cannot_authorize(self, client, monkeypatch):
        moment = w.freeze_totp(monkeypatch)
        nobody = factories.UserFactory()
        enrolled = w.connect(factories.UserFactory(), moment)
        _as(client, nobody)
        query = urlencode(w.authorize_params())
        page = client.get(f"{AUTHORIZE}?{query}")
        # The page says nothing about whether this persona has a grant (MCPSEC-01).
        assert page.status_code == 200
        assert "may_connect" not in page.context
        refused = client.post(
            f"{AUTHORIZE}?{query}", {"otsus": "luba", "kood": w.code_at(enrolled.secret, moment, 1)}
        )
        # The same answer as a wrong code: no grant cannot be told from a typo.
        assert refused.status_code == 400
        assert refused.context["error"] == GENERIC_REFUSAL
        assert "Location" not in refused

    def test_a_gate_session_without_a_persona_is_sent_to_choose_one(self, client):
        assert client.post(GATE, {"password": PASSWORD}).status_code == 302
        query = urlencode(w.authorize_params())
        arrived = client.get(f"{AUTHORIZE}?{query}")
        assert arrived.status_code == 302
        target = urlsplit(arrived["Location"])
        assert target.path == "/konto/kasutaja/"
        assert parse_qs(target.query)["next"] == [f"{AUTHORIZE}?{query}"]
        posted = client.post(f"{AUTHORIZE}?{query}", {"otsus": "luba", "kood": "123456"})
        assert posted.status_code == 302
        assert "code=" not in posted["Location"]
