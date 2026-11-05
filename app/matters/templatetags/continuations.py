"""The successor chain, in both directions, as far as this reader may follow it.

`Seotud` on the rail names the Matter this one continued and the Matter it
continues as. Either end may be RESTRICTED while this one is not — a file closed
as superseded is checked against the closing person's sight only
(`close_matter`) — and its title is restricted content. Reading
`matter.superseded_by` and `matter.supersedes` straight off the instance would
print it: a relation cache is not a scope, and `select_related` does not even
pass through the default manager.

So both ends are re-read here through `Matter.objects.visible_to`, for whoever
this request is authorized as (`viewer_for`). A tag rather than a view key,
because the rail is rendered by the whole page and by several HTMX re-renders,
each with a Matter fetched its own way; deciding at the one place it is drawn
keeps every path right without each view having to remember it. Without a
request in the context nothing is shown.

The header, the closed banner and the rail all ask on one page load, so the
answer is kept on the request for that Matter — keyed on the columns a write in
this request would move (`superseded_by`, open or closed), so a save that
re-renders the band after changing the chain is never answered from before it
(docs/adr/0207).
"""

from __future__ import annotations

from typing import Any

from django import template

from app.core.decorators import viewer_for
from app.matters.continuations import Continuations, continuations_for
from app.matters.models import Matter

register = template.Library()


@register.simple_tag(takes_context=True)
def readable_continuations(context: Any, matter: Matter) -> Continuations:
    """The chain as this request's reader may follow it (app/matters/continuations.py)."""
    request = context.get("request")
    if request is None or matter is None or getattr(matter, "pk", None) is None:
        return Continuations()
    cache: dict[tuple[Any, ...], Continuations] | None = getattr(
        request, "_readable_continuations", None
    )
    if cache is None:
        cache = {}
        request._readable_continuations = cache
    key = (matter.pk, matter.superseded_by_id, matter.is_open)
    if key not in cache:
        cache[key] = continuations_for(matter, viewer_for(request))
    return cache[key]


@register.simple_tag
def continuation_label(matter: Matter | None) -> str:
    """A stage by its reference («2025_196»), or a short title when it has none.

    Only ever called with a Matter `readable_continuations` returned (or the
    page's own), so the title is one this reader may read.
    """
    if matter is None:
        return ""
    reference = matter.display_reference
    if reference:
        return reference
    title = matter.title or ""
    return title if len(title) <= 40 else f"{title[:39]}…"
