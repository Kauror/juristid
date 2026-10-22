"""A pending second factor is bound to the credentials it was proved against.

Dormant personal sign-in (docs/adr/0145): a correct password with a second factor
still to come leaves a *pending* marker, and the code completes the sign-in.
The marker used to name only the account and the time, so a password reset or
change in the minutes between the password and the code — the owner reacting to
"somebody has my password" — left the attacker's pending sign-in completable
with the old password already spent (csf_c366770b, docs/adr/0183).

Now the marker carries a keyed binding of the account's credential stamp and
security epoch, checked again when the code arrives. Nothing here enables MFA
anywhere: every test runs under its own `local_password` fixture.
"""

from __future__ import annotations

from datetime import timedelta

import pytest
from django.contrib.sessions.backends.db import SessionStore
from django.test import RequestFactory
from django.urls import reverse
from django.utils import timezone

from app.accounts import credentials, local_auth, tokens
from app.accounts.enums import CredentialTokenPurpose
from app.accounts.models import User
from app.audit.enums import SecurityEventType
from app.audit.models import SecurityAuditEvent
from tests.local_auth import OTHER_PASSWORD, PASSWORD, apply_local_password, code, person

pytestmark = pytest.mark.django_db

SIGN_IN = "accounts:sign_in"
SECOND_FACTOR = "accounts:sign_in_second_factor"


@pytest.fixture(autouse=True)
def _local_password(settings):
    return apply_local_password(settings)


def _password_step(client, user) -> None:
    response = client.post(reverse(SIGN_IN), {"email": user.upn, "password": PASSWORD})
    assert response["Location"] == reverse(SECOND_FACTOR)
    assert local_auth.SESSION_PENDING in client.session


def _signed_in(client) -> bool:
    return local_auth.SESSION_USER in client.session


def _assert_restarted(client, response) -> None:
    assert response.status_code == 302
    assert response["Location"] == reverse(SIGN_IN)
    assert not _signed_in(client)
    assert "_auth_user_id" not in client.session
    assert local_auth.SESSION_PENDING not in client.session


def _refusal_recorded(user) -> bool:
    return SecurityAuditEvent.objects.filter(
        event_type=SecurityEventType.AUTHENTICATION_FAILED,
        subject_id=str(user.pk),
        detail__stage="second_factor",
        detail__reason="credentials_changed",
    ).exists()


def test_an_unchanged_account_completes_the_sign_in(client):
    user, secret = person(totp=True)
    _password_step(client, user)

    response = client.post(reverse(SECOND_FACTOR), {"code": code(secret, user)})

    assert response.status_code == 302
    assert response["Location"] != reverse(SIGN_IN)
    assert _signed_in(client)


def test_a_password_reset_during_the_wait_ends_it(client):
    user, secret = person(totp=True)
    _password_step(client, user)

    issued = tokens.issue(user=user, purpose=CredentialTokenPurpose.PASSWORD_RESET)
    credentials.set_password_with_link(
        proof=tokens.proof_of(issued.token),
        purpose=CredentialTokenPurpose.PASSWORD_RESET,
        password=OTHER_PASSWORD,
    )

    response = client.post(reverse(SECOND_FACTOR), {"code": code(secret, user)})

    _assert_restarted(client, response)
    assert _refusal_recorded(user)


def test_a_password_change_during_the_wait_ends_it(client):
    user, secret = person(totp=True)
    _password_step(client, user)

    # The owner, signed in elsewhere, changes the password.
    request = RequestFactory().post("/konto/parool/")
    request.session = SessionStore()
    request.user = user
    outcome = credentials.change_password(request, user=user, current=PASSWORD, new=OTHER_PASSWORD)
    assert outcome.status == "changed"

    response = client.post(reverse(SECOND_FACTOR), {"code": code(secret, user)})

    _assert_restarted(client, response)


def test_a_security_epoch_bump_during_the_wait_ends_it(client):
    """A role change, a factor reset, a deactivation and reactivation: anything epoch-worthy."""
    user, secret = person(totp=True)
    _password_step(client, user)

    credentials._bump_epoch(user)

    response = client.post(reverse(SECOND_FACTOR), {"code": code(secret, user)})

    _assert_restarted(client, response)
    assert _refusal_recorded(user)


def test_the_code_page_itself_restarts_after_a_change(client):
    user, _secret = person(totp=True)
    _password_step(client, user)

    credentials._bump_epoch(user)

    _assert_restarted(client, client.get(reverse(SECOND_FACTOR)))


def test_a_change_while_the_code_is_being_checked_is_not_outrun(client, monkeypatch):
    """The binding is asked again after the code, with the account read afresh."""
    user, secret = person(totp=True)
    _password_step(client, user)

    real_verify = local_auth.mfa.verify_totp

    def verify_then_change(account, supplied):
        accepted = real_verify(account, supplied)
        User.objects.filter(pk=account.pk).update(security_epoch=account.security_epoch + 1)
        return accepted

    monkeypatch.setattr(local_auth.mfa, "verify_totp", verify_then_change)

    response = client.post(reverse(SECOND_FACTOR), {"code": code(secret, user)})

    _assert_restarted(client, response)


def test_an_expired_wait_still_ends(client):
    user, secret = person(totp=True)
    _password_step(client, user)
    session = client.session
    session[local_auth.SESSION_PENDING]["at"] = (
        timezone.now() - timedelta(seconds=local_auth.PENDING_SECONDS + 5)
    ).isoformat()
    session.save()

    response = client.post(reverse(SECOND_FACTOR), {"code": code(secret, user)})

    _assert_restarted(client, response)


def test_a_marker_without_a_binding_completes_nothing(client):
    """What a marker written before this change looks like: it is no longer enough."""
    user, secret = person(totp=True)
    _password_step(client, user)
    session = client.session
    session[local_auth.SESSION_PENDING].pop("binding")
    session.save()

    response = client.post(reverse(SECOND_FACTOR), {"code": code(secret, user)})

    _assert_restarted(client, response)


def test_the_binding_is_keyed_and_holds_no_password_material(client):
    user, _secret = person(totp=True)
    _password_step(client, user)

    binding = client.session[local_auth.SESSION_PENDING]["binding"]

    user.refresh_from_db()
    assert binding == local_auth.pending_binding(user)
    assert binding != tokens.credential_stamp(user)
    assert user.password not in binding
    assert PASSWORD not in str(dict(client.session.items()))
