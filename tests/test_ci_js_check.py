"""CI parses and lints the browser scripts, and the app gains no Node (F8, JARCH-004).

`static/js/*.js` is served exactly as written. Before this check nothing short
of a browser noticed a syntax error or a misspelt name in it, and either one at
start-up stops every behaviour registered after it (docs/adr/0171). These tests
hold the check in the `quality` job — its pinned Node, the parse of every
script, the pinned ESLint with its two rules — so a later edit cannot drop it
and leave a green build that checks less.

Pure text: no Node, no network, no database.
"""

from __future__ import annotations

import pathlib
import re
from typing import Any

import pytest
import yaml

ROOT = pathlib.Path(__file__).resolve().parent.parent
WORKFLOW = ROOT / ".github" / "workflows" / "ci.yml"
CONFIG = ROOT / "eslint.config.mjs"

EXACT_VERSION = re.compile(r"^\d+\.\d+\.\d+$")


@pytest.fixture(scope="module")
def steps() -> list[dict[str, Any]]:
    workflow = yaml.safe_load(WORKFLOW.read_text(encoding="utf-8"))
    return list(workflow["jobs"]["quality"]["steps"])


def _running(steps: list[dict[str, Any]], needle: str) -> list[dict[str, Any]]:
    return [step for step in steps if needle in str(step.get("run", ""))]


def test_node_is_set_up_at_one_exact_version(steps: list[dict[str, Any]]) -> None:
    setups = [s for s in steps if str(s.get("uses", "")).startswith("actions/setup-node@")]
    assert len(setups) == 1, "the quality job no longer sets up Node for the JS check"
    version = str(setups[0].get("with", {}).get("node-version", ""))
    assert EXACT_VERSION.match(version), f"node-version {version!r} is not one exact version"


def test_every_script_is_parsed(steps: list[dict[str, Any]]) -> None:
    parses = _running(steps, "node --check")
    assert len(parses) == 1, "nothing runs `node --check` over the browser scripts"
    script = str(parses[0]["run"])
    assert "static/js/*.js" in script
    assert not parses[0].get("continue-on-error"), "the parse is a gate, not a report"


def test_the_scripts_are_linted_by_a_pinned_eslint(steps: list[dict[str, Any]]) -> None:
    lints = _running(steps, "eslint")
    assert len(lints) == 1, "nothing lints the browser scripts"
    script = str(lints[0]["run"])
    assert re.search(r"\beslint@\d+\.\d+\.\d+\b", script), "ESLint is not pinned to one version"
    assert "--ignore-scripts" in script, "installing the linter must not run package scripts"
    assert "--config eslint.config.mjs" in script
    assert "--max-warnings 0" in script
    assert re.search(r"\sstatic/js\b", script)
    assert not lints[0].get("continue-on-error"), "the lint is a gate, not a report"


def test_the_lint_keeps_its_two_rules_as_errors() -> None:
    config = CONFIG.read_text(encoding="utf-8")
    assert re.search(r'"no-undef":\s*"error"', config)
    assert re.search(r'"no-unused-vars":\s*\[\s*"error"', config)
    assert '"static/js/**/*.js"' in config


def test_the_application_gains_no_node_build() -> None:
    """The check is CI's alone: no package manifest, and no Node in the image."""
    assert not (ROOT / "package.json").exists()
    assert not (ROOT / "package-lock.json").exists()
    dockerfile = (ROOT / "Dockerfile").read_text(encoding="utf-8")
    assert not re.search(r"\b(node|npm|npx|eslint)\b", dockerfile, re.IGNORECASE)
