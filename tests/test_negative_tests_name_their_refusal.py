"""No new negative test may pass on *any* refusal (ENG-108).

``pytest.raises(DomainError)`` with nothing else holds when the operation is
refused for any reason at all, so a test named for one rule kept passing after
that rule was gone, as long as something else refused first. 161 of them were
converted to name their refusal (`tests/refusals.py`: ``refused(message)``).
This keeps the count at zero: a ``pytest.raises(DomainError)`` must carry
``match=``, or bind the exception and read it afterwards.

A test that genuinely means "any business refusal will do" goes in
``ANY_REFUSAL_WILL_DO`` with the reason written next to it. It is empty: every
test converted so far had a specific refusal in mind.
"""

from __future__ import annotations

import ast
from pathlib import Path

from django.conf import settings

TESTS = Path(settings.BASE_DIR) / "tests"

#: ``"tests/test_x.py::test_name": "why any refusal is the point"``.
ANY_REFUSAL_WILL_DO: dict[str, str] = {}


def _is_domain_error_raises(call: ast.expr) -> bool:
    if not (isinstance(call, ast.Call) and call.args):
        return False
    func = call.func
    name = func.attr if isinstance(func, ast.Attribute) else getattr(func, "id", "")
    first = call.args[0]
    expected = first.attr if isinstance(first, ast.Attribute) else getattr(first, "id", "")
    return name == "raises" and expected == "DomainError"


def _reads(function: ast.AST, name: str) -> bool:
    """Whether the bound ExceptionInfo is looked at: ``name.value`` or ``name.match``."""
    return any(
        isinstance(node, ast.Attribute)
        and isinstance(node.value, ast.Name)
        and node.value.id == name
        and node.attr in {"value", "match", "typename", "type"}
        for node in ast.walk(function)
    )


def unnamed_refusals() -> list[str]:
    found: list[str] = []
    for path in sorted(TESTS.rglob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for function in ast.walk(tree):
            if not isinstance(function, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            for node in ast.walk(function):
                if not isinstance(node, (ast.With, ast.AsyncWith)):
                    continue
                for item in node.items:
                    call = item.context_expr
                    if not _is_domain_error_raises(call):
                        continue
                    assert isinstance(call, ast.Call)
                    if any(keyword.arg == "match" for keyword in call.keywords):
                        continue
                    bound = item.optional_vars
                    if isinstance(bound, ast.Name) and _reads(function, bound.id):
                        continue
                    key = f"{path.relative_to(TESTS.parent).as_posix()}::{function.name}"
                    if key in ANY_REFUSAL_WILL_DO:
                        continue
                    found.append(f"{key} (line {node.lineno})")
    return found


def test_every_domain_refusal_a_test_expects_is_named():
    assert unnamed_refusals() == []


def test_the_guard_sees_a_bare_raises(tmp_path, monkeypatch):
    """The guard is not vacuous: a bare one in a test file is reported."""
    sample = tmp_path / "tests" / "test_sample.py"
    sample.parent.mkdir()
    sample.write_text(
        "import pytest\n"
        "from app.core.errors import DomainError\n\n"
        "def test_x():\n"
        "    with pytest.raises(DomainError):\n"
        "        pass\n\n"
        "def test_y():\n"
        "    with pytest.raises(DomainError, match='x'):\n"
        "        pass\n\n"
        "def test_z():\n"
        "    with pytest.raises(DomainError) as info:\n"
        "        pass\n"
        "    assert 'x' in str(info.value)\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(__import__(__name__, fromlist=["TESTS"]), "TESTS", sample.parent)
    assert unnamed_refusals() == ["tests/test_sample.py::test_x (line 5)"]


def test_the_allow_list_carries_its_reasons():
    assert all(reason.strip() for reason in ANY_REFUSAL_WILL_DO.values())
