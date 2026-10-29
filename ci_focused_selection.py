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

A file the table has never measured — every file a new feature adds — is
priced from its own size rather than as an average file: its tests are counted
from the source (`count_tests`, literal parametrisation included) and
multiplied by the measured per-test rate of its directory, and the output
labels that price `unmeasured`. Pricing it as the median file (~3 s) is how a
70-test module used to look as cheap as a four-test one, so the budget never
deferred anything (audit 2026-10-28, E-01).

It also says whether a draft touches the UI (`UI_PATH_PREFIXES` and
`UI_PATH_SEGMENT`: templates, static files, the browser tests, and the Python
that writes markup or the dates on it). When it does, the FAST tier runs the
`Visual regression` job too, so a draft gets its rendering diffs and baseline
candidates without a FULL round. Again a convenience: on a draft that job is
aggregated by no required check, and FULL runs it on every ready pull request
whatever this says.

    ci_focused_selection.py --changed changed.txt --output focused.txt
    ci_focused_selection.py --changed changed.txt --output focused.txt \\
        --services-output "$GITHUB_OUTPUT"
    ci_focused_selection.py --changed changed.txt --ui-output "$GITHUB_OUTPUT"

`--services-output` writes `services=true|false`: whether the selection holds
anything beyond the database-free contract and CI tests. The focused job
installs the OCR runtime only when it does (docs/ci-architecture.md).

Standard library only (with ci_sharding.py), so the third form runs on the
bare runner before `uv` exists.
"""

from __future__ import annotations

import argparse
import ast
import os
import sys
from collections.abc import Callable, Iterable
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
    "tests/test_e2e_contracts.py",
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

#: A change under any of these can change what a page looks like.
#:
#: Not only the three directories a page is written in. Python that writes
#: markup, or the text on it, moves a rendering just as surely: the date
#: widget whose format the visual masks name (`app/core/widgets.py`), the date
#: formatting every page prints (`app/core/dates.py`), the context every
#: template receives (`app/core/context_processors.py`), and the release notes
#: `/uuendused/` renders (their parser and their file). Template tags are the
#: pattern below. A draft that changes one of these gets its visual diffs in
#: FAST rather than discovering a moved baseline in FULL — which runs the
#: visual job whatever this says.
UI_PATH_PREFIXES: tuple[str, ...] = (
    "templates/",
    "static/",
    "e2e/",
    "app/core/widgets.py",
    "app/core/dates.py",
    "app/core/context_processors.py",
    "app/core/release_notes.py",
    "docs/release-notes/",
)

#: `app/<app>/templatetags/…` for any app — the one UI rule that is a pattern
#: rather than a prefix, so the template tags of an app added tomorrow are
#: covered tomorrow.
UI_PATH_SEGMENT = "/templatetags/"

#: Predicted seconds of test time the selected files may take together.
DEFAULT_BUDGET_SECONDS = 240.0

#: How `Selection.unmeasured` records a file whose tests could not be counted.
UNCOUNTED = -1


@dataclass(frozen=True)
class Selection:
    run: tuple[str, ...]
    #: Changed test files left to FULL because of the budget, heaviest first.
    deferred: tuple[str, ...]
    predicted_seconds: float
    #: Selected or deferred files `ci/shard-timings.json` does not hold, each
    #: with the test count its price was estimated from (`UNCOUNTED` when the
    #: source could not be read, and the median file was used).
    unmeasured: tuple[tuple[str, int], ...] = ()
    #: The contract and CI tests: database-free, so they need no services.
    fixed: tuple[str, ...] = ()

    @property
    def needs_services(self) -> bool:
        """Whether anything selected is more than the database-free fixed set."""
        return any(path not in self.fixed for path in self.run)


def _is_test_module(path: str) -> bool:
    name = path.rsplit("/", 1)[-1]
    return path.startswith("tests/") and name.startswith("test_") and name.endswith(".py")


def _literal_cases(node: ast.AST, names: dict[str, ast.AST]) -> int | None:
    """How many cases a `parametrize` argument literally holds, when it can be read."""
    if isinstance(node, ast.Name) and node.id in names:
        node = names[node.id]
    if isinstance(node, ast.List | ast.Tuple | ast.Set):
        return len(node.elts)
    if isinstance(node, ast.Dict):
        return len(node.keys)
    if isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.args:
        if node.func.id == "range":
            bounds = [arg.value for arg in node.args if isinstance(arg, ast.Constant)]
            if len(bounds) == len(node.args) and all(isinstance(b, int) for b in bounds):
                return len(range(*bounds))
            return None
        if node.func.id in {"list", "tuple", "sorted"}:
            return _literal_cases(node.args[0], names)
    return None


def _parametrize_factor(decorators: list[ast.expr], names: dict[str, ast.AST]) -> int:
    """The product of every `@pytest.mark.parametrize(…, cases)` above a test.

    A cases argument that is not a literal — a call, a comprehension — counts
    as one case, so the estimate errs low rather than inventing tests.
    """
    factor = 1
    for decorator in decorators:
        if not isinstance(decorator, ast.Call):
            continue
        func = decorator.func
        if not (isinstance(func, ast.Attribute) and func.attr == "parametrize"):
            continue
        cases: ast.AST | None = decorator.args[1] if len(decorator.args) > 1 else None
        for keyword in decorator.keywords:
            if keyword.arg == "argvalues":
                cases = keyword.value
        counted = _literal_cases(cases, names) if cases is not None else None
        factor *= max(counted or 1, 1)
    return factor


def count_tests(source: str) -> int:
    """How many tests a module collects, read from its source without importing it.

    Module-level `test*` functions and the `test*` methods of `Test*` classes,
    each multiplied by its literal parametrisation and its class's. An estimate
    for pricing, never a count anything is proved with — pytest's collection is
    that. Deterministic, and standard library only, so it runs on the bare
    runner.
    """
    tree = ast.parse(source)
    names: dict[str, ast.AST] = {}
    for node in tree.body:
        if isinstance(node, ast.Assign) and len(node.targets) == 1:
            target = node.targets[0]
            if isinstance(target, ast.Name):
                names[target.id] = node.value
        elif isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name) and node.value:
            names[node.target.id] = node.value

    def tests_in(body: list[ast.stmt], outer: int) -> int:
        total = 0
        for node in body:
            if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef):
                if node.name.startswith("test"):
                    total += outer * _parametrize_factor(node.decorator_list, names)
            elif isinstance(node, ast.ClassDef) and node.name.startswith("Test"):
                inner = outer * _parametrize_factor(node.decorator_list, names)
                total += tests_in(node.body, inner)
        return total

    return tests_in(tree.body, 1)


def read_test_count(path: str) -> int | None:
    """`count_tests` of a repository file, or None when it cannot be read or parsed."""
    try:
        return count_tests((REPOSITORY_ROOT / path).read_text(encoding="utf-8"))
    except (OSError, SyntaxError, UnicodeDecodeError, ValueError):
        return None


def select(
    changed: Iterable[str],
    exists: Callable[[str], bool] = lambda path: (REPOSITORY_ROOT / path).is_file(),
    ci_tests: Iterable[str] = (),
    timings: dict[str, ci_sharding.Measurement] | None = None,
    budget: float = DEFAULT_BUDGET_SECONDS,
    test_count: Callable[[str], int | None] = read_test_count,
) -> Selection:
    """The focused selection: pure given its arguments, so tests can hold every rule.

    A file the timing table measured costs what it measured. One the table does
    not hold costs its counted tests times its directory's measured per-test
    rate (`ci_sharding.weight_of`); only when even the count cannot be read does
    it fall back to the median file.
    """
    weights = ci_sharding.load_timings() if timings is None else timings
    paths = sorted({path.strip().replace("\\", "/") for path in changed if path.strip()})

    fixed: list[str] = [path for path in ALWAYS if exists(path)]
    if any(path.startswith(CI_PATH_PREFIXES) for path in paths):
        fixed += sorted(
            {*ci_tests, *(path for path in CI_TESTS_EXTRA if exists(path))} - set(fixed)
        )

    touched = [path for path in paths if _is_test_module(path) and exists(path)]
    touched = [path for path in touched if path not in fixed]

    counts: dict[str, int] = {}
    unmeasured: list[tuple[str, int]] = []
    for path in sorted({*fixed, *touched}):
        if path in weights:
            continue
        counted = test_count(path)
        if counted is not None:
            counts[path] = counted
        unmeasured.append((path, UNCOUNTED if counted is None else counted))

    def cost(path: str) -> float:
        return ci_sharding.weight_of(path, counts.get(path), weights)

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
    return Selection(
        run=tuple(fixed + sorted(kept)),
        deferred=tuple(deferred),
        predicted_seconds=spent,
        unmeasured=tuple(unmeasured),
        fixed=tuple(fixed),
    )


def touches_ui(changed: Iterable[str]) -> bool:
    """Whether any changed path can change a rendering."""
    for raw in changed:
        path = raw.strip().replace("\\", "/")
        if path.startswith(UI_PATH_PREFIXES):
            return True
        if path.startswith("app/") and UI_PATH_SEGMENT in path:
            return True
    return False


def describe(selection: Selection, changed_count: int) -> list[str]:
    """The lines the job prints and writes to its step summary."""
    unmeasured = dict(selection.unmeasured)

    def label(path: str) -> str:
        if path not in unmeasured:
            return path
        if unmeasured[path] == UNCOUNTED:
            return f"{path} [unmeasured: tests not countable, priced as the median file]"
        return f"{path} [unmeasured: {unmeasured[path]} test(s) counted, priced per test]"

    headline = (
        f"FAST tier: {len(selection.run)} test file(s), predicted"
        f" {selection.predicted_seconds:.0f}s"
        f" of test time, from {changed_count} changed path(s)."
    )
    if unmeasured:
        headline += (
            f" {len(unmeasured)} of them not in ci/shard-timings.json, so their"
            " share of the prediction is an estimate."
        )
    return [
        headline,
        *(f"  run      {label(path)}" for path in selection.run),
        *(
            f"  deferred {label(path)} (over the budget; FULL runs it)"
            for path in selection.deferred
        ),
        f"  needs PostgreSQL and OCR: {'yes' if selection.needs_services else 'no'}",
        "This is not merge evidence. Marking the pull request ready runs the whole of CI.",
    ]


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--changed", type=Path, required=True, help="One changed path per line.")
    parser.add_argument("--output", type=Path, help="Test files, one per line.")
    parser.add_argument("--ui-output", type=Path, help="Append ui=true|false here.")
    parser.add_argument(
        "--services-output",
        type=Path,
        help="Append services=true|false here (needs --output).",
    )
    parser.add_argument("--budget", type=float, default=DEFAULT_BUDGET_SECONDS)
    args = parser.parse_args(argv)
    if args.output is None and args.ui_output is None:
        parser.error("give --output, --ui-output or both")
    if args.services_output is not None and args.output is None:
        parser.error("--services-output describes a selection; give --output too")

    changed = args.changed.read_text(encoding="utf-8").splitlines()
    if args.ui_output is not None:
        ui = touches_ui(changed)
        with args.ui_output.open("a", encoding="utf-8", newline="\n") as handle:
            handle.write(f"ui={'true' if ui else 'false'}\n")
        print(
            f"touches the UI ({', '.join(UI_PATH_PREFIXES)}, app/*{UI_PATH_SEGMENT}):"
            f" {'yes' if ui else 'no'}"
        )
    if args.output is None:
        return 0
    ci_tests = [
        path.relative_to(REPOSITORY_ROOT).as_posix()
        for path in sorted(REPOSITORY_ROOT.glob(CI_TESTS_GLOB))
    ]
    selection = select(changed, ci_tests=ci_tests, budget=args.budget)
    args.output.write_text(
        "".join(f"{path}\n" for path in selection.run), encoding="utf-8", newline="\n"
    )
    if args.services_output is not None:
        with args.services_output.open("a", encoding="utf-8", newline="\n") as handle:
            handle.write(f"services={'true' if selection.needs_services else 'false'}\n")

    lines = describe(selection, len(changed))
    print("\n".join(lines))
    summary = os.environ.get("GITHUB_STEP_SUMMARY")
    if summary:
        with open(summary, "a", encoding="utf-8") as handle:
            handle.write("```\n" + "\n".join(lines) + "\n```\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
