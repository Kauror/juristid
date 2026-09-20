"""`Kustuta teema` — what deleting one Matter actually removes, and what it cannot.

Deletion is the one operation on this product that cannot be undone by
correcting a record, so this module is built the way ``app.matters.purge`` is:
the graph is **walked**, never described. Every relation is discovered from
Django's own metadata at run time, which is the only version of this that stays
correct when somebody adds a model next month — a hand-written list of tables
is a list that is wrong the first time the schema moves.

It reuses that module's walker rather than growing a second one. ``purge``
answers *what would removing the development data touch*; this answers *what
does removing this one Matter touch*; the ownership graph, the outside-reference
check, the straddling-row check and the legal-hold check are the same four
questions and must not be able to disagree.

Why a tombstone
---------------
``matter.delete()`` is refused, and not by policy — by the database.
``ChangeEvent.matter`` is ``PROTECT``; ``ChangeEvent`` is append-only through a
``BEFORE UPDATE OR DELETE`` trigger on ``audit_changeevent``; and every Matter
carries change events from the moment it is created. So Django's collector
refuses the delete, deleting the events first is refused by the trigger, and
nulling the pointer is an ``UPDATE`` the same trigger refuses. There is no order
of operations that removes the row while leaving the audit guarantee standing.

Which is the right outcome rather than an obstacle. The audit trail of a
deletion is the part of it nobody may lose: «this Matter existed, this person
deleted it, on this day» is exactly what a deleted record has to be able to
answer. So the row stays, marked, and **stops being a Matter** — the default
manager excludes it, every read surface is therefore empty of it by
construction, and its detail URL answers 404.

What goes, and what a refusal is
--------------------------------
Everything the Matter *owns* goes: its entries, its consultations, its
positions, its opinions, its documents and their versions, its work items, its
search rows, its links. Shared vocabulary never does — an ``Organisation``, a
``Tag``, a ``PolicyArea``, an archive binary — because ownership is followed
through *reverse* relations only, so a row the Matter merely points at is
unreachable from here by construction rather than by exclusion list.

Four things refuse the whole operation, atomically:

* a document under the Matter is under a **legal hold**. A legal hold outlives
  anybody's wish to tidy up;
* a row **outside** the owned set points into it — a real record depends on
  something this would remove;
* an owned row **straddles** the boundary, holding the only record of a fact
  about data outside it;
* an owned row is **append-only** and is not one of the two the tombstone
  keeps. That is the `EntryRevision` case: a corrected entry's previous wording
  is historical evidence the database will not let anything remove, so a Matter
  whose entries have been corrected cannot have those entries deleted. It is
  refused by name rather than by an ``IntegrityError`` halfway through.

None of the four is worked around and none is reported as a partial success.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, cast

from django.db import transaction
from django.utils import timezone

from app.audit.enums import ChangeEventType
from app.audit.models import ChangeEvent
from app.core.models import AppendOnlyModel
from app.matters.models import Matter
from app.matters.purge import (
    Blocker,
    EvidenceSummary,
    RowGroup,
    _collect_owned,
    _evidence,
    _legal_holds,
    _outside_references,
    _straddling_rows,
)
from app.matters.services import DomainError

#: An append-only row a deletion would have to remove, and cannot.
BLOCKED_BY_APPEND_ONLY = "BLOCKED_BY_APPEND_ONLY"

#: Append-only rows that ride with the tombstone instead of blocking it.
#:
#: Both hang directly off the Matter row, and the Matter row survives — so
#: nothing has to remove them and nothing does. They are the deletion's own
#: proof: the ``ChangeEvent`` written by this operation is in here too.
#:
#: Deliberately an allow-list and not a rule. A future append-only model that
#: turns out to be owned by a Matter blocks deletion until somebody decides
#: what should happen to it, which is the same refusal-by-default
#: ``purge.py`` takes and for the same reason.
TOMBSTONE_KEEPS = frozenset(
    {
        "audit.ChangeEvent",
        "audit.SecurityAuditEvent",
    }
)

#: What the person is told when a Matter cannot be deleted. One sentence per
#: category, in the words the category actually means — «ei saa kustutada» with
#: no reason is an answer somebody will try again.
BLOCKER_MESSAGES: dict[str, str] = {
    "BLOCKED_BY_LEGAL_HOLD": (
        "Teema all on dokument, millele kehtib õiguslik säilituskohustus. "
        "Säilituskohustus tuleb enne lõpetada."
    ),
    "BLOCKED_BY_REAL_REFERENCE": (
        "Mõni teine kirje viitab selle teema andmetele. Kustutamine rikuks selle kirje."
    ),
    BLOCKED_BY_APPEND_ONLY: (
        "Teema all on muutumatuid ajalookirjeid, mida andmebaas ei luba "
        "kustutada. Kõige sagedasem põhjus on parandatud märge: paranduse-eelne "
        "sõnastus on tõend."
    ),
}

#: Shown when the Matter is blocked for a reason this module has no sentence
#: for, which is a schema that has moved under it.
BLOCKER_FALLBACK = "Teemat ei saa kustutada, sest mõni seotud kirje ei luba seda."


@dataclass(frozen=True)
class DeletionPlan:
    """What deleting one Matter would remove, and every reason it would refuse."""

    matter_id: Any
    owned: tuple[RowGroup, ...] = ()
    evidence: tuple[EvidenceSummary, ...] = ()
    blockers: tuple[Blocker, ...] = ()

    @property
    def is_blocked(self) -> bool:
        return bool(self.blockers)

    @property
    def business_rows(self) -> int:
        """Rows that would actually be removed — the Matter and its kept audit aside."""
        return sum(
            group.count
            for group in self.owned
            if group.label != Matter._meta.label and group.label not in TOMBSTONE_KEEPS
        )

    @property
    def refusal(self) -> str:
        """The first blocker's sentence, for a person rather than for a log."""
        if not self.blockers:
            return ""
        return BLOCKER_MESSAGES.get(self.blockers[0].category, BLOCKER_FALLBACK)


class MatterDeletionBlocked(DomainError):
    """Deletion was refused, and the plan says by what.

    A `DomainError` so that every caller already handling a refused write
    handles this one, and carrying the plan so a view can say *which* of the
    four reasons applied rather than «ei õnnestunud».
    """

    def __init__(self, plan: DeletionPlan) -> None:
        self.plan = plan
        super().__init__(plan.refusal or BLOCKER_FALLBACK)


def _append_only_blockers(collected: Any) -> list[Blocker]:
    """Append-only rows a deletion would have to remove, and therefore cannot."""
    blockers: list[Blocker] = []
    for label in sorted(collected.ids):
        if label in TOMBSTONE_KEEPS or label == Matter._meta.label:
            continue
        model = collected.models[label]
        if not issubclass(model, AppendOnlyModel):
            continue
        count = len(collected.ids[label])
        if not count:
            continue
        blockers.append(
            Blocker(
                category=BLOCKED_BY_APPEND_ONLY,
                label=label,
                count=count,
                detail="append-only rows cannot be removed by any operation",
            )
        )
    return blockers


def build_deletion_plan(matter: Matter) -> DeletionPlan:
    """Inventory what deleting this Matter would touch. Reads only, writes nothing.

    Deterministic by construction, exactly as `build_purge_plan` is: every
    collection is a set of primary keys and every reported sequence is sorted
    by a stable label, so a plan taken before a deletion is comparable with one
    taken after.
    """
    collected = _collect_owned([matter.pk])
    owned = tuple(
        sorted(
            RowGroup(
                label=label,
                count=len(ids),
                behaviour=collected.behaviour[label],
                append_only=issubclass(collected.models[label], AppendOnlyModel),
            )
            for label, ids in collected.ids.items()
            if ids
        )
    )
    blockers = (
        _legal_holds(collected)
        + _outside_references(collected)
        + _straddling_rows(collected)
        + _append_only_blockers(collected)
    )
    return DeletionPlan(
        matter_id=matter.pk,
        owned=owned,
        evidence=tuple(_evidence(collected)),
        blockers=tuple(sorted(blockers)),
    )


@transaction.atomic
def delete_matter(*, matter: Matter, actor: Any) -> DeletionPlan:
    """Remove this Matter's business content and leave an audit tombstone.

    **One transaction, and the plan is rebuilt inside it.** A plan computed for
    a confirmation page somebody read two minutes ago is a plan that may have
    stopped being true — a colleague may have put a document under a legal hold
    or filed an opinion that another record now depends on. The Matter is
    locked first and the graph walked second, so what is checked is what is
    deleted.

    **The audit row is written before anything is removed.** Writing it
    afterwards would mean a crash between the two left a Matter stripped of its
    content with nothing saying who did it; writing it first means a refusal
    rolls it back with everything else. It is the same ordering
    `record_procedural_development` uses for the same reason.

    Returns the plan that was executed, so a caller can report what went.
    """
    locked = Matter.all_objects.select_for_update(no_key=True).get(pk=matter.pk)
    if locked.deleted_at is not None:
        # Already a tombstone. Idempotent rather than an error: two tabs, two
        # clicks, one deletion — and the second one has nothing left to do.
        return DeletionPlan(matter_id=locked.pk)

    plan = build_deletion_plan(locked)
    if plan.is_blocked:
        raise MatterDeletionBlocked(plan)

    ChangeEvent.objects.create(
        matter=locked,
        actor=actor,
        event_type=ChangeEventType.MATTER_DELETED,
        object_type=Matter._meta.label,
        object_id=locked.pk,
        summary="Teema kustutati",
        # Counts, never content: what was in the record is what the deletion
        # removed, and copying it into the audit row would be keeping the
        # business data under another name.
        payload={
            "rows": plan.business_rows,
            "evidence_objects": sum(item.objects for item in plan.evidence),
        },
    )

    # Owned children, one relation at a time. Django cascades from each of
    # them, so this loop is over the Matter's *direct* dependants rather than
    # over the whole graph — and every deeper row the walk found is reached by
    # that cascade. The two kept audit models are skipped: nothing has to
    # remove them, because the row they point at survives.
    for relation in Matter._meta.get_fields(include_hidden=True):
        if not (relation.one_to_many or relation.one_to_one):
            continue
        if not relation.auto_created or relation.concrete:
            continue
        related = relation.related_model
        if related is None or related._meta.label in TOMBSTONE_KEEPS:
            continue
        # Narrowed for the type checker: `get_fields` is typed as the union
        # with concrete fields, and the two guards above have already excluded
        # those — only a reverse relation reaches here, and a reverse relation
        # has a `field`.
        column = cast(Any, relation).field.name
        related._base_manager.filter(**{column: locked.pk}).delete()

    # Many-to-many rows the Matter owns the *link* to but not the target:
    # `policy_areas`, `legal_instruments`, `source_organisations`,
    # `collaborators`, `tags`. Clearing the link leaves every shared row
    # standing, which is the whole distinction (`purge.NEVER_OWNED`).
    for field in Matter._meta.many_to_many:
        getattr(locked, field.name).clear()

    locked.deleted_at = timezone.now()
    locked.deleted_by = actor if actor is not None and actor.is_authenticated else None
    # `update_fields`, so this write cannot be read as a general save of a row
    # whose other columns are now describing content that no longer exists.
    locked.save(update_fields=["deleted_at", "deleted_by"])
    return plan


def deletion_is_available(matter: Matter) -> bool:
    """Whether the confirmation page should offer the button at all."""
    return matter.deleted_at is None


__all__ = [
    "BLOCKED_BY_APPEND_ONLY",
    "BLOCKER_FALLBACK",
    "BLOCKER_MESSAGES",
    "TOMBSTONE_KEEPS",
    "DeletionPlan",
    "MatterDeletionBlocked",
    "build_deletion_plan",
    "delete_matter",
    "deletion_is_available",
]
