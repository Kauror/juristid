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
    if request is None or matter is None:
        return Continuations()
    return continuations_for(matter, viewer_for(request))
