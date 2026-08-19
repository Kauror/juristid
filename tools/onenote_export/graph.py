"""A read-only Microsoft Graph client with a very short list of things it trusts.

The security property this module exists to hold is one sentence: **the bearer
token is only ever sent to a host on the allow-list, at a path that looks like a
Graph resource.** Everything else here is in service of that.

It matters because of where the URLs come from. Notebook and section listings
are Graph's own JSON, but page *resource* URLs are extracted from page HTML —
content a person authored, that may have been pasted from anywhere, and that a
crawler will happily follow. A tool that sends `Authorization: Bearer …` to a URL
it found in a document is a tool that leaks a Microsoft 365 token to whoever
wrote the document (Stage-2B brief 56, 57).

Two hosts are allowed, and both are needed:

* ``graph.microsoft.com`` — the API itself.
* ``www.onenote.com`` — where page HTML points its image and file resources.
  This is not a workaround; it is what the current documentation shows the
  service returning in ``src``, ``data-fullres-src`` and ``data`` attributes.

Verified against Microsoft Learn, *Get OneNote content and structure by using
the OneNote API* and *Use the OneNote REST API* (both current as of 2026-08).
"""

from __future__ import annotations

import json
import time
import urllib.error
import urllib.parse
import urllib.request
from collections.abc import Iterator
from dataclasses import dataclass

GRAPH_ROOT = "https://graph.microsoft.com/v1.0"

#: The only hosts an authenticated request may reach.
ALLOWED_HOSTS = frozenset({"graph.microsoft.com", "www.onenote.com"})

#: A resource URL must contain this segment. Narrow on purpose: it is the only
#: kind of URL page HTML is expected to yield, so anything else — a link to a
#: SharePoint file, a link to somebody's blog — is refused rather than guessed
#: about.
RESOURCE_SEGMENT = "/resources/"

#: Graph returns 20 entries by default and permits up to 100. Asking for the
#: maximum means fewer round trips and fewer chances to mishandle paging.
PAGE_SIZE = 100


class GraphError(RuntimeError):
    pass


class UnsafeUrl(GraphError):
    """A URL that will not be sent a token, and why."""


@dataclass(frozen=True)
class GraphResponse:
    status: int
    headers: dict[str, str]
    body: bytes

    def json(self) -> dict:
        return json.loads(self.body.decode("utf-8"))


def require_safe_url(url: str, *, expect_resource: bool = False) -> str:
    """Refuse anything that is not a Graph or OneNote HTTPS URL.

    Checks scheme, host and — for resource URLs — the path shape. The host
    comparison is against the parsed hostname, never a substring of the URL:
    ``https://graph.microsoft.com.attacker.invalid/…`` contains the allowed
    host as text and is a different host entirely.
    """
    parsed = urllib.parse.urlsplit(url)
    if parsed.scheme != "https":
        raise UnsafeUrl(f"Refusing a non-HTTPS URL: {parsed.scheme}://…")
    host = (parsed.hostname or "").lower()
    if host not in ALLOWED_HOSTS:
        raise UnsafeUrl(f"Refusing to send a token to {host or '(no host)'}")
    if expect_resource and RESOURCE_SEGMENT not in parsed.path:
        raise UnsafeUrl(f"Refusing a URL that is not a Graph resource path: {parsed.path}")
    return url


class GraphClient:
    """GET only. There is no method here that can change anything.

    Not an oversight and not a convention — the whole point of asking for
    ``Notes.Read`` is that the tool cannot write, and an unused ``post`` sitting
    in this class would be the first thing somebody reached for when a later
    stage wanted to "just update the status" (Stage-2B brief 62).
    """

    def __init__(self, access_token: str, *, opener: object | None = None) -> None:
        self._token = access_token
        self._opener = opener or urllib.request.build_opener()

    def get(
        self, url: str, *, accept: str = "application/json", expect_resource: bool = False
    ) -> GraphResponse:
        safe = require_safe_url(url, expect_resource=expect_resource)
        request = urllib.request.Request(safe, method="GET")  # noqa: S310 - scheme checked above
        request.add_header("Authorization", f"Bearer {self._token}")
        request.add_header("Accept", accept)
        request.add_header("User-Agent", "juristid-onenote-export/1.0")

        for attempt in range(4):
            try:
                with self._opener.open(request, timeout=60) as response:  # type: ignore[attr-defined]
                    return GraphResponse(
                        status=response.status,
                        headers={key.lower(): value for key, value in response.headers.items()},
                        body=response.read(),
                    )
            except urllib.error.HTTPError as error:
                # 429 and 503 carry Retry-After and mean "slow down", not "fail".
                # Anything else is a real answer and is reported as one.
                if error.code not in {429, 503} or attempt == 3:
                    raise GraphError(f"Graph returned {error.code} for {_redact(safe)}") from error
                time.sleep(_retry_after(error.headers))
        raise GraphError("unreachable")  # pragma: no cover

    def get_json(self, url: str) -> dict:
        return self.get(url).json()

    def paginate(self, url: str) -> Iterator[dict]:
        """Follow ``@odata.nextLink`` until it stops.

        Assuming one response holds everything is the mistake this method
        exists to prevent: Graph returns 20 pages by default, and a notebook
        with 900 of them would export 20 and look complete.

        The continuation URL is re-validated before it is followed. It arrives
        from the service and is almost certainly fine — but "almost certainly
        fine" is what a token allow-list is for (Stage-2B brief 57).
        """
        next_url: str | None = _with_page_size(url)
        while next_url:
            payload = self.get_json(require_safe_url(next_url))
            yield from payload.get("value", [])
            next_url = payload.get("@odata.nextLink")

    def resource_bytes(self, url: str) -> bytes:
        """Download one image or file resource named by page HTML."""
        return self.get(url, accept="*/*", expect_resource=True).body


def _with_page_size(url: str) -> str:
    parsed = urllib.parse.urlsplit(url)
    query = dict(urllib.parse.parse_qsl(parsed.query))
    query.setdefault("$top", str(PAGE_SIZE))
    return urllib.parse.urlunsplit(parsed._replace(query=urllib.parse.urlencode(query)))


def _retry_after(headers: object) -> float:
    try:
        return min(float(headers.get("Retry-After", 2)), 30.0)  # type: ignore[union-attr]
    except (TypeError, ValueError):
        return 2.0


def _redact(url: str) -> str:
    """A URL safe to log: host and path only.

    Query strings on Graph URLs can carry identifiers, and this tool's whole
    output discipline is that nothing identifying real content reaches a log.
    """
    parsed = urllib.parse.urlsplit(url)
    return f"{parsed.scheme}://{parsed.netloc}{parsed.path}"
