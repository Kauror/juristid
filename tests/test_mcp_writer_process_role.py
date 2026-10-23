"""The `mcp_writer` process role (docs/adr/0189) and the write settings of the other processes.

1. In role `mcp_writer` the settings bound every statement and keep no
   connection — and do NOT make the connection read-only (writing is its job;
   the role's privileges are what bound it, juristid.E050).
2. The system checks start the write gateway without any browser secret and
   refuse it what it needs instead (juristid.E051–E056).
3. `mcp` holds the gateway key only when writes are on, and never the writer's
   database role (E052, E058); `web` and the workers never hold the key (E057).
"""

from __future__ import annotations

import pytest

from app.core.checks import check_runtime_safety
from tests.test_mcp_process_role import (
    LONG_DATABASE_PASSWORD,
    LONG_SECRET_KEY,
    READ_ONLY_OPTIONS,
    _settings_under,
)

GATEWAY_KEY = "g" * 64
WRITER_OPTIONS = "-c statement_timeout=30000"


def _ids() -> set[str]:
    return {problem.id for problem in check_runtime_safety(None)}


# -- 1. the settings ----------------------------------------------------------------


def test_the_writer_role_bounds_statements_keeps_no_connection_and_is_not_read_only():
    loaded = _settings_under(
        {"JURISTID_PROCESS_ROLE": "mcp_writer", "MCP_WRITE_CALL_TIMEOUT_SECONDS": "20"}
    )
    assert loaded["role"] == "mcp_writer"
    assert loaded["conn_max_age"] == 0
    assert loaded["options"] == {"sslmode": "disable", "options": "-c statement_timeout=20000"}
    assert "read_only" not in str(loaded["options"])


# -- 2. the write gateway's checks ----------------------------------------------------


@pytest.fixture
def writer_process(settings, monkeypatch):
    """A complete, valid real-data configuration of the write gateway."""
    settings.DEBUG = False
    settings.DEV_LOGIN_ENABLED = False
    settings.REAL_DATA_ALLOWED = True
    settings.AUTH_MODE = "shared_gate"
    settings.SECRET_KEY = LONG_SECRET_KEY
    settings.SHARED_GATE_PASSWORD = ""
    settings.LOCAL_AUTH_MFA_ENCRYPTION_KEY = ""
    settings.EMAIL_HOST_PASSWORD = ""
    settings.MCP_AUTHENTICATOR_KEY = ""
    settings.MCP_TOKEN_DERIVATION_KEY = ""
    settings.TRUST_CF_CONNECTING_IP = False
    if hasattr(settings, "SECURE_PROXY_SSL_HEADER"):
        del settings.SECURE_PROXY_SSL_HEADER
    settings.JURISTID_PROCESS_ROLE = "mcp_writer"
    settings.MCP_ENABLED = True
    settings.MCP_WRITES_ENABLED = True
    settings.MCP_WRITE_GATEWAY_KEY = GATEWAY_KEY
    settings.JURISTID_PUBLIC_ORIGIN = "https://juristid.orgusaar.ee"
    settings.MCP_RESOURCE_URLS = ["https://api.openai.com/v1/mcp/tunnel_abc"]
    monkeypatch.setitem(settings.DATABASES["default"], "USER", "juristid_mcp_writer")
    monkeypatch.setitem(settings.DATABASES["default"], "PASSWORD", LONG_DATABASE_PASSWORD)
    monkeypatch.setitem(
        settings.DATABASES["default"],
        "OPTIONS",
        {"sslmode": "disable", "options": WRITER_OPTIONS},
    )
    return settings


def test_a_valid_writer_passes_without_any_browser_secret(writer_process):
    assert check_runtime_safety(None) == []


@pytest.mark.parametrize(
    ("change", "expected"),
    [
        ({"MCP_WRITES_ENABLED": False}, "juristid.E051"),
        ({"MCP_WRITE_GATEWAY_KEY": ""}, "juristid.E052"),
        ({"MCP_WRITE_GATEWAY_KEY": "short-key"}, "juristid.E052"),
        (
            {"MCP_WRITE_GATEWAY_KEY": "replace-me-with-openssl-rand-base64-48-xxxxxxxx"},
            "juristid.E052",
        ),
        ({"MCP_WRITE_GATEWAY_KEY": LONG_SECRET_KEY}, "juristid.E052"),
        ({"JURISTID_PUBLIC_ORIGIN": ""}, "juristid.E053"),
        ({"JURISTID_PUBLIC_ORIGIN": "http://juristid.orgusaar.ee"}, "juristid.E053"),
        ({"JURISTID_PUBLIC_ORIGIN": "https://juristid.orgusaar.ee/konto/"}, "juristid.E053"),
        ({"MCP_ENABLED": False}, "juristid.E054"),
        ({"MCP_RESOURCE_URLS": []}, "juristid.E054"),
        ({"MCP_RESOURCE_URLS": ["http://tunnel.example.net/mcp"]}, "juristid.E054"),
        ({"DEBUG": True}, "juristid.E055"),
        ({"SHARED_GATE_PASSWORD": "a-real-gate-password-x"}, "juristid.E056"),
        ({"LOCAL_AUTH_MFA_ENCRYPTION_KEY": "m" * 48}, "juristid.E056"),
        ({"EMAIL_HOST_PASSWORD": "mail-password"}, "juristid.E056"),
        ({"MCP_AUTHENTICATOR_KEY": "a" * 48}, "juristid.E056"),
        ({"MCP_TOKEN_DERIVATION_KEY": "d" * 48}, "juristid.E056"),
    ],
)
def test_each_missing_or_forbidden_piece_is_refused(writer_process, change, expected):
    for name, value in change.items():
        setattr(writer_process, name, value)
    assert expected in _ids()


def test_a_refusal_names_the_secret_never_its_value(writer_process):
    writer_process.SHARED_GATE_PASSWORD = "the-gate-password-value-x"  # noqa: S105
    messages = " ".join(problem.msg for problem in check_runtime_safety(None))
    assert "JURISTID_SHARED_GATE_PASSWORD" in messages
    assert "the-gate-password-value-x" not in messages


def test_the_writer_skips_the_browsers_own_checks(writer_process):
    """No gate password, no MFA key, no proxy flag: those are the browser's (E010, E033, E048)."""
    ids = _ids()
    for browser_only in ("juristid.E010", "juristid.E033", "juristid.E048", "juristid.E020"):
        assert browser_only not in ids


# -- 3. the other processes ------------------------------------------------------------


@pytest.fixture
def mcp_process(settings, monkeypatch):
    settings.DEBUG = False
    settings.DEV_LOGIN_ENABLED = False
    settings.REAL_DATA_ALLOWED = True
    settings.AUTH_MODE = "shared_gate"
    settings.SECRET_KEY = LONG_SECRET_KEY
    settings.SHARED_GATE_PASSWORD = ""
    settings.LOCAL_AUTH_MFA_ENCRYPTION_KEY = ""
    settings.EMAIL_HOST_PASSWORD = ""
    settings.MCP_AUTHENTICATOR_KEY = ""
    settings.MCP_TOKEN_DERIVATION_KEY = ""
    settings.TRUST_CF_CONNECTING_IP = False
    if hasattr(settings, "SECURE_PROXY_SSL_HEADER"):
        del settings.SECURE_PROXY_SSL_HEADER
    settings.JURISTID_PROCESS_ROLE = "mcp"
    settings.MCP_ENABLED = True
    settings.MCP_WRITES_ENABLED = False
    settings.MCP_WRITE_GATEWAY_KEY = ""
    settings.JURISTID_PUBLIC_ORIGIN = "https://juristid.orgusaar.ee"
    settings.MCP_RESOURCE_URLS = ["https://api.openai.com/v1/mcp/tunnel_abc"]
    monkeypatch.setitem(settings.DATABASES["default"], "USER", "juristid_mcp")
    monkeypatch.setitem(settings.DATABASES["default"], "PASSWORD", LONG_DATABASE_PASSWORD)
    monkeypatch.setitem(
        settings.DATABASES["default"],
        "OPTIONS",
        {"sslmode": "disable", "options": READ_ONLY_OPTIONS},
    )
    return settings


def test_mcp_with_writes_off_needs_no_gateway_key(mcp_process):
    assert check_runtime_safety(None) == []


def test_mcp_with_writes_on_needs_a_strong_gateway_key(mcp_process):
    mcp_process.MCP_WRITES_ENABLED = True
    assert "juristid.E052" in _ids()
    mcp_process.MCP_WRITE_GATEWAY_KEY = GATEWAY_KEY
    assert check_runtime_safety(None) == []


def test_mcp_never_connects_as_the_writer_role(mcp_process, monkeypatch):
    monkeypatch.setitem(mcp_process.DATABASES["default"], "USER", "juristid_mcp_writer")
    assert "juristid.E058" in _ids()


@pytest.mark.parametrize("role", ["", "web", "worker"])
def test_web_and_its_workers_never_hold_the_gateway_key(settings, role):
    settings.JURISTID_PROCESS_ROLE = role
    settings.MCP_WRITE_GATEWAY_KEY = ""
    assert "juristid.E057" not in _ids()
    settings.MCP_WRITE_GATEWAY_KEY = GATEWAY_KEY
    found = [p for p in check_runtime_safety(None) if p.id == "juristid.E057"]
    assert found and GATEWAY_KEY not in found[0].msg


def test_web_may_switch_writes_on_without_any_key(settings):
    """`web` needs MCP_WRITES_ENABLED only: the approval page and the scope."""
    settings.JURISTID_PROCESS_ROLE = ""
    settings.MCP_WRITES_ENABLED = True
    settings.MCP_WRITE_GATEWAY_KEY = ""
    ids = _ids()
    assert not {"juristid.E051", "juristid.E052", "juristid.E057"} & ids


def test_an_unknown_role_is_still_refused(settings):
    settings.JURISTID_PROCESS_ROLE = "mcp-writer"
    assert "juristid.E040" in _ids()


# -- agent A's checks let the writer start without the browser's keys ------------------


def test_mcp_access_checks_do_not_ask_the_writer_for_the_browsers_keys(writer_process):
    from app.mcp_access.checks import mcp_access_settings

    ids = {problem.id for problem in mcp_access_settings()}
    assert not {"mcp_access.E004", "mcp_access.E005"} & ids
