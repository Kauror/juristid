"""UI-04: the rail calendar opens itself in the large panel, in a real browser.

Owner's brief of 2026-11-06 §7. An *inactive* «4 nädalat» or «Kuu» switches
the rail's view as before. The *active* one, and the KALENDER heading, open the
existing panel (`kalendri_aken=1`) in the same view at the same period, on the
same page: Minu asjad stays Minu asjad, and Osakond stays Osakond with its
`vaade`. Quarter and year open the panel as they always did.
"""

from __future__ import annotations

import re

import pytest
from playwright.sync_api import expect

from e2e.conftest import SANDRA, sign_in

pytestmark = pytest.mark.e2e

MINE = "#minu-kalender"
DEPARTMENT = "#osakonna-kalender"


def _open(page, base_url, path):
    sign_in(page, base_url, SANDRA)
    page.set_viewport_size({"width": 1440, "height": 1000})
    page.goto(f"{base_url}{path}")
    page.wait_for_load_state("networkidle")


def _rail_title(page, block):
    return page.locator(f"{block} > .wcal__bar .wcal__title")


def test_an_inactive_control_switches_the_rail_view(page, base_url):
    _open(page, base_url, "/minu-asjad/")

    page.locator(f"{MINE} > .wcal__bar a.wcal__view", has_text="Kuu").click()

    expect(page.locator(f"{MINE} > .wcal__bar .wcal__view.is-active")).to_have_text("Kuu")
    expect(page).to_have_url(re.compile(r"kalender=kuu"))
    assert "kalendri_aken" not in page.url
    expect(page.get_by_role("dialog")).to_have_count(0)


def test_the_active_four_weeks_opens_the_panel_at_the_shown_period(page, base_url):
    _open(page, base_url, "/minu-asjad/")
    page.locator(f"{MINE} > .wcal__bar a[aria-label='Järgmine periood']").click()
    expect(page).to_have_url(re.compile(r"kalendri_aeg=\d{4}-\d{2}-\d{2}"))
    shown = _rail_title(page, MINE).inner_text()
    period = re.search(r"kalendri_aeg=([0-9-]+)", page.url).group(1)

    page.locator(f"{MINE} > .wcal__bar .wcal__view.is-active", has_text="4 nädalat").click()

    dialog = page.get_by_role("dialog")
    expect(dialog).to_be_visible()
    expect(page.locator("#wcal-panel-title")).to_have_text(f"Kalender · {shown}")
    expect(page).to_have_url(re.compile(rf"kalendri_aeg={period}"))
    expect(page).to_have_url(re.compile(r"kalendri_aken=1"))
    expect(dialog.locator(".wcal__view.is-active")).to_have_text("4 nädalat")

    page.keyboard.press("Escape")
    expect(dialog).to_be_hidden()
    expect(_rail_title(page, MINE)).to_have_text(shown)


def test_the_active_month_opens_the_panel_at_the_shown_month(page, base_url):
    _open(page, base_url, "/minu-asjad/?kalender=kuu&kalendri_aeg=2026-12")

    page.locator(f"{MINE} > .wcal__bar .wcal__view.is-active", has_text="Kuu").click()

    expect(page.get_by_role("dialog")).to_be_visible()
    expect(page.locator("#wcal-panel-title")).to_have_text("Kalender · Detsember 2026")
    expect(page).to_have_url(re.compile(r"kalender=kuu&kalendri_aeg=2026-12&kalendri_aken=1"))


def test_the_heading_is_a_button_that_looks_like_the_heading(page, base_url):
    _open(page, base_url, "/minu-asjad/?kalender=kuu&kalendri_aeg=2027-02")
    heading = page.get_by_role("button", name="Kalender", exact=True)
    expect(heading).to_be_visible()

    style = heading.evaluate(
        """(node) => {
          const own = getComputedStyle(node);
          const label = getComputedStyle(node.closest('.railblock__label'));
          return {
            color: own.color, labelColor: label.color,
            decoration: own.textDecorationLine, cursor: own.cursor,
            transform: own.textTransform, weight: own.fontWeight, labelWeight: label.fontWeight,
          };
        }"""
    )
    assert style["color"] == style["labelColor"]
    assert style["decoration"] == "none"
    assert style["cursor"] == "pointer"
    assert style["transform"] == "uppercase"
    assert style["weight"] == style["labelWeight"]

    # Hovering changes nothing about its colour.
    heading.hover()
    assert heading.evaluate("(node) => getComputedStyle(node).color") == style["color"]

    # Keyboard: it takes focus with a visible ring, and Enter opens the panel.
    heading.focus()
    outline = heading.evaluate("(node) => getComputedStyle(node).outlineStyle")
    assert outline != "none"
    page.keyboard.press("Enter")

    expect(page.get_by_role("dialog")).to_be_visible()
    expect(page.locator("#wcal-panel-title")).to_have_text("Kalender · Veebruar 2027")
    expect(page).to_have_url(re.compile(r"kalendri_aken=1"))

    page.keyboard.press("Escape")
    expect(page.get_by_role("dialog")).to_be_hidden()
    expect(_rail_title(page, MINE)).to_have_text("Veebruar 2027")
    expect(page.get_by_role("button", name="Kalender", exact=True)).to_be_visible()


@pytest.mark.parametrize("scope", ["osakond", "valdkonniti"])
def test_the_department_calendar_enlarges_as_the_department(page, base_url, scope):
    _open(page, base_url, f"/osakond/?vaade={scope}&kalender=kuu")
    rail_people = page.locator(f"{DEPARTMENT} .wcal__people li").all_inner_texts()

    page.get_by_role("button", name="Osakonna kalender", exact=True).click()

    dialog = page.locator(f"{DEPARTMENT} [data-wcal-panel]")
    expect(dialog).to_be_visible()
    expect(page.locator("#wcal-panel-title")).to_have_text(re.compile(r"^Osakonna kalender · "))
    expect(page).to_have_url(re.compile(r"/osakond/\?"))
    expect(page).to_have_url(re.compile(rf"vaade={scope}"))
    expect(page).to_have_url(re.compile(r"kalendri_aken=1"))
    # The department's calendar, never the personal one: the same person legend.
    expect(page.locator(MINE)).to_have_count(0)
    assert dialog.locator(".wcal__people li").all_inner_texts() == rail_people
    assert rail_people, "the seeded department has dated work with somebody responsible"

    page.keyboard.press("Escape")
    expect(dialog).to_be_hidden()
    expect(page).to_have_url(re.compile(rf"vaade={scope}"))


def test_the_department_active_control_enlarges_as_the_department(page, base_url):
    _open(page, base_url, "/osakond/?vaade=osakond")

    page.locator(f"{DEPARTMENT} > .wcal__bar .wcal__view.is-active").click()

    expect(page.locator(f"{DEPARTMENT} [data-wcal-panel]")).to_be_visible()
    expect(page).to_have_url(re.compile(r"/osakond/\?vaade=osakond&kalendri_aken=1"))
    expect(page.locator(MINE)).to_have_count(0)


def test_quarter_still_opens_the_panel_as_before(page, base_url):
    _open(page, base_url, "/minu-asjad/")

    page.locator(f"{MINE} > .wcal__bar a.wcal__view", has_text="Kvartal").click()

    expect(page.get_by_role("dialog")).to_be_visible()
    expect(page).to_have_url(re.compile(r"kalender=kvartal"))
    assert "kalendri_aken" not in page.url
