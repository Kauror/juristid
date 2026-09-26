"""Focus stays where the keyboard was after «Näita varasemaid» and after «Kustuta» (ENG-095).

The page's focus rule (`static/js/app.js`, `htmx:afterSettle`) was written for
saves, and its one landing on a Teema is `Lisa teemale` near the top. Two swaps
that are not saves lost focus deep in the chronology: loading older rows sent a
writer a page up and a READER to `body`, and the next Tab skipped every row just
loaded; removing a row far down sent focus to the top as well. Each now lands on
the first row that arrived, or on the row that took the removed one's place, and
the page does not scroll.

The rows are filed with the page's own `+ Märge` form, submitted from inside
the page with its CSRF token — thirty-one saves through the panel would be a
minute of clicking that proves nothing this file is about. The interaction under
test, the keyboard on the swap, is driven for real.
"""

from __future__ import annotations

import pytest
from playwright.sync_api import expect

from e2e.conftest import MARTIN, READER, create_matter, sign_in
from e2e.titles import unique_title

pytestmark = pytest.mark.e2e

VIEWPORT = {"width": 1440, "height": 800}

#: Thirty rows is the first page (`TIMELINE_PAGE_SIZE`); one more makes a second.
FILE_ROWS = """
async (count) => {
  const form = document.querySelector('#marge-tavaline form[hx-post]');
  const url = form.getAttribute('hx-post');
  for (let i = 0; i < count; i++) {
    const data = new FormData(form);
    data.set('title', 'Sünteetiline samm ' + String(i).padStart(2, '0'));
    const response = await fetch(url, {method: 'POST', body: data,
                                       headers: {'HX-Request': 'true'}});
    if (!response.ok) throw new Error('filing row ' + i + ' answered ' + response.status);
  }
}
"""

ACTIVE = """() => {
  const a = document.activeElement;
  const rows = [...document.querySelectorAll('article.uxtl__item')];
  const box = a ? a.getBoundingClientRect() : null;
  return {
    tag: a ? a.tagName.toLowerCase() : null,
    isRow: !!(a && a.matches && a.matches('article.uxtl__item')),
    inRow: !!(a && a.closest && a.closest('article.uxtl__item')),
    index: a ? rows.indexOf(a.closest ? a.closest('article.uxtl__item') : null) : -1,
    rows: rows.length,
    top: box ? box.top : null,
    scrollY: window.scrollY,
    viewport: window.innerHeight,
  };
}"""


def _matter_with_rows(page, base_url, count: int) -> str:
    sign_in(page, base_url, MARTIN)
    page.set_viewport_size(VIEWPORT)
    url = create_matter(page, base_url, unique_title("Pikk ajalugu"))
    page.evaluate(FILE_ROWS, count)
    page.goto(url)
    page.wait_for_load_state("networkidle")
    return url


def _load_older_and_read(page) -> tuple[dict, dict]:
    older = page.locator("button.uxtl__older")
    expect(older).to_be_visible()
    older.scroll_into_view_if_needed()
    older.focus()
    before = page.evaluate(ACTIVE)
    page.keyboard.press("Enter")
    page.wait_for_function(
        "n => document.querySelectorAll('article.uxtl__item').length > n", arg=before["rows"]
    )
    page.wait_for_timeout(300)
    return before, page.evaluate(ACTIVE)


def _assert_landed_on_the_first_new_row(before: dict, after: dict) -> None:
    # Inside the row: a chronology row is `display: contents` and has no box of
    # its own, so the landing is its first control (or its first visible part).
    assert after["inRow"], after
    assert after["index"] == before["rows"], (before, after)
    assert 0 <= after["top"] < after["viewport"], after
    assert abs(after["scrollY"] - before["scrollY"]) < 200, (before, after)


def test_a_writer_lands_on_the_first_older_row(page, base_url):
    _matter_with_rows(page, base_url, 31)

    before, after = _load_older_and_read(page)
    _assert_landed_on_the_first_new_row(before, after)

    # And the next Tab continues from there, not from the top of the page.
    page.keyboard.press("Tab")
    step = page.evaluate(ACTIVE)
    assert step["inRow"] and step["index"] >= after["index"], step
    assert abs(step["scrollY"] - after["scrollY"]) < after["viewport"], (after, step)


def test_a_reader_lands_on_the_first_older_row_too(page, base_url):
    url = _matter_with_rows(page, base_url, 31)
    page.context.clear_cookies()
    sign_in(page, base_url, READER)
    page.set_viewport_size(VIEWPORT)
    page.goto(url)
    page.wait_for_load_state("networkidle")

    before, after = _load_older_and_read(page)
    assert after["tag"] != "body", after
    _assert_landed_on_the_first_new_row(before, after)


def test_removing_a_row_lands_on_its_neighbour(page, base_url):
    _matter_with_rows(page, base_url, 8)
    rows = page.locator("article.uxtl__item")
    victim_index = 5
    # The row is `display: contents`, so it is reached through its own control.
    remove = rows.nth(victim_index).locator("summary.uxtl__edit", has_text="Kustuta")
    remove.scroll_into_view_if_needed()
    remove.click()
    rows.nth(victim_index).get_by_role("button", name="Eemalda").focus()
    count = rows.count()
    scroll_before = page.evaluate("window.scrollY")
    page.keyboard.press("Enter")
    page.wait_for_function(
        "n => document.querySelectorAll('article.uxtl__item').length < n", arg=count
    )
    page.wait_for_timeout(300)

    after = page.evaluate(ACTIVE)
    assert after["inRow"], after
    assert after["index"] == victim_index, after
    assert 0 <= after["top"] < after["viewport"], after
    assert abs(after["scrollY"] - scroll_before) < 200, (scroll_before, after)
