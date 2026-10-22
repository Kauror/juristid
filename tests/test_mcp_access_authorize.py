"""`/oauth/authorize` behind the shared gate, a persona and that persona's own authenticator.

docs/adr/0185 §3: the gate password is shared and the persona is a choice, so
neither alone may connect an AI client. These tests walk the real browser path
— gate, persona chooser, consent page — and prove the code from one persona's
authenticator opens nothing for another, five wrong codes lock the grant, and a
refusal goes back to the client only once its redirect URI is known good.
"""

from __future__ import annotations

from urllib.parse import parse_qs, urlencode, urlsplit

import pytest
from django.test import Client

from app.mcp_access import views
from app.mcp_access.models import McpAccessEvent, McpAccessEventType, McpAuthorizationCode
from tests import factories
from tests import mcp_access_support as support
from tests.gate import PASSWORD, apply_shared_gate

pytestmark = pytest.mark.django_db

AUTHORIZE = "/oauth/authorize"
GATE = "/konto/varav/"
CHOOSER = "/konto/kasutaja/"


@pytest.fixture(autouse=True)
def behind_the_gate(settings):
    apply_shared_gate(settings)
    support.enable(settings)


@pytest.fixture
def moment(monkeypatch):
    return support.freeze_totp(monkeypatch)


def _url(**overrides) -> str:
    return f"{AUTHORIZE}?{urlencode(support.authorize_params(**overrides))}"


def _through_the_gate(client: Client) -> None:
    assert client.post(GATE, {"password": PASSWORD}).status_code == 302


def _as(client: Client, user) -> None:
    _through_the_gate(client)
    assert client.post("/konto/kasutaja/vaheta/", {"user_id": str(user.pk)}).status_code == 302


def _query(response) -> dict[str, str]:
    location = response["Location"]
    return {key: values[0] for key, values in parse_qs(urlsplit(location).query).items()}


# -- the way in ---------------------------------------------------------------------


def test_a_visitor_stopped_at_the_gate_comes_back_through_the_persona_chooser(client, moment):
    user = factories.UserFactory()
    support.enrolled(user, moment)
    url = _url()

    stopped = client.get(url)
    assert stopped.status_code == 302
    assert stopped["Location"] == f"{GATE}?{urlencode({'next': url})}"

    passed = client.post(stopped["Location"], {"password": PASSWORD})
    assert passed.status_code == 302 and passed["Location"] == url

    no_persona = client.get(url)
    assert no_persona.status_code == 302
    assert urlsplit(no_persona["Location"]).path == CHOOSER
    assert parse_qs(urlsplit(no_persona["Location"]).query)["next"] == [url]

    chose = client.post(
        f"/konto/kasutaja/vaheta/?{urlencode({'next': url})}", {"user_id": str(user.pk)}
    )
    assert chose.status_code == 302 and chose["Location"] == url

    page = client.get(url)
    assert page.status_code == 200
    body = page.content.decode()
    assert "Testklient" in body
    assert "Ainult lugemine: otsing ja teemade, dokumentide ning ajaloo lugemine" in body
    assert user.display_name in body
    assert "Juristidi MCP autentija" in body
    assert "Luba" in body and "Keeldu" in body
    assert page["Cache-Control"].startswith("max-age=0") or "no-store" in page["Cache-Control"]


@pytest.mark.parametrize(
    "target", ["https://evil.example.test/", "//evil.example.test/x", "/\\evil.example.test"]
)
def test_the_gate_never_follows_a_next_off_this_site(client, target):
    response = client.post(f"{GATE}?{urlencode({'next': target})}", {"password": PASSWORD})
    assert response.status_code == 302
    assert "evil" not in response["Location"]


def test_the_gate_adds_no_next_for_an_ordinary_page(client):
    response = client.get("/teemad/?olek=koik")
    assert response.status_code == 302 and response["Location"] == GATE


def test_the_page_lets_its_form_reach_the_clients_redirect_origin_and_nothing_else(client, moment):
    user = factories.UserFactory()
    support.enrolled(user, moment)
    _as(client, user)
    policy = client.get(_url())["Content-Security-Policy"]
    assert "form-action 'self' https://client.example.test;" in policy
    assert "frame-ancestors 'none'" in policy


# -- errors before and after the redirect URI is known -------------------------------


def test_an_unknown_client_gets_an_error_page_and_no_redirect(client):
    _through_the_gate(client)
    response = client.get(_url(client_id="https://evil.example.test/client.json"))
    assert response.status_code == 400 and "Location" not in response
    assert "Tundmatu klient" in response.content.decode()


def test_an_unregistered_redirect_uri_gets_an_error_page_and_no_redirect(client):
    _through_the_gate(client)
    response = client.get(_url(redirect_uri="https://evil.example.test/callback"))
    assert response.status_code == 400 and "Location" not in response


def test_a_missing_redirect_uri_gets_an_error_page(client):
    _through_the_gate(client)
    response = client.get(_url(redirect_uri=None))
    assert response.status_code == 400 and "Location" not in response


@pytest.mark.parametrize(
    ("overrides", "error"),
    [
        ({"response_type": "token"}, "unsupported_response_type"),
        ({"code_challenge": None}, "invalid_request"),
        ({"code_challenge_method": "plain"}, "invalid_request"),
        ({"code_challenge_method": None}, "invalid_request"),
        ({"code_challenge": "short"}, "invalid_request"),
        ({"resource": None}, "invalid_target"),
        ({"resource": "https://elsewhere.example.test/mcp"}, "invalid_target"),
        ({"scope": "juristid.read juristid.write"}, "invalid_scope"),
    ],
)
def test_a_bad_request_is_refused_at_the_redirect_uri_with_state_and_iss(client, overrides, error):
    _through_the_gate(client)
    response = client.get(_url(**overrides))
    assert response.status_code == 302
    assert response["Location"].startswith(support.PUBLIC_REDIRECT + "?")
    query = _query(response)
    assert query["error"] == error
    assert query["state"] == "olek-123"
    assert query["iss"] == support.ORIGIN


@pytest.mark.parametrize("scope", ["", "offline_access", "openid profile email juristid.read"])
def test_generic_scopes_are_ignored_and_juristid_read_is_granted(client, moment, scope):
    user = factories.UserFactory()
    _, secret = support.enrolled(user, moment)
    _as(client, user)
    response = client.post(
        _url(scope=scope), {"otsus": "luba", "kood": support.code_at(secret, moment)}
    )
    assert response.status_code == 302
    assert McpAuthorizationCode.objects.get().scope == "juristid.read"


# -- consent -----------------------------------------------------------------------


def test_the_right_code_returns_a_one_time_code_with_state_and_iss(client, moment):
    user = factories.UserFactory()
    _, secret = support.enrolled(user, moment)
    _as(client, user)

    response = client.post(_url(), {"otsus": "luba", "kood": support.code_at(secret, moment)})

    assert response.status_code == 302
    assert response["Location"].startswith(support.PUBLIC_REDIRECT + "?")
    query = _query(response)
    assert query["state"] == "olek-123" and query["iss"] == support.ORIGIN
    assert len(query["code"]) == 43
    record = McpAuthorizationCode.objects.get()
    assert record.digest != query["code"] and record.user == user
    assert record.resource == support.RESOURCE and record.client_id == support.PUBLIC_CLIENT


def test_the_consent_post_needs_its_csrf_token(moment):
    user = factories.UserFactory()
    _, secret = support.enrolled(user, moment)
    lenient = Client()
    _as(lenient, user)
    strict = Client(enforce_csrf_checks=True)
    strict.cookies = lenient.cookies
    code = support.code_at(secret, moment)
    response = strict.post(_url(), {"otsus": "luba", "kood": code})
    assert response.status_code == 403
    assert not McpAuthorizationCode.objects.exists()
    # The same session, code and grant were good: it was the missing token.
    assert lenient.post(_url(), {"otsus": "luba", "kood": code}).status_code == 302


def test_another_personas_code_opens_nothing(client, moment):
    owner = factories.UserFactory()
    _, owner_secret = support.enrolled(owner, moment)
    colleague = factories.UserFactory()
    support.enrolled(colleague, moment)
    _as(client, colleague)

    response = client.post(_url(), {"otsus": "luba", "kood": support.code_at(owner_secret, moment)})

    assert response.status_code == 400
    assert not McpAuthorizationCode.objects.exists()


def test_a_spent_code_cannot_be_typed_again(client, moment):
    user = factories.UserFactory()
    _, secret = support.enrolled(user, moment)
    _as(client, user)
    code = support.code_at(secret, moment)
    assert client.post(_url(), {"otsus": "luba", "kood": code}).status_code == 302
    again = client.post(_url(), {"otsus": "luba", "kood": code})
    assert again.status_code == 400
    assert McpAuthorizationCode.objects.count() == 1


def test_five_wrong_codes_lock_the_grant_and_then_even_the_right_one_waits(client, moment):
    user = factories.UserFactory()
    grant, secret = support.enrolled(user, moment)
    _as(client, user)
    for attempt in range(4):
        assert client.post(_url(), {"otsus": "luba", "kood": "000000"}).status_code == 400, attempt
    fifth = client.post(_url(), {"otsus": "luba", "kood": "000000"})
    assert fifth.status_code == 429

    locked = client.post(_url(), {"otsus": "luba", "kood": support.code_at(secret, moment)})

    assert locked.status_code == 429
    assert not McpAuthorizationCode.objects.exists()
    grant.refresh_from_db()
    assert grant.locked_until is not None
    assert McpAccessEvent.objects.filter(event_type=McpAccessEventType.GRANT_LOCKED).count() == 1
    assert "Liiga palju valesid koode" in client.get(_url()).content.decode()


def test_deny_returns_access_denied_with_state_and_iss(client, moment):
    user = factories.UserFactory()
    support.enrolled(user, moment)
    _as(client, user)
    response = client.post(_url(), {"otsus": "keeldu"})
    query = _query(response)
    assert query == {
        "error": "access_denied",
        "error_description": "The user denied the request.",
        "state": "olek-123",
        "iss": support.ORIGIN,
    }
    assert McpAccessEvent.objects.filter(
        event_type=McpAccessEventType.AUTHORIZATION_DENIED
    ).exists()


def test_a_persona_without_a_grant_is_told_so_and_can_only_refuse(client):
    user = factories.UserFactory()
    _as(client, user)
    page = client.get(_url())
    body = page.content.decode()
    assert page.status_code == 200
    assert "ei ole MCP ligipääsu" in body and 'name="kood"' not in body
    assert "Vaheta kasutajat" in body
    assert client.post(_url(), {"otsus": "luba", "kood": "123456"}).status_code == 403
    assert not McpAuthorizationCode.objects.exists()


def test_a_pending_grant_cannot_authorize(client):
    from app.mcp_access import services

    user = factories.UserFactory()
    services.grant_access(user=user, provenance=support.OPERATOR)
    _as(client, user)
    assert client.post(_url(), {"otsus": "luba", "kood": "123456"}).status_code == 403


def test_an_account_that_lost_its_business_role_cannot_authorize(moment, settings):
    """The persona chooser no longer offers it; under AUTH_MODE=none it signs in directly."""
    from app.accounts.enums import UserRole
    from app.accounts.models import User

    settings.AUTH_MODE = "none"
    user = factories.UserFactory()
    _, secret = support.enrolled(user, moment)
    User.objects.filter(pk=user.pk).update(role=UserRole.ADMINISTRATOR)
    signed_in = Client()
    signed_in.force_login(User.objects.get(pk=user.pk))
    response = signed_in.post(_url(), {"otsus": "luba", "kood": support.code_at(secret, moment)})
    assert response.status_code == 403
    assert not McpAuthorizationCode.objects.exists()


def test_an_unknown_decision_is_refused(client, moment):
    user = factories.UserFactory()
    support.enrolled(user, moment)
    _as(client, user)
    assert client.post(_url(), {"otsus": "x"}).status_code == 400


# -- the exemptions are exact ----------------------------------------------------------


def test_the_machine_endpoints_answer_without_the_gate(client):
    metadata = client.get("/.well-known/oauth-authorization-server")
    assert metadata.status_code == 200
    token = client.post("/oauth/token", {"grant_type": "x"})
    assert token.status_code == 400 and token.json()["error"] == "invalid_request"
    revoke = client.post("/oauth/revoke", {"token": "x"})
    assert revoke.status_code == 400


@pytest.mark.parametrize(
    "path",
    [
        "/",
        "/teemad/",
        "/oauth/token/",
        "/oauth/tokens",
        "/oauth/",
        "/oauth/revoke/x",
        "/.well-known/oauth-authorization-server/x",
        "/.well-known/openid-configuration",
        "/.well-known/oauth-protected-resource",
        "/oauth/authorize",
        "/mcp-ligipaas/liitu/",
        "/statistika/",
    ],
)
def test_every_other_path_is_still_behind_the_gate(client, path):
    response = client.get(path)
    assert response.status_code == 302 and response["Location"].startswith(GATE), path


def test_with_mcp_off_the_machine_endpoints_are_behind_the_gate_like_everything(client, settings):
    settings.MCP_ENABLED = False
    for path in ("/.well-known/oauth-authorization-server", "/oauth/token", "/oauth/revoke"):
        response = client.get(path)
        assert response.status_code == 302 and response["Location"] == GATE, path


def test_with_mcp_off_every_route_is_a_404_behind_the_gate(client, settings):
    settings.MCP_ENABLED = False
    user = factories.UserFactory()
    _as(client, user)
    for path in (
        "/.well-known/oauth-authorization-server",
        AUTHORIZE,
        "/oauth/token",
        "/oauth/revoke",
        "/mcp-ligipaas/liitu/",
    ):
        assert client.get(path).status_code == 404, path
        assert client.post(path, {}).status_code == 404, path


def test_with_mcp_on_but_no_public_origin_nothing_answers(client, settings):
    settings.JURISTID_PUBLIC_ORIGIN = ""
    user = factories.UserFactory()
    _as(client, user)
    assert client.get(_url()).status_code == 404


# -- discovery ------------------------------------------------------------------------


def test_the_metadata_is_rfc8414_and_ignores_the_host_header(client, settings):
    settings.ALLOWED_HOSTS = ["testserver", "evil.example.test"]
    response = client.get("/.well-known/oauth-authorization-server", HTTP_HOST="evil.example.test")
    assert response.status_code == 200
    metadata = response.json()
    assert metadata == {
        "issuer": support.ORIGIN,
        "authorization_endpoint": f"{support.ORIGIN}/oauth/authorize",
        "token_endpoint": f"{support.ORIGIN}/oauth/token",
        "revocation_endpoint": f"{support.ORIGIN}/oauth/revoke",
        "response_types_supported": ["code"],
        "response_modes_supported": ["query"],
        "grant_types_supported": ["authorization_code", "refresh_token"],
        "code_challenge_methods_supported": ["S256"],
        "token_endpoint_auth_methods_supported": ["private_key_jwt", "none"],
        "token_endpoint_auth_signing_alg_values_supported": ["RS256"],
        "revocation_endpoint_auth_methods_supported": ["private_key_jwt", "none"],
        "scopes_supported": ["juristid.read"],
        "client_id_metadata_document_supported": True,
        "authorization_response_iss_parameter_supported": True,
    }
    assert "evil" not in response.content.decode()


def test_the_readonly_description_is_the_one_the_brief_names():
    assert views.READ_ONLY_DESCRIPTION == (
        "Ainult lugemine: otsing ja teemade, dokumentide ning ajaloo lugemine"
    )


# -- who may not reach the page at all ------------------------------------------------


def test_anonymous_in_mode_none_is_sent_to_sign_in(settings):
    settings.AUTH_MODE = "none"
    settings.LOGIN_URL = "accounts:dev_login"
    response = Client().get(_url())
    assert response.status_code == 302 and response["Location"].startswith("/konto/")
    assert not McpAuthorizationCode.objects.exists()


def test_a_persona_less_gate_session_is_sent_to_choose_a_persona_for_both_pages(client):
    _through_the_gate(client)
    for path in (_url(), "/mcp-ligipaas/liitu/"):
        response = client.get(path)
        assert response.status_code == 302 and response["Location"].startswith(CHOOSER), path
        assert client.post(path, {}).status_code == 302, path
