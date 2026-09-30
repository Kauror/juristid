"""A web address as a person types it, and the one rule for accepting it.

**One implementation of «a public http(s) address, or nothing»** for every
ordinary web-address box in the product: `Menetluse link`, a `Kaasamine`'s
`Smaily` and `Alchemer` links, `Teiste arvamus` and `Meile saadetud tagasiside`,
`Ülevaade / uudis`, a working document's SharePoint address, and the
`Ametlik allikas` / `Viide` of a `Jõustumine` or a `Töövõit`. It began as
`app.matters.services._normalize_public_link` and moved here when the last three
needed it too, because the rule is not the kind that may exist twice: it is the
difference between a clickable control on a page a lawyer trusts and a
script-delivery vector, and a second copy is a second place for `javascript:`
to be forgotten.

**Nobody has to type `https://`** (docs/adr/0121 §5). `www.delfi.ee`,
`delfi.ee` and `delfi.ee/uudised?id=123` are what people copy out of an address
bar or say aloud, and refusing them for want of a scheme was friction with no
safety in it. `with_web_scheme` adds `https://` to an address that **looks like
a host** and has no scheme of its own; everything else is left exactly as typed
and judged by the same rule as before. So:

* `https://www.delfi.ee` and `http://example.com` are returned unchanged — a
  scheme somebody wrote is never replaced or upgraded;
* `delfi.ee/uudised` becomes `https://delfi.ee/uudised`, and never
  `https://https://…`, because only an address with no scheme is touched;
* `kampaania`, `ei ole aadress`, `mailto:info@koda.ee` and `javascript:…` are
  not addresses and are still refused. Arbitrary text is not turned into a URL:
  the part before the first `/`, `?` or `#` has to be a dotted host name ending
  in a letter-only top-level label (optionally with a port), and nothing in the
  value may be whitespace.

The inference is shape-only and offline — nothing here resolves a name or
fetches a page.
"""

from __future__ import annotations

import re
from urllib.parse import urlsplit

from app.core.errors import DomainError

#: The only schemes a web address may use.
#:
#: Not a general URL policy — a narrow allow-list for fields that render as a
#: clickable control. `javascript:` and `data:` are script delivery dressed as an
#: address; `file:` and `ftp:` point somewhere the reader's browser cannot
#: usefully follow. Nothing checks whether the far end is alive: a link recorded
#: in 2019 whose campaign has since been archived is still a true record of what
#: the Chamber did (Agent-F brief 12).
WEB_SCHEMES: frozenset[str] = frozenset({"http", "https"})

#: The scheme a bare host is given. HTTPS, because that is what every site a
#: lawyer copies a link from serves today, and a browser offered `http://` for
#: one of them is redirected anyway.
DEFAULT_SCHEME = "https"

#: One DNS label: letters or digits at both ends, hyphens (and, for the odd
#: internal host, underscores) between. Unicode letters count, so `õigus.ee`
#: is a host and not a refusal.
_LABEL = r"[^\W_](?:[\w-]{0,61}[^\W_])?"
#: The top-level label: letters only — `ee`, `eu`, `com` — or an IDNA `xn--`
#: form. A final label of digits is an IP address, which a person types with its
#: scheme or not at all; a bare word with no dot at all is not a host.
_TOP_LABEL = r"(?:[^\W\d_]{2,63}|xn--[a-z0-9-]{1,59})"
_BARE_HOST = re.compile(rf"^(?:{_LABEL}\.)+{_TOP_LABEL}\.?(?::\d{{1,5}})?$", re.IGNORECASE)
_WHITESPACE = re.compile(r"\s")


def with_web_scheme(value: str | None) -> str:
    """``value`` stripped, with ``https://`` in front when it is a bare host.

    Returns everything else unchanged — including values that are not addresses
    at all, which the caller's rule then refuses in its own words. Idempotent:
    a value that already carries a scheme is never touched again.
    """
    text = (value or "").strip()
    if not text or _WHITESPACE.search(text):
        return text
    host = re.split(r"[/?#]", text, maxsplit=1)[0]
    if _BARE_HOST.match(host):
        return f"{DEFAULT_SCHEME}://{text}"
    return text


def normalize_web_address(
    value: str | None,
    *,
    max_length: int | None,
    reject_credentials: bool = False,
    not_a_url: str = "Link peab sisaldama veebiaadressi.",
    not_web_scheme: str = "Link peab algama http:// või https:// aadressiga.",
    has_credentials: str = "Link ei tohi sisaldada kasutajanime ega parooli.",
    too_long: str | None = None,
) -> str:
    """The stored form of a web address, or a `DomainError` in the caller's words.

    Empty stays empty — whether an address is *required* is each record's own
    question. Otherwise: a bare host gains ``https://`` (`with_web_scheme`), the
    scheme must be `http` or `https`, there must be a parsed **host** (not
    merely an authority — `https://user:pw@/x` has one and no host) with no
    whitespace in it, credentials are refused where the caller says so, and an
    address longer than ``max_length`` is **refused, never truncated**: a link
    cut off at the column width no longer resolves (red-team findings F-1, F-2).
    ``max_length=None`` leaves the length to the caller, which one of them words
    with the measured length.

    ``reject_credentials`` is off by default and the default is the older
    behaviour, deliberately. `https://user:pw@host/` has a perfectly good host,
    and for a campaign address out of the historical register — which this
    department did not choose and cannot re-issue — refusing it would mean
    refusing to record what actually happened. An address somebody is pasting
    *now*, from a page they have open, has no such history to accommodate
    (docs/adr/0081 §3, kept by docs/adr/0085 §2).

    The refusal sentences are parameters because several records share a Teema
    page, and a refusal that does not say *which* link it means has to be
    located before it can be acted on.
    """
    url = with_web_scheme(value)
    if not url:
        return ""
    try:
        parts = urlsplit(url)
    except ValueError:
        raise DomainError(not_a_url) from None
    if parts.scheme.lower() not in WEB_SCHEMES:
        raise DomainError(not_web_scheme)
    try:
        hostname = parts.hostname
        username = parts.username
        password = parts.password
    except ValueError:
        # A malformed authority — an unbracketed IPv6 literal, a port that is
        # not a number. A refusal, not an unhandled exception from a parser.
        hostname = None
        username = password = None
    if reject_credentials and (username or password):
        raise DomainError(has_credentials)
    if not hostname or _WHITESPACE.search(hostname):
        raise DomainError(not_a_url)
    if max_length is not None and len(url) > max_length:
        raise DomainError(
            too_long
            or (
                f"Link on liiga pikk — kuni {max_length} tähemärki. "
                "Lühenda aadressi või salvesta see märkusesse."
            )
        )
    return url
