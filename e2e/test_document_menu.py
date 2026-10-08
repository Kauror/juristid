"""Dokumendid's `⋯` is a compact menu over the table, not a panel in the row.

Owner's round, 2026-10-08. Pressing `⋯` on a document used to unfold the rename
form inside the table cell: the row grew by three lines under the cursor and
every row below it moved. On an opinion it unfolded the whole send record. The
menu now floats (`position: fixed`, placed by the shared popover contract in
`static/js/ux.js`), and these tests measure the thing the reader saw rather
than the markup that caused it:

* opening `⋯` changes no row's height and no column's width;
* the panel is on screen, at a desk and on a phone, and widens no page;
* `Muuda nime` renames — the title changes, the filename under it does not;
* Escape closes it and gives focus back to `⋯`, a click outside closes it, and
  opening another row's menu closes the first;
* the whole thing works from the keyboard;
* an opinion's longer menu floats the same way, and scrolls inside itself
  rather than running past a short window.
"""

from __future__ import annotations

import re

import pytest
from playwright.sync_api import expect

from app.core.management.commands.seed_e2e_data import OPEN_TITLE
from e2e.conftest import (
    DESKTOP_VIEWPORT,
    MARTIN,
    SANDRA,
    document_overflows,
    open_matter,
    sign_in,
)
from e2e.titles import unique_title

pytestmark = pytest.mark.e2e

FILES = ("menuu-kaaskiri.pdf", "menuu-eelnou.pdf", "menuu-lisa.pdf")
ROUNDING = 1

#: Every row's height and offset from the table's top, every column's width, and
#: the table's own size — what «the table did not move» means, read in one go.
LAYOUT = """() => {
  const table = document.querySelector('table.doctable');
  const box = table.getBoundingClientRect();
  return {
    rows: [...table.querySelectorAll('tbody tr')].map(row => {
      const r = row.getBoundingClientRect();
      return [r.top - box.top, r.height];
    }),
    columns: [...table.querySelectorAll('thead th')].map(th => th.getBoundingClientRect().width),
    table: [box.width, box.height],
  };
}"""

#: The open panel's box against the window, and whether the page scrolls sideways.
PANEL = """(panel) => {
  const r = panel.getBoundingClientRect();
  return {left: r.left, right: r.right, top: r.top, bottom: r.bottom, height: r.height,
          width: window.innerWidth, viewport: window.innerHeight,
          position: getComputedStyle(panel).position,
          scrolls: panel.scrollHeight > panel.clientHeight + 1,
          overflow: document.scrollingElement.scrollWidth - window.innerWidth};
}"""


def _same_layout(before: dict, after: dict, what: str) -> None:
    assert len(before["rows"]) == len(after["rows"]), what
    for (top, height), (top_after, height_after) in zip(before["rows"], after["rows"], strict=True):
        assert abs(height - height_after) <= ROUNDING, (what, before, after)
        assert abs(top - top_after) <= ROUNDING, (what, before, after)
    for width, width_after in zip(before["columns"], after["columns"], strict=True):
        assert abs(width - width_after) <= ROUNDING, (what, before, after)
    assert abs(before["table"][0] - after["table"][0]) <= ROUNDING, (what, before, after)
    assert abs(before["table"][1] - after["table"][1]) <= ROUNDING, (what, before, after)


def _on_screen(reading: dict, what: str) -> None:
    assert reading["position"] == "fixed", (what, reading)
    assert reading["height"] > 0, (what, reading)
    assert reading["left"] >= -ROUNDING, (what, reading)
    assert reading["right"] <= reading["width"] + ROUNDING, (what, reading)
    assert reading["top"] >= -ROUNDING, (what, reading)
    assert reading["bottom"] <= reading["viewport"] + ROUNDING, (what, reading)
    assert reading["overflow"] <= ROUNDING, (what, reading)


def _row(page, filename: str):
    """The row for one file. By the filename, which a rename leaves in the row."""
    return page.locator("table.doctable tbody tr", has_text=filename)


def _menu(row):
    return row.locator("details.opinionmenu")


def _is_open(menu) -> bool:
    return menu.evaluate("details => details.open")


def _matter_with_files(page, base_url: str, tmp_path) -> str:
    """A Teema of this file's own with three files, on its Dokumendid tab.

    Its own rather than the seeded one, because one test here renames a file
    and the seeded Teema is what the visual suite photographs.
    """
    paths = []
    for name in FILES:
        path = tmp_path / name
        # Different bytes for each, so no file is refused as a duplicate.
        path.write_bytes(b"%PDF-1.4\n% synthetic e2e " + name.encode() + b"\n%%EOF\n")
        paths.append(str(path))
    page.goto(f"{base_url}/teemad/uus/")
    page.wait_for_load_state("networkidle")
    page.fill("#id_title", unique_title("Dokumendimenüü"))
    page.locator("#id_files").set_input_files(paths)
    expect(page.locator(".dropzone__file")).to_have_count(len(FILES))
    page.get_by_role("button", name="Loo teema").click()
    page.wait_for_url(re.compile(r"/teemad/[0-9a-f-]{36}/$"))
    url = page.url
    page.goto(f"{url}dokumendid/")
    page.wait_for_load_state("networkidle")
    for name in FILES:
        expect(_row(page, name)).to_have_count(1)
    return url


def test_opening_the_menu_moves_nothing_and_stays_on_screen(page, base_url, tmp_path):
    sign_in(page, base_url, SANDRA)
    url = _matter_with_files(page, base_url, tmp_path)

    for width in (1440, 375):
        page.set_viewport_size({"width": width, "height": 900})
        page.goto(f"{url}dokumendid/")
        page.wait_for_load_state("networkidle")
        for name in FILES:
            row = _row(page, name)
            menu = _menu(row)
            trigger = menu.locator("summary.opinionmenu__trigger")
            trigger.scroll_into_view_if_needed()
            before = page.evaluate(LAYOUT)

            trigger.click()
            panel = menu.locator(".opinionmenu__body")
            expect(panel).to_be_visible()
            expect(panel.locator("summary", has_text="Muuda nime")).to_be_visible()
            expect(panel.get_by_role("link", name="Dokumendi leht")).to_be_visible()
            # A menu first: the title box waits for `Muuda nime`.
            expect(panel.locator("input[name=title]")).to_be_hidden()

            _same_layout(before, page.evaluate(LAYOUT), f"{name} at {width}")
            _on_screen(panel.evaluate(PANEL), f"{name} at {width}")
            assert not document_overflows(page), f"sideways scroll at {width} with {name} open"

            # The editor open is still a panel, not a taller row.
            panel.locator("summary", has_text="Muuda nime").click()
            expect(panel.locator("input[name=title]")).to_be_visible()
            _same_layout(before, page.evaluate(LAYOUT), f"{name} editing at {width}")
            _on_screen(panel.evaluate(PANEL), f"{name} editing at {width}")
            assert not document_overflows(page), f"sideways scroll at {width} editing {name}"

            page.keyboard.press("Escape")
            expect(panel).to_be_hidden()
    page.set_viewport_size(DESKTOP_VIEWPORT)


def test_muuda_nime_renames_and_the_filename_stays(page, base_url, tmp_path):
    sign_in(page, base_url, SANDRA)
    _matter_with_files(page, base_url, tmp_path)

    row = _row(page, "menuu-eelnou.pdf")
    menu = _menu(row)
    # The tooltips the row has always had: the arrow downloads, `⋯` is `Muuda`.
    expect(row.locator(".doctable__actions a[title='Tõmba alla']")).to_have_count(1)
    expect(menu.locator("summary.opinionmenu__trigger")).to_have_attribute("title", "Muuda")

    menu.locator("summary.opinionmenu__trigger").click()
    menu.locator("summary", has_text="Muuda nime").click()
    box = menu.locator("input[name=title]")
    # Choosing the command puts the cursor in the box.
    expect(box).to_be_focused()
    expect(box).to_have_value("menuu-eelnou.pdf")
    # A click inside the panel — here, on the box itself — keeps the menu open.
    box.click()
    assert _is_open(menu)
    box.fill("Eelnõu terviktekst menüüst")
    menu.get_by_role("button", name="Salvesta").click()
    page.wait_for_load_state("networkidle")

    renamed = page.locator("table.doctable tbody tr", has_text="Eelnõu terviktekst menüüst")
    expect(renamed).to_have_count(1)
    expect(renamed.locator(".doctable__filename")).to_have_text("menuu-eelnou.pdf")
    # The other files are untouched, and the menu came back closed.
    for name in ("menuu-kaaskiri.pdf", "menuu-lisa.pdf"):
        expect(_row(page, name)).to_have_count(1)
    expect(page.locator("table.doctable details.opinionmenu[open]")).to_have_count(0)


def test_escape_outside_click_and_one_menu_at_a_time(page, base_url, tmp_path):
    sign_in(page, base_url, SANDRA)
    _matter_with_files(page, base_url, tmp_path)
    rows = page.locator("table.doctable tbody tr")
    first, last = _menu(rows.first), _menu(rows.last)

    # Opening a second row's menu closes the first. The last row's panel opens
    # below the table, so the first row's `⋯` is not under it.
    last.locator("summary.opinionmenu__trigger").click()
    assert _is_open(last)
    first.locator("summary.opinionmenu__trigger").click()
    assert _is_open(first)
    assert not _is_open(last)
    expect(page.locator("table.doctable details.opinionmenu[open]")).to_have_count(1)

    # Escape closes it, and focus goes back to the `⋯` that opened it.
    page.keyboard.press("Escape")
    assert not _is_open(first)
    expect(first.locator("summary.opinionmenu__trigger")).to_be_focused()

    # A click outside closes it.
    first.locator("summary.opinionmenu__trigger").click()
    assert _is_open(first)
    page.locator("table.doctable thead th").first.click()
    assert not _is_open(first)

    # Escape from inside the half-typed title closes the whole menu, and the
    # next `⋯` opens a menu again — with the saved title, not the typing.
    first.locator("summary.opinionmenu__trigger").click()
    first.locator("summary", has_text="Muuda nime").click()
    box = first.locator("input[name=title]")
    original = box.input_value()
    box.fill("Pooleli jäänud nimi")
    page.keyboard.press("Escape")
    assert not _is_open(first)
    expect(first.locator("summary.opinionmenu__trigger")).to_be_focused()
    first.locator("summary.opinionmenu__trigger").click()
    expect(box).to_be_hidden()
    first.locator("summary", has_text="Muuda nime").click()
    expect(box).to_have_value(original)
    page.keyboard.press("Escape")


def test_the_menu_works_from_the_keyboard(page, base_url, tmp_path):
    sign_in(page, base_url, SANDRA)
    _matter_with_files(page, base_url, tmp_path)
    menu = _menu(_row(page, "menuu-lisa.pdf"))
    trigger = menu.locator("summary.opinionmenu__trigger")

    trigger.focus()
    page.keyboard.press("Enter")
    assert _is_open(menu)
    # The first command is the next stop for Tab, and Space chooses it.
    page.keyboard.press("Tab")
    expect(menu.locator("summary", has_text="Muuda nime")).to_be_focused()
    page.keyboard.press("Space")
    expect(menu.locator("input[name=title]")).to_be_focused()
    page.keyboard.press("Escape")
    assert not _is_open(menu)
    expect(trigger).to_be_focused()

    # And it saves from the keyboard: Enter on `⋯`, Tab, Enter, type, Enter.
    page.keyboard.press("Enter")
    page.keyboard.press("Tab")
    page.keyboard.press("Enter")
    expect(menu.locator("input[name=title]")).to_be_focused()
    page.keyboard.type("Lisa klaviatuurilt")
    page.keyboard.press("Enter")
    page.wait_for_load_state("networkidle")
    renamed = page.locator("table.doctable tbody tr", has_text="Lisa klaviatuurilt")
    expect(renamed).to_have_count(1)
    expect(renamed.locator(".doctable__filename")).to_have_text("menuu-lisa.pdf")


def test_an_opinions_menu_floats_and_scrolls_inside_itself(page, base_url):
    """Read-only on the seeded Teema: the menu is opened and closed, nothing saved."""
    sign_in(page, base_url, MARTIN)
    url = open_matter(page, base_url, OPEN_TITLE)
    page.goto(url.rstrip("/") + "/dokumendid/")
    page.wait_for_load_state("networkidle")
    row = page.locator("table.doctable tbody tr").filter(has=page.locator(".badge--opinion"))
    expect(row).to_have_count(1)
    menu = _menu(row)
    trigger = menu.locator("summary.opinionmenu__trigger")
    panel = menu.locator(".opinionmenu__body")

    trigger.scroll_into_view_if_needed()
    before = page.evaluate(LAYOUT)
    trigger.click()
    expect(panel.get_by_text("Saatmise andmed")).to_be_visible()
    # `Muuda nime` first, then what was there before.
    expect(panel.locator(".opinionmenu__item").first).to_have_text("Muuda nime")
    _same_layout(before, page.evaluate(LAYOUT), "opinion menu")
    _on_screen(panel.evaluate(PANEL), "opinion menu")

    # `Võta tagasi`'s confirmation is a click inside: the menu stays, and
    # `Loobu` folds the confirmation without sending anything.
    withdraw = panel.locator("details[data-withdraw]")
    withdraw.locator("summary").click()
    expect(withdraw.get_by_role("button", name="Kinnita tagasivõtmine")).to_be_visible()
    assert _is_open(menu)
    _same_layout(before, page.evaluate(LAYOUT), "opinion menu, withdrawal open")
    _on_screen(panel.evaluate(PANEL), "opinion menu, withdrawal open")
    withdraw.get_by_role("button", name="Loobu").click()
    assert _is_open(menu)
    page.keyboard.press("Escape")
    assert not _is_open(menu)

    # A window shorter than the menu: it stays inside it and scrolls itself.
    for width in (1440, 375):
        page.set_viewport_size({"width": width, "height": 320})
        trigger.scroll_into_view_if_needed()
        trigger.click()
        reading = panel.evaluate(PANEL)
        _on_screen(reading, f"opinion menu in a 320 px window at {width}")
        assert reading["scrolls"], ("the panel should scroll inside itself", reading)
        page.keyboard.press("Escape")
        assert not _is_open(menu)
    page.set_viewport_size(DESKTOP_VIEWPORT)
