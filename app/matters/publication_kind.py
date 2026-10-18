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

from typing import Any
from urllib.parse import urlsplit

from app.matters.enums import WebsiteOverviewKind, WebsiteOverviewStatus

KODA_HOSTS = frozenset({"koda.ee", "www.koda.ee"})

OVERVIEW_PATHS = (
    "/et/meie-moju/hetkel-kasil",
    "/en/current-drafts",
    "/ru/current-drafts",
    "/meie-moju/hetkel-kasil",
)
NEWS_PATHS = ("/et/uudised", "/en/news", "/ru/novosti", "/uudised")

#: Refused in a round's `Ülevaate link`: a news item is not the round's
#: overview. Stated here, beside the rule that tells the two apart, so the form
#: and the use case (`workspace._overview_for_engagement_link`) refuse in the
#: same words without the service reaching into `matters.forms`.
ENGAGEMENT_OVERVIEW_LINK_IS_NEWS = (
    "See on uudise aadress. Ülevaate link on koda.ee «Hetkel käsil» lehe aadress."
)


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


def overview_fulfils_step(overview: Any) -> bool:
    """Whether this write-up is the published `Ülevaade` a «Koosta kodulehe
    ülevaade» step asks for — the one answer for the step's own save
    (docs/adr/0168 §3) and for the record that proves it done (docs/adr/0161).

    Read on ``effective_kind``, the kind the page shows: a row from before the
    kind was stored is what its koda.ee address says, so a legacy news item
    printed as «Uudis» does not count as the overview. Where the address says
    nothing either, the row reads as an overview — which is what every one of
    them was filed as.
    """
    return overview.status == WebsiteOverviewStatus.PUBLISHED and overview.effective_kind in (
        WebsiteOverviewKind.OVERVIEW,
        "",
    )
