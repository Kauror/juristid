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

from django.db.models import Q

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


# -- every earlier stage, all branches (docs/adr/0209) ----------------------------------

#: How many earlier stages «Seotud materjalid» lists at most. Real histories are a
#: handful; a larger graph is cut, and says so, instead of being walked to the end.
MAX_HISTORY = 60


@dataclass(frozen=True)
class EarlierStage:
    """One readable earlier stage of a Teema, `depth` steps back (1 = direct)."""

    matter: Matter
    depth: int
    #: The stage this one continued as: the path back to the viewed Teema.
    continues_as: Any


@dataclass(frozen=True)
class EarlierStages:
    stages: tuple[EarlierStage, ...] = ()
    #: The walk met a Matter it had already visited, or stopped at MAX_HOPS or
    #: MAX_HISTORY. Nothing beyond is shown; nothing unreadable is counted.
    cyclic: bool = False
    truncated: bool = False

    def __bool__(self) -> bool:
        return bool(self.stages)

    def __len__(self) -> int:
        return len(self.stages)


def historical_predecessors(matter: Matter | None, viewer: Any) -> EarlierStages:
    """Every earlier stage of `matter` this reader may read, through every branch.

    Breadth-first against `Matter.superseded_by`, one query per level: every live
    Matter whose successor is in the current frontier. Each level is re-read
    through `visible_to(viewer)`. A stage the reader may not read is neither shown
    nor counted, and the walk does not pass through it, because what lies behind
    it would be read through a door the reader may not open. A deleted stage is
    not live, so the history behind it is not reached either. Converging branches
    are all followed: several Matters may continue as the same one.

    Ordered earliest first by depth (furthest back first), then by reference, for
    presentation only: the relationship comes from the edges alone.
    """
    if matter is None or matter.pk is None:
        return EarlierStages()
    readable = Matter.objects.visible_to(viewer)
    seen: set[Any] = {matter.pk}
    frontier: list[Any] = [matter.pk]
    found: list[EarlierStage] = []
    cyclic = truncated = False
    depth = 0
    while frontier:
        depth += 1
        if depth > MAX_HOPS:
            # Cut only if there is in fact something beyond the bound.
            truncated = readable.filter(superseded_by__in=frontier).exclude(pk__in=seen).exists()
            break
        rows = list(
            readable.filter(superseded_by__in=frontier)
            .exclude(pk__in=frontier)
            .order_by("reference_year", "reference_number", "pk")
        )
        next_frontier: list[Any] = []
        for row in rows:
            if row.pk in seen:
                cyclic = True
                continue
            if len(found) >= MAX_HISTORY:
                truncated = True
                break
            seen.add(row.pk)
            found.append(EarlierStage(matter=row, depth=depth, continues_as=row.superseded_by_id))
            next_frontier.append(row.pk)
        frontier = next_frontier
    found.sort(
        key=lambda s: (
            -s.depth,
            s.matter.reference_year or 0,
            s.matter.reference_number or 0,
            str(s.matter.pk),
        )
    )
    return EarlierStages(stages=tuple(found), cyclic=cyclic, truncated=truncated)


def continuation_component_ids(matter_id: Any) -> set[Any]:
    """Every live Matter on `matter_id`'s continuation history, both directions, all branches.

    Unscoped ids for exclusion only (the suggestion engine scopes its own pool):
    an established continuation, however many steps away, is never offered again
    as an uncertain «Võimalik seos». Bounded like the walks above; the Matter
    itself is included.

    A deleted stage (docs/adr/0102) is not live and the walk does not pass
    through it, exactly as `historical_predecessors` does not: a successor id is
    only followed onward once its own row is found live, so the other
    predecessors of a deleted Matter are not reached through it.
    """
    seen: set[Any] = {matter_id}
    dead: set[Any] = set()
    frontier: set[Any] = {matter_id}
    for _ in range(MAX_HOPS * 2):
        if not frontier or len(seen) > MAX_HISTORY * 2:
            break
        rows = list(
            Matter.objects.filter(Q(superseded_by__in=frontier) | Q(pk__in=frontier)).values_list(
                "pk", "superseded_by"
            )
        )
        live = {pk for pk, _successor in rows if pk in frontier}
        dead |= frontier - live
        nxt: set[Any] = set()
        for pk, successor in rows:
            if pk in live:
                step = successor  # forward from a live stage
            elif successor in live:
                step = pk  # a live predecessor of a live stage
            else:
                continue  # a predecessor of a deleted stage: not reached through it
            if step is not None and step not in seen:
                nxt.add(step)
        seen |= nxt
        frontier = nxt
    return seen - dead
