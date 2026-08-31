"""Deterministic partitioning of a test suite across independent CI runners.

The problem this solves is not "run fewer tests" — it is that the browser suite
and the PostgreSQL suite are each a single serial queue on a single runner, and
a queue does not get shorter by being asked nicely. Splitting the queue across
several runners is the only lever that does not cost coverage.

Two properties matter more than balance, and both are proved rather than
asserted in prose:

*Completeness.* Every collected test belongs to exactly one shard. The partition
below is over the files pytest itself collected, not over a list somebody
maintains — so a file added tomorrow is partitioned tomorrow, without anyone
remembering to edit CI. ``tests/test_ci_sharding.py`` proves the function is
complete and disjoint; ``scripts/ci/assert_shard_completeness.py`` proves it
again end to end, by collecting the real suites and comparing node ids.

*Reproducibility.* The assignment is a pure function of (file set, test counts,
shard count, timing table). No clock, no hash of the runner, no ``random``.
Re-running a shard runs the same tests, which is what makes a failure
investigable — and it is the reason this exists rather than ``pytest -n``,
whose worker assignment depends on which worker happens to be free.

Granularity is the *file*, never the individual test. The browser suite drives
one long-lived server whose database it mutates as it goes, and files were
written on the assumption that what precedes a test inside its own module is
whatever that module did. Splitting a file across two runners would change that
assumption silently, which is exactly the kind of speed-up this branch is not
allowed to buy.

Balance comes from ``ci/shard-timings.json``: measured seconds per file, taken
from a real CI run, alongside the number of tests that produced them. It is an
optimisation input and nothing more. A stale or missing entry costs balance and
never correctness, and the table is written to degrade rather than rot:

* a file that has grown since it was measured is weighted at its measured cost
  scaled by how many tests it holds now;
* a file nobody has measured — every file a parallel branch adds — is weighted
  at the median per-test cost of its own directory times its test count, which
  is a defensible estimate precisely because the PostgreSQL suite was measured
  to be uniform: 5328 tests, mean 0.116s, slowest single test 6.56s;
* a file whose test count is somehow unknown falls back to the median file.

Refreshing the table is one command, and ``docs/ci-architecture.md`` says which.
"""

from __future__ import annotations

import json
import statistics
from dataclasses import dataclass
from pathlib import Path

REPOSITORY_ROOT = Path(__file__).resolve().parent
TIMINGS_PATH = REPOSITORY_ROOT / "ci" / "shard-timings.json"

#: What an unmeasured file is assumed to cost when there is nothing to take a
#: median of. Only reachable when the timing table is empty, which is a
#: development state rather than a CI one.
FALLBACK_WEIGHT_SECONDS = 5.0

#: No file weighs nothing. Zero-weight files would all pile onto shard 1.
MINIMUM_WEIGHT_SECONDS = 0.01


@dataclass(frozen=True)
class Measurement:
    """One file's measured cost, and how many tests produced it.

    The test count is what lets a stale entry stay useful: a file that has
    doubled in size since it was measured is re-weighted from its own per-test
    rate rather than from a total that is now wrong.
    """

    seconds: float
    tests: int


def load_timings(path: Path | None = None) -> dict[str, Measurement]:
    """What a real CI run measured, keyed by repository-relative path.

    Missing file, empty table and unknown key are all ordinary; each costs
    balance and nothing else.
    """
    location = TIMINGS_PATH if path is None else path
    if not location.exists():
        return {}
    raw = json.loads(location.read_text(encoding="utf-8"))
    return {
        str(key): Measurement(seconds=float(value["seconds"]), tests=int(value["tests"]))
        for key, value in raw.get("files", {}).items()
    }


def median_file_seconds(timings: dict[str, Measurement]) -> float:
    """What a file with no measurement and no known test count is worth."""
    if not timings:
        return FALLBACK_WEIGHT_SECONDS
    return float(statistics.median(measurement.seconds for measurement in timings.values()))


def per_test_seconds(timings: dict[str, Measurement], directory: str) -> float:
    """What one test in this directory typically costs.

    Per directory, because a browser test and a service test differ by two
    orders of magnitude and a single blended rate would mis-weigh both. The
    median rather than the mean, so one pathological file does not set the
    estimate for every file written after it.
    """
    rates = [
        measurement.seconds / measurement.tests
        for path, measurement in timings.items()
        if path.startswith(f"{directory}/") and measurement.tests > 0
    ]
    if not rates:
        rates = [
            measurement.seconds / measurement.tests
            for measurement in timings.values()
            if measurement.tests > 0
        ]
    return float(statistics.median(rates)) if rates else FALLBACK_WEIGHT_SECONDS


def weight_of(path: str, test_count: int | None, timings: dict[str, Measurement]) -> float:
    """The predicted cost of running one test file."""
    measured = timings.get(path)
    if measured is not None:
        if test_count is None or measured.tests <= 0 or test_count == measured.tests:
            return max(measured.seconds, MINIMUM_WEIGHT_SECONDS)
        # Measured, but it has grown or shrunk since. Scaled rather than
        # discarded: a file's per-test cost is far more stable than its total.
        return max(measured.seconds * (test_count / measured.tests), MINIMUM_WEIGHT_SECONDS)

    if test_count is None:
        return max(median_file_seconds(timings), MINIMUM_WEIGHT_SECONDS)

    directory = path.split("/", 1)[0]
    return max(test_count * per_test_seconds(timings, directory), MINIMUM_WEIGHT_SECONDS)


def partition(
    files: list[str],
    shard_count: int,
    timings: dict[str, Measurement] | None = None,
    test_counts: dict[str, int] | None = None,
) -> list[list[str]]:
    """Split ``files`` into ``shard_count`` groups of comparable predicted cost.

    Longest-processing-time-first: sort by weight descending and hand each file
    to whichever shard is currently lightest. It is the standard greedy schedule
    for this problem and lands within a small constant of optimal, which is far
    more than good enough when the alternative is a hand-written list.

    Ties break on the path, so the result depends on nothing but the arguments.
    Returns exactly ``shard_count`` lists; a shard may legitimately be empty when
    there are fewer files than shards, and the caller decides whether that is a
    misconfiguration.
    """
    if shard_count < 1:
        raise ValueError(f"a suite cannot be split into {shard_count} shards")

    table = load_timings() if timings is None else timings
    counts = test_counts or {}
    weights = {path: weight_of(path, counts.get(path), table) for path in set(files)}

    ordered = sorted(weights, key=lambda path: (-weights[path], path))

    shards: list[list[str]] = [[] for _ in range(shard_count)]
    load = [0.0] * shard_count
    for path in ordered:
        lightest = min(range(shard_count), key=lambda index: (load[index], index))
        shards[lightest].append(path)
        load[lightest] += weights[path]

    # Alphabetical inside a shard, which is the order the whole suite runs in
    # today. Every shard is therefore an order-preserving subsequence of the
    # serial run that is green on main, so a shard can never reach a file
    # *earlier* than the serial suite reaches it. That is not a substitute for
    # running the shards, but it is why sharding this suite is a smaller change
    # than it looks (docs/ci-architecture.md).
    return [sorted(shard) for shard in shards]


def predicted_load(
    files: list[str],
    shard_count: int,
    timings: dict[str, Measurement] | None = None,
    test_counts: dict[str, int] | None = None,
) -> list[float]:
    """The seconds each shard is expected to take. For balance reporting."""
    table = load_timings() if timings is None else timings
    counts = test_counts or {}
    return [
        sum(weight_of(path, counts.get(path), table) for path in shard)
        for shard in partition(files, shard_count, table, counts)
    ]


def shard_for(
    files: list[str],
    shard_count: int,
    shard_index: int,
    timings: dict[str, Measurement] | None = None,
    test_counts: dict[str, int] | None = None,
) -> set[str]:
    """The files belonging to one 1-based shard."""
    if not 1 <= shard_index <= shard_count:
        raise ValueError(f"shard {shard_index} does not exist in a partition of {shard_count}")
    return set(partition(files, shard_count, timings, test_counts)[shard_index - 1])
