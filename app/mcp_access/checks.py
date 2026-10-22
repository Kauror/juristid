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


MIN_AUTHENTICATOR_KEY_LENGTH = 32


def _authenticator_key_problems() -> list[Error]:
    """mcp_access.E004: on real data, MCP authenticators have a key of their own (MCPSEC-07).

    Only `web` seals and opens them; the `mcp` process never does and must not
    hold the key (juristid.E046), so the check is skipped there.
    """
    if (getattr(settings, "JURISTID_PROCESS_ROLE", "") or "") == "mcp":
        return []
    if not getattr(settings, "REAL_DATA_ALLOWED", False):
        return []
    key = str(getattr(settings, "MCP_AUTHENTICATOR_KEY", "") or "")
    weak = (
        len(key) < MIN_AUTHENTICATOR_KEY_LENGTH
        or key.startswith("replace-me")
        or key == settings.SECRET_KEY
        or key == (getattr(settings, "LOCAL_AUTH_MFA_ENCRYPTION_KEY", "") or "")
    )
    if not weak:
        return []
    return [
        Error(
            "MCP_ENABLED on real data needs MCP_AUTHENTICATOR_KEY: at least "
            f"{MIN_AUTHENTICATOR_KEY_LENGTH} characters, its own value — not DJANGO_SECRET_KEY "
            "and not LOCAL_AUTH_MFA_ENCRYPTION_KEY.",
            hint=(
                "Generate it into juristid.env (docs/mcp/OPERATIONS.md, first-time enablement "
                "step 3). Without it the authenticators are sealed under DJANGO_SECRET_KEY and a "
                "routine rotation of that key would lock every colleague out."
            ),
            id="mcp_access.E004",
        )
    ]


def _derivation_key_problems() -> list[Error]:
    """mcp_access.E005: on real data, the refresh successor chain has a key of its own (NEW-01).

    `credentials.successor_pair` derives a rotated refresh token's successor
    from the presented token under this key. Under `SECRET_KEY` — the
    fallback off real data — that key and one refresh token would let
    somebody compute a family's every later token offline. Only `web`
    rotates refresh tokens; the `mcp` process must not hold it (juristid.E046).
    """
    if (getattr(settings, "JURISTID_PROCESS_ROLE", "") or "") == "mcp":
        return []
    if not getattr(settings, "REAL_DATA_ALLOWED", False):
        return []
    key = str(getattr(settings, "MCP_TOKEN_DERIVATION_KEY", "") or "")
    others = {
        settings.SECRET_KEY,
        str(getattr(settings, "LOCAL_AUTH_MFA_ENCRYPTION_KEY", "") or ""),
        str(getattr(settings, "MCP_AUTHENTICATOR_KEY", "") or ""),
    }
    weak = len(key) < MIN_AUTHENTICATOR_KEY_LENGTH or key.startswith("replace-me") or key in others
    if not weak:
        return []
    return [
        Error(
            "MCP_ENABLED on real data needs MCP_TOKEN_DERIVATION_KEY: at least "
            f"{MIN_AUTHENTICATOR_KEY_LENGTH} characters, its own value — not DJANGO_SECRET_KEY, "
            "not LOCAL_AUTH_MFA_ENCRYPTION_KEY and not MCP_AUTHENTICATOR_KEY.",
            hint=(
                "Generate it into juristid.env with scripts/deploy/juristid-mcp-web-keys.sh "
                "(docs/mcp/OPERATIONS.md, first-time enablement step 3). Under DJANGO_SECRET_KEY, "
                "that key and one refresh token would yield every later token of its family."
            ),
            id="mcp_access.E005",
        )
    ]


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
    errors += _authenticator_key_problems()
    errors += _derivation_key_problems()
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
