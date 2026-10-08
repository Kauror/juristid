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

import contextlib
import contextvars
import functools
import operator
import uuid
from collections.abc import Iterator
from dataclasses import dataclass
from typing import Any

from django.apps import apps
from django.db.models import Case, CharField, Count, Q, QuerySet, Value, When
from django.db.models.sql.datastructures import Join
from django.utils import timezone

from app.accounts import capabilities
from app.accounts.enums import Capability, UserRole
from app.core.enums import Visibility

# Business roles that may read RESTRICTED content without a break-glass grant.
#
# Both lawyer roles, because inside the legal department the confidentiality
# boundary is the application, not the Matter. A lawyer who cannot see what a
# colleague is working on cannot answer for the department's position on it, and
# the register this replaces was a shared notebook. `RESTRICTED` still divides
# the department from everyone else — it just stopped dividing one lawyer from
# another (docs/adr/0042).
#
# Ownership and `collaborators` stay exactly where they are, and keep meaning
# what they always meant: who is answerable for a file and who is working it.
# That is a question about responsibility, and it was never the same question as
# who may know the file exists.
#
# ADMINISTRATOR is still deliberately absent, and that absence carries more
# weight now that it is the only business role left outside: technical
# administration is not legal work, and an administrator who needs to read a
# restricted file uses break-glass, which is audited (master specification 5.2).
# READER is absent because this decision is about the legal team; what a
# management or communications reader may see is a separate decision nobody has
# taken (specification 5.1).
ROLES_WITH_RESTRICTED_ACCESS: frozenset[str] = frozenset(
    {UserRole.SPECIALIST.value, UserRole.DEPARTMENT_HEAD.value}
)

# Roles that may add or change business content on a Matter they can already
# see. ADMINISTRATOR is absent for the same reason it is absent above: technical
# administration is not business authorship, and an administrator who needs to
# author work has a business role for it. READER is absent because reading
# without editing is the entire definition of the role (specification 5.1).
ROLES_WITH_BUSINESS_WRITE: frozenset[str] = frozenset(
    {UserRole.SPECIALIST.value, UserRole.DEPARTMENT_HEAD.value}
)

# Who may turn a claimed work victory into a confirmed one, or record that it
# did not happen. A `Töövõit` is the department's own claim about its influence,
# so the judgement belongs with the person answerable for it. Specialists write
# and edit candidates freely; only the department head decides
# (specification 5.1, Stage-2G brief 25).
#
# Since docs/adr/0145 this is the *default* of the `work_victory.review`
# capability rather than a rule of its own — the same set, read from the one
# place the capability defaults are written, so the two cannot drift. An
# individual grant or denial is resolved by `has_capability` below.
ROLES_WITH_WORK_VICTORY_REVIEW: frozenset[str] = capabilities.DEFAULT_HOLDERS[
    Capability.REVIEW_WORK_VICTORIES.value
]


def acting_role(user: object | None) -> str:
    """The role a *person* is acting under, or "" if there is no person.

    The shared-gate sentinel and anonymous users fall through to "", which is in
    none of the role sets above. That is the property that keeps a shared
    password from authoring anything: `DepartmentViewer` is not somebody, it
    cannot be an audit actor, and it must never reach a write
    (docs/adr/0016, Stage-2G brief 29).
    """
    if isinstance(user, DepartmentViewer):
        return ""
    if user is None or not getattr(user, "is_authenticated", False):
        return ""
    if not getattr(user, "is_active", False):
        return ""
    return str(getattr(user, "role", "") or "")


def may_write_business_content(user: object | None) -> bool:
    """May this user add or change business content on a Matter they can read?

    Deliberately not "is this the Matter's owner". The department maintains
    these records collaboratively today, and narrowing authorship to the owner
    would make the product slower than the OneNote page it replaces
    (Stage-2G brief 29). *Which* Matters they can reach is a separate question,
    answered by `matter_visibility_q` before this is ever asked.

    Since docs/adr/0042 that reach is the whole department for both lawyer
    roles, so a specialist may now edit a colleague's RESTRICTED Matter exactly
    as they could already edit a colleague's NORMAL one. That is the same rule
    it always was, applied to a wider set of Matters — not a new one. What it
    does not touch is *which operations* a role may perform: a specialist who
    can now open a restricted file still cannot review a `Töövõit`, because that
    is decided by `may_review_work_victory` rather than by reach.
    """
    return acting_role(user) in ROLES_WITH_BUSINESS_WRITE


def may_review_work_victory(user: object | None) -> bool:
    """May this user confirm a work victory, or record that it did not happen?

    The department head by default, and anybody a capability grant names —
    never a READER or a technical administrator, because confirming one is a
    business write (docs/adr/0145 §3).
    """
    return has_capability(user, Capability.REVIEW_WORK_VICTORIES)


# ---------------------------------------------------------------------------
# Capabilities — the operations a role check used to answer (docs/adr/0145)
# ---------------------------------------------------------------------------


def capability_overrides(user: object | None) -> dict[str, str]:
    """This person's own allow/deny overrides, cleaned of anything unrecognised.

    Read from the user row the request already loaded. That is deliberate: the
    overrides are a column on the account rather than a table beside it, so
    asking a capability question costs no query — the context processor asks
    one on every page, and an extra round trip per page per question is the
    kind of cost the statistics pages already had to be rescued from
    (PERF-01).
    """
    if user is None:
        return {}
    return capabilities.clean_overrides(getattr(user, "capability_overrides", None))


def has_capability(user: object | None, capability: str) -> bool:
    """Whether this person currently holds ``capability``.

    In order, and every refusal fails closed:

    1. Somebody acting, through `acting_role` — so the shared-gate sentinel, an
       anonymous visitor and an inactive account hold nothing, exactly as they
       hold no role.
    2. A capability nobody has heard of is held by nobody.
    3. A write capability needs the business-write role underneath it. An
       override cannot make a READER or a technical administrator an author
       (docs/adr/0037).
    4. The person's own override, if they have one, decides; otherwise their
       role's default does.
    5. A capability that needs another one (`accounts.delegate` needs
       `accounts.manage`) is held only together with it.

    This answers whether the *account* holds a capability. Whether *this
    request* may exercise an administrative one is a stronger question — it
    also needs an individually authenticated, second-factor-verified session
    in `local_password` mode — and is answered by
    `app.accounts.local_auth.may_administer_accounts`, which asks this first.
    """
    return resolve_capability(acting_role(user), capability_overrides(user), str(capability))


def resolve_capability(role: str, overrides: dict[str, str], capability: str) -> bool:
    """Steps 2–5 of `has_capability`, for a role and a set of overrides.

    The one place the rule is written. `has_capability` hands it the role of
    somebody *acting* — nothing for an inactive account — and the account
    administration hands it an account's stored role, to show and to diff what
    the account will hold once it is active (`account_capabilities`).
    """
    if not role or not capabilities.is_known(capability):
        return False
    if capability in capabilities.REQUIRES_BUSINESS_WRITE and role not in ROLES_WITH_BUSINESS_WRITE:
        return False

    override = overrides.get(capability)
    if override == capabilities.DENY:
        return False
    if override == capabilities.ALLOW:
        held = capabilities.may_be_granted_to(role, capability)
    else:
        held = capabilities.role_default(role, capability)
    if not held:
        return False

    prerequisite = capabilities.PREREQUISITES.get(capability)
    return prerequisite is None or resolve_capability(role, overrides, prerequisite)


def effective_capabilities(user: object | None) -> frozenset[str]:
    """Every capability this person holds, for display and for the audit."""
    return frozenset(
        capability
        for capability in capabilities.ALL_CAPABILITIES
        if has_capability(user, capability)
    )


def account_capabilities(user: object | None) -> frozenset[str]:
    """What this *account* grants while it is active — whether or not it is now.

    For account administration only: a pending account's page shows, and its
    save compares against, what the person will hold once they have activated.
    Never an authorization answer — an inactive account holds nothing, and
    `has_capability` is the only question any gate asks.
    """
    role = str(getattr(user, "role", "") or "") if user is not None else ""
    overrides = capability_overrides(user)
    return frozenset(
        capability
        for capability in capabilities.ALL_CAPABILITIES
        if resolve_capability(role, overrides, capability)
    )


def may_view_department_management(user: object | None) -> bool:
    """The Osakond page's *Meeskond* and *Tehtud*, and a colleague's Minu asjad.

    The department head's surfaces since docs/adr/0049, by role — and now by the
    `department.view_management` capability whose default is that role, so an
    administrator can lend the view to a deputy without making them the head.
    What the sections then *show* is still `visible_to(viewer)` like every
    other read; this decides only whether they are built.
    """
    return has_capability(user, Capability.VIEW_DEPARTMENT_MANAGEMENT)


def may_assign_work(user: object | None) -> bool:
    """May this person give a Teema a Vastutaja, or hand it to somebody else?

    Both lawyer roles by default — assignment is authorship, and authorship is
    department-wide (docs/adr/0037, 0042) — and the capability exists so that
    one person's right to reassign can be withdrawn without withdrawing their
    right to write.

    What it governs, exactly (`app.matters.services`): every change to an
    existing Teema's Vastutaja — taking an unowned file, handing one on,
    clearing it — and naming somebody *else* as the Vastutaja of a Teema being
    created or registered. Creating one's own Teema is capture, not assignment,
    and stays with the write right alone.
    """
    return has_capability(user, Capability.ASSIGN_WORK)


@dataclass(frozen=True)
class Scope:
    """What one user may currently see."""

    user: object | None
    is_authenticated: bool
    sees_restricted_by_role: bool
    #: The grant this scope *relies on*, which is not the same as every grant
    #: the user holds. A role that already sees RESTRICTED content relies on no
    #: grant, so this is ``None`` for both lawyer roles even where one exists —
    #: and that is the honest reading: the grant is a reason for seeing
    #: restricted work, and for those two it is not the reason.
    #:
    #: Nothing may read this to answer "does this person hold a grant". The one
    #: consumer is :attr:`sees_all_restricted`, which asks the role first.
    break_glass_grant_id: uuid.UUID | None

    @property
    def sees_all_restricted(self) -> bool:
        return self.sees_restricted_by_role or self.break_glass_grant_id is not None


class DepartmentViewer:
    """A reader who is past the door but has chosen no persona.

    The shared-gate mode lands somebody on the department dashboard before they
    say whose work they are looking at, and that page has to be useful. It must
    not become useful by borrowing an arbitrary person's identity: a dashboard
    rendered "as Marko" would show Marko's restricted files to whoever passed a
    shared password (Stage-2D auth brief 6).

    So this is not a `User`. It has no primary key, cannot be written to a
    foreign key, cannot own anything and never appears as an audit actor. The
    only thing it does is map to a scope: NORMAL visibility, and no
    participation of any kind.
    """

    is_authenticated = True
    is_active = True
    is_anonymous = False
    pk = None
    role = ""

    def __repr__(self) -> str:  # pragma: no cover - debugging aid
        return "<DepartmentViewer>"


#: One instance, compared by identity. A second one would still work, but
#: `scope_for_user` recognising the *class* rather than the object leaves room
#: for somebody to subclass their way into a scope, and there is no reason to.
DEPARTMENT_VIEWER = DepartmentViewer()


def is_department_head(user: object | None) -> bool:
    """Whether this reader is the department head, by role.

    A *read* helper over the same role vocabulary the scope uses, not a second
    authorization system: it answers "may this person open the management
    surface", while what that surface may then *show* is still decided by
    ``visible_to`` like everything else.

    Three things it is deliberately not. It is not a name — hard-coding a
    colleague would break the day the role changes hands. It is not
    ``is_superuser`` or ADMINISTRATOR — technical administration is not
    business access, and that is the rule this module exists to hold
    (specification 5.2). And it is not the shared-gate sentinel: knowing the
    department's password proves somebody is behind the door, never that they
    are the head (Stage-2F brief 28).

    Resolved through `acting_role` so that "who is acting" is decided in one
    place. Stage 2G arrived with its own copy of those three refusals for
    `may_review_work_victory`, which answers the same question about the same
    person; two copies that agree today are two copies that can stop agreeing
    the day one of them learns about a new kind of non-person.
    """
    return acting_role(user) == UserRole.DEPARTMENT_HEAD.value


def may_review_historical_matches(user: object | None) -> bool:
    """Whether this reader may work the historical reconciliation queue.

    Migration work rather than legal work, and it can create Matters, so it is
    the ADMINISTRATOR's (Stage-2D brief 39). Through `acting_role`, like every
    other role question: the gate on the queue and the link to it on the
    quality page used to compare the raw `User.role` each on its own, which
    agreed with this today and would not have followed it the day `acting_role`
    learns about another kind of non-person (ENG-128).
    """
    return acting_role(user) == UserRole.ADMINISTRATOR.value


def department_scope() -> Scope:
    """Everything NORMAL, and nothing that depends on knowing who you are.

    `user=None` is load-bearing: `restricted_participation_q` refuses to build
    an owner clause without a user, so this scope cannot reach a RESTRICTED
    Matter by any route.
    """
    return Scope(
        user=None,
        is_authenticated=True,
        sees_restricted_by_role=False,
        break_glass_grant_id=None,
    )


#: Grants already looked up during the request being served, by user id.
#:
#: ``None`` — the default, and what every management command, worker and test
#: sees — means *no request is being served*, so nothing is remembered and the
#: database is asked every time. A cache that is absent by default cannot go
#: stale in a process nobody wrapped.
#:
#: A :class:`~contextvars.ContextVar` rather than a module global for the reason
#: :mod:`app.search.indexing` gives at its own: a global is one value for the
#: whole process, so one request would answer an authorization question on
#: behalf of another. This is per-thread and per-async-task for free.
_looked_up_grants: contextvars.ContextVar[dict[Any, uuid.UUID | None] | None] = (
    contextvars.ContextVar("break_glass_grants_this_request", default=None)
)


@contextlib.contextmanager
def remember_grants_for_one_request() -> Iterator[None]:
    """Ask the database once per person per request, instead of once per read.

    ``scope_for_user`` runs on every ``visible_to``, which is 109 call sites and
    over a hundred calls on a statistics page, and each one asked the same
    question about the same person and got the same answer. For the two lawyer
    roles the question is not asked at all any more; for a READER or an
    administrator it is asked, and it was asked ninety times on
    ``/statistika/andmekvaliteet/`` alone (PERF-01).

    **Bounded to one request, by a token reset in ``finally``.** The lifecycle
    is the whole safety argument: a dict that outlived its request would be an
    authorization answer served to somebody who never asked it, which is worse
    than the queries it saves. :class:`app.core.middleware.RequestScopeMiddleware`
    is what opens and closes it.

    **A grant created while a page is rendering is not seen by that page**, and
    that is the intended reading rather than a tolerated one: a request should
    resolve one person's authorization once and answer consistently, instead of
    widening halfway down a page and printing a total that no single scope
    produced. Grants are created by a POST to
    :func:`app.accounts.services.grant_break_glass`, never on a read path, so
    the following request sees it.
    """
    token = _looked_up_grants.set({})
    try:
        yield
    finally:
        _looked_up_grants.reset(token)


def active_break_glass_grant_id(user: object) -> uuid.UUID | None:
    """The grant this user is currently relying on, if any.

    Remembered for the rest of the request when one is being served. The key is
    the user's primary key, and the two callers that have no primary key never
    reach here: ``scope_for_user`` answers the shared-gate sentinel and the
    anonymous visitor before this, from their own early returns.

    That ordering is what makes a ``pk`` key safe, and it is not incidental.
    ``DepartmentViewer.pk`` and ``AnonymousUser.pk`` are **both** ``None`` while
    mapping to opposite scopes — NORMAL visibility and nothing at all — so a
    cache keyed on ``pk`` that either of them could enter would eventually hand
    a shared-gate reader's scope to an anonymous one. They cannot enter it.
    ``tests/test_authorization.py`` asserts both halves: that they never reach
    this, and that a request following a shared-gate request still sees nothing.
    """
    identifier = getattr(user, "pk", None)
    remembered = _looked_up_grants.get()
    if remembered is not None and identifier is not None and identifier in remembered:
        return remembered[identifier]

    grant_model = apps.get_model("accounts", "BreakGlassGrant")
    grant = (
        grant_model.objects.active_at(timezone.now())
        .filter(user=user)
        .order_by("-created_at")
        .first()
    )
    found = grant.id if grant is not None else None

    if remembered is not None and identifier is not None:
        remembered[identifier] = found
    return found


def scope_for_user(user: object | None) -> Scope:
    if isinstance(user, DepartmentViewer):
        # Recognised before the generic path, because the generic path would go
        # looking for this sentinel's break-glass grants and hand a non-model to
        # the ORM.
        return department_scope()
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
    sees_by_role = role in ROLES_WITH_RESTRICTED_ACCESS
    return Scope(
        user=user,
        is_authenticated=True,
        sees_restricted_by_role=sees_by_role,
        # Not asked when the role already answers the only question the grant is
        # consulted for. `sees_all_restricted` short-circuits on the role, so for
        # a lawyer the lookup was a database round trip whose result could not
        # change anything — and `scope_for_user` runs once per `visible_to`,
        # which is over a hundred times on a statistics page (PERF-01).
        break_glass_grant_id=None if sees_by_role else active_break_glass_grant_id(user),
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
    """The Matter participation that unlocks RESTRICTED content.

    **One rule, now one SQL shape** (QRY-05). The collaborator half used to be
    ``Q(collaborators=user)``, a ``LEFT OUTER JOIN`` on the through table that
    fans one Matter out into a row per collaborator, which :func:`apply` then
    collapsed with ``SELECT DISTINCT`` on every reader's and administrator's
    query. It is :func:`restricted_participation_subquery_q` now — the same two
    facts, the same refusal for a scope that knows nobody, and an uncorrelated
    ``matter_id IN (…)`` that cannot multiply a row, so nothing needs
    de-duplicating. The rows selected are the same by construction
    (`tests/test_participation_one_shape.py`).
    """
    if not scope.is_authenticated or scope.user is None:
        # Without a user there is no participation to test, and building the
        # clause anyway would be worse than useless: `Q(owner=None)` compiles to
        # `owner IS NULL`, which matches every ownerless archive row — including
        # the RESTRICTED ones. A scope that knows nobody must see nothing extra,
        # not everything nobody owns.
        return NOTHING
    if owner_field == "owner" and collaborators_field == "collaborators":
        return restricted_participation_subquery_q(
            scope, prefix=prefix, matter_field=prefix.removesuffix("__") or "pk"
        )
    # A caller naming other fields gets the literal join form; none does today.
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


def restricted_participation_subquery_q(
    scope: Scope,
    *,
    prefix: str = "matter__",
    matter_field: str = "matter",
) -> Q:
    """:func:`restricted_participation_q`, without the join that fans rows out.

    The same two facts — this person owns the Matter, or is one of its
    collaborators — and the same refusal for a scope that knows nobody. The
    difference is *how* the collaborator half reaches the through table.

    ``Q(matter__collaborators=user)`` compiles to a ``LEFT OUTER JOIN`` on it,
    and one Matter with three collaborators is then three rows. :func:`apply`
    answers that with ``.distinct()``, which is correct and, over a search
    projection, ruinous: PostgreSQL de-duplicates the *whole selected row*, and
    the search page selected about 260 columns. The planner alone spent 4–5
    seconds on that ``DISTINCT`` for a READER or an administrator (ENG-010).

    Here the collaborator half is ``matter_id IN (SELECT matter_id FROM the
    through table WHERE user_id = …)``. It is uncorrelated, so PostgreSQL runs it
    once and hashes it; it cannot produce a second row for anything, so nothing
    needs de-duplicating; and it stays safe to evaluate inside a parallel scan,
    which a correlated ``EXISTS`` would not.
    """
    if not scope.is_authenticated or scope.user is None:
        return NOTHING
    through = apps.get_model("matters", "Matter").collaborators.through
    return Q(**{f"{prefix}owner": scope.user}) | Q(
        **{
            f"{matter_field}__in": through.objects.filter(**{"user": scope.user}).values(
                "matter_id"
            )
        }
    )


def projected_visibility_q(
    scope: Scope,
    *,
    kind_field: str,
    kind_overrides: dict[str, str | None],
    parent_prefix: str = "matter__",
    participation_by_subquery: bool = False,
) -> Q:
    """Visibility for a table whose rows describe *different* kinds of source.

    The search projection holds one row per Matter, per Entry, per Submission
    and per document fragment, and each kind's own restriction lives in a
    different table. Stage 2A deferred child content partly because that looked
    like it would force a union of differently-scoped querysets — and a count
    taken across a union is a count that can disagree with the rows beside it
    (docs/adr/0013).

    It does not. ``kind_overrides`` maps each kind to the field path that
    reaches its live override, and the whole thing collapses into one ``Q``
    over one queryset:

        (parent is normal AND this row's own child is normal) OR participation

    which is exactly ``child_visibility_q`` with the second half selected by
    kind. Every override is read from the child's current row through a join, so
    restricting a document takes effect on the next query with no reindex — the
    property this codebase has refused to trade away twice now
    (docs/adr/0005, 0013, 0014).

    A kind mapped to ``None`` has no child of its own; the parent's visibility
    is the whole answer. A kind absent from the mapping is not matched at all,
    because authorization whitelists and an unrecognised source kind is not a
    reason to show somebody a row.

    ``participation_by_subquery`` chooses how the participation half reaches
    the collaborators (:func:`restricted_participation_subquery_q`). The rule it
    expresses is the same either way; the subquery form never multiplies rows,
    so a caller that uses it filters without ``.distinct()``.
    """
    if not scope.is_authenticated:
        return NOTHING
    if scope.sees_all_restricted:
        return Q()

    clauses: list[Q] = []
    for kind, override_field in kind_overrides.items():
        clause = Q(**{kind_field: kind})
        if override_field is not None:
            clause &= Q(**{f"{override_field}__in": UNRESTRICTED_OVERRIDE_VALUES})
        clauses.append(clause)
    if not clauses:
        return NOTHING
    child_is_normal = functools.reduce(operator.or_, clauses)

    parent_is_normal = Q(**{f"{parent_prefix}visibility": Visibility.NORMAL})
    if participation_by_subquery:
        participation = restricted_participation_subquery_q(
            scope, prefix=parent_prefix, matter_field=parent_prefix.removesuffix("__")
        )
    else:
        participation = restricted_participation_q(scope, prefix=parent_prefix)
    return (parent_is_normal & child_is_normal) | participation


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
    """Apply a visibility condition, de-duplicating only where rows can repeat.

    The visibility conditions no longer join anything multi-valued (QRY-05:
    participation is an uncorrelated subquery), so a filtered queryset holds each
    row once and a ``SELECT DISTINCT`` over every selected column would only cost
    the planner and the executor. It is kept exactly when the filtered query does
    fan out — a one-to-many or many-to-many join, from the condition or already
    on the queryset — which is where it changes a result. A scope that sees all
    restricted work (``Q()``) never had it, which is how every caller already
    reads a scoped queryset that may or may not be distinct.
    """
    if not condition.children and not condition.negated:
        return queryset
    filtered = queryset.filter(condition)
    return filtered.distinct() if _fans_out(filtered) else filtered


def _fans_out(queryset: QuerySet) -> bool:
    """Whether the query joins a one-to-many or many-to-many relation."""
    return any(
        isinstance(alias, Join)
        and (
            getattr(alias.join_field, "one_to_many", False)
            or getattr(alias.join_field, "many_to_many", False)
        )
        for alias in queryset.query.alias_map.values()
    )


def scoped_count() -> Count:
    """``Count("id", distinct=True)`` — the only counter safe over a scoped set.

    The companion to :func:`apply`, and it lives here because the reason for it
    lives here. ``matter_visibility_q`` reached RESTRICTED work through the
    ``collaborators`` many-to-many as a ``LEFT OUTER JOIN`` on the through table:
    one Matter with three collaborators was three rows. ``apply`` answered that
    with ``.distinct()``, which fixed ``.count()`` and every row it handed back —
    and did **nothing** for an aggregate, because ``COUNT(id)`` inside a
    ``GROUP BY`` counts join rows before the outer ``DISTINCT`` is ever reached.
    Participation is a subquery now (QRY-05) and no longer fans out, but a
    caller's own join still can, so counting distinctly stays the safe rule.

    So a scoped queryset has two counting rules, not one, and only the second
    needs remembering. Every ``values(...).annotate(...)`` over anything that
    passed through ``apply`` must count distinctly or it publishes a number
    inflated by however many colleagues happen to share the file.

    The failure is invisible in exactly the wrong way. A DEPARTMENT_HEAD sees
    ``Q()`` — no join, no fan-out — so the person most likely to review the
    dashboard is the one person for whom it is right; a specialist opening the
    same page gets bars that overshoot the headline beside them and a
    drill-through that opens a shorter list than the bar it came from.
    """
    return Count("id", distinct=True)
