"""Enrolling the personal «Juristidi MCP autentija» from a one-time link (docs/adr/0185 §3)."""

from __future__ import annotations

from datetime import timedelta
from urllib.parse import parse_qs, urlencode, urlsplit

import pyotp
import pytest
from django.utils import timezone

from app.accounts import mfa
from app.accounts.models import TotpDevice
from app.mcp_access import credentials, services
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


def _as(client, user) -> None:
    assert client.post(GATE, {"password": PASSWORD}).status_code == 302
    assert client.post("/konto/kasutaja/vaheta/", {"user_id": str(user.pk)}).status_code == 302


def _link(user) -> tuple[str, str]:
    """The printed URL's path+query, and the raw ``<selector>.<verifier>``."""
    issued = services.grant_access(user=user, provenance=support.OPERATOR)
    parts = urlsplit(issued.url)
    assert f"{parts.scheme}://{parts.netloc}" == support.ORIGIN
    return f"{parts.path}?{parts.query}", parse_qs(parts.query)["kood"][0]


def page_body(response) -> str:
    """The page's own content, without the chrome (whose persona switcher names everybody)."""
    text = response.content.decode()
    start = text.index('<div class="page page--narrow">')
    return text[start : text.index("</p>", text.index('class="acctnote"', start))]


def _secret_on(page) -> str:
    return page.context["enrolment"].secret


def test_the_link_is_taken_off_the_address_bar_and_shows_the_authenticator(client):
    user = factories.UserFactory(display_name="Mari Maasikas")
    path, token = _link(user)
    _as(client, user)

    arrived = client.get(path)
    assert arrived.status_code == 302 and arrived["Location"] == ENROL

    page = client.get(ENROL)
    body = page.content.decode()
    assert page.status_code == 200
    assert "<svg" in body
    secret = _secret_on(page)
    assert secret in body
    uri = page.context["enrolment"].uri
    assert "issuer=Juristid%20MCP" in uri and "Mari%20Maasikas" in uri
    # The session keeps the link's proof and a sealed secret: never the verifier
    # or the secret itself.
    stored = client.session[support_session_key()]
    verifier = token.split(".", 1)[1]
    assert verifier not in str(stored) and secret not in str(stored)


def support_session_key() -> str:
    from app.mcp_access.views import SESSION_ENROLMENT

    return SESSION_ENROLMENT


def test_a_current_code_activates_the_grant_and_spends_the_link(client, moment):
    user = factories.UserFactory()
    path, _ = _link(user)
    _as(client, user)
    client.get(path)
    secret = _secret_on(client.get(ENROL))

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
    _as(client, user)
    client.get(path)
    secret = _secret_on(client.get(ENROL))
    client.post(ENROL, {"kood": support.code_at(secret, moment)})

    client.get(path)
    again = client.get(ENROL)

    assert again.status_code == 400 and "See link ei kehti" in again.content.decode()


def test_a_wrong_code_stores_nothing(client, moment):
    user = factories.UserFactory()
    path, _ = _link(user)
    _as(client, user)
    client.get(path)
    page = client.get(ENROL)
    secret = _secret_on(page)

    wrong = client.post(ENROL, {"kood": "000000"})

    assert wrong.status_code == 400
    assert not McpAuthenticator.objects.exists()
    assert services.open_grant(user).status == GrantStatus.PENDING_ENROLMENT
    # The same secret is shown again, so the phone already holding it still works.
    assert _secret_on(wrong) == secret


def test_another_persona_is_refused_without_learning_whose_link_it_is(client, moment):
    owner = factories.UserFactory(display_name="Omanik Õunapuu")
    path, _ = _link(owner)
    colleague = factories.UserFactory()
    _as(client, colleague)
    client.get(path)

    page = client.get(ENROL)

    body = page_body(page)
    assert page.status_code == 400 and "See link ei kehti" in body
    assert "Omanik" not in body and owner.upn not in body
    assert client.post(ENROL, {"kood": "123456"}).status_code == 400
    assert services.open_grant(owner).status == GrantStatus.PENDING_ENROLMENT
    assert McpEnrolmentLink.objects.get().used_at is None


def test_the_wrong_persona_page_reads_exactly_like_an_expired_link(client):
    owner = factories.UserFactory()
    path, _ = _link(owner)
    colleague = factories.UserFactory()
    _as(client, colleague)
    client.get(path)
    wrong_persona = client.get(ENROL)

    McpEnrolmentLink.objects.update(expires_at=timezone.now() - timedelta(seconds=1))
    client.post("/konto/kasutaja/vaheta/", {"user_id": str(owner.pk)})
    client.get(path)
    expired = client.get(ENROL)

    assert wrong_persona.status_code == expired.status_code == 400
    assert page_body(wrong_persona) == page_body(expired)


def test_an_expired_link_is_refused(client, moment):
    user = factories.UserFactory()
    path, _ = _link(user)
    McpEnrolmentLink.objects.update(expires_at=timezone.now() - timedelta(seconds=1))
    _as(client, user)
    client.get(path)
    assert client.get(ENROL).status_code == 400


def test_a_new_link_supersedes_the_old_one(client, moment):
    user = factories.UserFactory()
    old_path, _ = _link(user)
    newer = services.issue_enrolment_link(user=user, provenance=support.OPERATOR)
    _as(client, user)

    client.get(old_path)
    assert client.get(ENROL).status_code == 400

    parts = urlsplit(newer.url)
    client.get(f"{parts.path}?{parts.query}")
    assert client.get(ENROL).status_code == 200


def test_a_malformed_link_is_refused_without_a_lookup(client):
    user = factories.UserFactory()
    _as(client, user)
    client.get(f"{ENROL}?{urlencode({'kood': 'not-a-link'})}")
    assert client.get(ENROL).status_code == 400


def test_a_visitor_at_the_gate_comes_back_to_the_link(client):
    user = factories.UserFactory()
    path, _ = _link(user)

    stopped = client.get(path)
    assert stopped["Location"] == f"{GATE}?{urlencode({'next': path})}"
    passed = client.post(stopped["Location"], {"password": PASSWORD})
    assert passed["Location"] == path
    assert client.get(path)["Location"] == ENROL
    chooser = client.get(ENROL)
    assert chooser.status_code == 302 and chooser["Location"].startswith("/konto/kasutaja/")
    client.post(f"/konto/kasutaja/vaheta/?{urlencode({'next': ENROL})}", {"user_id": str(user.pk)})
    assert client.get(ENROL).status_code == 200


def test_a_revoked_grant_cannot_be_enrolled(client):
    user = factories.UserFactory()
    path, _ = _link(user)
    services.revoke_grant(user=user, reason="ära", provenance=support.OPERATOR)
    _as(client, user)
    client.get(path)
    assert client.get(ENROL).status_code == 400


def test_the_enrolled_authenticator_is_the_one_authorize_checks(client, moment):
    user = factories.UserFactory()
    path, _ = _link(user)
    _as(client, user)
    client.get(path)
    secret = _secret_on(client.get(ENROL))
    client.post(ENROL, {"kood": pyotp.TOTP(secret).at(moment - timedelta(seconds=30))})
    code = support.code_for(user, secret, moment)
    assert code
