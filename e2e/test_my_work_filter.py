"""Minu asjad's own live filter (owner, 2026-11-16).

The box at the right end of the Seis strip (`[data-workfilter]`,
static/js/work-filter.js) narrows the dated rows already on the page: a row
stays when its link text holds every word typed, each band's count follows,
a band with nothing left is hidden, Esc brings the page back, and nothing
matching says «Ei leitud.».
"""

from __future__ import annotations

import pytest
from playwright.sync_api import expect

from e2e.conftest import SANDRA, create_matter, sign_in, start_first_step, unique_title

pytestmark = pytest.mark.e2e

BOX = "[data-workfilter]"
EMPTY = "[data-workfilter-empty]"
ROWS = "[data-workrow]"


def _dated_teema(page, base_url, prefix: str) -> str:
    title = unique_title(prefix)
    create_matter(page, base_url, title)
    # Same day for both, so both rows land in the same band.
    start_first_step(page, days=3, text="Filtri proovisamm")
    return title


def test_the_filter_narrows_rows_counts_and_bands_and_esc_restores(page, base_url):
    sign_in(page, base_url, SANDRA)
    first = _dated_teema(page, base_url, "Filtriproov alfa")
    second = _dated_teema(page, base_url, "Filtriproov beeta")
    page.goto(f"{base_url}/minu-asjad/")

    first_row = page.locator(ROWS, has_text=first)
    second_row = page.locator(ROWS, has_text=second)
    expect(first_row).to_have_count(1)
    expect(second_row).to_have_count(1)
    band = page.locator("section.workband", has=first_row)
    expect(band).to_have_count(1)
    count = band.locator(".workband__count")
    before = count.inner_text()
    assert int(before) >= 2  # both rows are in this band
    total_rows = page.locator(f"{ROWS}:visible").count()
    assert total_rows >= 2

    box = page.locator(BOX)
    expect(box).to_be_visible()
    token = first.split()[-1]
    box.fill(token)

    expect(first_row).to_be_visible()
    expect(second_row).to_be_hidden()
    expect(count).to_have_text("1")
    expect(page.locator(f"{ROWS}:visible")).to_have_count(1)
    expect(page.locator(EMPTY)).to_be_hidden()
    # Every other band with dated rows has nothing left and is hidden.
    for other in page.locator(f"section.workband:has({ROWS})").all():
        if other.locator(ROWS, has_text=first).count() == 0:
            expect(other).to_be_hidden()

    # Case and several words: every word must be in the row.
    box.fill(f"FILTRIPROOV {token.upper()}")
    expect(first_row).to_be_visible()
    expect(second_row).to_be_hidden()

    box.press("Escape")

    expect(box).to_have_value("")
    # Back as it was: neither row hidden by the filter (one may sit behind a
    # closed «Näita veel», which is how the page was drawn).
    expect(first_row).not_to_have_attribute("hidden", "")
    expect(second_row).not_to_have_attribute("hidden", "")
    expect(count).to_have_text(before)
    expect(page.locator(f"{ROWS}:visible")).to_have_count(total_rows)
    expect(page.locator(EMPTY)).to_be_hidden()


def test_nothing_matching_says_ei_leitud(page, base_url):
    sign_in(page, base_url, SANDRA)
    expect(page.locator(ROWS).first).to_be_attached()

    page.locator(BOX).fill("qqxz-ei-ole-sellist")

    expect(page.locator(EMPTY)).to_be_visible()
    expect(page.locator(EMPTY)).to_have_text("Ei leitud.")
    expect(page.locator(f"{ROWS}:visible")).to_have_count(0)

    page.locator(BOX).fill("")

    expect(page.locator(EMPTY)).to_be_hidden()
    expect(page.locator(ROWS).first).to_be_visible()
