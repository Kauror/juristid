"""The r98 Haldus pages in a real browser (final corrections, item 8).

Runs against the personal sign-in server (`AUTH_MODE=local_password`, its own database), because
these pages are for a verified technical administrator and that server is where one can be
appointed and enrolled the way an operator does it (`e2e/test_ls_c_data_sync.py` does the same).
The world is synthetic: `tests/r98_visual_fixture.seed_browser` writes an AI ledger, nightly
records with a night that left none, a source run that finished with errors, document states and
two parity reports, in a subprocess against this server's own database. Nothing reaches Lovable or
a model provider: no key is configured and every page only reads what was written.

What is proved here that the template tests cannot: the pages open for a technical administrator
and are refused to everyone else; the AI filters, totals, detail table, pagination and export
agree; incomplete, unknown and partial states are *said*; a missing night is a row; the two
parity verifications are shown apart with their scope; the discrepancy link works; every page
holds together at phone width, in both themes and at all four text sizes; and the filter form is
operable from the keyboard.
"""

from __future__ import annotations

import csv
import io
import re
import time
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

TECHNICIAN = "e2e.r98.tehnik@koda.ee"
TECHNICIAN_PASSWORD = "tehnik vaatab öösel kõiki lehti"  # noqa: S105
ORDINARY = "e2e.r98.jurist@koda.ee"
ORDINARY_PASSWORD = "jurist ei ava haldust ilma õiguseta"  # noqa: S105

AI = "/haldus/ai-kasutus/"
NIGHTS = "/haldus/oised-tsuklid/"
FRESH = "/haldus/varskus/"
PROCESSING = "/haldus/dokumentide-tootlus/"
LOVABLE = "/haldus/lovable/"
PAGES = [
    ("AI kasutus", AI, "Arvestuse seis"),
    ("Öised tsüklid", NIGHTS, "Öised tsüklid"),
    ("Allikate värskus", FRESH, "Allikate värskus"),
    ("Dokumentide töötlus", PROCESSING, "Dokumentide töötlus"),
    ("Lovable", LOVABLE, "Sünkroonimise diagnostika"),
]
RANGE = "?periood=custom&alates=2020-01-01&kuni=2099-12-31"
SIZES = (None, "vaike", "suur", "vaga-suur")


@pytest.fixture(scope="module")
def world(local_base_url):  # noqa: F811
    """An empty database, a technical administrator, an ordinary user and the synthetic world."""
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
        "E2E R98 Tehnik",
        "--role",
        "ADMINISTRATOR",
        "--note",
        "browser suite, r98 pages",
    )
    _manage(
        "shell",
        "-c",
        "from tests.r98_visual_fixture import seed_browser; seed_browser()",
    )
    return local_base_url


@pytest.fixture(scope="module")
def admin_state(world, browser, tmp_path_factory):
    """Activate, enrol the authenticator and sign in ONCE; the session is reused by every test
    (each code works once, so a sign-in per test would wait on the clock)."""
    context = browser.new_context(viewport={"width": 1440, "height": 900}, locale="et-EE")
    page = context.new_page()
    _set_password(page, _link_for(TECHNICIAN), TECHNICIAN_PASSWORD)
    _sign_in(page, world, TECHNICIAN, TECHNICIAN_PASSWORD)
    expect(page).to_have_url(f"{world}/konto/turvalisus/seadista/")
    secret = page.locator("[data-totp-secret]").inner_text().strip()
    STATE["r98_secret"] = secret
    page.get_by_label("Rakenduse kood").fill(_code(secret))
    page.get_by_role("button", name="Kinnita").click()
    expect(page.get_by_role("heading", name="Taastekoodid")).to_be_visible()
    page.get_by_role("link", name="Olen koodid salvestanud").click()
    page.wait_for_load_state("networkidle")
    # the ordinary colleague, invited the way an administrator invites one
    page.goto(f"{world}/haldus/kasutajad/")
    page.get_by_role("link", name="+ Uus kasutaja").click()
    page.get_by_label("Nimi").fill("E2E R98 Jurist")
    page.get_by_label("E-post").fill(ORDINARY)
    page.get_by_label("Roll").select_option("SPECIALIST")
    page.get_by_role("button", name="Salvesta").click()
    sent_at = time.time() - 1
    page.get_by_role("button", name="Kinnita ja saada kutse").click()
    expect(page.get_by_text("Link saadeti konto aadressile.")).to_be_visible()
    STATE["r98_invited_at"] = sent_at
    page.goto(f"{world}/haldus/")
    expect(page.get_by_role("heading", level=1)).to_be_visible()
    path = tmp_path_factory.mktemp("r98") / "state.json"
    context.storage_state(path=str(path))
    context.close()
    return path


@pytest.fixture(scope="module")
def browser_context_args(browser_context_args, admin_state):  # noqa: F811
    return {**browser_context_args, "storage_state": str(admin_state)}


def _open(page, base, path):
    page.goto(f"{base}{path}")
    page.wait_for_load_state("networkidle")


def _number(page, label: str) -> int:
    text = page.locator(f"dt:text-is('{label}') + dd").first.inner_text()
    return int(re.search(r"\d+", text).group())  # type: ignore[union-attr]


# -- access ---------------------------------------------------------------------------------


@pytest.mark.parametrize(("label", "path", "heading"), PAGES)
def test_the_technical_administrator_opens_every_new_page(page, world, label, path, heading):
    _open(page, world, path)
    expect(page.get_by_role("heading", name=heading, exact=True).first).to_be_visible()
    nav = page.get_by_role("navigation", name="Haldus", exact=True)
    expect(nav.get_by_role("link", name=label, exact=True)).to_have_attribute("aria-current", "page")


def test_a_visitor_and_an_ordinary_user_are_refused(world, browser):
    visitor = browser.new_context()
    page = visitor.new_page()
    for _, path, _ in PAGES:
        response = page.goto(f"{world}{path}")
        assert response is not None
        assert "Sünkroonimise diagnostika" not in page.content()
        assert "Arvestuse seis" not in page.content()
    visitor.close()
    ordinary = browser.new_context()
    page = ordinary.new_page()
    _set_password(
        page, _link_for(ORDINARY, after=float(STATE.get("r98_invited_at", 0))), ORDINARY_PASSWORD
    )
    _sign_in(page, world, ORDINARY, ORDINARY_PASSWORD)
    for _, path, _ in PAGES:
        response = page.goto(f"{world}{path}")
        assert response is not None and response.status == 404
        assert "Arvestuse seis" not in page.content() and "Teenus" not in page.content()
    assert page.goto(f"{world}{AI}csv/").status == 404  # type: ignore[union-attr]
    ordinary.close()


# -- the AI page ----------------------------------------------------------------------------


def test_filters_change_the_totals_and_the_totals_equal_the_detail_rows(page, world):
    """Each filter's «Füüsilisi katseid» is the number of rows the detail table lists."""
    _open(page, world, f"{AI}{RANGE}")
    everything = _number(page, "Füüsilised katsed")
    assert everything > 50
    seen = {}
    for query in ("&teenus=candidates", "&teenus=watch_findings&tulemus=SUCCEEDED", "&tulemus=FAILED"):
        _open(page, world, f"{AI}{RANGE}{query}")
        attempts = _number(page, "Füüsilised katsed")
        pager = page.locator("nav.pager span")
        pages = int(re.search(r"/ (\d+)", pager.inner_text()).group(1)) if pager.count() else 1  # type: ignore[union-attr]
        rows = page.locator("[aria-labelledby=aiusage-detail] tbody tr").count()
        if pages == 1:
            assert rows == attempts, query
        else:
            assert rows == 50 and (pages - 1) * 50 < attempts <= pages * 50, query
        seen[query] = attempts
    assert seen["&tulemus=FAILED"] < everything
    assert all(n <= everything for n in seen.values())


def test_unknown_and_incomplete_accounting_is_said_and_never_called_complete(page, world):
    _open(page, world, f"{AI}{RANGE}")
    body = page.locator("main").inner_text()
    assert "Arvestus puudulik" in body and "Arvestus täielik" not in body
    assert "teadmata" in body.lower()
    assert "Hoiatuspiire pole seatud" in body  # a policy gap, said as such, not as «Korras»
    assert "vähemalt" in body  # the cost is a lower bound
    expect(page.locator(".statechip", has_text="puudulik").first).to_be_visible()
    # the interrupted request without an attempt is a named reason
    assert "katkestatud" in body.lower() or "katse võis olla" in body.lower()


def test_pagination_and_the_export_agree_with_the_selection(page, world):
    _open(page, world, f"{AI}{RANGE}")
    total = _number(page, "Füüsilised katsed")
    first = page.locator("[aria-labelledby=aiusage-detail] tbody tr").first.inner_text()
    expect(page.get_by_role("navigation", name="Lehed")).to_contain_text("Leht 1 /")
    page.get_by_role("link", name="Järgmine").click()
    page.wait_for_load_state("networkidle")
    assert "leht=2" in page.url and "periood=custom" in page.url
    assert page.locator("[aria-labelledby=aiusage-detail] tbody tr").first.inner_text() != first
    with page.expect_download() as download:
        page.get_by_role("link", name="Laadi CSV").click()
    text = Path(download.value.path()).read_text(encoding="utf-8")
    rows = list(csv.reader(io.StringIO(text), delimiter=";"))
    assert rows[0][7] == "Aeg vastuse päisteni ms"
    assert len(rows) - 1 == total
    assert all(not cell.startswith(("=", "+", "@")) for row in rows[1:] for cell in row)


def test_a_day_link_keeps_the_filters(page, world):
    _open(page, world, f"{AI}{RANGE}&teenus=candidates")
    link = page.locator("[aria-labelledby=aiusage-days] tbody a").first
    href = link.get_attribute("href") or ""
    assert "teenus=candidates" in href and "periood=custom" in href
    link.click()
    page.wait_for_load_state("networkidle")
    assert "teenus=candidates" in page.url
    expect(page.locator("select[name=teenus]")).to_have_value("candidates")


# -- nightly history, freshness, processing, Lovable -------------------------------------------


def test_nightly_history_lists_the_night_that_left_nothing_and_the_partial_one(page, world):
    _open(page, world, NIGHTS)
    body = page.locator("main").inner_text()
    assert "Tsükkel puudub täielikult" in body
    assert "Osaline" in body and "Kirje puudub" in body
    assert "ligikaudu" in body  # the AI column says it is approximate
    assert page.locator("table tbody tr").count() >= 5


def test_a_source_run_with_errors_is_partial_not_fresh(page, world):
    _open(page, world, FRESH)
    row = page.locator("tr", has_text="Registrite jälgimine").first
    expect(row).to_contain_text("osaline")
    expect(page.locator("th", has_text="Viimane täielikult õnnestunud kontroll")).to_be_visible()
    expect(page.locator("th", has_text="Viimane lõppenud käivitus")).to_be_visible()


def test_document_and_onenote_diagnostics_keep_states_apart(page, world):
    _open(page, world, PROCESSING)
    for text in ("Ootab töötlemist", "Ebaõnnestunud", "Teadlikult töötlemata", "OneNote'i katvus"):
        expect(page.get_by_text(text).first).to_be_visible()
    assert "Faili lugemine katkestati" not in page.content()  # a failure's own words are not shown


def test_the_lovable_page_shows_both_verifications_apart_and_never_green_for_a_sample(page, world):
    _open(page, world, LOVABLE)
    stored = page.locator("[data-parity-source='snapshot']")
    fresh = page.locator("[data-parity-source='fresh']")
    expect(stored).to_have_attribute("data-parity-status", "mismatch")
    expect(fresh).to_have_attribute("data-parity-status", "clean_sample")
    expect(fresh).to_have_attribute("data-parity-currency", "incomplete")
    expect(fresh).to_contain_text("ei tõenda kogu registri kooskõla")
    expect(fresh).to_contain_text("valim 10 / 30")
    expect(stored).to_contain_text("Kinnitamata (UNVERIFIED)")
    assert "Kogu register on kooskõlas" not in page.locator("main").inner_text()
    expect(page.locator("[data-lovable-diagnostics]")).to_contain_text("Tavaline piir")


def test_a_discrepancy_leads_to_the_existing_differences_workflow(page, world):
    _open(page, world, LOVABLE)
    row = page.locator("[data-parity-source='snapshot'] tbody tr", has_text="2099_3")
    expect(row).to_contain_text("pealkiri")
    row.get_by_role("link", name="Erinevused").click()
    page.wait_for_url(re.compile(r"/haldus/andmete-sunkroonimine/erinevused/"))
    expect(page.get_by_role("heading", level=1)).to_be_visible()
    held = page.goto(f"{world}{LOVABLE}")
    assert held is not None and held.ok
    expect(page.locator("a", has_text="Ootel sulgemine").first).to_have_attribute(
        "href", re.compile(r"/ootel-sulgemine/\?viide=")
    )


# -- layout, themes, text sizes, keyboard ------------------------------------------------------


def test_every_page_holds_together_at_phone_width(page, world):
    page.set_viewport_size({"width": 390, "height": 844})
    for _, path, _ in PAGES:
        _open(page, world, f"{path}{RANGE if path == AI else ''}")
        assert not document_overflows(page), path
        main = page.locator("main").bounding_box()
        assert main is not None and main["x"] >= -1 and main["x"] + main["width"] <= 391, path
    # the filter form stays reachable and usable
    _open(page, world, AI)
    expect(page.locator("select[name=teenus]")).to_be_visible()
    expect(page.get_by_role("button", name="Näita")).to_be_visible()


@pytest.mark.parametrize("theme", ["dark", "light"])
@pytest.mark.parametrize("size", SIZES)
def test_every_page_holds_in_both_themes_at_all_four_text_sizes(page, world, theme, size):
    page.set_viewport_size({"width": 1280, "height": 900})
    for _, path, heading in PAGES:
        page.goto(f"{world}{path}")
        page.evaluate(
            """([theme, size]) => {
                localStorage.setItem('juristid-theme', theme);
                if (size) localStorage.setItem('juristid-text-size', size);
                else localStorage.removeItem('juristid-text-size');
            }""",
            [theme, size],
        )
        page.reload()
        page.wait_for_load_state("networkidle")
        assert page.evaluate("document.documentElement.getAttribute('data-theme')") == theme
        assert (page.evaluate("document.documentElement.getAttribute('data-text-size')") or None) == size
        assert not document_overflows(page), (path, theme, size)
        expect(page.get_by_role("heading", name=heading, exact=True).first).to_be_visible()
    page.evaluate("localStorage.removeItem('juristid-text-size')")


def test_the_filter_form_is_operable_from_the_keyboard(page, world):
    _open(page, world, AI)
    service = page.locator("select[name=teenus]")
    page.locator("#global-search").focus()
    for _ in range(80):
        page.keyboard.press("Tab")
        if service.evaluate("(el) => el === document.activeElement"):
            break
    else:
        raise AssertionError("Tab never reached the service filter")
    page.keyboard.press("ArrowDown")  # the first named service
    chosen = service.input_value()
    assert chosen
    page.get_by_role("button", name="Näita").focus()
    page.keyboard.press("Enter")
    page.wait_for_url(re.compile(f"teenus={chosen}"))
    expect(page.locator("select[name=teenus]")).to_have_value(chosen)
    # a visible focus ring on the control that has focus
    page.locator("select[name=teenus]").focus()
    outline = page.evaluate(
        "() => getComputedStyle(document.activeElement).outlineStyle + '|' + "
        "getComputedStyle(document.activeElement).boxShadow"
    )
    assert outline and "none|none" not in outline
