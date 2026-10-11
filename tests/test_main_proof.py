"""Main's push is proved by its pull request's run, or CI runs on main (main-proof.yml).

Main no longer re-runs `ci.yml` on every push: the pull request's run already
tested the exact tree a merge commits, and re-testing it held all twenty of the
account's jobs for eleven minutes. `scripts/ci/assert_main_proved.py` is the
replacement, and the property these hold it to is the one that made the old
push run worth having — **no commit on main is left without a green run on its
exact tree.** Either the pull request's run proves it (the release gate's own
proof b, imported rather than copied), or the whole of CI is started on main.
Every failure to prove leads to the second, never to a pass.
"""

from __future__ import annotations

import importlib.util
import pathlib
import sys
from typing import Any

import pytest
import yaml

ROOT = pathlib.Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "ci" / "assert_main_proved.py"
WORKFLOW = ROOT / ".github" / "workflows" / "main-proof.yml"
CI = ROOT / ".github" / "workflows" / "ci.yml"


def _load():
    sys.path.insert(0, str(SCRIPT.parent))
    spec = importlib.util.spec_from_file_location("assert_main_proved", SCRIPT)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


proof = _load()
gate = proof.gate

TARGET = "a" * 40
BASE = "b" * 40
HEAD = "c" * 40
TREE = "d" * 40
OTHER_TREE = "e" * 40

JOBS = [{"name": "PostgreSQL 18 test suite", "status": "completed", "conclusion": "success"}]


def pr_run(**overrides: Any) -> dict[str, Any]:
    fields = {
        "id": 11,
        "run_number": 5,
        "head_sha": HEAD,
        "event": "pull_request",
        "head_branch": "feature/x",
        "path": ".github/workflows/ci.yml",
        "status": "completed",
        "conclusion": "success",
        "html_url": "https://github.invalid/run/11",
    }
    return fields | overrides


def decide(runs_sequence: list[list[dict]], *, tested: str | None = TREE, wait: float = 600):
    """Run `prove` against a sequence of API answers, one per poll."""
    answers = iter(runs_sequence)
    last: list[dict] = []
    slept: list[float] = []
    now = [0.0]

    def fetch(_head: str) -> list[dict]:
        nonlocal last
        last = next(answers, last)
        return last

    def sleep(seconds: float) -> None:
        slept.append(seconds)
        now[0] += seconds

    verdict = proof.prove(
        TARGET,
        TREE,
        [BASE, HEAD],
        fetch,
        lambda _run: JOBS,
        lambda _run: tested,
        "ci.yml",
        wait,
        sleep=sleep,
        clock=lambda: now[0],
    )
    return verdict, slept


def test_a_green_pull_request_run_on_the_identical_tree_proves_the_merge() -> None:
    verdict, slept = decide([[pr_run()]])
    assert verdict.ok
    assert slept == []


def test_a_run_still_finishing_is_waited_for() -> None:
    running = pr_run(status="in_progress", conclusion=None)
    verdict, slept = decide([[running], [running], [pr_run()]])
    assert verdict.ok
    assert len(slept) == 2


def test_a_run_that_never_finishes_is_not_proof() -> None:
    running = pr_run(status="in_progress", conclusion=None)
    verdict, _ = decide([[running]], wait=90)
    assert not verdict.ok


@pytest.mark.parametrize(
    ("runs", "tested"),
    [
        ([pr_run(conclusion="failure")], TREE),
        ([], TREE),
        ([pr_run()], OTHER_TREE),
        ([pr_run()], None),
        ([pr_run(event="push", head_branch="main")], TREE),
    ],
    ids=["red", "absent", "another-tree", "no-tree-recorded", "not-a-pull-request"],
)
def test_anything_short_of_the_proof_is_not_proof(runs: list[dict], tested: str | None) -> None:
    verdict, _ = decide([runs], tested=tested)
    assert not verdict.ok


def test_a_commit_that_is_not_a_two_parent_merge_is_not_proved() -> None:
    verdict = proof.prove(
        TARGET,
        TREE,
        [BASE],
        lambda _head: [pr_run()],
        lambda _run: JOBS,
        lambda _run: TREE,
        "ci.yml",
        0,
    )
    assert not verdict.ok


# -- main(): prove, or start CI on main --------------------------------------------


@pytest.fixture
def harness(monkeypatch, tmp_path):
    state: dict[str, Any] = {"verdict": gate.Verdict(True, "proved"), "tip": TARGET}
    dispatched: list[tuple[str, str]] = []
    monkeypatch.setenv("GH_TOKEN", "fixture-token")
    monkeypatch.setenv("GITHUB_STEP_SUMMARY", str(tmp_path / "summary.md"))
    monkeypatch.setattr(
        gate, "commit_parents_and_tree", lambda target, git_dir: ([BASE, HEAD], TREE)
    )
    monkeypatch.setattr(proof, "prove", lambda *args, **kwargs: state["verdict"])
    monkeypatch.setattr(gate, "_get", lambda url, token: {"object": {"sha": state["tip"]}})

    def fake_dispatch(repository: str, workflow: str, token: str) -> None:
        if state.get("dispatch_error"):
            raise state["dispatch_error"]
        dispatched.append((repository, workflow))

    monkeypatch.setattr(proof, "dispatch", fake_dispatch)
    state["dispatched"] = dispatched
    return state


def run_main(*extra: str) -> int:
    return proof.main(["--target", TARGET, "--repository", "Kauror/juristid", *extra])


def test_a_proved_merge_starts_nothing(harness) -> None:
    assert run_main("--dispatch") == 0
    assert harness["dispatched"] == []


def test_an_unproved_merge_starts_the_whole_of_ci_on_main(harness) -> None:
    harness["verdict"] = gate.Verdict(False, "no run")
    assert run_main("--dispatch") == 0
    assert harness["dispatched"] == [("Kauror/juristid", "ci.yml")]


def test_an_unproved_merge_without_dispatch_fails(harness) -> None:
    harness["verdict"] = gate.Verdict(False, "no run")
    assert run_main() == 1


def test_a_dispatch_that_fails_is_a_red_job_not_a_quiet_one(harness) -> None:
    harness["verdict"] = gate.Verdict(False, "no run")
    harness["dispatch_error"] = OSError("network down")
    assert run_main("--dispatch") == 1


def test_main_having_moved_on_starts_nothing_for_the_old_commit(harness) -> None:
    """A dispatched run tests main's tip; for an older commit it would test another."""
    harness["verdict"] = gate.Verdict(False, "no run")
    harness["tip"] = "f" * 40
    assert run_main("--dispatch") == 0
    assert harness["dispatched"] == []


def test_without_a_token_nothing_is_proved_and_ci_is_not_skipped(harness, monkeypatch) -> None:
    monkeypatch.delenv("GH_TOKEN")
    monkeypatch.delenv("GITHUB_TOKEN", raising=False)
    harness["verdict"] = gate.Verdict(True, "would be proved")
    assert run_main("--dispatch") == 1


# -- the workflows ----------------------------------------------------------------------


def test_main_proof_runs_on_every_push_to_main_and_may_start_ci() -> None:
    workflow = yaml.safe_load(WORKFLOW.read_text(encoding="utf-8"))
    triggers = workflow.get("on") or workflow.get(True)
    assert triggers == {"push": {"branches": ["main"]}}
    assert workflow["permissions"] == {"contents": "read", "actions": "write"}
    (job,) = workflow["jobs"].values()
    script = next(
        step["run"] for step in job["steps"] if "assert_main_proved.py" in step.get("run", "")
    )
    assert "--dispatch" in script
    assert '"$TARGET"' in script and job["steps"][-1]["env"]["TARGET"] == "${{ github.sha }}"


def test_ci_runs_for_pull_requests_and_can_be_started_on_main() -> None:
    """No push trigger (main-proof.yml covers main), and dispatch for the fallback."""
    workflow = yaml.safe_load(CI.read_text(encoding="utf-8"))
    triggers = workflow.get("on") or workflow.get(True)
    assert set(triggers) == {"pull_request", "workflow_dispatch"}
