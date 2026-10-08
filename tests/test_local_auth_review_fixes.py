"""The independent security review of ADR 0145's first draft, finding by finding.

Each test names the scenario the review described and proves it no longer
works. Kept together so the next reader can see what was found and what holds
it shut.
"""

from __future__ import annotations

import threading

import pytest
from django.core import mail as django_mail
from django.test import Client
from django.urls import reverse

from app.accounts import administration, capabilities, local_auth, mfa, tokens
from app.accounts.enums import Capability, CredentialTokenPurpose, ThrottleScope
from app.accounts.models import AccountCredentialToken, AuthenticationThrottle, User
from app.core.authorization import account_capabilities, has_capability
from tests import factories
from tests.local_auth import PASSWORD, apply_local_password, person, sign_in
from tests.refusals import refused

pytestmark = pytest.mark.django_db


@pytest.fixture(autouse=True)
def _local_password(settings):
    return apply_local_password(settings)


def _wanted(user, **changes):
    wanted = {c: c in account_capabilities(user) for c in capabilities.ALL_CAPABILITIES}
    wanted.update(changes)
    return wanted


# -- 1. an address change cannot hand an administrator somebody's account -----------


def test_a_delegate_cannot_move_an_activated_persons_address():
    delegate, _ = person(manage=True, delegate=True)
    victim, _ = person()

    with refused(administration.ADDRESS_FIXED_AFTER_ACTIVATION):
        administration.change_login_email(
            actor=delegate,
            user=victim,
            email="ulevotja@gmail.com",
            external_reason="minu oma",
        )
    victim.refresh_from_db()
    assert victim.upn.endswith("@koda.ee")


def test_a_delegate_cannot_move_the_address_of_somebody_with_a_second_factor():
    delegate, _ = person(manage=True, delegate=True)
    victim, _ = person(password=None, totp=True)

    with refused(administration.ADDRESS_FIXED_AFTER_ACTIVATION):
        administration.change_login_email(actor=delegate, user=victim, email="uus@koda.ee")


def test_a_delegate_cannot_move_their_own_address():
    delegate, _ = person(manage=True, delegate=True)

    with refused(administration.NO_SELF_CHANGE):
        administration.change_login_email(actor=delegate, user=delegate, email="uus@koda.ee")


def test_a_placeholder_identity_still_gets_its_real_address():
    delegate, _ = person(manage=True, delegate=True)
    placeholder = factories.UserFactory(upn="sandra", email="")

    administration.change_login_email(actor=delegate, user=placeholder, email="sandra.x@koda.ee")

    placeholder.refresh_from_db()
    assert placeholder.upn == "sandra.x@koda.ee"


# -- 2. a hidden delegation allow does not come back -------------------------------


def test_withdrawing_management_takes_the_dormant_delegation_with_it():
    first, _ = person(manage=True, delegate=True)
    target, _ = person(manage=True, delegate=True)

    administration.update_account(
        actor=first,
        user=target,
        display_name=target.display_name,
        role=target.role,
        wanted=_wanted(target, **{Capability.MANAGE_ACCOUNTS.value: False}),
    )
    target.refresh_from_db()
    assert Capability.DELEGATE_ADMINISTRATION.value not in target.capability_overrides

    administration.update_account(
        actor=first,
        user=target,
        display_name=target.display_name,
        role=target.role,
        wanted=_wanted(target, **{Capability.MANAGE_ACCOUNTS.value: True}),
    )
    target.refresh_from_db()
    assert has_capability(target, Capability.MANAGE_ACCOUNTS)
    assert not has_capability(target, Capability.DELEGATE_ADMINISTRATION)


# -- 3. a stranger cannot lock a person out of their usual desk ---------------------


def _fail_from(client, email, address):
    return client.post(
        reverse("accounts:sign_in"),
        {"email": email, "password": "täiesti vale parool siin"},
        HTTP_CF_CONNECTING_IP=address,
    )


def test_the_account_counter_does_not_lock_a_trusted_source(client, settings):
    settings.LOCAL_AUTH_THROTTLE_ACCOUNT = {"max_failures": 4, "base_seconds": 300}
    user, _ = person()
    desk = "203.0.113.50"
    assert (
        client.post(
            reverse("accounts:sign_in"),
            {"email": user.upn, "password": PASSWORD},
            HTTP_CF_CONNECTING_IP=desk,
        ).status_code
        == 302
    )

    attacker = Client()
    for network in ("198.51.100.1", "198.51.100.2", "198.51.100.3", "198.51.100.4"):
        _fail_from(attacker, user.upn, network)
    assert AuthenticationThrottle.objects.get(scope=ThrottleScope.SIGN_IN_ACCOUNT).locked_until

    # A new network is held back by the tripped account counter …
    stranger = Client().post(
        reverse("accounts:sign_in"),
        {"email": user.upn, "password": PASSWORD},
        HTTP_CF_CONNECTING_IP="198.51.100.99",
    )
    assert stranger.status_code == 429
    # … the person's own desk is not.
    own = Client().post(
        reverse("accounts:sign_in"),
        {"email": user.upn, "password": PASSWORD},
        HTTP_CF_CONNECTING_IP=desk,
    )
    assert own.status_code == 302


def test_an_ipv6_neighbourhood_is_counted_as_one(client):
    user, _ = person()
    for suffix in ("1", "2", "3", "4", "5"):
        _fail_from(client, user.upn, f"2001:db8:1:1::{suffix}")

    response = client.post(
        reverse("accounts:sign_in"),
        {"email": user.upn, "password": PASSWORD},
        HTTP_CF_CONNECTING_IP="2001:db8:1:1::beef",
    )

    assert response.status_code == 429


# -- 4. the Django admin is not a way around the account rules ------------------------


def test_the_django_admin_cannot_switch_an_account_on_or_off(client):
    from app.core.admin import UserAdmin

    technical, _ = person(is_staff=True, is_superuser=True)
    request = type("R", (), {"user": technical})()
    fields = UserAdmin(User, None).get_readonly_fields(request, obj=technical)

    assert "is_active" in fields


def test_a_technical_account_must_use_a_second_factor_here():
    technical, _ = person(is_staff=True)
    assert local_auth.second_factor_mandatory(technical)
    assert sign_in(Client(), technical)["Location"] == reverse("accounts:security_enrol")


# -- 5. «Unustasid parooli?» takes the same path for every address ---------------------


@pytest.mark.django_db(transaction=True, serialized_rollback=True)
def test_the_reset_link_is_mailed_after_the_response(client, settings):
    settings.ACCOUNT_EMAIL_IN_BACKGROUND = True
    user, _ = person()

    response = client.post(reverse("accounts:forgot_password"), {"email": user.upn})

    assert response.status_code == 200
    for thread in threading.enumerate():
        if thread.name == "juristid-account-mail":
            thread.join(timeout=30)
    assert len(django_mail.outbox) == 1
    assert django_mail.outbox[0].to == [user.upn]


# -- 6. a non-ASCII digit is a wrong code, not a crash -----------------------------------


def test_arabic_indic_digits_are_refused_not_a_500(client):
    user, _ = person(totp=True)
    client.post(reverse("accounts:sign_in"), {"email": user.upn, "password": PASSWORD})

    response = client.post(reverse("accounts:sign_in_second_factor"), {"code": "١٢٣٤٥٦"})

    assert response.status_code == 400
    assert not mfa.verify_totp(user, "١٢٣٤٥٦")


# -- 7. the actor is re-read under the lock --------------------------------------------


def test_an_administrator_withdrawn_a_moment_ago_cannot_act_on_a_stale_copy():
    stale, _ = person(manage=True, delegate=True)
    target, _ = person()
    User.objects.filter(pk=stale.pk).update(capability_overrides={})

    with refused(administration.NOT_AN_ADMINISTRATOR):
        administration.deactivate_account(actor=stale, user=target)
    target.refresh_from_db()
    assert target.is_active


# -- 8. an administrator's account is a delegate's to touch at all ---------------------


def test_a_manager_cannot_rename_an_administrator():
    manager, _ = person(manage=True)
    other_admin, _ = person(manage=True, delegate=True)

    with refused(administration.NOT_A_DELEGATE):
        administration.update_account(
            actor=manager,
            user=other_admin,
            display_name="Uus Nimi",
            role=other_admin.role,
            wanted=_wanted(other_admin),
        )


def test_a_manager_cannot_cancel_an_administrators_invitation():
    manager, _ = person(manage=True)
    delegate, _ = person(manage=True, delegate=True)
    invited = administration.create_account(
        actor=delegate, email="tulev.haldur@koda.ee", display_name="Tulev", role="SPECIALIST"
    )
    administration.update_account(
        actor=delegate,
        user=invited,
        display_name=invited.display_name,
        role=invited.role,
        wanted=_wanted(invited, **{Capability.MANAGE_ACCOUNTS.value: True}),
    )
    administration.send_setup_link(actor=delegate, user=invited)

    with refused(administration.NOT_A_DELEGATE):
        administration.cancel_invitation(actor=manager, user=invited)


# -- 9. the session keeps a proof of the link, never the link ----------------------------


def test_the_session_never_holds_the_link_secret(client):
    delegate, _ = person(manage=True, delegate=True)
    invited = administration.create_account(
        actor=delegate, email="proov@koda.ee", display_name="Proov", role="SPECIALIST"
    )
    administration.send_setup_link(actor=delegate, user=invited)
    link = django_mail.outbox[-1].body.split("kood=")[1].split()[0]
    verifier = link.split(".")[1]

    client.get(f"{reverse('accounts:activate')}?kood={link}")

    stored = client.session[local_auth.SESSION_LINK]
    assert verifier not in str(stored)
    assert stored["proof"] == tokens.proof_of(link)
    record = AccountCredentialToken.objects.get(user=invited)
    assert stored["proof"].split(".")[1] == record.verifier_digest
    # And it still works end to end.
    done = client.post(
        reverse("accounts:activate"), {"password": PASSWORD, "password_again": PASSWORD}
    )
    assert done.status_code == 302
    assert not tokens.has_outstanding(user=invited, purpose=CredentialTokenPurpose.ACTIVATION)
