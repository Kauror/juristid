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
from app.accounts.models import AccountCredentialToken, User
from app.audit.enums import SecurityEventType
from app.audit.services import record_security_event

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
def set_password_with_link(*, proof: str, purpose: str, password: str) -> User | None:
    """Spend a one-time link on a new password. ``None`` if the link is no good.

    Validation first, spending second, all in one transaction: a password the
    validators refuse leaves the link usable for the next try, and a link
    spent is a password set — never one without the other.
    """
    preview = tokens.peek(proof, purpose=purpose)
    holder = link_holder(preview)
    if holder is None:
        return None
    check_new_password(password, holder)

    record = tokens.consume(proof, purpose=purpose)
    user = link_holder(record)
    if user is None:
        return None
    user = User.objects.select_for_update().get(pk=user.pk)
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
        ip_address=request.META.get("REMOTE_ADDR"),
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
    if throttle.is_locked(counters):
        detail["outcome"] = "throttled"
        record_security_event(
            event_type=SecurityEventType.PASSWORD_RESET_REQUESTED,
            succeeded=False,
            ip_address=request.META.get("REMOTE_ADDR"),
            detail=detail,
        )
        return
    throttle.charge(counters)

    user = User.objects.filter(upn=address).first() if address else None
    eligible = (
        user is not None
        and user.provisioning_state == ProvisioningState.ACTIVATED
        and user.is_active
        and user.has_local_password
        and not (user.is_synthetic and settings.REAL_DATA_ALLOWED)
    )
    if not eligible or user is None:
        detail["outcome"] = "no_eligible_account"
        record_security_event(
            event_type=SecurityEventType.PASSWORD_RESET_REQUESTED,
            succeeded=False,
            subject=user,
            ip_address=request.META.get("REMOTE_ADDR"),
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
        ip_address=request.META.get("REMOTE_ADDR"),
        detail=detail,
    )
    user_id = user.pk
    after_the_response(lambda: _send_reset_link(user_id))


def _send_reset_link(user_id: uuid.UUID) -> None:
    with transaction.atomic():
        user = User.objects.filter(pk=user_id).first()
        if user is None:  # pragma: no cover - accounts are never deleted
            return
        issued = tokens.issue(user=user, purpose=CredentialTokenPurpose.PASSWORD_RESET)
        delivery = mail.send_credential_link(
            user=user,
            purpose=CredentialTokenPurpose.PASSWORD_RESET,
            token=issued.token,
            actor=None,
            expires_at=issued.record.expires_at,
        )
        if not delivery.delivered:
            tokens.invalidate_outstanding(
                user=user,
                purpose=CredentialTokenPurpose.PASSWORD_RESET,
                reason=f"not_delivered:{delivery.reason}",
            )


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
