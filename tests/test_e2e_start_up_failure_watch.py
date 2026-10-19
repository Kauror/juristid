"""The browser suite's start-up error watch, held without a browser (JRF-03).

`e2e/conftest.py::no_feature_fails_to_start` fails a browser test whose pages
log a `safeInit` failure. Opting out is a marker, and only the file that makes
start-ups throw on purpose may carry it: a marker anywhere else would be a test
quietly excused from the watch.
"""

from __future__ import annotations

import ast
from pathlib import Path

from django.conf import settings

ROOT = Path(settings.BASE_DIR)
E2E = ROOT / "e2e"
MARKER = "start_up_failure_injected"
ALLOWED = {"test_startup_failure_isolation.py"}


def _marker_users() -> set[str]:
    users = set()
    for path in sorted(E2E.glob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, ast.Attribute) and node.attr == MARKER:
                users.add(path.name)
    return users


def test_only_the_injection_file_opts_out():
    assert _marker_users() == ALLOWED


def test_the_marker_is_registered():
    text = (ROOT / "pyproject.toml").read_text(encoding="utf-8")
    assert f'"{MARKER}:' in text


def test_the_watch_is_autouse_and_reads_the_safe_init_prefix():
    source = (E2E / "conftest.py").read_text(encoding="utf-8")
    tree = ast.parse(source)
    watch = next(
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.FunctionDef) and node.name == "no_feature_fails_to_start"
    )
    decorator = ast.unparse(watch.decorator_list[0])
    assert decorator == "pytest.fixture(autouse=True)"
    assert 'START_UP_FAILURE_PREFIX = "juristid: "' in source
    for script in ("app.js", "ux.js"):
        js = (ROOT / "static" / "js" / script).read_text(encoding="utf-8")
        assert 'console.error("juristid: " + name + " failed"' in js, script
