"""The historical OneNote rows of `Teema käik` in a real browser, against the real stylesheets.

The rows are rendered from the template with hand-built contract objects
(`tests/test_tl_c_historical_rows.py` builds them) and set into a page that
carries the application's own CSS and `app.js` inside the same
`#teema-vaade-wrap` / `#ajalugu-loend` scope a Teema page has. No server and no
seeded world are needed: what this proves is the template and the stylesheet —
a native `<details>` that opens by keyboard and pointer, with and without
scripting; the accordion's closed state not hiding it; and nothing pushing a
375 px page sideways. The live Teema page with projected rows is the
integration round's to drive.

Every string is invented.
"""

from __future__ import annotations

import os
import re
from datetime import UTC, datetime
from pathlib import Path

import pytest
from django.conf import settings
from playwright.sync_api import expect

from app.matters.timeline import HISTORICAL_IMPORT_ITEM, ChronologyMilestone, TimelineItem
from e2e.conftest import document_overflows
from tests.test_tl_c_historical_rows import a_file, a_note, files_item, note_item, render

pytestmark = pytest.mark.e2e

STATIC = Path(settings.BASE_DIR) / "static"
STYLESHEETS = ("css/tokens.css", "css/base.css", "css/app.css", "css/ux.css")

#: Where the screenshots go: `E2E_SCREENSHOT_DIR` as the suite has it, or none.
SCREENS = os.environ.get("TL_C_SCREENS", "")

LONG_TITLE = (
    "Kaubandus-tööstuskoja ja Näidisministeeriumi vahelise kooskõlastusringi "
    "teisevooruettepanekutekokkuvõttemärgukiri"
)
LONG_FILENAME = "KOOSKÕLASTUSTABEL_Näidisseaduse_muutmise_seaduse_eelnõu_2026_lõplik_versioon.docx"


def _rows() -> str:
    note = a_note(
        headline="Näidisamet saatis kooskõlastusringile eelnõu, millega…",
        direct_files=(
            a_file(1, title=LONG_TITLE, original_filename=LONG_FILENAME),
            a_file(2, comment="Märkus OneNote'is: kiri ministeeriumile", predates_page=True),
            a_file(3),
            a_file(4),
        ),
        page_files_count=4,
        links=(("Eelnõude infosüsteem", "https://example.invalid/eelnou/näidis"),),
    )
    plan = a_note(
        kind="NEXT_ACTIONS",
        date_display="08.2026",
        precision="MONTH",
        headline="Järgmised tegevused: küsida näidisametilt uus versioon…",
        paragraphs=("Järgmised tegevused:\n- küsida uus versioon\n- " + LONG_TITLE,),
    )
    undated = a_note(date_display="", precision=None, headline="koda ei esitanud märkusi")
    files = files_item(
        (a_file(5, title="EELNÕU.pdf", original_filename="EELNÕU.pdf"), a_file(6), a_file(7)),
        what="EELNÕU.pdf ja 2 muud faili",
    )
    when = datetime(2026, 7, 20, 9, 0, tzinfo=UTC)
    ordinary = TimelineItem(
        occurred_at=when,
        created_at=when,
        sort_key="onenote:vanem",
        item_type=HISTORICAL_IMPORT_ITEM,
        milestone=ChronologyMilestone(
            what="OneNote'i lisati 2 dokumenti", display_date="20.7.2026", sub="2 faili"
        ),
        imported_documents=2,
    )
    html, _root = render(
        note_item(note, key="onenote-note:a"),
        note_item(plan, key="onenote-note:b"),
        files,
        note_item(undated, key="onenote-note:c"),
        ordinary,  # type: ignore[arg-type]
    )
    return html


#: The page gutter `.teema`'s negative side margins (-32 px) are drawn into.
GUTTER = "body { margin: 0; padding: 40px 32px; }"


def _open_page(page, *, width: int, with_script: bool = True) -> None:
    page.set_default_timeout(5000)
    page.set_viewport_size({"width": width, "height": 900})
    # The stylesheets are inlined rather than added with `add_style_tag`, which
    # waits on a script of its own and so never returns with scripting off.
    styles = "".join(
        f"<style>{(STATIC / sheet).read_text(encoding='utf-8')}</style>" for sheet in STYLESHEETS
    )
    page.set_content(
        '<!doctype html><html lang="et" data-theme="dark"><head><meta charset="utf-8">'
        '<meta name="viewport" content="width=device-width, initial-scale=1">'
        f"{styles}<style>{GUTTER}</style></head>"
        '<body><main class="page"><div class="teema" id="teema-vaade-wrap">'
        f'<div id="ajalugu-loend" class="uxtl">{_rows()}</div></div></main></body></html>'
    )
    if with_script:
        page.add_script_tag(path=str(STATIC / "js" / "app.js"))


def _shot(page, name: str) -> None:
    if SCREENS:
        Path(SCREENS).mkdir(parents=True, exist_ok=True)
        page.screenshot(path=str(Path(SCREENS) / f"{name}.png"), full_page=True)


def _row(page, key: str):
    return page.locator(f'article.uxtl__item[data-kaik-rida="{key}"]')


@pytest.mark.parametrize("width", [1440, 375])
def test_a_historical_row_opens_and_closes_by_keyboard(page, width):
    _open_page(page, width=width)
    note = _row(page, "onenote-note:a")
    details = note.locator("details.uxtl__hist")
    summary = details.locator("summary")
    expect(details).not_to_have_attribute("open", re.compile(".*"))
    # Closed, only the line shows — and the line is visible, although the
    # accordion's closed-state rule hides a closed row's other children.
    expect(summary).to_be_visible()
    expect(summary).to_have_text(
        re.compile(r"^\s*27\.07\.2026\s+4 dokumenti\s+Näidisamet saatis kooskõlastusringile")
    )
    expect(note.get_by_text("Ava OneNote'i leht Juristides")).to_be_hidden()
    _shot(page, f"collapsed-{width}")

    summary.focus()
    expect(summary).to_be_focused()
    page.keyboard.press("Enter")
    expect(details).to_have_attribute("open", "")
    expect(note.get_by_role("link", name="Ava OneNote'i leht Juristides")).to_be_visible()
    expect(note.get_by_role("link", name="Sama OneNote'i lehe failid (4)")).to_be_visible()
    expect(note.locator("li.uxtl__histfile")).to_have_count(4)
    expect(note.get_by_text("lisatud enne praeguse lehe loomist")).to_be_visible()
    # Focus stays on the line, and the focus ring is drawn.
    expect(summary).to_be_focused()
    outline = summary.evaluate("el => getComputedStyle(el).outlineStyle")
    assert outline not in ("", "none")
    assert not document_overflows(page)
    _shot(page, f"expanded-{width}")

    page.keyboard.press("Space")
    expect(details).not_to_have_attribute("open", re.compile(".*"))
    expect(note.get_by_text("Ava OneNote'i leht Juristides")).to_be_hidden()


def test_the_accessible_name_is_the_line_and_the_tag_is_read_as_words(page):
    _open_page(page, width=1440)
    summary = _row(page, "onenote-note:a").locator("summary")
    tag = summary.locator(".uxtl__histtag")
    # Written in its own case, and drawn as a box — not by colour alone.
    assert tag.evaluate("el => getComputedStyle(el).textTransform") == "none"
    assert tag.evaluate("el => getComputedStyle(el).borderTopStyle") == "solid"
    snapshot = summary.aria_snapshot()
    assert "27.07.2026" in snapshot and "4 dokumenti" in snapshot
    assert "Näidisamet saatis kooskõlastusringile eelnõu" in snapshot
    assert summary.locator("time").get_attribute("datetime") == "2026-07-27"
    month = _row(page, "onenote-note:b").locator("summary time")
    assert month.get_attribute("datetime") == "2026-08"
    expect(_row(page, "onenote-note:c").locator("summary time")).to_have_count(0)


def test_next_actions_reads_as_history_with_no_control(page):
    _open_page(page, width=1440)
    plan = _row(page, "onenote-note:b")
    plan.locator("summary").click()
    expect(plan.locator("details")).to_have_attribute("open", "")
    expect(plan.locator(".uxtl__histtext .uxtl__msnotelabel")).to_have_text(
        "Ajalooline: Järgmised tegevused"
    )
    expect(plan.locator("input, select, textarea, form")).to_have_count(0)
    expect(plan.get_by_role("checkbox")).to_have_count(0)
    _shot(page, "next-actions-1440")


def test_the_document_row_lists_every_file_and_stands_beside_the_button_rows(page):
    _open_page(page, width=1440)
    files = _row(page, "onenote:grupp-1")
    expect(files.locator("summary")).to_have_text(
        re.compile(r"^\s*29\.07\.2026\s+3 dokumenti\s+EELNÕU\.pdf ja 2 muud faili")
    )
    files.locator("summary").click()
    expect(files.locator("li.uxtl__histfile")).to_have_count(3)
    expect(files.get_by_role("link", name="Ava või laadi alla — EELNÕU.pdf")).to_be_visible()

    # A row of the older kind keeps its button; opening it (app.js, one open at
    # a time) leaves the disclosure as the reader left it.
    ordinary = _row(page, "onenote:vanem")
    ordinary.locator(".uxtl__toggle").click()
    expect(ordinary).to_have_class(re.compile(r"\buxtl__item--open\b"))
    expect(files.locator("details")).to_have_attribute("open", "")


@pytest.mark.parametrize("width", [375])
def test_nothing_pushes_a_phone_page_sideways_with_every_row_open(page, width):
    _open_page(page, width=width)
    for summary in page.locator("details.uxtl__hist > summary").all():
        summary.click()
    expect(page.locator("details.uxtl__hist[open]")).to_have_count(4)
    assert not document_overflows(page)
    # The long title and the long filename wrap inside the row.
    long_link = page.locator("a.uxtl__file", has_text="teisevooruettepanekute")
    box = long_link.bounding_box()
    assert box is not None and box["x"] + box["width"] <= width
    _shot(page, f"all-open-{width}")


def test_the_row_works_with_no_script_at_all(browser):
    context = browser.new_context(java_script_enabled=False, viewport={"width": 375, "height": 900})
    try:
        page = context.new_page()
        _open_page(page, width=375, with_script=False)
        note = _row(page, "onenote-note:a")
        expect(note.get_by_text("Ava OneNote'i leht Juristides")).to_be_hidden()
        note.locator("summary").click()
        expect(note.locator("details")).to_have_attribute("open", "")
        expect(note.get_by_role("link", name="Ava OneNote'i leht Juristides")).to_be_visible()
        assert not document_overflows(page)
        _shot(page, "no-script-375")
    finally:
        context.close()
