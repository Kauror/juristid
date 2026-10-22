"""The MCP authorization server's configuration, read from settings in one place.

Nothing here reads a request. The issuer and every endpoint URL are built from
`JURISTID_PUBLIC_ORIGIN`, never from a Host header, so a request that names
another host cannot change what the metadata claims or where a code is bound.
"""

from __future__ import annotations

from dataclasses import dataclass

from django.conf import settings
from django.urls import reverse

#: The client authentication methods this server implements (RFC 8414
#: ``token_endpoint_auth_methods_supported``). No client secret exists.
AUTH_METHOD_NONE = "none"
AUTH_METHOD_PRIVATE_KEY_JWT = "private_key_jwt"
SUPPORTED_AUTH_METHODS = frozenset({AUTH_METHOD_NONE, AUTH_METHOD_PRIVATE_KEY_JWT})


@dataclass(frozen=True)
class OAuthClient:
    """One pre-registered client from `MCP_OAUTH_CLIENTS`."""

    client_id: str
    name: str
    redirect_uris: tuple[str, ...]
    auth_methods: frozenset[str]
    jwks_uri: str


def is_enabled() -> bool:
    """MCP access is switched on *and* has an origin to be the issuer of.

    An enabled server without a public origin would have to take its issuer
    from the request, which is exactly what it must never do; it answers 404
    instead, like a disabled one.
    """
    return bool(getattr(settings, "MCP_ENABLED", False)) and bool(issuer())


def issuer() -> str:
    return (getattr(settings, "JURISTID_PUBLIC_ORIGIN", "") or "").rstrip("/")


def absolute(route: str) -> str:
    return f"{issuer()}{reverse(route)}"


def authorization_endpoint() -> str:
    return absolute("mcp_access:authorize")


def token_endpoint() -> str:
    return absolute("mcp_access:token")


def revocation_endpoint() -> str:
    return absolute("mcp_access:revoke")


def scope() -> str:
    return str(getattr(settings, "MCP_SCOPE", "juristid.read"))


def normalise_resource(value: str) -> str:
    """A resource indicator compared without a trailing slash (RFC 8707)."""
    return (value or "").strip().rstrip("/")


def allowed_resources() -> frozenset[str]:
    return frozenset(
        normalise_resource(url)
        for url in getattr(settings, "MCP_RESOURCE_URLS", []) or []
        if normalise_resource(url)
    )


def resource_is_allowed(value: str) -> bool:
    normalised = normalise_resource(value)
    return bool(normalised) and normalised in allowed_resources()


def advertised_auth_methods() -> list[str]:
    """The client authentication methods some registered client may use (RFC 8414).

    Only what is configured: with ChatGPT registered for `private_key_jwt`
    alone, `none` is not advertised (MCPSEC-08). Stronger first.
    """
    configured: set[str] = set()
    for client_id in getattr(settings, "MCP_OAUTH_CLIENTS", {}) or {}:
        found = client(client_id)
        if found is not None:
            configured |= set(found.auth_methods)
    return [
        method for method in (AUTH_METHOD_PRIVATE_KEY_JWT, AUTH_METHOD_NONE) if method in configured
    ]


def client(client_id: str) -> OAuthClient | None:
    """The registered client with exactly this id, or None.

    A static allowlist: a client id that is a URL is never fetched. Juristid
    knows its clients from settings, so an unknown metadata document cannot
    register itself by being named.
    """
    clients = getattr(settings, "MCP_OAUTH_CLIENTS", {}) or {}
    raw = clients.get(client_id) if isinstance(client_id, str) else None
    if not isinstance(raw, dict):
        return None
    redirect_uris = _strings(raw.get("redirect_uris"))
    methods = _strings(raw.get("token_endpoint_auth_methods")) or (AUTH_METHOD_NONE,)
    return OAuthClient(
        client_id=client_id,
        name=str(raw.get("client_name") or client_id),
        redirect_uris=redirect_uris,
        auth_methods=frozenset(methods) & SUPPORTED_AUTH_METHODS,
        jwks_uri=str(raw.get("jwks_uri") or ""),
    )


def _strings(value: object) -> tuple[str, ...]:
    if not isinstance(value, list | tuple):
        return ()
    return tuple(str(item) for item in value if isinstance(item, str) and item)
