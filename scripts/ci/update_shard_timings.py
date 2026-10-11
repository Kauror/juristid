"""Regenerate ``ci/shard-timings.json`` from the JUnit reports a CI run produced.

Balance metadata that nobody can refresh is metadata that rots, and a rotted
table shows up as one shard carrying the suite while the others idle. This is
the refresh, and it takes one command:

    uv run python scripts/ci/update_shard_timings.py --latest 5

which downloads the newest five green runs' shard reports (with the `gh` CLI)
and weighs each file at the median of its per-test rate across them, so one
slow runner does not set a file's weight. The table records which runs it was
taken from, so the same runs always regenerate the same table. One run still
works, from reports already on disk:

    gh run download <run-id> --dir /tmp/junit --pattern 'test-report-*'
    uv run python scripts/ci/update_shard_timings.py /tmp/junit --run <run-id>

Every sharded job uploads its report, so the reports of one run together
describe the whole suite — which is the same completeness the shards themselves
have, and the reason this can be regenerated from an ordinary green run rather
than from a special measurement build.

Correctness never depends on the output. A file the table has never heard of is
weighted from its directory's per-test rate, and a file whose entry is stale is
rescaled by its current test count (``ci_sharding.py``). This only decides which
runner picks it up.
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
import tempfile
import xml.etree.ElementTree as ElementTree

ROOT = pathlib.Path(__file__).resolve().parents[2]
OUTPUT = ROOT / "ci" / "shard-timings.json"

#: Only these directories are partitioned, so only these are worth weighing.
SUITE_DIRECTORIES = ("tests", "e2e")


def case_path(case: ElementTree.Element) -> str | None:
    """The repository-relative test file one JUnit ``testcase`` came from.

    pytest writes the module as a dotted ``classname`` — ``e2e.test_ui_shell``,
    or ``tests.test_thing.TestClass`` when the test lives in a class — and does
    not write a ``file`` attribute. Trimming the dotted path from the right
    until it names a file on disk handles both, and returns nothing rather than
    a guess for anything it cannot resolve.
    """
    attribute = case.get("file")
    if attribute:
        candidate = pathlib.PurePosixPath(attribute.replace("\\", "/")).as_posix()
        if (ROOT / candidate).exists():
            return candidate

    parts = (case.get("classname") or "").split(".")
    while parts:
        candidate = "/".join(parts) + ".py"
        if (
            candidate.startswith(tuple(f"{name}/" for name in SUITE_DIRECTORIES))
            and (ROOT / candidate).exists()
        ):
            return candidate
        parts.pop()
    return None


def _cases(report: pathlib.Path) -> list[tuple[str, float]]:
    """One report's test cases, in the order pytest ran them, with their seconds."""
    # The reports are this repository's own CI artifacts, produced by pytest
    # minutes earlier and downloaded by the person running this. Reaching for
    # defusedxml here would be theatre.
    cases = []
    for case in ElementTree.parse(report).getroot().iter("testcase"):  # noqa: S314
        path = case_path(case)
        if path is not None:
            cases.append((path, float(case.get("time", "0") or 0)))
    return cases


def session_setup(reports: list[list[tuple[str, float]]]) -> dict[str, float]:
    """The one-off cost of a shard starting, per suite: creating and migrating the
    test database, starting the application — whatever the session pays once.

    ENG-139. JUnit books a test's setup to the test, so that cost landed on the
    first test of every shard, and so on whichever file happened to run first.
    `test_approximate_lateness.py` was weighed at 21.5 s in the table and took
    5.1 s when it did not run first; the file that did run first then carried
    unpredicted seconds of its own. The partition read those as file weight.

    It is modelled as a constant per shard instead: in each report, how much
    longer the first test took than the other tests of its own file, and the
    median of that across the suite's shards — the median, so a first file that
    really is heavy (a module that builds a world once) does not become every
    shard's setup.
    """
    excess: dict[str, list[float]] = collections.defaultdict(list)
    for cases in reports:
        if not cases:
            continue
        first_path, first_seconds = cases[0]
        siblings = [seconds for path, seconds in cases[1:] if path == first_path]
        typical = (
            statistics.median(siblings)
            if siblings
            else statistics.median(seconds for _, seconds in cases)
        )
        suite = first_path.split("/", 1)[0]
        excess[suite].append(max(0.0, first_seconds - typical))
    return {suite: round(statistics.median(values), 3) for suite, values in excess.items()}


def read_reports(
    directory: pathlib.Path,
) -> tuple[dict[str, float], dict[str, int], dict[str, float]]:
    """Seconds and test count per file, and the session setup per suite.

    The session setup is taken off the first test of each report, so no file
    carries it — it is recorded beside the files as a per-shard constant.
    """
    seconds: dict[str, float] = collections.defaultdict(float)
    counts: dict[str, int] = collections.defaultdict(int)

    reports = sorted(directory.rglob("*.xml"))
    if not reports:
        raise SystemExit(f"no JUnit reports under {directory}")

    all_cases = [_cases(report) for report in reports]
    setup = session_setup(all_cases)
    for cases in all_cases:
        for index, (path, case_seconds) in enumerate(cases):
            if index == 0:
                case_seconds = max(0.0, case_seconds - setup.get(path.split("/", 1)[0], 0.0))
            seconds[path] += case_seconds
            counts[path] += 1

    return dict(seconds), dict(counts), setup


def combine_runs(
    runs: list[tuple[dict[str, float], dict[str, int], dict[str, float]]],
) -> tuple[dict[str, float], dict[str, int], dict[str, float]]:
    """One table from several runs: each file at its median per-test rate.

    `runs` is newest first. A file's test count is the newest run's that holds
    it — the suite as it is now — and its seconds are that count times the
    median of its per-test rate across every run that ran it, so one slow
    runner's afternoon does not become the file's weight (2026-10-11: the same
    PostgreSQL files ran 0.6x to 1.6x their usual time depending on the
    runner). The session setup is the median of the runs' medians. A pure
    function of its inputs: the same runs always give the same table.
    """
    rates: dict[str, list[float]] = collections.defaultdict(list)
    newest_count: dict[str, int] = {}
    setups: dict[str, list[float]] = collections.defaultdict(list)
    for seconds, counts, setup in runs:
        for path, total in seconds.items():
            if counts.get(path):
                rates[path].append(total / counts[path])
                newest_count.setdefault(path, counts[path])
        for suite, value in setup.items():
            setups[suite].append(value)
    combined = {
        path: statistics.median(values) * newest_count[path] for path, values in rates.items()
    }
    return (
        combined,
        newest_count,
        {suite: round(statistics.median(values), 3) for suite, values in setups.items()},
    )


def _gh(*arguments: str) -> str:
    # A fixed command line built here; `gh` is the operator's own GitHub CLI.
    completed = subprocess.run(  # noqa: S603
        ["gh", *arguments],  # noqa: S607
        capture_output=True,
        text=True,
        encoding="utf-8",
        check=True,
    )
    return completed.stdout


def latest_green_runs(count: int, repository: str) -> list[tuple[str, str]]:
    """(run id, date) of the newest `count` green `ci.yml` runs, newest first."""
    listed = json.loads(
        _gh(
            "run",
            "list",
            "--repo",
            repository,
            "--workflow",
            "ci.yml",
            "--status",
            "success",
            "--limit",
            str(max(count * 4, 20)),
            "--json",
            "databaseId,createdAt",
        )
    )
    runs = sorted(listed, key=lambda run: run["createdAt"], reverse=True)[:count]
    if len(runs) < count:
        raise SystemExit(f"only {len(runs)} green ci.yml runs are listed; asked for {count}")
    return [(str(run["databaseId"]), run["createdAt"][:10]) for run in runs]


def download_reports(run_id: str, repository: str, into: pathlib.Path) -> pathlib.Path:
    """One run's shard reports — the JUnit files every sharded job uploads."""
    target = into / run_id
    if not any(target.rglob("*.xml")):
        _gh(
            "run",
            "download",
            run_id,
            "--repo",
            repository,
            "--dir",
            str(target),
            "--pattern",
            "test-report-tests-*",
            "--pattern",
            "test-report-browser-*",
        )
    return target


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "reports",
        type=pathlib.Path,
        nargs="*",
        help="directories of downloaded JUnit XML, one per run, newest first",
    )
    parser.add_argument(
        "--run",
        action="append",
        default=[],
        help="the CI run id each directory came from, in the same order",
    )
    parser.add_argument(
        "--latest",
        type=int,
        default=None,
        help="download and combine the newest N green ci.yml runs (needs the `gh` CLI)",
    )
    parser.add_argument("--repository", default="Kauror/juristid")
    parser.add_argument(
        "--download-dir",
        type=pathlib.Path,
        default=pathlib.Path(tempfile.gettempdir()) / "juristid-shard-reports",
    )
    parser.add_argument(
        "--measured-at",
        default=None,
        help=(
            "the date of the newest run, ISO 8601. --latest reads it from GitHub; "
            "otherwise it defaults to today, which is right when you refresh from a run "
            "you have just downloaded, and is what scripts/ci/report_shard_health.py ages "
            "the table against."
        ),
    )
    parser.add_argument("--out", type=pathlib.Path, default=OUTPUT)
    arguments = parser.parse_args()

    measured_at = arguments.measured_at
    if arguments.latest:
        if arguments.reports or arguments.run:
            parser.error("give either --latest or report directories with --run, not both")
        chosen = latest_green_runs(arguments.latest, arguments.repository)
        run_ids = [run_id for run_id, _ in chosen]
        directories = [
            download_reports(run_id, arguments.repository, arguments.download_dir)
            for run_id in run_ids
        ]
        measured_at = measured_at or chosen[0][1]
    else:
        if not arguments.reports or len(arguments.reports) != len(arguments.run):
            parser.error("give one --run per report directory, in the same order")
        run_ids, directories = list(arguments.run), list(arguments.reports)
    measured_at = measured_at or datetime.date.today().isoformat()

    seconds, counts, setup = combine_runs([read_reports(directory) for directory in directories])
    print(f"combined {len(run_ids)} run(s): {', '.join(run_ids)}")
    for suite, value in sorted(setup.items()):
        print(
            f"session setup in {suite}/: {value:.1f}s per shard, taken off each shard's first file"
        )

    known = {path for path in seconds if (ROOT / path).exists()}
    vanished = sorted(set(seconds) - known)
    if vanished:
        print(f"dropping {len(vanished)} file(s) that no longer exist, for example {vanished[0]}")

    urls = [f"https://github.com/Kauror/juristid/actions/runs/{run_id}" for run_id in run_ids]
    document = {
        # One run's URL, or the list a median was taken over.
        "measured_from": urls[0] if len(urls) == 1 else urls,
        "measured_at": measured_at,
        "note": (
            "Balance input for ci_sharding.py, regenerated with "
            "scripts/ci/update_shard_timings.py. Nothing about which tests run "
            "depends on it; see docs/ci-architecture.md."
        ),
        # Paid once per shard, not by any file (ENG-139). Balance does not use
        # it — every shard pays it — but a prediction of how long a shard takes
        # does, and `report_shard_health.py` adds it.
        "session_setup_seconds": setup,
        "files": {
            path: {"seconds": round(seconds[path], 3), "tests": counts[path]}
            for path in sorted(known)
        },
    }

    arguments.out.parent.mkdir(parents=True, exist_ok=True)
    arguments.out.write_text(
        json.dumps(document, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
    )

    total = sum(seconds[path] for path in known)
    shown = (
        arguments.out.resolve().relative_to(ROOT)
        if arguments.out.resolve().is_relative_to(ROOT)
        else arguments.out
    )
    print(f"wrote {shown}: {len(known)} files, {total:.0f}s measured")
    return 0


if __name__ == "__main__":
    sys.exit(main())
