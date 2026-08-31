"""Prove that sharding the suites did not stop CI from running tests.

A faster CI that quietly collects less than it used to is worse than a slow
one, because nothing about it looks wrong. This is the check that would notice.

It does not reason about the partition. It runs the *same pytest commands the
workflow runs* — every shard of every sharded suite, plus the whole suite
unsharded — and compares the collected node ids as sets:

* the union of a suite's shards is exactly the unsharded suite;
* no test appears in two shards of the same suite;
* no shard is empty;
* the browser suite and the visual suite together are exactly ``e2e``, so the
  ``--ignore`` that separates them cannot leave a file in neither.

The shard counts come from ``.github/workflows/ci.yml`` rather than from a
constant here, so the thing being proved is what CI will actually do. A matrix
that grows a fifth runner while this script still believes in four is precisely
the failure this is for.

Collection only: no database, no browser, no server. Cheap enough to run in the
fast quality job, which is where it belongs — the proof that the slow jobs are
complete should not itself be on the critical path.
"""

from __future__ import annotations

import concurrent.futures
import pathlib
import subprocess
import sys

import yaml

ROOT = pathlib.Path(__file__).resolve().parents[2]
WORKFLOW = ROOT / ".github" / "workflows" / "ci.yml"

#: The pytest arguments that define each logical suite, mirroring the workflow.
#: Keyed by the workflow job whose matrix decides how many shards it has;
#: ``None`` means the suite is not sharded and must run whole.
SUITES: dict[str, tuple[str, list[str]]] = {
    "tests": ("PostgreSQL test suite", ["tests"]),
    "browser": ("Browser workflow", ["e2e", "--ignore=e2e/test_ui_regression.py"]),
}
VISUAL = ("Visual regression", ["e2e/test_ui_regression.py"])
WHOLE_E2E = ("The whole browser directory", ["e2e"])


def collect(arguments: list[str]) -> set[str]:
    """The node ids one pytest invocation would run."""
    # The arguments are the constants at the top of this file plus shard indexes
    # read from this repository's own workflow. Nothing here comes from outside.
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
        raise SystemExit(
            f"collection failed for `pytest {' '.join(arguments)}`:\n"
            f"{completed.stdout}\n{completed.stderr}"
        )
    return {
        line.strip()
        for line in completed.stdout.splitlines()
        if "::" in line and not line.startswith(("=", "-", "["))
    }


def shard_counts() -> dict[str, int]:
    """How many runners the workflow gives each sharded job.

    Read from the matrix itself. A job with no shard matrix is unsharded and
    reported as one.
    """
    workflow = yaml.safe_load(WORKFLOW.read_text(encoding="utf-8"))
    counts: dict[str, int] = {}
    for name in SUITES:
        job = workflow["jobs"].get(name)
        if job is None:
            raise SystemExit(
                f"the workflow no longer has a `{name}` job; this script is out of date"
            )
        matrix = job.get("strategy", {}).get("matrix", {})
        shards = matrix.get("shard")
        if shards is None:
            counts[name] = 1
            continue
        expected = list(range(1, len(shards) + 1))
        if list(shards) != expected:
            raise SystemExit(
                f"job `{name}` has shard indexes {list(shards)}; they must be exactly {expected}, "
                "or some slice of the suite runs twice and another not at all"
            )
        counts[name] = len(shards)
    return counts


def main() -> int:
    counts = shard_counts()
    problems: list[str] = []

    # Every collection this script needs, run at once. Collection imports the
    # whole application, and doing that seven times in series is the only slow
    # thing here.
    jobs: dict[str, list[str]] = {}
    for job, (_, arguments) in SUITES.items():
        jobs[f"{job}:full"] = arguments
        for index in range(1, counts[job] + 1):
            jobs[f"{job}:{index}"] = [
                *arguments,
                f"--shard-count={counts[job]}",
                f"--shard-index={index}",
            ]
    jobs["visual"] = VISUAL[1]
    jobs["e2e:whole"] = WHOLE_E2E[1]

    with concurrent.futures.ThreadPoolExecutor(max_workers=len(jobs)) as pool:
        collected = dict(zip(jobs, pool.map(collect, jobs.values()), strict=True))

    for job, (label, _) in SUITES.items():
        full = collected[f"{job}:full"]
        shards = {index: collected[f"{job}:{index}"] for index in range(1, counts[job] + 1)}

        if not full:
            problems.append(f"{label}: the unsharded suite collected nothing")

        union: set[str] = set()
        for index, node_ids in shards.items():
            if not node_ids:
                problems.append(f"{label}: shard {index} of {counts[job]} collected nothing")
            overlap = union & node_ids
            if overlap:
                problems.append(
                    f"{label}: shard {index} repeats {len(overlap)} test(s) "
                    f"another shard already runs, for example {sorted(overlap)[0]}"
                )
            union |= node_ids

        missing = full - union
        if missing:
            problems.append(
                f"{label}: {len(missing)} test(s) are in no shard at all, "
                f"for example {sorted(missing)[0]}"
            )
        extra = union - full
        if extra:
            problems.append(
                f"{label}: {len(extra)} test(s) run in a shard but not in the suite, "
                f"for example {sorted(extra)[0]}"
            )

        print(
            f"{label}: {len(full)} tests across {counts[job]} shard(s) "
            f"({', '.join(str(len(shards[i])) for i in sorted(shards))}) — complete and disjoint"
        )

    # The visual split is an `--ignore`, not a shard, and it is the other way a
    # test can end up in nothing: a file that is neither collected by the
    # browser command nor by the visual one.
    browser_full = collected["browser:full"]
    visual = collected["visual"]
    whole = collected["e2e:whole"]
    if not visual:
        problems.append("Visual regression: collected nothing")
    if browser_full & visual:
        problems.append("the browser suite and the visual suite overlap")
    if browser_full | visual != whole:
        stranded = whole - (browser_full | visual)
        problems.append(
            f"{len(stranded)} browser test(s) are run by neither job, "
            f"for example {sorted(stranded)[0]}"
            if stranded
            else "the browser and visual jobs together collect something `pytest e2e` does not"
        )
    print(
        f"Visual regression: {len(visual)} tests; "
        f"browser + visual = {len(whole)} = the whole e2e directory"
    )

    if problems:
        print("\nCI would not run the whole suite:", file=sys.stderr)
        for problem in problems:
            print(f"  - {problem}", file=sys.stderr)
        return 1

    print("\nEvery test in both suites runs exactly once.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
