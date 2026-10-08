"""The shared upload queue in a real browser (owner's rounds, 2026-10-07/08).

- a file dropped on a dropzone joins the same queue a picked file does, the
  browser's own navigation prevented;
- several files, each listed as one line — its display title as text (the
  filename by default), a ✎, its size and a `×` that takes it back off;
- the ✎ makes that one title a box: Enter or leaving the box keeps what was
  typed, Escape and an empty box put the previous title back, and Enter never
  submits the form around it — the same on Uus teema's staged rows, which the
  server renders;
- a file let go of anywhere else on the page is refused rather than opened.

`DataTransfer` and `DragEvent` are built in the page, which is what a real drop
hands the document: the handlers cannot tell the two apart.
"""

from __future__ import annotations

import re

from playwright.sync_api import expect

from e2e.conftest import (
    MARTIN,
    add_panel_is_open,
    create_matter,
    give_first_step,
    open_add_panel,
    sign_in,
    start_first_step,
    unique_title,
)

DROP = """([selector, names]) => {
  const target = selector ? document.querySelector(selector) : document.body;
  const transfer = new DataTransfer();
  names.forEach((name) =>
    transfer.items.add(new File(["%PDF-1.4\\n" + name], name, { type: "application/pdf" }))
  );
  const init = { bubbles: true, cancelable: true, dataTransfer: transfer };
  const over = new DragEvent("dragover", init);
  target.dispatchEvent(over);
  const drop = new DragEvent("drop", init);
  target.dispatchEvent(drop);
  return [over.defaultPrevented, drop.defaultPrevented];
}"""

#: What the form will post beside its files, in the queue's order.
POSTED_TITLES = """(selector) => Array.from(
  document.querySelectorAll(selector + " [data-title-edit-value]"), (box) => box.value
)"""

#: The part of the focused box that is selected.
SELECTED = """() => {
  const box = document.activeElement;
  return box.value.slice(box.selectionStart, box.selectionEnd);
}"""

PDF = b"%PDF-1.4\n1 0 obj << /Type /Catalog >> endobj\ntrailer << /Root 1 0 R >>\n%%EOF\n"

#: Any panel that takes files shares the queue; `+ Lisa · Töövõit` since
#: `Tavaline`, which this used, left `+ Lisa` on 2026-10-07.
PANEL = "marge-toovoit"
ZONE = f"#{PANEL} [data-filedrop]"
QUEUE = f"#{PANEL} [data-upload-queue]"


def _rows(page):
    return page.locator(f"{QUEUE} .uploadqueue__row")


def _pencil(row, filename: str):
    return row.get_by_role("button", name=f"Muuda pealkirja: {filename}")


def _editor(page, filename: str):
    return page.get_by_role("textbox", name=f"Pealkiri: {filename}")


def test_dropped_files_are_titled_by_their_filenames_and_edited_one_at_a_time(page, base_url):
    sign_in(page, base_url, MARTIN)
    create_matter(page, base_url, unique_title("Lohistamine"))
    open_add_panel(page, PANEL)

    prevented = page.evaluate(DROP, [ZONE, ["esimene.pdf", "teine.pdf"]])

    assert prevented == [True, True]
    rows = _rows(page)
    expect(rows).to_have_count(2)
    # Text and a ✎, not boxes: nothing is editable until somebody asks.
    expect(page.locator(f"{QUEUE} input[type=text]")).to_have_count(0)
    expect(rows.nth(0).locator(".titleedit__text")).to_have_text("esimene.pdf")
    expect(rows.nth(1).locator(".titleedit__text")).to_have_text("teine.pdf")
    expect(_pencil(rows.nth(0), "esimene.pdf")).to_have_attribute("title", "Muuda pealkirja")
    expect(rows.nth(0).locator(".titleedit__original")).to_be_hidden()
    expect(rows.nth(0).locator(".uploadqueue__size")).to_contain_text("B")
    expect(rows.nth(0).get_by_role("button", name="Eemalda fail esimene.pdf")).to_be_visible()

    # A second drop adds to the queue rather than replacing it.
    page.evaluate(DROP, [ZONE, ["kolmas.pdf"]])
    expect(rows).to_have_count(3)

    # The ✎ opens that row's title only, focused and selected; Enter keeps it.
    _pencil(rows.nth(0), "esimene.pdf").click()
    editor = _editor(page, "esimene.pdf")
    expect(editor).to_be_focused()
    expect(editor).to_have_value("esimene.pdf")
    assert page.evaluate(SELECTED) == "esimene.pdf"
    expect(page.locator(f"{QUEUE} input[type=text]")).to_have_count(1)
    editor.fill("Ministeeriumi kiri")
    editor.press("Enter")
    expect(page.locator(f"{QUEUE} input[type=text]")).to_have_count(0)
    expect(rows.nth(0).locator(".titleedit__text")).to_have_text("Ministeeriumi kiri")
    # The filename stays on the row once the title is not the filename.
    expect(rows.nth(0).locator(".titleedit__original")).to_be_visible()
    expect(rows.nth(0).locator(".titleedit__original")).to_have_text("esimene.pdf")
    expect(_pencil(rows.nth(0), "esimene.pdf")).to_be_focused()
    expect(rows.nth(1).locator(".titleedit__text")).to_have_text("teine.pdf")

    # Escape puts the previous title back, and the panel stays open.
    _pencil(rows.nth(1), "teine.pdf").click()
    _editor(page, "teine.pdf").fill("Ei jää alles")
    page.keyboard.press("Escape")
    expect(page.locator(f"{QUEUE} input[type=text]")).to_have_count(0)
    expect(rows.nth(1).locator(".titleedit__text")).to_have_text("teine.pdf")
    expect(rows.nth(1).locator(".titleedit__original")).to_be_hidden()
    assert add_panel_is_open(page, PANEL)

    # Leaving the box keeps what was typed.
    _pencil(rows.nth(2), "kolmas.pdf").click()
    _editor(page, "kolmas.pdf").fill("Seletuskiri")
    _editor(page, "kolmas.pdf").blur()
    expect(page.locator(f"{QUEUE} input[type=text]")).to_have_count(0)
    expect(rows.nth(2).locator(".titleedit__text")).to_have_text("Seletuskiri")

    # An empty box is never a title: the previous one comes back.
    _pencil(rows.nth(2), "kolmas.pdf").click()
    _editor(page, "kolmas.pdf").fill("   ")
    _editor(page, "kolmas.pdf").press("Enter")
    expect(rows.nth(2).locator(".titleedit__text")).to_have_text("Seletuskiri")

    # One title per file, in the files' order — what UploadTitlesMiddleware pairs.
    assert page.evaluate(POSTED_TITLES, QUEUE) == [
        "Ministeeriumi kiri",
        "teine.pdf",
        "Seletuskiri",
    ]

    # A removal redraws the queue and keeps every title given so far.
    rows.nth(1).locator(".uploadqueue__remove").click()
    expect(rows).to_have_count(2)
    expect(rows.nth(0).locator(".titleedit__text")).to_have_text("Ministeeriumi kiri")
    expect(rows.nth(1).locator(".titleedit__text")).to_have_text("Seletuskiri")
    assert page.evaluate(POSTED_TITLES, QUEUE) == ["Ministeeriumi kiri", "Seletuskiri"]
    count = page.evaluate(f"document.querySelector('{ZONE} input[type=file]').files.length")
    assert count == 2


def test_picked_files_are_saved_under_the_titles_given_and_enter_never_submits(
    page, base_url, tmp_path
):
    sign_in(page, base_url, MARTIN)
    url = create_matter(page, base_url, unique_title("Failipealkiri"))
    open_add_panel(page, PANEL)
    page.locator(f"#{PANEL} [name=victory_change]").fill("Ministeerium pikendas üleminekuaega")

    letter = tmp_path / "skann_0412.pdf"
    annex = tmp_path / "lisa.pdf"
    letter.write_bytes(PDF)
    annex.write_bytes(PDF + b"%lisa\n")
    page.locator(f"{ZONE} input[type=file]").set_input_files([str(letter), str(annex)])
    rows = _rows(page)
    expect(rows).to_have_count(2)

    # Enter inside the title confirms it and nothing else: the form around it
    # is not submitted.
    page.evaluate(
        """() => {
            window.__submits = 0;
            document.addEventListener("submit", () => { window.__submits += 1; }, true);
        }"""
    )
    _pencil(rows.nth(0), "skann_0412.pdf").click()
    _editor(page, "skann_0412.pdf").fill("Ministeeriumi vastus")
    _editor(page, "skann_0412.pdf").press("Enter")
    expect(rows.nth(0).locator(".titleedit__text")).to_have_text("Ministeeriumi vastus")
    expect(rows.nth(1).locator(".titleedit__text")).to_have_text("lisa.pdf")
    assert page.evaluate("window.__submits") == 0
    assert add_panel_is_open(page, PANEL)

    # A box still open when `Salvesta` is pressed: leaving it is a confirm, so
    # the press saves what was typed — and still lands on the button.
    _pencil(rows.nth(1), "lisa.pdf").click()
    _editor(page, "lisa.pdf").fill("Eelnõu lisa")
    page.locator(f"#{PANEL} button[type=submit]").click()
    page.wait_for_selector("text=Ministeerium pikendas üleminekuaega", state="attached")

    # Dokumendid: the given title is the link, the original filename stays.
    page.goto(f"{url}dokumendid/")
    titled = page.locator("table.doctable tbody tr", has_text="Ministeeriumi vastus")
    expect(titled).to_have_count(1)
    expect(titled.get_by_role("link", name="Ministeeriumi vastus", exact=True)).to_be_visible()
    expect(titled.locator(".doctable__filename")).to_have_text("skann_0412.pdf")
    annexed = page.locator("table.doctable tbody tr", has_text="Eelnõu lisa")
    expect(annexed).to_have_count(1)
    expect(annexed.locator(".doctable__filename")).to_have_text("lisa.pdf")


def test_a_staged_title_on_uus_teema_survives_another_file_being_taken_off(
    page, base_url, tmp_path
):
    """Uus teema's staged rows are rendered by the server and share the ✎.

    Taking a staged file off re-renders the whole staged list from the
    server's answer; the title already confirmed on another row is put back by
    name (static/js/app.js `applyFragment`), and `Loo teema` files it.
    """
    sign_in(page, base_url, MARTIN)
    page.goto(f"{base_url}/teemad/uus/")
    page.locator("#id_title").fill(unique_title("Lavastatud pealkiri"))

    letter = tmp_path / "kaaskiri.pdf"
    annex = tmp_path / "lisa.pdf"
    letter.write_bytes(PDF)
    annex.write_bytes(PDF + b"%lisa\n")
    page.locator("#id_files").set_input_files([str(letter), str(annex)])
    staged = page.locator("#intake-failid .dropzone__file")
    expect(staged).to_have_count(2)
    expect(page.locator("#intake-failid input[type=text]")).to_have_count(0)
    expect(staged.nth(0).locator(".titleedit__text")).to_have_text("kaaskiri.pdf")

    page.get_by_role("button", name="Muuda pealkirja: kaaskiri.pdf").click()
    _editor(page, "kaaskiri.pdf").fill("Kaaskiri ministeeriumilt")
    _editor(page, "kaaskiri.pdf").press("Enter")
    expect(staged.nth(0).locator(".titleedit__text")).to_have_text("Kaaskiri ministeeriumilt")

    page.get_by_role("button", name="Eemalda fail lisa.pdf").click()
    expect(staged).to_have_count(1)
    expect(staged.locator(".titleedit__text")).to_have_text("Kaaskiri ministeeriumilt")
    expect(staged.locator(".titleedit__original")).to_be_visible()
    expect(staged.locator(".titleedit__original")).to_have_text("kaaskiri.pdf")

    give_first_step(page)
    page.get_by_role("button", name="Salvesta", exact=True).click()
    page.wait_for_url(re.compile(r"/teemad/[0-9a-f-]{36}/$"))
    url = page.url
    start_first_step(page)

    page.goto(f"{url}dokumendid/")
    titled = page.locator("table.doctable tbody tr", has_text="Kaaskiri ministeeriumilt")
    expect(titled).to_have_count(1)
    expect(titled.locator(".doctable__filename")).to_have_text("kaaskiri.pdf")
    expect(page.locator("table.doctable tbody tr", has_text="lisa.pdf")).to_have_count(0)


def test_a_file_dropped_off_a_zone_never_navigates(page, base_url):
    sign_in(page, base_url, MARTIN)
    url = create_matter(page, base_url, unique_title("Möödalohistamine"))

    prevented = page.evaluate(DROP, [None, ["eksitus.pdf"]])

    assert prevented == [True, True]
    assert page.url.rstrip("/") == url.rstrip("/")
