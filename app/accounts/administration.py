"""Account administration: the named use cases behind Haldus → Kasutajad.

Every change an account administrator can make is one function here, and every
function holds the same rules whichever view — or management command, or test,
or shell session — calls it (docs/adr/0145):

* **A verified actor only.** Under the shared gate the actor must be a
  `admin_session.VerifiedActor` — minted only from a request whose verified
  administrator session (the person's own password and TOTP, docs/adr/0186)
  was just re-checked — and its `security_epoch` must still be the one it was
  verified at when the account is re-read under the lock. A bare account, a
  persona, refuses before anything is read. Under `local_password` the
  personal sign-in is the proof: the views pass a `VerifiedActor` there too,
  and a bare account is still accepted, as it always was, from code that runs
  inside that mode (tests, a shell). `none` and Cloudflare Access administer
  nothing. The pages are unreachable without the same proof; this is the
  second lock on the same door.
* **The actor's own authority, resolved centrally.** `accounts.manage` for any
  change; `accounts.delegate` for anything that grants, withdraws or touches
  administrative power — including changing an account that already holds it.
  Resolved by `app.core.authorization.has_capability` from the actor's own row,
  never from anything the request claims.
* **Nobody changes their own power.** Not their role, not their capabilities,
  not their active state. A crafted POST naming one's own identifier is the
  self-elevation the brief forbids, and it is refused here rather than hidden
  in a template.
* **The last delegating administrator stays.** Revoking, demoting or switching
  off the final active account that can still hand out administration is
  refused, under one advisory lock so two administrators removing each other at
  the same moment cannot both succeed.
* **Two usable full administrators stay** (docs/adr/0186). An ordinary change
  that would leave fewer than `MINIMUM_FULL_ADMINISTRATORS` usable ones —
  active, activated, a personal password, a confirmed and readable TOTP, both
  administrative capabilities — and fewer than there were is refused, under the
  same lock and inside the change's transaction. While fewer than two exist (the
  bootstrap phase) only changes that do not reduce the count further pass. The
  operator's `emergency_disable_account` is the one route past it.
* **Every change is audited** with the actor, the subject, the before and the
  after, and **every change to power ends the subject's open sessions** through
  the security epoch.

What none of this touches: who owns a Teema, who wrote an entry, what an audit
row says, an Entra object identifier, or any account's historical identity. An
account is never deleted and never merged.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, cast

from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import connection, transaction
from django.db.models import F
from django.utils import timezone

from app.accounts import admin_session, capabilities, email_policy, mail, mfa, tokens
from app.accounts.admin_session import VerifiedActor
from app.accounts.enums import (
    AuthMode,
    Capability,
    CredentialTokenPurpose,
    ProvisioningState,
    UserRole,
)
from app.accounts.models import User
from app.audit.enums import SecurityEventType
from app.audit.services import record_security_event
from app.core.authorization import account_capabilities, resolve_capability
from app.core.errors import DomainError

# -- refusals, named so tests can name them (tests/refusals.py) ------------------

NOT_VERIFIED = (
    "Kasutajate halduseks on vaja halduri isiklikku kinnitust: oma e-posti aadress, parool ja "
    "autentimisrakenduse kood."
)
#: The name this refusal had while only personal sign-in could administer.
NOT_LOCAL_MODE = NOT_VERIFIED
STALE_PROOF = (
    "Sinu halduri kinnitus ei kehti enam, sest konto õigusi või turvaseadeid muudeti. Kinnita "
    "uuesti."
)
CONTINUITY = (
    "Seda muudatust ei saa teha: pärast seda jääks alles vähem kui kaks kasutatavat täishaldurit "
    "(aktiivne konto, isiklik parool, autentimisrakendus ning mõlemad haldusõigused). Määra "
    "kõigepealt uus täishaldur ja lase tal kinnitus seadistada."
)
EMERGENCY_REASON_REQUIRED = "Põhjus on kohustuslik. See jääb turvalogisse. Midagi ei muudetud."
EMERGENCY_UNKNOWN_ACCOUNT = "Sellise aadressiga kontot ei ole. Midagi ei muudetud."
EMERGENCY_ALREADY_OFF = "Konto on juba välja lülitatud. Midagi ei muudetud."
LINK_NOT_AN_ADMINISTRATOR = (
    "Sellele kontole ei saa halduri linki väljastada: tal ei ole kasutajate halduse õigust ega "
    "tehnilise halduri rolli, või konto on välja lülitatud. Midagi ei muudetud."
)
LINK_NOT_APPROVED = (
    "Konto ootab veel halduri kinnitust (Haldus → Kasutajad → «Kinnita ja saada kutse»). "
    "Midagi ei muudetud."
)
LINK_WRONG_MODE = (
    "Halduri linke saab väljastada ainult ühisparooli või isikliku sisselogimise režiimis. "
    "Midagi ei muudetud."
)
NOT_AN_ADMINISTRATOR = "Selleks on vaja kasutajate halduse õigust."
NOT_A_DELEGATE = (
    "Haldusõigusi ja teiste haldurite kontosid saab muuta ainult haldusõiguste andmise "
    "õigusega haldur."
)
NO_SELF_CHANGE = "Oma rolli, õigusi ega konto seisu ei saa ise muuta. Palu seda teiselt haldurilt."
FINAL_ADMINISTRATOR = (
    "Viimast aktiivset haldusõiguste andjat ei saa eemaldada ega välja lülitada. Anna "
    "kõigepealt õigus kellelegi teisele."
)
DUPLICATE_ACCOUNT = "Selle aadressiga konto on juba olemas."
DISPLAY_NAME_REQUIRED = "Nimi on kohustuslik."
UNKNOWN_ROLE = "Tundmatu roll."
UNKNOWN_CAPABILITY = "Tundmatu õigus."
NOT_GRANTABLE = "Seda õigust ei saa selle rolliga kontole anda."
EXCEPTION_REASON_REQUIRED = "Välise aadressi erandi jaoks on põhjus kohustuslik."
NOT_PENDING = "Konto ei oota kinnitust ega kutset."
NOT_ACTIVATED = "Konto ei ole veel aktiveeritud."
ALREADY_INACTIVE = "Konto on juba välja lülitatud."
ALREADY_ACTIVE = "Konto on juba aktiivne."
BOOTSTRAP_ALREADY_DONE = (
    "Haldur on juba olemas. Esimese halduri määramine on ühekordne; edasi annavad "
    "haldusõigusi haldurid ise."
)
BOOTSTRAP_ACCOUNT_UNUSABLE = (
    "Sellist kontot ei saa esimeseks halduriks määrata: see on välja lülitatud või sünteetiline."
)
BOOTSTRAP_NEEDS_A_CHANNEL = (
    "E-kirjade saatmine on välja lülitatud ja aktiveerimislinki ei palutud terminali printida "
    "(--print-activation-link). Midagi ei muudetud."
)
BOOTSTRAP_NOT_DELIVERED = "Aktiveerimislinki ei õnnestunud saata, seega midagi ei muudetud."
ADDRESS_FIXED_AFTER_ACTIVATION = (
    "Konto on juba isikliku parooliga kasutusel, seega selle aadressi siit muuta ei saa. "
    "Uus aadress tähendab uut kontot; vana lülita välja."
)


class AdministrationRefused(DomainError):
    """The change was not made, and the message says why."""


class PrivilegeRefused(AdministrationRefused):
    """A refusal about *authority* — the kind somebody probing the boundary causes.

    Carries what was attempted so the caller can record it. It is recorded by
    the caller (`record_refusal`) rather than here, because here is inside the
    transaction the refusal is about to roll back, and an audit row written
    there would roll back with it — leaving no trace of exactly the attempt
    worth having seen.
    """

    def __init__(self, message: str, *, action: str, subject: Any = None) -> None:
        super().__init__(message)
        self.action = action
        self.subject = subject if isinstance(subject, User) else None


def record_refusal(*, actor: Any, error: PrivilegeRefused, ip_address: str | None = None) -> None:
    """Write the ACCESS_REFUSED row for a refused privilege change, after its rollback."""
    if admin_session.is_verified_actor(actor):
        actor = actor.user
    record_security_event(
        event_type=SecurityEventType.ACCESS_REFUSED,
        actor=actor if isinstance(actor, User) else None,
        subject=error.subject,
        succeeded=False,
        ip_address=ip_address,
        detail={"action": error.action, "refusal": str(error)},
    )


#: Advisory-lock namespace for privilege changes. Arbitrary and fixed, and not
#: anybody else's (`app.search.indexing`, `app.accounts.views`).
_ADMINISTRATION_LOCK = (31338, 1)


# -- the preconditions every change shares ---------------------------------------


#: Anything a service accepts as the acting administrator.
Actor = User | VerifiedActor


def _mode() -> str:
    from app.accounts.shared_gate import current_mode

    return current_mode()


def _require_verified(actor: Any) -> User:
    """The account behind ``actor``, if this mode accepts it as proof; refused otherwise.

    * shared gate — a `VerifiedActor` minted by the administrator session;
      nothing else, so a persona (a bare `User`) never administers;
    * `local_password` — a `VerifiedActor` from the personal sign-in, or a bare
      account from code running inside that mode, as before;
    * any other mode — nothing.
    """
    mode = _mode()
    if admin_session.is_verified_actor(actor):
        expected = admin_session.PATH if mode == AuthMode.SHARED_GATE else "local_password"
        if mode in (AuthMode.SHARED_GATE, AuthMode.LOCAL_PASSWORD) and actor.via == expected:
            return cast(User, actor.user)
        raise AdministrationRefused(NOT_VERIFIED)
    if mode == AuthMode.LOCAL_PASSWORD and isinstance(actor, User):
        return actor
    raise AdministrationRefused(NOT_VERIFIED)


BOOTSTRAP_WRONG_MODE = (
    "Esimest haldurit saab määrata ainult ühisparooli või isikliku sisselogimise režiimis. "
    "Midagi ei muudetud."
)


def _require_mode_for_operator(message: str) -> None:
    """The operator's appointment and link commands: wherever the link pages exist."""
    if _mode() not in (AuthMode.SHARED_GATE, AuthMode.LOCAL_PASSWORD):
        raise AdministrationRefused(message)


def _has(user: Any, capability: str) -> bool:
    from app.core.authorization import has_capability

    return has_capability(user, capability)


def _require_actor(actor: Any, capability: str, *, subject: Any = None, action: str) -> None:
    if not isinstance(actor, User) or not _has(actor, capability):
        message = (
            NOT_A_DELEGATE
            if capability == Capability.DELEGATE_ADMINISTRATION
            else NOT_AN_ADMINISTRATOR
        )
        raise PrivilegeRefused(message, action=action, subject=subject)


def _begin(actor: Any, capability: str, *, action: str, subject: Any = None) -> User:
    """Every administration change starts here, in this order.

    The proof first (`_require_verified`); then the advisory lock that
    serialises privilege changes; then the actor **re-read under that lock** and
    their authority asked of the fresh row. Checking the instance the request
    loaded would let an administrator whose rights were withdrawn a moment ago
    by a concurrent request still complete one change on the strength of a
    stale copy. For a `VerifiedActor` the fresh row must also still carry the
    `security_epoch` it was verified at: any power or credential change since
    the proof — committed by another request while this one waited for the
    lock — refuses the change.
    """
    account = _require_verified(actor)
    _lock_administration()
    fresh = User.objects.filter(pk=account.pk).first()
    if admin_session.is_verified_actor(actor) and (
        fresh is None
        or int(fresh.security_epoch or 0) != actor.epoch
        or not fresh.is_active
        or fresh.provisioning_state != ProvisioningState.ACTIVATED
    ):
        raise PrivilegeRefused(STALE_PROOF, action=action, subject=subject)
    _require_actor(fresh, capability, subject=subject, action=action)
    return cast(User, fresh)


def _lock_administration() -> None:
    """Serialise every privilege change for the rest of this transaction."""
    with connection.cursor() as cursor:
        cursor.execute("SELECT pg_advisory_xact_lock(%s, %s)", list(_ADMINISTRATION_LOCK))


def _delegating_administrators() -> list[User]:
    """Every active account that can still hand out administration.

    Administrative capabilities have no role default, so an account holding one
    has an *allow* override for it — which the JSON containment query finds —
    and the central resolution then decides whether it is actually held.
    """
    candidates = User.objects.filter(
        is_active=True,
        capability_overrides__contains={
            Capability.DELEGATE_ADMINISTRATION.value: capabilities.ALLOW
        },
    )
    return [user for user in candidates if _has(user, Capability.DELEGATE_ADMINISTRATION)]


def administrators() -> list[User]:
    """Every active account that may manage accounts, for the list page."""
    candidates = User.objects.filter(
        is_active=True,
        capability_overrides__contains={Capability.MANAGE_ACCOUNTS.value: capabilities.ALLOW},
    )
    return [user for user in candidates if _has(user, Capability.MANAGE_ACCOUNTS)]


# -- continuity: at least two usable full administrators (docs/adr/0186) -----------------

#: How many usable full administrators ordinary changes must leave in place.
MINIMUM_FULL_ADMINISTRATORS = 2


def is_usable_full_administrator(user: Any) -> bool:
    """Somebody who could, today, verify themselves and do any administration.

    Every condition is about whether the person can actually *use* the power,
    not about stored flags: active and activated; a personal password set here;
    a confirmed authenticator whose secret the current key still opens (a
    device that can never produce an accepted code is no backup); both
    administrative capabilities, resolved centrally by `has_capability`; and
    not a synthetic account where real data is.
    """
    if not isinstance(user, User):
        return False
    if not user.is_active or user.provisioning_state != ProvisioningState.ACTIVATED:
        return False
    if not user.has_local_password:
        return False
    if user.is_synthetic and settings.REAL_DATA_ALLOWED:
        return False
    if not (
        _has(user, Capability.MANAGE_ACCOUNTS) and _has(user, Capability.DELEGATE_ADMINISTRATION)
    ):
        return False
    device = mfa.confirmed_device(user)
    return device is not None and mfa.secret_is_readable(device)


def usable_full_administrators() -> list[User]:
    """Every usable full administrator, by name. A handful of rows at most.

    Administrative capabilities have no role default, so a full administrator
    has an *allow* override for both — the JSON containment query narrows to
    them, and `is_usable_full_administrator` decides each one.
    """
    candidates = User.objects.filter(
        is_active=True,
        provisioning_state=ProvisioningState.ACTIVATED,
        local_password_set_at__isnull=False,
        capability_overrides__contains={
            Capability.MANAGE_ACCOUNTS.value: capabilities.ALLOW,
            Capability.DELEGATE_ADMINISTRATION.value: capabilities.ALLOW,
        },
    ).order_by("display_name", "upn")
    return [user for user in candidates if is_usable_full_administrator(user)]


@dataclass(frozen=True)
class ContinuityStatus:
    """Whether the department has the two independent administrators it needs."""

    usable: tuple[User, ...]
    required: int = MINIMUM_FULL_ADMINISTRATORS

    @property
    def count(self) -> int:
        return len(self.usable)

    @property
    def satisfied(self) -> bool:
        return self.count >= self.required

    @property
    def bootstrap_phase(self) -> bool:
        """Fewer than required: only changes that do not reduce the count further pass."""
        return not self.satisfied

    @property
    def summary(self) -> str:
        if self.satisfied:
            return f"Kasutatavaid täishaldureid: {self.count} (nõutud vähemalt {self.required})."
        if self.count == 0:
            return (
                "Ühtegi kasutatavat täishaldurit ei ole. Esimese määrab operaator käsuga "
                "bootstrap_account_admin."
            )
        return (
            f"Kasutatavaid täishaldureid: {self.count}, nõutud vähemalt {self.required}. "
            "Seadistusjärk: määra teine täishaldur ja lase tal kinnitus seadistada. Seni ei saa "
            "ühtegi täishaldurit eemaldada."
        )


def continuity_status() -> ContinuityStatus:
    return ContinuityStatus(usable=tuple(usable_full_administrators()))


def _guard_continuity(before: int, *, subject: Any, action: str) -> None:
    """Refuse a change that leaves fewer usable full administrators than both before and two.

    Called after the change is written and before its transaction commits, under
    the administration lock: the count is the database's after the change, and a
    refusal rolls the change back. Two administrators removing each other at the
    same moment take turns at the lock, and the second sees the first's result.
    """
    after = len(usable_full_administrators())
    if after < before and after < MINIMUM_FULL_ADMINISTRATORS:
        raise PrivilegeRefused(CONTINUITY, action=action, subject=subject)


def _bump_epoch(user: User) -> None:
    """End every open session of this account, in every worker, at its next request."""
    User.objects.filter(pk=user.pk).update(security_epoch=F("security_epoch") + 1)
    user.refresh_from_db(fields=["security_epoch"])


def _effective(user: User) -> list[str]:
    return sorted(account_capabilities(user))


def _touches_administration(user: User) -> bool:
    return _has(user, Capability.MANAGE_ACCOUNTS) or bool(
        set(capabilities.clean_overrides(user.capability_overrides))
        & set(capabilities.ADMINISTRATIVE_CAPABILITIES)
    )


def _guard_final_administrator(before: int, *, actor: Any, subject: Any, action: str) -> None:
    if before >= 1 and not _delegating_administrators():
        raise PrivilegeRefused(FINAL_ADMINISTRATOR, action=action, subject=subject)


# -- e-mail ---------------------------------------------------------------------------


def _clean_address(
    raw: str, *, actor: User, external_reason: str, subject: Any, action: str, exclude: Any = None
) -> tuple[str, bool]:
    """The normal form of a new sign-in address, and whether it is an exception."""
    try:
        address = email_policy.clean(raw)
    except ValidationError as error:
        raise AdministrationRefused(error.messages[0]) from error
    duplicates = User.objects.filter(upn__iexact=address) | User.objects.filter(
        email__iexact=address
    )
    if exclude is not None:
        duplicates = duplicates.exclude(pk=exclude.pk)
    if duplicates.exists():
        raise AdministrationRefused(DUPLICATE_ACCOUNT)
    if email_policy.is_allowed_domain(address):
        return address, False
    if not external_reason.strip():
        raise AdministrationRefused(email_policy.domain_refusal() + " " + EXCEPTION_REASON_REQUIRED)
    _require_actor(actor, Capability.DELEGATE_ADMINISTRATION, subject=subject, action=action)
    return address, True


# -- creating, inviting, activating ------------------------------------------------------


ASSIGNABLE_ROLES = (
    UserRole.SPECIALIST.value,
    UserRole.DEPARTMENT_HEAD.value,
    UserRole.READER.value,
)


def _clean_role(role: str, *, actor: User, subject: Any, action: str) -> str:
    if role not in UserRole.values:
        raise AdministrationRefused(UNKNOWN_ROLE)
    if role not in ASSIGNABLE_ROLES:
        # The technical administrator role reads the archive and works the
        # migration queues; handing it out is handing out technical power, so
        # it is a delegation decision.
        _require_actor(actor, Capability.DELEGATE_ADMINISTRATION, subject=subject, action=action)
    return role


@transaction.atomic
def create_account(
    *, actor: Actor, email: str, display_name: str, role: str, external_reason: str = ""
) -> User:
    """A new account, PENDING: inactive, password-less, and nobody's seat yet.

    Creation is never activation. The account becomes usable only when it has
    been approved and invited, and its owner has set their own password through
    the one-time link.
    """
    actor = _begin(actor, Capability.MANAGE_ACCOUNTS, action="create_account")
    name = " ".join((display_name or "").split())
    if not name:
        raise AdministrationRefused(DISPLAY_NAME_REQUIRED)
    role = _clean_role(role, actor=actor, subject=None, action="create_account")
    address, exception = _clean_address(
        email, actor=actor, external_reason=external_reason, subject=None, action="create_account"
    )
    now = timezone.now()
    user = User.objects.create_user(
        upn=address,
        email=address,
        display_name=name,
        role=role,
        is_active=False,
        provisioning_state=ProvisioningState.PENDING,
        # Outside a real-data deployment every account is a rehearsal account,
        # structurally: it can never carry an Entra identity, and a real-data
        # local sign-in refuses it (docs/adr/0004, 0145 §6).
        is_synthetic=not settings.REAL_DATA_ALLOWED,
        created_by=actor,
        email_exception_reason=external_reason.strip() if exception else "",
        email_exception_approved_by=actor if exception else None,
        email_exception_approved_at=now if exception else None,
    )
    record_security_event(
        event_type=SecurityEventType.ACCOUNT_CREATED,
        actor=actor,
        subject=user,
        detail={"role": role, "email": address, "external_address_exception": exception},
    )
    if exception:
        record_security_event(
            event_type=SecurityEventType.EMAIL_EXCEPTION_APPROVED,
            actor=actor,
            subject=user,
            detail={"email": address, "reason": external_reason.strip()},
        )
    return user


@dataclass(frozen=True)
class LinkOutcome:
    """What happened to a one-time link an administrator asked for."""

    delivery: mail.Delivery
    purpose: str


def _deliver(*, user: User, purpose: str, actor: User | None) -> LinkOutcome:
    """Issue a link and send it inside the caller's transaction.

    Only `bootstrap_first_administrator` sends this way, deliberately: a
    message that cannot be sent must undo the whole appointment, which is the
    transaction's rollback. It is an operator's one-time command on an account
    nobody can sign in to yet, so the locks it holds across the send keep
    nobody out. Every other link is sent after its transaction commits
    (`send_setup_link`).
    """
    issued = _issue_for_mail(user=user, purpose=purpose, actor=actor)
    delivery = _mail_issued(user=user, purpose=purpose, issued=issued, actor=actor)
    return LinkOutcome(delivery=delivery, purpose=str(purpose))


def _issue_for_mail(*, user: User, purpose: str, actor: User | None) -> tokens.IssuedLink:
    """Issue a link inside the caller's transaction and audit that it was issued."""
    issued = tokens.issue(user=user, purpose=purpose, issued_by=actor)
    record_security_event(
        event_type=SecurityEventType.CREDENTIAL_LINK_ISSUED,
        actor=actor,
        subject=user,
        detail={"purpose": str(purpose), "expires_at": issued.record.expires_at.isoformat()},
    )
    return issued


def _mail_issued(
    *, user: User, purpose: str, issued: tokens.IssuedLink, actor: User | None
) -> mail.Delivery:
    """Send a committed link, outside any lock; end that link if it did not leave."""
    delivery = mail.send_credential_link(
        user=user,
        purpose=purpose,
        token=issued.token,
        actor=actor,
        expires_at=issued.record.expires_at,
    )
    if not delivery.delivered:
        tokens.invalidate_link(issued.record, reason=f"not_delivered:{delivery.reason}")
    return delivery


def send_setup_link(*, actor: Actor, user: User) -> LinkOutcome:
    """Approve and invite a pending account, or send a password-setting link.

    One action on the page, three cases underneath:

    * a PENDING or INVITED account is approved (once, recorded) and sent an
      activation link — becoming INVITED only if the link actually left;
    * an ACTIVATED account that has never set a password here — every account
      that existed before this feature — is sent the same kind of link, which
      sets a first password and changes nothing else;
    * an ACTIVATED account that has a password is sent a reset link.

    The administrator sees whether the message was sent. They never see the
    link.

    **Three steps, and the message is sent between transactions, not inside
    one.** The decision, the approval and the link are written under the
    administration lock and the account's row lock, and committed. Then the
    message goes out with no lock held: an SMTP conversation can take as long
    as the transport's timeout, and holding the locks for it would hold up
    every other administration change and the person's own sign-in for as
    long (docs/adr/0180 §5). Then, for an invitation, the outcome is written:
    INVITED only if the message left and its link is still live — an
    invitation cancelled or a newer link sent meanwhile is not overwritten. A
    message that did not leave ends its own link.
    """
    with transaction.atomic():
        actor = _begin(actor, Capability.MANAGE_ACCOUNTS, subject=user, action="send_setup_link")
        user = User.objects.select_for_update().get(pk=user.pk)
        if _touches_administration(user):
            _require_actor(
                actor, Capability.DELEGATE_ADMINISTRATION, subject=user, action="send_setup_link"
            )

        inviting = user.provisioning_state in (
            ProvisioningState.PENDING,
            ProvisioningState.INVITED,
        )
        if inviting:
            if user.approved_at is None:
                user.approved_at = timezone.now()
                user.approved_by = actor
                user.save(update_fields=["approved_at", "approved_by", "updated_at"])
            purpose = CredentialTokenPurpose.ACTIVATION
        else:
            if not user.is_active:
                raise AdministrationRefused(ALREADY_INACTIVE)
            purpose = (
                CredentialTokenPurpose.PASSWORD_RESET
                if user.has_local_password
                else CredentialTokenPurpose.ACTIVATION
            )
        issued = _issue_for_mail(user=user, purpose=purpose, actor=actor)

    delivery = _mail_issued(user=user, purpose=purpose, issued=issued, actor=actor)
    outcome = LinkOutcome(delivery=delivery, purpose=str(purpose))
    if not inviting:
        return outcome

    with transaction.atomic():
        user = User.objects.select_for_update().get(pk=user.pk)
        if (
            delivery.delivered
            and user.provisioning_state == ProvisioningState.PENDING
            and tokens.is_live(issued.record)
        ):
            user.provisioning_state = ProvisioningState.INVITED
            user.save(update_fields=["provisioning_state", "updated_at"])
        record_security_event(
            event_type=SecurityEventType.ACCOUNT_INVITED,
            actor=actor,
            subject=user,
            succeeded=delivery.delivered,
            detail={"delivered": delivery.delivered, "outcome": delivery.reason},
        )
    return outcome


@transaction.atomic
def cancel_invitation(*, actor: Actor, user: User) -> User:
    """Withdraw an invitation: the link stops working and the account waits again."""
    actor = _begin(actor, Capability.MANAGE_ACCOUNTS, subject=user, action="cancel_invitation")
    user = User.objects.select_for_update().get(pk=user.pk)
    if _touches_administration(user):
        _require_actor(
            actor, Capability.DELEGATE_ADMINISTRATION, subject=user, action="cancel_invitation"
        )
    if user.provisioning_state != ProvisioningState.INVITED:
        raise AdministrationRefused(NOT_PENDING)
    tokens.invalidate_outstanding(user=user, reason="invitation_cancelled")
    user.provisioning_state = ProvisioningState.PENDING
    user.save(update_fields=["provisioning_state", "updated_at"])
    record_security_event(
        event_type=SecurityEventType.ACCOUNT_INVITATION_CANCELLED, actor=actor, subject=user
    )
    return user


# -- role, name and capabilities -----------------------------------------------------------


@dataclass(frozen=True)
class AccountChange:
    user: User
    changed: tuple[str, ...]


def overrides_after(
    *,
    role: str,
    wanted: dict[str, bool],
    previous_overrides: dict[str, str],
    previous_held: frozenset[str],
) -> tuple[dict[str, str], set[str]]:
    """The overrides a save stores, and which toggles the administrator moved.

    Read against what the page showed (``previous_held``), because that is what
    the administrator was looking at when they pressed `Salvesta`:

    * **A toggle they moved** is an explicit wish about the new role: stored as
      an override exactly when it differs from that role's default.
    * **A toggle they left alone** keeps whatever it was — an existing
      override stays (if it still means something for the new role), and the
      absence of one stays absent, so the capability *follows the role*. That
      is what makes changing Spetsialist to Osakonnajuht hand over the head's
      defaults rather than freezing the specialist's toggles in place as
      denials nobody asked for.

    A person whose permissions match their role carries no overrides at all,
    and a later change to a role's defaults reaches them, as it should.
    """
    result: dict[str, str] = {}
    moved: set[str] = set()
    for capability in capabilities.ALL_CAPABILITIES:
        default = capabilities.role_default(role, capability)
        if capability in wanted and bool(wanted[capability]) != (capability in previous_held):
            moved.add(capability)
            if bool(wanted[capability]) != default:
                result[capability] = capabilities.ALLOW if wanted[capability] else capabilities.DENY
            continue
        kept = previous_overrides.get(capability)
        if kept is None or (kept == capabilities.ALLOW) == default:
            continue
        if kept == capabilities.ALLOW and not capabilities.may_be_granted_to(role, capability):
            # An allow the new role cannot hold anyway; dropping it changes
            # nothing the person holds and keeps the stored state honest.
            continue
        result[capability] = kept
    # A stored allow whose prerequisite is not held does nothing today and must
    # not wake up silently later: «Haldusõiguste andmine» kept as an allow after
    # «Kasutajate haldus» was withdrawn would come back the day somebody ticks
    # only the second box, with the page having shown the first one unticked.
    for capability, prerequisite in capabilities.PREREQUISITES.items():
        if result.get(capability) == capabilities.ALLOW and not resolve_capability(
            role, result, prerequisite
        ):
            del result[capability]
    return result, moved


@transaction.atomic
def update_account(
    *,
    actor: Actor,
    user: User,
    display_name: str,
    role: str,
    wanted: dict[str, bool],
) -> AccountChange:
    """Save the account page: the name, the role and the permission toggles.

    ``wanted`` maps each capability to whether the person should hold it after
    the save. Only the difference from the role's default is stored.
    """
    actor = _begin(actor, Capability.MANAGE_ACCOUNTS, subject=user, action="update_account")
    user = User.objects.select_for_update().get(pk=user.pk)
    if user.pk != actor.pk and _touches_administration(user):
        # Not only its power: renaming another administrator is still acting on
        # an administrator, which is delegation work.
        _require_actor(
            actor, Capability.DELEGATE_ADMINISTRATION, subject=user, action="update_account"
        )

    unknown = set(wanted) - set(capabilities.ALL_CAPABILITIES)
    if unknown:
        raise PrivilegeRefused(UNKNOWN_CAPABILITY, action="update_account", subject=user)
    name = " ".join((display_name or "").split())
    if not name:
        raise AdministrationRefused(DISPLAY_NAME_REQUIRED)
    if role not in UserRole.values:
        raise AdministrationRefused(UNKNOWN_ROLE)

    old_role = user.role
    old_overrides = capabilities.clean_overrides(user.capability_overrides)
    old_held = account_capabilities(user)
    old_effective = sorted(old_held)
    new_overrides, moved = overrides_after(
        role=role, wanted=wanted, previous_overrides=old_overrides, previous_held=old_held
    )

    power_changes = role != old_role or new_overrides != old_overrides
    if power_changes:
        # Authority first, then whether the wish is grantable: somebody who may
        # not change this account at all learns nothing about which of their
        # wishes would have been allowed.
        if actor.pk == user.pk:
            raise PrivilegeRefused(NO_SELF_CHANGE, action="update_account", subject=user)
        if role != old_role:
            _clean_role(role, actor=actor, subject=user, action="update_account")
        new_held = frozenset(
            c for c in capabilities.ALL_CAPABILITIES if resolve_capability(role, new_overrides, c)
        )
        administrative = set(capabilities.ADMINISTRATIVE_CAPABILITIES)
        if (old_held | new_held) & administrative or _touches_administration(user):
            _require_actor(
                actor, Capability.DELEGATE_ADMINISTRATION, subject=user, action="update_account"
            )
        for capability in moved:
            if wanted[capability] and not capabilities.may_be_granted_to(role, capability):
                raise PrivilegeRefused(NOT_GRANTABLE, action="update_account", subject=user)

    changed: list[str] = []
    delegates_before = len(_delegating_administrators())
    usable_before = len(usable_full_administrators())
    if name != user.display_name:
        record_security_event(
            event_type=SecurityEventType.ACCOUNT_UPDATED,
            actor=actor,
            subject=user,
            detail={"field": "display_name", "from": user.display_name, "to": name},
        )
        user.display_name = name
        changed.append("display_name")
    if power_changes:
        user.role = role
        user.capability_overrides = new_overrides
        changed.extend(["role", "capability_overrides"])
    if not changed:
        return AccountChange(user=user, changed=())
    user.save(update_fields=[*changed, "updated_at"])

    if power_changes:
        _guard_final_administrator(
            delegates_before, actor=actor, subject=user, action="update_account"
        )
        _guard_continuity(usable_before, subject=user, action="update_account")
        new_effective = _effective(user)
        if role != old_role:
            record_security_event(
                event_type=SecurityEventType.ROLE_CHANGED,
                actor=actor,
                subject=user,
                detail={
                    "reason": "account_administration",
                    "previous_role": old_role,
                    "role": role,
                },
            )
        record_security_event(
            event_type=SecurityEventType.CAPABILITIES_CHANGED,
            actor=actor,
            subject=user,
            detail={
                "previous_role": old_role,
                "role": role,
                "previous_overrides": old_overrides,
                "overrides": new_overrides,
                "previous_capabilities": old_effective,
                "capabilities": new_effective,
            },
        )
        _bump_epoch(user)
    return AccountChange(user=user, changed=tuple(changed))


# -- switching off and on ----------------------------------------------------------------------


@transaction.atomic
def deactivate_account(*, actor: Actor, user: User, reason: str = "") -> User:
    """Switch an account off. Never a deletion.

    The register goes on naming the person on the work they did; their links
    stop working, their sessions end at their next request, and no
    authentication mode will sign them in.
    """
    actor = _begin(actor, Capability.MANAGE_ACCOUNTS, subject=user, action="deactivate_account")
    user = User.objects.select_for_update().get(pk=user.pk)
    if actor.pk == user.pk:
        raise PrivilegeRefused(NO_SELF_CHANGE, action="deactivate_account", subject=user)
    if user.provisioning_state != ProvisioningState.ACTIVATED:
        raise AdministrationRefused(NOT_ACTIVATED)
    if not user.is_active:
        raise AdministrationRefused(ALREADY_INACTIVE)
    if _touches_administration(user):
        _require_actor(
            actor, Capability.DELEGATE_ADMINISTRATION, subject=user, action="deactivate_account"
        )
    delegates_before = len(_delegating_administrators())
    usable_before = len(usable_full_administrators())
    user.is_active = False
    user.save(update_fields=["is_active", "updated_at"])
    _guard_final_administrator(
        delegates_before, actor=actor, subject=user, action="deactivate_account"
    )
    _guard_continuity(usable_before, subject=user, action="deactivate_account")
    tokens.invalidate_outstanding(user=user, reason="account_deactivated")
    _bump_epoch(user)
    record_security_event(
        event_type=SecurityEventType.ACCOUNT_DEACTIVATED,
        actor=actor,
        subject=user,
        detail={"reason": reason.strip()[:500]},
    )
    return user


@transaction.atomic
def reactivate_account(*, actor: Actor, user: User) -> User:
    """Switch an account back on. Its password and second factor are as they were."""
    actor = _begin(actor, Capability.MANAGE_ACCOUNTS, subject=user, action="reactivate_account")
    user = User.objects.select_for_update().get(pk=user.pk)
    if actor.pk == user.pk:
        raise PrivilegeRefused(NO_SELF_CHANGE, action="reactivate_account", subject=user)
    if user.provisioning_state != ProvisioningState.ACTIVATED:
        raise AdministrationRefused(NOT_ACTIVATED)
    if user.is_active:
        raise AdministrationRefused(ALREADY_ACTIVE)
    if _touches_administration(user):
        _require_actor(
            actor, Capability.DELEGATE_ADMINISTRATION, subject=user, action="reactivate_account"
        )
    user.is_active = True
    user.save(update_fields=["is_active", "updated_at"])
    _bump_epoch(user)
    record_security_event(
        event_type=SecurityEventType.ACCOUNT_REACTIVATED, actor=actor, subject=user
    )
    return user


@transaction.atomic
def reset_second_factor(*, actor: Actor, user: User) -> User:
    """Remove somebody's authenticator and recovery codes — the lost-phone path.

    Their sessions end; if they must use a second factor they are asked to
    enrol a new one at their next sign-in. One's own is removed from one's own
    security page, with a fresh proof, never from here.
    """
    actor = _begin(actor, Capability.MANAGE_ACCOUNTS, subject=user, action="reset_second_factor")
    user = User.objects.select_for_update().get(pk=user.pk)
    if actor.pk == user.pk:
        raise PrivilegeRefused(NO_SELF_CHANGE, action="reset_second_factor", subject=user)
    if _touches_administration(user):
        _require_actor(
            actor, Capability.DELEGATE_ADMINISTRATION, subject=user, action="reset_second_factor"
        )
    # Not held to the two-administrator rule, deliberately: this is the
    # lost-phone *recovery* the rule exists to keep possible. The person keeps
    # their password and enrols a new authenticator at their next step-up, so
    # nobody's power is withdrawn; refusing it would leave an administrator who
    # lost their phone locked out until an operator came (docs/adr/0186).
    removed = mfa.remove_second_factor(user)
    _bump_epoch(user)
    record_security_event(
        event_type=SecurityEventType.MFA_REMOVED,
        actor=actor,
        subject=user,
        detail={"by": "administrator", "had_second_factor": removed},
    )
    return user


@transaction.atomic
def change_login_email(*, actor: Actor, user: User, email: str, external_reason: str = "") -> User:
    """Correct the address an account signs in with. A delegation decision.

    The account keeps its identifier, its work, its history and its Entra
    object id; only the address it signs in with changes. Every outstanding
    link is invalidated — it was sent to the old address — and every session
    ends. This is how the placeholder identities of the shared-gate period get
    their real addresses before activation (docs/LOCAL_AUTH_ACTIVATION_RUNBOOK.md).
    """
    actor = _begin(
        actor, Capability.DELEGATE_ADMINISTRATION, subject=user, action="change_login_email"
    )
    user = User.objects.select_for_update().get(pk=user.pk)
    if actor.pk == user.pk:
        raise PrivilegeRefused(NO_SELF_CHANGE, action="change_login_email", subject=user)
    if user.has_local_password or mfa.has_second_factor(user):
        # After the person has signed in here, their address is how their own
        # reset links reach *them*. Letting an administrator move it would let
        # that administrator receive the next reset link and become them — the
        # one thing an administrator must never be able to do (docs/adr/0145 §4).
        raise PrivilegeRefused(
            ADDRESS_FIXED_AFTER_ACTIVATION, action="change_login_email", subject=user
        )
    address, exception = _clean_address(
        email,
        actor=actor,
        external_reason=external_reason,
        subject=user,
        action="change_login_email",
        exclude=user,
    )
    previous = user.upn
    if address == previous:
        return user
    user.upn = address
    user.email = address
    fields = ["upn", "email", "updated_at"]
    if exception:
        user.email_exception_reason = external_reason.strip()
        user.email_exception_approved_by = actor
        user.email_exception_approved_at = timezone.now()
        fields += [
            "email_exception_reason",
            "email_exception_approved_by",
            "email_exception_approved_at",
        ]
    user.save(update_fields=fields)
    tokens.invalidate_outstanding(user=user, reason="login_email_changed")
    _bump_epoch(user)
    record_security_event(
        event_type=SecurityEventType.LOGIN_EMAIL_CHANGED,
        actor=actor,
        subject=user,
        detail={"from": previous, "to": address, "external_address_exception": exception},
    )
    if exception:
        record_security_event(
            event_type=SecurityEventType.EMAIL_EXCEPTION_APPROVED,
            actor=actor,
            subject=user,
            detail={"email": address, "reason": external_reason.strip()},
        )
    return user


# -- the first administrator -------------------------------------------------------------------


@dataclass(frozen=True)
class Bootstrapped:
    user: User
    created: bool
    link: LinkOutcome | None
    #: Only when the operator asked for it and e-mail could not carry it: the
    #: link, for printing once on the operator's own terminal.
    printable_link: str = ""


@transaction.atomic
def bootstrap_first_administrator(
    *,
    email: str,
    display_name: str,
    role: str,
    operator_note: str,
    print_link: bool = False,
) -> Bootstrapped:
    """Make the very first account administrator. Once, and only by an operator.

    Refused when any account can already delegate administration — after the
    first, administrators appoint administrators, through the page and its
    audit. Never run by a migration or a deployment; `manage.py
    bootstrap_account_admin` is its only caller (docs/adr/0145 §11).

    The account gets both administrative capabilities and nothing else: no
    password (it sets its own through the link), no `is_staff`, no
    `is_superuser`, and its business role is whatever the operator states.
    """
    _require_mode_for_operator(BOOTSTRAP_WRONG_MODE)
    _lock_administration()
    try:
        address = email_policy.clean(email)
    except ValidationError as error:
        raise AdministrationRefused(error.messages[0]) from error
    # One-time means one person, not one run. Refused once anybody can
    # administer, and refused while a *different* first administrator is still
    # waiting to activate — re-running for the same address is how an expired
    # invitation is re-sent, because nobody exists yet who could resend it from
    # the page.
    holders = User.objects.filter(
        capability_overrides__contains={Capability.MANAGE_ACCOUNTS.value: capabilities.ALLOW}
    ).exclude(
        # A switched-off administrator — `emergency_disable_account`'s result —
        # administers nothing and waits for nothing; it must not stop the
        # operator appointing a first administrator again once nobody is left.
        provisioning_state=ProvisioningState.ACTIVATED,
        is_active=False,
    )
    if (
        _delegating_administrators()
        or administrators()
        or holders.exclude(upn__iexact=address).exists()
    ):
        raise AdministrationRefused(BOOTSTRAP_ALREADY_DONE)
    if role not in UserRole.values:
        raise AdministrationRefused(UNKNOWN_ROLE)

    user = User.objects.select_for_update().filter(upn__iexact=address).first()
    created = user is None
    if user is None:
        name = " ".join((display_name or "").split())
        if not name:
            raise AdministrationRefused(DISPLAY_NAME_REQUIRED)
        user = User.objects.create_user(
            upn=address,
            email=address,
            display_name=name,
            role=role,
            is_active=False,
            provisioning_state=ProvisioningState.PENDING,
            is_synthetic=not settings.REAL_DATA_ALLOWED,
        )
        record_security_event(
            event_type=SecurityEventType.ACCOUNT_CREATED,
            subject=user,
            detail={"role": role, "email": address, "via": "manage.py bootstrap_account_admin"},
        )
    elif (user.provisioning_state == ProvisioningState.ACTIVATED and not user.is_active) or (
        user.is_synthetic and settings.REAL_DATA_ALLOWED
    ):
        raise AdministrationRefused(BOOTSTRAP_ACCOUNT_UNUSABLE)

    overrides = capabilities.clean_overrides(user.capability_overrides)
    overrides[Capability.MANAGE_ACCOUNTS.value] = capabilities.ALLOW
    overrides[Capability.DELEGATE_ADMINISTRATION.value] = capabilities.ALLOW
    user.capability_overrides = overrides
    user.save(update_fields=["capability_overrides", "updated_at"])
    _bump_epoch(user)
    record_security_event(
        event_type=SecurityEventType.ADMINISTRATOR_BOOTSTRAPPED,
        subject=user,
        detail={
            "via": "manage.py bootstrap_account_admin",
            "note": operator_note.strip()[:500],
            "capabilities": _effective(user),
        },
    )

    if user.provisioning_state == ProvisioningState.ACTIVATED and user.has_local_password:
        return Bootstrapped(user=user, created=created, link=None)

    if user.approved_at is None:
        user.approved_at = timezone.now()
        user.save(update_fields=["approved_at", "updated_at"])
    purpose = CredentialTokenPurpose.ACTIVATION
    if mail.delivery_enabled() and not print_link:
        outcome = _deliver(user=user, purpose=purpose, actor=None)
        if not outcome.delivery.delivered:
            # Undo the whole appointment. An administrator who cannot set a
            # password is an administrator nobody can become — and, being one,
            # would make every later bootstrap refuse.
            raise AdministrationRefused(BOOTSTRAP_NOT_DELIVERED + " " + outcome.delivery.message_et)
        if user.provisioning_state == ProvisioningState.PENDING:
            user.provisioning_state = ProvisioningState.INVITED
            user.save(update_fields=["provisioning_state", "updated_at"])
        return Bootstrapped(user=user, created=created, link=outcome)
    if not print_link:
        raise AdministrationRefused(BOOTSTRAP_NEEDS_A_CHANNEL)

    issued = tokens.issue(user=user, purpose=purpose, issued_by=None)
    record_security_event(
        event_type=SecurityEventType.CREDENTIAL_LINK_ISSUED,
        subject=user,
        detail={
            "purpose": str(purpose),
            "expires_at": issued.record.expires_at.isoformat(),
            "channel": "operator_terminal",
        },
    )
    if user.provisioning_state == ProvisioningState.PENDING:
        user.provisioning_state = ProvisioningState.INVITED
        user.save(update_fields=["provisioning_state", "updated_at"])
    return Bootstrapped(
        user=user,
        created=created,
        link=None,
        printable_link=mail.build_link(purpose, issued.token),
    )


# -- the operator's routes --------------------------------------------------------------------


@dataclass(frozen=True)
class OperatorLink:
    """A one-time link an operator issued for an administrator, to print once."""

    user: User
    purpose: str
    link: str
    expires_at: Any


@transaction.atomic
def issue_administrator_link(*, email: str, operator_note: str) -> OperatorLink:
    """A one-time link for an administrator to set their own password. By an operator.

    Under the shared gate account e-mail cannot be switched on
    (`juristid.E036`), so «Kinnita ja saada kutse» on the page records the
    administrator's decision and sends nothing. This is how the decided link
    reaches the person: `manage.py issue_administrator_link`, on the host, to an
    interactive terminal only, handed over by the operator in person.

    Only for an account an administrator (or the bootstrap) already made one —
    `accounts.manage` stored on it, or the ADMINISTRATOR role
    (`admin_session.may_receive_administrator_link`) — never a switched-off or,
    on real data, synthetic one. A PENDING account must have been approved on
    the page first, and becomes INVITED. An account that already has a password
    gets a reset link; otherwise an activation link. Any older link of the same
    purpose stops working (`tokens.issue`). Audited with the operator's note;
    the link itself is never stored or logged.
    """
    _require_mode_for_operator(LINK_WRONG_MODE)
    if not (operator_note or "").strip():
        raise AdministrationRefused(EMERGENCY_REASON_REQUIRED)
    _lock_administration()
    address = email_policy.normalise(email)
    user = User.objects.select_for_update().filter(upn__iexact=address).first()
    if user is None:
        raise AdministrationRefused(EMERGENCY_UNKNOWN_ACCOUNT)
    if not admin_session.may_receive_administrator_link(user):
        raise AdministrationRefused(LINK_NOT_AN_ADMINISTRATOR)
    if user.provisioning_state == ProvisioningState.PENDING:
        if user.approved_at is None:
            raise AdministrationRefused(LINK_NOT_APPROVED)
        user.provisioning_state = ProvisioningState.INVITED
        user.save(update_fields=["provisioning_state", "updated_at"])
    purpose = (
        CredentialTokenPurpose.PASSWORD_RESET
        if user.provisioning_state == ProvisioningState.ACTIVATED and user.has_local_password
        else CredentialTokenPurpose.ACTIVATION
    )
    issued = tokens.issue(user=user, purpose=purpose, issued_by=None)
    record_security_event(
        event_type=SecurityEventType.CREDENTIAL_LINK_ISSUED,
        subject=user,
        detail={
            "purpose": str(purpose),
            "expires_at": issued.record.expires_at.isoformat(),
            "channel": "operator_terminal",
            "via": "manage.py issue_administrator_link",
            "note": operator_note.strip()[:500],
        },
    )
    return OperatorLink(
        user=user,
        purpose=str(purpose),
        link=mail.build_link(purpose, issued.token),
        expires_at=issued.record.expires_at,
    )


@dataclass(frozen=True)
class EmergencyOutcome:
    user: User
    applied: bool
    was_active: bool
    links_invalidated: int
    administrators_before: int
    administrators_after: int


def emergency_disable_account(
    *, email: str, reason: str, dry_run: bool = False
) -> EmergencyOutcome:
    """Switch a compromised account off at once, past the two-administrator rule.

    The independently verified recovery route the continuity rule must not make
    impossible (docs/adr/0186): an operator on the host, with a mandatory reason,
    audited as `EMERGENCY_ACCOUNT_DISABLED` with `by: operator`. Not a web
    route — no persona, no administrator session and no request can reach it.

    It does exactly what switching off does — `is_active` false, every
    outstanding link invalidated, every session (the administrator session
    included) ended through the security epoch — and nothing else: the account
    keeps its role, its capabilities, its password and its history, so the
    record of what it was is intact and a later reactivation (by a delegating
    administrator, through the page) is an ordinary, audited decision. Works in
    every mode. ``dry_run`` reports and writes nothing.
    """
    if not (reason or "").strip():
        raise AdministrationRefused(EMERGENCY_REASON_REQUIRED)
    address = email_policy.normalise(email)
    with transaction.atomic():
        _lock_administration()
        user = User.objects.select_for_update().filter(upn__iexact=address).first()
        if user is None:
            raise AdministrationRefused(EMERGENCY_UNKNOWN_ACCOUNT)
        before = len(usable_full_administrators())
        was_active = user.is_active
        if not was_active and not _has_live_links(user):
            raise AdministrationRefused(EMERGENCY_ALREADY_OFF)
        if dry_run:
            after = before - (1 if is_usable_full_administrator(user) else 0)
            return EmergencyOutcome(user, False, was_active, 0, before, after)
        if was_active:
            user.is_active = False
            user.save(update_fields=["is_active", "updated_at"])
        invalidated = tokens.invalidate_outstanding(user=user, reason="emergency_disabled")
        _bump_epoch(user)
        after = len(usable_full_administrators())
        record_security_event(
            event_type=SecurityEventType.EMERGENCY_ACCOUNT_DISABLED,
            subject=user,
            succeeded=True,
            detail={
                "by": "operator",
                "via": "manage.py emergency_disable_account",
                "reason": reason.strip()[:500],
                "was_active": was_active,
                "links_invalidated": invalidated,
                "usable_full_administrators_before": before,
                "usable_full_administrators_after": after,
            },
        )
    return EmergencyOutcome(user, True, was_active, invalidated, before, after)


def _has_live_links(user: User) -> bool:
    """Whether any one-time link of this account is still live."""
    return any(
        tokens.has_outstanding(user=user, purpose=purpose)
        for purpose in CredentialTokenPurpose.values
    )
