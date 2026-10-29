"""Security review 2026-10-29, assessor D — finding D-02 (P2, CONFIRMED).

`web` and the four workers connect to PostgreSQL as the role the `db` service
was initialised with, which the official image makes the cluster superuser.
The MCP roles were checked at start-up for exactly this (E047, E050); the
application's own connection was not. `check_application_database_role`
(juristid.W059) now reads the connected role's attributes on real data and
warns while they include SUPERUSER, CREATEROLE, BYPASSRLS or REPLICATION.

A warning, not an error, until the owner has split the role on the live
cluster (the review report §8); these tests pin the warning's shape and its
silences. The local and CI test databases are reached as whatever role the
developer has — usually a superuser — which is what makes the warning
reproducible here without a fixture.
"""

from __future__ import annotations

import pytest
from django.db import connection
from django.test import override_settings

from app.core.checks import check_application_database_role

pytestmark = pytest.mark.django_db


def _connected_as_a_privileged_role() -> bool:
    with connection.cursor() as cursor:
        cursor.execute(
            "SELECT rolsuper OR rolcreaterole OR rolbypassrls OR rolreplication "
            "FROM pg_roles WHERE rolname = current_user"
        )
        return bool(cursor.fetchone()[0])


def test_silent_without_real_data() -> None:
    with override_settings(REAL_DATA_ALLOWED=False):
        assert check_application_database_role(databases=["default"]) == []


def test_silent_when_no_database_is_named() -> None:
    with override_settings(REAL_DATA_ALLOWED=True):
        assert check_application_database_role(databases=None) == []
        assert check_application_database_role() == []


def test_silent_in_the_mcp_processes_which_have_their_own_checks() -> None:
    with override_settings(REAL_DATA_ALLOWED=True, JURISTID_PROCESS_ROLE="mcp"):
        assert check_application_database_role(databases=["default"]) == []
    with override_settings(REAL_DATA_ALLOWED=True, JURISTID_PROCESS_ROLE="mcp_writer"):
        assert check_application_database_role(databases=["default"]) == []


def test_warns_on_real_data_while_the_role_is_privileged() -> None:
    privileged = _connected_as_a_privileged_role()
    with override_settings(REAL_DATA_ALLOWED=True, JURISTID_PROCESS_ROLE=""):
        problems = check_application_database_role(databases=["default"])
    if not privileged:
        assert problems == []
        return
    assert [problem.id for problem in problems] == ["juristid.W059"]
    (problem,) = problems
    assert problem.is_serious() is False, "a warning until the owner splits the role"
    assert "connected to PostgreSQL as role" in problem.msg
    assert any(word in problem.msg for word in ("SUPERUSER", "CREATEROLE", "BYPASSRLS"))
    assert "REASSIGN OWNED BY" in (problem.hint or "")
    assert "password" not in problem.msg.lower(), "names only, never a value"
