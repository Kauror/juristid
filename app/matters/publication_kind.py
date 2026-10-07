"""Which publication a koda.ee address is: an `Ülevaade` or an `Uudis`.

The Chamber's own site says it in the path, and these are the paths it uses
(verified against koda.ee on 2026-10-07, in all three languages):

- overview — `/et/meie-moju/hetkel-kasil/…`, `/en/current-drafts/…`,
  `/ru/current-drafts/…` (the `Hetkel käsil` section and its translations);
- news — `/et/uudised/…`, `/en/news/…`, `/ru/novosti/…`;
- and the Estonian sections' older addresses with no language prefix, which
  koda.ee still redirects to them: `/meie-moju/hetkel-kasil/…` and
  `/uudised/…` (verified 2026-10-07; `/hetkel-kasil` alone is not a page).

Anything else — another host, another section — is **not classified**: the
caller asks the person rather than guessing (docs/adr/0142 §C).
"""

from __future__ import annotations

from urllib.parse import urlsplit

from app.matters.enums import WebsiteOverviewKind

KODA_HOSTS = frozenset({"koda.ee", "www.koda.ee"})

OVERVIEW_PATHS = (
    "/et/meie-moju/hetkel-kasil",
    "/en/current-drafts",
    "/ru/current-drafts",
    "/meie-moju/hetkel-kasil",
)
NEWS_PATHS = ("/et/uudised", "/en/news", "/ru/novosti", "/uudised")


def _under(path: str, prefix: str) -> bool:
    return path == prefix or path.startswith(prefix + "/")


def classify_publication_url(url: str | None) -> str:
    """`OVERVIEW`, `NEWS`, or ``""`` when the address does not say which."""
    if not url:
        return ""
    try:
        parts = urlsplit(url.strip())
    except ValueError:
        return ""
    host = (parts.hostname or "").lower()
    if host not in KODA_HOSTS:
        return ""
    path = (parts.path or "/").rstrip("/").lower() or "/"
    if any(_under(path, prefix) for prefix in OVERVIEW_PATHS):
        return WebsiteOverviewKind.OVERVIEW
    if any(_under(path, prefix) for prefix in NEWS_PATHS):
        return WebsiteOverviewKind.NEWS
    return ""


def same_publication_url(first: str | None, second: str | None) -> bool:
    """Whether two addresses name the same page: scheme, `www.`, case of the
    host, a trailing slash and a fragment do not make a second page."""

    def key(value: str | None) -> tuple[str, str, str]:
        parts = urlsplit((value or "").strip())
        host = (parts.hostname or "").lower().removeprefix("www.")
        return (host, (parts.path or "/").rstrip("/") or "/", parts.query)

    if not first or not second:
        return False
    return key(first) == key(second)
