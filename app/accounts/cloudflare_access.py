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

import hmac
import json
import logging
import re
import time
import urllib.request
from typing import Any
from urllib.parse import urlsplit

from django.conf import settings
from django.core.cache import cache
from django.utils.crypto import salted_hmac

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


# -- the session this mode establishes ------------------------------------------
#
# The middleware used to let any authenticated session through whose account's
# address matched the assertion. That is a statement about the *account*, not
# about the *session*: a persona chosen behind the shared gate, a development
# sign-in or a personal-password session naming the same person would have
# carried on — with its own keys, its own expiry and whatever administrator
# state it held — once the deployment switched to Access (SEC-012). A session is
# reused only when it carries the marker below, written by this mode at the
# moment it signed the person in from a verified assertion, and only while
# every fact the marker records is still true.

#: Session key for the provenance marker written at an Access sign-in.
SESSION_MARKER = "cf_access:session"

#: What the marker says established the session. A constant, so a marker some
#: future mode writes under the same key can never be read as this one's.
PROVENANCE = "cloudflare_access"


def mark_session(session: Any, user: Any, *, email: str, claims: dict[str, Any]) -> None:
    """Record, in a session this mode just signed in, what established it."""
    session[SESSION_MARKER] = {
        "via": PROVENANCE,
        "user": str(user.pk),
        "email": email,
        "sub": str(claims.get("sub") or ""),
        "expires_at": int(claims.get("exp") or 0),
    }


def session_holds(session: Any, user: Any, *, email: str, claims: dict[str, Any]) -> bool:
    """Whether this authenticated session may serve a request carrying ``claims``.

    Every condition is about the session *and* the assertion now in front of
    it, which has already been verified for this request:

    * the session was established by this mode (`SESSION_MARKER`, `PROVENANCE`)
      for this account — a session from the shared gate, `none` or
      `local_password` has no marker, whatever address its account carries;
    * for the identity this assertion names: the same address, and the same
      Cloudflare subject (`sub`), or none on either side;
    * the account is still one Access may sign in: active (Django has already
      answered an inactive account as anonymous, asked again here) and not
      synthetic;
    * inside the lifetime of the assertion that established it — a session
      never outlives its sign-in, whatever its cookie says.

    ``False`` means the caller ends the session and decides the request from
    the assertion alone, as for a first visit.
    """
    marker = session.get(SESSION_MARKER)
    if not isinstance(marker, dict) or marker.get("via") != PROVENANCE:
        return False
    if marker.get("user") != str(getattr(user, "pk", "")):
        return False
    if marker.get("email") != email or (getattr(user, "upn", "") or "").lower() != email:
        return False
    if not hmac.compare_digest(str(marker.get("sub") or ""), str(claims.get("sub") or "")):
        return False
    if not getattr(user, "is_active", False) or getattr(user, "is_synthetic", True):
        return False
    expires_at = marker.get("expires_at")
    return isinstance(expires_at, int) and expires_at > int(time.time())


# -- signing out ---------------------------------------------------------------
#
# Under Access this application does not own the sign-in: Cloudflare does, and
# keeps forwarding the same signed assertion until its own session ends. Ending
# only the local session therefore ended nothing — the very next request carried
# the assertion again and the middleware signed the person straight back in
# (csf_92343c92). Signing out has two halves now (docs/adr/0183):
#
# 1. **Upstream.** The sign-out answers with a redirect to Cloudflare's own
#    logout endpoint, `/cdn-cgi/access/logout`, which ends the Access session.
# 2. **Here, until upstream has happened.** The fresh, signed-out session
#    remembers a keyed fingerprint of the assertion it was signed out with, and
#    the middleware refuses to sign anybody in from that same assertion again.
#    A *different* assertion — the person has signed in at Access afresh — signs
#    in as usual.

#: Cloudflare's logout endpoint, on the team domain or on the application's own.
LOGOUT_PATH = "/cdn-cgi/access/logout"

#: Every Cloudflare Zero Trust team domain is `<team>.cloudflareaccess.com`.
TEAM_HOST_SUFFIX = ".cloudflareaccess.com"

#: Session key for the fingerprint of the assertion this browser signed out with.
SIGNED_OUT_ASSERTION = "cf_access:signed_out_assertion"

_SIGNED_OUT_SALT = "app.accounts.cloudflare_access.signed_out_assertion"

#: A DNS host name: dot-separated labels of letters, digits and inner hyphens.
_LABEL = r"[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?"
_HOST_LABELS = re.compile(rf"^{_LABEL}(?:\.{_LABEL})+$")


def team_logout_url() -> str:
    """The team domain's logout URL, or "" when the team domain is not one.

    Built from `CF_ACCESS_TEAM_DOMAIN` — configuration, never the request — and
    only when that is a plain `https://<team>.cloudflareaccess.com`: no path, no
    query, no port, no credentials. Anything else gets no absolute URL at all,
    so this can never become a redirect to somewhere a setting was mistyped to.
    """
    raw = (getattr(settings, "CF_ACCESS_TEAM_DOMAIN", "") or "").strip().rstrip("/")
    if not raw:
        return ""
    if "://" not in raw:
        raw = f"https://{raw}"
    try:
        parts = urlsplit(raw)
        port = parts.port
    except ValueError:
        return ""
    host = (parts.hostname or "").lower()
    if (
        parts.scheme != "https"
        or port is not None
        or parts.username
        or parts.password
        or parts.path
        or parts.query
        or parts.fragment
        or not host.endswith(TEAM_HOST_SUFFIX)
        or host == TEAM_HOST_SUFFIX.lstrip(".")
        or not _HOST_LABELS.match(host)
    ):
        return ""
    return f"https://{host}{LOGOUT_PATH}"


def logout_url() -> str:
    """Where the sign-out sends the browser: the team logout, else this host's own.

    The team domain's endpoint ends the person's Access session for every
    application. When the team domain is not a recognisable one, the
    application's own `/cdn-cgi/access/logout` — a path on this host, which
    Cloudflare's edge answers before the request reaches Juristid — ends the
    Access session for this application.
    """
    return team_logout_url() or LOGOUT_PATH


def _fingerprint(token: str) -> str:
    """A keyed digest of an assertion: enough to recognise it, useless as one."""
    return salted_hmac(_SIGNED_OUT_SALT, token, algorithm="sha256").hexdigest()[:32]


def remember_signed_out(session: Any, token: str) -> None:
    """Record, in a freshly signed-out session, the assertion it was signed out with."""
    if token:
        session[SIGNED_OUT_ASSERTION] = _fingerprint(token)


def was_signed_out_with(session: Any, token: str) -> bool:
    """Whether this browser signed out while holding exactly this assertion."""
    stored = session.get(SIGNED_OUT_ASSERTION)
    if not token or not isinstance(stored, str) or not stored:
        return False
    return hmac.compare_digest(stored, _fingerprint(token))
