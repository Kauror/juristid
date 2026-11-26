"""Security/resilience review 2026-10-29, assessor C, workstream 1: the approval page's persona.

Under ``AUTH_MODE=shared_gate`` a persona is a *choice* behind one department
password (docs/adr/0185 §3, app/accounts/shared_gate.py). The approval page
(docs/adr/0189) binds «Kinnita» to the grant's own authenticator code, so a
colleague who picks the proposal owner's persona cannot approve. These tests
record what that colleague CAN do with the proposal's link and no code:
decline a pending proposal, and withdraw an approval that has not been
committed yet — both answered as the owner's own decision, with the owner's
persona in the event, and with no way for the owner or the operator to tell
the two apart afterwards.

Every test PASSED on main 359e34bf: this is the design, not a defect. It is
listed as owner question Q-3 in WS1C-FINDINGS.md (whether declining and
withdrawing should also ask for the code, or whether the audit event should at
least carry the gate session so two persona choices can be told apart).

**Since r96 (SEC-009, docs/adr/0234)** «Kinnita» carries the binding the page
showed and approves *and saves* in one step, so there is no approved-but-unsaved
proposal left to withdraw: the withdraw half of Q-3 is gone, and the last test
now records that a colleague's «tuhista» changes nothing. Declining without a
code is unchanged (still Q-3).
"""

from __future__ import annotations

import pytest
from django.test import Client

from app.mcp_writes import proposals
from app.mcp_writes.models import (
    McpWriteApproval,
    McpWriteEvent,
    McpWriteEventType,
    McpWriteProposal,
    ProposalStatus,
)
from tests import factories
from tests import test_mcp_write_proposals_support as support
from tests.test_mcp_write_approval import binding_on
from tests.gate import PASSWORD, apply_shared_gate

pytestmark = pytest.mark.django_db

GATE = "/konto/varav/"
SWITCH = "/konto/kasutaja/vaheta/"


@pytest.fixture(autouse=True)
def behind_the_gate(settings):
    apply_shared_gate(settings)
    support.enable(settings)
    settings.MCP_WRITE_REVERIFY_SECONDS = 0  # production


@pytest.fixture
def world(monkeypatch):
    support.register(monkeypatch)
    support.install_access(monkeypatch)
    user = factories.UserFactory()
    matter = factories.MatterFactory(owner=user, title="Pakendiseaduse muutmine")
    grant, _raw, access = support.writer(user)
    return {"user": user, "matter": matter, "grant": grant, "access": access}


def prepared(world) -> McpWriteProposal:
    answer = proposals.prepare(
        access=world["access"],
        user=world["user"],
        operation_name="test_rename_matter",
        raw_arguments={"matter": str(world["matter"].pk), "title": "Uus pealkiri"},
    )
    assert isinstance(answer, dict), answer
    return McpWriteProposal.objects.get(pk=answer["proposal"]["id"])


def url(proposal) -> str:
    return f"/mcp-ligipaas/muudatus/{proposal.pk}/"


def another_browser_as(user) -> Client:
    """A second gate session — another colleague's browser — choosing ``user``."""
    client = Client()
    assert client.post(GATE, {"password": PASSWORD}).status_code == 302
    assert client.post(SWITCH, {"user_id": str(user.pk)}).status_code == 302
    return client


def test_a_second_gate_session_in_the_owners_persona_cannot_approve_without_the_code(world):
    proposal = prepared(world)
    stranger = another_browser_as(world["user"])
    # The binding the page shows the stranger too (format 2): only the code is missing.
    response = stranger.post(
        url(proposal),
        {"otsus": "kinnita", "sidumine": binding_on(stranger, proposal), "kood": "000000"},
    )
    assert response.status_code == 400
    proposal.refresh_from_db()
    assert proposal.status == ProposalStatus.PENDING
    assert not McpWriteApproval.objects.filter(proposal=proposal).exists()


def test_a_second_gate_session_in_the_owners_persona_declines_with_no_code_q3(world):
    proposal = prepared(world)
    stranger = another_browser_as(world["user"])
    response = stranger.post(url(proposal), {"otsus": "keeldu"})
    assert response.status_code == 200
    proposal.refresh_from_db()
    assert proposal.status == ProposalStatus.DECLINED
    event = McpWriteEvent.objects.get(proposal=proposal, event_type=McpWriteEventType.DECLINED)
    # The event names the owner — the persona chosen — and nothing about the session.
    assert event.user_id == world["user"].pk
    assert "session" not in event.detail and "gate" not in event.detail


def test_a_second_gate_session_in_the_owners_persona_cannot_withdraw_a_saved_approval_q3(world):
    proposal = prepared(world)
    owner = another_browser_as(world["user"])
    approved = owner.post(
        url(proposal),
        {"otsus": "kinnita", "sidumine": binding_on(owner, proposal), "kood": support.GOOD_CODE},
    )
    assert approved.status_code == 200, approved.content.decode()[:300]
    proposal.refresh_from_db()
    # Format 2: approving is saving — nothing waits to be withdrawn.
    assert proposal.status == ProposalStatus.EXECUTED
    stranger = another_browser_as(world["user"])
    stranger.post(url(proposal), {"otsus": "tuhista"})
    proposal.refresh_from_db()
    assert proposal.status == ProposalStatus.EXECUTED
    assert proposal.failure_code != "withdrawn"
