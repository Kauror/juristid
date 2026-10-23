"""`services.confirm_write_code`: the approval page's code check is the authorize step's.

The same authenticator, the same single-use time step, the same lock ladder
and the same per-session and per-address throttle rows as `/oauth/authorize`
(MCPSEC-01): a wrong code typed on the approval page counts exactly like one
typed at the consent step, and a code spent at one cannot be spent at the
other.
"""

from __future__ import annotations

import uuid
from datetime import timedelta
from types import SimpleNamespace

import pytest
from django.utils import timezone

from app.accounts import mfa
from app.accounts.models import User
from app.mcp_access import services
from app.mcp_access.models import (
    McpAccessEvent,
    McpAccessEventType,
    McpAuthenticator,
    McpAuthorizeThrottle,
)
from tests import factories
from tests import mcp_access_support as support

pytestmark = pytest.mark.django_db

SESSION = services.ThrottleKey(key="session-key", limit=8)
ADDRESS = services.ThrottleKey(key="address-key", limit=30)


@pytest.fixture(autouse=True)
def writes_on(settings):
    support.enable(settings)
    settings.MCP_WRITES_ENABLED = True


@pytest.fixture
def moment(monkeypatch):
    return support.freeze_totp(monkeypatch)


@pytest.fixture
def writer(moment):
    user = factories.UserFactory()
    grant, secret = support.enrolled(user, moment)
    services.grant_write(user=user, provenance=support.OPERATOR)
    grant.refresh_from_db()
    return user, grant, secret


def confirm(user, grant, code, *, throttle=(SESSION, ADDRESS)):
    return services.confirm_write_code(
        user=user, grant_id=grant.pk, code=code, ip_address="192.0.2.7", throttle=list(throttle)
    )


def _events(event_type: str) -> list[McpAccessEvent]:
    return list(McpAccessEvent.objects.filter(event_type=event_type).order_by("created_at"))


def test_the_right_code_confirms_once(writer, moment):
    user, grant, secret = writer
    code = support.code_at(secret, moment)

    assert confirm(user, grant, code) == services.AuthorizationOutcome(status="ok")

    [event] = _events(McpAccessEventType.WRITE_CONFIRMED)
    assert event.grant_id == grant.pk and event.ip_address == "192.0.2.7"
    assert code not in str(event.detail)
    assert McpAuthenticator.objects.get(grant=grant).last_used_step > 0
    # The same code again: its step is spent.
    assert confirm(user, grant, code).status == "bad_code"


def test_a_code_spent_connecting_cannot_approve(writer, moment):
    user, grant, secret = writer
    support.code_for(user, secret, moment)  # spends step 0 at /oauth/authorize
    assert confirm(user, grant, support.code_at(secret, moment)).status == "bad_code"
    assert confirm(user, grant, support.code_at(secret, moment, 1)).status == "ok"


def test_a_code_spent_approving_cannot_connect(writer, moment):
    user, grant, secret = writer
    assert confirm(user, grant, support.code_at(secret, moment)).status == "ok"
    outcome = services.authorize(
        user=user,
        request=support.request_for(),
        totp_code=support.code_at(secret, moment),
        ip_address=None,
    )
    assert outcome.status == "bad_code"


def test_an_earlier_step_is_refused_after_a_later_one(writer, moment):
    user, grant, secret = writer
    assert confirm(user, grant, support.code_at(secret, moment, 1)).status == "ok"
    assert confirm(user, grant, support.code_at(secret, moment)).status == "bad_code"


def test_wrong_codes_here_and_at_authorize_share_one_lock_ladder(writer, moment):
    user, grant, secret = writer
    for _ in range(3):
        outcome = services.authorize(
            user=user, request=support.request_for(), totp_code="000000", ip_address=None
        )
        assert outcome.status == "bad_code"
    assert confirm(user, grant, "000000").status == "bad_code"
    assert confirm(user, grant, "000001").status == "bad_code"  # the fifth: locks

    grant.refresh_from_db()
    assert grant.locked_until is not None and grant.lock_count == 1
    assert grant.total_failed_attempts == 5
    assert len(_events(McpAccessEventType.GRANT_LOCKED)) == 1
    assert len(_events(McpAccessEventType.WRITE_CONFIRMATION_REFUSED)) == 2
    # Even the right code learns only that it is locked, and approves nothing.
    locked = confirm(user, grant, support.code_at(secret, moment))
    assert locked.status == "locked" and locked.wait > 0
    assert not _events(McpAccessEventType.WRITE_CONFIRMED)


def test_the_ladder_escalates_to_suspension_through_this_page_too(writer, moment):
    user, grant, _ = writer
    for rung in range(len(services.LOCK_LADDER_SECONDS) + 1):
        for _ in range(services.MAX_FAILED_CODES):
            assert confirm(user, grant, "999999", throttle=()).status == "bad_code"
        grant.refresh_from_db()
        if rung < len(services.LOCK_LADDER_SECONDS):
            grant.locked_until = timezone.now() - timedelta(seconds=1)
            grant.save(update_fields=["locked_until"])
    grant.refresh_from_db()
    assert grant.is_suspended
    assert len(_events(McpAccessEventType.GRANT_SUSPENDED)) == 1


def test_a_right_code_resets_the_consecutive_count(writer, moment):
    user, grant, secret = writer
    for _ in range(4):
        confirm(user, grant, "123123", throttle=())
    assert confirm(user, grant, support.code_at(secret, moment), throttle=()).status == "ok"
    grant.refresh_from_db()
    assert grant.failed_attempts == 0 and grant.total_failed_attempts == 0


def test_a_suspended_grant_says_so_only_to_its_own_code(writer, moment):
    user, grant, secret = writer
    grant.suspended_at = timezone.now()
    grant.save(update_fields=["suspended_at"])
    assert confirm(user, grant, "000000").status == "bad_code"
    assert confirm(user, grant, support.code_at(secret, moment)).status == "suspended"


def test_wrong_codes_are_charged_to_the_shared_throttle_rows(writer, moment):
    user, grant, secret = writer
    tight = services.ThrottleKey(key="tight-session", limit=2)
    assert confirm(user, grant, "000000", throttle=[tight]).status == "bad_code"
    assert confirm(user, grant, "000000", throttle=[tight]).status == "bad_code"
    blocked = confirm(user, grant, support.code_at(secret, moment), throttle=[tight])
    assert blocked.status == "throttled" and blocked.wait > 0
    assert McpAuthorizeThrottle.objects.get(key="tight-session").failures == 2
    # The throttled attempt had no code checked: the right code is still unspent.
    assert confirm(user, grant, support.code_at(secret, moment)).status == "ok"
    reasons = [
        e.detail.get("reason") for e in _events(McpAccessEventType.WRITE_CONFIRMATION_REFUSED)
    ]
    assert "throttled" in reasons


def test_failures_at_authorize_count_against_the_approval_page_in_the_same_session(writer, moment):
    user, grant, secret = writer
    tight = services.ThrottleKey(key="shared-session", limit=2)
    for _ in range(2):
        outcome = services.authorize_attempt(
            user=user,
            request=support.request_for(),
            totp_code="000000",
            ip_address=None,
            throttle=[tight],
        )
        assert outcome.status == "bad_code"
    assert confirm(user, grant, support.code_at(secret, moment), throttle=[tight]).status == (
        "throttled"
    )


def test_a_grant_without_write_gets_no_grant_without_a_code_being_checked(moment):
    user = factories.UserFactory()
    grant, secret = support.enrolled(user, moment)
    code = support.code_at(secret, moment)
    assert confirm(user, grant, code).status == "no_grant"
    grant.refresh_from_db()
    assert grant.failed_attempts == 0
    [event] = _events(McpAccessEventType.WRITE_CONFIRMATION_REFUSED)
    assert event.detail["reason"] == "write_not_allowed"
    # Unspent: the same code still connects.
    outcome = services.authorize(
        user=user, request=support.request_for(), totp_code=code, ip_address=None
    )
    assert outcome.status == "ok"


def test_revoke_write_refuses_confirmation_at_once(writer, moment):
    user, grant, secret = writer
    services.revoke_write(user=user, reason="test", provenance=support.OPERATOR)
    assert confirm(user, grant, support.code_at(secret, moment)).status == "no_grant"


def test_writes_switched_off_refuse_confirmation(writer, moment, settings):
    user, grant, secret = writer
    settings.MCP_WRITES_ENABLED = False
    assert confirm(user, grant, support.code_at(secret, moment)).status == "no_grant"


def test_another_personas_grant_cannot_be_confirmed(writer, moment):
    _, grant, secret = writer
    other = factories.UserFactory()
    support.enrolled(other, moment)
    services.grant_write(user=other, provenance=support.OPERATOR)
    outcome = confirm(other, grant, support.code_at(secret, moment))
    assert outcome.status == "no_grant"


def test_an_unknown_grant_id_gets_no_grant(writer, moment):
    user, _, secret = writer
    outcome = services.confirm_write_code(
        user=user,
        grant_id=uuid.uuid4(),
        code=support.code_at(secret, moment),
        ip_address=None,
        throttle=[],
    )
    assert outcome.status == "no_grant"


@pytest.mark.parametrize("role", ["READER", "ADMINISTRATOR"])
def test_a_role_change_refuses_confirmation(writer, moment, role):
    user, grant, secret = writer
    User.objects.filter(pk=user.pk).update(role=role)
    user.refresh_from_db()
    assert confirm(user, grant, support.code_at(secret, moment)).status == "no_grant"


def test_a_deactivated_account_cannot_confirm(writer, moment):
    user, grant, secret = writer
    User.objects.filter(pk=user.pk).update(is_active=False)
    assert confirm(user, grant, support.code_at(secret, moment)).status == "no_grant"


def test_a_later_step_is_accepted_when_the_clock_moves_on(writer, moment, monkeypatch):
    user, grant, secret = writer
    assert confirm(user, grant, support.code_at(secret, moment)).status == "ok"
    later = moment + timedelta(minutes=5)
    monkeypatch.setattr(mfa, "timezone", SimpleNamespace(now=lambda: later))
    assert confirm(user, grant, support.code_at(secret, later)).status == "ok"
    assert len(_events(McpAccessEventType.WRITE_CONFIRMED)) == 2
