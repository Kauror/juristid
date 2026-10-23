"""The calendar opens inside the window, whichever edge its date box is near.

The 2026-10-23 bug-bounty (B-01) measured the panel off the left edge of a
390px phone on every narrow date box: `PRAEGUNE TEGEVUS`' `✓ Tehtud` and `Muuda`
by 70px, the header's `Saabus` and `Arvamuse tähtaeg` by 41px, `Muuda teemat`
by 98px and the register's `Täpsem otsing` by 65px — so «Eelmine kuu» and the
Monday and Tuesday columns could not be tapped. At 768px the register's «Kuni»
calendar ran off the right edge instead and gave the whole page a horizontal
scroll.

The stylesheet can only anchor the panel to one side of its box, and which side
is right depends on where the box happens to sit, so `bindDatePickers` measures
the panel when it opens (and when the window is resized while it is open) and
moves it back inside the window (static/js/app.js `placePanel`). Every assertion
here reads the rendered rectangle of the open panel.
"""

from __future__ import annotations

import pytest
from playwright.sync_api import expect

from e2e.conftest import (
    SANDRA,
    create_matter,
    open_done_form,
    sign_in,
    start_first_step,
    unique_title,
)

pytestmark = pytest.mark.e2e

PHONE = {"width": 390, "height": 844}
TABLET = {"width": 768, "height": 1024}

#: Sub-pixel slack for a bounding box measured in floats.
TOLERANCE_PX = 0.5

GEOMETRY = """(panel) => {
    const r = panel.getBoundingClientRect();
    const root = document.documentElement;
    return {left: r.left, right: r.right, width: r.width,
            viewport: root.clientWidth,
            scrollWidth: root.scrollWidth, clientWidth: root.clientWidth};
}"""


def assert_calendar_on_screen(page, trigger, name: str) -> None:
    """Open the calendar behind ``trigger`` and assert it is whole and tappable."""
    trigger.scroll_into_view_if_needed()
    trigger.click()
    picker = trigger.locator("xpath=..")
    panel = picker.locator(".datepicker__panel")
    expect(panel).to_be_visible()
    measured = panel.evaluate(GEOMETRY)
    assert measured["left"] >= -TOLERANCE_PX, (
        f"{name}: the calendar starts {measured['left']:.0f}px left of the window"
    )
    assert measured["right"] <= measured["viewport"] + TOLERANCE_PX, (
        f"{name}: the calendar ends at {measured['right']:.0f}px"
        f" in a {measured['viewport']}px window"
    )
    assert measured["scrollWidth"] == measured["clientWidth"], (
        f"{name}: the open calendar widens the page to {measured['scrollWidth']}px "
        f"in a {measured['clientWidth']}px window"
    )
    # The previous-month arrow is the control the off-screen panel hid first.
    previous = panel.locator('[data-datepicker-nav="previous"]')
    box = previous.bounding_box()
    assert box is not None and box["x"] >= -TOLERANCE_PX, f"{name}: «Eelmine kuu» is off-screen"
    page.keyboard.press("Escape")
    expect(panel).to_be_hidden()


@pytest.fixture
def matter_with_step(page, base_url):
    sign_in(page, base_url, SANDRA)
    url = create_matter(page, base_url, unique_title("Kalender ekraanil"))
    start_first_step(page)
    return url


def test_praegune_tegevus_calendars_stay_on_a_phone(page, matter_with_step):
    page.set_viewport_size(PHONE)
    page.goto(matter_with_step)
    open_done_form(page)
    assert_calendar_on_screen(
        page, page.locator("#tehtud .curact__when .datepicker__trigger"), "✓ Tehtud · Millal?"
    )

    page.goto(matter_with_step)
    page.locator("#lisa-jargmine > summary").click()
    assert_calendar_on_screen(
        page, page.locator("#lisa-jargmine .datepicker__trigger").first, "Muuda · Millal?"
    )


def test_header_calendars_stay_on_a_phone(page, matter_with_step):
    page.set_viewport_size(PHONE)
    page.goto(matter_with_step)
    for name, field in (("Saabus", "received_date"), ("Arvamuse tähtaeg", "response_deadline")):
        editor = page.locator(f"#teema-pais details.inlineedit:has(input[name={field}])").first
        editor.locator("summary").first.click()
        assert_calendar_on_screen(page, editor.locator(".datepicker__trigger").first, name)


def test_muuda_teemat_calendars_stay_on_a_phone(page, matter_with_step):
    page.set_viewport_size(PHONE)
    page.goto(matter_with_step + "muuda/")
    triggers = page.locator("main .datepicker__trigger:visible")
    assert triggers.count() > 0
    for index in range(triggers.count()):
        page.goto(matter_with_step + "muuda/")
        assert_calendar_on_screen(
            page,
            page.locator("main .datepicker__trigger:visible").nth(index),
            f"Muuda teemat #{index}",
        )


@pytest.mark.parametrize("viewport", [PHONE, TABLET], ids=["390", "768"])
def test_register_filter_calendars_stay_on_screen(page, base_url, viewport):
    sign_in(page, base_url, SANDRA)
    page.set_viewport_size(viewport)
    page.goto(f"{base_url}/teemad/")
    page.locator("details.filterpanel > summary").click()
    triggers = page.locator("details.filterpanel .datepicker__trigger:visible")
    assert triggers.count() >= 2
    for index in range(triggers.count()):
        assert_calendar_on_screen(page, triggers.nth(index), f"Täpsem otsing #{index}")
