"""«Tagasi tulemustesse» — back to the Teemad list a Teema was opened from.

The register keeps everything that shapes it in its address: the search, every
filter, the order, the page size and the page (`hx-push-url` on the live search,
plain links for the rest). So the browser's own Back already returns to the
same list, scrolled where it was. What it cannot do is survive a second step:
after a tab switch on the Teema, or for somebody who looks for a link rather
than a browser button, the filtered list is gone.

The Teema page therefore offers one quiet link back — **only** when the page was
opened from the list, which the browser says in `Referer` (the policy is
`same-origin`, so a same-site navigation carries the whole address and any other
carries none). Nothing is stored anywhere and no parameter is added to the
Teema's own address, so a Teema link somebody copies is the same link it always
was, and an ordinary deep link to a Teema shows no such link at all.

**The return address is rebuilt, never echoed.** It must be on this host, by
http(s), and its path must be exactly the register's; its query is re-encoded
from the parsed parameters. Anything else — another host, `//evil`,
`javascript:`, another page of this site — offers no link.
"""

from __future__ import annotations

from urllib.parse import parse_qsl, urlencode, urlsplit

from django.http import HttpRequest
from django.urls import reverse
from django.utils.http import url_has_allowed_host_and_scheme

#: The longest return address honoured. A register address is a few hundred
#: characters at most; anything longer is not one this site wrote.
MAX_RETURN_LENGTH = 2000


def results_return_url(request: HttpRequest) -> str:
    """The Teemad list address this page was opened from, or ``""``.

    Only a list that was narrowed or moved — a search, a filter, an order, a
    page — earns the link: the bare list is the `Teemad` destination the top
    bar already offers.
    """
    candidate = request.META.get("HTTP_REFERER", "")
    if not candidate or len(candidate) > MAX_RETURN_LENGTH:
        return ""
    if not url_has_allowed_host_and_scheme(
        candidate, allowed_hosts={request.get_host()}, require_https=request.is_secure()
    ):
        return ""
    parts = urlsplit(candidate)
    # A relative referrer is not something a browser sends; an absolute one on
    # this host is the only shape accepted.
    if parts.scheme not in {"http", "https"} or not parts.netloc:
        return ""
    if parts.path != reverse("matters:matter_list"):
        return ""
    # Blank values kept: `olek=` is a choice the register's links make on
    # purpose (every state, rather than the default open ones).
    params = parse_qsl(parts.query, keep_blank_values=True)
    if not params:
        return ""
    return f"{parts.path}?{urlencode(params)}"
