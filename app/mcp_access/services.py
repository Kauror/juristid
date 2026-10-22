"""Grants, enrolment, authorization and tokens: every write this app makes (docs/adr/0185 §3–§4).

The views and the operator command call these; nothing else writes the tables.
Each decision that matters is taken under a row lock, inside one transaction,
and recorded as an `McpAccessEvent` in the same transaction — a refusal too,
which is why refusals are *returned* from inside the transaction rather than
raised through it: an exception would roll back the very counter or audit row
the refusal exists to leave behind.

Three questions are asked again at every step — granting, enrolling,
authorizing, issuing and refreshing a token — because any of them can change in
between: is the grant ACTIVE, is the account active, and does
`may_read_business_content` hold for it (docs/adr/0181).
"""

from __future__ import annotations

import getpass
import socket
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Any

from django.conf import settings
from django.db import transaction
from django.db.models import Count, Max, Q
from django.urls import reverse
from django.utils import timezone

from app.accounts.models import User
from app.core.authorization import ROLES_WITH_BUSINESS_READ, acting_role, may_read_business_content
from app.mcp_access import config, credentials
from app.mcp_access.models import (
    GrantStatus,
    McpAccessEvent,
    McpAccessEventType,
    McpAccessGrant,
    McpAuthenticator,
    McpAuthorizationCode,
    McpEnrolmentLink,
    McpToken,
    TokenKind,
)

#: Wrong authenticator codes in a row before the grant locks, and for how long.
MAX_FAILED_CODES = 5
LOCK_SECONDS = 15 * 60

WEB = "web"


# -- the audit trail -----------------------------------------------------------


def record(
    event_type: str,
    *,
    succeeded: bool = True,
    user: User | None = None,
    grant: McpAccessGrant | None = None,
    client_id: str = "",
    ip_address: str | None = None,
    provenance: str = WEB,
    **detail: Any,
) -> McpAccessEvent:
    """Append one event. ``detail`` holds reasons and ids — never a code, token or secret."""
    return McpAccessEvent.objects.create(
        event_type=event_type,
        succeeded=succeeded,
        user=user,
        grant=grant,
        client_id=(client_id or "")[:500],
        ip_address=ip_address,
        provenance=(provenance or "")[:300],
        detail=detail,
    )


def operator_provenance(command: str) -> str:
    """Who ran an operator command, and where: there is no request user to name."""
    try:
        who = getpass.getuser()
    except Exception:  # pragma: no cover - no login name in a bare container
        who = "unknown"
    return f"manage.py mcp_access {command} (os-user={who}, host={socket.gethostname()})"[:300]


# -- who may hold a grant ---------------------------------------------------------


class GrantRefused(Exception):
    """An operator request that cannot be carried out; the message is safe to print."""


def grant_refusal(user: User) -> str:
    """Why this account may not be granted MCP access, or "" if it may.

    Stricter than `may_read_business_content`: a business *role* is required,
    so an administrator holding a temporary break-glass grant is not given a
    standing AI connection on the strength of it. Synthetic rehearsal accounts
    are refused wherever real data is allowed.
    """
    if not user.is_active:
        return "konto ei ole aktiivne"
    if acting_role(user) not in ROLES_WITH_BUSINESS_READ:
        return "kontol ei ole sisu lugemise rolli (SPECIALIST, DEPARTMENT_HEAD või READER)"
    if not may_read_business_content(user):  # pragma: no cover - implied by the role today
        return "konto ei tohi sisu lugeda"
    if user.is_synthetic and getattr(settings, "REAL_DATA_ALLOWED", False):
        return "sünteetilisele kontole ei anta päris andmetega keskkonnas ligipääsu"
    return ""


def may_connect(user: User | None, grant: McpAccessGrant | None) -> bool:
    """The three questions every step asks again: grant ACTIVE, account active, may read."""
    if user is None or grant is None:
        return False
    return (
        grant.status == GrantStatus.ACTIVE
        and grant.user_id == user.pk
        and bool(user.is_active)
        and may_read_business_content(user)
    )


def open_grant(user: User) -> McpAccessGrant | None:
    """This account's grant that is not revoked, if it has one."""
    return McpAccessGrant.objects.filter(user=user).exclude(status=GrantStatus.REVOKED).first()


def active_grant(user: User) -> McpAccessGrant | None:
    return McpAccessGrant.objects.filter(user=user, status=GrantStatus.ACTIVE).first()


# -- operator actions ------------------------------------------------------------


@dataclass(frozen=True)
class IssuedEnrolment:
    grant: McpAccessGrant
    url: str
    expires_at: datetime


def enrolment_url(token: str) -> str:
    return f"{config.issuer()}{reverse('mcp_access:enrol')}?kood={token}"


def _lock_user(user: User) -> User:
    return User.objects.select_for_update().get(pk=user.pk)


def grant_access(*, user: User, provenance: str, created_by: User | None = None) -> IssuedEnrolment:
    """A PENDING grant and its first enrolment link. Raises `GrantRefused`.

    The refusal is raised after the transaction, so its audit row is kept.
    """
    with transaction.atomic():
        outcome = _grant_access(user=user, provenance=provenance, created_by=created_by)
    if isinstance(outcome, str):
        raise GrantRefused(outcome)
    return outcome


def _grant_access(*, user: User, provenance: str, created_by: User | None) -> IssuedEnrolment | str:
    locked = _lock_user(user)
    reason = grant_refusal(locked)
    if not reason and open_grant(locked) is not None:
        reason = "kontol on juba kehtiv või seadistamist ootav ligipääs"
    if reason:
        record(
            McpAccessEventType.GRANT_REFUSED,
            succeeded=False,
            user=locked,
            provenance=provenance,
            reason=reason,
        )
        return reason
    grant = McpAccessGrant.objects.create(
        user=locked, created_by=created_by, created_via=provenance[:300]
    )
    record(McpAccessEventType.GRANT_CREATED, user=locked, grant=grant, provenance=provenance)
    return _issue_link(grant, provenance=provenance)


@transaction.atomic
def issue_enrolment_link(*, user: User, provenance: str) -> IssuedEnrolment:
    """A new link for a PENDING grant; the outstanding one stops working."""
    locked = _lock_user(user)
    grant = (
        McpAccessGrant.objects.select_for_update()
        .filter(user=locked, status=GrantStatus.PENDING_ENROLMENT)
        .first()
    )
    if grant is None:
        raise GrantRefused("kontol ei ole seadistamist ootavat ligipääsu")
    reason = grant_refusal(locked)
    if reason:
        raise GrantRefused(reason)
    return _issue_link(grant, provenance=provenance)


def _issue_link(grant: McpAccessGrant, *, provenance: str) -> IssuedEnrolment:
    now = timezone.now()
    McpEnrolmentLink.objects.filter(
        grant=grant, used_at__isnull=True, invalidated_at__isnull=True
    ).update(invalidated_at=now, updated_at=now)
    link = credentials.new_link()
    expires_at = now + timedelta(seconds=int(settings.MCP_ENROLMENT_LINK_SECONDS))
    record_row = McpEnrolmentLink.objects.create(
        grant=grant,
        selector=link.selector,
        verifier_digest=link.verifier_digest,
        expires_at=expires_at,
        issued_via=provenance[:300],
    )
    record(
        McpAccessEventType.ENROLMENT_LINK_ISSUED,
        user=grant.user,
        grant=grant,
        provenance=provenance,
        link=str(record_row.pk),
    )
    return IssuedEnrolment(grant=grant, url=enrolment_url(link.token), expires_at=expires_at)


def _revoke_tokens(queryset: Any, *, reason: str, now: datetime) -> int:
    return int(
        queryset.filter(revoked_at__isnull=True).update(
            revoked_at=now, revoke_reason=reason[:64], updated_at=now
        )
    )


def _end_grant(grant: McpAccessGrant, *, reason: str, now: datetime) -> int:
    grant.status = GrantStatus.REVOKED
    grant.revoked_at = now
    grant.revoke_reason = reason[:300]
    grant.save(update_fields=["status", "revoked_at", "revoke_reason", "updated_at"])
    McpEnrolmentLink.objects.filter(
        grant=grant, used_at__isnull=True, invalidated_at__isnull=True
    ).update(invalidated_at=now, updated_at=now)
    McpAuthorizationCode.objects.filter(grant=grant, used_at__isnull=True).update(
        used_at=now, updated_at=now
    )
    return _revoke_tokens(McpToken.objects.filter(grant=grant), reason="grant_revoked", now=now)


@transaction.atomic
def revoke_grant(*, user: User, reason: str, provenance: str) -> int:
    """End this account's grant and every token, code and link it has. Tokens revoked."""
    if not reason.strip():
        raise GrantRefused("põhjus on kohustuslik")
    grant = (
        McpAccessGrant.objects.select_for_update()
        .filter(user=user)
        .exclude(status=GrantStatus.REVOKED)
        .first()
    )
    if grant is None:
        raise GrantRefused("kontol ei ole kehtivat ligipääsu")
    now = timezone.now()
    revoked = _end_grant(grant, reason=reason, now=now)
    record(
        McpAccessEventType.GRANT_REVOKED,
        user=user,
        grant=grant,
        provenance=provenance,
        reason=reason[:300],
        tokens_revoked=revoked,
    )
    return revoked


@transaction.atomic
def revoke_all(*, reason: str, provenance: str) -> tuple[int, int]:
    """The kill switch: every grant and every token. Returns (grants, tokens)."""
    if not reason.strip():
        raise GrantRefused("põhjus on kohustuslik")
    now = timezone.now()
    grants = list(
        McpAccessGrant.objects.select_for_update()
        .exclude(status=GrantStatus.REVOKED)
        .order_by("pk")
    )
    tokens = 0
    for grant in grants:
        revoked = _end_grant(grant, reason=reason, now=now)
        tokens += revoked
        record(
            McpAccessEventType.GRANT_REVOKED,
            user=grant.user,
            grant=grant,
            provenance=provenance,
            reason=reason[:300],
            tokens_revoked=revoked,
            revoke_all=True,
        )
    # Belt and braces: a token whose grant was already revoked is revoked too.
    tokens += _revoke_tokens(McpToken.objects.all(), reason="revoke_all", now=now)
    return len(grants), tokens


@transaction.atomic
def unlock(*, user: User, provenance: str) -> bool:
    grant = (
        McpAccessGrant.objects.select_for_update()
        .filter(user=user)
        .exclude(status=GrantStatus.REVOKED)
        .first()
    )
    if grant is None:
        raise GrantRefused("kontol ei ole kehtivat ligipääsu")
    was_locked = grant.locked_until is not None or grant.failed_attempts > 0
    grant.failed_attempts = 0
    grant.locked_until = None
    grant.save(update_fields=["failed_attempts", "locked_until", "updated_at"])
    record(McpAccessEventType.GRANT_UNLOCKED, user=user, grant=grant, provenance=provenance)
    return was_locked


@dataclass(frozen=True)
class GrantRow:
    upn: str
    status: str
    enrolled: bool
    active_tokens: int
    last_used: datetime | None
    locked_until: datetime | None


def grant_rows() -> list[GrantRow]:
    """Every grant, newest first, for `mcp_access list`. No secret, no digest."""
    now = timezone.now()
    rows = (
        McpAccessGrant.objects.select_related("user")
        .annotate(
            active_tokens=Count(
                "tokens",
                filter=Q(tokens__revoked_at__isnull=True, tokens__expires_at__gt=now),
            ),
            last_issue=Max("tokens__issued_at"),
            last_refresh=Max("tokens__last_used_at"),
            enrolled=Count("authenticator"),
        )
        .order_by("-created_at")
    )
    result = []
    for grant in rows:
        stamps = [stamp for stamp in (grant.last_issue, grant.last_refresh) if stamp is not None]
        result.append(
            GrantRow(
                upn=grant.user.upn,
                status=grant.status,
                enrolled=bool(grant.enrolled),
                active_tokens=int(grant.active_tokens),
                last_used=max(stamps) if stamps else None,
                locked_until=grant.locked_until if grant.is_locked_at(now) else None,
            )
        )
    return result


# -- enrolment ------------------------------------------------------------------


def peek_link(proof: str) -> McpEnrolmentLink | None:
    """The usable link this proof names, without spending it."""
    parts = credentials.split_proof(proof)
    if parts is None:
        return None
    selector, verifier_digest = parts
    link = (
        McpEnrolmentLink.objects.select_related("grant", "grant__user")
        .filter(selector=selector)
        .first()
    )
    if link is None or not credentials.same(link.verifier_digest, verifier_digest):
        return None
    if not link.is_usable_at(timezone.now()):
        return None
    if link.grant.status != GrantStatus.PENDING_ENROLMENT:
        return None
    return link


def link_for(proof: str, user: User) -> McpEnrolmentLink | None:
    """The usable link, only if it is this persona's. Otherwise indistinguishable from none."""
    link = peek_link(proof)
    if link is None or link.grant.user_id != user.pk or grant_refusal(user):
        return None
    return link


def account_label(user: User) -> str:
    return user.display_name or user.upn


@transaction.atomic
def confirm_enrolment(
    *, proof: str, user: User, sealed: str, code: str, ip_address: str | None
) -> bool:
    """Spend the link; make the sealed secret the grant's authenticator if the code proves it."""
    parts = credentials.split_proof(proof)
    if parts is None:
        return False
    selector, verifier_digest = parts
    # The account first, then the grant, then the link: the order every writer
    # here takes them in.
    locked = User.objects.select_for_update().filter(pk=user.pk).first()
    link = McpEnrolmentLink.objects.select_for_update().filter(selector=selector).first()
    if (
        locked is None
        or link is None
        or not credentials.same(link.verifier_digest, verifier_digest)
    ):
        return False
    grant = McpAccessGrant.objects.select_for_update().get(pk=link.grant_id)
    now = timezone.now()
    refusal = ""
    if grant.user_id != locked.pk:
        refusal = "another_persona"
    elif not link.is_usable_at(now) or grant.status != GrantStatus.PENDING_ENROLMENT:
        refusal = "link_not_usable"
    elif grant_refusal(locked):
        refusal = "account_not_eligible"
    secret = credentials.unseal(sealed) if not refusal else None
    step = credentials.matching_step(secret, code, after_step=0) if secret is not None else None
    if refusal or step is None:
        record(
            McpAccessEventType.ENROLMENT_REFUSED,
            succeeded=False,
            user=locked,
            grant=grant if grant.user_id == locked.pk else None,
            ip_address=ip_address,
            reason=refusal or "bad_code",
        )
        return False
    McpAuthenticator.objects.create(
        grant=grant,
        encrypted_secret=credentials.seal(secret or ""),
        last_used_step=step,
        confirmed_at=now,
    )
    link.used_at = now
    link.save(update_fields=["used_at", "updated_at"])
    grant.status = GrantStatus.ACTIVE
    grant.activated_at = now
    grant.failed_attempts = 0
    grant.locked_until = None
    grant.save(
        update_fields=["status", "activated_at", "failed_attempts", "locked_until", "updated_at"]
    )
    record(McpAccessEventType.ENROLMENT_COMPLETED, user=locked, grant=grant, ip_address=ip_address)
    return True


# -- the authorization step ---------------------------------------------------------


@dataclass(frozen=True)
class AuthorizationRequest:
    """A validated `/oauth/authorize` request (`app.mcp_access.oauth.parse_authorization`)."""

    client: config.OAuthClient
    redirect_uri: str
    code_challenge: str
    scope: str
    resource: str
    state: str | None


@dataclass(frozen=True)
class AuthorizationOutcome:
    #: ``"ok"``, ``"bad_code"``, ``"locked"`` or ``"no_grant"``.
    status: str
    code: str = ""
    wait: int = 0


def _seconds_until(moment: datetime | None, now: datetime) -> int:
    if moment is None:
        return 0
    return max(1, int((moment - now).total_seconds()))


@transaction.atomic
def authorize(
    *, user: User, request: AuthorizationRequest, totp_code: str, ip_address: str | None
) -> AuthorizationOutcome:
    """Check the persona's authenticator code and mint a one-time authorization code.

    Under the grant's lock: five wrong codes in a row lock *authorizing* for
    fifteen minutes; a locked grant has its code checked by nobody. The code is
    spent on success (its time step is recorded), so the same or an earlier
    code is refused afterwards, in any worker.
    """
    client_id = request.client.client_id
    fresh = User.objects.filter(pk=user.pk).first()
    grant = (
        McpAccessGrant.objects.select_for_update()
        .filter(user_id=user.pk, status=GrantStatus.ACTIVE)
        .first()
    )
    if fresh is None or grant is None or not may_connect(fresh, grant):
        record(
            McpAccessEventType.AUTHORIZATION_REFUSED,
            succeeded=False,
            user=fresh,
            grant=grant,
            client_id=client_id,
            ip_address=ip_address,
            reason="no_active_grant",
        )
        return AuthorizationOutcome(status="no_grant")
    now = timezone.now()
    if grant.is_locked_at(now):
        record(
            McpAccessEventType.AUTHORIZATION_REFUSED,
            succeeded=False,
            user=fresh,
            grant=grant,
            client_id=client_id,
            ip_address=ip_address,
            reason="locked",
        )
        return AuthorizationOutcome(status="locked", wait=_seconds_until(grant.locked_until, now))

    authenticator = McpAuthenticator.objects.select_for_update().filter(grant=grant).first()
    secret = credentials.unseal(authenticator.encrypted_secret) if authenticator else None
    step = (
        credentials.matching_step(secret, totp_code, after_step=authenticator.last_used_step)
        if secret is not None and authenticator is not None
        else None
    )
    if step is None or authenticator is None:
        grant.failed_attempts += 1
        locked = grant.failed_attempts >= MAX_FAILED_CODES
        if locked:
            grant.failed_attempts = 0
            grant.locked_until = now + timedelta(seconds=LOCK_SECONDS)
        grant.save(update_fields=["failed_attempts", "locked_until", "updated_at"])
        record(
            McpAccessEventType.AUTHORIZATION_REFUSED,
            succeeded=False,
            user=fresh,
            grant=grant,
            client_id=client_id,
            ip_address=ip_address,
            reason="bad_code",
        )
        if locked:
            record(
                McpAccessEventType.GRANT_LOCKED,
                succeeded=False,
                user=fresh,
                grant=grant,
                client_id=client_id,
                ip_address=ip_address,
                seconds=LOCK_SECONDS,
            )
            return AuthorizationOutcome(status="locked", wait=LOCK_SECONDS)
        return AuthorizationOutcome(status="bad_code")

    authenticator.last_used_step = step
    authenticator.save(update_fields=["last_used_step", "updated_at"])
    grant.failed_attempts = 0
    grant.locked_until = None
    grant.save(update_fields=["failed_attempts", "locked_until", "updated_at"])
    raw = credentials.new_opaque()
    McpAuthorizationCode.objects.create(
        digest=credentials.digest(raw),
        grant=grant,
        user=fresh,
        client_id=client_id,
        redirect_uri=request.redirect_uri,
        code_challenge=request.code_challenge,
        scope=request.scope,
        resource=request.resource,
        user_epoch=fresh.security_epoch,
        expires_at=now + timedelta(seconds=int(settings.MCP_AUTHORIZATION_CODE_SECONDS)),
    )
    record(
        McpAccessEventType.AUTHORIZATION_GRANTED,
        user=fresh,
        grant=grant,
        client_id=client_id,
        ip_address=ip_address,
        resource=request.resource,
        scope=request.scope,
    )
    return AuthorizationOutcome(status="ok", code=raw)


def record_denied(
    *, user: User | None, request: AuthorizationRequest, ip_address: str | None
) -> None:
    """The person pressed «Keeldu»."""
    record(
        McpAccessEventType.AUTHORIZATION_DENIED,
        succeeded=False,
        user=user if user is not None and user.is_authenticated else None,
        client_id=request.client.client_id,
        ip_address=ip_address,
    )


# -- tokens ------------------------------------------------------------------------


@dataclass(frozen=True)
class OAuthError:
    """An RFC 6749 §5.2 error. ``description`` is safe to show and names no secret."""

    error: str
    description: str = ""
    status: int = 400


@dataclass(frozen=True)
class IssuedTokens:
    access_token: str
    refresh_token: str
    expires_in: int
    scope: str
    family_id: uuid.UUID = field(default_factory=uuid.uuid4)

    def as_json(self) -> dict[str, Any]:
        return {
            "access_token": self.access_token,
            "token_type": "Bearer",
            "expires_in": self.expires_in,
            "refresh_token": self.refresh_token,
            "scope": self.scope,
        }


def _issue_pair(
    *,
    grant: McpAccessGrant,
    user: User,
    client_id: str,
    scopes: str,
    resource: str,
    family_id: uuid.UUID,
    now: datetime,
) -> tuple[IssuedTokens, McpToken]:
    access_raw = credentials.new_opaque()
    refresh_raw = credentials.new_opaque()
    access_seconds = int(settings.MCP_ACCESS_TOKEN_SECONDS)
    common = {
        "family_id": family_id,
        "grant": grant,
        "user": user,
        "client_id": client_id,
        "scopes": scopes,
        "resource": resource,
        "user_epoch": user.security_epoch,
        "issued_at": now,
    }
    McpToken.objects.create(
        kind=TokenKind.ACCESS,
        digest=credentials.digest(access_raw),
        expires_at=now + timedelta(seconds=access_seconds),
        **common,
    )
    refresh = McpToken.objects.create(
        kind=TokenKind.REFRESH,
        digest=credentials.digest(refresh_raw),
        expires_at=now + timedelta(seconds=int(settings.MCP_REFRESH_TOKEN_SECONDS)),
        **common,
    )
    issued = IssuedTokens(
        access_token=access_raw,
        refresh_token=refresh_raw,
        expires_in=access_seconds,
        scope=scopes,
        family_id=family_id,
    )
    return issued, refresh


def _still_may_connect(grant: McpAccessGrant, user_id: Any, epoch: int) -> User | None:
    """The account, read afresh, if the grant, the account and its epoch still allow a token."""
    user = User.objects.filter(pk=user_id).first()
    if user is None or not may_connect(user, grant) or user.security_epoch != epoch:
        return None
    return user


def exchange_code(
    *,
    client_id: str,
    code: str,
    redirect_uri: str,
    code_verifier: str,
    resource: str,
    ip_address: str | None,
) -> IssuedTokens | OAuthError:
    """``grant_type=authorization_code`` (RFC 6749 §4.1.3 with RFC 7636 and RFC 8707)."""
    with transaction.atomic():
        return _exchange_code(
            client_id=client_id,
            code=code,
            redirect_uri=redirect_uri,
            code_verifier=code_verifier,
            resource=resource,
            ip_address=ip_address,
        )


def _exchange_code(
    *,
    client_id: str,
    code: str,
    redirect_uri: str,
    code_verifier: str,
    resource: str,
    ip_address: str | None,
) -> IssuedTokens | OAuthError:
    invalid = OAuthError("invalid_grant", "The authorization code is invalid.")
    code_digest = credentials.opaque_digest(code)
    if code_digest is None:
        return invalid
    record_row = McpAuthorizationCode.objects.select_for_update().filter(digest=code_digest).first()
    if record_row is None:
        return invalid
    now = timezone.now()

    def refuse(reason: str) -> OAuthError:
        record(
            McpAccessEventType.TOKEN_REFUSED,
            succeeded=False,
            user=record_row.user,
            grant=record_row.grant,
            client_id=client_id,
            ip_address=ip_address,
            grant_type="authorization_code",
            reason=reason,
        )
        return invalid

    if record_row.used_at is not None:
        # A code presented twice: whoever has it now may not be whoever had it
        # first, so what it was exchanged for goes too (RFC 6749 §4.1.2).
        if record_row.family_id is not None:
            _revoke_tokens(
                McpToken.objects.filter(family_id=record_row.family_id),
                reason="code_reused",
                now=now,
            )
        return refuse("code_reused")
    # Spent on its first presentation, whatever follows: a wrong verifier must
    # not leave the code available for another guess.
    record_row.used_at = now
    record_row.save(update_fields=["used_at", "updated_at"])
    if record_row.expires_at <= now:
        return refuse("code_expired")
    if not credentials.same(record_row.client_id, client_id or ""):
        return refuse("client_mismatch")
    if record_row.redirect_uri != (redirect_uri or ""):
        return refuse("redirect_uri_mismatch")
    if not credentials.pkce_matches(code_verifier, record_row.code_challenge):
        return refuse("pkce_mismatch")
    if config.normalise_resource(resource) != record_row.resource:
        return refuse("resource_mismatch")
    if not config.resource_is_allowed(record_row.resource):
        return refuse("resource_not_allowed")
    grant = McpAccessGrant.objects.select_for_update().get(pk=record_row.grant_id)
    user = _still_may_connect(grant, record_row.user_id, record_row.user_epoch)
    if user is None:
        return refuse("account_not_eligible")
    family_id = uuid.uuid4()
    issued, _ = _issue_pair(
        grant=grant,
        user=user,
        client_id=record_row.client_id,
        scopes=record_row.scope,
        resource=record_row.resource,
        family_id=family_id,
        now=now,
    )
    record_row.family_id = family_id
    record_row.save(update_fields=["family_id", "updated_at"])
    record(
        McpAccessEventType.TOKEN_ISSUED,
        user=user,
        grant=grant,
        client_id=client_id,
        ip_address=ip_address,
        family=str(family_id),
        resource=record_row.resource,
    )
    return issued


def refresh(
    *,
    client_id: str,
    refresh_token: str,
    resource: str,
    scope: str,
    ip_address: str | None,
) -> IssuedTokens | OAuthError:
    """``grant_type=refresh_token``, rotating the refresh token (RFC 6749 §6, OAuth 2.1 §4.3)."""
    with transaction.atomic():
        return _refresh(
            client_id=client_id,
            refresh_token=refresh_token,
            resource=resource,
            scope=scope,
            ip_address=ip_address,
        )


def _refresh(
    *,
    client_id: str,
    refresh_token: str,
    resource: str,
    scope: str,
    ip_address: str | None,
) -> IssuedTokens | OAuthError:
    invalid = OAuthError("invalid_grant", "The refresh token is invalid.")
    token_digest = credentials.opaque_digest(refresh_token)
    if token_digest is None:
        return invalid
    token = (
        McpToken.objects.select_for_update()
        .filter(digest=token_digest, kind=TokenKind.REFRESH)
        .first()
    )
    if token is None:
        return invalid
    now = timezone.now()

    def refuse(reason: str, *, error: OAuthError = invalid) -> OAuthError:
        record(
            McpAccessEventType.TOKEN_REFUSED,
            succeeded=False,
            user=token.user,
            grant=token.grant,
            client_id=client_id,
            ip_address=ip_address,
            grant_type="refresh_token",
            reason=reason,
        )
        return error

    if not credentials.same(token.client_id, client_id or ""):
        return refuse("client_mismatch")
    if token.replaced_by_id is not None:
        # A rotated refresh token came back. One of the two holders is not
        # the client it was issued to, and nothing says which: the whole
        # family ends, and the legitimate client authorizes again.
        revoked = _revoke_tokens(
            McpToken.objects.filter(family_id=token.family_id), reason="refresh_reused", now=now
        )
        record(
            McpAccessEventType.REFRESH_REUSE_DETECTED,
            succeeded=False,
            user=token.user,
            grant=token.grant,
            client_id=client_id,
            ip_address=ip_address,
            family=str(token.family_id),
            tokens_revoked=revoked,
        )
        return invalid
    if token.revoked_at is not None:
        return refuse("revoked")
    if token.expires_at <= now:
        return refuse("expired")
    if resource and config.normalise_resource(resource) != token.resource:
        return refuse("resource_mismatch", error=OAuthError("invalid_target", "Wrong resource."))
    if not config.resource_is_allowed(token.resource):
        return refuse("resource_not_allowed")
    requested = frozenset(part for part in (scope or "").split() if part)
    if requested and not requested <= token.scope_set:
        return refuse("scope_widened", error=OAuthError("invalid_scope", "Scope not granted."))
    grant = McpAccessGrant.objects.select_for_update().get(pk=token.grant_id)
    user = _still_may_connect(grant, token.user_id, token.user_epoch)
    if user is None:
        _revoke_tokens(
            McpToken.objects.filter(family_id=token.family_id), reason="not_eligible", now=now
        )
        return refuse("account_not_eligible")
    issued, new_refresh = _issue_pair(
        grant=grant,
        user=user,
        client_id=token.client_id,
        scopes=token.scopes,
        resource=token.resource,
        family_id=token.family_id,
        now=now,
    )
    token.replaced_by = new_refresh
    token.revoked_at = now
    token.revoke_reason = "rotated"
    token.last_used_at = now
    token.save(
        update_fields=["replaced_by", "revoked_at", "revoke_reason", "last_used_at", "updated_at"]
    )
    record(
        McpAccessEventType.TOKEN_REFRESHED,
        user=user,
        grant=grant,
        client_id=client_id,
        ip_address=ip_address,
        family=str(token.family_id),
    )
    return issued


@transaction.atomic
def revoke_token(*, client_id: str, token: str, ip_address: str | None) -> bool:
    """RFC 7009: end this token (a refresh token's whole family). True if anything ended.

    Only the client the token was issued to may revoke it; anybody else's
    attempt changes nothing, and the endpoint answers 200 either way.
    """
    token_digest = credentials.opaque_digest(token)
    if token_digest is None:
        return False
    row = McpToken.objects.select_for_update().filter(digest=token_digest).first()
    if row is None or not credentials.same(row.client_id, client_id or ""):
        return False
    now = timezone.now()
    if row.kind == TokenKind.REFRESH:
        revoked = _revoke_tokens(
            McpToken.objects.filter(family_id=row.family_id), reason="client_revoked", now=now
        )
    else:
        revoked = _revoke_tokens(
            McpToken.objects.filter(pk=row.pk), reason="client_revoked", now=now
        )
    record(
        McpAccessEventType.TOKEN_REVOKED,
        user=row.user,
        grant=row.grant,
        client_id=client_id,
        ip_address=ip_address,
        kind=row.kind,
        tokens_revoked=revoked,
    )
    return bool(revoked)
