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
import os
import pathlib
import re
import statistics
import subprocess
import sys
import xml.etree.ElementTree as ElementTree

ROOT = pathlib.Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

import ci_sharding  # noqa: E402  (after sys.path, so this runs from anywhere)

WORKFLOW = ROOT / ".github" / "workflows" / "ci.yml"
TIMINGS = ROOT / "ci" / "shard-timings.json"

import ci_workflow_selection  # noqa: E402  (after sys.path, like ci_sharding)

#: The suites the workflow shards, and the pytest arguments that define each —
#: read from the workflow's own commands by `ci_workflow_selection`, the one
#: definition ``scripts/ci/assert_shard_completeness.py`` also works from. This
#: used to be a second hand-copied list that nothing tied to either (ENG-110).
SUITES: dict[str, tuple[str, list[str]]] = {
    name: (suite.label, list(suite.selection))
    for name, suite in ci_workflow_selection.suites(WORKFLOW).items()
    if name in ("tests", "browser")
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
    """How many shards the workflow runs of each sharded suite (runners x shards each)."""
    layout = ci_workflow_selection.topology(WORKFLOW)
    return {name: layout[name].shard_count for name in SUITES}


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


#: Where each suite's files live, which is how its session setup is keyed.
SUITE_DIRECTORY = {"tests": "tests", "browser": "e2e"}

#: `junit-tests-3.xml`, `junit-browser-2.xml`: the job and the 1-based shard.
REPORT_NAME = re.compile(r"^junit-(?P<job>[a-z]+)-(?P<shard>\d+)$")

#: `shard-tests-3.json`: what scripts/ci/run_slices.py recorded about a shard.
RECORD_NAME = re.compile(r"^shard-(?P<job>[a-z]+)-(?P<shard>\d+)$")

#: The sharded jobs' names as the Actions API reports them, one per runner.
JOB_NAME = re.compile(r"^(?P<kind>PostgreSQL tests|Browser), runner (?P<runner>\d+) of \d+$")

#: The step of each sharded job that runs its tests; everything else is setup.
SUITE_STEP = {job: ci_workflow_selection.SUITE_STEPS[job][0] for job in ("tests", "browser")}


def predicted_shards(
    job: str,
    counts: dict[str, int],
    shard_count: int,
    timings: dict[str, ci_sharding.Measurement],
    setup: dict[str, float],
) -> list[float]:
    """Seconds each shard is predicted to take, in shard order, setup included."""
    loads = ci_sharding.predicted_load(sorted(counts), shard_count, timings, counts)
    fixed = setup.get(SUITE_DIRECTORY.get(job, job), 0.0)
    return [load + fixed for load in loads]


def report_suite(
    label: str,
    counts: dict[str, int],
    shard_count: int,
    timings: dict[str, ci_sharding.Measurement],
    setup: float = 0.0,
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

    loads = [
        load + setup for load in ci_sharding.predicted_load(files, shard_count, timings, counts)
    ]
    if setup:
        print(f"  session setup       {setup:.0f}s per shard, modelled apart from the files")
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


def _cases(report: pathlib.Path) -> list[tuple[str, float]]:
    """(file, seconds) for each test case in one JUnit report, in run order."""
    # This repository's own CI artifacts, downloaded by the person running
    # this. Reaching for defusedxml here would be theatre.
    cases = []
    for case in ElementTree.parse(report).getroot().iter("testcase"):  # noqa: S314
        parts = (case.get("classname") or "").split(".")
        while parts and not (ROOT / ("/".join(parts) + ".py")).exists():
            parts.pop()
        path = "/".join(parts) + ".py" if parts else "?"
        cases.append((path, float(case.get("time", "0") or 0)))
    return cases


def shard_reports(directory: pathlib.Path) -> dict[str, dict[int, list[tuple[str, float]]]]:
    """Each sharded suite's JUnit cases, by 1-based shard, from one run's artifacts."""
    found: dict[str, dict[int, list[tuple[str, float]]]] = collections.defaultdict(dict)
    for report in sorted(directory.rglob("*.xml")):
        match = REPORT_NAME.match(report.stem)
        if match and match.group("job") in SUITES:
            found[match.group("job")][int(match.group("shard"))] = _cases(report)
    return found


def shard_records(directory: pathlib.Path) -> dict[str, dict[int, dict]]:
    """What `run_slices.py` recorded per shard: its runner, pytest wall time, world setup."""
    found: dict[str, dict[int, dict]] = collections.defaultdict(dict)
    for record in sorted(directory.rglob("shard-*-*.json")):
        match = RECORD_NAME.match(record.stem)
        if match:
            found[match.group("job")][int(match.group("shard"))] = json.loads(
                record.read_text(encoding="utf-8")
            )
    return found


def counts_from_reports(reports: dict[int, list[tuple[str, float]]]) -> dict[str, int]:
    """Tests per file across every shard of a suite: the collection, as it ran.

    Every collected test is in exactly one shard's report (the completeness
    proof's property), so the union is what pytest collected — without paying
    for a collection to learn it.
    """
    counts: collections.Counter[str] = collections.Counter()
    for cases in reports.values():
        counts.update(path for path, _ in cases)
    return dict(counts)


def job_timings(run_id: str, repository: str, token: str) -> dict[str, dict[int, dict]]:
    """Per runner: the job's wall time and its test step's, from the Actions API."""
    import urllib.request

    found: dict[str, dict[int, dict]] = collections.defaultdict(dict)
    request = urllib.request.Request(
        f"https://api.github.com/repos/{repository}/actions/runs/{run_id}/jobs?per_page=100",
        headers={
            "Accept": "application/vnd.github+json",
            "Authorization": f"Bearer {token}",
            "User-Agent": "juristid-shard-health",
        },
    )
    with urllib.request.urlopen(request, timeout=30) as response:  # noqa: S310
        jobs = json.load(response).get("jobs", [])

    def seconds(item: dict) -> float | None:
        if not item.get("started_at") or not item.get("completed_at"):
            return None
        started = datetime.datetime.fromisoformat(item["started_at"].replace("Z", "+00:00"))
        done = datetime.datetime.fromisoformat(item["completed_at"].replace("Z", "+00:00"))
        return (done - started).total_seconds()

    for job in jobs:
        match = JOB_NAME.match(job.get("name", ""))
        if not match:
            continue
        suite = "tests" if match.group("kind") == "PostgreSQL tests" else "browser"
        test_step = SUITE_STEP[suite]
        step = next((s for s in job.get("steps", []) if s.get("name") == test_step), {})
        found[suite][int(match.group("runner"))] = {
            "job_seconds": seconds(job),
            "test_step_seconds": seconds(step),
        }
    return found


def _ratio(values: list[float]) -> str:
    if not values or min(values) <= 0:
        return "n/a"
    slowest, median, fastest = max(values), statistics.median(values), min(values)
    return (
        f"slowest / median {slowest:.0f}s / {median:.0f}s = {slowest / median:.2f}, "
        f"slowest / fastest {slowest:.0f}s / {fastest:.0f}s = {slowest / fastest:.2f}"
    )


def report_durations(
    directory: pathlib.Path,
    collected: dict[str, dict[str, int]],
    shard_counts_by_job: dict[str, int],
    timings: dict[str, ci_sharding.Measurement],
    setup: dict[str, float],
    *,
    runners_by_job: dict[str, int] | None = None,
    jobs: dict[str, dict[int, dict]] | None = None,
) -> dict[str, object]:
    """What this run's shards were predicted to take, beside what they took.

    ENG-139. The build printed only the prediction, and a model balances
    perfectly against itself: «slowest / median 1.00» while the same run's
    PostgreSQL shards took 338/283/384/275/344/286 s. Read from the reports the
    shards of *this* run uploaded, so the two columns describe one run.

    Per shard: the prediction, the test time its JUnit report accounts for, the
    ratio of the two (a runner that is simply slower shows as a ratio well
    above the rest — balance cannot fix that, a better runner can), the wall
    time of its pytest process and, for a browser shard, of building its world.
    Per runner: its job and its test step, so setup and tests are seen apart —
    the round waits for the slowest *runner*, which is the number to tune.
    Returns the same numbers as data, for the job summary and for later tuning.
    """
    print("\npredicted against actual, this run")
    reports = shard_reports(directory)
    records = shard_records(directory)
    summary: dict[str, object] = {}
    if not reports:
        print(f"  no shard JUnit reports under {directory}")
        return summary
    for job in SUITES:
        actual_cases = reports.get(job)
        if not actual_cases:
            continue
        count = shard_counts_by_job[job]
        runners = (runners_by_job or {}).get(job, count)
        per_runner = max(count // runners, 1)
        counts = collected.get(job) or counts_from_reports(actual_cases)
        files = sorted(counts)
        partition = ci_sharding.partition(files, count, timings, counts)
        fixed = setup.get(SUITE_DIRECTORY.get(job, job), 0.0)
        predicted = [
            sum(ci_sharding.weight_of(path, counts.get(path), timings) for path in shard) + fixed
            for shard in partition
        ]
        actual = {
            index: sum(seconds for _, seconds in cases) for index, cases in actual_cases.items()
        }
        # The partition recomputed here is the one the shards ran, or the
        # prediction describes some other run. Same inputs, same pure function.
        diverged = [
            index
            for index, cases in actual_cases.items()
            if 1 <= index <= count and {path for path, _ in cases} != set(partition[index - 1])
        ]

        print(f"\n  {SUITES[job][0]}: {count} shards on {runners} runners")
        print("    shard runner  predicted     actual  actual/pred  pytest wall  world setup")
        rows = []
        for index in range(1, count + 1):
            record = records.get(job, {}).get(index, {})
            runner = record.get("runner") or (index - 1) // per_runner + 1
            took = actual.get(index)
            world = (record.get("setup_seconds") or {}).get("total")
            wall = record.get("pytest_seconds")
            factor = took / predicted[index - 1] if took and predicted[index - 1] else None
            rows.append(
                {
                    "shard": index,
                    "runner": runner,
                    "predicted": round(predicted[index - 1], 1),
                    "actual": None if took is None else round(took, 1),
                    "pytest_wall": wall,
                    "world_setup": None if world is None else round(world, 1),
                }
            )
            print(
                f"    {index:5d} {runner:6d}  {predicted[index - 1]:8.0f}s  "
                + (f"{took:8.0f}s" if took is not None else "   missing")
                + (f"  {factor:11.2f}" if factor else "            -")
                + (f"  {wall:10.0f}s" if wall else "            -")
                + (f"  {world:10.0f}s" if world else "            -")
            )
        print(f"    predicted  {_ratio(predicted)}")
        print(f"    actual     {_ratio(list(actual.values()))}")
        factors = [
            actual[index] / predicted[index - 1]
            for index in actual
            if 1 <= index <= count and predicted[index - 1]
        ]
        if factors:
            # The spread here is what runners add beyond the table: a shard on a
            # slow runner reads high whatever its files. Wide spread with a fresh
            # table is runner variance, not balance.
            print(
                f"    actual/predicted  min {min(factors):.2f}  median "
                f"{statistics.median(factors):.2f}  max {max(factors):.2f}"
            )
        walls = [row["pytest_wall"] for row in rows if row["pytest_wall"]]
        if walls:
            print(f"    pytest wall {_ratio(walls)}")
        runner_rows = []
        for runner, timing in sorted((jobs or {}).get(job, {}).items()):
            job_seconds, step_seconds = timing.get("job_seconds"), timing.get("test_step_seconds")
            if job_seconds is None or step_seconds is None:
                continue
            runner_rows.append(
                {
                    "runner": runner,
                    "job": round(job_seconds),
                    "tests": round(step_seconds),
                    "setup": round(job_seconds - step_seconds),
                }
            )
        if runner_rows:
            print("    runner     job   test step   everything else (setup, uploads)")
            for row in runner_rows:
                runner, total, tests, rest = row["runner"], row["job"], row["tests"], row["setup"]
                print(f"    {runner:6d}  {total:5d}s  {tests:9d}s  {rest:9d}s")
            print(f"    runner job {_ratio([float(row['job']) for row in runner_rows])}")
        if diverged:
            print(
                f"    NOTE: shard(s) {sorted(diverged)} ran other files than the partition "
                "recomputed here; the table or the collection differs from the run's"
            )
        summary[job] = {
            "shard_count": count,
            "runners": runners,
            "shards": rows,
            "runner_jobs": runner_rows,
            "partition_reproduced": not diverged,
        }
    return summary


def write_step_summary(summary: dict[str, object], path: str) -> None:
    """The same numbers as a table on the run's page."""
    lines = ["## Shard balance, predicted against actual", ""]
    for job, data in summary.items():
        if not isinstance(data, dict):
            continue
        lines += [
            f"### {SUITES[job][0]}: {data['shard_count']} shards on {data['runners']} runners",
            "",
            "| shard | runner | predicted | actual | pytest wall | world setup |",
            "| ---: | ---: | ---: | ---: | ---: | ---: |",
        ]
        for row in data["shards"]:

            def cell(value: object) -> str:
                return "–" if value is None else f"{float(value):.0f} s"

            lines.append(
                f"| {row['shard']} | {row['runner']} | {cell(row['predicted'])} | "
                f"{cell(row['actual'])} | {cell(row['pytest_wall'])} | {cell(row['world_setup'])} |"
            )
        if data["runner_jobs"]:
            lines += [
                "",
                "| runner | job | test step | everything else |",
                "| ---: | ---: | ---: | ---: |",
            ]
            for row in data["runner_jobs"]:
                lines.append(
                    f"| {row['runner']} | {row['job']} s | {row['tests']} s | {row['setup']} s |"
                )
        lines.append("")
    with open(path, "a", encoding="utf-8") as handle:
        handle.write("\n".join(lines) + "\n")


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
    parser.add_argument(
        "--no-collect",
        action="store_true",
        help=(
            "take each suite's files and test counts from the --durations reports instead "
            "of collecting them; the quality job already reports staleness from a collection"
        ),
    )
    parser.add_argument(
        "--run-id",
        default=None,
        help="this run's id, to read each runner's job and test-step times (needs GH_TOKEN)",
    )
    parser.add_argument("--repository", default="Kauror/juristid")
    parser.add_argument(
        "--summary-json",
        type=pathlib.Path,
        default=None,
        help="also write the predicted-against-actual numbers here, as data",
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
        if isinstance(measured_from, list):
            print(f"\ntable is the median of {len(measured_from)} runs:")
            for run in measured_from:
                print(f"           {run}")
        else:
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
    setup = ci_sharding.load_session_setup()
    layout = ci_workflow_selection.topology(WORKFLOW)
    counts = {job: layout[job].shard_count for job in SUITES}
    collected: dict[str, dict[str, int]] = {}
    for job, (label, arguments_for_pytest) in SUITES.items():
        if arguments.no_collect:
            continue
        collected[job] = collect(arguments_for_pytest)
        warnings.extend(
            report_suite(
                label,
                collected[job],
                counts[job],
                timings,
                setup.get(SUITE_DIRECTORY.get(job, job), 0.0),
            )
        )

    if arguments.durations is not None:
        jobs: dict[str, dict[int, dict]] = {}
        token = os.environ.get("GH_TOKEN") or os.environ.get("GITHUB_TOKEN")
        if arguments.run_id and token:
            try:
                jobs = job_timings(arguments.run_id, arguments.repository, token)
            except (OSError, ValueError, KeyError) as error:
                print(f"\n(the runners' job times could not be read: {error})")
        summary = report_durations(
            arguments.durations,
            collected,
            counts,
            timings,
            setup,
            runners_by_job={job: layout[job].runners for job in SUITES},
            jobs=jobs,
        )
        if arguments.summary_json is not None:
            arguments.summary_json.write_text(
                json.dumps(summary, indent=2) + "\n", encoding="utf-8"
            )
        if os.environ.get("GITHUB_STEP_SUMMARY") and summary:
            write_step_summary(summary, os.environ["GITHUB_STEP_SUMMARY"])

    print()
    if warnings:
        print(HEADLINE)
        for warning in warnings:
            print(f"  - {warning}")
        print()
        print("  Refresh it from the median of recent green runs (docs/ci-architecture.md):")
        print("    uv run python scripts/ci/update_shard_timings.py --latest 5")
    else:
        print("shard timings are current; nothing to do")

    # Always 0. This reports, it does not gate — see the module docstring.
    return 0


if __name__ == "__main__":
    sys.exit(main())
