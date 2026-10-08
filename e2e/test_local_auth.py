"""Personal sign-in and account administration in a real browser (docs/adr/0145).

Runs against the third server — `AUTH_MODE=local_password`, its own database,
mail written to files (`config/e2e_local_auth_settings.py`). Nothing here is
seeded: the module flushes that database and appoints the first administrator
the way an operator would, with `manage.py bootstrap_account_admin`, and every
later account is created, invited and activated through the pages. The one-time
links are read from the mail files, as a person reads them from their inbox.

One module, in order, because it is one story: the administrator arrives, sets
up their authenticator, creates a colleague, grants a permission; the colleague
activates, signs in, forgets the password and recovers it; the failure states
answer as they should; and the pages hold together at phone width.
"""

from __future__ import annotations

import os
import re
import subprocess
import sys
import time
from pathlib import Path

import pyotp
import pytest
from playwright.sync_api import expect

from e2e.conftest import _missing, navigation_targets

pytestmark = pytest.mark.e2e

LOCAL_BASE_URL = os.environ.get("E2E_LOCAL_AUTH_BASE_URL", "")
MAIL_DIR = os.environ.get("E2E_LOCAL_AUTH_MAIL_DIR", "")
SETTINGS = "config.e2e_local_auth_settings"
ROOT = Path(__file__).resolve().parents[1]

ADMIN = "e2e.haldur@koda.ee"
COLLEAGUE = "e2e.jurist@koda.ee"
ADMIN_PASSWORD = "haldur tuleb hommikul tööle rattaga"  # noqa: S105
COLLEAGUE_PASSWORD = "jurist joob kohvi ja loeb eelnõu"  # noqa: S105
NEW_PASSWORD = "uus parool pärast unustamist kevadel"  # noqa: S105

#: What the story carries from one test to the next: the administrator's
#: authenticator, and the steps already spent (a code works once).
STATE: dict[str, object] = {}


def _manage(*args: str) -> str:
    environment = {**os.environ, "DJANGO_SETTINGS_MODULE": SETTINGS}
    result = subprocess.run(  # noqa: S603 - our own manage.py, our own arguments
        [sys.executable, str(ROOT / "manage.py"), *args],
        cwd=ROOT,
        env=environment,
        capture_output=True,
        text=True,
        timeout=180,
        check=False,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    return result.stdout


@pytest.fixture(scope="module")
def local_base_url() -> str:
    if not LOCAL_BASE_URL or not MAIL_DIR:
        _missing("E2E_LOCAL_AUTH_BASE_URL and E2E_LOCAL_AUTH_MAIL_DIR are not both set")
    return LOCAL_BASE_URL.rstrip("/")


@pytest.fixture(scope="module")
def first_administrator(local_base_url):
    """An empty world and its first administrator, appointed by the operator command."""
    _manage("flush", "--noinput")
    for old in Path(MAIL_DIR).glob("*"):
        old.unlink()
    _manage(
        "bootstrap_account_admin",
        "--email",
        ADMIN,
        "--confirm-email",
        ADMIN,
        "--display-name",
        "E2E Haldur",
        "--role",
        "SPECIALIST",
        "--note",
        "browser suite",
    )
    return ADMIN


def _link_for(address: str, *, after: float = 0.0) -> str:
    """The newest one-time link mailed to ``address``."""
    deadline = time.monotonic() + 15
    while time.monotonic() < deadline:
        files = sorted(Path(MAIL_DIR).glob("*.log"), key=lambda path: path.stat().st_mtime)
        for path in reversed(files):
            if path.stat().st_mtime < after:
                continue
            text = path.read_text(encoding="utf-8", errors="ignore")
            if f"To: {address}" in text:
                match = re.search(r"https?://\S+\?kood=\S+", text)
                if match:
                    return match.group(0)
        time.sleep(0.5)
    raise AssertionError(f"no link was mailed to {address}")


def _code(secret: str) -> str:
    """A code the server has not accepted yet; waits for the next step if needed."""
    totp = pyotp.TOTP(secret)
    spent = int(STATE.get("spent_step", 0))
    while True:
        current = int(time.time()) // 30
        for step in (current - 1, current, current + 1):
            if step > spent:
                STATE["spent_step"] = step
                return totp.at(step * 30)
        time.sleep(2)


def _sign_in(page, base: str, address: str, password: str, *, secret: str = "") -> None:
    page.goto(f"{base}/konto/sisene/")
    page.get_by_label("E-post").fill(address)
    page.get_by_label("Parool").fill(password)
    page.get_by_role("button", name="Logi sisse").click()
    if secret:
        expect(page.get_by_role("heading", name="Kinnituskood")).to_be_visible()
        page.get_by_label("Kood").fill(_code(secret))
        page.get_by_role("button", name="Kinnita").click()
    page.wait_for_load_state("networkidle")


def _set_password(page, link: str, password: str) -> None:
    page.goto(link)
    expect(page).not_to_have_url(re.compile("kood="))
    page.get_by_label("Uus parool", exact=True).fill(password)
    page.get_by_label("Uus parool uuesti").fill(password)
    page.get_by_role("button", name="Salvesta parool").click()
    expect(page.get_by_text("Parool on seatud. Logi nüüd sisse.")).to_be_visible()


# -- the administrator arrives --------------------------------------------------------------


def test_the_first_administrator_activates_and_must_enrol(
    page, local_base_url, first_administrator
):
    _set_password(page, _link_for(ADMIN), ADMIN_PASSWORD)

    _sign_in(page, local_base_url, ADMIN, ADMIN_PASSWORD)

    # A second factor is mandatory for an administrator: nothing else opens.
    expect(page).to_have_url(f"{local_base_url}/konto/turvalisus/seadista/")
    expect(page.locator(".acctqr svg")).to_be_visible()
    page.goto(f"{local_base_url}/minu-asjad/")
    expect(page).to_have_url(f"{local_base_url}/konto/turvalisus/seadista/")

    secret = page.locator("[data-totp-secret]").inner_text().strip()
    STATE["secret"] = secret
    page.get_by_label("Rakenduse kood").fill(_code(secret))
    page.get_by_role("button", name="Kinnita").click()

    expect(page.get_by_role("heading", name="Taastekoodid")).to_be_visible()
    expect(page.locator(".acctcodes__code")).to_have_count(10)
    page.get_by_role("link", name="Olen koodid salvestanud").click()
    page.wait_for_load_state("networkidle")
    # Offered now, and only now: an administrator with a second-factor session.
    # Read from the bar's hrefs, because at 1440px the item sits inside the
    # collapsed «Veel» menu (e2e/conftest.py `navigation_targets`).
    assert "/haldus/kasutajad/" in navigation_targets(page)


def test_the_user_list_and_creating_an_account(page, local_base_url, first_administrator):
    _sign_in(page, local_base_url, ADMIN, ADMIN_PASSWORD, secret=str(STATE["secret"]))
    page.goto(f"{local_base_url}/haldus/kasutajad/")

    expect(page.get_by_role("heading", name="Kasutajad")).to_be_visible()
    expect(page.get_by_role("cell", name="E2E Haldur")).to_be_visible()

    page.get_by_role("link", name="+ Uus kasutaja").click()
    page.get_by_label("Nimi").fill("E2E Jurist")
    page.get_by_label("E-post").fill(COLLEAGUE)
    page.get_by_label("Roll").select_option("SPECIALIST")
    page.get_by_role("button", name="Salvesta").click()

    expect(page.get_by_role("heading", name="E2E Jurist")).to_be_visible()
    expect(page.locator(".pagehead .badge")).to_have_text("Ootab kinnitust")

    sent_at = time.time() - 1
    page.get_by_role("button", name="Kinnita ja saada kutse").click()
    expect(page.get_by_text("Link saadeti konto aadressile.")).to_be_visible()
    expect(page.locator(".pagehead .badge")).to_have_text("Kutsutud")
    STATE["invited_at"] = sent_at


def test_granting_a_permission(page, local_base_url, first_administrator):
    _sign_in(page, local_base_url, ADMIN, ADMIN_PASSWORD, secret=str(STATE["secret"]))
    page.goto(f"{local_base_url}/haldus/kasutajad/?q=jurist")
    page.get_by_role("link", name="Ava").first.click()

    toggle = page.get_by_role("checkbox", name=re.compile("Osakonna juhtimisvaade"))
    expect(toggle).not_to_be_checked()
    toggle.check()
    page.get_by_role("button", name="Salvesta").click()

    expect(page.get_by_text("Salvestatud.")).to_be_visible()
    expect(page.get_by_role("checkbox", name=re.compile("Osakonna juhtimisvaade"))).to_be_checked()
    expect(page.get_by_role("cell", name="Õigused muudeti")).to_be_visible()


# -- the colleague arrives --------------------------------------------------------------------


def test_the_colleague_activates_with_their_own_password(
    browser, local_base_url, first_administrator
):
    context = browser.new_context(locale="et-EE")
    page = context.new_page()
    link = _link_for(COLLEAGUE, after=float(STATE.get("invited_at", 0)))

    page.goto(link)
    page.get_by_label("Uus parool", exact=True).fill("liiga lühike")
    page.get_by_label("Uus parool uuesti").fill("liiga lühike")
    page.get_by_role("button", name="Salvesta parool").click()
    expect(page.get_by_text(re.compile("vähemalt 15 märki"))).to_be_visible()

    page.get_by_label("Uus parool", exact=True).fill(COLLEAGUE_PASSWORD)
    page.get_by_label("Uus parool uuesti").fill(COLLEAGUE_PASSWORD)
    page.get_by_role("button", name="Salvesta parool").click()
    expect(page.get_by_text("Parool on seatud. Logi nüüd sisse.")).to_be_visible()

    _sign_in(page, local_base_url, COLLEAGUE, COLLEAGUE_PASSWORD)
    expect(page).to_have_url(f"{local_base_url}/minu-asjad/")
    page.get_by_role("link", name=re.compile("Minu konto")).click()
    expect(page.get_by_text("Osakonna juhtimisvaade")).to_be_visible()
    # A colleague who is not an administrator is offered no administration.
    assert "/haldus/kasutajad/" not in navigation_targets(page)
    context.close()


def test_a_forgotten_password_is_recovered(browser, local_base_url, first_administrator):
    context = browser.new_context(locale="et-EE")
    page = context.new_page()
    asked_at = time.time() - 1
    page.goto(f"{local_base_url}/konto/sisene/")
    page.get_by_role("link", name="Unustasid parooli?").click()
    page.get_by_label("E-post").fill(COLLEAGUE)
    page.get_by_role("button", name="Saada link").click()
    expect(
        page.get_by_text(re.compile("Kui see aadress kuulub aktiivsele kontole"))
    ).to_be_visible()

    _set_password(page, _link_for(COLLEAGUE, after=asked_at), NEW_PASSWORD)

    _sign_in(page, local_base_url, COLLEAGUE, COLLEAGUE_PASSWORD)
    expect(page.get_by_text("E-posti aadress või parool on vale.")).to_be_visible()
    _sign_in(page, local_base_url, COLLEAGUE, NEW_PASSWORD)
    expect(page).to_have_url(f"{local_base_url}/minu-asjad/")
    context.close()


# -- failure states ---------------------------------------------------------------------------


def test_failures_say_one_thing_each(page, local_base_url, first_administrator):
    _sign_in(page, local_base_url, "pole.olemas@koda.ee", "mingi parool siin kirjas")
    expect(page.get_by_text("E-posti aadress või parool on vale.")).to_be_visible()

    page.goto(f"{local_base_url}/konto/aktiveeri/?kood=vale")
    expect(page.get_by_text(re.compile("See link ei kehti enam"))).to_be_visible()

    page.goto(f"{local_base_url}/konto/sisene/")
    page.get_by_label("E-post").fill(ADMIN)
    page.get_by_label("Parool").fill(ADMIN_PASSWORD)
    page.get_by_role("button", name="Logi sisse").click()
    page.get_by_label("Kood").fill("000000")
    page.get_by_role("button", name="Kinnita").click()
    expect(page.get_by_text("Kood ei sobinud. Proovi uuesti.")).to_be_visible()


def test_the_pages_hold_together_on_a_phone(browser, local_base_url, first_administrator):
    context = browser.new_context(locale="et-EE", viewport={"width": 375, "height": 812})
    page = context.new_page()

    def no_sideways_scroll() -> None:
        overflow = page.evaluate(
            "document.documentElement.scrollWidth - document.documentElement.clientWidth"
        )
        assert overflow <= 1, f"{page.url} scrolls sideways by {overflow}px"

    page.goto(f"{local_base_url}/konto/sisene/")
    no_sideways_scroll()
    _sign_in(page, local_base_url, ADMIN, ADMIN_PASSWORD, secret=str(STATE["secret"]))
    for path in ("/haldus/kasutajad/", "/haldus/kasutajad/uus/", "/konto/profiil/"):
        page.goto(f"{local_base_url}{path}")
        no_sideways_scroll()
    page.goto(f"{local_base_url}/haldus/kasutajad/?q=jurist")
    page.get_by_role("link", name="Ava").first.click()
    no_sideways_scroll()
    context.close()
