"""Prove main's new commit was already tested green, or have CI test it now.

Run by `.github/workflows/main-proof.yml` on every push to main:

    assert_main_proved.py --target SHA --repository OWNER/REPO
        [--workflow ci.yml] [--git-dir DIR] [--wait-minutes 20] [--dispatch]

It needs `GH_TOKEN` (the workflow's token: `actions: read`, and `actions:
write` for `--dispatch`).

**Why main no longer re-runs CI on every merge.** Branch protection requires a
pull request to be up to date with main and green before it merges, and the
pull request's run tests GitHub's test merge — the very tree the merge button
then commits. Main's own push run re-tested those identical bytes, held all
twenty of the account's concurrent jobs for about eleven minutes, and made every
pull request updated in that window wait behind it (2026-10-11: rounds of 21–25
minutes instead of 11).

The proof is the one the release gate already accepts
(`scripts/ci/assert_release_provenance.py`, proof b, imported from there rather
than copied): the commit is a two-parent merge, the latest `ci.yml` run for a
pull request whose head is the second parent completed green in **every** job,
and the tree that run recorded as tested is byte-for-byte this commit's tree.
A pull request run that is still finishing its last jobs is waited for.

**Anything short of that proof runs the whole of CI on main** (`--dispatch`): a
squash or direct push, a merge whose run is red, missing or tested another
tree, an API failure. Nothing is assumed. The release gate never trusts this
job's word either: it re-derives the same proof itself, or reads the
dispatched run.

Standard library only, like the gate it imports: it runs on the bare runner.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
import urllib.error
import urllib.request
from collections.abc import Callable, Sequence
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import assert_release_provenance as gate

#: How long a pull request's run may still be finishing when its merge lands.
#: The required checks finish first; an informational job can trail them.
DEFAULT_WAIT_MINUTES = 20
POLL_SECONDS = 30


def latest_pull_request_run(runs: Sequence[dict]) -> dict | None:
    if not runs:
        return None
    return max(runs, key=lambda run: (run.get("run_number", 0), run.get("id", 0)))


def prove(
    target: str,
    tree: str,
    parents: Sequence[str],
    fetch_pull_request_runs: Callable[[str], list[dict]],
    jobs_for: Callable[[dict], Sequence[dict]],
    tested_tree_for: Callable[[dict], str | None],
    workflow: str,
    wait_seconds: float,
    sleep: Callable[[float], None] = time.sleep,
    clock: Callable[[], float] = time.monotonic,
) -> gate.Verdict:
    """Proof (b) for ``target``, waiting while the pull request's run is unfinished."""
    if len(parents) != 2:
        return gate.pull_request_verdict(
            target, tree, parents, [], jobs_for, tested_tree_for, workflow
        )
    deadline = clock() + wait_seconds
    while True:
        runs = gate.pull_request_runs(parents[1], fetch_pull_request_runs(parents[1]), workflow)
        latest = latest_pull_request_run(runs)
        unfinished = latest is not None and latest.get("status") != "completed"
        if not unfinished or clock() >= deadline:
            return gate.pull_request_verdict(
                target, tree, parents, runs, jobs_for, tested_tree_for, workflow
            )
        status, url = latest.get("status"), latest.get("html_url")
        print(f"the pull request's run is still {status}; waiting: {url}")
        sleep(POLL_SECONDS)


def dispatch(repository: str, workflow: str, token: str) -> None:
    """Start the whole of `workflow` on main."""
    request = urllib.request.Request(  # noqa: S310 - a fixed https API base
        f"{gate.API}/repos/{repository}/actions/workflows/{workflow}/dispatches",
        data=json.dumps({"ref": "main"}).encode("utf-8"),
        method="POST",
        headers={
            "Accept": "application/vnd.github+json",
            "Authorization": f"Bearer {token}",
            "X-GitHub-Api-Version": "2022-11-28",
            "User-Agent": "juristid-main-proof",
            "Content-Type": "application/json",
        },
    )
    with urllib.request.urlopen(request, timeout=30) as response:  # noqa: S310
        if response.status != 204:
            raise RuntimeError(f"dispatching {workflow} answered {response.status}")


def summary(text: str) -> None:
    """Into the job summary when there is one, so the proof is one click away."""
    path = os.environ.get("GITHUB_STEP_SUMMARY")
    if path:
        with open(path, "a", encoding="utf-8") as handle:
            handle.write(text + "\n")


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--target", required=True)
    parser.add_argument("--repository", required=True, help="OWNER/REPO")
    parser.add_argument("--workflow", default="ci.yml")
    parser.add_argument("--git-dir", type=Path, default=Path.cwd())
    parser.add_argument("--wait-minutes", type=float, default=DEFAULT_WAIT_MINUTES)
    parser.add_argument(
        "--dispatch",
        action="store_true",
        help="when the proof fails, run the whole workflow on main instead of failing",
    )
    args = parser.parse_args(argv)

    target = args.target.strip().lower()
    if len(target) != 40 or any(c not in "0123456789abcdef" for c in target):
        print(f"REFUSED: {args.target!r} is not a full 40-character commit id", file=sys.stderr)
        return 1

    token = os.environ.get("GH_TOKEN") or os.environ.get("GITHUB_TOKEN") or ""
    if not token:
        # Nothing can be read and nothing can be started: a red job, so the
        # commit is visibly unproved rather than quietly so.
        print("REFUSED: no GH_TOKEN, so no run can be read and none started", file=sys.stderr)
        return 1
    try:
        parents, tree = gate.commit_parents_and_tree(target, args.git_dir)
        verdict = prove(
            target,
            tree,
            parents,
            lambda head: gate.fetch_runs(
                args.repository, args.workflow, head, token, event="pull_request"
            ),
            lambda run: gate.fetch_jobs(args.repository, run, token),
            lambda run: gate.fetch_tested_tree(args.repository, run, token),
            args.workflow,
            args.wait_minutes * 60,
        )
    except (
        RuntimeError,
        OSError,
        urllib.error.URLError,
        ValueError,
        KeyError,
        IndexError,
    ) as error:
        verdict = gate.Verdict(False, f"could not establish the proof: {error}")

    if verdict.ok:
        print(f"ok       {verdict.detail}")
        summary(f"**Main's tree was already tested green.** {verdict.detail}")
        return 0

    print(f"NOT PROVED  {verdict.detail}")
    if not args.dispatch:
        return 1
    try:
        # A dispatched run tests main's tip *now*. If main has already moved
        # on, that run would test another commit — whose own push has its own
        # proof — and nothing here would be proved by it.
        tip = gate._get(f"{gate.API}/repos/{args.repository}/git/ref/heads/main", token)
        tip_sha = tip.get("object", {}).get("sha", "")
        if tip_sha != target:
            print(
                f"main has moved on to {tip_sha}; that commit's own proof covers it, and "
                f"{target} stays unproved (the release gate will refuse it)"
            )
            summary(f"**Not proved, and superseded by {tip_sha}.** ({verdict.detail})")
            return 0
        dispatch(args.repository, args.workflow, token)
    except (RuntimeError, OSError, urllib.error.URLError) as error:
        print(f"and the full run on main could not be started: {error}")
        return 1
    message = (
        f"no pull request run proves {target}'s tree, so the whole of {args.workflow} "
        "was started on main; that run is this commit's proof"
    )
    print(message)
    summary(f"**{message}.** ({verdict.detail})")
    return 0


if __name__ == "__main__":
    sys.exit(main())
