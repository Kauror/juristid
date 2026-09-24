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
from html.parser import HTMLParser

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


#: Elements whose boundary is a boundary between words. `<p>Tere</p><p>kolleeg</p>`
#: is two words on the page, and stripping the tags without a space between
#: them fused them into one token nobody could search for (ENG-082).
_BLOCK_ELEMENTS: frozenset[str] = frozenset(
    {
        "address", "article", "aside", "blockquote", "br", "dd", "div", "dl", "dt",
        "figcaption", "figure", "footer", "h1", "h2", "h3", "h4", "h5", "h6", "header",
        "hr", "li", "main", "nav", "ol", "p", "pre", "section", "table", "tbody", "td",
        "tfoot", "th", "thead", "tr", "ul",
    }
)  # fmt: skip

#: Elements whose *contents* are not text a reader sees.
_INVISIBLE_ELEMENTS: frozenset[str] = frozenset({"script", "style", "template", "noscript"})


class _TextOnly(HTMLParser):
    """Collects the text a browser would show, and nothing else."""

    def __init__(self) -> None:
        # `convert_charrefs` decodes `&amp;`, `&nbsp;` and `&lt;` exactly once,
        # in the text nodes and nowhere else.
        super().__init__(convert_charrefs=True)
        self.parts: list[str] = []
        self._hidden = 0

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag in _INVISIBLE_ELEMENTS:
            self._hidden += 1
        elif tag in _BLOCK_ELEMENTS:
            self.parts.append(" ")

    def handle_startendtag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag in _BLOCK_ELEMENTS:
            self.parts.append(" ")

    def handle_endtag(self, tag: str) -> None:
        if tag in _INVISIBLE_ELEMENTS:
            self._hidden = max(self._hidden - 1, 0)
        elif tag in _BLOCK_ELEMENTS:
            self.parts.append(" ")

    def handle_data(self, data: str) -> None:
        if not self._hidden:
            self.parts.append(data)


def plain_text(html: str) -> str:
    """The text content of an entry body, for excerpts and search.

    **Text, not escaped HTML.** It used to return nh3's output, which is
    markup with every tag removed and every entity still escaped: a lawyer's
    «AS Näide & Partnerid» was indexed as `AS Näide &amp; Partnerid`, and the
    template's own escaping then showed the entity literally in the snippet.
    Adjacent blocks were fused into one word as well (ENG-082). Now entities
    are decoded once, block boundaries become spaces, and script and style
    contents are dropped.

    The result is a plain string. It is never marked safe and never inserted
    as markup: every place it reaches a page goes through template
    autoescaping, which is the only escaping layer — so `&lt;script&gt;` in
    the source is the visible text `<script>` and renders as that text.
    """
    if not html:
        return ""
    parser = _TextOnly()
    parser.feed(html)
    parser.close()
    return _WHITESPACE.sub(" ", "".join(parser.parts)).strip()


def excerpt(html: str, limit: int = 200) -> str:
    text = plain_text(html)
    if len(text) <= limit:
        return text
    return text[: limit - 1].rstrip() + "…"


def is_empty(html: str) -> bool:
    return not plain_text(html)
