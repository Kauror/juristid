"""Which tests a draft pull request's FAST run executes, and nothing more.

The FAST tier (docs/adr/0193) gives a draft a three-minute answer instead of
the whole of CI. Part of that answer is the PostgreSQL tests the draft itself
touched, run on one database beside the quality job — a convenience for the
author, never evidence:

* **Nothing the FULL tier runs depends on this.** The shards still partition
  the whole collection (ci_sharding.py), and the completeness proof still
  compares them with an unfiltered `pytest`. This module is imported by no
  part of FULL, so a mistake here can make a draft's answer less useful but
  cannot make a ready pull request's suite smaller.
* **It is not a required check**, and the release gate never reads it. Only a
  green FULL run of the exact tree releases anything
  (scripts/ci/assert_release_provenance.py).

What it selects, from the paths the pull request changes:

1. a fixed set of database-free contract tests that catch the cheap mistakes
   (an undefined CSS class, a wrapped template comment, a bare
   `pytest.raises(DomainError)`), always;
2. the CI tests, whenever the CI itself changes;
3. every changed `tests/**/test_*.py` that still exists,

within a time budget priced from `ci/shard-timings.json`. When the changed
files would exceed it, the most expensive ones are left to FULL and named, so
a draft's run stays a few minutes long and never pretends to have run them.
Browser tests are never selected: they need the servers only FULL builds.

    ci_focused_selection.py --changed changed.txt --output focused.txt
"""

from __future__ import annotations

import argparse
import os
import sys
from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path

import ci_sharding

REPOSITORY_ROOT = Path(__file__).resolve().parent

#: Database-free and seconds-cheap, and each has caught a real defect that ruff
#: and mypy passed (AGENTS.md, "local checks worth running").
ALWAYS: tuple[str, ...] = (
    "tests/test_ui_contract.py",
    "tests/test_templates.py",
    "tests/test_era_contracts.py",
    "tests/test_reporting_contract.py",
    "tests/test_runtime_safety.py",
    "tests/test_repository_data_safety.py",
    "tests/test_architecture_decisions.py",
    "tests/test_negative_tests_name_their_refusal.py",
)

#: A change under any of these is a change to CI itself.
CI_PATH_PREFIXES: tuple[str, ...] = (
    ".github/",
    "scripts/ci/",
    "ci_",
    "ci/",
    "conftest.py",
    "pyproject.toml",
)

#: What runs when CI changes: the tests of the workflows, the shards, the
#: tiers and the two gates that read CI's results.
CI_TESTS_GLOB = "tests/test_ci_*.py"
CI_TESTS_EXTRA: tuple[str, ...] = (
    "tests/test_release_provenance_gate.py",
    "tests/test_main_proof.py",
)

#: Predicted seconds of test time the selected files may take together.
DEFAULT_BUDGET_SECONDS = 240.0


@dataclass(frozen=True)
class Selection:
    run: tuple[str, ...]
    #: Changed test files left to FULL because of the budget, heaviest first.
    deferred: tuple[str, ...]
    predicted_seconds: float


def _is_test_module(path: str) -> bool:
    name = path.rsplit("/", 1)[-1]
    return path.startswith("tests/") and name.startswith("test_") and name.endswith(".py")


def select(
    changed: Iterable[str],
    exists=lambda path: (REPOSITORY_ROOT / path).is_file(),
    ci_tests: Iterable[str] = (),
    timings: dict[str, ci_sharding.Measurement] | None = None,
    budget: float = DEFAULT_BUDGET_SECONDS,
) -> Selection:
    """The focused selection: pure, so tests can hold every rule."""
    weights = ci_sharding.load_timings() if timings is None else timings
    paths = sorted({path.strip().replace("\\", "/") for path in changed if path.strip()})

    fixed: list[str] = [path for path in ALWAYS if exists(path)]
    if any(path.startswith(CI_PATH_PREFIXES) for path in paths):
        fixed += sorted(
            {*ci_tests, *(path for path in CI_TESTS_EXTRA if exists(path))} - set(fixed)
        )

    touched = [path for path in paths if _is_test_module(path) and exists(path)]
    touched = [path for path in touched if path not in fixed]

    def cost(path: str) -> float:
        return ci_sharding.weight_of(path, None, weights)

    spent = sum(cost(path) for path in fixed)
    kept: list[str] = []
    deferred: list[str] = []
    # Cheapest first, so the budget buys as many of the touched files as it can.
    for path in sorted(touched, key=lambda p: (cost(p), p)):
        if spent + cost(path) <= budget:
            kept.append(path)
            spent += cost(path)
        else:
            deferred.append(path)
    deferred.sort(key=lambda p: (-cost(p), p))
    return Selection(tuple(fixed + sorted(kept)), tuple(deferred), spent)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--changed", type=Path, required=True, help="One changed path per line.")
    parser.add_argument("--output", type=Path, required=True, help="Test files, one per line.")
    parser.add_argument("--budget", type=float, default=DEFAULT_BUDGET_SECONDS)
    args = parser.parse_args(argv)

    changed = args.changed.read_text(encoding="utf-8").splitlines()
    ci_tests = [
        path.relative_to(REPOSITORY_ROOT).as_posix()
        for path in sorted(REPOSITORY_ROOT.glob(CI_TESTS_GLOB))
    ]
    selection = select(changed, ci_tests=ci_tests, budget=args.budget)
    args.output.write_text(
        "".join(f"{path}\n" for path in selection.run), encoding="utf-8", newline="\n"
    )

    lines = [
        f"FAST tier: {len(selection.run)} test file(s), predicted"
        f" {selection.predicted_seconds:.0f}s"
        f" of test time, from {len(changed)} changed path(s).",
        *(f"  run      {path}" for path in selection.run),
        *(f"  deferred {path} (over the budget; FULL runs it)" for path in selection.deferred),
        "This is not merge evidence. Marking the pull request ready runs the whole of CI.",
    ]
    print("\n".join(lines))
    summary = os.environ.get("GITHUB_STEP_SUMMARY")
    if summary:
        with open(summary, "a", encoding="utf-8") as handle:
            handle.write("```\n" + "\n".join(lines) + "\n```\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
