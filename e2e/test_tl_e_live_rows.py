"""Agent E (independent QA): the projected historical rows on a live Teema page.

Needs a Teema seeded with synthetic OneNote notes (the id in ``TL_E_MATTER``);
skipped otherwise. Screenshots go to ``TL_E_SCREENS`` when set.
"""

from __future__ import annotations

import os
import re

import pytest
from playwright.sync_api import expect

from e2e.conftest import SANDRA, document_overflows, sign_in

pytestmark = [
    pytest.mark.e2e,
    pytest.mark.skipif(not os.environ.get("TL_E_MATTER"), reason="TL_E_MATTER is not set"),
]

MATTER = os.environ.get("TL_E_MATTER", "")
SCREENS = os.environ.get("TL_E_SCREENS", "")


def _shot(page, name):
    if SCREENS:
        page.screenshot(path=os.path.join(SCREENS, f"{name}.png"), full_page=False)


def _teema(page, base_url, query=""):
    page.goto(f"{base_url}/teemad/{MATTER}/{query}")
    page.wait_for_load_state("networkidle")


def _rows(page):
    return page.locator("article.uxtl__item--historical")


@pytest.mark.parametrize("width", [1440, 375])
def test_rows_render_closed_and_fit(page, base_url, width):
    dialogs = []
    page.on("dialog", lambda d: (dialogs.append(d.message), d.dismiss()))
    page.set_viewport_size({"width": width, "height": 900})
    sign_in(page, base_url, SANDRA)
    _teema(page, base_url)
    rows = _rows(page)
    expect(rows.first).to_be_attached()
    # 3 dated notes + 1 dated month note + 1 document row; the undated NA prose is no row.
    assert rows.count() == 5
    assert page.locator("details.uxtl__hist[open]").count() == 0
    lines = [rows.nth(i).locator("summary").inner_text() for i in range(rows.count())]
    joined = " | ".join(" ".join(line.split()) for line in lines)
    assert "Ootame vastust" not in page.content()
    assert re.search(r"05\.08\.2026 2 dokumenti Koda saatis arvamuse", joined), joined
    assert re.search(r"29\.07\.2026 .*EELNÕU\.pdf ja 1 muu fail", joined), joined
    assert "08.2026" in joined
    rows.first.locator("summary").scroll_into_view_if_needed()
    _shot(page, f"rows-closed-{width}")
    if width == 375:
        assert not document_overflows(page)
    note = rows.filter(has_text="Koda saatis arvamuse").first
    note.locator("summary").click()
    expect(note.locator("details")).to_have_attribute("open", "")
    expect(note).to_contain_text("<script>alert(1)</script>")
    expect(note.locator("a[href^='javascript:']")).to_have_count(0)
    expect(note.locator("a[href='https://naidis.example.invalid/eelnou']")).to_have_count(1)
    expect(note).to_contain_text("Arvamus.pdf")
    _shot(page, f"row-open-{width}")
    if width == 375:
        assert not document_overflows(page)
    assert dialogs == []


def test_keyboard_and_accessible_name(page, base_url):
    sign_in(page, base_url, SANDRA)
    _teema(page, base_url)
    note = _rows(page).filter(has_text="Koda saatis arvamuse").first
    summary = note.locator("summary")
    expect(summary).to_have_accessible_name(
        re.compile(r"05\.08\.2026.*2 dokumenti.*Koda saatis arvamuse")
    )
    summary.focus()
    expect(summary).to_be_focused()
    page.keyboard.press("Enter")
    expect(note.locator("details")).to_have_attribute("open", "")
    page.keyboard.press("Space")
    expect(note.locator("details")).not_to_have_attribute("open", "")
    # Tab reaches the summary from the element before it.
    page.keyboard.press("Shift+Tab")
    page.keyboard.press("Tab")
    expect(summary).to_be_focused()


def test_without_javascript_the_rows_open_natively(browser, base_url):
    context = browser.new_context(
        java_script_enabled=False, viewport={"width": 1440, "height": 900}
    )
    page = context.new_page()
    try:
        page.goto(f"{base_url}/konto/arendus-sisselogimine/")
        page.get_by_label(SANDRA.display_name, exact=False).check()
        page.get_by_role("button", name="Logi sisse").click()
        page.wait_for_load_state("load")
        page.goto(f"{base_url}/teemad/{MATTER}/")
        rows = _rows(page)
        assert rows.count() == 5
        note = rows.filter(has_text="kojal ei olnud märkusi").first
        note.locator("summary").click()
        expect(note.locator("details")).to_have_attribute("open", "")
        expect(note).to_contain_text("Ava OneNote'i leht Juristides")
        _shot(page, "nojs-open")
    finally:
        context.close()


def test_a_deep_link_opens_the_row_and_survives_back_and_forward(page, base_url):
    sign_in(page, base_url, SANDRA)
    _teema(page, base_url)
    note = _rows(page).filter(has_text="kojal ei olnud märkusi").first
    key = note.get_attribute("data-kaik-rida")
    assert key and key.startswith("onenote-note:")
    _teema(page, base_url, f"?kirje={key}#kaik-{key}-rida")
    target = page.locator(f"article[data-kaik-rida='{key}']")
    expect(target.locator("details")).to_have_attribute("open", "")
    _shot(page, "deeplink-open")
    page.goto(f"{base_url}/minu-asjad/")
    page.go_back()
    page.wait_for_load_state("networkidle")
    expect(page.locator(f"article[data-kaik-rida='{key}'] details")).to_have_attribute("open", "")
    page.go_forward()
    page.wait_for_url(f"{base_url}/minu-asjad/")
    page.go_back()
    page.wait_for_load_state("networkidle")
    expect(page.locator(f"article[data-kaik-rida='{key}'] details")).to_have_attribute("open", "")


def test_the_page_files_link_lands_on_the_onenote_page(page, base_url):
    sign_in(page, base_url, SANDRA)
    _teema(page, base_url)
    note = _rows(page).filter(has_text="kojal ei olnud märkusi").first
    note.locator("summary").click()
    link = note.get_by_role("link", name=re.compile(r"Sama OneNote'i lehe failid \(2\)"))
    link.click()
    page.wait_for_load_state("load")
    assert "/ajalugu/" in page.url and page.url.endswith("#sisu-heading")
    expect(page.locator("#sisu-heading")).to_be_visible()
    expect(page.get_by_text("EELNÕU.pdf").first).to_be_visible()
    _shot(page, "ajalugu-page")
