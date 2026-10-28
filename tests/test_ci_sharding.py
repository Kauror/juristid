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
import ci_slices
import ci_workflow_selection

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

    ``--shard-count`` is a number in the pytest command, ``runner`` is a list
    in the matrix and ``--runners`` is a number given to the script that hosts
    each runner's shards, and nothing but this ties them together. A matrix of
    [1, 2, 3] against four runners' worth of shards leaves a quarter of the
    suite unrun, and every job passes.
    """
    workflow = yaml.safe_load(WORKFLOW.read_text(encoding="utf-8"))
    layout = ci_workflow_selection.topology(WORKFLOW)

    for job_name in SHARDED_JOBS:
        shape = layout[job_name]
        runners = workflow["jobs"][job_name]["strategy"]["matrix"]["runner"]
        assert runners == list(range(1, shape.runners + 1))
        assert shape.shard_count == shape.runners * shape.per_runner

        # Every shard index is hosted by exactly one runner.
        hosted = [
            shard
            for runner in runners
            for shard in ci_slices.slices_for(runner, shape.runners, shape.shard_count)
        ]
        assert sorted(hosted) == list(range(1, shape.shard_count + 1))

        steps = json.dumps(workflow["jobs"][job_name]["steps"])
        assert f"--shard-count={shape.shard_count}" in steps
        assert "--shard-index={shard}" in steps, (
            f"job `{job_name}` does not give each hosted shard its own shard index"
        )


@pytest.mark.parametrize(("gate", "job"), [("tests-gate", "tests"), ("browser-gate", "browser")])
def test_each_gate_requires_a_report_from_every_shard(gate: str, job: str) -> None:
    """The runtime half: a runner reports its shards together, so a shard that
    never ran would otherwise leave nothing missing that an upload notices.
    Each gate counts the reports against the layout's shard count."""
    workflow = yaml.safe_load(WORKFLOW.read_text(encoding="utf-8"))
    shape = ci_workflow_selection.topology(WORKFLOW)[job]
    (step,) = [
        step for step in workflow["jobs"][gate]["steps"] if "SHARD_COUNT" in step.get("env", {})
    ]
    assert int(step["env"]["SHARD_COUNT"]) == shape.shard_count
    assert 'seq 1 "$SHARD_COUNT"' in step["run"]
    assert f"junit-{job}-$shard.xml" in step["run"]


#: The aggregators and the report, which wait for their dependencies' *results*.
AGGREGATORS = {"tests-gate", "browser-gate", "shard-balance"}

#: The FULL tier's condition (docs/adr/0193; tests/test_ci_tiers.py holds every
#: job to it). Every job below is written as this, or as `always()` /
#: `!cancelled()` and this.
FULL = "github.event_name!='pull_request'||!github.event.pull_request.draft"

#: Jobs that wait for the preflight's *result* and may be skipped by it:
#: none is a required check, and each stands behind an aggregator that
#: refuses a skipped dependency.
BEHIND_THE_PREFLIGHT = {"postgres-safety", "tests", "browser"}

#: The visual job also runs in FAST on a draft that touches the UI, so it says
#: `!cancelled()` and asks for the preflight's success itself in FULL
#: (tests/test_ci_tiers.py holds its whole condition).
VISUAL = "visual"


def test_a_job_that_waits_only_for_a_slot_still_runs_when_its_predecessor_fails() -> None:
    """Chaining is how two short jobs share one of the account's twenty slots.

    A job with `needs:` is skipped when what it needs fails — and a skipped
    required check blocks nothing. So every non-aggregating job that waits
    for another must say `!cancelled()`: it runs and reports under its own
    name whatever its predecessor did. And the aggregators must say
    `always()`, so they run and refuse. The one exception is the preflight's
    dependants, which are *meant* to be skipped when it fails — that is the
    saving — and are each aggregated by a gate that then refuses.
    """
    workflow = yaml.safe_load(WORKFLOW.read_text(encoding="utf-8"))
    for name, job in workflow["jobs"].items():
        if not job.get("needs"):
            continue
        condition = str(job.get("if", "")).replace(" ", "")
        if name in AGGREGATORS:
            assert condition == f"${{{{always()&&({FULL})}}}}", (
                f"`{name}` must run whatever it aggregates did"
            )
        elif name in BEHIND_THE_PREFLIGHT:
            assert job["needs"] == ["preflight"]
            assert condition == f"${{{{{FULL}}}}}"
            assert any(
                name in workflow["jobs"][gate]["needs"] for gate in AGGREGATORS - {"shard-balance"}
            )
        elif name == VISUAL:
            assert job["needs"] == ["preflight", "changes"]
            assert condition.startswith("${{!cancelled()&&")
            assert f"(({FULL})&&needs.preflight.result=='success')" in condition
            assert name in workflow["jobs"]["browser-gate"]["needs"]
        else:
            assert condition == f"${{{{!cancelled()&&({FULL})}}}}", (
                f"`{name}` waits for {job['needs']} but would be skipped if it failed"
            )


def test_a_workflow_whose_runners_and_shards_disagree_is_refused(tmp_path) -> None:
    """Seven runners and fifteen shards cannot be shared; neither can a stale count."""
    original = WORKFLOW.read_text(encoding="utf-8")
    shape = ci_workflow_selection.topology(WORKFLOW)["tests"]
    mutations = [
        original.replace(
            f"--shard-count={shape.shard_count} --shard-index={{shard}}",
            f"--shard-count={shape.shard_count + 1} --shard-index={{shard}}",
            1,
        ),
        original.replace(
            f"--runner ${{{{ matrix.runner }}}} --runners {shape.runners} --",
            f"--runner ${{{{ matrix.runner }}}} --runners {shape.runners + 1} --",
            1,
        ),
    ]
    for number, text in enumerate(mutations):
        assert text != original, "the mutation did not apply; this test proves nothing"
        path = tmp_path / f"ci-{number}.yml"
        path.write_text(text, encoding="utf-8")
        with pytest.raises(ci_workflow_selection.InconsistentTopology):
            ci_workflow_selection.topology(path)
