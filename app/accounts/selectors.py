"""Who does the department's work, in one place.

An account is not a colleague. Somebody has to administer the system, somebody
may be given read-only sight of the register, and the deployment's own technical
accounts are accounts like any other — none of which makes any of them a person
the department's policy and legal work can belong to. This module holds the one
answer to *is this a current department worker*, and the surfaces that need that
answer for their own reasons read it here rather than each rebuilding it.

Two questions are asked of the same rule, and they are deliberately the same
rule rather than two that happen to agree today:

**Whose work is this application showing?** — the persona list behind the
shared door (docs/adr/0034).

**Who may current business work be given to?** — the Vastutaja on a new Teema,
the Vastutaja on an existing one, the owner on `Saabunud`, and the person a
`Järgmiseks` step is set for (docs/adr/0036).

A third question that is *not* this one, and is answered a few sections down:
**whose name does the stored work carry?** The `Vastutaja` filters on Teemad and
Statistika ask that, not this — see `owner_filter_choices`.

Before those were joined, the persona list was narrowed and the ownership
controls were not, so an account that had just been refused as a persona was
still one dropdown away from owning a file. Two rules that mean the same thing
drift, and they drift silently, because nothing fails when one of them is
widened.

Three properties this module exists to hold:

**One definition, read by both halves of every flow.** The GET that renders a
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
to be somebody, or a way to be given somebody's files. An administrator who
genuinely does departmental work holds a department role in their own right, on
their own account — that is the shape the model is built for, and it is the
shape that keeps an audit row honest.

What this module does **not** answer: who *historically* owns a record. A file
assigned to a colleague who has since left is still theirs, the register still
says so, and nothing here rewrites that. Narrowing a chooser is a statement
about new work only, which is what `assignable_including` exists to keep true.
"""

from __future__ import annotations

import uuid
from typing import Any

from django.db.models import Q, QuerySet

from app.accounts.enums import UserRole
from app.accounts.models import User

#: The business roles that do the department's policy and legal work.
#: ADMINISTRATOR and READER are deliberately absent, for the reason the module
#: docstring gives.
DEPARTMENT_WORK_ROLES: frozenset[str] = frozenset(
    {UserRole.SPECIALIST.value, UserRole.DEPARTMENT_HEAD.value}
)

#: The persona list's roles, which are these roles. The same object rather than
#: a second frozenset with the same contents: a copy is something to keep in
#: step, and this one was already read by name elsewhere.
PERSONA_ROLES: frozenset[str] = DEPARTMENT_WORK_ROLES


# ---------------------------------------------------------------------------
# The rule
# ---------------------------------------------------------------------------


def department_workers() -> QuerySet[User]:
    """Everybody who currently does the department's work, and nobody else.

    Ordered by display name, because every list built from this is read by
    somebody looking for a colleague rather than by a machine.

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
        role__in=sorted(DEPARTMENT_WORK_ROLES),
        is_staff=False,
        is_superuser=False,
    ).order_by("display_name")


def is_department_worker(user: object | None) -> bool:
    """Whether this particular account currently does department work.

    Asked of an object rather than of the database, so a caller that already
    holds a `User` does not need a second query to find out. It answers exactly
    what `department_workers()` answers, and the parity is asserted directly
    rather than assumed: a predicate that has drifted from its queryset is a
    rule enforced in one half of a flow and not the other.
    """
    if user is None or getattr(user, "pk", None) is None:
        return False
    if not getattr(user, "is_active", False):
        return False
    if getattr(user, "is_staff", False) or getattr(user, "is_superuser", False):
        return False
    return str(getattr(user, "role", "") or "") in DEPARTMENT_WORK_ROLES


# ---------------------------------------------------------------------------
# Persona — whose work the application is showing
# ---------------------------------------------------------------------------


def persona_candidates() -> QuerySet[User]:
    """The people who may be offered, and accepted, as a persona."""
    return department_workers()


def is_persona_candidate(user: object | None) -> bool:
    """Whether this particular account may be a persona."""
    return is_department_worker(user)


def persona_from_id(raw_id: str) -> User | None:
    """The candidate with this identifier, or nothing at all.

    Nothing at all covers every way the value can be wrong — not a UUID, no such
    user, an inactive one, a technical account, a role that does not do
    department work — because the caller's answer is the same in all of them and
    a refusal that distinguishes them tells somebody probing which guess was
    closer.
    """
    return _one_of(persona_candidates(), raw_id)


# ---------------------------------------------------------------------------
# Assignment — who current business work may be given to
# ---------------------------------------------------------------------------


def assignable_business_users() -> QuerySet[User]:
    """The people current business work may be assigned to.

    Every control that hands out *new* work reads this: the Vastutaja on
    `Uus teema` and on `Saabunud`, the owner control on the Teema header and on
    the edit page, and the person a `Järgmiseks` step names.

    Not a superset of the persona list and not a subset of it — the same list.
    Being somebody the application can show work as, and being somebody work can
    be given to, are one property of one account, and on the day they were two
    functions one of them was wrong.
    """
    return department_workers()


def is_assignable_business_user(user: object | None) -> bool:
    """Whether current business work may be assigned to this account."""
    return is_department_worker(user)


def assignable_including(*bound: object) -> QuerySet[User]:
    """The assignable workers, plus the particular people a record already names.

    The one place the historical/current distinction is actually implemented,
    and the reason it is a function rather than a note in a review.

    A Matter filed in 2019 may be owned by somebody who has since left, or by
    somebody whose account now exists only to administer the system. That is a
    true fact about the record, and correcting the Matter's *title* must not
    depend on rewriting it. A form whose queryset were narrowed to the current
    workers would refuse the unchanged value: the POST carries an owner the
    field does not know, validation fails, and the page either rejects an
    innocent edit or — the field being optional — silently clears an owner
    nobody asked to remove.

    So the bound value joins the population, and only the bound value: exactly
    the people this record already names, never "anybody inactive". A crafted
    POST naming a *different* non-assignable account is still refused, because
    that account is not in this queryset either.

    The same shape as `MatterEditForm`'s retired-Valdkonna handling, and for the
    same reason (Teema redesign §7.2): validation accepts what the record
    carries; the chooser offers what may be chosen today.
    """
    kept = {
        primary_key
        for primary_key in (getattr(person, "pk", None) for person in bound)
        if primary_key is not None
    }
    if not kept:
        return assignable_business_users()
    # `order_by()` first: the base list is ordered for presentation, and an
    # ordered subquery inside `IN (…)` sorts a set nobody reads.
    return User.objects.filter(
        Q(pk__in=department_workers().order_by().values("pk")) | Q(pk__in=kept)
    ).order_by("display_name")


# ---------------------------------------------------------------------------
# Filtering — who the stored records actually name
# ---------------------------------------------------------------------------


def owner_filter_choices(population: QuerySet[Any]) -> QuerySet[User]:
    """The people a `Vastutaja` **filter** should offer, for one authorized set.

    A chooser and a filter are not the same control wearing two labels. A
    chooser hands out work, so it offers the people work may be given to. A
    filter describes work that already exists, so it has to offer the people the
    records *name* — and a register whose filter cannot reach a departed
    colleague's seventeen unhandled files is a register that hides exactly the
    work somebody is looking for. Narrowing the chooser was the point of
    `assignable_business_users`; narrowing the filter with it was a mistake that
    read as tidiness.

    So: today's department workers — who belong on the list whether or not they
    currently hold anything, because filtering to a colleague and getting an
    honest empty page is a useful answer — union the people who genuinely own
    something in ``population``.

    **``population`` is the authorization boundary, and it is the caller's job.**
    Never `User.objects.all()`, never every owner in the database: an option is
    a name on a page, and a name that appears only because of records this
    reader may not see tells them that a colleague, a file and a working
    relationship exist. The caller passes the queryset its own surface is
    allowed to show — `Matter.objects.visible_to(viewer)` for the register, the
    same narrowed by `real_data()` for the reports — and passes it *before* its
    own owner filter, so choosing a name does not collapse the list to that one
    name and strand the reader with no way back.

    ``order_by()`` on the subqueries deliberately: both sides are ordered for
    presentation, and an ORDER BY inside `IN (…)` sorts a set nobody reads while
    dragging its sort columns into the SELECT.
    """
    represented = population.order_by().exclude(owner_id=None).values("owner_id")
    return User.objects.filter(
        Q(pk__in=department_workers().order_by().values("pk")) | Q(pk__in=represented)
    ).order_by("display_name")


def named_owner_in(population: QuerySet[Any], raw_id: str) -> User | None:
    """The person a `Vastutaja` filter value names — if this reader's own data
    names them.

    A filter *chip* is a rendered name, and it was being rendered by looking the
    query string's UUID up in `User.objects`. That turns the address bar into a
    directory: anybody who knows or guesses an identifier gets a colleague's
    name back, and gets it precisely when that person appears nowhere the reader
    is allowed to look — which is when the answer is most revealing, because it
    says a person, a file and a working relationship exist (AUTH-003).

    So the label resolves against the same bounded population that made the
    *option* legitimate in the first place: today's department workers, union
    the owners genuinely represented in ``population``. A caller passes the
    queryset its own surface may show, exactly as it already does for the
    dropdown — and a name that is not in it is not a name this reader learns.

    Returning ``None`` rather than raising: an unrecognised value is a filter
    that matches nothing, which is a legitimate if unhelpful thing to ask for,
    and the caller decides what to print instead.
    """
    return _one_of(owner_filter_choices(population), raw_id)


def _one_of(population: QuerySet[User], raw_id: str) -> User | None:
    """One row of a narrowed population, by identifier, or nothing at all.

    The identifier is parsed rather than handed to the ORM: `pk="puudub"` is not
    a failed lookup but a ValidationError, and anything at all can arrive in a
    query string or a POST body.
    """
    try:
        identifier = uuid.UUID(str(raw_id))
    except (ValueError, AttributeError, TypeError):
        return None
    return population.filter(pk=identifier).first()
