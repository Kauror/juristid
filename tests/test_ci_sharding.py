"""The properties CI's speed now rests on.

Splitting a suite across runners buys wall-clock time and risks the one thing
a test suite exists to provide: the guarantee that everything ran. These are
the assertions that keep the trade honest. They are pure — no database, no
browser — so they run inside every shard, and a partition that stopped being
complete fails in all of them at once rather than nowhere.

The end-to-end counterpart is ``scripts/ci/assert_shard_completeness.py``, which
proves the same thing by collecting the real suites. This file proves it about
the function, for every shard count and every file set, including the ones no
workflow currently asks for.
"""

from __future__ import annotations

import json
import pathlib

import pytest
import yaml

import ci_sharding

ROOT = pathlib.Path(ci_sharding.REPOSITORY_ROOT)
WORKFLOW = ROOT / ".github" / "workflows" / "ci.yml"

#: The suites the workflow shards, and the job whose matrix decides how many.
SHARDED_JOBS = ("tests", "browser")


def repository_test_files() -> list[str]:
    """Every test file in the repository, as the partition sees them."""
    return sorted(
        path.relative_to(ROOT).as_posix()
        for directory in ("tests", "e2e")
        for path in (ROOT / directory).glob("test_*.py")
    )


@pytest.mark.parametrize("shard_count", [1, 2, 3, 4, 5, 8, 13])
def test_every_test_file_lands_in_exactly_one_shard(shard_count: int) -> None:
    """Completeness and disjointness, over the repository's real file list.

    The property that makes sharding safe at all: not "roughly everything runs"
    but "each file runs, once". Checked for shard counts nobody uses as well,
    because the number in the workflow is a thing people change.
    """
    files = repository_test_files()
    assert files, "no test files were discovered, so this proves nothing"

    shards = ci_sharding.partition(files, shard_count)

    assert len(shards) == shard_count
    flattened = [path for shard in shards for path in shard]
    assert sorted(flattened) == files, "a file is in no shard, or in the wrong suite"
    assert len(flattened) == len(set(flattened)), "a file is in more than one shard"


@pytest.mark.parametrize("shard_count", [2, 3, 4, 5])
def test_the_assignment_does_not_depend_on_the_order_it_was_given(shard_count: int) -> None:
    """Reproducibility. A shard that re-ran a different set is not a re-run."""
    files = repository_test_files()
    forwards = ci_sharding.partition(files, shard_count)
    backwards = ci_sharding.partition(list(reversed(files)), shard_count)
    assert forwards == backwards
    assert forwards == ci_sharding.partition(files, shard_count)


def test_a_file_nobody_has_measured_still_runs() -> None:
    """The property that makes this safe for other people's pull requests.

    A test file added by a parallel branch has no timing entry. It must still be
    partitioned — weighted at the median, and belonging to exactly one shard —
    because the alternative is a test that is committed, collected locally, and
    never executed by CI again.
    """
    files = [*repository_test_files(), "tests/test_a_branch_added_this_today.py"]
    shards = ci_sharding.partition(files, 4)
    holders = [
        index
        for index, shard in enumerate(shards)
        if "tests/test_a_branch_added_this_today.py" in shard
    ]
    assert len(holders) == 1, "an unmeasured file was dropped or duplicated"


def test_the_timing_table_describes_files_that_exist() -> None:
    """Balance metadata that outlived its files is a slow shard nobody expects.

    A stale entry does not break correctness — the partition ignores it — but it
    silently reserves capacity for a file that is gone, and the balance the
    numbers in `docs/ci-architecture.md` claim stops being true. Cheap to fix,
    so it is a red build rather than a comment.
    """
    timings = ci_sharding.load_timings()
    if not timings:
        pytest.skip("no timing table is committed")
    stale = sorted(path for path in timings if not (ROOT / path).exists())
    assert not stale, f"ci/shard-timings.json still weighs deleted files: {stale}"


def test_the_committed_timings_actually_balance_the_shards() -> None:
    """A guard on the metadata, not on the machine.

    Timings drift, and drift costs a little balance — that is expected and
    harmless. What is not harmless is a table so wrong that one runner carries
    the suite while three finish in a minute, which is the state in which
    sharding has stopped paying for its complexity. Generous on purpose: this
    should fire when the metadata is broken, never when it is merely old.
    """
    timings = ci_sharding.load_timings()
    if not timings:
        pytest.skip("no timing table is committed")

    for shard_count in (2, 3, 4):
        for prefix in ("tests/", "e2e/"):
            files = [path for path in repository_test_files() if path.startswith(prefix)]
            counts = {path: timings[path].tests for path in files if path in timings}
            loads = ci_sharding.predicted_load(files, shard_count, timings, counts)
            assert min(loads) > 0
            assert max(loads) / min(loads) < 2.0, (
                f"{prefix} across {shard_count} shards is predicted to run {loads}; "
                "regenerate ci/shard-timings.json (docs/ci-architecture.md)"
            )


def test_the_timing_table_is_readable_and_says_where_it_came_from() -> None:
    """Metadata whose provenance is unrecorded is metadata nobody dares refresh."""
    path = ROOT / "ci" / "shard-timings.json"
    if not path.exists():
        pytest.skip("no timing table is committed")
    document = json.loads(path.read_text(encoding="utf-8"))
    assert document["measured_from"], "the run these numbers came from is not recorded"
    assert isinstance(document["files"], dict) and document["files"]


@pytest.mark.parametrize(("shard_index", "shard_count"), [(0, 4), (5, 4), (-1, 2), (2, 1)])
def test_a_shard_that_does_not_exist_is_refused(shard_index: int, shard_count: int) -> None:
    """Off-by-one in a workflow matrix must be loud.

    Silently running shard 4 of 4 when the matrix meant 5 is how a quarter of a
    suite stops being executed while every job stays green.
    """
    with pytest.raises(ValueError):
        ci_sharding.shard_for(repository_test_files(), shard_count, shard_index)


def test_the_workflow_matrix_covers_every_shard_exactly_once() -> None:
    """The other half of the same off-by-one, on the CI side of the line.

    ``--shard-count`` is a number in the workflow and ``shard`` is a list in the
    workflow, and nothing but this ties them together. A matrix of [1, 2, 3]
    against a count of 4 leaves a quarter of the suite unrun, and every job
    passes.
    """
    workflow = yaml.safe_load(WORKFLOW.read_text(encoding="utf-8"))

    for job_name in SHARDED_JOBS:
        job = workflow["jobs"][job_name]
        matrix = job.get("strategy", {}).get("matrix", {})
        shards = matrix.get("shard")
        if shards is None:
            continue

        assert list(shards) == list(range(1, len(shards) + 1)), (
            f"job `{job_name}` has shard indexes {list(shards)}, which is not 1..{len(shards)}"
        )

        steps = json.dumps(job["steps"])
        assert f"--shard-count={len(shards)}" in steps, (
            f"job `{job_name}` runs {len(shards)} runners but does not pass "
            f"--shard-count={len(shards)} to pytest"
        )
        assert "--shard-index=${{ matrix.shard }}" in steps, (
            f"job `{job_name}` does not give each runner its own shard index"
        )
