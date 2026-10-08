"""Signing in with a personal password: the mode, the session, the assurance.

`AUTH_MODE=local_password` is a complete fourth authentication mode, and it is
**dormant**: no deployment runs it, every route it adds answers 404 in the
other three modes, and switching to it is a separate approved activation
(docs/adr/0145, docs/LOCAL_AUTH_ACTIVATION_RUNBOOK.md).

The flow is `e-mail → password → second factor where required → Juristid`, and
the module keeps four things true about it:

* **Nobody is signed in until every required factor is proved.** A correct
  password with a second factor still to come leaves a *pending* marker in a
  freshly rotated session, and `login()` is called only after the code. A
  pending marker opens nothing but the code page, and expires in minutes.
* **One answer for every failure.** Unknown address, wrong password, an
  account not yet activated, an account switched off — the page says the same
  sentence and the throttle counts the same failure. Only the audit, which a
  stranger cannot read, records which it was.
* **A session knows how it was established.** The keys below record that this
  session was signed in here, when, when it was last used and when the person
  last proved who they are. The middleware ends a session that outlives its
  idle or absolute limit; `may_administer_accounts` asks for a second-factor
  session; critical changes ask for a recent proof (`needs_reauthentication`).
* **The shared gate's persona is never an identity here.** A session that
  carries a persona but no local sign-in marker is signed out, and no
  administrative question is ever answered "yes" for it — in any mode.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any

from django.conf import settings
from django.contrib.auth import login, logout
from django.contrib.auth.hashers import make_password
from django.db import transaction
from django.http import Http404, HttpRequest
from django.utils import timezone

from app.accounts import email_policy, mfa, passwords, throttle
from app.accounts.enums import AuthMode, Capability, ProvisioningState, ThrottleScope
from app.accounts.models import User
from app.audit.enums import SecurityEventType
from app.audit.services import record_security_event

MODEL_BACKEND = "django.contrib.auth.backends.ModelBackend"

#: Session keys. Namespaced, so nothing else can collide with them and a grep
#: finds every place the state is touched.
SESSION_USER = "local_auth:user"
SESSION_AUTHENTICATED_AT = "local_auth:authenticated_at"
SESSION_LAST_SEEN = "local_auth:last_seen"
SESSION_SECOND_FACTOR_AT = "local_auth:second_factor_at"
SESSION_STRONG_AT = "local_auth:strong_at"
SESSION_PENDING = "local_auth:pending"
SESSION_ENROLMENT_REQUIRED = "local_auth:enrolment_required"
SESSION_SEALED_ENROLMENT = "local_auth:sealed_enrolment"
SESSION_LINK = "local_auth:link"
SESSION_NEXT = "local_auth:next"

#: How long a correct password waits for its second factor.
PENDING_SECONDS = 300

#: How often the "last seen" stamp is rewritten. Every request would be a
#: session write per page; once a minute is precise enough for an idle limit
#: measured in tens of minutes.
LAST_SEEN_RESOLUTION = timedelta(seconds=60)

#: The one sentence every refused sign-in shows (docs/adr/0145 §6).
SIGN_IN_REFUSED = "E-posti aadress või parool on vale."
SECOND_FACTOR_REFUSED = "Kood ei sobinud. Proovi uuesti."


def person(request: HttpRequest) -> User:
    """The signed-in account, as a `User`, for views already behind the sign-in gate.

    Every caller sits behind `signed_in_here` or `may_administer_accounts`, so
    the refusal below is unreachable in practice — and a 404 rather than an
    assertion if a future caller forgets, because an assertion is stripped
    under `python -O` and would let the view continue with an anonymous user.
    """
    user = getattr(request, "user", None)
    if not isinstance(user, User):
        raise Http404("Lehte ei leitud.")
    return user


def is_local_password() -> bool:
    from app.accounts.shared_gate import current_mode

    return current_mode() == AuthMode.LOCAL_PASSWORD


def authenticated_via() -> str:
    return "LOCAL_PASSWORD"


# -- who must use a second factor -----------------------------------------------


def holds_administrative_capability(user: Any) -> bool:
    from app.core.authorization import has_capability

    return has_capability(user, Capability.MANAGE_ACCOUNTS)


def second_factor_mandatory(user: Any) -> bool:
    """Whether this person may not use the application without a second factor.

    Always for anybody who may administer accounts — that is not configurable,
    and turning the all-users policy off can never turn it off. For everybody
    else, exactly when `LOCAL_AUTH_MFA_REQUIRED_FOR_ALL` says so (docs/adr/0145
    §8).
    """
    if holds_administrative_capability(user):
        return True
    return bool(getattr(settings, "LOCAL_AUTH_MFA_REQUIRED_FOR_ALL", False))


def must_enrol(user: Any) -> bool:
    return second_factor_mandatory(user) and not mfa.has_second_factor(user)


# -- signing in -----------------------------------------------------------------


@dataclass(frozen=True)
class SignInOutcome:
    #: "signed_in", "second_factor", "refused" or "locked".
    status: str
    wait: int = 0


def _refusal_detail(email: str, reason: str, **extra: Any) -> dict[str, Any]:
    detail: dict[str, Any] = {"path": "local_password", "reason": reason, **extra}
    detail["email"] = (
        email_policy.normalise(email)
        if email_policy.looks_like_an_address(email)
        else "(malformed)"
    )
    return detail


def _client(request: HttpRequest) -> dict[str, Any]:
    return {
        "ip_address": request.META.get("REMOTE_ADDR"),
        "user_agent": request.META.get("HTTP_USER_AGENT", ""),
    }


def _ineligibility(user: User | None) -> str:
    """Why a correct password still does not sign this account in, or ""."""
    if user is None:
        return "unknown_account"
    if user.provisioning_state != ProvisioningState.ACTIVATED:
        return "not_activated"
    if not user.is_active:
        return "inactive"
    if user.local_password_set_at is None:
        return "no_local_password"
    if user.is_synthetic and settings.REAL_DATA_ALLOWED:
        # A synthetic account belongs to a rehearsal world. It must never be a
        # way into the real one, whatever password it carries.
        return "synthetic_account"
    return ""


def attempt_sign_in(request: HttpRequest, *, email: str, password: str) -> SignInOutcome:
    """Spend one password attempt: throttle, verify, count — as one step.

    The account is looked up by its normalised address, and an address that
    belongs to nobody costs the same Argon2 hash as one that does, so the time
    a refusal takes does not say whether somebody works here.
    """
    address = email_policy.normalise(email)
    counters = throttle.sign_in_counters(request, address)
    raw = passwords.normalise(password)
    with transaction.atomic(), throttle.holding(counters) as held:
        wait = held.wait()
        if wait:
            record_security_event(
                event_type=SecurityEventType.AUTHENTICATION_FAILED,
                succeeded=False,
                detail=_refusal_detail(email, "locked_out"),
                **_client(request),
            )
            return SignInOutcome("locked", wait=wait)

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
            wait = held.fail()
            record_security_event(
                event_type=SecurityEventType.AUTHENTICATION_FAILED,
                succeeded=False,
                subject=user,
                detail=_refusal_detail(email, reason),
                **_client(request),
            )
            return SignInOutcome("locked" if wait else "refused", wait=wait)
        held.succeed()

    if user is None:  # pragma: no cover - every refusal above has returned
        return SignInOutcome("refused")
    if mfa.has_second_factor(user):
        _begin_pending(request, user)
        return SignInOutcome("second_factor")
    complete_sign_in(request, user, second_factor=None)
    return SignInOutcome("signed_in")


def _begin_pending(request: HttpRequest, user: User) -> None:
    """A correct password, waiting for its second factor. Not a sign-in."""
    next_path = request.session.get(SESSION_NEXT)
    request.session.cycle_key()
    request.session[SESSION_PENDING] = {"user": str(user.pk), "at": timezone.now().isoformat()}
    if next_path:
        request.session[SESSION_NEXT] = next_path


def pending_user(request: HttpRequest) -> User | None:
    """The account whose second factor this session is waiting for, if still waiting."""
    pending = request.session.get(SESSION_PENDING)
    if not isinstance(pending, dict):
        return None
    try:
        started = datetime.fromisoformat(str(pending.get("at", "")))
    except ValueError:
        return None
    if timezone.is_naive(started) or timezone.now() - started > timedelta(seconds=PENDING_SECONDS):
        request.session.pop(SESSION_PENDING, None)
        return None
    user = (
        User.objects.filter(pk=str(pending.get("user") or "")).first()
        if pending.get("user")
        else None
    )
    if user is None or _ineligibility(user):
        request.session.pop(SESSION_PENDING, None)
        return None
    return user


def verify_second_factor(request: HttpRequest, *, code: str, kind: str) -> SignInOutcome:
    """Finish a pending sign-in with a TOTP code or a recovery code."""
    user = pending_user(request)
    if user is None:
        return SignInOutcome("refused")
    counters = [throttle.user_counter(ThrottleScope.SECOND_FACTOR, user.pk)]
    with transaction.atomic(), throttle.holding(counters) as held:
        wait = held.wait()
        if wait:
            record_security_event(
                event_type=SecurityEventType.AUTHENTICATION_FAILED,
                succeeded=False,
                subject=user,
                detail={"path": "local_password", "stage": "second_factor", "reason": "locked_out"},
                **_client(request),
            )
            return SignInOutcome("locked", wait=wait)
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
                    "path": "local_password",
                    "stage": "second_factor",
                    "method": kind,
                    "reason": "bad_code",
                },
                **_client(request),
            )
            return SignInOutcome("locked" if wait else "refused", wait=wait)
        held.succeed()

    if kind == "recovery":
        record_security_event(
            event_type=SecurityEventType.MFA_RECOVERY_CODE_USED,
            actor=user,
            subject=user,
            detail={"remaining": mfa.remaining_recovery_codes(user)},
            **_client(request),
        )
    request.session.pop(SESSION_PENDING, None)
    complete_sign_in(request, user, second_factor=kind)
    return SignInOutcome("signed_in")


def complete_sign_in(request: HttpRequest, user: User, *, second_factor: str | None) -> None:
    """Sign the person in, with the session recording how.

    `login` rotates the session key, which is what stops an identifier planted
    before sign-in from becoming an authenticated one after it.
    """
    next_path = request.session.get(SESSION_NEXT)
    login(request, user, backend=MODEL_BACKEND)
    now = timezone.now()
    stamp = now.isoformat()
    request.session[SESSION_USER] = str(user.pk)
    request.session[SESSION_AUTHENTICATED_AT] = stamp
    request.session[SESSION_LAST_SEEN] = stamp
    request.session[SESSION_STRONG_AT] = stamp
    if second_factor:
        request.session[SESSION_SECOND_FACTOR_AT] = stamp
    if must_enrol(user):
        request.session[SESSION_ENROLMENT_REQUIRED] = True
    if next_path:
        request.session[SESSION_NEXT] = next_path
    request.session.set_expiry(int(settings.LOCAL_AUTH_SESSION_ABSOLUTE_SECONDS))
    User.objects.filter(pk=user.pk).update(last_authenticated_at=now)
    record_security_event(
        event_type=SecurityEventType.AUTHENTICATION_SUCCEEDED,
        actor=user,
        detail={
            "path": "local_password",
            "authenticated_via": authenticated_via(),
            "second_factor": second_factor or "none",
        },
        **_client(request),
    )


# -- the session ----------------------------------------------------------------


def _stamp(request: HttpRequest, key: str) -> datetime | None:
    raw = request.session.get(key)
    if not raw:
        return None
    try:
        moment = datetime.fromisoformat(str(raw))
    except ValueError:
        return None
    return None if timezone.is_naive(moment) else moment


def is_local_session(request: HttpRequest) -> bool:
    """Whether this session was signed in here, as the user it now carries."""
    user = getattr(request, "user", None)
    if user is None or not user.is_authenticated:
        return False
    return request.session.get(SESSION_USER) == str(user.pk)


def expiry_reason(request: HttpRequest) -> str:
    """Why this local session must end now, or "" if it may go on."""
    now = timezone.now()
    authenticated = _stamp(request, SESSION_AUTHENTICATED_AT)
    if authenticated is None or now - authenticated > timedelta(
        seconds=int(settings.LOCAL_AUTH_SESSION_ABSOLUTE_SECONDS)
    ):
        return "absolute_timeout"
    seen = _stamp(request, SESSION_LAST_SEEN) or authenticated
    if now - seen > timedelta(seconds=int(settings.LOCAL_AUTH_SESSION_IDLE_SECONDS)):
        return "idle_timeout"
    return ""


def touch(request: HttpRequest) -> None:
    seen = _stamp(request, SESSION_LAST_SEEN)
    now = timezone.now()
    if seen is None or now - seen >= LAST_SEEN_RESOLUTION:
        request.session[SESSION_LAST_SEEN] = now.isoformat()


def end_session(request: HttpRequest, *, reason: str) -> None:
    """Sign out for the system's reasons — expiry, a disabled account, a mode change."""
    user = getattr(request, "user", None)
    actor = user if user is not None and user.is_authenticated else None
    record_security_event(
        event_type=SecurityEventType.SESSION_ENDED,
        actor=actor,
        detail={"path": "local_password", "reason": reason},
        **_client(request),
    )
    logout(request)


def has_second_factor_session(request: HttpRequest) -> bool:
    return is_local_session(request) and _stamp(request, SESSION_SECOND_FACTOR_AT) is not None


def needs_reauthentication(request: HttpRequest) -> bool:
    """Whether a critical change must first ask the person to prove it is them again."""
    strong = _stamp(request, SESSION_STRONG_AT)
    if strong is None:
        return True
    return timezone.now() - strong > timedelta(seconds=int(settings.LOCAL_AUTH_REAUTH_SECONDS))


def enrolment_pending(request: HttpRequest) -> bool:
    return bool(request.session.get(SESSION_ENROLMENT_REQUIRED))


def may_administer_accounts(request: HttpRequest) -> bool:
    """The one gate in front of every account-administration page and action.

    Five conditions, all required, every one of them about *this request*:

    1. the deployment is in `local_password` mode — so under the shared gate
       the answer is "no" for everybody, whichever persona is selected;
    2. somebody is signed in, and this session was signed in *here*;
    3. that session proved a second factor;
    4. it is not waiting for a mandatory second factor to be enrolled;
    5. the account holds `accounts.manage`, resolved by the central
       authorization service.

    The services under it ask (5) again of the actor they are handed, so a
    view that forgot this gate would still be refused below it.
    """
    if not is_local_password() or not is_local_session(request):
        return False
    if not has_second_factor_session(request) or enrolment_pending(request):
        return False
    return holds_administrative_capability(request.user)


@dataclass(frozen=True)
class ReauthOutcome:
    accepted: bool
    wait: int = 0


def reauthenticate(request: HttpRequest, *, password: str, code: str) -> ReauthOutcome:
    """Prove again that the person at the keyboard is the account's owner."""
    user = person(request)
    counters = [throttle.user_counter(ThrottleScope.REAUTHENTICATION, user.pk)]
    with transaction.atomic(), throttle.holding(counters) as held:
        wait = held.wait()
        if wait:
            return ReauthOutcome(False, wait=wait)
        accepted = user.check_password(passwords.normalise(password))
        if accepted and mfa.has_second_factor(user):
            accepted = mfa.verify_totp(user, code)
        if not accepted:
            wait = held.fail()
            record_security_event(
                event_type=SecurityEventType.AUTHENTICATION_FAILED,
                succeeded=False,
                subject=user,
                detail={"path": "local_password", "stage": "reauthentication"},
                **_client(request),
            )
            return ReauthOutcome(False, wait=wait)
        held.succeed()
    stamp = timezone.now().isoformat()
    request.session[SESSION_STRONG_AT] = stamp
    if mfa.has_second_factor(user):
        request.session[SESSION_SECOND_FACTOR_AT] = stamp
    record_security_event(
        event_type=SecurityEventType.REAUTHENTICATED,
        actor=user,
        detail={"path": "local_password"},
        **_client(request),
    )
    return ReauthOutcome(True)


def mark_second_factor_enrolled(request: HttpRequest) -> None:
    """The person has just proved a second factor by enrolling one."""
    stamp = timezone.now().isoformat()
    request.session[SESSION_SECOND_FACTOR_AT] = stamp
    request.session[SESSION_STRONG_AT] = stamp
    request.session.pop(SESSION_ENROLMENT_REQUIRED, None)
