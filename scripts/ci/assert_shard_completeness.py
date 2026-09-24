"""Prove that sharding the suites did not stop CI from running tests.

A faster CI that quietly collects less than it used to is worse than a slow
one, because nothing about it looks wrong. This is the check that would notice.

It does not reason about the partition. It runs the *same pytest commands the
workflow runs* — every shard of every sharded suite, plus the whole suite
unsharded — and compares the collected node ids as sets. "The same commands"
is now literally true: the selecting arguments are read from each job's own
`run:` line by `ci_workflow_selection.py`, the one definition this script and
`report_shard_health.py` share. It used to keep a hand-copied list that a
workflow edit could leave behind (ENG-110).

* the union of a suite's shards is exactly the unsharded suite;
* no test appears in two shards of the same suite;
* no shard is empty;
* the browser suite and the visual suite together are exactly ``e2e``, so the
  ``--ignore`` that separates them cannot leave a file in neither;
* the PostgreSQL suite is exactly what bare ``pytest`` collects — so an
  ``--ignore``, ``-k``, ``-m``, ``--deselect`` or path added to the workflow's
  command is a missing-collection failure here, not a smaller suite that is
  "complete" by its own new definition.

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
from collections.abc import Callable

import yaml

ROOT = pathlib.Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

import ci_workflow_selection  # noqa: E402  (after sys.path, so this runs from anywhere)

WORKFLOW = ROOT / ".github" / "workflows" / "ci.yml"

Collector = Callable[[list[str]], set[str]]


def collect(arguments: list[str]) -> set[str]:
    """The node ids one pytest invocation would run."""
    # The arguments are read from this repository's own workflow by a parser
    # that refuses anything it does not recognise. Nothing here comes from outside.
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


def shard_counts(workflow_path: pathlib.Path, suites: dict) -> dict[str, int]:
    """How many runners the workflow gives each job, checked against its command.

    The matrix and the `--shard-count` in the command are two statements of the
    same fact; a disagreement means some shard index runs a partition computed
    for a different number of runners.
    """
    workflow = yaml.safe_load(workflow_path.read_text(encoding="utf-8"))
    counts: dict[str, int] = {}
    for name, suite in suites.items():
        job = workflow["jobs"].get(name)
        if job is None:
            raise SystemExit(
                f"the workflow no longer has a `{name}` job; this script is out of date"
            )
        shards = job.get("strategy", {}).get("matrix", {}).get("shard")
        if shards is None:
            counts[name] = 1
        else:
            expected = list(range(1, len(shards) + 1))
            if list(shards) != expected:
                raise SystemExit(
                    f"job `{name}` has shard indexes {list(shards)}; they must be exactly "
                    f"{expected}, or some slice of the suite runs twice and another not at all"
                )
            counts[name] = len(shards)
        if suite.shard_count != counts[name]:
            raise SystemExit(
                f"job `{name}` runs {counts[name]} shard(s) but its command says "
                f"--shard-count={suite.shard_count}"
            )
    return counts


def check(
    workflow_path: pathlib.Path = WORKFLOW, collector: Collector = collect
) -> tuple[list[str], list[str]]:
    """(problems, report lines) for the workflow at ``workflow_path``."""
    try:
        suites = ci_workflow_selection.suites(workflow_path)
    except ci_workflow_selection.UnknownSelection as error:
        return [f"the workflow's test selection cannot be read: {error}"], []
    counts = shard_counts(workflow_path, suites)
    problems: list[str] = []
    report: list[str] = []

    # Every collection this needs, run at once. Collection imports the whole
    # application, and doing it ten times in series is the only slow thing here.
    jobs: dict[str, list[str]] = {}
    for name, suite in suites.items():
        jobs[f"{name}:full"] = list(suite.selection)
        if counts[name] > 1:
            for index in range(1, counts[name] + 1):
                jobs[f"{name}:{index}"] = [
                    *suite.selection,
                    f"--shard-count={counts[name]}",
                    f"--shard-index={index}",
                ]
    for universe, (_, arguments) in ci_workflow_selection.UNIVERSES.items():
        jobs[f"universe:{universe}"] = list(arguments)

    with concurrent.futures.ThreadPoolExecutor(max_workers=len(jobs)) as pool:
        collected = dict(zip(jobs, pool.map(collector, jobs.values()), strict=True))

    for name, suite in suites.items():
        full = collected[f"{name}:full"]
        if not full:
            problems.append(f"{suite.label}: the unsharded suite collected nothing")
        if counts[name] == 1:
            report.append(f"{suite.label}: {len(full)} tests, unsharded")
            continue
        shards = {index: collected[f"{name}:{index}"] for index in range(1, counts[name] + 1)}
        union: set[str] = set()
        for index, node_ids in shards.items():
            if not node_ids:
                problems.append(f"{suite.label}: shard {index} of {counts[name]} collected nothing")
            overlap = union & node_ids
            if overlap:
                problems.append(
                    f"{suite.label}: shard {index} repeats {len(overlap)} test(s) "
                    f"another shard already runs, for example {sorted(overlap)[0]}"
                )
            union |= node_ids
        missing = full - union
        if missing:
            problems.append(
                f"{suite.label}: {len(missing)} test(s) are in no shard at all, "
                f"for example {sorted(missing)[0]}"
            )
        extra = union - full
        if extra:
            problems.append(
                f"{suite.label}: {len(extra)} test(s) run in a shard but not in the suite, "
                f"for example {sorted(extra)[0]}"
            )
        report.append(
            f"{suite.label}: {len(full)} tests across {counts[name]} shard(s) "
            f"({', '.join(str(len(shards[i])) for i in sorted(shards))}) — complete and disjoint"
        )

    # The universes: what the jobs select between them must be everything, so
    # a narrowed command fails here instead of redefining "the whole suite".
    for universe, (members, arguments) in ci_workflow_selection.UNIVERSES.items():
        everything = collected[f"universe:{universe}"]
        command = " ".join(["pytest", *arguments])
        selected = [collected[f"{member}:full"] for member in members]
        for position, first in enumerate(selected):
            for second_position in range(position + 1, len(selected)):
                if first & selected[second_position]:
                    problems.append(
                        f"`{members[position]}` and `{members[second_position]}` run the same tests"
                    )
        chosen = set().union(*selected)
        left_out = everything - chosen
        if left_out:
            problems.append(
                f"{len(left_out)} test(s) of `{command}` are selected by no "
                f"CI job ({', '.join(members)}), for example {sorted(left_out)[0]} — the "
                "workflow's command narrows the suite"
            )
        beyond = chosen - everything
        if beyond:
            problems.append(
                f"{len(beyond)} test(s) selected by {', '.join(members)} are outside "
                f"`{command}`, for example {sorted(beyond)[0]}"
            )
        report.append(
            f"{' + '.join(members)} = {len(chosen)} of the {len(everything)} tests "
            f"`{command}` collects"
        )
    return problems, report


def main() -> int:
    problems, report = check()
    for line in report:
        print(line)
    if problems:
        print("\nCI would not run the whole suite:", file=sys.stderr)
        for problem in problems:
            print(f"  - {problem}", file=sys.stderr)
        return 1
    print("\nEvery test in both suites runs exactly once.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
