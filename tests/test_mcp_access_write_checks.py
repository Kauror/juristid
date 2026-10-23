"""mcp_access.E004/E005 do not ask the write gateway for `web`'s MCP keys.

`mcp-writer` must not hold MCP_AUTHENTICATOR_KEY or MCP_TOKEN_DERIVATION_KEY
(juristid.E056), exactly as `mcp` must not (juristid.E046). Asking for them
there would make `manage.py check` fail on real data whichever way the
operator chose.
"""

from __future__ import annotations

import pytest

from app.mcp_access import checks
from tests import mcp_access_support as support

KEYS = {"mcp_access.E004", "mcp_access.E005"}


@pytest.fixture(autouse=True)
def real_data(settings):
    support.enable(settings)
    settings.REAL_DATA_ALLOWED = True
    settings.MCP_AUTHENTICATOR_KEY = ""
    settings.MCP_TOKEN_DERIVATION_KEY = ""


def _ids() -> set[str]:
    return {item.id for item in checks.mcp_access_settings()}


def test_web_on_real_data_without_the_keys_is_refused(settings):
    settings.JURISTID_PROCESS_ROLE = "web"
    assert _ids() >= KEYS


@pytest.mark.parametrize("role", ["mcp", "mcp_writer"])
def test_the_mcp_and_writer_processes_are_not_asked_for_the_keys(settings, role):
    settings.JURISTID_PROCESS_ROLE = role
    assert not _ids() & KEYS


def test_the_writer_role_is_read_from_its_setting(settings):
    settings.MCP_WRITER_PROCESS_ROLE = "kirjutaja"
    settings.JURISTID_PROCESS_ROLE = "kirjutaja"
    assert not _ids() & KEYS
