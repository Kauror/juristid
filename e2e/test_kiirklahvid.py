"""Kiirklahvid in a real browser: the page, and two promises it makes.

* `/kiirklahvid/` is reached from the footer beside Uuendused.
* The Lühikokkuvõte editor lost its «Ctrl + Enter salvestab · Esc katkestab»
  caption, so `Esc` needs a click equivalent on the screen: `Loobu` closes the
  box and posts nothing (AGENTS.md: every shortcut has a click equivalent).
* Minu asjad's `Enter` opens the selected row only from inside that row and
  only from something with no Enter of its own: on the row's ⋯ it opens the
  menu, not the Matter (2026-10-25 review, S2).
"""

from __future__ import annotations

import re

import pytest
from playwright.sync_api import expect

from app.core.management.commands.seed_e2e_data import OPEN_TITLE
from e2e.conftest import SANDRA, open_matter, sign_in

pytestmark = pytest.mark.e2e


def test_the_footer_reaches_the_shortcut_page(page, base_url):
    sign_in(page, base_url, SANDRA)
    page.locator(".app__footer").get_by_role("link", name="Kiirklahvid").click()
    page.wait_for_url(f"{base_url}/kiirklahvid/")
    expect(page.get_by_role("heading", level=1, name="Kiirklahvid")).to_be_visible()
    expect(page.get_by_role("heading", level=2, name="Minu asjad")).to_be_visible()


def test_loobu_closes_the_summary_editor_without_saving(page, base_url):
    sign_in(page, base_url, SANDRA)
    url = open_matter(page, base_url, OPEN_TITLE)

    posts: list[str] = []
    page.on(
        "request",
        lambda request: (
            posts.append(request.url)
            if request.method == "POST" and "/luhikokkuvote/" in request.url
            else None
        ),
    )

    summary = page.locator("#teema-luhikokkuvote")
    editor = summary.locator("details.summary__edit")
    summary.locator(".summary__trigger").click()
    expect(editor).to_have_attribute("open", "")
    box = summary.locator("textarea")
    box.fill("SEDA-EI-SALVESTATA-4471")

    cancel = summary.get_by_role("button", name="Loobu", exact=True)
    expect(cancel).to_be_visible()
    cancel.click()

    expect(editor).not_to_have_attribute("open", "")
    expect(summary.locator(".summary__trigger")).to_be_focused()
    assert posts == []
    # Dropped, not parked: the box is back to what the server rendered, so
    # leaving the page has nothing to ask about.
    assert box.input_value() != "SEDA-EI-SALVESTATA-4471"

    asked: list[str] = []

    def answer(dialog) -> None:
        asked.append(dialog.type)
        dialog.accept()

    page.on("dialog", answer)
    page.goto(url)
    page.wait_for_load_state("networkidle")
    assert asked == [], "leaving after Loobu asked «leave this page?»"
    expect(page.locator("#teema-luhikokkuvote")).not_to_contain_text("SEDA-EI-SALVESTATA-4471")


def test_enter_on_a_rows_menu_opens_the_menu_not_the_matter(page, base_url):
    sign_in(page, base_url, SANDRA)  # lands on /minu-asjad/
    first = page.locator("[data-workrow]").first
    expect(first).to_be_visible()

    first.locator(".workrow2__link").focus()
    expect(first).to_have_class(re.compile(r"\bis-selected\b"))

    page.keyboard.press("Tab")
    trigger = first.locator("summary.rowmenu__trigger")
    expect(trigger).to_be_focused()
    page.keyboard.press("Enter")

    expect(first.locator("details.rowmenu")).to_have_attribute("open", "")
    assert page.url == f"{base_url}/minu-asjad/"


def test_enter_elsewhere_on_the_page_does_not_open_the_selected_row(page, base_url):
    """A selection left by J must not turn the bell's or the theme's Enter into a jump."""
    sign_in(page, base_url, SANDRA)
    first = page.locator("[data-workrow]").first
    expect(first).to_be_visible()

    page.keyboard.press("j")
    expect(first).to_have_class(re.compile(r"\bis-selected\b"))

    # Off the row, on a header control that is a button with its own Enter.
    toggle = page.locator("[data-theme-toggle]").first
    toggle.focus()
    page.keyboard.press("Enter")
    page.wait_for_load_state("networkidle")
    assert page.url == f"{base_url}/minu-asjad/"
    expect(toggle).to_be_focused()
