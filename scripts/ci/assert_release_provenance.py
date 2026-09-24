"""Is this commit an accepted revision of main, and did main's CI pass on it?

Run by `.github/workflows/release-image.yml` before anything is built:

    assert_release_provenance.py --target SHA --repository OWNER/REPO
        [--main-ref origin/main] [--workflow ci.yml] [--git-dir DIR] [--env-file "$GITHUB_ENV"]

It needs `GH_TOKEN` (the workflow's own token, with `actions: read`).

**Two facts, both required, and neither implies the other (ENG-015).**

1. *The commit is main's own.* It is on main's **first-parent** history — the
   merge commits main recorded, one per accepted pull request, and anything
   pushed to main directly. Ancestry is not enough and is the trap: the head of
   a merged pull request is an ancestor of main, has a tree identical to its
   merge commit and green checks of its own, and is not what main accepted.
   That is exactly what was built on 2026-09-09 (`82e5afbe4541`, the head of
   PR #158, second parent of `2bc5f534`). An older first-parent commit is
   accepted: it is a revision main really was, which is what a rebuild for
   rollback needs; the workflow's own `previous_sha` check refuses going
   backwards past what production runs.
2. *Main's CI passed on exactly this commit.* The latest run of `ci.yml` for a
   **push to main** with this **head SHA** has completed, concluded `success`,
   and every job in its latest attempt concluded `success`. A pull request's
   run is not main's run; a run for another SHA is not this commit's; a run
   still in progress has proved nothing yet; and a run where "the required
   jobs" passed while another was skipped or cancelled is not a green run.

Fails closed: anything it cannot establish — no run, an API error, a missing
ref — is a refusal, not a pass.

The two decisions are pure functions of plain data (`on_first_parent_history`,
`ci_verdict`), so `tests/test_release_provenance_gate.py` holds every answer
they can give, against a real synthetic repository and fixture API payloads.
Standard library only: it runs on the bare runner before `uv` exists.
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import urllib.error
import urllib.request
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

API = "https://api.github.com"
WORKFLOW_PATH_SUFFIX = ".github/workflows/"


@dataclass(frozen=True)
class Verdict:
    ok: bool
    detail: str


# ---------------------------------------------------------------------------
# 1. Main's own history
# ---------------------------------------------------------------------------


def first_parent_history(main_ref: str, git_dir: Path) -> list[str]:
    """Every commit on the first-parent chain of ``main_ref``, newest first."""
    result = subprocess.run(  # noqa: S603 - fixed git arguments, no shell
        ["git", "-C", str(git_dir), "rev-list", "--first-parent", main_ref],  # noqa: S607
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode != 0:
        raise RuntimeError(f"cannot read the history of {main_ref}: {result.stderr.strip()}")
    return [line for line in result.stdout.split() if line]


def on_first_parent_history(target: str, history: Sequence[str]) -> Verdict:
    if not history:
        return Verdict(False, "main's history is empty; nothing can be proved about it")
    if target not in history:
        return Verdict(
            False,
            f"{target} is not on main's first-parent history. A commit that is only an "
            "ancestor of main — a merged pull request's own head, for one — is not what main "
            "accepted; release the merge commit main recorded instead.",
        )
    behind = history.index(target)
    where = "main's current tip" if behind == 0 else f"{behind} accepted revision(s) behind main"
    return Verdict(True, f"{target} is on main's first-parent history ({where})")


# ---------------------------------------------------------------------------
# 2. Main's CI on exactly this commit
# ---------------------------------------------------------------------------


def main_push_runs(target: str, runs: Sequence[dict[str, Any]], workflow: str) -> list[dict]:
    """The runs that are main's CI for this commit, and nothing else.

    Filtered here as well as in the API query: the query's filters are a
    convenience of the endpoint, and this is the definition.
    """
    return [
        run
        for run in runs
        if run.get("head_sha") == target
        and run.get("event") == "push"
        and run.get("head_branch") == "main"
        and str(run.get("path", "")).endswith(WORKFLOW_PATH_SUFFIX + workflow)
    ]


def ci_verdict(
    target: str,
    runs: Sequence[dict[str, Any]],
    jobs_for: Callable[[dict[str, Any]], Sequence[dict[str, Any]]],
    workflow: str = "ci.yml",
) -> Verdict:
    candidates = main_push_runs(target, runs, workflow)
    if not candidates:
        other = sorted({f"{r.get('event')}@{r.get('head_branch')}" for r in runs})
        seen = f" (runs seen: {', '.join(other)})" if other else ""
        return Verdict(
            False,
            f"no {workflow} run for a push to main at {target}{seen}. A pull request's checks "
            "are not main's; the merge commit is tested when it lands on main.",
        )
    latest = max(candidates, key=lambda run: (run.get("run_number", 0), run.get("id", 0)))
    url = latest.get("html_url", f"run {latest.get('id')}")
    if latest.get("status") != "completed":
        return Verdict(
            False, f"main's CI for {target} has not finished ({latest.get('status')}): {url}"
        )
    if latest.get("conclusion") != "success":
        return Verdict(
            False, f"main's CI for {target} concluded {latest.get('conclusion')!r}: {url}"
        )
    jobs = list(jobs_for(latest))
    if not jobs:
        return Verdict(False, f"main's CI for {target} reports no jobs: {url}")
    not_green = sorted(
        f"{job.get('name')} ({job.get('conclusion') or job.get('status')})"
        for job in jobs
        if job.get("conclusion") != "success"
    )
    if not_green:
        return Verdict(
            False,
            f"main's CI for {target} was not green in every job: {', '.join(not_green)}: {url}",
        )
    return Verdict(True, f"main's CI passed on {target}, all {len(jobs)} jobs: {url}")


# ---------------------------------------------------------------------------
# The GitHub API, as little of it as this needs
# ---------------------------------------------------------------------------


def _get(url: str, token: str) -> dict[str, Any]:
    request = urllib.request.Request(  # noqa: S310 - a fixed https API base
        url,
        headers={
            "Accept": "application/vnd.github+json",
            "Authorization": f"Bearer {token}",
            "X-GitHub-Api-Version": "2022-11-28",
            "User-Agent": "juristid-release-provenance",
        },
    )
    with urllib.request.urlopen(request, timeout=30) as response:  # noqa: S310
        return json.load(response)


def fetch_runs(repository: str, workflow: str, target: str, token: str) -> list[dict]:
    runs: list[dict] = []
    for page in range(1, 11):
        body = _get(
            f"{API}/repos/{repository}/actions/workflows/{workflow}/runs"
            f"?head_sha={target}&event=push&branch=main&per_page=100&page={page}",
            token,
        )
        batch = body.get("workflow_runs", [])
        runs.extend(batch)
        if len(batch) < 100:
            break
    return runs


def fetch_jobs(repository: str, run: dict[str, Any], token: str) -> list[dict]:
    jobs: list[dict] = []
    for page in range(1, 11):
        body = _get(
            f"{API}/repos/{repository}/actions/runs/{run['id']}/jobs"
            f"?filter=latest&per_page=100&page={page}",
            token,
        )
        batch = body.get("jobs", [])
        jobs.extend(batch)
        if len(batch) < 100:
            break
    return jobs


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--target", required=True)
    parser.add_argument("--repository", required=True, help="OWNER/REPO")
    parser.add_argument("--main-ref", default="origin/main")
    parser.add_argument("--workflow", default="ci.yml")
    parser.add_argument("--git-dir", type=Path, default=Path.cwd())
    parser.add_argument("--env-file", type=Path, help="Append PROVENANCE=… here ($GITHUB_ENV).")
    args = parser.parse_args(argv)

    target = args.target.strip().lower()
    if len(target) != 40 or any(c not in "0123456789abcdef" for c in target):
        print(f"REFUSED: {args.target!r} is not a full 40-character commit id", file=sys.stderr)
        return 1

    verdicts: list[Verdict] = []
    try:
        verdicts.append(
            on_first_parent_history(target, first_parent_history(args.main_ref, args.git_dir))
        )
        token = os.environ.get("GH_TOKEN") or os.environ.get("GITHUB_TOKEN") or ""
        if not token:
            raise RuntimeError("no GH_TOKEN: main's CI result cannot be read, so it is not assumed")
        runs = fetch_runs(args.repository, args.workflow, target, token)
        verdicts.append(
            ci_verdict(
                target,
                runs,
                lambda run: fetch_jobs(args.repository, run, token),
                workflow=args.workflow,
            )
        )
    except (RuntimeError, OSError, urllib.error.URLError, ValueError, KeyError) as error:
        verdicts.append(Verdict(False, f"could not establish provenance: {error}"))

    for verdict in verdicts:
        print(("ok       " if verdict.ok else "REFUSED  ") + verdict.detail)
    ok = all(verdict.ok for verdict in verdicts)
    if args.env_file and ok:
        with args.env_file.open("a", encoding="utf-8") as env:
            env.write(f"PROVENANCE={verdicts[-1].detail}\n")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
