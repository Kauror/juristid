"""Bearer-token verification for the `mcp` and `mcp-writer` processes (docs/adr/0185 §5).

Pure reads. The `mcp` process opens its database connections with
``default_transaction_read_only=on`` and these functions work there: they
never write — not ``last_used_at``, not an audit row, not the cache.

A token is looked up by the SHA-256 of what the client sent, so the database
compares digests, never the token itself, and a token another issuer minted is
simply a digest nobody has. Every refusal is the same ``None``: the caller
learns nothing about *why* a token failed.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import datetime

from django.utils import timezone

from app.accounts.models import User
from app.mcp_access import config, credentials
from app.mcp_access.models import GrantStatus, McpAccessGrant, McpToken, TokenKind
from app.mcp_access.services import (
    may_connect,
    may_write,
    write_enablement_covers,
    write_scope,
)

#: What `VerifiedAccess.family_id` holds when a caller built one by hand
#: (test fakes): no token has it, so `writer_for` refuses such an access.
NO_FAMILY = uuid.UUID(int=0)


@dataclass(frozen=True)
class VerifiedAccess:
    token_id: uuid.UUID
    user_id: uuid.UUID
    grant_id: uuid.UUID
    client_id: str
    scopes: frozenset[str]
    resource: str
    expires_at: datetime  # aware UTC
    #: The token family (one authorization and its rotations). A write
    #: proposal is bound to it, so a token from another connection of the same
    #: person cannot commit it.
    family_id: uuid.UUID = NO_FAMILY


def _token_passes(token: McpToken, *, now: datetime) -> bool:
    if token.kind != TokenKind.ACCESS or token.revoked_at is not None:
        return False
    if token.expires_at <= now:
        return False
    if not config.resource_is_allowed(token.resource):
        return False
    return config.scope() in token.scope_set


def _account_passes(token: McpToken, grant: McpAccessGrant | None, user: User | None) -> bool:
    if grant is None or user is None or grant.status != GrantStatus.ACTIVE:
        return False
    if not may_connect(user, grant):
        return False
    return user.security_epoch == token.user_epoch


def verify_access_token(raw_token: str, *, now: datetime | None = None) -> VerifiedAccess | None:
    """None unless the token names a live ACCESS token for an account that may still read.

    The SHA-256 of ``raw_token`` must name an ACCESS token that is not revoked
    and not expired, whose resource is in `MCP_RESOURCE_URLS` (normalised, no
    trailing slash) and whose scopes include `MCP_SCOPE`; its grant must be
    ACTIVE; the account active, `may_read_business_content` true for it, and
    its ``security_epoch`` the one recorded on the token. Also None when MCP
    access is switched off. Never writes.
    """
    if not config.is_enabled():
        return None
    token_digest = credentials.opaque_digest(raw_token)
    if token_digest is None:
        return None
    token = (
        McpToken.objects.select_related("grant", "user")
        .filter(digest=token_digest, kind=TokenKind.ACCESS)
        .first()
    )
    moment = now or timezone.now()
    if token is None or not _token_passes(token, now=moment):
        return None
    if not _account_passes(token, token.grant, token.user):
        return None
    return VerifiedAccess(
        token_id=token.pk,
        user_id=token.user_id,
        grant_id=token.grant_id,
        client_id=token.client_id,
        scopes=token.scope_set,
        resource=token.resource,
        expires_at=token.expires_at,
        family_id=token.family_id,
    )


def _afresh(access: VerifiedAccess) -> tuple[McpToken, McpAccessGrant, User] | None:
    """The token, grant and account re-read, if every check still passes for them."""
    if not config.is_enabled():
        return None
    token = McpToken.objects.filter(pk=access.token_id, kind=TokenKind.ACCESS).first()
    if token is None or token.user_id != access.user_id or token.grant_id != access.grant_id:
        return None
    if not _token_passes(token, now=timezone.now()):
        return None
    grant = McpAccessGrant.objects.filter(pk=access.grant_id).first()
    user = User.objects.filter(pk=access.user_id).first()
    if grant is None or user is None or not _account_passes(token, grant, user):
        return None
    return token, grant, user


def reader_for(access: VerifiedAccess) -> User | None:
    """The account to pass to the readers, with every check above applied afresh.

    Re-reads the token, the grant and the account rather than trusting the
    `VerifiedAccess` it was handed, so a revocation, a role change or a
    deactivation between verifying a token and running a tool is honoured.
    """
    fresh = _afresh(access)
    return fresh[2] if fresh is not None else None


def writer_for(access: VerifiedAccess) -> User | None:
    """The account a write step may act for, or None. Pure reads; asked at every call.

    Everything `reader_for` asks, re-read now, and: the token's family is the
    one `access` names, the token itself carries `MCP_WRITE_SCOPE` (granted only
    at consent, never widened), and `services.may_write` holds — the grant's
    write decision stands, the account has a business-write role, and
    `MCP_WRITES_ENABLED` is on in *this* process — and the token's family
    began at or after the grant's current ``write_enabled_at``. Nothing is cached, so
    ``revoke-write``, ``revoke-all-writes``, a revoked grant, a deactivation,
    a role change or a security-epoch bump answer None at the very next call.
    """
    fresh = _afresh(access)
    if fresh is None:
        return None
    token, grant, user = fresh
    if token.family_id != access.family_id:
        return None
    if write_scope() not in token.scope_set:
        return None
    if not may_write(user, grant):
        return None
    # A family begun under an earlier write enablement is not honoured after
    # revoke-write and a later grant-write (P3-5): the person connects again.
    if not write_enablement_covers(grant, token.family_started_at or token.issued_at):
        return None
    return user
