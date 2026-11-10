"""The desktop capture script, read as text (live-sync round 2, agent C).

CI has no Windows PowerShell and no OneNote, so these guard what the 2026-11-09
desktop rehearsal found and what must never change: ASCII only (PS 5.1 reads a
BOM-less script as ANSI), only read calls on OneNote, and the namespace manager
returned whole (PowerShell unrolls an IEnumerable a function returns; the
script could not select a single node before that fix).
"""

from __future__ import annotations

import re
from pathlib import Path

SCRIPT = Path(__file__).resolve().parents[1] / "tools" / "onenote_capture" / "capture_pages.ps1"
ALLOWED = {"GetHierarchy", "GetHyperlinkToObject", "GetPageContent", "NavigateTo"}
WRITES = (
    "UpdatePageContent",
    "UpdateHierarchy",
    "DeleteHierarchy",
    "DeletePageContent",
    "CreateNewPage",
    "OpenHierarchy",
    "CloseNotebook",
    "Publish",
    "MergeFiles",
    "MergeSections",
    "SyncHierarchy",
)


def test_the_script_is_ascii_only():
    SCRIPT.read_bytes().decode("ascii")


def test_onenote_is_only_read():
    text = SCRIPT.read_text(encoding="ascii")
    called = set(re.findall(r"\$app\.([A-Za-z]+)\(", text))
    assert called <= ALLOWED, called - ALLOWED
    code = "\n".join(line for line in text.splitlines() if not line.lstrip().startswith("#"))
    assert not [w for w in WRITES if w in code]


def test_the_namespace_manager_is_returned_whole():
    text = SCRIPT.read_text(encoding="ascii")
    (nsm,) = [line for line in text.splitlines() if line.startswith("function Nsm(")]
    assert "return ,$m" in nsm


def test_an_unreadable_original_page_is_logged_not_silently_skipped():
    """10.11 rehearsal: under PS 5.1 `$app.Windows` is null, so the page the
    operator had open cannot be read; the run log says so (RUNBOOK §2.3)."""
    text = SCRIPT.read_text(encoding="ascii")
    restore = text[text.index("if ($originalPage) {") :]
    restore = restore[: restore.index("event = 'end'")]
    assert "} else {" in restore and "'restore_unavailable'" in restore
    runbook = SCRIPT.with_name("RUNBOOK.md").read_text(encoding="utf-8")
    assert "restore_unavailable" in runbook
    assert "the script returns to it at the end" not in runbook
