"""UI-13 in a real browser: an opened OneNote note row says each thing once.

The rows are rendered from the real template with contract objects
(`tests/test_tl_c_historical_rows.py`) and served, with the application's own
stylesheets and scripts inlined, from a routed `http://juristid.test/` page so
a relative address resolves the way it does on the Teema page. No server and
no seeded world: what is proved is the template, the stylesheet and
`bindCopyLink` (static/js/ux.js) — the first paragraph replacing the preview
in place, keyboard expand and collapse, the copy icon right after the link it
copies and on its line, and what it copies.

Every string is invented.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest
from django.conf import settings
from playwright.sync_api import expect

from e2e.conftest import document_overflows
from tests.test_tl_c_historical_rows import a_file, a_note, note_item, render

pytestmark = pytest.mark.e2e

STATIC = Path(settings.BASE_DIR) / "static"
STYLESHEETS = (
    "css/tokens.css",
    "css/base.css",
    "css/app.css",
    "css/ux.css",
    "css/historical-reading.css",
)
SCRIPTS = ("js/app.js", "js/ux.js")
ORIGIN = "http://juristid.test"
PAGE_URL = "/ajalugu/0b5d8f0e-1111-4c2b-9d0e-000000000001/"

FIRST = (
    "Näidisamet saatis kooskõlastusringile eelnõu, millega muudetakse näidisseadust ja "
    "sellega seotud kolme määrust, ning palus koja arvamust kolme nädala jooksul."
)
SECOND = 'Tähtaeg on kolm nädalat; koja "märkuste tabel" on lisatud.'
SHORT = "Koda ei esitanud märkusi."


def _html() -> str:
    long_note = a_note(
        headline="Näidisamet saatis kooskõlastusringile eelnõu, millega…",
        paragraphs=(FIRST, SECOND),
        direct_files=(a_file(1), a_file(2)),
        page_url=PAGE_URL,
        anchor="plokk-0",
        page_title="Näidisseaduse muutmine 2026",
        shared_page=True,
    )
    short_note = a_note(
        date_display="10.08.2026",
        headline=SHORT,
        paragraphs=(SHORT,),
        page_url=PAGE_URL,
        anchor="plokk-5",
    )
    html, _root = render(
        note_item(long_note, key="onenote-note:a"), note_item(short_note, key="onenote-note:b")
    )
    styles = "".join(
        f"<style>{(STATIC / sheet).read_text(encoding='utf-8')}</style>" for sheet in STYLESHEETS
    )
    scripts = "".join(
        f"<script>{(STATIC / script).read_text(encoding='utf-8')}</script>" for script in SCRIPTS
    )
    return (
        '<!doctype html><html lang="et" data-theme="dark"><head><meta charset="utf-8">'
        '<meta name="viewport" content="width=device-width, initial-scale=1">'
        f"{styles}<style>body {{ margin: 0; padding: 40px 32px; }}</style></head>"
        '<body><main class="page"><div class="teema" id="teema-vaade-wrap">'
        f'<div id="ajalugu-loend" class="uxtl">{html}</div></div></main>'
        f"{scripts}</body></html>"
    )


#: The browser's clipboard, recorded: the routed page is not a secure context.
CLIPBOARD = """
Object.defineProperty(navigator, 'clipboard', {
  configurable: true,
  value: { writeText: (text) => { window.__copied = text; return Promise.resolve(); } },
});
"""


def _open(page, width: int) -> None:
    page.set_default_timeout(5000)
    page.set_viewport_size({"width": width, "height": 900})
    body = _html()
    page.route(
        f"{ORIGIN}/**",
        lambda route: route.fulfill(status=200, content_type="text/html", body=body),
    )
    page.add_init_script(CLIPBOARD)
    page.goto(f"{ORIGIN}/teemad/naidis/")


def _row(page, key: str):
    return page.locator(f'article.uxtl__item[data-kaik-rida="{key}"]')


@pytest.mark.parametrize("width", [1440, 390])
def test_the_first_paragraph_replaces_the_preview_in_place(page, width):
    _open(page, width)
    row = _row(page, "onenote-note:a")
    summary = row.locator("summary")
    preview = summary.locator(".uxtl__histpreview")
    first = summary.locator(".uxtl__histfirst")
    expect(preview).to_be_visible()
    expect(first).to_be_hidden()

    summary.focus()
    page.keyboard.press("Enter")
    expect(row.locator("details")).to_have_attribute("open", "")
    expect(preview).to_be_hidden()
    expect(first).to_be_visible()
    expect(first).to_have_text(FIRST)
    # The rest once below, and the first paragraph nowhere else.
    expect(row.locator(".uxtl__histpara")).to_have_count(1)
    expect(row.locator(".uxtl__histpara")).to_have_text(SECOND)
    expect(row.get_by_text(FIRST, exact=True)).to_have_count(1)
    # The headline line keeps the date; the line wraps inside the row.
    date_box = summary.locator("time").bounding_box()
    first_box = first.bounding_box()
    assert date_box is not None and first_box is not None
    assert first_box["y"] < date_box["y"] + date_box["height"] + 40
    assert not document_overflows(page)
    # Focus stays on the line; the accessible name holds the full first paragraph.
    expect(summary).to_be_focused()
    assert FIRST in summary.aria_snapshot()

    page.keyboard.press("Space")
    expect(row.locator("details")).not_to_have_attribute("open", re.compile(".*"))
    expect(preview).to_be_visible()


def test_a_note_that_fits_its_preview_reads_once_open(page):
    _open(page, 1440)
    row = _row(page, "onenote-note:b")
    row.locator("summary").click()
    expect(row.get_by_text(SHORT, exact=True)).to_have_count(1)
    expect(row.locator(".uxtl__histtext")).to_have_count(0)
    expect(row.get_by_role("link", name="OneNote'i koopia")).to_be_visible()


@pytest.mark.parametrize("width", [1440, 390])
def test_the_copy_icon_sits_right_after_its_link_and_copies_the_anchor(page, width):
    _open(page, width)
    row = _row(page, "onenote-note:a")
    row.locator("summary").click()
    link = row.get_by_role("link", name="OneNote'i koopia")
    button = row.get_by_role(
        "button", name="Kopeeri OneNote'i koopia link: Näidisseaduse muutmine 2026"
    )
    expect(link).to_have_attribute("href", f"{PAGE_URL}#plokk-0")
    link_box, button_box = link.bounding_box(), button.bounding_box()
    assert link_box is not None and button_box is not None
    # Same line, immediately after.
    assert (
        abs((link_box["y"] + link_box["height"] / 2) - (button_box["y"] + button_box["height"] / 2))
        < 6
    )
    assert 0 <= button_box["x"] - (link_box["x"] + link_box["width"]) < 24
    assert row.locator(".uxtl__histcopy > *").evaluate_all("els => els.map(e => e.tagName)") == [
        "A",
        "BUTTON",
    ]
    assert "ühine leht" in row.locator(".uxtl__histsource").inner_text()

    button.click()
    page.wait_for_function("() => window.__copied !== undefined")
    assert page.evaluate("() => window.__copied") == f"{ORIGIN}{PAGE_URL}#plokk-0"
    # The entry's permalink copies the entry, not the source.
    row.locator(".uxtl__permalink button").click()
    page.wait_for_function("() => window.__copied.includes('kirje=')")
    assert "#kaik-onenote-note:a-rida" in page.evaluate("() => window.__copied")
    assert not document_overflows(page)


def test_no_generated_date_or_source_row_shows_when_open(page):
    _open(page, 1440)
    row = _row(page, "onenote-note:a")
    row.locator("summary").click()
    text = row.inner_text()
    assert "KUUPÄEV" not in text.upper()
    assert "ALLIKAS" not in text.upper()
    assert "capture näidis" not in text and "plokid" not in text
