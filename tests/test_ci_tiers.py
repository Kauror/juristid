"""FAST for a draft, FULL for anything that could merge or release (docs/adr/0193).

`.github/workflows/ci.yml` decides its tier from one fact of the event — is
this a pull request that is a draft? — and every job's `if:` and every
required gate's `name:` is written in terms of it. These tests read the real
workflow and evaluate those expressions for each event a pull request goes
through, so what they prove is what GitHub will do with the file:

1. a draft's creation and every push to it run only the FAST jobs;
2. marking it ready runs FULL;
3. a push to a ready pull request runs FULL again, and the earlier FULL result
   does not carry over to the new commit;
4. returning it to draft starts nothing, and its next push is FAST;
5. a run started by hand or by main-proof — on main or anywhere — is FULL;
6. a FAST run's evidence can satisfy neither main-proof nor the release gate;
7. a failed or skipped prerequisite makes a required gate fail, never pass;
8. the existing completeness, isolation and security invariants still hold.

The expressions are evaluated by a deliberately small translator: it accepts
exactly the operators the workflow uses and refuses anything else, so a new
construct is a failing test rather than a silently misread condition.
"""

from __future__ import annotations

import importlib.util
import pathlib
import re
import shutil
import subprocess
import sys
from typing import Any

import pytest
import yaml

import ci_focused_selection
import ci_sharding

ROOT = pathlib.Path(__file__).resolve().parent.parent
WORKFLOWS = ROOT / ".github" / "workflows"
CI = WORKFLOWS / "ci.yml"
BASH = shutil.which("bash")

FAST = "github.event_name == 'pull_request' && github.event.pull_request.draft"
FULL = "github.event_name != 'pull_request' || !github.event.pull_request.draft"


def _load_gate():
    path = ROOT / "scripts" / "ci" / "assert_release_provenance.py"
    spec = importlib.util.spec_from_file_location("assert_release_provenance", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


gate = _load_gate()


@pytest.fixture(scope="module")
def workflow() -> dict[str, Any]:
    return yaml.safe_load(CI.read_text(encoding="utf-8"))


def triggers(workflow: dict[str, Any]) -> dict[str, Any]:
    # PyYAML reads the bare key `on` as the boolean True.
    return workflow.get("on", workflow.get(True))


# ---------------------------------------------------------------------------
# A small, strict evaluator for the workflow's expressions
# ---------------------------------------------------------------------------

_TOKEN = re.compile(
    r"\s*(?:(?P<string>'[^']*')|(?P<op>&&|\|\||==|!=|!|\(|\))"
    r"|(?P<call>always\(\)|cancelled\(\)|success\(\)|failure\(\))"
    r"|(?P<name>[A-Za-z_][A-Za-z0-9_.-]*))"
)


class UnknownExpression(Exception):
    pass


def evaluate(expression: str, context: dict[str, Any], run_state: str = "running") -> Any:
    """Evaluate `${{ ... }}` (or a bare condition) the way Actions would, for these cases."""
    text = expression.strip()
    if text.startswith("${{") and text.endswith("}}"):
        text = text[3:-2]
    python: list[str] = []
    position = 0
    while position < len(text):
        match = _TOKEN.match(text, position)
        if not match or match.end() == position:
            if text[position:].strip() == "":
                break
            raise UnknownExpression(f"cannot read {text[position:]!r} in {expression!r}")
        position = match.end()
        if match.group("string") is not None:
            python.append(repr(match.group("string")[1:-1]))
        elif match.group("op"):
            python.append(
                {"&&": " and ", "||": " or ", "!": " not ", "==": "==", "!=": "!="}.get(
                    match.group("op"), match.group("op")
                )
            )
        elif match.group("call"):
            call = match.group("call")
            python.append(
                repr(
                    {
                        "always()": True,
                        "cancelled()": run_state == "cancelled",
                        "success()": run_state == "running",
                        "failure()": run_state == "failed",
                    }[call]
                )
            )
        else:
            name = match.group("name")
            if name not in context:
                raise UnknownExpression(f"unknown name {name!r} in {expression!r}")
            python.append(f"_ctx[{name!r}]")
    return eval("".join(python), {"__builtins__": {}}, {"_ctx": context})  # noqa: S307


def event(name: str, *, draft: bool | None = None) -> dict[str, Any]:
    return {
        "github.event_name": name,
        "github.event.pull_request.draft": draft,
    }


DRAFT_PUSH = event("pull_request", draft=True)
READY = event("pull_request", draft=False)
DISPATCH = event("workflow_dispatch")


def job_name(job: dict[str, Any], context: dict[str, Any]) -> str:
    name = str(job["name"])
    if "matrix." in name:
        return name
    return str(evaluate(name, context)) if name.startswith("${{") else name


def results(
    workflow: dict[str, Any],
    context: dict[str, Any],
    *,
    ui: bool = False,
    failed: frozenset[str] = frozenset(),
) -> dict[str, str]:
    """Every job's result for this event, the way Actions decides it.

    A job whose condition has no status function carries an implicit
    `success()`: it is skipped unless every job it needs succeeded. A job that
    says `always()` or `!cancelled()` is evaluated whatever they did, with
    their results in `needs.<job>.result`. ``ui`` is what the `changes` job
    reports when it runs; ``failed`` lists jobs that run and fail.
    """
    jobs = workflow["jobs"]
    result: dict[str, str] = {}
    for key, job in jobs.items():  # a job's needs come before it in this file
        needs = job.get("needs", [])
        scope = dict(context)
        for need in needs:
            scope[f"needs.{need}.result"] = result[need]
        ran_changes = result.get("changes") == "success"
        scope["needs.changes.outputs.ui"] = ("true" if ui else "false") if ran_changes else ""
        condition = str(job.get("if", ""))
        status_aware = "always()" in condition or "cancelled()" in condition
        if not status_aware and any(result[need] != "success" for need in needs):
            result[key] = "skipped"
        elif condition and not evaluate(condition, scope):
            result[key] = "skipped"
        else:
            result[key] = "failure" if key in failed else "success"
    return result


def running(
    workflow: dict[str, Any],
    context: dict[str, Any],
    *,
    ui: bool = False,
    failed: frozenset[str] = frozenset(),
) -> set[str]:
    """The jobs that run (pass or fail) for this event."""
    return {
        key
        for key, outcome in results(workflow, context, ui=ui, failed=failed).items()
        if outcome != "skipped"
    }


def simulated_jobs(
    workflow: dict[str, Any], context: dict[str, Any], *, ui: bool = False
) -> list[dict[str, Any]]:
    """The jobs API's view of a green run of this event: names as GitHub shows them."""
    ran = running(workflow, context, ui=ui)
    return [
        {
            "name": job_name(job, context),
            "status": "completed",
            "conclusion": "success" if key in ran else "skipped",
        }
        for key, job in workflow["jobs"].items()
    ]


FAST_JOBS = {"quality", "focused", "changes"}
#: The FAST tier on a draft that touches templates, static files or e2e/.
FAST_UI_JOBS = FAST_JOBS | {"visual"}
#: The jobs that only ever run in FAST.
FAST_ONLY = {"focused", "changes"}

#: The visual job's condition: FULL after a green preflight, or FAST on a UI draft.
VISUAL = (
    f"!cancelled() && ((({FULL}) && needs.preflight.result == 'success') || "
    f"(({FAST}) && needs.changes.outputs.ui == 'true'))"
)


def test_the_evaluator_refuses_what_it_cannot_read() -> None:
    with pytest.raises(UnknownExpression):
        evaluate("${{ contains(github.event.pull_request.labels, 'x') }}", DRAFT_PUSH)
    with pytest.raises(UnknownExpression):
        evaluate("${{ github.head_ref == 'main' }}", DRAFT_PUSH)


def test_every_job_condition_is_the_fast_or_the_full_condition(workflow) -> None:
    """One definition of each tier, written out identically everywhere."""
    for key, job in workflow["jobs"].items():
        condition = str(job.get("if", "")).replace("${{", "").replace("}}", "").strip()
        if key == "quality":
            assert condition == "", "the quality job runs in both tiers, unconditionally"
            continue
        if key == "visual":
            assert condition == VISUAL, f"the visual job's condition changed: {condition!r}"
            continue
        assert condition in {
            FAST,
            FULL,
            f"always() && ({FULL})",
            f"!cancelled() && ({FULL})",
        }, f"`{key}` has a condition outside the two tiers: {condition!r}"


# ---------------------------------------------------------------------------
# 1–5: which tier each event of a pull request's life runs
# ---------------------------------------------------------------------------


def test_the_triggers_are_the_pull_request_events_that_change_what_could_merge(workflow) -> None:
    on = triggers(workflow)
    assert set(on) == {"pull_request", "workflow_dispatch"}
    assert on["pull_request"]["types"] == [
        "opened",
        "synchronize",
        "reopened",
        "ready_for_review",
    ]


def test_1_a_draft_and_every_push_to_it_run_only_fast(workflow) -> None:
    """`opened` and `synchronize` on a draft carry draft=true: quality + focused."""
    assert running(workflow, DRAFT_PUSH) == FAST_JOBS


def test_1_a_draft_that_touches_the_ui_also_runs_the_visual_job(workflow) -> None:
    """Rendering diffs and baseline candidates without a FULL round — and nothing more."""
    assert running(workflow, DRAFT_PUSH, ui=True) == FAST_UI_JOBS


@pytest.mark.parametrize("ui", [False, True])
def test_full_is_the_same_whatever_the_draft_touched(workflow, ui) -> None:
    """`changes` is FAST's alone: in FULL it is skipped and decides nothing."""
    assert running(workflow, READY, ui=ui) == set(workflow["jobs"]) - FAST_ONLY


def test_2_marking_ready_runs_full(workflow) -> None:
    """`ready_for_review` carries draft=false: every job but the FAST-only ones."""
    assert running(workflow, READY) == set(workflow["jobs"]) - FAST_ONLY


def test_3_a_push_to_a_ready_pull_request_runs_full_again(workflow) -> None:
    """`synchronize` on a ready pull request is the READY context once more."""
    assert running(workflow, READY) == set(workflow["jobs"]) - FAST_ONLY
    concurrency = workflow["concurrency"]
    assert concurrency["group"] == "${{ github.workflow }}-${{ github.ref }}"
    assert concurrency["cancel-in-progress"] is True


def test_3_a_full_result_does_not_carry_over_to_the_next_commit() -> None:
    """Evidence is per head: the old head's green FULL run proves nothing about the new one."""
    old_head, new_head = "1" * 40, "2" * 40
    runs = [
        {
            "id": 1,
            "run_number": 1,
            "head_sha": old_head,
            "event": "pull_request",
            "path": ".github/workflows/ci.yml",
            "status": "completed",
            "conclusion": "success",
        }
    ]
    full = [
        {"name": n, "status": "completed", "conclusion": "success"} for n in gate.REQUIRED_GATES
    ]
    verdict = gate.pull_request_verdict(
        "t" * 40, "d" * 40, ["b" * 40, new_head], runs, lambda _r: full, lambda _r: "d" * 40
    )
    assert not verdict.ok
    assert "no ci.yml pull-request run for the merged head" in verdict.detail


def test_4_returning_to_draft_starts_nothing_and_the_next_push_is_fast(workflow) -> None:
    assert "converted_to_draft" not in triggers(workflow)["pull_request"]["types"]
    assert running(workflow, DRAFT_PUSH) == FAST_JOBS


@pytest.mark.parametrize("context", [DISPATCH, event("workflow_dispatch", draft=None)])
def test_5_a_dispatched_run_is_full(workflow, context) -> None:
    """main-proof's fallback and a run started by hand: no pull request, so FULL."""
    assert running(workflow, context) == set(workflow["jobs"]) - FAST_ONLY


def test_5_main_proof_still_dispatches_this_workflow() -> None:
    main_proof = (WORKFLOWS / "main-proof.yml").read_text(encoding="utf-8")
    assert "assert_main_proved.py" in main_proof
    assert "--dispatch" in main_proof
    script = (ROOT / "scripts" / "ci" / "assert_main_proved.py").read_text(encoding="utf-8")
    assert '"ci.yml"' in script or "'ci.yml'" in script


# ---------------------------------------------------------------------------
# Required check names: earned in FULL, absent in FAST
# ---------------------------------------------------------------------------


def test_full_reports_each_required_gate_under_its_exact_name(workflow) -> None:
    names = [job_name(job, READY) for job in workflow["jobs"].values()]
    for required in gate.REQUIRED_GATES:
        assert names.count(required) == 1, f"{required!r} is not reported exactly once in FULL"


def test_fast_reports_no_required_gate_but_the_quality_job(workflow) -> None:
    """Skipped jobs keep no required name, so branch protection waits ("Expected").

    A skipped required check counts as passing, and a draft's commit is the
    same commit the pull request has the moment it is marked ready — before
    the FULL run's checks exist. Without the renaming, `gh pr ready && gh pr
    merge` could merge on FAST evidence in that window.
    """
    for key, job in workflow["jobs"].items():
        name = job_name(job, DRAFT_PUSH)
        if key == "quality":
            assert name == gate.REQUIRED_GATES[0]
            continue
        assert name not in gate.REQUIRED_GATES, f"`{key}` reports {name!r} on a draft"


def test_the_quality_job_is_the_same_job_in_both_tiers(workflow) -> None:
    """FAST's only required check proves exactly what FULL's does: no step depends on the tier."""
    quality = workflow["jobs"]["quality"]
    assert "if" not in quality
    for step in quality["steps"]:
        assert "if" not in step, f"quality step {step.get('name')!r} is conditional"


# ---------------------------------------------------------------------------
# 6: FAST evidence satisfies neither main-proof nor the release gate
# ---------------------------------------------------------------------------


def _pr_run() -> dict[str, Any]:
    return {
        "id": 9,
        "run_number": 9,
        "head_sha": "h" * 40,
        "event": "pull_request",
        "path": ".github/workflows/ci.yml",
        "status": "completed",
        "conclusion": "success",
    }


@pytest.mark.parametrize("ui", [False, True])
def test_6_a_green_fast_run_is_not_proof_of_a_merge(workflow, ui) -> None:
    """Its run concludes success — skipped jobs fail nothing — and is still refused.

    With the visual job green on a UI draft too: it is not a required gate."""
    fast = simulated_jobs(workflow, DRAFT_PUSH, ui=ui)
    verdict = gate.pull_request_verdict(
        "t" * 40, "d" * 40, ["b" * 40, "h" * 40], [_pr_run()], lambda _r: fast, lambda _r: "d" * 40
    )
    assert not verdict.ok
    for required in gate.REQUIRED_GATES[1:]:
        assert f"{required} (not in the run" in verdict.detail


@pytest.mark.parametrize("ui", [False, True])
def test_6_even_with_its_skipped_jobs_hidden_a_fast_run_lacks_the_gates(workflow, ui) -> None:
    """Independent of the skip check: only the jobs that ran, and still not FULL."""
    ran_only = [
        job for job in simulated_jobs(workflow, DRAFT_PUSH, ui=ui) if job["conclusion"] == "success"
    ]
    assert gate.not_green(ran_only)


def test_6_a_fast_record_names_no_tested_tree() -> None:
    record = f"tree {'d' * 40}\ncommit {'c' * 40}\nevent pull_request\ntier fast\n"
    assert gate.parse_tested_tree(record) is None


def test_6_a_fast_run_on_main_could_not_be_mains_proof_either(workflow) -> None:
    fast = simulated_jobs(workflow, DRAFT_PUSH)
    main_run = _pr_run() | {
        "event": "workflow_dispatch",
        "head_branch": "main",
        "head_sha": "t" * 40,
    }
    assert not gate.ci_verdict("t" * 40, [main_run], lambda _r: fast).ok


def test_6_a_full_run_is_proof(workflow) -> None:
    """The control: the same check accepts what a green FULL run reports."""
    full = simulated_jobs(workflow, READY)
    full = [job for job in full if job["conclusion"] == "success"]  # FAST-only jobs are skipped
    assert not gate.not_green(full)


def test_the_tier_is_recorded_from_the_same_condition(workflow) -> None:
    (step,) = [
        s
        for s in workflow["jobs"]["quality"]["steps"]
        if s.get("name") == "Record the tree this run tests"
    ]
    assert evaluate(step["env"]["TIER"], DRAFT_PUSH) == "fast"
    assert evaluate(step["env"]["TIER"], READY) == "full"
    assert evaluate(step["env"]["TIER"], DISPATCH) == "full"
    assert 'echo "tier $TIER"' in step["run"]


# ---------------------------------------------------------------------------
# 7: a failed or skipped prerequisite can never pass a required gate
# ---------------------------------------------------------------------------

AGGREGATORS = {"tests-gate", "browser-gate"}
INFORMATIONAL = {"shard-balance", "focused", "changes"}


def _required_keys(workflow: dict[str, Any]) -> dict[str, str]:
    return {
        key: job_name(job, READY)
        for key, job in workflow["jobs"].items()
        if job_name(job, READY) in gate.REQUIRED_GATES
    }


def test_7_every_job_is_a_gate_or_stands_behind_one(workflow) -> None:
    """A job whose failure no required gate sees could fail and merge anyway."""
    required = _required_keys(workflow)
    aggregated = {need for key in AGGREGATORS for need in workflow["jobs"][key]["needs"]}
    for key in workflow["jobs"]:
        assert key in required or key in aggregated or key in INFORMATIONAL, (
            f"`{key}` is neither a required gate nor aggregated by one"
        )


def test_7_every_job_behind_the_preflight_is_gated_by_it(workflow) -> None:
    """Expensive jobs start only after the preflight; required leaves refuse without it."""
    for key in ("postgres-safety", "tests", "browser"):
        assert workflow["jobs"][key]["needs"] == ["preflight"]
    assert workflow["jobs"]["visual"]["needs"] == ["preflight", "changes"]


@pytest.mark.parametrize("ui", [False, True])
def test_7_a_failed_preflight_skips_every_expensive_job(workflow, ui) -> None:
    """Including the visual job, which in FULL waits for the preflight's result."""
    outcome = results(workflow, READY, ui=ui, failed=frozenset({"preflight"}))
    for key in ("postgres-safety", "tests", "browser", "visual"):
        assert outcome[key] == "skipped", key
    # They run — and refuse; the bash tests below run their refusals.
    for key in ("tests-gate", "browser-gate", "recovery", "container"):
        assert outcome[key] != "skipped", key


def test_7_a_fast_visual_failure_fails_no_required_check(workflow) -> None:
    """On a draft the visual job is information: no required job aggregates it."""
    outcome = results(workflow, DRAFT_PUSH, ui=True, failed=frozenset({"visual"}))
    for key, job in workflow["jobs"].items():
        if job_name(job, DRAFT_PUSH) in gate.REQUIRED_GATES:
            assert outcome[key] == "success", key
    for key in ("tests-gate", "browser-gate", "recovery", "container"):
        assert "preflight" in workflow["jobs"][key]["needs"]


def test_7_a_required_job_with_needs_runs_whatever_they_did(workflow) -> None:
    """A required check that is skipped blocks nothing, so none of them may be."""
    for key in _required_keys(workflow):
        job = workflow["jobs"][key]
        if not job.get("needs"):
            continue
        condition = str(job["if"])
        assert evaluate(condition, READY, run_state="failed") is True, key
        assert evaluate(condition, READY, run_state="running") is True, key


def _run_bash(script: str, env: dict[str, str]) -> int:
    assert BASH, "bash is needed to run the workflow's own scripts"
    return subprocess.run(  # noqa: S603 - the workflow's own script, in a temporary shell
        [BASH, "-c", script], env={"PATH": "/usr/bin:/bin", **env}, capture_output=True, check=False
    ).returncode


@pytest.mark.parametrize("gate_key", sorted(AGGREGATORS))
@pytest.mark.parametrize("bad", ["failure", "skipped", "cancelled", ""])
def test_7_an_aggregator_refuses_any_dependency_that_did_not_succeed(
    workflow, gate_key, bad
) -> None:
    step = workflow["jobs"][gate_key]["steps"][0]
    env_names = list(step["env"])
    assert "PREFLIGHT" in env_names
    assert _run_bash(step["run"], dict.fromkeys(env_names, "success")) == 0
    for name in env_names:
        values = dict.fromkeys(env_names, "success") | {name: bad}
        assert _run_bash(step["run"], values) != 0, f"{gate_key} passed with {name}={bad!r}"


@pytest.mark.parametrize("leaf", ["recovery", "container"])
@pytest.mark.parametrize("bad", ["failure", "skipped", "cancelled", ""])
def test_7_a_chained_required_leaf_refuses_a_failed_preflight(workflow, leaf, bad) -> None:
    first = workflow["jobs"][leaf]["steps"][0]
    assert first["name"] == "The preflight passed"
    assert first["env"]["PREFLIGHT"] == "${{ needs.preflight.result }}"
    assert _run_bash(first["run"], {"PREFLIGHT": "success"}) == 0
    assert _run_bash(first["run"], {"PREFLIGHT": bad}) != 0


def test_7_the_shard_matrices_keep_running_every_shard(workflow) -> None:
    """The matrices' deliberate behaviour is unchanged: every shard reports."""
    for key in ("tests", "browser"):
        assert workflow["jobs"][key]["strategy"]["fail-fast"] is False


# ---------------------------------------------------------------------------
# 8: what was true before still is
# ---------------------------------------------------------------------------


def test_8_no_workflow_runs_pull_request_code_with_a_privileged_trigger() -> None:
    for path in WORKFLOWS.glob("*.yml"):
        on = triggers(yaml.safe_load(path.read_text(encoding="utf-8")))
        assert "pull_request_target" not in (on or {}), path.name
        assert "workflow_run" not in (on or {}), path.name


def test_8_the_fast_jobs_cannot_write_anything(workflow) -> None:
    assert workflow["permissions"] == {"contents": "read"}
    for key in ("quality", "focused", "changes", "preflight", "visual"):
        assert "permissions" not in workflow["jobs"][key]


def test_8_the_focused_selection_feeds_nothing_in_full() -> None:
    """FULL's suites come from ci_sharding over the whole collection; this is FAST's alone."""
    for path in [
        ROOT / "ci_sharding.py",
        ROOT / "ci_slices.py",
        ROOT / "ci_workflow_selection.py",
        ROOT / "conftest.py",
        ROOT / "scripts" / "ci" / "assert_shard_completeness.py",
        ROOT / "scripts" / "ci" / "run_slices.py",
    ]:
        assert "ci_focused_selection" not in path.read_text(encoding="utf-8"), path.name
    jobs = yaml.safe_load(CI.read_text(encoding="utf-8"))["jobs"]
    usage = {key for key, job in jobs.items() if "ci_focused_selection" in yaml.safe_dump(job)}
    assert usage == FAST_ONLY
    for key in usage:
        assert str(jobs[key]["if"]) == f"${{{{ {FAST} }}}}", f"`{key}` must be FAST-only"


# ---------------------------------------------------------------------------
# The focused selection itself
# ---------------------------------------------------------------------------

TIMINGS = {
    "tests/test_cheap.py": ci_sharding.Measurement(seconds=2.0, tests=4),
    "tests/test_mid.py": ci_sharding.Measurement(seconds=30.0, tests=10),
    "tests/test_heavy.py": ci_sharding.Measurement(seconds=500.0, tests=100),
    **{path: ci_sharding.Measurement(seconds=1.0, tests=1) for path in ci_focused_selection.ALWAYS},
}


def _select(changed, existing=None, budget=240.0, ci_tests=()):
    if existing is None:
        existing = [*changed, *ci_focused_selection.ALWAYS, *ci_tests]
    existing = set(existing)
    return ci_focused_selection.select(
        changed, exists=existing.__contains__, ci_tests=ci_tests, timings=TIMINGS, budget=budget
    )


def test_focused_runs_the_contract_set_even_with_nothing_changed() -> None:
    assert _select([]).run == ci_focused_selection.ALWAYS


def test_focused_runs_the_changed_test_files() -> None:
    picked = _select(["app/matters/views.py", "tests/test_mid.py", "tests/test_cheap.py"])
    assert picked.run[-2:] == ("tests/test_cheap.py", "tests/test_mid.py")
    assert not picked.deferred


def test_focused_never_selects_browser_tests_or_deleted_files() -> None:
    picked = _select(["e2e/test_shell.py", "tests/test_gone.py"], existing=[])
    assert picked.run == ()
    picked = _select(["e2e/test_shell.py", "tests/test_gone.py"])
    assert "e2e/test_shell.py" not in picked.run


def test_focused_defers_what_exceeds_the_budget_and_names_it() -> None:
    picked = _select(["tests/test_heavy.py", "tests/test_mid.py"])
    assert "tests/test_mid.py" in picked.run
    assert picked.deferred == ("tests/test_heavy.py",)
    assert picked.predicted_seconds <= 240.0


def test_focused_adds_the_ci_tests_when_ci_changes() -> None:
    ci_tests = ("tests/test_ci_tiers.py",)
    picked = _select([".github/workflows/ci.yml"], ci_tests=ci_tests)
    assert "tests/test_ci_tiers.py" in picked.run
    assert "tests/test_ci_tiers.py" not in _select(["app/x.py"], ci_tests=ci_tests).run


def test_focused_is_deterministic() -> None:
    changed = ["tests/test_mid.py", "tests/test_cheap.py", "tests/test_heavy.py"]
    assert _select(changed) == _select(list(reversed(changed)))


def test_focused_contract_set_exists() -> None:
    for path in ci_focused_selection.ALWAYS:
        assert (ROOT / path).is_file(), path


@pytest.mark.parametrize(
    ("changed", "ui"),
    [
        (["templates/matters/detail.html"], True),
        (["static/css/app.css", "app/x.py"], True),
        (["e2e/baselines/otsing.png"], True),
        (["e2e/test_ui_regression.py"], True),
        (["app/matters/views.py", "tests/test_matters.py"], False),
        (["docs/ci-architecture.md", ".github/workflows/ci.yml"], False),
        (["app/templatetags/x.py", "mystatic/x.css"], False),
        ([], False),
        (["templates\\base.html"], True),
    ],
)
def test_the_ui_rule(changed: list[str], ui: bool) -> None:
    assert ci_focused_selection.touches_ui(changed) is ui


def test_the_ui_answer_is_written_as_a_step_output(tmp_path) -> None:
    changed = tmp_path / "changed.txt"
    output = tmp_path / "github_output"
    changed.write_text("templates/base.html\n", encoding="utf-8")
    assert ci_focused_selection.main(["--changed", str(changed), "--ui-output", str(output)]) == 0
    changed.write_text("app/x.py\n", encoding="utf-8")
    assert ci_focused_selection.main(["--changed", str(changed), "--ui-output", str(output)]) == 0
    assert output.read_text(encoding="utf-8") == "ui=true\nui=false\n"


def test_the_changes_job_needs_nothing_but_the_standard_library() -> None:
    """It runs `python3` on the bare runner, before (and without) `uv sync`."""
    import ast

    for name in ("ci_focused_selection.py", "ci_sharding.py"):
        tree = ast.parse((ROOT / name).read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, ast.Import | ast.ImportFrom):
                module = (
                    node.module if isinstance(node, ast.ImportFrom) else node.names[0].name
                ) or ""
                top = module.split(".")[0]
                assert top in sys.stdlib_module_names or top in {"ci_sharding", "__future__"}, (
                    f"{name} imports {module}"
                )
