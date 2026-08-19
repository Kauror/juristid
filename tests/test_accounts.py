"""Custom user, Entra identity reservation and break-glass access."""

from __future__ import annotations

import uuid
from datetime import timedelta

import pytest
from django.db import DatabaseError, IntegrityError, transaction
from django.db.migrations.loader import MigrationLoader
from django.urls import reverse
from django.utils import timezone

from app.accounts.enums import UserRole
from app.accounts.models import BreakGlassGrant, User
from app.accounts.services import (
    MAX_BREAK_GLASS_DURATION,
    grant_break_glass,
    revoke_break_glass,
)
from app.audit.enums import SecurityEventType
from app.audit.models import SecurityAuditEvent
from app.core.errors import DomainError, InvariantViolation
from tests import factories

pytestmark = pytest.mark.django_db


def test_custom_user_exists_in_the_first_accounts_migration():
    """The user table must not be retrofitted later (specification 16.2)."""
    loader = MigrationLoader(connection=None, ignore_no_migrations=True)
    migration = loader.disk_migrations[("accounts", "0001_initial")]
    created = {
        operation.name
        for operation in migration.operations
        if operation.__class__.__name__ == "CreateModel"
    }
    assert "User" in created

    user_operation = next(op for op in migration.operations if getattr(op, "name", "") == "User")
    field_names = {name for name, _ in user_operation.fields}
    assert "entra_object_id" in field_names


def test_upn_is_normalised_and_unique(specialist):
    user = User.objects.create_user(upn="  Jurist@Example.Invalid ", display_name="Jurist")
    assert user.upn == "jurist@example.invalid"

    with pytest.raises(IntegrityError), transaction.atomic():
        User.objects.create_user(upn="JURIST@example.invalid", display_name="Teine")


def test_entra_object_id_is_immutable_once_assigned():
    user = User.objects.create_user(
        upn="entra@example.invalid",
        display_name="Entra kasutaja",
        entra_object_id=uuid.uuid4(),
    )
    user.entra_object_id = uuid.uuid4()
    with pytest.raises(InvariantViolation):
        user.save()


def test_entra_object_id_may_be_assigned_to_a_user_that_had_none():
    """The first Entra sign-in writes the object id onto an existing account."""
    user = User.objects.create_user(upn="uus@example.invalid", display_name="Uus")
    assert user.entra_object_id is None

    user.entra_object_id = uuid.uuid4()
    user.save()
    user.refresh_from_db()
    assert user.entra_object_id is not None


def test_an_assigned_entra_object_id_cannot_be_changed_by_a_bulk_update():
    """The model guard is not enough: `update()` never calls `save()`.

    Identity is the one fact that must not drift, so the rule lives in the
    database where a shell session, a data migration or a future importer
    cannot route around it (master specification 16.2).
    """
    assigned = uuid.uuid4()
    user = User.objects.create_user(
        upn="kindel@example.invalid", display_name="Kindel", entra_object_id=assigned
    )

    with pytest.raises(DatabaseError), transaction.atomic():
        User.objects.filter(pk=user.pk).update(entra_object_id=uuid.uuid4())

    user.refresh_from_db()
    assert user.entra_object_id == assigned


def test_an_assigned_entra_object_id_cannot_be_cleared_by_a_bulk_update():
    assigned = uuid.uuid4()
    user = User.objects.create_user(
        upn="puhastus@example.invalid", display_name="Puhastus", entra_object_id=assigned
    )

    with pytest.raises(DatabaseError), transaction.atomic():
        User.objects.filter(pk=user.pk).update(entra_object_id=None)

    user.refresh_from_db()
    assert user.entra_object_id == assigned


def test_the_first_assignment_is_still_allowed_through_a_bulk_update():
    """This is what the first Entra sign-in does to an existing account."""
    user = User.objects.create_user(upn="esimene@example.invalid", display_name="Esimene")
    assigned = uuid.uuid4()

    User.objects.filter(pk=user.pk).update(entra_object_id=assigned)

    user.refresh_from_db()
    assert user.entra_object_id == assigned


def test_other_fields_remain_updatable_on_a_user_with_an_identity():
    user = User.objects.create_user(
        upn="muudetav@example.invalid",
        display_name="Muudetav",
        entra_object_id=uuid.uuid4(),
    )
    User.objects.filter(pk=user.pk).update(display_name="Uus nimi")
    user.refresh_from_db()
    assert user.display_name == "Uus nimi"


def test_synthetic_users_cannot_carry_a_real_identity():
    with pytest.raises(IntegrityError), transaction.atomic():
        User.objects.create_user(
            upn="vale@example.invalid",
            display_name="Vale",
            is_synthetic=True,
            entra_object_id=uuid.uuid4(),
        )


def test_break_glass_grant_is_time_bounded_and_audited(specialist, department_head):
    grant = grant_break_glass(
        user=specialist,
        granted_by=department_head,
        reason="Tugijuhtum 42",
        duration=timedelta(hours=2),
    )
    assert grant.is_active_at(timezone.now())
    assert not grant.is_active_at(timezone.now() + timedelta(hours=3))
    assert SecurityAuditEvent.objects.filter(
        event_type=SecurityEventType.BREAK_GLASS_GRANTED
    ).exists()


def test_break_glass_requires_a_reason_and_a_bounded_duration(specialist, department_head):
    with pytest.raises(DomainError):
        grant_break_glass(
            user=specialist, granted_by=department_head, reason="  ", duration=timedelta(hours=1)
        )
    with pytest.raises(DomainError):
        grant_break_glass(
            user=specialist,
            granted_by=department_head,
            reason="liiga pikk",
            duration=MAX_BREAK_GLASS_DURATION + timedelta(hours=1),
        )


def test_a_specialist_cannot_grant_break_glass(specialist, other_specialist):
    with pytest.raises(DomainError):
        grant_break_glass(
            user=other_specialist,
            granted_by=specialist,
            reason="ei tohi",
            duration=timedelta(hours=1),
        )


def test_revoking_a_grant_deactivates_it(specialist, department_head):
    grant = grant_break_glass(
        user=specialist,
        granted_by=department_head,
        reason="Tugijuhtum 43",
        duration=timedelta(hours=2),
    )
    revoke_break_glass(grant=grant, revoked_by=department_head)
    grant.refresh_from_db()
    assert not grant.is_active_at(timezone.now())
    assert BreakGlassGrant.objects.active_at(timezone.now()).count() == 0
    assert SecurityAuditEvent.objects.filter(
        event_type=SecurityEventType.BREAK_GLASS_REVOKED
    ).exists()


def test_roles_are_the_ones_the_specification_names():
    assert set(UserRole.values) == {
        "SPECIALIST",
        "DEPARTMENT_HEAD",
        "ADMINISTRATOR",
        "READER",
    }


# --------------------------------------------------------------------------
# The shared PIN in front of the synthetic sign-in.
#
# Not authentication, and not pretending to be: it is a speed bump for an
# instance that is reachable from outside the LAN while its sign-in has no
# password by design. The tests that matter are the ones proving it cannot be
# skipped and cannot be brute-forced at speed.
# --------------------------------------------------------------------------


@pytest.fixture
def pin_login(db, settings):
    from django.core.cache import cache

    settings.DEV_LOGIN_ENABLED = True
    settings.DEV_LOGIN_PIN = "1925"
    settings.DEV_LOGIN_PIN_MAX_ATTEMPTS = 5
    settings.DEV_LOGIN_PIN_LOCKOUT_SECONDS = 300
    cache.clear()
    return factories.UserFactory(is_synthetic=True)


def _post(client, user, pin=None):
    payload = {"user_id": str(user.pk)}
    if pin is not None:
        payload["pin"] = pin
    return client.post(reverse("accounts:dev_login"), payload)


def test_the_right_pin_signs_you_in(client, pin_login):
    response = _post(client, pin_login, "1925")
    assert response.status_code == 302
    assert response.wsgi_request.user.is_authenticated


def test_the_wrong_pin_does_not(client, pin_login):
    response = _post(client, pin_login, "0000")
    assert response.status_code == 400
    assert not response.wsgi_request.user.is_authenticated


def test_omitting_the_pin_entirely_does_not_skip_it(client, pin_login):
    """The failure mode worth naming: a missing field must not read as valid."""
    response = _post(client, pin_login)
    assert response.status_code == 400
    assert not response.wsgi_request.user.is_authenticated


def test_repeated_wrong_pins_lock_the_caller_out(client, pin_login):
    """Four digits is 10,000 guesses. Without this, that is seconds of work."""
    for _ in range(5):
        assert _post(client, pin_login, "0000").status_code == 400

    # Locked out now — even the correct PIN is refused.
    response = _post(client, pin_login, "1925")
    assert response.status_code == 429
    assert not response.wsgi_request.user.is_authenticated


def test_a_correct_pin_clears_the_counter(client, pin_login):
    """A typo must not cost somebody the rest of the lockout window."""
    for _ in range(3):
        _post(client, pin_login, "0000")
    assert _post(client, pin_login, "1925").status_code == 302

    client.logout()
    for _ in range(4):
        assert _post(client, pin_login, "0000").status_code == 400
    assert _post(client, pin_login, "1925").status_code == 302


def test_the_lockout_counts_the_forwarded_address_behind_a_proxy(client, pin_login):
    """Behind the tunnel every request shares one REMOTE_ADDR."""
    for _ in range(5):
        client.post(
            reverse("accounts:dev_login"),
            {"user_id": str(pin_login.pk), "pin": "0000"},
            HTTP_CF_CONNECTING_IP="203.0.113.9",
        )
    blocked = client.post(
        reverse("accounts:dev_login"),
        {"user_id": str(pin_login.pk), "pin": "1925"},
        HTTP_CF_CONNECTING_IP="203.0.113.9",
    )
    assert blocked.status_code == 429

    # A different caller is unaffected.
    other = client.post(
        reverse("accounts:dev_login"),
        {"user_id": str(pin_login.pk), "pin": "1925"},
        HTTP_CF_CONNECTING_IP="203.0.113.10",
    )
    assert other.status_code == 302


def test_no_pin_configured_means_no_pin_field(client, db, settings):
    """The default everywhere else: a laptop and CI have no PIN."""
    from django.core.cache import cache

    settings.DEV_LOGIN_ENABLED = True
    settings.DEV_LOGIN_PIN = ""
    cache.clear()
    user = factories.UserFactory(is_synthetic=True)

    assert _post(client, user).status_code == 302


def test_a_failed_pin_is_recorded_as_a_security_event(client, pin_login):
    from app.audit.models import SecurityAuditEvent

    _post(client, pin_login, "0000")
    event = SecurityAuditEvent.objects.order_by("-created_at").first()
    assert event is not None
    assert event.succeeded is False
    assert event.detail.get("reason") == "bad_pin"
