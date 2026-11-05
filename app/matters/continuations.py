"""A Matter's continuation chain, as far as one reader may follow it (docs/adr/0207).

`Matter.superseded_by` is the only continuation relationship there is: one
nullable self-reference, so a Matter continues as at most one other Matter, and
any number may continue as this one (converging predecessors). Nothing here
stores, infers or repairs an edge. Titles, a shared OneNote page, dates and the
sentence «Jätkub teema … all» are never read as one.

Every step is re-read through `Matter.objects.visible_to(viewer)`. Each end may
be restricted while this Matter is not, and a deleted Matter is not readable.
So a step the reader may not see stops the walk and is reported only as
*something there* (`hidden_after`, `hidden_before`), never by reference or title.

The walk is bounded (`MAX_HOPS`) and remembers where it has been. A cycle — which
the database does not forbid beyond a self-reference — ends the walk with
`cyclic` set instead of looping. The endpoint is wherever the forward walk
actually ends, by graph direction, never the newest year or reference.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from app.matters.models import Matter

#: How far a chain is followed in either direction. Real chains are two or three
#: stages; anything longer is reported as truncated rather than walked.
MAX_HOPS = 12


@dataclass(frozen=True)
class Continuations:
    """What one reader may know about where a Matter came from and where it went."""

    #: Readable Matters that continue *as* this one (their `superseded_by` is
    #: this Matter), ordered by reference. Several is a converging history.
    predecessors: tuple[Matter, ...] = ()
    #: Predecessors that exist but this reader may not read. Counted, never named.
    hidden_predecessors: int = 0
    #: The direct successor, when readable.
    successor: Matter | None = None
    #: Forward chain after this Matter, nearest first, every step readable: the
    #: successor, its successor, … the endpoint. Empty when there is none.
    after: tuple[Matter, ...] = ()
    #: The forward walk stopped at a step this reader may not read (or one that
    #: no longer exists): «jätkub teemas, mida sa ei näe».
    hidden_after: bool = False
    #: Backward chain before this Matter, earliest first, followed only while
    #: every step has exactly one predecessor and the reader may read it. A
    #: converging step ends it; `predecessors` still lists the direct ones.
    before: tuple[Matter, ...] = ()
    hidden_before: bool = False
    #: The walk met a Matter it had already visited.
    cyclic: bool = False
    #: The walk reached `MAX_HOPS` in either direction.
    truncated: bool = False

    def __bool__(self) -> bool:
        return bool(
            self.predecessors or self.hidden_predecessors or self.after or self.hidden_after
        )

    @property
    def endpoint(self) -> Matter | None:
        """The last readable stage of the forward chain, or ``None`` without one."""
        return self.after[-1] if self.after else None

    @property
    def has_chain(self) -> bool:
        """More than one hop in either direction: worth printing the whole path."""
        return len(self.after) > 1 or len(self.before) > 1

    @property
    def stage_count(self) -> int:
        """Readable stages on the linear path through this Matter, itself included."""
        return len(self.before) + 1 + len(self.after)

    @property
    def shows_path(self) -> bool:
        """Three or more readable stages: print them in order, not as two neighbours.

        `has_chain` plus the Matter in the middle of a three-stage chain, which
        has one hop each way and is still a chain to whoever opens it.
        """
        return self.stage_count > 2

    @property
    def endpoint_is_known(self) -> bool:
        """`endpoint` really is where the work ends, as far as anybody can tell.

        Not when the walk stopped at a step this reader may not read, met a
        cycle or hit `MAX_HOPS`: the last readable stage is then only the last
        one *shown*, and calling it the endpoint would be a guess.
        """
        return bool(self.after) and not (self.hidden_after or self.cyclic or self.truncated)

    @property
    def hop_limit(self) -> int:
        """`MAX_HOPS`, for the sentence that says a chain was cut."""
        return MAX_HOPS

    # Kept for the rail's existing template names (`earlier`, `later`).
    @property
    def earlier(self) -> tuple[Matter, ...]:
        return self.predecessors

    @property
    def later(self) -> Matter | None:
        return self.successor


def continuations_for(matter: Matter | None, viewer: Any) -> Continuations:
    """The reader's view of `matter`'s continuation chain, in both directions."""
    if matter is None or matter.pk is None:
        return Continuations()
    readable = Matter.objects.visible_to(viewer)
    seen = {matter.pk}
    cyclic = truncated = False

    # Forward: superseded_by is single-valued, so the path is linear.
    after: list[Matter] = []
    hidden_after = False
    next_id = matter.superseded_by_id
    while next_id is not None:
        if next_id in seen:
            cyclic = True
            break
        if len(after) >= MAX_HOPS:
            truncated = True
            break
        step = readable.filter(pk=next_id).first()
        if step is None:
            hidden_after = True
            break
        after.append(step)
        seen.add(step.pk)
        next_id = step.superseded_by_id

    # Backward: the direct predecessors, then the linear part of the history.
    def direct(of: Any) -> tuple[list[Matter], int]:
        # Live Matters only: a deleted predecessor is not history this page can offer.
        everyone = (
            Matter.objects.filter(superseded_by=of).exclude(pk=of).values_list("pk", flat=True)
        )
        ids = set(everyone)
        if not ids:
            return [], 0  # the usual answer, and no second query to reach it
        visible = sorted(
            readable.filter(pk__in=ids),
            key=lambda m: (m.reference_year or 0, m.reference_number or 0, str(m.pk)),
        )
        return visible, len(ids) - len(visible)

    predecessors, hidden_predecessors = direct(matter.pk)
    before: list[Matter] = []
    hidden_before = hidden_predecessors > 0
    current = predecessors[0] if len(predecessors) == 1 and not hidden_predecessors else None
    while current is not None:
        if current.pk in seen:
            cyclic = True
            break
        if len(before) >= MAX_HOPS:
            truncated = True
            break
        before.append(current)
        seen.add(current.pk)
        earlier, hidden = direct(current.pk)
        if hidden:
            hidden_before = True
            break
        current = earlier[0] if len(earlier) == 1 else None
    before.reverse()
    return Continuations(
        predecessors=tuple(predecessors),
        hidden_predecessors=hidden_predecessors,
        successor=after[0] if after else None,
        after=tuple(after),
        hidden_after=hidden_after,
        before=tuple(before),
        hidden_before=hidden_before,
        cyclic=cyclic,
        truncated=truncated,
    )
