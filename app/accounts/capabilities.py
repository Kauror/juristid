"""The capability vocabulary: what each role holds by default, and what may change it.

This module is policy written down, and nothing else. It holds no query and no
request; *resolving* a capability for a person — role default, then that
person's own allow or deny — is `app.core.authorization.has_capability`,
because that module is the one chokepoint every authorization question already
passes through (docs/adr/0005, docs/adr/0145 §3).

Three properties it exists to hold:

**The defaults are today's rules, exactly.** Every business capability below
was a role check before it was a capability, and its default holders are the
roles that check admitted. A deployment with no individual overrides — which is
every deployment there is, today — behaves precisely as it did. Nothing about
collaborative work narrows: both lawyer roles keep assigning work, because
ADR 0037 and ADR 0042 made authorship department-wide and an assignment is
authorship.

**Administration is never a default.** No role carries `accounts.manage` or
`accounts.delegate`. Somebody becomes an account administrator because another
administrator — or the one-time operator bootstrap — said so, on their own
account, with the reason audited. Not because their role is ADMINISTRATOR
(technical administration is a different job), and not because they head the
department (managing the people is a different job from managing their work).

**Administration grants no business access.** An account administrator reads
and writes exactly what their business role reads and writes. The two families
are separate sets and no rule here ever reads one to answer the other.
"""

from __future__ import annotations

from dataclasses import dataclass

from app.accounts.enums import Capability, UserRole

#: The two values an individual override may hold. Anything else stored in the
#: column is ignored — authorization whitelists, so an unrecognised value is
#: not a reason to grant anything (docs/adr/0005).
ALLOW = "allow"
DENY = "deny"
OVERRIDE_VALUES: frozenset[str] = frozenset({ALLOW, DENY})

BUSINESS_CAPABILITIES: tuple[str, ...] = (
    Capability.VIEW_DEPARTMENT_MANAGEMENT.value,
    Capability.ASSIGN_WORK.value,
    Capability.REVIEW_WORK_VICTORIES.value,
)

ADMINISTRATIVE_CAPABILITIES: tuple[str, ...] = (
    Capability.MANAGE_ACCOUNTS.value,
    Capability.DELEGATE_ADMINISTRATION.value,
)

ALL_CAPABILITIES: tuple[str, ...] = BUSINESS_CAPABILITIES + ADMINISTRATIVE_CAPABILITIES

#: Which roles hold each capability with no override at all.
#:
#: Read against the rules they replace, one by one:
#:
#: * `department.view_management` — the Osakond page's *Meeskond* and *Tehtud*
#:   sections and a colleague's Minu asjad page were `is_department_head`
#:   (docs/adr/0049, app/matters/person_work.py).
#: * `work.assign` — choosing or changing a Teema's Vastutaja was any business
#:   writer, `ROLES_WITH_BUSINESS_WRITE` (docs/adr/0037, 0042). Not narrowed.
#: * `work_victory.review` — `ROLES_WITH_WORK_VICTORY_REVIEW`, the department
#:   head (Stage-2G brief 25).
#: * the two administrative capabilities — nobody, by default, ever.
DEFAULT_HOLDERS: dict[str, frozenset[str]] = {
    Capability.VIEW_DEPARTMENT_MANAGEMENT.value: frozenset({UserRole.DEPARTMENT_HEAD.value}),
    Capability.ASSIGN_WORK.value: frozenset(
        {UserRole.SPECIALIST.value, UserRole.DEPARTMENT_HEAD.value}
    ),
    Capability.REVIEW_WORK_VICTORIES.value: frozenset({UserRole.DEPARTMENT_HEAD.value}),
    Capability.MANAGE_ACCOUNTS.value: frozenset(),
    Capability.DELEGATE_ADMINISTRATION.value: frozenset(),
}

#: Capabilities that only mean something for somebody who may author business
#: content. Assigning a Teema and confirming a Töövõit are both writes, and the
#: business-write boundary is not something an override can step around: a
#: READER or a technical administrator granted one of these still holds nothing
#: (docs/adr/0037).
REQUIRES_BUSINESS_WRITE: frozenset[str] = frozenset(
    {Capability.ASSIGN_WORK.value, Capability.REVIEW_WORK_VICTORIES.value}
)

#: Capabilities that require another one to mean anything. Granting somebody
#: the right to hand out account administration without the right to manage
#: accounts would be a power with nothing to exercise it on.
PREREQUISITES: dict[str, str] = {
    Capability.DELEGATE_ADMINISTRATION.value: Capability.MANAGE_ACCOUNTS.value,
}

#: Who may *receive* each business capability as an individual grant.
#:
#: The management view may be shown to a reader — it is a read surface, filtered
#: by the reader's own visibility like every other — but never to the technical
#: administrator: technical administration is not business access, and a team
#: table of the department's open work is business content (docs/adr/0005).
#: The two write capabilities go only to the two writing roles, for the reason
#: `REQUIRES_BUSINESS_WRITE` gives.
GRANTABLE_TO_ROLES: dict[str, frozenset[str]] = {
    Capability.VIEW_DEPARTMENT_MANAGEMENT.value: frozenset(
        {UserRole.SPECIALIST.value, UserRole.DEPARTMENT_HEAD.value, UserRole.READER.value}
    ),
    Capability.ASSIGN_WORK.value: frozenset(
        {UserRole.SPECIALIST.value, UserRole.DEPARTMENT_HEAD.value}
    ),
    Capability.REVIEW_WORK_VICTORIES.value: frozenset(
        {UserRole.SPECIALIST.value, UserRole.DEPARTMENT_HEAD.value}
    ),
    # Any role may administer accounts — the point of the separation is that
    # doing so changes nothing about what that role may read.
    Capability.MANAGE_ACCOUNTS.value: frozenset(UserRole.values),
    Capability.DELEGATE_ADMINISTRATION.value: frozenset(UserRole.values),
}


@dataclass(frozen=True)
class CapabilityText:
    """What the administration page says about one capability."""

    label: str
    description: str


#: Estonian labels and one-line descriptions. The identifiers above never reach
#: an ordinary administrator's screen (brief §12).
TEXT: dict[str, CapabilityText] = {
    Capability.VIEW_DEPARTMENT_MANAGEMENT.value: CapabilityText(
        "Osakonna juhtimisvaade",
        "Osakonna lehe Meeskond ja Tehtud ning kolleegide «Minu asjad» lehed.",
    ),
    Capability.ASSIGN_WORK.value: CapabilityText(
        "Töö määramine",
        "Teema vastutaja määramine ja muutmine.",
    ),
    Capability.REVIEW_WORK_VICTORIES.value: CapabilityText(
        "Töövõitude kinnitamine",
        "Töövõidu kinnitamine või mitterealiseerunuks märkimine.",
    ),
    Capability.MANAGE_ACCOUNTS.value: CapabilityText(
        "Kasutajate haldus",
        "Kontode loomine, kutsumine, rollid, õigused ja välja lülitamine.",
    ),
    Capability.DELEGATE_ADMINISTRATION.value: CapabilityText(
        "Haldusõiguste andmine",
        "Teistele haldusõiguste andmine ja äravõtmine; teiste haldurite haldamine.",
    ),
}


def is_known(capability: str) -> bool:
    return capability in DEFAULT_HOLDERS


def role_default(role: str, capability: str) -> bool:
    """Whether ``role`` holds ``capability`` when nobody has said otherwise."""
    return role in DEFAULT_HOLDERS.get(capability, frozenset())


def may_be_granted_to(role: str, capability: str) -> bool:
    """Whether an individual *allow* of ``capability`` is meaningful for ``role``.

    A deny is always meaningful where the role holds the capability by default
    and harmless where it does not, so only grants are policed here.
    """
    return role in GRANTABLE_TO_ROLES.get(capability, frozenset())


def is_administrative(capability: str) -> bool:
    return capability in ADMINISTRATIVE_CAPABILITIES


def clean_overrides(raw: object) -> dict[str, str]:
    """The overrides a stored value actually expresses, and nothing it does not.

    Unknown capability names and values other than allow/deny are dropped
    rather than interpreted: the column is JSON, a JSON column holds whatever
    somebody once wrote to it, and the safe reading of a value nobody
    recognises is that it says nothing.
    """
    if not isinstance(raw, dict):
        return {}
    return {
        str(name): str(value)
        for name, value in raw.items()
        if is_known(str(name)) and str(value) in OVERRIDE_VALUES
    }
