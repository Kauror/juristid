"""The verified administrator session: a privileged step-up behind the shared gate.

Production runs `AUTH_MODE=shared_gate`. One department password opens the
door and a persona is then **picked from a list** — which proves nothing about
who is at the keyboard (docs/adr/0016). Account administration must not rest
on that: selecting the department head's name must not let anybody create,
switch off or empower an account.

So account administration under the shared gate needs a second, separate
proof, and this module is it (docs/adr/0186):

* **The person's own credentials.** Their e-mail address, the personal password
  *they* set through a one-time link (`tokens`, `credentials`), and a current
  code from *their own* authenticator — mandatory, enrolled through the existing
  `mfa` service the first time. Throttled by the existing `throttle` counters;
  every attempt audited.
* **Bound to the principal, not to the persona.** The session stores the
  verified account's id and a keyed binding of its credential state
  (`tokens.credential_stamp`: password hash, sign-in address, active flag) and
  its `security_epoch`. A password change, a deactivation, an address change, a
  second factor enrolled or reset, a role or capability change — anything that
  moves either — ends it at its next use, in every worker.
* **Narrow.** It never changes `request.user` or the persona, grants no
  business read or write (docs/adr/0181), and opens nothing but Haldus
  administration. It is not a sign-in to the application: without the gate it
  is worth nothing, and the gate closing — sign-out, expiry, idle, a rotated
  shared password — ends it.
* **Short.** Its own idle and absolute limits (`ADMIN_SESSION_IDLE_SECONDS`,
  `ADMIN_SESSION_ABSOLUTE_SECONDS`), and a fresh proof (password and code again)
  before any change once `ADMIN_SESSION_REAUTH_SECONDS` have passed.

Under `AUTH_MODE=local_password` none of this is needed: the personal sign-in
with its second factor *is* the verified principal, and `verified_administrator`
answers from it exactly as `local_auth.may_administer_accounts` always has.

**Who may hold one.** An active, activated account with a personal password
that holds `accounts.manage` (resolved centrally) or the technical
ADMINISTRATOR role. Nobody else gets past the password step, and the page says
the same sentence for that as for a wrong password.

**What the services accept.** `VerifiedActor`, minted only here, from a request
whose verified principal was just re-checked. `app.accounts.administration`
refuses a bare account under the shared gate, so a view that forgot this module
cannot administer anything on the strength of a persona (the second lock).
"""

from __future__ import annotations

import hmac
import threading
import weakref
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Any

from django.conf import settings
from django.contrib.auth.hashers import make_password
from django.db import transaction
from django.http import HttpRequest
from django.utils import timezone
from django.utils.crypto import salted_hmac

from app.accounts import email_policy, mfa, passwords, shared_gate, throttle, tokens
from app.accounts.enums import AuthMode, Capability, ProvisioningState, ThrottleScope, UserRole
from app.accounts.models import User
from app.audit.enums import SecurityEventType
from app.audit.services import record_security_event
from app.core.client_ip import client_ip

#: Session keys. Namespaced, so a grep finds every place the state is touched
#: and nothing else can collide with them. None of them holds a secret.
SESSION_STATE = "admin_session:state"
SESSION_PENDING = "admin_session:pending"
SESSION_SEALED_ENROLMENT = "admin_session:sealed_enrolment"
SESSION_NEXT = "admin_session:next"

#: Where a request keeps the principal it acts through, for the audit
#: provenance of the rows that request writes (app/audit/provenance.py).
_ACTING = "_juristid_administrator_principal"
#: Where a request caches the answer of `verified_administrator`.
_CHECKED = "_juristid_verified_administrator"
_UNSET = object()

#: The audit `path` every row of this flow carries.
PATH = "administrator_session"

#: How long a correct password waits for its code (or its enrolment).
PENDING_SECONDS = 300

#: How often the last-use stamp is rewritten. Finer than the gate's five
#: minutes because the idle limit is minutes, not hours; still not every request.
LAST_SEEN_RESOLUTION = timedelta(seconds=30)

#: Safe defaults, used when a setting is absent or out of its bounds.
DEFAULT_IDLE_SECONDS = 900
DEFAULT_ABSOLUTE_SECONDS = 7200
DEFAULT_REAUTH_SECONDS = 600

#: The one sentence every refused step-up shows, whatever the reason.
SIGN_IN_REFUSED = "E-posti aadress, parool või kood on vale."
CODE_REFUSED = "Kood ei sobinud. Proovi uuesti."

_BINDING_SALT = "app.accounts.admin_session.binding"


# -- the limits ---------------------------------------------------------------------


def _setting(name: str, default: int, *, low: int, high: int) -> int:
    try:
        value = int(getattr(settings, name, default))
    except (TypeError, ValueError):
        return default
    return value if low <= value <= high else default


def absolute_seconds() -> int:
    """At most this long after the code was typed, whatever the activity. ≤ 12 h."""
    return _setting("ADMIN_SESSION_ABSOLUTE_SECONDS", DEFAULT_ABSOLUTE_SECONDS, low=300, high=43200)


def idle_seconds() -> int:
    """At most this long without a Haldus request. Never longer than the absolute limit."""
    return min(
        _setting("ADMIN_SESSION_IDLE_SECONDS", DEFAULT_IDLE_SECONDS, low=60, high=43200),
        absolute_seconds(),
    )


def reauth_seconds() -> int:
    """How old the last proof may be before a change asks for password and code again."""
    return min(
        _setting("ADMIN_SESSION_REAUTH_SECONDS", DEFAULT_REAUTH_SECONDS, low=60, high=43200),
        idle_seconds(),
    )


# -- who may hold one ---------------------------------------------------------------


def is_available() -> bool:
    """Whether this deployment offers the step-up at all: the shared gate only."""
    return shared_gate.current_mode() == AuthMode.SHARED_GATE


def holds_administrative_standing(user: Any) -> bool:
    """`accounts.manage`, resolved centrally, or the technical ADMINISTRATOR role.

    Asked of an *active* account (`has_capability` and `acting_role` both
    answer nothing for an inactive one). What either grants inside Haldus is
    decided again by each section and each service.
    """
    from app.core.authorization import acting_role, has_capability

    return (
        has_capability(user, Capability.MANAGE_ACCOUNTS)
        or acting_role(user) == UserRole.ADMINISTRATOR
    )


def eligible(user: Any) -> bool:
    """Whether this account may hold a verified administrator session now."""
    if not isinstance(user, User):
        return False
    if user.provisioning_state != ProvisioningState.ACTIVATED or not user.is_active:
        return False
    if not user.has_local_password:
        return False
    if user.is_synthetic and settings.REAL_DATA_ALLOWED:
        return False
    return holds_administrative_standing(user)


def may_receive_administrator_link(user: Any) -> bool:
    """Whether a one-time link may set this account's password under the shared gate.

    The link pages exist under the shared gate for this one purpose: an
    administrator setting their own password before their first step-up. So the
    holder must be somebody an administrator (or the operator's bootstrap)
    already made one: `accounts.manage` stored on the account, read from what
    the account will hold once active — a first administrator is still INVITED
    when they follow the link — or, for an account already active, the
    technical ADMINISTRATOR role through `acting_role` (the one place role
    questions are answered, ENG-128). An invited technical administrator
    therefore needs `accounts.manage` granted, which the bootstrap always does.
    Never a switched-off account; never a synthetic one on real data.
    """
    from app.core.authorization import account_capabilities, acting_role

    if not isinstance(user, User):
        return False
    if user.provisioning_state == ProvisioningState.ACTIVATED and not user.is_active:
        return False
    if user.is_synthetic and settings.REAL_DATA_ALLOWED:
        return False
    return (
        Capability.MANAGE_ACCOUNTS.value in account_capabilities(user)
        or acting_role(user) == UserRole.ADMINISTRATOR
    )


def binding(user: User) -> str:
    """The account's credential state and epoch, as the session may keep it.

    The same material a pending personal sign-in is bound to
    (`local_auth.pending_binding`, docs/adr/0183), under a purpose of its own so
    one can never be presented as the other. Keyed under the deployment's
    secret: the session row holds nothing a password hash could be compared
    against offline.
    """
    material = f"{tokens.credential_stamp(user)}\x1f{int(user.security_epoch or 0)}"
    return salted_hmac(_BINDING_SALT, material, algorithm="sha256").hexdigest()


def _binding_holds(stored: Any, user: User) -> bool:
    if not isinstance(stored, str) or not stored:
        return False
    return hmac.compare_digest(stored, binding(user))


# -- the actor the services accept --------------------------------------------------

#: Every actor `_mint` made, by identity, for as long as it is referenced. A
#: copy — `dataclasses.replace(actor, user=somebody_else)`, `copy.copy`, a
#: pickle round trip — is a different object and is in none of these.
_MINTED: weakref.WeakValueDictionary[int, VerifiedActor] = weakref.WeakValueDictionary()
_minting = threading.local()


@dataclass(frozen=True, eq=False)
class VerifiedActor:
    """An administrator whose own proof this request has just re-checked.

    The only form in which `app.accounts.administration` accepts an actor under
    the shared gate. Minted by `administrator_actor` and nowhere else:
    constructing one anywhere else — `dataclasses.replace` included, which
    calls the constructor — raises `TypeError`, and `is_verified_actor` accepts
    only the very instance `_mint` registered, never an equal-looking copy.
    Identity, not equality (`eq=False`), so no two actors compare alike.

    ``epoch`` is the account's `security_epoch` when the proof was checked. The
    services re-read the account under the administration lock and refuse if
    it has moved — a power withdrawn by a concurrent request ends this actor's
    authority before its change is made, not after.
    """

    user: User
    via: str
    epoch: int

    def __post_init__(self) -> None:
        if not getattr(_minting, "active", False):
            raise TypeError("A VerifiedActor is minted by app.accounts.admin_session only.")

    @property
    def pk(self) -> Any:
        return self.user.pk


def _mint(user: User, *, via: str) -> VerifiedActor:
    _minting.active = True
    try:
        actor = VerifiedActor(user=user, via=via, epoch=int(user.security_epoch or 0))
    finally:
        _minting.active = False
    _MINTED[id(actor)] = actor
    return actor


def is_verified_actor(actor: Any) -> bool:
    """True only for an actor `_mint` made — the same object, not a copy of one."""
    return isinstance(actor, VerifiedActor) and _MINTED.get(id(actor)) is actor


# -- reading the session ------------------------------------------------------------


def _stamp(raw: Any) -> datetime | None:
    if not raw:
        return None
    try:
        moment = datetime.fromisoformat(str(raw))
    except ValueError:
        return None
    return None if timezone.is_naive(moment) else moment


def _client(request: HttpRequest) -> dict[str, Any]:
    return {
        "ip_address": client_ip(request),
        "user_agent": request.META.get("HTTP_USER_AGENT", ""),
    }


def _state(request: HttpRequest) -> dict[str, Any] | None:
    session = getattr(request, "session", None)
    if session is None:
        return None
    state = session.get(SESSION_STATE)
    return state if isinstance(state, dict) else None


def _gate_pass(request: HttpRequest) -> str:
    return shared_gate.gate_passed_at(request) or ""


def _expiry_reason(request: HttpRequest, state: dict[str, Any]) -> str:
    """Why this administrator session must end now, or "" while it may go on."""
    if not shared_gate.has_passed(request):
        return "gate_closed"
    stored_gate = state.get("gate")
    if not isinstance(stored_gate, str) or not hmac.compare_digest(
        stored_gate, _gate_pass(request)
    ):
        # Opened behind a different pass of the gate. A flush ends the state
        # with the gate anyway; this is the same rule stated where it is read.
        return "gate_closed"
    now = timezone.now()
    verified = _stamp(state.get("verified_at"))
    if verified is None or now - verified >= timedelta(seconds=absolute_seconds()):
        return "absolute_timeout"
    seen = _stamp(state.get("last_seen")) or verified
    if now - seen >= timedelta(seconds=idle_seconds()):
        return "idle_timeout"
    return ""


def _principal_of(state: dict[str, Any]) -> User | None:
    raw = state.get("user")
    if not raw:
        return None
    try:
        return User.objects.filter(pk=str(raw)).first()
    except (ValueError, TypeError):
        return None


def _shared_gate_principal(request: HttpRequest) -> User | None:
    state = _state(request)
    if state is None:
        return None
    reason = _expiry_reason(request, state)
    user = _principal_of(state) if not reason else None
    if not reason:
        if user is None:
            reason = "unknown_account"
        elif not _binding_holds(state.get("binding"), user):
            # A password, address, active flag or epoch change since the proof.
            reason = "credentials_changed"
        elif not eligible(user):
            reason = "no_longer_eligible"
    if reason:
        end(request, reason=reason, principal=user)
        return None
    # Deliberately no `_touch` here: this answers for every page, business
    # pages and the top bar included, and a colleague reading Teemad must not
    # keep an absent administrator's session alive. Only Haldus administration
    # itself counts as activity (`note_activity`).
    return user


def _touch(request: HttpRequest, state: dict[str, Any]) -> None:
    seen = _stamp(state.get("last_seen"))
    now = timezone.now()
    if seen is None or now - seen >= LAST_SEEN_RESOLUTION:
        request.session[SESSION_STATE] = {**state, "last_seen": now.isoformat()}


def note_activity(request: HttpRequest) -> None:
    """A Haldus administration request: restart the administrator session's idle clock.

    Called by the Haldus and Haldus → Kasutajad views and the session's own
    pages — never by `verified_administrator`, which every page asks.
    """
    if shared_gate.current_mode() != AuthMode.SHARED_GATE:
        return
    state = _state(request)
    if state is not None and verified_administrator(request) is not None:
        _touch(request, _state(request) or state)


def _local_principal(request: HttpRequest) -> User | None:
    from app.accounts import local_auth

    if not local_auth.is_local_session(request):
        return None
    if not local_auth.has_second_factor_session(request) or local_auth.enrolment_pending(request):
        return None
    user = getattr(request, "user", None)
    return user if eligible(user) else None


def verified_administrator(request: HttpRequest) -> User | None:
    """The independently verified administrator principal of this session, or None.

    Re-checked on every call — the account read afresh, active, activated,
    still holding `accounts.manage` or the ADMINISTRATOR role, its credential
    state and `security_epoch` what they were at the proof, the session inside
    its idle and absolute limits and still behind the same pass of the gate.
    The caller then asks the capability its page or action needs.

    **Never derived from the selected persona.** Under the shared gate it comes
    from the step-up's own session state only; `request.user` is not read. Under
    `local_password` it is the personally signed-in account with a proved
    second factor — the same answer `local_auth.may_administer_accounts` gave.
    Every other mode: None.

    Cached on the request, so a page that asks in its context, its gate and its
    view pays one query. The services check again under their lock.
    """
    cached = getattr(request, _CHECKED, _UNSET)
    if cached is not _UNSET:
        return cached  # type: ignore[return-value]
    mode = shared_gate.current_mode()
    if mode == AuthMode.SHARED_GATE:
        user = _shared_gate_principal(request)
    elif mode == AuthMode.LOCAL_PASSWORD:
        user = _local_principal(request)
    else:
        user = None
    setattr(request, _CHECKED, user)
    return user


def holds(request: HttpRequest, capability: str) -> bool:
    """Whether the verified principal of this request holds ``capability``."""
    from app.core.authorization import has_capability

    user = verified_administrator(request)
    return user is not None and has_capability(user, capability)


def may_administer_accounts(request: HttpRequest) -> bool:
    """Haldus → Kasutajad: a verified principal holding `accounts.manage`."""
    return holds(request, Capability.MANAGE_ACCOUNTS)


def is_technical_administrator(request: HttpRequest) -> bool:
    """A verified principal under the technical ADMINISTRATOR role (Süsteemi seis)."""
    from app.core.authorization import acting_role

    user = verified_administrator(request)
    return user is not None and acting_role(user) == UserRole.ADMINISTRATOR


def administrator_actor(request: HttpRequest) -> VerifiedActor | None:
    """The actor an administration view hands to the services, or None.

    Marks the request as acting through the verified principal, so every audit
    row it writes records that principal with `auth_method =
    administrator_session` beside whatever persona is chosen (docs/adr/0180,
    docs/adr/0186).
    """
    user = verified_administrator(request)
    if user is None:
        return None
    mode = shared_gate.current_mode()
    via = PATH if mode == AuthMode.SHARED_GATE else "local_password"
    if mode == AuthMode.SHARED_GATE:
        setattr(request, _ACTING, user)
    return _mint(user, via=via)


def acting_principal(request: HttpRequest) -> User | None:
    """The principal this request acts through, for the audit provenance only."""
    user = getattr(request, _ACTING, None)
    return user if isinstance(user, User) else None


def is_open(request: HttpRequest) -> bool:
    """Whether this session holds step-up state at all (unverified, for display)."""
    return _state(request) is not None


# -- signing in ---------------------------------------------------------------------


@dataclass(frozen=True)
class Outcome:
    #: "code" (password accepted, a code is due), "enrol" (accepted, an
    #: authenticator must be set up first), "verified", "refused" or "locked".
    status: str
    wait: int = 0


def _refused_detail(email: str, reason: str, **extra: Any) -> dict[str, Any]:
    detail: dict[str, Any] = {"path": PATH, "reason": reason, **extra}
    detail["email"] = (
        email_policy.normalise(email)
        if email_policy.looks_like_an_address(email)
        else "(malformed)"
    )
    return detail


def _ineligibility(user: User | None) -> str:
    if user is None:
        return "unknown_account"
    if user.provisioning_state != ProvisioningState.ACTIVATED:
        return "not_activated"
    if not user.is_active:
        return "inactive"
    if not user.has_local_password:
        return "no_local_password"
    if user.is_synthetic and settings.REAL_DATA_ALLOWED:
        return "synthetic_account"
    if not holds_administrative_standing(user):
        return "not_an_administrator"
    return ""


def attempt_password(request: HttpRequest, *, email: str, password: str) -> Outcome:
    """The address and the personal password: throttle, verify, count — one step.

    The existing sign-in counters (account+network, account, network), so a
    guess here and a guess at the dormant personal sign-in are the same guess.
    An unknown address costs the same hash as a known one. Every refusal —
    unknown, wrong password, not activated, switched off, no administrative
    standing — is the same sentence and the same count; only the audit says
    which. A correct password opens nothing yet: it leaves a pending marker,
    bound to the account's credential state, that the code must complete.
    """
    if not is_available() or not shared_gate.has_passed(request):
        return Outcome("refused")
    address = email_policy.normalise(email)
    counters = throttle.sign_in_counters(request, address)
    raw = passwords.normalise(password)
    exempt = (
        frozenset({ThrottleScope.SIGN_IN_ACCOUNT.value})
        if throttle.is_trusted_source(address, request)
        else frozenset()
    )
    with transaction.atomic(), throttle.holding(counters) as held:
        wait = held.wait(ignoring=exempt)
        if wait:
            record_security_event(
                event_type=SecurityEventType.AUTHENTICATION_FAILED,
                succeeded=False,
                detail=_refused_detail(email, "locked_out"),
                **_client(request),
            )
            return Outcome("locked", wait=wait)
        user = User.objects.filter(upn=address).first() if address else None
        if user is None or not user.has_usable_password():
            make_password(raw)  # the cost of a real check, for nobody
            correct = False
        else:
            correct = user.check_password(raw)
        reason = "bad_password" if not correct else _ineligibility(user)
        if user is None:
            reason = "unknown_account"
        if reason:
            held.fail()
            wait = held.wait(ignoring=exempt)
            record_security_event(
                event_type=SecurityEventType.AUTHENTICATION_FAILED,
                succeeded=False,
                subject=user,
                detail=_refused_detail(email, reason),
                **_client(request),
            )
            return Outcome("locked" if wait else "refused", wait=wait)
        held.succeed()

    if user is None:  # pragma: no cover - every refusal above has returned
        return Outcome("refused")
    request.session.pop(SESSION_SEALED_ENROLMENT, None)
    request.session[SESSION_PENDING] = {
        "user": str(user.pk),
        "at": timezone.now().isoformat(),
        "binding": binding(user),
        "gate": _gate_pass(request),
    }
    return Outcome("code" if mfa.has_second_factor(user) else "enrol")


def pending_user(request: HttpRequest) -> User | None:
    """The account whose code this session is waiting for, while it still may.

    Inside `PENDING_SECONDS`, behind the same pass of the gate, the account
    still eligible and its credential state what it was at the password.
    """
    pending = request.session.get(SESSION_PENDING) if hasattr(request, "session") else None
    if not isinstance(pending, dict):
        return None
    started = _stamp(pending.get("at"))
    gate = pending.get("gate")
    if (
        started is None
        or timezone.now() - started > timedelta(seconds=PENDING_SECONDS)
        or not shared_gate.has_passed(request)
        or not isinstance(gate, str)
        or not hmac.compare_digest(gate, _gate_pass(request))
    ):
        _drop_pending(request)
        return None
    user = User.objects.filter(pk=str(pending.get("user") or "")).first()
    if user is None or _ineligibility(user):
        _drop_pending(request)
        return None
    if not _binding_holds(pending.get("binding"), user):
        _drop_pending(request)
        record_security_event(
            event_type=SecurityEventType.AUTHENTICATION_FAILED,
            succeeded=False,
            subject=user,
            detail={"path": PATH, "stage": "second_factor", "reason": "credentials_changed"},
            **_client(request),
        )
        return None
    return user


def _drop_pending(request: HttpRequest) -> None:
    request.session.pop(SESSION_PENDING, None)
    request.session.pop(SESSION_SEALED_ENROLMENT, None)


def verify_code(request: HttpRequest, *, code: str, kind: str = "totp") -> Outcome:
    """Finish the step-up with a code from the person's authenticator (or a recovery code)."""
    user = pending_user(request)
    if user is None or not mfa.has_second_factor(user):
        return Outcome("refused")
    counters = [throttle.user_counter(ThrottleScope.SECOND_FACTOR, user.pk)]
    with transaction.atomic(), throttle.holding(counters) as held:
        wait = held.wait()
        if wait:
            record_security_event(
                event_type=SecurityEventType.AUTHENTICATION_FAILED,
                succeeded=False,
                subject=user,
                detail={"path": PATH, "stage": "second_factor", "reason": "locked_out"},
                **_client(request),
            )
            return Outcome("locked", wait=wait)
        if kind == "recovery":
            accepted = mfa.use_recovery_code(user, code)
        else:
            accepted = mfa.verify_totp(user, code)
        if not accepted:
            wait = held.fail()
            record_security_event(
                event_type=SecurityEventType.AUTHENTICATION_FAILED,
                succeeded=False,
                subject=user,
                detail={
                    "path": PATH,
                    "stage": "second_factor",
                    "method": kind,
                    "reason": "bad_code",
                },
                **_client(request),
            )
            return Outcome("locked" if wait else "refused", wait=wait)
        held.succeed()
    if kind == "recovery":
        record_security_event(
            event_type=SecurityEventType.MFA_RECOVERY_CODE_USED,
            actor=user,
            subject=user,
            detail={"path": PATH, "remaining": mfa.remaining_recovery_codes(user)},
            **_client(request),
        )
    return _establish_if_still_bound(request, user, second_factor=kind)


def begin_enrolment(request: HttpRequest) -> mfa.Enrolment | None:
    """The authenticator a pending administrator without one is asked to set up.

    Reached only after a correct password, for an account that has no confirmed
    second factor; the secret is kept sealed in the session, never in clear.
    """
    user = pending_user(request)
    if user is None or mfa.has_second_factor(user):
        return None
    sealed = request.session.get(SESSION_SEALED_ENROLMENT, "")
    enrolment = mfa.resume_enrolment(user, sealed) if sealed else None
    if enrolment is None:
        enrolment = mfa.begin_enrolment(user)
        request.session[SESSION_SEALED_ENROLMENT] = enrolment.sealed
    return enrolment


@dataclass(frozen=True)
class Enrolled:
    outcome: Outcome
    recovery_codes: list[str] = field(default_factory=list)


def confirm_enrolment(request: HttpRequest, *, code: str) -> Enrolled:
    """Confirm the new authenticator with a code from it, and open the session.

    The enrolment itself is the existing one (`mfa.confirm_enrolment`), its
    recovery codes the existing ones; the epoch moves, as for every factor
    enrolled (docs/adr/0145 §9), and the session is bound to the new epoch.
    """
    user = pending_user(request)
    sealed = request.session.get(SESSION_SEALED_ENROLMENT, "")
    if user is None or mfa.has_second_factor(user) or not sealed:
        return Enrolled(Outcome("refused"))
    counters = [throttle.user_counter(ThrottleScope.SECOND_FACTOR, user.pk)]
    with transaction.atomic(), throttle.holding(counters) as held:
        wait = held.wait()
        if wait:
            return Enrolled(Outcome("locked", wait=wait))
        locked = User.objects.select_for_update().get(pk=user.pk)
        if mfa.has_second_factor(locked) or not mfa.confirm_enrolment(
            locked, sealed=sealed, code=code
        ):
            wait = held.fail()
            record_security_event(
                event_type=SecurityEventType.AUTHENTICATION_FAILED,
                succeeded=False,
                subject=user,
                detail={"path": PATH, "stage": "second_factor_enrolment", "reason": "bad_code"},
                **_client(request),
            )
            return Enrolled(Outcome("locked" if wait else "refused", wait=wait))
        held.succeed()
        codes = mfa.generate_recovery_codes(locked)
        old_binding = binding(locked)
        User.objects.filter(pk=locked.pk).update(security_epoch=locked.security_epoch + 1)
        locked.refresh_from_db(fields=["security_epoch"])
        record_security_event(
            event_type=SecurityEventType.MFA_ENROLLED,
            actor=locked,
            subject=locked,
            detail={"method": "totp", "replaced_existing": False, "path": PATH},
            **_client(request),
        )
    request.session.pop(SESSION_SEALED_ENROLMENT, None)
    pending = request.session.get(SESSION_PENDING)
    if not isinstance(pending, dict) or not hmac.compare_digest(
        str(pending.get("binding") or ""), old_binding
    ):
        _drop_pending(request)
        return Enrolled(Outcome("refused"))
    # The enrolment moved the epoch this request itself proved against; the
    # pending marker follows it, and nothing else that moved it is forgiven.
    request.session[SESSION_PENDING] = {**pending, "binding": binding(locked)}
    _keep_persona_after_epoch_change(request, locked)
    outcome = _establish_if_still_bound(request, locked, second_factor="enrolment")
    return Enrolled(outcome, recovery_codes=codes if outcome.status == "verified" else [])


def _keep_persona_after_epoch_change(request: HttpRequest, user: User) -> None:
    """If the persona *is* this account, keep it chosen across the epoch it just moved.

    Moving the epoch ends the account's other sessions (that is what it is for).
    This session chose that account as its persona and has just proved to be
    its owner; Django's own `update_session_auth_hash` keeps it, exactly as a
    personal password change keeps the session it was made in.
    """
    from django.contrib.auth import update_session_auth_hash

    current = getattr(request, "user", None)
    if isinstance(current, User) and current.pk == user.pk:
        update_session_auth_hash(request, user)


def _establish_if_still_bound(request: HttpRequest, user: User, *, second_factor: str) -> Outcome:
    """Asked once more after the code, with the account read afresh."""
    pending = request.session.get(SESSION_PENDING)
    current = User.objects.filter(pk=user.pk).first()
    if (
        current is None
        or not isinstance(pending, dict)
        or _ineligibility(current)
        or not _binding_holds(pending.get("binding"), current)
    ):
        _drop_pending(request)
        record_security_event(
            event_type=SecurityEventType.AUTHENTICATION_FAILED,
            succeeded=False,
            subject=user,
            detail={"path": PATH, "stage": "second_factor", "reason": "credentials_changed"},
            **_client(request),
        )
        return Outcome("refused")
    _drop_pending(request)
    _establish(request, current, second_factor=second_factor)
    return Outcome("verified")


def _establish(request: HttpRequest, user: User, *, second_factor: str) -> None:
    """Open the administrator session. `request.user` and the persona are untouched.

    The session identifier is rotated (`cycle_key` keeps the gate and the
    persona), so an identifier planted before the step-up is not the one that
    carries it afterwards.
    """
    request.session.cycle_key()
    now = timezone.now()
    stamp = now.isoformat()
    request.session[SESSION_STATE] = {
        "user": str(user.pk),
        "binding": binding(user),
        "gate": _gate_pass(request),
        "verified_at": stamp,
        "last_seen": stamp,
        "strong_at": stamp,
    }
    setattr(request, _CHECKED, user)
    setattr(request, _ACTING, user)
    User.objects.filter(pk=user.pk).update(last_authenticated_at=now)
    throttle.remember_trusted_source(email_policy.normalise(user.upn), request)
    record_security_event(
        event_type=SecurityEventType.ADMINISTRATOR_SESSION_STARTED,
        actor=user,
        subject=user,
        detail={
            "path": PATH,
            "second_factor": second_factor,
            "idle_seconds": idle_seconds(),
            "absolute_seconds": absolute_seconds(),
        },
        **_client(request),
    )


# -- fresh proof before a change ------------------------------------------------------


def needs_fresh_proof(request: HttpRequest) -> bool:
    """Whether a change must first ask for the password and the code again."""
    if shared_gate.current_mode() != AuthMode.SHARED_GATE:
        from app.accounts import local_auth

        return local_auth.needs_reauthentication(request)
    state = _state(request)
    if state is None:
        return True
    strong = _stamp(state.get("strong_at"))
    return strong is None or timezone.now() - strong > timedelta(seconds=reauth_seconds())


@dataclass(frozen=True)
class ReauthOutcome:
    accepted: bool
    wait: int = 0


def reauthenticate(request: HttpRequest, *, password: str, code: str) -> ReauthOutcome:
    """Prove again — password **and** code — that the verified administrator is here."""
    user = verified_administrator(request)
    if user is None or shared_gate.current_mode() != AuthMode.SHARED_GATE:
        return ReauthOutcome(False)
    counters = [throttle.user_counter(ThrottleScope.REAUTHENTICATION, user.pk)]
    with transaction.atomic(), throttle.holding(counters) as held:
        wait = held.wait()
        if wait:
            return ReauthOutcome(False, wait=wait)
        accepted = user.check_password(passwords.normalise(password)) and mfa.verify_totp(
            user, code
        )
        if not accepted:
            wait = held.fail()
            record_security_event(
                event_type=SecurityEventType.AUTHENTICATION_FAILED,
                succeeded=False,
                subject=user,
                detail={"path": PATH, "stage": "reauthentication"},
                **_client(request),
            )
            return ReauthOutcome(False, wait=wait)
        held.succeed()
    # `check_password` upgrades a hash whose parameters have moved and saves it,
    # which changes the credential stamp this session is bound to. The person
    # has just proved the password *and* the code, so the session is re-bound
    # to the account as it is now — read afresh, and only while it is still
    # eligible; anything else ends it as usual. A hash upgrade moves only the
    # credential stamp: a security epoch that moved meanwhile is somebody
    # else's change — a reset second factor, a password set by link — and ends
    # the session rather than being absorbed by the re-binding.
    fresh = User.objects.filter(pk=user.pk).first()
    if fresh is None or not eligible(fresh):
        end(request, reason="no_longer_eligible", principal=user)
        return ReauthOutcome(False)
    if fresh.security_epoch != user.security_epoch:
        end(request, reason="credentials_changed", principal=user)
        return ReauthOutcome(False)
    state = _state(request) or {}
    request.session[SESSION_STATE] = {
        **state,
        "binding": binding(fresh),
        "strong_at": timezone.now().isoformat(),
        "last_seen": timezone.now().isoformat(),
    }
    setattr(request, _CHECKED, fresh)
    setattr(request, _ACTING, fresh)
    record_security_event(
        event_type=SecurityEventType.REAUTHENTICATED,
        actor=user,
        detail={"path": PATH},
        **_client(request),
    )
    return ReauthOutcome(True)


# -- ending it ----------------------------------------------------------------------


def end(request: HttpRequest, *, reason: str, principal: User | None = None) -> None:
    """End the administrator session, recorded once. The gate and the persona stay.

    For the system's reasons (expiry, a credential or power change, the gate
    closing) and for «Lõpeta halduri seanss». Pending state goes with it.
    """
    state = _state(request)
    session = getattr(request, "session", None)
    if session is not None:
        session.pop(SESSION_STATE, None)
        session.pop(SESSION_PENDING, None)
        session.pop(SESSION_SEALED_ENROLMENT, None)
    setattr(request, _CHECKED, None)
    if hasattr(request, _ACTING):
        delattr(request, _ACTING)
    if state is None:
        return
    who = principal if principal is not None else _principal_of(state)
    record_security_event(
        event_type=SecurityEventType.ADMINISTRATOR_SESSION_ENDED,
        actor=who,
        subject=who,
        detail={"path": PATH, "reason": reason},
        **_client(request),
    )


def note_session_closing(request: HttpRequest, *, reason: str) -> None:
    """Record the end of an administrator session the whole session is about to lose.

    Called just before a flush — «Välju», the gate expiring or idling out, the
    shared password rotated — so the trail says the administrator session ended
    and why, not merely that the gate did.
    """
    if _state(request) is not None:
        end(request, reason=reason)


# -- across a persona change --------------------------------------------------------


@dataclass(frozen=True)
class Carried:
    state: dict[str, Any] | None


def carry(request: HttpRequest) -> Carried:
    """This session's administrator state, to hand back after `login` / `logout`.

    A persona change replaces the session. The administrator session is bound
    to its verified principal and to the gate's pass, not to the persona, so it
    survives the change unchanged — same principal, same clocks — and the
    persona changes nothing about who administers. A pending step-up does not
    survive: it starts again from the password.
    """
    state = _state(request)
    return Carried(dict(state) if state is not None else None)


def restore(request: HttpRequest, carried: Carried) -> None:
    if carried.state is not None:
        request.session[SESSION_STATE] = carried.state
