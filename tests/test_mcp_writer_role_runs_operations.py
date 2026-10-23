"""The write operations' and the gateway's own test suites, run again AS the writer role.

Agent B's tests (`tests/test_mcp_write_operations_*.py`) drive every operation
through prepare → lock → state → execute; agent C's (`test_mcp_write_proposals`,
`test_mcp_write_commit`, `test_mcp_write_gateway`, `test_mcp_writes_e2e`) drive
the gateway's prepare and commit — proposals, events, security audit rows —
end to end. Their test functions are collected a second time, with one
difference: every call into an operation's `prepare`, `lock`, `state` and
`execute`, and into the gateway's `proposals.prepare` and `commit.commit`, runs
under `SET ROLE juristid_mcp_writer_<random>`, a role made by
`app.mcp_writes.db_role.role_sql`. PostgreSQL then refuses any write, row lock
or read the role does not hold — so a green run proves the allow-list suffices
for every case those tests cover, and a table a canonical service starts
writing fails here by name.

This module re-collects B's four operation suites (they share no fixtures).
Each of C's suites has its own module (`test_mcp_writer_role_runs_<suite>.py`),
because their autouse fixtures — stand-in verifiers, a deployment's settings —
must apply to their own tests only. `collect_into` and `as_writer_role` are the
shared machinery.

In a non-transactional test the role is created inside the test's transaction
and rolled back with it; in a transactional one (the e2e suite, whose gateway
runs in a worker thread on its own connection) it is committed under a random
name and dropped afterwards — the local cluster is shared.
"""

from __future__ import annotations

import functools
import importlib
import secrets
import sys
import threading
from collections.abc import Callable, Iterator
from typing import Any

import pytest
from django.db import connection

from app.mcp_writes import db_role

#: The gateway's two entry points (agent C). The gateway calls them through
#: the module (`proposals.prepare`, `commits.commit`), so patching the
#: attribute reaches every caller.
GATEWAY_ENTRIES = (("app.mcp_writes.proposals", "prepare"), ("app.mcp_writes.commit", "commit"))

#: How many calls ran under the role, by "<test module>:<entry point>".
CALLS: dict[str, int] = {}


def calls(module: str, label: str) -> int:
    return CALLS.get(f"{module}:{label}", 0)


def collect_into(namespace: dict[str, Any], *suites: str) -> list[str]:
    """Copy the suites' tests (renamed) and their fixtures and helpers into ``namespace``."""
    loaded = []
    for name in suites:
        try:
            module = importlib.import_module(name)
        except ImportError:
            continue
        loaded.append(name)
        short = name.rsplit(".", 1)[1].removeprefix("test_")
        for attr, value in vars(module).items():
            if attr.startswith("test_") and callable(value):
                namespace[f"{attr}__{short}__as_writer_role"] = value
            elif not attr.startswith("__") and attr not in namespace:
                namespace[attr] = value
    return loaded


@pytest.fixture
def as_writer_role(request: Any, monkeypatch: Any) -> Iterator[None]:
    """Every operation method and gateway entry runs as a fresh writer role."""
    if not request.node.get_closest_marker("django_db"):
        yield
        return
    from app.mcp_writes.operations import REGISTRY

    here = request.module.__name__
    created: dict[str, str] = {}
    lock = threading.Lock()
    depth = threading.local()

    def role() -> str:
        with lock:
            if "name" not in created:
                name = f"juristid_mcp_writer_o{secrets.token_hex(4)}"
                with connection.cursor() as cursor:
                    for statement in db_role.role_sql(
                        role=name,
                        owner=connection.settings_dict["USER"],
                        database=connection.settings_dict["NAME"],
                        allowlist=db_role.existing_allowlist(cursor),
                        withheld=db_role.withheld_existing(cursor),
                    ):
                        cursor.execute(statement)
                created["name"] = name
                created["committed"] = "" if connection.in_atomic_block else "yes"
            return created["name"]

    def wrap(function: Callable[..., Any], label: str) -> Callable[..., Any]:
        @functools.wraps(function)
        def run(*args: Any, **kwargs: Any) -> Any:
            outermost = not getattr(depth, "n", 0)
            if outermost:
                name = role()
                with connection.cursor() as cursor:
                    cursor.execute(f'SET ROLE "{name}"')
            depth.n = getattr(depth, "n", 0) + 1
            key = f"{here}:{label}"
            CALLS[key] = CALLS.get(key, 0) + 1
            try:
                return function(*args, **kwargs)
            finally:
                depth.n -= 1
                if outermost:
                    # After a refused statement the subtransaction is aborted
                    # and its rollback restores the role; RESET has nothing to do.
                    try:
                        with connection.cursor() as cursor:
                            cursor.execute("RESET ROLE")
                    except Exception:  # noqa: S110 - see above
                        pass

        return run

    seen: set[type] = set()
    for operation in REGISTRY.values():
        cls = type(operation)
        if cls in seen:
            continue
        seen.add(cls)
        for method in ("prepare", "lock", "state", "execute"):
            monkeypatch.setattr(cls, method, wrap(getattr(cls, method), method))
    for module_name, function_name in GATEWAY_ENTRIES:
        try:
            module = importlib.import_module(module_name)
        except ImportError:
            continue
        original = getattr(module, function_name, None)
        if callable(original):
            wrapped = wrap(original, f"gateway.{function_name}")
            monkeypatch.setattr(module, function_name, wrapped)
            # A test module that imported the function by name holds its own
            # reference (`from app.mcp_writes.commit import commit`).
            for loaded in list(sys.modules.values()):
                name = getattr(loaded, "__name__", "")
                if name.startswith("tests.") and getattr(loaded, function_name, None) is original:
                    monkeypatch.setattr(loaded, function_name, wrapped)
    yield
    if created.get("committed"):
        try:
            with connection.cursor() as cursor:
                cursor.execute(f'DROP OWNED BY "{created["name"]}"')
                cursor.execute(f'DROP ROLE "{created["name"]}"')
        except Exception:  # noqa: S110 - a role still in use is dropped by a later run
            pass


# -- B's operation suites --------------------------------------------------------

LOADED = collect_into(
    globals(),
    "tests.test_mcp_write_operations_actions",
    "tests.test_mcp_write_operations_dates",
    "tests.test_mcp_write_operations_entries",
    "tests.test_mcp_write_operations_matters",
)


@pytest.fixture(autouse=True)
def _as_writer_role(as_writer_role: None) -> None:
    return None


def test_zz_the_operations_really_ran_as_the_writer_role() -> None:
    if not LOADED:
        pytest.skip("INTEGRATION: agent B's operation suites are not on this branch")
    assert calls(__name__, "execute") > 0 and calls(__name__, "prepare") > 0, CALLS
