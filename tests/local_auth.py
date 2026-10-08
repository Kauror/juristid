"""Personal sign-in, set up explicitly for the tests that exercise it.

The same shape as `tests/gate.py`, for the same reason: `config/settings.py`
defaults `AUTH_MODE` to `none` and almost every test assumes exactly that, so a
module that needs `local_password` says so with its own fixture and calls this.
Nothing here is a real credential: every identity is synthetic, every address
is in the test domain, and the database is the test database (docs/adr/0145).
"""

from __future__ import annotations

import uuid
from typing import Any

import pyotp
from django.test import Client
from django.urls import reverse
from django.utils import timezone

from app.accounts import capabilities, mfa
from app.accounts.enums import AuthMode, Capability, ProvisioningState, UserRole
from app.accounts.models import TotpDevice, User
from tests import factories

#: A passphrase every validator accepts. Not a secret: it exists in the test
#: database and nowhere else.
PASSWORD = "kolm hobust jooksevad üle silla 2026"  # noqa: S105
OTHER_PASSWORD = "neli kassi istuvad aknal vihmaga"  # noqa: S105

LINK_BASE = "https://juristid.test"


def apply_local_password(settings: Any, *, delivery: bool = True) -> Any:
    """Put this test module under personal sign-in, with the limits stated."""
    settings.AUTH_MODE = AuthMode.LOCAL_PASSWORD
    settings.LOGIN_URL = "accounts:sign_in"
    settings.DEV_LOGIN_ENABLED = False
    settings.ACCOUNT_EMAIL_DELIVERY_ENABLED = delivery
    settings.ACCOUNT_LINK_BASE_URL = LINK_BASE
    settings.ACCOUNT_EMAIL_FROM = "juristid@koda.ee"
    settings.ACCOUNT_ALLOWED_EMAIL_DOMAINS = ["koda.ee"]
    settings.LOCAL_AUTH_MFA_REQUIRED_FOR_ALL = False
    settings.LOCAL_AUTH_SESSION_IDLE_SECONDS = 3600
    settings.LOCAL_AUTH_SESSION_ABSOLUTE_SECONDS = 36000
    settings.LOCAL_AUTH_REAUTH_SECONDS = 900
    settings.LOCAL_AUTH_ACTIVATION_LINK_SECONDS = 172800
    settings.LOCAL_AUTH_RESET_LINK_SECONDS = 3600
    return settings


def person(
    *,
    email: str | None = None,
    role: str = UserRole.SPECIALIST,
    password: str | None = PASSWORD,
    manage: bool = False,
    delegate: bool = False,
    totp: bool = False,
    **extra: Any,
) -> tuple[User, str]:
    """An activated account with a personal password, and its TOTP secret if any."""
    overrides: dict[str, str] = {}
    if manage or delegate:
        overrides[Capability.MANAGE_ACCOUNTS.value] = capabilities.ALLOW
    if delegate:
        overrides[Capability.DELEGATE_ADMINISTRATION.value] = capabilities.ALLOW
    address = email or f"kasutaja.{uuid.uuid4().hex[:12]}@koda.ee"
    user = factories.UserFactory(
        upn=address,
        email=address,
        role=role,
        capability_overrides=overrides,
        provisioning_state=ProvisioningState.ACTIVATED,
        **extra,
    )
    if password is not None:
        user.set_password(password)
        user.local_password_set_at = timezone.now()
    user.save()
    secret = ""
    if totp:
        secret = pyotp.random_base32(length=32)
        TotpDevice.objects.create(
            user=user,
            encrypted_secret=mfa._encrypt(secret),
            confirmed_at=timezone.now(),
        )
        mfa.generate_recovery_codes(user)
    return user, secret


def code(secret: str, user: User | None = None) -> str:
    """A TOTP code the device will still accept: the earliest step not yet spent."""
    totp = pyotp.TOTP(secret)
    current = totp.timecode(timezone.now())
    spent = 0
    if user is not None:
        device = TotpDevice.objects.filter(user=user).first()
        spent = device.last_used_step if device is not None else 0
    for step in (current - 1, current, current + 1):
        if step > spent:
            return totp.generate_otp(step)
    # Three codes inside one step's tolerance window: let the test go on by
    # forgetting the spent step, which is test-only bookkeeping.
    TotpDevice.objects.filter(user=user).update(last_used_step=0)
    return totp.generate_otp(current)


def sign_in(client: Client, user: User, *, password: str = PASSWORD, secret: str = "") -> Any:
    """The real flow through the real views: address and password, then the code."""
    response = client.post(reverse("accounts:sign_in"), {"email": user.upn, "password": password})
    if (
        secret
        and response.status_code == 302
        and response["Location"].endswith(reverse("accounts:sign_in_second_factor"))
    ):
        response = client.post(
            reverse("accounts:sign_in_second_factor"), {"code": code(secret, user)}
        )
    return response


def administrator(client: Client, *, delegate: bool = True, **extra: Any) -> tuple[User, str]:
    """A signed-in account administrator with a second-factor session."""
    user, secret = person(manage=True, delegate=delegate, totp=True, **extra)
    response = sign_in(client, user, secret=secret)
    assert response.status_code == 302, response
    return user, secret
