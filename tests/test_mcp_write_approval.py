"""The approval page: only its own person, behind the gate, with their own code (docs/adr/0189).

`/mcp-ligipaas/muudatus/<proposal uuid>/` walked the way a browser walks it:
the shared gate, the persona chooser, the page. The authenticator check is
`app.mcp_access.services.confirm_write_code`; until that lands it is the
stand-in in `tests/test_mcp_write_proposals_support.py`, which accepts
``123456`` and counts every other code.
"""

from __future__ import annotations

import uuid
from datetime import timedelta

import pytest
from django.test import Client
from django.utils import timezone

from app.accounts import shared_gate
from app.mcp_writes import approval_views, proposals
from app.mcp_writes.models import (
    ApprovalMethod,
    McpWriteApproval,
    McpWriteEvent,
    McpWriteEventType,
    McpWriteProposal,
    ProposalStatus,
)
from tests import factories
from tests import test_mcp_write_proposals_support as support
from tests.gate import PASSWORD, apply_shared_gate

pytestmark = pytest.mark.django_db

GATE = "/konto/varav/"
SWITCH = "/konto/kasutaja/vaheta/"
ORIGINAL = "Pakendiseaduse muutmine"
SCRIPT = "<script>alert(1)</script>"


@pytest.fixture(autouse=True)
def behind_the_gate(settings):
    apply_shared_gate(settings)
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
    matter = factories.MatterFactory(owner=user, title=ORIGINAL)
    grant, _raw, access = support.writer(user)
    return {"user": user, "matter": matter, "grant": grant, "access": access}


def prepared(world, title: str = "Uus pealkiri") -> McpWriteProposal:
    answer = proposals.prepare(
        access=world["access"],
        user=world["user"],
        operation_name="test_rename_matter",
        raw_arguments={"matter": str(world["matter"].pk), "title": title},
    )
    assert isinstance(answer, dict), answer
    return McpWriteProposal.objects.get(pk=answer["proposal"]["id"])


def url(proposal) -> str:
    return f"/mcp-ligipaas/muudatus/{proposal.pk}/"


def as_persona(client: Client, user) -> None:
    assert client.post(GATE, {"password": PASSWORD}).status_code == 302
    assert client.post(SWITCH, {"user_id": str(user.pk)}).status_code == 302


def body(response) -> str:
    return response.content.decode()


# -- who may see it ---------------------------------------------------------------------


def test_the_owner_sees_the_stored_preview_with_a_code_field(client, world):
    proposal = prepared(world)
    as_persona(client, world["user"])

    page = client.get(url(proposal))

    assert page.status_code == 200
    text = body(page)
    assert "Muuda teema pealkirja (test)" in text
    assert ORIGINAL in text and "Uus pealkiri" in text
    assert "Teemat ei suleta" in text and "Uus pealkiri on kohe kõigile nähtav." in text
    assert f'href="/teemad/{world["matter"].pk}/"' in text
    assert 'name="kood"' in text and 'value="kinnita"' in text and 'value="keeldu"' in text
    assert "no-store" in page["Cache-Control"]
    assert page["X-Frame-Options"] == "DENY"
    assert "frame-ancestors 'none'" in page["Content-Security-Policy"]
    assert "form-action 'self'" in page["Content-Security-Policy"]


def test_preview_text_is_escaped(client, world):
    proposal = prepared(world, title=SCRIPT)
    as_persona(client, world["user"])
    text = body(client.get(url(proposal)))
    assert SCRIPT not in text and "&lt;script&gt;alert(1)&lt;/script&gt;" in text


def test_a_stored_link_off_this_site_is_not_linked(client, world):
    proposal = prepared(world)
    preview = dict(proposal.preview)
    preview["target"] = {**preview["target"], "url": "javascript:alert(1)"}
    McpWriteProposal.objects.filter(pk=proposal.pk).update(preview=preview)
    as_persona(client, world["user"])
    text = body(client.get(url(proposal)))
    assert "javascript:" not in text


def _generic(response) -> bool:
    return response.status_code == 404 and approval_views.GENERIC_REFUSAL in body(response)


def test_another_persona_gets_the_one_generic_refusal(client, world):
    proposal = prepared(world)
    as_persona(client, factories.UserFactory())

    page = client.get(url(proposal))
    missing = client.get(f"/mcp-ligipaas/muudatus/{uuid.uuid4()}/")
    posted = client.post(url(proposal), {"otsus": "kinnita", "kood": support.GOOD_CODE})

    assert _generic(page) and _generic(missing) and _generic(posted)

    def main(response) -> str:
        text = body(response)
        return text[text.index('<div class="page page--narrow">') :].split("</div>", 2)[1]

    assert main(page) == main(missing)
    assert ORIGINAL not in body(page) and "Uus pealkiri" not in body(page)
    proposal.refresh_from_db()
    assert proposal.status == ProposalStatus.PENDING
    assert not McpWriteApproval.objects.exists()


def test_a_grant_that_may_not_write_gets_the_generic_refusal(client, world, stand_ins):
    proposal = prepared(world)
    stand_ins.write_revoked.add(world["grant"].pk)
    as_persona(client, world["user"])
    assert _generic(client.get(url(proposal)))


def test_no_persona_is_sent_to_choose_one(client, world):
    proposal = prepared(world)
    assert client.post(GATE, {"password": PASSWORD}).status_code == 302
    response = client.get(url(proposal))
    assert response.status_code == 302 and "/konto/kasutaja/" in response["Location"]


def test_without_the_gate_the_page_is_behind_the_gate(client, world):
    proposal = prepared(world)
    response = client.get(url(proposal))
    assert response.status_code == 302 and response["Location"].startswith(GATE)


def test_with_writes_off_the_route_is_a_404_for_everybody(client, world, settings):
    proposal = prepared(world)
    as_persona(client, world["user"])
    settings.MCP_WRITES_ENABLED = False
    assert client.get(url(proposal)).status_code == 404
    assert client.post(url(proposal), {"otsus": "kinnita"}).status_code == 404


# -- approving ------------------------------------------------------------------------------


def test_the_right_code_approves_once_and_sends_the_person_back_to_chatgpt(
    client, world, stand_ins, settings
):
    proposal = prepared(world)
    as_persona(client, world["user"])

    page = client.post(url(proposal), {"otsus": "kinnita", "kood": support.GOOD_CODE})

    assert page.status_code == 200
    assert approval_views.DONE in body(page)
    assert 'value="kinnita"' not in body(page)
    approval = McpWriteApproval.objects.get(proposal=proposal)
    assert approval.user == world["user"] and approval.grant == world["grant"]
    assert approval.arguments_digest == proposal.arguments_digest
    assert approval.method == ApprovalMethod.TOTP
    window = (approval.expires_at - approval.approved_at).total_seconds()
    assert window == settings.MCP_WRITE_APPROVAL_SECONDS
    proposal.refresh_from_db()
    assert proposal.status == ProposalStatus.APPROVED
    world["matter"].refresh_from_db()
    assert world["matter"].title == ORIGINAL  # approval is not commit
    assert McpWriteEvent.objects.filter(
        proposal=proposal, event_type=McpWriteEventType.APPROVED
    ).exists()
    # The same counters as /oauth/authorize.
    [call] = stand_ins.code_calls
    assert call["grant"] == world["grant"].pk
    gate = client.session[shared_gate.GATE_PASSED_AT]
    assert shared_gate.key_for_address(f"mcp-authorize|session|{gate}") in call["throttle"]

    again = client.post(url(proposal), {"otsus": "kinnita", "kood": support.GOOD_CODE})
    assert McpWriteApproval.objects.filter(proposal=proposal).count() == 1
    assert 'value="kinnita"' not in body(again)


def test_a_wrong_code_is_counted_and_approves_nothing(client, world, stand_ins):
    proposal = prepared(world)
    as_persona(client, world["user"])

    page = client.post(url(proposal), {"otsus": "kinnita", "kood": "000000"})

    assert page.status_code == 400 and approval_views.BAD_CODE in body(page)
    assert stand_ins.wrong_codes == 1
    assert not McpWriteApproval.objects.exists()
    proposal.refresh_from_db()
    assert proposal.status == ProposalStatus.PENDING
    refused = McpWriteEvent.objects.get(
        proposal=proposal, event_type=McpWriteEventType.APPROVAL_REFUSED
    )
    assert refused.detail == {"code": "bad_code"}
    assert "000000" not in str(refused.detail)


@pytest.mark.parametrize(
    ("status", "http", "text"),
    [
        ("locked", 429, "Liiga palju valesid koode"),
        ("suspended", 403, "peatatud"),
        ("throttled", 429, "Liiga palju katseid"),
    ],
)
def test_lock_suspension_and_throttle_are_answered_like_authorize(
    client, world, monkeypatch, status, http, text
):
    from app.mcp_access import services as access_services

    monkeypatch.setattr(
        access_services,
        "confirm_write_code",
        lambda **kwargs: access_services.AuthorizationOutcome(status=status, wait=120),
        raising=False,
    )
    proposal = prepared(world)
    as_persona(client, world["user"])
    page = client.post(url(proposal), {"otsus": "kinnita", "kood": support.GOOD_CODE})
    assert page.status_code == http and text in body(page)
    assert not McpWriteApproval.objects.exists()


def test_a_recent_code_in_this_gate_session_approves_the_next_change_with_one_press(
    client, world, stand_ins
):
    first, second = prepared(world), prepared(world, title="Teine pealkiri")
    as_persona(client, world["user"])
    assert (
        client.post(url(first), {"otsus": "kinnita", "kood": support.GOOD_CODE}).status_code == 200
    )

    page = client.get(url(second))
    assert 'name="kood"' not in body(page) and 'value="kinnita"' in body(page)
    approved = client.post(url(second), {"otsus": "kinnita"})

    assert approval_views.DONE in body(approved)
    assert McpWriteApproval.objects.get(proposal=second).method == ApprovalMethod.RECENT_TOTP
    assert len(stand_ins.code_calls) == 1


def test_the_recent_code_expires(client, world, settings):
    first, second = prepared(world), prepared(world, title="Teine pealkiri")
    as_persona(client, world["user"])
    client.post(url(first), {"otsus": "kinnita", "kood": support.GOOD_CODE})
    session = client.session
    stored = session[approval_views.SESSION_RECENT]
    stored["verified_at"] = (
        timezone.now() - timedelta(seconds=settings.MCP_WRITE_REVERIFY_SECONDS + 1)
    ).isoformat()
    session[approval_views.SESSION_RECENT] = stored
    session.save()

    page = client.post(url(second), {"otsus": "kinnita"})

    assert page.status_code == 400 and approval_views.BAD_CODE in body(page)
    assert not McpWriteApproval.objects.filter(proposal=second).exists()


def test_the_recent_code_is_bound_to_the_gate_pass_and_the_epoch(client, world):
    first, second, third = (
        prepared(world),
        prepared(world, title="Teine"),
        prepared(world, title="Kolmas"),
    )
    as_persona(client, world["user"])
    client.post(url(first), {"otsus": "kinnita", "kood": support.GOOD_CODE})
    stored = client.session[approval_views.SESSION_RECENT]

    # The same state under another pass of the gate is worth nothing.
    session = client.session
    session[shared_gate.GATE_PASSED_AT] = (timezone.now() - timedelta(seconds=5)).isoformat()
    session[approval_views.SESSION_RECENT] = stored
    session.save()
    assert client.post(url(second), {"otsus": "kinnita"}).status_code == 400

    # Nor after the account's security epoch moved.
    other = Client()
    as_persona(other, world["user"])
    other.post(url(second), {"otsus": "kinnita", "kood": support.GOOD_CODE})
    world["user"].security_epoch += 1
    world["user"].save(update_fields=["security_epoch"])
    assert other.post(url(third), {"otsus": "kinnita"}).status_code in (302, 400)
    assert not McpWriteApproval.objects.filter(proposal=third).exists()


def test_each_approval_needs_its_own_press(client, world):
    first, second = prepared(world), prepared(world, title="Teine")
    as_persona(client, world["user"])
    client.post(url(first), {"otsus": "kinnita", "kood": support.GOOD_CODE})
    assert McpWriteApproval.objects.filter(proposal=second).count() == 0
    second.refresh_from_db()
    assert second.status == ProposalStatus.PENDING


def test_declining_records_it_and_approves_nothing(client, world):
    proposal = prepared(world)
    as_persona(client, world["user"])

    page = client.post(url(proposal), {"otsus": "keeldu"})

    assert page.status_code == 200 and approval_views.DECLINED in body(page)
    proposal.refresh_from_db()
    assert proposal.status == ProposalStatus.DECLINED
    assert not McpWriteApproval.objects.exists()
    assert McpWriteEvent.objects.filter(
        proposal=proposal, event_type=McpWriteEventType.DECLINED
    ).exists()
    later = client.post(url(proposal), {"otsus": "kinnita", "kood": support.GOOD_CODE})
    assert not McpWriteApproval.objects.exists() and 'value="kinnita"' not in body(later)


def test_an_unknown_decision_is_refused(client, world):
    proposal = prepared(world)
    as_persona(client, world["user"])
    page = client.post(url(proposal), {"otsus": "jah", "kood": support.GOOD_CODE})
    assert page.status_code == 400 and approval_views.CHOOSE in body(page)
    assert not McpWriteApproval.objects.exists()


def test_a_stale_proposal_shows_no_approve_button_and_cannot_be_approved(client, world):
    proposal = prepared(world)
    world["matter"].title = "Keegi muutis vahepeal"
    world["matter"].save(update_fields=["title"])
    as_persona(client, world["user"])

    page = client.get(url(proposal))
    assert approval_views.STALE in body(page) and 'value="kinnita"' not in body(page)
    assert 'value="keeldu"' in body(page)
    posted = client.post(url(proposal), {"otsus": "kinnita", "kood": support.GOOD_CODE})
    assert posted.status_code == 409 and not McpWriteApproval.objects.exists()


def test_an_expired_proposal_is_marked_and_offers_nothing(client, world):
    proposal = prepared(world)
    McpWriteProposal.objects.filter(pk=proposal.pk).update(
        expires_at=timezone.now() - timedelta(seconds=1)
    )
    as_persona(client, world["user"])
    page = client.get(url(proposal))
    assert approval_views.EXPIRED in body(page) and 'value="kinnita"' not in body(page)
    proposal.refresh_from_db()
    assert proposal.status == ProposalStatus.EXPIRED


def test_the_approve_post_needs_its_csrf_token(world):
    proposal = prepared(world)
    lenient = Client()
    as_persona(lenient, world["user"])
    strict = Client(enforce_csrf_checks=True)
    strict.cookies = lenient.cookies
    response = strict.post(url(proposal), {"otsus": "kinnita", "kood": support.GOOD_CODE})
    assert response.status_code == 403
    assert not McpWriteApproval.objects.exists()
    # The same session and code were good: it was the missing token.
    lenient.post(url(proposal), {"otsus": "kinnita", "kood": support.GOOD_CODE})
    assert McpWriteApproval.objects.filter(proposal=proposal).exists()


def test_a_proposal_tampered_before_approval_is_failed_not_approved(client, world):
    proposal = prepared(world)
    McpWriteProposal.objects.filter(pk=proposal.pk).update(
        canonical_arguments={"matter_id": str(world["matter"].pk), "title": "Muu"}
    )
    as_persona(client, world["user"])
    client.post(url(proposal), {"otsus": "kinnita", "kood": support.GOOD_CODE})
    assert not McpWriteApproval.objects.exists()
    proposal.refresh_from_db()
    assert proposal.status == ProposalStatus.FAILED


# -- a real browser's same-origin POST (security review P1) -----------------------------

PUBLIC_HOST = "juristid.example.test"


def _browser(settings, world, proposal) -> tuple[Client, str]:
    """A CSRF-enforcing client over https, its token taken from the page it was shown."""
    settings.ALLOWED_HOSTS = [PUBLIC_HOST, "testserver"]
    lenient = Client()
    as_persona(lenient, world["user"])
    strict = Client(enforce_csrf_checks=True)
    strict.cookies = lenient.cookies
    page = strict.get(url(proposal), secure=True, HTTP_HOST=PUBLIC_HOST)
    assert page.status_code == 200
    assert page.get("Referrer-Policy") != "no-referrer"
    text = body(page)
    marker = 'name="csrfmiddlewaretoken" value="'
    token = text[text.index(marker) + len(marker) :].split('"', 1)[0]
    return strict, token


def _post(client: Client, proposal, data: dict, origin: str):
    return client.post(url(proposal), data, secure=True, HTTP_HOST=PUBLIC_HOST, HTTP_ORIGIN=origin)


def test_a_same_origin_browser_approves_with_csrf_enforced(settings, world):
    proposal = prepared(world)
    strict, token = _browser(settings, world, proposal)
    response = _post(
        strict,
        proposal,
        {"otsus": "kinnita", "kood": support.GOOD_CODE, "csrfmiddlewaretoken": token},
        f"https://{PUBLIC_HOST}",
    )
    assert response.status_code == 200 and approval_views.DONE in body(response)
    assert McpWriteApproval.objects.filter(proposal=proposal).exists()


def test_a_same_origin_browser_declines_with_csrf_enforced(settings, world):
    proposal = prepared(world)
    strict, token = _browser(settings, world, proposal)
    response = _post(
        strict,
        proposal,
        {"otsus": "keeldu", "csrfmiddlewaretoken": token},
        f"https://{PUBLIC_HOST}",
    )
    assert response.status_code == 200
    proposal.refresh_from_db()
    assert proposal.status == ProposalStatus.DECLINED


def test_a_cross_origin_post_is_refused_even_with_a_token(settings, world):
    proposal = prepared(world)
    strict, token = _browser(settings, world, proposal)
    for origin in ("https://evil.example.test", "null"):
        response = _post(
            strict,
            proposal,
            {"otsus": "kinnita", "kood": support.GOOD_CODE, "csrfmiddlewaretoken": token},
            origin,
        )
        assert response.status_code == 403
    assert not McpWriteApproval.objects.exists()


# -- withdrawing an approval (security review P3-4) --------------------------------------


def test_an_approval_can_be_withdrawn_before_commit_and_the_commit_is_declined(client, world):
    from app.mcp_writes.commit import commit

    proposal = prepared(world)
    as_persona(client, world["user"])
    approved = client.post(url(proposal), {"otsus": "kinnita", "kood": support.GOOD_CODE})
    assert 'value="tuhista"' in body(approved)

    page = client.post(url(proposal), {"otsus": "tuhista"})

    assert page.status_code == 200 and approval_views.WITHDRAWN in body(page)
    assert 'value="tuhista"' not in body(page)
    proposal.refresh_from_db()
    assert proposal.status == ProposalStatus.DECLINED and proposal.failure_code == "withdrawn"
    event = McpWriteEvent.objects.filter(
        proposal=proposal, event_type=McpWriteEventType.DECLINED
    ).get()
    assert event.detail == {"reason": "approval_withdrawn"}
    answer = commit(access=world["access"], user=world["user"], proposal_id=str(proposal.pk))
    assert answer.code == "declined"
    world["matter"].refresh_from_db()
    assert world["matter"].title == ORIGINAL


def test_an_executed_change_cannot_be_withdrawn(client, world):
    from app.mcp_writes.commit import commit

    proposal = prepared(world)
    as_persona(client, world["user"])
    client.post(url(proposal), {"otsus": "kinnita", "kood": support.GOOD_CODE})
    commit(access=world["access"], user=world["user"], proposal_id=str(proposal.pk))

    page = client.post(url(proposal), {"otsus": "tuhista"})

    assert page.status_code == 409
    proposal.refresh_from_db()
    assert proposal.status == ProposalStatus.EXECUTED


def test_a_pending_proposal_has_nothing_to_withdraw(client, world):
    proposal = prepared(world)
    as_persona(client, world["user"])
    assert 'value="tuhista"' not in body(client.get(url(proposal)))
    assert client.post(url(proposal), {"otsus": "tuhista"}).status_code == 400
    proposal.refresh_from_db()
    assert proposal.status == ProposalStatus.PENDING


def test_another_persona_cannot_withdraw(client, world):
    proposal = prepared(world)
    owner = Client()
    as_persona(owner, world["user"])
    owner.post(url(proposal), {"otsus": "kinnita", "kood": support.GOOD_CODE})
    as_persona(client, factories.UserFactory())
    assert _generic(client.post(url(proposal), {"otsus": "tuhista"}))
    proposal.refresh_from_db()
    assert proposal.status == ProposalStatus.APPROVED
