"""A link to one `Tegevused` row, and the row a save just wrote.

**A link to a row** is the Teema page with ``?kirje=<row key>`` and the row's
own anchor (``#kaik-<row key>-rida``). The key is `TimelineItem.row_key`: the
record's own id where the row stands for a record, else the id of the entry or
event it was sorted by. Every one of those is an id the row keeps for good, so a
link copied today still names the same row after a hundred more are recorded —
nothing here reads a position, a page number or a time.

`Tegevused` shows its newest rows (`TIMELINE_PAGE_SIZE`, or the grouped
chronology's `GROUPED_WINDOW`) and older ones on request. A link to a row behind
that window would open the page and scroll nowhere, so the page asks once, up
to :data:`LOCATE_LIMIT` rows deep, **through the same reader-scoped chronology
the page renders** (`matter_timeline`), where the row stands — and then shows
enough rows to include it. Never more than that limit: an older row is one
«Näita kõik tegevused» away, as it always was.

**An unknown key and a key this reader may not see are the same answer.** A
restricted record contributes no row to this reader's chronology, so it cannot
be found in it; the page then renders exactly as it does with no key at all.
Nothing on the page — no message, no different window — says whether the id
exists.

**The row a save wrote** is marked on the answer to that save and nowhere
else: the rows whose key is the id of a record, an entry or an event that
*this request* wrote (`ChangeEvent.correlation_id`, one value per request).
Only on a successful POST — a refused save rendered nothing new — and only
on rows the reader is already shown, so the marker discloses nothing.
"""

from __future__ import annotations

import re
from typing import Any

from django.http import HttpRequest

from app.audit.models import ChangeEvent
from app.audit.provenance import current_correlation_id
from app.matters.timeline import TimelineItem, matter_timeline

#: The query parameter naming the row a link points at.
ROW_PARAM = "kirje"

#: How deep the page looks for a linked row before giving up: ten of the flat
#: chronology's pages. Bounded so that a link — or a made-up key — can never
#: make the page read a fifteen-year history; past it the row is still one
#: «Näita kõik tegevused» away.
LOCATE_LIMIT = 300

#: A row key as `TimelineItem.row_key` spells it: a UUID, or a historical
#: import's `onenote:day:2026-01-31` / `onenote:undated`. Anything else is not a
#: key this page ever printed, and is ignored before a query is made.
_ROW_KEY = re.compile(
    r"^(?:[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}|onenote:[0-9a-z:-]{1,40})$"
)


def requested_row(request: HttpRequest) -> str:
    """The row key a full-page GET asked for, or ``""``."""
    if request.method != "GET":
        return ""
    value = (request.GET.get(ROW_PARAM) or "").strip().lower()
    return value if _ROW_KEY.match(value) else ""


def locate_row(
    *,
    matter: Any,
    user: Any,
    key: str,
    only: str,
    intelligence: Any,
    current_action: Any,
) -> tuple[list[TimelineItem], bool, int | None]:
    """Read up to :data:`LOCATE_LIMIT` rows and say where ``key`` stands among them.

    Returns the rows read, whether more exist beyond them, and the row's index
    — ``None`` where this reader's chronology has no such row within the limit.
    """
    items, more = matter_timeline(
        matter=matter,
        user=user,
        limit=LOCATE_LIMIT,
        only=only,
        intelligence=intelligence,
        current_action=current_action,
    )
    index = next((i for i, item in enumerate(items) if item.row_key == key), None)
    return items, more, index


def window_reaching(index: int, page: int) -> int:
    """The smallest whole number of pages that includes the row at ``index``."""
    return (index // page + 1) * page


def saved_row_keys(request: HttpRequest, matter: Any, status: int) -> frozenset[str]:
    """The ids this request wrote on ``matter``, for marking the rows they draw.

    Empty for anything but a successful POST. One query, on an indexed column;
    the ids are compared against rows already rendered for this reader and
    never printed themselves.
    """
    if request.method != "POST" or not 200 <= status < 300:
        return frozenset()
    correlation = current_correlation_id()
    if correlation is None:
        return frozenset()
    keys: set[str] = set()
    for event_id, object_id in ChangeEvent.objects.filter(
        matter=matter, correlation_id=correlation
    ).values_list("id", "object_id"):
        keys.add(str(event_id))
        if object_id is not None:
            keys.add(str(object_id))
    return frozenset(keys)
