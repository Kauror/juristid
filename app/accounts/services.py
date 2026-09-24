"""Named use cases for identity and emergency access."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta

from django.core.exceptions import ValidationError
from django.core.validators import validate_email
from django.db import transaction
from django.utils import timezone

from app.accounts.enums import UserRole
from app.accounts.models import BreakGlassGrant, User
from app.accounts.selectors import DEPARTMENT_WORK_ROLES, is_department_worker
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


class ProvisioningRefused(DomainError):
    """The account was not created or changed, and the message says why."""


@dataclass(frozen=True)
class Provisioned:
    user: User
    #: False when an identical account already existed and nothing was written.
    created: bool


@transaction.atomic
def provision_department_user(
    *, upn: str, display_name: str, role: str = UserRole.SPECIALIST
) -> Provisioned:
    """Create the account a real member of the department signs in as.

    What that account is, is already decided elsewhere, and this only applies
    it (ENG-013):

    * **The UPN is the identity.** `cloudflare_access` signs in the active,
      non-synthetic account whose UPN matches the address Cloudflare asserts,
      so it has to be that address — not a name, and never an `.invalid` one
      (deploy/unraid-main/README.md, step 6).
    * **A department role, and nothing technical.** SPECIALIST or
      DEPARTMENT_HEAD, not staff, not superuser: that is what makes somebody a
      persona and somebody work can be given to (`selectors.department_workers`,
      docs/adr/0034, docs/adr/0036). The technical administrator is a separate
      account, made with `createsuperuser`, and is never either.
    * **No password.** Neither the shared gate nor Cloudflare Access reads one,
      and a usable password on a real account is a way in nobody decided on.
    * **The Entra object id is left alone.** It is immutable once set and
      reserved for an authentication mode not yet in use.

    Idempotent, and never an edit: asking again for exactly the account that
    exists changes nothing; asking for anything different about an existing
    UPN is refused, naming the difference. A role change, a rename or a
    reactivation is a decision about a person, and it is not made here by
    accident.
    """
    upn = (upn or "").strip().lower()
    display_name = (display_name or "").strip()
    try:
        validate_email(upn)
    except ValidationError as error:
        raise ProvisioningRefused(
            f"'{upn}' is not an e-mail address. The UPN is the address Cloudflare Access "
            "asserts, so it has to be the person's real one."
        ) from error
    if upn.endswith(".invalid"):
        raise ProvisioningRefused(
            f"'{upn}' is a made-up address. Real accounts use the person's real address; "
            ".invalid identities belong to the synthetic worlds."
        )
    if not display_name:
        raise ProvisioningRefused("A display name is required: it is how colleagues find them.")
    if role not in DEPARTMENT_WORK_ROLES:
        roles = ", ".join(sorted(DEPARTMENT_WORK_ROLES))
        raise ProvisioningRefused(
            f"'{role}' is not a department role. This makes {roles} accounts; the "
            "technical administrator is `createsuperuser`, and never a persona."
        )

    existing = User.objects.select_for_update().filter(upn__iexact=upn).first()
    if existing is not None:
        differences = _differences(existing, display_name=display_name, role=role)
        if differences:
            raise ProvisioningRefused(
                f"{existing.upn} already exists and differs: {'; '.join(differences)}. "
                "Nothing was changed."
            )
        return Provisioned(user=existing, created=False)

    user = User.objects.create_user(upn=upn, display_name=display_name, role=role)
    if not is_department_worker(user):  # pragma: no cover - create_user's own defaults
        raise ProvisioningRefused(f"{upn} was created but is not a department worker.")
    record_security_event(
        event_type=SecurityEventType.ROLE_CHANGED,
        actor=None,
        subject=user,
        detail={
            "reason": "account_provisioned",
            "via": "manage.py provision_user",
            "previous_role": None,
            "role": role,
        },
    )
    return Provisioned(user=user, created=True)


def _differences(user: User, *, display_name: str, role: str) -> list[str]:
    wanted = {
        "display name": (user.display_name, display_name),
        "role": (user.role, role),
        "active": (user.is_active, True),
        "staff": (user.is_staff, False),
        "superuser": (user.is_superuser, False),
        "synthetic": (user.is_synthetic, False),
    }
    return [
        f"{label} is {current!r}, asked for {asked!r}"
        for label, (current, asked) in wanted.items()
        if current != asked
    ]


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
