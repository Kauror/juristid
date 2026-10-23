"""The write side's questions to `app.mcp_access`, asked in one place (CONTRACT §3).

The write gateway, the commit and the approval page ask the authorization
server three things: *may this verified token write* (`writer_for`), *may this
account write through this grant now* (`may_write`), and *is this the grant's
current authenticator code* (`confirm_write_code`). `app.mcp_access` answers
all three; this module only looks them up at call time, so

* a test can stand in for any of them with ``monkeypatch.setattr`` on
  `app.mcp_access.verification` / `app.mcp_access.services`, and
* a build in which one is missing **fails closed**: no writer, no write right,
  no approval — never an accidental yes.

Every answer is also gated by `writes_enabled()`: with `MCP_WRITES_ENABLED`
off nothing here says yes, whatever the authorization server would say.
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any

from django.conf import settings

from app.mcp_access import config, verification
from app.mcp_access import services as access_services


def writes_enabled() -> bool:
    """MCP access on (with its public origin) and MCP writes on."""
    return config.is_enabled() and bool(getattr(settings, "MCP_WRITES_ENABLED", False))


def write_scope() -> str:
    return str(getattr(settings, "MCP_WRITE_SCOPE", "juristid.write"))


def verify_access_token(raw_token: str) -> Any:
    """The token's `VerifiedAccess`, or None. A pure read."""
    return verification.verify_access_token(raw_token)


def writer_for(access: Any) -> Any:
    """The account that may write with this verified token now, or None."""
    if not writes_enabled():
        return None
    check = getattr(verification, "writer_for", None)
    if check is None:
        return None
    return check(access)


def may_write(user: Any, grant: Any) -> bool:
    """Whether ``user`` may write through ``grant`` right now."""
    if not writes_enabled() or user is None or grant is None:
        return False
    check = getattr(access_services, "may_write", None)
    if check is None:
        return False
    return bool(check(user, grant))


def family_id(access: Any) -> Any:
    """The token family the verified token belongs to, if verification reports it."""
    return getattr(access, "family_id", None)


#: What `confirm_write_code` answers when the authorization server offers no
#: such step: nothing is approved.
_NO_CONFIRMATION = access_services.AuthorizationOutcome(status="no_grant")


def confirm_write_code(
    *,
    user: Any,
    grant_id: Any,
    code: str,
    ip_address: str | None,
    throttle: Sequence[access_services.ThrottleKey],
) -> access_services.AuthorizationOutcome:
    """Check the grant's personal authenticator code for one approval."""
    if not writes_enabled():
        return _NO_CONFIRMATION
    confirm = getattr(access_services, "confirm_write_code", None)
    if confirm is None:
        return _NO_CONFIRMATION
    outcome = confirm(
        user=user, grant_id=grant_id, code=code, ip_address=ip_address, throttle=throttle
    )
    return (
        outcome if isinstance(outcome, access_services.AuthorizationOutcome) else _NO_CONFIRMATION
    )
