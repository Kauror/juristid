"""The release-time release-note gate, case by case.

`scripts/ci/assert_release_note.py` is the decision; `.github/workflows/
release-image.yml` is where it runs. The first half of this file holds every
answer the decision can give, over lists of paths and no repository. The second
half pins the workflow to the shape the decision needs: the operator names what
production runs, the gate runs before the build, and the manifest records the
answer.

The rule it exists beside is unchanged and is asserted too: there is no per-PR
check, so an internal pull request still owes nothing.
"""

from __future__ import annotations

import importlib.util
import pathlib
import subprocess
import sys

import pytest
import yaml

ROOT = pathlib.Path(__file__).resolve().parent.parent
SCRIPT = ROOT / "scripts" / "ci" / "assert_release_note.py"
WORKFLOW = ROOT / ".github" / "workflows" / "release-image.yml"
CI_WORKFLOW = ROOT / ".github" / "workflows" / "ci.yml"
NOTES_README = ROOT / "docs" / "release-notes" / "README.md"


def _load():
    spec = importlib.util.spec_from_file_location("assert_release_note", SCRIPT)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    # Registered before it runs: the script's dataclass carries string
    # annotations (`from __future__ import annotations`), and `dataclasses`
    # resolves those through `sys.modules[cls.__module__]`.
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


gate = _load()

NOTES = "docs/release-notes/uuendused.toml"


# ---------------------------------------------------------------------------
# The decision
# ---------------------------------------------------------------------------


def test_a_payload_that_carries_the_note_passes() -> None:
    verdict = gate.decide(["app/matters/views.py", NOTES])

    assert verdict.ok is True
    assert verdict.kind == "note-present"


def test_an_application_change_without_the_note_is_refused() -> None:
    verdict = gate.decide(["app/matters/views.py", "tests/test_matters.py"])

    assert verdict.ok is False
    assert verdict.kind == "missing"
    assert "app/matters/views.py" in verdict.detail
    assert "tests/test_matters.py" not in verdict.detail


@pytest.mark.parametrize(
    "paths",
    [
        ["tests/test_matters.py", "e2e/test_lawyer_workflow.py"],
        [".github/workflows/ci.yml", "scripts/ci/update_shard_timings.py"],
        ["docs/adr/0076-closed-matter-dokumendid-is-read-only.md", "docs/release-notes/README.md"],
        ["deploy/unraid-main/compose.yml", "Dockerfile", "uv.lock", "pyproject.toml"],
        ["README.md", "AGENTS.md", "prompts/x.md", "tools/onenote_export/x.py"],
        [],
    ],
    ids=["tests", "ci", "docs", "deploy", "root-and-tooling", "rebuild-of-the-same-revision"],
)
def test_an_internal_only_payload_owes_no_note(paths: list[str]) -> None:
    """The per-PR rule, kept at release granularity: internal work needs no note."""
    verdict = gate.decide(paths)

    assert verdict.ok is True
    assert verdict.kind == "internal-only"


def test_a_waiver_releases_anyway_and_keeps_the_reason() -> None:
    verdict = gate.decide(
        ["app/core/views.py"], waiver="Ainult logimise parandus, kasutaja ei näe."
    )

    assert verdict.ok is True
    assert verdict.kind == "waived"
    assert verdict.detail == "Ainult logimise parandus, kasutaja ei näe."


def test_a_blank_waiver_is_no_waiver() -> None:
    verdict = gate.decide(["app/core/views.py"], waiver="   ")

    assert verdict.ok is False
    assert verdict.kind == "missing"


def test_the_readme_beside_the_notes_is_not_the_note() -> None:
    verdict = gate.decide(["app/core/views.py", "docs/release-notes/README.md"])

    assert verdict.ok is False


@pytest.mark.parametrize(
    ("path", "facing"),
    [
        ("app/matters/views.py", True),
        ("app/matters/migrations/0019_x.py", True),
        ("templates/matters/partials/header.html", True),
        ("static/js/app.js", True),
        ("static/css/tokens.css", True),
        ("config/settings.py", True),
        ("manage.py", True),
        ("tests/test_x.py", False),
        ("e2e/test_x.py", False),
        ("e2e/baselines/teema-1024.png", False),
        ("docs/adr/0001.md", False),
        (NOTES, False),
        (".github/workflows/release-image.yml", False),
        ("scripts/ci/assert_release_note.py", False),
        ("deploy/unraid-main/README.md", False),
        ("Dockerfile", False),
        ("docker-compose.yml", False),
        ("uv.lock", False),
        ("ci_sharding.py", False),
        ("templates\\matters\\x.html", True),
    ],
)
def test_where_a_reader_can_meet_a_change(path: str, facing: bool) -> None:
    assert gate.is_user_facing(path) is facing


def test_windows_separators_and_blank_lines_are_read_as_paths() -> None:
    verdict = gate.decide(["", "docs\\release-notes\\uuendused.toml", "  "])

    assert verdict.kind == "note-present"


# ---------------------------------------------------------------------------
# The command line, as the workflow calls it
# ---------------------------------------------------------------------------


def _run(tmp_path: pathlib.Path, paths: list[str], *extra: str) -> tuple[int, str, str, str]:
    changed = tmp_path / "changed.txt"
    changed.write_text("\n".join(paths) + "\n", encoding="utf-8")
    env_file = tmp_path / "env.txt"
    env_file.write_text("EXISTING=1\n", encoding="utf-8")
    completed = subprocess.run(  # noqa: S603 - a fixed interpreter and a repository path
        [
            sys.executable,
            str(SCRIPT),
            "--changed-files",
            str(changed),
            "--env-file",
            str(env_file),
            *extra,
        ],
        capture_output=True,
        text=True,
        encoding="utf-8",
        check=False,
    )
    return completed.returncode, completed.stdout, completed.stderr, env_file.read_text("utf-8")


def test_the_command_fails_the_build_on_a_missing_note(tmp_path: pathlib.Path) -> None:
    code, out, err, env = _run(tmp_path, ["app/matters/views.py"])

    assert code == 1
    assert "release note: missing" in out
    assert "uuendused.toml" in err
    assert "EXISTING=1\n" in env
    assert "RELEASE_NOTE=missing\n" in env


def test_the_command_passes_and_records_the_kind(tmp_path: pathlib.Path) -> None:
    code, out, _, env = _run(tmp_path, ["app/matters/views.py", NOTES])

    assert code == 0
    assert "release note: note-present" in out
    assert "RELEASE_NOTE=note-present\n" in env


def test_the_command_records_the_waiver_for_the_manifest(tmp_path: pathlib.Path) -> None:
    code, _, _, env = _run(tmp_path, ["app/x.py"], "--waiver", "Kasutaja jaoks ei muutu midagi.")

    assert code == 0
    assert "RELEASE_NOTE=waived\n" in env
    assert "RELEASE_NOTE_DETAIL=Kasutaja jaoks ei muutu midagi.\n" in env


# ---------------------------------------------------------------------------
# The workflow
# ---------------------------------------------------------------------------


@pytest.fixture(scope="module")
def workflow() -> dict:
    # `on` is parsed by YAML 1.1 as the boolean True; read it under either key.
    return yaml.safe_load(WORKFLOW.read_text(encoding="utf-8"))


def _inputs(workflow: dict) -> dict:
    trigger = workflow.get("on", workflow.get(True))
    return trigger["workflow_dispatch"]["inputs"]


def _steps(workflow: dict) -> list[dict]:
    return workflow["jobs"]["build"]["steps"]


def test_the_operator_must_name_what_production_runs(workflow: dict) -> None:
    inputs = _inputs(workflow)

    assert inputs["previous_sha"]["required"] is True
    assert inputs["previous_sha"]["type"] == "string"
    assert inputs["sha"]["required"] is True


def test_the_waiver_is_optional_and_empty_by_default(workflow: dict) -> None:
    waiver = _inputs(workflow)["release_note_waiver"]

    assert waiver.get("required", False) is False
    assert waiver.get("default", "") == ""


def test_the_gate_runs_before_the_build(workflow: dict) -> None:
    steps = _steps(workflow)
    gate_index = next(
        index for index, step in enumerate(steps) if "assert_release_note.py" in step.get("run", "")
    )
    build_index = next(index for index, step in enumerate(steps) if step.get("name") == "Build")

    assert gate_index < build_index


def test_the_gate_step_proves_the_two_commits_are_related(workflow: dict) -> None:
    step = next(
        step for step in _steps(workflow) if "assert_release_note.py" in step.get("run", "")
    )

    assert "merge-base --is-ancestor" in step["run"]
    assert "git diff --name-only" in step["run"]
    assert "set -euo pipefail" in step["run"]
    assert "inputs.release_note_waiver" in str(step.get("env", {}))


def test_the_manifest_records_the_previous_revision_and_the_verdict(workflow: dict) -> None:
    step = next(step for step in _steps(workflow) if step.get("name") == "Save and digest")

    assert "previous:" in step["run"]
    assert "release_note:" in step["run"]
    assert "$RELEASE_NOTE" in step["run"]


def test_pull_requests_still_owe_no_note() -> None:
    """The per-PR half of the rule is unchanged, in the workflow and in the README."""
    assert "assert_release_note.py" not in CI_WORKFLOW.read_text(encoding="utf-8")
    readme = NOTES_README.read_text(encoding="utf-8")
    assert "no CI rule that a pull request must edit this file" in readme
    assert "release-image.yml" in readme
    assert "release_note_waiver" in readme
