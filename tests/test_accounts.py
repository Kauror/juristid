"""Custom user, Entra identity reservation and break-glass access."""

from __future__ import annotations

import uuid
from datetime import timedelta

import pytest
from django.db import IntegrityError, transaction
from django.db.migrations.loader import MigrationLoader
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


def test_entra_object_id_may_be_assigned_to_a_user_that_had_none(specialist):
    assert specialist.entra_object_id is None
    specialist.entra_object_id = uuid.uuid4()
    specialist.save()
    specialist.refresh_from_db()
    assert specialist.entra_object_id is not None


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
