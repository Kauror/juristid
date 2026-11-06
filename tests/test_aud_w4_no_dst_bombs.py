"""No daylight-saving time bomb in a test that reads Tallinn day offsets (G-01).

`tests/test_work_activity.py` built "sixty days ago" as `timezone.now() +
timedelta(days=-60)` — sixty times 24 hours — and then asserted that the
activity it recorded sat exactly `-60` *Tallinn calendar days* back. Across a
change between EET and EEST, sixty times 24 hours is not sixty calendar days
for one hour of the evening: between about 26 October and 24 December, 21:00 to
22:00 UTC, the record landed on day -59, and between about 29 March and 28 May
on day -61. Sixteen tests failed in that hour (audit 2026-11, G-01; CI run
42109147055). The application was right throughout: it reads every instant by
its local day (`app.matters.activity`, `work_items.quiet_matters`).

The fix is to build the instant from the local day it is meant to be on —
`start_of_local_day(_day(-60))` — so the test says what it asserts. This file
keeps the pattern from coming back.

**The rule, scoped to the shape that failed.** A test module is flagged when it
builds an instant as `timezone.now() ± timedelta(days=…)` *and* either

* **A** reads a day offset back — `(<day> - <local today>).days`, where the
  local today is `timezone.localdate()` or a module helper built on it — or
* **B** asserts a local day at the same offset the instant was built with —
  `now - timedelta(days=8)` beside `_day(-8)`, where `_day` is such a helper.

**Its limit, said plainly.** Most `timezone.now() - timedelta(days=N)` in the
suite are legitimate: "older than", "within the last week", a token's age —
comparisons a one-hour shift cannot flip. Those are not flagged, and the rule
does not try to follow an instant through variables, fixtures or other modules.
It catches the two shapes G-01 was made of; a new kind of DST-sensitive test
would need its own guard. The audit's pinned-clock runs (21:30 UTC in November,
December and April) are the evidence the rest of the suite is not affected by
this shape.
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
SCANNED = (ROOT / "tests", ROOT / "e2e")


def _is_call_of(node: ast.AST, owner: str, name: str) -> bool:
    """`owner.name()` with no arguments, e.g. `timezone.now()`."""
    return (
        isinstance(node, ast.Call)
        and not node.args
        and not node.keywords
        and isinstance(node.func, ast.Attribute)
        and node.func.attr == name
        and isinstance(node.func.value, ast.Name)
        and node.func.value.id == owner
    )


def _timedelta_days(node: ast.AST) -> ast.expr | None:
    """The `days` of `timedelta(days=…)` / `dt.timedelta(…)`, or None."""
    if not isinstance(node, ast.Call):
        return None
    func = node.func
    named = (isinstance(func, ast.Name) and func.id == "timedelta") or (
        isinstance(func, ast.Attribute) and func.attr == "timedelta"
    )
    if not named:
        return None
    for keyword in node.keywords:
        if keyword.arg == "days":
            return keyword.value
    return node.args[0] if node.args else None


def _constants(tree: ast.Module) -> dict[str, int]:
    """Module-level `NAME = <int>` (such as `OLD = -60`)."""
    found: dict[str, int] = {}
    for statement in tree.body:
        if isinstance(statement, ast.Assign) and len(statement.targets) == 1:
            target = statement.targets[0]
            value = _int(statement.value, {})
            if isinstance(target, ast.Name) and value is not None:
                found[target.id] = value
    return found


def _int(node: ast.AST, constants: dict[str, int]) -> int | None:
    if isinstance(node, ast.Constant) and type(node.value) is int:
        return node.value
    if isinstance(node, ast.UnaryOp) and isinstance(node.op, ast.USub):
        inner = _int(node.operand, constants)
        return None if inner is None else -inner
    if isinstance(node, ast.Name):
        return constants.get(node.id)
    return None


def _local_day_helpers(tree: ast.Module) -> set[str]:
    """Module functions whose answer is built on `timezone.localdate()`.

    `_today()` returns it; `_day(offset)` returns `_today() + timedelta(...)`.
    Found to a fixed point, so a helper built on a helper counts.
    """
    functions = [node for node in tree.body if isinstance(node, ast.FunctionDef)]
    helpers: set[str] = set()
    changed = True
    while changed:
        changed = False
        for function in functions:
            if function.name in helpers:
                continue
            for returned in ast.walk(function):
                if not isinstance(returned, ast.Return) or returned.value is None:
                    continue
                if any(_is_local_today(node, helpers) for node in ast.walk(returned.value)):
                    helpers.add(function.name)
                    changed = True
                    break
    return helpers


def _is_local_today(node: ast.AST, helpers: set[str]) -> bool:
    if _is_call_of(node, "timezone", "localdate"):
        return True
    return (
        isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id in helpers
    )


def _instants(tree: ast.Module, constants: dict[str, int]) -> list[tuple[int, int | None]]:
    """Every `timezone.now() ± timedelta(days=K)`: (line, signed K if constant)."""
    found = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.BinOp) or not isinstance(node.op, ast.Add | ast.Sub):
            continue
        if not _is_call_of(node.left, "timezone", "now"):
            continue
        days = _timedelta_days(node.right)
        if days is None:
            continue
        value = _int(days, constants)
        if value is not None and isinstance(node.op, ast.Sub):
            value = -value
        found.append((node.lineno, value))
    return found


def _reads_day_offsets(tree: ast.Module, helpers: set[str]) -> bool:
    """Rule A: `(<day> - <local today>).days` anywhere in the module."""
    for node in ast.walk(tree):
        if (
            isinstance(node, ast.Attribute)
            and node.attr == "days"
            and isinstance(node.value, ast.BinOp)
            and isinstance(node.value.op, ast.Sub)
            and (
                _is_local_today(node.value.left, helpers)
                or _is_local_today(node.value.right, helpers)
            )
        ):
            return True
    return False


def _asserted_offsets(tree: ast.Module, helpers: set[str], constants: dict[str, int]) -> set[int]:
    """Rule B: the constant offsets the module's local-day helpers are called with."""
    offsets = set()
    for node in ast.walk(tree):
        if (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Name)
            and node.func.id in helpers
            and len(node.args) == 1
        ):
            value = _int(node.args[0], constants)
            if value is not None:
                offsets.add(value)
    return offsets


def dst_bombs(source: str) -> list[int]:
    """The lines of `source` that build a DST-sensitive instant, in order."""
    tree = ast.parse(source)
    constants = _constants(tree)
    instants = _instants(tree, constants)
    if not instants:
        return []
    helpers = _local_day_helpers(tree)
    if _reads_day_offsets(tree, helpers):
        return sorted(line for line, _ in instants)
    offsets = _asserted_offsets(tree, helpers, constants)
    return sorted(line for line, value in instants if value is not None and value in offsets)


# -- the scanner on the shapes it is for ---------------------------------------

_HEADER = """
import datetime as dt
from django.utils import timezone
OLD = -60
def _today():
    return timezone.localdate()
def _day(offset):
    return _today() + dt.timedelta(days=offset)
"""


@pytest.mark.parametrize(
    ("body", "flagged"),
    [
        pytest.param(
            "at = timezone.now() + dt.timedelta(days=OLD)\n"
            "def _last(fact):\n    return (fact.occurred_on - _today()).days\n",
            True,
            id="A: an offset read back (the G-01 fixture)",
        ),
        pytest.param(
            "at = timezone.now() - dt.timedelta(days=8)\nassert window(_day(-8))\n",
            True,
            id="B: the same offset asserted as a local day",
        ),
        pytest.param(
            "at = timezone.now() - dt.timedelta(days=90)\nassert window(_day(-8))\n",
            False,
            id="B: a different offset is a comparison, not a day",
        ),
        pytest.param(
            "at = timezone.now() - dt.timedelta(days=3)\nassert at < timezone.now()\n",
            False,
            id="an ordinary older-than comparison",
        ),
        pytest.param(
            "from app.core.dates import start_of_local_day\n"
            "at = start_of_local_day(_day(OLD))\n"
            "def _last(fact):\n    return (fact.occurred_on - _today()).days\n",
            False,
            id="the fix: an instant built from its local day",
        ),
        pytest.param(
            "at = timezone.now() - dt.timedelta(hours=3)\n"
            "def _last(fact):\n    return (fact.occurred_on - _today()).days\n",
            False,
            id="hours are not days",
        ),
    ],
)
def test_the_scanner_tells_a_dst_bomb_from_a_legitimate_use(body: str, flagged: bool) -> None:
    assert bool(dst_bombs(_HEADER + body)) is flagged


# -- the suite --------------------------------------------------------------------


def test_no_test_builds_a_local_day_from_now_plus_whole_days() -> None:
    """G-01: build the instant from its local day, `start_of_local_day(_day(n))`."""
    offenders = []
    for directory in SCANNED:
        for path in sorted(directory.rglob("*.py")):
            for line in dst_bombs(path.read_text(encoding="utf-8")):
                offenders.append(f"{path.relative_to(ROOT).as_posix()}:{line}")
    assert offenders == [], (
        "an instant built as `timezone.now() ± timedelta(days=…)` in a module that "
        "asserts Tallinn calendar days shifts by one day across EET/EEST for an hour "
        f"of the evening; use `start_of_local_day(_day(n))` instead: {offenders}"
    )


def test_the_suite_scan_reaches_the_files_g01_was_found_in() -> None:
    """The scan is not vacuous: both modules it guards are read, and still use the helpers."""
    for name in ("test_work_activity.py", "test_muu_and_kohtumine_are_never_dated_ahead.py"):
        tree = ast.parse((ROOT / "tests" / name).read_text(encoding="utf-8"))
        helpers = _local_day_helpers(tree)
        assert {"_today", "_day"} <= helpers, name
        assert _reads_day_offsets(tree, helpers), name
