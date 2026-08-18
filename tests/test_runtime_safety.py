"""Configuration mistakes must stop the process, not be discovered later."""

from __future__ import annotations

from app.core.checks import check_runtime_safety


def _ids(settings_obj) -> set[str]:
    return {problem.id for problem in check_runtime_safety(None)}


def test_development_secret_key_is_rejected_outside_debug(settings):
    settings.DEBUG = False
    settings.SECRET_KEY = settings.DEV_INSECURE_SECRET_KEY
    assert "juristid.E001" in _ids(settings)


def test_development_login_is_rejected_outside_debug(settings):
    settings.DEBUG = False
    settings.DEV_LOGIN_ENABLED = True
    assert "juristid.E002" in _ids(settings)


def test_development_login_can_never_be_combined_with_real_data(settings):
    settings.DEBUG = True
    settings.DEV_LOGIN_ENABLED = True
    settings.REAL_DATA_ALLOWED = True
    problems = _ids(settings)
    assert "juristid.E003" in problems
    assert "juristid.E004" in problems


def test_a_clean_development_configuration_passes(settings):
    settings.DEBUG = True
    settings.SECRET_KEY = settings.DEV_INSECURE_SECRET_KEY
    settings.DEV_LOGIN_ENABLED = True
    settings.REAL_DATA_ALLOWED = False
    assert _ids(settings) == set()


def test_a_clean_production_configuration_passes(settings):
    settings.DEBUG = False
    settings.SECRET_KEY = "a-real-secret"  # noqa: S105
    settings.DEV_LOGIN_ENABLED = False
    settings.REAL_DATA_ALLOWED = True
    assert _ids(settings) == set()
