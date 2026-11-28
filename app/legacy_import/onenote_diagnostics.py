"""OneNote coverage in the owner's six words (r98, docs/adr/0239). Read-only; counts only.

Everything is read from `onenote_coverage.coverage_summary()` and `held_counts()` — the same
records the OneNote page shows — and only regrouped:

* **Imported** — pages whose latest mark is IMPORTED, and attachments imported.
* **Captured, not applied** — a capture was prepared or verified but not imported.
* **Awaiting capture** — operational pages with no capture yet.
* **Held for review** — awaiting an association decision, plus held attachments and readings.
* **Changed since capture** — new material found after a capture.
* **Failed** — pages or attachments that failed.

«Unknown coverage» is stated, never hidden: Juristid keeps no complete list of the notebook's
pages, so «the archive is captured» is never claimed (`archive_complete` is False).
"""

from __future__ import annotations

from typing import Any

from app.legacy_import import onenote_coverage
from app.legacy_import.onenote_coverage_models import PageState


def diagnostics() -> dict[str, Any]:
    summary = onenote_coverage.coverage_summary()
    operational = summary["operational"]
    states: dict[str, int] = operational["states"]
    held = onenote_coverage.held_counts()
    rows = {
        "imported": states.get(PageState.IMPORTED, 0),
        "captured_not_applied": states.get(PageState.CAPTURE_PREPARED, 0)
        + states.get(PageState.VERIFIED, 0),
        "awaiting_capture": operational["pages_not_captured"],
        "held": states.get(PageState.AWAITING_ASSOCIATION, 0) + held["open"],
        "changed_since_capture": states.get(PageState.NEW_MATERIAL, 0),
        "failed": states.get(PageState.FAILED, 0) + operational["attachments_failed"],
    }
    return {
        "pages": operational["pages"],
        "rows": rows,
        "attachments_imported": operational["attachments_imported"],
        "held_open_by_category": held["open_by_category"],
        "teemad_without_page_link": operational["teemad_without_page_link"],
        "teemad_with_ambiguous_link": operational["teemad_with_ambiguous_link"],
        "open_teemad_without_capture": operational["open_teemad_without_capture"],
        "never_inventoried": summary["sections"]["never_inventoried"],
        "archive_complete": summary["archive_complete"],
        "archive_note": summary["archive_note"],
        "last_capture": summary["last_capture"],
    }
