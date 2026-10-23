"""The `mcp` process role (docs/adr/0185 §1, §6).

Three promises, each proved here:

1. In role `mcp` the database refuses writes: the connection options the
   settings produce make an INSERT fail with ReadOnlySqlTransaction, on a real
   PostgreSQL connection opened through Django's own backend.
2. The system checks let the MCP process start without the browser's secrets —
   and refuse it the MCP configuration it does need (juristid.E040–E046).
3. Nothing changes for the web and worker processes: every check that applied
   to them still fires there exactly as before.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest
from django.conf import settings as live_settings

from app.core.checks import check_runtime_safety

ROOT = Path(live_settings.BASE_DIR)

LONG_SECRET_KEY = "k" * 88
LONG_DATABASE_PASSWORD = "d" * 48
READ_ONLY_OPTIONS = "-c default_transaction_read_only=on -c statement_timeout=60000"


def _ids() -> set[str]:
    return {problem.id for problem in check_runtime_safety(None)}


def _settings_under(environment: dict[str, str]) -> dict[str, object]:
    """The production settings module's DATABASES and role, loaded fresh in a child process."""
    child = {
        key: value
        for key, value in os.environ.items()
        if not key.startswith(("JURISTID_", "MCP_", "DATABASE_URL"))
    }
    child.update(
        {
            "DJANGO_SETTINGS_MODULE": "config.settings",
            "DJANGO_SECRET_KEY": "x",
            "POSTGRES_SSLMODE": "disable",
            **environment,
        }
    )
    script = (
        "import json, django; from django.conf import settings; django.setup(); "
        "d = settings.DATABASES['default']; "
        "print(json.dumps({'options': d['OPTIONS'], 'conn_max_age': d['CONN_MAX_AGE'], "
        "'role': settings.JURISTID_PROCESS_ROLE, 'hosts': settings.MCP_ALLOWED_HOSTS, "
        "'origins': settings.MCP_ALLOWED_ORIGINS}))"
    )
    completed = subprocess.run(  # noqa: S603 - our own interpreter, our own code
        [sys.executable, "-c", script],
        cwd=ROOT,
        env=child,
        capture_output=True,
        text=True,
        timeout=120,
        check=True,
    )
    loaded: dict[str, object] = json.loads(completed.stdout.strip().splitlines()[-1])
    return loaded


# -- 1. the database refuses writes ------------------------------------------------------


def test_the_mcp_role_opens_the_database_read_only_and_keeps_no_connection():
    loaded = _settings_under({"JURISTID_PROCESS_ROLE": "mcp", "MCP_CALL_TIMEOUT_SECONDS": "45"})
    assert loaded["role"] == "mcp"
    assert loaded["conn_max_age"] == 0
    assert loaded["options"] == {
        "sslmode": "disable",
        "options": "-c default_transaction_read_only=on -c statement_timeout=45000",
    }


def test_the_browser_role_is_unchanged():
    loaded = _settings_under({})
    assert loaded["role"] == ""
    assert loaded["options"] == {"sslmode": "disable"}
    assert loaded["conn_max_age"] == 60


def test_the_default_host_and_origin_lists_follow_the_resource_urls():
    loaded = _settings_under(
        {
            "MCP_RESOURCE_URLS": "https://abc.tunnel.example/mcp,https://mcp.juristid.example/mcp",
            "JURISTID_PUBLIC_ORIGIN": "https://juristid.example",
        }
    )
    assert loaded["hosts"] == [
        "abc.tunnel.example",
        "mcp.juristid.example",
        "mcp",
        "mcp:8001",
        "localhost",
        "localhost:*",
        "127.0.0.1",
        "127.0.0.1:*",
    ]
    assert loaded["origins"] == [
        "https://abc.tunnel.example",
        "https://mcp.juristid.example",
        "https://juristid.example",
        "https://chatgpt.com",
    ]


@pytest.mark.django_db
def test_an_insert_through_the_mcp_roles_connection_is_refused():
    """A dedicated connection, opened by Django's backend with the role's options."""
    import psycopg
    from django.db import DEFAULT_DB_ALIAS, InternalError, connection
    from django.db.backends.postgresql.base import DatabaseWrapper

    loaded = _settings_under({"JURISTID_PROCESS_ROLE": "mcp"})
    settings_dict = {
        **connection.settings_dict,
        "OPTIONS": {**connection.settings_dict.get("OPTIONS", {}), **loaded["options"]},  # type: ignore[dict-item]
        "CONN_MAX_AGE": 0,
    }
    # Its own wrapper and its own connection, under the default alias only so
    # that the test harness's per-alias bookkeeping recognises it.
    read_only = DatabaseWrapper(settings_dict, alias=DEFAULT_DB_ALIAS)
    try:
        with read_only.cursor() as cursor:
            cursor.execute("SHOW default_transaction_read_only")
            assert cursor.fetchone() == ("on",)
            cursor.execute("SELECT count(*) FROM django_migrations")
            assert cursor.fetchone()[0] > 0
            with pytest.raises(InternalError, match="read-only transaction") as refused:
                cursor.execute(
                    "INSERT INTO django_migrations (app, name, applied) "
                    "VALUES ('mcp_probe', 'must_not_exist', now())"
                )
            assert isinstance(refused.value.__cause__, psycopg.errors.ReadOnlySqlTransaction)
            for statement in (
                "UPDATE django_migrations SET name = name WHERE false",
                "DELETE FROM django_migrations WHERE false",
                "CREATE TABLE mcp_probe (id int)",
            ):
                with pytest.raises(InternalError, match="read-only transaction"):
                    cursor.execute(statement)
    finally:
        read_only.close()

    with connection.cursor() as cursor:
        cursor.execute("SELECT count(*) FROM django_migrations WHERE app = 'mcp_probe'")
        assert cursor.fetchone() == (0,)


# -- 2. the checks in role mcp -----------------------------------------------------------------


@pytest.fixture
def mcp_process(settings, monkeypatch):
    """A complete, valid real-data configuration of the MCP process, behind the shared gate."""
    settings.DEBUG = False
    settings.DEV_LOGIN_ENABLED = False
    settings.REAL_DATA_ALLOWED = True
    settings.AUTH_MODE = "shared_gate"
    settings.SECRET_KEY = LONG_SECRET_KEY
    # None of the browser's secrets, and no proxy flag.
    settings.SHARED_GATE_PASSWORD = ""
    settings.LOCAL_AUTH_MFA_ENCRYPTION_KEY = ""
    settings.EMAIL_HOST_PASSWORD = ""
    settings.TRUST_CF_CONNECTING_IP = False
    if hasattr(settings, "SECURE_PROXY_SSL_HEADER"):
        del settings.SECURE_PROXY_SSL_HEADER
    settings.JURISTID_PROCESS_ROLE = "mcp"
    settings.MCP_ENABLED = True
    settings.JURISTID_PUBLIC_ORIGIN = "https://juristid.orgusaar.ee"
    settings.MCP_RESOURCE_URLS = ["https://tunnel.example.net/mcp", "https://mcp.example.ee/mcp"]
    monkeypatch.setitem(settings.DATABASES["default"], "PASSWORD", LONG_DATABASE_PASSWORD)
    monkeypatch.setitem(
        settings.DATABASES["default"],
        "OPTIONS",
        {"sslmode": "disable", "options": READ_ONLY_OPTIONS},
    )
    return settings


def test_a_valid_mcp_process_passes_without_any_browser_secret(mcp_process):
    assert check_runtime_safety(None) == []


@pytest.mark.parametrize(
    ("change", "expected"),
    [
        ({"MCP_ENABLED": False}, "juristid.E041"),
        ({"JURISTID_PUBLIC_ORIGIN": ""}, "juristid.E042"),
        ({"JURISTID_PUBLIC_ORIGIN": "http://juristid.orgusaar.ee"}, "juristid.E042"),
        ({"JURISTID_PUBLIC_ORIGIN": "https://juristid.orgusaar.ee/app"}, "juristid.E042"),
        ({"MCP_RESOURCE_URLS": []}, "juristid.E043"),
        ({"MCP_RESOURCE_URLS": ["https://ok.example/mcp", "http://mcp:8001/mcp"]}, "juristid.E043"),
        ({"MCP_RESOURCE_URLS": ["https://user:pw@ok.example/mcp"]}, "juristid.E043"),
        ({"SHARED_GATE_PASSWORD": "g" * 32}, "juristid.E046"),
        ({"LOCAL_AUTH_MFA_ENCRYPTION_KEY": "m" * 32}, "juristid.E046"),
        ({"EMAIL_HOST_PASSWORD": "p" * 32}, "juristid.E046"),
    ],
)
def test_the_mcp_process_refuses_a_missing_or_wrong_mcp_setting(mcp_process, change, expected):
    for name, value in change.items():
        setattr(mcp_process, name, value)
    assert expected in _ids()


def test_the_mcp_process_names_held_secrets_never_their_values(mcp_process):
    mcp_process.SHARED_GATE_PASSWORD = "gate-password-value-123456"  # noqa: S105
    [problem] = [p for p in check_runtime_safety(None) if p.id == "juristid.E046"]
    assert "JURISTID_SHARED_GATE_PASSWORD" in problem.msg
    assert "gate-password-value-123456" not in problem.msg + (problem.hint or "")


def test_the_mcp_process_refuses_debug(mcp_process):
    mcp_process.REAL_DATA_ALLOWED = False
    mcp_process.DEBUG = True
    assert "juristid.E044" in _ids()


def test_the_mcp_process_refuses_a_writable_database_connection(mcp_process, monkeypatch):
    monkeypatch.setitem(mcp_process.DATABASES["default"], "OPTIONS", {"sslmode": "disable"})
    assert "juristid.E045" in _ids()


def test_an_unknown_role_is_refused(settings):
    settings.JURISTID_PROCESS_ROLE = "mpc"
    assert "juristid.E040" in _ids()


@pytest.mark.parametrize("role", ["", "web", "worker"])
def test_the_other_roles_get_none_of_the_mcp_checks(settings, role):
    settings.JURISTID_PROCESS_ROLE = role
    settings.MCP_ENABLED = False
    settings.MCP_RESOURCE_URLS = []
    assert not {i for i in _ids() if i.startswith("juristid.E04")}


@pytest.mark.parametrize(
    ("change", "expected"),
    [
        ({"DEV_LOGIN_ENABLED": True}, "juristid.E002"),
        ({"DEV_LOGIN_ENABLED": True}, "juristid.E008"),
        ({"SECRET_KEY": "short"}, "juristid.E017"),
        ({"SECRET_KEY": "replace-me-" + "x" * 60}, "juristid.E016"),
        ({"AUTH_MODE": "none"}, "juristid.E006"),
        ({"AUTH_MODE": "invented"}, "juristid.E009"),
    ],
)
def test_the_mcp_process_still_refuses_what_every_process_refuses(mcp_process, change, expected):
    for name, value in change.items():
        setattr(mcp_process, name, value)
    assert expected in _ids()


def test_the_mcp_process_still_refuses_a_short_database_password(mcp_process, monkeypatch):
    monkeypatch.setitem(mcp_process.DATABASES["default"], "PASSWORD", "short")
    assert "juristid.E019" in _ids()


# -- 3. nothing weakens for the web and worker processes -----------------------------------------


@pytest.fixture
def web_process(mcp_process):
    """The same environment, as the browser application would run it."""
    mcp_process.JURISTID_PROCESS_ROLE = ""
    return mcp_process


def test_the_web_process_still_needs_the_gate_password(web_process):
    assert "juristid.E010" in _ids()


def test_the_web_process_still_needs_a_long_gate_password(web_process):
    web_process.SHARED_GATE_PASSWORD = "short-pass"  # noqa: S105
    assert "juristid.E011" in _ids()


def test_the_web_process_still_needs_the_proxy_trust_on_real_data(web_process):
    web_process.SHARED_GATE_PASSWORD = "g" * 32
    assert "juristid.E020" in _ids()


def test_the_web_process_still_needs_the_mfa_key_under_personal_sign_in(web_process):
    web_process.AUTH_MODE = "local_password"
    assert "juristid.E033" in _ids()
    web_process.JURISTID_PROCESS_ROLE = "mcp"
    assert "juristid.E033" not in _ids()


@pytest.mark.parametrize("role", ["", "web", "worker"])
def test_the_web_process_may_hold_its_own_secrets(web_process, role):
    web_process.JURISTID_PROCESS_ROLE = role
    web_process.SHARED_GATE_PASSWORD = "g" * 32
    # The browser holds the administrators' MFA key (juristid.E048); the MCP
    # process must not (juristid.E046).
    web_process.LOCAL_AUTH_MFA_ENCRYPTION_KEY = "mfa-" + "k" * 44
    web_process.TRUST_CF_CONNECTING_IP = True
    web_process.SESSION_COOKIE_SECURE = True
    web_process.SECURE_PROXY_SSL_HEADER = ("HTTP_X_FORWARDED_PROTO", "https")
    assert _ids() == set()
