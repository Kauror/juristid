"""Haldus → Andmete sünkroonimine's confirm flow in a real browser (LS-C).

Runs against the personal sign-in server (`AUTH_MODE=local_password`, its own
database, mail to files — `config/e2e_local_auth_settings.py`), because the
page is for a verified technical administrator and that server is where one
can be appointed and enrolled the way an operator does it. It reuses
`e2e/test_local_auth.py`'s helpers, flushes that database first, appoints a
technical administrator with `bootstrap_account_admin --role ADMINISTRATOR`,
and switches the synchronisation to COMPARE with the operator command.

Nothing here reaches Lovable: «Sünkrooni nüüd» only records a request, and the
test reads it back with `data_sync_requests list`. No host runner runs.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from playwright.sync_api import expect

from e2e.conftest import document_overflows
from e2e.test_local_auth import (
    MAIL_DIR,
    STATE,
    _code,
    _link_for,
    _manage,
    _set_password,
    _sign_in,
    local_base_url,  # noqa: F401 - the fixture
)

pytestmark = pytest.mark.e2e

TECHNICIAN = "e2e.tehnik@koda.ee"
TECHNICIAN_PASSWORD = "tehnik vaatab öösel sünkroonimist"  # noqa: S105
PAGE = "/haldus/andmete-sunkroonimine/"


@pytest.fixture(scope="module")
def technical_administrator(local_base_url):  # noqa: F811
    """An empty world, one technical administrator and the sync in COMPARE."""
    _manage("flush", "--noinput")
    for old in Path(MAIL_DIR).glob("*"):
        old.unlink()
    _manage(
        "bootstrap_account_admin",
        "--email",
        TECHNICIAN,
        "--confirm-email",
        TECHNICIAN,
        "--display-name",
        "E2E Tehnik",
        "--role",
        "ADMINISTRATOR",
        "--note",
        "browser suite, Andmete sünkroonimine",
    )
    _manage("lovable_sync", "control", "--mode", "compare", "--reason", "browser suite")
    return TECHNICIAN


def _requests() -> list[dict]:
    out = _manage("data_sync_requests", "list", "--kind", "lovable")
    return [json.loads(line) for line in out.splitlines() if line.strip().startswith("{")]


def _enrolled(page, base: str) -> None:
    """Activate, sign in, set up the authenticator: the page needs a second factor."""
    _set_password(page, _link_for(TECHNICIAN), TECHNICIAN_PASSWORD)
    _sign_in(page, base, TECHNICIAN, TECHNICIAN_PASSWORD)
    expect(page).to_have_url(f"{base}/konto/turvalisus/seadista/")
    secret = page.locator("[data-totp-secret]").inner_text().strip()
    STATE["technician_secret"] = secret
    page.get_by_label("Rakenduse kood").fill(_code(secret))
    page.get_by_role("button", name="Kinnita").click()
    expect(page.get_by_role("heading", name="Taastekoodid")).to_be_visible()
    page.get_by_role("link", name="Olen koodid salvestanud").click()
    page.wait_for_load_state("networkidle")


def test_sync_now_is_confirmed_with_the_mode_and_recorded_once(
    page,
    local_base_url,  # noqa: F811
    technical_administrator,
):
    _enrolled(page, local_base_url)
    page.goto(f"{local_base_url}{PAGE}")
    expect(page.get_by_role("heading", name="Lovable", exact=True)).to_be_visible()
    expect(page.get_by_role("heading", name="OneNote", exact=True)).to_be_visible()
    expect(page.get_by_text("Inventuuri pole veel tehtud.")).to_be_visible()

    # The confirmation says the mode and what it does; cancelling records nothing.
    page.get_by_role("link", name="Sünkrooni nüüd").click()
    expect(page.get_by_role("heading", name="Sünkrooni Lovable nüüd")).to_be_visible()
    expect(page.get_by_text("Juristide teemasid ei muudeta.", exact=False)).to_be_visible()
    confirm = page.get_by_role("button", name="Kinnitan: sünkrooni režiimis «Ainult võrdlus»")
    expect(confirm).to_be_visible()
    page.get_by_role("link", name="Loobu").click()
    page.wait_for_url(f"{local_base_url}{PAGE}")
    assert _requests() == []

    page.get_by_role("link", name="Sünkrooni nüüd").click()
    page.get_by_label("Põhjus").fill("uus teema Lovable'is")
    page.get_by_role("button", name="Kinnitan: sünkrooni režiimis «Ainult võrdlus»").click()
    page.wait_for_url(f"{local_base_url}{PAGE}")
    expect(page.get_by_text("Sünkroonimise taotlus on salvestatud.", exact=False)).to_be_visible()
    expect(page.locator(".dsync__request").first).to_contain_text("Ootab käivitamist")
    recorded = _requests()
    assert [(r["kind"], r["status"]) for r in recorded] == [("LOVABLE_SYNC", "PENDING")]

    # A second request while one is open is refused, and says why.
    page.get_by_role("link", name="Sünkrooni nüüd").click()
    expect(page.get_by_text("Üks taotlus juba ootab või käib", exact=False)).to_be_visible()
    page.get_by_label("Põhjus").fill("teine kord")
    page.get_by_role("button", name="Kinnitan: sünkrooni režiimis «Ainult võrdlus»").click()
    expect(page.get_by_role("alert").or_(page.locator(".messages"))).to_contain_text(
        "Üks taotlus selles reas juba ootab"
    )
    assert len(_requests()) == 1


def test_the_page_holds_together_at_phone_width(
    page,
    local_base_url,  # noqa: F811
    technical_administrator,
):
    _sign_in(
        page,
        local_base_url,
        TECHNICIAN,
        TECHNICIAN_PASSWORD,
        secret=str(STATE["technician_secret"]),
    )
    page.set_viewport_size({"width": 390, "height": 844})
    for path in (PAGE, f"{PAGE}sunkrooni/", f"{PAGE}ajalugu/", f"{PAGE}erinevused/"):
        page.goto(f"{local_base_url}{path}")
        page.wait_for_load_state("networkidle")
        assert not document_overflows(page), path
