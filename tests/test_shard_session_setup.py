"""Session setup is a per-shard cost, not the weight of whichever file ran first (ENG-139).

JUnit books a test's setup to the test, so creating and migrating the test
database landed on the first test of every shard and the table weighed that
file accordingly. `update_shard_timings.py` now measures the one-off cost as the
first test's excess over its own file's other tests, takes the median across
shards, removes it from each first file and records it beside the files; the
health report adds it back to predicted shard durations and prints them beside
what the same run's shards actually took.
"""

from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

import pytest
from django.conf import settings

ROOT = Path(settings.BASE_DIR)


def _load(name: str):
    spec = importlib.util.spec_from_file_location(name, ROOT / "scripts" / "ci" / f"{name}.py")
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


update = _load("update_shard_timings")

A = "tests.test_ci_sharding"
B = "tests.test_ci_skip_policy"


def _report(path: Path, cases: list[tuple[str, float]]) -> None:
    body = "".join(
        f'<testcase classname="{classname}" name="t{index}" time="{seconds}"/>'
        for index, (classname, seconds) in enumerate(cases)
    )
    path.write_text(f'<testsuites><testsuite name="pytest">{body}</testsuite></testsuites>')


def test_the_first_tests_excess_is_the_session_setup(tmp_path):
    # Three shards; each starts with a file whose first test paid ~20 s extra.
    _report(tmp_path / "junit-tests-1.xml", [(A, 21.0), (A, 1.0), (A, 1.0), (B, 2.0)])
    _report(tmp_path / "junit-tests-2.xml", [(B, 22.0), (B, 2.0), (A, 1.0)])
    _report(tmp_path / "junit-tests-3.xml", [(A, 20.5), (A, 0.5), (B, 2.0), (B, 2.0)])

    seconds, counts, setup = update.read_reports(tmp_path)

    assert setup == {"tests": 20.0}
    # Each shard's first test gives up the setup, and no file carries it.
    assert seconds["tests/test_ci_sharding.py"] == pytest.approx(1.0 + 1 + 1 + 1 + 0.5 + 0.5)
    assert seconds["tests/test_ci_skip_policy.py"] == pytest.approx(2.0 + 2 + 2 + 2 + 2)
    assert counts["tests/test_ci_sharding.py"] == 6


def test_one_heavy_first_file_does_not_become_every_shards_setup(tmp_path):
    """The median: a module that really builds a world in its first test is
    one shard's outlier, not the suite's setup."""
    _report(tmp_path / "junit-tests-1.xml", [(A, 21.0), (A, 1.0)])
    _report(tmp_path / "junit-tests-2.xml", [(B, 22.0), (B, 2.0)])
    _report(tmp_path / "junit-tests-3.xml", [(A, 140.0), (A, 1.0)])

    _seconds, _counts, setup = update.read_reports(tmp_path)

    assert setup == {"tests": 20.0}


def test_the_table_records_the_setup_and_the_report_adds_it_back(tmp_path, monkeypatch):
    import ci_sharding

    table = tmp_path / "shard-timings.json"
    table.write_text(
        json.dumps(
            {
                "session_setup_seconds": {"tests": 20.0, "e2e": 1.5},
                "files": {"tests/test_ci_sharding.py": {"seconds": 10.0, "tests": 5}},
            }
        )
    )
    assert ci_sharding.load_session_setup(table) == {"tests": 20.0, "e2e": 1.5}

    health = _load("report_shard_health")
    timings = ci_sharding.load_timings(table)
    predicted = health.predicted_shards(
        "tests",
        {"tests/test_ci_sharding.py": 5},
        2,
        timings,
        {"tests": 20.0},
    )
    # Two shards: one holds the file, the other nothing — both pay the setup.
    assert sorted(predicted) == [20.0, 30.0]


def test_the_report_prints_predicted_and_actual_side_by_side(tmp_path, capsys):
    import ci_sharding

    health = _load("report_shard_health")
    _report(tmp_path / "junit-tests-1.xml", [(A, 30.0)])
    _report(tmp_path / "junit-tests-2.xml", [(B, 40.0)])
    _report(tmp_path / "junit-visual.xml", [(A, 99.0)])
    timings = {
        "tests/test_ci_sharding.py": ci_sharding.Measurement(seconds=10.0, tests=1),
        "tests/test_ci_skip_policy.py": ci_sharding.Measurement(seconds=12.0, tests=1),
    }
    health.report_durations(
        tmp_path,
        {
            "tests": {"tests/test_ci_sharding.py": 1, "tests/test_ci_skip_policy.py": 1},
            "browser": {},
        },
        {"tests": 2, "browser": 1},
        timings,
        {"tests": 20.0},
    )
    printed = capsys.readouterr().out

    assert "predicted against actual, this run" in printed
    assert "30s" in printed and "40s" in printed
    assert "predicted  slowest / median" in printed
    assert "actual     slowest / median 40s / 35s = 1.14" in printed
    # The visual suite's report is not a shard and is not read as one.
    assert "99s" not in printed
