"""At least two usable full administrators, and the operator's emergency route (docs/adr/0186).

What "usable" means (stored flags are not enough), what the rule refuses and
what it lets through — including the bootstrap phase — and the one way past it:
`manage.py emergency_disable_account`, on the host, with a reason.
"""

from __future__ import annotations

import io

import pytest
from django.core.management import call_command
from django.core.management.base import CommandError

from app.accounts import admin_session, administration, mfa, tokens
from app.accounts.enums import Capability, CredentialTokenPurpose
from app.accounts.models import TotpDevice, User
from app.audit.enums import SecurityEventType
from app.audit.models import SecurityAuditEvent
from tests.admin_session import apply_admin_gate, full_admin
from tests.local_auth import person
from tests.refusals import refused

pytestmark = pytest.mark.django_db


@pytest.fixture(autouse=True)
def _gate(settings):
    return apply_admin_gate(settings)


def _actor(user):
    return admin_session._mint(user, via=admin_session.PATH)


def _withdraw(actor, user, *capabilities_off):
    return administration.update_account(
        actor=_actor(actor),
        user=user,
        display_name=user.display_name,
        role=user.role,
        wanted=dict.fromkeys(capabilities_off, False),
    )


# -- what counts as usable -----------------------------------------------------------------


def test_a_usable_full_administrator_holds_everything_and_can_prove_it():
    usable, _ = full_admin()
    no_code, _ = person(manage=True, delegate=True)
    no_password, _ = person(manage=True, delegate=True, totp=True, password=None)
    manager_only, _ = person(manage=True, totp=True)
    switched_off, _ = full_admin()
    User.objects.filter(pk=switched_off.pk).update(is_active=False)
    unreadable, _ = full_admin()
    TotpDevice.objects.filter(user=unreadable).update(encrypted_secret="not-a-fernet-token")  # noqa: S106

    assert administration.usable_full_administrators() == [usable]
    status = administration.continuity_status()
    assert status.count == 1 and not status.satisfied and status.bootstrap_phase
    for other in (no_code, no_password, manager_only, switched_off, unreadable):
        other.refresh_from_db()
        assert not administration.is_usable_full_administrator(other)


def test_the_status_says_when_the_rule_holds():
    full_admin()
    assert not administration.continuity_status().satisfied
    full_admin()
    status = administration.continuity_status()
    assert status.satisfied and status.count == 2 and "2" in status.summary


# -- what the rule refuses ---------------------------------------------------------------------


def test_one_of_two_cannot_switch_off_the_other():
    first, _ = full_admin()
    second, _ = full_admin()

    with refused(administration.CONTINUITY):
        administration.deactivate_account(actor=_actor(first), user=second)

    second.refresh_from_db()
    assert second.is_active
    assert len(administration.usable_full_administrators()) == 2


def test_one_of_two_cannot_withdraw_the_others_administration():
    first, _ = full_admin()
    second, _ = full_admin()

    with refused(administration.CONTINUITY):
        _withdraw(first, second, Capability.DELEGATE_ADMINISTRATION.value)

    second.refresh_from_db()
    assert administration.is_usable_full_administrator(second)


def test_with_three_one_may_go():
    first, _ = full_admin()
    second, _ = full_admin()
    third, _ = full_admin()

    administration.deactivate_account(actor=_actor(first), user=third)

    assert len(administration.usable_full_administrators()) == 2
    with refused(administration.CONTINUITY):
        administration.deactivate_account(actor=_actor(first), user=second)


def test_the_bootstrap_phase_allows_only_what_does_not_reduce_it():
    only, _ = full_admin()
    newcomer, _ = person()  # an ordinary colleague, made the second administrator below

    # Growing is allowed while only one exists.
    administration.update_account(
        actor=_actor(only),
        user=newcomer,
        display_name=newcomer.display_name,
        role=newcomer.role,
        wanted={
            Capability.MANAGE_ACCOUNTS.value: True,
            Capability.DELEGATE_ADMINISTRATION.value: True,
        },
    )
    newcomer.refresh_from_db()
    # Not usable yet (no authenticator), so still one usable: nothing reduces it.
    assert administration.usable_full_administrators() == [only]
    # Changes that do not touch the count pass, and so does withdrawing a power
    # from somebody who is not (yet) usable.
    _withdraw(only, newcomer, Capability.DELEGATE_ADMINISTRATION.value)
    assert administration.usable_full_administrators() == [only]


def test_reaching_two_ends_the_bootstrap_phase():
    full_admin()
    assert administration.continuity_status().bootstrap_phase
    full_admin()
    assert not administration.continuity_status().bootstrap_phase


def test_a_lost_phone_reset_is_recovery_and_stays_possible():
    first, _ = full_admin()
    second, _ = full_admin()

    administration.reset_second_factor(actor=_actor(first), user=second)

    assert not mfa.has_second_factor(second)
    assert administration.usable_full_administrators() == [first]


def test_the_final_delegate_floor_still_holds_under_the_new_rule():
    """Two delegates, neither usable: continuity has nothing to protect, the floor does."""
    first, _ = person(manage=True, delegate=True)
    second, _ = person(manage=True, delegate=True)
    _withdraw(first, second, Capability.DELEGATE_ADMINISTRATION.value)
    second.refresh_from_db()  # a fresh proof after the change, so authority is what is asked

    with refused(administration.NOT_A_DELEGATE):
        _withdraw(second, first, Capability.DELEGATE_ADMINISTRATION.value)


def test_withdrawing_an_administrators_power_invalidates_their_next_action():
    first, _ = full_admin()
    second, _ = full_admin()
    third, _ = full_admin()
    stale = _actor(second)  # verified before the change
    _withdraw(first, second, Capability.MANAGE_ACCOUNTS.value)

    with refused(administration.STALE_PROOF):
        administration.create_account(
            actor=stale, email="uus@koda.ee", display_name="Uus", role="SPECIALIST"
        )
    del third


# -- the emergency route -----------------------------------------------------------------------


def _emergency(**options):
    out = io.StringIO()
    call_command("emergency_disable_account", stdout=out, **options)
    return out.getvalue()


def test_the_emergency_route_switches_off_even_below_two():
    first, _ = full_admin()
    compromised, _ = full_admin()
    link = tokens.issue(user=compromised, purpose=CredentialTokenPurpose.PASSWORD_RESET)

    output = _emergency(
        email=compromised.upn,
        confirm_email=compromised.upn,
        reason="Telefon varastati, testoperaator",
    )

    compromised.refresh_from_db()
    assert not compromised.is_active
    assert not tokens.is_live(link.record)
    assert "2 before, 1 after" in output
    assert "Below the required number" in output
    row = SecurityAuditEvent.objects.get(
        event_type=SecurityEventType.EMERGENCY_ACCOUNT_DISABLED, subject_id=compromised.pk
    )
    assert row.detail["reason"] == "Telefon varastati, testoperaator"
    assert row.detail["by"] == "operator"
    assert row.actor_id is None
    # Nothing else about the account moved.
    assert administration.administrators() == [first]
    assert compromised.capability_overrides.get(Capability.MANAGE_ACCOUNTS.value) == "allow"
    assert link.token not in str(row.detail)


def test_the_emergency_route_needs_a_reason_and_the_address_twice():
    victim, _ = full_admin()
    with pytest.raises(CommandError, match="differ"):
        _emergency(email=victim.upn, confirm_email="muu@koda.ee", reason="x")
    with pytest.raises(CommandError):
        _emergency(email=victim.upn, confirm_email=victim.upn, reason="   ")
    victim.refresh_from_db()
    assert victim.is_active


def test_a_dry_run_changes_nothing():
    victim, _ = full_admin()
    output = _emergency(email=victim.upn, confirm_email=victim.upn, reason="harjutus", dry_run=True)
    victim.refresh_from_db()
    assert victim.is_active and "DRY RUN" in output
    assert not SecurityAuditEvent.objects.filter(
        event_type=SecurityEventType.EMERGENCY_ACCOUNT_DISABLED
    ).exists()


def test_the_emergency_route_ends_an_open_administrator_session(client):
    from tests.admin_session import verified

    victim, secret = full_admin()
    verified(client, victim, secret)
    _emergency(email=victim.upn, confirm_email=victim.upn, reason="kompromiteeritud")

    from django.urls import reverse

    assert client.get(reverse("account_admin:list")).status_code == 404


def test_after_an_emergency_the_operator_may_bootstrap_again():
    only, _ = full_admin()
    _emergency(email=only.upn, confirm_email=only.upn, reason="ainus haldur kadus")

    result = administration.bootstrap_first_administrator(
        email="uus.haldur@koda.ee",
        display_name="Uus Haldur",
        role="SPECIALIST",
        operator_note="taastamine",
        print_link=True,
    )

    assert result.printable_link
