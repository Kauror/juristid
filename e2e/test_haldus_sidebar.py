"""The Haldus sidebar in a real browser (UX-002, docs/adr/0221).

* From 900 px it is a column beside the page: Tab reaches its links in order,
  Enter follows one, and the page it opens is marked current.
* Narrower it is one closed «Haldus» menu above the page, opened from the
  keyboard, with no horizontal scroll at phone width; only one of the two
  renderings is ever displayed.
"""

from __future__ import annotations

import pytest
from playwright.sync_api import expect

from e2e.conftest import HEAD, SANDRA, document_overflows, sign_in

pytestmark = pytest.mark.e2e


def test_the_sidebar_is_reached_and_followed_from_the_keyboard(page, base_url):
    page.set_viewport_size({"width": 1280, "height": 900})
    sign_in(page, base_url, SANDRA)
    page.goto(f"{base_url}/haldus/")
    nav = page.get_by_role("navigation", name="Haldus", exact=True)
    expect(nav).to_be_visible()
    expect(nav.get_by_role("link", name="Ülevaade", exact=True)).to_have_attribute(
        "aria-current", "page"
    )
    expect(page.locator("details.haldusmenu")).to_be_hidden()

    target = nav.get_by_role("link", name="Kuvamine", exact=True)
    page.locator("#global-search").focus()
    for _ in range(40):
        page.keyboard.press("Tab")
        if target.evaluate("(link) => link === document.activeElement"):
            break
    else:
        raise AssertionError("Tab never reached Kuvamine in the Haldus sidebar")
    page.keyboard.press("Enter")
    page.wait_for_url(f"{base_url}/haldus/seaded/")
    expect(nav.get_by_role("link", name="Kuvamine", exact=True)).to_have_attribute(
        "aria-current", "page"
    )
    expect(page.get_by_role("heading", level=1, name="Kuvamine")).to_be_visible()


def test_a_head_is_offered_tookorraldus(page, base_url):
    page.set_viewport_size({"width": 1280, "height": 900})
    sign_in(page, base_url, HEAD)
    page.goto(f"{base_url}/haldus/")
    nav = page.get_by_role("navigation", name="Haldus", exact=True)
    expect(nav.get_by_text("Töökorraldus", exact=True)).to_be_visible()
    nav.get_by_role("link", name="Osakonna töökorraldus", exact=True).click()
    page.wait_for_url(f"{base_url}/haldus/osakond/")


def test_a_lawyer_is_not_offered_tookorraldus(page, base_url):
    page.set_viewport_size({"width": 1280, "height": 900})
    sign_in(page, base_url, SANDRA)
    page.goto(f"{base_url}/haldus/")
    nav = page.get_by_role("navigation", name="Haldus", exact=True)
    expect(nav.get_by_text("Töökorraldus", exact=True)).to_have_count(0)
    expect(nav.get_by_text("Integratsioonid", exact=True)).to_be_visible()


def test_at_phone_width_the_sidebar_is_one_closed_menu(page, base_url):
    page.set_viewport_size({"width": 375, "height": 812})
    sign_in(page, base_url, SANDRA)
    page.goto(f"{base_url}/haldus/registrid/")

    menu = page.locator("details.haldusmenu")
    expect(menu).to_be_visible()
    expect(page.locator("nav.haldusnav")).to_be_hidden()
    expect(menu).not_to_have_attribute("open", "")
    expect(menu.locator("summary")).to_contain_text("Registrite jälgimine")
    assert not document_overflows(page)

    menu.locator("summary").focus()
    page.keyboard.press("Enter")
    expect(menu).to_have_attribute("open", "")
    link = menu.get_by_role("link", name="Kuvamine", exact=True)
    expect(link).to_be_visible()
    assert not document_overflows(page)
    link.click()
    page.wait_for_url(f"{base_url}/haldus/seaded/")
