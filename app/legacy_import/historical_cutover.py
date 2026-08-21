"""Which imported register rows are still current work, and which are history.

The register's own answer turned out to be unusable. Closure was not recorded
before 2025 — fourteen consecutive years contain **zero** closed rows — so the
mechanical reading "not explicitly closed means still open" marks almost the
whole archive as current. Measured on the real corpus it would have activated
2354 of 2455 Matters. That is not a work portfolio, it is the archive.

Two further measurements shaped this module. No Matter appears in more than one
register year: the register opens a fresh row per sheet rather than carrying a
file forward, so there is no cross-year carry-over population to find. And
response deadlines are year-bounded — outside the current year not one row has
a deadline still in the future.

So the department decided a **default**, not a discovery: a pre-cutover
imported register Matter is no longer current unless somebody says otherwise.

What that default may and may not assert is the whole delicacy here. It moves
``is_open`` and nothing else. It does not invent a disposition, a closure
timestamp, a person who closed it, a next action or a submission, because none
of those facts exists anywhere. The resulting shape — ARCHIVE, closed, no
disposition, no ``closed_at`` — is exactly what the closure constraint was
written to permit, and it reads *historical at cutover, exact closure fact
unknown* (ADR 0020).

The exception is per-Matter and human: see
:func:`app.matters.services.reactivate_historical_matter`. Whole older years are
never activated.
"""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass, field
from typing import Any

from django.db import transaction

from app.legacy_import.models import MatterSourceReference
from app.legacy_import.parser import SOURCE_SYSTEM
from app.matters.enums import MatterOrigin, RecordMode
from app.matters.models import Matter
from app.matters.services import mark_historical_archive_inactive
from app.search.indexing import indexable_matters, refresh_matters
from app.workflow.enums import ActionStatus

#: Bumped when this operation's rules change. Recorded on every row it touches.
HISTORICAL_CUTOVER_VERSION = "2I.1.0"

#: The cutover years a person has decided about. The cutover year is the first
#: year treated as *current*; everything before it defaults to historical.
#:
#: One entry, and extending it is a reviewed code change rather than a flag —
#: the same discipline `REVIEWED_CURRENT_YEARS` uses, and for the same reason:
#: next year's rollover is a decision somebody must take deliberately, not an
#: automatic moving window that quietly retires a live file (ADR 0020).
REVIEWED_HISTORICAL_CUTOVER_YEARS: tuple[int, ...] = (2026,)

#: Origins that mean "this row came from the Excel register". A OneNote-only or
#: natively created Matter never had a register year and is not this
#: operation's business.
REGISTER_ORIGINS: frozenset[str] = frozenset(
    {MatterOrigin.LEGACY_IMPORT, MatterOrigin.PROMOTED_LEGACY}
)


class Classification:
    """What the cutover would do with one Matter. Exactly one per Matter."""

    WOULD_CLOSE_HISTORICAL = "WOULD_CLOSE_HISTORICAL"
    ALREADY_CLOSED = "ALREADY_CLOSED"
    CURRENT_EXCEPTION = "CURRENT_EXCEPTION"
    REVIEW_REQUIRED = "REVIEW_REQUIRED"
    EXCLUDED = "EXCLUDED"


#: Reported in this order, most actionable first.
CLASSIFICATIONS: tuple[str, ...] = (
    Classification.WOULD_CLOSE_HISTORICAL,
    Classification.ALREADY_CLOSED,
    Classification.CURRENT_EXCEPTION,
    Classification.REVIEW_REQUIRED,
    Classification.EXCLUDED,
)


class ReviewReason:
    """Why a row was held back. Aggregate reasons only — never row content."""

    OPEN_NEXT_ACTION = "OPEN_NEXT_ACTION"
    UNEXPECTED_ORIGIN = "UNEXPECTED_ORIGIN"
    MULTIPLE_SOURCE_YEARS = "MULTIPLE_SOURCE_YEARS"


class UnreviewedCutoverYear(Exception):
    """Applying a cutover year nobody has decided about."""


@dataclass(frozen=True)
class HistoricalCutoverCandidate:
    """One Matter, its classification, and why."""

    matter: Matter
    source_year: int
    classification: str
    reason: str = ""
    review_reason: str = ""

    @property
    def closes(self) -> bool:
        return self.classification == Classification.WOULD_CLOSE_HISTORICAL

    def provenance(self, cutover_year: int) -> dict[str, Any]:
        """Metadata for the audit payload. No titles, names or source text."""
        return {
            "operation": "historical_cutover_state",
            "operation_version": HISTORICAL_CUTOVER_VERSION,
            "cutover_year": cutover_year,
            "source_year": self.source_year,
            "rule": "pre_cutover_register_row_defaults_to_historical",
        }


@dataclass
class HistoricalCutoverPlan:
    """Every pre-cutover register Matter, with one outcome each."""

    cutover_year: int
    candidates: list[HistoricalCutoverCandidate] = field(default_factory=list)

    @property
    def is_reviewed_year(self) -> bool:
        return self.cutover_year in REVIEWED_HISTORICAL_CUTOVER_YEARS

    @property
    def counts(self) -> dict[str, int]:
        tally = Counter(c.classification for c in self.candidates)
        return {name: tally.get(name, 0) for name in CLASSIFICATIONS}

    @property
    def closable(self) -> list[HistoricalCutoverCandidate]:
        return [c for c in self.candidates if c.closes]

    @property
    def by_source_year(self) -> dict[int, dict[str, int]]:
        """Per-year classification counts, newest year first."""
        years: dict[int, Counter[str]] = {}
        for candidate in self.candidates:
            years.setdefault(candidate.source_year, Counter())[candidate.classification] += 1
        return {
            year: {name: years[year].get(name, 0) for name in CLASSIFICATIONS}
            for year in sorted(years, reverse=True)
        }

    @property
    def review_reasons(self) -> dict[str, int]:
        tally = Counter(c.review_reason for c in self.candidates if c.review_reason)
        return dict(sorted(tally.items()))


def _source_years(reference_sheets: list[str]) -> list[int]:
    return sorted({int(sheet) for sheet in reference_sheets if sheet.isdigit()})


def _classify(
    matter: Matter, source_years: list[int], *, open_action: bool
) -> HistoricalCutoverCandidate:
    """One Matter's outcome. The order of these tests is the whole safety story.

    Everything representing a decision somebody already made is checked
    *before* the default, so the default can only ever reach a row nobody has
    touched.
    """
    latest = max(source_years)

    # A Matter somebody activated is current work, whatever year it came from.
    # Re-running the cutover after a manual carry-over attestation must leave
    # that attestation standing.
    if matter.record_mode != RecordMode.ARCHIVE:
        return HistoricalCutoverCandidate(
            matter=matter,
            source_year=latest,
            classification=Classification.CURRENT_EXCEPTION,
            reason="Kirje on aktiveeritud jooksvaks tööks; ajalooline vaikimisi ei kehti.",
        )

    # An existing closure is a fact, possibly a real professional one with a
    # disposition behind it. Never rewrite it into a cutover default.
    if not matter.is_open:
        return HistoricalCutoverCandidate(
            matter=matter,
            source_year=latest,
            classification=Classification.ALREADY_CLOSED,
            reason="Juba suletud; olemasolevat sulgemist ei kirjutata ule.",
        )

    if matter.origin not in REGISTER_ORIGINS:
        return HistoricalCutoverCandidate(
            matter=matter,
            source_year=latest,
            classification=Classification.REVIEW_REQUIRED,
            reason="Ootamatu paritolu registriviitega kirjel.",
            review_reason=ReviewReason.UNEXPECTED_ORIGIN,
        )

    # Live operational work. The bulk default is not entitled to erase it, and
    # closing the Matter would strand an action somebody is waiting on.
    if open_action:
        return HistoricalCutoverCandidate(
            matter=matter,
            source_year=latest,
            classification=Classification.REVIEW_REQUIRED,
            reason="Arhiivikirjel on kehtiv jargmine tegevus.",
            review_reason=ReviewReason.OPEN_NEXT_ACTION,
        )

    # The measured corpus has one source reference per Matter, but that is an
    # observation about today's data rather than a database invariant, so a row
    # spanning years is reviewed instead of silently filed under its latest.
    if len(source_years) > 1:
        return HistoricalCutoverCandidate(
            matter=matter,
            source_year=latest,
            classification=Classification.REVIEW_REQUIRED,
            reason="Kirje esineb mitmes registriaastas.",
            review_reason=ReviewReason.MULTIPLE_SOURCE_YEARS,
        )

    return HistoricalCutoverCandidate(
        matter=matter,
        source_year=latest,
        classification=Classification.WOULD_CLOSE_HISTORICAL,
        reason="Enne uleminekuaastat imporditud registrikirje; vaikimisi ajalooline.",
    )


def build_cutover_plan(*, cutover_year: int) -> HistoricalCutoverPlan:
    """Decide what the historical cutover would do. Writes nothing."""
    references = MatterSourceReference.objects.filter(source_system=SOURCE_SYSTEM).values_list(
        "matter_id", "source_sheet"
    )

    sheets_by_matter: dict[Any, list[str]] = {}
    for matter_id, sheet in references:
        sheets_by_matter.setdefault(matter_id, []).append(sheet)

    # Only Matters whose *every* register appearance predates the cutover. A
    # row that also appears in the cutover year is current-year business and
    # belongs to the promotion operation, not to this one.
    pre_cutover: dict[Any, list[int]] = {}
    for matter_id, sheets in sheets_by_matter.items():
        years = _source_years(sheets)
        if years and max(years) < cutover_year:
            pre_cutover[matter_id] = years

    matters = {
        matter.pk: matter
        for matter in Matter.objects.filter(pk__in=list(pre_cutover)).select_related("stage")
    }

    with_open_action = set(
        Matter.objects.filter(
            pk__in=list(pre_cutover), next_actions__status=ActionStatus.OPEN
        ).values_list("pk", flat=True)
    )

    plan = HistoricalCutoverPlan(cutover_year=cutover_year)
    for matter_id, years in sorted(pre_cutover.items(), key=lambda item: str(item[0])):
        matter = matters.get(matter_id)
        if matter is None:  # pragma: no cover - referential integrity holds
            continue
        plan.candidates.append(_classify(matter, years, open_action=matter_id in with_open_action))
    return plan


@dataclass(frozen=True)
class HistoricalCutoverResult:
    cutover_year: int
    closed: int
    examined: int


@transaction.atomic
def apply_cutover_plan(
    plan: HistoricalCutoverPlan, *, actor: Any = None
) -> HistoricalCutoverResult:
    """Apply the historical default. One transaction, or none of it.

    Refuses a cutover year outside :data:`REVIEWED_HISTORICAL_CUTOVER_YEARS`.
    Retiring a decade of somebody's work is not something a ``--cutover-year``
    argument should be able to do on its own.
    """
    if not plan.is_reviewed_year:
        raise UnreviewedCutoverYear(
            f"{plan.cutover_year} is not a reviewed cutover year. "
            f"Reviewed: {', '.join(str(y) for y in REVIEWED_HISTORICAL_CUTOVER_YEARS)}. "
            "Analyse it with --dry-run; applying needs a decision recorded in "
            "REVIEWED_HISTORICAL_CUTOVER_YEARS, not a flag."
        )

    closed = 0
    touched: list[Any] = []
    for candidate in plan.closable:
        # Re-read under the lock. A plan is minutes old, and somebody may have
        # closed or activated this in between; re-running must be a no-op
        # rather than a second write.
        matter = Matter.objects.select_for_update().get(pk=candidate.matter.pk)
        if matter.record_mode != RecordMode.ARCHIVE or not matter.is_open:
            continue
        mark_historical_archive_inactive(
            matter=matter,
            actor=actor,
            provenance=candidate.provenance(plan.cutover_year),
        )
        touched.append(matter.pk)
        closed += 1

    # Batched, once, after the writes. `is_open` is part of the register's
    # filter surface, and search is a derived layer: the rows stay indexed and
    # findable, they simply now describe a closed Matter.
    if touched:
        refresh_matters(indexable_matters().filter(pk__in=touched))

    return HistoricalCutoverResult(
        cutover_year=plan.cutover_year, closed=closed, examined=len(plan.candidates)
    )


def summary(plan: HistoricalCutoverPlan) -> dict[str, Any]:
    """Aggregate counts only. No titles, no names, no source text."""
    return {
        "operation": "historical_cutover_state",
        "operation_version": HISTORICAL_CUTOVER_VERSION,
        "cutover_year": plan.cutover_year,
        "reviewed_year": plan.is_reviewed_year,
        "pre_cutover_matters": len(plan.candidates),
        "classifications": plan.counts,
        "would_close": len(plan.closable),
        "review_reasons": plan.review_reasons,
        "by_source_year": plan.by_source_year,
    }


__all__ = [
    "CLASSIFICATIONS",
    "HISTORICAL_CUTOVER_VERSION",
    "REVIEWED_HISTORICAL_CUTOVER_YEARS",
    "Classification",
    "HistoricalCutoverCandidate",
    "HistoricalCutoverPlan",
    "HistoricalCutoverResult",
    "ReviewReason",
    "UnreviewedCutoverYear",
    "apply_cutover_plan",
    "build_cutover_plan",
    "summary",
]
