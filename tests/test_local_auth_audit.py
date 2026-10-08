"""The security audit records every account event and no secret (docs/adr/0145 §9).

One long journey through every flow that handles a secret — invitation,
activation, enrolment, sign-in with a code and a recovery code, reset, change —
and then a scan of every audit row, every outgoing message's metadata and every
log line for the secrets it handled. Append-only is checked too: the trail is
the existing `SecurityAuditEvent`, unchanged in kind.
"""

from __future__ import annotations

import json
import logging
import re

import pyotp
import pytest
from django.core import mail
from django.test import Client
from django.urls import reverse

from app.accounts import administration, mfa
from app.audit.enums import SecurityEventType
from app.audit.models import SecurityAuditEvent
from app.core.errors import ImmutableRecordError
from tests.local_auth import LINK_BASE, OTHER_PASSWORD, PASSWORD, apply_local_password, person

pytestmark = pytest.mark.django_db


@pytest.fixture(autouse=True)
def _local_password(settings):
    return apply_local_password(settings)


def _link(message) -> str:
    return re.search(rf"{re.escape(LINK_BASE)}(\S+)", message.body).group(1)


def test_a_whole_lifecycle_leaves_a_complete_trail_and_no_secret(caplog):
    caplog.set_level(logging.DEBUG)
    admin, _ = person(manage=True, delegate=True, totp=True)
    secrets_seen: list[str] = [PASSWORD, OTHER_PASSWORD]

    user = administration.create_account(
        actor=admin, email="tee.lopuni@koda.ee", display_name="Tee Lõpuni", role="SPECIALIST"
    )
    administration.send_setup_link(actor=admin, user=user)
    activation = _link(mail.outbox[-1])
    secrets_seen.append(activation.split("kood=")[1])

    browser = Client()
    target = browser.get(activation)["Location"]
    browser.post(target, {"password": PASSWORD, "password_again": PASSWORD})
    browser.post(reverse("accounts:sign_in"), {"email": user.upn, "password": PASSWORD})
    page = browser.get(reverse("accounts:security_enrol"))
    totp_secret = page.context["enrolment"].secret
    secrets_seen.append(totp_secret)
    enrolled = browser.post(
        reverse("accounts:security_enrol"), {"code": pyotp.TOTP(totp_secret).now()}
    )
    codes = enrolled.context["codes"]
    secrets_seen.extend(codes)

    second = Client()
    second.post(reverse("accounts:sign_in"), {"email": user.upn, "password": PASSWORD})
    second.post(reverse("accounts:sign_in_second_factor"), {"code": codes[0]})
    second.post(
        reverse("accounts:change_password"),
        {"current": PASSWORD, "password": OTHER_PASSWORD, "password_again": OTHER_PASSWORD},
    )
    Client().post(reverse("accounts:forgot_password"), {"email": user.upn})
    reset = _link(mail.outbox[-1])
    secrets_seen.append(reset.split("kood=")[1])

    kinds = set(SecurityAuditEvent.objects.values_list("event_type", flat=True))
    for expected in (
        SecurityEventType.ACCOUNT_CREATED,
        SecurityEventType.ACCOUNT_INVITED,
        SecurityEventType.CREDENTIAL_LINK_ISSUED,
        SecurityEventType.ACCOUNT_EMAIL_DELIVERY,
        SecurityEventType.PASSWORD_SET,
        SecurityEventType.ACCOUNT_ACTIVATED,
        SecurityEventType.AUTHENTICATION_SUCCEEDED,
        SecurityEventType.MFA_ENROLLED,
        SecurityEventType.MFA_RECOVERY_CODE_USED,
        SecurityEventType.PASSWORD_CHANGED,
        SecurityEventType.PASSWORD_RESET_REQUESTED,
    ):
        assert expected in kinds, expected

    trail = json.dumps(
        list(SecurityAuditEvent.objects.values("detail", "user_agent", "subject_type")),
        default=str,
        ensure_ascii=False,
    )
    logged = "\n".join(record.getMessage() for record in caplog.records)
    for secret in secrets_seen:
        assert secret not in trail, "a secret reached the audit"
        assert secret not in logged, "a secret reached the log"
    # The stored forms are not the secrets either.
    user.refresh_from_db()
    assert PASSWORD not in user.password and OTHER_PASSWORD not in user.password
    assert totp_secret not in user.totp_device.encrypted_secret
    assert all(
        code not in str(row.code_digest) for row in user.recovery_codes.all() for code in codes
    )
    assert mfa.remaining_recovery_codes(user) == len(codes) - 1


def test_the_trail_is_still_append_only():
    admin, _ = person(manage=True, delegate=True)
    user = administration.create_account(
        actor=admin, email="lisatav@koda.ee", display_name="Lisatav", role="READER"
    )
    event = SecurityAuditEvent.objects.get(
        event_type=SecurityEventType.ACCOUNT_CREATED, subject_id=user.pk
    )

    with pytest.raises(ImmutableRecordError):
        event.save()
    with pytest.raises(ImmutableRecordError):
        event.delete()


def test_a_refused_privilege_change_is_recorded_even_though_it_rolled_back(client):
    from tests.local_auth import administrator

    manager, _ = administrator(client, delegate=False)
    target, _ = person(manage=True, delegate=True)

    client.post(reverse("account_admin:deactivate", kwargs={"pk": target.pk}))

    refusal = SecurityAuditEvent.objects.get(
        event_type=SecurityEventType.ACCESS_REFUSED, actor=manager
    )
    assert refusal.subject_id == target.pk
    assert refusal.detail["action"] == "deactivate_account"
    assert not refusal.succeeded
    target.refresh_from_db()
    assert target.is_active
