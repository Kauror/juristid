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
    """What "clean" means now includes an authenticator *and* a scanner.

    Stage 2D added juristid.E006: real data with nothing authenticating the
    request is not a configuration this system will start in (docs/adr/0016).
    docs/adr/0066 adds juristid.E015 on the same principle applied one layer in:
    real data with no malware scanner is a deployment that can never read a
    document, because nothing can move a file from PENDING to CLEAN and
    `is_scan_state_extractable` refuses everything else.
    """
    settings.DEBUG = False
    settings.SECRET_KEY = "a-real-secret"  # noqa: S105
    settings.DEV_LOGIN_ENABLED = False
    settings.REAL_DATA_ALLOWED = True
    settings.AUTH_MODE = "cloudflare_access"
    settings.CF_ACCESS_TEAM_DOMAIN = "naidiskoda.cloudflareaccess.invalid"
    settings.CF_ACCESS_AUDIENCE = "a" * 64
    settings.MALWARE_SCANNER_BACKEND = "clamav"
    assert _ids(settings) == set()


# --------------------------------------------------------------------------
# The scan gate. Real data with no scanner was not merely unguarded before this
# — it was the reported defect: `Uus teema` staged a file, showed «Loen faili…»
# and waited for an answer no code path could produce (docs/adr/0066).
# --------------------------------------------------------------------------


def test_real_data_without_a_scanner_is_refused(settings):
    settings.DEBUG = False
    settings.SECRET_KEY = "a-real-secret"  # noqa: S105
    settings.DEV_LOGIN_ENABLED = False
    settings.REAL_DATA_ALLOWED = True
    settings.AUTH_MODE = "cloudflare_access"
    settings.CF_ACCESS_TEAM_DOMAIN = "naidiskoda.cloudflareaccess.invalid"
    settings.CF_ACCESS_AUDIENCE = "a" * 64
    settings.MALWARE_SCANNER_BACKEND = "none"
    assert "juristid.E015" in _ids(settings)


def test_development_without_a_scanner_is_fine(settings):
    """And it must stay fine, or nobody could run this locally.

    With REAL_DATA_ALLOWED off, PENDING is already extractable — it always has
    been, because the data is invented — so a development environment with no
    scanner reads its files exactly as before. The check is about the *pair*,
    not about the scanner alone.
    """
    settings.DEBUG = True
    settings.SECRET_KEY = settings.DEV_INSECURE_SECRET_KEY
    settings.DEV_LOGIN_ENABLED = True
    settings.REAL_DATA_ALLOWED = False
    settings.MALWARE_SCANNER_BACKEND = "none"
    assert "juristid.E015" not in _ids(settings)


def test_a_scanner_backend_that_does_not_exist_is_refused(settings):
    """A typo must not read as "no scanner, carry on".

    `scanner_configured()` answers False for an unknown backend as well as for
    `none`, so without this check a misspelled MALWARE_SCANNER_BACKEND would
    silently produce exactly the state this round removed.
    """
    settings.REAL_DATA_ALLOWED = False
    settings.MALWARE_SCANNER_BACKEND = "clamvav"
    assert "juristid.E015" in _ids(settings)


# --------------------------------------------------------------------------
# Language. Found by opening the deployed rehearsal in an ordinary browser:
# LocaleMiddleware honours Accept-Language, Django ships an `en` locale, and an
# English-language browser was served an Estonian interface with English dates
# and English form errors. The browser suite never saw it because Chromium was
# driven without a language preference.
# --------------------------------------------------------------------------


def test_the_product_offers_exactly_one_language():
    """Estonian-first is a product decision, not a default (specification 3.10)."""
    from django.conf import settings

    assert settings.LANGUAGE_CODE == "et"
    assert [code for code, _ in settings.LANGUAGES] == ["et"]


def test_an_english_browser_still_gets_the_estonian_interface(client, settings):
    """The regression itself, expressed as the request that exposed it."""
    from django.utils import translation

    settings.DEV_LOGIN_ENABLED = True
    response = client.get("/", HTTP_ACCEPT_LANGUAGE="en-US,en;q=0.9,de;q=0.8")
    assert response.status_code in {200, 302}
    assert translation.get_language() == "et"


def test_language_negotiation_cannot_reach_a_language_we_do_not_ship(client):
    from django.utils.translation import get_language_from_request

    class _Request:
        META = {"HTTP_ACCEPT_LANGUAGE": "de,fr;q=0.9,en;q=0.8"}
        COOKIES: dict[str, str] = {}
        session: dict[str, str] = {}

        def get_host(self):
            return "testserver"

    assert get_language_from_request(_Request()) == "et"
