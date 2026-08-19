"""Named use cases for identity and emergency access."""

from __future__ import annotations

from datetime import datetime, timedelta

from django.db import transaction
from django.utils import timezone

from app.accounts.enums import UserRole
from app.accounts.models import BreakGlassGrant, User
from app.audit.enums import SecurityEventType
from app.audit.services import record_security_event
from app.core.errors import DomainError

# A grant is emergency access, not a second permanent role.
MAX_BREAK_GLASS_DURATION = timedelta(hours=24)


@transaction.atomic
def create_synthetic_user(
    *,
    upn: str,
    display_name: str,
    role: str = UserRole.SPECIALIST,
    is_staff: bool = False,
) -> User:
    """Create a development-only account with no real identity attached."""
    return User.objects.create_user(
        upn=upn,
        display_name=display_name,
        role=role,
        is_staff=is_staff,
        is_synthetic=True,
    )


@transaction.atomic
def grant_break_glass(
    *,
    user: User,
    granted_by: User,
    reason: str,
    duration: timedelta,
    starts_at: datetime | None = None,
) -> BreakGlassGrant:
    """Give one user time-bounded sight of RESTRICTED content."""
    if not reason.strip():
        raise DomainError("A break-glass grant requires a written reason.")
    if duration <= timedelta(0):
        raise DomainError("A break-glass grant must last a positive amount of time.")
    if duration > MAX_BREAK_GLASS_DURATION:
        raise DomainError(f"A break-glass grant may not exceed {MAX_BREAK_GLASS_DURATION}.")
    if granted_by.role != UserRole.DEPARTMENT_HEAD and not granted_by.is_superuser:
        raise DomainError(
            "Only the department head or a system owner may grant break-glass access."
        )

    begins = starts_at or timezone.now()
    grant = BreakGlassGrant.objects.create(
        user=user,
        granted_by=granted_by,
        reason=reason.strip(),
        starts_at=begins,
        expires_at=begins + duration,
    )
    record_security_event(
        event_type=SecurityEventType.BREAK_GLASS_GRANTED,
        actor=granted_by,
        subject=grant,
        detail={
            "user": str(user.pk),
            "expires_at": grant.expires_at.isoformat(),
            "reason": grant.reason,
        },
    )
    return grant


@transaction.atomic
def revoke_break_glass(*, grant: BreakGlassGrant, revoked_by: User) -> BreakGlassGrant:
    if grant.revoked_at is not None:
        return grant
    grant.revoked_at = timezone.now()
    grant.revoked_by = revoked_by
    grant.save(update_fields=["revoked_at", "revoked_by", "updated_at"])
    record_security_event(
        event_type=SecurityEventType.BREAK_GLASS_REVOKED,
        actor=revoked_by,
        subject=grant,
        detail={"user": str(grant.user_id)},
    )
    return grant
