"""Absolute Juristid links for what the MCP tools return (docs/adr/0185 §2).

Every URL a tool answers with is ``settings.JURISTID_PUBLIC_ORIGIN`` followed by
a path `reverse()` built from a verified record id. Never from a request's Host
header — the MCP process sits behind a tunnel and its Host is whatever the
tunnel says — and never from anything the caller sent. No URL carries a token,
a credential or a query the caller chose.

The paths themselves are the search page's own (`app.search.links`), so a
colleague who follows a link from ChatGPT lands where the same result on
`/otsing/` would have taken them.
"""

from __future__ import annotations

from typing import Any
from urllib.parse import urlsplit

from django.conf import settings
from django.core.exceptions import ImproperlyConfigured
from django.urls import reverse

from app.search import links as search_links
from app.submissions.opinions import opinions_url


def public_origin() -> str:
    """``https://juristid.example`` — checked every time, because it is cheap.

    Refused when empty, when it is not a bare origin (a path, query, fragment
    or credentials), and when it is not ``https`` — unless ``DEBUG`` is on, where
    a developer's ``http://localhost:8000`` is the honest value.
    """
    origin = (getattr(settings, "JURISTID_PUBLIC_ORIGIN", "") or "").rstrip("/")
    if not origin:
        raise ImproperlyConfigured("JURISTID_PUBLIC_ORIGIN is not set.")
    parts = urlsplit(origin)
    if (
        parts.scheme not in ("https", "http")
        or not parts.hostname
        or parts.path
        or parts.query
        or parts.fragment
        or parts.username
        or parts.password
    ):
        raise ImproperlyConfigured("JURISTID_PUBLIC_ORIGIN must be a bare origin.")
    if parts.scheme != "https" and not settings.DEBUG:
        raise ImproperlyConfigured("JURISTID_PUBLIC_ORIGIN must use https.")
    return origin


def absolute(path: str) -> str:
    """The origin plus one of this application's own paths.

    A path that is not site-absolute (``//elsewhere``, ``https://…``, or no
    leading slash) is refused: it would make the origin a prefix of somebody
    else's address.
    """
    if not path.startswith("/") or path.startswith("//"):
        raise ValueError("Only this application's own paths are made absolute.")
    return public_origin() + path


def matter_url(matter_id: Any) -> str:
    return absolute(search_links.matter_path(matter_id))


def matter_documents_url(matter_id: Any) -> str:
    return absolute(reverse("matters:matter_documents", kwargs={"pk": matter_id}))


def entry_url(matter_id: Any, entry_id: Any) -> str:
    return absolute(search_links.entry_path(matter_id, entry_id))


def document_url(document_id: Any) -> str:
    return absolute(search_links.document_path(document_id))


def source_page_url(link_id: Any) -> str:
    return absolute(search_links.source_page_path(link_id))


def submissions_url(matter: Any) -> str:
    """A Matter's opinions: Dokumendid filtered to `Arvamus` (docs/adr/0061)."""
    return absolute(opinions_url(matter))


def result_url(result: Any) -> str:
    """Where a search result opens — the search page's own target, made absolute."""
    return absolute(search_links.target_path(result))
