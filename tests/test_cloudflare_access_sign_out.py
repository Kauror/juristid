"""Signing out under Cloudflare Access stays signed out (csf_92343c92, ADR 0183).

Dormant mode. Cloudflare owns the sign-in and forwards the same signed assertion
on every request until its own session ends, so ending only the local session
ended nothing: the next request carried the assertion again, the middleware
verified it — correctly — and signed the person straight back in.

Now «Välju» sends the browser on to Cloudflare's logout endpoint, built from
configuration and never from the request, and the fresh session refuses the
assertion it was signed out with. Everything is mocked: the team's signing key
is the test's own and nothing reaches Cloudflare.
"""

from __future__ import annotations

import time

import jwt
import pytest
from cryptography.hazmat.primitives.asymmetric import rsa
from django.core.cache import cache
from django.urls import reverse

from app.accounts import cloudflare_access
from app.accounts.enums import AuthMode
from app.audit.enums import SecurityEventType
from app.audit.models import SecurityAuditEvent
from tests import factories

pytestmark = pytest.mark.django_db

TEAM_DOMAIN = "https://naidiskoda.cloudflareaccess.com"
TEAM_LOGOUT = "https://naidiskoda.cloudflareaccess.com/cdn-cgi/access/logout"
AUDIENCE = "0123456789abcdef0123456789abcdef0123456789abcdef0123456789abcdef"
PERSON = "jurist@naidiskoda.invalid"


@pytest.fixture
def signing_key():
    return rsa.generate_private_key(public_exponent=65537, key_size=2048)


@pytest.fixture(autouse=True)
def access_configured(settings, signing_key, monkeypatch):
    settings.AUTH_MODE = AuthMode.CLOUDFLARE_ACCESS
    settings.CF_ACCESS_TEAM_DOMAIN = TEAM_DOMAIN
    settings.CF_ACCESS_AUDIENCE = AUDIENCE
    settings.DEV_LOGIN_ENABLED = False
    cache.clear()

    published = jwt.algorithms.RSAAlgorithm.to_jwk(signing_key.public_key(), as_dict=True)
    published.update({"kid": "current", "alg": "RS256", "use": "sig"})
    monkeypatch.setattr(cloudflare_access, "_fetch_jwks", lambda: {"keys": [published]})
    yield
    cache.clear()


@pytest.fixture
def jurist():
    return factories.UserFactory(is_synthetic=False, upn=PERSON, email=PERSON)


def assertion(key, **claims) -> str:
    now = int(time.time())
    payload = {
        "aud": AUDIENCE,
        "iss": TEAM_DOMAIN,
        "email": PERSON,
        "iat": now,
        "exp": now + 3600,
        **claims,
    }
    return jwt.encode(payload, key, algorithm="RS256", headers={"kid": "current"})


def _get(client, token, path="/osakond/"):
    return client.get(path, HTTP_CF_ACCESS_JWT_ASSERTION=token)


def _signed_in(response) -> bool:
    return response.wsgi_request.user.is_authenticated


def _sign_out(client, token):
    return client.post(reverse("accounts:sign_out"), HTTP_CF_ACCESS_JWT_ASSERTION=token)


# -- the sign-out --------------------------------------------------------------------


def test_signing_out_goes_on_to_the_team_logout(client, signing_key, jurist):
    token = assertion(signing_key)
    assert _signed_in(_get(client, token))

    response = _sign_out(client, token)

    assert response.status_code == 302
    assert response["Location"] == TEAM_LOGOUT
    assert response["Clear-Site-Data"] == '"cache", "storage"'
    assert "_auth_user_id" not in client.session
    assert SecurityAuditEvent.objects.filter(
        event_type=SecurityEventType.SIGNED_OUT, actor=jurist, detail__path="cloudflare_access"
    ).exists()


def test_the_same_assertion_does_not_sign_the_person_back_in(client, signing_key, jurist):
    token = assertion(signing_key)
    _get(client, token)
    _sign_out(client, token)

    for _ in range(2):
        response = _get(client, token)
        # A page, not a redirect: there is nothing to loop through.
        assert response.status_code == 200
        assert not _signed_in(response)
        assert "_auth_user_id" not in client.session
        body = response.content.decode()
        assert "Oled välja logitud" in body
        assert f'href="{TEAM_LOGOUT}"' in body
        assert 'href="/cdn-cgi/access/logout"' in body
        assert "no-store" in response["Cache-Control"]


def test_nothing_behind_the_door_answers_the_signed_out_assertion(client, signing_key, jurist):
    token = assertion(signing_key)
    _get(client, token)
    _sign_out(client, token)

    response = _get(client, token, reverse("matters:my_work"))

    assert response.status_code == 200
    assert not _signed_in(response)
    assert "Oled välja logitud" in response.content.decode()


def test_a_new_access_sign_in_is_let_in_again(client, signing_key, jurist):
    token = assertion(signing_key)
    _get(client, token)
    _sign_out(client, token)

    fresh = assertion(signing_key, iat=int(time.time()) + 1, nonce="uus-sisselogimine")
    assert fresh != token
    response = _get(client, fresh)

    assert response.status_code == 200
    assert _signed_in(response)
    assert cloudflare_access.SIGNED_OUT_ASSERTION not in client.session


def test_the_session_keeps_a_keyed_fingerprint_not_the_assertion(client, signing_key, jurist):
    token = assertion(signing_key)
    _get(client, token)
    _sign_out(client, token)

    stored = client.session[cloudflare_access.SIGNED_OUT_ASSERTION]
    assert len(stored) == 32
    assert stored not in token
    assert token not in str(dict(client.session.items()))


def test_a_forged_assertion_is_still_refused_after_sign_out(client, signing_key, jurist):
    token = assertion(signing_key)
    _get(client, token)
    _sign_out(client, token)

    other_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    assert _get(client, assertion(other_key)).status_code == 403


# -- the logout address is configuration, validated --------------------------------


@pytest.mark.parametrize(
    "configured",
    [
        "https://naidiskoda.cloudflareaccess.com",
        "https://naidiskoda.cloudflareaccess.com/",
        "naidiskoda.cloudflareaccess.com",
        "https://NAIDISKODA.cloudflareaccess.com",
    ],
)
def test_a_team_domain_gives_the_team_logout(settings, configured):
    settings.CF_ACCESS_TEAM_DOMAIN = configured
    assert cloudflare_access.logout_url() == TEAM_LOGOUT


@pytest.mark.parametrize(
    "configured",
    [
        "",
        "http://naidiskoda.cloudflareaccess.com",
        "https://naidiskoda.cloudflareaccess.com/muu",
        "https://naidiskoda.cloudflareaccess.com?next=https://evil.invalid",
        "https://naidiskoda.cloudflareaccess.com#x",
        "https://naidiskoda.cloudflareaccess.com:8443",
        "https://user:pw@naidiskoda.cloudflareaccess.com",
        "https://cloudflareaccess.com",
        "https://evilcloudflareaccess.com",
        "https://naidiskoda.cloudflareaccess.com.evil.invalid",
        "https://evil.invalid/.cloudflareaccess.com",
        "https://evil.invalid\\.cloudflareaccess.com",
        "https://-x.cloudflareaccess.com",
    ],
)
def test_anything_else_falls_back_to_this_hosts_own_logout(settings, configured):
    settings.CF_ACCESS_TEAM_DOMAIN = configured
    assert cloudflare_access.team_logout_url() == ""
    assert cloudflare_access.logout_url() == "/cdn-cgi/access/logout"


def test_the_request_cannot_choose_where_sign_out_goes(client, signing_key, jurist):
    token = assertion(signing_key)
    _get(client, token)

    response = client.post(
        reverse("accounts:sign_out") + "?next=https://evil.invalid/",
        {"next": "https://evil.invalid/"},
        HTTP_CF_ACCESS_JWT_ASSERTION=token,
        HTTP_REFERER="https://evil.invalid/",
    )

    assert response["Location"] == TEAM_LOGOUT
