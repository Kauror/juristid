"""`/oauth/token` and `/oauth/revoke`: codes, PKCE, rotation, client auth (ADR 0185 §4)."""

from __future__ import annotations

import json
import logging
from datetime import timedelta
from urllib.parse import urlencode

import jwt
import pytest
from django.core.cache import cache
from django.db.models import F
from django.urls import reverse
from django.utils import timezone

from app.accounts.models import User
from app.mcp_access import credentials
from app.mcp_access.models import (
    McpAccessEvent,
    McpAccessEventType,
    McpAuthorizationCode,
    McpToken,
    TokenKind,
)
from app.mcp_access.verification import verify_access_token
from tests import factories
from tests import mcp_access_support as support

pytestmark = pytest.mark.django_db

TOKEN = "/oauth/token"  # noqa: S105 - a path, not a credential
REVOKE = "/oauth/revoke"


@pytest.fixture(autouse=True)
def mcp_on(settings):
    support.enable(settings)
    cache.clear()
    yield
    cache.clear()


@pytest.fixture
def moment(monkeypatch):
    return support.freeze_totp(monkeypatch)


@pytest.fixture
def person(moment):
    user = factories.UserFactory()
    _, secret = support.enrolled(user, moment)
    return user, secret


def form(client, path, data, **extra):
    """POST as OAuth clients do: application/x-www-form-urlencoded."""
    return client.post(
        path, urlencode(data), content_type="application/x-www-form-urlencoded", **extra
    )


def _exchange(client, code, **overrides):
    data = {
        "grant_type": "authorization_code",
        "code": code,
        "redirect_uri": support.PUBLIC_REDIRECT,
        "code_verifier": support.VERIFIER,
        "client_id": support.PUBLIC_CLIENT,
        "resource": support.RESOURCE,
    }
    data.update(overrides)
    return form(client, TOKEN, {k: v for k, v in data.items() if v is not None})


def _refresh(client, refresh_token, **overrides):
    data = {
        "grant_type": "refresh_token",
        "refresh_token": refresh_token,
        "client_id": support.PUBLIC_CLIENT,
    }
    data.update(overrides)
    return form(client, TOKEN, {k: v for k, v in data.items() if v is not None})


def _error(response) -> str:
    return response.json()["error"]


def test_the_routes_are_exact_paths():
    assert reverse("mcp_access:token") == TOKEN
    assert reverse("mcp_access:revoke") == REVOKE


# -- authorization_code -------------------------------------------------------------


def test_a_code_is_exchanged_for_a_bearer_pair_that_is_never_cached(client, person, moment):
    user, secret = person
    response = _exchange(client, support.code_for(user, secret, moment))

    assert response.status_code == 200
    assert response["Cache-Control"] == "no-store"
    assert response["Pragma"] == "no-cache"
    body = response.json()
    assert body["token_type"] == "Bearer"  # noqa: S105
    assert body["expires_in"] == 3600
    assert body["scope"] == "juristid.read"
    assert body["access_token"] != body["refresh_token"]
    assert verify_access_token(body["access_token"]).user_id == user.pk
    assert McpAccessEvent.objects.filter(event_type=McpAccessEventType.TOKEN_ISSUED).count() == 1


def test_a_code_works_once_and_its_second_use_revokes_what_the_first_bought(client, person, moment):
    user, secret = person
    code = support.code_for(user, secret, moment)
    first = _exchange(client, code).json()

    again = _exchange(client, code)

    assert again.status_code == 400 and _error(again) == "invalid_grant"
    assert verify_access_token(first["access_token"]) is None
    assert _error(_refresh(client, first["refresh_token"])) == "invalid_grant"


def test_an_expired_code_is_refused(client, person, moment):
    user, secret = person
    code = support.code_for(user, secret, moment)
    McpAuthorizationCode.objects.update(expires_at=timezone.now() - timedelta(seconds=1))
    assert _error(_exchange(client, code)) == "invalid_grant"


def test_a_wrong_pkce_verifier_is_refused_and_spends_the_code(client, person, moment):
    user, secret = person
    code = support.code_for(user, secret, moment)
    wrong = _exchange(client, code, code_verifier="x" * 43)
    assert _error(wrong) == "invalid_grant"
    assert _error(_exchange(client, code)) == "invalid_grant"


def test_a_missing_verifier_is_refused(client, person, moment):
    user, secret = person
    code = support.code_for(user, secret, moment)
    assert _error(_exchange(client, code, code_verifier=None)) == "invalid_grant"


def test_the_redirect_uri_must_be_the_one_the_code_was_issued_for(client, person, moment):
    user, secret = person
    code = support.code_for(user, secret, moment)
    response = _exchange(client, code, redirect_uri=support.PUBLIC_REDIRECT + "/x")
    assert _error(response) == "invalid_grant"


def test_the_resource_must_be_the_one_the_code_was_issued_for(client, person, moment):
    user, secret = person
    code = support.code_for(user, secret, moment)
    assert _error(_exchange(client, code, resource=None)) == "invalid_target"
    assert _error(_exchange(client, code, resource=support.TUNNEL_RESOURCE)) == "invalid_grant"


def test_another_client_cannot_exchange_the_code(client, person, moment, monkeypatch):
    user, secret = person
    code = support.code_for(user, secret, moment)
    keys = support.FakeClientKeys()
    keys.install(monkeypatch)
    response = _exchange(
        client,
        code,
        client_id=None,
        client_assertion_type="urn:ietf:params:oauth:client-assertion-type:jwt-bearer",
        client_assertion=keys.assertion(),
    )
    assert _error(response) == "invalid_grant"


def test_an_unknown_client_is_invalid_client(client, person, moment):
    user, secret = person
    code = support.code_for(user, secret, moment)
    response = _exchange(client, code, client_id="https://evil.example.test/client.json")
    assert response.status_code == 401 and _error(response) == "invalid_client"
    assert response["Cache-Control"] == "no-store"


def test_an_account_that_changed_since_the_code_gets_no_token(client, person, moment):
    user, secret = person
    code = support.code_for(user, secret, moment)
    User.objects.filter(pk=user.pk).update(security_epoch=F("security_epoch") + 1)
    assert _error(_exchange(client, code)) == "invalid_grant"
    assert not McpToken.objects.exists()


def test_http_basic_client_authentication_is_refused(client, person, moment):
    user, secret = person
    code = support.code_for(user, secret, moment)
    response = form(
        client,
        TOKEN,
        {
            "grant_type": "authorization_code",
            "code": code,
            "client_id": support.PUBLIC_CLIENT,
        },
        headers={"Authorization": "Basic Zm9vOmJhcg=="},
    )
    assert response.status_code == 401 and _error(response) == "invalid_client"


def test_a_repeated_parameter_is_invalid_request(client):
    response = client.post(
        TOKEN,
        "grant_type=refresh_token&grant_type=authorization_code&client_id=x",
        content_type="application/x-www-form-urlencoded",
    )
    assert response.status_code == 400 and _error(response) == "invalid_request"


def test_a_json_body_is_invalid_request(client):
    response = client.post(
        TOKEN, json.dumps({"grant_type": "refresh_token"}), content_type="application/json"
    )
    assert _error(response) == "invalid_request"


def test_an_unknown_grant_type_is_unsupported(client):
    response = form(
        client, TOKEN, {"grant_type": "client_credentials", "client_id": support.PUBLIC_CLIENT}
    )
    assert _error(response) == "unsupported_grant_type"


def test_get_is_not_a_token_request(client):
    assert client.get(TOKEN).status_code == 405


def test_the_endpoint_is_rate_limited_per_address(client):
    statuses = [
        form(client, TOKEN, {"grant_type": "x"}, REMOTE_ADDR="192.0.2.7").status_code
        for _ in range(31)
    ]
    assert statuses[:30] == [401] * 30
    assert statuses[30] == 429
    # Another address is somebody else.
    assert form(client, TOKEN, {"grant_type": "x"}, REMOTE_ADDR="192.0.2.8").status_code == 401


# -- refresh_token --------------------------------------------------------------------


def test_a_refresh_rotates_the_refresh_token(client, person, moment):
    user, secret = person
    first = _exchange(client, support.code_for(user, secret, moment)).json()

    response = _refresh(client, first["refresh_token"], resource=support.RESOURCE + "/")

    assert response.status_code == 200 and response["Cache-Control"] == "no-store"
    second = response.json()
    assert second["refresh_token"] != first["refresh_token"]
    assert verify_access_token(second["access_token"]).user_id == user.pk
    old = McpToken.objects.get(digest=credentials.digest(first["refresh_token"]))
    assert old.replaced_by is not None and old.last_used_at is not None


def test_reusing_a_rotated_refresh_token_revokes_the_whole_family(client, person, moment):
    """Past the retry grace window (MCPSEC-09, tests/test_mcp_access_refresh_grace.py)."""
    from datetime import timedelta

    from django.utils import timezone

    user, secret = person
    first = _exchange(client, support.code_for(user, secret, moment)).json()
    second = _refresh(client, first["refresh_token"]).json()
    McpToken.objects.filter(replaced_by__isnull=False).update(
        revoked_at=timezone.now() - timedelta(minutes=5)
    )

    reuse = _refresh(client, first["refresh_token"])

    assert reuse.status_code == 400 and _error(reuse) == "invalid_grant"
    assert verify_access_token(second["access_token"]) is None
    assert _error(_refresh(client, second["refresh_token"])) == "invalid_grant"
    assert McpAccessEvent.objects.filter(
        event_type=McpAccessEventType.REFRESH_REUSE_DETECTED
    ).exists()
    assert not McpToken.objects.filter(revoked_at__isnull=True).exists()


def test_a_refresh_for_another_resource_is_refused(client, person, moment):
    user, secret = person
    first = _exchange(client, support.code_for(user, secret, moment)).json()
    response = _refresh(client, first["refresh_token"], resource=support.TUNNEL_RESOURCE)
    assert _error(response) == "invalid_target"


def test_a_refresh_cannot_widen_the_scope(client, person, moment):
    user, secret = person
    first = _exchange(client, support.code_for(user, secret, moment)).json()
    response = _refresh(client, first["refresh_token"], scope="juristid.read juristid.write")
    assert _error(response) == "invalid_scope"


def test_a_refresh_after_an_epoch_bump_is_refused(client, person, moment):
    user, secret = person
    first = _exchange(client, support.code_for(user, secret, moment)).json()
    User.objects.filter(pk=user.pk).update(security_epoch=F("security_epoch") + 1)
    assert _error(_refresh(client, first["refresh_token"])) == "invalid_grant"


def test_an_expired_refresh_token_is_refused(client, person, moment):
    user, secret = person
    first = _exchange(client, support.code_for(user, secret, moment)).json()
    McpToken.objects.filter(kind=TokenKind.REFRESH).update(
        expires_at=timezone.now() - timedelta(seconds=1)
    )
    assert _error(_refresh(client, first["refresh_token"])) == "invalid_grant"


def test_another_client_cannot_use_the_refresh_token(client, person, moment, monkeypatch):
    user, secret = person
    first = _exchange(client, support.code_for(user, secret, moment)).json()
    keys = support.FakeClientKeys()
    keys.install(monkeypatch)
    response = _refresh(
        client,
        first["refresh_token"],
        client_id=None,
        client_assertion_type="urn:ietf:params:oauth:client-assertion-type:jwt-bearer",
        client_assertion=keys.assertion(),
    )
    assert _error(response) == "invalid_grant"


# -- private_key_jwt -------------------------------------------------------------------

ASSERTION_TYPE = "urn:ietf:params:oauth:client-assertion-type:jwt-bearer"


@pytest.fixture
def keys(monkeypatch):
    keys = support.FakeClientKeys()
    keys.install(monkeypatch)
    return keys


def _jwt_exchange(client, code, assertion, **overrides):
    return _exchange(
        client,
        code,
        client_id=overrides.pop("client_id", None),
        redirect_uri=support.JWT_REDIRECT,
        client_assertion_type=overrides.pop("client_assertion_type", ASSERTION_TYPE),
        client_assertion=assertion,
        **overrides,
    )


def _jwt_code(person, moment):
    user, secret = person
    return support.code_for(
        user, secret, moment, client_id=support.JWT_CLIENT, redirect_uri=support.JWT_REDIRECT
    )


def test_a_private_key_jwt_client_is_authenticated_by_its_signed_assertion(
    client, person, moment, keys
):
    response = _jwt_exchange(client, _jwt_code(person, moment), keys.assertion())
    assert response.status_code == 200, response.content
    access = verify_access_token(response.json()["access_token"])
    assert access.client_id == support.JWT_CLIENT


def test_the_issuer_is_an_accepted_audience_too(client, person, moment, keys):
    response = _jwt_exchange(client, _jwt_code(person, moment), keys.assertion(aud=support.ORIGIN))
    assert response.status_code == 200


@pytest.mark.parametrize(
    "claims",
    [
        {"aud": "https://elsewhere.example.test/oauth/token"},
        {"iss": support.PUBLIC_CLIENT},
        {"sub": "https://someone-else.example.test"},
        {"exp": 1},
        {"exp": 10**10},
        {"jti": None},
        {"exp": None},
    ],
    ids=["wrong-aud", "wrong-iss", "wrong-sub", "expired", "too-long", "no-jti", "no-exp"],
)
def test_a_bad_assertion_is_invalid_client(client, person, moment, keys, claims):
    response = _jwt_exchange(client, _jwt_code(person, moment), keys.assertion(**claims))
    assert response.status_code == 401 and _error(response) == "invalid_client"


def test_a_replayed_assertion_is_invalid_client(client, person, moment, keys):
    assertion = keys.assertion()
    first = _jwt_exchange(client, _jwt_code(person, moment), assertion)
    assert first.status_code == 200
    user, secret = person
    code = support.code_for(
        user,
        secret,
        moment,
        steps=1,
        client_id=support.JWT_CLIENT,
        redirect_uri=support.JWT_REDIRECT,
    )
    replay = _jwt_exchange(client, code, assertion)
    assert replay.status_code == 401 and _error(replay) == "invalid_client"


def test_an_assertion_signed_by_another_key_is_invalid_client(client, person, moment, keys):
    impostor = support.FakeClientKeys(kid=keys.kid)
    response = _jwt_exchange(client, _jwt_code(person, moment), impostor.assertion())
    assert _error(response) == "invalid_client"


def test_a_symmetric_assertion_is_invalid_client(client, person, moment, keys):
    forged = jwt.encode(
        {"iss": support.JWT_CLIENT, "sub": support.JWT_CLIENT, "aud": support.ORIGIN, "jti": "1"},
        "a-shared-secret-that-is-long-enough-for-hs256",
        algorithm="HS256",
    )
    assert _error(_jwt_exchange(client, _jwt_code(person, moment), forged)) == "invalid_client"


def test_the_assertion_type_must_be_jwt_bearer(client, person, moment, keys):
    response = _jwt_exchange(
        client, _jwt_code(person, moment), keys.assertion(), client_assertion_type="other"
    )
    assert _error(response) == "invalid_client"


def test_a_named_client_id_must_match_the_assertion(client, person, moment, keys):
    response = _jwt_exchange(
        client, _jwt_code(person, moment), keys.assertion(), client_id=support.PUBLIC_CLIENT
    )
    assert _error(response) == "invalid_client"


def test_a_private_key_jwt_client_cannot_skip_its_assertion(client, person, moment):
    response = _exchange(
        client,
        _jwt_code(person, moment),
        client_id=support.JWT_CLIENT,
        redirect_uri=support.JWT_REDIRECT,
    )
    assert response.status_code == 401 and _error(response) == "invalid_client"


def test_a_public_client_cannot_present_an_assertion_it_has_no_key_for(
    client, person, moment, keys
):
    user, secret = person
    assertion = keys.assertion(iss=support.PUBLIC_CLIENT, sub=support.PUBLIC_CLIENT)
    response = _exchange(
        client,
        support.code_for(user, secret, moment),
        client_id=None,
        client_assertion_type=ASSERTION_TYPE,
        client_assertion=assertion,
    )
    assert _error(response) == "invalid_client"


def test_an_unreachable_jwks_is_invalid_client(client, person, moment, monkeypatch):
    from app.mcp_access import oauth

    keys = support.FakeClientKeys()
    oauth._jwks_client.cache_clear()

    def unreachable(self):
        raise jwt.PyJWKClientConnectionError("timed out")

    monkeypatch.setattr(jwt.PyJWKClient, "fetch_data", unreachable)
    response = _jwt_exchange(client, _jwt_code(person, moment), keys.assertion())
    assert _error(response) == "invalid_client"


def test_the_jwks_client_is_bounded_in_time():
    from app.mcp_access import oauth

    oauth._jwks_client.cache_clear()
    jwks = oauth._jwks_client(support.JWKS_URI)
    assert jwks.timeout <= 5
    assert oauth._jwks_client(support.JWKS_URI) is jwks


# -- revocation ------------------------------------------------------------------------


def test_revoking_a_refresh_token_ends_its_family(client, person, moment):
    user, secret = person
    pair = _exchange(client, support.code_for(user, secret, moment)).json()
    response = form(
        client, REVOKE, {"token": pair["refresh_token"], "client_id": support.PUBLIC_CLIENT}
    )
    assert response.status_code == 200 and response["Cache-Control"] == "no-store"
    assert verify_access_token(pair["access_token"]) is None
    assert _error(_refresh(client, pair["refresh_token"])) == "invalid_grant"


def test_revoking_an_access_token_ends_only_that_token(client, person, moment):
    user, secret = person
    pair = _exchange(client, support.code_for(user, secret, moment)).json()
    form(client, REVOKE, {"token": pair["access_token"], "client_id": support.PUBLIC_CLIENT})
    assert verify_access_token(pair["access_token"]) is None
    assert _refresh(client, pair["refresh_token"]).status_code == 200


def test_revocation_answers_200_for_an_unknown_token(client):
    response = form(
        client, REVOKE, {"token": credentials.new_opaque(), "client_id": support.PUBLIC_CLIENT}
    )
    assert response.status_code == 200 and response.content == b""


def test_another_client_cannot_revoke_a_token(client, person, moment, keys):
    user, secret = person
    pair = _exchange(client, support.code_for(user, secret, moment)).json()
    response = form(
        client,
        REVOKE,
        {
            "token": pair["access_token"],
            "client_assertion_type": ASSERTION_TYPE,
            "client_assertion": keys.assertion(),
        },
    )
    assert response.status_code == 200
    assert verify_access_token(pair["access_token"]) is not None


def test_revocation_needs_a_known_client(client):
    response = form(client, REVOKE, {"token": "x", "client_id": "https://evil.example.test"})
    assert response.status_code == 401


# -- nothing secret leaks ----------------------------------------------------------------


def test_no_code_token_or_secret_reaches_the_audit_trail_or_the_logs(
    client, person, moment, caplog
):
    caplog.set_level(logging.DEBUG)
    user, secret = person
    code = support.code_for(user, secret, moment)
    pair = _exchange(client, code).json()
    second = _refresh(client, pair["refresh_token"]).json()
    _refresh(client, pair["refresh_token"])  # reuse: an error path
    _exchange(client, code)  # reuse of the code: an error path

    secrets_seen = [
        code,
        pair["access_token"],
        pair["refresh_token"],
        second["access_token"],
        second["refresh_token"],
        secret,
        support.VERIFIER,
    ]
    trail = json.dumps(
        list(McpAccessEvent.objects.values("detail", "client_id", "provenance")), default=str
    )
    for value in secrets_seen:
        assert value not in trail
        assert value not in caplog.text
        assert credentials.digest(value) not in trail


def test_an_error_response_never_echoes_what_it_was_sent(client, person, moment):
    user, secret = person
    code = support.code_for(user, secret, moment)
    response = _exchange(client, code, code_verifier="y" * 43)
    assert code.encode() not in response.content
    assert b"y" * 43 not in response.content


def test_every_endpoint_is_absent_while_mcp_access_is_off(client, settings):
    settings.MCP_ENABLED = False
    assert form(client, TOKEN, {"grant_type": "x"}).status_code == 404
    assert form(client, REVOKE, {"token": "x"}).status_code == 404
