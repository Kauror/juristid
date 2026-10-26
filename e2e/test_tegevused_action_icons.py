"""`Teema käik` row icons in a real browser (owner's UX corrections, 2026-10-26).

Copy → edit → remove, together, on a `Märge` of a Teema of its own: reached in
that order by Tab, each named for the row, the copy icon copying this history
entry's own address without moving anything, the X opening the existing
confirmation (and `Loobu` closing it onto the X), the pencil opening the
correction and the icons coming back after the HTMX swap — and none of the
three opening or closing the row they sit in.
"""

from __future__ import annotations

import re

import pytest
from playwright.sync_api import expect

from e2e.conftest import (
    SANDRA,
    chronology,
    create_matter,
    open_kaik_row,
    record_marge,
    sign_in,
    unique_title,
    wait_for_htmx,
)

pytestmark = pytest.mark.e2e


def _row(page, text):
    return chronology(page).locator("article.uxtl__item").filter(has_text=text).first


def test_the_icons_copy_edit_and_remove_without_touching_the_row(page, base_url):
    page.context.grant_permissions(["clipboard-read", "clipboard-write"], origin=base_url)
    sign_in(page, base_url, SANDRA)
    page.set_viewport_size({"width": 1440, "height": 1000})
    url = create_matter(page, base_url, unique_title("Ikoonid"))
    note = unique_title("Ikoonide märge")
    assert record_marge(page, note) == 200

    row = _row(page, note)
    open_kaik_row(row)
    toggle = row.locator(".uxtl__toggle")
    expect(toggle).to_have_attribute("aria-expanded", "true")
    icons = row.locator(".uxtl__icon")
    expect(icons).to_have_count(3)
    assert [icons.nth(i).get_attribute("class").split()[-1] for i in range(3)] == [
        "uxtl__icon--copy",
        "uxtl__icon--edit",
        "uxtl__icon--remove",
    ]

    copy, edit, remove = (
        row.locator(".uxtl__icon--copy"),
        row.locator(".uxtl__icon--edit"),
        row.locator("summary.uxtl__icon--remove"),
    )
    # Named for the row, not three identical words.
    for control, word in ((copy, "Kopeeri link"), (edit, "Muuda"), (remove, "Kustuta")):
        name = control.evaluate(
            "n => (n.getAttribute('aria-labelledby') || '').split(' ')"
            ".map(id => document.getElementById(id)?.textContent.trim()).join(' ')"
            " || n.textContent.trim()"
        )
        assert name.startswith(word), name
        assert len(name) > len(word), name
        assert control.get_attribute("title") == word

    # Tab walks them in order.
    copy.focus()
    page.keyboard.press("Tab")
    expect(edit).to_be_focused()
    page.keyboard.press("Tab")
    expect(remove).to_be_focused()

    # Copy: this history entry's address; nothing on the line moves.
    before = row.locator("div.uxtl__editactions").first.bounding_box()
    copy.click()
    expect(copy).to_have_attribute("data-copied", "Link kopeeritud")
    after = row.locator("div.uxtl__editactions").first.bounding_box()
    assert before == after
    copied = page.evaluate("navigator.clipboard.readText()")
    key = row.get_attribute("data-kaik-rida")
    assert copied.startswith(url) and copied.endswith(f"?kirje={key}#kaik-{key}-rida")
    expect(toggle).to_have_attribute("aria-expanded", "true")

    # X: the existing confirmation; `Loobu` closes it onto the X.
    remove.click()
    confirm = row.locator(".uxtl__removeform")
    expect(confirm).to_be_visible()
    expect(confirm.get_by_role("button", name="Eemalda")).to_be_visible()
    confirm.get_by_role("button", name="Loobu").click()
    expect(confirm).to_be_hidden()
    expect(remove).to_be_focused()
    expect(toggle).to_have_attribute("aria-expanded", "true")
    expect(_row(page, note)).to_be_visible()

    # Pencil: the entry's own correction, and the icons back after the swap.
    edit.click()
    wait_for_htmx(page)
    form = row.locator(".uxtl__editform")
    expect(form).to_be_visible()
    form.get_by_role("button", name="Tühista").click()
    wait_for_htmx(page)
    expect(row.locator(".uxtl__editform")).to_have_count(0)
    expect(row.locator(".uxtl__icon")).to_have_count(3)
    expect(toggle).to_have_attribute("aria-expanded", "true")


def test_no_icon_leaves_the_window_on_a_phone(page, base_url):
    sign_in(page, base_url, SANDRA)
    create_matter(page, base_url, unique_title("Ikoonid telefonis"))
    note = unique_title("Väga pikk märge " + "sõna " * 30)
    assert record_marge(page, note) == 200
    page.set_viewport_size({"width": 375, "height": 812})
    page.reload()
    page.wait_for_load_state("networkidle")

    row = _row(page, note[:30])
    open_kaik_row(row)
    for icon in row.locator(".uxtl__icon").all():
        box = icon.bounding_box()
        assert box is not None and box["x"] >= 0 and box["x"] + box["width"] <= 375
        assert box["width"] >= 24 and box["height"] >= 24
    overflow = page.evaluate(
        "document.documentElement.scrollWidth - document.documentElement.clientWidth"
    )
    assert overflow <= 0
    assert re.search(r"kaik-.*-rida", row.inner_html())
