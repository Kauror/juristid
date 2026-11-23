"""Work a person still holds, and work held by nobody active (docs/adr/0220 §4).

When a lawyer leaves, their open Teemad and steps must go to somebody before —
or at least knowingly when — their account is switched off: a Teema owned by an
inactive person sits on nobody's «Minu asjad», and a new step on it is refused
until it has a new owner (`DEPARTED_OWNER_REFUSAL`). These read models say what
is left: `held_work` for one person (shown on Haldus → Kasutajad and checked by
`deactivate_account`), and `work_without_active_owner` for everyone at once.

A Teema the Lovable synchronisation follows changes owner only in Lovable
(docs/adr/0213 §10, its «Teemade üleandmine»); such Teemad are counted apart so
the page can say where the hand-over is done. Counts only: no title, no step
text, so the page shows nothing a technical administrator may not read
(AGENTS.md, «technical administration does not imply business-content access»).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from django.db.models import Count, Exists, OuterRef, Q

from app.matters.models import Matter
from app.workflow.enums import ActionStatus
from app.workflow.models import NextAction

#: The steps that are still somebody's work.
LIVE_STEP_STATUSES = (ActionStatus.OPEN, ActionStatus.PLANNED)


def _synced() -> Exists:
    from app.legacy_import.lovable_sync_models import LovableSyncState

    return Exists(LovableSyncState.objects.filter(matter_id=OuterRef("pk")))


def _open_teemad() -> Any:
    """Live (not deleted) open Teemad; `Matter.objects` already hides deleted ones,
    and the filter says so again."""
    return Matter.objects.filter(is_open=True, deleted_at__isnull=True)


def _live_steps() -> Any:
    return NextAction.objects.filter(
        status__in=LIVE_STEP_STATUSES, matter__deleted_at__isnull=True, matter__is_open=True
    )


@dataclass(frozen=True)
class HeldWork:
    """What one person still holds: open Teemad they own (and how many of them
    Lovable owns), and open or planned steps they answer for."""

    teemad: int = 0
    lovable_teemad: int = 0
    steps: int = 0

    @property
    def any(self) -> bool:
        return bool(self.teemad or self.steps)

    @property
    def juristid_teemad(self) -> int:
        return self.teemad - self.lovable_teemad

    def sentence(self) -> str:
        """«3 avatud teemat (neist 2 Lovable'is) ja 4 pooleli sammu» — counts only."""
        parts = []
        if self.teemad:
            teemad = f"{self.teemad} avatud {'teema' if self.teemad == 1 else 'teemat'}"
            if self.lovable_teemad:
                teemad += f" (neist {self.lovable_teemad} Lovable'is)"
            parts.append(teemad)
        if self.steps:
            parts.append(f"{self.steps} pooleli {'samm' if self.steps == 1 else 'sammu'}")
        return " ja ".join(parts)


def held_work(user: Any) -> HeldWork:
    """The open work ``user`` still holds. Two queries."""
    pk = getattr(user, "pk", None)
    if pk is None:
        return HeldWork()
    teemad = (
        _open_teemad()
        .filter(owner_id=pk)
        .aggregate(total=Count("pk"), lovable=Count("pk", filter=Q(_synced())))
    )
    steps = _live_steps().filter(responsible_id=pk).count()
    return HeldWork(teemad=teemad["total"], lovable_teemad=teemad["lovable"], steps=steps)


@dataclass(frozen=True)
class InactiveHolder:
    """One person no longer active who still holds open work."""

    person: Any
    work: HeldWork


@dataclass(frozen=True)
class OrphanedWork:
    """Open work nobody active holds: per inactive person, plus open Teemad with
    no owner at all."""

    holders: list[InactiveHolder]
    unowned_teemad: int
    unowned_lovable_teemad: int

    @property
    def any(self) -> bool:
        return bool(self.holders or self.unowned_teemad)


def work_without_active_owner() -> OrphanedWork:
    """Every inactive person who still owns an open Teema or answers for a live
    step, with the counts, and the open Teemad with no owner. Three queries."""
    from app.accounts.models import User

    teemad = {
        row["owner_id"]: row
        for row in _open_teemad()
        .filter(owner__is_active=False)
        .values("owner_id")
        .annotate(total=Count("pk"), lovable=Count("pk", filter=Q(_synced())))
    }
    steps = {
        row["responsible_id"]: row["total"]
        for row in _live_steps()
        .filter(responsible__is_active=False)
        .values("responsible_id")
        .annotate(total=Count("pk"))
    }
    people = {p.pk: p for p in User.objects.filter(pk__in=set(teemad) | set(steps))}
    holders = [
        InactiveHolder(
            person=people[pk],
            work=HeldWork(
                teemad=(teemad.get(pk) or {}).get("total", 0),
                lovable_teemad=(teemad.get(pk) or {}).get("lovable", 0),
                steps=steps.get(pk, 0),
            ),
        )
        for pk in people
    ]
    holders.sort(key=lambda h: (str(h.person.display_name).casefold(), str(h.person.pk)))
    unowned = (
        _open_teemad()
        .filter(owner__isnull=True)
        .aggregate(total=Count("pk"), lovable=Count("pk", filter=Q(_synced())))
    )
    return OrphanedWork(
        holders=holders,
        unowned_teemad=unowned["total"],
        unowned_lovable_teemad=unowned["lovable"],
    )
