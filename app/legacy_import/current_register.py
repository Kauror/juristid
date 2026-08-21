"""Turning the maintained current register into current Juristid work.

The historical import is deliberately conservative: it proposes ``FULL`` only
where a reviewed override file says so, and everything else lands as ``ARCHIVE``
with the proposal kept in the ledger. That was right for a decade of history —
"active at cutover" is a human decision, not an algorithm (specification 19.5) —
and it stays exactly as it is. Nothing in this module changes how an import
behaves.

What it adds is a separate, explicit operation for one specific thing the
product owner has now decided: the year the department is *currently working
in* should be represented as current work rather than as archive. That decision
is about one register year, it was made by a person, and it therefore lives in
its own reviewed command rather than inside a generic importer.

Four properties make it safe to run:

**Source authority, not recency.** A candidate is a Matter with a genuine Excel
source reference for the requested sheet. Nothing qualifies because it was
imported recently, and a OneNote-only Matter or a natively created one never
qualifies at all.

**Explicit closure is respected.** A row whose status meant Koda stopped is not
promoted, no matter how recent it is. A closed ``FULL`` Matter would need a
closure timestamp the register never recorded, and there is no honest way to
produce one.

**Nothing is fabricated.** Promotion moves ``record_mode`` and leaves every
other field as the register left it. No next action is invented from
``JÄRGMISEKS`` free text, no submission from a ``VÄLJA`` date, no outcome and no
closure. A promoted Matter with no instruction shows *Järgmiseks puudub*, which
is the truth.

**One reviewed year at a time.** :data:`REVIEWED_CURRENT_YEARS` is the list of
years a person has actually decided about. Any other year may be analysed with
``--dry-run`` and cannot be applied, so the 2026 decision cannot leak backwards
into a decade nobody reviewed (Stage-2F brief 22).
"""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass, field
from typing import Any

from django.db import transaction

from app.legacy_import.enums import Anomaly, ProposedRecordMode, RowOutcome
from app.legacy_import.models import ImportRowLedger, MatterSourceReference
from app.legacy_import.parser import SOURCE_SYSTEM
from app.legacy_import.resolution import resolve_status
from app.legacy_import.source_cells import contracts_by_sheet, source_cell
from app.matters.enums import MatterOrigin, RecordMode
from app.matters.models import Matter
from app.matters.services import promote_matter_to_full
from app.search.indexing import indexable_matters, refresh_matters

#: Bumped when this operation's rules change. Recorded on every promotion.
PROMOTION_VERSION = "2F.1.0"

#: The register years a person has decided should represent current work.
#:
#: One entry today. Extending this tuple is the whole of the change needed when
#: the department decides about another year — and it is a reviewed code change
#: with a diff, rather than a flag somebody passes at three in the afternoon
#: (Stage-2F brief 22, docs/open-decisions.md).
REVIEWED_CURRENT_YEARS: tuple[int, ...] = (2026,)

#: Ledger anomalies that mean the row and the Matter are not settled.
_BLOCKING_ANOMALIES: frozenset[str] = frozenset(
    {
        Anomaly.SOURCE_DISAGREES_WITH_MATTER.value,
        Anomaly.REFERENCE_CONFLICTS_WITH_NATIVE.value,
        Anomaly.DUPLICATE_REFERENCE.value,
        Anomaly.INVALID_REFERENCE.value,
        Anomaly.REFERENCE_YEAR_MISMATCH.value,
    }
)


class Classification:
    """What the operation would do with one candidate Matter."""

    PROMOTE = "PROMOTE"
    ALREADY_FULL = "ALREADY_FULL"
    EXPLICITLY_CLOSED = "EXPLICITLY_CLOSED"
    NATIVE_SKIP = "NATIVE_SKIP"
    CONFLICT = "CONFLICT"
    INSUFFICIENT_SOURCE = "INSUFFICIENT_SOURCE"
    REVIEW_REQUIRED = "REVIEW_REQUIRED"


CLASSIFICATIONS: tuple[str, ...] = (
    Classification.PROMOTE,
    Classification.ALREADY_FULL,
    Classification.EXPLICITLY_CLOSED,
    Classification.NATIVE_SKIP,
    Classification.CONFLICT,
    Classification.INSUFFICIENT_SOURCE,
    Classification.REVIEW_REQUIRED,
)


@dataclass(frozen=True)
class SourceEvidence:
    """What the register rows behind one Matter say about its current state."""

    sheets: tuple[str, ...]
    eras: tuple[str, ...]
    reference_ids: tuple[Any, ...]
    status_labels: tuple[str, ...]
    next_action_texts: tuple[str, ...]
    says_closed: bool
    conflicted: bool

    @property
    def has_next_action_text(self) -> bool:
        return any(text.strip() for text in self.next_action_texts)

    @property
    def has_status_label(self) -> bool:
        return any(label.strip() for label in self.status_labels)


@dataclass(frozen=True)
class Candidate:
    """One Matter the register year names, with its classification and why."""

    matter: Matter
    classification: str
    reason: str
    evidence: SourceEvidence
    full_candidate_ledger: bool = False

    @property
    def promotes(self) -> bool:
        return self.classification == Classification.PROMOTE

    def provenance(self, year: int) -> dict[str, Any]:
        """The audit payload for this promotion.

        Identifiers and interpretation only. The status label and the
        ``JÄRGMISEKS`` text are source content and stay on the source
        references this points at (Stage-2F brief 20).
        """
        return {
            "operation": "promote_current_register",
            "operation_version": PROMOTION_VERSION,
            "source_system": SOURCE_SYSTEM,
            "source_year": year,
            "source_eras": list(self.evidence.eras),
            "source_references": [str(value) for value in self.evidence.reference_ids],
            "rule": self.reason,
            "ledger_proposed_full_candidate": self.full_candidate_ledger,
        }


@dataclass
class PromotionPlan:
    year: int
    candidates: list[Candidate] = field(default_factory=list)

    @property
    def counts(self) -> dict[str, int]:
        tally = Counter(candidate.classification for candidate in self.candidates)
        return {name: tally.get(name, 0) for name in CLASSIFICATIONS}

    @property
    def promotable(self) -> list[Candidate]:
        return [candidate for candidate in self.candidates if candidate.promotes]

    @property
    def is_reviewed_year(self) -> bool:
        return self.year in REVIEWED_CURRENT_YEARS


# ---------------------------------------------------------------------------
# Gathering the evidence
# ---------------------------------------------------------------------------


def _evidence_for(references: list[MatterSourceReference], contracts: Any) -> SourceEvidence:
    statuses: list[str] = []
    next_actions: list[str] = []
    says_closed = False
    conflicted = False

    for reference in references:
        if reference.conflict_state not in {"", "NONE", "RESOLVED_BY_REVIEW"}:
            conflicted = True
        label = source_cell(reference, contracts, "legacy_status") or ""
        if label:
            statuses.append(label)
            # Read through the same era-aware vocabulary the import used, so a
            # label that meant one thing in 2024 and another in 2026 stays two
            # facts rather than one averaged guess.
            if resolve_status(label, reference.source_era).is_closure:
                says_closed = True
        next_actions.append(source_cell(reference, contracts, "next_action_text") or "")

    return SourceEvidence(
        sheets=tuple(sorted({r.source_sheet for r in references})),
        eras=tuple(sorted({r.source_era for r in references if r.source_era})),
        reference_ids=tuple(r.pk for r in references),
        status_labels=tuple(statuses),
        next_action_texts=tuple(next_actions),
        says_closed=says_closed,
        conflicted=conflicted,
    )


def _ledger_signals(matter_ids: list[Any]) -> tuple[set[Any], set[Any]]:
    """Which Matters the import proposed as FULL candidates, and which it flagged.

    Read from :class:`ImportRowLedger`, which is append-only: it records what
    the import decided about a row at the time, and that decision is evidence
    about the row rather than something a later run can quietly restate.
    """
    proposed: set[Any] = set()
    flagged: set[Any] = set()
    entries = ImportRowLedger.objects.filter(matter_id__in=matter_ids).values(
        "matter_id", "proposed_record_mode", "outcome", "anomalies"
    )
    for entry in entries:
        if entry["proposed_record_mode"] in {
            ProposedRecordMode.FULL_CANDIDATE.value,
            ProposedRecordMode.FULL.value,
        }:
            proposed.add(entry["matter_id"])
        if entry["outcome"] == RowOutcome.REVIEW_REQUIRED.value:
            flagged.add(entry["matter_id"])
        if set(entry["anomalies"] or []) & _BLOCKING_ANOMALIES:
            flagged.add(entry["matter_id"])
    return proposed, flagged


# ---------------------------------------------------------------------------
# Classifying
# ---------------------------------------------------------------------------


def _classify(
    matter: Matter,
    evidence: SourceEvidence,
    *,
    proposed_full: bool,
    flagged: bool,
) -> Candidate:
    def decided(classification: str, reason: str) -> Candidate:
        return Candidate(
            matter=matter,
            classification=classification,
            reason=reason,
            evidence=evidence,
            full_candidate_ledger=proposed_full,
        )

    if matter.origin == MatterOrigin.NATIVE:
        return decided(
            Classification.NATIVE_SKIP,
            "Süsteemis loodud teema; importija ei kirjuta seda üle.",
        )
    if matter.origin == MatterOrigin.LEGACY_ONENOTE:
        # OneNote provenance and a register row on the same Matter is a
        # reconciliation somebody should look at, not a promotion to make
        # quietly (Stage-2F brief 14, 17).
        return decided(
            Classification.REVIEW_REQUIRED,
            "Teema päritolu on OneNote, kuid tal on ka registririda; identiteet on lahendamata.",
        )
    if matter.record_mode == RecordMode.FULL:
        return decided(Classification.ALREADY_FULL, "Kirje on juba täielik.")
    if evidence.conflicted:
        return decided(
            Classification.CONFLICT,
            "Allikaviide on märgitud vastuoluliseks; sidumine ei ole kinnitatud.",
        )
    if flagged:
        return decided(
            Classification.REVIEW_REQUIRED,
            "Impordi kanne jättis rea ülevaatusse; enne aktiveerimist otsustab inimene.",
        )
    if not matter.is_open or evidence.says_closed:
        return decided(
            Classification.EXPLICITLY_CLOSED,
            "Allikas ütleb, et töö on lõpetatud; sulgemise kuupäeva ei ole ja seda ei mõelda "
            "välja.",
        )

    # The one place the 2026 decision is broader than the migration default: a
    # genuine register row that is not closed counts as current work. It still
    # has to *say* something — a row with a title and nothing else would become
    # an empty active Matter, which helps nobody (Stage-2F brief 16, 17).
    substance = any(
        (
            matter.owner_id is not None,
            matter.stage_id is not None,
            matter.received_date is not None,
            matter.response_deadline is not None,
            evidence.has_next_action_text,
            evidence.has_status_label,
            proposed_full,
        )
    )
    if not substance:
        return decided(
            Classification.INSUFFICIENT_SOURCE,
            "Registrireal ei ole peale pealkirja ühtki tunnust; aastaarv üksi ei tee teemat "
            "aktiivseks.",
        )

    reason = (
        "Impordi kanne pakkus täieliku kirje kandidaati."
        if proposed_full
        else "Ülevaadatud registriaasta rida, mida allikas lõpetatuks ei märgi."
    )
    return decided(Classification.PROMOTE, reason)


def build_promotion_plan(*, year: int) -> PromotionPlan:
    """Decide what promoting one register year would do. Writes nothing."""
    contracts = contracts_by_sheet()
    sheet = str(year)

    references = list(
        MatterSourceReference.objects.filter(
            source_system=SOURCE_SYSTEM, source_sheet=sheet
        ).select_related("matter", "matter__stage")
    )

    by_matter: dict[Any, list[MatterSourceReference]] = {}
    matters: dict[Any, Matter] = {}
    for reference in references:
        matters.setdefault(reference.matter_id, reference.matter)
        by_matter.setdefault(reference.matter_id, []).append(reference)

    proposed_full, flagged = _ledger_signals(list(matters))

    plan = PromotionPlan(year=year)
    for matter_id, matter in sorted(
        matters.items(), key=lambda item: (item[1].reference_number or 0, str(item[0]))
    ):
        evidence = _evidence_for(by_matter[matter_id], contracts)
        plan.candidates.append(
            _classify(
                matter,
                evidence,
                proposed_full=matter_id in proposed_full,
                flagged=matter_id in flagged,
            )
        )
    return plan


# ---------------------------------------------------------------------------
# Applying
# ---------------------------------------------------------------------------


class UnreviewedYear(Exception):
    """Applying a year nobody has decided about."""


@dataclass(frozen=True)
class PromotionResult:
    year: int
    promoted: int
    examined: int


@transaction.atomic
def apply_promotion_plan(plan: PromotionPlan, *, actor: Any = None) -> PromotionResult:
    """Promote what the plan found. One transaction, or none of it.

    Refuses outright for a year outside :data:`REVIEWED_CURRENT_YEARS`. The
    2026 decision is about 2026; extending it to a decade of history because
    the command happens to take a ``--year`` would be the same mistake the
    conservative importer exists to avoid.
    """
    if not plan.is_reviewed_year:
        raise UnreviewedYear(
            f"{plan.year} is not a reviewed current-register year. "
            f"Reviewed years: {', '.join(str(y) for y in REVIEWED_CURRENT_YEARS)}. "
            "Analyse it with --dry-run; applying needs a decision recorded in "
            "REVIEWED_CURRENT_YEARS, not a flag."
        )

    promoted = 0
    touched: list[Any] = []
    for candidate in plan.promotable:
        # Re-read under the transaction: a plan is minutes old, and somebody
        # may have promoted or closed this in between. Re-running must be a
        # no-op, not a second promotion.
        matter = Matter.objects.select_for_update().get(pk=candidate.matter.pk)
        if matter.record_mode == RecordMode.FULL or not matter.is_open:
            continue
        promote_matter_to_full(
            matter=matter,
            actor=actor,
            reason=candidate.reason,
            provenance=candidate.provenance(plan.year),
        )
        touched.append(matter.pk)
        promoted += 1

    # Batched, once, after the writes. Search is a derived layer.
    if touched:
        refresh_matters(indexable_matters().filter(pk__in=touched))

    return PromotionResult(year=plan.year, promoted=promoted, examined=len(plan.candidates))


# ---------------------------------------------------------------------------
# Reporting
# ---------------------------------------------------------------------------


def summary(plan: PromotionPlan) -> dict[str, Any]:
    """Aggregate counts only. No titles, no names, no source text.

    The field-completeness figures describe the population that *would be
    promoted*, because that is the set whose data quality the department is
    about to start working with. They are counts of rows, not a judgement about
    anybody's work.
    """
    would = plan.promotable
    return {
        "operation": "promote_current_register",
        "operation_version": PROMOTION_VERSION,
        "year": plan.year,
        "reviewed_year": plan.is_reviewed_year,
        "source_matters": len(plan.candidates),
        "classifications": plan.counts,
        "would_promote": len(would),
        "of_which": {
            "owner_populated": sum(1 for c in would if c.matter.owner_id is not None),
            "owner_unresolved": sum(1 for c in would if c.matter.owner_id is None),
            "stage_populated": sum(1 for c in would if c.matter.stage_id is not None),
            "stage_unresolved": sum(1 for c in would if c.matter.stage_id is None),
            "with_source_next_action": sum(1 for c in would if c.evidence.has_next_action_text),
            "without_source_next_action": sum(
                1 for c in would if not c.evidence.has_next_action_text
            ),
            "with_response_deadline": sum(
                1 for c in would if c.matter.response_deadline is not None
            ),
            "without_response_deadline": sum(
                1 for c in would if c.matter.response_deadline is None
            ),
        },
        "source_signals": {
            "explicit_closure": plan.counts[Classification.EXPLICITLY_CLOSED],
            "mapped_stage": sum(1 for c in plan.candidates if c.matter.stage_id is not None),
            "nonblank_next_action": sum(
                1 for c in plan.candidates if c.evidence.has_next_action_text
            ),
            "deterministic_owner": sum(1 for c in plan.candidates if c.matter.owner_id is not None),
            "full_candidate_ledger": sum(1 for c in plan.candidates if c.full_candidate_ledger),
            "already_full": plan.counts[Classification.ALREADY_FULL],
        },
    }


__all__ = [
    "CLASSIFICATIONS",
    "PROMOTION_VERSION",
    "REVIEWED_CURRENT_YEARS",
    "Candidate",
    "Classification",
    "PromotionPlan",
    "PromotionResult",
    "SourceEvidence",
    "UnreviewedYear",
    "apply_promotion_plan",
    "build_promotion_plan",
    "summary",
]
