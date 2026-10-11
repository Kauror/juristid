"""A lost or changed MFA key, and the audited way back (AUD-051 AU-6).

What the key loss really does is pinned first, because the runbook used to say
the opposite ("unenrols every authenticator"): the device stays on record, the
person still counts as having a second factor and is never asked to enrol, and
no code from their phone is accepted again. Recovery codes are hashed, not
encrypted with the key, and keep working.

Then the remedy, `manage.py reset_second_factor`: one named account, a note the
audit keeps, the device and codes retired, the second factor still mandatory —
and a read-only listing of everybody the current key no longer opens
(docs/LOCAL_AUTH_ACTIVATION_RUNBOOK.md §15).
"""

from __future__ import annotations

from io import StringIO

import pyotp
import pytest
from django.core.management import call_command
from django.core.management.base import CommandError

from app.accounts import credentials, local_auth, mfa
from app.accounts.models import RecoveryCode, TotpDevice, User
from app.audit.enums import SecurityEventType
from app.audit.models import SecurityAuditEvent
from tests.gate import apply_shared_gate
from tests.local_auth import apply_local_password, code, person
from tests.refusals import refused

pytestmark = pytest.mark.django_db

OLD_KEY = "the-key-the-authenticators-were-enrolled-under"
NEW_KEY = "a-different-key-after-the-old-one-was-lost"


@pytest.fixture
def local(settings):
    apply_local_password(settings)
    settings.LOCAL_AUTH_MFA_ENCRYPTION_KEY = OLD_KEY
    return settings


@pytest.fixture
def administrator_under_the_old_key(local):
    """An administrator (the second factor is mandatory) enrolled; then the key changes."""
    user, secret = person(manage=True, delegate=True, totp=True)
    recovery = mfa.generate_recovery_codes(user)
    local.LOCAL_AUTH_MFA_ENCRYPTION_KEY = NEW_KEY
    return user, secret, recovery


def reset(*args: str) -> str:
    out = StringIO()
    call_command("reset_second_factor", *args, stdout=out)
    return out.getvalue()


# -- what a changed key really does ------------------------------------------------------


def test_a_changed_key_breaks_the_authenticator_in_place(administrator_under_the_old_key):
    user, secret, recovery = administrator_under_the_old_key

    assert mfa.has_second_factor(user)
    assert not local_auth.must_enrol(user)
    assert not mfa.verify_totp(user, code(secret, user))
    assert mfa.use_recovery_code(user, recovery[0])
    assert [device.user_id for device in mfa.unreadable_devices()] == [user.pk]


def test_the_original_key_brings_every_authenticator_back(administrator_under_the_old_key, local):
    """The runbook's first step: restore the key and nothing else is needed."""
    user, secret, _ = administrator_under_the_old_key
    local.LOCAL_AUTH_MFA_ENCRYPTION_KEY = OLD_KEY

    assert mfa.verify_totp(user, code(secret, user))
    assert mfa.unreadable_devices() == []


# -- the operator's reset ---------------------------------------------------------------


def test_the_command_resets_the_account_and_enrolment_under_the_new_key_works(
    administrator_under_the_old_key,
):
    user, _, _ = administrator_under_the_old_key
    epoch = user.security_epoch

    out = reset("--email", user.upn.upper(), "--note", "Operaator Olev: MFA võti kadus")

    user.refresh_from_db()
    assert not TotpDevice.objects.filter(user=user).exists()
    assert not RecoveryCode.objects.filter(user=user).exists()
    assert not mfa.has_second_factor(user)
    assert local_auth.must_enrol(user), "the second factor stays mandatory"
    assert user.security_epoch == epoch + 1, "their open sessions end"
    assert "Second factor reset" in out

    enrolment = mfa.begin_enrolment(user)
    totp = pyotp.TOTP(enrolment.secret)
    assert mfa.confirm_enrolment(user, sealed=enrolment.sealed, code=totp.now())
    assert mfa.secret_is_readable(TotpDevice.objects.get(user=user))
    assert not local_auth.must_enrol(user)


def test_the_reset_is_in_the_security_audit_with_its_note(administrator_under_the_old_key):
    user, _, _ = administrator_under_the_old_key

    reset("--email", user.upn, "--note", "Operaator Olev: MFA võti kadus, kinnitas Kaur")

    event = SecurityAuditEvent.objects.get(
        event_type=SecurityEventType.MFA_REMOVED, subject_id=str(user.pk)
    )
    assert event.actor is None
    assert event.detail["by"] == "operator"
    assert event.detail["via"] == "manage.py reset_second_factor"
    assert event.detail["note"] == "Operaator Olev: MFA võti kadus, kinnitas Kaur"
    assert event.detail["secret_was_readable"] is False


@pytest.mark.parametrize("note", ["", "   "])
def test_the_command_refuses_without_a_note(administrator_under_the_old_key, note):
    user, _, _ = administrator_under_the_old_key

    with pytest.raises(CommandError, match="Märkus on kohustuslik"):
        reset("--email", user.upn, "--note", note)

    assert TotpDevice.objects.filter(user=user).exists()
    assert not SecurityAuditEvent.objects.filter(event_type=SecurityEventType.MFA_REMOVED).exists()


def test_the_service_refuses_without_a_note(administrator_under_the_old_key):
    user, _, _ = administrator_under_the_old_key

    with refused(credentials.SECOND_FACTOR_RESET_NEEDS_A_NOTE):
        credentials.operator_reset_second_factor(email=user.upn, note=" ")

    assert TotpDevice.objects.filter(user=user).exists()


def test_a_dry_run_writes_nothing(administrator_under_the_old_key):
    user, _, _ = administrator_under_the_old_key
    before = (
        TotpDevice.objects.count(),
        RecoveryCode.objects.count(),
        SecurityAuditEvent.objects.count(),
        User.objects.get(pk=user.pk).security_epoch,
    )

    out = reset("--email", user.upn, "--note", "proov", "--dry-run")

    after = (
        TotpDevice.objects.count(),
        RecoveryCode.objects.count(),
        SecurityAuditEvent.objects.count(),
        User.objects.get(pk=user.pk).security_epoch,
    )
    assert after == before
    assert "Dry run" in out
    assert "can no longer be read" in out


def test_an_unknown_address_or_nothing_to_reset_is_refused(local):
    without, _ = person()

    with refused(credentials.SECOND_FACTOR_RESET_UNKNOWN_ACCOUNT):
        credentials.operator_reset_second_factor(email="keegi@koda.ee", note="proov")
    with refused(credentials.SECOND_FACTOR_RESET_NOTHING_TO_RESET):
        credentials.operator_reset_second_factor(email=without.upn, note="proov")

    assert not SecurityAuditEvent.objects.filter(event_type=SecurityEventType.MFA_REMOVED).exists()


def test_the_command_works_after_a_rollback_to_the_gate(administrator_under_the_old_key, settings):
    """A key can be lost after a rollback too; the devices are still there."""
    user, _, _ = administrator_under_the_old_key
    apply_shared_gate(settings)

    reset("--email", user.upn, "--note", "pärast tagasipööramist")

    assert not mfa.has_second_factor(user)


# -- the blast radius ------------------------------------------------------------------


def test_the_listing_names_only_the_unreadable_and_writes_nothing(local):
    lost, _ = person(totp=True)
    local.LOCAL_AUTH_MFA_ENCRYPTION_KEY = NEW_KEY
    fine, _ = person(totp=True)
    before = (TotpDevice.objects.count(), SecurityAuditEvent.objects.count())

    out = reset("--list-undecryptable")

    assert lost.upn in out
    assert fine.upn not in out
    assert (TotpDevice.objects.count(), SecurityAuditEvent.objects.count()) == before


def test_the_listing_says_so_when_every_key_opens(local):
    person(totp=True)

    assert "Every confirmed authenticator opens" in reset("--list-undecryptable")
