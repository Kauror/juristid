"""What CI's supply-chain gates promise, read off the workflow itself (JSEC-006).

The dependency audit used to run an unpinned ``uvx pip-audit`` over the runtime
set only, and nothing looked at the image's operating-system layers at all: the
production image shipped 104 CRITICAL/HIGH Debian CVEs that no check had named
(JSEC-007). These tests hold the gates in place — the auditor's version, the
development set as well as the runtime one, and an image scan that fails on
what can be fixed — so that a later edit cannot quietly drop one of them and
leave a green build that scans less.

Pure text: no Docker, no network, no database.
"""

from __future__ import annotations

import pathlib
import re
import shlex
from typing import Any

import pytest
import yaml

ROOT = pathlib.Path(__file__).resolve().parent.parent
WORKFLOW = ROOT / ".github" / "workflows" / "ci.yml"

#: `pip-audit==X.Y.Z` — one exact version, not a range or a bare name.
PINNED_PIP_AUDIT = re.compile(r"pip-audit==\d+\.\d+\.\d+")

#: `aquasec/trivy:<version>@sha256:<64 hex>` — the version for the reader, the
#: digest for Docker, so a re-pushed tag cannot change what runs.
PINNED_TRIVY = re.compile(r"aquasec/trivy:\d+\.\d+\.\d+@sha256:[0-9a-f]{64}")


@pytest.fixture(scope="module")
def workflow() -> dict[str, Any]:
    return yaml.safe_load(WORKFLOW.read_text(encoding="utf-8"))


def _steps(workflow: dict[str, Any], job: str) -> list[dict[str, Any]]:
    return list(workflow["jobs"][job]["steps"])


def _expanded(text: str, env: dict[str, str]) -> str:
    """The script with the step's and job's own `${NAME}`/`$NAME` values put in."""
    for name, value in env.items():
        text = text.replace("${" + name + "}", value).replace("$" + name, value)
    return text


def _scripts(workflow: dict[str, Any], job: str) -> list[tuple[dict[str, Any], str]]:
    """Every `run:` in the job, with its environment expanded, beside its step."""
    job_env = {k: str(v) for k, v in (workflow["jobs"][job].get("env") or {}).items()}
    found = []
    for step in _steps(workflow, job):
        if "run" not in step:
            continue
        env = {**job_env, **{k: str(v) for k, v in (step.get("env") or {}).items()}}
        found.append((step, _expanded(step["run"], env)))
    return found


def _audits(workflow: dict[str, Any]) -> list[tuple[dict[str, Any], str]]:
    return [
        (step, script)
        for step, script in _scripts(workflow, "dependency-scan")
        if "pip-audit" in script
    ]


def test_every_pip_audit_run_names_one_exact_version(workflow: dict[str, Any]) -> None:
    audits = _audits(workflow)
    assert audits, "the dependency-scan job no longer runs pip-audit"
    for step, script in audits:
        assert PINNED_PIP_AUDIT.search(script), (
            f"{step.get('name')!r} runs pip-audit without an exact version: "
            "an unpinned auditor changes what it reports with nobody's edit"
        )
        assert not re.search(r"uvx\s+pip-audit(?!==)", script), (
            f"{step.get('name')!r} still has a bare `uvx pip-audit`"
        )


def _exports(workflow: dict[str, Any]) -> dict[str, str]:
    """Each `uv export` in the scan, by the requirements file it writes."""
    exports = {}
    for _, script in _scripts(workflow, "dependency-scan"):
        if "uv export" not in script:
            continue
        words = shlex.split(script)
        exports[words[words.index("-o") + 1]] = script
    return exports


def _audited_files(workflow: dict[str, Any]) -> dict[str, dict[str, Any]]:
    audited = {}
    for step, script in _audits(workflow):
        words = shlex.split(script)
        audited[words[words.index("--requirement") + 1]] = step | {"script": script}
    return audited


def test_the_runtime_set_is_audited_strictly(workflow: dict[str, Any]) -> None:
    exports = _exports(workflow)
    runtime = [name for name, script in exports.items() if "--no-dev" in script]
    assert len(runtime) == 1, exports
    audit = _audited_files(workflow)[runtime[0]]
    assert "--strict" in audit["script"]
    assert not audit.get("continue-on-error"), "the runtime audit is a gate, not a report"


def test_the_development_set_is_audited_too(workflow: dict[str, Any]) -> None:
    """The tools CI and every laptop run carry advisories of their own.

    `urllib3 2.7.0` reached only the development group (pytest-playwright →
    pytest-base-url → requests), so the runtime-only audit could not see its
    three advisories. The whole lock is exported and audited, and the audit
    fails the run like the runtime one does.
    """
    exports = _exports(workflow)
    every_group = [name for name, script in exports.items() if "--all-groups" in script]
    assert len(every_group) == 1, f"no `uv export --all-groups` in the scan: {exports}"
    assert "--frozen" in exports[every_group[0]]
    audited = _audited_files(workflow)
    assert every_group[0] in audited, "the development set is exported but never audited"
    audit = audited[every_group[0]]
    assert "--strict" in audit["script"]
    assert not audit.get("continue-on-error"), "the development audit is a gate, not a report"


def _scan_steps(workflow: dict[str, Any]) -> list[tuple[dict[str, Any], str]]:
    return [
        (step, script)
        for step, script in _scripts(workflow, "container")
        if "aquasec/trivy" in script
    ]


def _built_image(workflow: dict[str, Any]) -> str:
    for _, script in _scripts(workflow, "container"):
        match = re.search(r"docker build -t (\S+) \.", script)
        if match:
            return match.group(1)
    raise AssertionError("the container job no longer builds the production image")


def test_the_built_image_is_scanned_by_a_pinned_trivy(workflow: dict[str, Any]) -> None:
    scans = _scan_steps(workflow)
    assert scans, "nothing scans the image the container job builds (JSEC-006)"
    image = _built_image(workflow)
    for step, script in scans:
        assert PINNED_TRIVY.search(script), (
            f"{step.get('name')!r} runs Trivy by a floating reference; pin it by digest"
        )
        assert re.search(rf"\bimage\b[^\n]*\s{re.escape(image)}\s*$", script, re.MULTILINE), (
            f"{step.get('name')!r} does not scan {image}, the image this job built"
        )


def test_the_scan_fails_on_fixable_high_and_critical_only(workflow: dict[str, Any]) -> None:
    """Fail on what an upgrade can fix; report the rest.

    A gate on every HIGH would be red from the first day over CVEs Debian has
    not patched, and a gate that is always red is switched off.
    """
    gates = [(step, script) for step, script in _scan_steps(workflow) if "--exit-code 1" in script]
    assert len(gates) == 1, "exactly one Trivy invocation decides the job"
    step, script = gates[0]
    assert "--ignore-unfixed" in script
    assert re.search(r"--severity[ =]HIGH,CRITICAL\b", script)
    assert not step.get("continue-on-error"), "the image scan is a gate, not a report"


def test_the_full_scan_report_is_kept(workflow: dict[str, Any]) -> None:
    steps = _steps(workflow, "container")
    reports = [
        step
        for step in steps
        if str(step.get("uses", "")).startswith("actions/upload-artifact@")
        and "image-scan" in str(step.get("with", {}).get("path", ""))
    ]
    assert len(reports) == 1, "the full Trivy report is not uploaded"
    report = reports[0]
    # Uploaded when the gate fails too — that is when somebody needs it most.
    assert "always()" in str(report.get("if", "")) or "!cancelled()" in str(report.get("if", ""))
    full = [
        line
        for _, script in _scan_steps(workflow)
        for line in script.splitlines()
        if "--format json" in line
    ]
    assert full, "no Trivy run writes the full JSON report"
    for line in full:
        assert "--ignore-unfixed" not in line, "the kept report must list unfixed CVEs too"
        assert "--severity" not in line, "the kept report must list every severity"


def test_the_scan_is_not_a_new_long_pole(workflow: dict[str, Any]) -> None:
    """It runs inside the container job, which already has the image.

    The account runs twenty jobs at once and every slot is in use from the
    first second (docs/ci-architecture.md), so a separate scan job would
    rebuild the image in a slot a shard runner needs.
    """
    jobs = workflow["jobs"]
    elsewhere = [
        name
        for name, job in jobs.items()
        if name != "container" and "aquasec/trivy" in yaml.safe_dump(job.get("steps", []))
    ]
    assert not elsewhere, f"Trivy runs outside the container job: {elsewhere}"
