"""An approved MCP write says so in the audit trail and in the history (docs/adr/0180, 0189).

Every business audit row the canonical service writes inside a commit carries
`auth_method = mcp`, `auth_assurance = individual_mfa`, the token's account as
the authenticated principal, no persona, and the proposal's correlation id —
and the history pages read «ChatGPT kaudu, isiklikult kinnitatud» beside the
name. The write trail itself (`McpWriteEvent`, `McpWriteApproval`) is
append-only in the database.
"""

from __future__ import annotations

import io
import uuid
from datetime import timedelta

import pytest
from django.core.management import call_command
from django.db import connection, transaction
from django.db.utils import DatabaseError
from django.test import RequestFactory
from django.urls import reverse
from django.utils import timezone

from app.audit.enums import AuthAssurance, AuthMethod, ChangeEventType
from app.audit.models import ChangeEvent
from app.audit.provenance import (
    MCP_QUALIFIER,
    actor_label,
    actor_qualifier,
    current_provenance,
    mcp_write_context,
    request_context,
)
from app.core.errors import ImmutableRecordError
from app.mcp_writes import proposals
from app.mcp_writes.commit import commit
from app.mcp_writes.models import (
    ApprovalMethod,
    McpWriteApproval,
    McpWriteEvent,
    McpWriteProposal,
    ProposalStatus,
)
from tests import factories
from tests import test_mcp_write_proposals_support as support
from tests.gate import PASSWORD, apply_shared_gate

pytestmark = pytest.mark.django_db


@pytest.fixture(autouse=True)
def writes_on(settings):
    support.enable(settings)


@pytest.fixture
def executed(monkeypatch):
    support.register(monkeypatch)
    support.install_access(monkeypatch)
    user = factories.UserFactory()
    matter = factories.MatterFactory(owner=user, title="Vana pealkiri")
    _grant, _raw, access = support.writer(user)
    answer = proposals.prepare(
        access=access,
        user=user,
        operation_name="test_rename_matter",
        raw_arguments={"matter": str(matter.pk), "title": "Uus pealkiri"},
    )
    proposal = McpWriteProposal.objects.get(pk=answer["proposal"]["id"])
    now = timezone.now()
    McpWriteApproval.objects.create(
        proposal=proposal,
        user=user,
        grant=proposal.grant,
        arguments_digest=proposal.arguments_digest,
        approved_at=now,
        expires_at=now + timedelta(minutes=10),
        method=ApprovalMethod.TOTP,
    )
    McpWriteProposal.objects.filter(pk=proposal.pk).update(status=ProposalStatus.APPROVED)
    assert commit(access=access, user=user, proposal_id=str(proposal.pk))["status"] == "executed"
    proposal.refresh_from_db()
    return {"user": user, "matter": matter, "proposal": proposal}


def test_the_business_audit_row_names_the_approved_principal_and_no_persona(executed):
    row = ChangeEvent.objects.get(
        matter=executed["matter"], event_type=ChangeEventType.MATTER_TITLE_CHANGED
    )
    assert row.auth_method == AuthMethod.MCP == "mcp"
    assert row.auth_assurance == AuthAssurance.INDIVIDUAL_MFA
    assert row.authenticated_principal == executed["user"]
    assert row.acting_persona is None
    assert row.actor == executed["user"]
    assert row.correlation_id == executed["proposal"].correlation_id
    assert row.actor_qualifier == MCP_QUALIFIER == "ChatGPT kaudu, isiklikult kinnitatud"


def test_the_write_events_share_the_proposals_correlation_id(executed):
    ids = set(
        McpWriteEvent.objects.filter(proposal=executed["proposal"]).values_list(
            "correlation_id", flat=True
        )
    )
    assert ids == {executed["proposal"].correlation_id}


def test_the_matter_history_page_says_it_came_through_chatgpt(executed, client, settings):
    apply_shared_gate(settings)
    reader = factories.UserFactory()
    assert client.post(reverse("accounts:shared_gate"), {"password": PASSWORD}).status_code == 302
    assert client.post(reverse("accounts:act_as"), {"user_id": str(reader.pk)}).status_code == 302

    page = client.get(reverse("matters:matter_changes", kwargs={"pk": executed["matter"].pk}))

    assert page.status_code == 200
    assert f"({MCP_QUALIFIER})" in page.content.decode()


def test_the_qualifier_and_label_for_each_method():
    assert actor_qualifier(AuthMethod.MCP) == MCP_QUALIFIER
    assert actor_label("Mari Maasikas", AuthMethod.MCP) == f"Mari Maasikas ({MCP_QUALIFIER})"
    assert actor_qualifier(AuthMethod.SHARED_GATE) == "valitud ühisparooliga"
    assert actor_qualifier(AuthMethod.SYSTEM) == ""
    assert actor_qualifier("") == ""


def test_the_context_is_mcp_inside_and_what_it_was_outside():
    user = factories.UserFactory()
    correlation = uuid.uuid4()
    assert current_provenance().method == AuthMethod.SYSTEM
    with mcp_write_context(user, correlation):
        inside = current_provenance()
        # Even if a request context were open around it, the approved write's
        # principal is who acted.
        with request_context(RequestFactory().get("/")):
            nested = current_provenance()
    assert inside.method == AuthMethod.MCP and inside.principal == user
    assert inside.persona is None and inside.correlation_id == correlation
    assert nested.method == AuthMethod.MCP and nested.principal == user
    assert current_provenance().method == AuthMethod.SYSTEM
    assert current_provenance().correlation_id is None


def test_the_context_refuses_to_run_without_a_principal():
    with pytest.raises(ValueError, match="verified principal"):
        with mcp_write_context(None, uuid.uuid4()):
            pass  # pragma: no cover


def test_the_event_and_the_approval_are_append_only_in_python_and_the_database(executed):
    event = McpWriteEvent.objects.filter(proposal=executed["proposal"]).first()
    approval = McpWriteApproval.objects.get(proposal=executed["proposal"])
    for row in (event, approval):
        with pytest.raises(ImmutableRecordError):
            row.save()
        with pytest.raises(ImmutableRecordError):
            row.delete()
    for table in ("mcp_writes_mcpwriteevent", "mcp_writes_mcpwriteapproval"):
        with pytest.raises(DatabaseError), transaction.atomic(), connection.cursor() as cursor:
            cursor.execute(f"UPDATE {table} SET created_at = created_at")  # noqa: S608
        with pytest.raises(DatabaseError), transaction.atomic(), connection.cursor() as cursor:
            cursor.execute(f"DELETE FROM {table}")  # noqa: S608


def test_the_operator_command_lists_and_shows_without_content(executed):
    out = io.StringIO()
    call_command("mcp_writes", "list", stdout=out)
    listing = out.getvalue()
    assert str(executed["proposal"].pk) in listing and "EXECUTED" in listing

    out = io.StringIO()
    call_command("mcp_writes", "show", str(executed["proposal"].pk), stdout=out)
    shown = out.getvalue()
    for word in ("PREPARED", "COMMITTED", "totp", str(executed["proposal"].correlation_id)):
        assert word in shown
    assert "Uus pealkiri" not in shown and "Vana pealkiri" not in shown
    assert "Uus pealkiri" not in listing


def test_the_operator_command_filters_by_account_and_status(executed):
    out = io.StringIO()
    call_command(
        "mcp_writes", "list", "--user", executed["user"].upn, "--status", "PENDING", stdout=out
    )
    assert out.getvalue().strip() == "No MCP write proposals."
