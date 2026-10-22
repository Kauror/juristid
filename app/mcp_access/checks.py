"""System checks for the MCP authorization server's settings (docs/adr/0185 §4).

Silent while `MCP_ENABLED` is off. Once it is on, `web` refuses to start with
an issuer, a resource or a client that is not HTTPS: the issuer is what every
token is bound to, and an `http://` value would be accepted by nobody careful
and by everybody careless.
"""

from __future__ import annotations

from typing import Any
from urllib.parse import urlsplit

from django.conf import settings
from django.core.checks import Error, register

from app.mcp_access import config


def _is_https_origin(value: str, *, path_allowed: bool) -> bool:
    parts = urlsplit(value or "")
    if parts.scheme != "https" or not parts.hostname or parts.username or parts.password:
        return False
    if parts.query or parts.fragment:
        return False
    return path_allowed or parts.path in ("", "/")


@register()
def mcp_access_settings(app_configs: Any = None, **kwargs: Any) -> list[Error]:
    if not getattr(settings, "MCP_ENABLED", False):
        return []
    errors: list[Error] = []
    if not _is_https_origin(config.issuer(), path_allowed=False):
        errors.append(
            Error(
                "MCP_ENABLED needs JURISTID_PUBLIC_ORIGIN to be an https origin with no path.",
                id="mcp_access.E001",
            )
        )
    resources = list(getattr(settings, "MCP_RESOURCE_URLS", []) or [])
    if not resources or not all(_is_https_origin(url, path_allowed=True) for url in resources):
        errors.append(
            Error(
                "MCP_ENABLED needs MCP_RESOURCE_URLS: one or more https URLs.",
                id="mcp_access.E002",
            )
        )
    clients = getattr(settings, "MCP_OAUTH_CLIENTS", {}) or {}
    for client_id in clients:
        client = config.client(client_id)
        if client is None or not client.redirect_uris or not client.auth_methods:
            errors.append(Error(f"MCP client {client_id!r} is incomplete.", id="mcp_access.E003"))
            continue
        if not all(_is_https_origin(uri, path_allowed=True) for uri in client.redirect_uris):
            errors.append(
                Error(
                    f"MCP client {client_id!r}: redirect URIs must be https.", id="mcp_access.E003"
                )
            )
        if config.AUTH_METHOD_PRIVATE_KEY_JWT in client.auth_methods and not _is_https_origin(
            client.jwks_uri, path_allowed=True
        ):
            errors.append(
                Error(
                    f"MCP client {client_id!r}: private_key_jwt needs an https jwks_uri.",
                    id="mcp_access.E003",
                )
            )
    return errors
