"""Resource occurrences on a page, and proof that downloaded bytes are the original.

A page's output HTML (Graph) names what the page holds:

* ``<object data=… data-attachment="name.pdf" type="application/pdf">`` — an
  embedded **original file**. Its ``data`` URL is the only route to the bytes.
* ``<img src=… data-fullres-src=…>`` — an image; ``data-fullres-src`` is the
  original, ``src`` an optimised rendition. A file *printout* is also rendered
  as images. Graph's output HTML gives no documented marker that tells a
  printout page image from a pasted picture, so an image is never recorded as
  a substitute for an attached file, and printout detection is UNVERIFIED.
* ``<a href=…>`` — a hyperlink. A SharePoint document link is a separately
  classified access path, not an embedded attachment, and is never crawled.

Every resource URL comes from content a person authored. Before a token is
attached, a URL must be HTTPS on an allowed Graph host, inside the *same* site
scope the page was read from, and shaped ``…/onenote/resources/{id}/$value`` or
``…/content``. Everything else is counted and never requested.

The desktop reader (``one:InsertedFile``) is the same idea with a different
route to the bytes: the client's local cache file. Its bytes are hashed twice
around the copy so a cache file rewritten mid-copy is detected, not trusted.
"""

from __future__ import annotations

import hashlib
import re
import urllib.parse
from dataclasses import dataclass, field
from enum import StrEnum
from html.parser import HTMLParser

#: The only host a Graph bearer token is ever sent to. Older output HTML names
#: ``www.onenote.com`` resource URLs; they are recorded but never fetched with a
#: Graph token (the token's audience is Graph), so they fail closed.
GRAPH_HOSTS = frozenset({"graph.microsoft.com"})
_RESOURCE_PATH = re.compile(r"/onenote/resources/(?P<id>[^/]+)/(?:\$value|content)$")

SIGNED_CONTAINER_EXTENSIONS = frozenset(
    {".asice", ".bdoc", ".asics", ".ddoc", ".edoc", ".p7m", ".p7s"}
)

#: Per-file and per-run download ceilings. A file above them is NOT_TESTED, never truncated.
MAX_RESOURCE_BYTES = 25 * 2**20
MAX_RUN_BYTES = 150 * 2**20
MAX_RUN_RESOURCES = 20


class OccurrenceKind(StrEnum):
    EMBEDDED_FILE = "EMBEDDED_FILE"
    IMAGE = "IMAGE"  # original via data-fullres-src; role (picture vs printout) unverified
    IMAGE_RENDITION_ONLY = "IMAGE_RENDITION_ONLY"  # only an optimised src: never the original
    LINKED_SHAREPOINT = "LINKED_SHAREPOINT"
    EXTERNAL_LINK = "EXTERNAL_LINK"
    INTERNAL_ONENOTE_LINK = "INTERNAL_ONENOTE_LINK"


class UrlVerdict(StrEnum):
    OK = "OK"
    NOT_HTTPS = "NOT_HTTPS"
    HOST_NOT_ALLOWED = "HOST_NOT_ALLOWED"
    OUTSIDE_SITE_SCOPE = "OUTSIDE_SITE_SCOPE"
    NOT_A_RESOURCE_PATH = "NOT_A_RESOURCE_PATH"


@dataclass(frozen=True)
class Occurrence:
    page_id: str
    ordinal: int  # position on the page, in document order
    kind: OccurrenceKind
    url: str
    resource_id: str = ""
    filename: str = ""  # untrusted display text, never a path
    declared_type: str = ""
    url_verdict: UrlVerdict = UrlVerdict.OK


def check_resource_url(url: str, *, site_scope_path: str) -> tuple[UrlVerdict, str]:
    """``site_scope_path`` is e.g. ``/v1.0/sites/{site-id}`` — the page's own scope."""
    parts = urllib.parse.urlsplit(url)
    if parts.scheme.lower() != "https":
        return UrlVerdict.NOT_HTTPS, ""
    if (parts.hostname or "").lower() not in GRAPH_HOSTS:
        return UrlVerdict.HOST_NOT_ALLOWED, ""
    if parts.username or parts.password or parts.port not in (None, 443):
        return UrlVerdict.HOST_NOT_ALLOWED, ""
    path = urllib.parse.unquote(parts.path)
    if any(segment in {".", ".."} for segment in path.split("/")) or "\\" in path:
        return UrlVerdict.OUTSIDE_SITE_SCOPE, ""  # dot-segments could walk out of the scope
    if not path.startswith(site_scope_path.rstrip("/") + "/onenote/resources/"):
        return UrlVerdict.OUTSIDE_SITE_SCOPE, ""
    match = _RESOURCE_PATH.search(path)
    if not match or "/" in match.group("id") or ".." in match.group("id"):
        return UrlVerdict.NOT_A_RESOURCE_PATH, ""
    return UrlVerdict.OK, match.group("id")


class _PageParser(HTMLParser):
    def __init__(self, page_id: str, site_scope_path: str) -> None:
        super().__init__(convert_charrefs=True)
        self.page_id = page_id
        self.scope = site_scope_path
        self.items: list[Occurrence] = []

    def _add(self, kind: OccurrenceKind, url: str, **extra: str) -> None:
        verdict, resource_id = (UrlVerdict.OK, "")
        if kind in {
            OccurrenceKind.EMBEDDED_FILE,
            OccurrenceKind.IMAGE,
            OccurrenceKind.IMAGE_RENDITION_ONLY,
        }:
            verdict, resource_id = check_resource_url(url, site_scope_path=self.scope)
        self.items.append(
            Occurrence(
                page_id=self.page_id,
                ordinal=len(self.items),
                kind=kind,
                url=url,
                resource_id=resource_id,
                url_verdict=verdict,
                **extra,
            )
        )

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        a = {key.lower(): value or "" for key, value in attrs}
        if tag == "object" and a.get("data"):
            self._add(
                OccurrenceKind.EMBEDDED_FILE,
                a["data"],
                filename=a.get("data-attachment", ""),
                declared_type=a.get("type", ""),
            )
        elif tag == "img" and (a.get("data-fullres-src") or a.get("src")):
            self._add(
                OccurrenceKind.IMAGE
                if a.get("data-fullres-src")
                else OccurrenceKind.IMAGE_RENDITION_ONLY,
                a.get("data-fullres-src") or a["src"],
                declared_type=a.get("data-fullres-src-type") or a.get("data-src-type", ""),
            )
        elif tag == "a" and a.get("href"):
            href = a["href"]
            host = (urllib.parse.urlsplit(href).hostname or "").lower()
            if href.lower().startswith("onenote:"):
                kind = OccurrenceKind.INTERNAL_ONENOTE_LINK
            elif host.endswith(".sharepoint.com"):
                kind = OccurrenceKind.LINKED_SHAREPOINT
            else:
                kind = OccurrenceKind.EXTERNAL_LINK
            self._add(kind, href)


def occurrences(page_id: str, html: str, *, site_scope_path: str) -> list[Occurrence]:
    parser = _PageParser(page_id, site_scope_path)
    parser.feed(html)
    parser.close()
    return parser.items


# -- downloads ----------------------------------------------------------------


_MAGIC: tuple[tuple[bytes, str], ...] = (
    (b"%PDF-", "pdf"),
    (b"PK\x03\x04", "zip"),  # docx/xlsx/pptx/asice/bdoc are ZIP containers
    (b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1", "ole"),  # doc/xls/msg
    (b"\x89PNG\r\n\x1a\n", "png"),
    (b"\xff\xd8\xff", "jpeg"),
    (b"GIF8", "gif"),
    (b"{\\rtf", "rtf"),
)

_EXPECTED = {
    ".pdf": {"pdf"},
    ".docx": {"zip"},
    ".xlsx": {"zip"},
    ".pptx": {"zip"},
    ".asice": {"zip"},
    ".bdoc": {"zip"},
    ".asics": {"zip"},
    ".doc": {"ole", "rtf"},
    ".xls": {"ole"},
    ".msg": {"ole"},
    ".png": {"png"},
    ".jpg": {"jpeg"},
    ".jpeg": {"jpeg"},
    ".gif": {"gif"},
    ".rtf": {"rtf"},
}


def sniff_format(head: bytes) -> str:
    """A label from the leading bytes; ``login-html`` for sign-in pages."""
    for magic, label in _MAGIC:
        if head.startswith(magic):
            return label
    lowered = head[:4096].lower()
    if b"<html" in lowered or b"<!doctype html" in lowered:
        if b"login.microsoftonline.com" in lowered or b"sign in" in lowered:
            return "login-html"
        return "html"
    if lowered.lstrip().startswith(b"{") and b'"error"' in lowered:
        return "json-error"
    return "unknown"


def format_plausible(extension: str, sniffed: str) -> bool | None:
    """``None`` when no expectation is recorded for the extension."""
    expected = _EXPECTED.get(extension.lower())
    if expected is None:
        return None
    return sniffed in expected


class DownloadVerdict(StrEnum):
    VERIFIED = "VERIFIED"
    HTTP_ERROR = "HTTP_ERROR"
    REDIRECTED = "REDIRECTED"
    TRUNCATED = "TRUNCATED"
    TOO_LARGE_NOT_TESTED = "TOO_LARGE_NOT_TESTED"
    NOT_AN_ORIGINAL = "NOT_AN_ORIGINAL"  # sign-in page, error JSON, HTML instead of a file
    FORMAT_IMPLAUSIBLE = "FORMAT_IMPLAUSIBLE"
    EMPTY = "EMPTY"


@dataclass(frozen=True)
class DownloadReceipt:
    occurrence: Occurrence
    verdict: DownloadVerdict
    status: int
    byte_length: int
    sha256: str
    sniffed: str
    signed_container: bool
    signature_verified: bool = False  # bytes downloaded is not a verified signature
    notes: tuple[str, ...] = field(default_factory=tuple)


def verify_download(
    occurrence: Occurrence,
    *,
    status: int,
    headers: dict[str, str],
    body: bytes,
    requested_url: str,
    final_url: str,
) -> DownloadReceipt:
    """Decide whether a response is the complete original of this occurrence."""
    lowered = {key.lower(): value for key, value in headers.items()}
    ext = ""
    if "." in occurrence.filename:
        ext = "." + occurrence.filename.rsplit(".", 1)[-1].lower()
    sniffed = sniff_format(body[:8192])
    digest = hashlib.sha256(body).hexdigest()
    signed = ext in SIGNED_CONTAINER_EXTENSIONS

    def receipt(verdict: DownloadVerdict, *notes: str) -> DownloadReceipt:
        return DownloadReceipt(
            occurrence, verdict, status, len(body), digest, sniffed, signed, False, notes
        )

    if final_url != requested_url:
        return receipt(DownloadVerdict.REDIRECTED)
    if status != 200:
        return receipt(DownloadVerdict.HTTP_ERROR)
    declared = lowered.get("content-length")
    if declared is not None and declared.isdigit() and int(declared) > MAX_RESOURCE_BYTES:
        return receipt(DownloadVerdict.TOO_LARGE_NOT_TESTED)
    if len(body) > MAX_RESOURCE_BYTES:
        return receipt(DownloadVerdict.TOO_LARGE_NOT_TESTED)
    if declared is not None and declared.isdigit() and int(declared) != len(body):
        return receipt(DownloadVerdict.TRUNCATED, f"declared={declared}")
    if not body:
        return receipt(DownloadVerdict.EMPTY)
    if sniffed in {"login-html", "json-error"} or (
        sniffed == "html" and ext not in {".htm", ".html"}
    ):
        return receipt(DownloadVerdict.NOT_AN_ORIGINAL)
    content_type = lowered.get("content-type", "").split(";")[0].strip().lower()
    if content_type.startswith("text/html") and ext not in {".htm", ".html"}:
        return receipt(DownloadVerdict.NOT_AN_ORIGINAL, "content-type text/html")
    if format_plausible(ext, sniffed) is False:
        return receipt(DownloadVerdict.FORMAT_IMPLAUSIBLE, f"ext={ext} sniffed={sniffed}")
    return receipt(DownloadVerdict.VERIFIED)
