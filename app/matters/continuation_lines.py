"""Where a continued Teema's work went, for every row of one list page.

A list row whose work status is «Jätkub mujal» says *that* the work continued.
This answers *where*, for a whole page of rows at once: «Jätkub teemas 2025_196»
with the reference linked, and — only when it is known and is not the direct
successor itself — the endpoint of the chain («→ … 2026_190 (avatud)»).

The same rules as the Teema page (`app/matters/continuations.py`, docs/adr/0207),
read in batches instead of per Matter (the later round 0207 §8 left lists for):

* `Matter.superseded_by` is the only edge. Nothing is inferred.
* Every step is re-read through `Matter.objects.visible_to(viewer)`. A successor
  this reader may not read — restricted, deleted, or any Teema at all for an
  administrator without a grant (docs/adr/0181) — is never named: the row keeps
  the plain «Jätkub mujal» and gains no reference, no link and no title.
* The endpoint is wherever the forward walk ends by graph direction. It is shown
  only when the walk ended on its own: a step the reader may not read, a cycle or
  `MAX_HOPS` leaves the immediate successor alone on the line.

**Cost.** One scoped query for the direct successors of every continued row on
the page, whatever the number of rows. Endpoints add one query per further
level of the deepest chain on the page (at most `MAX_HOPS` - 1), never one per
row: each level is fetched for the whole batch.

A navigation label only. The work status badge is derived from the row's own
columns (`app/matters/work_status.py`) and nothing here changes it.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from typing import Any

from app.matters.continuations import MAX_HOPS
from app.matters.models import Matter
from app.matters.work_status import CONTINUES, work_status_of

#: The longest title a reference-less stage is named by before it is cut.
LABEL_CHARACTERS = 40


def stage_label(matter: Matter) -> str:
    """A stage by its reference («2025_196»), or a short title when it has none."""
    reference = matter.display_reference
    if reference:
        return reference
    title = matter.title or ""
    return title if len(title) <= LABEL_CHARACTERS else f"{title[: LABEL_CHARACTERS - 1]}…"


@dataclass(frozen=True)
class ContinuationLine:
    """What one continued row may say about where its work went."""

    #: The direct successor, when this reader may read it. ``None`` is the
    #: non-disclosing fallback: the row says «Jätkub mujal» and nothing else.
    successor: Matter | None = None
    #: The last stage of the chain, when it is known and is not `successor`.
    endpoint: Matter | None = None
    #: Readable stages between `successor` and `endpoint` («…» when any).
    skipped: int = 0

    @property
    def successor_label(self) -> str:
        return stage_label(self.successor) if self.successor else ""

    @property
    def endpoint_label(self) -> str:
        return stage_label(self.endpoint) if self.endpoint else ""

    @property
    def endpoint_state(self) -> str:
        if self.endpoint is None:
            return ""
        return "avatud" if self.endpoint.is_open else "suletud"


#: The fallback, shared: it carries nothing about any other Matter.
UNNAMED = ContinuationLine()


def continuation_lines(matters: Iterable[Any], viewer: Any) -> dict[Any, ContinuationLine]:
    """``{row pk: ContinuationLine}`` for every «Jätkub mujal» row in `matters`.

    Rows with any other work status are absent, so a template asking for one
    gets nothing and draws nothing.
    """
    rows = [m for m in matters if isinstance(m, Matter) and work_status_of(m) is CONTINUES]
    if not rows:
        return {}
    successor_ids = {
        m.superseded_by_id for m in rows if m.superseded_by_id and m.superseded_by_id != m.pk
    }
    readable = Matter.objects.visible_to(viewer)
    known: dict[Any, Matter] = {}
    unreadable: set[Any] = set()

    frontier = successor_ids
    for _level in range(MAX_HOPS):
        if not frontier:
            break
        found = {m.pk: m for m in readable.filter(pk__in=frontier)}
        unreadable |= frontier - found.keys()
        known.update(found)
        frontier = {
            m.superseded_by_id
            for m in found.values()
            if m.superseded_by_id
            and m.superseded_by_id not in known
            and m.superseded_by_id not in unreadable
        }

    lines: dict[Any, ContinuationLine] = {}
    for row in rows:
        successor = known.get(row.superseded_by_id) if row.superseded_by_id else None
        if successor is None:
            lines[row.pk] = UNNAMED
            continue
        lines[row.pk] = _line(row, successor, known)
    return lines


def _line(row: Matter, successor: Matter, known: dict[Any, Matter]) -> ContinuationLine:
    """Follow the chain in memory; name the endpoint only when the walk ended on its own."""
    seen = {row.pk, successor.pk}
    path = [successor]
    next_id = successor.superseded_by_id
    while next_id is not None:
        step = known.get(next_id)
        if next_id in seen or len(path) >= MAX_HOPS or step is None:
            # A cycle, the bound, or a step this reader may not read (or one the
            # batch did not reach): the last shown stage is not the endpoint.
            return ContinuationLine(successor=successor)
        path.append(step)
        seen.add(step.pk)
        next_id = step.superseded_by_id
    if len(path) == 1:
        return ContinuationLine(successor=successor)
    return ContinuationLine(successor=successor, endpoint=path[-1], skipped=len(path) - 2)
