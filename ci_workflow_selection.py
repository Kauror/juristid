"""What the CI workflow actually asks pytest to run, read from the workflow.

The proof that sharding runs every test (`scripts/ci/assert_shard_completeness.py`)
and the shard-health report (`scripts/ci/report_shard_health.py`) each kept a
hand-copied list of the pytest arguments that define a suite. Nothing tied the
copies to `.github/workflows/ci.yml`, so a workflow edit that narrowed a suite —
an added `--ignore`, `-k`, `-m` or `--deselect`, a changed path — left both
guards green over a smaller suite (ENG-110).

This module is the one definition, and it is derived: it finds each sharded
job's pytest step, splits its `run:` line, and keeps the arguments that decide
*which tests* run. Everything that only decides *how* they run — coverage,
reports, tracing, the browser, the shard options themselves — is dropped by
name. **Anything it does not recognise stops it**: an unknown flag might
select, and a parser that guessed would make every new flag a new blind spot.

At the repository root beside `ci_sharding.py`, so both scripts and the tests
import it without any path arithmetic of their own.
"""

from __future__ import annotations

import pathlib
import re
import shlex
from dataclasses import dataclass
from typing import Any

import yaml

REPOSITORY_ROOT = pathlib.Path(__file__).resolve().parent
WORKFLOW = REPOSITORY_ROOT / ".github" / "workflows" / "ci.yml"


@dataclass(frozen=True)
class Suite:
    """One pytest invocation the workflow runs, and where it comes from."""

    job: str
    step: str
    label: str
    #: The arguments that select tests, in the order the workflow gives them.
    selection: tuple[str, ...]
    #: The shard count the command itself states, or 1 when it is unsharded.
    shard_count: int


#: The workflow steps whose pytest command defines a suite. Keyed by job; the
#: value is the step's `name:` and the label a report prints. A step that is
#: renamed or removed is an error here rather than a suite nobody checks.
SUITE_STEPS: dict[str, tuple[str, str]] = {
    "tests": ("Test suite", "PostgreSQL test suite"),
    "browser": ("Browser workflow", "Browser workflow"),
    "visual": ("Visual regression", "Visual regression"),
}

#: The universe each group of suites must cover between them. `tests` is what
#: `pytest` collects with no arguments (the `testpaths` in pyproject.toml); the
#: browser and visual jobs split the `e2e` directory between them. A selection
#: that leaves any test of its universe out is a narrowing, and a narrowing is
#: a failure of the proof — not a new, smaller definition of complete.
UNIVERSES: dict[str, tuple[tuple[str, ...], tuple[str, ...]]] = {
    "tests": (("tests",), ()),
    "e2e": (("browser", "visual"), ("e2e",)),
}

#: Flags that decide how tests run, never which. Each takes a value, given
#: either as `--flag=value` or as the next word.
_NON_SELECTING_WITH_VALUE = frozenset(
    {
        "--browser",
        "--tracing",
        "--output",
        "--junitxml",
        "--junit-xml",
        "--cov-report",
        "--screenshot",
        "--video",
        "--timeout",
        "--durations",
        "--maxfail",
    }
)
#: Flags without a value that decide nothing about selection.
_NON_SELECTING_FLAGS = frozenset({"-q", "-v", "-vv", "-ra", "-rA", "--no-header", "--cov"})
#: The shard options, read for the count and otherwise dropped: sharding is what
#: the proof checks, not part of what a suite is.
_SHARD_OPTIONS = frozenset({"--shard-count", "--shard-index"})
#: Selecting flags that take a value. Kept, with their value, in the selection.
_SELECTING_WITH_VALUE = frozenset({"--ignore", "--ignore-glob", "--deselect", "-k", "-m"})


class UnknownSelection(Exception):
    """The workflow uses a pytest argument this module cannot classify."""


#: A GitHub Actions expression, which may contain spaces (`${{ matrix.shard }}`)
#: and would otherwise be split into three words.
_EXPRESSION = re.compile(r"\$\{\{[^}]*\}\}")
_PLACEHOLDER = "@EXPRESSION@"


def _pytest_arguments(run: str) -> list[str]:
    """The words after `pytest` on the step's pytest line."""
    run = _EXPRESSION.sub(_PLACEHOLDER, run)
    lines = [line for line in run.splitlines() if "pytest" in line]
    if len(lines) != 1:
        raise UnknownSelection(f"expected one pytest command in the step, found {len(lines)}")
    words = shlex.split(lines[0])
    if "pytest" not in words:
        raise UnknownSelection(f"cannot find the pytest executable in: {lines[0]}")
    return words[words.index("pytest") + 1 :]


def parse_selection(run: str) -> tuple[tuple[str, ...], int]:
    """(selecting arguments, stated shard count) for one pytest command.

    Raises :class:`UnknownSelection` on anything it does not recognise.
    """
    words = _pytest_arguments(run)
    selection: list[str] = []
    shard_count = 1
    index = 0
    while index < len(words):
        word = words[index]
        name, has_inline_value, value = word.partition("=")
        if word.startswith("-"):
            if name in _NON_SELECTING_WITH_VALUE or name in _SHARD_OPTIONS:
                if not has_inline_value:
                    index += 1
                    value = words[index] if index < len(words) else ""
                if name == "--shard-count":
                    shard_count = int(value)
                index += 1
                continue
            if word in _NON_SELECTING_FLAGS or name in _NON_SELECTING_FLAGS:
                index += 1
                continue
            if name in _SELECTING_WITH_VALUE:
                if has_inline_value:
                    selection.append(word)
                else:
                    index += 1
                    if index >= len(words):
                        raise UnknownSelection(f"{name} has no value")
                    selection.extend([name, words[index]])
                index += 1
                continue
            raise UnknownSelection(
                f"the workflow passes pytest {word!r}, which ci_workflow_selection.py does not "
                "know. Decide whether it selects tests, and add it to the right set."
            )
        if _PLACEHOLDER in word:
            raise UnknownSelection(f"a selecting argument depends on an expression: {word}")
        selection.append(word)  # a path or a node id
        index += 1
    return tuple(selection), shard_count


def _step(workflow: dict[str, Any], job: str, name: str) -> dict[str, Any]:
    steps = workflow.get("jobs", {}).get(job, {}).get("steps", [])
    for step in steps:
        if step.get("name") == name:
            return step
    raise UnknownSelection(f"job `{job}` has no step named {name!r}; this module is out of date")


def suites(workflow_path: pathlib.Path = WORKFLOW) -> dict[str, Suite]:
    """Every suite the workflow runs, derived from its own pytest commands."""
    workflow = yaml.safe_load(workflow_path.read_text(encoding="utf-8"))
    found: dict[str, Suite] = {}
    for job, (step_name, label) in SUITE_STEPS.items():
        run = _step(workflow, job, step_name).get("run", "")
        selection, shard_count = parse_selection(run)
        found[job] = Suite(job, step_name, label, selection, shard_count)
    return found
