"""Prepare: a proposed change is validated, previewed and stored — and changes nothing (0189).

`app.mcp_writes.proposals.prepare` with a stand-in operation that renames a
Matter (`tests/test_mcp_write_proposals_support.py`). What is proved here: a
prepared change writes no business row; a refused one stores no proposal;
an invalid argument is named by field and never repeated; the stored digest
binds the operation, the arguments, the user and the grant.
"""

from __future__ import annotations

import uuid

import pytest
from django.db import transaction
from django.utils import timezone

from app.audit.models import ChangeEvent
from app.matters.services import set_matter_title
from app.mcp_writes import proposals
from app.mcp_writes.models import McpWriteEvent, McpWriteEventType, McpWriteProposal, ProposalStatus
from app.mcp_writes.refusals import Refusal
from tests import factories
from tests import test_mcp_write_proposals_support as support

pytestmark = pytest.mark.django_db

SECRET_VALUE = "SALAJANE-ARGUMENDI-VÄÄRTUS-7731"  # noqa: S105 - an argument, not a credential


@pytest.fixture(autouse=True)
def writes_on(settings):
    support.enable(settings)


@pytest.fixture
def op(monkeypatch):
    return support.register(monkeypatch)


@pytest.fixture
def stand_ins(monkeypatch):
    return support.install_access(monkeypatch)


@pytest.fixture
def world(op, stand_ins):
    user = factories.UserFactory()
    matter = factories.MatterFactory(owner=user, title="Pakendiseaduse muutmine")
    grant, raw, access = support.writer(user)
    return {"user": user, "matter": matter, "grant": grant, "raw": raw, "access": access}


def _prepare(world, **arguments):
    payload = {"matter": str(world["matter"].pk), "title": "Uus pealkiri", **arguments}
    return proposals.prepare(
        access=world["access"],
        user=world["user"],
        operation_name="test_rename_matter",
        raw_arguments=payload,
    )


def test_a_prepared_change_is_stored_pending_and_writes_no_business_row(world, settings):
    events_before = ChangeEvent.objects.count()

    answer = _prepare(world)

    assert isinstance(answer, dict) and answer["status"] == "prepared"
    proposal = McpWriteProposal.objects.get(pk=answer["proposal"]["id"])
    assert proposal.status == ProposalStatus.PENDING
    assert proposal.user == world["user"] and proposal.grant == world["grant"]
    assert proposal.client_id == world["access"].client_id
    assert proposal.token_family_id == world["access"].family_id
    assert proposal.canonical_arguments == {
        "matter_id": str(world["matter"].pk),
        "title": "Uus pealkiri",
    }
    assert proposal.state == {"title": "Pakendiseaduse muutmine"}
    assert proposal.target_matter_id == world["matter"].pk
    assert proposal.arguments_digest == proposals.digest_of(proposal)
    remaining = (proposal.expires_at - timezone.now()).total_seconds()
    assert (
        settings.MCP_WRITE_PROPOSAL_SECONDS - 60 < remaining <= settings.MCP_WRITE_PROPOSAL_SECONDS
    )

    world["matter"].refresh_from_db()
    assert world["matter"].title == "Pakendiseaduse muutmine"
    assert ChangeEvent.objects.count() == events_before
    [event] = McpWriteEvent.objects.filter(proposal=proposal)
    assert event.event_type == McpWriteEventType.PREPARED
    assert event.correlation_id == proposal.correlation_id


def test_the_answer_carries_the_preview_and_an_absolute_approval_url(world):
    answer = _prepare(world)
    body = answer["proposal"]
    assert body["operation"] == "test_rename_matter"
    assert body["label"] == "Muuda teema pealkirja (test)"
    assert body["approval_url"] == (
        f"{support.access_support.ORIGIN}/mcp-ligipaas/muudatus/{body['id']}/"
    )
    assert body["preview"]["changes"] == [
        {
            "field": "title",
            "label": "Pealkiri",
            "current": "Pakendiseaduse muutmine",
            "proposed": "Uus pealkiri",
        }
    ]
    assert body["preview"]["unchanged"] == ["Teemat ei suleta"]
    assert uuid.UUID(body["id"]).version == 4


def test_an_invalid_argument_is_named_by_field_and_its_value_is_never_repeated(world):
    answer = _prepare(world, title=SECRET_VALUE * 20)

    assert isinstance(answer, Refusal) and answer.code == "invalid_argument"
    text = str(answer.as_json())
    assert "title" in text and "string_too_long" in text
    assert SECRET_VALUE not in text
    assert not McpWriteProposal.objects.exists()
    [event] = McpWriteEvent.objects.all()
    assert event.event_type == McpWriteEventType.PREPARE_REFUSED and not event.succeeded
    assert event.detail["fields"] == ["title"]
    assert SECRET_VALUE not in str(event.detail)


def test_an_unexpected_argument_is_refused(world):
    answer = _prepare(world, user_id=str(uuid.uuid4()))
    assert isinstance(answer, Refusal) and answer.code == "invalid_argument"
    assert "user_id (extra_forbidden)" in answer.message


def test_an_unknown_operation_is_refused_without_repeating_its_name(world):
    answer = proposals.prepare(
        access=world["access"],
        user=world["user"],
        operation_name=SECRET_VALUE,
        raw_arguments={},
    )
    assert isinstance(answer, Refusal) and answer.code == "invalid_argument"
    assert SECRET_VALUE not in str(answer.as_json())
    [event] = McpWriteEvent.objects.all()
    assert event.detail == {"code": "invalid_argument", "reason": "unknown_operation"}


def test_arguments_that_are_not_an_object_are_refused(world):
    answer = proposals.prepare(
        access=world["access"],
        user=world["user"],
        operation_name="test_rename_matter",
        raw_arguments=[SECRET_VALUE],
    )
    assert isinstance(answer, Refusal) and answer.code == "invalid_argument"
    assert SECRET_VALUE not in str(answer.as_json())


def test_oversized_arguments_are_refused_before_validation(world, settings):
    settings.MCP_WRITE_MAX_REQUEST_BYTES = 200
    answer = _prepare(world, title="x" * 150, filler="y" * 100)
    assert isinstance(answer, Refusal) and answer.code == "too_large"
    assert not McpWriteProposal.objects.exists()


@pytest.mark.parametrize(
    ("title", "matter", "code"),
    [
        ("PUUDU", None, "missing_information"),
        # A fixed id nobody has: a random one changes the test's name on every
        # collection, and the shard plan then finds it in no shard.
        ("Uus", "00000000-0000-4000-8000-00000000dead", "not_found"),
    ],
)
def test_an_operation_refusal_stores_no_proposal_and_says_why(world, title, matter, code):
    arguments = {"title": title}
    if matter:
        arguments["matter"] = matter
    answer = _prepare(world, **arguments)
    assert isinstance(answer, Refusal) and answer.code == code
    assert not McpWriteProposal.objects.exists()
    [event] = McpWriteEvent.objects.all()
    assert event.event_type == McpWriteEventType.PREPARE_REFUSED
    assert event.detail == {"code": code}


def test_whatever_an_operations_prepare_writes_is_rolled_back(
    world, op, monkeypatch, django_capture_on_commit_callbacks
):
    """Belt and braces: `prepare` must write nothing, and if it did, nothing survives."""
    real_prepare = op.prepare

    def careless(*, user, arguments):
        set_matter_title(matter=world["matter"], value="Kirjutati eelvaates", actor=user)
        transaction.on_commit(lambda: None)
        return real_prepare(user=user, arguments=arguments)

    monkeypatch.setattr(op, "prepare", careless)
    events_before = ChangeEvent.objects.count()
    with django_capture_on_commit_callbacks() as callbacks:
        answer = _prepare(world)
    assert isinstance(answer, dict)
    world["matter"].refresh_from_db()
    assert world["matter"].title == "Pakendiseaduse muutmine"
    assert ChangeEvent.objects.count() == events_before
    assert callbacks == []


def test_the_digest_binds_operation_arguments_user_and_grant():
    base = {
        "operation": "add_matter_note",
        "canonical_arguments": {"matter_id": "a", "text": "x"},
        "user_id": uuid.UUID(int=1),
        "grant_id": uuid.UUID(int=2),
    }
    digest = proposals.arguments_digest(**base)
    assert len(digest) == 64
    reordered = {**base, "canonical_arguments": {"text": "x", "matter_id": "a"}}
    assert proposals.arguments_digest(**reordered) == digest
    for key, value in (
        ("operation", "update_matter"),
        ("canonical_arguments", {"matter_id": "a", "text": "y"}),
        ("user_id", uuid.UUID(int=3)),
        ("grant_id", uuid.UUID(int=4)),
    ):
        assert proposals.arguments_digest(**{**base, key: value}) != digest


@pytest.mark.parametrize(
    "arguments",
    [{"title": "Uus\x00pealkiri"}, {"title": "Uus", "x\x00": "y"}, {"title": ["a", {"b": "\x00"}]}],
)
def test_a_nul_character_anywhere_is_refused_with_an_event(world, arguments):
    answer = _prepare(world, **arguments)
    assert isinstance(answer, Refusal) and answer.code == "invalid_argument"
    assert not McpWriteProposal.objects.exists()
    [event] = McpWriteEvent.objects.all()
    assert event.event_type == McpWriteEventType.PREPARE_REFUSED
    assert event.detail == {"code": "invalid_argument", "reason": "nul_character"}


def test_an_unexpected_failure_in_prepare_still_records_a_refusal(world, op, monkeypatch):
    def broken(*, user, arguments):
        raise RuntimeError(SECRET_VALUE)

    monkeypatch.setattr(op, "prepare", broken)
    answer = _prepare(world)
    assert isinstance(answer, Refusal) and answer.code == "internal_error"
    assert SECRET_VALUE not in str(answer.as_json())
    assert not McpWriteProposal.objects.exists()
    [event] = McpWriteEvent.objects.all()
    assert event.event_type == McpWriteEventType.PREPARE_REFUSED
    assert event.detail == {"code": "internal_error", "error": "RuntimeError"}
