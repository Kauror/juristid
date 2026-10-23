"""«Otsi minu asjadest» finds what is on the page, wherever on the page it is.

The box on Minu asjad narrows the open files already rendered under «Aktiivsed
teemad» (static/js/ux.js `bindRowFilter`). Three ways it used to fail (E-05):

* «zurii» did not find «Žürii» — a plain lower-case `indexOf`, while every
  server-side search folds diacritics;
* a match among the rows folded behind «Näita veel N» stayed inside the closed
  `<details>`, so the list looked empty while the row was there;
* nothing matching looked exactly like a page that had lost its rows.
"""

from __future__ import annotations

import unicodedata

import pytest
from playwright.sync_api import expect

from e2e.conftest import MARTIN, create_matter, sign_in, unique_title

pytestmark = pytest.mark.e2e

BOX = "#pw-search"
MORE = "details.pw-more:has(.pw-matters)"


def _folded(text: str) -> str:
    decomposed = unicodedata.normalize("NFD", text.lower())
    return "".join(char for char in decomposed if not unicodedata.combining(char))


def _a_desk_with_folded_rows(page, base_url) -> str:
    """Martin's desk with a «Žürii» file and more open files than the preview shows."""
    title = unique_title("Žürii hindamiskorra eelnõu")
    create_matter(page, base_url, title)
    page.goto(f"{base_url}/minu-asjad/")
    # Seven files is one past the preview (`PORTFOLIO_VISIBLE`); the seeded
    # desk usually has them already, and filing more only when it does not
    # keeps this test from growing the desk every other test reads.
    for _ in range(7):
        if page.locator(MORE).count():
            break
        create_matter(page, base_url, unique_title("Laua täiteteema"))
        page.goto(f"{base_url}/minu-asjad/")
    expect(page.locator(MORE)).to_have_count(1)
    return title


def test_the_filter_folds_diacritics_opens_the_fold_and_says_when_nothing_matches(page, base_url):
    sign_in(page, base_url, MARTIN)
    zurii = _a_desk_with_folded_rows(page, base_url)
    more = page.locator(MORE)
    expect(more).not_to_have_attribute("open", "")

    # «zurii» finds «Žürii».
    page.fill(BOX, _folded(zurii))
    expect(page.locator(".pw-matter", has_text=zurii)).to_be_visible()
    expect(page.locator("#pw-search-empty")).to_be_hidden()

    # A row behind «Näita veel» is shown, by opening the fold.
    folded_title = more.locator(".pw-matter__title").first.inner_text()
    page.fill(BOX, _folded(folded_title))
    expect(more).to_have_attribute("open", "")
    expect(more.locator(".pw-matter", has_text=folded_title).first).to_be_visible()

    # Nothing matching says so.
    page.fill(BOX, "qqxz-ei-ole-sellist")
    expect(page.locator("#pw-search-empty")).to_be_visible()
    expect(page.locator("#pw-search-empty")).to_have_text("Ükski teema ei vasta otsingule.")

    # Cleared: everything back, and the fold as the person left it — closed.
    page.fill(BOX, "")
    expect(page.locator("#pw-search-empty")).to_be_hidden()
    expect(more).not_to_have_attribute("open", "")
    expect(page.locator(".pw-matters > .pw-matter").first).to_be_visible()
