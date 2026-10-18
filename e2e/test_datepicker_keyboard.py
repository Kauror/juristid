"""The calendar is a dialog with a day grid a keyboard can walk (JUX-008).

The 2026-10-17 audit (JUX-008, with JUX-007's datepicker half) found the
application's one date control usable by keyboard only the long way round:

* every day of the month was its own Tab stop — 31 of them between the month
  arrows and the next field — and no arrow key did anything;
* the panel had no role and no name, and the days were not a grid;
* pressing a month arrow rebuilt the panel and dropped focus to `<body>`;
* the trigger sat inside the field's `<label>`, so the date box was announced
  as «Millal? (kohustuslik) Ava kalender»;
* Escape closed the calendar without saying it had handled the key, so a
  surrounding panel closed too.

The control now follows the date-picker dialog pattern: the panel is a labelled
`role="dialog"`, the days are a `role="grid"` with one Tab stop, arrow keys move
a day or a week, Home/End go to the week's ends and PageUp/PageDown change the
month; focus stays on a month arrow after it is pressed; and the trigger lives
outside the label (static/js/app.js `buildCalendar`, `bindDatePickers`).
"""

from __future__ import annotations

import re

import pytest
from playwright.sync_api import expect

from e2e.conftest import MARTIN, SANDRA, create_matter, open_add_panel, sign_in, unique_title

pytestmark = pytest.mark.e2e

FIELD = "#id_received_date"


def _picker(page, field: str = FIELD):
    return page.locator(f".datepicker:has({field})")


@pytest.fixture
def create_form(page, base_url):
    sign_in(page, base_url, MARTIN)
    page.goto(f"{base_url}/teemad/uus/")
    expect(page.locator(FIELD)).to_be_visible()


def _open_on(page, value: str):
    page.locator(FIELD).fill(value)
    picker = _picker(page)
    picker.locator(".datepicker__trigger").click()
    panel = picker.locator(".datepicker__panel")
    expect(panel).to_be_visible()
    return panel


def _focused_label(page) -> str:
    return page.evaluate(
        "() => document.activeElement && document.activeElement.getAttribute('aria-label')"
    )


def test_the_panel_is_a_labelled_dialog_holding_a_day_grid(page, create_form):
    panel = _open_on(page, "15.6.2026")

    expect(panel).to_have_attribute("role", "dialog")
    expect(panel).to_have_accessible_name(re.compile(r"\S"))
    grid = panel.get_by_role("grid")
    expect(grid).to_have_count(1)
    expect(grid).to_have_accessible_name("juuni 2026")
    expect(grid.get_by_role("columnheader")).to_have_count(7)
    expect(grid.get_by_role("gridcell", name="15.6.2026")).to_have_count(1)

    # One Tab stop for the whole month, on the chosen day, which has focus.
    days = panel.locator(".datepicker__day")
    expect(days).to_have_count(30)
    expect(panel.locator('.datepicker__day[tabindex="0"]')).to_have_count(1)
    expect(panel.locator('.datepicker__day[tabindex="-1"]')).to_have_count(29)
    assert _focused_label(page) == "15.6.2026"


def test_arrow_keys_move_the_day_and_enter_picks_it(page, create_form):
    panel = _open_on(page, "15.6.2026")  # a Monday
    assert _focused_label(page) == "15.6.2026"

    steps = [
        ("ArrowRight", "16.6.2026"),
        ("ArrowDown", "23.6.2026"),
        ("ArrowLeft", "22.6.2026"),
        ("ArrowUp", "15.6.2026"),
        ("End", "21.6.2026"),
        ("Home", "15.6.2026"),
        ("PageDown", "15.7.2026"),
        ("PageUp", "15.6.2026"),
        ("ArrowUp", "8.6.2026"),
        ("ArrowUp", "1.6.2026"),
        ("ArrowLeft", "31.5.2026"),
    ]
    for key, expected in steps:
        page.keyboard.press(key)
        assert _focused_label(page) == expected, f"after {key}"
    expect(panel.locator(".datepicker__title")).to_have_text("mai 2026")
    # The roving Tab stop follows the focus.
    expect(panel.locator('.datepicker__day[tabindex="0"]')).to_have_attribute(
        "aria-label", "31.5.2026"
    )

    page.keyboard.press("Enter")
    expect(page.locator(FIELD)).to_have_value("31.5.2026")
    expect(panel).to_be_hidden()
    expect(page.locator(FIELD)).to_be_focused()


def test_tab_leaves_the_days_in_one_step(page, create_form):
    panel = _open_on(page, "15.6.2026")
    assert _focused_label(page) == "15.6.2026"
    page.keyboard.press("Shift+Tab")
    # Back out of the grid to the month arrow before it, never to the 14th.
    expect(panel.get_by_role("button", name="Järgmine kuu")).to_be_focused()
    page.keyboard.press("Tab")
    assert _focused_label(page) == "15.6.2026"


def test_month_navigation_keeps_focus_on_the_arrow_pressed(page, create_form):
    panel = _open_on(page, "15.6.2026")
    following = panel.get_by_role("button", name="Järgmine kuu")
    following.focus()
    page.keyboard.press("Enter")
    expect(panel.locator(".datepicker__title")).to_have_text("juuli 2026")
    expect(panel.get_by_role("button", name="Järgmine kuu")).to_be_focused()

    page.keyboard.press("Enter")
    expect(panel.locator(".datepicker__title")).to_have_text("august 2026")
    expect(panel.get_by_role("button", name="Järgmine kuu")).to_be_focused()

    panel.get_by_role("button", name="Eelmine kuu").click()
    expect(panel.locator(".datepicker__title")).to_have_text("juuli 2026")
    expect(panel.get_by_role("button", name="Eelmine kuu")).to_be_focused()
    expect(panel).to_be_visible()


def test_escape_closes_the_calendar_and_says_it_handled_the_key(page, create_form):
    panel = _open_on(page, "15.6.2026")
    page.evaluate(
        """() => {
            window.__escapeHandled = null;
            document.addEventListener('keydown', (event) => {
                if (event.key === 'Escape') window.__escapeHandled = event.defaultPrevented;
            });
        }"""
    )
    page.keyboard.press("ArrowRight")
    page.keyboard.press("Escape")
    expect(panel).to_be_hidden()
    expect(page.locator(FIELD)).to_be_focused()
    expect(page.locator(FIELD)).to_have_value("15.6.2026")
    assert page.evaluate("() => window.__escapeHandled") is True


def test_the_date_box_is_named_by_its_label_alone(page, base_url):
    sign_in(page, base_url, SANDRA)
    create_matter(page, base_url, unique_title("Kalendri nimi"))
    open_add_panel(page, "marge-muu")
    form = page.locator("#marge-muu form")

    day = form.locator("[name=occurred_on]")
    expect(day).to_have_accessible_name("Millal? (kohustuslik)")
    expect(form.locator("label .datepicker__trigger")).to_have_count(0)
    expect(form.get_by_role("button", name="Ava kalender")).to_have_count(1)
    # The label still focuses the box it names.
    form.locator("label", has_text="Millal?").click()
    expect(day).to_be_focused()


def test_no_trigger_on_the_creation_form_sits_inside_a_label(page, create_form):
    expect(page.locator(FIELD)).to_have_accessible_name(re.compile(r"^(?!.*Ava kalender)"))
    expect(page.locator("label .datepicker__trigger")).to_have_count(0)
    expect(page.locator(".datepicker__trigger").first).to_be_visible()
