"""Real data behind secrets that are real (JOPS-003).

`manage.py check` with `REAL_DATA_ALLOWED=1`, `AUTH_MODE=shared_gate` and the
deployment template's own `replace-me-…` values said "System check identified no
issues": the gate placeholder is long enough for juristid.E011, and the
SECRET_KEY placeholder is not the development key juristid.E001 knows. The
repository is public, so a copied template with one value left unfilled served
the register behind a password anybody could read.

Placeholders are refused (E016), and on real data the secrets have floors:
SECRET_KEY 50 (E017), the gate password 16 (E018, above E011's 12, which still
applies to the mode everywhere) and the database password 16 (E019).
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest
from django.conf import settings as live_settings

from app.core.checks import check_runtime_safety

ROOT = Path(live_settings.BASE_DIR)

#: The two templates that describe a REAL_DATA_ALLOWED=1 environment.
REAL_DATA_TEMPLATES = [
    ROOT / "deploy" / "unraid-main" / ".env.example",
    ROOT / "deploy" / "recovery-rehearsal" / "real-data.env.example",
]

NEW_IDS = {"juristid.E016", "juristid.E017", "juristid.E018", "juristid.E019"}

LONG_SECRET_KEY = "k" * 88
LONG_GATE_PASSWORD = "g" * 32
LONG_DATABASE_PASSWORD = "d" * 48


def _template(path: Path) -> dict[str, str]:
    values = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        if line and not line.startswith("#") and "=" in line:
            name, _, value = line.partition("=")
            values[name.strip()] = value.strip()
    return values


def _ids() -> set[str]:
    return {problem.id for problem in check_runtime_safety(None)}


@pytest.fixture
def real_data(settings, monkeypatch):
    """A complete, valid real-data configuration behind the shared gate."""
    settings.DEBUG = False
    settings.DEV_LOGIN_ENABLED = False
    settings.REAL_DATA_ALLOWED = True
    settings.AUTH_MODE = "shared_gate"
    settings.SECRET_KEY = LONG_SECRET_KEY
    settings.SHARED_GATE_PASSWORD = LONG_GATE_PASSWORD
    # Administrators' authenticators need their own key beside the gate
    # (juristid.E048, docs/adr/0186).
    settings.LOCAL_AUTH_MFA_ENCRYPTION_KEY = "mfa-" + "k" * 44
    settings.SESSION_COOKIE_SECURE = True
    settings.SECURE_PROXY_SSL_HEADER = ("HTTP_X_FORWARDED_PROTO", "https")
    monkeypatch.setitem(settings.DATABASES["default"], "PASSWORD", LONG_DATABASE_PASSWORD)
    return settings


def test_a_valid_real_data_configuration_passes(real_data):
    assert check_runtime_safety(None) == []


@pytest.mark.parametrize("template", REAL_DATA_TEMPLATES, ids=lambda path: path.parent.name)
def test_the_templates_own_placeholders_are_refused(real_data, monkeypatch, template):
    values = _template(template)
    real_data.SECRET_KEY = values["DJANGO_SECRET_KEY"]
    real_data.SHARED_GATE_PASSWORD = values["JURISTID_SHARED_GATE_PASSWORD"]
    monkeypatch.setitem(real_data.DATABASES["default"], "PASSWORD", values["POSTGRES_PASSWORD"])

    problems = {problem.id: problem for problem in check_runtime_safety(None)}
    assert "juristid.E016" in problems
    for name in ("DJANGO_SECRET_KEY", "JURISTID_SHARED_GATE_PASSWORD", "POSTGRES_PASSWORD"):
        assert name in problems["juristid.E016"].msg
    # The values themselves are never repeated back.
    for problem in problems.values():
        for value in values.values():
            if value.startswith("replace-me"):
                assert value not in problem.msg
                assert value not in (problem.hint or "")


@pytest.mark.parametrize(
    "variable",
    [
        "SECRET_KEY",
        "SHARED_GATE_PASSWORD",
        "DATABASE_PASSWORD",
        "LOCAL_AUTH_MFA_ENCRYPTION_KEY",
        "EMAIL_HOST_PASSWORD",
    ],
)
def test_one_placeholder_left_in_is_enough_to_refuse(real_data, monkeypatch, variable):
    placeholder = "replace-me-at-activation-" + "x" * 64
    if variable == "DATABASE_PASSWORD":
        monkeypatch.setitem(real_data.DATABASES["default"], "PASSWORD", placeholder)
    else:
        setattr(real_data, variable, placeholder)
    assert "juristid.E016" in _ids()


def test_the_secret_key_needs_fifty_characters(real_data):
    real_data.SECRET_KEY = "k" * 49
    assert "juristid.E017" in _ids()
    real_data.SECRET_KEY = "k" * 50
    assert "juristid.E017" not in _ids()


def test_the_gate_password_needs_sixteen_characters_on_real_data(real_data):
    real_data.SHARED_GATE_PASSWORD = "g" * 15
    problems = _ids()
    assert "juristid.E018" in problems
    # Long enough for the mode's own floor, which is unchanged.
    assert "juristid.E011" not in problems
    real_data.SHARED_GATE_PASSWORD = "g" * 16
    assert "juristid.E018" not in _ids()


def test_the_gate_floor_applies_only_where_the_gate_is_the_door(real_data):
    real_data.AUTH_MODE = "cloudflare_access"
    real_data.CF_ACCESS_TEAM_DOMAIN = "naidiskoda.cloudflareaccess.invalid"
    real_data.CF_ACCESS_AUDIENCE = "a" * 64
    real_data.SHARED_GATE_PASSWORD = "unused"  # noqa: S105
    assert "juristid.E018" not in _ids()


def test_the_database_password_needs_sixteen_characters(real_data, monkeypatch):
    monkeypatch.setitem(real_data.DATABASES["default"], "PASSWORD", "d" * 15)
    assert "juristid.E019" in _ids()
    monkeypatch.setitem(real_data.DATABASES["default"], "PASSWORD", "")
    assert "juristid.E019" in _ids()
    monkeypatch.setitem(real_data.DATABASES["default"], "PASSWORD", "d" * 16)
    assert "juristid.E019" not in _ids()


def test_synthetic_stacks_keep_their_short_values(real_data, monkeypatch):
    """The development and rehearsal stacks hold no real data and are untouched."""
    real_data.REAL_DATA_ALLOWED = False
    real_data.SECRET_KEY = "replace-me-with-openssl-rand-base64-64"  # noqa: S105
    real_data.SHARED_GATE_PASSWORD = "replace-me-12"  # noqa: S105
    monkeypatch.setitem(real_data.DATABASES["default"], "PASSWORD", "juristid")
    assert not (_ids() & NEW_IDS)


# -- the real command, as an operator runs it ------------------------------------


def _manage_check(environment: dict[str, str]) -> subprocess.CompletedProcess[str]:
    """`manage.py check` in a clean child process, production settings."""
    inherited = {
        name: os.environ[name]
        for name in ("PATH", "SYSTEMROOT", "TEMP", "TMP", "HOME", "USERPROFILE")
        if name in os.environ
    }
    return subprocess.run(  # noqa: S603 - this interpreter and the repository's manage.py
        [sys.executable, str(ROOT / "manage.py"), "check", "--fail-level", "ERROR"],
        cwd=ROOT,
        env={**inherited, **environment, "DJANGO_SETTINGS_MODULE": "config.settings"},
        capture_output=True,
        text=True,
        timeout=180,
    )


def test_manage_check_fails_on_the_unfilled_template():
    """The audit's reproduction: the template as copied, REAL_DATA_ALLOWED=1."""
    result = _manage_check(_template(REAL_DATA_TEMPLATES[0]))
    output = result.stdout + result.stderr
    assert result.returncode != 0, output
    assert "juristid.E016" in output
    assert "juristid.E017" in output
    assert "replace-me-with" not in output


def test_manage_check_passes_on_the_filled_template():
    values = _template(REAL_DATA_TEMPLATES[0])
    values.update(
        DJANGO_SECRET_KEY=LONG_SECRET_KEY,
        JURISTID_SHARED_GATE_PASSWORD=LONG_GATE_PASSWORD,
        POSTGRES_PASSWORD=LONG_DATABASE_PASSWORD,
        LOCAL_AUTH_MFA_ENCRYPTION_KEY="mfa-" + "k" * 44,
    )
    result = _manage_check(values)
    assert result.returncode == 0, result.stdout + result.stderr
    assert "no issues" in result.stdout
