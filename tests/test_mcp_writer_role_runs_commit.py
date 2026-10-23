"""Agent C's commit tests, run as the writer role.

Collected again AS the writer role; see tests/test_mcp_writer_role_runs_operations.py.
"""

from __future__ import annotations

import pytest

from tests.test_mcp_writer_role_runs_operations import as_writer_role, calls, collect_into

LOADED = collect_into(globals(), "tests.test_mcp_write_commit")


@pytest.fixture(autouse=True)
def _as_writer_role(as_writer_role: None) -> None:
    return None


def test_zz_mcp_write_commit_really_ran_as_the_writer_role() -> None:
    if not LOADED:
        pytest.skip("INTEGRATION: tests/test_mcp_write_commit.py is not on this branch")
    assert calls(__name__, "gateway.commit") > 0, (
        __name__,
        calls(__name__, "gateway.prepare"),
        calls(__name__, "gateway.commit"),
    )


__all__ = ["as_writer_role"]
