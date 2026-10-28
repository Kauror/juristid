"""Kalender on Minu asjad, in a real browser (docs/adr/0158, amendment of 2026-10-26).

What only a browser can say: that a day opens from the keyboard and closes with
Escape back onto itself, that hover is a shortcut and not the only way, that a
view change swaps the block in place and lands in the history, that Back brings
the period back, and that a phone gets no horizontal scroll.

Sandra's seeded world always holds a step dated *today* (`seed_e2e_data`), so
today's cell in the default four weeks is a day with a record on every run.
"""

from __future__ import annotations

import re

import pytest
from playwright.sync_api import expect

from e2e.conftest import SANDRA, sign_in

pytestmark = pytest.mark.e2e

TODAY_DAY = '#minu-kalender details[data-wcal-day]:has(summary[aria-current="date"])'


def _open(page, base_url, width=1440, height=1000, path="/minu-asjad/"):
    sign_in(page, base_url, SANDRA)
    page.set_viewport_size({"width": width, "height": height})
    if path != "/minu-asjad/":
        page.goto(f"{base_url}{path}")


def test_a_day_opens_from_the_keyboard_and_escape_returns_to_it(page, base_url):
    _open(page, base_url)
    day = page.locator(TODAY_DAY)
    summary = day.locator(":scope > summary")

    summary.focus()
    page.keyboard.press("Enter")

    popover = day.locator(".wcal__pop")
    expect(popover).to_be_visible()
    expect(popover).to_contain_text("Konfidentsiaalne järgmine samm")
    expect(popover).to_contain_text("Tähtajad ja tegevused")
    box = popover.bounding_box()
    assert box is not None
    assert box["x"] >= 0 and box["x"] + box["width"] <= 1440

    page.keyboard.press("Escape")
    expect(popover).to_be_hidden()
    assert summary.evaluate("node => node === document.activeElement")


def test_hover_opens_a_day_and_leaving_closes_it(page, base_url):
    _open(page, base_url)
    day = page.locator(TODAY_DAY)

    day.locator(":scope > summary").hover()
    expect(day.locator(".wcal__pop")).to_be_visible()

    page.mouse.move(5, 5)
    expect(day.locator(".wcal__pop")).to_be_hidden()


def test_a_view_change_swaps_in_place_and_back_restores_the_period(page, base_url):
    _open(page, base_url)
    page.evaluate("window.__stillHere = true")

    page.locator("#minu-kalender a.wcal__view", has_text="Kuu").first.click()
    expect(page).to_have_url(re.compile(r"kalender=kuu"))
    expect(page.locator("#minu-kalender .wcal__view.is-active")).to_have_text("Kuu")
    # Swapped, not navigated: the page's own state survived the click.
    assert page.evaluate("window.__stillHere === true")

    page.locator("#minu-kalender a.wcal__view", has_text="Kvartal").first.click()
    dialog = page.get_by_role("dialog")
    expect(dialog).to_be_visible()
    expect(page.locator("#wcal-panel-title")).to_be_focused()

    page.keyboard.press("Escape")
    expect(dialog).to_be_hidden()
    expect(page).to_have_url(re.compile(r"kalender=kuu"))

    page.go_back()
    expect(page).to_have_url(re.compile(r"kalender=kvartal"))
    expect(page.get_by_role("dialog")).to_be_visible()


def test_the_year_opens_a_month_by_click(page, base_url):
    _open(page, base_url, path="/minu-asjad/?kalender=aasta&kalendri_aeg=2027")

    page.locator("#minu-kalender .wcal__tile", has_text="Märts").click()

    expect(page).to_have_url(re.compile(r"kalender=kuu&kalendri_aeg=2027-03"))
    expect(page.locator("#minu-kalender .wcal__title").first).to_have_text("Märts 2027")


@pytest.mark.parametrize("view", ["", "?kalender=kuu", "?kalender=kvartal", "?kalender=aasta"])
def test_no_horizontal_scroll_on_a_phone(page, base_url, view):
    _open(page, base_url, width=375, height=812, path=f"/minu-asjad/{view}")

    overflow = page.evaluate(
        "document.documentElement.scrollWidth - document.documentElement.clientWidth"
    )

    assert overflow <= 0


def test_inside_the_quarter_panel_a_day_opens_on_screen_and_escape_closes_only_it(page, base_url):
    """Review of #550: the panel must not displace its popovers, and Escape on an
    open day closes the day, not the panel."""
    _open(page, base_url, path="/minu-asjad/?kalender=kvartal")
    day = page.locator(f"[data-wcal-panel] {TODAY_DAY.removeprefix('#minu-kalender ')}")
    summary = day.locator(":scope > summary")

    summary.focus()
    page.keyboard.press("Enter")
    popover = day.locator(".wcal__pop")
    expect(popover).to_be_visible()
    pop = popover.bounding_box()
    anchor = summary.bounding_box()
    assert pop is not None and anchor is not None
    assert 0 <= pop["x"] and pop["x"] + pop["width"] <= 1440
    # Placed against its own day, not shifted by the panel's offset.
    assert abs((pop["x"] + pop["width"]) - (anchor["x"] + anchor["width"])) < 40

    page.keyboard.press("Escape")
    expect(popover).to_be_hidden()
    expect(page.get_by_role("dialog")).to_be_visible()


# ---------------------------------------------------------------------------
# Osakond's calendar — the same block, department-wide (app/matters/calendar.py)
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("scope", ["osakond", "valdkonniti"])
def test_the_department_calendar_swaps_in_place_and_keeps_the_scope(page, base_url, scope):
    _open(page, base_url, path=f"/osakond/?vaade={scope}")
    page.evaluate("window.__stillHere = true")

    page.locator("#osakonna-kalender a.wcal__view", has_text="Kuu").first.click()

    expect(page).to_have_url(re.compile(r"kalender=kuu"))
    expect(page).to_have_url(re.compile(rf"vaade={scope}"))
    expect(page.locator("#osakonna-kalender .wcal__view.is-active")).to_have_text("Kuu")
    assert page.evaluate("window.__stillHere === true")


def test_a_department_day_opens_from_the_keyboard_with_person_first_entries(page, base_url):
    _open(page, base_url, path="/osakond/")
    day = page.locator(
        '#osakonna-kalender details[data-wcal-day]:has(summary[aria-current="date"])'
    )
    summary = day.locator(":scope > summary")

    summary.focus()
    page.keyboard.press("Enter")

    popover = day.locator(".wcal__pop")
    expect(popover).to_be_visible()
    expect(popover.locator(".wcal__eventperson").first).to_be_visible()
    expect(popover).not_to_contain_text("Konfidentsiaalne järgmine samm")

    page.keyboard.press("Escape")
    expect(popover).to_be_hidden()


@pytest.mark.parametrize("view", ["", "&kalender=kuu", "&kalender=kvartal", "&kalender=aasta"])
def test_no_horizontal_scroll_on_a_phone_on_osakond(page, base_url, view):
    _open(page, base_url, width=375, height=812, path=f"/osakond/?vaade=osakond{view}")

    overflow = page.evaluate(
        "document.documentElement.scrollWidth - document.documentElement.clientWidth"
    )

    assert overflow <= 0
