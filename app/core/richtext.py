"""Constrained rich text for authored entries.

The composer is not a document editor. It supports what a lawyer actually needs
when recording a meeting or a call — paragraphs, emphasis, links, lists and a
simple comparison table — and nothing that could carry script, styling or
tracking into the record (master specification 8.3, 15.6).

Everything stored has been through :func:`sanitize_entry_html`. There is no code
path that writes authored HTML to the database without passing it here first.
"""

from __future__ import annotations

import re

import nh3

# Deliberately small. Anything outside this set is stripped rather than escaped,
# which is what makes a paste from Word or Outlook come out clean.
ALLOWED_TAGS: set[str] = {
    "p",
    "br",
    "strong",
    "b",
    "em",
    "i",
    "u",
    "a",
    "ul",
    "ol",
    "li",
    "h3",
    "h4",
    "blockquote",
    "table",
    "thead",
    "tbody",
    "tr",
    "th",
    "td",
}

ALLOWED_ATTRIBUTES: dict[str, set[str]] = {
    "a": {"href", "title"},
}

# No javascript:, no data:, no file:.
ALLOWED_URL_SCHEMES: set[str] = {"http", "https", "mailto"}

_WHITESPACE = re.compile(r"\s+")


def sanitize_entry_html(raw: str) -> str:
    """Return a safe HTML fragment, or an empty string for empty input.

    Unsupported markup is removed, not escaped: pasted Word and Outlook content
    arrives wrapped in font, span and style noise, and the useful outcome is the
    text with its structure, not a visible dump of the original markup.
    """
    if not raw or not raw.strip():
        return ""

    cleaned = nh3.clean(
        raw,
        tags=ALLOWED_TAGS,
        attributes=ALLOWED_ATTRIBUTES,
        url_schemes=ALLOWED_URL_SCHEMES,
        link_rel="noopener noreferrer nofollow",
        strip_comments=True,
    )
    return cleaned.strip()


def plain_text(html: str) -> str:
    """The text content of an entry body, for excerpts and search.

    Never used for rendering — it exists so a list can show a preview without
    putting markup through another escaping decision.
    """
    if not html:
        return ""
    text = nh3.clean(html, tags=set(), attributes={}, strip_comments=True)
    # nh3 leaves the text nodes; collapse the whitespace the tags used to hold.
    return _WHITESPACE.sub(" ", text).strip()


def excerpt(html: str, limit: int = 200) -> str:
    text = plain_text(html)
    if len(text) <= limit:
        return text
    return text[: limit - 1].rstrip() + "…"


def is_empty(html: str) -> bool:
    return not plain_text(html)
