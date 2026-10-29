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

Writing through an AI client is a second, separate decision on the same grant
(`grant_write`, `may_write`, docs/mcp/OAUTH-SCOPES.md): the ``juristid.write``
scope is granted only when asked for and allowed at that moment, narrowed
away at every later step where it no longer is, and never widened.
"""

from __future__ import annotations

import getpass
import logging
import socket
import uuid
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Any

from django.conf import settings
from django.db import transaction
from django.db.models import Count, Max, Q
from django.urls import reverse
from django.utils import timezone

from app.accounts.models import User
from app.core.authorization import (
    ROLES_WITH_BUSINESS_READ,
    acting_role,
    may_read_business_content,
    may_write_business_content,
)
from app.mcp_access import config, credentials
from app.mcp_access.models import (
    GrantStatus,
    McpAccessEvent,
    McpAccessEventType,
    McpAccessGrant,
    McpAuthenticator,
    McpAuthorizationCode,
    McpAuthorizeThrottle,
    McpEnrolmentLink,
    McpToken,
    TokenKind,
)

#: Wrong authenticator codes in a row before the grant locks.
MAX_FAILED_CODES = 5
#: How long each consecutive lock lasts (MCPSEC-01). The lock after the last
#: rung suspends the grant until an operator runs ``mcp_access unlock``: at most
#: ``MAX_FAILED_CODES * (len(LOCK_LADDER_SECONDS) + 1)`` = 20 guesses before a
#: person has to be involved, against up to three valid codes in a million.
LOCK_LADDER_SECONDS = (15 * 60, 60 * 60, 4 * 60 * 60)
LOCK_SECONDS = LOCK_LADDER_SECONDS[0]

logger = logging.getLogger("app.mcp_access")

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
    standing AI connection on the strength of it; a technical account
    (``is_staff`` / ``is_superuser``) is refused even with a business role, as
    the persona list refuses it. Synthetic rehearsal accounts are refused
    wherever real data is allowed.
    """
    if not user.is_active:
        return "konto ei ole aktiivne"
    if acting_role(user) not in ROLES_WITH_BUSINESS_READ:
        return "kontol ei ole sisu lugemise rolli (SPECIALIST, DEPARTMENT_HEAD või READER)"
    if user.is_staff or user.is_superuser:
        # Technical access is not business identity (app/accounts/selectors.py).
        # The persona rule used to keep these accounts off the consent page;
        # since the page asks for the account's own address instead (docs/adr/0185
        # §3), the refusal is said here, where every step asks it.
        return "tehnilisele kontole (is_staff / is_superuser) ei anta ligipääsu"
    if not may_read_business_content(user):  # pragma: no cover - implied by the role today
        return "konto ei tohi sisu lugeda"
    if user.is_synthetic and getattr(settings, "REAL_DATA_ALLOWED", False):
        return "sünteetilisele kontole ei anta päris andmetega keskkonnas ligipääsu"
    return ""


def may_connect(user: User | None, grant: McpAccessGrant | None) -> bool:
    """The questions every step asks again: grant ACTIVE, and the account still grantable.

    The same eligibility as `grant_refusal` (MCPSEC-06): an active account
    acting under a business role, which `may_read_business_content` implies.
    Asking only that predicate let an administrator holding a temporary
    break-glass grant — whose grant predated the role change — authorize and
    refresh as if it were a standing business role.
    """
    if user is None or grant is None:
        return False
    return (
        grant.status == GrantStatus.ACTIVE
        and grant.user_id == user.pk
        and bool(user.is_active)
        and may_read_business_content(user)
        and not grant_refusal(user)
    )


def may_write(user: User | None, grant: McpAccessGrant | None) -> bool:
    """May this grant's person *prepare changes* through an AI client right now?

    Everything `may_connect` asks, and the operator's separate write decision
    (``grant-write``, not ended by ``revoke-write``), and a business-write role
    (`may_write_business_content` — never a READER, never a technical
    ADMINISTRATOR without SPECIALIST or DEPARTMENT_HEAD), and
    `MCP_WRITES_ENABLED` on this process. Read afresh at every step; nothing
    caches it. A True here never writes anything by itself: every change still
    needs the person's own approval in Juristid (docs/mcp/OAUTH-SCOPES.md).
    """
    if not bool(getattr(settings, "MCP_WRITES_ENABLED", False)):
        return False
    if grant is None or not may_connect(user, grant):
        return False
    return grant.write_is_enabled and may_write_business_content(user)


def write_enablement_covers(grant: McpAccessGrant, started_at: datetime | None) -> bool:
    """Whether a connection begun at ``started_at`` falls under the current write decision.

    A write scope is honoured only for a token family (or an authorization
    code) that began at or after ``write_enabled_at``. Without this, a token
    minted under an earlier enablement — revoked by ``revoke-write`` and never
    refreshed — would write again the moment ``grant-write`` was run a second
    time (security review P3-5). A pure comparison: no query.
    """
    enabled_at = grant.write_enabled_at
    return enabled_at is not None and started_at is not None and started_at >= enabled_at


def write_scope() -> str:
    return str(getattr(settings, "MCP_WRITE_SCOPE", "juristid.write"))


def narrow_scope(requested: str, *, write_allowed: bool) -> str:
    """The scope actually granted: always the read scope, plus write only if asked for and allowed.

    RFC 6749 §3.3: the server may grant less than was asked for, and says so in
    the token response. Never more: write is added only when ``requested``
    names it.
    """
    parts = {part for part in (requested or "").split() if part}
    granted = [config.scope()]
    if write_allowed and write_scope() in parts:
        granted.append(write_scope())
    return " ".join(granted)


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
    was_suspended = grant.is_suspended
    was_locked = (
        grant.locked_until is not None
        or grant.failed_attempts > 0
        or grant.lock_count > 0
        or was_suspended
    )
    # The lock, the escalation and the suspension end; the cumulative count of
    # wrong codes does not. Only a successful authorization resets that, so the
    # history an operator is unlocking stays visible (`mcp_access list`).
    grant.failed_attempts = 0
    grant.locked_until = None
    grant.lock_count = 0
    grant.suspended_at = None
    grant.save(
        update_fields=[
            "failed_attempts",
            "locked_until",
            "lock_count",
            "suspended_at",
            "updated_at",
        ]
    )
    record(
        McpAccessEventType.GRANT_UNLOCKED,
        user=user,
        grant=grant,
        provenance=provenance,
        was_suspended=was_suspended,
        total_failed_attempts=grant.total_failed_attempts,
    )
    return was_locked


# -- the separate write decision (docs/mcp/OAUTH-SCOPES.md) ----------------------------


def write_refusal(user: User, grant: McpAccessGrant | None) -> str:
    """Why this account's grant may not be allowed to prepare changes, or "" if it may.

    The business-*write* role is asked before anything else that would also
    refuse, so a READER or a technical ADMINISTRATOR is told the reason that
    matters for writing.
    """
    if not user.is_active:
        return "konto ei ole aktiivne"
    if not may_write_business_content(user):
        return "kontol ei ole sisu muutmise rolli (SPECIALIST või DEPARTMENT_HEAD)"
    reason = grant_refusal(user)
    if reason:
        return reason
    if grant is None:
        return "kontol ei ole kehtivat ligipääsu"
    if grant.write_is_enabled:
        return "muudatuste ettevalmistamine on juba lubatud"
    return ""


def grant_write(*, user: User, provenance: str) -> McpAccessGrant:
    """Let this account's open grant prepare changes (``juristid.write``). Raises `GrantRefused`.

    Reading is untouched. A token issued before this carries no write scope:
    the person connects again to get one. The refusal is raised after the
    transaction, so its audit row is kept.
    """
    with transaction.atomic():
        outcome = _grant_write(user=user, provenance=provenance)
    if isinstance(outcome, str):
        raise GrantRefused(outcome)
    return outcome


def _grant_write(*, user: User, provenance: str) -> McpAccessGrant | str:
    locked = _lock_user(user)
    grant = (
        McpAccessGrant.objects.select_for_update()
        .filter(user=locked)
        .exclude(status=GrantStatus.REVOKED)
        .first()
    )
    reason = write_refusal(locked, grant)
    if reason:
        record(
            McpAccessEventType.WRITE_REFUSED,
            succeeded=False,
            user=locked,
            grant=grant,
            provenance=provenance,
            reason=reason,
        )
        return reason
    if grant is None:  # pragma: no cover - write_refusal refuses a missing grant
        return "kontol ei ole kehtivat ligipääsu"
    now = timezone.now()
    grant.write_enabled_at = now
    grant.write_enabled_via = provenance[:300]
    grant.write_revoked_at = None
    grant.write_revoke_reason = ""
    grant.save(
        update_fields=[
            "write_enabled_at",
            "write_enabled_via",
            "write_revoked_at",
            "write_revoke_reason",
            "updated_at",
        ]
    )
    record(McpAccessEventType.WRITE_ENABLED, user=locked, grant=grant, provenance=provenance)
    return grant


def _end_write(grant: McpAccessGrant, *, reason: str, now: datetime) -> None:
    grant.write_revoked_at = now
    grant.write_revoke_reason = reason[:300]
    grant.save(update_fields=["write_revoked_at", "write_revoke_reason", "updated_at"])


@transaction.atomic
def revoke_write(*, user: User, reason: str, provenance: str) -> None:
    """End this account's write decision at once; reading and every token stay.

    No token is revoked: a token that carries ``juristid.write`` keeps working
    for reading, and `may_write` — asked at every write step, by the writer
    and by the approval page — now answers False. A refresh drops the write
    scope from the family for good.
    """
    if not reason.strip():
        raise GrantRefused("põhjus on kohustuslik")
    _lock_user(user)
    grant = (
        McpAccessGrant.objects.select_for_update()
        .filter(user=user)
        .exclude(status=GrantStatus.REVOKED)
        .first()
    )
    if grant is None:
        raise GrantRefused("kontol ei ole kehtivat ligipääsu")
    if not grant.write_is_enabled:
        raise GrantRefused("kontol ei ole muudatuste ettevalmistamise luba")
    _end_write(grant, reason=reason, now=timezone.now())
    record(
        McpAccessEventType.WRITE_REVOKED,
        user=user,
        grant=grant,
        provenance=provenance,
        reason=reason[:300],
    )


@transaction.atomic
def revoke_all_writes(*, reason: str, provenance: str) -> int:
    """The write kill switch: every grant's write decision ends; reading is untouched.

    Independent of `MCP_WRITES_ENABLED` and of ``revoke-all``: AI writing stops
    for everybody while every read connection keeps working. Returns how many
    grants had write.
    """
    if not reason.strip():
        raise GrantRefused("põhjus on kohustuslik")
    now = timezone.now()
    grants = list(
        McpAccessGrant.objects.select_for_update()
        .filter(write_enabled_at__isnull=False, write_revoked_at__isnull=True)
        .order_by("pk")
    )
    for grant in grants:
        _end_write(grant, reason=reason, now=now)
        record(
            McpAccessEventType.WRITE_REVOKED,
            user=grant.user,
            grant=grant,
            provenance=provenance,
            reason=reason[:300],
            revoke_all_writes=True,
        )
    return len(grants)


@dataclass(frozen=True)
class GrantRow:
    upn: str
    status: str
    enrolled: bool
    active_tokens: int
    last_used: datetime | None
    locked_until: datetime | None
    suspended: bool = False
    total_failed_attempts: int = 0
    #: ``"jah"`` while the write decision stands, ``"tühistatud"`` after
    #: ``revoke-write``, ``"ei"`` when it was never made.
    write: str = "ei"


def _write_state(grant: McpAccessGrant) -> str:
    if grant.write_is_enabled:
        return "jah"
    if grant.write_revoked_at is not None:
        return "tühistatud"
    return "ei"


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
                suspended=grant.is_suspended,
                total_failed_attempts=grant.total_failed_attempts,
                write=_write_state(grant),
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


def enrolment_account(proof: str) -> User | None:
    """The account a usable link was issued to, while it may still be granted; else None.

    Read from the link, never from the request: the link (with the gate in
    front of it) is the proof of possession, and the address typed on the page
    must then name this very account (`check_enrolment_address`).
    """
    link = peek_link(proof) if proof else None
    if link is None:
        return None
    account = User.objects.filter(pk=link.grant.user_id).first()
    if account is None or grant_refusal(account):
        return None
    return account


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
    """What the consent page is told.

    ``status`` is ``"ok"``; ``"bad_code"`` or ``"no_grant"`` — which the page
    answers identically, so a guess learns nothing about the persona; or, only
    once the persona's own current code has been verified, ``"locked"`` or
    ``"suspended"``; ``"unreadable"`` when the authenticator's secret cannot be
    opened with the configured key (answered like ``bad_code``, counted as
    nothing, MCPSEC-07); or ``"throttled"`` when this session or address has
    failed too often to have any code checked (`authorize_attempt`).
    """

    status: str
    code: str = ""
    wait: int = 0


def _seconds_until(moment: datetime | None, now: datetime) -> int:
    if moment is None:
        return 0
    return max(1, int((moment - now).total_seconds()))


def _count_wrong_code(
    grant: McpAccessGrant,
    *,
    user: User,
    client_id: str,
    ip_address: str | None,
    now: datetime,
    refused_event: str = McpAccessEventType.AUTHORIZATION_REFUSED,
) -> AuthorizationOutcome:
    """One wrong code against a held grant: count it, and lock or suspend when due.

    Counted whether or not the grant is already locked: a lock refuses the
    *right* code, it is not a pause in which guesses are free. The answer is
    ``bad_code`` every time — the guess that trips a lock is told nothing the
    other guesses were not. One ladder for both places a code is typed —
    connecting (`authorize`) and approving a change (`confirm_write_code`);
    ``refused_event`` only says which of the two it was.
    """
    grant.total_failed_attempts += 1
    if not grant.is_suspended:
        grant.failed_attempts += 1
    tripped = not grant.is_suspended and grant.failed_attempts >= MAX_FAILED_CODES
    seconds = 0
    if tripped:
        grant.failed_attempts = 0
        grant.lock_count += 1
        if grant.lock_count > len(LOCK_LADDER_SECONDS):
            grant.suspended_at = now
            grant.locked_until = None
        else:
            seconds = LOCK_LADDER_SECONDS[grant.lock_count - 1]
            grant.locked_until = now + timedelta(seconds=seconds)
    grant.save(
        update_fields=[
            "failed_attempts",
            "total_failed_attempts",
            "lock_count",
            "locked_until",
            "suspended_at",
            "updated_at",
        ]
    )
    record(
        refused_event,
        succeeded=False,
        user=user,
        grant=grant,
        client_id=client_id,
        ip_address=ip_address,
        reason="bad_code",
        total_failed_attempts=grant.total_failed_attempts,
    )
    if tripped and grant.is_suspended:
        record(
            McpAccessEventType.GRANT_SUSPENDED,
            succeeded=False,
            user=user,
            grant=grant,
            client_id=client_id,
            ip_address=ip_address,
            lock_count=grant.lock_count,
            total_failed_attempts=grant.total_failed_attempts,
        )
        logger.warning(
            "MCP grant %s (user %s) suspended after %s wrong authenticator codes since the "
            "last success; only `manage.py mcp_access unlock` reopens it",
            grant.pk,
            user.pk,
            grant.total_failed_attempts,
        )
    elif tripped:
        record(
            McpAccessEventType.GRANT_LOCKED,
            succeeded=False,
            user=user,
            grant=grant,
            client_id=client_id,
            ip_address=ip_address,
            seconds=seconds,
            lock_count=grant.lock_count,
            total_failed_attempts=grant.total_failed_attempts,
        )
        logger.warning(
            "MCP grant %s (user %s) locked for %s s: lock %s of %s, %s wrong authenticator "
            "codes since the last success",
            grant.pk,
            user.pk,
            seconds,
            grant.lock_count,
            len(LOCK_LADDER_SECONDS),
            grant.total_failed_attempts,
        )
    return AuthorizationOutcome(status="bad_code")


def _check_personal_code(
    *,
    fresh: User | None,
    grant: McpAccessGrant | None,
    eligible: bool,
    ineligible_reason: str,
    totp_code: str,
    client_id: str,
    ip_address: str | None,
    refused_event: str,
) -> AuthorizationOutcome | datetime:
    """The one check of the grant's personal authenticator code, for connecting and approving.

    The caller holds the grant under `select_for_update` and says whether the
    persona may do what it asked (``eligible``). Returns the moment of success
    — the step is spent, the wrong-code counters reset — or the refusal to
    answer with. A wrong code is counted (`_count_wrong_code`): five in a row
    lock, consecutive locks escalate, and the lock after the last rung
    suspends the grant until an operator unlocks it. A right code is spent
    whatever follows (its time step is recorded), so the same or an earlier
    code is refused afterwards, in any worker and at either step — and only a
    right code learns that the grant is locked or suspended.
    """
    if fresh is None or grant is None or not eligible:
        record(
            refused_event,
            succeeded=False,
            user=fresh,
            grant=grant,
            client_id=client_id,
            ip_address=ip_address,
            reason=ineligible_reason,
        )
        return AuthorizationOutcome(status="no_grant")
    now = timezone.now()
    authenticator = McpAuthenticator.objects.select_for_update().filter(grant=grant).first()
    secret = credentials.unseal(authenticator.encrypted_secret) if authenticator else None
    if authenticator is not None and secret is None:
        # The key the secret was sealed under is not the one configured now
        # (MCPSEC-07). No code can be checked, so none is counted as wrong:
        # counting would lock every colleague out after a key change, for a
        # reason none of them caused. The answer is the generic one; the
        # operator sees why.
        record(
            refused_event,
            succeeded=False,
            user=fresh,
            grant=grant,
            client_id=client_id,
            ip_address=ip_address,
            reason="authenticator_unreadable",
        )
        logger.warning(
            "MCP grant %s (user %s): the authenticator secret cannot be read with the "
            "configured MCP_AUTHENTICATOR_KEY; the person must enrol again",
            grant.pk,
            fresh.pk,
        )
        return AuthorizationOutcome(status="unreadable")
    step = (
        credentials.matching_step(secret, totp_code, after_step=authenticator.last_used_step)
        if secret is not None and authenticator is not None
        else None
    )
    if step is None or authenticator is None:
        return _count_wrong_code(
            grant,
            user=fresh,
            client_id=client_id,
            ip_address=ip_address,
            now=now,
            refused_event=refused_event,
        )

    authenticator.last_used_step = step
    authenticator.save(update_fields=["last_used_step", "updated_at"])
    if grant.is_suspended or grant.is_locked_at(now):
        record(
            refused_event,
            succeeded=False,
            user=fresh,
            grant=grant,
            client_id=client_id,
            ip_address=ip_address,
            reason="suspended" if grant.is_suspended else "locked",
        )
        if grant.is_suspended:
            return AuthorizationOutcome(status="suspended")
        return AuthorizationOutcome(status="locked", wait=_seconds_until(grant.locked_until, now))

    grant.failed_attempts = 0
    grant.total_failed_attempts = 0
    grant.lock_count = 0
    grant.locked_until = None
    grant.save(
        update_fields=[
            "failed_attempts",
            "total_failed_attempts",
            "lock_count",
            "locked_until",
            "updated_at",
        ]
    )
    return now


@transaction.atomic
def authorize(
    *, user: User, request: AuthorizationRequest, totp_code: str, ip_address: str | None
) -> AuthorizationOutcome:
    """Check the persona's authenticator code and mint a one-time authorization code.

    Under the grant's lock (`_check_personal_code`). The code's scope is the
    read scope, plus ``juristid.write`` only when the request asked for it
    *and* `may_write` holds now, under that same lock; otherwise it is
    narrowed to read without an error (RFC 6749 §3.3) and the token response
    says so.
    """
    client_id = request.client.client_id
    fresh = User.objects.filter(pk=user.pk).first()
    grant = (
        McpAccessGrant.objects.select_for_update()
        .filter(user_id=user.pk, status=GrantStatus.ACTIVE)
        .first()
    )
    checked = _check_personal_code(
        fresh=fresh,
        grant=grant,
        eligible=may_connect(fresh, grant),
        ineligible_reason="no_active_grant",
        totp_code=totp_code,
        client_id=client_id,
        ip_address=ip_address,
        refused_event=McpAccessEventType.AUTHORIZATION_REFUSED,
    )
    if isinstance(checked, AuthorizationOutcome):
        return checked
    if fresh is None or grant is None:  # pragma: no cover - refused by the check above
        return AuthorizationOutcome(status="no_grant")
    now = checked
    scope = narrow_scope(request.scope, write_allowed=may_write(fresh, grant))
    raw = credentials.new_opaque()
    McpAuthorizationCode.objects.create(
        digest=credentials.digest(raw),
        grant=grant,
        user=fresh,
        client_id=client_id,
        redirect_uri=request.redirect_uri,
        code_challenge=request.code_challenge,
        scope=scope,
        resource=request.resource,
        user_epoch=fresh.security_epoch,
        expires_at=now + timedelta(seconds=int(settings.MCP_AUTHORIZATION_CODE_SECONDS)),
    )
    detail: dict[str, Any] = {"resource": request.resource, "scope": scope}
    if scope != request.scope:
        detail["requested_scope"] = request.scope
    record(
        McpAccessEventType.AUTHORIZATION_GRANTED,
        user=fresh,
        grant=grant,
        client_id=client_id,
        ip_address=ip_address,
        **detail,
    )
    return AuthorizationOutcome(status="ok", code=raw)


@transaction.atomic
def _confirm_write_code(
    *, user: User, grant_id: Any, code: str, ip_address: str | None
) -> AuthorizationOutcome:
    fresh = User.objects.filter(pk=user.pk).first()
    grant = (
        McpAccessGrant.objects.select_for_update()
        .filter(pk=grant_id, user_id=user.pk, status=GrantStatus.ACTIVE)
        .first()
    )
    connects = may_connect(fresh, grant)
    checked = _check_personal_code(
        fresh=fresh,
        grant=grant,
        eligible=connects and may_write(fresh, grant),
        ineligible_reason="write_not_allowed" if connects else "no_active_grant",
        totp_code=code,
        client_id="",
        ip_address=ip_address,
        refused_event=McpAccessEventType.WRITE_CONFIRMATION_REFUSED,
    )
    if isinstance(checked, AuthorizationOutcome):
        return checked
    record(McpAccessEventType.WRITE_CONFIRMED, user=fresh, grant=grant, ip_address=ip_address)
    return AuthorizationOutcome(status="ok")


def confirm_write_code(
    *,
    user: User,
    grant_id: Any,
    code: str,
    ip_address: str | None,
    throttle: Sequence[ThrottleKey],
) -> AuthorizationOutcome:
    """The grant's personal code, typed on the write-approval page to approve one change.

    The same authenticator, the same single-use time step, the same lock
    ladder and the same per-session and per-address throttle rows as
    ``/oauth/authorize`` (`authorize_attempt`): a wrong code here counts
    exactly like one there, and a code spent connecting cannot approve, nor
    the other way round. ``grant_id`` is the proposal's grant; it must be this
    persona's ACTIVE grant and `may_write` must hold, or the answer is
    ``no_grant`` without a code being checked — which the page answers like
    ``bad_code``. Statuses as `AuthorizationOutcome`; ``code`` stays empty.
    Records ``WRITE_CONFIRMED`` or ``WRITE_CONFIRMATION_REFUSED``. Writes
    only this app's own rows (step, counters, throttle, event); the approval
    row is the caller's. Call it outside any transaction the caller might roll
    back on a refusal, or the wrong-code count would be rolled back with it.
    """
    return _throttled(
        user=user,
        client_id="",
        ip_address=ip_address,
        throttle=throttle,
        refused_event=McpAccessEventType.WRITE_CONFIRMATION_REFUSED,
        attempt=lambda: _confirm_write_code(
            user=user, grant_id=grant_id, code=code, ip_address=ip_address
        ),
    )


# -- the per-session and per-address throttle ------------------------------------------


@dataclass(frozen=True)
class ThrottleKey:
    """One counter `authorize_attempt` checks: an HMAC'd key and its failure limit."""

    key: str
    limit: int


def _held_throttle(key: str, *, now: datetime) -> McpAuthorizeThrottle:
    """This key's row, created if absent, held under its own lock.

    The two-step shape of `app.accounts.shared_gate._locked_throttle`, for the
    reasons given there: `get_or_create` so two first attempts cannot lose each
    other, then the locking read.
    """
    McpAuthorizeThrottle.objects.get_or_create(key=key, defaults={"window_started_at": now})
    return McpAuthorizeThrottle.objects.select_for_update().get(key=key)


def _throttle_window() -> timedelta:
    return timedelta(seconds=int(settings.MCP_AUTHORIZE_THROTTLE_SECONDS))


def _hold_throttles(
    throttle: Sequence[ThrottleKey], *, now: datetime
) -> tuple[list[McpAuthorizeThrottle], int]:
    """Every key's row under its own lock, in a fixed order; and the wait if one is spent.

    Taken before anything is checked, and charged (`_charge_throttles`) after
    a failure in the same transaction, so parallel attempts cannot all read
    "under the limit". A wait above zero means nothing may be checked at all.
    """
    window = _throttle_window()
    rows: list[McpAuthorizeThrottle] = []
    blocked: list[McpAuthorizeThrottle] = []
    for item in sorted(throttle, key=lambda entry: entry.key):
        row = _held_throttle(item.key, now=now)
        if row.window_started_at + window <= now:
            row.failures = 0
            row.window_started_at = now
        rows.append(row)
        if row.failures >= item.limit:
            blocked.append(row)
    wait = max((_seconds_until(row.window_started_at + window, now) for row in blocked), default=0)
    return rows, wait


def _charge_throttles(rows: Sequence[McpAuthorizeThrottle], *, failed: bool) -> None:
    for row in rows:
        if failed:
            row.failures += 1
        row.save(update_fields=["failures", "window_started_at", "updated_at"])


def account_for_address(address: str) -> User | None:
    """The one account whose sign-in address (``upn``) this is, normalised; else None.

    The accounts app's own normal form (`app.accounts.email_policy.normalise`).
    Says nothing about whether the account may connect — every step asks that
    itself — and an address two accounts could answer to is nobody's.
    """
    from app.accounts import email_policy

    normal = email_policy.normalise(address)[:320]
    if not normal or "@" not in normal:
        return None
    matches = list(User.objects.filter(upn__iexact=normal).order_by("pk")[:2])
    return matches[0] if len(matches) == 1 else None


def authorize_attempt(
    *,
    user: User | None,
    request: AuthorizationRequest,
    totp_code: str,
    ip_address: str | None,
    throttle: Sequence[ThrottleKey],
) -> AuthorizationOutcome:
    """`authorize`, unless this session or address has already failed too often.

    ``user`` is the account the typed address names (`account_for_address`),
    or None when it names nobody — which is answered like a wrong code and
    counted like one, so the throttle bounds guessing addresses as much as
    guessing codes (docs/adr/0185 §3).

    The per-grant lock bounds the guesses at one colleague's code; this bounds
    one gate session — and one client address — across *every* account it can
    name (MCPSEC-01). A refused attempt has no code checked and charges no
    grant. The window is fixed (`MCP_AUTHORIZE_THROTTLE_SECONDS`) and a success
    resets nothing: a colleague's own right code must not buy another round of
    guesses. The rows are shared with approving a change (`confirm_write_code`):
    a guesser does not get a second budget by switching pages.
    """

    def attempt() -> AuthorizationOutcome:
        if user is None:
            # Nothing is said about which address was typed: the audit row is
            # not a list of guesses at colleagues' addresses.
            record(
                McpAccessEventType.AUTHORIZATION_REFUSED,
                succeeded=False,
                client_id=request.client.client_id,
                ip_address=ip_address,
                reason="unknown_account",
            )
            return AuthorizationOutcome(status="no_grant")
        return authorize(user=user, request=request, totp_code=totp_code, ip_address=ip_address)

    return _throttled(
        user=user,
        client_id=request.client.client_id,
        ip_address=ip_address,
        throttle=throttle,
        refused_event=McpAccessEventType.AUTHORIZATION_REFUSED,
        attempt=attempt,
    )


def _throttled(
    *,
    user: User | None,
    client_id: str,
    ip_address: str | None,
    throttle: Sequence[ThrottleKey],
    refused_event: str,
    attempt: Callable[[], AuthorizationOutcome],
) -> AuthorizationOutcome:
    """Run ``attempt`` unless a throttle row is over its limit; charge every row if it fails.

    The rows are held (`_hold_throttles`) before anything is checked and
    charged (`_charge_throttles`) in the same transaction, so parallel attempts
    cannot all read "under the limit". Shared by `/oauth/authorize` and the
    write-approval page.
    """
    with transaction.atomic():
        now = timezone.now()
        rows, wait = _hold_throttles(throttle, now=now)
        if wait:
            record(
                refused_event,
                succeeded=False,
                user=user if user is not None and user.is_authenticated else None,
                client_id=client_id,
                ip_address=ip_address,
                reason="throttled",
            )
            return AuthorizationOutcome(status="throttled", wait=wait)
        outcome = attempt()
        _charge_throttles(rows, failed=outcome.status != "ok")
        return outcome


@dataclass(frozen=True)
class EnrolmentAddressOutcome:
    """The enrolment page's address step: ``"ok"`` with the account, or a refusal.

    ``"throttled"`` when this session or address has failed too often; else
    ``"refused"``, which covers an unknown address, another account's and a link that
    stopped being usable meanwhile, alike.
    """

    status: str
    user: User | None = None
    wait: int = 0


def check_enrolment_address(
    *,
    proof: str,
    address: str,
    ip_address: str | None,
    throttle: Sequence[ThrottleKey],
) -> EnrolmentAddressOutcome:
    """Whether the typed address is the account the link was issued to (docs/adr/0185 §3).

    Counted by the consent page's own throttle (the same keys), so a gate
    session cannot try addresses here that it could not try there. A wrong
    address, an unknown one and a link that stopped being usable get one answer.
    """
    with transaction.atomic():
        now = timezone.now()
        rows, wait = _hold_throttles(throttle, now=now)
        if wait:
            record(
                McpAccessEventType.ENROLMENT_REFUSED,
                succeeded=False,
                ip_address=ip_address,
                reason="throttled",
            )
            return EnrolmentAddressOutcome(status="throttled", wait=wait)
        account = enrolment_account(proof)
        typed = account_for_address(address)
        matched = account is not None and typed is not None and typed.pk == account.pk
        if not matched:
            record(
                McpAccessEventType.ENROLMENT_REFUSED,
                succeeded=False,
                user=account,
                grant=open_grant(account) if account is not None else None,
                ip_address=ip_address,
                reason="address_mismatch" if account is not None else "link_not_usable",
            )
        _charge_throttles(rows, failed=not matched)
        if not matched:
            return EnrolmentAddressOutcome(status="refused")
        return EnrolmentAddressOutcome(status="ok", user=account)


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
    family_started_at: datetime,
    auth_method: str = "",
    raw_pair: tuple[str, str] | None = None,
) -> tuple[IssuedTokens, McpToken]:
    access_raw, refresh_raw = raw_pair or (credentials.new_opaque(), credentials.new_opaque())
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
        "family_started_at": family_started_at,
        "auth_method": auth_method[:32],
    }
    # Never past the family's absolute end (MCPSEC-08).
    family_ends = family_started_at + timedelta(seconds=family_max_seconds())
    McpToken.objects.create(
        kind=TokenKind.ACCESS,
        digest=credentials.digest(access_raw),
        expires_at=now + timedelta(seconds=access_seconds),
        **common,
    )
    refresh = McpToken.objects.create(
        kind=TokenKind.REFRESH,
        digest=credentials.digest(refresh_raw),
        expires_at=min(
            now + timedelta(seconds=int(settings.MCP_REFRESH_TOKEN_SECONDS)), family_ends
        ),
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


def family_max_seconds() -> int:
    return int(getattr(settings, "MCP_REFRESH_FAMILY_MAX_SECONDS", 90 * 24 * 3600))


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
    auth_method: str = "",
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
            auth_method=auth_method,
        )


def _exchange_code(
    *,
    client_id: str,
    code: str,
    redirect_uri: str,
    code_verifier: str,
    resource: str,
    ip_address: str | None,
    auth_method: str = "",
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
    # Narrowed again: a write decision revoked between the consent and the
    # exchange is honoured here, and a code never yields more than it names.
    scopes = narrow_scope(
        record_row.scope,
        write_allowed=may_write(user, grant)
        and write_enablement_covers(grant, record_row.created_at),
    )
    issued, _ = _issue_pair(
        grant=grant,
        user=user,
        client_id=record_row.client_id,
        scopes=scopes,
        resource=record_row.resource,
        family_id=family_id,
        now=now,
        family_started_at=now,
        auth_method=auth_method,
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
        scope=scopes,
    )
    return issued


def refresh(
    *,
    client_id: str,
    refresh_token: str,
    resource: str,
    scope: str,
    ip_address: str | None,
    auth_method: str = "",
) -> IssuedTokens | OAuthError:
    """``grant_type=refresh_token``, rotating the refresh token (RFC 6749 §6, OAuth 2.1 §4.3)."""
    with transaction.atomic():
        return _refresh(
            client_id=client_id,
            refresh_token=refresh_token,
            resource=resource,
            scope=scope,
            ip_address=ip_address,
            auth_method=auth_method,
        )


def _refresh(
    *,
    client_id: str,
    refresh_token: str,
    resource: str,
    scope: str,
    ip_address: str | None,
    auth_method: str = "",
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
        retried = _retried_rotation(token, refresh_token, now=now)
        if retried is not None:
            record(
                McpAccessEventType.TOKEN_REFRESHED,
                user=token.user,
                grant=token.grant,
                client_id=client_id,
                ip_address=ip_address,
                family=str(token.family_id),
                retry_within_grace=True,
            )
            return retried
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
            # A retry and a theft look alike but for the delay (MCPSEC-09).
            seconds_since_rotation=(
                round((now - token.revoked_at).total_seconds()) if token.revoked_at else None
            ),
        )
        return invalid
    if token.revoked_at is not None:
        return refuse("revoked")
    if token.expires_at <= now:
        return refuse("expired")
    family_started = token.family_started_at or token.issued_at
    if family_started + timedelta(seconds=family_max_seconds()) <= now:
        # However often it was refreshed, the connection is as old as the code
        # the person typed: they authorize again (MCPSEC-08).
        _revoke_tokens(
            McpToken.objects.filter(family_id=token.family_id), reason="family_expired", now=now
        )
        return refuse("family_expired")
    if token.auth_method and auth_method and token.auth_method != auth_method:
        # Bound to how the client authenticated at the code exchange: a family
        # begun with a signed assertion is not refreshed without one.
        return refuse("auth_method_changed")
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
    # Never wider than the token (checked above), and narrowed to what the
    # account may do *now*: after `revoke_write` the write scope drops out of
    # the family for good — getting it back means connecting again.
    scopes = narrow_scope(
        " ".join(sorted(requested or token.scope_set)),
        write_allowed=may_write(user, grant) and write_enablement_covers(grant, family_started),
    )
    issued, new_refresh = _issue_pair(
        grant=grant,
        user=user,
        client_id=token.client_id,
        scopes=scopes,
        resource=token.resource,
        family_id=token.family_id,
        now=now,
        family_started_at=family_started,
        auth_method=token.auth_method or auth_method,
        # Derived from the presented token, so a retry can be answered with
        # this very pair (`_retried_rotation`).
        raw_pair=credentials.successor_pair(refresh_token),
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
        scope=scopes,
    )
    return issued


def _retried_rotation(token: McpToken, refresh_token: str, *, now: datetime) -> IssuedTokens | None:
    """The pair this token's rotation issued, when presenting it again is a retry (MCPSEC-09).

    Only within `MCP_REFRESH_GRACE_SECONDS` of the rotation, only while the
    replacement refresh token is unused and live, and only for the client the
    token belongs to (checked before this is asked). The row is held under
    `select_for_update` by the caller, so two refreshes in flight take turns:
    the second finds the first's rotation and is answered with its pair.
    Anything else is reuse, and the caller ends the family.
    """
    grace = timedelta(seconds=int(getattr(settings, "MCP_REFRESH_GRACE_SECONDS", 30)))
    if token.revoked_at is None or now - token.revoked_at > grace:
        return None
    if token.replaced_by_id is None:
        return None
    successor = McpToken.objects.select_for_update().filter(pk=token.replaced_by_id).first()
    if (
        successor is None
        or successor.replaced_by_id is not None
        or successor.revoked_at is not None
        or successor.expires_at <= now
    ):
        return None
    access_raw, refresh_raw = credentials.successor_pair(refresh_token)
    if not credentials.same(successor.digest, credentials.digest(refresh_raw)):
        return None  # derived under another key: not this rotation's pair
    access = (
        McpToken.objects.filter(
            digest=credentials.digest(access_raw),
            kind=TokenKind.ACCESS,
            family_id=token.family_id,
            revoked_at__isnull=True,
        )
        .only("expires_at")
        .first()
    )
    if access is None or access.expires_at <= now:
        return None
    return IssuedTokens(
        access_token=access_raw,
        refresh_token=refresh_raw,
        expires_in=max(1, int((access.expires_at - now).total_seconds())),
        scope=successor.scopes,
        family_id=token.family_id,
    )


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
