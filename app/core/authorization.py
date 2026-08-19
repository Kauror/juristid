"""The single authorization chokepoint.

Every read of business content — lists, detail pages, search, counts, exports,
downloads and later AI retrieval — resolves through ``scope_for_user`` and the
``Q`` builders here. Modules must not re-implement visibility rules locally.

Three rules matter and are tested:

1. Technical administration is not business access. Neither ``is_superuser``
   nor the ADMINISTRATOR role grants sight of RESTRICTED content; a
   time-bounded, audited break-glass grant does.
2. A child record's effective visibility is **derived** from its Matter and its
   own override, here, in SQL. Nothing stores it, so nothing can hold a stale
   copy after a bulk update, a data migration or a shell session.
3. A child override can only make a record more restrictive. That is a property
   of the derivation — it takes the more restrictive of the two — rather than a
   rule something has to remember to enforce.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass

from django.apps import apps
from django.db.models import Case, CharField, Q, QuerySet, Value, When
from django.utils import timezone

from app.accounts.enums import UserRole
from app.core.enums import Visibility

# Business roles that may read RESTRICTED content without a break-glass grant.
# ADMINISTRATOR is deliberately absent (master specification 5.2).
ROLES_WITH_RESTRICTED_ACCESS: frozenset[str] = frozenset({UserRole.DEPARTMENT_HEAD.value})


@dataclass(frozen=True)
class Scope:
    """What one user may currently see."""

    user: object | None
    is_authenticated: bool
    sees_restricted_by_role: bool
    break_glass_grant_id: uuid.UUID | None

    @property
    def sees_all_restricted(self) -> bool:
        return self.sees_restricted_by_role or self.break_glass_grant_id is not None


def active_break_glass_grant_id(user: object) -> uuid.UUID | None:
    grant_model = apps.get_model("accounts", "BreakGlassGrant")
    grant = (
        grant_model.objects.active_at(timezone.now())
        .filter(user=user)
        .order_by("-created_at")
        .first()
    )
    return grant.id if grant is not None else None


def scope_for_user(user: object | None) -> Scope:
    if user is None or not getattr(user, "is_authenticated", False):
        return Scope(
            user=None,
            is_authenticated=False,
            sees_restricted_by_role=False,
            break_glass_grant_id=None,
        )
    if not getattr(user, "is_active", False):
        return Scope(
            user=None,
            is_authenticated=False,
            sees_restricted_by_role=False,
            break_glass_grant_id=None,
        )

    role = getattr(user, "role", "")
    return Scope(
        user=user,
        is_authenticated=True,
        sees_restricted_by_role=role in ROLES_WITH_RESTRICTED_ACCESS,
        break_glass_grant_id=active_break_glass_grant_id(user),
    )


NOTHING = Q(pk__in=[])


def matter_visibility_q(
    scope: Scope,
    *,
    prefix: str = "",
    visibility_field: str = "visibility",
    owner_field: str = "owner",
    collaborators_field: str = "collaborators",
) -> Q:
    """Rows of (or joined to) Matter that ``scope`` may read."""
    if not scope.is_authenticated:
        return NOTHING
    if scope.sees_all_restricted:
        return Q()

    normal = Q(**{f"{prefix}{visibility_field}": Visibility.NORMAL})
    return normal | restricted_participation_q(
        scope, prefix=prefix, owner_field=owner_field, collaborators_field=collaborators_field
    )


def restricted_participation_q(
    scope: Scope,
    *,
    prefix: str = "",
    owner_field: str = "owner",
    collaborators_field: str = "collaborators",
) -> Q:
    """The Matter participation that unlocks RESTRICTED content."""
    if not scope.is_authenticated:
        return NOTHING
    return Q(**{f"{prefix}{owner_field}": scope.user}) | Q(
        **{f"{prefix}{collaborators_field}": scope.user}
    )


# The only override values that mean "no extra restriction". Anything else —
# including a value some future migration or integration invents — is treated as
# restricted. Authorization whitelists; it never blacklists.
UNRESTRICTED_OVERRIDE_VALUES: tuple[str, ...] = ("", Visibility.NORMAL.value)


def child_is_normal_q(
    *,
    parent_prefix: str = "matter__",
    override_field: str = "visibility_override",
) -> Q:
    """Child rows whose derived effective visibility is NORMAL.

    Effective visibility is the more restrictive of the Matter's visibility and
    the child's own override, so a child is NORMAL only when **both** are
    explicitly normal.

    Both halves are inclusive whitelists rather than "not RESTRICTED" exclusions.
    An unrecognised value in either column then fails closed — it is simply not
    in the allowed set, so the row does not appear — instead of being read as
    permissive. A CHECK constraint keeps such values out of the tables in the
    first place; this is the second lock on the same door.
    """
    return Q(**{f"{parent_prefix}visibility": Visibility.NORMAL}) & Q(
        **{f"{override_field}__in": UNRESTRICTED_OVERRIDE_VALUES}
    )


def child_visibility_q(
    scope: Scope,
    *,
    parent_prefix: str = "matter__",
    override_field: str = "visibility_override",
) -> Q:
    """Child rows that ``scope`` may read, derived from the parent every time."""
    if not scope.is_authenticated:
        return NOTHING
    if scope.sees_all_restricted:
        return Q()

    return child_is_normal_q(
        parent_prefix=parent_prefix, override_field=override_field
    ) | restricted_participation_q(scope, prefix=parent_prefix)


def effective_visibility_expression(
    *,
    parent_prefix: str = "matter__",
    override_field: str = "visibility_override",
) -> Case:
    """SQL for a child's effective visibility, for annotating list queries."""
    return Case(
        When(
            **{f"{parent_prefix}visibility": Visibility.RESTRICTED},
            then=Value(Visibility.RESTRICTED.value),
        ),
        When(
            **{override_field: Visibility.RESTRICTED},
            then=Value(Visibility.RESTRICTED.value),
        ),
        default=Value(Visibility.NORMAL.value),
        output_field=CharField(max_length=16),
    )


def apply[QuerySetT: QuerySet](queryset: QuerySetT, condition: Q) -> QuerySetT:
    """Apply a visibility condition, collapsing the many-to-many join fan-out."""
    if not condition.children and not condition.negated:
        return queryset
    return queryset.filter(condition).distinct()
