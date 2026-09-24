"""The completeness proof reads the workflow's real test selection (ENG-110).

`scripts/ci/assert_shard_completeness.py` used to collect with a hand-copied
list of pytest arguments, so an `--ignore` added to the workflow's command left
the proof green over a narrower suite: the audit dropped 756 of 10,245
PostgreSQL tests that way and both guards passed. The selection is now parsed
from the workflow itself (`ci_workflow_selection.py`), and every suite is also
compared with its unfiltered universe, so a narrowing is a failure rather than
a new definition of "the whole suite".

The mutation tests below write a scratch workflow and run the real `check()`
over it with a collector that applies pytest's own selection rules to a small
synthetic tree — the real collection is what the CI quality job runs.
"""

from __future__ import annotations

import fnmatch
import importlib.util
import pathlib
import sys

import pytest
import yaml

import ci_workflow_selection as selection

ROOT = pathlib.Path(selection.REPOSITORY_ROOT)
SCRIPT = ROOT / "scripts" / "ci" / "assert_shard_completeness.py"


def _load_proof():
    spec = importlib.util.spec_from_file_location("assert_shard_completeness", SCRIPT)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


proof = _load_proof()

#: A tree small enough to reason about: two PostgreSQL files, two browser files,
#: and the visual file the browser job ignores.
TREE = [
    "tests/test_alpha.py::test_one",
    "tests/test_alpha.py::test_two",
    "tests/test_beta.py::test_three",
    "tests/test_beta.py::test_four_slow",
    "e2e/test_pages.py::test_a",
    "e2e/test_pages.py::test_b",
    "e2e/test_forms.py::test_c",
    "e2e/test_ui_regression.py::test_visual",
]


def fake_collect(arguments: list[str]) -> set[str]:
    """What pytest would collect from TREE for these arguments."""
    paths: list[str] = []
    ignores: list[str] = []
    deselected: set[str] = set()
    keyword = ""
    count, index = 1, 1
    words = list(arguments)
    position = 0
    while position < len(words):
        word = words[position]
        if word.startswith("--ignore="):
            ignores.append(word.split("=", 1)[1])
        elif word.startswith("--deselect="):
            deselected.add(word.split("=", 1)[1])
        elif word == "--deselect":
            position += 1
            deselected.add(words[position])
        elif word == "-k":
            position += 1
            keyword = words[position]
        elif word.startswith("--shard-count="):
            count = int(word.split("=", 1)[1])
        elif word.startswith("--shard-index="):
            index = int(word.split("=", 1)[1])
        else:
            paths.append(word)
        position += 1
    roots = paths or ["tests"]  # `testpaths`
    chosen = [
        node
        for node in TREE
        if any(node.startswith(root.rstrip("/")) for root in roots)
        and not any(node.startswith(ignored) for ignored in ignores)
        and node not in deselected
        and (not keyword or keyword in node)
    ]
    files = sorted({node.split("::")[0] for node in chosen})
    mine = {file for number, file in enumerate(files) if number % count == index - 1}
    return {node for node in chosen if node.split("::")[0] in mine}


def _workflow(tmp_path: pathlib.Path, *, tests: str = "", browser: str = "") -> pathlib.Path:
    """A scratch workflow with the three suite steps, optionally mutated."""
    document = {
        "jobs": {
            "tests": {
                "strategy": {"matrix": {"shard": [1, 2]}},
                "steps": [
                    {
                        "name": "Test suite",
                        "run": (
                            f"uv run pytest {tests} --shard-count=2 "
                            "--shard-index=${{ matrix.shard }} --cov --cov-report= "
                            "--junitxml=artifacts/junit-tests-${{ matrix.shard }}.xml"
                        ),
                    }
                ],
            },
            "browser": {
                "strategy": {"matrix": {"shard": [1, 2]}},
                "steps": [
                    {
                        "name": "Browser workflow",
                        "run": (
                            f"uv run pytest e2e --ignore=e2e/test_ui_regression.py {browser} "
                            "--shard-count=2 --shard-index=${{ matrix.shard }} "
                            "--browser chromium --tracing retain-on-failure "
                            "--output artifacts/playwright --junitxml=artifacts/junit.xml"
                        ),
                    }
                ],
            },
            "visual": {
                "steps": [
                    {
                        "name": "Visual regression",
                        "run": "uv run pytest e2e/test_ui_regression.py --browser chromium",
                    }
                ]
            },
        }
    }
    path = tmp_path / "ci.yml"
    path.write_text(yaml.safe_dump(document), encoding="utf-8")
    return path


# -- the parser ---------------------------------------------------------------


def test_the_real_workflow_is_read_as_the_suites_it_runs():
    suites = selection.suites()

    assert suites["tests"].selection == ()
    assert suites["browser"].selection == ("e2e", "--ignore=e2e/test_ui_regression.py")
    assert suites["visual"].selection == ("e2e/test_ui_regression.py",)
    assert suites["tests"].shard_count > 1 and suites["browser"].shard_count > 1


@pytest.mark.parametrize(
    ("command", "expected"),
    [
        ("uv run pytest -k slow --shard-count=3 --shard-index=1", (("-k", "slow"), 3)),
        ("uv run pytest tests/test_beta.py", (("tests/test_beta.py",), 1)),
        ("uv run pytest --deselect tests/x.py::t", (("--deselect", "tests/x.py::t"), 1)),
        ("uv run pytest -m 'not slow' --cov", (("-m", "not slow"), 1)),
        ("uv run pytest --junitxml out.xml --browser chromium e2e", (("e2e",), 1)),
    ],
)
def test_selecting_arguments_are_kept_and_the_rest_dropped(command, expected):
    assert selection.parse_selection(command) == expected


@pytest.mark.parametrize("flag", ["--lf", "-x", "--co", "-p no:randomly", "--last-failed"])
def test_an_unknown_argument_fails_closed(flag):
    with pytest.raises(selection.UnknownSelection):
        selection.parse_selection(f"uv run pytest {flag} tests")


def test_a_selection_built_from_an_expression_fails_closed():
    with pytest.raises(selection.UnknownSelection):
        selection.parse_selection("uv run pytest ${{ matrix.path }}")


# -- the proof, over mutated workflows ----------------------------------------


def test_the_unmutated_workflow_is_complete(tmp_path):
    problems, _ = proof.check(_workflow(tmp_path), fake_collect)
    assert problems == []


@pytest.mark.parametrize(
    ("where", "mutation"),
    [
        ("tests", "--ignore=tests/test_beta.py"),
        ("tests", "-k alpha"),
        ("tests", "--deselect=tests/test_alpha.py::test_one"),
        ("tests", "tests/test_alpha.py"),
        ("browser", "--ignore=e2e/test_forms.py"),
    ],
)
def test_a_narrowed_command_fails_the_proof(tmp_path, where, mutation):
    workflow = _workflow(tmp_path, **{where: mutation})

    problems, _ = proof.check(workflow, fake_collect)

    assert any("narrows the suite" in problem for problem in problems), problems


def test_an_unknown_flag_in_the_workflow_fails_the_proof(tmp_path):
    problems, _ = proof.check(_workflow(tmp_path, tests="--lf"), fake_collect)
    assert any("cannot be read" in problem for problem in problems), problems


def test_a_matrix_and_a_command_that_disagree_are_refused(tmp_path):
    workflow = _workflow(tmp_path)
    document = yaml.safe_load(workflow.read_text())
    document["jobs"]["tests"]["strategy"]["matrix"]["shard"] = [1]
    workflow.write_text(yaml.safe_dump(document))

    with pytest.raises(SystemExit):
        proof.check(workflow, fake_collect)


def test_both_scripts_use_the_same_definition():
    health = (ROOT / "scripts" / "ci" / "report_shard_health.py").read_text()
    completeness = SCRIPT.read_text()

    assert "ci_workflow_selection" in health and "ci_workflow_selection" in completeness
    assert '["tests"]' not in completeness.split("def check")[0]
    assert not fnmatch.filter(health.splitlines(), '*"tests": ("*')
