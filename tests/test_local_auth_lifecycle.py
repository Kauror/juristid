"""The account lifecycle and its administration (docs/adr/0145 §4–§7, §11).

Services first, then the one-time links through the real views. Mail goes to
Django's in-memory test outbox; the default backend that refuses all delivery
is exercised on its own. No real address, no real mail, no real person.
"""

from __future__ import annotations

import io
import re
from datetime import timedelta

import pytest
from django.core import mail as django_mail
from django.core.management import call_command
from django.core.management.base import CommandError
from django.test import Client
from django.urls import reverse
from django.utils import timezone

from app.accounts import administration, email_policy, mfa, tokens
from app.accounts.enums import (
    Capability,
    CredentialTokenPurpose,
    ProvisioningState,
    UserRole,
)
from app.accounts.models import AccountCredentialToken, User
from app.audit.enums import SecurityEventType
from app.audit.models import SecurityAuditEvent
from app.core.authorization import has_capability
from tests import factories
from tests.local_auth import (
    LINK_BASE,
    OTHER_PASSWORD,
    PASSWORD,
    apply_local_password,
    person,
    sign_in,
)
from tests.refusals import refused

pytestmark = pytest.mark.django_db


@pytest.fixture(autouse=True)
def _local_password(settings):
    return apply_local_password(settings)


@pytest.fixture
def admin(db):
    user, _ = person(manage=True, delegate=True, totp=True)
    return user


@pytest.fixture
def manager(db):
    """Manages accounts and may not delegate administration."""
    user, _ = person(manage=True, totp=True)
    return user


def _link_in(message) -> str:
    match = re.search(rf"{re.escape(LINK_BASE)}(\S+)", message.body)
    assert match, message.body
    return match.group(1)


# -- creating ------------------------------------------------------------------------


def test_a_new_account_is_pending_inactive_and_has_no_password(admin):
    user = administration.create_account(
        actor=admin, email="Uus.Jurist@koda.ee", display_name=" Uus  Jurist ", role="SPECIALIST"
    )

    assert user.upn == "uus.jurist@koda.ee" == user.email
    assert user.display_name == "Uus Jurist"
    assert user.provisioning_state == ProvisioningState.PENDING
    assert not user.is_active
    assert not user.has_usable_password()
    assert not user.is_staff and not user.is_superuser
    assert user.created_by == admin
    assert SecurityAuditEvent.objects.filter(
        event_type=SecurityEventType.ACCOUNT_CREATED, actor=admin, subject_id=user.pk
    ).exists()


def test_a_pending_account_is_nobodys_seat(admin):
    """Not a persona, not assignable, and not signed in by Cloudflare Access either."""
    from app.accounts.selectors import department_workers

    user = administration.create_account(
        actor=admin, email="ootel@koda.ee", display_name="Ootel Olev", role="SPECIALIST"
    )

    assert user not in department_workers()


def test_the_database_refuses_an_active_pending_account(admin):
    from django.db import IntegrityError, transaction

    user = administration.create_account(
        actor=admin, email="otse@koda.ee", display_name="Otse", role="SPECIALIST"
    )
    with pytest.raises(IntegrityError), transaction.atomic():
        User.objects.filter(pk=user.pk).update(is_active=True)


@pytest.mark.parametrize(
    "raw",
    ["Mari@KODA.ee", "mari@koda.ee", " mari@koda.ee "],
)
def test_duplicates_are_refused_however_they_are_spelled(admin, raw):
    administration.create_account(
        actor=admin, email="mari@koda.ee", display_name="Mari", role="SPECIALIST"
    )
    if email_policy.normalise(raw) == "mari@koda.ee":
        with refused(administration.DUPLICATE_ACCOUNT):
            administration.create_account(
                actor=admin, email=raw, display_name="Mari Teine", role="SPECIALIST"
            )


def test_a_subaddress_is_refused(admin):
    with refused(email_policy.SUBADDRESS):
        administration.create_account(
            actor=admin, email="mari+test@koda.ee", display_name="Mari", role="SPECIALIST"
        )


def test_an_outside_domain_is_refused_without_an_exception(admin):
    with refused(email_policy.domain_refusal()):
        administration.create_account(
            actor=admin, email="mari@gmail.com", display_name="Mari", role="SPECIALIST"
        )


def test_an_outside_address_needs_a_delegating_administrator_and_a_reason(admin, manager):
    with refused(administration.NOT_A_DELEGATE):
        administration.create_account(
            actor=manager,
            email="konsultant@partner.ee",
            display_name="Konsultant",
            role="READER",
            external_reason="Lepinguline partner",
        )

    user = administration.create_account(
        actor=admin,
        email="konsultant@partner.ee",
        display_name="Konsultant",
        role="READER",
        external_reason="Lepinguline partner",
    )

    assert user.email_exception_approved_by == admin
    exception = SecurityAuditEvent.objects.get(
        event_type=SecurityEventType.EMAIL_EXCEPTION_APPROVED, subject_id=user.pk
    )
    assert exception.actor == admin
    assert exception.detail["reason"] == "Lepinguline partner"
    # One address, not its domain.
    with refused(email_policy.domain_refusal()):
        administration.create_account(
            actor=admin, email="teine@partner.ee", display_name="Teine", role="READER"
        )


def test_only_an_administrator_may_create(admin):
    specialist, _ = person()
    with refused(administration.NOT_AN_ADMINISTRATOR):
        administration.create_account(
            actor=specialist, email="x@koda.ee", display_name="X", role="SPECIALIST"
        )


def test_nothing_is_administered_outside_local_password(admin, settings):
    settings.AUTH_MODE = "shared_gate"
    with refused(administration.NOT_LOCAL_MODE):
        administration.create_account(
            actor=admin, email="x@koda.ee", display_name="X", role="SPECIALIST"
        )


def test_the_technical_role_is_a_delegation_decision(admin, manager):
    with refused(administration.NOT_A_DELEGATE):
        administration.create_account(
            actor=manager, email="tech@koda.ee", display_name="Tehnik", role="ADMINISTRATOR"
        )
    user = administration.create_account(
        actor=admin, email="tech@koda.ee", display_name="Tehnik", role="ADMINISTRATOR"
    )
    assert user.role == UserRole.ADMINISTRATOR and not user.is_staff


# -- inviting and activating -------------------------------------------------------------


def test_approving_sends_one_activation_link_and_activates_nothing(admin):
    user = administration.create_account(
        actor=admin, email="kutsutav@koda.ee", display_name="Kutsutav", role="SPECIALIST"
    )

    outcome = administration.send_setup_link(actor=admin, user=user)

    user.refresh_from_db()
    assert outcome.delivery.delivered
    assert user.provisioning_state == ProvisioningState.INVITED
    assert not user.is_active
    assert user.approved_by == admin
    assert len(django_mail.outbox) == 1
    message = django_mail.outbox[0]
    assert message.to == ["kutsutav@koda.ee"]
    assert "?kood=" in message.body
    # Only a digest is stored.
    token = _link_in(message).split("kood=")[1]
    record = AccountCredentialToken.objects.get(user=user)
    assert token.split(".")[1] not in record.verifier_digest
    assert token not in str(list(SecurityAuditEvent.objects.values_list("detail", flat=True)))


def test_the_activation_link_lets_the_person_set_their_own_password(admin, client):
    user = administration.create_account(
        actor=admin, email="aktiveerija@koda.ee", display_name="Aktiveerija", role="SPECIALIST"
    )
    administration.send_setup_link(actor=admin, user=user)
    link = _link_in(django_mail.outbox[0])

    landing = client.get(link)
    assert landing.status_code == 302
    assert "kood=" not in landing["Location"]  # out of the address bar at once
    form = client.get(landing["Location"])
    assert form.status_code == 200
    assert "aktiveerija@koda.ee" in form.content.decode()

    done = client.post(landing["Location"], {"password": PASSWORD, "password_again": PASSWORD})

    assert done["Location"] == f"{reverse('accounts:sign_in')}?olek=parool-seatud"
    user.refresh_from_db()
    assert user.is_active and user.provisioning_state == ProvisioningState.ACTIVATED
    assert user.has_local_password
    assert user.password.startswith("argon2$argon2id$")
    assert SecurityAuditEvent.objects.filter(
        event_type=SecurityEventType.ACCOUNT_ACTIVATED, subject_id=user.pk
    ).exists()
    # And they can sign in.
    assert sign_in(Client(), user).status_code == 302


def test_a_link_works_once(admin, client):
    user = administration.create_account(
        actor=admin, email="kord@koda.ee", display_name="Kord", role="SPECIALIST"
    )
    administration.send_setup_link(actor=admin, user=user)
    link = _link_in(django_mail.outbox[0])
    target = client.get(link)["Location"]
    client.post(target, {"password": PASSWORD, "password_again": PASSWORD})

    replay = Client()
    again = replay.get(link)
    response = replay.post(
        again["Location"], {"password": OTHER_PASSWORD, "password_again": OTHER_PASSWORD}
    )

    assert response.status_code == 400
    assert "ei kehti" in response.content.decode()
    user.refresh_from_db()
    assert user.check_password(PASSWORD)


def test_a_refused_password_leaves_the_link_usable(admin, client):
    user = administration.create_account(
        actor=admin, email="proovib@koda.ee", display_name="Proovib", role="SPECIALIST"
    )
    administration.send_setup_link(actor=admin, user=user)
    target = client.get(_link_in(django_mail.outbox[0]))["Location"]

    short = client.post(target, {"password": "lühike", "password_again": "lühike"})
    assert short.status_code == 400
    assert "vähemalt 15" in short.content.decode()

    ok = client.post(target, {"password": PASSWORD, "password_again": PASSWORD})
    assert ok.status_code == 302


def test_an_expired_link_is_refused(admin, client):
    user = administration.create_account(
        actor=admin, email="aegunud@koda.ee", display_name="Aegunud", role="SPECIALIST"
    )
    administration.send_setup_link(actor=admin, user=user)
    # Issued two days ago, so that it has now expired (the database keeps every
    # link's expiry after its issue).
    AccountCredentialToken.objects.filter(user=user).update(
        created_at=timezone.now() - timedelta(days=2),
        expires_at=timezone.now() - timedelta(seconds=1),
    )

    target = client.get(_link_in(django_mail.outbox[0]))["Location"]
    response = client.get(target)

    assert response.status_code == 400
    assert "ei kehti" in response.content.decode()


def test_a_new_link_supersedes_the_old_one(admin, client):
    user = administration.create_account(
        actor=admin, email="uuesti@koda.ee", display_name="Uuesti", role="SPECIALIST"
    )
    administration.send_setup_link(actor=admin, user=user)
    administration.send_setup_link(actor=admin, user=user)
    first, second = (_link_in(message) for message in django_mail.outbox)

    assert client.get(client.get(first)["Location"]).status_code == 400
    fresh = Client()
    assert fresh.get(fresh.get(second)["Location"]).status_code == 200


def test_a_malformed_or_invented_link_is_the_same_refusal(client):
    for value in ("", "x", "a" * 32 + "." + "b" * 43, "../../etc"):
        target = client.get(reverse("accounts:activate"), {"kood": value})["Location"]
        assert client.get(target).status_code == 400


def test_without_delivery_the_link_is_neither_sent_nor_kept(admin, settings):
    settings.ACCOUNT_EMAIL_DELIVERY_ENABLED = False
    user = administration.create_account(
        actor=admin, email="saatmata@koda.ee", display_name="Saatmata", role="SPECIALIST"
    )

    outcome = administration.send_setup_link(actor=admin, user=user)

    user.refresh_from_db()
    assert not outcome.delivery.delivered
    assert outcome.delivery.reason == "disabled"
    assert django_mail.outbox == []
    assert user.provisioning_state == ProvisioningState.PENDING  # approved, not invited
    assert user.approved_by == admin
    assert not tokens.has_outstanding(user=user, purpose=CredentialTokenPurpose.ACTIVATION)
    assert SecurityAuditEvent.objects.filter(
        event_type=SecurityEventType.ACCOUNT_EMAIL_DELIVERY,
        subject_id=user.pk,
        succeeded=False,
        detail__outcome="disabled",
    ).exists()


def test_the_default_backend_delivers_nothing_at_all(settings):
    """Even a stray `send_mail` reaches nobody under the shipped default."""
    from django.core.mail import send_mail

    settings.EMAIL_BACKEND = "app.accounts.mail.DeliveryDisabledBackend"

    assert send_mail("Teema", "Sisu", "a@koda.ee", ["b@koda.ee"]) == 0


def test_cancelling_an_invitation_kills_its_link(admin, client):
    user = administration.create_account(
        actor=admin, email="tuhistatud@koda.ee", display_name="Tühistatud", role="SPECIALIST"
    )
    administration.send_setup_link(actor=admin, user=user)
    administration.cancel_invitation(actor=admin, user=user)

    target = client.get(_link_in(django_mail.outbox[0]))["Location"]

    assert client.get(target).status_code == 400
    user.refresh_from_db()
    assert user.provisioning_state == ProvisioningState.PENDING


def test_an_existing_account_gets_a_first_password_link_without_anything_else_changing(
    admin, client
):
    """Every account from the shared-gate period: active, no password here."""
    legacy = factories.UserFactory(upn="vana.kasutaja@koda.ee", email="vana.kasutaja@koda.ee")
    before = (legacy.role, legacy.is_active, legacy.provisioning_state, legacy.entra_object_id)

    administration.send_setup_link(actor=admin, user=legacy)
    target = client.get(_link_in(django_mail.outbox[0]))["Location"]
    client.post(target, {"password": PASSWORD, "password_again": PASSWORD})

    legacy.refresh_from_db()
    assert (
        legacy.role,
        legacy.is_active,
        legacy.provisioning_state,
        legacy.entra_object_id,
    ) == before
    assert legacy.has_local_password


# -- resetting and changing --------------------------------------------------------------


def test_forgotten_password_sends_a_reset_link_and_says_the_same_for_anybody(client):
    user, _ = person()

    real = client.post(reverse("accounts:forgot_password"), {"email": user.upn})
    fake = Client().post(reverse("accounts:forgot_password"), {"email": "pole.olemas@koda.ee"})

    assert real.status_code == fake.status_code == 200
    assert real.content.decode().count("Kui see aadress") == 1
    assert fake.content.decode().count("Kui see aadress") == 1
    assert len(django_mail.outbox) == 1
    assert django_mail.outbox[0].to == [user.upn]


def test_a_reset_link_changes_the_password_and_ends_old_sessions(client):
    user, _ = person()
    old_session = Client()
    sign_in(old_session, user)
    client.post(reverse("accounts:forgot_password"), {"email": user.upn})
    target = client.get(_link_in(django_mail.outbox[0]))["Location"]

    client.post(target, {"password": OTHER_PASSWORD, "password_again": OTHER_PASSWORD})

    user.refresh_from_db()
    assert user.check_password(OTHER_PASSWORD)
    assert old_session.get(reverse("matters:my_work")).status_code == 302


def test_reset_requests_are_throttled_quietly(client):
    user, _ = person()
    for _ in range(6):
        response = client.post(reverse("accounts:forgot_password"), {"email": user.upn})
        assert response.status_code == 200
    assert len(django_mail.outbox) == 3


def test_an_account_without_a_password_here_gets_no_reset_link(client):
    legacy = factories.UserFactory(upn="vana@koda.ee", email="vana@koda.ee")

    client.post(reverse("accounts:forgot_password"), {"email": legacy.upn})

    assert django_mail.outbox == []


def test_changing_the_password_ends_other_sessions_and_keeps_this_one(client):
    user, _ = person()
    other = Client()
    sign_in(other, user)
    sign_in(client, user)

    response = client.post(
        reverse("accounts:change_password"),
        {"current": PASSWORD, "password": OTHER_PASSWORD, "password_again": OTHER_PASSWORD},
    )

    assert response["Location"] == reverse("accounts:profile")
    assert client.get(reverse("matters:my_work")).status_code == 200
    assert other.get(reverse("matters:my_work")).status_code == 302
    assert SecurityAuditEvent.objects.filter(
        event_type=SecurityEventType.PASSWORD_CHANGED, actor=user
    ).exists()


def test_changing_the_password_needs_the_current_one(client):
    user, _ = person()
    sign_in(client, user)

    response = client.post(
        reverse("accounts:change_password"),
        {
            "current": "see ei ole õige parool",
            "password": OTHER_PASSWORD,
            "password_again": OTHER_PASSWORD,
        },
    )

    assert response.status_code == 400
    user.refresh_from_db()
    assert user.check_password(PASSWORD)


def test_a_reset_link_dies_when_the_password_changes_another_way(client):
    user, _ = person()
    client.post(reverse("accounts:forgot_password"), {"email": user.upn})
    link = _link_in(django_mail.outbox[0])
    signed = Client()
    sign_in(signed, user)
    signed.post(
        reverse("accounts:change_password"),
        {"current": PASSWORD, "password": OTHER_PASSWORD, "password_again": OTHER_PASSWORD},
    )

    holder = Client()
    target = holder.get(link)["Location"]
    assert holder.get(target).status_code == 400


# -- roles and capabilities --------------------------------------------------------------


def _wanted(user, **changes):
    from app.accounts import capabilities

    wanted = {c: has_capability(user, c) for c in capabilities.ALL_CAPABILITIES}
    wanted.update({c.value if hasattr(c, "value") else c: v for c, v in changes.items()})
    return wanted


def test_a_permission_can_be_granted_and_withdrawn_individually(admin):
    specialist, _ = person()
    assert not has_capability(specialist, Capability.VIEW_DEPARTMENT_MANAGEMENT)

    administration.update_account(
        actor=admin,
        user=specialist,
        display_name=specialist.display_name,
        role=specialist.role,
        wanted=_wanted(specialist, **{Capability.VIEW_DEPARTMENT_MANAGEMENT.value: True}),
    )
    specialist.refresh_from_db()
    assert has_capability(specialist, Capability.VIEW_DEPARTMENT_MANAGEMENT)
    assert specialist.capability_overrides == {"department.view_management": "allow"}

    administration.update_account(
        actor=admin,
        user=specialist,
        display_name=specialist.display_name,
        role=specialist.role,
        wanted=_wanted(specialist, **{Capability.VIEW_DEPARTMENT_MANAGEMENT.value: False}),
    )
    specialist.refresh_from_db()
    assert specialist.capability_overrides == {}


def test_a_role_default_can_be_individually_disabled(admin):
    head, _ = person(role=UserRole.DEPARTMENT_HEAD)

    administration.update_account(
        actor=admin,
        user=head,
        display_name=head.display_name,
        role=head.role,
        wanted=_wanted(head, **{Capability.REVIEW_WORK_VICTORIES.value: False}),
    )

    head.refresh_from_db()
    assert not has_capability(head, Capability.REVIEW_WORK_VICTORIES)
    assert has_capability(head, Capability.VIEW_DEPARTMENT_MANAGEMENT)


def test_a_change_of_power_is_audited_with_before_and_after_and_ends_sessions(admin):
    specialist, _ = person()
    session = Client()
    sign_in(session, specialist)
    epoch = specialist.security_epoch

    administration.update_account(
        actor=admin,
        user=specialist,
        display_name=specialist.display_name,
        role=UserRole.DEPARTMENT_HEAD,
        wanted=_wanted(specialist),
    )

    specialist.refresh_from_db()
    assert specialist.role == UserRole.DEPARTMENT_HEAD
    assert specialist.security_epoch == epoch + 1
    change = SecurityAuditEvent.objects.get(
        event_type=SecurityEventType.CAPABILITIES_CHANGED, subject_id=specialist.pk
    )
    assert change.actor == admin
    assert change.detail["previous_role"] == "SPECIALIST"
    assert change.detail["role"] == "DEPARTMENT_HEAD"
    assert "department.view_management" in change.detail["capabilities"]
    assert "department.view_management" not in change.detail["previous_capabilities"]
    assert SecurityAuditEvent.objects.filter(
        event_type=SecurityEventType.ROLE_CHANGED, subject_id=specialist.pk, actor=admin
    ).exists()
    assert session.get(reverse("matters:my_work")).status_code == 302


def test_a_name_change_alone_ends_no_session(admin):
    specialist, _ = person()
    session = Client()
    sign_in(session, specialist)

    administration.update_account(
        actor=admin,
        user=specialist,
        display_name="Uus Nimi",
        role=specialist.role,
        wanted=_wanted(specialist),
    )

    assert session.get(reverse("matters:my_work")).status_code == 200


def test_a_reader_cannot_be_given_a_write_capability(admin):
    reader, _ = person(role=UserRole.READER)

    with refused(administration.NOT_GRANTABLE):
        administration.update_account(
            actor=admin,
            user=reader,
            display_name=reader.display_name,
            role=reader.role,
            wanted=_wanted(reader, **{Capability.REVIEW_WORK_VICTORIES.value: True}),
        )


def test_a_technical_administrator_cannot_be_given_the_management_view(admin):
    technical, _ = person(role=UserRole.ADMINISTRATOR)

    with refused(administration.NOT_GRANTABLE):
        administration.update_account(
            actor=admin,
            user=technical,
            display_name=technical.display_name,
            role=technical.role,
            wanted=_wanted(technical, **{Capability.VIEW_DEPARTMENT_MANAGEMENT.value: True}),
        )


# -- administrators managing administrators -----------------------------------------------


def test_a_delegating_administrator_may_appoint_another(admin):
    colleague, _ = person()

    administration.update_account(
        actor=admin,
        user=colleague,
        display_name=colleague.display_name,
        role=colleague.role,
        wanted=_wanted(
            colleague,
            **{
                Capability.MANAGE_ACCOUNTS.value: True,
                Capability.DELEGATE_ADMINISTRATION.value: True,
            },
        ),
    )

    colleague.refresh_from_db()
    assert has_capability(colleague, Capability.DELEGATE_ADMINISTRATION)
    # And administration is not business access: still exactly a specialist.
    assert not colleague.is_staff and not colleague.is_superuser


def test_a_manager_cannot_grant_administration(manager):
    colleague, _ = person()

    with refused(administration.NOT_A_DELEGATE):
        administration.update_account(
            actor=manager,
            user=colleague,
            display_name=colleague.display_name,
            role=colleague.role,
            wanted=_wanted(colleague, **{Capability.MANAGE_ACCOUNTS.value: True}),
        )


def test_a_manager_cannot_touch_another_administrator(manager, admin):
    with refused(administration.NOT_A_DELEGATE):
        administration.deactivate_account(actor=manager, user=admin)
    with refused(administration.NOT_A_DELEGATE):
        administration.update_account(
            actor=manager,
            user=admin,
            display_name=admin.display_name,
            role=UserRole.READER,
            wanted=_wanted(admin),
        )


def test_nobody_changes_their_own_power(admin):
    with refused(administration.NO_SELF_CHANGE):
        administration.update_account(
            actor=admin,
            user=admin,
            display_name=admin.display_name,
            role=UserRole.DEPARTMENT_HEAD,
            wanted=_wanted(admin),
        )
    with refused(administration.NO_SELF_CHANGE):
        administration.deactivate_account(actor=admin, user=admin)


def test_an_ordinary_account_cannot_elevate_itself():
    specialist, _ = person()

    with refused(administration.NOT_AN_ADMINISTRATOR):
        administration.update_account(
            actor=specialist,
            user=specialist,
            display_name=specialist.display_name,
            role=specialist.role,
            wanted=_wanted(specialist, **{Capability.MANAGE_ACCOUNTS.value: True}),
        )
    specialist.refresh_from_db()
    assert specialist.capability_overrides == {}


def test_the_last_delegating_administrator_cannot_be_removed(admin):
    other, _ = person(manage=True, delegate=True)

    # Two of them: one may remove the other.
    administration.update_account(
        actor=other,
        user=admin,
        display_name=admin.display_name,
        role=admin.role,
        wanted=_wanted(
            admin,
            **{
                Capability.MANAGE_ACCOUNTS.value: False,
                Capability.DELEGATE_ADMINISTRATION.value: False,
            },
        ),
    )
    admin.refresh_from_db()
    assert not has_capability(admin, Capability.MANAGE_ACCOUNTS)

    # Now `other` is the last one, and nobody left can remove them — not even
    # through a third administrator who merely manages.
    third, _ = person(manage=True)
    with refused(administration.NOT_A_DELEGATE):
        administration.deactivate_account(actor=third, user=other)


def test_one_delegate_may_switch_off_another_until_one_is_left(admin):
    second, _ = person(manage=True, delegate=True)

    administration.deactivate_account(actor=admin, user=second)

    assert administration._delegating_administrators() == [admin]
    with refused(administration.NO_SELF_CHANGE):
        administration.deactivate_account(actor=admin, user=admin)


def test_removing_the_last_delegate_by_demotion_is_refused():
    """Two delegates; one demotes the other, then the survivor cannot be demoted by a manager."""
    first, _ = person(manage=True, delegate=True)
    second, _ = person(manage=True, delegate=True)
    administration.update_account(
        actor=first,
        user=second,
        display_name=second.display_name,
        role=second.role,
        wanted=_wanted(second, **{Capability.DELEGATE_ADMINISTRATION.value: False}),
    )
    second.refresh_from_db()
    assert administration._delegating_administrators() == [first]
    assert has_capability(second, Capability.MANAGE_ACCOUNTS)

    with refused(administration.NOT_A_DELEGATE):
        administration.update_account(
            actor=second,
            user=first,
            display_name=first.display_name,
            role=first.role,
            wanted=_wanted(first, **{Capability.DELEGATE_ADMINISTRATION.value: False}),
        )


def test_the_final_delegate_guard_itself(monkeypatch, admin):
    """Even if every authority check passed, the last delegate cannot be removed."""
    second, _ = person(manage=True, delegate=True)
    administration.update_account(
        actor=admin,
        user=second,
        display_name=second.display_name,
        role=second.role,
        wanted=_wanted(second, **{Capability.DELEGATE_ADMINISTRATION.value: False}),
    )
    # Pretend `second` could still act as a delegate (a race the lock exists for).
    monkeypatch.setattr(administration, "_require_actor", lambda *a, **k: None)

    with refused(administration.FINAL_ADMINISTRATOR):
        administration.update_account(
            actor=second,
            user=admin,
            display_name=admin.display_name,
            role=admin.role,
            wanted=_wanted(admin, **{Capability.DELEGATE_ADMINISTRATION.value: False}),
        )
    with refused(administration.FINAL_ADMINISTRATOR):
        administration.deactivate_account(actor=second, user=admin)
    admin.refresh_from_db()
    assert admin.is_active and has_capability(admin, Capability.DELEGATE_ADMINISTRATION)


def test_deactivation_ends_sessions_kills_links_and_keeps_history(admin):
    specialist, _ = person()
    matter = factories.MatterFactory(owner=specialist)
    session = Client()
    sign_in(session, specialist)
    administration.send_setup_link(actor=admin, user=specialist)

    administration.deactivate_account(actor=admin, user=specialist, reason="Lahkus")

    specialist.refresh_from_db()
    matter.refresh_from_db()
    assert not specialist.is_active
    assert matter.owner == specialist  # history is not rewritten
    assert not tokens.has_outstanding(
        user=specialist, purpose=CredentialTokenPurpose.PASSWORD_RESET
    )
    response = session.get(reverse("matters:my_work"))
    assert response["Location"] == f"{reverse('accounts:sign_in')}?olek=valjas"
    assert sign_in(Client(), specialist).status_code == 400

    administration.reactivate_account(actor=admin, user=specialist)
    assert sign_in(Client(), specialist).status_code == 302


def test_resetting_somebodys_second_factor(admin):
    specialist, _ = person(totp=True)

    administration.reset_second_factor(actor=admin, user=specialist)

    assert not mfa.has_second_factor(specialist)
    assert SecurityAuditEvent.objects.filter(
        event_type=SecurityEventType.MFA_REMOVED, subject_id=specialist.pk, actor=admin
    ).exists()


def test_changing_the_sign_in_address_keeps_the_identity(admin):
    placeholder = factories.UserFactory(upn="marko", email="")
    pk, entra = placeholder.pk, placeholder.entra_object_id

    administration.change_login_email(actor=admin, user=placeholder, email="marko.x@koda.ee")

    placeholder.refresh_from_db()
    assert placeholder.pk == pk and placeholder.entra_object_id == entra
    assert placeholder.upn == "marko.x@koda.ee"
    assert SecurityAuditEvent.objects.get(
        event_type=SecurityEventType.LOGIN_EMAIL_CHANGED, subject_id=pk
    ).detail == {"from": "marko", "to": "marko.x@koda.ee", "external_address_exception": False}


def test_changing_an_address_is_a_delegation_decision(manager):
    specialist, _ = person()
    with refused(administration.NOT_A_DELEGATE):
        administration.change_login_email(actor=manager, user=specialist, email="uus@koda.ee")


# -- the first administrator ---------------------------------------------------------------


def _bootstrap(**options):
    out = io.StringIO()
    call_command("bootstrap_account_admin", stdout=out, **options)
    return out.getvalue()


def test_bootstrap_refuses_outside_local_password(settings):
    settings.AUTH_MODE = "shared_gate"
    with pytest.raises(CommandError, match=re.escape(administration.NOT_LOCAL_MODE)):
        _bootstrap(
            email="esimene@koda.ee",
            confirm_email="esimene@koda.ee",
            display_name="Esimene",
            role="SPECIALIST",
            note="test",
        )
    assert not User.objects.filter(upn="esimene@koda.ee").exists()


def test_bootstrap_needs_the_address_twice():
    with pytest.raises(CommandError, match="differ"):
        _bootstrap(
            email="esimene@koda.ee",
            confirm_email="teine@koda.ee",
            display_name="Esimene",
            role="SPECIALIST",
            note="test",
        )


def test_bootstrap_appoints_the_first_administrator_once():
    output = _bootstrap(
        email="esimene@koda.ee",
        confirm_email="esimene@koda.ee",
        display_name="Esimene Haldur",
        role="SPECIALIST",
        note="Paigaldus, operaator Test",
    )

    user = User.objects.get(upn="esimene@koda.ee")
    assert "No password was set" in output
    # Held only once the account is active: an invitation is not a seat.
    assert not has_capability(user, Capability.DELEGATE_ADMINISTRATION)
    assert user.capability_overrides == {
        "accounts.manage": "allow",
        "accounts.delegate": "allow",
    }
    assert not user.has_usable_password()
    assert not user.is_staff and not user.is_superuser
    assert len(django_mail.outbox) == 1
    event = SecurityAuditEvent.objects.get(event_type=SecurityEventType.ADMINISTRATOR_BOOTSTRAPPED)
    assert event.actor is None and event.detail["note"] == "Paigaldus, operaator Test"

    # Activate, then a second bootstrap is refused.
    client = Client()
    target = client.get(_link_in(django_mail.outbox[0]))["Location"]
    client.post(target, {"password": PASSWORD, "password_again": PASSWORD})
    user.refresh_from_db()
    assert has_capability(user, Capability.DELEGATE_ADMINISTRATION)
    with pytest.raises(CommandError, match=re.escape(administration.BOOTSTRAP_ALREADY_DONE)):
        _bootstrap(
            email="teine@koda.ee",
            confirm_email="teine@koda.ee",
            display_name="Teine",
            role="SPECIALIST",
            note="test",
        )


def test_bootstrap_without_a_channel_changes_nothing(settings):
    settings.ACCOUNT_EMAIL_DELIVERY_ENABLED = False

    with pytest.raises(CommandError, match="Midagi ei muudetud"):
        _bootstrap(
            email="esimene@koda.ee",
            confirm_email="esimene@koda.ee",
            display_name="Esimene",
            role="SPECIALIST",
            note="test",
        )

    assert not User.objects.filter(upn="esimene@koda.ee").exists()
    assert not SecurityAuditEvent.objects.filter(
        event_type=SecurityEventType.ADMINISTRATOR_BOOTSTRAPPED
    ).exists()


def test_printing_the_link_is_refused_into_a_captured_output(settings):
    settings.ACCOUNT_EMAIL_DELIVERY_ENABLED = False
    with pytest.raises(CommandError, match="interactive terminal"):
        _bootstrap(
            email="esimene@koda.ee",
            confirm_email="esimene@koda.ee",
            display_name="Esimene",
            role="SPECIALIST",
            note="test",
            print_activation_link=True,
        )
    assert not User.objects.filter(upn="esimene@koda.ee").exists()


def test_the_printed_link_activates(settings):
    settings.ACCOUNT_EMAIL_DELIVERY_ENABLED = False

    result = administration.bootstrap_first_administrator(
        email="esimene@koda.ee",
        display_name="Esimene",
        role="SPECIALIST",
        operator_note="test",
        print_link=True,
    )

    assert result.printable_link.startswith(f"{LINK_BASE}{reverse('accounts:activate')}?kood=")
    client = Client()
    target = client.get(result.printable_link.removeprefix(LINK_BASE))["Location"]
    assert (
        client.post(target, {"password": PASSWORD, "password_again": PASSWORD}).status_code == 302
    )


def test_no_migration_or_seed_appoints_an_administrator():
    """Bootstrap is an operator's act and nothing else's."""
    from pathlib import Path

    from django.conf import settings as django_settings

    root = Path(django_settings.BASE_DIR) / "app"
    callers = [
        path.relative_to(root).as_posix()
        for path in root.rglob("*.py")
        if "bootstrap_first_administrator" in path.read_text(encoding="utf-8")
    ]
    assert sorted(callers) == [
        "accounts/administration.py",
        "accounts/management/commands/bootstrap_account_admin.py",
    ]


def test_a_second_first_administrator_is_refused_while_the_first_is_pending():
    administration.bootstrap_first_administrator(
        email="esimene@koda.ee", display_name="Esimene", role="SPECIALIST", operator_note="test"
    )

    with refused(administration.BOOTSTRAP_ALREADY_DONE):
        administration.bootstrap_first_administrator(
            email="teine@koda.ee", display_name="Teine", role="SPECIALIST", operator_note="test"
        )
    assert not User.objects.filter(upn="teine@koda.ee").exists()


def test_rerunning_for_the_same_pending_person_resends_their_link():
    administration.bootstrap_first_administrator(
        email="esimene@koda.ee", display_name="Esimene", role="SPECIALIST", operator_note="test"
    )
    first = _link_in(django_mail.outbox[-1])

    administration.bootstrap_first_administrator(
        email="esimene@koda.ee", display_name="Esimene", role="SPECIALIST", operator_note="test"
    )

    second = _link_in(django_mail.outbox[-1])
    assert first != second
    stale = Client()
    assert stale.get(stale.get(first)["Location"]).status_code == 400
    fresh = Client()
    assert fresh.get(fresh.get(second)["Location"]).status_code == 200
