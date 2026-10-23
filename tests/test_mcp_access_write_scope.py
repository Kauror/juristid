"""The `juristid.write` scope at authorize, code exchange and refresh (docs/mcp/OAUTH-SCOPES.md).

The code's scope is `juristid.read`, plus `juristid.write` only when the
request asked for it *and* `services.may_write` held at the consent step;
otherwise it is narrowed to read without an error (RFC 6749 §3.3) and the
token response says so. A refresh never widens and narrows to what the
account may do now.
"""

from __future__ import annotations

import re
import uuid
from urllib.parse import urlencode

import pytest
from django.core.cache import cache
from django.http import QueryDict
from django.utils import timezone

from app.core import authorization
from app.mcp_access import credentials, oauth, services, views
from app.mcp_access.models import (
    McpAccessEvent,
    McpAccessEventType,
    McpAccessGrant,
    McpAuthorizationCode,
    McpToken,
    TokenKind,
)
from tests import factories
from tests import mcp_access_support as support
from tests.gate import PASSWORD, apply_shared_gate

pytestmark = pytest.mark.django_db

READ = "juristid.read"
BOTH = "juristid.read juristid.write"
AUTHORIZE = "/oauth/authorize"


@pytest.fixture(autouse=True)
def writes_on(settings):
    apply_shared_gate(settings)
    support.enable(settings)
    settings.MCP_WRITES_ENABLED = True
    cache.clear()
    yield
    cache.clear()


@pytest.fixture
def moment(monkeypatch):
    return support.freeze_totp(monkeypatch)


def _person(moment, *, write: bool = True, make=factories.UserFactory):
    user = make()
    grant, secret = support.enrolled(user, moment)
    if write:
        services.grant_write(user=user, provenance=support.OPERATOR)
    return user, grant, secret


def _refresh(issued: services.IssuedTokens, *, scope: str = ""):
    return services.refresh(
        client_id=support.PUBLIC_CLIENT,
        refresh_token=issued.refresh_token,
        resource=support.RESOURCE,
        scope=scope,
        ip_address=None,
    )


def _access_scope(issued: services.IssuedTokens) -> str:
    return McpToken.objects.get(
        digest=credentials.digest(issued.access_token), kind=TokenKind.ACCESS
    ).scopes


# -- parsing the request ---------------------------------------------------------------


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("", READ),
        (READ, READ),
        (BOTH, BOTH),
        ("juristid.write", BOTH),
        ("offline_access juristid.write openid", BOTH),
    ],
)
def test_the_write_scope_is_accepted_in_a_request_while_writes_are_on(raw, expected):
    assert oauth.granted_scope(raw) == expected


@pytest.mark.parametrize("raw", [BOTH, "juristid.write"])
def test_with_writes_off_the_write_scope_is_narrowed_away_not_refused(settings, raw):
    settings.MCP_WRITES_ENABLED = False
    assert oauth.granted_scope(raw) == READ


@pytest.mark.parametrize("raw", ["juristid.admin", f"{BOTH} juristid.delete", "JURISTID.WRITE"])
def test_an_unknown_scope_is_still_refused(raw):
    assert oauth.granted_scope(raw) is None


def test_an_unknown_scope_beside_write_goes_back_as_invalid_scope(client):
    assert client.post("/konto/varav/", {"password": PASSWORD}).status_code == 302
    url = f"{AUTHORIZE}?{urlencode(support.authorize_params(scope=BOTH + ' juristid.admin'))}"
    response = client.get(url)
    assert response.status_code == 302
    assert "error=invalid_scope" in response["Location"]


# -- the consent step --------------------------------------------------------------------


def test_a_write_grant_asking_for_write_gets_read_and_write(moment):
    user, _, secret = _person(moment)
    issued = support.tokens_for(user, secret, moment, scope=BOTH)
    assert issued.scope == BOTH
    assert issued.as_json()["scope"] == BOTH
    assert _access_scope(issued) == BOTH


def test_a_write_grant_asking_for_read_gets_read_only(moment):
    user, _, secret = _person(moment)
    issued = support.tokens_for(user, secret, moment, scope=READ)
    assert issued.scope == READ


def test_a_read_only_grant_asking_for_write_is_narrowed_to_read(moment):
    user, _, secret = _person(moment, write=False)
    issued = support.tokens_for(user, secret, moment, scope=BOTH)
    assert issued.scope == READ and _access_scope(issued) == READ
    granted = McpAccessEvent.objects.get(event_type=McpAccessEventType.AUTHORIZATION_GRANTED)
    assert granted.detail["scope"] == READ and granted.detail["requested_scope"] == BOTH


def test_a_read_only_grants_token_never_carries_write_however_it_asks(moment):
    user, _, secret = _person(moment, write=False)
    for steps, scope in ((0, BOTH), (1, "juristid.write offline_access")):
        code = support.code_for(user, secret, moment, steps=steps, scope=scope)
        assert McpAuthorizationCode.objects.get(digest=_digest(code)).scope == READ
    assert not McpToken.objects.filter(scopes__contains="juristid.write").exists()


def _digest(raw: str) -> str:
    return credentials.digest(raw)


def test_writes_switched_off_mean_read_even_for_a_write_grant(moment, settings):
    user, _, secret = _person(moment)
    settings.MCP_WRITES_ENABLED = False
    issued = support.tokens_for(user, secret, moment, scope=BOTH)
    assert issued.scope == READ


def test_a_write_grant_revoked_before_the_consent_step_gets_read(moment):
    user, _, secret = _person(moment)
    services.revoke_write(user=user, reason="test", provenance=support.OPERATOR)
    issued = support.tokens_for(user, secret, moment, scope=BOTH)
    assert issued.scope == READ


def test_write_revoked_between_consent_and_exchange_is_narrowed_at_the_exchange(moment):
    user, _, secret = _person(moment)
    code = support.code_for(user, secret, moment, scope=BOTH)
    assert McpAuthorizationCode.objects.get(digest=_digest(code)).scope == BOTH
    services.revoke_write(user=user, reason="test", provenance=support.OPERATOR)
    issued = services.exchange_code(
        client_id=support.PUBLIC_CLIENT,
        code=code,
        redirect_uri=support.PUBLIC_REDIRECT,
        code_verifier=support.VERIFIER,
        resource=support.RESOURCE,
        ip_address=None,
    )
    assert isinstance(issued, services.IssuedTokens) and issued.scope == READ


def test_a_reader_asking_for_write_is_narrowed_to_read(moment, settings):
    reader = factories.ReaderFactory()
    _, secret = support.enrolled(reader, moment)
    # Even a write decision forced into the row by hand gives a READER nothing.
    McpAccessGrant.objects.filter(user=reader).update(write_enabled_at=timezone.now())
    issued = support.tokens_for(reader, secret, moment, scope=BOTH)
    assert issued.scope == READ


def test_a_break_glass_administrator_is_refused_at_authorize_whatever_scope(moment, monkeypatch):
    """An ADMINISTRATOR without a business role never connects, so never writes."""
    admin = factories.AdministratorFactory()
    monkeypatch.setattr(authorization, "active_break_glass_grant_id", lambda user: uuid.uuid4())
    outcome = services.authorize(
        user=admin,
        request=support.request_for(scope=BOTH),
        totp_code="123456",
        ip_address=None,
    )
    assert outcome.status == "no_grant"
    assert not McpAuthorizationCode.objects.exists()


def test_an_administrator_whose_role_changed_after_grant_write_gets_no_write(moment):
    user, _, secret = _person(moment)
    user.role = "ADMINISTRATOR"
    user.save(update_fields=["role"])
    outcome = services.authorize(
        user=user,
        request=support.request_for(scope=BOTH),
        totp_code=support.code_at(secret, moment),
        ip_address=None,
    )
    assert outcome.status == "no_grant"


# -- the consent page says which ----------------------------------------------------------


def _page(client, user, scope: str):
    assert client.post("/konto/varav/", {"password": PASSWORD}).status_code == 302
    assert client.post("/konto/kasutaja/vaheta/", {"user_id": str(user.pk)}).status_code == 302
    return client.get(f"{AUTHORIZE}?{urlencode(support.authorize_params(scope=scope))}")


def test_the_consent_page_says_read_only_for_a_read_request(client, moment):
    user, _, _ = _person(moment)
    body = _page(client, user, READ).content.decode()
    assert views.READ_ONLY_DESCRIPTION in body
    assert "vajab eraldi kinnitust Juristidis" not in body


def test_the_consent_page_names_preparing_changes_for_a_write_request(client, moment):
    user, _, _ = _person(moment)
    body = _page(client, user, BOTH).content.decode()
    assert "Lugemine ja muudatuste ettevalmistamine" in body
    assert "iga muudatus vajab eraldi kinnitust Juristidis" in body


def test_the_write_consent_page_is_the_same_for_a_persona_without_write(client, moment):
    """Uniform until the persona's own code is verified (MCPSEC-01): no hint of who may write."""
    writer, _, _ = _person(moment)
    with_write = _page(client, writer, BOTH).content.decode()
    client.logout()
    reader_only = factories.UserFactory(display_name=writer.display_name)
    support.enrolled(reader_only, moment)
    without_write = _page(client, reader_only, BOTH).content.decode()
    client.logout()
    nobody = factories.UserFactory(display_name=writer.display_name)
    no_grant = _page(client, nobody, BOTH).content.decode()

    def scrub(body: str) -> str:
        # The consent block itself; the header's persona menu lists every
        # account and so differs by construction.
        page = body[body.index('<div class="page page--narrow">') : body.index("</form>")]
        return re.sub(r'name="csrfmiddlewaretoken" value="[^"]+"', "", page)

    assert scrub(with_write) == scrub(without_write) == scrub(no_grant)


def test_with_writes_off_the_consent_page_shows_read_only_for_a_write_request(
    client, moment, settings
):
    settings.MCP_WRITES_ENABLED = False
    user, _, _ = _person(moment)
    body = _page(client, user, BOTH).content.decode()
    assert views.READ_ONLY_DESCRIPTION in body


# -- refresh ---------------------------------------------------------------------------


def test_a_refresh_keeps_write_while_it_is_allowed(moment):
    user, _, secret = _person(moment)
    first = support.tokens_for(user, secret, moment, scope=BOTH)
    second = _refresh(first)
    assert isinstance(second, services.IssuedTokens) and second.scope == BOTH


def test_a_refresh_cannot_enlarge_a_read_token_to_write(moment):
    user, _, secret = _person(moment)
    first = support.tokens_for(user, secret, moment, scope=READ)
    refused = _refresh(first, scope=BOTH)
    assert isinstance(refused, services.OAuthError) and refused.error == "invalid_scope"
    # And asking for nothing gets what the family has: read.
    again = _refresh(first)
    assert isinstance(again, services.IssuedTokens) and again.scope == READ


def test_a_refresh_after_revoke_write_drops_write_and_never_gets_it_back(moment):
    user, _, secret = _person(moment)
    first = support.tokens_for(user, secret, moment, scope=BOTH)
    services.revoke_write(user=user, reason="test", provenance=support.OPERATOR)
    second = _refresh(first)
    assert isinstance(second, services.IssuedTokens) and second.scope == READ
    assert _access_scope(second) == READ
    services.grant_write(user=user, provenance=support.OPERATOR)
    third = _refresh(second)
    assert isinstance(third, services.IssuedTokens) and third.scope == READ
    widened = _refresh(third, scope=BOTH)
    assert isinstance(widened, services.OAuthError) and widened.error == "invalid_scope"


def test_a_refresh_with_writes_switched_off_drops_write(moment, settings):
    user, _, secret = _person(moment)
    first = support.tokens_for(user, secret, moment, scope=BOTH)
    settings.MCP_WRITES_ENABLED = False
    second = _refresh(first)
    assert isinstance(second, services.IssuedTokens) and second.scope == READ


def test_a_refresh_may_ask_for_read_only_from_a_write_family(moment):
    user, _, secret = _person(moment)
    first = support.tokens_for(user, secret, moment, scope=BOTH)
    second = _refresh(first, scope=READ)
    assert isinstance(second, services.IssuedTokens) and second.scope == READ


def test_the_token_endpoint_states_the_narrowed_scope(client, moment):
    user, _, secret = _person(moment, write=False)
    code = support.code_for(user, secret, moment, scope=BOTH)
    response = client.post(
        "/oauth/token",
        urlencode(
            {
                "grant_type": "authorization_code",
                "code": code,
                "redirect_uri": support.PUBLIC_REDIRECT,
                "code_verifier": support.VERIFIER,
                "client_id": support.PUBLIC_CLIENT,
                "resource": support.RESOURCE,
            }
        ),
        content_type="application/x-www-form-urlencoded",
    )
    assert response.status_code == 200
    assert response.json()["scope"] == READ


def test_the_request_object_holds_at_most_what_may_be_granted():
    query = QueryDict(mutable=True)
    query.update(support.authorize_params(scope=BOTH))
    parsed = oauth.parse_authorization(query)
    assert isinstance(parsed, services.AuthorizationRequest) and parsed.scope == BOTH


def test_a_refresh_of_a_family_from_an_earlier_enablement_comes_back_read_only(moment):
    """revoke-write, then grant-write: the old family stays read-only (security review P3-5)."""
    user, _, secret = _person(moment)
    first = support.tokens_for(user, secret, moment, scope=BOTH)
    services.revoke_write(user=user, reason="test", provenance=support.OPERATOR)
    services.grant_write(user=user, provenance=support.OPERATOR)
    second = _refresh(first)
    assert isinstance(second, services.IssuedTokens) and second.scope == READ
    # A new connection gets write again.
    fresh = support.tokens_for(user, secret, moment, steps=1, scope=BOTH)
    assert fresh.scope == BOTH


def test_a_code_minted_under_an_earlier_enablement_exchanges_for_read(moment):
    user, _, secret = _person(moment)
    code = support.code_for(user, secret, moment, scope=BOTH)
    services.revoke_write(user=user, reason="test", provenance=support.OPERATOR)
    services.grant_write(user=user, provenance=support.OPERATOR)
    issued = services.exchange_code(
        client_id=support.PUBLIC_CLIENT,
        code=code,
        redirect_uri=support.PUBLIC_REDIRECT,
        code_verifier=support.VERIFIER,
        resource=support.RESOURCE,
        ip_address=None,
    )
    assert isinstance(issued, services.IssuedTokens) and issued.scope == READ
