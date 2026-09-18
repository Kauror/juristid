"""The datepicker's buttons are big enough to hit, measured rather than declared.

Overnight QA (JURISTID-QA-OVERNIGHT-2026-09-18 §QA-12) measured the 📅 «Ava
kalender» button at **32×23** and the month arrows at **23×24** — all three
under the 24×24 floor WCAG 2.2 AA calls *Target Size (Minimum)*, on every date
box in the application.

Every assertion here reads `getBoundingClientRect()` off the real button.
Asserting that `min-height: 32px` appears in the stylesheet would prove nothing:
the previous size came from `padding` plus whatever the emoji happened to
rasterise to, so the declaration and the rendered box were never the same
question. A rule that is overridden, or that loses to `line-height` on a
non-flex box, still reads fine in the file — and is exactly the defect this is
here to catch.

The day cells are measured too, and deliberately *not* changed: they came out
31×27 before this fix and after it, which already clears the floor. They are
asserted so that a later change to the seven-column grid cannot quietly shrink
them under it.
"""

from __future__ import annotations

import pytest

from e2e.conftest import MARTIN, sign_in

pytestmark = pytest.mark.e2e

#: WCAG 2.2 AA, *Target Size (Minimum)*, 2.5.8. The floor, not the target.
MINIMUM_TARGET_PX = 24

#: What the trigger and the month arrows are actually sized to. Square, and the
#: height of the input beside them, so the pair reads as one control; 44 would
#: be taller than the field it belongs to and would make a secondary affordance
#: the heaviest thing on a dense form (static/css/app.css, the date control).
COMFORTABLE_TARGET_PX = 32

#: Sub-pixel slack. A bounding box is a float — the trigger measures 32.31 wide
#: because of its border and its glyph — and a viewport that lands a box on
#: 31.98 is not a defect anybody can click wrong.
TOLERANCE_PX = 0.5

#: Narrow phone, wide phone, tablet, docked window, desktop.
WIDTHS = (375, 420, 768, 1024, 1440)


def box(locator) -> dict[str, float]:
    measured = locator.bounding_box()
    assert measured is not None, "the control is not rendered"
    return measured


def assert_meets(measured: dict[str, float], name: str, minimum: int) -> None:
    assert measured["width"] >= minimum - TOLERANCE_PX, (
        f"{name} is {measured['width']:.2f}px wide, under the {minimum}px minimum"
    )
    assert measured["height"] >= minimum - TOLERANCE_PX, (
        f"{name} is {measured['height']:.2f}px tall, under the {minimum}px minimum"
    )


def picker_for(page, field_id: str):
    """The wrapper `app.js` puts around one date box, holding trigger and panel."""
    return page.locator(field_id).locator("xpath=..")


def create_form(page, base_url: str) -> None:
    page.goto(f"{base_url}/teemad/uus/")
    page.wait_for_load_state("networkidle")


def open_calendar(page, base_url: str, field_id: str = "#id_received_date"):
    create_form(page, base_url)
    picker = picker_for(page, field_id)
    picker.locator(".datepicker__trigger").click()
    panel = picker.locator(".datepicker__panel")
    panel.wait_for(state="visible")
    return picker, panel


# ---------------------------------------------------------------------------
# The opener
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("width", WIDTHS)
def test_the_calendar_trigger_is_big_enough_to_hit(page, base_url, width):
    """The reported defect, at every width the product is used at.

    23.33px tall before the fix — and identically so at all five widths, because
    nothing in the old rule depended on the viewport. That is why this is
    parametrised: the failure was uniform, so a single-width test would have
    passed the day somebody made it responsive and wrong.
    """
    sign_in(page, base_url, MARTIN)
    page.set_viewport_size({"width": width, "height": 900})
    create_form(page, base_url)

    measured = box(picker_for(page, "#id_received_date").locator(".datepicker__trigger"))
    assert_meets(measured, f"the calendar trigger at {width}px", MINIMUM_TARGET_PX)
    # And the size it was actually given, so a regression that merely scrapes
    # past 24 is still a regression this notices.
    assert_meets(measured, f"the calendar trigger at {width}px", COMFORTABLE_TARGET_PX)


def test_every_date_box_on_the_form_has_a_usable_trigger(page, base_url):
    """Not just the first one. The rule is on the class, and the form renders
    several — a fix that reached one field because of where it sat would pass a
    single-locator test.

    `visible=true`, because the creation form also carries date boxes inside
    collapsed sections. Those measure 0×0 while their section is shut, which is
    what `display: none` means and not a target anybody can miss; asserting over
    them would be asserting that a hidden control is 24px wide.
    """
    sign_in(page, base_url, MARTIN)
    create_form(page, base_url)

    triggers = page.locator(".createform .datepicker__trigger").locator("visible=true")
    count = triggers.count()
    assert count >= 2, f"expected the creation form to render several date boxes, got {count}"
    for index in range(count):
        assert_meets(box(triggers.nth(index)), f"date trigger {index}", MINIMUM_TARGET_PX)


# ---------------------------------------------------------------------------
# The controls inside the calendar
# ---------------------------------------------------------------------------


def test_the_month_arrows_are_big_enough_to_hit(page, base_url):
    """Previous and next month, which failed the same floor on both axes.

    A calendar whose opener is comfortable while its month arrows are not is
    half a fix, so they are asserted in the same file as the opener.
    """
    sign_in(page, base_url, MARTIN)
    _, panel = open_calendar(page, base_url)

    for name in ("Eelmine kuu", "Järgmine kuu"):
        arrow = panel.get_by_role("button", name=name)
        assert_meets(box(arrow), name, MINIMUM_TARGET_PX)
        assert_meets(box(arrow), name, COMFORTABLE_TARGET_PX)


def test_the_day_cells_clear_the_minimum(page, base_url):
    """Measured, and left alone.

    31×27 before this change and after it. The assertion exists so that a later
    change to the seven-column grid cannot shrink a day under the floor without
    a test noticing — not because anything here moved them.
    """
    sign_in(page, base_url, MARTIN)
    _, panel = open_calendar(page, base_url)

    days = panel.locator(".datepicker__day")
    count = days.count()
    assert count >= 28, f"expected a month of days, got {count}"
    for index in (0, count // 2, count - 1):
        assert_meets(box(days.nth(index)), f"day cell {index}", MINIMUM_TARGET_PX)


# ---------------------------------------------------------------------------
# What the larger buttons must not have cost
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("width", WIDTHS)
def test_the_date_field_still_fits_on_one_row(page, base_url, width):
    """The input keeps the width; the trigger keeps its size; neither wraps.

    `.datepicker` is an `inline-flex` whose input flexes and whose trigger does
    not. A minimum size on the trigger is exactly the kind of change that takes
    the room out of the box beside it, so the row is measured rather than
    eyeballed: same vertical centre means one row, and a positive input width
    means the field did not collapse.
    """
    sign_in(page, base_url, MARTIN)
    page.set_viewport_size({"width": width, "height": 900})
    create_form(page, base_url)

    picker = picker_for(page, "#id_received_date")
    field = box(picker.locator(".dateinput"))
    trigger = box(picker.locator(".datepicker__trigger"))

    assert field["width"] > trigger["width"], (
        f"at {width}px the date box ({field['width']:.2f}px) is no wider than its "
        f"trigger ({trigger['width']:.2f}px)"
    )
    field_centre = field["y"] + field["height"] / 2
    trigger_centre = trigger["y"] + trigger["height"] / 2
    assert abs(field_centre - trigger_centre) <= 2, (
        f"at {width}px the trigger wrapped off the field's row "
        f"({field_centre:.2f} vs {trigger_centre:.2f})"
    )


@pytest.mark.parametrize("width", WIDTHS)
def test_the_open_calendar_adds_no_horizontal_overflow(page, base_url, width):
    """A panel that pushes the document wider than the window is a panel that
    put a horizontal scrollbar on a form."""
    sign_in(page, base_url, MARTIN)
    page.set_viewport_size({"width": width, "height": 900})
    create_form(page, base_url)
    _, panel = open_calendar(page, base_url)

    overflow = page.evaluate(
        "() => [document.documentElement.scrollWidth, document.documentElement.clientWidth]"
    )
    assert overflow[0] <= overflow[1] + 1, f"at {width}px the document overflows: {overflow}"

    measured = box(panel)
    assert measured["x"] >= 0, measured
    assert measured["x"] + measured["width"] <= width + 1, measured


def test_the_calendar_is_still_seven_columns_at_375(page, base_url):
    """The grid, after the head above it grew.

    Seven columns, and the weekday headings standing on the same x positions as
    the days beneath them — a calendar whose headings have drifted a column is
    read wrong at a glance rather than seen to be broken.
    """
    sign_in(page, base_url, MARTIN)
    page.set_viewport_size({"width": 375, "height": 900})
    create_form(page, base_url)
    open_calendar(page, base_url)

    grid = page.evaluate(
        """() => {
            const panel = document.querySelector('.datepicker__panel:not([hidden])');
            const column = (element) => Math.round(element.getBoundingClientRect().x);
            return {
                columns: getComputedStyle(panel.querySelector('.datepicker__grid'))
                    .gridTemplateColumns.split(' ').length,
                headings: [...panel.querySelectorAll('.datepicker__weekday')].map(column),
                days: [...new Set([...panel.querySelectorAll('.datepicker__day')]
                    .map(column))].sort((a, b) => a - b),
            };
        }"""
    )
    assert grid["columns"] == 7, grid
    assert len(grid["headings"]) == 7, grid
    assert grid["headings"] == grid["days"], grid


# ---------------------------------------------------------------------------
# Behaviour, which a size change is not allowed to touch
# ---------------------------------------------------------------------------


def test_the_bigger_trigger_still_opens_and_closes_the_calendar(page, base_url):
    """`aria-expanded` false → true → false, and the panel following it.

    The trigger became an `inline-flex` box with a centred glyph. A click that
    landed on the glyph rather than the button would still open the calendar and
    would still look right — so what is asserted is the button's own state.
    """
    sign_in(page, base_url, MARTIN)
    create_form(page, base_url)

    picker = picker_for(page, "#id_received_date")
    trigger = picker.locator(".datepicker__trigger")
    panel = picker.locator(".datepicker__panel")

    assert trigger.get_attribute("aria-expanded") == "false"
    trigger.click()
    panel.wait_for(state="visible")
    assert trigger.get_attribute("aria-expanded") == "true"
    trigger.click()
    panel.wait_for(state="hidden")
    assert trigger.get_attribute("aria-expanded") == "false"


def test_the_trigger_is_still_a_named_button(page, base_url):
    """The accessible name, the element and the glyph, none of which moved.

    A target-size fix that reached the size by replacing the button with a
    padded `span` would pass every measurement above and lose the keyboard.
    """
    sign_in(page, base_url, MARTIN)
    create_form(page, base_url)

    trigger = picker_for(page, "#id_received_date").locator(".datepicker__trigger")
    assert trigger.evaluate("element => element.tagName") == "BUTTON"
    assert trigger.get_attribute("type") == "button"
    assert trigger.get_attribute("aria-label") == "Ava kalender"
    assert trigger.inner_text().strip() == "📅"


def test_choosing_a_day_from_the_bigger_calendar_still_writes_the_date(page, base_url):
    """The whole selection contract, unchanged: the value, both events, the
    panel closing and focus coming back to the box."""
    sign_in(page, base_url, MARTIN)
    _, panel = open_calendar(page, base_url, "#id_response_deadline")
    page.evaluate(
        """() => {
            window.__datepickerEvents = [];
            const input = document.querySelector('#id_response_deadline');
            input.addEventListener('input', () => window.__datepickerEvents.push('input'));
            input.addEventListener('change', () => window.__datepickerEvents.push('change'));
        }"""
    )

    panel.locator(".datepicker__day").filter(has_text="15").first.click()

    assert page.locator("#id_response_deadline").input_value().startswith("15.")
    assert page.evaluate("() => window.__datepickerEvents") == ["input", "change"]
    panel.wait_for(state="hidden")
    assert page.evaluate("() => document.activeElement.id") == "id_response_deadline"


def test_the_month_arrows_still_navigate_without_closing_the_calendar(page, base_url):
    """Bigger arrows, same `stopPropagation` contract."""
    sign_in(page, base_url, MARTIN)
    _, panel = open_calendar(page, base_url)

    opening = panel.locator(".datepicker__title").inner_text()
    panel.get_by_role("button", name="Järgmine kuu").click()
    panel.wait_for(state="visible")
    assert panel.locator(".datepicker__title").inner_text() != opening

    panel.get_by_role("button", name="Eelmine kuu").click()
    panel.wait_for(state="visible")
    assert panel.locator(".datepicker__title").inner_text() == opening
