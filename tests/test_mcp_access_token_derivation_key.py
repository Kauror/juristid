"""A rotated refresh token's successor is derived under a key of its own (NEW-01).

`successor_pair` makes the refresh grace possible (MCPSEC-09): the pair a
rotation issues is computed from the presented token, so a retry can be
answered with it although only digests are stored. Under `SECRET_KEY`,
whoever held that key (a leaked juristid.env, an old backup) and one refresh
token could compute every later token of the family offline and never trip
reuse detection. So the chain is derived under `MCP_TOKEN_DERIVATION_KEY`, on
`web` only: required on real data (mcp_access.E005), refused in the `mcp`
process (juristid.E046), its own value.
"""

from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest
from django.conf import settings as django_settings

from app.core import checks as core_checks
from app.mcp_access import checks, credentials, services
from tests import factories
from tests import mcp_access_support as support

KEY = "d" * 20 + "-a-dedicated-token-derivation-key"
OTHER_KEY = "e" * 20 + "-another-token-derivation-key-xxx"
AUTHENTICATOR_KEY = "k" * 20 + "-a-dedicated-mcp-authenticator-key"
TOKEN = "A" * 43
CHECKER = Path(django_settings.BASE_DIR) / "scripts" / "ci" / "assert_service_env_isolation.py"


def _isolation():
    spec = importlib.util.spec_from_file_location("assert_service_env_isolation", CHECKER)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture(autouse=True)
def enabled(settings):
    support.enable(settings)
    settings.MCP_AUTHENTICATOR_KEY = AUTHENTICATOR_KEY
    settings.MCP_TOKEN_DERIVATION_KEY = KEY


def _ids(found):
    return {item.id for item in found}


# -- the derivation -----------------------------------------------------------------


def test_the_chain_does_not_follow_from_secret_key(settings):
    pair = credentials.successor_pair(TOKEN)
    settings.SECRET_KEY = "a-rotated-or-leaked-django-secret-key-" + "x" * 40
    assert credentials.successor_pair(TOKEN) == pair
    settings.MCP_TOKEN_DERIVATION_KEY = ""
    assert credentials.successor_pair(TOKEN) != pair, "SECRET_KEY alone yields another chain"


def test_the_chain_follows_the_derivation_key(settings):
    pair = credentials.successor_pair(TOKEN)
    assert credentials.successor_pair(TOKEN) == pair, "deterministic: a retry gets the same pair"
    settings.MCP_TOKEN_DERIVATION_KEY = OTHER_KEY
    assert credentials.successor_pair(TOKEN) != pair
    assert all(credentials.opaque_digest(raw) for raw in credentials.successor_pair(TOKEN))


@pytest.mark.django_db
def test_a_retried_refresh_still_gets_the_same_pair_under_the_key(monkeypatch):
    moment = support.freeze_totp(monkeypatch)
    user = factories.UserFactory()
    _, secret = support.enrolled(user, moment)
    issued = support.tokens_for(user, secret, moment)

    def refresh():
        return services.refresh(
            client_id=support.PUBLIC_CLIENT,
            refresh_token=issued.refresh_token,
            resource="",
            scope="",
            ip_address=None,
        )

    first, second = refresh(), refresh()
    assert isinstance(first, services.IssuedTokens) and isinstance(second, services.IssuedTokens)
    assert (first.access_token, first.refresh_token) == (second.access_token, second.refresh_token)
    assert (first.access_token, first.refresh_token) == credentials.successor_pair(
        issued.refresh_token
    )


# -- mcp_access.E005 ------------------------------------------------------------------


@pytest.mark.parametrize(
    "key",
    ["", "short", "replace-me-with-openssl-rand-base64-48-xxxxxxx"],
    ids=["empty", "short", "placeholder"],
)
def test_real_data_needs_a_proper_derivation_key(settings, key):
    settings.REAL_DATA_ALLOWED = True
    settings.MCP_TOKEN_DERIVATION_KEY = key
    assert "mcp_access.E005" in _ids(checks.mcp_access_settings())


@pytest.mark.parametrize(
    "other", ["SECRET_KEY", "LOCAL_AUTH_MFA_ENCRYPTION_KEY", "MCP_AUTHENTICATOR_KEY"]
)
def test_the_derivation_key_is_its_own_value(settings, other):
    settings.REAL_DATA_ALLOWED = True
    setattr(settings, other, KEY)
    assert "mcp_access.E005" in _ids(checks.mcp_access_settings())


def test_a_dedicated_derivation_key_satisfies_the_check(settings):
    settings.REAL_DATA_ALLOWED = True
    assert not _ids(checks.mcp_access_settings()) & {"mcp_access.E004", "mcp_access.E005"}


def test_off_real_data_in_the_mcp_process_or_switched_off_it_is_not_required(settings):
    settings.MCP_TOKEN_DERIVATION_KEY = ""
    assert "mcp_access.E005" not in _ids(checks.mcp_access_settings())
    settings.REAL_DATA_ALLOWED = True
    settings.JURISTID_PROCESS_ROLE = "mcp"
    assert "mcp_access.E005" not in _ids(checks.mcp_access_settings())
    settings.JURISTID_PROCESS_ROLE = ""
    settings.MCP_ENABLED = False
    assert checks.mcp_access_settings() == []


# -- never in the mcp process ----------------------------------------------------------


def test_the_mcp_process_must_not_hold_the_derivation_key(settings):
    settings.JURISTID_PROCESS_ROLE = "mcp"
    problems = core_checks._process_role_problems()
    assert any(p.id == "juristid.E046" and "MCP_TOKEN_DERIVATION_KEY" in p.msg for p in problems)


def test_the_derivation_key_is_a_browser_only_secret_everywhere_it_is_listed():
    isolation = _isolation()
    assert "MCP_TOKEN_DERIVATION_KEY" in isolation.BROWSER_ONLY
    assert "MCP_TOKEN_DERIVATION_KEY" not in isolation.MCP_ALLOWED
    root = Path(django_settings.BASE_DIR)
    preflight = (root / "scripts" / "deploy" / "juristid-deploy-preflight.sh").read_text(
        encoding="utf-8"
    )
    [deny] = [line for line in preflight.splitlines() if "CONTROL_PLANE_API_KEY MCP_" in line]
    assert "MCP_TOKEN_DERIVATION_KEY" in deny.split()
    example = (root / "deploy" / "unraid-main" / ".env.example").read_text(encoding="utf-8")
    assert "# MCP_TOKEN_DERIVATION_KEY=replace-me" in example
    mcp_example = (root / "deploy" / "unraid-main" / "juristid-mcp.env.example").read_text(
        encoding="utf-8"
    )
    assert "MCP_TOKEN_DERIVATION_KEY" not in mcp_example
