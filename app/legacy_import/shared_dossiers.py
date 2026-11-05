"""A OneNote page linked to several Teemad, read as one dossier (docs/adr/0208).

The capture import holds every file of such a page (`SKIPPED ·
SHARED_PAGE_AWAITING_REVIEW`, docs/adr/0205 §4): which Teema each file belongs
to is a person's decision. The page itself is not in doubt. It was captured
whole and is readable whole, and very often it *is* one dossier — a Teema that
continued under a new reference (`Matter.superseded_by`) kept writing on the
page it started.

This module says, for one reader, how the page's Teemad relate. It decides
nothing and writes nothing: it does not assign a file, approve a hold, or let a
continuation edge grant a read. Every Teema is read through
`Matter.objects.visible_to(viewer)`; one the reader may not read is counted
(`hidden`), never named. The only edge read is `superseded_by` — a shared page,
a title or a date is never taken for one (docs/adr/0207).

The classes:

* `ONE_CHAIN` — every linked Teema is on one linear continuation chain
  (2025_140 → 2025_196), and no linked Teema is hidden from this reader.
* `PARTIAL_CHAIN` — a chain covers some of the linked Teemad but not all (or
  some are hidden, so «all» cannot be shown to this reader).
* `COMPLEX` — the linked Teemad converge, or a walk met a cycle: not one line.
* `NONE` — no linked Teema continues another, as far as this reader can see.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from app.legacy_import.source_pages import LegacySourcePage, MatterSourcePage
from app.matters.continuations import MAX_HOPS
from app.matters.models import Matter

ONE_CHAIN = "ONE_CHAIN"
PARTIAL_CHAIN = "PARTIAL_CHAIN"
COMPLEX = "COMPLEX"
NONE = "NONE"


def _reference_key(matter: Matter) -> tuple[int, int, str]:
    return (matter.reference_year or 0, matter.reference_number or 0, str(matter.pk))


@dataclass(frozen=True)
class DossierTeema:
    """One readable Teema on the page, with the link through which it reads it."""

    matter: Matter
    link: MatterSourcePage
    #: The chain this Teema sits on (index into `SharedDossier.chains`), or ``None``.
    chain: int | None = None
    #: Its 1-based position on that chain.
    position: int = 0


@dataclass(frozen=True)
class SharedDossier:
    """What one reader may know about the Teemad sharing one page."""

    kind: str
    #: Readable linked Teemad, chain order first, then by reference.
    teemad: tuple[DossierTeema, ...] = ()
    #: Each chain of two or more readable linked Teemad, earliest stage first.
    chains: tuple[tuple[DossierTeema, ...], ...] = ()
    #: Linked Teemad this reader may not read. Never named; the page says only
    #: *that* there is one (yes/no), never how many (owner, 2026-11-05, QA-05 / E-2).
    #: The number is kept for `shared`, which decides whether the files wait.
    hidden: int = 0
    #: A forward walk met a Matter it had already visited.
    cyclic: bool = False
    #: Linked Teemad (by pk) on no line: their walks converge with another
    #: linked Teema's, or loop.
    nonlinear: frozenset[Any] = field(default_factory=frozenset)

    @property
    def total(self) -> int:
        return len(self.teemad) + self.hidden

    @property
    def unchained(self) -> tuple[DossierTeema, ...]:
        """Readable linked Teemad on no chain."""
        return tuple(item for item in self.teemad if item.chain is None)

    @property
    def shared(self) -> bool:
        """Linked to more than one live Teema (whether or not each is readable)."""
        return self.total > 1

    @property
    def ambiguous(self) -> bool:
        """Worth a review overview: not one chain covering every Teema."""
        return self.shared and self.kind != ONE_CHAIN

    def relation_of(self, item: DossierTeema) -> str:
        """«Etapp 1/2», «Jätku seos teiste selle lehe teemadega puudub», …"""
        if item.matter.pk in self.nonlinear:
            return "Jätku seos ei ole lineaarne"
        if item.chain is not None:
            return f"Etapp {item.position}/{len(self.chains[item.chain])}"
        return "Jätku seos teiste selle lehe teemadega puudub"


def _forward(start: Matter, readable: Any) -> tuple[list[Any], bool]:
    """`start` and the readable stages after it, by pk, and whether it looped."""
    path = [start.pk]
    seen = {start.pk}
    next_id = start.superseded_by_id
    while next_id is not None and len(path) <= MAX_HOPS:
        if next_id in seen:
            return path, True
        step = readable.filter(pk=next_id).only("pk", "superseded_by").first()
        if step is None:
            # A stage this reader may not read ends the walk: a continuation
            # edge never opens a Matter (docs/adr/0207).
            break
        path.append(step.pk)
        seen.add(step.pk)
        next_id = step.superseded_by_id
    return path, False


def shared_dossier(page: LegacySourcePage, viewer: Any) -> SharedDossier:
    """How the Teemad linked to `page` relate, as far as `viewer` may see."""
    links = list(MatterSourcePage.objects.filter(source_page=page).select_related("matter"))
    # Live Matters only: a deleted Teema's old link is not part of the dossier.
    live_ids = set(
        Matter.objects.filter(pk__in=[link.matter_id for link in links]).values_list(
            "pk", flat=True
        )
    )
    readable = Matter.objects.visible_to(viewer)
    readable_ids = set(readable.filter(pk__in=live_ids).values_list("pk", flat=True))
    by_matter: dict[Any, MatterSourcePage] = {}
    for link in links:
        if link.matter_id in readable_ids:
            by_matter.setdefault(link.matter_id, link)
    hidden = len(live_ids - readable_ids)
    matters = sorted((link.matter for link in by_matter.values()), key=_reference_key)

    paths: dict[Any, list[Any]] = {}
    looped: set[Any] = set()
    for matter in matters:
        paths[matter.pk], loops = _forward(matter, readable)
        if loops:
            looped.add(matter.pk)

    linked = set(by_matter)
    # `a` precedes `b` when `b` is on `a`'s forward path.
    precedes: dict[Any, set[Any]] = {a: {b for b in paths[a][1:] if b in linked} for a in linked}

    # Group the linked Teemad whose walks meet (comparable, or sharing a later stage).
    parent = {pk: pk for pk in linked}

    def find(pk: Any) -> Any:
        while parent[pk] != pk:
            parent[pk] = parent[parent[pk]]
            pk = parent[pk]
        return pk

    ordered = [m.pk for m in matters]
    for i, a in enumerate(ordered):
        for b in ordered[i + 1 :]:
            if set(paths[a]) & set(paths[b]):
                parent[find(a)] = find(b)
    groups: dict[Any, list[Any]] = {}
    for pk in ordered:
        groups.setdefault(find(pk), []).append(pk)

    nonlinear: set[Any] = set(looped)
    chains: list[list[Any]] = []
    for members in groups.values():
        if any(pk in looped for pk in members):
            nonlinear.update(members)
            continue
        if len(members) < 2:
            continue
        # A chain: every pair comparable. Ordered by how many members follow.
        line = all(
            b in precedes[a] or a in precedes[b]
            for i, a in enumerate(members)
            for b in members[i + 1 :]
        )
        if line:
            chains.append(sorted(members, key=lambda pk: -len(precedes[pk])))
        else:
            nonlinear.update(members)

    if nonlinear:
        kind = COMPLEX
    elif chains and len(chains) == 1 and len(chains[0]) == len(linked) and not hidden:
        kind = ONE_CHAIN
    elif chains:
        kind = PARTIAL_CHAIN
    else:
        kind = NONE

    position = {
        pk: (index, place + 1) for index, line in enumerate(chains) for place, pk in enumerate(line)
    }
    items = {
        pk: DossierTeema(
            matter=by_matter[pk].matter,
            link=by_matter[pk],
            chain=position[pk][0] if pk in position else None,
            position=position[pk][1] if pk in position else 0,
        )
        for pk in ordered
    }
    in_chains = [items[pk] for line in chains for pk in line]
    rest = [items[pk] for pk in ordered if pk not in position]
    return SharedDossier(
        kind=kind,
        teemad=tuple(in_chains + rest),
        chains=tuple(tuple(items[pk] for pk in line) for line in chains),
        hidden=hidden,
        cyclic=bool(looped),
        nonlinear=frozenset(nonlinear),
    )
