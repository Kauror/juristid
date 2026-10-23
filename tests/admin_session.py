"""The verified administrator session under the shared gate, set up for the tests that need it.

The same shape as `tests/gate.py` and `tests/local_auth.py`: a module that wants
the shared gate *and* administrator accounts says so with its own fixture and
calls `apply_admin_gate`. Every identity is synthetic, every address is in the
test domain, and the passwords below exist nowhere but the test database
(docs/adr/0186).
"""

from __future__ import annotations

from typing import Any

from django.test import Client
from django.urls import reverse

from app.accounts.enums import UserRole
from app.accounts.models import User
from tests.gate import PASSWORD as GATE_PASSWORD
from tests.gate import apply_shared_gate
from tests.local_auth import LINK_BASE, PASSWORD, code, person


def apply_admin_gate(settings: Any) -> Any:
    """The shared gate, with the link and step-up limits stated rather than defaulted."""
    apply_shared_gate(settings)
    settings.ACCOUNT_EMAIL_DELIVERY_ENABLED = False
    settings.ACCOUNT_LINK_BASE_URL = LINK_BASE
    settings.ACCOUNT_EMAIL_FROM = "juristid@koda.ee"
    settings.ACCOUNT_ALLOWED_EMAIL_DOMAINS = ["koda.ee"]
    settings.LOCAL_AUTH_ACTIVATION_LINK_SECONDS = 172800
    settings.LOCAL_AUTH_RESET_LINK_SECONDS = 3600
    settings.ADMIN_SESSION_IDLE_SECONDS = 900
    settings.ADMIN_SESSION_ABSOLUTE_SECONDS = 7200
    settings.ADMIN_SESSION_REAUTH_SECONDS = 600
    return settings


def full_admin(**extra: Any) -> tuple[User, str]:
    """A usable full administrator: password, confirmed TOTP, both capabilities."""
    return person(manage=True, delegate=True, totp=True, **extra)


def technical_admin(**extra: Any) -> tuple[User, str]:
    """The technical ADMINISTRATOR role with both administrative capabilities."""
    return person(role=UserRole.ADMINISTRATOR, manage=True, delegate=True, totp=True, **extra)


def pass_gate(client: Client) -> Client:
    response = client.post(reverse("accounts:shared_gate"), {"password": GATE_PASSWORD})
    assert response.status_code == 302, response
    return client


def choose(client: Client, persona: User) -> Any:
    return client.post(reverse("accounts:act_as"), {"user_id": str(persona.pk)})


def step_up(client: Client, user: User, secret: str, *, password: str = PASSWORD) -> Any:
    """The real flow through the real views: address and password, then the code."""
    response = client.post(reverse("haldus:sign_in"), {"email": user.upn, "password": password})
    if response.status_code == 302 and response["Location"] == reverse("haldus:sign_in_code"):
        response = client.post(reverse("haldus:sign_in_code"), {"code": code(secret, user)})
    return response


def verified(client: Client, user: User, secret: str) -> Client:
    """A gate session with a verified administrator session for ``user`` and no persona."""
    pass_gate(client)
    response = step_up(client, user, secret)
    assert response.status_code == 302, getattr(response, "content", response)
    assert response["Location"] == reverse("haldus:index"), response["Location"]
    return client
