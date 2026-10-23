"""Who is connecting is the account the address names, proven by its own authenticator.

docs/adr/0185 §3, owner decision of 2026-10-23. The consent page
(`/oauth/authorize`) and the enrolment page (`/mcp-ligipaas/liitu/`) stay
behind the shared gate and no longer ask for, or read, a persona: a persona is
a choice anybody with the gate password makes, and READER accounts — who read
everything (docs/adr/0181) — are never offered as one, so a READER with a grant
could never connect. Identity is the operator's grant, the gate, the account's
own e-mail address and a current code from that account's own authenticator
(or, to enrol, the one-time link issued for that account).

These tests walk the real browser path with no persona chosen at any point.
"""

from __future__ import annotations

import io
import re
import uuid
from urllib.parse import parse_qs, urlencode, urlsplit

import pytest
from django.core.management import call_command
from django.test import Client

from app.accounts.enums import UserRole
from app.accounts.models import User
from app.core import authorization
from app.mcp_access import services, views
from app.mcp_access.models import (
    GrantStatus,
    McpAccessEvent,
    McpAccessEventType,
    McpAccessGrant,
    McpAuthorizationCode,
    McpAuthorizeThrottle,
)
from tests import factories
from tests import mcp_access_support as support
from tests.gate import PASSWORD, apply_shared_gate

pytestmark = pytest.mark.django_db

AUTHORIZE = support.AUTHORIZE
ENROL = support.ENROL
GATE = support.GATE


@pytest.fixture(autouse=True)
def behind_the_gate(settings):
    apply_shared_gate(settings)
    support.enable(settings)


@pytest.fixture
def moment(monkeypatch):
    return support.freeze_totp(monkeypatch)


def _url(**overrides) -> str:
    return f"{AUTHORIZE}?{urlencode(support.authorize_params(**overrides))}"


def _grant_by_command(upn: str) -> tuple[str, str]:
    """`manage.py mcp_access grant`: its whole output, and the link's path and query."""
    out = io.StringIO()
    call_command("mcp_access", "grant", "--user", upn, stdout=out)
    [link] = [line for line in out.getvalue().splitlines() if line.startswith("https://")]
    parts = urlsplit(link)
    return out.getvalue(), f"{parts.path}?{parts.query}"


def _secret(page) -> str:
    [secret] = re.findall(r'class="mono acctsecret">([A-Z2-7]+)<', page.content.decode())
    return secret


def _no_persona(response) -> None:
    assert not response.wsgi_request.user.is_authenticated


# -- a READER connects, and nobody is ever chosen -------------------------------------


def test_a_reader_with_a_grant_enrols_and_authorizes_without_any_persona(client, moment):
    reader = factories.ReaderFactory(upn="lugeja@example.invalid", display_name="Lea Lugeja")
    # A READER is not somebody a persona can be (app/accounts/selectors.py).
    from app.accounts.selectors import is_persona_candidate

    assert not is_persona_candidate(reader)

    output, path = _grant_by_command(reader.upn)
    assert "personat" in output and "valima ei" in output
    assert McpAccessGrant.objects.get(user=reader).status == GrantStatus.PENDING_ENROLMENT

    # The link from outside: the gate, and straight back to the page.
    stopped = client.get(path)
    assert stopped["Location"] == f"{GATE}?{urlencode({'next': path})}"
    assert client.post(stopped["Location"], {"password": PASSWORD})["Location"] == path
    assert client.get(path)["Location"] == ENROL
    asked = client.get(ENROL)
    assert asked.status_code == 200
    _no_persona(asked)
    assert client.post(ENROL, {"epost": "LUGEJA@example.invalid"})["Location"] == ENROL
    page = client.get(ENROL)
    assert page.status_code == 200 and "Lea Lugeja" in support.page_body(page)
    secret = _secret(page)
    done = client.post(ENROL, {"kood": support.code_at(secret, moment, -1)})
    assert done.status_code == 200 and "Autentija on seadistatud" in done.content.decode()
    assert McpAccessGrant.objects.get(user=reader).status == GrantStatus.ACTIVE

    # Consent, in the same persona-less session.
    consent_page = client.get(_url())
    assert consent_page.status_code == 200
    _no_persona(consent_page)
    answered = client.post(_url(), support.consent(reader, support.code_at(secret, moment)))
    assert answered.status_code == 302
    assert answered["Location"].startswith(support.PUBLIC_REDIRECT + "?")
    record = McpAuthorizationCode.objects.get()
    assert record.user == reader and record.grant.user == reader
    _no_persona(answered)


def test_a_reader_enrolled_elsewhere_authorizes_from_a_fresh_gate_session(moment):
    reader = factories.ReaderFactory()
    _, secret = support.enrolled(reader, moment)
    fresh = Client()
    support.through_the_gate(fresh)
    response = fresh.post(_url(), support.consent(reader, support.code_at(secret, moment)))
    assert response.status_code == 302 and "code=" in response["Location"]


# -- the door ---------------------------------------------------------------------------


def test_a_persona_less_gate_session_reaches_both_pages(client):
    owner = factories.UserFactory()
    issued = services.grant_access(user=owner, provenance=support.OPERATOR)
    parts = urlsplit(issued.url)
    support.through_the_gate(client)

    consent_page = client.get(_url())
    assert consent_page.status_code == 200
    assert 'name="epost"' in consent_page.content.decode()
    _no_persona(consent_page)

    assert client.get(f"{parts.path}?{parts.query}")["Location"] == ENROL
    enrol_page = client.get(ENROL)
    assert enrol_page.status_code == 200
    assert 'name="epost"' in enrol_page.content.decode()
    _no_persona(enrol_page)


@pytest.mark.parametrize("page", ["authorize", "enrol"], ids=lambda page: page)
def test_an_anonymous_visitor_is_still_sent_to_the_gate(client, page):
    path = _url() if page == "authorize" else f"{ENROL}?kood=a.b"
    stopped = client.get(path)
    assert stopped.status_code == 302
    assert stopped["Location"] == f"{GATE}?{urlencode({'next': path})}"
    posted = client.post(path.split("?kood=")[0], {"epost": "x@example.invalid", "kood": "1"})
    assert posted.status_code == 302 and posted["Location"].startswith(GATE)


def test_a_gate_less_request_never_reaches_the_form_or_the_counters(client, moment):
    user = factories.UserFactory()
    _, secret = support.enrolled(user, moment)
    events_before = McpAccessEvent.objects.count()

    page = client.get(_url())
    posted = client.post(_url(), support.consent(user, support.code_at(secret, moment)))

    for response in (page, posted):
        assert response.status_code == 302 and response["Location"].startswith(GATE)
        assert 'name="epost"' not in response.content.decode()
    assert not McpAuthorizationCode.objects.exists()
    assert not McpAuthorizeThrottle.objects.exists()
    assert McpAccessEvent.objects.count() == events_before
    # The code was never checked, so it still works behind the gate.
    support.through_the_gate(client)
    assert (
        client.post(_url(), support.consent(user, support.code_at(secret, moment))).status_code
        == 302
    )


def test_a_gate_opened_under_an_old_password_does_not_reach_the_form(client, settings):
    support.through_the_gate(client)
    assert client.get(_url()).status_code == 200
    settings.SHARED_GATE_PASSWORD = "uus-parool-mida-keegi-veel-ei-tea"  # noqa: S105
    response = client.get(_url())
    assert response.status_code == 302 and response["Location"].startswith(GATE)


# -- the address and the code must be the same account's --------------------------------


def test_another_accounts_address_with_my_code_fails_generically(client, moment):
    me = factories.UserFactory()
    _, my_secret = support.enrolled(me, moment)
    colleague = factories.UserFactory()
    colleague_grant, _ = support.enrolled(colleague, moment)
    support.through_the_gate(client)

    response = client.post(_url(), support.consent(colleague, support.code_at(my_secret, moment)))

    assert response.status_code == 400
    assert response.context["error"] == views.GENERIC_REFUSAL
    assert not McpAuthorizationCode.objects.exists()
    # It counts as a wrong code against the colleague's grant, not against mine.
    colleague_grant.refresh_from_db()
    assert colleague_grant.failed_attempts == 1
    assert services.active_grant(me).failed_attempts == 0
    # And my code is unspent: it still opens my own consent.
    assert (
        client.post(_url(), support.consent(me, support.code_at(my_secret, moment))).status_code
        == 302
    )


def test_a_chosen_persona_is_not_the_account_that_connects(client, moment):
    """Acting as a colleague changes nothing: the address names the account."""
    me = factories.UserFactory()
    _, my_secret = support.enrolled(me, moment)
    colleague = factories.UserFactory()
    support.enrolled(colleague, moment)
    support.through_the_gate(client)
    assert client.post("/konto/kasutaja/vaheta/", {"user_id": str(colleague.pk)}).status_code == 302

    response = client.post(_url(), support.consent(me, support.code_at(my_secret, moment)))

    assert response.status_code == 302
    assert McpAuthorizationCode.objects.get().user == me


@pytest.mark.parametrize(
    "typed",
    ["  {upn}  ", "{upper}"],
    ids=["surrounding-space", "upper-case"],
)
def test_the_address_is_compared_in_its_normal_form(client, moment, typed):
    user = factories.UserFactory(upn="mari.maasikas@example.invalid")
    _, secret = support.enrolled(user, moment)
    support.through_the_gate(client)
    address = typed.format(upn=user.upn, upper=user.upn.upper())
    response = client.post(_url(), support.consent(address, support.code_at(secret, moment)))
    assert response.status_code == 302


def test_an_unknown_address_fails_generically_and_is_throttled(client, moment, settings):
    settings.MCP_AUTHORIZE_MAX_FAILURES_PER_SESSION = 3
    settings.MCP_AUTHORIZE_MAX_FAILURES_PER_ADDRESS = 100
    user = factories.UserFactory()
    _, secret = support.enrolled(user, moment)
    support.through_the_gate(client)

    statuses = []
    for number in range(3):
        response = client.post(_url(), support.consent(f"keegi{number}@example.invalid", "123456"))
        statuses.append(response.status_code)
        assert response.context["error"] == views.GENERIC_REFUSAL
    assert statuses == [400, 400, 400]
    refusals = McpAccessEvent.objects.filter(
        event_type=McpAccessEventType.AUTHORIZATION_REFUSED, detail__reason="unknown_account"
    )
    assert refusals.count() == 3
    # The audit row does not keep the guesses.
    assert all("keegi" not in str(event.detail) for event in refusals)
    assert all(event.user_id is None for event in refusals)

    # The fourth attempt — a real account's right code included — is not checked.
    throttled = client.post(_url(), support.consent(user, support.code_at(secret, moment)))
    assert throttled.status_code == 429
    assert "Liiga palju katseid" in throttled.content.decode()
    assert not McpAuthorizationCode.objects.exists()
    # No grant was charged for the unknown addresses or for the refused attempt.
    assert services.active_grant(user).total_failed_attempts == 0


def test_the_enrolment_address_step_counts_on_the_same_throttle(client, moment, settings):
    settings.MCP_AUTHORIZE_MAX_FAILURES_PER_SESSION = 2
    settings.MCP_AUTHORIZE_MAX_FAILURES_PER_ADDRESS = 100
    owner = factories.UserFactory()
    issued = services.grant_access(user=owner, provenance=support.OPERATOR)
    parts = urlsplit(issued.url)
    support.through_the_gate(client)
    client.get(f"{parts.path}?{parts.query}")

    assert client.post(ENROL, {"epost": "keegi@example.invalid"}).status_code == 400
    # One guess here, one on the consent page: the budget is shared.
    assert (
        client.post(_url(), support.consent("teine@example.invalid", "123456")).status_code == 400
    )
    throttled = client.post(ENROL, {"epost": owner.upn})
    assert throttled.status_code == 429
    assert "acctsecret" not in throttled.content.decode()
    assert client.post(_url(), support.consent(owner, "123456")).status_code == 429


# -- who can never authorize --------------------------------------------------------


def _make_staff_specialist(user):
    User.objects.filter(pk=user.pk).update(is_staff=True)


def _make_superuser(user):
    User.objects.filter(pk=user.pk).update(is_superuser=True)


def _make_administrator(user):
    User.objects.filter(pk=user.pk).update(role=UserRole.ADMINISTRATOR, is_staff=True)


def _make_inactive(user):
    User.objects.filter(pk=user.pk).update(is_active=False)


@pytest.mark.parametrize(
    "change",
    [_make_staff_specialist, _make_superuser, _make_administrator, _make_inactive],
    ids=["staff-specialist", "superuser", "administrator", "inactive"],
)
def test_a_technical_administrator_or_inactive_account_cannot_authorize(client, moment, change):
    """Each was enrolled while eligible; the change is what the consent step must notice."""
    user = factories.UserFactory()
    _, secret = support.enrolled(user, moment)
    change(user)
    support.through_the_gate(client)

    response = client.post(_url(), support.consent(user, support.code_at(secret, moment)))

    assert response.status_code == 400
    assert response.context["error"] == views.GENERIC_REFUSAL
    assert not McpAuthorizationCode.objects.exists()


def test_a_break_glass_administrator_cannot_authorize(client, moment, monkeypatch):
    user = factories.UserFactory()
    _, secret = support.enrolled(user, moment)
    _make_administrator(user)
    monkeypatch.setattr(authorization, "active_break_glass_grant_id", lambda account: uuid.uuid4())
    assert authorization.may_read_business_content(User.objects.get(pk=user.pk))
    support.through_the_gate(client)

    response = client.post(_url(), support.consent(user, support.code_at(secret, moment)))

    assert response.status_code == 400
    assert response.context["error"] == views.GENERIC_REFUSAL
    assert not McpAuthorizationCode.objects.exists()


@pytest.mark.parametrize(
    ("make", "words"),
    [
        (lambda: factories.UserFactory(is_staff=True), "tehnilisele kontole"),
        (lambda: factories.UserFactory(is_superuser=True), "tehnilisele kontole"),
        (lambda: factories.AdministratorFactory(), "sisu lugemise rolli"),
    ],
    ids=["staff-specialist", "superuser-specialist", "administrator"],
)
def test_a_technical_account_is_never_granted(make, words):
    user = make()
    with pytest.raises(services.GrantRefused, match=words):
        services.grant_access(user=user, provenance=support.OPERATOR)
    assert not McpAccessGrant.objects.filter(user=user).exists()


# -- enumeration ------------------------------------------------------------------------


def test_an_unknown_address_and_a_wrong_code_read_exactly_the_same(client, moment):
    granted = factories.UserFactory()
    support.enrolled(granted, moment)
    nobody = factories.UserFactory()
    reader_without_grant = factories.ReaderFactory()
    support.through_the_gate(client)

    answers = {}
    for case, address in (
        ("wrong-code", granted.upn),
        ("unknown-address", "keegi.ei.ole@example.invalid"),
        ("no-grant", nobody.upn),
        ("reader-without-grant", reader_without_grant.upn),
    ):
        response = client.post(_url(), support.consent(address, "000000"))
        answers[case] = (
            response.status_code,
            [template.name for template in response.templates],
            response.context["error"],
            support.comparable(response, address),
        )
    first = answers["wrong-code"]
    assert first[0] == 400 and first[2] == views.GENERIC_REFUSAL
    assert all(answer == first for answer in answers.values()), answers.keys()


def test_the_consent_page_names_nobody(client, moment):
    granted = factories.UserFactory(display_name="Grant Omanik", upn="omanik@example.invalid")
    support.enrolled(granted, moment)
    support.through_the_gate(client)
    body = support.page_body(client.get(_url()))
    assert "Grant Omanik" not in body and "omanik@example.invalid" not in body
    refused_page = support.page_body(client.post(_url(), support.consent(granted, "000000")))
    assert "Grant Omanik" not in refused_page


# -- mode none: the development sign-in is the door -------------------------------------


def test_under_mode_none_a_signed_in_developer_still_names_the_account(settings, moment):
    settings.AUTH_MODE = "none"
    me = factories.UserFactory()
    _, secret = support.enrolled(me, moment)
    signed_in = Client()
    signed_in.force_login(factories.UserFactory())  # somebody else is signed in
    response = signed_in.post(_url(), support.consent(me, support.code_at(secret, moment)))
    assert response.status_code == 302
    assert McpAuthorizationCode.objects.get().user == me
    assert parse_qs(urlsplit(response["Location"]).query)["state"] == ["olek-123"]
