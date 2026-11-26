"""`mcp_writes/0002`: from the r94 schema, and r94's own writes against the new one (ADR 0234 §3).

* **Upgrade from r94.** Rows written the r94 way (format 1) — a PENDING, an
  APPROVED-but-uncommitted and an EXECUTED proposal with its approval and
  events — are migrated: the two open ones are retired as EXPIRED
  (``format_retired``) with one `FORMAT_RETIRED` event each; the executed one,
  its receipt, its approval and every event are untouched.
* **App rollback against the forward schema.** After 0002, an INSERT and the
  status updates exactly as r94's code issues them (naming only r94's columns)
  still work, land as format 1, and format-2 code never approves them.
* **Reversal.** 0002 reverses cleanly and applies again.

Synthetic accounts only; the schema moves inside the test's transaction.
"""

from __future__ import annotations

import json
import uuid

import pytest
from django.db import connection
from django.db.migrations.executor import MigrationExecutor

from app.mcp_writes.models import (
    McpWriteApproval,
    McpWriteEvent,
    McpWriteEventType,
    McpWriteProposal,
    ProposalStatus,
)
from tests import factories
from tests import test_mcp_write_proposals_support as support

pytestmark = pytest.mark.django_db

BEFORE = ("mcp_writes", "0001_initial")
AFTER = ("mcp_writes", "0002_trusted_approval_and_budgets")


def _migrate_to(target: tuple[str, str]) -> None:
    with connection.cursor() as cursor:
        cursor.execute("SET CONSTRAINTS ALL IMMEDIATE")
    executor = MigrationExecutor(connection)
    executor.loader.build_graph()
    executor.migrate([target])


@pytest.fixture
def back_at_the_end():
    yield
    _migrate_to(AFTER)


def _r94_proposal(user, grant, status: str, *, result: dict | None = None) -> uuid.UUID:
    """A proposal as r94's gateway inserted it (and r94's commit moved it on)."""
    pid = uuid.uuid4()
    with connection.cursor() as cursor:
        cursor.execute(
            "INSERT INTO mcp_writes_mcpwriteproposal (id, created_at, updated_at, user_id,"
            " grant_id, token_family_id, client_id, operation, canonical_arguments,"
            " arguments_digest, target_matter_id, target_record_type, target_record_id, state,"
            " preview, status, expires_at, executed_at, result, failure_code, correlation_id)"
            " VALUES (%s, now(), now(), %s, %s, %s, 'client', 'set_matter_title',"
            " '{\"title\": \"x\"}', %s, NULL, '', '', '{}', '{\"summary\": \"s\"}', %s,"
            " now() + interval '10 minutes', %s, %s, '', %s)",
            [
                pid,
                user.pk,
                grant.pk,
                uuid.uuid4(),
                "a" * 64,
                status,
                None if result is None else "2026-11-20T10:00:00Z",
                json.dumps(result or {}),
                uuid.uuid4(),
            ],
        )
    return pid


def _r94_approval(pid: uuid.UUID, user, grant) -> None:
    with connection.cursor() as cursor:
        cursor.execute(
            "INSERT INTO mcp_writes_mcpwriteapproval (id, created_at, proposal_id, user_id,"
            " grant_id, arguments_digest, approved_at, expires_at, method, ip_address)"
            " VALUES (%s, now(), %s, %s, %s, %s, now(), now() + interval '10 minutes', 'totp',"
            " NULL)",
            [uuid.uuid4(), pid, user.pk, grant.pk, "a" * 64],
        )


def _r94_event(pid: uuid.UUID, user, event_type: str) -> None:
    with connection.cursor() as cursor:
        cursor.execute(
            "INSERT INTO mcp_writes_mcpwriteevent (id, created_at, event_type, succeeded,"
            " proposal_id, user_id, grant_id, client_id, operation, correlation_id, detail)"
            " VALUES (%s, now(), %s, true, %s, %s, NULL, '', '', NULL, '{}')",
            [uuid.uuid4(), event_type, pid, user.pk],
        )


def test_upgrade_from_r94_retires_open_proposals_and_keeps_executed_history(back_at_the_end):
    user = factories.UserFactory()
    grant = support.active_grant(user)
    _migrate_to(BEFORE)
    pending = _r94_proposal(user, grant, "PENDING")
    approved = _r94_proposal(user, grant, "APPROVED")
    _r94_approval(approved, user, grant)
    executed = _r94_proposal(user, grant, "EXECUTED", result={"record_id": "r1"})
    _r94_approval(executed, user, grant)
    for pid, kind in ((pending, "PREPARED"), (approved, "APPROVED"), (executed, "COMMITTED")):
        _r94_event(pid, user, kind)

    _migrate_to(AFTER)

    rows = {row.pk: row for row in McpWriteProposal.objects.all()}
    for pid, previous in ((pending, "PENDING"), (approved, "APPROVED")):
        assert rows[pid].status == ProposalStatus.EXPIRED
        assert rows[pid].failure_code == "format_retired" and rows[pid].format_version == 1
        [retired] = McpWriteEvent.objects.filter(
            proposal_id=pid, event_type=McpWriteEventType.FORMAT_RETIRED
        )
        assert retired.detail == {
            "code": "format_retired",
            "previous_status": previous,
            "format": 1,
        }
    assert rows[executed].status == ProposalStatus.EXECUTED
    assert rows[executed].result == {"record_id": "r1"}
    assert McpWriteApproval.objects.filter(proposal_id=executed).get().format_version == 1
    assert McpWriteEvent.objects.filter(proposal_id=executed).count() == 1  # untouched
    assert McpWriteEvent.objects.count() == 5


def test_r94_code_still_writes_against_the_forward_schema_as_format_one(back_at_the_end):
    """App rollback: what r94 inserts and updates still works, and is never format 2."""
    from app.mcp_writes import approval

    user = factories.UserFactory()
    grant = support.active_grant(user)

    pid = _r94_proposal(user, grant, "PENDING")
    with connection.cursor() as cursor:
        # r94's approval page, then r94's commit.
        cursor.execute(
            "UPDATE mcp_writes_mcpwriteproposal SET status = 'APPROVED', updated_at = now()"
            " WHERE id = %s",
            [pid],
        )
    _r94_approval(pid, user, grant)
    with connection.cursor() as cursor:
        cursor.execute(
            "UPDATE mcp_writes_mcpwriteproposal SET status = 'EXECUTED', executed_at = now(),"
            " result = '{\"record_id\": \"r2\"}', failure_code = '', updated_at = now()"
            " WHERE id = %s",
            [pid],
        )
    row = McpWriteProposal.objects.get(pk=pid)
    assert row.status == ProposalStatus.EXECUTED and row.format_version == 1

    open_one = _r94_proposal(user, grant, "PENDING")
    checked = approval.check(open_one, user, canonical_locks=False)
    assert checked.problem in {"format", "no_write_right"}  # never approvable as format 2


def test_the_migration_reverses_and_applies_again(back_at_the_end):
    _migrate_to(BEFORE)
    with connection.cursor() as cursor:
        cursor.execute(
            "SELECT count(*) FROM information_schema.columns WHERE table_name ="
            " 'mcp_writes_mcpwriteproposal' AND column_name IN ('format_version', 'arguments')"
        )
        assert cursor.fetchone()[0] == 0
        cursor.execute("SELECT to_regclass('mcp_writes_mcpwritecounter')")
        assert cursor.fetchone()[0] is None
        cursor.execute(
            "SELECT count(*) FROM pg_trigger WHERE tgname LIKE 'mcp_writes_proposal_%%_guard'"
        )
        assert cursor.fetchone()[0] == 0
    _migrate_to(AFTER)
    with connection.cursor() as cursor:
        cursor.execute(
            "SELECT count(*) FROM pg_trigger WHERE tgname LIKE 'mcp_writes_proposal_%%_guard'"
        )
        assert cursor.fetchone()[0] == 2
