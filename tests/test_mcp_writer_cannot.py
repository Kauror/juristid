"""What the write gateway's database role can no longer do (SEC-009, docs/adr/0234 §2).

Real PostgreSQL privileges, not mocks: each case creates the least-privilege
writer role with `app.mcp_writes.db_role.role_sql` *inside the test's
transaction* (roles are transactional, so it is rolled back with everything
else and never exists outside this test — the local cluster is shared), then
runs the attack in a savepoint under ``SET ROLE``. PostgreSQL's own refusal is
the assertion; the savepoint's rollback restores the role.

Covered: the gateway cannot modify an approved or pending proposal's effective
action, substitute the preview, change the target, extend the expiry, change
the grant, client or token-family identity, rewrite the approved state, forge
an approval, insert an already-approved proposal, or make a business write by
any path — the SQL tables the canonical services write, or an operation's
``execute`` called directly. And the integrity triggers hold for every role,
the application's own included.
"""

# ruff: noqa: S608 - every statement here is built from fixed test table and column names

from __future__ import annotations

import secrets
import uuid
from collections.abc import Iterator
from datetime import timedelta

import pytest
from django.db import DatabaseError, connection, transaction
from django.utils import timezone

from app.mcp_writes import db_role, proposals
from app.mcp_writes.models import McpWriteApproval, McpWriteProposal, ProposalStatus
from tests import factories
from tests import test_mcp_write_proposals_support as support

pytestmark = pytest.mark.django_db

PROPOSAL = "mcp_writes_mcpwriteproposal"
DENIED = "permission denied"


@pytest.fixture(autouse=True)
def enabled(settings):
    support.enable(settings)


@pytest.fixture
def op(monkeypatch):
    return support.register(monkeypatch)


@pytest.fixture
def world(op, monkeypatch):
    support.install_access(monkeypatch)
    user = factories.UserFactory()
    matter = factories.MatterFactory(owner=user, title="Algne pealkiri")
    grant, _raw, access = support.writer(user)
    answer = proposals.prepare(
        access=access,
        user=user,
        operation_name=op.name,
        raw_arguments={"matter": str(matter.pk), "title": "Uus pealkiri"},
    )
    assert isinstance(answer, dict), answer
    proposal = McpWriteProposal.objects.get(pk=answer["proposal"]["id"])
    return {"user": user, "matter": matter, "grant": grant, "proposal": proposal}


@pytest.fixture
def role() -> Iterator[str]:
    """The writer role, made by this release's `role_sql`, inside the test's transaction."""
    name = f"juristid_mcp_writer_c{secrets.token_hex(4)}"
    with connection.cursor() as cursor:
        for statement in db_role.role_sql(
            role=name,
            owner=connection.settings_dict["USER"],
            database=connection.settings_dict["NAME"],
            allowlist=db_role.existing_allowlist(cursor),
            withheld=db_role.withheld_existing(cursor),
        ):
            cursor.execute(statement)
    yield name


def refusal_as(role: str | None, sql: str, params: list | None = None) -> str:
    """PostgreSQL's refusal of ``sql`` run as ``role`` (the owner when None); fails if it runs."""
    try:
        with transaction.atomic():
            with connection.cursor() as cursor:
                if role:
                    cursor.execute(f'SET ROLE "{role}"')
                cursor.execute(sql, params or [])
    except DatabaseError as error:
        return str(error)
    pytest.fail(f"PostgreSQL ran it: {sql}")


def runs_as(role: str, sql: str, params: list | None = None) -> None:
    with transaction.atomic(), connection.cursor() as cursor:
        cursor.execute(f'SET ROLE "{role}"')
        cursor.execute(sql, params or [])
        cursor.execute("RESET ROLE")


# -- a proposal the gateway stored cannot be changed by it ------------------------------


@pytest.mark.parametrize(
    ("column", "value"),
    [
        ("canonical_arguments", '{"matter_id": "x", "title": "Muu pealkiri"}'),
        ("arguments", '{"matter": "x", "title": "Muu pealkiri"}'),
        ("preview", '{"summary": "Midagi süütut"}'),
        ("target_matter_id", str(uuid.UUID(int=7))),
        ("target_record_id", "other"),
        ("expires_at", "2099-01-01T00:00:00Z"),
        ("client_id", "https://attacker.example/client"),
        ("token_family_id", str(uuid.UUID(int=9))),
        ("arguments_digest", "0" * 64),
        ("state", '{"title": "Algne pealkiri"}'),
        ("status", "APPROVED"),
        ("status", "EXECUTED"),
        ("result", '{"record_id": "forged"}'),
    ],
)
def test_the_gateway_cannot_update_any_column_of_a_stored_proposal(world, role, column, value):
    message = refusal_as(
        role,
        f"UPDATE {PROPOSAL} SET {column} = %s WHERE id = %s",
        [value, world["proposal"].pk],
    )
    assert DENIED in message and PROPOSAL in message


def test_the_gateway_cannot_change_the_grant_of_a_proposal(world, role):
    other = support.active_grant(factories.UserFactory())
    message = refusal_as(
        role, f"UPDATE {PROPOSAL} SET grant_id = %s WHERE id = %s", [other.pk, world["proposal"].pk]
    )
    assert DENIED in message


def test_the_gateway_cannot_delete_a_proposal(world, role):
    assert DENIED in refusal_as(
        role, f"DELETE FROM {PROPOSAL} WHERE id = %s", [world["proposal"].pk]
    )


def test_the_gateway_cannot_forge_an_approval(world, role):
    proposal = world["proposal"]
    now = timezone.now()
    message = refusal_as(
        role,
        "INSERT INTO mcp_writes_mcpwriteapproval (id, created_at, proposal_id, user_id, grant_id,"
        " arguments_digest, approved_at, expires_at, method, format_version, binding_digest,"
        " canonical_arguments, state, preview)"
        " VALUES (gen_random_uuid(), now(), %s, %s, %s, %s, %s, %s, 'totp', 2, '',"
        " '{}', '{}', '{}')",
        [proposal.pk, proposal.user_id, proposal.grant_id, proposal.arguments_digest, now, now],
    )
    assert DENIED in message and "mcp_writes_mcpwriteapproval" in message
    assert not McpWriteApproval.objects.filter(proposal=proposal).exists()


def test_the_gateway_cannot_insert_an_already_approved_or_executed_proposal(world, role):
    """INSERT is its job — but a proposal is born PENDING, whoever inserts it (the trigger)."""
    source = world["proposal"]
    for status in ("APPROVED", "EXECUTED"):
        message = refusal_as(
            role,
            f"INSERT INTO {PROPOSAL} (id, created_at, updated_at, format_version, user_id,"
            " grant_id, token_family_id, client_id, operation, arguments, canonical_arguments,"
            " arguments_digest, target_matter_id, target_record_type, target_record_id, state,"
            " preview, status, expires_at, executed_at, result, failure_code, correlation_id)"
            " SELECT gen_random_uuid(), now(), now(), 2, user_id, grant_id, token_family_id,"
            " client_id, operation, arguments, canonical_arguments, arguments_digest,"
            " target_matter_id, target_record_type, target_record_id, state, preview, %s,"
            f" expires_at, NULL, '{{}}', '', gen_random_uuid() FROM {PROPOSAL} WHERE id = %s",
            [status, source.pk],
        )
        assert "born pending" in message


def test_the_gateway_can_still_insert_a_pending_proposal(world, role):
    """Preparing stays possible: the role is narrowed, not broken."""
    source = world["proposal"]
    runs_as(
        role,
        f"INSERT INTO {PROPOSAL} (id, created_at, updated_at, format_version, user_id,"
        " grant_id, token_family_id, client_id, operation, arguments, canonical_arguments,"
        " arguments_digest, target_matter_id, target_record_type, target_record_id, state,"
        " preview, status, expires_at, executed_at, result, failure_code, correlation_id)"
        " SELECT gen_random_uuid(), now(), now(), 2, user_id, grant_id, token_family_id,"
        " client_id, operation, arguments, canonical_arguments, arguments_digest,"
        " target_matter_id, target_record_type, target_record_id, state, preview, 'PENDING',"
        f" expires_at, NULL, '{{}}', '', gen_random_uuid() FROM {PROPOSAL} WHERE id = %s",
        [source.pk],
    )
    assert McpWriteProposal.objects.filter(user=world["user"]).count() == 2


# -- no business write by any path ----------------------------------------------------------


def _a_column(table: str) -> str:
    with connection.cursor() as cursor:
        cursor.execute(
            "SELECT column_name FROM information_schema.columns WHERE table_name = %s "
            "ORDER BY ordinal_position LIMIT 1",
            [table],
        )
        return str(cursor.fetchone()[0])


@pytest.mark.parametrize("table", db_role.FORMER_BUSINESS_WRITES)
def test_the_gateway_cannot_write_a_business_table(world, role, table):
    """Every table r94's role could write: INSERT, UPDATE and DELETE are all refused."""
    for sql in (
        f"INSERT INTO {table} SELECT * FROM {table} WHERE false",
        f"UPDATE {table} SET {_a_column(table)} = {_a_column(table)} WHERE false",
        f"DELETE FROM {table} WHERE false",
    ):
        message = refusal_as(role, sql)
        assert DENIED in message and table in message, (sql, message)


def test_an_operations_execute_called_directly_by_the_gateway_saves_nothing(world, role, op):
    """The alternate path: the canonical service itself, run as the gateway's role."""
    proposal = world["proposal"]
    try:
        with transaction.atomic():
            with connection.cursor() as cursor:
                cursor.execute(f'SET ROLE "{role}"')
            op.execute(
                user=world["user"], canonical=proposal.canonical_arguments, once_token=proposal.pk
            )
    except DatabaseError as error:
        assert DENIED in str(error)
    else:
        pytest.fail("the gateway's role ran a business write")
    world["matter"].refresh_from_db()
    assert world["matter"].title == "Algne pealkiri"


def test_the_role_check_names_a_business_grant_left_from_r94(world, role):
    """juristid.E050's check: an r94-style grant on a business table is a start-up error."""
    with connection.cursor() as cursor:
        cursor.execute(f'GRANT INSERT ON TABLE matters_entry TO "{role}"')
    with transaction.atomic(), connection.cursor() as cursor:
        cursor.execute(f'SET ROLE "{role}"')
        problems = db_role.privilege_problems_writer(cursor, expected_role=role)
        cursor.execute("RESET ROLE")
    assert any("must never write" in problem and "matters_entry" in problem for problem in problems)


def test_this_releases_role_passes_its_own_check(world, role):
    with transaction.atomic(), connection.cursor() as cursor:
        cursor.execute(f'SET ROLE "{role}"')
        problems = db_role.privilege_problems_writer(cursor, expected_role=role)
        cursor.execute("RESET ROLE")
    assert problems == []


def test_the_allow_list_cannot_be_widened_to_a_business_table():
    with pytest.raises(ValueError, match="never writable"):
        db_role.role_sql(
            role="juristid_mcp_writer_x",
            owner="owner",
            database="db",
            allowlist={**db_role.WRITE_ALLOWLIST, "matters_entry": ("INSERT",)},
        )


# -- the triggers hold for everybody, the application's own role included -------------------


def test_what_a_proposal_proposes_never_changes_even_for_the_application(world):
    proposal = world["proposal"]
    for column, value in (
        ("canonical_arguments", '{"title": "x"}'),
        ("preview", '{"summary": "x"}'),
        ("expires_at", (proposal.expires_at + timedelta(days=1)).isoformat()),
        ("client_id", "other"),
    ):
        message = refusal_as(
            None,
            f"UPDATE {PROPOSAL} SET {column} = %s WHERE id = %s",
            [value, proposal.pk],
        )
        assert "never changes" in message, column


def test_a_finished_proposal_never_changes_and_none_is_deleted(world):
    proposal = world["proposal"]
    McpWriteProposal.objects.filter(pk=proposal.pk).update(
        status=ProposalStatus.DECLINED, failure_code="declined"
    )
    message = refusal_as(
        None, f"UPDATE {PROPOSAL} SET status = 'PENDING' WHERE id = %s", [proposal.pk]
    )
    assert "finished MCP proposal never changes" in message
    assert "never deleted" in refusal_as(
        None, f"DELETE FROM {PROPOSAL} WHERE id = %s", [proposal.pk]
    )


def test_only_an_executed_proposal_carries_a_result(world):
    message = refusal_as(
        None,
        f"UPDATE {PROPOSAL} SET status = 'FAILED', result = '{{\"x\": 1}}' WHERE id = %s",
        [world["proposal"].pk],
    )
    assert "only an executed MCP proposal has a result" in message
