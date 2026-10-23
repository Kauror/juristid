"""juristid.E048: the administrators' TOTP key beside the shared gate (docs/adr/0186 §7).

Without a dedicated `LOCAL_AUTH_MFA_ENCRYPTION_KEY`, administrator authenticators
are sealed under a key derived from SECRET_KEY, and a routine SECRET_KEY rotation
would leave both administrators unable to step up. juristid.E033 refused that
only under personal sign-in; E048 refuses it on a real-data shared-gate stack.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from django.conf import settings as django_settings
from django.core.checks import run_checks

ENV_EXAMPLE = Path(django_settings.BASE_DIR) / "deploy" / "unraid-main" / ".env.example"

LONG_SECRET_KEY = "s" * 64
LONG_GATE_PASSWORD = "jagatud-parool-" + "g" * 24
LONG_DATABASE_PASSWORD = "andmebaasi-parool-" + "d" * 24


def _ids() -> set[str]:
    return {message.id for message in run_checks(include_deployment_checks=False)}


@pytest.fixture
def real_gate(settings, monkeypatch):
    settings.REAL_DATA_ALLOWED = True
    settings.AUTH_MODE = "shared_gate"
    settings.DEBUG = False
    settings.DEV_LOGIN_ENABLED = False
    settings.SECRET_KEY = LONG_SECRET_KEY
    settings.SHARED_GATE_PASSWORD = LONG_GATE_PASSWORD
    settings.SESSION_COOKIE_SECURE = True
    settings.SECURE_PROXY_SSL_HEADER = ("HTTP_X_FORWARDED_PROTO", "https")
    monkeypatch.setitem(settings.DATABASES["default"], "PASSWORD", LONG_DATABASE_PASSWORD)
    return settings


def test_an_empty_key_is_refused_on_a_real_data_gate(real_gate):
    real_gate.LOCAL_AUTH_MFA_ENCRYPTION_KEY = ""
    assert "juristid.E048" in _ids()


def test_a_short_key_is_refused(real_gate):
    real_gate.LOCAL_AUTH_MFA_ENCRYPTION_KEY = "k" * 31
    assert "juristid.E048" in _ids()
    real_gate.LOCAL_AUTH_MFA_ENCRYPTION_KEY = "k" * 32
    assert "juristid.E048" not in _ids()


def test_the_hint_names_the_release_kit_step_and_never_the_value(real_gate):
    real_gate.LOCAL_AUTH_MFA_ENCRYPTION_KEY = "lühike-voti-7781"
    problem = next(
        message
        for message in run_checks(include_deployment_checks=False)
        if message.id == "juristid.E048"
    )
    assert "release kit" in (problem.hint or "")
    assert "lühike-voti-7781" not in problem.msg + (problem.hint or "")


def test_a_synthetic_stack_is_not_affected(real_gate):
    real_gate.REAL_DATA_ALLOWED = False
    real_gate.LOCAL_AUTH_MFA_ENCRYPTION_KEY = ""
    assert "juristid.E048" not in _ids()


def test_the_mcp_process_is_not_asked_for_a_key_it_must_not_hold(real_gate):
    real_gate.LOCAL_AUTH_MFA_ENCRYPTION_KEY = ""
    real_gate.JURISTID_PROCESS_ROLE = "mcp"
    assert "juristid.E048" not in _ids()


def test_the_template_documents_the_key_and_the_link_base():
    text = ENV_EXAMPLE.read_text(encoding="utf-8")
    lines = dict(
        line.split("=", 1)
        for line in text.splitlines()
        if line and not line.startswith("#") and "=" in line
    )
    # A placeholder juristid.E016 refuses until the release kit replaces it.
    assert lines["LOCAL_AUTH_MFA_ENCRYPTION_KEY"].startswith("replace-me")
    assert "juristid.E048" in text
    # Named, not filled in: the repository is public (test_deployment_unraid_main).
    assert "# ACCOUNT_LINK_BASE_URL=https://" in text
