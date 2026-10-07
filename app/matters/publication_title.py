"""The page title of a koda.ee publication, read before the record is saved.

`+ Ülevaade / uudis` previews what the pasted address is before `Lisa ülevaade
/ uudis` is pressed: its kind (`publication_kind`) and its title. This module is
the title half, and the **one** place this application contacts an address a
person pasted (docs/adr/0089 §4, amended 2026-10-07).

**Only the Chamber's own site, and only like this.** The fence is the host, not
the content:

- `https` only — an `http://` koda.ee address is read over `https`;
- the host is `koda.ee` or `www.koda.ee` and nothing else — no port, no
  userinfo, no other domain — so nobody can point the server at an internal
  address, a metadata endpoint or a third-party site;
- every redirect is followed by hand and must pass the same test (koda.ee
  sends `koda.ee/…` to `www.koda.ee/…`); at most three;
- a short timeout and a size cap; nothing is stored, logged or followed from
  the page itself.

What it reads is `og:title`, or `<title>` without the site's own
«| Eesti Kaubandus-Tööstuskoda». Any failure is an empty answer: the preview
then says nothing and the person types the title, as before.

`settings.PUBLICATION_TITLE_FETCH` switches it off where no network is wanted
(the test suite, CI, a recovery rehearsal).
"""

from __future__ import annotations

import html
import re
import urllib.error
import urllib.request
from html.parser import HTMLParser
from typing import Any
from urllib.parse import urljoin, urlsplit, urlunsplit

from django.conf import settings

from app.matters.publication_kind import KODA_HOSTS

#: Seconds for each request; the preview is a convenience, never a wait.
TIMEOUT_SECONDS = 4
#: A koda.ee article is ~90 KB; the title is in the first few.
MAX_BYTES = 512 * 1024
MAX_REDIRECTS = 3
SITE_SUFFIX = re.compile(r"\s*[|–-]\s*Eesti Kaubandus-Tööstuskoda\s*$")
USER_AGENT = "Juristid (Eesti Kaubandus-Tööstuskoda; pealkirja eelvaade)"


def fetchable_url(url: str | None) -> str:
    """The `https` form of a koda.ee address, or ``""`` for anything else."""
    if not url:
        return ""
    try:
        parts = urlsplit(url.strip())
        port = parts.port
    except ValueError:
        return ""
    if parts.scheme.lower() not in ("http", "https"):
        return ""
    if parts.username or parts.password or port not in (None, 443):
        return ""
    host = (parts.hostname or "").lower()
    if host not in KODA_HOSTS:
        return ""
    return urlunsplit(("https", host, parts.path or "/", parts.query, ""))


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    """Hand every redirect back, so `_fetch` can check where it points."""

    def redirect_request(
        self,
        req: urllib.request.Request,
        fp: Any,
        code: int,
        msg: str,
        headers: Any,
        newurl: str,
    ) -> urllib.request.Request | None:
        return None


_OPENER = urllib.request.build_opener(_NoRedirect)


class _TitleReader(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.og_title = ""
        self.title = ""
        self._in_title = False

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag == "meta":
            named = dict(attrs)
            if (named.get("property") or "").lower() == "og:title" and not self.og_title:
                self.og_title = named.get("content") or ""
        elif tag == "title" and not self.title:
            self._in_title = True

    def handle_endtag(self, tag: str) -> None:
        if tag == "title":
            self._in_title = False

    def handle_data(self, data: str) -> None:
        if self._in_title:
            self.title += data


def _clean(text: str) -> str:
    text = " ".join(html.unescape(text or "").split())
    return SITE_SUFFIX.sub("", text).strip()


def title_from_html(body: str) -> str:
    """`og:title`, else `<title>` without the site's name; ``""`` if neither."""
    reader = _TitleReader()
    try:
        reader.feed(body)
    except Exception:
        return ""
    return _clean(reader.og_title) or _clean(reader.title)


def _fetch(url: str) -> str:
    for _hop in range(MAX_REDIRECTS + 1):
        # Every address here came through `fetchable_url`: `https`, koda.ee.
        if not url.startswith("https://"):
            return ""
        request = urllib.request.Request(  # noqa: S310 — https and koda.ee only, checked above
            url, headers={"User-Agent": USER_AGENT, "Accept": "text/html"}
        )
        try:
            with _OPENER.open(request, timeout=TIMEOUT_SECONDS) as response:
                raw = response.read(MAX_BYTES)
                charset = response.headers.get_content_charset() or "utf-8"
                return raw.decode(charset, errors="replace")
        except urllib.error.HTTPError as error:
            if error.code not in (301, 302, 303, 307, 308):
                return ""
            url = fetchable_url(urljoin(url, error.headers.get("Location") or ""))
            if not url:
                return ""
    return ""


def fetch_publication_title(url: str | None) -> str:
    """The koda.ee page's title, or ``""`` — never an exception."""
    if not getattr(settings, "PUBLICATION_TITLE_FETCH", False):
        return ""
    target = fetchable_url(url)
    if not target:
        return ""
    try:
        body = _fetch(target)
    except (OSError, ValueError):
        return ""
    return title_from_html(body)
