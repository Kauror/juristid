"""The only way this package talks to Microsoft Graph: scoped, GET-only, bounded.

* GET only. There is no other verb in this module and no way to pass one.
* A request URL must be HTTPS on ``graph.microsoft.com`` under the configured
  site scope (``/v1.0/sites/{site-id}/onenote/``) — or a resource URL that
  :func:`tools.onenote_ingress.resources.check_resource_url` accepted for the
  same scope. The bearer token is attached only after that check.
* Redirects are refused, not followed: a 3xx would carry the token, or the
  caller's trust, to a host nobody approved.
* Every response is read to at most ``max_bytes + 1`` so an oversized body is
  detected rather than silently truncated.
* Each request carries a fresh ``client-request-id``; the capture verifier
  checks the echo.
* 429/503 honour ``Retry-After`` with a bound; retries are finite.

The token comes from a ``TokenSource`` callable. Phase 1 ships none: the
tenant has no app registration and no delegated consent for this reader
(see the Phase 1 report). Nothing here stores, prints or accepts a token as a
command-line argument.
"""

from __future__ import annotations

import time
import urllib.error
import urllib.parse
import urllib.request
import uuid
from collections.abc import Callable
from dataclasses import dataclass

from tools.onenote_ingress.resources import UrlVerdict, check_resource_url
from tools.onenote_ingress.scan import retry_after_seconds

GRAPH_HOST = "graph.microsoft.com"
TokenSource = Callable[[], str]


class ScopeViolation(RuntimeError):
    pass


class TooLarge(RuntimeError):
    pass


@dataclass(frozen=True)
class Response:
    requested_url: str
    final_url: str
    status: int
    headers: dict[str, str]
    body: bytes
    client_request_id: str


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None  # urllib then raises HTTPError with the 3xx status


def default_opener() -> urllib.request.OpenerDirector:
    return urllib.request.build_opener(_NoRedirect())


@dataclass
class ScopedGraphTransport:
    site_id: str
    token_source: TokenSource
    opener: urllib.request.OpenerDirector | None = None
    timeout: float = 30.0
    max_bytes: int = 25 * 2**20
    max_attempts: int = 4
    sleep: Callable[[float], None] = time.sleep

    @property
    def scope_path(self) -> str:
        return f"/v1.0/sites/{self.site_id}"

    def check(self, url: str) -> None:
        parts = urllib.parse.urlsplit(url)
        if parts.scheme != "https" or (parts.hostname or "").lower() != GRAPH_HOST:
            raise ScopeViolation("URL outside the approved Graph host")
        if parts.username or parts.password or parts.port not in (None, 443):
            raise ScopeViolation("credentials or port in URL")
        path = urllib.parse.unquote(parts.path)
        if any(segment in {".", ".."} for segment in path.split("/")) or "\\" in path:
            raise ScopeViolation("dot-segment or backslash in path")
        if not path.startswith(self.scope_path + "/onenote/"):
            raise ScopeViolation("URL outside the approved site's OneNote scope")
        if "/onenote/resources/" in path and (
            check_resource_url(url, site_scope_path=self.scope_path)[0] is not UrlVerdict.OK
        ):
            raise ScopeViolation("malformed resource URL")

    def get(self, url: str, *, accept: str = "application/json") -> Response:
        self.check(url)
        opener = self.opener or default_opener()
        last: Response | None = None
        for attempt in range(self.max_attempts):
            client_request_id = str(uuid.uuid4())
            request = urllib.request.Request(  # noqa: S310 - URL checked above
                url,
                method="GET",
                headers={
                    "Authorization": f"Bearer {self.token_source()}",
                    "Accept": accept,
                    "client-request-id": client_request_id,
                    "return-client-request-id": "true",
                },
            )
            try:
                with opener.open(request, timeout=self.timeout) as handle:
                    body = handle.read(self.max_bytes + 1)
                    status = handle.status
                    headers = dict(handle.headers.items())
                    final_url = handle.geturl()
            except urllib.error.HTTPError as error:
                body = error.read(self.max_bytes + 1) if error.fp else b""
                status, headers, final_url = error.code, dict(error.headers.items()), url
            if len(body) > self.max_bytes:
                raise TooLarge(f"response exceeds {self.max_bytes} bytes")
            if final_url != url:
                # A redirect was followed somewhere below us: the token may have travelled.
                raise ScopeViolation("response came from a URL other than the one requested")
            last = Response(url, final_url, status, headers, body, client_request_id)
            if status in {429, 503} and attempt + 1 < self.max_attempts:
                self.sleep(retry_after_seconds(headers.get("Retry-After"), attempt=attempt))
                continue
            return last
        assert last is not None  # noqa: S101 - max_attempts >= 1
        return last
