"""«Eelvaade» in a browser (UX-006): a Teema beside the list, and the list untouched.

* The row's icon opens the drawer with that Teema; the address does not change,
  and nothing about the list — its filters, its page, its scroll — moves.
* Focus goes into the drawer and Tab stays there; Escape, ✕ and the backdrop
  close it, and focus returns to the icon it was opened from.
* The row last previewed is marked, from the browser session, also after the
  list is loaded again.
* On a phone the drawer is the window's width and the page does not scroll
  sideways.
* Opening and closing are immediate (the shell is shown and hidden in the click
  and key handlers themselves); the Teema arrives in it well within a second on
  the seeded world.
* Previews opened one after another show one drawer with the *last* Teema asked
  for — an earlier, slower answer arriving late never replaces it.

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
    trigger = triggers.nth(count - 1)
    page.evaluate("window.scrollTo(0, document.documentElement.scrollHeight)")
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


def _title_of(trigger) -> str:
    return trigger.locator("xpath=ancestor::tr").locator(".table__titlelink").inner_text().strip()


def _row_ids(page) -> list[str]:
    return page.locator("tr[data-matter-row]").evaluate_all(
        "rows => rows.map(row => row.getAttribute('data-matter-row'))"
    )


def test_opening_and_closing_are_immediate_and_the_teema_arrives_quickly(page, base_url):
    """The shell is shown by the click handler itself, before any answer, and
    hidden by the Escape handler itself: no wait is needed to see either. The
    fragment then arrives within a generous bound (measured, printed)."""
    _open_list(page, base_url)
    drawer = page.locator(DRAWER)
    # In-page clock: from the click event to the Teema's title in the drawer.
    page.evaluate(
        """() => {
            window.__mpreviewTimes = [];
            document.addEventListener("click", (event) => {
                if (event.target.closest("[data-mpreview-open]")) {
                    window.__mpreviewClick = performance.now();
                }
            }, true);
            new MutationObserver(() => {
                const shown = document.querySelector("[data-mpreview-content] #mpreview-title");
                if (shown && window.__mpreviewClick) {
                    window.__mpreviewTimes.push(performance.now() - window.__mpreviewClick);
                    window.__mpreviewClick = 0;
                }
            }).observe(document.querySelector("[data-mpreview]"), {childList: true, subtree: true});
        }"""
    )
    for index in (0, 2, 4, 1, 3):
        trigger = page.locator(TRIGGER).nth(index)
        title = _title_of(trigger)
        trigger.click()
        assert drawer.is_visible(), "the drawer is shown in the click handler, not after the fetch"
        expect(drawer.locator("#mpreview-title")).to_have_text(title, timeout=3000)

        page.keyboard.press("Escape")
        assert drawer.is_hidden(), "the drawer is hidden in the key handler, not later"
        expect(trigger).to_be_focused()
    timings = page.evaluate("window.__mpreviewTimes")
    print(f"preview click to content, ms: {[round(t) for t in timings]}")
    assert len(timings) == 5
    assert max(timings) < 3000, "a preview that takes seconds is not a quick look"


def test_filters_order_page_and_scroll_survive_the_preview(page, base_url):
    """A narrowed, re-ordered second page, scrolled: the drawer moves none of it."""
    sign_in(page, base_url, SANDRA)
    address = "/teemad/?olek=koik&jarjestus=title&kaupa=12&leht=2"
    page.goto(base_url + address)
    expect(page.locator(TRIGGER).first).to_be_visible()
    rows_before = _row_ids(page)
    assert rows_before, "the second page holds rows"
    url_before = page.url
    trigger = page.locator(TRIGGER).nth(len(rows_before) - 1)
    trigger.scroll_into_view_if_needed()
    scroll_before = page.evaluate("window.scrollY")

    for _ in range(2):
        trigger.click()
        expect(page.locator("#mpreview-title")).to_have_text(_title_of(trigger))
        page.get_by_role("button", name="Sulge eelvaade").click()
        expect(page.locator(DRAWER)).to_be_hidden()

    assert page.url == url_before
    assert "jarjestus=title" in page.url and "leht=2" in page.url
    assert _row_ids(page) == rows_before, "the same rows, in the same order"
    assert page.evaluate("window.scrollY") == scroll_before
    expect(trigger).to_be_focused()


def test_previews_in_quick_succession_show_only_the_last_one_asked_for(page, base_url):
    """A, B and C previewed one after another — the drawer is modal, so each is
    closed before the next row is reached — with A's and C's answers held back:
    B shows, C shows its loading line, A's answer arriving late changes nothing,
    then C lands. One drawer throughout, one row marked as expanded."""
    _open_list(page, base_url)
    triggers = page.locator(TRIGGER)
    first, second, third = triggers.nth(0), triggers.nth(1), triggers.nth(2)
    titles = [_title_of(trigger) for trigger in (first, second, third)]
    urls = [trigger.get_attribute("data-mpreview-open") for trigger in (first, second, third)]
    held: dict[str, object] = {}

    def hold(route) -> None:
        path = re.sub(r"^https?://[^/]+", "", route.request.url)
        if path in (urls[0], urls[2]):
            held[path] = route
        else:
            route.continue_()

    page.route("**/teemad/*/eelvaade/", hold)
    drawer = page.locator(DRAWER)
    title = drawer.locator("#mpreview-title")

    with page.expect_request(lambda request: request.url.endswith(urls[0])):
        first.click()
    expect(drawer).to_contain_text("Laen eelvaadet")
    page.keyboard.press("Escape")
    expect(first).to_be_focused()

    second.click()
    expect(title).to_have_text(titles[1])
    page.keyboard.press("Escape")

    with page.expect_request(lambda request: request.url.endswith(urls[2])):
        third.click()
    expect(drawer).to_contain_text("Laen eelvaadet")

    with page.expect_response(lambda response: response.url.endswith(urls[0])) as answer:
        held.pop(urls[0]).continue_()
    answer.value.finished()
    # Two frames for the page's own promise chain to run on the late answer.
    page.evaluate("() => new Promise(r => requestAnimationFrame(() => requestAnimationFrame(r)))")
    expect(drawer).to_contain_text("Laen eelvaadet")
    expect(drawer).not_to_contain_text(titles[0])

    held.pop(urls[2]).continue_()
    expect(title).to_have_text(titles[2])
    expect(page.locator(DRAWER)).to_have_count(1)
    expect(page.locator("[data-mpreview-open][aria-expanded='true']")).to_have_count(1)
    expect(third).to_have_attribute("aria-expanded", "true")
    page.keyboard.press("Escape")
    expect(third).to_be_focused()
    page.unroute("**/teemad/*/eelvaade/")
