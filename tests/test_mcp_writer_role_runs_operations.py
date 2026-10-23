"""The write operations' and the gateway's own test suites, run again AS the writer role.

Agent B's tests (`tests/test_mcp_write_operations_*.py`) drive every operation
through prepare → lock → state → execute; agent C's (`test_mcp_write_proposals`,
`test_mcp_write_commit`, `test_mcp_write_gateway`, `test_mcp_writes_e2e`) drive
the gateway's prepare and commit — proposals, events, security audit rows —
end to end. Here the same test functions are collected a second time, with one
difference: every call into an operation's `prepare`, `lock`, `state` and
`execute`, and into the gateway's `proposals.prepare` and `commit.commit`, runs
under
`SET ROLE juristid_mcp_writer_<random>`, a role created by
`app.mcp_writes.db_role.role_sql` inside the test's own transaction (roles are
transactional in PostgreSQL, so it never outlives the test). PostgreSQL then
refuses any write, row lock or read the role does not hold — so a green run
proves the allow-list suffices for every case those tests cover, and a new
table a canonical service starts writing fails here by name.

Skipped where the operations or their tests are not on the branch.
"""

from __future__ import annotations

import functools
import importlib
import secrets
import threading
from collections.abc import Callable, Iterator
from typing import Any

import pytest
from django.db import connection

from app.mcp_writes import db_role

_SUITES = (
    "tests.test_mcp_write_operations_actions",
    "tests.test_mcp_write_operations_dates",
    "tests.test_mcp_write_operations_entries",
    "tests.test_mcp_write_operations_matters",
    "tests.test_mcp_write_proposals",
    "tests.test_mcp_write_commit",
    "tests.test_mcp_write_gateway",
    "tests.test_mcp_writes_e2e",
)

#: The gateway's two entry points (agent C), by module and function. The
#: gateway calls them through the module (`proposals.prepare`,
#: `commits.commit`), so patching the attribute reaches every caller.
_GATEWAY_ENTRIES = (("app.mcp_writes.proposals", "prepare"), ("app.mcp_writes.commit", "commit"))

_loaded = []
for _name in _SUITES:
    try:
        _module = importlib.import_module(_name)
    except ImportError:
        continue
    _loaded.append(_name)
    _short = _name.rsplit(".", 1)[1].removeprefix("test_")
    for _attr, _value in vars(_module).items():
        if _attr.startswith("test_") and callable(_value):
            globals()[f"{_attr}__{_short}__as_writer_role"] = _value
        elif not _attr.startswith("__") and _attr not in globals():
            # The suite's own fixtures and helpers, so its tests resolve here.
            globals()[_attr] = _value

#: How many calls ran under the role; the last test asserts it is not zero.
CALLS: dict[str, int] = {}


@pytest.fixture(autouse=True)
def _operations_run_as_the_writer_role(request: Any, monkeypatch: Any) -> Iterator[None]:
    if not request.node.get_closest_marker("django_db") and "db" not in str(request.fixturenames):
        yield
        return
    from app.mcp_writes.operations import REGISTRY

    created: dict[str, str] = {}
    depth = threading.local()

    def role() -> str:
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
        return created["name"]

    def as_role(function: Callable[..., Any], label: str) -> Callable[..., Any]:
        @functools.wraps(function)
        def run(*args: Any, **kwargs: Any) -> Any:
            outermost = not getattr(depth, "n", 0)
            if outermost:
                name = role()
                with connection.cursor() as cursor:
                    cursor.execute(f'SET ROLE "{name}"')
            depth.n = getattr(depth, "n", 0) + 1
            CALLS[label] = CALLS.get(label, 0) + 1
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
            monkeypatch.setattr(cls, method, as_role(getattr(cls, method), method))
    for module_name, function_name in _GATEWAY_ENTRIES:
        try:
            module = importlib.import_module(module_name)
        except ImportError:
            continue
        if callable(getattr(module, function_name, None)):
            label = f"gateway.{function_name}"
            monkeypatch.setattr(
                module, function_name, as_role(getattr(module, function_name), label)
            )
    yield
    if "name" in created and not connection.in_atomic_block:
        # A transactional test committed the role; drop it (the cluster is shared).
        with connection.cursor() as cursor:
            cursor.execute(f'DROP OWNED BY "{created["name"]}"')
            cursor.execute(f'DROP ROLE "{created["name"]}"')


def test_zz_the_operations_really_ran_as_the_writer_role() -> None:
    if not _loaded:
        pytest.skip("INTEGRATION: the operation and gateway suites are not on this branch")
    assert CALLS.get("execute", 0) > 0 and CALLS.get("prepare", 0) > 0, CALLS
    if "tests.test_mcp_write_commit" in _loaded:
        assert CALLS.get("gateway.commit", 0) > 0, CALLS
