"""The approval page: only its own person, behind the gate, with their own code (ADR 0189, 0234).

`/mcp-ligipaas/muudatus/<proposal uuid>/` walked the way a browser walks it:
the shared gate, the persona chooser, the page. The authenticator check is
`app.mcp_access.services.confirm_write_code`; here it is the stand-in in
`tests/test_mcp_write_proposals_support.py`, which accepts ``123456`` and
counts every other code.

Since format 2 (SEC-009) the page shows the preview it derives itself from the
proposal's arguments — never the gateway's stored copy — its form carries the
binding digest of exactly that, and «Kinnita» approves *and saves* in one
transaction. ``approve`` below reads the binding from the page the way a
browser submits it.
"""

from __future__ import annotations

import re
import uuid
from datetime import timedelta

import pytest
from django.test import Client
from django.utils import timezone

from app.accounts import shared_gate
from app.mcp_access.models import McpToken
from app.mcp_writes import approval, approval_views, proposals, review
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
BINDING = re.compile(r'name="sidumine" value="([0-9a-f]{64})"')


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


def forged(world, **fields) -> McpWriteProposal:
    """A proposal row as a compromised gateway could insert it (PENDING, by the trigger)."""
    source = prepared(world)
    values = {
        field.attname: getattr(source, field.attname)
        for field in McpWriteProposal._meta.concrete_fields
        if field.attname not in ("id", "created_at", "updated_at", "correlation_id")
    }
    values.update(fields)
    return McpWriteProposal.objects.create(id=uuid.uuid4(), correlation_id=uuid.uuid4(), **values)


def url(proposal) -> str:
    return f"/mcp-ligipaas/muudatus/{proposal.pk}/"


def as_persona(client: Client, user) -> None:
    assert client.post(GATE, {"password": PASSWORD}).status_code == 302
    assert client.post(SWITCH, {"user_id": str(user.pk)}).status_code == 302


def body(response) -> str:
    return response.content.decode()


def binding_on(client: Client, proposal) -> str:
    found = BINDING.search(body(client.get(url(proposal))))
    return found.group(1) if found else ""


def approve(client: Client, proposal, code: str | None = support.GOOD_CODE, **extra):
    """«Kinnita» as a browser sends it: the binding the page showed, and the code typed."""
    data = {"otsus": "kinnita", "sidumine": binding_on(client, proposal), **extra}
    if code is not None:
        data["kood"] = code
    return client.post(url(proposal), data)


def title_of(world) -> str:
    world["matter"].refresh_from_db()
    return world["matter"].title


# -- who may see it ---------------------------------------------------------------------


def test_the_owner_sees_the_derived_preview_with_a_code_field_and_the_binding(client, world):
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
    assert BINDING.search(text).group(1) == review.derive(proposal, world["user"]).binding
    assert 'value="tuhista"' not in text
    assert "no-store" in page["Cache-Control"]
    assert page["X-Frame-Options"] == "DENY"
    assert "frame-ancestors 'none'" in page["Content-Security-Policy"]
    assert "form-action 'self'" in page["Content-Security-Policy"]


def test_preview_text_is_escaped(client, world):
    proposal = prepared(world, title=SCRIPT)
    as_persona(client, world["user"])
    text = body(client.get(url(proposal)))
    assert SCRIPT not in text and "&lt;script&gt;alert(1)&lt;/script&gt;" in text


def test_a_stored_preview_that_lies_is_never_shown(client, world):
    """SEC-009: the gateway's stored preview is not what the person reads."""
    proposal = prepared(world)
    lie = dict(proposal.preview)
    lie["summary"] = "Lisa märge — midagi ei muutu"
    lie["changes"] = []
    lie["target"] = {**lie["target"], "url": "javascript:alert(1)"}
    liar = forged(world, preview=lie)
    as_persona(client, world["user"])

    text = body(client.get(url(liar)))

    assert "midagi ei muutu" not in text and "javascript:" not in text
    assert "Uus pealkiri" in text and ORIGINAL in text  # what will really happen
    assert 'value="kinnita"' in text


def test_a_stored_change_the_arguments_do_not_give_is_never_offered(client, world, stand_ins):
    """Stored canonical arguments that differ from what the arguments resolve to: refused."""
    liar = forged(
        world, canonical_arguments={"matter_id": str(world["matter"].pk), "title": "Kurjus"}
    )
    as_persona(client, world["user"])

    page = client.get(url(liar))
    assert approval_views.MISMATCH in body(page) and 'value="kinnita"' not in body(page)
    assert "Kurjus" not in body(page)

    client.post(url(liar), {"otsus": "kinnita", "sidumine": "0" * 64, "kood": support.GOOD_CODE})
    liar.refresh_from_db()
    assert liar.status == ProposalStatus.FAILED and liar.failure_code == "approval_mismatch"
    assert stand_ins.code_calls == [] and title_of(world) == ORIGINAL


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


def test_a_disconnected_chatgpt_connection_offers_no_approval(client, world, stand_ins):
    proposal = prepared(world)
    McpToken.objects.filter(family_id=proposal.token_family_id).update(
        revoked_at=timezone.now(), revoke_reason="test"
    )
    as_persona(client, world["user"])
    page = client.get(url(proposal))
    assert approval_views.DISCONNECTED in body(page) and 'value="kinnita"' not in body(page)
    posted = client.post(
        url(proposal), {"otsus": "kinnita", "sidumine": "0" * 64, "kood": support.GOOD_CODE}
    )
    assert posted.status_code == 409 and stand_ins.code_calls == []
    assert not McpWriteApproval.objects.exists() and title_of(world) == ORIGINAL


# -- approving is saving -------------------------------------------------------------------


def test_the_right_code_approves_and_saves_once(client, world, stand_ins):
    proposal = prepared(world)
    as_persona(client, world["user"])

    page = approve(client, proposal)

    assert page.status_code == 200
    assert approval_views.DONE in body(page)
    assert 'value="kinnita"' not in body(page)
    recorded = McpWriteApproval.objects.get(proposal=proposal)
    assert recorded.user == world["user"] and recorded.grant == world["grant"]
    assert recorded.arguments_digest == proposal.arguments_digest
    assert recorded.binding_digest and recorded.format_version == 2
    assert recorded.method == ApprovalMethod.TOTP
    proposal.refresh_from_db()
    assert proposal.status == ProposalStatus.EXECUTED
    assert title_of(world) == "Uus pealkiri"  # approving is saving
    kinds = set(
        McpWriteEvent.objects.filter(proposal=proposal).values_list("event_type", flat=True)
    )
    assert {McpWriteEventType.APPROVED, McpWriteEventType.COMMITTED} <= kinds
    # The same counters as /oauth/authorize.
    [call] = stand_ins.code_calls
    assert call["grant"] == world["grant"].pk
    gate = client.session[shared_gate.GATE_PASSED_AT]
    assert shared_gate.key_for_address(f"mcp-authorize|session|{gate}") in call["throttle"]

    again = client.post(url(proposal), {"otsus": "kinnita", "kood": support.GOOD_CODE})
    assert McpWriteApproval.objects.filter(proposal=proposal).count() == 1
    assert 'value="kinnita"' not in body(again)
    assert approval_views.STATUS_TEXT[ProposalStatus.EXECUTED] in body(again)
    assert "Uus pealkiri" in body(again)  # what was approved, from the approval row


def test_a_preview_that_changed_after_it_was_shown_is_shown_again_and_spends_no_code(
    client, world, stand_ins
):
    proposal = prepared(world)
    as_persona(client, world["user"])
    shown = binding_on(client, proposal)

    page = client.post(
        url(proposal), {"otsus": "kinnita", "sidumine": "1" * 64, "kood": support.GOOD_CODE}
    )

    assert page.status_code == 409 and approval_views.CHANGED in body(page)
    assert BINDING.search(body(page)).group(1) == shown
    assert stand_ins.code_calls == [] and not McpWriteApproval.objects.exists()
    proposal.refresh_from_db()
    assert proposal.status == ProposalStatus.PENDING
    assert McpWriteEvent.objects.filter(
        proposal=proposal, event_type=McpWriteEventType.APPROVAL_REFUSED
    ).exists()


def test_a_wrong_code_is_counted_and_approves_nothing(client, world, stand_ins):
    proposal = prepared(world)
    as_persona(client, world["user"])

    page = approve(client, proposal, code="000000")

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
    page = approve(client, proposal)
    assert page.status_code == http and text in body(page)
    assert not McpWriteApproval.objects.exists()


def test_a_recent_code_in_this_gate_session_approves_the_next_change_with_one_press(
    client, world, stand_ins
):
    first, second = prepared(world), prepared(world, title="Teine pealkiri")
    as_persona(client, world["user"])
    assert approve(client, first).status_code == 200
    # The first save moved the title: the second proposal was judged against the old one.
    second = prepared(world, title="Kolmas pealkiri")

    page = client.get(url(second))
    assert 'name="kood"' not in body(page) and 'value="kinnita"' in body(page)
    approved = approve(client, second, code=None)

    assert approval_views.DONE in body(approved)
    assert McpWriteApproval.objects.get(proposal=second).method == ApprovalMethod.RECENT_TOTP
    assert len(stand_ins.code_calls) == 1


def test_the_recent_code_expires(client, world, settings):
    first = prepared(world)
    as_persona(client, world["user"])
    approve(client, first)
    second = prepared(world, title="Teine pealkiri")
    session = client.session
    stored = session[approval_views.SESSION_RECENT]
    stored["verified_at"] = (
        timezone.now() - timedelta(seconds=settings.MCP_WRITE_REVERIFY_SECONDS + 1)
    ).isoformat()
    session[approval_views.SESSION_RECENT] = stored
    session.save()

    page = approve(client, second, code=None)

    assert page.status_code == 400 and approval_views.BAD_CODE in body(page)
    assert not McpWriteApproval.objects.filter(proposal=second).exists()


def test_the_recent_code_is_bound_to_the_gate_pass_and_the_epoch(client, world):
    first = prepared(world)
    as_persona(client, world["user"])
    approve(client, first)
    second, third = prepared(world, title="Teine"), prepared(world, title="Kolmas")
    stored = client.session[approval_views.SESSION_RECENT]

    # The same state under another pass of the gate is worth nothing.
    session = client.session
    session[shared_gate.GATE_PASSED_AT] = (timezone.now() - timedelta(seconds=5)).isoformat()
    session[approval_views.SESSION_RECENT] = stored
    session.save()
    assert approve(client, second, code=None).status_code == 400

    # Nor after the account's security epoch moved.
    other = Client()
    as_persona(other, world["user"])
    approve(other, second)
    third = prepared(world, title="Neljas")
    world["user"].security_epoch += 1
    world["user"].save(update_fields=["security_epoch"])
    response = other.post(url(third), {"otsus": "kinnita", "sidumine": "0" * 64})
    assert response.status_code in (302, 400, 409)
    assert not McpWriteApproval.objects.filter(proposal=third).exists()


def test_each_approval_needs_its_own_press(client, world):
    first, second = prepared(world), prepared(world, title="Teine")
    as_persona(client, world["user"])
    approve(client, first)
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
    assert title_of(world) == ORIGINAL


def test_an_unknown_decision_is_refused(client, world):
    proposal = prepared(world)
    as_persona(client, world["user"])
    page = client.post(url(proposal), {"otsus": "jah", "kood": support.GOOD_CODE})
    assert page.status_code == 400 and approval_views.CHOOSE in body(page)
    assert not McpWriteApproval.objects.exists()


def test_the_retired_withdraw_decision_does_nothing(client, world):
    """Approving is saving: there is no approved-but-unsaved state left to withdraw."""
    proposal = prepared(world)
    as_persona(client, world["user"])
    page = client.post(url(proposal), {"otsus": "tuhista"})
    assert page.status_code == 400 and approval_views.CHOOSE in body(page)
    proposal.refresh_from_db()
    assert proposal.status == ProposalStatus.PENDING


def test_a_stale_proposal_shows_no_approve_button_and_cannot_be_approved(client, world):
    proposal = prepared(world)
    world["matter"].title = "Keegi muutis vahepeal"
    world["matter"].save(update_fields=["title"])
    as_persona(client, world["user"])

    page = client.get(url(proposal))
    assert approval_views.STALE in body(page) and 'value="kinnita"' not in body(page)
    assert 'value="keeldu"' in body(page)
    posted = client.post(
        url(proposal), {"otsus": "kinnita", "sidumine": "0" * 64, "kood": support.GOOD_CODE}
    )
    assert not McpWriteApproval.objects.exists()
    assert approval_views.STALE in body(posted)
    proposal.refresh_from_db()
    assert proposal.status == ProposalStatus.STALE


def test_an_expired_proposal_is_marked_and_offers_nothing(client, world, settings):
    settings.MCP_WRITE_PROPOSAL_SECONDS = 0
    proposal = prepared(world)
    as_persona(client, world["user"])
    page = client.get(url(proposal))
    assert approval_views.EXPIRED in body(page) and 'value="kinnita"' not in body(page)
    proposal.refresh_from_db()
    assert proposal.status == ProposalStatus.EXPIRED


def test_the_approve_post_needs_its_csrf_token(world):
    proposal = prepared(world)
    lenient = Client()
    as_persona(lenient, world["user"])
    binding = binding_on(lenient, proposal)
    strict = Client(enforce_csrf_checks=True)
    strict.cookies = lenient.cookies
    data = {"otsus": "kinnita", "sidumine": binding, "kood": support.GOOD_CODE}
    response = strict.post(url(proposal), data)
    assert response.status_code == 403
    assert not McpWriteApproval.objects.exists()
    # The same session and code were good: it was the missing token.
    lenient.post(url(proposal), data)
    assert McpWriteApproval.objects.filter(proposal=proposal).exists()


def test_an_unexpected_failure_while_saving_rolls_the_approval_back(client, world, op):
    proposal = prepared(world)
    op.fail_after_write = RuntimeError("lost")
    as_persona(client, world["user"])

    page = approve(client, proposal)

    assert page.status_code == 500 and approval_views.RETRY in body(page)
    proposal.refresh_from_db()
    assert proposal.status == ProposalStatus.PENDING
    assert not McpWriteApproval.objects.exists() and title_of(world) == ORIGINAL
    failed = McpWriteEvent.objects.get(proposal=proposal, event_type=McpWriteEventType.FAILED)
    assert failed.detail["code"] == "internal_error" and "lost" not in str(failed.detail)


def test_a_domain_refusal_while_saving_says_nothing_was_changed(client, world, op):
    proposal = prepared(world)
    op.refuse_after_write = True
    as_persona(client, world["user"])
    page = approve(client, proposal)
    assert page.status_code == 409 and approval_views.FAILED_TEXT in body(page)
    proposal.refresh_from_db()
    assert proposal.status == ProposalStatus.FAILED and title_of(world) == ORIGINAL


# -- a real browser's same-origin POST (security review P1) -----------------------------

PUBLIC_HOST = "juristid.example.test"


def _browser(settings, world, proposal) -> tuple[Client, str, str]:
    """A CSRF-enforcing client over https, its token and binding from the page it was shown."""
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
    return strict, token, BINDING.search(text).group(1)


def _post(client: Client, proposal, data: dict, origin: str):
    return client.post(url(proposal), data, secure=True, HTTP_HOST=PUBLIC_HOST, HTTP_ORIGIN=origin)


def test_a_same_origin_browser_approves_with_csrf_enforced(settings, world):
    proposal = prepared(world)
    strict, token, binding = _browser(settings, world, proposal)
    response = _post(
        strict,
        proposal,
        {
            "otsus": "kinnita",
            "sidumine": binding,
            "kood": support.GOOD_CODE,
            "csrfmiddlewaretoken": token,
        },
        f"https://{PUBLIC_HOST}",
    )
    assert response.status_code == 200 and approval_views.DONE in body(response)
    assert McpWriteApproval.objects.filter(proposal=proposal).exists()


def test_a_same_origin_browser_declines_with_csrf_enforced(settings, world):
    proposal = prepared(world)
    strict, token, _binding = _browser(settings, world, proposal)
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
    strict, token, binding = _browser(settings, world, proposal)
    for origin in ("https://evil.example.test", "null"):
        response = _post(
            strict,
            proposal,
            {
                "otsus": "kinnita",
                "sidumine": binding,
                "kood": support.GOOD_CODE,
                "csrfmiddlewaretoken": token,
            },
            origin,
        )
        assert response.status_code == 403
    assert not McpWriteApproval.objects.exists()


# -- MCP_WRITE_REVERIFY_SECONDS=0, and no code spent on what cannot be approved ------
# (audit 2026-10-28 C-03; owner's decision 2026-10-29, scenario G)


def test_with_reverify_zero_every_approval_needs_its_own_fresh_code(
    client, world, stand_ins, settings
):
    settings.MCP_WRITE_REVERIFY_SECONDS = 0
    first = prepared(world)
    as_persona(client, world["user"])

    approved = approve(client, first)
    assert approval_views.DONE in body(approved)
    assert approval_views.SESSION_RECENT not in client.session

    second = prepared(world, title="Teine pealkiri")
    page = client.get(url(second))
    assert 'name="kood"' in body(page)  # the page asks again
    shortcut = approve(client, second, code=None)
    assert shortcut.status_code == 400 and approval_views.BAD_CODE in body(shortcut)
    second.refresh_from_db()
    assert second.status == ProposalStatus.PENDING
    assert not McpWriteApproval.objects.filter(proposal=second).exists()

    fresh = approve(client, second)
    assert approval_views.DONE in body(fresh)
    assert McpWriteApproval.objects.get(proposal=second).method == ApprovalMethod.TOTP
    assert len(stand_ins.code_calls) == 3


def test_with_reverify_zero_a_window_left_in_the_session_is_worthless(client, world, settings):
    first = prepared(world)
    as_persona(client, world["user"])
    approve(client, first)
    assert approval_views.SESSION_RECENT in client.session  # opened under the old 300 s
    second = prepared(world, title="Teine pealkiri")
    settings.MCP_WRITE_REVERIFY_SECONDS = 0
    page = approve(client, second, code=None)
    assert page.status_code == 400 and approval_views.BAD_CODE in body(page)
    assert not McpWriteApproval.objects.filter(proposal=second).exists()


@pytest.mark.parametrize("seconds", [0, 300])
def test_a_tampered_proposal_spends_no_code_and_opens_no_window(
    client, world, stand_ins, settings, seconds
):
    settings.MCP_WRITE_REVERIFY_SECONDS = seconds
    tampered = forged(
        world, canonical_arguments={"matter_id": str(world["matter"].pk), "title": "Muu"}
    )
    other = prepared(world, title="Teine pealkiri")
    as_persona(client, world["user"])

    client.post(
        url(tampered), {"otsus": "kinnita", "sidumine": "0" * 64, "kood": support.GOOD_CODE}
    )

    tampered.refresh_from_db()
    assert tampered.status == ProposalStatus.FAILED
    assert stand_ins.code_calls == []  # the code was never presented, so never spent
    assert approval_views.SESSION_RECENT not in client.session
    no_code = approve(client, other, code=None)
    assert no_code.status_code == 400
    assert not McpWriteApproval.objects.exists()


@pytest.mark.parametrize("seconds", [0, 300])
def test_an_expired_proposal_spends_no_code_and_opens_no_window(
    client, world, stand_ins, settings, seconds
):
    settings.MCP_WRITE_REVERIFY_SECONDS = seconds
    settings.MCP_WRITE_PROPOSAL_SECONDS = 0
    expired = prepared(world)
    settings.MCP_WRITE_PROPOSAL_SECONDS = 900
    other = prepared(world, title="Teine pealkiri")
    as_persona(client, world["user"])

    page = client.post(
        url(expired), {"otsus": "kinnita", "sidumine": "0" * 64, "kood": support.GOOD_CODE}
    )

    assert approval_views.EXPIRED in body(page)
    expired.refresh_from_db()
    assert expired.status == ProposalStatus.EXPIRED
    assert stand_ins.code_calls == []
    assert approval_views.SESSION_RECENT not in client.session
    assert approve(client, other, code=None).status_code == 400
    # The same code still approves a proposal that can be approved.
    approved = approve(client, other)
    assert approval_views.DONE in body(approved)


def test_a_proposal_that_moves_on_while_the_code_is_checked_is_refused_under_the_lock(
    client, world, stand_ins, monkeypatch
):
    """Everything is derived again after the code, under the locks: a record that moved
    on then is refused, saves nothing and, having approved nothing, opens no window."""
    proposal = prepared(world)
    as_persona(client, world["user"])
    shown = binding_on(client, proposal)
    real = approval.check

    def moves_on_under_the_lock(proposal_id, user, *, canonical_locks):
        if canonical_locks:
            type(world["matter"]).objects.filter(pk=world["matter"].pk).update(title="Vahepeal")
        return real(proposal_id, user, canonical_locks=canonical_locks)

    monkeypatch.setattr(approval, "check", moves_on_under_the_lock)
    client.post(url(proposal), {"otsus": "kinnita", "sidumine": shown, "kood": support.GOOD_CODE})
    proposal.refresh_from_db()
    assert proposal.status == ProposalStatus.STALE
    assert len(stand_ins.code_calls) == 1  # only a genuine race spends the code
    assert not McpWriteApproval.objects.exists()
    assert approval_views.SESSION_RECENT not in client.session
    assert title_of(world) == "Vahepeal"
