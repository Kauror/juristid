"""Dates, in a browser, because that is the only place the defect existed.

A native `<input type="date">` renders in the *browser's* locale. On the machine
this software is built on it looked Estonian; on a US-English Windows the same
page offered `mm/dd/yyyy` and read `7.9.2026` as the 9th of July. No server-side
assertion can see that, and no Python test can — which is why the control is
gone and why these tests drive the real one.

The browser context is `et-EE` (e2e/conftest.py), so a control that still
deferred to the browser would *pass* a naive check here. Every assertion below
is therefore about the markup and behaviour the application controls: the input
type, the placeholder, the order of the weekday headings, and what lands in the
box after a click.
"""

from __future__ import annotations

import re
from datetime import date, timedelta

import pytest
from playwright.sync_api import expect

from e2e.conftest import MARTIN, sign_in

pytestmark = pytest.mark.e2e

#: What must never appear on an Estonian screen.
US_PLACEHOLDER = "mm/dd/yyyy"
#: Monday first, Sunday last. The order is the assertion — a calendar that
#: starts on Sunday puts every date one column out and is read wrong at a
#: glance by everybody who grew up with a Monday week.
ESTONIAN_WEEK = ["E", "T", "K", "N", "R", "L", "P"]
#: `7.9.2026`, not `07.09.2026` and not `2026-09-07`.
ESTONIAN_DATE = re.compile(r"^\d{1,2}\.\d{1,2}\.\d{4}$")


def typed(days: int, *, padded: bool = False) -> str:
    """A date `days` from today, written the way a lawyer types one.

    Relative rather than a literal, and that is not tidiness. These two tests
    persist a Matter, so whatever they type into `Arvamuse tähtaeg` becomes part
    of the world every other test in this suite reads — and since that field
    became real deadline work, a literal that has quietly slipped into the past
    makes a Matter genuinely overdue and moves the department's *üle tähtaja*
    figure (app/matters/work_items.py). `23.8.2026` was in the future when it
    was written here and was not by the time it mattered.
    """
    on = date.today() + timedelta(days=days)
    return f"{on.day:02d}.{on.month:02d}.{on.year}" if padded else f"{on.day}.{on.month}.{on.year}"


def create_form(page, base_url) -> None:
    """Open Uus teema.

    Nothing to reveal afterwards. `Arvamuse tähtaeg` used to live inside a
    closed `<details>`, so every test below opened it before it could type into
    it; the redesign put the whole form on screen and the helper that opened
    the panel went with it (Uus teema redesign §3).
    """
    page.goto(f"{base_url}/teemad/uus/")
    page.wait_for_load_state("networkidle")


def picker_for(page, field_id: str):
    """The calendar button belonging to one date box.

    `app.js` wraps each input in a `.datepicker` span at load, so the input's
    parent is the wrapper that also holds the trigger and the panel.
    """
    return page.locator(field_id).locator("xpath=..")


# ---------------------------------------------------------------------------
# The control itself
# ---------------------------------------------------------------------------


def test_no_date_box_on_the_creation_form_is_a_native_date_input(page, base_url):
    """The assertion that would have caught the production defect."""
    sign_in(page, base_url, MARTIN)
    create_form(page, base_url)
    expect(page.locator('.createform input[type="date"]')).to_have_count(0)


def test_every_date_box_promises_the_estonian_format(page, base_url):
    sign_in(page, base_url, MARTIN)
    create_form(page, base_url)

    boxes = page.locator(".createform .dateinput")
    expect(boxes.first).to_be_visible()
    for index in range(boxes.count()):
        placeholder = boxes.nth(index).get_attribute("placeholder")
        assert placeholder == "pp.kk.aaaa", placeholder


def test_the_us_placeholder_appears_nowhere_on_the_page(page, base_url):
    sign_in(page, base_url, MARTIN)
    create_form(page, base_url)
    assert US_PLACEHOLDER not in page.content()


def test_the_prefilled_arrival_date_is_written_the_estonian_way(page, base_url):
    """Saabus defaults to today, which is the one date rendered on load."""
    sign_in(page, base_url, MARTIN)
    create_form(page, base_url)

    value = page.locator("#id_received_date").input_value()
    assert ESTONIAN_DATE.match(value), value
    assert not value.startswith("20"), f"{value} is ISO, not Estonian"


# ---------------------------------------------------------------------------
# The calendar
# ---------------------------------------------------------------------------


def test_the_calendar_starts_the_week_on_monday(page, base_url, screenshots):
    sign_in(page, base_url, MARTIN)
    create_form(page, base_url)

    picker_for(page, "#id_received_date").locator(".datepicker__trigger").click()
    panel = picker_for(page, "#id_received_date").locator(".datepicker__panel")
    expect(panel).to_be_visible()
    screenshots(page, "kuupaevavalija")

    assert panel.locator(".datepicker__weekday").all_inner_texts() == ESTONIAN_WEEK


def test_the_calendar_names_the_month_in_estonian(page, base_url):
    """Month names come from the application, not from `toLocaleDateString`.

    A browser with no Estonian locale data would otherwise render "September"
    on an otherwise Estonian panel.
    """
    sign_in(page, base_url, MARTIN)
    create_form(page, base_url)

    picker_for(page, "#id_received_date").locator(".datepicker__trigger").click()
    title = picker_for(page, "#id_received_date").locator(".datepicker__title").inner_text().lower()
    months = (
        "jaanuar veebruar märts aprill mai juuni juuli august september oktoober november detsember"
    ).split()
    assert any(month in title for month in months), title


def test_picking_a_day_writes_an_estonian_date_into_the_box(page, base_url):
    sign_in(page, base_url, MARTIN)
    create_form(page, base_url)

    picker = picker_for(page, "#id_response_deadline")
    picker.locator(".datepicker__trigger").click()
    panel = picker.locator(".datepicker__panel")
    expect(panel).to_be_visible()

    panel.locator(".datepicker__day", has_text=re.compile(r"^15$")).first.click()
    value = page.locator("#id_response_deadline").input_value()
    assert ESTONIAN_DATE.match(value), value
    assert value.startswith("15."), value


def test_the_calendar_survives_month_navigation(page, base_url):
    """The reported defect: one click on ‹ or › and the calendar vanished.

    `buildCalendar` empties the panel before redrawing it, which detaches the
    very button that was clicked. The click then reached the document, where the
    outside-close check asked whether the wrapper still contained
    `event.target` — and a detached node is contained by nothing, so the panel
    closed the instant it had been rebuilt.

    Only a browser can see this: the handler order, the detachment and the
    bubbling are all real DOM behaviour (Teema QA §6).
    """
    sign_in(page, base_url, MARTIN)
    create_form(page, base_url)

    picker = picker_for(page, "#id_received_date")
    picker.locator(".datepicker__trigger").click()
    panel = picker.locator(".datepicker__panel")
    expect(panel).to_be_visible()

    first = panel.locator(".datepicker__title").inner_text()

    # Forward, and the panel is still open on a different month.
    panel.get_by_role("button", name="Järgmine kuu").click()
    expect(panel).to_be_visible()
    assert panel.locator(".datepicker__title").inner_text() != first

    # Back twice, still open, and now a month before where it started.
    panel.get_by_role("button", name="Eelmine kuu").click()
    panel.get_by_role("button", name="Eelmine kuu").click()
    expect(panel).to_be_visible()
    assert panel.locator(".datepicker__title").inner_text() != first

    # And a day in the navigated month is still pickable, which is the thing
    # the defect actually prevented.
    panel.locator(".datepicker__day", has_text=re.compile(r"^12$")).first.click()
    expect(panel).to_be_hidden()
    assert page.locator("#id_received_date").input_value().startswith("12.")


def test_clicking_outside_still_closes_the_calendar(page, base_url):
    """The behaviour the fix must not have removed.

    One delegated document listener replaced one listener per input, and it
    reads containment from the event's composed path rather than from the live
    tree. Both halves have to hold: navigating stays inside, and clicking the
    page genuinely outside still closes.
    """
    sign_in(page, base_url, MARTIN)
    create_form(page, base_url)

    picker = picker_for(page, "#id_received_date")
    picker.locator(".datepicker__trigger").click()
    panel = picker.locator(".datepicker__panel")
    expect(panel).to_be_visible()

    page.locator("#id_title").click()
    expect(panel).to_be_hidden()


def test_the_calendar_closes_on_escape(page, base_url):
    """A floating panel that stays open until its own button is clicked again
    is the disclosure people report as stuck."""
    sign_in(page, base_url, MARTIN)
    create_form(page, base_url)

    picker = picker_for(page, "#id_response_deadline")
    picker.locator(".datepicker__trigger").click()
    panel = picker.locator(".datepicker__panel")
    expect(panel).to_be_visible()

    page.keyboard.press("Escape")
    expect(panel).to_be_hidden()


# ---------------------------------------------------------------------------
# Typing, which is what a keyboard user does instead
# ---------------------------------------------------------------------------


def test_a_typed_estonian_date_is_saved(page, base_url):
    sign_in(page, base_url, MARTIN)
    create_form(page, base_url)

    # Saabus stays the literal it always was. Only `Arvamuse tähtaeg` had to
    # move: it is the field that is now deadline work, and a received date is
    # not. Making this relative as well shifted a month row on Statistika,
    # which is a page this change has no business touching.
    deadline = typed(21)
    page.fill("#id_title", "Eestikeelse kuupäevaga teema")
    page.fill("#id_received_date", "7.9.2026")
    page.fill("#id_response_deadline", deadline)
    page.get_by_role("button", name="Loo teema").click()
    page.wait_for_load_state("networkidle")

    # The Matter page shows both dates back, in the same format they were typed.
    # Saabus is a rail fact now and the one active deadline is in the header
    # meta line. Both are dates the page renders in Estonian, which is what
    # this is about (Teema redesign §5.4, §22.1).
    body = page.locator(".railcard__value--date, .metaline__value--deadline").all_inner_texts()
    assert any("7.9.2026" in text for text in body), body
    assert any(deadline in text for text in body), body


def test_a_padded_estonian_date_is_accepted_too(page, base_url):
    sign_in(page, base_url, MARTIN)
    create_form(page, base_url)

    page.fill("#id_title", "Nullidega kuupäevaga teema")
    # Eight days, which is where the literal `07.09.2026` sat and is what keeps
    # this Matter inside Ülevaade's «arvamuse tähtaega 14 päeva jooksul»
    # window. That figure reads `Matter.response_deadline` directly and has
    # nothing to do with this change; moving the date out of its window altered
    # a number on a page for no reason anybody could later explain.
    page.fill("#id_response_deadline", typed(8, padded=True))
    page.get_by_role("button", name="Loo teema").click()
    page.wait_for_load_state("networkidle")

    assert typed(8) in " ".join(
        page.locator(".railcard__value--date, .metaline__value--deadline").all_inner_texts()
    )


def test_an_impossible_date_is_refused_in_estonian(page, base_url):
    """31.02 is somebody mistyping, and the 28th is not what they meant."""
    sign_in(page, base_url, MARTIN)
    create_form(page, base_url)

    page.fill("#id_title", "Võimatu kuupäevaga teema")
    page.fill("#id_response_deadline", "31.02.2026")
    page.get_by_role("button", name="Loo teema").click()
    page.wait_for_load_state("networkidle")

    expect(page.locator(".field__error").first).to_contain_text("7.9.2026")
    # And what was typed is still on screen: a blank box under "correct this"
    # loses the input while the message says to fix it.
    assert page.locator("#id_response_deadline").input_value() == "31.02.2026"


# ---------------------------------------------------------------------------
# The register's own date filters
# ---------------------------------------------------------------------------


def test_the_register_filters_take_estonian_dates(page, base_url):
    sign_in(page, base_url, MARTIN)
    page.goto(f"{base_url}/teemad/?tahtaeg_alates=1.1.2020")
    page.wait_for_load_state("networkidle")

    expect(page.locator(".filterchip").filter(has_text="Tähtaeg alates").first).to_contain_text(
        "1.1.2020"
    )


def test_an_older_iso_link_still_works_and_still_reads_estonian(page, base_url):
    """Links written before the date system was Estonian carry ISO.

    They keep working, and the chip above the results reads the one way this
    application writes a date.
    """
    sign_in(page, base_url, MARTIN)
    page.goto(f"{base_url}/teemad/?tahtaeg_alates=2020-01-01")
    page.wait_for_load_state("networkidle")

    expect(page.locator(".filterchip").filter(has_text="Tähtaeg alates").first).to_contain_text(
        "1.1.2020"
    )


def test_the_register_filter_boxes_are_not_native_date_inputs(page, base_url):
    sign_in(page, base_url, MARTIN)
    page.goto(f"{base_url}/teemad/")
    page.wait_for_load_state("networkidle")
    expect(page.locator('input[type="date"]')).to_have_count(0)


# ---------------------------------------------------------------------------
# Narrow windows
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("width", [1024, 420])
def test_the_calendar_stays_inside_the_window(page, base_url, width):
    """A panel that overflows the viewport is a panel with unreachable days."""
    sign_in(page, base_url, MARTIN)
    page.set_viewport_size({"width": width, "height": 900})
    create_form(page, base_url)

    picker = picker_for(page, "#id_received_date")
    picker.locator(".datepicker__trigger").click()
    panel = picker.locator(".datepicker__panel")
    expect(panel).to_be_visible()

    box = panel.bounding_box()
    assert box is not None
    assert box["x"] >= 0, box
    assert box["x"] + box["width"] <= width + 1, box
