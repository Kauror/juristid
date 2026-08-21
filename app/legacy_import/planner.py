"""Deciding what an import would do — without doing any of it.

The planner reads the database and writes nothing. That separation is what
makes ``--dry-run`` worth running: the plan it produces is the same object the
apply step consumes, so what a reviewer approves is literally what gets
executed, rather than a description of it produced by different code.

Every relevant source row leaves here with exactly one outcome. The outcomes
partition the sheet, the totals are checked against the row count, and a row the
planner cannot make sense of becomes ``REVIEW_REQUIRED`` rather than
disappearing (master specification 19.9).
"""

from __future__ import annotations

import datetime as dt
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from django.utils import timezone

from app.core.text import normalize_for_matching
from app.legacy_import.contracts import contract_for_year
from app.legacy_import.enums import (
    BLOCKING_ANOMALIES,
    Anomaly,
    ProposedRecordMode,
    RowOutcome,
)
from app.legacy_import.extraction import ExtractedRow, extract_sheet, summarize
from app.legacy_import.models import MatchMethod, MatterSourceReference
from app.legacy_import.next_actions import NextActionCandidate, extract_candidate
from app.legacy_import.parser import (
    PARSER_VERSION,
    SOURCE_SYSTEM,
    RegisterWorkbook,
    WorkbookInventory,
)
from app.legacy_import.resolution import (
    KnownPeople,
    MappingTables,
    Resolution,
    StatusResolution,
    resolve_organisation,
    resolve_owner,
    resolve_status,
)
from app.matters.enums import MatterOrigin
from app.matters.models import Matter


@dataclass
class RowPlan:
    """What would happen to one source row, and why."""

    row: ExtractedRow
    outcome: str
    matter: Matter | None = None
    match_method: str = MatchMethod.UNMATCHED.value
    proposed_record_mode: str = ""
    proposed_record_mode_reason: str = ""
    owner: Resolution | None = None
    organisation: Resolution | None = None
    status: StatusResolution | None = None
    candidate: NextActionCandidate | None = None
    note: str = ""

    @property
    def anomalies(self) -> list[str]:
        return list(self.row.anomalies)

    @property
    def creates_matter(self) -> bool:
        return self.outcome == RowOutcome.WOULD_CREATE.value


@dataclass
class ImportPlan:
    inventory: WorkbookInventory
    rows: list[RowPlan]
    sheet_counts: dict[str, dict[str, Any]]
    structure_findings: list[str]
    highest_reference_by_year: dict[int, int]
    parser_version: str = PARSER_VERSION

    @property
    def outcome_counts(self) -> dict[str, int]:
        counts: dict[str, int] = {}
        for plan in self.rows:
            counts[plan.outcome] = counts.get(plan.outcome, 0) + 1
        return dict(sorted(counts.items()))

    @property
    def anomaly_counts(self) -> dict[str, int]:
        counts: dict[str, int] = {}
        for plan in self.rows:
            for anomaly in plan.anomalies:
                counts[anomaly] = counts.get(anomaly, 0) + 1
        return dict(sorted(counts.items()))

    @property
    def is_complete(self) -> bool:
        """Every row below every header is accounted for exactly once."""
        return sum(self.outcome_counts.values()) == len(self.rows)

    def rows_with_outcome(self, outcome: str) -> list[RowPlan]:
        return [plan for plan in self.rows if plan.outcome == outcome]


def _proposed_record_mode(
    row: ExtractedRow,
    status: StatusResolution,
    mappings: MappingTables,
    today: dt.date,
) -> tuple[str, str]:
    """Suggest FULL or ARCHIVE, always with the reason attached.

    A proposal, never a decision: the active set at cutover is attested by
    people, one lawyer's slice at a time (master specification 19.5). The rule
    below is deliberately conservative in one direction — it will propose
    ARCHIVE for something a lawyer knows is live, and a person fixes that. It
    will not propose FULL for a row whose only evidence is its calendar year.
    """
    override = mappings.record_modes.get(
        normalize_for_matching(str(row.reference)) if row.reference else ""
    )
    if override:
        return override, "Ülevaadatud vastefail määras kirje liigi."

    if status.is_closure:
        # The one place where the model and the source genuinely disagree, and
        # it is not fixable by inventing data. A FULL Matter that is closed must
        # carry a closure timestamp; the register recorded that Koda stopped but
        # never recorded when. Proposing ARCHIVE keeps the closure fact and
        # loses nothing, where proposing FULL would require a fabricated date.
        return (
            ProposedRecordMode.ARCHIVE.value,
            "Seisund tähendab töö lõpetamist, kuid sulgemise kuupäeva allikas ei ole; "
            "FULL nõuaks väljamõeldud kuupäeva.",
        )

    if row.year < today.year - 1:
        return ProposedRecordMode.ARCHIVE.value, "Vanem registriaasta."

    signals: list[str] = []
    if row.next_action_raw:
        signals.append("järgmiseks on täidetud")
    if status.stage is not None:
        signals.append("hetkeseis on kaardistatud etapile")

    if signals:
        return (
            ProposedRecordMode.FULL_CANDIDATE.value,
            "Hiljutine aasta ja " + ", ".join(signals) + ".",
        )
    return (
        ProposedRecordMode.ARCHIVE.value,
        "Hiljutine aasta, kuid ei kaardistatud etappi ega järgmist tegevust; "
        "üksnes aastaarv ei tee kirjet aktiivseks.",
    )


def _material_disagreement(row: ExtractedRow, matter: Matter) -> bool:
    """Does the source contradict what Juristid already holds?

    Only the title is compared, and only after normalising whitespace. A
    different title on the same reference means the two are probably not the
    same matter, which a person must settle. Dates and organisations are *not*
    compared: they are routinely enriched after import, and treating an
    improvement as a conflict would make every reviewed record fail its next
    reconciliation.
    """
    source_title = " ".join(row.title.split()).casefold()
    stored_title = " ".join(matter.title.split()).casefold()
    return bool(source_title) and bool(stored_title) and source_title != stored_title


def build_plan(
    workbook_path: str | Path,
    *,
    mappings: MappingTables | None = None,
    today: dt.date | None = None,
) -> ImportPlan:
    """Read a snapshot and decide what an import would do. Writes nothing."""
    tables = mappings or MappingTables.empty()
    when = today or timezone.localdate()
    # Read once for the whole run. The department is a handful of accounts and
    # the register is thousands of rows, so one query beats one per row — and
    # the ambiguity test needs the whole set to be exact anyway.
    people = KnownPeople.load()

    with RegisterWorkbook(workbook_path) as workbook:
        inventory = workbook.inventory()
        snapshot = inventory.sha256

        plans: list[RowPlan] = []
        sheet_counts: dict[str, dict[str, Any]] = {}
        structure_findings: list[str] = []
        highest: dict[int, int] = {}

        for sheet_inventory in inventory.year_sheets:
            year = sheet_inventory.year
            contract = contract_for_year(year) if year is not None else None
            if contract is None:
                structure_findings.append(
                    f"{sheet_inventory.name}: ajastulepingut ei ole; lehte ei imporditud."
                )
                continue

            structure_findings.extend(
                f"{sheet_inventory.name}: {finding.describe()}"
                for finding in sheet_inventory.header_findings
            )

            rows = extract_sheet(workbook.rows(contract), contract)
            sheet_counts[sheet_inventory.name] = summarize(rows)

            for row in rows:
                plans.append(_plan_row(row, snapshot, tables, when, people))
                if row.reference is not None and row.reference.year == row.year:
                    highest[row.year] = max(highest.get(row.year, 0), row.reference.number)

    return ImportPlan(
        inventory=inventory,
        rows=plans,
        sheet_counts=sheet_counts,
        structure_findings=structure_findings,
        highest_reference_by_year=highest,
    )


def _plan_row(
    row: ExtractedRow,
    snapshot: str,
    mappings: MappingTables,
    today: dt.date,
    people: KnownPeople,
) -> RowPlan:
    if row.is_blank:
        return RowPlan(row=row, outcome=RowOutcome.BLANK_PADDING.value)
    if row.is_reserved_reference:
        return RowPlan(
            row=row,
            outcome=RowOutcome.RESERVED_REFERENCE.value,
            note="Broneeritud number ilma sisuta; teemat ei looda, kuid jada arvestab sellega.",
        )
    if not row.is_matter_row:
        return RowPlan(row=row, outcome=RowOutcome.NON_MATTER_ROW.value)

    # 1. Already recorded from this exact snapshot. Idempotency's first line.
    existing_reference = (
        MatterSourceReference.objects.filter(
            source_system=SOURCE_SYSTEM,
            source_snapshot_sha256=snapshot,
            source_sheet=row.sheet,
            source_row_number=row.row_number,
        )
        .select_related("matter")
        .first()
    )
    if existing_reference is not None:
        return RowPlan(
            row=row,
            outcome=RowOutcome.ALREADY_IMPORTED.value,
            matter=existing_reference.matter,
            match_method=existing_reference.match_method,
            note="Sama hetktõmmise sama rida on juba imporditud.",
        )

    if any(anomaly in BLOCKING_ANOMALIES for anomaly in row.anomalies):
        return RowPlan(
            row=row,
            outcome=RowOutcome.REVIEW_REQUIRED.value,
            note="Rida kannab tõket: " + ", ".join(sorted(set(row.anomalies) & BLOCKING_ANOMALIES)),
        )

    owner = resolve_owner(row.owner_raw, mappings, people)
    organisation = resolve_organisation(row.counterparty_raw, mappings)
    status = resolve_status(row.status_raw, row.era)

    if owner.needs_mapping:
        row.note(Anomaly.UNMAPPED_OWNER.value)
    if organisation.needs_mapping:
        row.note(Anomaly.UNMAPPED_ORGANISATION.value)
    if row.status_raw and not status.resolved:
        row.note(Anomaly.UNMAPPED_STATUS.value)

    candidate = extract_candidate(row.next_action_raw) if row.next_action_raw else None
    if candidate is not None and not candidate.is_deterministic:
        row.note(Anomaly.NEXT_ACTION_NOT_CONVERTED.value)

    mode, reason = _proposed_record_mode(row, status, mappings, today)
    if mode == ProposedRecordMode.FULL.value and status.is_closure:
        # An operator asked for FULL on a row whose status says Koda stopped
        # working on it. A closed FULL Matter must carry a closure timestamp and
        # the register never recorded one, so honouring this would mean
        # inventing a date. Refusing out loud is the only honest option; the
        # override is not silently downgraded either.
        return RowPlan(
            row=row,
            outcome=RowOutcome.REVIEW_REQUIRED.value,
            note=(
                "Vastefail nõuab FULL-kirjet, kuid seisund tähendab töö lõpetamist ja "
                "sulgemise kuupäeva allikas ei ole. FULL nõuaks väljamõeldud kuupäeva."
            ),
        )

    # 2. The human reference. The only identifier the department shares with the
    #    system, so it is the strongest signal after the snapshot itself.
    if row.reference is not None:
        matter = Matter.objects.filter(
            reference_year=row.reference.year, reference_number=row.reference.number
        ).first()
        if matter is not None:
            return _plan_against_existing(
                row, matter, owner, organisation, status, candidate, mode, reason
            )

    return RowPlan(
        row=row,
        outcome=RowOutcome.WOULD_CREATE.value,
        match_method=MatchMethod.REFERENCE_TOKEN.value,
        proposed_record_mode=mode,
        proposed_record_mode_reason=reason,
        owner=owner,
        organisation=organisation,
        status=status,
        candidate=candidate,
    )


def _plan_against_existing(
    row: ExtractedRow,
    matter: Matter,
    owner: Resolution,
    organisation: Resolution,
    status: StatusResolution,
    candidate: NextActionCandidate | None,
    mode: str,
    reason: str,
) -> RowPlan:
    """A Matter already holds this reference. Decide, do not overwrite."""
    if matter.origin == MatterOrigin.NATIVE:
        # Somebody has been working in Juristid under this number. The import
        # has nothing to add and everything to lose.
        row.note(Anomaly.REFERENCE_CONFLICTS_WITH_NATIVE.value)
        return RowPlan(
            row=row,
            outcome=RowOutcome.REVIEW_REQUIRED.value,
            matter=matter,
            note=(
                "Viide kuulub süsteemis loodud teemale. Importija ei kirjuta üle; "
                "kokkulangevuse lahendab inimene."
            ),
        )

    if _material_disagreement(row, matter):
        row.note(Anomaly.SOURCE_DISAGREES_WITH_MATTER.value)
        return RowPlan(
            row=row,
            outcome=RowOutcome.REVIEW_REQUIRED.value,
            matter=matter,
            note=(
                "Sama viide, kuid allika pealkiri erineb teema omast. Mõlemad väärtused "
                "säilivad; sidumise kinnitab inimene."
            ),
        )

    return RowPlan(
        row=row,
        outcome=RowOutcome.WOULD_MATCH.value,
        matter=matter,
        match_method=MatchMethod.REFERENCE_TOKEN.value,
        proposed_record_mode=mode,
        proposed_record_mode_reason=reason,
        owner=owner,
        organisation=organisation,
        status=status,
        candidate=candidate,
        note="Olemasolev imporditud teema; lisatakse uus allikaviide.",
    )
