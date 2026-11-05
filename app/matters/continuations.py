"""A Matter's continuation chain, as far as one reader may follow it (docs/adr/0207).

`Matter.superseded_by` is the only continuation relationship there is: one
nullable self-reference, so a Matter continues as at most one other Matter, and
any number may continue as this one (converging predecessors). Nothing here
stores, infers or repairs an edge. Titles, a shared OneNote page, dates and the
sentence «Jätkub teema … all» are never read as one.

**A reader who may not read the Matter gets nothing** (QA-06): every public
helper here answers empty, not even a flag, for a Matter outside the reader's
scope. Every step is re-read through `Matter.objects.visible_to(viewer)`. Each
end may be restricted while this Matter is not, and a deleted Matter is not
readable. So a step the reader may not read stops the walk and is reported only
as a yes/no *something there* (`hidden_after`, `hidden_before`,
`hidden_predecessors`), never by count, reference or title (QA-05).

The walks are bounded (`MAX_HOPS`, `MAX_HISTORY`, `MAX_PREDECESSORS`) and
remember where they have been. A cycle — which the database does not forbid
beyond a self-reference, and `close_matter` refuses since QA-03 — ends the walk
with `cyclic` set instead of looping. The endpoint is wherever the forward walk
actually ends, by graph direction, never the newest year or reference.

**One backward walk per Matter instance and reader** (QA-10). The header
(`continuations_for`), «Seotud materjalid» (`historical_predecessors`) and the
suggestion exclusion all need the same earlier history, so it is walked once —
one id-only query per level, every live predecessor marked readable or not in
the same query — and kept on the Matter instance, keyed on the reader's
identity and on the columns a write to this Matter would move. Never a global
or cross-user cache: another reader, or another instance, walks again.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from django.db.models import Exists, F, OuterRef, Q

from app.matters.models import Matter

#: How far a chain is followed in either direction. Real chains are two or three
#: stages; anything longer is reported as truncated rather than walked.
MAX_HOPS = 12

#: How many earlier stages «Seotud materjalid» lists at most. Real histories are a
#: handful; a larger graph is cut, and says so, instead of being walked to the end.
MAX_HISTORY = 60

#: How many direct predecessors the header, the rail and MCP name at most
#: (QA-08). Real Teemad have one or two; more than this is cut with
#: `more_predecessors` set, and the full list lives under «Varasemad etapid».
MAX_PREDECESSORS = 12


@dataclass(frozen=True)
class Continuations:
    """What one reader may know about where a Matter came from and where it went."""

    #: Readable Matters that continue *as* this one (their `superseded_by` is
    #: this Matter), ordered by reference, at most `MAX_PREDECESSORS`. Several
    #: is a converging history.
    predecessors: tuple[Matter, ...] = ()
    #: There are more readable direct predecessors than `predecessors` names.
    more_predecessors: bool = False
    #: A direct predecessor exists that this reader may not read. Yes or no,
    #: never a count (QA-05).
    hidden_predecessors: bool = False
    #: The direct successor, when readable.
    successor: Matter | None = None
    #: Forward chain after this Matter, nearest first, every step readable: the
    #: successor, its successor, … the endpoint. Empty when there is none.
    after: tuple[Matter, ...] = ()
    #: The forward walk stopped at a step this reader may not read (or one that
    #: no longer exists): «Jätkub teemas, mida siit avada ei saa».
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


# -- the one backward walk ----------------------------------------------------------------

#: One live predecessor as a level query returns it: (pk, successor pk, readable).
_Row = tuple[Any, Any, bool]


@dataclass(frozen=True)
class _History:
    """The earlier history once, for every public helper below (QA-10)."""

    #: The reader may read the Matter itself. False: every helper answers empty.
    readable: bool = False
    #: Readable earlier stages, in walk order, at most `MAX_HISTORY`.
    stages: tuple[EarlierStage, ...] = ()
    cyclic: bool = False
    truncated: bool = False
    #: Each level's live predecessors as the level query returned them (readable
    #: first, then by reference; at most the level's budget + 1). Level 0 is the
    #: Matter's own direct predecessors.
    levels: tuple[tuple[_Row, ...], ...] = ()
    #: A live direct predecessor exists that this reader may not read, as far
    #: as the first level shows: when readable rows alone filled its budget, the
    #: answer is left to `_hidden_direct` (asked only by `continuations_for`).
    hidden_direct: bool = False


def _hidden_direct(matter: Matter, viewer: Any, history: _History) -> bool:
    """Whether an unreadable direct predecessor exists: yes or no (QA-05)."""
    if history.hidden_direct or not history.levels:
        return history.hidden_direct
    if len(history.levels[0]) < MAX_HISTORY + 1:
        return False  # the whole level was read, readable rows first
    # The first level was cut by readable rows alone: ask the one question the
    # cut left open.
    return (
        Matter.objects.filter(superseded_by=matter.pk)
        .exclude(pk=matter.pk)
        .exclude(pk__in=Matter.objects.visible_to(viewer).values("pk"))
        .exists()
    )


def _history_key(matter: Matter) -> tuple[Any, ...]:
    return (matter.pk, matter.superseded_by_id, matter.is_open)


def _history(matter: Matter, viewer: Any) -> _History:
    """The walk, memoized on this Matter instance for this reader (QA-10).

    The key is the reader *object* (identity, never equality: two users with one
    primary key are still asked separately) plus the columns a write to this
    Matter in the same request would change — so a closure or reopening that
    re-renders the page is never answered from before it. A write to *another*
    Matter in the same request after this one was read is not seen; no request
    does that and then re-reads this instance's history.
    """
    memo: list[tuple[Any, tuple[Any, ...], _History]] = matter.__dict__.setdefault(
        "_continuation_history_memo", []
    )
    key = _history_key(matter)
    for reader, seen_key, history in memo:
        if reader is viewer and seen_key == key:
            return history
    history = _walk_back(matter, viewer)
    memo.append((viewer, key, history))
    return history


def forget_continuation_history(matter: Matter) -> None:
    """Drop the memoised history on this instance (after a write that changes it)."""
    matter.__dict__.pop("_continuation_history_memo", None)


def _level(frontier: list[Any], readable: Any, limit: int) -> list[_Row]:
    """Every live Matter whose successor is in `frontier`, readable ones first.

    Ids and one readability flag only: nothing of an unreadable row is loaded.
    Limited at the database (QA-08), ordered so the cut keeps the readable rows
    by reference — the order the stages were always cut in.
    """
    return list(
        Matter.objects.filter(superseded_by__in=frontier)
        .exclude(pk__in=frontier)
        .annotate(visible_to_reader=Exists(readable.filter(pk=OuterRef("pk"))))
        .order_by(F("visible_to_reader").desc(), "reference_year", "reference_number", "pk")
        .values_list("pk", "superseded_by", "visible_to_reader")[:limit]
    )


def _walk_back(matter: Matter, viewer: Any) -> _History:
    """Breadth-first against `superseded_by`, one query per level.

    Each level holds every live Matter whose successor is in the current frontier,
    marked readable or not. Only readable rows become stages and extend the
    frontier: what lies behind an unreadable stage would be read through a door
    the reader may not open, and a deleted stage is not live at all, so the
    history behind it is not reached either. Converging branches are all
    followed.

    Bounded twice. `MAX_HOPS` levels are listed; one more level is read only to
    tell «there is more» (`truncated`) from «it comes back here» (`cyclic`,
    QA-09: a cycle of exactly MAX_HOPS + 1 stages meets the viewed Matter on
    that level). `MAX_HISTORY` stages are listed and each level query asks the
    database for no more than the remaining budget + 1 rows.

    A row met again is the viewed Matter itself: each Matter has one successor,
    so the only way back into the walk is a cycle through the start.
    """
    readable = Matter.objects.visible_to(viewer)
    if not readable.filter(pk=matter.pk).exists():
        return _History()
    seen: set[Any] = {matter.pk}
    frontier: list[Any] = [matter.pk]
    #: (pk, depth, successor pk) of each readable stage, in walk order.
    stages: list[tuple[Any, int, Any]] = []
    levels: list[tuple[_Row, ...]] = []
    cyclic = truncated = False
    depth = 0
    while frontier:
        depth += 1
        limit = MAX_HISTORY - len(stages) + 1
        rows = _level(frontier, readable, limit)
        levels.append(tuple(rows))
        if depth > MAX_HOPS:
            # The level beyond the bound: read only to say why the walk stopped.
            if any(pk in seen for pk, _successor, ok in rows if ok):
                cyclic = True
            if any(pk not in seen for pk, _successor, ok in rows if ok):
                truncated = True
            break
        next_frontier: list[Any] = []
        for pk, successor, ok in rows:
            if not ok:
                continue
            if pk in seen:
                cyclic = True
                continue
            if len(stages) >= MAX_HISTORY:
                truncated = True
                break
            seen.add(pk)
            stages.append((pk, depth, successor))
            next_frontier.append(pk)
        if truncated:
            break  # cut at MAX_HISTORY: nothing further can be listed
        frontier = next_frontier

    hidden_direct = bool(levels) and any(not ok for _pk, _successor, ok in levels[0])

    # One query for every readable stage's row, instead of one per level.
    loaded = {row.pk: row for row in readable.filter(pk__in=[pk for pk, _d, _s in stages])}
    return _History(
        readable=True,
        stages=tuple(
            EarlierStage(matter=loaded[pk], depth=at, continues_as=successor)
            for pk, at, successor in stages
            if pk in loaded
        ),
        cyclic=cyclic,
        truncated=truncated,
        levels=tuple(levels),
        hidden_direct=hidden_direct,
    )


def _by_reference(matter: Matter) -> tuple[Any, ...]:
    return (matter.reference_year or 0, matter.reference_number or 0, str(matter.pk))


# -- the public helpers ------------------------------------------------------------------


def continuations_for(matter: Matter | None, viewer: Any) -> Continuations:
    """The reader's view of `matter`'s continuation chain, in both directions.

    Empty — no stage, no flag — when `viewer` may not read `matter` (QA-06).
    """
    if matter is None or matter.pk is None:
        return Continuations()
    history = _history(matter, viewer)
    if not history.readable:
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

    # Backward, from the one walk: the direct predecessors, then the linear part.
    stage_rows = {stage.matter.pk: stage.matter for stage in history.stages}
    direct = sorted(
        (stage.matter for stage in history.stages if stage.depth == 1), key=_by_reference
    )
    predecessors = direct[:MAX_PREDECESSORS]
    more_predecessors = len(direct) > MAX_PREDECESSORS
    hidden_predecessors = _hidden_direct(matter, viewer, history)

    before: list[Matter] = []
    hidden_before = hidden_predecessors
    index = 0
    level = history.levels[0] if history.levels else ()
    # On the linear part every level so far held exactly one row, so the next
    # level is that row's predecessors and nobody else's.
    while len(level) == 1 and level[0][2]:
        nearest = level[0][0]
        if nearest in seen:
            cyclic = True
            break
        if len(before) >= MAX_HOPS:
            truncated = True
            break
        row = stage_rows.get(nearest)
        if row is None:  # gone between the level query and the row read
            break
        before.append(row)
        seen.add(nearest)
        index += 1
        level = history.levels[index] if index < len(history.levels) else ()
        if any(not ok for _pk, _successor, ok in level):
            hidden_before = True
            break
    before.reverse()
    return Continuations(
        predecessors=tuple(predecessors),
        more_predecessors=more_predecessors,
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


def historical_predecessors(matter: Matter | None, viewer: Any) -> EarlierStages:
    """Every earlier stage of `matter` this reader may read, through every branch.

    Breadth-first against `Matter.superseded_by` (`_walk_back`): every live Matter
    whose successor is in the current frontier, each level re-read through
    `visible_to(viewer)`. A stage the reader may not read is neither shown nor
    counted, and the walk does not pass through it. A deleted stage is not live,
    so the history behind it is not reached either. Converging branches are all
    followed: several Matters may continue as the same one. Empty when `viewer`
    may not read `matter` (QA-06).

    Ordered earliest first by depth (furthest back first), then by reference, for
    presentation only: the relationship comes from the edges alone.
    """
    if matter is None or matter.pk is None:
        return EarlierStages()
    history = _history(matter, viewer)
    if not history.readable:
        return EarlierStages()
    found = sorted(history.stages, key=lambda s: (-s.depth, *_by_reference(s.matter)))
    return EarlierStages(stages=tuple(found), cyclic=history.cyclic, truncated=history.truncated)


def continuation_component_ids(matter_id: Any) -> set[Any]:
    """Every live Matter on `matter_id`'s continuation history, both directions, all branches.

    Unscoped ids for exclusion only (the suggestion engine scopes its own pool):
    an established continuation, however many steps away, is never offered again
    as an uncertain «Võimalik seos». Bounded like the walks above; the Matter
    itself is included.

    **Bounded, and the bound is documented rather than infinite** (QA-02): at
    most `MAX_HOPS * 2` levels and about `MAX_HISTORY * 2` Matters. A stage the
    page *shows* is excluded whatever this walk reaches, because
    `confirmed_continuation_ids` adds every shown stage by construction (QA-01);
    what this walk adds is the rest of the component — siblings and stages
    beyond the page's own bounds — as far as its budget goes.

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


def confirmed_continuation_ids(matter: Matter, viewer: Any) -> set[Any]:
    """Every Matter that must never come back as an uncertain suggestion (QA-01, QA-02).

    The union of three answers, so that nothing this reader is *shown* as a
    confirmed stage can also be offered as «Võimalik seos», at any size:

    * every stage `historical_predecessors(matter, viewer)` lists («Varasemad
      etapid»), which includes the header's direct predecessors and its linear
      earlier chain;
    * every stage of the readable forward chain (`continuations_for(...).after`);
    * the unscoped component walk (`continuation_component_ids`), for the
      siblings and stages beyond the page's bounds.

    Bounded: the two scoped walks are the page's own (memoized, so the page
    pays for them once), and the component walk has its own budget. Ids for
    exclusion only; the Matter itself is included.
    """
    ids = continuation_component_ids(matter.pk)
    ids.update(stage.matter.pk for stage in historical_predecessors(matter, viewer).stages)
    chain = continuations_for(matter, viewer)
    ids.update(step.pk for step in chain.after)
    ids.update(step.pk for step in chain.predecessors)
    return ids
