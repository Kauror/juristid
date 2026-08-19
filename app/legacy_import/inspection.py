"""Offline inspection of a register snapshot.

The first useful thing anyone can do with a new workbook is look at it without
changing anything, and that has to work on a machine with no PostgreSQL —
otherwise people go back to opening the real file in Excel, which is how a
register gets edited by accident.

So nothing in this module imports a model, opens a connection or writes a row.
It reads the file, applies the era contracts, and produces two kinds of report:
aggregate counts that are safe to publish, and row-level detail that stays in
local ignored storage.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

from app.legacy_import.contracts import contract_for_year, contract_versions
from app.legacy_import.enums import BLOCKING_ANOMALIES, Anomaly, RowOutcome
from app.legacy_import.extraction import ExtractedRow, extract_sheet, summarize
from app.legacy_import.next_actions import NextActionCandidate, extract_candidate
from app.legacy_import.parser import PARSER_VERSION, RegisterWorkbook, WorkbookInventory
from app.legacy_import.reporting import WrittenReports, inspection_markdown, write_reports
from app.workflow.vocabulary import CONTROLLED_LABELS

ROW_HEADER = [
    "leht",
    "rida",
    "viide",
    "seis",
    "pealkiri",
    "asutus",
    "suund",
    "vastutaja",
    "saabus_toores",
    "saabus",
    "tahtaeg_toores",
    "tahtaeg",
    "valja_toores",
    "hetkeseis_toores",
    "jargmiseks_toores",
    "onenote_link",
    "vastanuid",
    "kusitud",
    "korvalekalded",
]

ANOMALY_HEADER = ["leht", "rida", "viide", "kood", "pealkiri"]
MAPPING_GAP_HEADER = ["liik", "vaartus", "esinemisi", "aastad"]
CANDIDATE_HEADER = [
    "leht",
    "rida",
    "viide",
    "algne_tekst",
    "reegel",
    "reegli_versioon",
    "liik",
    "kuupaeva_tahendus",
    "kuupaev",
    "tapsus",
    "kindlus",
    "selgitus",
]


@dataclass
class SheetInspection:
    name: str
    year: int | None
    contract_version: str
    counts: dict[str, Any]
    rows: list[ExtractedRow]
    structure_findings: list[str]


@dataclass
class Inspection:
    inventory: WorkbookInventory
    sheets: list[SheetInspection]
    vocabulary: dict[str, Any]
    candidates: list[tuple[ExtractedRow, NextActionCandidate]]

    @property
    def all_rows(self) -> list[ExtractedRow]:
        return [row for sheet in self.sheets for row in sheet.rows]


def classify_offline(row: ExtractedRow) -> str:
    """What can be said about a row without a database.

    Deliberately unable to say ``WOULD_CREATE``: whether a row creates a Matter
    or matches one already imported is a question only the database can answer,
    and pretending otherwise would make a dry run look unnecessary.
    """
    if row.is_blank:
        return RowOutcome.BLANK_PADDING.value
    if row.is_reserved_reference:
        return RowOutcome.RESERVED_REFERENCE.value
    if not row.is_matter_row:
        return RowOutcome.NON_MATTER_ROW.value
    if any(anomaly in BLOCKING_ANOMALIES for anomaly in row.anomalies):
        return RowOutcome.REVIEW_REQUIRED.value
    return RowOutcome.IMPORTABLE.value


def inspect_workbook(path: str | Path) -> Inspection:
    """Read a snapshot end to end. Makes no database call and no write."""
    with RegisterWorkbook(path) as workbook:
        inventory = workbook.inventory()
        sheets: list[SheetInspection] = []
        candidates: list[tuple[ExtractedRow, NextActionCandidate]] = []

        for sheet_inventory in inventory.year_sheets:
            year = sheet_inventory.year
            contract = contract_for_year(year) if year is not None else None
            if contract is None:
                sheets.append(
                    SheetInspection(
                        name=sheet_inventory.name,
                        year=year,
                        contract_version="",
                        counts={},
                        rows=[],
                        structure_findings=[
                            f"Lehel {sheet_inventory.name} ei ole ajastulepingut; lehte ei loeta."
                        ],
                    )
                )
                continue

            rows = extract_sheet(workbook.rows(contract), contract)
            findings = [finding.describe() for finding in sheet_inventory.header_findings]
            for letter, count in sorted(sheet_inventory.uncontracted_columns_with_data.items()):
                findings.append(
                    f"Veerg {letter}: leping ei kirjelda seda veergu, kuid selles on "
                    f"{count} mittetühja väärtust."
                )

            for row in rows:
                if row.next_action_raw:
                    candidate = extract_candidate(row.next_action_raw)
                    if candidate is not None:
                        candidates.append((row, candidate))
                        if not candidate.is_deterministic:
                            row.note(Anomaly.NEXT_ACTION_NOT_CONVERTED.value)

            sheets.append(
                SheetInspection(
                    name=sheet_inventory.name,
                    year=year,
                    contract_version=contract.contract_version,
                    counts=summarize(rows),
                    rows=rows,
                    structure_findings=findings,
                )
            )

        vocabulary = _compare_vocabulary(workbook, sheets)

    return Inspection(
        inventory=inventory, sheets=sheets, vocabulary=vocabulary, candidates=candidates
    )


def _compare_vocabulary(
    workbook: RegisterWorkbook, sheets: list[SheetInspection]
) -> dict[str, Any]:
    """Check the workbook's own vocabulary sheet against the reviewed one.

    Reports disagreement; never resolves it. Overwriting reviewed help text from
    a sheet that may itself be stale is how a vocabulary drifts without anyone
    deciding anything (Stage-2A brief 17).
    """
    entries = workbook.reference_vocabulary()
    workbook_labels = {label for label, _ in entries}
    used_labels = {row.status_raw for sheet in sheets for row in sheet.rows if row.status_raw}
    return {
        "workbook_label_count": len(workbook_labels),
        "known_label_count": len(CONTROLLED_LABELS),
        "labels_missing_from_seed": sorted(workbook_labels - CONTROLLED_LABELS),
        "labels_missing_from_workbook": sorted(CONTROLLED_LABELS - workbook_labels),
        "used_labels_not_in_controlled_vocabulary": sorted(used_labels - CONTROLLED_LABELS),
        "has_explanations": sum(1 for _, explanation in entries if explanation),
    }


def build_summary(inspection: Inspection) -> dict[str, Any]:
    """Aggregate-only. Nothing here reproduces a cell's contents.

    The single exception is the status vocabulary, which is controlled
    reference data rather than case content: the whole point of reporting it is
    to show which labels a year used.
    """
    inventory = inspection.inventory
    totals: dict[str, int] = {}
    anomalies: dict[str, int] = {}
    outcomes: dict[str, int] = {}
    structure_findings: list[str] = []

    numeric_keys = (
        "rows_below_header",
        "blank_rows",
        "matter_rows",
        "reserved_references",
        "valid_references",
        "hyperlinks",
        "other_hyperlinks",
        "next_action_populated",
        "status_populated",
        "feedback_responded_present",
        "feedback_requested_present",
        "feedback_measured_zero",
        "unknown_column_values",
    )
    for key in numeric_keys:
        totals[key] = 0

    sheets_payload: list[dict[str, Any]] = []
    for sheet in inspection.sheets:
        for key in numeric_keys:
            totals[key] += int(sheet.counts.get(key, 0))
        for code, count in (sheet.counts.get("anomalies") or {}).items():
            anomalies[code] = anomalies.get(code, 0) + count
        structure_findings.extend(
            f"{sheet.name}: {finding}" for finding in sheet.structure_findings
        )

        sheet_outcomes: dict[str, int] = {}
        for row in sheet.rows:
            outcome = classify_offline(row)
            sheet_outcomes[outcome] = sheet_outcomes.get(outcome, 0) + 1
            outcomes[outcome] = outcomes.get(outcome, 0) + 1

        sheets_payload.append(
            {
                "name": sheet.name,
                "year": sheet.year,
                "contract_version": sheet.contract_version,
                "counts": sheet.counts,
                "outcomes": dict(sorted(sheet_outcomes.items())),
                "structure_findings": sheet.structure_findings,
            }
        )

    duplicate_references = sorted(
        {
            row.display_reference
            for row in inspection.all_rows
            if Anomaly.DUPLICATE_REFERENCE.value in row.anomalies and row.display_reference
        }
    )

    candidate_rules: dict[str, int] = {}
    for _, candidate in inspection.candidates:
        candidate_rules[candidate.rule_id] = candidate_rules.get(candidate.rule_id, 0) + 1

    return {
        "source": {
            "file_name": inventory.file_name,
            "sha256": inventory.sha256,
            "byte_size": inventory.byte_size,
            "parser_version": PARSER_VERSION,
            "contract_versions": contract_versions(),
            "sheet_names": inventory.sheet_names,
            "year_sheets": sorted(
                sheet.year for sheet in inventory.year_sheets if sheet.year is not None
            ),
            "sheets_without_contract": inventory.sheets_without_contract,
        },
        "sheets": sheets_payload,
        "totals": totals,
        "outcomes": dict(sorted(outcomes.items())),
        "anomalies": dict(sorted(anomalies.items())),
        "duplicate_references": duplicate_references,
        "next_action_candidates": {
            "total": len(inspection.candidates),
            "by_rule": dict(sorted(candidate_rules.items())),
            "deterministic": sum(
                1 for _, candidate in inspection.candidates if candidate.is_deterministic
            ),
        },
        "structure_findings": structure_findings,
        "vocabulary": inspection.vocabulary,
    }


def _row_record(row: ExtractedRow) -> list[Any]:
    return [
        row.sheet,
        row.row_number,
        row.display_reference or row.reference_raw,
        classify_offline(row),
        row.title,
        row.counterparty_raw,
        row.counterparty_direction,
        row.owner_raw,
        row.received.raw if row.received else "",
        row.received.value.isoformat() if row.received and row.received.value else "",
        row.deadline.raw if row.deadline else "",
        row.deadline.value.isoformat() if row.deadline and row.deadline.value else "",
        row.sent.raw if row.sent else "",
        row.status_raw,
        row.next_action_raw,
        row.onenote_url,
        row.feedback_responded.raw,
        row.feedback_requested.raw,
        " ".join(row.anomalies),
    ]


def _mapping_gaps(inspection: Inspection) -> list[list[Any]]:
    """Distinct source values that need a reviewed mapping before they can land.

    Grouped rather than listed per row, because the reviewer's job is to decide
    once per ministry, not once per matter.
    """
    buckets: dict[tuple[str, str], dict[str, Any]] = {}

    def add(kind: str, value: str, year: int) -> None:
        if not value.strip():
            return
        entry = buckets.setdefault((kind, value), {"count": 0, "years": set()})
        entry["count"] += 1
        entry["years"].add(year)

    for row in inspection.all_rows:
        if not row.is_matter_row:
            continue
        add(
            f"organisation:{row.counterparty_direction or 'unknown'}",
            row.counterparty_raw,
            row.year,
        )
        add("owner", row.owner_raw, row.year)
        if row.status_raw and row.status_raw not in CONTROLLED_LABELS:
            add("status", row.status_raw, row.year)
        for letter, value in row.unknown_values.items():
            add(f"unknown-column:{letter}", value, row.year)

    return [
        [kind, value, entry["count"], " ".join(str(year) for year in sorted(entry["years"]))]
        for (kind, value), entry in sorted(buckets.items())
    ]


def write_inspection_reports(inspection: Inspection, directory: Path) -> WrittenReports:
    rows = [_row_record(row) for row in inspection.all_rows if not row.is_blank]
    anomalies = [
        [row.sheet, row.row_number, row.display_reference or row.reference_raw, code, row.title]
        for row in inspection.all_rows
        for code in row.anomalies
    ]
    candidates = [
        [
            row.sheet,
            row.row_number,
            row.display_reference,
            candidate.source_text,
            candidate.rule_id,
            candidate.rules_version,
            candidate.kind,
            candidate.date_semantics,
            candidate.target_date.isoformat() if candidate.target_date else "",
            candidate.date_precision,
            candidate.confidence,
            candidate.explanation,
        ]
        for row, candidate in inspection.candidates
    ]

    summary = build_summary(inspection)
    return write_reports(
        directory,
        summary=summary,
        markdown=inspection_markdown(summary),
        rows=rows,
        row_header=ROW_HEADER,
        anomalies=anomalies,
        anomaly_header=ANOMALY_HEADER,
        mapping_gaps=_mapping_gaps(inspection),
        mapping_gap_header=MAPPING_GAP_HEADER,
        candidates=candidates,
        candidate_header=CANDIDATE_HEADER,
    )
