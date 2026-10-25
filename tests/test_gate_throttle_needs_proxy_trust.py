"""Real data behind a password throttle needs the client's real address (JRF-02).

Since JOPS-008 (#465) `CF-Connecting-IP` names the client only where the
deployment says a proxy stands in front of it: `TRUST_CF_CONNECTING_IP`, which
is `DJANGO_BEHIND_TLS_PROXY` (app/core/client_ip.py). Without the flag every
request behind the tunnel arrives from the `cloudflared` connector's address,
so the per-client gate throttle becomes one bucket for the whole department:
five wrong passwords from anybody lock everybody out.

`juristid.E014` only warned about the missing flag. On real data, in a mode
whose password throttle keys on the client address (`shared_gate`, and the
dormant `local_password`), it is now an error, `juristid.E020`. Release step 6
(`run --rm web python manage.py migration_plan`) runs the checks against the
host's environment file before the backup and before anything is replaced, so
a missing flag shows there and not as a locked-out department.
"""

from __future__ import annotations

import pytest
from django.core.checks import Error, Warning

from app.core.checks import check_runtime_safety
from tests.test_real_data_secrets import (
    LONG_DATABASE_PASSWORD,
    LONG_GATE_PASSWORD,
    LONG_SECRET_KEY,
    REAL_DATA_TEMPLATES,
    _manage_check,
    _template,
)

PROXY_TRUST = "juristid.E020"
PROXY_WARNING = "juristid.E014"


def _problems() -> dict[str, Error | Warning]:
    return {problem.id: problem for problem in check_runtime_safety(None)}


def _without_proxy_trust(settings) -> None:
    """What `DJANGO_BEHIND_TLS_PROXY` unset gives: both halves of the one switch off."""
    settings.TRUST_CF_CONNECTING_IP = False
    settings.SECURE_PROXY_SSL_HEADER = None


@pytest.fixture
def real_data_gate(settings, monkeypatch):
    """A complete, valid real-data configuration behind the shared gate."""
    settings.DEBUG = False
    settings.DEV_LOGIN_ENABLED = False
    settings.REAL_DATA_ALLOWED = True
    settings.AUTH_MODE = "shared_gate"
    settings.SECRET_KEY = LONG_SECRET_KEY
    settings.SHARED_GATE_PASSWORD = LONG_GATE_PASSWORD
    settings.SESSION_COOKIE_SECURE = True
    settings.SECURE_PROXY_SSL_HEADER = ("HTTP_X_FORWARDED_PROTO", "https")
    settings.TRUST_CF_CONNECTING_IP = True
    # Administrators' authenticators need their own key beside the gate
    # (juristid.E048, docs/adr/0186).
    settings.LOCAL_AUTH_MFA_ENCRYPTION_KEY = "mfa-" + "k" * 44
    monkeypatch.setitem(settings.DATABASES["default"], "PASSWORD", LONG_DATABASE_PASSWORD)
    return settings


def test_real_data_behind_the_gate_without_proxy_trust_refuses(real_data_gate):
    _without_proxy_trust(real_data_gate)
    problems = _problems()
    assert PROXY_TRUST in problems
    assert isinstance(problems[PROXY_TRUST], Error)
    assert "DJANGO_BEHIND_TLS_PROXY" in (problems[PROXY_TRUST].hint or "")
    # One problem, said once: the warning is not repeated beside the error.
    assert PROXY_WARNING not in problems


def test_the_trust_flag_alone_decides_it(real_data_gate):
    """The throttle reads `TRUST_CF_CONNECTING_IP`, so the check reads the same."""
    real_data_gate.TRUST_CF_CONNECTING_IP = False
    assert PROXY_TRUST in _problems()


def test_real_data_behind_the_gate_with_proxy_trust_is_clean(real_data_gate):
    assert check_runtime_safety(None) == []


def test_the_dormant_personal_sign_in_throttles_by_address_too(real_data_gate):
    real_data_gate.AUTH_MODE = "local_password"
    _without_proxy_trust(real_data_gate)
    assert PROXY_TRUST in _problems()
    real_data_gate.TRUST_CF_CONNECTING_IP = True
    assert PROXY_TRUST not in _problems()


def _cloudflare_access(settings) -> None:
    settings.AUTH_MODE = "cloudflare_access"
    settings.CF_ACCESS_TEAM_DOMAIN = "naidiskoda.cloudflareaccess.invalid"
    settings.CF_ACCESS_AUDIENCE = "a" * 64


def test_cloudflare_access_has_no_password_throttle_to_share(real_data_gate):
    _cloudflare_access(real_data_gate)
    _without_proxy_trust(real_data_gate)
    assert PROXY_TRUST not in _problems()


# -- juristid.E022: real data behind Cloudflare Access needs the flag too ------------------
#
# No throttle to share, but without the flag Django believes every request
# arrived in clear: no HSTS, `request.is_secure()` False, CSRF without its
# referer check, and audit rows naming the tunnel. Both real-data templates set
# DJANGO_SECURE_SSL_REDIRECT=0, so no redirect loop would show it either.

TLS_PROXY = "juristid.E022"


def test_real_data_behind_cloudflare_access_without_the_flag_refuses(real_data_gate):
    _cloudflare_access(real_data_gate)
    _without_proxy_trust(real_data_gate)
    problems = _problems()
    assert TLS_PROXY in problems
    assert isinstance(problems[TLS_PROXY], Error)
    assert "DJANGO_BEHIND_TLS_PROXY" in (problems[TLS_PROXY].hint or "")


def test_real_data_behind_cloudflare_access_with_the_flag_is_clean(real_data_gate):
    _cloudflare_access(real_data_gate)
    assert check_runtime_safety(None) == []


def test_synthetic_data_behind_cloudflare_access_is_not_refused(real_data_gate):
    _cloudflare_access(real_data_gate)
    real_data_gate.REAL_DATA_ALLOWED = False
    _without_proxy_trust(real_data_gate)
    assert TLS_PROXY not in _problems()


def test_the_gate_says_it_once_as_e020(real_data_gate):
    """Behind the shared gate the same missing flag is E020; E022 is not added."""
    _without_proxy_trust(real_data_gate)
    problems = _problems()
    assert PROXY_TRUST in problems
    assert TLS_PROXY not in problems


def test_the_mcp_processes_do_not_need_the_flag(real_data_gate):
    """`mcp` and `mcp-writer` serve no browser; the client-address checks skip them."""
    _cloudflare_access(real_data_gate)
    _without_proxy_trust(real_data_gate)
    for role in ("mcp", "mcp_writer"):
        real_data_gate.JURISTID_PROCESS_ROLE = role
        assert TLS_PROXY not in _problems()


def test_a_synthetic_gate_without_a_proxy_only_warns(real_data_gate):
    """The rehearsal and test stacks hold no real data: unchanged, a warning."""
    real_data_gate.REAL_DATA_ALLOWED = False
    _without_proxy_trust(real_data_gate)
    problems = _problems()
    assert PROXY_TRUST not in problems
    assert isinstance(problems[PROXY_WARNING], Warning)


def test_the_development_configuration_is_unaffected(settings):
    settings.DEBUG = True
    settings.SECRET_KEY = settings.DEV_INSECURE_SECRET_KEY
    settings.DEV_LOGIN_ENABLED = True
    settings.REAL_DATA_ALLOWED = False
    _without_proxy_trust(settings)
    assert check_runtime_safety(None) == []


def test_the_test_configuration_is_unaffected():
    """The suite's own settings, as they are: no error of this kind."""
    assert PROXY_TRUST not in _problems()


def test_manage_check_fails_on_the_filled_template_without_the_flag():
    """Release step 6's view: the host's environment file, one line missing."""
    values = _template(REAL_DATA_TEMPLATES[0])
    values.update(
        DJANGO_SECRET_KEY=LONG_SECRET_KEY,
        JURISTID_SHARED_GATE_PASSWORD=LONG_GATE_PASSWORD,
        POSTGRES_PASSWORD=LONG_DATABASE_PASSWORD,
    )
    assert values.pop("DJANGO_BEHIND_TLS_PROXY") == "1"
    result = _manage_check(values)
    output = result.stdout + result.stderr
    assert result.returncode != 0, output
    assert PROXY_TRUST in output


@pytest.mark.parametrize("template", REAL_DATA_TEMPLATES, ids=lambda path: path.parent.name)
def test_every_real_data_template_sets_the_flag(template):
    assert _template(template).get("DJANGO_BEHIND_TLS_PROXY") == "1"
