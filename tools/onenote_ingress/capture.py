"""Bind a captured page body to the page it was requested for — or refuse it.

ADR 0015 records the failure this module exists to prevent: an earlier Graph
export stored one page's HTML under 342 other pages and reported PASS, because
the worker saved whatever the response pane showed, which was often the
*previous* request's body. Every file was internally consistent; none of the
checks asked whether the body belonged to the page it was filed under.

So a capture is accepted only when all of these hold:

1. the response is to the exact URL requested (no redirect), status 200,
   ``text/html``, not truncated, not a sign-in or error page;
2. the request carried a fresh ``client-request-id`` and the response echoes it
   — a cached or misrouted response for another request does not;
3. the body's own markers — ``<title>`` and ``<meta name="created">`` in Graph
   output HTML — equal the page metadata read for that same page id;
4. no other identity in the run already holds the same non-trivial body.

(3) is only as strong as the markers are distinctive. Two pages with the same
title created in the same second cannot be told apart by their bodies, so such
a capture is accepted with ``WEAK_BINDING`` and needs a second, independent
reference before it may count as verified.

The same body under two identities is not automatically wrong — a page can be
copied, and empty templates are common — so it is *classified*
(``EMPTY_TEMPLATE`` / ``DUPLICATE_BODY_REVIEW``), never silently kept.
"""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass, field
from enum import StrEnum
from html import unescape

_TITLE = re.compile(r"<title>(.*?)</title>", re.IGNORECASE | re.DOTALL)
_CREATED = re.compile(r"<meta\s+name=\"created\"\s+content=\"([^\"]+)\"", re.IGNORECASE)
_TAGS = re.compile(r"<[^>]+>")
_SIGN_IN_MARKERS = (b"login.microsoftonline.com", b"<title>sign in", b"aadsts")

#: Below this many characters of visible text a body is a template, not evidence.
TRIVIAL_TEXT_CHARS = 40


class CaptureVerdict(StrEnum):
    ACCEPTED = "ACCEPTED"
    WEAK_BINDING = "WEAK_BINDING"
    EMPTY_TEMPLATE = "EMPTY_TEMPLATE"
    DUPLICATE_BODY_REVIEW = "DUPLICATE_BODY_REVIEW"
    REJECTED = "REJECTED"


@dataclass(frozen=True)
class PageMetadata:
    page_id: str  # reader-scoped id the request named
    title: str
    created: str  # ISO 8601 as the provider returned it
    last_modified: str


@dataclass(frozen=True)
class CaptureResponse:
    requested_url: str
    final_url: str
    status: int
    headers: dict[str, str]
    body: bytes
    sent_client_request_id: str


@dataclass(frozen=True)
class CaptureResult:
    page_id: str
    verdict: CaptureVerdict
    body_sha256: str
    request_id: str
    reasons: tuple[str, ...] = field(default_factory=tuple)

    @property
    def usable(self) -> bool:
        """Only a uniquely bound, non-trivial body counts as a verified capture.

        Every other verdict is kept for a reviewer (or refused) — an empty
        template, a duplicate or a weakly bound body is never evidence alone.
        """
        return self.verdict is CaptureVerdict.ACCEPTED


def _instant(value: str) -> str:
    """``2026-01-02T03:04:05.0000000Z`` / ``…+00:00`` → ``2026-01-02T03:04:05``."""
    text = value.strip().replace("Z", "").split("+")[0]
    return text.split(".")[0]


def visible_text(html: str) -> str:
    body = html.split("<body", 1)[-1]
    return " ".join(unescape(_TAGS.sub(" ", body)).split())


@dataclass
class CaptureLedger:
    """Bodies seen in one run, so a body filed under a second identity is caught."""

    by_sha: dict[str, str] = field(default_factory=dict)  # sha -> first page_id
    markers: dict[tuple[str, str], set[str]] = field(default_factory=dict)
    registered: set[str] = field(default_factory=set)
    #: Every page whose body is shared with another identity — both sides.
    duplicate_holders: set[str] = field(default_factory=set)

    def register_metadata(self, metadata: PageMetadata) -> None:
        key = (metadata.title, _instant(metadata.created))
        self.markers.setdefault(key, set()).add(metadata.page_id)
        self.registered.add(metadata.page_id)

    @classmethod
    def for_run(cls, metadata: list[PageMetadata]) -> CaptureLedger:
        """Build from the run's *whole* metadata listing before any capture."""
        ledger = cls()
        for item in metadata:
            ledger.register_metadata(item)
        return ledger


def verify_capture(
    metadata: PageMetadata, response: CaptureResponse, ledger: CaptureLedger
) -> CaptureResult:
    body = response.body
    digest = hashlib.sha256(body).hexdigest()
    headers = {key.lower(): value for key, value in response.headers.items()}
    request_id = headers.get("request-id", "")
    reasons: list[str] = []

    def result(verdict: CaptureVerdict) -> CaptureResult:
        return CaptureResult(metadata.page_id, verdict, digest, request_id, tuple(reasons))

    if metadata.page_id not in ledger.registered:
        reasons.append("METADATA_NOT_IN_RUN_LEDGER")  # twin detection would be blind
    if response.final_url != response.requested_url:
        reasons.append("REDIRECTED")
    if response.status != 200:
        reasons.append(f"STATUS_{response.status}")
    if not headers.get("content-type", "").lower().startswith("text/html"):
        reasons.append("NOT_TEXT_HTML")
    declared = headers.get("content-length", "")
    if declared.isdigit() and int(declared) != len(body):
        reasons.append("LENGTH_MISMATCH")
    head = body[:8192].lower()
    if any(marker in head for marker in _SIGN_IN_MARKERS):
        reasons.append("SIGN_IN_OR_ERROR_PAGE")
    if not body.rstrip().lower().endswith(b"</html>"):
        reasons.append("TRUNCATED_OR_NOT_A_DOCUMENT")
    echoed = headers.get("client-request-id", "")
    if (
        not response.sent_client_request_id
        or echoed.lower() != response.sent_client_request_id.lower()
    ):
        reasons.append("CLIENT_REQUEST_ID_NOT_ECHOED")
    if reasons:
        return result(CaptureVerdict.REJECTED)

    html = body.decode("utf-8", errors="replace")
    title = _TITLE.search(html)
    created = _CREATED.search(html)
    if title is None or unescape(title.group(1)).strip() != metadata.title.strip():
        reasons.append("TITLE_MARKER_MISMATCH")
    if created is None or _instant(created.group(1)) != _instant(metadata.created):
        reasons.append("CREATED_MARKER_MISMATCH")
    if reasons:
        return result(CaptureVerdict.REJECTED)

    trivial = len(visible_text(html)) < TRIVIAL_TEXT_CHARS
    twins = ledger.markers.get((metadata.title, _instant(metadata.created)), set()) - {
        metadata.page_id
    }
    first = ledger.by_sha.setdefault(digest, metadata.page_id)
    if first != metadata.page_id:
        ledger.duplicate_holders.update({first, metadata.page_id})
        reasons.append(f"SAME_BODY_AS:{first}")
    if twins:
        # Markers cannot tell this page from another one: an independent reference must.
        reasons.append(f"MARKERS_SHARED_WITH:{len(twins)}")
        return result(CaptureVerdict.WEAK_BINDING)
    if first != metadata.page_id:
        return result(
            CaptureVerdict.EMPTY_TEMPLATE if trivial else CaptureVerdict.DUPLICATE_BODY_REVIEW
        )
    return result(CaptureVerdict.EMPTY_TEMPLATE if trivial else CaptureVerdict.ACCEPTED)
