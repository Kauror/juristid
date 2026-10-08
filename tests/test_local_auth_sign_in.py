"""Personal sign-in: e-mail → password → second factor → Juristid (docs/adr/0145).

Every test here runs the real views under `AUTH_MODE=local_password`, with
synthetic identities in the test database. The properties under test are the
ones a person or an attacker meets at the door: one refusal for every failure,
nobody signed in until every factor is proved, throttles that hold across
workers without becoming a lockout primitive, and sessions that end when they
must.
"""

from __future__ import annotations

from datetime import timedelta

import pyotp
import pytest
from django.urls import reverse
from django.utils import timezone

from app.accounts import local_auth, mfa
from app.accounts.enums import ProvisioningState, ThrottleScope, UserRole
from app.accounts.models import AuthenticationThrottle, User
from app.audit.enums import SecurityEventType
from app.audit.models import SecurityAuditEvent
from tests import factories
from tests.local_auth import PASSWORD, apply_local_password, code, person, sign_in

pytestmark = pytest.mark.django_db


@pytest.fixture(autouse=True)
def _local_password(settings):
    return apply_local_password(settings)


SIGN_IN = "accounts:sign_in"


def _refusal_text(response) -> str:
    return response.content.decode()


def _signed_in(client) -> bool:
    return local_auth.SESSION_USER in client.session


# -- the happy path ----------------------------------------------------------------


def test_a_correct_password_signs_the_person_in(client):
    user, _ = person()

    response = sign_in(client, user)

    assert response.status_code == 302
    assert response["Location"] == reverse("core:home")
    assert client.session[local_auth.SESSION_USER] == str(user.pk)
    user.refresh_from_db()
    assert user.last_authenticated_at is not None
    event = SecurityAuditEvent.objects.filter(
        event_type=SecurityEventType.AUTHENTICATION_SUCCEEDED, actor=user
    ).get()
    assert event.detail["path"] == "local_password"
    assert event.detail["authenticated_via"] == "LOCAL_PASSWORD"
    # And the application opens.
    assert client.get(reverse("matters:my_work")).status_code == 200


def test_the_sign_in_rotates_the_session_key(client):
    user, _ = person()
    client.get(reverse(SIGN_IN))
    before = client.session.session_key

    sign_in(client, user)

    assert client.session.session_key != before


def test_a_safe_next_is_honoured_and_an_external_one_is_not(client):
    user, _ = person()
    client.get(reverse(SIGN_IN), {"next": "/teemad/"})
    assert sign_in(client, user)["Location"] == "/teemad/"

    client.logout()
    client.get(reverse(SIGN_IN), {"next": "https://pahatahtlik.example/"})
    assert sign_in(client, user)["Location"] == reverse("core:home")


def test_a_password_manager_can_fill_both_fields(client):
    page = client.get(reverse(SIGN_IN)).content.decode()

    assert 'autocomplete="username"' in page
    assert 'autocomplete="current-password"' in page
    assert "onpaste" not in page


# -- one refusal for every failure ---------------------------------------------------


@pytest.mark.parametrize(
    "case",
    ["wrong_password", "unknown_account", "pending", "invited", "disabled", "no_local_password"],
)
def test_every_failure_reads_the_same(client, case):
    """A stranger cannot tell which of these they hit."""
    if case == "unknown_account":
        email = "keegi.ei.ole@koda.ee"
    else:
        user, _ = person()
        email = user.upn
        if case == "pending":
            User.objects.filter(pk=user.pk).update(
                is_active=False, provisioning_state=ProvisioningState.PENDING
            )
        elif case == "invited":
            User.objects.filter(pk=user.pk).update(
                is_active=False, provisioning_state=ProvisioningState.INVITED
            )
        elif case == "disabled":
            User.objects.filter(pk=user.pk).update(is_active=False)
        elif case == "no_local_password":
            User.objects.filter(pk=user.pk).update(local_password_set_at=None)
    password = "vale parool ka pikk küll" if case == "wrong_password" else PASSWORD

    response = client.post(reverse(SIGN_IN), {"email": email, "password": password})

    assert response.status_code == 400
    assert local_auth.SIGN_IN_REFUSED in _refusal_text(response)
    assert not _signed_in(client)
    # The audit knows which one it was; the page does not say.
    event = SecurityAuditEvent.objects.filter(
        event_type=SecurityEventType.AUTHENTICATION_FAILED
    ).latest("occurred_at")
    expected = {
        "wrong_password": "bad_password",
        "unknown_account": "unknown_account",
        "pending": "not_activated",
        "invited": "not_activated",
        "disabled": "inactive",
        "no_local_password": "no_local_password",
    }[case]
    assert event.detail["reason"] == expected


def test_a_synthetic_account_is_refused_on_a_real_data_deployment(client, settings):
    user, _ = person()
    assert user.is_synthetic
    settings.REAL_DATA_ALLOWED = True

    response = sign_in(client, user)

    assert response.status_code == 400
    assert not _signed_in(client)


def test_a_password_typed_into_the_address_field_never_reaches_the_audit(client):
    client.post(reverse(SIGN_IN), {"email": PASSWORD, "password": PASSWORD})

    event = SecurityAuditEvent.objects.filter(
        event_type=SecurityEventType.AUTHENTICATION_FAILED
    ).latest("occurred_at")
    assert event.detail["email"] == "(malformed)"
    assert PASSWORD not in str(event.detail)


def test_the_address_is_normalised(client):
    user, _ = person(email="mari.maasikas@koda.ee")

    response = client.post(
        reverse(SIGN_IN), {"email": "  Mari.Maasikas@KODA.ee ", "password": PASSWORD}
    )

    assert response.status_code == 302
    assert client.session[local_auth.SESSION_USER] == str(user.pk)


def test_a_password_is_compared_exactly_never_trimmed(client):
    user, _ = person()

    response = client.post(reverse(SIGN_IN), {"email": user.upn, "password": f" {PASSWORD} "})

    assert response.status_code == 400


# -- the second factor ---------------------------------------------------------------


def test_a_correct_password_with_a_second_factor_is_not_yet_a_sign_in(client):
    user, _secret = person(totp=True)

    response = client.post(reverse(SIGN_IN), {"email": user.upn, "password": PASSWORD})

    assert response["Location"] == reverse("accounts:sign_in_second_factor")
    assert not _signed_in(client)
    assert "_auth_user_id" not in client.session
    # Nothing behind the door opens on a pending marker.
    blocked = client.get(reverse("matters:my_work"))
    assert blocked.status_code == 302
    assert blocked["Location"].startswith(reverse(SIGN_IN))


def test_the_authenticator_code_completes_the_sign_in(client):
    user, secret = person(totp=True)

    response = sign_in(client, user, secret=secret)

    assert response.status_code == 302
    assert _signed_in(client)
    assert local_auth.SESSION_SECOND_FACTOR_AT in client.session


def test_a_wrong_code_is_refused(client):
    user, _secret = person(totp=True)
    client.post(reverse(SIGN_IN), {"email": user.upn, "password": PASSWORD})

    response = client.post(reverse("accounts:sign_in_second_factor"), {"code": "000000"})

    assert response.status_code == 400
    assert local_auth.SECOND_FACTOR_REFUSED in response.content.decode()
    assert not _signed_in(client)


def test_a_spent_code_cannot_be_replayed(client):
    user, secret = person(totp=True)
    totp = pyotp.TOTP(secret)
    current = totp.now()
    client.post(reverse(SIGN_IN), {"email": user.upn, "password": PASSWORD})
    assert (
        client.post(reverse("accounts:sign_in_second_factor"), {"code": current}).status_code == 302
    )

    second = factories_client()
    second.post(reverse(SIGN_IN), {"email": user.upn, "password": PASSWORD})
    response = second.post(reverse("accounts:sign_in_second_factor"), {"code": current})

    assert response.status_code == 400
    assert local_auth.SESSION_USER not in second.session


def factories_client():
    from django.test import Client

    return Client()


def test_a_recovery_code_works_once(client):
    user, _ = person(totp=True)
    codes = mfa.generate_recovery_codes(user)

    client.post(reverse(SIGN_IN), {"email": user.upn, "password": PASSWORD})
    first = client.post(reverse("accounts:sign_in_second_factor"), {"code": codes[0]})
    assert first.status_code == 302
    assert SecurityAuditEvent.objects.filter(
        event_type=SecurityEventType.MFA_RECOVERY_CODE_USED, actor=user
    ).exists()

    other = factories_client()
    other.post(reverse(SIGN_IN), {"email": user.upn, "password": PASSWORD})
    replay = other.post(reverse("accounts:sign_in_second_factor"), {"code": codes[0]})
    assert replay.status_code == 400
    assert mfa.remaining_recovery_codes(user) == len(codes) - 1


def test_a_pending_second_factor_expires(client):
    user, secret = person(totp=True)
    client.post(reverse(SIGN_IN), {"email": user.upn, "password": PASSWORD})
    session = client.session
    session[local_auth.SESSION_PENDING]["at"] = (
        timezone.now() - timedelta(seconds=local_auth.PENDING_SECONDS + 5)
    ).isoformat()
    session.save()

    response = client.post(reverse("accounts:sign_in_second_factor"), {"code": code(secret, user)})

    assert response["Location"] == reverse(SIGN_IN)
    assert not _signed_in(client)


def test_second_factor_attempts_are_throttled(client):
    user, secret = person(totp=True)
    client.post(reverse(SIGN_IN), {"email": user.upn, "password": PASSWORD})
    for _ in range(4):
        client.post(reverse("accounts:sign_in_second_factor"), {"code": "111111"})
    locked = client.post(reverse("accounts:sign_in_second_factor"), {"code": "111111"})
    assert locked.status_code == 429

    # Locked means locked: even the right code waits.
    still = client.post(reverse("accounts:sign_in_second_factor"), {"code": code(secret, user)})
    assert still.status_code == 429
    assert not _signed_in(client)


# -- throttling ------------------------------------------------------------------------


def _wrong(client, email, *, address="203.0.113.10"):
    return client.post(
        reverse(SIGN_IN),
        {"email": email, "password": "täiesti vale parool siin"},
        HTTP_CF_CONNECTING_IP=address,
    )


def test_five_failures_from_one_network_lock_that_pair(client):
    user, _ = person()
    statuses = [_wrong(client, user.upn).status_code for _ in range(5)]
    assert statuses == [400, 400, 400, 400, 429]

    locked = client.post(
        reverse(SIGN_IN),
        {"email": user.upn, "password": PASSWORD},
        HTTP_CF_CONNECTING_IP="203.0.113.10",
    )
    assert locked.status_code == 429
    assert not _signed_in(client)


def test_a_locked_pair_does_not_lock_the_person_out_from_elsewhere(client):
    """A stranger who knows an address cannot keep its owner out from their own desk."""
    user, _ = person()
    for _ in range(5):
        _wrong(client, user.upn, address="198.51.100.66")

    response = client.post(
        reverse(SIGN_IN),
        {"email": user.upn, "password": PASSWORD},
        HTTP_CF_CONNECTING_IP="203.0.113.77",
    )

    assert response.status_code == 302


def test_an_unknown_address_is_throttled_exactly_like_a_real_one(client):
    user, _ = person()
    real = [_wrong(client, user.upn, address="203.0.113.1").status_code for _ in range(6)]
    nobody = [
        _wrong(client, "keegi.pole@koda.ee", address="203.0.113.2").status_code for _ in range(6)
    ]

    assert real == nobody


def test_the_counters_live_in_the_database_and_name_nobody(client):
    """Shared by every gunicorn worker, and an HMAC rather than an address."""
    user, _ = person()
    _wrong(client, user.upn)

    rows = AuthenticationThrottle.objects.all()
    assert {row.scope for row in rows} == {
        ThrottleScope.SIGN_IN_PAIR,
        ThrottleScope.SIGN_IN_ACCOUNT,
        ThrottleScope.SIGN_IN_NETWORK,
    }
    for row in rows:
        assert user.upn not in row.key_digest
        assert "203.0.113" not in row.key_digest


def test_a_correct_password_clears_the_pair_but_not_the_network(client):
    user, _ = person()
    for _ in range(3):
        _wrong(client, user.upn)
    sign_in_response = client.post(
        reverse(SIGN_IN),
        {"email": user.upn, "password": PASSWORD},
        HTTP_CF_CONNECTING_IP="203.0.113.10",
    )
    assert sign_in_response.status_code == 302

    remaining = set(AuthenticationThrottle.objects.values_list("scope", flat=True))
    assert remaining == {ThrottleScope.SIGN_IN_NETWORK}


# -- the session -------------------------------------------------------------------------


def _age(client, key, seconds):
    session = client.session
    session[key] = (timezone.now() - timedelta(seconds=seconds)).isoformat()
    session.save()


def test_an_idle_session_ends(client, settings):
    user, _ = person()
    sign_in(client, user)
    _age(client, local_auth.SESSION_LAST_SEEN, settings.LOCAL_AUTH_SESSION_IDLE_SECONDS + 60)

    response = client.get(reverse("matters:my_work"))

    assert response["Location"] == f"{reverse(SIGN_IN)}?olek=aegunud"
    assert SecurityAuditEvent.objects.filter(
        event_type=SecurityEventType.SESSION_ENDED, detail__reason="idle_timeout"
    ).exists()
    assert client.get(reverse("matters:my_work")).status_code == 302


def test_a_session_ends_at_its_absolute_limit_however_busy(client, settings):
    user, _ = person()
    sign_in(client, user)
    _age(
        client,
        local_auth.SESSION_AUTHENTICATED_AT,
        settings.LOCAL_AUTH_SESSION_ABSOLUTE_SECONDS + 60,
    )

    response = client.get(reverse("matters:my_work"))

    assert response["Location"] == f"{reverse(SIGN_IN)}?olek=aegunud"


def test_a_security_change_ends_every_open_session(client):
    """The epoch is folded into the session hash; bumping it signs the account out everywhere."""
    from django.db.models import F

    user, _ = person()
    sign_in(client, user)
    assert client.get(reverse("matters:my_work")).status_code == 200

    User.objects.filter(pk=user.pk).update(security_epoch=F("security_epoch") + 1)

    response = client.get(reverse("matters:my_work"))
    assert response.status_code == 302
    assert response["Location"].startswith(reverse(SIGN_IN))


def test_a_switched_off_account_is_told_so_at_its_next_request(client):
    user, _ = person()
    sign_in(client, user)
    User.objects.filter(pk=user.pk).update(is_active=False)

    response = client.get(reverse("matters:my_work"))

    assert response["Location"] == f"{reverse(SIGN_IN)}?olek=valjas"
    page = client.get(response["Location"]).content.decode()
    assert "välja lülitatud" in page


def test_a_persona_session_from_another_mode_is_not_an_identity_here(client):
    """`force_login` is what the shared gate's persona choice does; it proves nothing here."""
    user, _ = person()
    client.force_login(user)

    response = client.get(reverse("matters:my_work"))

    assert response.status_code == 302
    assert response["Location"].startswith(reverse(SIGN_IN))
    assert SecurityAuditEvent.objects.filter(
        event_type=SecurityEventType.SESSION_ENDED, detail__reason="not_signed_in_here"
    ).exists()


def test_signing_out_is_recorded_and_ends_the_session(client):
    user, _ = person()
    sign_in(client, user)

    response = client.post(reverse("accounts:sign_out"))

    assert response["Location"] == f"{reverse(SIGN_IN)}?olek=valjunud"
    assert SecurityAuditEvent.objects.filter(
        event_type=SecurityEventType.SIGNED_OUT, actor=user
    ).exists()
    assert client.get(reverse("matters:my_work")).status_code == 302


def test_an_htmx_request_without_a_session_gets_a_401_not_a_page(client):
    response = client.post(
        reverse("matters:matter_create"),
        HTTP_HX_REQUEST="true",
    )
    assert response.status_code == 401


# -- mandatory second factor -------------------------------------------------------------


def test_an_administrator_without_a_second_factor_must_enrol_first(client):
    user, _ = person(manage=True)

    response = sign_in(client, user)

    assert response["Location"] == reverse("accounts:security_enrol")
    blocked = client.get(reverse("matters:my_work"))
    assert blocked["Location"] == reverse("accounts:security_enrol")
    assert client.get(reverse("account_admin:list")).status_code == 302


def test_enrolling_releases_the_administrator(client):
    user, _ = person(manage=True)
    sign_in(client, user)
    page = client.get(reverse("accounts:security_enrol"))
    secret = page.context["enrolment"].secret
    assert "<svg" in page.content.decode()

    done = client.post(reverse("accounts:security_enrol"), {"code": pyotp.TOTP(secret).now()})

    assert done.status_code == 200
    assert len(done.context["codes"]) == mfa.RECOVERY_CODE_COUNT
    assert mfa.has_second_factor(user)
    assert client.get(reverse("account_admin:list")).status_code == 200
    assert SecurityAuditEvent.objects.filter(
        event_type=SecurityEventType.MFA_ENROLLED, actor=user
    ).exists()


def test_a_wrong_enrolment_code_stores_nothing(client):
    user, _ = person(manage=True)
    sign_in(client, user)
    client.get(reverse("accounts:security_enrol"))

    response = client.post(reverse("accounts:security_enrol"), {"code": "123456"})

    assert response.status_code == 400
    assert not mfa.has_second_factor(user)


def test_the_all_users_policy_requires_everybody_to_enrol(client, settings):
    settings.LOCAL_AUTH_MFA_REQUIRED_FOR_ALL = True
    user, _ = person(role=UserRole.SPECIALIST)

    response = sign_in(client, user)

    assert response["Location"] == reverse("accounts:security_enrol")


def test_turning_the_all_users_policy_off_never_relaxes_an_administrator(client, settings):
    settings.LOCAL_AUTH_MFA_REQUIRED_FOR_ALL = False
    user, _ = person(manage=True)

    assert local_auth.second_factor_mandatory(user)
    assert sign_in(client, user)["Location"] == reverse("accounts:security_enrol")


def test_an_administrator_cannot_remove_their_own_second_factor(client):
    user, secret = person(manage=True, delegate=True, totp=True)
    sign_in(client, user, secret=secret)

    response = client.post(reverse("accounts:security_remove"))

    assert response.status_code == 302
    assert mfa.has_second_factor(user)


def test_an_ordinary_person_may_remove_theirs_after_proving_it_is_them(client, settings):
    user, secret = person(totp=True)
    sign_in(client, user, secret=secret)
    _age(client, local_auth.SESSION_STRONG_AT, settings.LOCAL_AUTH_REAUTH_SECONDS + 60)

    asked = client.post(reverse("accounts:security_remove"))
    assert asked["Location"].startswith(reverse("accounts:reauthenticate"))
    assert mfa.has_second_factor(user)

    proved = client.post(
        reverse("accounts:reauthenticate"),
        {"password": PASSWORD, "code": code(secret, user), "next": reverse("accounts:security")},
    )
    assert proved.status_code == 302
    client.post(reverse("accounts:security_remove"))

    assert not mfa.has_second_factor(user)
    assert SecurityAuditEvent.objects.filter(
        event_type=SecurityEventType.MFA_REMOVED, actor=user
    ).exists()


def test_new_recovery_codes_replace_the_old_ones(client):
    user, secret = person(totp=True)
    old = mfa.generate_recovery_codes(user)
    sign_in(client, user, secret=secret)

    response = client.post(reverse("accounts:security_recovery_codes"))

    new = response.context["codes"]
    assert set(new).isdisjoint(old)
    assert not mfa.use_recovery_code(user, old[0])
    assert mfa.use_recovery_code(user, new[0])


def test_the_secret_is_encrypted_at_rest(client):
    user, secret = person(totp=True)

    stored = user.totp_device.encrypted_secret

    assert secret not in stored
    assert mfa._decrypt(stored) == secret


def test_no_other_person_ever_appears_on_the_sign_in_page(client):
    """An unauthenticated visitor learns nothing about who works here."""
    factories.UserFactory(display_name="Salajane Kolleeg")

    page = client.get(reverse(SIGN_IN)).content.decode()

    assert "Salajane Kolleeg" not in page
