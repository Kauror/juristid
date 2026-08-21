"""Restoring ``VASTUTAJA`` to Matters that already imported without it.

The register named a responsible lawyer on almost every row. The Matters
imported from it very largely have no owner, and the reason was never the
source: the resolver compared a first name against a full display name, so the
commonest shape in the column matched nothing
(:mod:`app.legacy_import.resolution`).

This module repairs that **from the provenance already stored**, not by
re-running the import. Every answer is read back out of
``MatterSourceReference.source_row_raw`` — the verbatim cells, write-once and
trigger-protected — through the reviewed era contract that says which column
carried the owner in that year. Nothing re-reads a workbook, nothing touches
source bytes, and the correction is a change to an *interpreted* field, which
is exactly the direction the provenance design allows (specification 19.3).

Three rules hold throughout.

**Only ever fill a hole.** A Matter that already has an owner is left alone,
whoever put it there. A backfill that could overwrite a human decision is a
backfill nobody can safely run twice.

**Disagreement is not a tie to break.** Where two source rows for one Matter
name two different people, the answer is that nobody knows, and the run says
so. Choosing the later row would be an inference dressed as a fact.

**Every assignment is auditable.** The method, the era, the sheet, the row and
the operation version go onto the ``MATTER_ASSIGNED`` change event, so a
reviewer can tell a mapped answer from an inferred one months later.
"""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass, field
from typing import Any

from django.db import transaction

from app.accounts.models import User
from app.legacy_import.contracts import EraContract
from app.legacy_import.models import MatterSourceReference
from app.legacy_import.parser import SOURCE_SYSTEM
from app.legacy_import.resolution import (
    DETERMINISTIC_OWNER_METHODS,
    METHOD_AMBIGUOUS,
    METHOD_BLANK,
    METHOD_EXACT,
    METHOD_GIVEN_NAME,
    METHOD_MAPPING,
    METHOD_MULTI_PERSON,
    KnownPeople,
    MappingTables,
    resolve_owner,
)
from app.legacy_import.source_cells import contracts_by_sheet, source_cell
from app.matters.models import Matter
from app.matters.services import assign_matter
from app.search.indexing import indexable_matters, refresh_matters

#: Bumped when this operation's rules change. Recorded on every assignment, so
#: a row can be traced back to the version of the rules that produced it.
BACKFILL_VERSION = "2F.1.0"


class Outcome:
    """What the backfill would do with one Matter. Exactly one per Matter."""

    ALREADY_OWNED = "ALREADY_OWNED"
    WOULD_ASSIGN = "WOULD_ASSIGN"
    NO_SOURCE_OWNER = "NO_SOURCE_OWNER"
    AMBIGUOUS = "AMBIGUOUS"
    MULTI_PERSON = "MULTI_PERSON"
    CONFLICTING_SOURCES = "CONFLICTING_SOURCES"
    UNKNOWN_OWNER_VALUE = "UNKNOWN_OWNER_VALUE"
    NO_CONTRACT = "NO_CONTRACT"


#: Reported in this order, most actionable first.
OUTCOMES: tuple[str, ...] = (
    Outcome.WOULD_ASSIGN,
    Outcome.ALREADY_OWNED,
    Outcome.NO_SOURCE_OWNER,
    Outcome.CONFLICTING_SOURCES,
    Outcome.AMBIGUOUS,
    Outcome.MULTI_PERSON,
    Outcome.UNKNOWN_OWNER_VALUE,
    Outcome.NO_CONTRACT,
)


@dataclass(frozen=True)
class Observation:
    """One source row's opinion about who owned a Matter."""

    reference_id: Any
    sheet: str
    era: str
    row_number: int | None
    raw_value: str
    owner: User | None
    method: str

    @property
    def is_blank(self) -> bool:
        return self.method == METHOD_BLANK

    @property
    def is_deterministic(self) -> bool:
        return self.owner is not None and self.method in DETERMINISTIC_OWNER_METHODS


@dataclass(frozen=True)
class MatterOwnerPlan:
    """What would happen to one Matter, and why."""

    matter: Matter
    outcome: str
    owner: User | None = None
    method: str = ""
    reason: str = ""
    observations: tuple[Observation, ...] = ()

    @property
    def assigns(self) -> bool:
        return self.outcome == Outcome.WOULD_ASSIGN and self.owner is not None

    @property
    def provenance(self) -> dict[str, Any]:
        """The audit payload for this assignment.

        Deliberately identifiers and interpretation only. The raw owner cell is
        source content and stays on the source reference the ``source`` list
        points at; copying it into a change-event payload would spread private
        register text into a table that is read far more widely (Stage-2F
        brief 9).
        """
        deciding = [o for o in self.observations if o.is_deterministic]
        return {
            "operation": "backfill_legacy_owners",
            "operation_version": BACKFILL_VERSION,
            "source_system": SOURCE_SYSTEM,
            "resolution_method": self.method,
            "source_eras": sorted({o.era for o in deciding if o.era}),
            "source": [
                {"reference_id": str(o.reference_id), "sheet": o.sheet, "row": o.row_number}
                for o in deciding
            ],
        }


@dataclass
class BackfillPlan:
    """Every Matter examined, with one outcome each."""

    plans: list[MatterOwnerPlan] = field(default_factory=list)

    @property
    def counts(self) -> dict[str, int]:
        tally = Counter(plan.outcome for plan in self.plans)
        return {outcome: tally.get(outcome, 0) for outcome in OUTCOMES}

    @property
    def method_counts(self) -> dict[str, int]:
        tally = Counter(plan.method for plan in self.plans if plan.assigns)
        methods = (METHOD_MAPPING, METHOD_EXACT, METHOD_GIVEN_NAME)
        return {method: tally.get(method, 0) for method in methods}

    @property
    def assignable(self) -> list[MatterOwnerPlan]:
        return [plan for plan in self.plans if plan.assigns]

    @property
    def unresolved_values(self) -> Counter[str]:
        """Distinct source owner values nobody could identify, with counts.

        Source content. Useful to an operator writing a mapping file and
        therefore never printed by default and never written anywhere but
        ignored local storage (Stage-2F brief 10, 50).
        """
        tally: Counter[str] = Counter()
        interesting = {
            Outcome.AMBIGUOUS,
            Outcome.MULTI_PERSON,
            Outcome.UNKNOWN_OWNER_VALUE,
            Outcome.CONFLICTING_SOURCES,
        }
        for plan in self.plans:
            if plan.outcome not in interesting:
                continue
            for observation in plan.observations:
                if observation.raw_value and not observation.is_deterministic:
                    tally[observation.raw_value] += 1
        return tally


# ---------------------------------------------------------------------------
# Reading the owner cell back out of stored provenance
# ---------------------------------------------------------------------------


def owner_cell(reference: MatterSourceReference, contracts: dict[str, EraContract]) -> str | None:
    """The raw ``VASTUTAJA`` text for one source reference, or ``None``.

    Looked up **through the era contract**, never at a fixed letter.
    ``VASTUTAJA`` is column H on the current sheet and is not column H in every
    year, and hard-coding one year's layout is how a decade of owners would be
    read out of the wrong column (Stage-2F brief 7).
    """
    return source_cell(reference, contracts, "owner_name")


def _observe(
    reference: MatterSourceReference,
    contracts: dict[str, EraContract],
    mappings: MappingTables,
    people: KnownPeople,
) -> Observation | None:
    cell = owner_cell(reference, contracts)
    if cell is None:
        return None
    resolution = resolve_owner(cell, mappings, people)
    return Observation(
        reference_id=reference.pk,
        sheet=reference.source_sheet,
        era=reference.source_era,
        row_number=reference.source_row_number,
        raw_value=cell.strip(),
        owner=resolution.value if resolution.resolved else None,
        method=resolution.method,
    )


# ---------------------------------------------------------------------------
# Planning
# ---------------------------------------------------------------------------


def _classify(matter: Matter, observations: list[Observation]) -> MatterOwnerPlan:
    """Turn one Matter's observations into exactly one outcome.

    The four cases the brief enumerates, plus the one it implies: a
    deterministic answer standing beside a *non-blank* value nobody could
    identify is not agreement. The unidentified cell may name the same person
    or somebody else, and there is no evidence which — so it is reported as a
    conflict rather than quietly resolved in favour of the half we happen to
    understand (Stage-2F brief 8).
    """
    if not observations:
        return MatterOwnerPlan(
            matter=matter,
            outcome=Outcome.NO_CONTRACT,
            reason="Ühtki allikaviidet ei õnnestunud ajastulepingu järgi lugeda.",
        )

    deterministic = [o for o in observations if o.is_deterministic]
    distinct = {o.owner.pk for o in deterministic if o.owner is not None}
    stated = [o for o in observations if not o.is_blank]
    unidentified = [o for o in stated if not o.is_deterministic]

    if len(distinct) > 1:
        return MatterOwnerPlan(
            matter=matter,
            outcome=Outcome.CONFLICTING_SOURCES,
            reason="Kaks allikarida nimetavad eri vastutajad; valikut ei tehta.",
            observations=tuple(observations),
        )

    if len(distinct) == 1 and unidentified:
        return MatterOwnerPlan(
            matter=matter,
            outcome=Outcome.CONFLICTING_SOURCES,
            reason=(
                "Üks allikarida nimetab tuvastatud vastutaja, teine nime, mida ei õnnestu "
                "tuvastada. Kas need on sama inimene, ei ole teada."
            ),
            observations=tuple(observations),
        )

    if len(distinct) == 1:
        decisive = deterministic[0]
        return MatterOwnerPlan(
            matter=matter,
            outcome=Outcome.WOULD_ASSIGN,
            owner=decisive.owner,
            method=decisive.method,
            reason=f"Allikas nimetab vastutaja ({decisive.method}).",
            observations=tuple(observations),
        )

    if not stated:
        return MatterOwnerPlan(
            matter=matter,
            outcome=Outcome.NO_SOURCE_OWNER,
            reason="Vastutaja lahter on kõikidel allikaridadel tühi.",
            observations=tuple(observations),
        )

    methods = {o.method for o in unidentified}
    if METHOD_MULTI_PERSON in methods:
        outcome, reason = (
            Outcome.MULTI_PERSON,
            "Lahter nimetab mitut inimest; jagatud vastutust ei omistata ühele.",
        )
    elif METHOD_AMBIGUOUS in methods:
        outcome, reason = (
            Outcome.AMBIGUOUS,
            "Nimele vastab mitu kasutajat; vastefail peab ütlema, kes neist.",
        )
    else:
        outcome, reason = (
            Outcome.UNKNOWN_OWNER_VALUE,
            "Nimele ei vasta ükski teadaolev kasutaja.",
        )
    return MatterOwnerPlan(
        matter=matter, outcome=outcome, reason=reason, observations=tuple(observations)
    )


def build_backfill_plan(*, mappings: MappingTables | None = None) -> BackfillPlan:
    """Decide what the backfill would do. Reads the database, writes nothing."""
    tables = mappings or MappingTables.empty()
    people = KnownPeople.load()
    contracts = contracts_by_sheet()

    references = (
        MatterSourceReference.objects.filter(source_system=SOURCE_SYSTEM)
        .select_related("matter")
        .order_by("source_sheet", "source_row_number")
    )

    by_matter: dict[Any, list[Observation]] = {}
    matters: dict[Any, Matter] = {}
    for reference in references.iterator(chunk_size=500):
        matters.setdefault(reference.matter_id, reference.matter)
        observation = _observe(reference, contracts, tables, people)
        entries = by_matter.setdefault(reference.matter_id, [])
        if observation is not None:
            entries.append(observation)

    plan = BackfillPlan()
    for matter_id, matter in matters.items():
        if matter.owner_id is not None:
            plan.plans.append(
                MatterOwnerPlan(
                    matter=matter,
                    outcome=Outcome.ALREADY_OWNED,
                    reason="Vastutaja on juba määratud; importija ei kirjuta seda üle.",
                )
            )
            continue
        plan.plans.append(_classify(matter, by_matter.get(matter_id, [])))
    return plan


# ---------------------------------------------------------------------------
# Applying
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class BackfillResult:
    assigned: int
    examined: int


@transaction.atomic
def apply_backfill_plan(plan: BackfillPlan, *, actor: Any = None) -> BackfillResult:
    """Assign the owners the plan found. One transaction, or none of them.

    Goes through :func:`app.matters.services.assign_matter` rather than writing
    the column, so the change lands in the professional timeline like any other
    assignment — with the provenance attached that says an operator command,
    not a colleague, decided it. A management command is not a licence to skip
    the domain's own audit (Stage-2F brief 9).
    """
    assigned = 0
    touched: list[Any] = []
    for candidate in plan.assignable:
        # Re-read under the transaction. The plan may have been produced minutes
        # ago, and an owner set in the meantime outranks anything here.
        matter = Matter.objects.select_for_update().get(pk=candidate.matter.pk)
        if matter.owner_id is not None:
            continue
        assign_matter(
            matter=matter,
            owner=candidate.owner,
            actor=actor,
            provenance=candidate.provenance,
        )
        touched.append(matter.pk)
        assigned += 1

    # One batched pass rather than a rebuild per Matter: search is a derived
    # layer and is refreshed after the operation, not maintained during it.
    if touched:
        refresh_matters(indexable_matters().filter(pk__in=touched))

    return BackfillResult(assigned=assigned, examined=len(plan.plans))


def summary(plan: BackfillPlan) -> dict[str, Any]:
    """Aggregate counts only. Safe to print, attach or paste anywhere."""
    counts = plan.counts
    return {
        "operation": "backfill_legacy_owners",
        "operation_version": BACKFILL_VERSION,
        "matters_examined": len(plan.plans),
        "outcomes": counts,
        "methods": plan.method_counts,
        "would_update": counts[Outcome.WOULD_ASSIGN],
        "distinct_unresolved_values": len(plan.unresolved_values),
    }


__all__ = [
    "BACKFILL_VERSION",
    "OUTCOMES",
    "BackfillPlan",
    "BackfillResult",
    "MatterOwnerPlan",
    "Observation",
    "Outcome",
    "apply_backfill_plan",
    "build_backfill_plan",
    "owner_cell",
    "summary",
]
