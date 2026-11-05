"""«Jätkub teemas 2025_196» under a register row's title, batched per table.

`matters/partials/matter_table.html` is drawn by Teemad, Minu asjad and
Saabunud, each with rows fetched its own way. Deciding at the table — one call
for the rows on screen and the «Näita veel» group together — keeps every
surface right without each view remembering to build a mapping, and keeps the
cost at one scoped batch per table instead of a query per row
(`app/matters/continuation_lines.py`).

The reader is whoever this request is authorized as (`viewer_for`), the same
scope the Teema page's own continuation line reads. Without a request in the
context nothing is named.
"""

from __future__ import annotations

from itertools import chain
from typing import Any

from django import template

from app.core.decorators import viewer_for
from app.matters.continuation_lines import ContinuationLine, continuation_lines

register = template.Library()


@register.simple_tag(takes_context=True)
def continuation_lines_for(context: Any, *groups: Any) -> dict[Any, ContinuationLine]:
    """``{pk: ContinuationLine}`` for the continued rows of every group given."""
    request = context.get("request")
    if request is None:
        return {}
    rows = list(chain.from_iterable(group for group in groups if group))
    return continuation_lines(rows, viewer_for(request))
