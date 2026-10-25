"""Acting on a technical ADMINISTRATOR is a delegation decision (security review 2026-10-25).

Handing out the ADMINISTRATOR role already needs `accounts.delegate`
(`administration._clean_role`): it is technical power — an administrator
session, Süsteemi seis, and `reference.manage` where granted. Until this
review only an `accounts.manage` *capability* counted as "another
administrator" (`_touches_administration`), so an administrator who may not
delegate could still switch a technical administrator that a delegate had
switched off back on, reset their second factor, send them a password link, or
rename them. Each of those now needs `accounts.delegate`, exactly as it does
for an account administrator.
"""

from __future__ import annotations

import pytest

from app.accounts import administration
from app.accounts.enums import UserRole
from tests.local_auth import apply_local_password, person
from tests.refusals import refused

pytestmark = pytest.mark.django_db


@pytest.fixture(autouse=True)
def _local_password(settings):
    return apply_local_password(settings)


@pytest.fixture
def delegate(db):
    user, _ = person(manage=True, delegate=True, totp=True)
    return user


@pytest.fixture
def manager(db):
    """Manages accounts and may not delegate administration."""
    user, _ = person(manage=True, totp=True)
    return user


@pytest.fixture
def technical(db):
    user, _ = person(role=UserRole.ADMINISTRATOR)
    return user


def test_a_manager_cannot_switch_a_technical_administrator_back_on(delegate, manager, technical):
    technical.is_active = False
    technical.save(update_fields=["is_active"])

    with refused(administration.NOT_A_DELEGATE):
        administration.reactivate_account(actor=manager, user=technical)
    technical.refresh_from_db()
    assert not technical.is_active

    administration.reactivate_account(actor=delegate, user=technical)
    technical.refresh_from_db()
    assert technical.is_active


def test_a_manager_cannot_switch_a_technical_administrator_off(manager, technical):
    with refused(administration.NOT_A_DELEGATE):
        administration.deactivate_account(actor=manager, user=technical)
    technical.refresh_from_db()
    assert technical.is_active


def test_a_manager_cannot_reset_a_technical_administrators_second_factor(manager, technical):
    with refused(administration.NOT_A_DELEGATE):
        administration.reset_second_factor(actor=manager, user=technical)


def test_a_manager_cannot_send_a_technical_administrator_a_password_link(manager, technical):
    with refused(administration.NOT_A_DELEGATE):
        administration.send_setup_link(actor=manager, user=technical)


def test_a_manager_cannot_rename_or_repurpose_a_technical_administrator(
    delegate, manager, technical
):
    with refused(administration.NOT_A_DELEGATE):
        administration.update_account(
            actor=manager,
            user=technical,
            display_name="Uus nimi",
            role=UserRole.ADMINISTRATOR,
            wanted={},
        )
    with refused(administration.NOT_A_DELEGATE):
        administration.update_account(
            actor=manager,
            user=technical,
            display_name=technical.display_name,
            role=UserRole.DEPARTMENT_HEAD,
            wanted={},
        )
    technical.refresh_from_db()
    assert technical.role == UserRole.ADMINISTRATOR

    administration.update_account(
        actor=delegate,
        user=technical,
        display_name="Uus nimi",
        role=UserRole.ADMINISTRATOR,
        wanted={},
    )
    technical.refresh_from_db()
    assert technical.display_name == "Uus nimi"


def test_a_manager_still_administers_a_business_account(manager):
    """The boundary moved for the technical role only: ordinary accounts are as before."""
    colleague, _ = person(role=UserRole.SPECIALIST)
    administration.update_account(
        actor=manager,
        user=colleague,
        display_name="Kolleeg",
        role=UserRole.DEPARTMENT_HEAD,
        wanted={},
    )
    colleague.refresh_from_db()
    assert colleague.role == UserRole.DEPARTMENT_HEAD
