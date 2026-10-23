"""A person's own credentials: setting, changing and recovering them.

The administrator's half of the lifecycle is `app.accounts.administration`.
This is the account owner's half, and its rule is the mirror image: **only the
person sets their password.** An administrator can send a one-time link; the
person follows it and chooses. Nobody — no administrator, no operator, no
e-mail — ever sees, sets or recovers somebody else's password (docs/adr/0145 §7).
"""

from __future__ import annotations

import logging
import threading
import uuid
from collections.abc import Callable
from dataclasses import dataclass

from django.conf import settings
from django.contrib.auth import update_session_auth_hash
from django.contrib.auth.password_validation import validate_password
from django.db import connection, transaction
from django.db.models import F
from django.http import HttpRequest
from django.utils import timezone

from app.accounts import email_policy, mail, mfa, passwords, throttle, tokens
from app.accounts.enums import CredentialTokenPurpose, ProvisioningState, ThrottleScope
from app.accounts.models import AccountCredentialToken, RecoveryCode, TotpDevice, User
from app.audit import provenance
from app.audit.enums import SecurityEventType
from app.audit.services import record_security_event
from app.core.client_ip import client_ip
from app.core.errors import DomainError

logger = logging.getLogger(__name__)


def _bump_epoch(user: User) -> None:
    User.objects.filter(pk=user.pk).update(security_epoch=F("security_epoch") + 1)
    user.refresh_from_db(fields=["security_epoch"])


def link_holder(record: AccountCredentialToken | None) -> User | None:
    """Whom a usable link may set a password for — or nobody.

    An activation link works for an account that was invited, or for an
    existing account setting its first password here. A reset link works only
    for an active account that already has one. Neither ever works for a
    switched-off account, nor — on a real-data deployment — for a synthetic one.
    """
    if record is None:
        return None
    user = record.user
    if user.is_synthetic and settings.REAL_DATA_ALLOWED:
        return None
    if record.purpose == CredentialTokenPurpose.ACTIVATION:
        if user.provisioning_state == ProvisioningState.INVITED:
            return user
        if user.provisioning_state == ProvisioningState.ACTIVATED and user.is_active:
            return user
        return None
    if (
        user.provisioning_state == ProvisioningState.ACTIVATED
        and user.is_active
        and user.has_local_password
    ):
        return user
    return None


def check_new_password(password: str, user: User) -> None:
    """Raise `ValidationError` with every reason this password is not acceptable."""
    validate_password(passwords.normalise(password), user=user)


@transaction.atomic
def set_password_with_link(
    *,
    proof: str,
    purpose: str,
    password: str,
    holder_may: Callable[[User], bool] | None = None,
) -> User | None:
    """Spend a one-time link on a new password. ``None`` if the link is no good.

    Validation first, spending second, all in one transaction: a password the
    validators refuse leaves the link usable for the next try, and a link
    spent is a password set — never one without the other.

    ``holder_may``, when given, is asked again of the holder **under its row
    lock, inside this transaction** — under the shared gate, whether the
    holder may still administer (docs/adr/0186). A power withdrawn between the
    page and the save refuses the save and leaves the link unspent.
    """
    preview = tokens.peek(proof, purpose=purpose)
    holder = link_holder(preview)
    if holder is None:
        return None
    check_new_password(password, holder)

    if holder_may is not None:
        locked = User.objects.select_for_update().get(pk=holder.pk)
        if not holder_may(locked):
            return None
    record = tokens.consume(proof, purpose=purpose)
    user = link_holder(record)
    if user is None:
        return None
    user = User.objects.select_for_update().get(pk=user.pk)
    if holder_may is not None and not holder_may(user):
        transaction.set_rollback(True)
        return None
    now = timezone.now()
    user.set_password(passwords.normalise(password))
    user.local_password_set_at = now
    fields = ["password", "local_password_set_at", "updated_at"]
    activated = False
    if user.provisioning_state == ProvisioningState.INVITED:
        user.provisioning_state = ProvisioningState.ACTIVATED
        user.is_active = True
        fields += ["provisioning_state", "is_active"]
        activated = True
    user.save(update_fields=fields)
    tokens.invalidate_outstanding(user=user, reason="password_set")
    _bump_epoch(user)
    record_security_event(
        event_type=SecurityEventType.PASSWORD_SET,
        actor=user,
        subject=user,
        detail={"purpose": str(purpose)},
    )
    if activated:
        record_security_event(
            event_type=SecurityEventType.ACCOUNT_ACTIVATED, actor=user, subject=user
        )
    return user


@dataclass(frozen=True)
class ChangeOutcome:
    #: "changed", "wrong_password" or "locked".
    status: str
    wait: int = 0


@transaction.atomic
def change_password(request: HttpRequest, *, user: User, current: str, new: str) -> ChangeOutcome:
    """Change one's own password, proving the current one first.

    Every other session the account has ends; this one continues, rotated.
    Every outstanding link is invalidated — a reset link in an inbox is not a
    second way in after the password it would have replaced has changed.
    """
    counters = [throttle.user_counter(ThrottleScope.PASSWORD_CHANGE, user.pk)]
    with throttle.holding(counters) as held:
        wait = held.wait()
        if wait:
            return ChangeOutcome("locked", wait=wait)
        if not user.check_password(passwords.normalise(current)):
            wait = held.fail()
            record_security_event(
                event_type=SecurityEventType.AUTHENTICATION_FAILED,
                succeeded=False,
                subject=user,
                detail={"path": "local_password", "stage": "password_change"},
            )
            return ChangeOutcome("locked" if wait else "wrong_password", wait=wait)
        held.succeed()
    check_new_password(new, user)
    user.set_password(passwords.normalise(new))
    user.local_password_set_at = timezone.now()
    user.save(update_fields=["password", "local_password_set_at", "updated_at"])
    tokens.invalidate_outstanding(user=user, reason="password_changed")
    _bump_epoch(user)
    update_session_auth_hash(request, user)
    record_security_event(
        event_type=SecurityEventType.PASSWORD_CHANGED,
        actor=user,
        subject=user,
        ip_address=client_ip(request),
    )
    return ChangeOutcome("changed")


def request_password_reset(request: HttpRequest, *, email: str) -> None:
    """«Unustasid parooli?» — always the same answer, whatever the address.

    A link is issued only for an active account that already has a password
    here. Every request is counted, per address and per network, and a request
    over the limit is quietly not acted on: the page still says the same thing,
    because a different page would be an oracle for which addresses are real.
    An account that has never set a password here gets nothing — its first
    password comes from an administrator's link, by decision, not from a form
    anybody can fill in.
    """
    address = email_policy.normalise(email)
    counters = [
        throttle.Counter(ThrottleScope.RESET_REQUEST_ACCOUNT, throttle.digest("reset", address)),
        throttle.network_counter(ThrottleScope.RESET_REQUEST_NETWORK, request),
    ]
    detail: dict[str, object] = {
        "email": address if email_policy.looks_like_an_address(email) else "(malformed)"
    }
    # The unlocked read answers most requests over the limit cheaply; the
    # charge decides on the locked rows, so requests that passed the read
    # together cannot all be acted on (AUD-052 AU-3).
    if throttle.is_locked(counters) or throttle.charge(counters).already_locked:
        detail["outcome"] = "throttled"
        record_security_event(
            event_type=SecurityEventType.PASSWORD_RESET_REQUESTED,
            succeeded=False,
            ip_address=client_ip(request),
            detail=detail,
        )
        return

    user = User.objects.filter(upn=address).first() if address else None
    if not _reset_eligible(user) or user is None:
        detail["outcome"] = "no_eligible_account"
        record_security_event(
            event_type=SecurityEventType.PASSWORD_RESET_REQUESTED,
            succeeded=False,
            subject=user,
            ip_address=client_ip(request),
            detail=detail,
        )
        return

    # The same synchronous work as the refusal above — one audit row — and the
    # link issued and mailed after the response. Sending mail takes the time of
    # an SMTP conversation, and a page that took that long only for real
    # addresses would say which addresses are real (docs/adr/0145 §7).
    detail["outcome"] = "queued"
    record_security_event(
        event_type=SecurityEventType.PASSWORD_RESET_REQUESTED,
        subject=user,
        ip_address=client_ip(request),
        detail=detail,
    )
    user_id = user.pk
    stamp = tokens.credential_stamp(user)
    correlation = provenance.current_correlation_id()
    after_the_response(lambda: _send_reset_link(user_id, stamp, correlation))


def _reset_eligible(user: User | None) -> bool:
    """Whether a reset link may go to this account now — asked twice, see below."""
    return (
        user is not None
        and user.provisioning_state == ProvisioningState.ACTIVATED
        and user.is_active
        and user.has_local_password
        and not (user.is_synthetic and settings.REAL_DATA_ALLOWED)
    )


def _send_reset_link(user_id: uuid.UUID, stamp: str, correlation: uuid.UUID | None = None) -> None:
    """Issue and mail the link the request asked for — if it is still due.

    This runs after the response, possibly much later: a worker can be slow or
    paused. So the request's decision is not trusted here. Under the account's
    row lock the account must **still** be eligible and its credentials
    must **still** be the ones the request saw (`tokens.credential_stamp`); a
    password changed, a new login address or an account switched off in
    between means no link is minted
    (docs/adr/0180 §5). The audit rows it writes carry the request's
    correlation id, and say they are the system's.

    **The lock covers the decision and the issue, not the message.** Sending
    takes an SMTP conversation — up to the transport's timeout — and holding
    the account's row for it would hold up that person's sign-in and every
    other write to their account for as long. So the link is issued and
    committed first and mailed afterwards, outside any transaction. Nothing is
    lost by that: the link is bound to the credentials it was issued under, so
    a password change committed while the message is on its way kills it
    anyway. A message that could not be sent ends its own link — that one
    link, not a newer one issued meanwhile.
    """
    with provenance.correlated(correlation):
        with transaction.atomic():
            user = User.objects.select_for_update().filter(pk=user_id).first()
            if not _reset_eligible(user) or user is None or tokens.credential_stamp(user) != stamp:
                record_security_event(
                    event_type=SecurityEventType.PASSWORD_RESET_REQUESTED,
                    succeeded=False,
                    subject=user,
                    detail={"outcome": "no_longer_due"},
                )
                return
            try:
                issued = tokens.issue(
                    user=user, purpose=CredentialTokenPurpose.PASSWORD_RESET, expected_stamp=stamp
                )
            except tokens.StaleIssuance:  # pragma: no cover - the lock above already decided
                return
        delivery = mail.send_credential_link(
            user=user,
            purpose=CredentialTokenPurpose.PASSWORD_RESET,
            token=issued.token,
            actor=None,
            expires_at=issued.record.expires_at,
        )
        if not delivery.delivered:
            tokens.invalidate_link(issued.record, reason=f"not_delivered:{delivery.reason}")


def after_the_response(work: Callable[[], None]) -> None:
    """Run ``work`` once this request's transaction has committed, off the request.

    A daemon thread with its own database connection, closed when it is done.
    `ACCOUNT_EMAIL_IN_BACKGROUND=False` runs it inline instead — the test
    suite's setting, so the work is asserted where it happens. A message lost
    to a worker restart is a person asking again, which the page invites.
    """
    if not getattr(settings, "ACCOUNT_EMAIL_IN_BACKGROUND", True):
        work()
        return

    def run() -> None:
        try:
            work()
        except Exception:  # pragma: no cover - logged without content, never raised into nothing
            logger.warning("A password-reset message could not be prepared.")
        finally:
            connection.close()

    transaction.on_commit(
        lambda: threading.Thread(target=run, name="juristid-account-mail", daemon=True).start()
    )


# -- the second factor, by its owner ----------------------------------------------------


@transaction.atomic
def enrol_second_factor(
    request: HttpRequest, *, user: User, sealed: str, code: str
) -> list[str] | None:
    """Confirm a new authenticator and hand back fresh recovery codes, once.

    ``None`` when the code does not prove the phone has the secret. Replacing an
    existing authenticator is the same call; the old one stops working in the
    same write that makes the new one count.
    """
    replacing = mfa.has_second_factor(user)
    if not mfa.confirm_enrolment(user, sealed=sealed, code=code):
        record_security_event(
            event_type=SecurityEventType.AUTHENTICATION_FAILED,
            succeeded=False,
            subject=user,
            detail={"path": "local_password", "stage": "second_factor_enrolment"},
        )
        return None
    codes = mfa.generate_recovery_codes(user)
    _bump_epoch(user)
    update_session_auth_hash(request, user)
    record_security_event(
        event_type=SecurityEventType.MFA_ENROLLED,
        actor=user,
        subject=user,
        detail={"method": "totp", "replaced_existing": replacing},
    )
    return codes


@transaction.atomic
def remove_own_second_factor(request: HttpRequest, *, user: User) -> None:
    mfa.remove_second_factor(user)
    _bump_epoch(user)
    update_session_auth_hash(request, user)
    record_security_event(
        event_type=SecurityEventType.MFA_REMOVED,
        actor=user,
        subject=user,
        detail={"by": "self"},
    )


@transaction.atomic
def regenerate_recovery_codes(*, user: User) -> list[str]:
    codes = mfa.generate_recovery_codes(user)
    record_security_event(
        event_type=SecurityEventType.MFA_RECOVERY_CODES_REGENERATED,
        actor=user,
        subject=user,
        detail={"count": len(codes)},
    )
    return codes


# -- the operator's reset, for a lost or changed MFA key ------------------------------

SECOND_FACTOR_RESET_NEEDS_A_NOTE = (
    "Märkus on kohustuslik: kes seda käivitab ja miks. See jääb turvalogisse. Midagi ei muudetud."
)
SECOND_FACTOR_RESET_UNKNOWN_ACCOUNT = "Sellise aadressiga kontot ei ole. Midagi ei muudetud."
SECOND_FACTOR_RESET_NOTHING_TO_RESET = (
    "Sellel kontol ei ole autentimisrakendust ega taastekoode. Midagi ei muudetud."
)


class SecondFactorResetRefused(DomainError):
    """The second factor was not reset, and the message says why."""


@dataclass(frozen=True)
class SecondFactorReset:
    user: User
    #: Whether the device's secret was still readable under the current key.
    #: False is the lost-key case; True is a lost phone with nobody left who
    #: could reset it from Haldus → Kasutajad.
    secret_was_readable: bool | None
    recovery_codes_left: int
    #: False for a dry run: nothing was written.
    applied: bool


def operator_reset_second_factor(
    *, email: str, note: str, dry_run: bool = False
) -> SecondFactorReset:
    """Retire somebody's authenticator and recovery codes, by an operator, audited.

    The way out when the MFA key is lost or changed and nothing inside the
    application can help: every confirmed device still counts as a second
    factor (`mfa.has_second_factor`), so nobody is asked to enrol, and none of
    them can ever produce an accepted code (docs/LOCAL_AUTH_ACTIVATION_RUNBOOK.md
    §15). It removes exactly what an administrator's «Lähtesta teine tegur»
    removes — the device and the recovery codes, which enrolment replaces with a
    new set anyway — ends the person's sessions, and records who ran it and why.

    **The second factor stays mandatory.** Nothing here changes who must have
    one: for whoever must, `local_auth.must_enrol` answers "yes" again at the
    next sign-in and they reach nothing but the enrolment page until a new
    device is confirmed under the current key.

    Works in every mode, because a key can be lost after a rollback too, but
    acts only on an account that has something to reset, and never without a
    note. ``dry_run`` reports and writes nothing.
    """
    if not (note or "").strip():
        raise SecondFactorResetRefused(SECOND_FACTOR_RESET_NEEDS_A_NOTE)
    address = email_policy.normalise(email)
    with transaction.atomic():
        user = User.objects.select_for_update().filter(upn__iexact=address).first()
        if user is None:
            raise SecondFactorResetRefused(SECOND_FACTOR_RESET_UNKNOWN_ACCOUNT)
        device = mfa.confirmed_device(user)
        readable = mfa.secret_is_readable(device) if device is not None else None
        codes_left = mfa.remaining_recovery_codes(user)
        # An unconfirmed device counts too: an enrolment abandoned under the
        # old key is as dead as a confirmed one.
        has_anything = (
            TotpDevice.objects.filter(user=user).exists()
            or RecoveryCode.objects.filter(user=user).exists()
        )
        if not has_anything:
            raise SecondFactorResetRefused(SECOND_FACTOR_RESET_NOTHING_TO_RESET)
        if dry_run:
            return SecondFactorReset(user, readable, codes_left, applied=False)
        mfa.remove_second_factor(user)
        _bump_epoch(user)
        record_security_event(
            event_type=SecurityEventType.MFA_REMOVED,
            actor=None,
            subject=user,
            detail={
                "by": "operator",
                "via": "manage.py reset_second_factor",
                "note": note.strip()[:500],
                "secret_was_readable": readable,
                "recovery_codes_left": codes_left,
            },
        )
    return SecondFactorReset(user, readable, codes_left, applied=True)
