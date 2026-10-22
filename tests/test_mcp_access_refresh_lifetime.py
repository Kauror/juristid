"""A token family has an absolute lifetime, and ChatGPT must sign (MCPSEC-08).

* Every rotation issued a fresh 30-day refresh token, so one authenticator
  code bought a connection for as long as it was used monthly. Now a family
  ends `MCP_REFRESH_FAMILY_MAX_SECONDS` (90 days) after the code was typed.
* ChatGPT was registered for `private_key_jwt` *and* `none`, so a leaked
  refresh token could be rotated by anybody. Now `private_key_jwt` only, the
  metadata advertises only configured methods, and a family begun with a
  signed assertion cannot be refreshed without one.
"""

from __future__ import annotations

from datetime import timedelta
from urllib.parse import urlencode

import pytest
from django.test import Client
from django.utils import timezone

from app.mcp_access import config, services
from app.mcp_access.models import McpAccessEvent, McpToken, TokenKind
from tests import factories
from tests import mcp_access_support as support

pytestmark = pytest.mark.django_db

ASSERTION_TYPE = "urn:ietf:params:oauth:client-assertion-type:jwt-bearer"


@pytest.fixture(autouse=True)
def enabled(settings):
    support.enable(settings)


@pytest.fixture
def moment(monkeypatch):
    return support.freeze_totp(monkeypatch)


def _refresh(issued, **kwargs):
    return services.refresh(
        client_id=kwargs.pop("client_id", support.PUBLIC_CLIENT),
        refresh_token=issued.refresh_token,
        resource="",
        scope="",
        ip_address=None,
        **kwargs,
    )


def test_a_family_past_its_absolute_lifetime_is_refused_and_ended(moment):
    user = factories.UserFactory()
    _, secret = support.enrolled(user, moment)
    issued = support.tokens_for(user, secret, moment)
    rotated = _refresh(issued)
    assert isinstance(rotated, services.IssuedTokens)
    McpToken.objects.filter(family_id=issued.family_id).update(
        family_started_at=timezone.now() - timedelta(days=91)
    )

    refused = _refresh(rotated)

    assert isinstance(refused, services.OAuthError) and refused.error == "invalid_grant"
    assert not McpToken.objects.filter(family_id=issued.family_id, revoked_at__isnull=True)
    assert McpAccessEvent.objects.filter(detail__reason="family_expired").exists()


def test_rotation_keeps_the_family_start_and_never_outlives_it(moment, settings):
    settings.MCP_REFRESH_FAMILY_MAX_SECONDS = 10 * 24 * 3600
    user = factories.UserFactory()
    _, secret = support.enrolled(user, moment)
    issued = support.tokens_for(user, secret, moment)
    first = McpToken.objects.get(family_id=issued.family_id, kind=TokenKind.REFRESH)
    assert first.family_started_at is not None
    end = first.family_started_at + timedelta(days=10)
    assert first.expires_at <= end
    rotated = _refresh(issued)
    assert isinstance(rotated, services.IssuedTokens)
    newest = McpToken.objects.filter(
        family_id=issued.family_id, kind=TokenKind.REFRESH, revoked_at__isnull=True
    ).get()
    assert newest.family_started_at == first.family_started_at
    assert newest.expires_at <= end


def test_chatgpt_is_registered_for_signed_assertions_only():
    from config import settings as project_settings

    chatgpt = project_settings.MCP_OAUTH_CLIENTS["https://chatgpt.com/oauth/client.json"]
    assert chatgpt["token_endpoint_auth_methods"] == ["private_key_jwt"]


def test_the_metadata_advertises_only_configured_methods(settings):
    settings.MCP_OAUTH_CLIENTS = {
        support.JWT_CLIENT: {
            "client_name": "ChatGPT",
            "redirect_uris": [support.JWT_REDIRECT],
            "token_endpoint_auth_methods": ["private_key_jwt"],
            "jwks_uri": support.JWKS_URI,
        }
    }
    assert config.advertised_auth_methods() == ["private_key_jwt"]
    from tests.gate import apply_shared_gate

    apply_shared_gate(settings)
    metadata = Client().get("/.well-known/oauth-authorization-server").json()
    assert metadata["token_endpoint_auth_methods_supported"] == ["private_key_jwt"]
    assert metadata["revocation_endpoint_auth_methods_supported"] == ["private_key_jwt"]


def test_a_family_begun_with_an_assertion_needs_one_to_refresh(moment):
    user = factories.UserFactory()
    _, secret = support.enrolled(user, moment)
    code = support.code_for(user, secret, moment)
    issued = services.exchange_code(
        client_id=support.PUBLIC_CLIENT,
        code=code,
        redirect_uri=support.PUBLIC_REDIRECT,
        code_verifier=support.VERIFIER,
        resource=support.RESOURCE,
        ip_address=None,
        auth_method=config.AUTH_METHOD_PRIVATE_KEY_JWT,
    )
    assert isinstance(issued, services.IssuedTokens)
    refused = _refresh(issued, auth_method=config.AUTH_METHOD_NONE)
    assert isinstance(refused, services.OAuthError)
    assert McpAccessEvent.objects.filter(detail__reason="auth_method_changed").exists()
    assert isinstance(
        _refresh(issued, auth_method=config.AUTH_METHOD_PRIVATE_KEY_JWT), services.IssuedTokens
    )


def test_the_token_endpoint_records_how_the_client_authenticated(client, moment, monkeypatch):
    keys = support.FakeClientKeys()
    keys.install(monkeypatch)
    user = factories.UserFactory()
    _, secret = support.enrolled(user, moment)
    code = support.code_for(
        user, secret, moment, client_id=support.JWT_CLIENT, redirect_uri=support.JWT_REDIRECT
    )
    response = client.post(
        "/oauth/token",
        urlencode(
            {
                "grant_type": "authorization_code",
                "code": code,
                "redirect_uri": support.JWT_REDIRECT,
                "code_verifier": support.VERIFIER,
                "resource": support.RESOURCE,
                "client_assertion_type": ASSERTION_TYPE,
                "client_assertion": keys.assertion(),
            }
        ),
        content_type="application/x-www-form-urlencoded",
    )
    assert response.status_code == 200, response.content
    assert set(McpToken.objects.values_list("auth_method", flat=True)) == {"private_key_jwt"}
