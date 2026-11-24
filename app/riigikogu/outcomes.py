"""Was koda's proposal taken into account? Official evidence first (ADR 0225 §5–§6).

The evidence hierarchy, mapped onto Juristid's own work-victory vocabulary
(`MatterWorkVictory` is a candidate until a person confirms it):

* **DIRECT** — the lead committee's list of amendments names koda as the
  proposer and records «ARVESTADA TÄIELIKULT / OSALISELT / SISULISELT»
  (`app.riigikogu.evidence`). Read deterministically; no model involved.
* **SHARED** — the list names koda among several proposers.
* **ALIGNMENT** — the later text matches what koda asked, but no official
  document says it was koda's doing. A model may find this; it is never
  presented as influence.
* **INSUFFICIENT** — the relationship cannot be established.

The **stage** is kept: a proposal reflected in the second-reading text is not
yet in the adopted act, and a later reading may reverse it.
"""

from __future__ import annotations

from typing import Any

from app.riigikogu import analysis, evidence
from app.riigikogu.enums import AnalysisType
from app.riigikogu.models import Bill

DECISION_OUTCOME = {
    "FULL": "REFLECTED",
    "SUBSTANCE": "REFLECTED",
    "PARTIAL": "PARTIAL",
    "REJECTED": "NOT_REFLECTED",
}


def official(amendment_list: str, *, stage: str) -> list[dict[str, Any]]:
    """Rows from the official list alone: koda's proposals and the committee's decision."""
    rows = []
    for entry in evidence.koda_entries(amendment_list):
        others = [p.strip() for p in entry.proposer.replace(" ja ", ",").split(",") if p.strip()]
        rows.append(
            {
                "proposal": entry.text.split("\n")[0][:300] or f"Ettepanek {entry.number}",
                "provision": entry.number,
                "outcome": DECISION_OUTCOME.get(entry.decision, "INSUFFICIENT"),
                "stage": stage,
                "evidence_level": "SHARED" if len(others) > 1 else "DIRECT",
                "quote": f"{entry.proposer} — Juhtivkomisjon: {entry.decision_text}",
                "explanation": "Juhtivkomisjoni muudatusettepanekute loetelu.",
                "official": True,
            }
        )
    return rows


def analyse(
    *,
    bill: Bill,
    koda_opinion: analysis.Source,
    revised_text: analysis.Source,
    amendment_list: analysis.Source | None,
    stage: str,
    model: analysis.Model | None = None,
) -> Any:
    """Official rows first; then, for koda's proposals the list does not settle, the
    model's comparison with the revised text — at most ALIGNMENT."""
    official_rows = official(amendment_list.text, stage=stage) if amendment_list else []
    sources = [koda_opinion, revised_text] + ([amendment_list] if amendment_list else [])
    row = analysis.run(
        AnalysisType.PROPOSAL_OUTCOMES,
        sources,
        bill=bill,
        context=(
            "Find each concrete proposal in koda's opinion (the first source). For each, "
            "compare with the revised bill text (the second source): REFLECTED, PARTIAL, "
            "NOT_REFLECTED, REVERSED or INSUFFICIENT. Quote the passage of the revised text "
            "that shows it. Do not claim that koda caused a change."
        ),
        model=model,
    )
    if row.status == "DONE":
        inferred = []
        for item in row.result.get("proposals", []):
            item.setdefault("stage", stage)
            # Inference is never more than alignment (ADR 0225 §6).
            item["evidence_level"] = (
                "ALIGNMENT" if item.get("outcome") in ("REFLECTED", "PARTIAL") else "INSUFFICIENT"
            )
            inferred.append(item)
        row.result = {"proposals": official_rows + inferred}
        row.save(update_fields=["result", "updated_at"])
    return row
