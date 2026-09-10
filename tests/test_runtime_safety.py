"""Configuration mistakes must stop the process, not be discovered later."""

from __future__ import annotations

import pathlib

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
    assert _ids(settings) == set()


# --------------------------------------------------------------------------
# The scan gate is gone, and nothing may quietly bring it back.
#
# juristid.E015 refused real data with no scanner configured, because in that
# state no file could ever be read: `Uus teema` staged a file, showed «Loen
# faili…» and waited for an answer no code path could produce (docs/adr/0066).
# docs/adr/0072 removed the subsystem instead, so the state E015 guarded cannot
# be entered — there is nothing to configure and nothing to gate.
# --------------------------------------------------------------------------


def test_the_scanner_check_is_gone_with_the_scanner(settings):
    """No setting anywhere may still make the application refuse to start.

    Written as "E015 is never raised, under any of the combinations it used to
    fire on" rather than as "the function is deleted": what mattered about the
    check was its effect on a deployment, and a leftover copy under another
    name would have exactly the old effect.
    """
    settings.DEBUG = False
    settings.SECRET_KEY = "a-real-secret"  # noqa: S105
    settings.DEV_LOGIN_ENABLED = False
    settings.AUTH_MODE = "cloudflare_access"
    settings.CF_ACCESS_TEAM_DOMAIN = "naidiskoda.cloudflareaccess.invalid"
    settings.CF_ACCESS_AUDIENCE = "a" * 64
    for real_data in (True, False):
        settings.REAL_DATA_ALLOWED = real_data
        assert "juristid.E015" not in _ids(settings)


def test_no_scanner_setting_survives_in_the_configuration(settings):
    """The keys themselves, not only the check that read them.

    A setting nothing reads is a line somebody will eventually wire back up,
    and an operator finding `MALWARE_SCANNER_BACKEND` in `.env.example` has
    every reason to believe it does something.
    """
    from django.conf import settings as live

    assert not [name for name in dir(live) if name.startswith("MALWARE_SCANNER")]

    template = (pathlib.Path(live.BASE_DIR) / "deploy" / "unraid-main" / ".env.example").read_text(
        encoding="utf-8"
    )
    assert "MALWARE_SCANNER" not in template


def test_upload_validation_is_what_now_stands_at_the_door(settings):
    """Removing the scanner did not widen what may be uploaded (task §23).

    The three cheap checks are the whole of the remaining refusal, and each of
    them is asserted properly in `tests/test_held_uploads.py`. This is the
    contract-level statement that they still exist and are still applied to
    the bytes rather than to the name.
    """
    from app.documents.uploads import read_upload

    source = pathlib.Path(settings.BASE_DIR) / "app" / "documents" / "uploads.py"
    text = source.read_text(encoding="utf-8")
    assert "MAX_EVIDENCE_UPLOAD_BYTES" in text
    assert "ALLOWED_EVIDENCE_MIME_TYPES" in text
    assert "sniff" in text or "signature" in text
    assert callable(read_upload)


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
