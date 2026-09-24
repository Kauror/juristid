"""Only main's own revisions, green on main, become release images (ENG-015).

`scripts/ci/assert_release_provenance.py` is the decision and
`.github/workflows/release-image.yml` is where it runs; the host preflight
repeats the half that needs no credentials.

The history half is proved against a real synthetic repository, because the
trap is a property of git and not of any list: a merged pull request's own head
is an ancestor of main — `merge-base --is-ancestor` says yes — with the merge
commit's tree and green checks of its own, and main never accepted it. That is
what was built on 2026-09-09. The CI half is proved against fixture API
payloads covering every way a run can fail to be *main's* run, *this commit's*
run, *finished*, or *green in every job*.
"""

from __future__ import annotations

import importlib.util
import os
import pathlib
import shutil
import stat
import subprocess
import sys
import urllib.error
from typing import Any

import pytest
import yaml

ROOT = pathlib.Path(__file__).resolve().parent.parent
SCRIPT = ROOT / "scripts" / "ci" / "assert_release_provenance.py"
WORKFLOW = ROOT / ".github" / "workflows" / "release-image.yml"
PREFLIGHT = ROOT / "scripts" / "deploy" / "juristid-deploy-preflight.sh"
COMPOSE = ROOT / "deploy" / "unraid-main" / "compose.yml"
GIT = shutil.which("git") or "git"
BASH = shutil.which("bash") or "bash"
#: What the fake API expects as a bearer token. A fixture, not a secret.
FIXTURE_TOKEN = "fixture-token"  # noqa: S105


def _load():
    spec = importlib.util.spec_from_file_location("assert_release_provenance", SCRIPT)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


gate = _load()


# ---------------------------------------------------------------------------
# A repository with main's shape: merge commits, a direct push, a merged pull
# request's own head, and a branch nobody merged.
# ---------------------------------------------------------------------------


def git(repo: pathlib.Path, *args: str) -> str:
    return subprocess.run(  # noqa: S603 - fixed git arguments in a temporary directory
        [GIT, "-C", str(repo), "-c", "user.name=Test", "-c", "user.email=test@koda.test", *args],
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()


def commit(repo: pathlib.Path, name: str) -> str:
    (repo / f"{name}.txt").write_text(name, encoding="utf-8")
    git(repo, "add", f"{name}.txt")
    git(repo, "commit", "-q", "-m", name)
    return git(repo, "rev-parse", "HEAD")


@pytest.fixture(scope="module")
def history(tmp_path_factory) -> dict[str, Any]:
    """`origin/main` with two accepted merges, and the commits it did not accept."""
    repo = tmp_path_factory.mktemp("repo")
    git(repo, "init", "-q", "-b", "main")
    base = commit(repo, "base")

    git(repo, "checkout", "-q", "-b", "feature/one")
    commit(repo, "one-a")
    pr_head = commit(repo, "one-b")
    git(repo, "checkout", "-q", "main")
    git(repo, "merge", "-q", "--no-ff", "-m", "Merge pull request #1", "feature/one")
    older_merge = git(repo, "rev-parse", "HEAD")

    direct = commit(repo, "direct")

    git(repo, "checkout", "-q", "-b", "feature/two")
    commit(repo, "two")
    git(repo, "checkout", "-q", "main")
    git(repo, "merge", "-q", "--no-ff", "-m", "Merge pull request #2", "feature/two")
    tip = git(repo, "rev-parse", "HEAD")

    git(repo, "checkout", "-q", "-b", "feature/unmerged")
    unmerged = commit(repo, "unmerged")
    git(repo, "checkout", "-q", "main")
    git(repo, "update-ref", "refs/remotes/origin/main", tip)

    return {
        "repo": repo,
        "base": base,
        "pr_head": pr_head,
        "older_merge": older_merge,
        "direct": direct,
        "tip": tip,
        "unmerged": unmerged,
    }


def verdict_for(history: dict[str, Any], name: str):
    chain = gate.first_parent_history("origin/main", history["repo"])
    return gate.on_first_parent_history(history[name], chain)


def test_the_exact_current_main_is_accepted(history) -> None:
    verdict = verdict_for(history, "tip")
    assert verdict.ok
    assert "current tip" in verdict.detail


@pytest.mark.parametrize("name", ["older_merge", "direct", "base"])
def test_an_older_accepted_revision_of_main_is_accepted(history, name: str) -> None:
    """A revision main really was — what a rollback rebuild needs.

    Going back past what production runs is refused separately, by the
    workflow's `previous_sha` ancestry check.
    """
    verdict = verdict_for(history, name)
    assert verdict.ok
    assert "behind main" in verdict.detail


def test_a_merged_pull_requests_own_head_is_refused_although_it_is_an_ancestor(history) -> None:
    """The 2026-09-09 case, and why ancestry alone is not the check."""
    ancestry = subprocess.run(  # noqa: S603
        [
            GIT,
            "-C",
            str(history["repo"]),
            "merge-base",
            "--is-ancestor",
            history["pr_head"],
            "origin/main",
        ],
        check=False,
    )
    assert ancestry.returncode == 0, "the trap: a plain ancestry check would pass this commit"

    verdict = verdict_for(history, "pr_head")
    assert not verdict.ok
    assert "first-parent" in verdict.detail


def test_a_branch_nobody_merged_is_refused(history) -> None:
    assert not verdict_for(history, "unmerged").ok


def test_an_unreadable_main_is_an_error_not_a_pass(tmp_path) -> None:
    git(tmp_path, "init", "-q")
    with pytest.raises(RuntimeError):
        gate.first_parent_history("origin/main", tmp_path)


# ---------------------------------------------------------------------------
# Main's CI, for exactly this commit
# ---------------------------------------------------------------------------

SHA = "a" * 40
OTHER = "b" * 40


def run(**overrides: Any) -> dict[str, Any]:
    fields = {
        "id": 1,
        "run_number": 10,
        "head_sha": SHA,
        "event": "push",
        "head_branch": "main",
        "path": ".github/workflows/ci.yml",
        "status": "completed",
        "conclusion": "success",
        "html_url": "https://github.invalid/run/1",
    }
    return fields | overrides


GREEN_JOBS = [
    {
        "name": "Format, lint, types and system checks",
        "status": "completed",
        "conclusion": "success",
    },
    {"name": "PostgreSQL 18 test suite", "status": "completed", "conclusion": "success"},
    {
        "name": "Browser workflow (Playwright on PostgreSQL 18)",
        "status": "completed",
        "conclusion": "success",
    },
]


def green(_run: dict) -> list[dict]:
    return GREEN_JOBS


def test_main_ci_that_passed_in_every_job_is_accepted() -> None:
    verdict = gate.ci_verdict(SHA, [run()], green)
    assert verdict.ok
    assert "all 3 jobs" in verdict.detail


@pytest.mark.parametrize("conclusion", ["failure", "cancelled", "timed_out", "neutral", None])
def test_main_ci_that_did_not_pass_is_refused(conclusion: Any) -> None:
    assert not gate.ci_verdict(SHA, [run(conclusion=conclusion)], green).ok


@pytest.mark.parametrize("status", ["in_progress", "queued", "waiting", "requested"])
def test_main_ci_that_is_still_running_is_refused(status: str) -> None:
    verdict = gate.ci_verdict(SHA, [run(status=status, conclusion=None)], green)
    assert not verdict.ok
    assert "has not finished" in verdict.detail


def test_a_pull_requests_ci_is_not_mains() -> None:
    """Green checks on the PR head were what the 2026-09-09 build leaned on."""
    verdict = gate.ci_verdict(SHA, [run(event="pull_request", head_branch="feature/one")], green)
    assert not verdict.ok
    assert "pull_request@feature/one" in verdict.detail


def test_ci_for_another_commit_is_not_this_ones() -> None:
    assert not gate.ci_verdict(SHA, [run(head_sha=OTHER)], green).ok


def test_ci_from_a_branch_other_than_main_is_not_mains() -> None:
    assert not gate.ci_verdict(SHA, [run(head_branch="release/x")], green).ok


def test_another_workflow_is_not_ci() -> None:
    assert not gate.ci_verdict(SHA, [run(path=".github/workflows/release-image.yml")], green).ok


def test_no_run_at_all_is_refused() -> None:
    assert not gate.ci_verdict(SHA, [], green).ok


@pytest.mark.parametrize("conclusion", ["skipped", "cancelled", "failure", None])
def test_some_jobs_green_is_not_green(conclusion: Any) -> None:
    """A run whose summary is green while one job did not pass is refused."""
    jobs = [
        *GREEN_JOBS,
        {"name": "Backup and restore rehearsal", "status": "completed", "conclusion": conclusion},
    ]
    verdict = gate.ci_verdict(SHA, [run()], lambda _run: jobs)
    assert not verdict.ok
    assert "Backup and restore rehearsal" in verdict.detail


def test_a_run_with_no_jobs_proves_nothing() -> None:
    assert not gate.ci_verdict(SHA, [run()], lambda _run: []).ok


def test_the_latest_run_decides() -> None:
    older_green = run(id=1, run_number=10)
    newer_red = run(id=2, run_number=11, conclusion="failure")
    assert not gate.ci_verdict(SHA, [older_green, newer_red], green).ok
    assert gate.ci_verdict(
        SHA, [run(id=1, run_number=10, conclusion="failure"), run(id=2, run_number=11)], green
    ).ok


# ---------------------------------------------------------------------------
# The command, end to end, against the repository and a fake API
# ---------------------------------------------------------------------------


@pytest.fixture
def api(monkeypatch):
    """GitHub, as fixtures: runs per SHA and jobs per run id."""
    state: dict[str, Any] = {"runs": {}, "jobs": {}, "error": None}

    def fake_get(url: str, token: str) -> dict[str, Any]:
        assert token == FIXTURE_TOKEN
        if state["error"]:
            raise state["error"]
        if "/jobs?" in url:
            run_id = int(url.split("/runs/")[1].split("/")[0])
            return {"jobs": state["jobs"].get(run_id, [])}
        sha = url.split("head_sha=")[1].split("&")[0]
        return {"workflow_runs": state["runs"].get(sha, [])}

    monkeypatch.setattr(gate, "_get", fake_get)
    monkeypatch.setenv("GH_TOKEN", FIXTURE_TOKEN)
    return state


def invoke(history, target: str, tmp_path, *extra: str) -> tuple[int, str]:
    env_file = tmp_path / "github_env"
    rc = gate.main(
        [
            "--target",
            target,
            "--repository",
            "Kauror/juristid",
            "--git-dir",
            str(history["repo"]),
            "--env-file",
            str(env_file),
            *extra,
        ]
    )
    return rc, env_file.read_text(encoding="utf-8") if env_file.exists() else ""


def test_main_tip_with_green_main_ci_is_released(history, api, tmp_path) -> None:
    api["runs"][history["tip"]] = [run(id=7, head_sha=history["tip"])]
    api["jobs"][7] = GREEN_JOBS

    rc, env = invoke(history, history["tip"], tmp_path)

    assert rc == 0
    assert env.startswith("PROVENANCE=")


def test_the_2026_09_09_build_is_refused_even_with_green_checks(history, api, tmp_path) -> None:
    """The PR head: an ancestor of main, green in its own CI — and still refused."""
    api["runs"][history["pr_head"]] = [
        run(id=8, head_sha=history["pr_head"], event="pull_request", head_branch="feature/one")
    ]
    api["jobs"][8] = GREEN_JOBS

    rc, env = invoke(history, history["pr_head"], tmp_path)

    assert rc == 1
    assert env == ""


def test_main_tip_whose_main_ci_failed_is_refused(history, api, tmp_path) -> None:
    api["runs"][history["tip"]] = [run(id=9, head_sha=history["tip"], conclusion="failure")]
    assert invoke(history, history["tip"], tmp_path)[0] == 1


def test_main_tip_whose_main_ci_is_still_running_is_refused(history, api, tmp_path) -> None:
    api["runs"][history["tip"]] = [
        run(id=10, head_sha=history["tip"], status="in_progress", conclusion=None)
    ]
    assert invoke(history, history["tip"], tmp_path)[0] == 1


def test_an_unmerged_commit_is_refused_whatever_its_ci_says(history, api, tmp_path) -> None:
    api["runs"][history["unmerged"]] = [run(id=11, head_sha=history["unmerged"])]
    api["jobs"][11] = GREEN_JOBS
    assert invoke(history, history["unmerged"], tmp_path)[0] == 1


def test_an_api_failure_refuses_rather_than_passes(history, api, tmp_path) -> None:
    api["error"] = urllib.error.URLError("unreachable")
    assert invoke(history, history["tip"], tmp_path)[0] == 1


def test_without_a_token_nothing_is_assumed(history, api, tmp_path, monkeypatch) -> None:
    monkeypatch.delenv("GH_TOKEN")
    monkeypatch.delenv("GITHUB_TOKEN", raising=False)
    api["runs"][history["tip"]] = [run(id=12, head_sha=history["tip"])]
    api["jobs"][12] = GREEN_JOBS
    assert invoke(history, history["tip"], tmp_path)[0] == 1


@pytest.mark.parametrize("target", ["main", "abc1234", "HEAD", "g" * 40])
def test_anything_but_a_full_commit_id_is_refused(history, api, tmp_path, target: str) -> None:
    assert invoke(history, target, tmp_path)[0] == 1


# ---------------------------------------------------------------------------
# The workflow runs it, first, from main's own copy
# ---------------------------------------------------------------------------


@pytest.fixture(scope="module")
def workflow() -> dict:
    return yaml.safe_load(WORKFLOW.read_text(encoding="utf-8"))


def _steps(workflow: dict) -> list[dict]:
    return workflow["jobs"]["build"]["steps"]


def _index(workflow: dict, predicate) -> int:
    return next(index for index, step in enumerate(_steps(workflow)) if predicate(step))


def test_the_workflow_may_read_ci_results_and_nothing_more(workflow) -> None:
    assert workflow["permissions"] == {"contents": "read", "actions": "read"}


def test_provenance_is_proved_before_the_release_note_and_the_build(workflow) -> None:
    gate_step = _index(workflow, lambda step: "assert_release_provenance.py" in step.get("run", ""))
    note_step = _index(workflow, lambda step: "assert_release_note.py" in step.get("run", ""))
    build_step = _index(workflow, lambda step: step.get("name") == "Build")
    assert gate_step < note_step < build_step


def test_the_gate_is_mains_own_copy_not_the_judged_commits(workflow) -> None:
    """The commit being judged must not be able to bring its own judge."""
    step = _steps(workflow)[
        _index(workflow, lambda step: "assert_release_provenance.py" in step.get("run", ""))
    ]
    script = step["run"]
    assert 'git show "$WORKFLOW_SHA:scripts/ci/assert_release_provenance.py"' in script
    assert 'python3 "$RUNNER_TEMP/assert_release_provenance.py"' in script
    assert "python3 scripts/ci/assert_release_provenance.py" not in script
    assert '"refs/heads/main"' in script
    assert step["env"]["WORKFLOW_SHA"] == "${{ github.sha }}"
    assert step["env"]["TARGET"] == "${{ inputs.sha }}"
    assert "--main-ref origin/main" in script


def test_the_manifest_records_the_provenance(workflow) -> None:
    step = _steps(workflow)[_index(workflow, lambda step: step.get("name") == "Save and digest")]
    assert "provenance:" in step["run"]


# ---------------------------------------------------------------------------
# The host preflight: the half that needs no credentials
# ---------------------------------------------------------------------------


@pytest.fixture(scope="module")
def deployment_checkout(history, tmp_path_factory) -> pathlib.Path:
    """A bare origin holding main and both branches, and a checkout of it."""
    base = tmp_path_factory.mktemp("deploy")
    origin = base / "origin.git"
    subprocess.run([GIT, "clone", "-q", "--bare", str(history["repo"]), str(origin)], check=True)  # noqa: S603
    checkout = base / "checkout"
    subprocess.run([GIT, "clone", "-q", str(origin), str(checkout)], check=True)  # noqa: S603
    return checkout


def preflight(
    checkout: pathlib.Path, target: str, tmp_path: pathlib.Path
) -> subprocess.CompletedProcess:
    """The real preflight, with a `docker` that answers and does nothing."""
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir(exist_ok=True)
    docker = bin_dir / "docker"
    docker.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
    docker.chmod(docker.stat().st_mode | stat.S_IEXEC)
    return subprocess.run(  # noqa: S603
        [
            BASH,
            str(PREFLIGHT),
            "--repo",
            str(checkout),
            "--target",
            target,
            "--compose-file",
            str(COMPOSE),
        ],
        capture_output=True,
        text=True,
        check=False,
        env={**os.environ, "PATH": f"{bin_dir}{os.pathsep}{os.environ['PATH']}"},
    )


def test_the_preflight_accepts_a_revision_of_main(history, deployment_checkout, tmp_path) -> None:
    result = preflight(deployment_checkout, history["older_merge"], tmp_path)
    assert "the target is one of main's own revisions" in result.stdout
    assert "not on origin/main's first-parent history" not in result.stderr


@pytest.mark.parametrize("name", ["pr_head", "unmerged"])
def test_the_preflight_refuses_what_main_did_not_accept(
    history, deployment_checkout, tmp_path, name: str
) -> None:
    result = preflight(deployment_checkout, history[name], tmp_path)
    assert "the target commit exists in this checkout" in result.stdout
    assert "not on origin/main's first-parent history" in result.stderr
    assert result.returncode != 0
