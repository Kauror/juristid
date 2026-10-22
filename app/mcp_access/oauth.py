"""Reading OAuth requests: the authorization request and client authentication.

Pure validation — nothing here writes, except the replay cache a client
assertion's ``jti`` is remembered in.

**Scopes.** The only scope this server grants is `MCP_SCOPE` (``juristid.read``),
and it is the only one the metadata advertises. A client may also ask for
``offline_access``, ``openid``, ``profile`` or ``email`` — generic clients add
them by habit — and those are ignored rather than refused: none of them would
be honoured (there is no ID token and no user-info endpoint), and the granted
scope, ``juristid.read``, is what the token response states (RFC 6749 §3.3).
Any other scope is refused with ``invalid_scope``.
"""

from __future__ import annotations

import functools
import hashlib
import time
from dataclasses import dataclass
from typing import Any

import jwt
from django.core.cache import cache
from django.http import QueryDict

from app.mcp_access import config, credentials
from app.mcp_access.services import AuthorizationRequest, OAuthError

#: Scopes a client may ask for that are dropped rather than refused.
IGNORED_SCOPES = frozenset({"offline_access", "openid", "profile", "email"})

#: RFC 7523 §2.2.
CLIENT_ASSERTION_TYPE = "urn:ietf:params:oauth:client-assertion-type:jwt-bearer"

#: How far ahead a client assertion's ``exp`` may be, and the clock skew allowed.
MAX_ASSERTION_LIFETIME = 5 * 60
LEEWAY = 30
#: The JWKS fetch: bounded in time, and its keys cached for this long.
JWKS_TIMEOUT = 5
JWKS_LIFESPAN = 15 * 60

MAX_STATE_LENGTH = 2048


@dataclass(frozen=True)
class EarlyError:
    """A request so wrong it cannot be answered at its redirect URI: an error *page*."""

    message: str


@dataclass(frozen=True)
class RedirectError:
    """A refusal sent back to the (validated) redirect URI with ``error`` and ``state``."""

    redirect_uri: str
    error: str
    description: str
    state: str | None


def granted_scope(raw: str | None) -> str | None:
    """The scope this request is granted, or None when it asks for something unknown."""
    requested = {part for part in (raw or "").split() if part}
    unknown = requested - IGNORED_SCOPES - {config.scope()}
    if unknown:
        return None
    return config.scope()


def parse_authorization(params: QueryDict) -> AuthorizationRequest | EarlyError | RedirectError:
    """Validate an authorization request, in the order RFC 6749 §4.1.2.1 requires.

    The client and the redirect URI first: until both are known good, an
    error must not be sent anywhere, because the redirect URI may be the
    attacker's. Every later error goes back to that URI.
    """
    client_id = params.get("client_id", "")
    client = config.client(client_id)
    if client is None:
        return EarlyError("Tundmatu klient.")
    redirect_uri = params.get("redirect_uri", "")
    if not redirect_uri or redirect_uri not in client.redirect_uris:
        return EarlyError("Tagasisuunamise aadress ei ole selle kliendi jaoks registreeritud.")
    state = params.get("state")
    if state is not None and len(state) > MAX_STATE_LENGTH:
        return EarlyError("Päring on vigane.")

    def refuse(error: str, description: str) -> RedirectError:
        return RedirectError(redirect_uri, error, description, state)

    if params.get("response_type", "") != "code":
        return refuse("unsupported_response_type", "Only response_type=code is supported.")
    for name in ("client_id", "redirect_uri", "response_type", "code_challenge", "resource"):
        if len(params.getlist(name)) > 1:
            return refuse("invalid_request", f"{name} given more than once.")
    method = params.get("code_challenge_method", "")
    challenge = params.get("code_challenge", "")
    if not challenge or method != "S256":
        return refuse("invalid_request", "PKCE with code_challenge_method=S256 is required.")
    if not credentials.is_pkce_challenge(challenge):
        return refuse("invalid_request", "Malformed code_challenge.")
    resource = config.normalise_resource(params.get("resource", ""))
    if not resource:
        return refuse("invalid_target", "A resource indicator is required.")
    if not config.resource_is_allowed(resource):
        return refuse("invalid_target", "Unknown resource.")
    scope = granted_scope(params.get("scope"))
    if scope is None:
        return refuse("invalid_scope", "Unknown scope.")
    return AuthorizationRequest(
        client=client,
        redirect_uri=redirect_uri,
        code_challenge=challenge,
        scope=scope,
        resource=resource,
        state=state,
    )


# -- client authentication at the token and revocation endpoints -----------------


def invalid_client(description: str = "Client authentication failed.") -> OAuthError:
    return OAuthError("invalid_client", description, status=401)


@functools.cache
def _jwks_client(jwks_uri: str) -> jwt.PyJWKClient:
    """One cached key set per registered JWKS URI (never a URI a request named)."""
    return jwt.PyJWKClient(
        jwks_uri, cache_keys=True, cache_jwk_set=True, lifespan=JWKS_LIFESPAN, timeout=JWKS_TIMEOUT
    )


def signing_key(client: config.OAuthClient, assertion: str) -> Any:
    """The client's published key for this assertion. Patched in tests; raises on failure."""
    return _jwks_client(client.jwks_uri).get_signing_key_from_jwt(assertion).key


def _remember_jti(client_id: str, jti: str, expires: int) -> bool:
    """True the first time this (client, jti) is seen; the cache is shared by every worker."""
    key = "mcp_access:jti:" + hashlib.sha256(f"{client_id}\x1f{jti}".encode()).hexdigest()
    timeout = max(1, expires - int(time.time()) + LEEWAY)
    return bool(cache.add(key, 1, timeout=timeout))


def _verify_assertion(client: config.OAuthClient, assertion: str) -> bool:
    if not client.jwks_uri:
        return False
    try:
        header = jwt.get_unverified_header(assertion)
        if header.get("alg") != "RS256":
            return False
        key = signing_key(client, assertion)
        claims = jwt.decode(
            assertion,
            key,
            algorithms=["RS256"],
            audience=[config.token_endpoint(), config.issuer()],
            issuer=client.client_id,
            leeway=LEEWAY,
            options={"require": ["exp", "iss", "sub", "aud", "jti"]},
        )
    except Exception:
        # Every failure is the same answer — a bad signature, an unknown key,
        # a JWKS that did not answer in time, a malformed token.
        return False
    if claims.get("sub") != client.client_id:
        return False
    exp = claims.get("exp")
    jti = claims.get("jti")
    if not isinstance(exp, int) or not isinstance(jti, str) or not jti or len(jti) > 256:
        return False
    if exp > int(time.time()) + MAX_ASSERTION_LIFETIME + LEEWAY:
        return False
    return _remember_jti(client.client_id, jti, exp)


def authenticate_client(
    params: QueryDict, *, authorization_header: str = ""
) -> config.OAuthClient | OAuthError:
    """The registered client this request authenticated as, by its configured method.

    ``private_key_jwt`` (RFC 7523): an RS256 assertion with ``iss = sub =
    client_id``, ``aud`` this token endpoint or the issuer, at most five
    minutes to live and a ``jti`` never seen before, verified against the
    client's registered JWKS. ``none``: a public client naming its
    ``client_id``, whose security is PKCE. A client allowed both may use
    either, but an assertion that is present must verify. HTTP Basic is not a
    method this server has, and is refused rather than ignored.
    """
    if authorization_header:
        return invalid_client("Unsupported client authentication method.")
    assertion = params.get("client_assertion", "")
    named = params.get("client_id", "")
    if assertion:
        if params.get("client_assertion_type", "") != CLIENT_ASSERTION_TYPE:
            return invalid_client()
        try:
            claimed = jwt.decode(assertion, options={"verify_signature": False}).get("iss", "")
        except Exception:
            return invalid_client()
        if not isinstance(claimed, str) or (named and named != claimed):
            return invalid_client()
        client = config.client(claimed)
        if client is None or config.AUTH_METHOD_PRIVATE_KEY_JWT not in client.auth_methods:
            return invalid_client()
        if not _verify_assertion(client, assertion):
            return invalid_client()
        return client
    if params.get("client_assertion_type"):
        return invalid_client()
    client = config.client(named)
    if client is None or config.AUTH_METHOD_NONE not in client.auth_methods:
        return invalid_client()
    return client
