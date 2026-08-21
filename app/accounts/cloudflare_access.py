"""Cloudflare Access as the production authenticator.

Cloudflare Access sits in front of the real-data deployment. It authenticates a
person against the Chamber's identity provider and then adds a signed assertion
to the request it forwards::

    Cf-Access-Jwt-Assertion: <RS256 JWT>

**The header is not the authentication. The signature is.** A request header is
attacker-controlled by definition: anybody who can reach the application
directly can set `Cf-Access-Jwt-Assertion` to whatever they like, and a system
that trusts the value without checking who signed it has an authentication
bypass rather than an authenticator (Stage-2D brief 57).

So every request is verified against the team's published JWKS: RS256 signature,
`aud` equal to this application's Access audience tag, `iss` equal to the team
domain, and the standard time claims. The keys are fetched over HTTPS from
Cloudflare and cached; a fetch failure denies rather than admits.

Two things are deliberately *not* here. There is no fallback to
`Cf-Access-Authenticated-User-Email`, because that header carries no signature
and exists only for people who have not read this note. And there is no
provisioning of identities that Access did not assert — a person exists in this
system because Cloudflare said who they are, not because a configuration file
invented them (Stage-2D brief 59).
"""

from __future__ import annotations

import json
import logging
import time
import urllib.request
from typing import Any

from django.conf import settings
from django.core.cache import cache

logger = logging.getLogger(__name__)

HEADER = "HTTP_CF_ACCESS_JWT_ASSERTION"

#: Long enough that a key fetch is rare, short enough that a rotated key is
#: picked up without a deployment. Cloudflare publishes the next key before it
#: signs with it, so both are in the set during a rotation.
JWKS_CACHE_SECONDS = 600
JWKS_CACHE_KEY = "cloudflare-access-jwks"
JWKS_TIMEOUT_SECONDS = 5

#: A few seconds, for clock skew between Cloudflare's edge and this host.
LEEWAY_SECONDS = 30


class AccessDenied(Exception):
    """The request carries no assertion this deployment is willing to trust."""


def is_enabled() -> bool:
    """Access is on exactly when the deployment's mode says so."""
    from app.accounts.enums import AuthMode
    from app.accounts.shared_gate import current_mode

    return current_mode() == AuthMode.CLOUDFLARE_ACCESS


def _configuration() -> tuple[str, str]:
    team_domain = (getattr(settings, "CF_ACCESS_TEAM_DOMAIN", "") or "").strip().rstrip("/")
    audience = (getattr(settings, "CF_ACCESS_AUDIENCE", "") or "").strip()
    if not team_domain or not audience:
        # Refused rather than defaulted. An Access check with no audience
        # configured would accept a token minted for a *different* application
        # in the same Cloudflare account.
        raise AccessDenied("Cloudflare Access is enabled but not configured.")
    if not team_domain.startswith("https://"):
        team_domain = f"https://{team_domain}"
    return team_domain, audience


def certs_url() -> str:
    team_domain, _ = _configuration()
    return f"{team_domain}/cdn-cgi/access/certs"


def _fetch_jwks() -> dict[str, Any]:
    url = certs_url()
    # The URL is built from configuration this deployment owns, and is always
    # https on Cloudflare's own domain; it never comes from a request.
    request = urllib.request.Request(url, headers={"Accept": "application/json"})  # noqa: S310
    with urllib.request.urlopen(request, timeout=JWKS_TIMEOUT_SECONDS) as response:  # noqa: S310
        return json.loads(response.read().decode("utf-8"))


def jwks(*, refresh: bool = False) -> dict[str, Any]:
    """The team's signing keys, cached.

    A failed fetch is not cached: caching it would turn one bad minute at
    Cloudflare into ten minutes of everybody being locked out.
    """
    if not refresh:
        cached = cache.get(JWKS_CACHE_KEY)
        if cached:
            return cached
    payload = _fetch_jwks()
    cache.set(JWKS_CACHE_KEY, payload, timeout=JWKS_CACHE_SECONDS)
    return payload


def _key_for(token: str, *, refresh: bool = False) -> Any:
    import jwt
    from jwt import PyJWKSet

    header = jwt.get_unverified_header(token)
    kid = header.get("kid")
    if not kid:
        raise AccessDenied("The assertion names no signing key.")

    keyset = PyJWKSet.from_dict(jwks(refresh=refresh))
    for key in keyset.keys:
        if key.key_id == kid:
            return key.key
    if refresh:
        raise AccessDenied("The assertion was signed by a key Cloudflare does not publish.")
    # One retry with fresh keys, for the minutes around a rotation.
    return _key_for(token, refresh=True)


def verify(token: str) -> dict[str, Any]:
    """Return the assertion's claims, or raise. Never returns unverified data."""
    import jwt

    if not token:
        raise AccessDenied("No Cloudflare Access assertion on the request.")

    team_domain, audience = _configuration()
    try:
        return jwt.decode(
            token,
            key=_key_for(token),
            algorithms=["RS256"],
            audience=audience,
            issuer=team_domain,
            leeway=LEEWAY_SECONDS,
            options={"require": ["exp", "iat", "aud", "iss"]},
        )
    except AccessDenied:
        raise
    except Exception as error:
        # The reason is logged, not returned. "wrong audience" and "expired"
        # are useful to an operator reading the log and useful to somebody
        # probing the endpoint, and only one of them should have them.
        logger.warning("Cloudflare Access assertion rejected: %s", type(error).__name__)
        raise AccessDenied("The Cloudflare Access assertion is not valid here.") from error


def email_from(claims: dict[str, Any]) -> str:
    """The authenticated person, from the verified claims only."""
    email = (claims.get("email") or "").strip().lower()
    if not email:
        raise AccessDenied("The verified assertion carries no email address.")
    return email


def identity_from_request(meta: dict[str, Any]) -> tuple[str, dict[str, Any]]:
    """(email, claims) for a request, or raise `AccessDenied`."""
    claims = verify(meta.get(HEADER, ""))
    return email_from(claims), claims


def seconds_until_expiry(claims: dict[str, Any]) -> int:
    expiry = int(claims.get("exp") or 0)
    return max(0, expiry - int(time.time()))
