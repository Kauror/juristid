"""A break-glass administrator cannot use an MCP grant (MCPSEC-06, docs/adr/0185 §3).

`grant_refusal` requires a business *role*, so an administrator holding a
temporary break-glass grant is never given a standing AI connection. Every
later step — authorizing, exchanging a code, refreshing, verifying a token —
asks `may_connect`, which used to ask only `may_read_business_content`, and
that is true during break-glass. A granted specialist made ADMINISTRATOR
could therefore authorize and keep refreshing until break-glass ended. Now
`may_connect` asks the same question `grant_refusal` asks.
"""

from __future__ import annotations

import uuid

import pytest

from app.accounts.enums import UserRole
from app.accounts.models import User
from app.core import authorization
from app.mcp_access import services, verification
from tests import factories
from tests import mcp_access_support as support

pytestmark = pytest.mark.django_db


@pytest.fixture(autouse=True)
def enabled(settings):
    support.enable(settings)


@pytest.fixture
def moment(monkeypatch):
    return support.freeze_totp(monkeypatch)


@pytest.fixture
def break_glass(monkeypatch):
    """Every account holds an active break-glass grant, as far as the predicate can tell."""
    monkeypatch.setattr(authorization, "active_break_glass_grant_id", lambda user: uuid.uuid4())


def _now_administrator(user) -> User:
    # The role changes without an epoch bump, so only `may_connect` stands between.
    User.objects.filter(pk=user.pk).update(role=UserRole.ADMINISTRATOR)
    return User.objects.get(pk=user.pk)


def test_a_break_glass_administrator_cannot_authorize(moment, break_glass):
    user = factories.UserFactory()
    grant, secret = support.enrolled(user, moment)
    admin = _now_administrator(user)
    assert authorization.may_read_business_content(admin)

    outcome = services.authorize(
        user=admin,
        request=support.request_for(),
        totp_code=support.code_at(secret, moment),
        ip_address=None,
    )
    assert outcome.status == "no_grant"
    assert services.may_connect(admin, grant) is False


def test_a_break_glass_administrator_cannot_use_or_refresh_tokens(moment, break_glass):
    user = factories.UserFactory()
    _, secret = support.enrolled(user, moment)
    issued = support.tokens_for(user, secret, moment)
    _now_administrator(user)

    assert verification.verify_access_token(issued.access_token) is None
    refreshed = services.refresh(
        client_id=support.PUBLIC_CLIENT,
        refresh_token=issued.refresh_token,
        resource="",
        scope="",
        ip_address=None,
    )
    assert isinstance(refreshed, services.OAuthError)


def test_a_specialist_still_connects(moment):
    user = factories.UserFactory()
    grant, _ = support.enrolled(user, moment)
    assert services.may_connect(User.objects.get(pk=user.pk), grant) is True
