"""The digest an operator approves before `import_legacy_register --apply` (docs/adr/0184).

The dry run used to print the workbook's hash, and the apply then rebuilt a
fresh plan from whatever the disk and the database held at that moment. The
workbook could be swapped between the two runs, the mapping file edited, a
Matter created under a reference the plan meant to create — and the apply
would do something nobody had read.

The digest covers everything that decides a write:

* **the bytes** — the workbook as parsed (`RegisterWorkbook` hashes the bytes
  it loads, not a second read of the path), the reviewed mapping file, and
  every era contract the sheets were read with;
* **the resolved plan** — each row's outcome and why, the values that would be
  written (title, reference, dates, era), and every database row a decision
  rests on: the Matter matched, the owner and organisation resolved, the stage,
  as their primary keys;
* **the code** — importer, parser and contract-set versions.

It is canonical JSON with sorted keys and no display text, so a reworded
warning or a reordered report does not change it, and a different decision
always does. The apply recomputes it inside the transaction that writes, after
locking the Matter table, and refuses on a mismatch before writing anything.
"""

from __future__ import annotations

from typing import Any

from app.legacy_import.apply import IMPORTER_VERSION
from app.legacy_import.contracts import contract_versions, load_contracts
from app.legacy_import.integrity import canonical_sha256
from app.legacy_import.planner import ImportPlan, RowPlan
from app.legacy_import.resolution import MappingTables

#: Bumped when what the digest covers changes.
REGISTER_PLAN_DIGEST_VERSION = "register-plan/1"


def _resolution(value: Any) -> Any:
    if value is None:
        return None
    return {"value": getattr(value, "value", None), "method": getattr(value, "method", "")}


def _status(status: Any) -> Any:
    if status is None:
        return None
    return {
        "raw_label": status.raw_label,
        "stage": status.stage,
        "disposition": status.disposition,
        "mapping": status.mapping,
        "is_closure": bool(getattr(status, "is_closure", False)),
    }


def _row(plan: RowPlan) -> dict[str, Any]:
    row = plan.row
    return {
        "sheet": row.sheet,
        "row": row.row_number,
        "outcome": plan.outcome,
        "note": plan.note,
        "anomalies": list(plan.anomalies),
        "matter": plan.matter,
        "match_method": plan.match_method,
        "record_mode": plan.proposed_record_mode,
        "record_mode_reason": plan.proposed_record_mode_reason,
        "owner": _resolution(plan.owner),
        "organisation": _resolution(plan.organisation),
        "status": _status(plan.status),
        "candidate": plan.candidate,
        "values": {
            "era": row.era,
            "contract_version": row.contract_version,
            "reference": row.display_reference,
            "title": row.title,
            "received": row.received.value if row.received else None,
            "deadline": row.deadline.value if row.deadline else None,
            "counterparty_direction": row.counterparty_direction,
            "onenote_url": row.onenote_url,
        },
    }


def register_plan_digest(plan: ImportPlan, mappings: MappingTables) -> str:
    """The canonical SHA-256 of one register plan and everything it was built from."""
    contracts = load_contracts()
    return canonical_sha256(
        {
            "version": REGISTER_PLAN_DIGEST_VERSION,
            "importer": IMPORTER_VERSION,
            "parser": plan.parser_version,
            "contracts": {
                "versions": contract_versions(),
                "files": {str(year): c.source_sha256 for year, c in sorted(contracts.items())},
            },
            "workbook": {
                "file_name": plan.inventory.file_name,
                "sha256": plan.inventory.sha256,
                "bytes": plan.inventory.byte_size,
            },
            "mapping_sha256": mappings.source_sha256,
            "highest_reference_by_year": {
                str(year): number for year, number in sorted(plan.highest_reference_by_year.items())
            },
            "rows": [_row(row_plan) for row_plan in plan.rows],
        }
    )
