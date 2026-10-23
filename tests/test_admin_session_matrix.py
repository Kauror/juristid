"""The Haldus permission matrix under the shared gate (owner's brief A3, docs/adr/0186).

Every kind of caller the brief names, against every Haldus surface: who reaches
what, and — the acceptance criterion — that choosing the department head's
persona behind the shared password grants no account administration, while the
department head who verified themselves can administer accounts when explicitly
granted. Every 404 here also means nothing was changed (the POST rows).
"""

from __future__ import annotations

from collections.abc import Callable

import pytest
from django.test import Client
from django.urls import reverse

from app.accounts import administration
from app.accounts.enums import UserRole
from app.accounts.models import User
from tests import factories
from tests.admin_session import (
    apply_admin_gate,
    choose,
    full_admin,
    pass_gate,
    step_up,
    technical_admin,
)
from tests.local_auth import PASSWORD, person

pytestmark = pytest.mark.django_db

ROUTES = (
    "haldus:index",
    "account_admin:list",
    "account_admin:create",
    "haldus:susteem",
    "haldus:osakond",
)


@pytest.fixture(autouse=True)
def _gate(settings):
    return apply_admin_gate(settings)


def _verified(client: Client, user: User, secret: str, *, persona: User | None = None) -> Client:
    pass_gate(client)
    if persona is not None:
        choose(client, persona)
    response = step_up(client, user, secret)
    assert response.status_code == 302 and response["Location"] == reverse("haldus:index")
    return client


def technical_verified(client: Client) -> Client:
    admin, secret = technical_admin()
    return _verified(client, admin, secret)


def head_without_admin(client: Client) -> Client:
    head, _ = person(role=UserRole.DEPARTMENT_HEAD, totp=True)
    pass_gate(client)
    choose(client, head)
    return client


def head_manage_persona_only(client: Client) -> Client:
    head, _ = person(role=UserRole.DEPARTMENT_HEAD, manage=True, totp=True)
    pass_gate(client)
    choose(client, head)
    return client


def head_manage_verified(client: Client) -> Client:
    head, secret = person(role=UserRole.DEPARTMENT_HEAD, manage=True, totp=True)
    return _verified(client, head, secret, persona=head)


def head_full_verified(client: Client) -> Client:
    head, secret = full_admin(role=UserRole.DEPARTMENT_HEAD)
    return _verified(client, head, secret, persona=head)


def specialist(client: Client) -> Client:
    user, _ = person()
    pass_gate(client)
    choose(client, user)
    return client


def reader(client: Client) -> Client:
    user, _ = person(role=UserRole.READER)
    pass_gate(client)
    choose(client, user)
    return client


def specialist_choosing_the_head(client: Client) -> Client:
    """A specialist who knows the shared password and picks the (administrator) head."""
    me, _ = person()
    head, _ = full_admin(role=UserRole.DEPARTMENT_HEAD)
    pass_gate(client)
    choose(client, head)
    # Their own credentials do not open the step-up; the head's need the head's
    # password and code, which the specialist does not have.
    assert (
        client.post(reverse("haldus:sign_in"), {"email": me.upn, "password": PASSWORD}).status_code
        == 400
    )
    assert (
        client.post(
            reverse("haldus:sign_in"), {"email": head.upn, "password": "arvatud parool 12345"}
        ).status_code
        == 400
    )
    return client


def gate_only(client: Client) -> Client:
    pass_gate(client)
    return client


def anonymous(client: Client) -> Client:
    return client


def disabled_administrator(client: Client) -> Client:
    admin, secret = full_admin()
    _verified(client, admin, secret)
    User.objects.filter(pk=admin.pk).update(is_active=False)
    return client


def revoked_administrator(client: Client) -> Client:
    admin, secret = full_admin()
    _verified(client, admin, secret)
    other, _ = full_admin()
    full_admin()  # a third, so continuity allows the withdrawal
    from app.accounts import admin_session

    administration.update_account(
        actor=admin_session._mint(other, via=admin_session.PATH),
        user=admin,
        display_name=admin.display_name,
        role=admin.role,
        wanted={"accounts.manage": False},
    )
    return client


OK, NO, GATE = 200, 404, "gate"

#: caller → expected answer for each of ROUTES, in order.
MATRIX: dict[str, tuple[Callable[[Client], Client], tuple[object, ...]]] = {
    "technical_admin_verified": (technical_verified, (OK, OK, OK, OK, NO)),
    "head_without_admin_caps": (head_without_admin, (OK, NO, NO, NO, OK)),
    "head_manage_persona_only": (head_manage_persona_only, (OK, NO, NO, NO, OK)),
    "head_manage_verified": (head_manage_verified, (OK, OK, OK, NO, OK)),
    "head_manage_delegate_verified": (head_full_verified, (OK, OK, OK, NO, OK)),
    "specialist": (specialist, (NO, NO, NO, NO, NO)),
    "reader": (reader, (NO, NO, NO, NO, NO)),
    "specialist_choosing_the_head": (specialist_choosing_the_head, (OK, NO, NO, NO, OK)),
    "gate_without_persona": (gate_only, (NO, NO, NO, NO, NO)),
    "unauthenticated": (anonymous, (GATE, GATE, GATE, GATE, GATE)),
    "disabled_administrator": (disabled_administrator, (NO, NO, NO, NO, NO)),
    "revoked_administrator": (revoked_administrator, (NO, NO, NO, NO, NO)),
}


@pytest.mark.parametrize("caller", sorted(MATRIX))
def test_the_matrix(client, caller):
    build, expected = MATRIX[caller]
    build(client)
    for route, answer in zip(ROUTES, expected, strict=True):
        response = client.get(reverse(route))
        if answer == GATE:
            assert response.status_code == 302, (route, response.status_code)
            assert response["Location"].startswith(reverse("accounts:shared_gate")), route
        else:
            assert response.status_code == answer, (caller, route, response.status_code)


@pytest.mark.parametrize("caller", sorted(c for c, (_, e) in MATRIX.items() if e[1] != OK))
def test_no_refused_caller_can_change_an_account(client, caller):
    build, _ = MATRIX[caller]
    build(client)
    target = factories.UserFactory()
    for route in (
        "account_admin:detail",
        "account_admin:deactivate",
        "account_admin:reset_second_factor",
        "account_admin:send_link",
        "account_admin:change_email",
    ):
        response = client.post(
            reverse(route, kwargs={"pk": target.pk}),
            {"display_name": "Muudetud", "role": "READER", "email": "x@koda.ee"},
        )
        assert response.status_code in (302, 404), (caller, route)
        if response.status_code == 302:
            assert response["Location"].startswith(reverse("accounts:shared_gate"))
    target.refresh_from_db()
    assert target.is_active and target.role == UserRole.SPECIALIST
    assert target.display_name != "Muudetud"


def test_a_verified_manager_without_delegation_cannot_touch_administration(client):
    head_manage_verified(client)
    other_admin, _ = full_admin()

    assert (
        client.get(reverse("account_admin:change_email", kwargs={"pk": other_admin.pk})).status_code
        == 404
    )
    client.post(reverse("account_admin:deactivate", kwargs={"pk": other_admin.pk}))
    other_admin.refresh_from_db()
    assert other_admin.is_active


def test_the_verified_head_administers_when_explicitly_granted(client):
    head_full_verified(client)
    response = client.post(
        reverse("account_admin:create"),
        {"display_name": "Uus Jurist", "email": "uus.jurist@koda.ee", "role": "SPECIALIST"},
    )
    assert response.status_code == 302
    assert User.objects.filter(upn="uus.jurist@koda.ee").exists()
