"""MCP Phase 2, verified independently with real credentials (docs/adr/0189; 2026-10-23).

The suites that came with #523 mostly stand in for the authorization server
(`tests/test_mcp_write_proposals_support.install_access`) when they test the
commit and the approval page. This file closes the gaps an independent review
found, with **real** grants, authenticators, tokens and the real
`add_matter_note` operation — nothing about who may write is faked:

* a token that expired, was revoked, or was refreshed into a read-only token of
  the same family after ``revoke-write`` cannot commit an approved proposal,
  and neither can a family begun before a later ``grant-write`` — while a new
  connection after that ``grant-write`` is told the old proposal does not exist;
* the approval page refuses a proposal of the person's *earlier* grant, even
  with a right code for the current one;
* a lock or suspension of the grant that begins after a code was typed ends the
  «recent code» shortcut: the page asks for a code again and approves nothing;
* every operation that names a Matter answers ``not_found`` for a Matter the
  person may not see, at prepare and again at execute (the visibility is made
  participation-only here, because since docs/adr/0181 every business role
  reads everything — the write side must still ask, not assume).
"""

from __future__ import annotations

import contextlib
import sys
import uuid
from datetime import timedelta
from typing import Any

import pytest
from django.test import Client
from django.utils import timezone

from app.core import authorization
from app.core.authorization import Scope
from app.core.enums import Visibility
from app.matters.models import Entry, Matter
from app.mcp_access import services
from app.mcp_access.models import McpAccessGrant, McpToken
from app.mcp_access.verification import verify_access_token
from app.mcp_writes import proposals
from app.mcp_writes.commit import commit
from app.mcp_writes.models import (
    ApprovalMethod,
    McpWriteApproval,
    McpWriteProposal,
    ProposalStatus,
)
from app.mcp_writes.operations.base import OperationRefused
from app.mcp_writes.refusals import Refusal
from app.workflow.services import set_next_action as set_next_action_service
from tests import factories
from tests import mcp_access_support as support
from tests import test_mcp_write_operations_support as ops
from tests.gate import PASSWORD, apply_shared_gate

pytestmark = pytest.mark.django_db

BOTH = "juristid.read juristid.write"
NOTE = "Ministeerium lubas eelnõu uue versiooni saata novembris."


@pytest.fixture(autouse=True)
def writes_on(settings):
    support.enable(settings)
    settings.MCP_WRITES_ENABLED = True


@pytest.fixture
def moment(monkeypatch):
    return support.freeze_totp(monkeypatch)


def _connect(user: Any, moment: Any) -> tuple[McpAccessGrant, str, services.IssuedTokens]:
    """An enrolled grant with write, and a read+write connection (the step of ``moment``)."""
    grant, secret = support.enrolled(user, moment)
    services.grant_write(user=user, provenance=support.OPERATOR)
    issued = support.tokens_for(user, secret, moment, scope=BOTH)
    assert issued.scope == BOTH
    return grant, secret, issued


def _access(raw: str) -> Any:
    access = verify_access_token(raw)
    assert access is not None
    return access


def _prepare_note(user: Any, access: Any, matter: Matter) -> McpWriteProposal:
    answer = proposals.prepare(
        access=access,
        user=user,
        operation_name="add_matter_note",
        raw_arguments={
            "matter": str(matter.pk),
            "text": NOTE,
            "occurred_on": timezone.localdate().isoformat(),
        },
    )
    assert isinstance(answer, dict), answer
    return McpWriteProposal.objects.get(pk=answer["proposal"]["id"])


def _approve_row(proposal: McpWriteProposal) -> McpWriteProposal:
    """What the approval page writes once the person's code was accepted."""
    now = timezone.now()
    McpWriteApproval.objects.create(
        proposal=proposal,
        user=proposal.user,
        grant=proposal.grant,
        arguments_digest=proposal.arguments_digest,
        approved_at=now,
        expires_at=now + timedelta(minutes=10),
        method=ApprovalMethod.TOTP,
    )
    McpWriteProposal.objects.filter(pk=proposal.pk).update(status=ProposalStatus.APPROVED)
    proposal.refresh_from_db()
    return proposal


def _notes(matter: Matter) -> int:
    return Entry.objects.filter(matter=matter).count()


@pytest.fixture
def world(moment):
    user = factories.UserFactory()
    matter = factories.MatterFactory(owner=user)
    grant, secret, issued = _connect(user, moment)
    access = _access(issued.access_token)
    proposal = _approve_row(_prepare_note(user, access, matter))
    return {
        "user": user,
        "matter": matter,
        "grant": grant,
        "secret": secret,
        "issued": issued,
        "access": access,
        "proposal": proposal,
        "notes": _notes(matter),
    }


def _commit_with(world: dict[str, Any], access: Any) -> Any:
    return commit(access=access, user=world["user"], proposal_id=str(world["proposal"].pk))


def _refused_with(answer: Any, code: str) -> None:
    assert isinstance(answer, Refusal), answer
    assert answer.code == code


# -- credentials between approval and commit ---------------------------------------------


def test_the_real_chain_commits_once_when_nothing_changed(world):
    """The control for the refusals below: the same world, untouched, commits."""
    first = _commit_with(world, world["access"])
    assert first["status"] == "executed" and first["replayed"] is False
    again = _commit_with(world, world["access"])
    assert again["replayed"] is True
    assert _notes(world["matter"]) == world["notes"] + 1


def test_an_access_token_that_expired_after_approval_cannot_commit(world):
    McpToken.objects.filter(pk=world["access"].token_id).update(
        expires_at=timezone.now() - timedelta(seconds=1)
    )
    _refused_with(_commit_with(world, world["access"]), "forbidden")
    assert verify_access_token(world["issued"].access_token) is None
    assert _notes(world["matter"]) == world["notes"]
    world["proposal"].refresh_from_db()
    assert world["proposal"].status == ProposalStatus.APPROVED


def test_an_access_token_revoked_after_approval_cannot_commit(world):
    assert services.revoke_token(
        client_id=support.PUBLIC_CLIENT, token=world["issued"].refresh_token, ip_address=None
    )
    _refused_with(_commit_with(world, world["access"]), "forbidden")
    assert _notes(world["matter"]) == world["notes"]


def test_a_refreshed_token_after_revoke_write_is_read_only_and_cannot_commit(world):
    """Same person, grant, client and family — but the refresh dropped the write scope."""
    services.revoke_write(user=world["user"], reason="test", provenance=support.OPERATOR)
    refreshed = services.refresh(
        client_id=support.PUBLIC_CLIENT,
        refresh_token=world["issued"].refresh_token,
        resource=support.RESOURCE,
        scope="",
        ip_address=None,
    )
    assert isinstance(refreshed, services.IssuedTokens), refreshed
    assert refreshed.scope == "juristid.read"
    narrowed = _access(refreshed.access_token)
    assert narrowed.family_id == world["access"].family_id

    _refused_with(_commit_with(world, narrowed), "forbidden")
    # A later grant-write does not bring this family's write back.
    services.grant_write(user=world["user"], provenance=support.OPERATOR)
    _refused_with(_commit_with(world, narrowed), "forbidden")
    assert _notes(world["matter"]) == world["notes"]


def test_a_new_write_enablement_ends_the_old_family_and_the_new_one_sees_no_proposal(world, moment):
    services.revoke_write(user=world["user"], reason="test", provenance=support.OPERATOR)
    services.grant_write(user=world["user"], provenance=support.OPERATOR)

    # The old connection still carries juristid.write, but its family began
    # under the earlier enablement.
    assert McpToken.objects.get(pk=world["access"].token_id).scope_set >= {"juristid.write"}
    _refused_with(_commit_with(world, world["access"]), "forbidden")

    # Connecting again gives a writer — of a new family, to which the old
    # proposal does not belong.
    fresh = support.tokens_for(world["user"], world["secret"], moment, steps=1, scope=BOTH)
    _refused_with(_commit_with(world, _access(fresh.access_token)), "not_found")
    assert _notes(world["matter"]) == world["notes"]


# -- the approval page with the real authenticator ---------------------------------------

GATE = "/konto/varav/"
SWITCH = "/konto/kasutaja/vaheta/"


def _as(client: Client, user: Any) -> None:
    assert client.post(GATE, {"password": PASSWORD}).status_code == 302
    assert client.post(SWITCH, {"user_id": str(user.pk)}).status_code == 302


def _page(proposal: McpWriteProposal) -> str:
    return f"/mcp-ligipaas/muudatus/{proposal.pk}/"


@pytest.fixture
def gated(settings):
    apply_shared_gate(settings)


def test_a_proposal_of_an_earlier_grant_cannot_be_approved_with_the_new_grants_code(
    gated, moment, client
):
    user = factories.UserFactory()
    matter = factories.MatterFactory(owner=user)
    _grant, _secret, issued = _connect(user, moment)
    old = _prepare_note(user, _access(issued.access_token), matter)

    services.revoke_grant(user=user, reason="test", provenance=support.OPERATOR)
    _new_grant, new_secret = support.enrolled(user, moment)
    services.grant_write(user=user, provenance=support.OPERATOR)

    _as(client, user)
    assert client.get(_page(old)).status_code == 404
    answer = client.post(
        _page(old), {"otsus": "kinnita", "kood": support.code_at(new_secret, moment, 1)}
    )
    assert answer.status_code == 404
    assert "Kinnitatud" not in answer.content.decode()
    assert not McpWriteApproval.objects.filter(proposal=old).exists()
    old.refresh_from_db()
    assert old.status == ProposalStatus.PENDING


@pytest.mark.parametrize("hold", ["locked", "suspended"])
def test_a_lock_after_the_code_was_typed_ends_the_recent_code_shortcut(gated, moment, client, hold):
    user = factories.UserFactory()
    matter = factories.MatterFactory(owner=user)
    grant, secret, issued = _connect(user, moment)
    access = _access(issued.access_token)
    first = _prepare_note(user, access, matter)
    second = _prepare_note(user, access, matter)
    _as(client, user)

    approved = client.post(
        _page(first), {"otsus": "kinnita", "kood": support.code_at(secret, moment, 1)}
    )
    assert approved.status_code == 200 and "Kinnitatud" in approved.content.decode()

    # Somebody else now spends the wrong-code budget: the grant is locked
    # (or, past the last rung, suspended until an operator unlocks it).
    if hold == "locked":
        McpAccessGrant.objects.filter(pk=grant.pk).update(
            locked_until=timezone.now() + timedelta(minutes=15)
        )
    else:
        McpAccessGrant.objects.filter(pk=grant.pk).update(suspended_at=timezone.now())

    refused = client.post(_page(second), {"otsus": "kinnita"})
    assert refused.status_code != 200 or "Kinnitatud" not in refused.content.decode()
    assert not McpWriteApproval.objects.filter(proposal=second).exists()
    second.refresh_from_db()
    assert second.status == ProposalStatus.PENDING


# -- a Matter the person may not see, through every operation ------------------------------


def _participation_only(user: object | None) -> Scope:
    """The scope a business role had before docs/adr/0181: NORMAL plus own participation."""
    real = ORIGINAL_SCOPE_FOR_USER(user)
    if not real.is_authenticated:
        return real
    return Scope(
        user=user, is_authenticated=True, sees_restricted_by_role=False, break_glass_grant_id=None
    )


ORIGINAL_SCOPE_FOR_USER = authorization.scope_for_user


@pytest.fixture
def participation_only(monkeypatch):
    """Every module that imported `scope_for_user` gets the participation-only scope."""
    for module in list(sys.modules.values()):
        if getattr(module, "scope_for_user", None) is ORIGINAL_SCOPE_FOR_USER:
            monkeypatch.setattr(module, "scope_for_user", _participation_only)


def _soon(days: int = 7) -> str:
    return (timezone.localdate() + timedelta(days=days)).isoformat()


def _arguments(name: str, matter: Matter, colleague: Any) -> dict[str, Any]:
    reference = str(matter.pk)
    return {
        "update_matter": {"matter": reference, "title": "Uus pealkiri"},
        "assign_matter": {
            "matter": reference,
            "action": "assign",
            "responsible_person": colleague.display_name,
        },
        "add_matter_note": {"matter": reference, "text": NOTE, "occurred_on": _soon(0)},
        "record_meeting": {"matter": reference, "summary": "Kohtumine.", "held_on": _soon(0)},
        "add_procedural_link": {
            "matter": reference,
            "kind": "eis",
            "url": "https://eelnoud.valitsus.ee/main/mount/docList/abc",
            "label": "Kooskõlastus",
        },
        "set_next_action": {
            "matter": reference,
            "mode": "new",
            "text": "Saata arvamus",
            "kind": "do",
            "date": _soon(),
            "date_precision": "exact",
        },
        "complete_action": {"matter": reference, "what_was_done": "Tehtud."},
        "response_deadline": {"matter": reference, "mode": "new_request", "deadline": _soon(30)},
        "add_important_date": {
            "matter": reference,
            "title": "Eeldatav jõustumine",
            "date": (timezone.localdate() + timedelta(days=120)).strftime("%Y-%m"),
            "date_precision": "month",
            "kind": "estimated_commencement",
        },
    }[name]


MATTER_OPERATIONS = sorted(ops.EXPECTED - {"create_matter"})


def test_every_operation_but_create_names_a_matter():
    """`create_matter` makes a new Matter; every other operation is swept below."""
    assert len(MATTER_OPERATIONS) == 9
    for name in MATTER_OPERATIONS:
        assert "matter" in ops.REGISTRY[name].arguments.model_fields


def _hidden_from(outsider: Any, owner: Any) -> Matter:
    matter = factories.MatterFactory(owner=owner, visibility=Visibility.RESTRICTED)
    assert not Matter.objects.visible_to(outsider).filter(pk=matter.pk).exists()
    return matter


@pytest.mark.parametrize("name", MATTER_OPERATIONS)
def test_a_hidden_matter_is_not_found_at_prepare(name, participation_only):
    outsider = factories.UserFactory(display_name="Väline Kolleeg")
    owner = factories.UserFactory(display_name="Omanik Kolleeg")
    matter = _hidden_from(outsider, owner)
    if name == "complete_action":
        set_next_action_service(matter=matter, text="Koostada arvamus", actor=owner)

    refused = ops.refusal(name, outsider, **_arguments(name, matter, owner))

    assert refused.code == "not_found"
    # The same arguments by somebody who sees it are not refused as not found.
    assert ops.prepare(name, owner, **_arguments(name, matter, outsider)).canonical


@pytest.mark.parametrize("name", MATTER_OPERATIONS)
def test_a_matter_hidden_after_prepare_is_not_written_at_commit(name, participation_only):
    writer = factories.UserFactory(display_name="Kirjutaja Kolleeg")
    owner = factories.UserFactory(display_name="Omanik Kolleeg")
    matter = factories.MatterFactory(owner=writer)
    if name == "complete_action":
        set_next_action_service(matter=matter, text="Koostada arvamus", actor=writer)
    prepared = ops.prepare(name, writer, **_arguments(name, matter, owner))

    # Before the commit the Matter becomes somebody else's restricted file.
    Matter.objects.filter(pk=matter.pk).update(owner=owner, visibility=Visibility.RESTRICTED)
    assert not Matter.objects.visible_to(writer).filter(pk=matter.pk).exists()

    # Some operations' state does not name the visibility (their state is the
    # Matter's content), so the comparison alone may pass; `execute` must
    # still refuse, under the commit's savepoint, before any service runs.
    with contextlib.suppress(OperationRefused):
        ops.is_stale(name, writer, prepared)
    assert ops.execute_refusal(name, writer, prepared).code == "not_found"
    matter.refresh_from_db()
    assert matter.title != "Uus pealkiri" and matter.owner_id == owner.pk


def test_the_participation_scope_is_really_installed(participation_only):
    outsider = factories.UserFactory()
    owner = factories.UserFactory()
    hidden = _hidden_from(outsider, owner)
    assert Matter.objects.visible_to(owner).filter(pk=hidden.pk).exists()
    assert uuid.UUID(str(hidden.pk))
