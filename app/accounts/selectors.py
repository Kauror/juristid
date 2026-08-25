"""Who may be offered as a persona, in one place.

The persona list is not "every account that can sign in". It answers a business
question — *whose work is this application showing?* — and the only people that
question has an answer for are the ones who do the department's policy and legal
work. An account exists for other reasons too: somebody has to administer the
system, somebody may be given read-only sight of the register, and the
deployment's own technical accounts are accounts like any other.

Three properties this module exists to hold:

**One definition, read by both halves of the flow.** The GET that renders the
list and the POST that acts on it call the same function. A list narrowed only
in the template is a list an attacker types a UUID past, and the shared gate
makes that a realistic shape: everybody behind the door can post to the switch
endpoint, so hiding a row in HTML hides nothing at all.

**Roles, never names.** The department's people change. A hard-coded colleague
is a rule that has to be found and edited the day somebody joins or leaves, and
the edit is made by whoever notices — which is how a list quietly grows an
account that should not be on it. The rule is the role, and the database says
who currently holds one.

**Technical access is not business identity.** This is the same refusal
`app/core/authorization.py` already makes about RESTRICTED content, applied one
step earlier: ADMINISTRATOR and READER are not department work roles, and
``is_staff`` / ``is_superuser`` are technical grants that must not become a way
to be somebody. An administrator who genuinely does departmental work holds a
department role in their own right, on their own account — that is the shape the
model is built for, and it is the shape that keeps an audit row honest
(docs/adr/0034).
"""

from __future__ import annotations

import uuid

from django.db.models import QuerySet

from app.accounts.enums import UserRole
from app.accounts.models import User

#: The business roles that do the department's policy and legal work, and are
#: therefore the only roles a persona may represent. ADMINISTRATOR and READER
#: are deliberately absent, for the reason the module docstring gives.
PERSONA_ROLES: frozenset[str] = frozenset(
    {UserRole.SPECIALIST.value, UserRole.DEPARTMENT_HEAD.value}
)


def persona_candidates() -> QuerySet[User]:
    """The people who may be offered, and accepted, as a persona.

    Ordered by display name, because the list is read by somebody looking for a
    colleague rather than by a machine.

    ``is_staff`` and ``is_superuser`` are excluded even when the account also
    carries a department role. That is stricter than "role is enough", and
    deliberately so: a technical account is exactly what a crafted POST would
    reach for, and this deployment's identity model keeps technical
    administration on separate accounts from business work. The cost is that
    granting a lawyer Django-admin access would take them off the list, which is
    a visible, fixable inconvenience — the opposite failure, a privileged
    account quietly becoming selectable, is neither.
    """
    return User.objects.filter(
        is_active=True,
        role__in=sorted(PERSONA_ROLES),
        is_staff=False,
        is_superuser=False,
    ).order_by("display_name")


def is_persona_candidate(user: object | None) -> bool:
    """Whether this particular account may be a persona.

    Asked of an object rather than of the database, so a caller that already
    holds a `User` does not need a second query to find out.
    """
    if user is None or getattr(user, "pk", None) is None:
        return False
    if not getattr(user, "is_active", False):
        return False
    if getattr(user, "is_staff", False) or getattr(user, "is_superuser", False):
        return False
    return str(getattr(user, "role", "") or "") in PERSONA_ROLES


def persona_from_id(raw_id: str) -> User | None:
    """The candidate with this identifier, or nothing at all.

    Nothing at all covers every way the value can be wrong — not a UUID, no such
    user, an inactive one, a technical account, a role that does not do
    department work — because the caller's answer is the same in all of them and
    a refusal that distinguishes them tells somebody probing which guess was
    closer.
    """
    try:
        identifier = uuid.UUID(str(raw_id))
    except (ValueError, AttributeError, TypeError):
        return None
    return persona_candidates().filter(pk=identifier).first()
