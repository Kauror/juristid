"""«Eelvaade» in a browser (UX-006): a Teema beside the list, and the list untouched.

* The row's icon opens the drawer with that Teema; the address does not change,
  and nothing about the list — its filters, its page, its scroll — moves.
* Focus goes into the drawer and Tab stays there; Escape, ✕ and the backdrop
  close it, and focus returns to the icon it was opened from.
* The row last previewed is marked, from the browser session, also after the
  list is loaded again.
* On a phone the drawer is the window's width and the page does not scroll
  sideways.

The server half (content, visibility, the query budget) is
`tests/test_matter_preview.py`.
"""

from __future__ import annotations

import re

import pytest
from playwright.sync_api import expect

from e2e.conftest import SANDRA, document_overflows, sign_in

pytestmark = pytest.mark.e2e

#: Every state, so the seeded world fills more than a screen.
LIST = "/teemad/?olek=koik&kaupa=50"
DRAWER = "[data-mpreview-panel]"
TRIGGER = "[data-mpreview-open]"


def _open_list(page, base_url):
    sign_in(page, base_url, SANDRA)
    page.goto(base_url + LIST)
    expect(page.locator(TRIGGER).first).to_be_visible()


def _a_row_below_the_fold(page):
    triggers = page.locator(TRIGGER)
    count = triggers.count()
    assert count >= 6, "the seeded register should hold more than a handful of rows"
    trigger = triggers.nth(count - 2)
    trigger.scroll_into_view_if_needed()
    assert page.evaluate("window.scrollY") > 0, "the row is below the first screen"
    return trigger


def test_the_drawer_opens_beside_the_list_and_leaves_it_where_it_was(page, base_url):
    _open_list(page, base_url)
    trigger = _a_row_below_the_fold(page)
    title = trigger.locator("xpath=ancestor::tr").locator(".table__titlelink").inner_text().strip()
    url_before = page.url
    scroll_before = page.evaluate("window.scrollY")

    trigger.click()

    drawer = page.locator(DRAWER)
    expect(drawer).to_be_visible()
    expect(drawer.locator("#mpreview-title")).to_have_text(title)
    expect(drawer.locator("#mpreview-title")).to_be_focused()
    expect(drawer.get_by_role("link", name="Ava teema →")).to_be_visible()
    assert page.url == url_before, "opening the preview changes no address"
    expect(trigger).to_have_attribute("aria-expanded", "true")

    page.keyboard.press("Escape")

    expect(drawer).to_be_hidden()
    expect(trigger).to_be_focused()
    assert page.url == url_before
    assert page.evaluate("window.scrollY") == scroll_before, "the list did not move"
    row = trigger.locator("xpath=ancestor::tr")
    expect(row).to_have_class("is-lastviewed")


def test_tab_stays_inside_the_drawer_and_both_controls_close_it(page, base_url):
    _open_list(page, base_url)
    trigger = page.locator(TRIGGER).first
    trigger.click()
    drawer = page.locator(DRAWER)
    expect(drawer.locator("#mpreview-title")).to_be_focused()

    for _ in range(8):
        page.keyboard.press("Tab")
        assert page.evaluate(
            "document.querySelector('[data-mpreview-panel]').contains(document.activeElement)"
        ), "focus left the open drawer"
    page.keyboard.press("Shift+Tab")
    assert page.evaluate(
        "document.querySelector('[data-mpreview-panel]').contains(document.activeElement)"
    )

    drawer.get_by_role("button", name="Sulge eelvaade").click()
    expect(drawer).to_be_hidden()
    expect(trigger).to_be_focused()

    trigger.click()
    expect(drawer.locator("#mpreview-title")).to_be_visible()
    # The backdrop is the window outside the drawer: its left edge.
    page.mouse.click(10, 400)
    expect(drawer).to_be_hidden()
    expect(trigger).to_be_focused()


def test_the_last_viewed_row_is_marked_again_when_the_list_returns(page, base_url):
    _open_list(page, base_url)
    trigger = page.locator(TRIGGER).nth(2)
    matter_id = trigger.locator("xpath=ancestor::tr").get_attribute("data-matter-row")
    trigger.click()
    expect(page.locator("#mpreview-title")).to_be_visible()
    page.keyboard.press("Escape")

    page.reload()

    marked = page.locator("tr.is-lastviewed")
    expect(marked).to_have_count(1)
    expect(marked).to_have_attribute("data-matter-row", matter_id)


def test_on_a_phone_the_drawer_is_the_window_and_nothing_scrolls_sideways(page, base_url):
    page.set_viewport_size({"width": 375, "height": 812})
    _open_list(page, base_url)
    trigger = page.locator(TRIGGER).first
    trigger.scroll_into_view_if_needed()
    trigger.click()

    drawer = page.locator(DRAWER)
    expect(drawer.locator("#mpreview-title")).to_be_visible()
    box = drawer.bounding_box()
    assert box is not None
    assert box["x"] <= 1 and box["width"] >= 370, "the drawer fills a phone's width"
    assert not document_overflows(page)
    page.keyboard.press("Escape")
    expect(drawer).to_be_hidden()


def test_back_to_the_results_by_the_link_brings_the_teema_row_into_view(page, base_url):
    """The ordinary way back, without the drawer: Back restores the scroll by
    itself; «← Tagasi tulemustesse» reloads the list at its top, so the row of
    the Teema it came from is brought into view and marked."""
    _open_list(page, base_url)
    trigger = _a_row_below_the_fold(page)
    row = trigger.locator("xpath=ancestor::tr")
    matter_id = row.get_attribute("data-matter-row")
    row.locator(".table__titlelink").click()
    page.wait_for_url(re.compile(f"/teemad/{matter_id}/$"))

    page.get_by_role("link", name="← Tagasi tulemustesse").click()
    page.wait_for_url(re.compile(re.escape(LIST) + "$"))

    marked = page.locator(f'tr[data-matter-row="{matter_id}"]')
    expect(marked).to_have_class("is-lastviewed")
    expect(marked).to_be_in_viewport()
