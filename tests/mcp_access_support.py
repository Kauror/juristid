"""Shared set-up for the MCP access tests (tests/test_mcp_access*.py, docs/adr/0185).

Synthetic accounts only. The settings are applied explicitly by each module's
own fixture, as `tests/gate.py` does for the shared gate.
"""

from __future__ import annotations

import time
import uuid
from datetime import timedelta
from types import SimpleNamespace
from typing import Any
from urllib.parse import parse_qs, urlsplit

import jwt
import pyotp
from cryptography.hazmat.primitives.asymmetric import rsa
from django.utils import timezone

from app.accounts import mfa
from app.mcp_access import credentials, oauth, services
from app.mcp_access.models import McpAuthenticator

ORIGIN = "https://juristid.example.test"
RESOURCE = "https://mcp.example.test/mcp"
TUNNEL_RESOURCE = "https://tunnel.example.test/mcp"
PUBLIC_CLIENT = "https://client.example.test/oauth/client.json"
PUBLIC_REDIRECT = "https://client.example.test/oauth/callback"
JWT_CLIENT = "https://chatgpt.example.test/oauth/client.json"
JWT_REDIRECT = "https://chatgpt.example.test/connector_platform_oauth_redirect"
JWKS_URI = "https://chatgpt.example.test/oauth/jwks.json"
VERIFIER = "dBjftJeZ4CVP-mB92K27uhbUJU1p1r_wW1gFWFOEjXk"
CHALLENGE = "E9Melhoa2OwvFrEMTJguCHaoeK1t8URWbuGJSstw-cM"
OPERATOR = "test-operator"


def enable(settings: Any) -> Any:
    settings.MCP_ENABLED = True
    settings.JURISTID_PUBLIC_ORIGIN = ORIGIN
    settings.MCP_RESOURCE_URLS = [RESOURCE, TUNNEL_RESOURCE + "/"]
    settings.MCP_OAUTH_CLIENTS = {
        PUBLIC_CLIENT: {
            "client_name": "Testklient",
            "redirect_uris": [PUBLIC_REDIRECT],
            "token_endpoint_auth_methods": ["none"],
        },
        JWT_CLIENT: {
            "client_name": "ChatGPT",
            "redirect_uris": [JWT_REDIRECT],
            "token_endpoint_auth_methods": ["private_key_jwt"],
            "jwks_uri": JWKS_URI,
        },
    }
    return settings


def freeze_totp(monkeypatch: Any) -> Any:
    """Pin the clock the TOTP check reads, so codes at known steps are deterministic."""
    fixed = timezone.now()
    monkeypatch.setattr(mfa, "timezone", SimpleNamespace(now=lambda: fixed))
    return fixed


def code_at(secret: str, moment: Any, steps: int = 0) -> str:
    return pyotp.TOTP(secret).at(moment + timedelta(seconds=30 * steps))


def enrolled(user: Any, moment: Any) -> tuple[Any, str]:
    """An ACTIVE grant for ``user`` and its authenticator secret, through the real services.

    The enrolment spends the step before ``moment``, so the current and the
    next step are both still unspent for the test.
    """
    issued = services.grant_access(user=user, provenance=OPERATOR)
    token = parse_qs(urlsplit(issued.url).query)["kood"][0]
    enrolment = credentials.new_enrolment(services.account_label(user))
    assert services.confirm_enrolment(
        proof=credentials.link_proof(token),
        user=user,
        sealed=enrolment.sealed,
        code=code_at(enrolment.secret, moment, -1),
        ip_address=None,
    )
    issued.grant.refresh_from_db()
    assert McpAuthenticator.objects.filter(grant=issued.grant).exists()
    return issued.grant, enrolment.secret


GATE = "/konto/varav/"
ENROL = "/mcp-ligipaas/liitu/"
AUTHORIZE = "/oauth/authorize"


def through_the_gate(client: Any) -> None:
    """Pass the shared gate and choose nobody: the MCP pages need no persona (ADR 0185 §3)."""
    from tests.gate import PASSWORD

    assert client.post(GATE, {"password": PASSWORD}).status_code == 302


def consent(user: Any, code: str, *, decision: str = "luba") -> dict[str, str]:
    """The consent form as a person fills it in: their own address and their own code."""
    address = user if isinstance(user, str) else user.upn
    return {"otsus": decision, "epost": address, "kood": code}


def page_body(response: Any) -> str:
    """The page's own content, without the chrome (whose persona switcher names colleagues)."""
    text = response.content.decode()
    start = text.index('<div class="page page--narrow">')
    return text[start : text.index("</main>", start)]


def comparable(response: Any, typed: str) -> str:
    """The page's own content with what this browser typed and its CSRF token taken out.

    What is left is everything the server chose to say, so two refusals that
    compare equal said exactly the same thing.
    """
    import re

    body = re.sub(r'name="csrfmiddlewaretoken" value="[^"]+"', "", page_body(response))
    return body.replace(typed, "<typed>") if typed else body


def authorize_params(**overrides: Any) -> dict[str, str]:
    params = {
        "response_type": "code",
        "client_id": PUBLIC_CLIENT,
        "redirect_uri": PUBLIC_REDIRECT,
        "code_challenge": CHALLENGE,
        "code_challenge_method": "S256",
        "resource": RESOURCE,
        "scope": "juristid.read",
        "state": "olek-123",
    }
    params.update(overrides)
    return {key: value for key, value in params.items() if value is not None}


def request_for(**overrides: Any) -> services.AuthorizationRequest:
    from django.http import QueryDict

    query = QueryDict(mutable=True)
    query.update(authorize_params(**overrides))
    parsed = oauth.parse_authorization(query)
    assert isinstance(parsed, services.AuthorizationRequest), parsed
    return parsed


def code_for(user: Any, secret: str, moment: Any, *, steps: int = 0, **overrides: Any) -> str:
    """A fresh authorization code through the real authorize service."""
    outcome = services.authorize(
        user=user,
        request=request_for(**overrides),
        totp_code=code_at(secret, moment, steps),
        ip_address=None,
    )
    assert outcome.status == "ok", outcome
    return outcome.code


def tokens_for(user: Any, secret: str, moment: Any, **overrides: Any) -> services.IssuedTokens:
    code = code_for(user, secret, moment, **overrides)
    issued = services.exchange_code(
        client_id=overrides.get("client_id", PUBLIC_CLIENT),
        code=code,
        redirect_uri=overrides.get("redirect_uri", PUBLIC_REDIRECT),
        code_verifier=VERIFIER,
        resource=overrides.get("resource", RESOURCE),
        ip_address=None,
    )
    assert isinstance(issued, services.IssuedTokens), issued
    return issued


# -- private_key_jwt --------------------------------------------------------------


class FakeClientKeys:
    """A locally generated RSA key and the JWKS a client would publish for it."""

    def __init__(self, kid: str = "testikood-1") -> None:
        self.kid = kid
        self.private_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
        public = jwt.algorithms.RSAAlgorithm.to_jwk(self.private_key.public_key(), as_dict=True)
        self.jwks = {"keys": [{**public, "kid": kid, "use": "sig", "alg": "RS256"}]}

    def install(self, monkeypatch: Any) -> None:
        """Answer the JWKS fetch from memory: no test contacts a network."""
        oauth._jwks_client.cache_clear()
        monkeypatch.setattr(jwt.PyJWKClient, "fetch_data", lambda client: self.jwks)

    def assertion(self, **claims: Any) -> str:
        now = int(time.time())
        body = {
            "iss": JWT_CLIENT,
            "sub": JWT_CLIENT,
            "aud": f"{ORIGIN}/oauth/token",
            "iat": now,
            "exp": now + 120,
            "jti": str(uuid.uuid4()),
        }
        body.update(claims)
        body = {key: value for key, value in body.items() if value is not None}
        return jwt.encode(body, self.private_key, algorithm="RS256", headers={"kid": self.kid})
