"""What removing development data would actually have to touch.

**Nothing here deletes anything, and nothing here writes anything.** This module
builds an inventory: the exact set of rows a future purge of TEST Matters would
have to account for, the evidence objects behind them, and every reason such a
purge would have to refuse.

Why an inventory before a delete. ``Matter.objects.test_data().delete()`` is not
a small operation in this system and it is not one that fails loudly. The
deletion graph runs through ``PROTECT`` relations that would refuse, through
append-only audit rows the database will not let anything remove, through
canonical evidence bytes that live outside PostgreSQL, and — the case that
matters most — through objects a *real* record may be pointing at. A cascade
that reached one of those would either abort halfway or take real data with it.

So the graph is walked rather than described. Every relation is discovered from
Django's own metadata at run time, which is the only version of this that stays
correct when somebody adds a model next month: a hand-written list of tables is
a list that is wrong the first time the schema moves (Agent-C brief 33).

**Ownership runs one way.** The walk follows *reverse* relations only — rows
that point at something already owned. It never follows a foreign key
*outwards*, which is what keeps shared and archive data out of the plan by
construction rather than by exclusion list. An ``OpinionArchiveBinary``, an
``ImportBatch``, a ``LegacySourcePage``, an ``Organisation``, a ``Tag``: none of
them is reachable, because a test Matter *refers to* them and does not own them
(brief 21, 35, 61).
"""

from __future__ import annotations

import uuid
from collections.abc import Iterable, Iterator, Sequence
from dataclasses import dataclass, field
from typing import Any, cast

from django.db import models

from app.core.models import AppendOnlyModel
from app.documents.references import EVIDENCE_REFERENCES
from app.matters.enums import MatterOrigin
from app.matters.models import Matter

#: How many primary keys go into one ``__in`` clause.
CHUNK = 500

#: Deletion behaviours that make the referring row a *dependent* of the row it
#: points at. Following these is what "owned by a test Matter" means.
#:
#: ``SET_NULL`` is deliberately absent. A row that would merely have its pointer
#: cleared is not owned by what it points at — it survives the deletion, keeps
#: its own identity, and may very well belong to a real Matter. Those are found
#: by the outside-reference pass instead, where they belong.
OWNING_BEHAVIOURS = frozenset({models.CASCADE, models.PROTECT, models.RESTRICT})

# -- blocker categories -----------------------------------------------------

#: A real record depends on something a purge would have to remove.
BLOCKED_BY_REAL_REFERENCE = "BLOCKED_BY_REAL_REFERENCE"
#: A document under a test Matter is under a legal hold.
BLOCKED_BY_LEGAL_HOLD = "BLOCKED_BY_LEGAL_HOLD"
#: A TEST Matter that is not natively created. The database constraint should
#: make this impossible; the planner checks anyway, because a plan that trusted
#: the constraint would have nothing to say on the day the constraint was
#: missing (brief 38).
BLOCKED_INVALID_TEST_CLASSIFICATION = "BLOCKED_INVALID_TEST_CLASSIFICATION"


@dataclass(frozen=True, order=True)
class RowGroup:
    """One model's contribution to the plan."""

    label: str
    count: int
    behaviour: str
    append_only: bool = False


@dataclass(frozen=True, order=True)
class Blocker:
    """One reason a future purge would have to refuse."""

    category: str
    label: str
    count: int
    detail: str = ""


@dataclass(frozen=True)
class EvidenceSummary:
    """Canonical evidence objects held by rows a purge would remove.

    Counted through ``app.documents.references.EVIDENCE_REFERENCES`` rather than
    through a second definition of "evidence", so a future holder of evidence
    bytes is picked up here at the moment it is registered there — and so
    ``OpinionArchiveBinary`` is excluded for the right reason. It is a
    registered holder; it is simply never *owned* by a Matter, so no test Matter
    can ever bring one into this set (brief 35, 58).
    """

    label: str
    objects: int
    distinct_keys: int
    total_bytes: int


@dataclass(frozen=True)
class PurgePlan:
    test_matters: int
    owned: tuple[RowGroup, ...] = ()
    evidence: tuple[EvidenceSummary, ...] = ()
    derivative_objects: int = 0
    blockers: tuple[Blocker, ...] = ()
    unreachable_by_design: tuple[str, ...] = ()

    @property
    def is_blocked(self) -> bool:
        return bool(self.blockers)

    @property
    def append_only_rows(self) -> tuple[RowGroup, ...]:
        """Rows the database itself will not allow anything to delete.

        Not a blocker — a plan is not wrong for containing audit history, and
        every Matter has some from the moment it was created. It is a *deletion
        dependency*, and it is the reason this branch stops at planning: whether
        a test Matter's audit trail may be removed at all, and under what
        protocol, is an architecture decision and not a flag on a utility
        command (brief 39, 41).
        """
        return tuple(group for group in self.owned if group.append_only)

    @property
    def total_owned_rows(self) -> int:
        return sum(group.count for group in self.owned)

    def count_of(self, label: str) -> int:
        """Rows inventoried for one model label, or zero."""
        for group in self.owned:
            if group.label == label:
                return group.count
        return 0


@dataclass
class _Collected:
    """Primary keys reached, per model, during the ownership walk."""

    ids: dict[str, set[Any]] = field(default_factory=dict)
    behaviour: dict[str, str] = field(default_factory=dict)
    models: dict[str, type[models.Model]] = field(default_factory=dict)


def _chunked(values: Sequence[Any], size: int = CHUNK) -> Iterator[Sequence[Any]]:
    for start in range(0, len(values), size):
        yield values[start : start + size]


def _reverse_relations(model: type[models.Model]) -> list[Any]:
    """Every relation pointing *at* this model, hidden ones included.

    ``include_hidden`` matters twice. It is how the implicit through tables
    behind ``Matter.policy_areas`` and ``Matter.collaborators`` are found — they
    have no accessor, and a walk that missed them would under-report every test
    Matter's rows. And it is how a ``related_name="+"`` relation such as
    ``Document.current_version`` is found, which is one of the pointers the
    cross-boundary check has to look at.
    """
    return [
        relation
        for relation in model._meta.get_fields(include_hidden=True)
        if (relation.one_to_many or relation.one_to_one)
        and relation.auto_created
        and not relation.concrete
    ]


def _behaviour_name(on_delete: Any) -> str:
    return {
        models.CASCADE: "CASCADE",
        models.PROTECT: "PROTECT",
        models.RESTRICT: "RESTRICT",
        models.SET_NULL: "SET_NULL",
        models.DO_NOTHING: "DO_NOTHING",
    }.get(on_delete, getattr(on_delete, "__name__", str(on_delete)))


def _referring_ids(relation: Any, ids: Sequence[Any]) -> set[Any]:
    """Primary keys of rows whose foreign key lands on one of ``ids``."""
    child = relation.related_model
    found: set[Any] = set()
    for chunk in _chunked(ids):
        found.update(
            child._base_manager.filter(**{f"{relation.field.name}__in": chunk}).values_list(
                "pk", flat=True
            )
        )
    return found


def _collect_owned(matter_ids: Sequence[Any]) -> _Collected:
    """Breadth-first over ownership relations, starting at the test Matters."""
    collected = _Collected()
    label = Matter._meta.label
    collected.ids[label] = set(matter_ids)
    collected.behaviour[label] = "ROOT"
    collected.models[label] = Matter

    queue: list[tuple[type[models.Model], list[Any]]] = [(Matter, list(matter_ids))]
    while queue:
        model, ids = queue.pop(0)
        if not ids:
            continue
        for relation in _reverse_relations(model):
            if relation.on_delete not in OWNING_BEHAVIOURS:
                continue
            child = relation.related_model
            child_label = child._meta.label
            found = _referring_ids(relation, ids)
            if not found:
                continue
            known = collected.ids.setdefault(child_label, set())
            collected.models.setdefault(child_label, child)
            # A model reached by two relations keeps the stricter description:
            # PROTECT is what a future purge would have to solve for.
            behaviour = _behaviour_name(relation.on_delete)
            if collected.behaviour.get(child_label) != "PROTECT":
                collected.behaviour[child_label] = behaviour
            fresh = found - known
            known.update(found)
            if fresh:
                queue.append((child, sorted(fresh, key=str)))
    return collected


def _outside_references(collected: _Collected) -> list[Blocker]:
    """Rows outside the owned set that point into it.

    This is the check the whole exercise exists for. A ``DocumentVersion``
    sitting under a test Matter can be the ``final_version`` a *real* Matter's
    submission stands on; a real search row can hang off a test fragment. Every
    one of those is a real record that depends on something a purge would
    remove (brief 36).

    All three deletion behaviours are refused, not only the loud one. A cascade
    would destroy the real row, a PROTECT would abort the purge halfway, and a
    cleared pointer would silently change a real record without anything
    failing. None of the three is something a maintenance command may do on its
    own authority, and the quiet one is the worst of them.
    """
    blockers: list[Blocker] = []
    for label in sorted(collected.ids):
        model = collected.models[label]
        owned_ids = collected.ids[label]
        if not owned_ids:
            continue
        target = sorted(owned_ids, key=str)
        for relation in _reverse_relations(model):
            child_label = relation.related_model._meta.label
            outside = _referring_ids(relation, target) - collected.ids.get(child_label, set())
            if not outside:
                continue
            blockers.append(
                Blocker(
                    category=BLOCKED_BY_REAL_REFERENCE,
                    label=f"{child_label}.{relation.field.name}",
                    count=len(outside),
                    detail=(
                        f"{_behaviour_name(relation.on_delete)} onto {label}; "
                        "referring row is outside the test set"
                    ),
                )
            )
    return blockers


def _straddling_rows(collected: _Collected) -> list[Blocker]:
    """Owned rows that also anchor something outside the test set.

    The reverse-relation pass above finds real rows pointing *in*. This finds
    the harder case: a row that is legitimately owned by a test Matter and is
    at the same time the only record of a fact about real data.

    ``EmailAttachmentLink`` is the concrete one. It says "this exact binary
    arrived inside that exact message", and it holds two versions under
    ``PROTECT``. If the attachment sits under a test Matter and the email it
    came in sits under a real one, the link is reached from the test side and
    looks owned — but deleting it destroys provenance for the real email that
    no parser can recompute once it has moved on.

    So a row is only owned if every forward key it holds *into a model this plan
    already owns* lands inside the set. One that straddles the boundary blocks.
    Keys to shared vocabulary — a user, an organisation, a tag, an archive
    binary — are not considered, because those models never enter the owned
    inventory in the first place (Agent-C brief 36).
    """
    blockers: list[Blocker] = []
    for label in sorted(collected.ids):
        model = collected.models[label]
        ids = sorted(collected.ids[label], key=str)
        if not ids:
            continue
        for key in model._meta.get_fields():
            if not (key.is_relation and getattr(key, "concrete", False)):
                continue
            if not (key.many_to_one or key.one_to_one):
                continue
            related = key.related_model
            if related is None or related._meta.label not in collected.ids:
                continue
            # Narrowed for the type checker: `get_fields` is typed as returning
            # the union with reverse relations, and the two guards above have
            # already excluded those.
            column = cast(Any, key).attname
            inside = collected.ids[related._meta.label]
            outside = 0
            for chunk in _chunked(ids):
                targets = model._base_manager.filter(pk__in=chunk).values_list(column, flat=True)
                outside += sum(1 for target in targets if target and target not in inside)
            if not outside:
                continue
            blockers.append(
                Blocker(
                    category=BLOCKED_BY_REAL_REFERENCE,
                    label=f"{label}.{key.name}",
                    count=outside,
                    detail=(
                        f"test-owned row also holds a {related._meta.label} outside the test set"
                    ),
                )
            )
    return blockers


def _legal_holds(collected: _Collected) -> list[Blocker]:
    """A legal hold outlives a data class.

    Marking a record as development data is a statement about what it is about.
    It is not, and can never be, permission to destroy something the
    organisation has been told to preserve (brief 37).
    """
    from app.documents.models import Document

    ids = sorted(collected.ids.get(Document._meta.label, set()), key=str)
    if not ids:
        return []
    held = 0
    for chunk in _chunked(ids):
        held += Document._base_manager.filter(pk__in=chunk, legal_hold=True).count()
    if not held:
        return []
    return [
        Blocker(
            category=BLOCKED_BY_LEGAL_HOLD,
            label=Document._meta.label,
            count=held,
            detail="legal_hold is set on a document under a test matter",
        )
    ]


def _invalid_classification(matters: models.QuerySet[Matter]) -> list[Blocker]:
    invalid = matters.exclude(origin=MatterOrigin.NATIVE).count()
    if not invalid:
        return []
    return [
        Blocker(
            category=BLOCKED_INVALID_TEST_CLASSIFICATION,
            label=Matter._meta.label,
            count=invalid,
            detail="data_class=TEST on a matter whose origin is not NATIVE",
        )
    ]


def _evidence(collected: _Collected) -> list[EvidenceSummary]:
    """Evidence objects held by owned rows, per registered holder.

    Sizes and keys are read from the columns the reference registry names.
    Nothing opens, hashes or touches a stored object: the plan reports what the
    database says is out there, and the bytes are not this command's business
    (brief 34).
    """
    summaries: list[EvidenceSummary] = []
    for reference in EVIDENCE_REFERENCES:
        model = reference.model()
        ids = sorted(collected.ids.get(model._meta.label, set()), key=str)
        if not ids:
            continue
        objects = 0
        total = 0
        keys: set[str] = set()
        for chunk in _chunked(ids):
            rows = model._base_manager.filter(pk__in=chunk).values_list(
                reference.field, reference.size_field
            )
            for key, size in rows:
                objects += 1
                total += size or 0
                if key:
                    keys.add(key)
        summaries.append(
            EvidenceSummary(
                label=reference.label,
                objects=objects,
                distinct_keys=len(keys),
                total_bytes=total,
            )
        )
    return sorted(summaries, key=lambda summary: summary.label)


def _derivative_objects(collected: _Collected) -> int:
    """Derived files, counted separately because they are a different promise.

    Derivatives live in their own storage class and are rebuildable from the
    evidence, which is why the evidence-reference registry deliberately excludes
    them (docs/adr/0014). They are still stored objects a purge would leave
    behind, so the plan says how many.
    """
    from app.documents.models import DocumentDerivative

    ids = sorted(collected.ids.get(DocumentDerivative._meta.label, set()), key=str)
    keys: set[str] = set()
    for chunk in _chunked(ids):
        for key in DocumentDerivative._base_manager.filter(pk__in=chunk).values_list(
            "storage_key", flat=True
        ):
            if key:
                keys.add(key)
    return len(keys)


#: Named in the report so a reader can see these were considered rather than
#: forgotten. None of them is reachable from a Matter by a reverse relation, and
#: that is the guarantee — not a filter somebody has to remember to apply.
NEVER_OWNED: tuple[str, ...] = (
    "legacy_import.ImportBatch",
    "legacy_import.LegacySourcePage",
    "legacy_import.OpinionArchiveBinary",
    "legacy_import.OpinionArchiveItem",
    "matters.MatterReferenceSequence",
)


def test_matter_queryset(references: Iterable[str] | None = None) -> models.QuerySet[Matter]:
    """The plan population: every TEST Matter, or only the named ones.

    Never a title match. Testness is the stored class and nothing else — a
    planner that also swept up matters whose title happened to start with TEST
    would be reinventing the convention this feature exists to replace
    (brief 32).
    """
    matters = Matter.objects.test_data()
    names = list(references or [])
    if not names:
        return matters

    condition = models.Q(pk__in=[])
    for name in names:
        parsed = Matter.parse_reference(name)
        if parsed is not None:
            condition |= models.Q(reference_year=parsed[0], reference_number=parsed[1])
            continue
        try:
            condition |= models.Q(pk=uuid.UUID(name))
        except ValueError as error:
            raise ValueError(f"Ei ole UUID ega viide kujul YYYY_N: {name!r}") from error
    return matters.filter(condition)


def build_purge_plan(references: Iterable[str] | None = None) -> PurgePlan:
    """Inventory what a purge would touch. Reads only.

    Deterministic by construction: every collection is a set of primary keys,
    every reported sequence is sorted by a stable label, and no count depends on
    the order the graph happened to be walked. Two runs against the same
    database state produce the same plan, which is what makes a plan taken
    before a change comparable with one taken after (brief 44).
    """
    matters = test_matter_queryset(references)
    matter_ids = sorted(matters.values_list("pk", flat=True), key=str)

    collected = _collect_owned(matter_ids)
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
        _invalid_classification(matters)
        + _legal_holds(collected)
        + (_outside_references(collected) if matter_ids else [])
        + (_straddling_rows(collected) if matter_ids else [])
    )

    return PurgePlan(
        test_matters=len(matter_ids),
        owned=owned,
        evidence=tuple(_evidence(collected)),
        derivative_objects=_derivative_objects(collected),
        blockers=tuple(sorted(blockers)),
        unreachable_by_design=NEVER_OWNED,
    )
