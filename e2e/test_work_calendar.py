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


def test_inside_the_quarter_panel_a_click_lists_the_day_under_the_calendars(page, base_url):
    """Owner's review of 2026-10-30: in the panel a click on a day lists it below
    the calendars, where it reads more easily; hover still opens the popover."""
    _open(page, base_url, path="/minu-asjad/?kalender=kvartal")
    day = page.locator(f"[data-wcal-panel] {TODAY_DAY.removeprefix('#minu-kalender ')}")
    summary = day.locator(":scope > summary")
    detail = page.locator("[data-wcal-panel] [data-wcal-daydetail]")
    expect(detail).to_be_hidden()

    summary.focus()
    page.keyboard.press("Enter")

    expect(detail).to_be_visible()
    expect(detail).to_contain_text("Konfidentsiaalne järgmine samm")
    expect(day.locator(".wcal__pop")).to_be_hidden()
    expect(day).to_have_class(re.compile(r"(^| )is-selected( |$)"))
    box = detail.bounding_box()
    calendars = page.locator("[data-wcal-panel] .wcal__months").bounding_box()
    assert box is not None and calendars is not None
    assert box["y"] >= calendars["y"] + calendars["height"] - 1

    # The same day again folds the list away.
    page.keyboard.press("Enter")
    expect(detail).to_be_hidden()

    # Hover is still the quick look (focus elsewhere, as a pointer user's is).
    page.locator("#wcal-panel-title").focus()
    page.mouse.move(5, 5)
    summary.hover()
    expect(day.locator(".wcal__pop")).to_be_visible()
    page.mouse.move(5, 5)
    expect(day.locator(".wcal__pop")).to_be_hidden()
    expect(page.get_by_role("dialog")).to_be_visible()


@pytest.mark.parametrize(("label", "view"), [("4 nädalat", None), ("Kuu", "kuu")])
def test_four_weeks_and_month_stay_in_the_panel(page, base_url, label, view):
    """Owner's review of 2026-10-30: the panel is not only for quarter and year."""
    _open(page, base_url, path="/minu-asjad/?kalender=kvartal")

    page.locator("[data-wcal-panel] a.wcal__view", has_text=label).click()

    dialog = page.get_by_role("dialog")
    expect(dialog).to_be_visible()
    expect(page).to_have_url(re.compile(r"kalendri_aken=1"))
    expect(page.locator("[data-wcal-panel] .wcal__view.is-active")).to_have_text(label)
    if view:
        expect(page).to_have_url(re.compile(rf"kalender={view}"))

    page.keyboard.press("Escape")
    expect(dialog).to_be_hidden()
    assert "kalendri_aken" not in page.url
    expect(page.locator("#minu-kalender .wcal__view.is-active")).to_have_text(label)


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


def test_department_dots_are_painted_in_their_persons_colour(page, base_url):
    """The person classes win over the generic work fill declared later in app.css.

    They once did not: every dot on Osakond drew in the one work colour and the
    legend's names matched nothing on the grid. Read from the computed style,
    because only the cascade can tell.
    """
    _open(page, base_url, path="/osakond/")
    fills = page.evaluate(
        """() => {
          const work = getComputedStyle(
            document.querySelector('#osakonna-kalender .wcal__legend .wcal__dot--too')
          ).backgroundColor;
          const people = [...document.querySelectorAll(
            '#osakonna-kalender .wcal__grid .wcal__dot[class*="wcal__dot--p"]'
          )].map((dot) => getComputedStyle(dot).backgroundColor);
          const nobody = [...document.querySelectorAll(
            '#osakonna-kalender .wcal__grid .wcal__dot--none'
          )].map((dot) => getComputedStyle(dot).backgroundColor);
          return {work, people, nobody};
        }"""
    )

    assert fills["people"], "the seeded world has dated work with somebody responsible"
    assert all(fill != fills["work"] for fill in fills["people"])
    assert all(fill == "rgba(0, 0, 0, 0)" for fill in fills["nobody"])
