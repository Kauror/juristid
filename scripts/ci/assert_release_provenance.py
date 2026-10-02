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
2. *CI passed on exactly the bytes being released.* Either of two proofs:

   a. **Main's own run.** The latest run of `ci.yml` for a **push to main**
      with this **head SHA** has completed, concluded `success`, and every job
      in its latest attempt concluded `success`. A run for another SHA is not
      this commit's; a run where "the required jobs" passed while another was
      skipped or cancelled is not a green run.
   b. **The pull request's run, on the identical tree** (added 2026-10-02, so a
      release does not wait ~11 minutes for main to re-test what the pull
      request already tested). Tried only while main's own run is *absent or
      still running* — a main run that finished red always refuses. The commit
      must be a merge with exactly two parents; the latest `ci.yml` run for a
      **pull request** whose head is the **second parent** must be completed,
      `success`, green in every job; and the tree that run recorded as tested
      (its `tested-tree` artifact: `HEAD^{tree}` of its checkout, which for a
      pull request is GitHub's test merge) must equal this commit's tree. Equal
      trees are equal bytes, so this proves what (a) proves about the release.
      Branch protection's "up to date" rule is what makes the two coincide; the
      tree comparison is what makes relying on it safe.

Fails closed: anything it cannot establish — no run, an API error, a missing
ref — is a refusal, not a pass.

The two decisions are pure functions of plain data (`on_first_parent_history`,
`ci_verdict`), so `tests/test_release_provenance_gate.py` holds every answer
they can give, against a real synthetic repository and fixture API payloads.
Standard library only: it runs on the bare runner before `uv` exists.
"""

from __future__ import annotations

import argparse
import io
import json
import os
import subprocess
import sys
import urllib.error
import urllib.request
import zipfile
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


def main_run_state(target: str, runs: Sequence[dict[str, Any]], workflow: str = "ci.yml") -> str:
    """``absent``, ``running`` or ``finished`` — whether proof (b) may be tried at all."""
    candidates = main_push_runs(target, runs, workflow)
    if not candidates:
        return "absent"
    latest = max(candidates, key=lambda run: (run.get("run_number", 0), run.get("id", 0)))
    return "finished" if latest.get("status") == "completed" else "running"


def pull_request_runs(head: str, runs: Sequence[dict[str, Any]], workflow: str) -> list[dict]:
    """The `ci.yml` runs for a pull request whose head is ``head``, and nothing else."""
    return [
        run
        for run in runs
        if run.get("head_sha") == head
        and run.get("event") == "pull_request"
        and str(run.get("path", "")).endswith(WORKFLOW_PATH_SUFFIX + workflow)
    ]


def pull_request_verdict(
    target: str,
    target_tree: str,
    parents: Sequence[str],
    runs: Sequence[dict[str, Any]],
    jobs_for: Callable[[dict[str, Any]], Sequence[dict[str, Any]]],
    tested_tree_for: Callable[[dict[str, Any]], str | None],
    workflow: str = "ci.yml",
) -> Verdict:
    """Proof (b): a green pull-request run that tested exactly this commit's tree."""
    if len(parents) != 2:
        return Verdict(
            False,
            f"{target} is not a two-parent merge, so no pull request's run can stand in "
            "for main's own",
        )
    head = parents[1]
    candidates = pull_request_runs(head, runs, workflow)
    if not candidates:
        return Verdict(False, f"no {workflow} pull-request run for the merged head {head}")
    latest = max(candidates, key=lambda run: (run.get("run_number", 0), run.get("id", 0)))
    url = latest.get("html_url", f"run {latest.get('id')}")
    if latest.get("status") != "completed" or latest.get("conclusion") != "success":
        return Verdict(
            False,
            f"the pull request's CI for {head} is {latest.get('status')}/"
            f"{latest.get('conclusion')}, not a completed success: {url}",
        )
    jobs = list(jobs_for(latest))
    if not jobs:
        return Verdict(False, f"the pull request's CI for {head} reports no jobs: {url}")
    not_green = sorted(
        f"{job.get('name')} ({job.get('conclusion') or job.get('status')})"
        for job in jobs
        if job.get("conclusion") != "success"
    )
    if not_green:
        return Verdict(
            False,
            f"the pull request's CI for {head} was not green in every job: "
            f"{', '.join(not_green)}: {url}",
        )
    tested = tested_tree_for(latest)
    if not tested:
        return Verdict(
            False,
            f"the pull request's CI for {head} recorded no tested tree, so what it tested "
            f"is not known: {url}",
        )
    if tested != target_tree:
        return Verdict(
            False,
            f"the pull request's CI tested tree {tested}, but {target} has tree "
            f"{target_tree} — main moved after the run, or the merge differs: {url}",
        )
    return Verdict(
        True,
        f"the pull request's CI passed on {target}'s exact tree {target_tree}, all "
        f"{len(jobs)} jobs: {url}",
    )


def commit_parents_and_tree(target: str, git_dir: Path) -> tuple[list[str], str]:
    """The commit's parents, in order, and its tree."""
    result = subprocess.run(  # noqa: S603 - fixed git arguments, no shell
        ["git", "-C", str(git_dir), "log", "-1", "--format=%P%n%T", target],  # noqa: S607
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode != 0:
        raise RuntimeError(f"cannot read {target}: {result.stderr.strip()}")
    lines = [*result.stdout.splitlines(), "", ""]
    return lines[0].split(), lines[1].strip()


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


class _StripAuthOnRedirect(urllib.request.HTTPRedirectHandler):
    """An artifact download redirects to blob storage, which refuses a bearer token."""

    def redirect_request(self, req, fp, code, msg, headers, newurl):  # type: ignore[no-untyped-def]
        new = super().redirect_request(req, fp, code, msg, headers, newurl)
        if new is not None:
            new.remove_header("Authorization")
        return new


def _get_bytes(url: str, token: str) -> bytes:
    request = urllib.request.Request(  # noqa: S310 - a fixed https API base
        url,
        headers={
            "Authorization": f"Bearer {token}",
            "X-GitHub-Api-Version": "2022-11-28",
            "User-Agent": "juristid-release-provenance",
        },
    )
    opener = urllib.request.build_opener(_StripAuthOnRedirect)
    with opener.open(request, timeout=60) as response:
        return response.read()


def parse_tested_tree(text: str) -> str | None:
    """The ``tree <sha>`` line `ci.yml` wrote, or nothing."""
    for line in text.splitlines():
        key, _, value = line.partition(" ")
        if key == "tree" and len(value.strip()) == 40:
            return value.strip()
    return None


def fetch_tested_tree(repository: str, run: dict[str, Any], token: str) -> str | None:
    body = _get(
        f"{API}/repos/{repository}/actions/runs/{run['id']}/artifacts?name=tested-tree&per_page=10",
        token,
    )
    artifacts = [a for a in body.get("artifacts", []) if not a.get("expired")]
    if not artifacts:
        return None
    archive = _get_bytes(artifacts[0]["archive_download_url"], token)
    with zipfile.ZipFile(io.BytesIO(archive)) as bundle:
        return parse_tested_tree(bundle.read("tested-tree.txt").decode("utf-8"))


def fetch_runs(
    repository: str, workflow: str, target: str, token: str, event: str = "push"
) -> list[dict]:
    runs: list[dict] = []
    branch = "&branch=main" if event == "push" else ""
    for page in range(1, 11):
        body = _get(
            f"{API}/repos/{repository}/actions/workflows/{workflow}/runs"
            f"?head_sha={target}&event={event}{branch}&per_page=100&page={page}",
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
        main_proof = ci_verdict(
            target,
            runs,
            lambda run: fetch_jobs(args.repository, run, token),
            workflow=args.workflow,
        )
        if main_proof.ok or main_run_state(target, runs, args.workflow) == "finished":
            verdicts.append(main_proof)
        else:
            parents, tree = commit_parents_and_tree(target, args.git_dir)
            pr_runs = (
                fetch_runs(args.repository, args.workflow, parents[1], token, event="pull_request")
                if len(parents) == 2
                else []
            )
            pr_proof = pull_request_verdict(
                target,
                tree,
                parents,
                pr_runs,
                lambda run: fetch_jobs(args.repository, run, token),
                lambda run: fetch_tested_tree(args.repository, run, token),
                workflow=args.workflow,
            )
            verdicts.append(
                pr_proof
                if pr_proof.ok
                else Verdict(False, f"{main_proof.detail}; and {pr_proof.detail}")
            )
    except (
        RuntimeError,
        OSError,
        urllib.error.URLError,
        ValueError,
        KeyError,
        IndexError,
        zipfile.BadZipFile,
    ) as error:
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
