"""The shard-health report names a missing file and an overrun shard, loudly (JTEST-006).

The 2026-10-11 table missed 66 files — 30 % of the suite's time — and the
report said "timings are current", because it only warned on the *share* of
unmeasured tests; the slowest file ran about 200 times its estimate, and no
share threshold sees one file. `scripts/ci/report_shard_health.py` now names
every collected file the table does not hold, and every shard whose actual test
time is more than twice its prediction, as `Loud` warnings, which `--strict`
(as CI runs it, in a step that continues on error) turns into exit 1.
"""

from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

from django.conf import settings

ROOT = Path(settings.BASE_DIR)
sys.path.insert(0, str(ROOT))

import ci_sharding  # noqa: E402


def _load(name: str):
    spec = importlib.util.spec_from_file_location(name, ROOT / "scripts" / "ci" / f"{name}.py")
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


health = _load("report_shard_health")

#: The real loader, kept before any test replaces it on the module.
LOAD_TIMINGS = ci_sharding.load_timings

MEASURED = "tests/test_ci_sharding.py"
UNMEASURED = "tests/test_ci_skip_policy.py"


def _report(path: Path, cases: list[tuple[str, float]]) -> None:
    body = "".join(
        f'<testcase classname="{path_.removesuffix(".py").replace("/", ".")}" '
        f'name="t{index}" time="{seconds}"/>'
        for index, (path_, seconds) in enumerate(cases)
    )
    path.write_text(f'<testsuites><testsuite name="pytest">{body}</testsuite></testsuites>')


def _table(**files: float) -> dict[str, ci_sharding.Measurement]:
    return {
        path: ci_sharding.Measurement(seconds=seconds, tests=1) for path, seconds in files.items()
    }


def test_a_collected_file_the_table_does_not_hold_is_named():
    warnings = health.report_suite(
        "PostgreSQL test suite",
        {MEASURED: 1, UNMEASURED: 4},
        2,
        _table(**{MEASURED: 10.0}),
    )

    loud = [warning for warning in warnings if isinstance(warning, health.Loud)]
    assert len(loud) == 1
    assert f"{UNMEASURED} (4)" in loud[0]
    assert "1 collected file(s) are not in ci/shard-timings.json" in loud[0]


def test_a_fully_measured_suite_says_nothing_loud():
    warnings = health.report_suite(
        "PostgreSQL test suite",
        {MEASURED: 1, UNMEASURED: 1},
        2,
        _table(**{MEASURED: 10.0, UNMEASURED: 10.0}),
    )

    assert not [warning for warning in warnings if isinstance(warning, health.Loud)]


def test_a_shard_past_twice_its_prediction_is_named_with_the_file_that_did_it():
    timings = _table(**{MEASURED: 10.0, UNMEASURED: 5.0})
    counts = {MEASURED: 1, UNMEASURED: 1}

    over = health.overrun_warnings(
        "PostgreSQL test suite", {3: [(MEASURED, 40.0), (UNMEASURED, 5.0)]}, counts, timings, 0.0
    )
    within = health.overrun_warnings(
        "PostgreSQL test suite", {3: [(MEASURED, 25.0), (UNMEASURED, 5.0)]}, counts, timings, 0.0
    )

    assert within == []
    assert len(over) == 1 and isinstance(over[0], health.Loud)
    assert "shard 3 took 45s, 3.0x its prediction of 15s" in over[0]
    assert over[0].index(MEASURED) < over[0].index(UNMEASURED)
    assert f"{MEASURED} 40s vs 10s" in over[0]


def test_the_prediction_is_of_the_files_the_shard_ran_plus_its_setup():
    """A run made under another table ran other files; its own files are what it is held to."""
    timings = _table(**{MEASURED: 10.0})

    # 25 s of tests against 10 s of files and 20 s of setup: 0.83x, not 2.5x.
    assert health.overrun_warnings("x", {1: [(MEASURED, 25.0)]}, {MEASURED: 1}, timings, 20.0) == []


def test_this_runs_reports_raise_both_warnings(tmp_path):
    _report(tmp_path / "junit-tests-1.xml", [(MEASURED, 50.0)])
    _report(tmp_path / "junit-tests-2.xml", [(UNMEASURED, 3.0)])
    found: list[str] = []

    health.report_durations(
        tmp_path,
        {},
        {"tests": 2, "browser": 1},
        _table(**{MEASURED: 10.0}),
        {},
        warnings=found,
    )

    assert all(isinstance(warning, health.Loud) for warning in found)
    assert any(
        UNMEASURED in warning and "not in ci/shard-timings.json" in warning for warning in found
    )
    assert any("shard 1 took 50s, 5.0x its prediction of 10s" in warning for warning in found)


def _main(monkeypatch, tmp_path, *flags: str) -> int:
    tmp_path.mkdir(parents=True)
    table = tmp_path / "shard-timings.json"
    table.write_text(
        json.dumps(
            {
                "measured_at": "2026-10-18",
                "files": {MEASURED: {"seconds": 10.0, "tests": 1}},
            }
        )
    )
    reports = tmp_path / "junit"
    reports.mkdir()
    _report(reports / "junit-tests-1.xml", [(MEASURED, 10.0), (UNMEASURED, 2.0)])
    monkeypatch.setattr(health, "TIMINGS", table)
    measured = LOAD_TIMINGS(table)
    monkeypatch.setattr(health.ci_sharding, "load_timings", lambda: measured)
    monkeypatch.setattr(health.ci_sharding, "load_session_setup", lambda: {})
    monkeypatch.delenv("GITHUB_ACTIONS", raising=False)
    monkeypatch.delenv("GITHUB_STEP_SUMMARY", raising=False)
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "report_shard_health.py",
            "--durations",
            str(reports),
            "--no-collect",
            "--today",
            "2026-10-18",
            *flags,
        ],
    )
    return health.main()


def test_strict_fails_on_a_loud_warning_and_the_plain_report_does_not(
    monkeypatch, tmp_path, capsys
):
    assert _main(monkeypatch, tmp_path / "plain") == 0
    assert _main(monkeypatch, tmp_path / "strict", "--strict") == 1
    assert "CI SHARD TIMINGS NEED REFRESH" in capsys.readouterr().out


def test_in_ci_every_warning_is_an_annotation(monkeypatch, tmp_path, capsys):
    monkeypatch.setenv("GITHUB_ACTIONS", "true")
    table = tmp_path / "shard-timings.json"
    table.write_text(json.dumps({"measured_at": "2026-10-18", "files": {}}))
    monkeypatch.setattr(health, "TIMINGS", table)
    monkeypatch.setattr(health.ci_sharding, "load_timings", lambda: {})
    monkeypatch.setattr(health.ci_sharding, "load_session_setup", lambda: {})
    reports = tmp_path / "junit"
    reports.mkdir()
    _report(reports / "junit-tests-1.xml", [(MEASURED, 10.0)])
    monkeypatch.setattr(
        sys,
        "argv",
        ["report_shard_health.py", "--durations", str(reports), "--no-collect", "--strict"],
    )

    assert health.main() == 1
    assert f"::warning title={health.HEADLINE}::PostgreSQL" in capsys.readouterr().out
