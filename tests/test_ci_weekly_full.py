"""The weekly full run (coordinator QA 2026-11-20): it only starts `ci.yml` on
main — no job of its own to drift from ci.yml, no checkout, no write beyond
starting a workflow."""

from __future__ import annotations

from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]
WEEKLY = ROOT / ".github" / "workflows" / "weekly-full.yml"


def _workflow() -> dict:
    return yaml.safe_load(WEEKLY.read_text(encoding="utf-8"))


def test_it_runs_weekly_and_on_demand_only():
    on = _workflow().get(True) or _workflow().get("on")
    assert set(on) == {"schedule", "workflow_dispatch"}
    assert on["schedule"] == [{"cron": "0 3 * * 1"}]


def test_it_may_only_start_workflows():
    workflow = _workflow()
    assert workflow["permissions"] == {"actions": "write"}
    (job,) = workflow["jobs"].values()
    assert "permissions" not in job
    assert not any("uses" in step for step in job["steps"])


def test_it_starts_the_whole_of_ci_on_main_and_nothing_else():
    (job,) = _workflow()["jobs"].values()
    (step,) = job["steps"]
    assert step["run"] == 'gh workflow run ci.yml --repo "$REPOSITORY" --ref main'
    assert "${{" not in step["run"]
