"""Enrolling the personal «Juristidi MCP autentija» from a one-time link (docs/adr/0185 §3).

Behind the shared gate and without a persona: the link is the proof of
possession, and the account's own e-mail address must name the link's account
before the secret is drawn.
"""

from __future__ import annotations

from datetime import timedelta
from urllib.parse import parse_qs, urlencode, urlsplit

import pyotp
import pytest
from django.utils import timezone

from app.accounts import mfa
from app.accounts.models import TotpDevice
from app.mcp_access import credentials, services, views
from app.mcp_access.models import (
    GrantStatus,
    McpAccessEvent,
    McpAccessEventType,
    McpAuthenticator,
    McpEnrolmentLink,
)
from tests import factories
from tests import mcp_access_support as support
from tests.gate import PASSWORD, apply_shared_gate

pytestmark = pytest.mark.django_db

ENROL = "/mcp-ligipaas/liitu/"
GATE = "/konto/varav/"


@pytest.fixture(autouse=True)
def behind_the_gate(settings):
    apply_shared_gate(settings)
    support.enable(settings)


@pytest.fixture
def moment(monkeypatch):
    return support.freeze_totp(monkeypatch)


def _link(user) -> tuple[str, str]:
    """The printed URL's path+query, and the raw ``<selector>.<verifier>``."""
    issued = services.grant_access(user=user, provenance=support.OPERATOR)
    parts = urlsplit(issued.url)
    assert f"{parts.scheme}://{parts.netloc}" == support.ORIGIN
    return f"{parts.path}?{parts.query}", parse_qs(parts.query)["kood"][0]


def _opened(client, path: str, address: str):
    """Follow the link behind the gate and name the account; the page that follows."""
    arrived = client.get(path)
    assert arrived.status_code == 302 and arrived["Location"] == ENROL
    named = client.post(ENROL, {"epost": address})
    assert named.status_code == 302 and named["Location"] == ENROL, named.status_code
    return client.get(ENROL)


def _secret_on(page) -> str:
    return page.context["enrolment"].secret


def support_session_key() -> str:
    return views.SESSION_ENROLMENT


def test_the_link_is_taken_off_the_address_bar_and_asks_for_the_address_first(client):
    user = factories.UserFactory(display_name="Mari Maasikas")
    path, token = _link(user)
    support.through_the_gate(client)

    arrived = client.get(path)
    assert arrived.status_code == 302 and arrived["Location"] == ENROL

    asked = client.get(ENROL)
    body = support.page_body(asked)
    assert asked.status_code == 200
    assert 'name="epost"' in body
    # No secret, no QR and no name until the address matches.
    assert "<svg" not in body and "acctsecret" not in body
    assert "Mari Maasikas" not in body and user.upn not in body
    assert not asked.wsgi_request.user.is_authenticated

    page = _opened(client, path, user.upn)
    body = page.content.decode()
    assert page.status_code == 200
    assert "<svg" in body
    secret = _secret_on(page)
    assert secret in body
    uri = page.context["enrolment"].uri
    assert "issuer=Juristid%20MCP" in uri and "Mari%20Maasikas" in uri
    # The session keeps the link's proof, the matched account's id and a sealed
    # secret: never the verifier, the secret or the address.
    stored = client.session[support_session_key()]
    verifier = token.split(".", 1)[1]
    assert verifier not in str(stored) and secret not in str(stored)
    assert user.upn not in str(stored)


def test_the_address_is_compared_in_its_normal_form(client):
    user = factories.UserFactory(upn="mari.maasikas@example.invalid")
    path, _ = _link(user)
    support.through_the_gate(client)
    page = _opened(client, path, "  Mari.Maasikas@EXAMPLE.invalid ")
    assert page.status_code == 200 and page.context["enrolment"].secret


def test_a_current_code_activates_the_grant_and_spends_the_link(client, moment):
    user = factories.UserFactory()
    path, _ = _link(user)
    support.through_the_gate(client)
    secret = _secret_on(_opened(client, path, user.upn))

    done = client.post(ENROL, {"kood": support.code_at(secret, moment)})

    assert done.status_code == 200 and "Autentija on seadistatud" in done.content.decode()
    grant = services.active_grant(user)
    assert grant is not None and grant.activated_at is not None
    authenticator = McpAuthenticator.objects.get(grant=grant)
    assert secret not in authenticator.encrypted_secret
    assert credentials.unseal(authenticator.encrypted_secret) == secret
    # Not the personal sign-in's factor, and not openable as one.
    assert mfa._decrypt(authenticator.encrypted_secret) is None
    assert not TotpDevice.objects.exists()
    assert McpEnrolmentLink.objects.get().used_at is not None
    assert McpAccessEvent.objects.filter(event_type=McpAccessEventType.ENROLMENT_COMPLETED).exists()


def test_the_link_works_once(client, moment):
    user = factories.UserFactory()
    path, _ = _link(user)
    support.through_the_gate(client)
    secret = _secret_on(_opened(client, path, user.upn))
    client.post(ENROL, {"kood": support.code_at(secret, moment)})

    client.get(path)
    again = client.get(ENROL)

    assert again.status_code == 400 and "See link ei kehti" in again.content.decode()


def test_a_wrong_code_stores_nothing(client, moment):
    user = factories.UserFactory()
    path, _ = _link(user)
    support.through_the_gate(client)
    secret = _secret_on(_opened(client, path, user.upn))

    wrong = client.post(ENROL, {"kood": "000000"})

    assert wrong.status_code == 400
    assert not McpAuthenticator.objects.exists()
    assert services.open_grant(user).status == GrantStatus.PENDING_ENROLMENT
    # The same secret is shown again, so the phone already holding it still works.
    assert _secret_on(wrong) == secret


@pytest.mark.parametrize(
    "who", ["colleague", "unknown", "empty", "not-an-address"], ids=lambda who: who
)
def test_another_address_is_refused_without_learning_whose_link_it_is(client, who):
    owner = factories.UserFactory(display_name="Omanik Õunapuu")
    path, _ = _link(owner)
    colleague = factories.UserFactory()
    typed = {
        "colleague": colleague.upn,
        "unknown": "keegi.teine@example.invalid",
        "empty": "",
        "not-an-address": "Omanik",
    }[who]
    support.through_the_gate(client)
    client.get(path)

    refused = client.post(ENROL, {"epost": typed})

    body = support.page_body(refused)
    assert refused.status_code == 400
    assert views.ENROLMENT_ADDRESS_REFUSAL in body
    assert "Omanik" not in body.replace(typed, "") and owner.upn not in body
    assert "<svg" not in body and "acctsecret" not in body
    # Nothing was spent and nothing was drawn: the owner can still enrol.
    assert services.open_grant(owner).status == GrantStatus.PENDING_ENROLMENT
    assert McpEnrolmentLink.objects.get().used_at is None
    assert "sealed" not in client.session[support_session_key()]
    # A code posted now is still the address step, not an enrolment.
    assert client.post(ENROL, {"kood": "123456"}).status_code == 400
    assert not McpAuthenticator.objects.exists()
    refusal = McpAccessEvent.objects.filter(
        event_type=McpAccessEventType.ENROLMENT_REFUSED, detail__reason="address_mismatch"
    )
    # The audit says why — the code post too was an address step — and never what was typed.
    assert [event.detail for event in refusal] == [{"reason": "address_mismatch"}] * 2


def test_a_wrong_address_and_an_unknown_one_read_the_same(client):
    owner = factories.UserFactory()
    path, _ = _link(owner)
    colleague = factories.UserFactory()
    support.through_the_gate(client)
    client.get(path)

    colleagues = client.post(ENROL, {"epost": colleague.upn})
    unknown = client.post(ENROL, {"epost": "keegi@example.invalid"})

    assert colleagues.status_code == unknown.status_code == 400
    assert colleagues.context["error"] == unknown.context["error"]
    assert support.comparable(colleagues, colleague.upn) == support.comparable(
        unknown, "keegi@example.invalid"
    )


def test_an_expired_link_is_refused_before_any_address_is_asked(client, moment):
    user = factories.UserFactory()
    path, _ = _link(user)
    McpEnrolmentLink.objects.update(expires_at=timezone.now() - timedelta(seconds=1))
    support.through_the_gate(client)
    client.get(path)
    expired = client.get(ENROL)
    assert expired.status_code == 400 and 'name="epost"' not in expired.content.decode()
    assert client.post(ENROL, {"epost": user.upn}).status_code == 400


def test_a_link_that_expires_after_the_address_is_refused(client, moment):
    user = factories.UserFactory()
    path, _ = _link(user)
    support.through_the_gate(client)
    _opened(client, path, user.upn)
    McpEnrolmentLink.objects.update(expires_at=timezone.now() - timedelta(seconds=1))
    assert client.get(ENROL).status_code == 400
    assert client.post(ENROL, {"kood": "123456"}).status_code == 400
    assert not McpAuthenticator.objects.exists()


def test_a_new_link_supersedes_the_old_one(client, moment):
    user = factories.UserFactory()
    old_path, _ = _link(user)
    newer = services.issue_enrolment_link(user=user, provenance=support.OPERATOR)
    support.through_the_gate(client)

    client.get(old_path)
    assert client.get(ENROL).status_code == 400

    parts = urlsplit(newer.url)
    assert _opened(client, f"{parts.path}?{parts.query}", user.upn).status_code == 200


def test_a_malformed_link_is_refused_without_a_lookup(client):
    support.through_the_gate(client)
    client.get(f"{ENROL}?{urlencode({'kood': 'not-a-link'})}")
    assert client.get(ENROL).status_code == 400


def test_a_visitor_at_the_gate_comes_back_to_the_link_and_needs_no_persona(client):
    user = factories.UserFactory()
    path, _ = _link(user)

    stopped = client.get(path)
    assert stopped["Location"] == f"{GATE}?{urlencode({'next': path})}"
    passed = client.post(stopped["Location"], {"password": PASSWORD})
    assert passed["Location"] == path
    assert client.get(path)["Location"] == ENROL
    asked = client.get(ENROL)
    assert asked.status_code == 200 and 'name="epost"' in asked.content.decode()
    assert client.post(ENROL, {"epost": user.upn})["Location"] == ENROL
    assert client.get(ENROL).status_code == 200
    assert not client.get(ENROL).wsgi_request.user.is_authenticated


def test_a_revoked_grant_cannot_be_enrolled(client):
    user = factories.UserFactory()
    path, _ = _link(user)
    services.revoke_grant(user=user, reason="ära", provenance=support.OPERATOR)
    support.through_the_gate(client)
    client.get(path)
    assert client.get(ENROL).status_code == 400


def test_a_chosen_persona_changes_nothing(client, moment):
    """Acting as somebody else does not open their link, nor close one's own."""
    owner = factories.UserFactory()
    path, _ = _link(owner)
    colleague = factories.UserFactory()
    support.through_the_gate(client)
    assert client.post("/konto/kasutaja/vaheta/", {"user_id": str(colleague.pk)}).status_code == 302
    client.get(path)
    # The persona's address is not the link's account.
    assert client.post(ENROL, {"epost": colleague.upn}).status_code == 400
    page = _opened(client, path, owner.upn)
    assert page.status_code == 200
    secret = _secret_on(page)
    assert client.post(ENROL, {"kood": support.code_at(secret, moment)}).status_code == 200
    assert services.active_grant(owner) is not None


def test_the_enrolled_authenticator_is_the_one_authorize_checks(client, moment):
    user = factories.UserFactory()
    path, _ = _link(user)
    support.through_the_gate(client)
    secret = _secret_on(_opened(client, path, user.upn))
    client.post(ENROL, {"kood": pyotp.TOTP(secret).at(moment - timedelta(seconds=30))})
    code = support.code_for(user, secret, moment)
    assert code
