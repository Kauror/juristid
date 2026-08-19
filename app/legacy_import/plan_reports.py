"""Reports for a database-aware run.

Same two-class split as the offline inspector: ``summary.json`` and
``summary.md`` are counts and may be published, everything else reproduces
source content and stays local. The plan reports add what the offline reports
cannot know — which rows would create, which would match something already
present, and which mappings are missing before any of that is safe.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from app.legacy_import.apply import IMPORTER_VERSION
from app.legacy_import.contracts import contract_versions
from app.legacy_import.enums import ProposedRecordMode, RowOutcome
from app.legacy_import.planner import ImportPlan, RowPlan
from app.legacy_import.reporting import WrittenReports, write_reports

ROW_HEADER = [
    "leht",
    "rida",
    "viide",
    "tulem",
    "sidumise_meetod",
    "pakutud_kirje_liik",
    "pakutud_kirje_liigi_pohjus",
    "pealkiri",
    "asutus",
    "suund",
    "asutuse_vaste",
    "vastutaja",
    "vastutaja_vaste",
    "hetkeseis_toores",
    "hetkeseis_vaste",
    "onenote_link",
    "onenote_sisu_seis",
    "korvalekalded",
    "markus",
]

ANOMALY_HEADER = ["leht", "rida", "viide", "kood", "tulem", "pealkiri"]
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


def _status_label(plan: RowPlan) -> str:
    if plan.status is None or not plan.status.resolved:
        return ""
    if plan.status.stage is not None:
        return f"stage:{plan.status.stage.key}"
    return f"disposition:{plan.status.disposition}"


def _row_record(plan: RowPlan) -> list[Any]:
    row = plan.row
    return [
        row.sheet,
        row.row_number,
        row.display_reference or row.reference_raw,
        plan.outcome,
        plan.match_method,
        plan.proposed_record_mode,
        plan.proposed_record_mode_reason,
        row.title,
        row.counterparty_raw,
        row.counterparty_direction,
        str(plan.organisation.value) if plan.organisation and plan.organisation.resolved else "",
        row.owner_raw,
        str(plan.owner.value) if plan.owner and plan.owner.resolved else "",
        row.status_raw,
        _status_label(plan),
        row.onenote_url,
        "NOT_IMPORTED" if row.onenote_url else "NOT_APPLICABLE",
        " ".join(plan.anomalies),
        plan.note,
    ]


def _mapping_gaps(plan: ImportPlan) -> list[list[Any]]:
    buckets: dict[tuple[str, str], dict[str, Any]] = {}

    def add(kind: str, value: str, year: int) -> None:
        if not value.strip():
            return
        entry = buckets.setdefault((kind, value), {"count": 0, "years": set()})
        entry["count"] += 1
        entry["years"].add(year)

    for row_plan in plan.rows:
        row = row_plan.row
        if row_plan.owner is not None and row_plan.owner.needs_mapping:
            add("owner", row.owner_raw, row.year)
        if row_plan.organisation is not None and row_plan.organisation.needs_mapping:
            add(
                f"organisation:{row.counterparty_direction or 'unknown'}",
                row.counterparty_raw,
                row.year,
            )
        if row_plan.status is not None and row.status_raw and not row_plan.status.resolved:
            add("status", row.status_raw, row.year)
        for value in row.unknown_values.values():
            add("unknown-column", value, row.year)

    return [
        [kind, value, entry["count"], " ".join(str(year) for year in sorted(entry["years"]))]
        for (kind, value), entry in sorted(buckets.items())
    ]


def build_summary(plan: ImportPlan, *, mode: str) -> dict[str, Any]:
    inventory = plan.inventory
    record_modes: dict[str, int] = {}
    onenote_links = 0
    for row_plan in plan.rows:
        if row_plan.proposed_record_mode:
            record_modes[row_plan.proposed_record_mode] = (
                record_modes.get(row_plan.proposed_record_mode, 0) + 1
            )
        if row_plan.row.onenote_url:
            onenote_links += 1

    candidates = [p for p in plan.rows if p.candidate is not None]
    return {
        "mode": mode,
        "source": {
            "file_name": inventory.file_name,
            "sha256": inventory.sha256,
            "byte_size": inventory.byte_size,
            "parser_version": plan.parser_version,
            "importer_version": IMPORTER_VERSION,
            "contract_versions": contract_versions(),
            "sheet_names": inventory.sheet_names,
            "sheets_without_contract": inventory.sheets_without_contract,
        },
        "rows_considered": len(plan.rows),
        "outcomes": plan.outcome_counts,
        "accounting_is_complete": plan.is_complete,
        "anomalies": plan.anomaly_counts,
        "proposed_record_modes": dict(sorted(record_modes.items())),
        "full_candidates": record_modes.get(ProposedRecordMode.FULL_CANDIDATE.value, 0),
        "onenote_links_retained": onenote_links,
        "onenote_content_status": "NOT_IMPORTED",
        "highest_reference_by_year": {
            str(year): number for year, number in sorted(plan.highest_reference_by_year.items())
        },
        "next_action_candidates": {
            "total": len(candidates),
            "deterministic": sum(
                1 for p in candidates if p.candidate is not None and p.candidate.is_deterministic
            ),
            "would_create_next_action": 0,
        },
        "mapping_gaps": {
            "owners": sum(1 for p in plan.rows if p.owner is not None and p.owner.needs_mapping),
            "organisations": sum(
                1 for p in plan.rows if p.organisation is not None and p.organisation.needs_mapping
            ),
            "statuses": sum(
                1
                for p in plan.rows
                if p.status is not None and p.row.status_raw and not p.status.resolved
            ),
        },
        "sheets": [
            {"name": name, "counts": counts} for name, counts in sorted(plan.sheet_counts.items())
        ],
        "structure_findings": plan.structure_findings,
        # Kept prominent: this is the number a reviewer must reach zero on, or
        # consciously accept, before an apply is defensible.
        "review_required": plan.outcome_counts.get(RowOutcome.REVIEW_REQUIRED.value, 0),
    }


def _markdown(summary: dict[str, Any]) -> str:
    source = summary["source"]
    lines = [
        f"# Registri import — {summary['mode']}",
        "",
        f"- fail: `{source['file_name']}`",
        f"- SHA-256: `{source['sha256']}`",
        f"- importija: {source['importer_version']}, parser {source['parser_version']}",
        f"- lepingud: {source['contract_versions']}",
        "",
        "## Ridade tulemid",
        "",
        "| Tulem | Ridu |",
        "| --- | ---: |",
    ]
    lines += [f"| {key} | {value} |" for key, value in summary["outcomes"].items()]
    lines += [
        "",
        f"Kõik read arvestatud: **{'jah' if summary['accounting_is_complete'] else 'EI'}**",
        f"Ülevaatust vajab: **{summary['review_required']}**",
        "",
        "## Pakutud kirje liigid",
        "",
    ]
    lines += [f"- {key}: {value}" for key, value in summary["proposed_record_modes"].items()]
    lines += [
        "",
        "## Vasted, mis puuduvad",
        "",
        f"- vastutajaid: {summary['mapping_gaps']['owners']}",
        f"- asutusi: {summary['mapping_gaps']['organisations']}",
        f"- seisundeid: {summary['mapping_gaps']['statuses']}",
        "",
        "## OneNote",
        "",
        f"- säilitatud linke: {summary['onenote_links_retained']}",
        f"- sisu seis: {summary['onenote_content_status']} (sisu ei imporditud ega päritud)",
        "",
        "## Kõrvalekalded",
        "",
        "| Kood | Ridu |",
        "| --- | ---: |",
    ]
    lines += [f"| {key} | {value} |" for key, value in summary["anomalies"].items()]

    if summary["structure_findings"]:
        lines += ["", "## Struktuurileiud", ""]
        lines += [f"- {finding}" for finding in summary["structure_findings"]]

    return "\n".join(lines) + "\n"


def write_plan_reports(plan: ImportPlan, directory: Path, *, mode: str) -> WrittenReports:
    summary = build_summary(plan, mode=mode)
    rows = [
        _row_record(row_plan)
        for row_plan in plan.rows
        if row_plan.outcome != RowOutcome.BLANK_PADDING.value
    ]
    anomalies = [
        [
            row_plan.row.sheet,
            row_plan.row.row_number,
            row_plan.row.display_reference or row_plan.row.reference_raw,
            code,
            row_plan.outcome,
            row_plan.row.title,
        ]
        for row_plan in plan.rows
        for code in row_plan.anomalies
    ]
    candidates = [
        [
            row_plan.row.sheet,
            row_plan.row.row_number,
            row_plan.row.display_reference,
            row_plan.candidate.source_text,
            row_plan.candidate.rule_id,
            row_plan.candidate.rules_version,
            row_plan.candidate.kind,
            row_plan.candidate.date_semantics,
            row_plan.candidate.target_date.isoformat() if row_plan.candidate.target_date else "",
            row_plan.candidate.date_precision,
            row_plan.candidate.confidence,
            row_plan.candidate.explanation,
        ]
        for row_plan in plan.rows
        if row_plan.candidate is not None
    ]

    written = write_reports(
        directory,
        summary=summary,
        rows=rows,
        row_header=ROW_HEADER,
        anomalies=anomalies,
        anomaly_header=ANOMALY_HEADER,
        mapping_gaps=_mapping_gaps(plan),
        mapping_gap_header=MAPPING_GAP_HEADER,
        candidates=candidates,
        candidate_header=CANDIDATE_HEADER,
    )
    (directory / "summary.md").write_text(_markdown(summary), encoding="utf-8")
    return written
