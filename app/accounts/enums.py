from __future__ import annotations

from django.db import models


class UserRole(models.TextChoices):
    """Application roles (master specification 5.1).

    ADMINISTRATOR is a technical role. It does not carry business access to
    RESTRICTED content; see app/core/authorization.py.
    """

    SPECIALIST = "SPECIALIST", "Spetsialist / jurist"
    DEPARTMENT_HEAD = "DEPARTMENT_HEAD", "Osakonnajuht"
    ADMINISTRATOR = "ADMINISTRATOR", "Süsteemiadministraator"
    READER = "READER", "Lugeja"


class Capability(models.TextChoices):
    """The individually grantable permissions (docs/adr/0145 §3).

    Deliberately small. Each one names an operation this product actually has
    and that the repository audit found gated by a role: not Django's model
    permissions, which describe tables rather than work, and not a generic
    editor over them, which would let an administrator invent a permission
    nobody reviewed.

    Two families, and the line between them is the rule ADR 0005 drew for
    content: **business capabilities** decide what somebody may do with the
    department's work; **administrative capabilities** decide who may manage
    the accounts. Holding the second grants nothing of the first — an account
    administrator reads exactly what their business role reads.

    The resolution — role default, then the person's own allow/deny — lives in
    `app.accounts.capabilities`, and every check in the application goes
    through it.
    """

    # -- business ----------------------------------------------------------
    VIEW_DEPARTMENT_MANAGEMENT = (
        "department.view_management",
        "Osakonna juhtimisvaade",
    )
    ASSIGN_WORK = "work.assign", "Töö määramine ja ümbermääramine"
    REVIEW_WORK_VICTORIES = "work_victory.review", "Töövõitude kinnitamine"
    # -- administrative ----------------------------------------------------
    MANAGE_ACCOUNTS = "accounts.manage", "Kasutajakontode haldus"
    DELEGATE_ADMINISTRATION = "accounts.delegate", "Haldusõiguste andmine"


class AuthMode(models.TextChoices):
    """How this deployment decides who is at the keyboard.

    The three are genuinely different claims, and the difference is the point:

    * ``NONE`` — nothing authenticates. Only a developer laptop and CI, where
      the synthetic sign-in provides an identity and the data is invented.
    * ``SHARED_GATE`` — one password, shared by the department, then a persona
      picked from a list. This **authenticates the door, not the person**: it
      proves somebody knows the shared password, and nothing more. It is a
      temporary development-phase mode and its limitation is recorded on every
      audit row it produces (docs/adr/0016).
    * ``CLOUDFLARE_ACCESS`` — Cloudflare authenticates the individual against
      the Chamber's identity provider and signs an assertion this application
      verifies. An individual identity, proved by somebody else.
    * ``LOCAL_PASSWORD`` — the individual signs in here, with their own e-mail
      address, their own password and — where required — a second factor. An
      individual identity, proved by this application (docs/adr/0145). Built
      and tested, and **not the mode any deployment runs today**: switching to
      it is a separate, approved activation (docs/LOCAL_AUTH_ACTIVATION_RUNBOOK.md).

    Business authorization does not change between them. All four end in the
    same `scope_for_user` chokepoint; what changes is how much the deployment
    is entitled to say about who that user is.

    Exactly one is in force. A deployment does not run the shared gate *and*
    local passwords, because two identity-establishing paths are two front
    doors, and the audit trail could no longer say which one somebody came
    through (docs/adr/0016, docs/adr/0145).
    """

    NONE = "none", "Autentimiseta (arendus)"
    SHARED_GATE = "shared_gate", "Jagatud parool"
    CLOUDFLARE_ACCESS = "cloudflare_access", "Cloudflare Access"
    LOCAL_PASSWORD = "local_password", "Isiklik parool"


#: What an audit row records about how the actor arrived. Never "the person is
#: who they say they are" unless the mode can actually prove it.
AUTHENTICATED_VIA = {
    AuthMode.NONE: "NONE",
    AuthMode.SHARED_GATE: "SHARED_GATE",
    AuthMode.CLOUDFLARE_ACCESS: "CLOUDFLARE_ACCESS",
    AuthMode.LOCAL_PASSWORD: "LOCAL_PASSWORD",
}


class ProvisioningState(models.TextChoices):
    """Where an account is on its way to being usable (docs/adr/0145 §5).

    Separate from ``is_active`` on purpose, and the separation is the rule the
    brief asked for: **saving an account never makes it usable.** An account an
    administrator has just created is PENDING; approving it and sending the
    invitation makes it INVITED; only the person themselves, setting their own
    password through the one-time link, makes it ACTIVATED — and that is the
    only step that turns ``is_active`` on.

    A database constraint holds the half that matters most: a PENDING or
    INVITED account is never ``is_active``. Every authentication mode reads
    ``is_active`` — Cloudflare Access signs in *any* active account whose
    address matches — so an account that skipped the lifecycle would be a
    seat somebody was never given.

    ACTIVATED is also what every account that existed before this lifecycle
    is: they were provisioned by an operator and are in use, and the default
    on the new column says so without a data migration. Whether an ACTIVATED
    account is currently switched on is ``is_active``, as it always was —
    "Välja lülitatud" is ACTIVATED with ``is_active`` off, not a fourth state.
    """

    PENDING = "PENDING", "Ootab kinnitust"
    INVITED = "INVITED", "Kutsutud"
    ACTIVATED = "ACTIVATED", "Aktiveeritud"


class AccountStatus(models.TextChoices):
    """What the administration list says about an account, derived and never stored."""

    PENDING = "PENDING", "Ootab kinnitust"
    INVITED = "INVITED", "Kutsutud"
    ACTIVE = "ACTIVE", "Aktiivne"
    DISABLED = "DISABLED", "Välja lülitatud"


class CredentialTokenPurpose(models.TextChoices):
    """What a one-time link lets its holder do — and nothing else."""

    ACTIVATION = "ACTIVATION", "Konto aktiveerimine"
    PASSWORD_RESET = "PASSWORD_RESET", "Parooli taastamine"


class ThrottleScope(models.TextChoices):
    """Which counter one failed attempt is charged to (docs/adr/0145 §9).

    Several narrow counters rather than one wide one. A single per-account
    lockout is a denial-of-service primitive — anybody who knows a colleague's
    address can keep them out — so the tightest limit sits on the *pair* of
    account and network, a looser one on the account alone (which only a
    distributed attack reaches), and another on the network alone. Nothing is
    global.
    """

    SIGN_IN_PAIR = "SIGN_IN_PAIR", "Sisselogimine: konto ja võrk"
    SIGN_IN_ACCOUNT = "SIGN_IN_ACCOUNT", "Sisselogimine: konto"
    SIGN_IN_NETWORK = "SIGN_IN_NETWORK", "Sisselogimine: võrk"
    SECOND_FACTOR = "SECOND_FACTOR", "Teine tegur"
    REAUTHENTICATION = "REAUTHENTICATION", "Isiku kinnitamine"
    CREDENTIAL_LINK = "CREDENTIAL_LINK", "Ühekordne link"
    RESET_REQUEST_ACCOUNT = "RESET_REQUEST_ACCOUNT", "Taastamise taotlus: konto"
    RESET_REQUEST_NETWORK = "RESET_REQUEST_NETWORK", "Taastamise taotlus: võrk"
    PASSWORD_CHANGE = "PASSWORD_CHANGE", "Parooli muutmine"
