"""Bearer tokens at the MCP endpoint: who is asking, and where to send them if nobody is.

The MCP process is an OAuth 2.1 *resource server* (docs/adr/0185 §3–§5). It
issues nothing and stores nothing: `web` is the authorization server, and the
access tokens it issued are checked here by
`app.mcp_access.verification.verify_access_token` — a hash lookup, expiry,
audience against every MCP_RESOURCE_URLS entry, scope, grant, account and
security epoch — in a worker thread, never on the event loop.

Three pieces:

- `JuristidTokenVerifier`, the SDK's `TokenVerifier`: a raw bearer token in, an
  SDK `AccessToken` out, carrying the `VerifiedAccess` it was built from so each
  tool call can re-authorize the same grant (`verified_access`);
- the RFC 9728 protected-resource metadata, served at
  `/.well-known/oauth-protected-resource` and at that path plus the resource's
  own path (`/.well-known/oauth-protected-resource/mcp`), naming the public
  origin as the one authorization server;
- `RequireBearer`, the 401 a request without a valid token gets, whose
  `WWW-Authenticate` names that metadata and the scope a client must ask for.

With more than one resource URL (the Secure MCP Tunnel URL and a public one),
the metadata and the challenge name the one whose host the request arrived
under, so a client always sees the resource it actually dialled; anything else
gets the first. The Host header has already been held to MCP_ALLOWED_HOSTS.
"""

from __future__ import annotations

import importlib
import json
import logging
from typing import Any
from urllib.parse import urlsplit

from django.conf import settings
from mcp.server.auth.middleware.auth_context import get_access_token
from mcp.server.auth.middleware.bearer_auth import AuthenticatedUser
from mcp.server.auth.provider import AccessToken
from mcp.server.auth.settings import AuthSettings
from mcp.shared.auth import ProtectedResourceMetadata
from pydantic import ConfigDict, Field
from starlette.middleware.cors import CORSMiddleware
from starlette.requests import HTTPConnection, Request
from starlette.responses import JSONResponse, Response
from starlette.routing import Route, request_response
from starlette.types import ASGIApp, Receive, Scope, Send

from app.mcp_server.runtime import ReaderRuntime

logger = logging.getLogger(__name__)

#: Where the access module lives (workstream C). Imported when first needed, so
#: this module loads, and its tests run, without it.
VERIFICATION_MODULE = "app.mcp_access.verification"

#: RFC 9728's well-known path.
PROTECTED_RESOURCE_PATH = "/.well-known/oauth-protected-resource"

#: Longer than any token Juristid issues (43 characters of base64url), short
#: enough that nobody gets to make the database hash a megabyte.
MAX_BEARER_TOKEN_LENGTH = 512


def verification_module() -> Any:
    return importlib.import_module(VERIFICATION_MODULE)


class JuristidAccessToken(AccessToken):
    """The SDK's AccessToken, plus the verified grant it stands for.

    `token` holds the token's *id*, never the bearer secret: the SDK keeps this
    object for the length of the request and nothing here needs the secret
    again. `verified` is the `VerifiedAccess` the verifier returned; it is
    excluded from every dump and repr.
    """

    model_config = ConfigDict(arbitrary_types_allowed=True)

    verified: Any = Field(default=None, exclude=True, repr=False)


class JuristidTokenVerifier:
    """`mcp.server.auth.provider.TokenVerifier` over Juristid's own access tokens."""

    def __init__(self, runtime: ReaderRuntime) -> None:
        self.runtime = runtime

    async def verify_token(self, token: str) -> AccessToken | None:
        if not token or len(token) > MAX_BEARER_TOKEN_LENGTH:
            return None
        verification = verification_module()
        access = await self.runtime.run(lambda: verification.verify_access_token(token))
        if access is None:
            return None
        return JuristidAccessToken(
            token=str(access.token_id),
            client_id=str(access.client_id),
            scopes=sorted(access.scopes),
            expires_at=int(access.expires_at.timestamp()),
            resource=str(access.resource),
            subject=str(access.user_id),
            verified=access,
        )


def auth_settings() -> AuthSettings:
    """The SDK's view of this resource server.

    `validate_token_resource` is off because the verifier already holds the
    token's audience to the *whole* MCP_RESOURCE_URLS list; the SDK's own check
    would accept only the first.
    """
    return AuthSettings.model_validate(
        {
            "issuer_url": settings.JURISTID_PUBLIC_ORIGIN,
            "resource_server_url": resource_urls()[0],
            "required_scopes": [settings.MCP_SCOPE],
            "validate_token_resource": False,
        }
    )


def resource_urls() -> list[str]:
    urls = [str(url).rstrip("/") for url in settings.MCP_RESOURCE_URLS if str(url).strip()]
    if not urls:
        raise ValueError("MCP_RESOURCE_URLS is empty (juristid.E043).")
    return urls


def resource_for(connection: HTTPConnection) -> str:
    """The configured resource URL whose host this request arrived under, else the first."""
    host = (connection.headers.get("host") or "").strip().lower()
    urls = resource_urls()
    for url in urls:
        if urlsplit(url).netloc.lower() == host:
            return url
    return urls[0]


def metadata_url_for(resource: str) -> str:
    """RFC 9728 §3.1: the well-known path goes between the host and the resource's path."""
    parts = urlsplit(resource)
    path = parts.path if parts.path not in ("", "/") else ""
    return f"{parts.scheme}://{parts.netloc}{PROTECTED_RESOURCE_PATH}{path}"


def protected_resource_metadata(resource: str) -> dict[str, Any]:
    document = ProtectedResourceMetadata.model_validate(
        {
            "resource": resource,
            "authorization_servers": [settings.JURISTID_PUBLIC_ORIGIN],
            "scopes_supported": [settings.MCP_SCOPE],
            "bearer_methods_supported": ["header"],
            "resource_name": "Juristid",
        }
    )
    dumped: dict[str, Any] = json.loads(document.model_dump_json(exclude_none=True))
    return dumped


async def _protected_resource_metadata_endpoint(request: Request) -> Response:
    return JSONResponse(
        protected_resource_metadata(resource_for(request)),
        headers={"Cache-Control": "public, max-age=3600"},
    )


def protected_resource_routes() -> list[Route]:
    """The metadata at the root form and at every configured resource's path form."""
    paths = [PROTECTED_RESOURCE_PATH]
    for url in resource_urls():
        path = urlsplit(metadata_url_for(url)).path
        if path not in paths:
            paths.append(path)
    endpoint = CORSMiddleware(
        app=request_response(_protected_resource_metadata_endpoint),
        allow_origins="*",
        allow_methods=["GET", "OPTIONS"],
        allow_headers=["mcp-protocol-version"],
    )
    return [Route(path, endpoint=endpoint, methods=["GET", "OPTIONS"]) for path in paths]


def challenge(resource: str, *, error: str = "invalid_token", description: str = "") -> str:
    """The `WWW-Authenticate` value that sends a client to the metadata and the scope."""
    parts = [
        f'resource_metadata="{metadata_url_for(resource)}"',
        f'scope="{settings.MCP_SCOPE}"',
        f'error="{error}"',
    ]
    if description:
        parts.append(f'error_description="{description}"')
    return "Bearer " + ", ".join(parts)


class RequireBearer:
    """Refuse the MCP endpoint to a request without a verified token (401) or scope (403).

    The SDK's `RequireAuthMiddleware`, with two differences ChatGPT and the MCP
    authorization spec rely on: the challenge carries `scope`, and it names the
    metadata of the resource the request came in under. The body says nothing
    but the error.
    """

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        connection = HTTPConnection(scope)
        user = scope.get("user")
        if not isinstance(user, AuthenticatedUser):
            await self._refuse(connection, scope, receive, send, 401, "invalid_token")
            return
        credentials = scope.get("auth")
        if credentials is None or settings.MCP_SCOPE not in credentials.scopes:
            await self._refuse(connection, scope, receive, send, 403, "insufficient_scope")
            return
        await self.app(scope, receive, send)

    async def _refuse(
        self,
        connection: HTTPConnection,
        scope: Scope,
        receive: Receive,
        send: Send,
        status: int,
        error: str,
    ) -> None:
        description = (
            "Authentication required" if status == 401 else f"Required scope: {settings.MCP_SCOPE}"
        )
        response = JSONResponse(
            {"error": error, "error_description": description},
            status_code=status,
            headers={
                "WWW-Authenticate": challenge(
                    resource_for(connection), error=error, description=description
                )
            },
        )
        await response(scope, receive, send)


def verified_access(request: Request | None) -> Any | None:
    """The `VerifiedAccess` behind the request a tool call arrived in, or None.

    Read from the request's authenticated user, which the bearer middleware set;
    the SDK's context variable is the fallback for a transport that carries no
    request.
    """
    token: AccessToken | None = None
    user = request.scope.get("user") if request is not None else None
    if isinstance(user, AuthenticatedUser):
        token = user.access_token
    if token is None:
        token = get_access_token()
    if isinstance(token, JuristidAccessToken):
        return token.verified
    return None
