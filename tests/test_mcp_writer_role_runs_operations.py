"""Every write operation's own test suite, run again AS the writer database role.

Agent B's tests (`tests/test_mcp_write_operations_*.py`) drive every operation
through prepare → lock → state → execute on synthetic data. Here the same test
functions are collected a second time, with one difference: each call into an
operation's `prepare`, `lock`, `state` and `execute` runs under
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
)

_loaded = []
for _name in _SUITES:
    try:
        _module = importlib.import_module(_name)
    except ImportError:
        continue
    _loaded.append(_name)
    for _attr, _value in vars(_module).items():
        if _attr.startswith("test_") and callable(_value):
            globals()[f"{_attr}__as_writer_role"] = _value
        elif not _attr.startswith("__") and _attr not in globals():
            # The suite's own fixtures and helpers, so its tests resolve here.
            globals()[_attr] = _value

#: How many operation calls ran under the role; the last test asserts it is not zero.
CALLS: dict[str, int] = {}


@pytest.fixture(autouse=True)
def _operations_run_as_the_writer_role(request: Any, monkeypatch: Any) -> Iterator[None]:
    if not request.node.get_closest_marker("django_db") and "db" not in str(request.fixturenames):
        yield
        return
    from app.mcp_writes.operations import REGISTRY

    created: dict[str, str] = {}

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

    def as_role(method: Callable[..., Any]) -> Callable[..., Any]:
        @functools.wraps(method)
        def run(self: Any, *args: Any, **kwargs: Any) -> Any:
            name = role()
            with connection.cursor() as cursor:
                cursor.execute(f'SET ROLE "{name}"')
            CALLS[method.__name__] = CALLS.get(method.__name__, 0) + 1
            try:
                return method(self, *args, **kwargs)
            finally:
                # After a refused statement the subtransaction is aborted and
                # its rollback restores the role; RESET then has nothing to do.
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
            monkeypatch.setattr(cls, method, as_role(getattr(cls, method)))
    yield


def test_zz_the_operations_really_ran_as_the_writer_role() -> None:
    if not _loaded:
        pytest.skip("INTEGRATION: agent B's operation suites are not on this branch")
    assert CALLS.get("execute", 0) > 0 and CALLS.get("prepare", 0) > 0, CALLS
