"""A one-time link's proof enters a session only under a new identifier (SEC-006).

Activation and password-reset links are followed into a session: the page keeps
a *proof* of the link (selector + SHA-256 of the secret), and the POST that sets
the password reads it back from there. Until 2026-11-26 the proof was written
into whatever session the browser already had, without rotating it — so an
identifier planted in the browser beforehand (a sibling host's cookie, a shared
machine, a gate session somebody else opened and left) was a session the
planter could use to set the password the link was for: a conditional session
fixation, in front of the administrator's credential enrolment under the shared
gate and of every link in the dormant personal sign-in.

Now (docs/adr/0232):

* only a link that opens something is kept — checked for shape, the
  credential-link throttle, purpose, expiry, single use, the account's
  credential state and who may hold it — and an invalid one writes no session;
* keeping it rotates the session identifier *and* the CSRF secret, and setting
  the password rotates them again;
* any earlier proof is dropped the moment another link arrives, and a proof
  that stops opening anything leaves the session.

Everything here is synthetic and the authentication mode is set by each test
module's own settings fixture; no deployment mode is changed.
"""

from __future__ import annotations

import logging
import threading
from datetime import timedelta

import pytest
from django.contrib.sessions.models import Session
from django.db import connection
from django.test import Client
from django.urls import reverse
from django.utils import timezone

from app.accounts import local_auth, tokens
from app.accounts.enums import CredentialTokenPurpose, ProvisioningState, ThrottleScope, UserRole
from app.accounts.models import AccountCredentialToken, AuthenticationThrottle, User
from tests import factories
from tests.admin_session import apply_admin_gate, pass_gate
from tests.local_auth import OTHER_PASSWORD, PASSWORD, apply_local_password, person, sign_in

ACTIVATE = "accounts:activate"
RESET = "accounts:reset_password"


def _cookie(client: Client, name: str = "sessionid") -> str:
    morsel = client.cookies.get(name)
    return morsel.value if morsel is not None else ""


def _invited(email: str = "kutsutu@koda.ee", **extra) -> User:
    user = factories.UserFactory(
        upn=email,
        email=email,
        role=UserRole.SPECIALIST,
        provisioning_state=ProvisioningState.INVITED,
        is_active=False,
        **extra,
    )
    user.set_unusable_password()
    user.save()
    return user


def _issue(user: User, purpose: str = CredentialTokenPurpose.ACTIVATION) -> str:
    return tokens.issue(user=user, purpose=purpose).token


def _open(client: Client, route: str, token: str):
    return client.get(reverse(route), {"kood": token})


def _planted_session(client: Client) -> str:
    """A session the planter holds: any page that writes one will do."""
    client.get(reverse("accounts:sign_in"), {"next": "/teemad/"})
    planted = _cookie(client)
    assert planted and Session.objects.filter(session_key=planted).exists()
    return planted


# ===========================================================================
# Personal sign-in mode (dormant in every deployment; set here by the fixture)
# ===========================================================================


@pytest.fixture
def local(settings):
    return apply_local_password(settings)


@pytest.mark.django_db
def test_a_planted_session_cannot_use_the_link_its_victim_opened(local):
    """The fixation itself: the planter's identifier names nothing after the link."""
    user = _invited()
    token = _issue(user)
    attacker = Client()
    planted = _planted_session(attacker)
    victim = Client()
    victim.cookies["sessionid"] = planted

    landing = _open(victim, ACTIVATE, token)

    assert landing.status_code == 302
    assert "kood" not in landing["Location"]
    assert _cookie(victim) != planted
    assert not Session.objects.filter(session_key=planted).exists()
    # The planter's copy of the old identifier opens nothing and sets nothing.
    assert attacker.get(reverse(ACTIVATE)).status_code == 400
    refused = attacker.post(
        reverse(ACTIVATE), {"password": OTHER_PASSWORD, "password_again": OTHER_PASSWORD}
    )
    assert refused.status_code == 400
    user.refresh_from_db()
    assert not user.has_local_password
    # The person who followed the link still can.
    done = victim.post(reverse(ACTIVATE), {"password": PASSWORD, "password_again": PASSWORD})
    assert done.status_code == 302
    user.refresh_from_db()
    assert user.is_active and user.check_password(PASSWORD)


@pytest.mark.django_db
def test_keeping_the_proof_rotates_the_csrf_secret(local):
    user = _invited()
    token = _issue(user)
    victim = Client(enforce_csrf_checks=True)
    victim.get(reverse("accounts:sign_in"))
    planted_csrf = _cookie(victim, "csrftoken")
    assert planted_csrf

    _open(victim, ACTIVATE, token)

    assert _cookie(victim, "csrftoken") not in ("", planted_csrf)
    # A form forged with the planted secret no longer matches the browser's
    # cookie, and is refused before the view runs.
    forged = victim.post(
        reverse(ACTIVATE),
        {"password": PASSWORD, "password_again": PASSWORD, "csrfmiddlewaretoken": planted_csrf},
    )
    assert forged.status_code == 403
    user.refresh_from_db()
    assert not user.has_local_password


@pytest.mark.django_db
def test_setting_the_password_rotates_the_session_again(local):
    user = _invited()
    client = Client()
    _open(client, ACTIVATE, _issue(user))
    holding = _cookie(client)

    done = client.post(reverse(ACTIVATE), {"password": PASSWORD, "password_again": PASSWORD})

    assert done.status_code == 302
    assert _cookie(client) != holding
    assert not Session.objects.filter(session_key=holding).exists()
    assert local_auth.SESSION_LINK not in client.session


@pytest.mark.django_db
def test_a_valid_reset_link_works_and_rotates(local):
    user, _ = person(email="lahtestaja@koda.ee")
    client = Client()
    planted = _planted_session(client)

    landing = _open(client, RESET, _issue(user, CredentialTokenPurpose.PASSWORD_RESET))

    assert landing.status_code == 302
    assert _cookie(client) != planted
    assert client.get(reverse(RESET)).status_code == 200
    done = client.post(
        reverse(RESET), {"password": OTHER_PASSWORD, "password_again": OTHER_PASSWORD}
    )
    assert done["Location"] == f"{reverse('accounts:sign_in')}?olek=parool-seatud"
    user.refresh_from_db()
    assert user.check_password(OTHER_PASSWORD)


@pytest.mark.django_db
@pytest.mark.parametrize("case", ["expired", "used", "invalidated", "invented", "wrong_purpose"])
def test_a_link_that_opens_nothing_writes_no_session(local, case):
    user = _invited()
    route = ACTIVATE
    if case == "invented":
        token = f"{'a' * 32}.{'b' * 43}"
    elif case == "wrong_purpose":
        token = _issue(user)  # an activation link, presented at the reset page
        route = RESET
    else:
        token = _issue(user)
        record = AccountCredentialToken.objects.get(user=user)
        if case == "expired":
            now = timezone.now()
            AccountCredentialToken.objects.filter(pk=record.pk).update(
                created_at=now - timedelta(hours=2), expires_at=now - timedelta(hours=1)
            )
        elif case == "used":
            record.used_at = timezone.now()
            record.save(update_fields=["used_at"])
        else:
            tokens.invalidate_outstanding(user=user, reason="test")
    before = Session.objects.count()
    client = Client()

    landing = _open(client, route, token)

    assert landing.status_code == 302 and "kood" not in landing["Location"]
    assert Session.objects.count() == before
    assert _cookie(client) == ""
    assert client.get(reverse(route)).status_code == 400
    assert Session.objects.count() == before


@pytest.mark.django_db
def test_a_link_used_once_is_refused_the_second_time(local):
    user = _invited()
    token = _issue(user)
    first = Client()
    _open(first, ACTIVATE, token)
    first.post(reverse(ACTIVATE), {"password": PASSWORD, "password_again": PASSWORD})

    again = Client()
    _open(again, ACTIVATE, token)

    assert again.get(reverse(ACTIVATE)).status_code == 400
    assert _cookie(again) == ""


@pytest.mark.django_db
def test_an_arriving_link_replaces_the_proof_the_session_held(local):
    """Kept or refused, the earlier proof is obsolete once another link arrives."""
    user = _invited()
    client = Client()
    _open(client, ACTIVATE, _issue(user))
    assert local_auth.SESSION_LINK in client.session

    _open(client, ACTIVATE, f"{'c' * 32}.{'d' * 43}")

    assert local_auth.SESSION_LINK not in client.session
    assert client.get(reverse(ACTIVATE)).status_code == 400


@pytest.mark.django_db
def test_a_proof_of_the_other_purpose_leaves_the_session(local):
    user = _invited()
    client = Client()
    _open(client, ACTIVATE, _issue(user))

    assert client.get(reverse(RESET)).status_code == 400

    assert local_auth.SESSION_LINK not in client.session


@pytest.mark.django_db
def test_a_locked_network_keeps_no_proof_even_for_a_good_link(local, settings):
    settings.LOCAL_AUTH_THROTTLE_LINK = {"max_failures": 2, "base_seconds": 300}
    client = Client()
    for n in range(2):
        _open(client, ACTIVATE, f"{'e' * 30}{n:02d}.{'f' * 43}")
    assert AuthenticationThrottle.objects.filter(
        scope=ThrottleScope.CREDENTIAL_LINK, locked_until__gt=timezone.now()
    ).exists()
    user = _invited()

    _open(client, ACTIVATE, _issue(user))

    assert local_auth.SESSION_LINK not in client.session
    assert client.get(reverse(ACTIVATE)).status_code == 400
    assert tokens.has_outstanding(user=user, purpose=CredentialTokenPurpose.ACTIVATION)


@pytest.mark.django_db
def test_each_link_that_opens_nothing_is_counted_once(local):
    client = Client()

    _open(client, ACTIVATE, f"{'1' * 32}.{'2' * 43}")
    client.get(reverse(ACTIVATE))

    row = AuthenticationThrottle.objects.get(scope=ThrottleScope.CREDENTIAL_LINK)
    assert row.failures == 1


@pytest.mark.django_db
def test_a_signed_in_person_stays_signed_in_through_their_own_reset_link(local):
    """The existing legitimate session: rotated, not ended, until the password moves."""
    user, _ = person(email="sees@koda.ee")
    client = Client()
    sign_in(client, user)
    before = _cookie(client)

    _open(client, RESET, _issue(user, CredentialTokenPurpose.PASSWORD_RESET))

    assert _cookie(client) != before
    assert local_auth.is_local_session(client.get(reverse("accounts:profile")).wsgi_request)


@pytest.mark.django_db
def test_signing_in_drops_a_proof_left_from_before(local):
    user, _ = person(email="vana.link@koda.ee")
    invited = _invited()
    client = Client()
    _open(client, ACTIVATE, _issue(invited))
    assert local_auth.SESSION_LINK in client.session

    sign_in(client, user)

    assert local_auth.SESSION_LINK not in client.session


@pytest.mark.django_db
def test_the_link_pages_send_no_referrer_and_the_secret_appears_nowhere(local, caplog):
    user = _invited()
    token = _issue(user)
    selector, verifier = token.split(".")
    client = Client()
    caplog.set_level(logging.DEBUG)

    landing = _open(client, ACTIVATE, token)
    page = client.get(reverse(ACTIVATE))
    done = client.post(reverse(ACTIVATE), {"password": PASSWORD, "password_again": PASSWORD})

    # The arriving link (its `?kood=` in the address) is answered with no-referrer.
    assert landing["Referrer-Policy"] == "no-referrer"
    # The page the browser then renders posts its form: under `no-referrer` a
    # browser sends `Origin: null` and CSRF refuses it, so it keeps the site's
    # `same-origin` (no secret is in its address; nothing goes cross-origin).
    assert page.get("Referrer-Policy", "same-origin") == "same-origin"
    for response in (landing, page, done):
        assert verifier not in response.get("Location", "")
        assert verifier not in response.content.decode()
    assert verifier not in caplog.text and selector not in caplog.text


@pytest.mark.django_db(transaction=True, serialized_rollback=True)
def test_two_browsers_holding_one_link_set_one_password(local):
    """Concurrent use: the link is spent once, under its row lock."""
    user = _invited()
    token = _issue(user)
    browsers = [Client(), Client()]
    for browser in browsers:
        _open(browser, ACTIVATE, token)
    results: list[int] = []
    start = threading.Barrier(len(browsers))

    def submit(browser: Client, password: str) -> None:
        try:
            start.wait(timeout=10)
            response = browser.post(
                reverse(ACTIVATE), {"password": password, "password_again": password}
            )
            results.append(response.status_code)
        finally:
            connection.close()

    threads = [
        threading.Thread(target=submit, args=(browser, password))
        for browser, password in zip(browsers, (PASSWORD, OTHER_PASSWORD), strict=True)
    ]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=30)

    assert sorted(results) == [302, 400]
    record = AccountCredentialToken.objects.get(user=user)
    assert record.used_at is not None
    user.refresh_from_db()
    assert user.check_password(PASSWORD) != user.check_password(OTHER_PASSWORD)


# ===========================================================================
# The shared gate (production's mode): an administrator's own enrolment link
# ===========================================================================


@pytest.fixture
def gate(settings):
    return apply_admin_gate(settings)


def _administrator_to_be() -> User:
    user = _invited(email="tulevane.haldur@koda.ee")
    from app.accounts import capabilities
    from app.accounts.enums import Capability

    user.capability_overrides = {Capability.MANAGE_ACCOUNTS.value: capabilities.ALLOW}
    user.save(update_fields=["capability_overrides"])
    return user


@pytest.mark.django_db
def test_a_gate_session_somebody_else_opened_cannot_set_the_administrators_password(gate):
    """Fixation behind the gate: the planter knows the department password too."""
    user = _administrator_to_be()
    token = _issue(user)
    attacker = pass_gate(Client())
    planted = _cookie(attacker)
    victim = Client()
    victim.cookies["sessionid"] = planted

    landing = _open(victim, ACTIVATE, token)

    assert landing.status_code == 302 and landing["Location"] == reverse(ACTIVATE)
    assert _cookie(victim) != planted
    # The planter's identifier no longer names a session at all: back to the door.
    gate_url = reverse("accounts:shared_gate")
    assert attacker.get(reverse(ACTIVATE))["Location"].startswith(gate_url)
    forged = attacker.post(
        reverse(ACTIVATE), {"password": OTHER_PASSWORD, "password_again": OTHER_PASSWORD}
    )
    assert forged.status_code == 302 and forged["Location"].startswith(gate_url)
    # Passing the gate again opens a session of their own, which holds no proof.
    pass_gate(attacker)
    assert attacker.get(reverse(ACTIVATE)).status_code == 404
    user.refresh_from_db()
    assert not user.has_local_password
    done = victim.post(reverse(ACTIVATE), {"password": PASSWORD, "password_again": PASSWORD})
    assert done["Location"] == reverse("haldus:sign_in")
    user.refresh_from_db()
    assert user.check_password(PASSWORD)


@pytest.mark.django_db
def test_a_link_before_the_gate_comes_back_to_the_bare_page_after_it(gate):
    """The shared-gate return navigation is kept, and the secret never travels in it."""
    from tests.gate import PASSWORD as GATE_PASSWORD

    user = _administrator_to_be()
    token = _issue(user)
    client = Client()

    arriving = _open(client, ACTIVATE, token)

    gate_url = arriving["Location"]
    assert gate_url.startswith(reverse("accounts:shared_gate"))
    assert "kood" not in gate_url and token.split(".")[1] not in gate_url
    assert arriving["Referrer-Policy"] == "no-referrer"
    before_gate = _cookie(client)
    after = client.post(gate_url, {"password": GATE_PASSWORD})
    assert after["Location"] == reverse(ACTIVATE)
    assert _cookie(client) != before_gate
    assert client.get(reverse(ACTIVATE)).status_code == 200


@pytest.mark.django_db
def test_before_the_gate_an_invalid_link_writes_no_session(gate):
    before = Session.objects.count()
    client = Client()

    arriving = _open(client, ACTIVATE, f"{'9' * 32}.{'8' * 43}")

    assert arriving["Location"] == reverse("accounts:shared_gate")
    assert Session.objects.count() == before


@pytest.mark.django_db
def test_before_the_gate_a_link_for_somebody_who_may_not_administer_writes_no_session(gate):
    user = _invited(email="tavaline@koda.ee")
    before = Session.objects.count()

    arriving = _open(Client(), ACTIVATE, _issue(user))

    assert arriving["Location"] == reverse("accounts:shared_gate")
    assert Session.objects.count() == before


@pytest.mark.django_db
def test_the_persona_chosen_behind_the_gate_survives_the_rotation(gate):
    """Persona selection is untouched: the rotation keeps what the session holds."""
    from tests.admin_session import choose

    persona = factories.UserFactory(role=UserRole.SPECIALIST)
    user = _administrator_to_be()
    client = pass_gate(Client())
    choose(client, persona)

    _open(client, ACTIVATE, _issue(user))

    assert client.get(reverse(ACTIVATE)).wsgi_request.user.pk == persona.pk


@pytest.mark.django_db
def test_a_real_browser_can_post_the_link_page_under_csrf(local):
    """r96 integration: the browser suite's first administrator could not set a
    password («Leht on aegunud»). A browser posts with the Origin its page's
    referrer policy allows — `null` under `no-referrer`, which Django's CSRF check
    refuses. With the page's policy as served, the form must go through."""
    user = _invited()
    token = _issue(user)
    client = Client(enforce_csrf_checks=True)

    _open(client, ACTIVATE, token)
    page = client.get(reverse(ACTIVATE))
    policy = page.get("Referrer-Policy", "same-origin")
    origin = "null" if policy == "no-referrer" else "http://testserver"
    done = client.post(
        reverse(ACTIVATE),
        {
            "password": PASSWORD,
            "password_again": PASSWORD,
            "csrfmiddlewaretoken": page.context["csrf_token"],
        },
        HTTP_ORIGIN=origin,
    )
    assert done.status_code == 302, done.status_code
    user.refresh_from_db()
    assert user.has_usable_password()
