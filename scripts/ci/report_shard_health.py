"""Say out loud when CI's balance metadata has gone stale, before anyone times it.

``ci/shard-timings.json`` decays quietly. Nothing goes red, no test stops
running, no reviewer sees a warning — the shards simply drift apart until one
runner carries the suite and the others idle, and the only symptom is that
everybody waits longer. That is exactly the kind of rot that is noticed months
late, and it is what happened here: the table was refreshed on 2026-08-30 and
read again on 2026-09-21, by which point 39% of the collected files had no
measurement at all and the slowest PostgreSQL shard was running 42% above the
median of its own run.

So this prints the numbers every build, and says the words
``CI SHARD TIMINGS NEED REFRESH`` when they cross a threshold that was chosen
from that measurement rather than from taste.

It is **informational and exits 0 whatever it finds**, including when it cannot
collect at all. A performance report is not a correctness proof, hosted runners
have slow days, and a build that goes red because a table is three weeks old
would teach people to ignore it. The correctness proofs are
``tests/test_ci_sharding.py`` and ``scripts/ci/assert_shard_completeness.py``;
this is the gauge next to them, not another gate.

Run it anywhere::

    uv run python scripts/ci/report_shard_health.py

and, when the JUnit artifacts of a finished run are on disk, ask it to compare
the prediction against what those runners actually did::

    gh run download <run-id> --dir /tmp/junit --pattern 'test-report-*'
    uv run python scripts/ci/report_shard_health.py --durations /tmp/junit
"""

from __future__ import annotations

import argparse
import collections
import datetime
import json
import pathlib
import statistics
import subprocess
import sys
import xml.etree.ElementTree as ElementTree

import yaml

ROOT = pathlib.Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

import ci_sharding  # noqa: E402  (after sys.path, so this runs from anywhere)

WORKFLOW = ROOT / ".github" / "workflows" / "ci.yml"
TIMINGS = ROOT / "ci" / "shard-timings.json"

#: The suites the workflow shards, and the pytest arguments that define each —
#: the same pair ``scripts/ci/assert_shard_completeness.py`` works from, so the
#: two scripts cannot disagree about what a suite is.
SUITES: dict[str, tuple[str, list[str]]] = {
    "tests": ("PostgreSQL", ["tests"]),
    "browser": ("Browser", ["e2e", "--ignore=e2e/test_ui_regression.py"]),
}

# ---------------------------------------------------------------------------
# Thresholds
#
# Every one of these is a number the 2026-08-30 table crossed before anybody
# noticed, so each is set where it would have fired while the drift was still
# cheap to fix rather than after it had cost minutes a run.
# ---------------------------------------------------------------------------

#: A table older than this is suspect on its age alone. A backstop rather than
#: the main signal: this repository churns fast enough that the unmeasured
#: fraction below always crosses first, and a quiet month is the case this
#: catches.
STALE_AFTER_DAYS = 30

#: What share of the collected *tests* may be weighed by estimate rather than by
#: measurement. Unmeasured tests are not wrong, they are guessed — from their
#: directory's median per-test rate — and balance degrades roughly with their
#: mass. At 36% the partition was 42% out; 15% is where the cost is still around
#: a few seconds a shard.
UNMEASURED_TESTS_WARN = 0.15

#: The same for files. A directory full of small new files can pass the test
#: threshold while most of the table is guesswork, which is worth saying.
UNMEASURED_FILES_WARN = 0.20

#: How far the slowest shard may be predicted above the median before the split
#: itself is the problem. The partition balances its own model to within a few
#: percent, so this only fires when the model *cannot* balance — one file
#: heavier than a whole shard's fair share — which is the signal that the shard
#: count has been raised past what whole-file granularity supports.
IMBALANCE_WARN = 1.25

HEADLINE = "CI SHARD TIMINGS NEED REFRESH"


def shard_counts() -> dict[str, int]:
    """How many runners the workflow gives each sharded job, read from the matrix."""
    workflow = yaml.safe_load(WORKFLOW.read_text(encoding="utf-8"))
    counts: dict[str, int] = {}
    for name in SUITES:
        job = workflow["jobs"].get(name) or {}
        shards = job.get("strategy", {}).get("matrix", {}).get("shard")
        counts[name] = len(shards) if shards else 1
    return counts


def collect(arguments: list[str]) -> dict[str, int]:
    """How many tests each file holds right now, as pytest collects them."""
    # The arguments are the constants at the top of this file. Nothing here
    # comes from outside the repository.
    completed = subprocess.run(  # noqa: S603
        [
            sys.executable,
            "-m",
            "pytest",
            "--collect-only",
            "-q",
            "--no-header",
            "-p",
            "no:cacheprovider",
            *arguments,
        ],
        cwd=ROOT,
        capture_output=True,
        text=True,
    )
    if completed.returncode != 0:
        return {}
    counts: collections.Counter[str] = collections.Counter()
    for line in completed.stdout.splitlines():
        line = line.strip()
        if "::" not in line or line.startswith(("=", "-", "[")):
            continue
        path = line.split("::", 1)[0]
        if path.endswith(".py"):
            counts[path] += 1
    return dict(counts)


def actual_durations(directory: pathlib.Path) -> dict[str, float]:
    """Seconds of test time each downloaded JUnit report accounts for.

    One report per shard, so the spread across them is what those runners
    actually did — the only honest check on what the table predicted.
    """
    durations: dict[str, float] = {}
    for report in sorted(directory.rglob("*.xml")):
        # This repository's own CI artifacts, downloaded by the person running
        # this. Reaching for defusedxml here would be theatre.
        root = ElementTree.parse(report).getroot()  # noqa: S314
        durations[report.stem] = sum(
            float(case.get("time", "0") or 0) for case in root.iter("testcase")
        )
    return durations


def report_suite(
    label: str,
    counts: dict[str, int],
    shard_count: int,
    timings: dict[str, ci_sharding.Measurement],
) -> list[str]:
    """Print one suite's health, and return whatever is worth warning about."""
    warnings: list[str] = []
    print(f"\n{label}: {shard_count} shards")

    if not counts:
        print("  could not collect this suite; skipping its report")
        return warnings

    files = sorted(counts)
    tests = sum(counts.values())

    unmeasured = [path for path in files if path not in timings]
    unmeasured_tests = sum(counts[path] for path in unmeasured)
    file_share = len(unmeasured) / len(files)
    test_share = unmeasured_tests / tests if tests else 0.0

    print(f"  collected           {len(files)} files, {tests} tests")
    print(
        f"  unmeasured          {len(unmeasured)} files ({file_share:.0%}), "
        f"{unmeasured_tests} tests ({test_share:.0%})"
    )

    # Drift is the other half of staleness: a file the table knows, whose test
    # count has moved. The partition rescales those from their own per-test
    # rate, so they degrade gently — but a lot of movement means the run behind
    # the table is no longer the suite being run.
    grown = sum(1 for path in files if path in timings and timings[path].tests != counts[path])
    print(
        f"  moved since measured {grown} files hold a different number of tests than the table says"
    )

    loads = ci_sharding.predicted_load(files, shard_count, timings, counts)
    slowest, median = max(loads), statistics.median(loads)
    ratio = slowest / median if median else 0.0
    print(
        f"  predicted per shard {' '.join(f'{load:.0f}s' for load in sorted(loads, reverse=True))}"
    )
    print(f"  slowest / median    {slowest:.0f}s / {median:.0f}s = {ratio:.2f}")

    if test_share > UNMEASURED_TESTS_WARN:
        warnings.append(
            f"{label}: {test_share:.0%} of collected tests have no measurement "
            f"(over {UNMEASURED_TESTS_WARN:.0%})"
        )
    if file_share > UNMEASURED_FILES_WARN:
        warnings.append(
            f"{label}: {file_share:.0%} of collected files have no measurement "
            f"(over {UNMEASURED_FILES_WARN:.0%})"
        )
    if ratio > IMBALANCE_WARN:
        warnings.append(
            f"{label}: the slowest shard is predicted at {ratio:.2f}x the median "
            f"(over {IMBALANCE_WARN:.2f}) — the split cannot balance this file set, "
            "so the shard count is past what whole-file granularity supports"
        )
    return warnings


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--durations",
        type=pathlib.Path,
        default=None,
        help="a directory of downloaded JUnit reports, to compare against the prediction",
    )
    parser.add_argument(
        "--today",
        default=datetime.date.today().isoformat(),
        help="the date to age the table against; for testing this script",
    )
    arguments = parser.parse_args()

    print("CI shard health")
    print("=" * 62)

    warnings: list[str] = []

    if not TIMINGS.exists():
        print("\nno ci/shard-timings.json is committed; every file is weighed by estimate")
        warnings.append("there is no timing table at all")
        document: dict[str, object] = {}
    else:
        document = json.loads(TIMINGS.read_text(encoding="utf-8"))
        measured_from = document.get("measured_from", "unrecorded")
        measured_at = document.get("measured_at")
        print(f"\ntable measured from {measured_from}")
        if measured_at:
            age = (
                datetime.date.fromisoformat(arguments.today)
                - datetime.date.fromisoformat(str(measured_at))
            ).days
            print(f"           measured on {measured_at} ({age} days ago)")
            if age > STALE_AFTER_DAYS:
                warnings.append(f"the timing table is {age} days old (over {STALE_AFTER_DAYS})")
        else:
            print("           measured on an unrecorded date")
            warnings.append(
                "the timing table does not say when it was measured, so its age is unknowable"
            )
        entries = document.get("files") or {}
        print(f"           {len(entries)} files weighed")

    timings = ci_sharding.load_timings()
    counts = shard_counts()
    for job, (label, arguments_for_pytest) in SUITES.items():
        warnings.extend(report_suite(label, collect(arguments_for_pytest), counts[job], timings))

    if arguments.durations is not None:
        print("\nwhat those runners actually did")
        durations = actual_durations(arguments.durations)
        if not durations:
            print(f"  no JUnit reports under {arguments.durations}")
        else:
            for name, seconds in sorted(durations.items()):
                print(f"  {seconds:7.0f}s  {name}")
            values = sorted(durations.values())
            median = statistics.median(values)
            slowest = max(values)
            print(f"  slowest / median    {slowest:.0f}s / {median:.0f}s = {slowest / median:.2f}")

    print()
    if warnings:
        print(HEADLINE)
        for warning in warnings:
            print(f"  - {warning}")
        print()
        print("  Refresh it from any ordinary green run (docs/ci-architecture.md):")
        print("    gh run download <run-id> --dir /tmp/junit --pattern 'test-report-*'")
        print("    uv run python scripts/ci/update_shard_timings.py /tmp/junit --run <run-id>")
    else:
        print("shard timings are current; nothing to do")

    # Always 0. This reports, it does not gate — see the module docstring.
    return 0


if __name__ == "__main__":
    sys.exit(main())
