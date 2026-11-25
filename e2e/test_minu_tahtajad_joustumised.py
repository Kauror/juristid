"""Minu tähtajad and Minu jõustumised in a browser.

The populations and the self-only rule are proved in `tests/test_my_dates.py`.
Here: the tab row moves between the pages, the calendar's day links and month
arrows work without script, a count card filters the table, and neither page
scrolls sideways at phone width.
"""

from __future__ import annotations

import pytest
from playwright.sync_api import expect

from e2e.conftest import SANDRA, document_overflows, sign_in

pytestmark = pytest.mark.e2e


def test_the_tab_row_and_the_calendar_work_without_script(page, base_url):
    sign_in(page, base_url, SANDRA)
    page.goto(f"{base_url}/minu-asjad/tahtajad/")

    expect(page.get_by_role("heading", name="Minu tähtajad", exact=True)).to_be_visible()
    tabs = page.get_by_role("navigation", name="Minu lehed")
    expect(tabs.get_by_role("link", name="Minu tähtajad")).to_have_attribute("aria-current", "page")
    expect(page.locator(".mydates__card")).to_have_count(5)
    expect(page.locator(".mycal__grid thead th")).to_have_count(7)

    title = page.locator("#mycal-title")
    before = title.inner_text()
    page.get_by_role("link", name="Järgmine kuu").click()
    expect(title).not_to_have_text(before)
    page.get_by_role("link", name="Eelmine kuu").click()
    expect(title).to_have_text(before)

    page.locator(".mycal__day").nth(10).click()
    page.wait_for_url("**paev=**")
    expect(page.locator(".mycal__day[aria-current='date']")).to_have_count(1)

    page.locator(".mydates__card").first.click()
    page.wait_for_url("**aeg=moodunud**")
    expect(page.locator(".mydates__card").first).to_have_attribute("aria-current", "true")

    tabs.get_by_role("link", name="Minu jõustumised").click()
    page.wait_for_url(f"{base_url}/minu-asjad/joustumised/")
    expect(page.get_by_role("heading", name="Minu jõustumised", exact=True)).to_be_visible()
    expect(page.locator(".mydates__headline")).to_contain_text("Järgmise 30 päeva jooksul")


@pytest.mark.parametrize("path", ["/minu-asjad/tahtajad/", "/minu-asjad/joustumised/"])
def test_neither_page_scrolls_sideways_at_phone_width(page, base_url, path):
    page.set_viewport_size({"width": 375, "height": 812})
    sign_in(page, base_url, SANDRA)
    page.goto(f"{base_url}{path}")

    expect(page.locator(".mycal__grid")).to_be_visible()
    assert not document_overflows(page)
