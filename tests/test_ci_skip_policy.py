"""In CI a skip is either one the policy names, or a failure (ENG-051).

Before this, 24 browser tests skipped on every CI run and counted as green: 22
scenarios of a withdrawn feature, one test whose control had been removed, and
one waiting for a seed shape that never existed. A missing E2E variable would
have skipped the whole browser gate the same way.
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest

import ci_skip_policy as policy

ROOT = Path(__file__).resolve().parents[1]

INTAKE_REASON = (
    "the document reading is withdrawn from Uus teema (docs/adr/0088); set "
    "E2E_INTAKE_SUGGESTIONS=1 against a server started with "
    "MATTER_INTAKE_SUGGESTIONS_ENABLED=1 to run this"
)


def test_the_withdrawn_reading_is_an_expected_skip():
    assert policy.is_expected("e2e/test_assisted_intake.py::test_x[chromium]", INTAKE_REASON)


@pytest.mark.parametrize(
    ("node", "reason"),
    [
        # A seed shape, a removed control, a missing server, a missing engine:
        # none of them is coverage, and none may pass silently in CI.
        ("e2e/test_kpi_navigation.py::test_rail", "every area with open work has an owner"),
        ("e2e/test_ux_pass.py::test_defer", "«Lükka edasi» is not offered on the Teema page"),
        ("e2e/test_persona_switcher.py::test_x", "E2E_BASE_URL is not set"),
        ("tests/test_extraction_parsers.py::test_ocr", "Tesseract is not installed"),
        # The right reason on the wrong test is not the entry either.
        ("tests/test_intake_staging.py::test_x", INTAKE_REASON),
    ],
)
def test_anything_else_is_unexpected(node, reason):
    assert not policy.is_expected(node, reason)
    assert policy.unexpected([(node, reason)]) == [(node, reason)]


def test_every_entry_says_why_it_is_legitimate():
    for entry in policy.EXPECTED_SKIPS:
        assert len(entry.why.split()) >= 8, entry


def test_the_reason_is_read_from_a_skipped_report():
    assert policy.skip_reason(("file.py", 3, "Skipped: E2E_BASE_URL is not set")) == (
        "E2E_BASE_URL is not set"
    )


def test_enforcement_is_on_in_github_actions(monkeypatch):
    monkeypatch.delenv("JURISTID_ENFORCE_EXPECTED_SKIPS", raising=False)
    monkeypatch.setenv("GITHUB_ACTIONS", "true")
    assert policy.enforced()
    monkeypatch.setenv("GITHUB_ACTIONS", "false")
    assert not policy.enforced()


def test_a_missing_e2e_variable_fails_in_ci_and_skips_locally(monkeypatch):
    from e2e import conftest as browser

    monkeypatch.setenv("GITHUB_ACTIONS", "true")
    with pytest.raises(pytest.fail.Exception, match="cannot run in CI"):
        browser._missing("E2E_BASE_URL is not set")

    monkeypatch.setenv("GITHUB_ACTIONS", "false")
    monkeypatch.delenv("JURISTID_ENFORCE_EXPECTED_SKIPS", raising=False)
    with pytest.raises(pytest.skip.Exception):
        browser._missing("E2E_BASE_URL is not set")


#: The probe's conftest takes the two skip hooks from the real root conftest, so
#: the run exercises them without the probe file ever entering the repository,
#: where a test that scans the tree could read it half-written or already gone.
PROBE_CONFTEST = f"""
import importlib.util

_spec = importlib.util.spec_from_file_location(
    "juristid_root_conftest", {str(ROOT / "conftest.py")!r}
)
_root = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_root)

pytest_runtest_logreport = _root.pytest_runtest_logreport
pytest_sessionfinish = _root.pytest_sessionfinish
"""


def _run_probe(source: str, tmp_path: Path, *, enforce: bool) -> subprocess.CompletedProcess[str]:
    """Run a throwaway test file, outside the repository, through the root skip hooks."""
    (tmp_path / "conftest.py").write_text(PROBE_CONFTEST, encoding="utf-8")
    (tmp_path / "test_skip_probe.py").write_text(source, encoding="utf-8")
    environment = {
        **os.environ,
        "JURISTID_ENFORCE_EXPECTED_SKIPS": "1" if enforce else "0",
        "PYTHONPATH": os.pathsep.join(filter(None, [str(ROOT), os.environ.get("PYTHONPATH")])),
    }
    environment.pop("GITHUB_ACTIONS", None)
    return subprocess.run(  # noqa: S603 - our own interpreter and files we wrote
        [
            sys.executable,
            "-m",
            "pytest",
            "-q",
            "-p",
            "no:cacheprovider",
            "-p",
            "no:django",
            "--rootdir",
            str(tmp_path),
            str(tmp_path / "test_skip_probe.py"),
        ],
        cwd=tmp_path,
        env=environment,
        capture_output=True,
        text=True,
        check=False,
    )


SKIPPING = """
import pytest

def test_passes():
    assert True

def test_waits_for_a_seed_that_never_comes():
    pytest.skip("this world has no such row")
"""


def test_an_unexpected_skip_fails_the_run_when_enforced(tmp_path):
    result = _run_probe(SKIPPING, tmp_path, enforce=True)

    assert result.returncode == 1, result.stdout
    assert "unexpected skips" in result.stdout
    assert "this world has no such row" in result.stdout


def test_the_same_skip_is_only_a_skip_locally(tmp_path):
    result = _run_probe(SKIPPING, tmp_path, enforce=False)
    assert result.returncode == 0, result.stdout


def test_an_expected_failure_is_not_a_skip(tmp_path):
    result = _run_probe(
        """
import pytest

@pytest.mark.xfail(reason="known", strict=True)
def test_known():
    assert False
""",
        tmp_path,
        enforce=True,
    )
    assert result.returncode == 0, result.stdout
