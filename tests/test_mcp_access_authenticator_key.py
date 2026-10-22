"""MCP authenticators are sealed under a key of their own (MCPSEC-07, docs/adr/0185 §3).

On the shared-gate stack the MFA key is unset, so without `MCP_AUTHENTICATOR_KEY`
every MCP authenticator is sealed under `SECRET_KEY`, and a routine rotation of
that key would make all of them unreadable — and every attempt would then be
counted as a wrong code, locking each colleague out with no event saying why.
"""

from __future__ import annotations

import logging

import pytest

from app.accounts import mfa
from app.core import checks as core_checks
from app.mcp_access import checks, credentials, services
from app.mcp_access.models import McpAccessEvent, McpAccessEventType, McpAccessGrant
from tests import factories
from tests import mcp_access_support as support

pytestmark = pytest.mark.django_db

KEY = "k" * 20 + "-a-dedicated-mcp-authenticator-key"
OTHER_KEY = "o" * 20 + "-another-mcp-authenticator-key-xx"


@pytest.fixture(autouse=True)
def enabled(settings):
    support.enable(settings)
    settings.MCP_AUTHENTICATOR_KEY = KEY


@pytest.fixture
def moment(monkeypatch):
    return support.freeze_totp(monkeypatch)


def test_a_secret_sealed_under_the_key_opens_only_under_it(settings):
    sealed = credentials.seal("JBSWY3DPEHPK3PXP")
    assert credentials.unseal(sealed) == "JBSWY3DPEHPK3PXP"
    # Not the MFA derivation's form: it does not open as one.
    assert mfa.unseal(sealed, purpose=credentials.AUTHENTICATOR_PURPOSE) is None
    settings.MCP_AUTHENTICATOR_KEY = OTHER_KEY
    assert credentials.unseal(sealed) is None


def test_the_personal_sign_in_encryption_is_untouched(settings):
    before = mfa._encrypt("JBSWY3DPEHPK3PXP")
    settings.MCP_AUTHENTICATOR_KEY = OTHER_KEY
    assert mfa._decrypt(before) == "JBSWY3DPEHPK3PXP"


def test_rotating_secret_key_leaves_mcp_authenticators_working(settings, moment):
    user = factories.UserFactory()
    _, secret = support.enrolled(user, moment)
    settings.SECRET_KEY = "a-rotated-django-secret-key-" + "x" * 40
    assert support.code_for(user, secret, moment)


def test_an_unreadable_authenticator_is_not_counted_as_wrong_codes(settings, moment, caplog):
    user = factories.UserFactory()
    grant, secret = support.enrolled(user, moment)
    settings.MCP_AUTHENTICATOR_KEY = OTHER_KEY
    caplog.set_level(logging.WARNING, logger="app.mcp_access")
    for steps in range(6):
        outcome = services.authorize(
            user=user,
            request=support.request_for(),
            totp_code=support.code_at(secret, moment, steps % 2),
            ip_address=None,
        )
        assert outcome.status == "unreadable"
    grant = McpAccessGrant.objects.get(pk=grant.pk)
    assert (grant.failed_attempts, grant.total_failed_attempts, grant.lock_count) == (0, 0, 0)
    assert grant.locked_until is None
    reasons = McpAccessEvent.objects.filter(
        event_type=McpAccessEventType.AUTHORIZATION_REFUSED
    ).values_list("detail__reason", flat=True)
    assert set(reasons) == {"authenticator_unreadable"}
    assert any("cannot be read" in record.getMessage() for record in caplog.records)


# -- mcp_access.E004 and juristid.E046 -----------------------------------------------


def _ids(found):
    return {item.id for item in found}


@pytest.mark.parametrize("key", ["", "short", "replace-me-with-openssl-rand-base64-48-xxxxxxx"])
def test_real_data_needs_a_proper_dedicated_key(settings, key):
    settings.REAL_DATA_ALLOWED = True
    settings.MCP_AUTHENTICATOR_KEY = key
    assert "mcp_access.E004" in _ids(checks.mcp_access_settings())


def test_the_key_may_not_be_the_secret_key_or_the_mfa_key(settings):
    settings.REAL_DATA_ALLOWED = True
    for other in ("SECRET_KEY", "LOCAL_AUTH_MFA_ENCRYPTION_KEY"):
        setattr(settings, other, KEY)
        assert "mcp_access.E004" in _ids(checks.mcp_access_settings()), other
        setattr(settings, other, "z" * 60)


def test_a_dedicated_key_satisfies_the_check(settings):
    settings.REAL_DATA_ALLOWED = True
    assert "mcp_access.E004" not in _ids(checks.mcp_access_settings())


def test_off_real_data_or_in_the_mcp_process_the_key_is_not_required(settings):
    settings.MCP_AUTHENTICATOR_KEY = ""
    assert "mcp_access.E004" not in _ids(checks.mcp_access_settings())
    settings.REAL_DATA_ALLOWED = True
    settings.JURISTID_PROCESS_ROLE = "mcp"
    assert "mcp_access.E004" not in _ids(checks.mcp_access_settings())


def test_the_mcp_process_must_not_hold_the_key(settings):
    settings.JURISTID_PROCESS_ROLE = "mcp"
    settings.MCP_AUTHENTICATOR_KEY = KEY
    problems = core_checks._process_role_problems()
    assert any(p.id == "juristid.E046" and "MCP_AUTHENTICATOR_KEY" in p.msg for p in problems)
