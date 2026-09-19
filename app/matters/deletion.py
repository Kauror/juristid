"""Deleting one Teema: what it would touch, what refuses, and what is left.

`app.matters.purge` inventories what a purge of *development* data would have to
account for and deliberately stops at planning. This module is the operation a
person performs on one Matter from `Muuda teemat`, and it answers the questions
that planner raised rather than describing them (docs/adr/0096 §4).

Three facts about this schema decide the whole shape, and all three were
measured from Django's own metadata rather than assumed:

1. **The `Matter` row cannot be removed.** ``audit.ChangeEvent.matter`` is
   ``PROTECT`` and ``audit_changeevent`` carries a ``BEFORE UPDATE OR DELETE``
   trigger, so the audit rows can be neither deleted nor detached — and every
   Matter has them from ``MATTER_CREATED`` onwards. ``matter.delete()`` raises
   ``ProtectedError``; nulling the pointer raises ``restrict_violation``. So the
   row stays, as a **tombstone**: ``deleted_at`` set, every owned business row
   gone, and excluded from every query by the default manager.
2. **An `Entry` that was ever corrected cannot be removed either.**
   ``matters.EntryRevision`` is append-only in the database and hangs off
   ``Entry`` under ``CASCADE``, so deleting the entry issues a ``DELETE`` the
   trigger refuses. That is a *blocker*, not a residue: a tombstone may keep the
   audit proof the architecture requires, and may not keep ordinary business
   records to make a deletion look complete. A Matter holding a corrected entry
   is refused, in full, with the reason said out loud.
3. **Everything else really goes.** Entries, actions, engagements, opinions,
   submissions, documents and their versions and derivatives, links, snapshots,
   search projections, taxonomy joins, notices, register state — deleted rows,
   not hidden ones.

**Ownership runs one way**, exactly as it does in the planner: the walk follows
*reverse* relations only, so shared and archive data — an ``Organisation``, a
``Tag``, a ``PolicyArea``, an ``OpinionArchiveBinary``, an ``ImportBatch`` — is
out of the set by construction and not by exclusion list. One addition here that
the planner does not need: **another `Matter` is never owned**. The walk refuses
to follow ``Matter.superseded_by`` into a second record, and a Matter superseded
by this one is reported as a blocker instead.

**Nothing here bypasses anything.** No trigger is dropped, no append-only model
is made mutable, no audit row is rewritten, no ``on_delete`` is reinterpreted at
run time. Where the database refuses, this module refuses.
"""

from __future__ import annotations

import logging
from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field
from typing import Any, cast

from django.db import models, transaction
from django.utils import timezone

from app.audit.enums import ChangeEventType
from app.audit.services import record_change_event
from app.core.errors import DomainError
from app.core.models import AppendOnlyModel
from app.documents.references import EVIDENCE_REFERENCES
from app.matters.models import Matter
from app.matters.purge import CHUNK, OWNING_BEHAVIOURS, _behaviour_name, _reverse_relations
from app.search.indexing import forget_matter, suspend_indexing

logger = logging.getLogger(__name__)

# -- refusal codes ----------------------------------------------------------

#: A document under this Matter is under a legal hold.
BLOCKED_BY_LEGAL_HOLD = "LEGAL_HOLD"
#: An owned row is append-only and is not part of the audit trail this
#: architecture keeps under the tombstone — today, a corrected `Entry`.
BLOCKED_BY_APPEND_ONLY_CHILD = "APPEND_ONLY_CHILD"
#: A record outside this Matter depends on something the deletion would remove.
BLOCKED_BY_OUTSIDE_REFERENCE = "OUTSIDE_REFERENCE"
#: An owned row also anchors a fact about something outside the Matter.
BLOCKED_BY_STRADDLING_ROW = "STRADDLING_ROW"
#: Another Matter points at this one under an owning relation.
BLOCKED_BY_RELATED_MATTER = "RELATED_MATTER"

#: What each refusal says to the person who pressed the button. One sentence
#: naming the obstacle in the product's own words, because "ProtectedError" is
#: not an answer and "something went wrong" is worse.
REFUSAL_TEXT: dict[str, str] = {
    BLOCKED_BY_LEGAL_HOLD: (
        "Teema dokumentidele on seatud säilitamiskohustus, seega ei saa teemat kustutada."
    ),
    BLOCKED_BY_APPEND_ONLY_CHILD: (
        "Teemal on kirjeid, mille muudatuslugu on jäädavalt salvestatud ja mida ei saa "
        "eemaldada. Seetõttu ei saa teemat kustutada."
    ),
    BLOCKED_BY_OUTSIDE_REFERENCE: (
        "Mõni teine kirje tugineb selle teema sisule, seega ei saa teemat kustutada."
    ),
    BLOCKED_BY_STRADDLING_ROW: (
        "Teema sisu on seotud väljaspool teemat oleva kirjega, seega ei saa teemat kustutada."
    ),
    BLOCKED_BY_RELATED_MATTER: (
        "Mõni teine teema viitab sellele teemale, seega ei saa seda kustutada."
    ),
}

#: The refusal a caller sees when it asks to delete a Matter that is blocked.
#: Named because the view prints it and the tests assert on it.
DELETION_REFUSED = "Teemat ei saa kustutada."


@dataclass(frozen=True, order=True)
class DeletionBlocker:
    """One reason this deletion refuses."""

    code: str
    label: str
    count: int
    detail: str = ""

    @property
    def message(self) -> str:
        return REFUSAL_TEXT.get(self.code, DELETION_REFUSED)


@dataclass(frozen=True, order=True)
class RowGroup:
    """One model's contribution to the deletion."""

    label: str
    count: int


@dataclass(frozen=True)
class DeletionPlan:
    """What deleting this Matter would remove, and every reason it would not.

    Read-only. Built by `plan_matter_deletion` and re-built inside
    `delete_matter` under the row lock, because a plan taken to render a
    confirmation page is a plan taken before somebody had time to press the
    button (docs/adr/0096 §4.4).
    """

    matter_id: Any
    owned: tuple[RowGroup, ...] = ()
    retained: tuple[RowGroup, ...] = ()
    evidence_keys: tuple[str, ...] = ()
    derivative_keys: tuple[str, ...] = ()
    blockers: tuple[DeletionBlocker, ...] = ()

    @property
    def is_blocked(self) -> bool:
        return bool(self.blockers)

    @property
    def refusals(self) -> tuple[str, ...]:
        """The distinct sentences to show, in the order they were found."""
        seen: list[str] = []
        for blocker in self.blockers:
            if blocker.message not in seen:
                seen.append(blocker.message)
        return tuple(seen)

    def count_of(self, label: str) -> int:
        for group in self.owned:
            if group.label == label:
                return group.count
        return 0


@dataclass
class _Owned:
    """Primary keys reached, per model, during the ownership walk."""

    ids: dict[str, set[Any]] = field(default_factory=dict)
    models: dict[str, type[models.Model]] = field(default_factory=dict)
    #: Which models were reached by a relation coming straight off the Matter.
    #: Only those may be *retained* when they are append-only — an audit row
    #: about the Matter survives under the tombstone; an audit row about a
    #: business child would keep that child alive and is a refusal instead.
    direct: set[str] = field(default_factory=set)


def _chunked(values: Sequence[Any], size: int = CHUNK) -> Iterable[Sequence[Any]]:
    for start in range(0, len(values), size):
        yield values[start : start + size]


def _referring_ids(relation: Any, ids: Sequence[Any]) -> set[Any]:
    child = relation.related_model
    found: set[Any] = set()
    for chunk in _chunked(ids):
        found.update(
            child._base_manager.filter(**{f"{relation.field.name}__in": chunk}).values_list(
                "pk", flat=True
            )
        )
    return found


def _collect_owned(matter: Matter) -> _Owned:
    """Breadth-first over ownership relations, starting at this one Matter.

    `Matter` itself is never followed into. ``Matter.superseded_by`` is a
    ``PROTECT`` reverse relation and would otherwise pull a *second* record into
    the owned set and delete it, which is the one mistake this whole module
    exists to be incapable of. The relation is reported as a blocker in
    `_related_matters` instead.
    """
    owned = _Owned()
    label = Matter._meta.label
    owned.ids[label] = {matter.pk}
    owned.models[label] = Matter

    queue: list[tuple[type[models.Model], list[Any]]] = [(Matter, [matter.pk])]
    depth = 0
    while queue:
        model, ids = queue.pop(0)
        if not ids:
            continue
        for relation in _reverse_relations(model):
            if relation.on_delete not in OWNING_BEHAVIOURS:
                continue
            child = relation.related_model
            if child is Matter:
                continue
            child_label = child._meta.label
            found = _referring_ids(relation, ids)
            if not found:
                continue
            if model is Matter:
                owned.direct.add(child_label)
            known = owned.ids.setdefault(child_label, set())
            owned.models.setdefault(child_label, child)
            fresh = found - known
            known.update(found)
            if fresh:
                queue.append((child, sorted(fresh, key=str)))
        depth += 1
    return owned


def _append_only_blockers(owned: _Owned) -> list[DeletionBlocker]:
    """Append-only rows that would keep an ordinary business record alive.

    An append-only model reached straight off the Matter — `audit.ChangeEvent`,
    `legacy_import.ImportRowLedger` — is the audit proof the tombstone exists to
    carry, and is retained deliberately. One reached further down hangs off a
    row this deletion has to remove, and the database will refuse that removal:
    `matters.EntryRevision` under `Entry` is the case that exists today.

    Refusing is the decision, not a limitation nobody noticed. The alternative
    would be to leave the `Entry` behind, and a tombstone that keeps ordinary
    child business records is a deletion pretending to be one.
    """
    blockers: list[DeletionBlocker] = []
    for label in sorted(owned.ids):
        model = owned.models[label]
        if model is Matter or not issubclass(model, AppendOnlyModel):
            continue
        if label in owned.direct:
            continue
        blockers.append(
            DeletionBlocker(
                code=BLOCKED_BY_APPEND_ONLY_CHILD,
                label=label,
                count=len(owned.ids[label]),
                detail="append-only rows hang off a business record this deletion must remove",
            )
        )
    return blockers


def _legal_holds(owned: _Owned) -> list[DeletionBlocker]:
    """A legal hold outlives a deletion request.

    Somebody deciding a record should not exist is not, and can never be,
    permission to destroy something the organisation has been told to preserve
    (the rule `app.matters.purge` states for development data, applied to the
    one operation a person can reach).
    """
    from app.documents.models import Document

    ids = sorted(owned.ids.get(Document._meta.label, set()), key=str)
    if not ids:
        return []
    held = 0
    for chunk in _chunked(ids):
        held += Document._base_manager.filter(pk__in=chunk, legal_hold=True).count()
    if not held:
        return []
    return [
        DeletionBlocker(
            code=BLOCKED_BY_LEGAL_HOLD,
            label=Document._meta.label,
            count=held,
            detail="legal_hold is set on a document under this matter",
        )
    ]


def _related_matters(matter: Matter) -> list[DeletionBlocker]:
    """Another Matter that points at this one under an owning relation.

    ``Matter.superseded_by`` is the only one today. A record that says "this
    file was replaced by that one" is a real record depending on this one, and
    the honest answer is to refuse rather than to delete somebody else's Teema
    or to leave a pointer into a tombstone that reads as a live replacement.
    """
    count = Matter.all_objects.filter(superseded_by=matter.pk).exclude(pk=matter.pk).count()
    if not count:
        return []
    return [
        DeletionBlocker(
            code=BLOCKED_BY_RELATED_MATTER,
            label=Matter._meta.label,
            count=count,
            detail="another matter names this one as its successor",
        )
    ]


def _outside_references(owned: _Owned) -> list[DeletionBlocker]:
    """Rows outside the owned set that point into it.

    The check this exercise exists for. A ``DocumentVersion`` under this Matter
    can be the ``final_version`` a *different* Matter's submission stands on; a
    row outside can hang off an owned fragment. Every one of those is a real
    record that depends on something this deletion would remove.

    All four deletion behaviours are refused, not only the loud ones. A cascade
    would destroy the outside row, a PROTECT would abort the deletion halfway,
    and a cleared pointer would change an outside record with nothing failing.
    The quiet one is the worst of them.

    The Matter row is deliberately **not** a source here: it is not deleted, so
    nothing pointing at it is disturbed, and scanning it would report every
    ordinary inbound reference — including the audit rows this design retains
    on purpose.
    """
    blockers: list[DeletionBlocker] = []
    for label in sorted(owned.ids):
        model = owned.models[label]
        if model is Matter:
            continue
        target = sorted(owned.ids[label], key=str)
        if not target:
            continue
        for relation in _reverse_relations(model):
            child_label = relation.related_model._meta.label
            outside = _referring_ids(relation, target) - owned.ids.get(child_label, set())
            if not outside:
                continue
            blockers.append(
                DeletionBlocker(
                    code=BLOCKED_BY_OUTSIDE_REFERENCE,
                    label=f"{child_label}.{relation.field.name}",
                    count=len(outside),
                    detail=(
                        f"{_behaviour_name(relation.on_delete)} onto {label}; "
                        "the referring row is outside this matter"
                    ),
                )
            )
    return blockers


def _straddling_rows(owned: _Owned) -> list[DeletionBlocker]:
    """Owned rows that also anchor a fact about something outside the Matter.

    The pass above finds outside rows pointing *in*. This finds the harder case:
    a row legitimately owned here that is at the same time the only record of a
    fact about data that survives. ``documents.EmailAttachmentLink`` is the
    concrete one — it says "this exact binary arrived inside that exact
    message" and holds two versions under ``PROTECT``.

    So a row is owned only if every forward key it holds *into a model this
    deletion already owns* lands inside the set.

    **`Matter`-valued keys are exempt, deliberately.** `MatterRelation` and
    `RelatedSuggestionDismissal` exist precisely to name two Matters, and they
    are reached from either end. Deleting the row that says "these two files are
    related" is the correct consequence of one of them ceasing to exist — it is
    not a fact about the surviving Matter that this deletion is destroying, it
    is a fact about the pair. Shared vocabulary is not considered at all,
    because those models never enter the owned inventory.
    """
    blockers: list[DeletionBlocker] = []
    for label in sorted(owned.ids):
        model = owned.models[label]
        if model is Matter:
            continue
        ids = sorted(owned.ids[label], key=str)
        if not ids:
            continue
        for key in model._meta.get_fields():
            if not (key.is_relation and getattr(key, "concrete", False)):
                continue
            if not (key.many_to_one or key.one_to_one):
                continue
            related = key.related_model
            if related is None or related is Matter:
                continue
            if related._meta.label not in owned.ids:
                continue
            column = cast(Any, key).attname
            inside = owned.ids[related._meta.label]
            outside = 0
            for chunk in _chunked(ids):
                targets = model._base_manager.filter(pk__in=chunk).values_list(column, flat=True)
                outside += sum(1 for target in targets if target and target not in inside)
            if not outside:
                continue
            blockers.append(
                DeletionBlocker(
                    code=BLOCKED_BY_STRADDLING_ROW,
                    label=f"{label}.{key.name}",
                    count=outside,
                    detail=f"also holds a {related._meta.label} outside this matter",
                )
            )
    return blockers


def _evidence_keys(owned: _Owned) -> tuple[str, ...]:
    """Canonical evidence objects held by rows this deletion removes.

    Read through ``EVIDENCE_REFERENCES`` rather than through a second definition
    of "evidence", so a future holder of evidence bytes is picked up here at the
    moment it is registered there — and so ``OpinionArchiveBinary`` is excluded
    for the right reason: it is a registered holder that a Matter never owns.
    """
    keys: set[str] = set()
    for reference in EVIDENCE_REFERENCES:
        model = reference.model()
        ids = sorted(owned.ids.get(model._meta.label, set()), key=str)
        for chunk in _chunked(ids):
            rows = model._base_manager.filter(pk__in=chunk).values_list(reference.field, flat=True)
            keys.update(key for key in rows if key)
    return tuple(sorted(keys))


def _derivative_keys(owned: _Owned) -> tuple[str, ...]:
    """Derived files, counted separately because they are a different promise.

    Derivatives live in their own storage class and are rebuildable from the
    evidence, which is why the evidence registry excludes them (docs/adr/0014).
    They are still stored objects this deletion leaves unreferenced.
    """
    from app.documents.models import DocumentDerivative

    ids = sorted(owned.ids.get(DocumentDerivative._meta.label, set()), key=str)
    keys: set[str] = set()
    for chunk in _chunked(ids):
        for key in DocumentDerivative._base_manager.filter(pk__in=chunk).values_list(
            "storage_key", flat=True
        ):
            if key:
                keys.add(key)
    return tuple(sorted(keys))


def _deletion_order(owned: _Owned) -> list[str]:
    """Model labels, in an order the database will accept.

    A row holding a ``PROTECT`` or ``RESTRICT`` key must be gone before the row
    it points at, and *only* those two constrain the order: a ``CASCADE`` parent
    takes its children with it and a ``SET_NULL`` pointer is cleared for free.
    That is the edge set this sorts on, which is why a deepest-first order would
    be wrong — ``submissions.Submission`` sits one step from the Matter and
    holds ``final_version`` onto a ``DocumentVersion`` two steps away.

    Kahn's algorithm, with ties broken by label so that two runs over the same
    graph delete in the same order. A cycle would be a schema in which two rows
    each protect the other, which cannot be satisfied and cannot be deleted; the
    remaining labels are appended in name order so the caller still attempts
    them and gets the database's own refusal rather than a silent skip.
    """
    labels = [label for label in owned.ids if owned.models[label] is not Matter]
    known = set(labels)
    # `after[a]` — every model `a` must be deleted before.
    after: dict[str, set[str]] = {label: set() for label in labels}
    incoming: dict[str, int] = dict.fromkeys(labels, 0)
    for label in labels:
        model = owned.models[label]
        for key in model._meta.get_fields():
            if not (key.is_relation and getattr(key, "concrete", False)):
                continue
            if not (key.many_to_one or key.one_to_one):
                continue
            if cast(Any, key).remote_field.on_delete not in (models.PROTECT, models.RESTRICT):
                continue
            related = key.related_model
            if related is None:
                continue
            target = related._meta.label
            if target not in known or target == label:
                continue
            if target not in after[label]:
                after[label].add(target)
                incoming[target] += 1

    ready = sorted(label for label in labels if not incoming[label])
    order: list[str] = []
    while ready:
        label = ready.pop(0)
        order.append(label)
        for target in sorted(after[label]):
            incoming[target] -= 1
            if not incoming[target]:
                ready.append(target)
                ready.sort()
    order.extend(sorted(set(labels) - set(order)))
    return order


def plan_matter_deletion(matter: Matter) -> DeletionPlan:
    """Inventory what deleting this Matter would do. Reads only.

    Deterministic by construction: every collection is a set of primary keys and
    every reported sequence is sorted by a stable label, so a plan taken to
    render the confirmation page is comparable with the one taken under the lock
    a moment later.
    """
    owned = _collect_owned(matter)
    blockers = (
        _legal_holds(owned)
        + _related_matters(matter)
        + _append_only_blockers(owned)
        + _outside_references(owned)
        + _straddling_rows(owned)
    )
    removed: list[RowGroup] = []
    retained: list[RowGroup] = []
    for label in sorted(owned.ids):
        model = owned.models[label]
        if model is Matter:
            continue
        group = RowGroup(label=label, count=len(owned.ids[label]))
        if issubclass(model, AppendOnlyModel) and label in owned.direct:
            retained.append(group)
        else:
            removed.append(group)
    return DeletionPlan(
        matter_id=matter.pk,
        owned=tuple(removed),
        retained=tuple(retained),
        evidence_keys=_evidence_keys(owned),
        derivative_keys=_derivative_keys(owned),
        blockers=tuple(sorted(blockers)),
    )


def _forget_storage(keys: Sequence[str]) -> None:
    """Best effort, after the commit, with the pruner as the guarantee.

    Storage cleanup is deliberately *not* part of the transaction. The bytes are
    outside PostgreSQL and cannot be rolled back, so deleting them before the
    commit would destroy evidence a rollback then claims still exists.

    Afterwards, an object whose row is gone is by definition unreferenced, which
    is precisely what ``prune_orphaned_evidence`` finds and removes — so a
    failure here costs disk and nothing else, and it is tracked rather than
    lost. The attempt is made anyway, because reclaiming at once is better than
    reclaiming at the next run (app/documents/management/commands).
    """
    if not keys:
        return
    from app.documents.services import evidence_storage

    storage = evidence_storage()
    for key in keys:
        try:
            storage.delete(key)
        except Exception:  # pragma: no cover - reported, never fatal
            logger.warning(
                "matter deletion could not remove evidence object %s; "
                "it is unreferenced and prune_orphaned_evidence will reclaim it",
                key,
                extra={"storage_key": key},
            )


def delete_matter(*, matter: Matter, actor: Any = None) -> DeletionPlan:
    """Delete one Teema. All of it, or none of it.

    The locking is the canonical order and nothing more: the Matter is taken
    ``FOR NO KEY UPDATE`` — the strength every write in this codebase takes on a
    Matter, so that inserting a child row still goes through — and the owned
    rows are removed underneath it. Anything that arrives afterwards asks
    `Matter.objects` for a row the default manager no longer returns and is
    refused by the surface it arrived at.

    **The plan is rebuilt under the lock.** The one rendered on the confirmation
    page was true when it was drawn; a legal hold, a joint submission or a
    second Matter naming this one may have appeared while somebody was reading
    it, and the refusal has to be decided against the database the deletion is
    about to act on.

    Raises `DomainError` when the deletion refuses. Nothing is written in that
    case — the refusal happens before the first delete, inside the transaction,
    so there is no partial state to undo.
    """
    with transaction.atomic():
        try:
            locked = Matter.all_objects.select_for_update(no_key=True).get(pk=matter.pk)
        except Matter.DoesNotExist as error:  # pragma: no cover - defensive
            raise DomainError(DELETION_REFUSED) from error
        if locked.deleted_at is not None:
            # Somebody else won the race. Saying so is honest and is not an
            # error the reader can act on differently, so it reads as done.
            return DeletionPlan(matter_id=locked.pk)

        plan = plan_matter_deletion(locked)
        if plan.is_blocked:
            raise DomainError(" ".join((DELETION_REFUSED, *plan.refusals)))

        owned = _collect_owned(locked)

        # **The tombstone is marked first, and that ordering is load-bearing.**
        #
        # `app.search.signals` re-projects a Matter when one of its children is
        # deleted, and `indexable_matters()` reads `Matter.objects`. Marked
        # first, the Matter is already outside that manager, so every refresh
        # the purge provokes finds nothing and writes nothing. Marked last, the
        # first `Entry` removed would put a fresh `SearchDocument` back — which
        # is exactly what happened, and what a reader would have seen in the
        # register's search for a Teema that no longer exists.
        #
        # `update()` rather than `save()`, so no `post_save` fires and the
        # indexer is never asked about a row mid-deletion. Indexing is suspended
        # over the loop as well: the refreshes would all be no-ops now, and
        # doing nothing is cheaper than proving it several dozen times.
        deleted_at = timezone.now()
        Matter.all_objects.filter(pk=locked.pk).update(
            deleted_at=deleted_at,
            deleted_by=getattr(actor, "pk", None),
            updated_at=deleted_at,
        )

        with suspend_indexing():
            for label in _deletion_order(owned):
                model = owned.models[label]
                if issubclass(model, AppendOnlyModel):
                    # Retained under the tombstone. Only the direct audit
                    # children reach this line; anything else append-only was a
                    # blocker.
                    continue
                ids = sorted(owned.ids[label], key=str)
                for chunk in _chunked(ids):
                    model._base_manager.filter(pk__in=chunk).delete()

        # The projection, once more and unconditionally, through the search
        # app's own door. The two guards above make this a no-op, and it is here
        # because «a deleted Teema disappears from search at once» is a promise
        # to a reader rather than a property of whichever signal handlers happen
        # to exist next year.
        #
        # `forget_matter` rather than a `SearchDocument` query: no module
        # outside `app.search` may name that table, which is the rule that keeps
        # business state from depending on derived data
        # (tests/test_search_reliability.py).
        forget_matter(locked.pk)

        # The audit row points at the tombstone and is the thing that keeps it
        # from ever being removed — which is the architecture stating its own
        # reason. Written last, so it describes a deletion that happened.
        record_change_event(
            event_type=ChangeEventType.MATTER_DELETED,
            matter=locked,
            actor=actor,
            summary=f"Teema kustutatud: {locked.title}",
            payload={
                "title": locked.title,
                "reference": locked.display_reference,
                "rows": {group.label: group.count for group in plan.owned},
                "evidence_objects": len(plan.evidence_keys),
            },
        )
        keys = (*plan.evidence_keys, *plan.derivative_keys)
        transaction.on_commit(lambda: _forget_storage(keys))
    return plan
