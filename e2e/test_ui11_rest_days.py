"""UI-11: weekends and Estonian public holidays on the calendars, in a real browser.

Owner's brief of 2026-11-06 §14. A rest day's number is drawn in
`--calendar-rest-day` in both themes. A holiday's Estonian name is reachable
by hover (the `title` and the popover), by keyboard (the day's accessible name,
and Enter opens it) and by touch (a tap opens it), never by colour alone.
December 2026 holds jõululaupäev and both jõulupühad.
"""

from __future__ import annotations

import re

import pytest
from playwright.sync_api import expect

from e2e.conftest import SANDRA, sign_in

pytestmark = pytest.mark.e2e

DECEMBER = "kalender=kuu&kalendri_aeg=2026-12"
LIGHT = "try { window.localStorage.setItem('juristid-theme', 'light') } catch (e) {}"


def _holiday(page, block, day=24):
    number = page.locator(".wcal__num", has_text=re.compile(rf"^{day}$"))
    return page.locator(f"{block} .wcal__grid > .wcal__day.is-holiday").filter(has=number)


def _colours(page, block):
    return page.evaluate(
        """(block) => {
          const probe = document.createElement('span');
          probe.style.color = 'var(--calendar-rest-day)';
          document.body.appendChild(probe);
          const rest = getComputedStyle(probe).color;
          probe.remove();
          const num = (selector) => {
            const node = document.querySelector(`${block} ${selector}`);
            return node ? getComputedStyle(node).color : null;
          };
          const cells = [...document.querySelectorAll(`${block} .wcal__grid > .wcal__day`)];
          const numberOf = (text) => cells
            .map((cell) => cell.querySelector('.wcal__num'))
            .find((node) => node.textContent.trim() === text);
          const heads = document.querySelectorAll(`${block} .wcal__head--rest`);
          return {
            rest,
            holiday: num('.wcal__day.is-holiday .wcal__num'),
            sunday: getComputedStyle(numberOf('27')).color,
            ordinary: getComputedStyle(numberOf('23')).color,
            sundayHead: getComputedStyle(heads[1]).color,
          };
        }""",
        block,
    )


@pytest.mark.parametrize("theme", ["dark", "light"])
@pytest.mark.parametrize(
    ("path", "block"),
    [
        (f"/minu-asjad/?{DECEMBER}", "#minu-kalender"),
        (f"/osakond/?vaade=osakond&{DECEMBER}", "#osakonna-kalender"),
        (f"/minu-asjad/?{DECEMBER}&kalendri_aken=1", "[data-wcal-panel]"),
    ],
)
def test_rest_days_are_drawn_in_the_rest_day_colour(page, base_url, theme, path, block):
    if theme == "light":
        page.add_init_script(LIGHT)
    sign_in(page, base_url, SANDRA)
    page.set_viewport_size({"width": 1440, "height": 1000})
    page.goto(f"{base_url}{path}")
    page.wait_for_load_state("networkidle")
    assert page.evaluate("document.documentElement.dataset.theme") == theme

    colours = _colours(page, block)

    assert colours["holiday"] == colours["rest"]
    assert colours["sunday"] == colours["rest"]
    assert colours["sundayHead"] == colours["rest"]
    assert colours["ordinary"] != colours["rest"]


def test_a_holiday_is_named_on_hover_and_by_keyboard(page, base_url):
    sign_in(page, base_url, SANDRA)
    page.set_viewport_size({"width": 1440, "height": 1000})
    page.goto(f"{base_url}/minu-asjad/?{DECEMBER}")
    page.wait_for_load_state("networkidle")
    day = _holiday(page, "#minu-kalender")
    summary = day.locator(":scope > summary")

    expect(summary).to_have_attribute("title", "jõululaupäev (riigipüha)")
    # The seed puts a deadline at today + 45 days (seed_e2e_data), which is 24.12
    # when the suite runs on 9.11: the holiday's name must not depend on the day.
    expect(summary).to_have_accessible_name(
        re.compile(r"^24\.12\.2026, jõululaupäev \(riigipüha\): (sündmusi ei ole|\d+ \S.*)$")
    )

    summary.hover()
    expect(day.locator(".wcal__pop")).to_contain_text("jõululaupäev (riigipüha)")
    page.mouse.move(5, 5)
    expect(day.locator(".wcal__pop")).to_be_hidden()

    summary.focus()
    page.keyboard.press("Enter")
    expect(day.locator(".wcal__pop")).to_be_visible()
    expect(day.locator(".wcal__pop")).to_contain_text("Neljapäev 24.12.2026 · jõululaupäev")
    page.keyboard.press("Escape")
    expect(day.locator(".wcal__pop")).to_be_hidden()
    assert summary.evaluate("node => node === document.activeElement")


def test_a_tap_names_a_holiday_on_a_phone(browser, base_url):
    context = browser.new_context(
        viewport={"width": 390, "height": 844}, has_touch=True, is_mobile=True
    )
    page = context.new_page()
    try:
        sign_in(page, base_url, SANDRA)
        page.goto(f"{base_url}/osakond/?vaade=osakond&{DECEMBER}")
        page.wait_for_load_state("networkidle")
        day = _holiday(page, "#osakonna-kalender", 25)

        day.locator(":scope > summary").tap()

        pop = day.locator(".wcal__pop")
        expect(pop).to_be_visible()
        expect(pop).to_contain_text("esimene jõulupüha (riigipüha)")
        box = pop.bounding_box()
        assert box is not None and box["x"] >= 0 and box["x"] + box["width"] <= 390
    finally:
        context.close()


def test_in_the_panel_a_holiday_lists_its_name_under_the_calendars(page, base_url):
    """The panel's own click rule (owner's review of 2026-10-30) applies to a holiday too."""
    sign_in(page, base_url, SANDRA)
    page.set_viewport_size({"width": 1440, "height": 1000})
    page.goto(f"{base_url}/minu-asjad/?{DECEMBER}&kalendri_aken=1")
    page.wait_for_load_state("networkidle")
    day = _holiday(page, "[data-wcal-panel]", 26)

    day.locator(":scope > summary").focus()
    page.keyboard.press("Enter")

    detail = page.locator("[data-wcal-panel] [data-wcal-daydetail]")
    expect(detail).to_be_visible()
    expect(detail).to_contain_text("teine jõulupüha (riigipüha)")
