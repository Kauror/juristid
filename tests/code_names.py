"""What a piece of application code *names*, read from its syntax tree (HR-19).

Some rules are about the absence of a dependency: «the department page does not
name `response_deadline`», «extraction does not suspend archive indexing». A
substring search over `inspect.getsource` states those rules badly. It fails when
a comment or a docstring mentions the name — which is how a module explains why it
does *not* use something — and its exact-line variants fail on an alias import or
a reordered import list.

`code_names` reads the code instead. Comments never reach the tree, and
docstrings are dropped, so only what runs is searched:

- identifiers (`Name`), attribute names (`obj.name`) and every name an import
  brings in or binds (`from m import name as alias` gives `m`, `name`, `alias`);
- keyword arguments and string constants, split on the ORM's `__` so that
  `filter(response_deadline__lte=…)` and `values("response_deadline")` count;
- definitions (functions, classes, arguments).

The result is a set of plain names. Ask it whether a name is in it; do not assert
on its exact contents, which change with every harmless refactor.
"""

from __future__ import annotations

import ast
import inspect
import textwrap
from typing import Any


def _is_docstring(node: ast.AST) -> bool:
    return isinstance(node, ast.Expr) and isinstance(node.value, ast.Constant)


def _without_docstrings(tree: ast.AST) -> None:
    for node in ast.walk(tree):
        body = getattr(node, "body", None)
        if isinstance(body, list) and body and _is_docstring(body[0]):
            # Keep the block syntactically valid; `pass` names nothing.
            node.body = body[1:] or [ast.Pass()]  # type: ignore[attr-defined]


def _orm_parts(text: str) -> set[str]:
    return {part for part in text.split("__") if part}


def code_names(obj: Any) -> set[str]:
    """Every name the code of ``obj`` (a module, class or function) uses or defines."""
    tree = ast.parse(textwrap.dedent(inspect.getsource(obj)))
    _without_docstrings(tree)
    names: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Name):
            names.add(node.id)
        elif isinstance(node, ast.Attribute):
            names.add(node.attr)
        elif isinstance(node, ast.alias):
            names.update(node.name.split("."))
            if node.asname:
                names.add(node.asname)
        elif isinstance(node, ast.ImportFrom) and node.module:
            names.update(node.module.split("."))
        elif isinstance(node, ast.keyword) and node.arg:
            names.update(_orm_parts(node.arg))
        elif isinstance(node, ast.Constant) and isinstance(node.value, str):
            names.update(_orm_parts(node.value))
        elif isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef | ast.ClassDef):
            names.add(node.name)
        elif isinstance(node, ast.arg):
            names.add(node.arg)
    return names
