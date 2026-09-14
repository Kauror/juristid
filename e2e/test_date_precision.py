"""Stating a period in a browser, at the precision somebody actually knows.

`tests/test_date_precision_composer.py` proves what gets stored. This file
proves the things that only exist in a browser: that choosing `Kvartal` reveals
a quarter control and hides the day box, that what comes back on the page is the
period and never the anchor, that the launcher above it does not move when a
chip is clicked, that the control is usable at 375px, and that the whole thing
works with JavaScript switched off — which is not a nicety here, because the
control it replaces could not state a precision at all without it
(docs/adr/0079 §1, brief §35).
"""

from __future__ import annotations

from datetime import date, timedelta

import pytest
from playwright.sync_api import expect

from e2e.conftest import (
    MARTIN,
    create_matter,
    open_add_panel,
    open_next_action_form,
    sign_in,
)

pytestmark = pytest.mark.e2e


def _future(days: int) -> str:
    value = date.today() + timedelta(days=days)
    return f"{value.day}.{value.month}.{value.year}"


def choose(page, panel: str, label: str) -> None:
    """Click one `Täpsus` chip, by the word on it.

    Through the visible `<label>` rather than the input: the radio is clipped,
    and a test that clicked the input would be testing something no person can
    reach (docs/adr/0078).
    """
    page.locator(f"{panel} label.precision__chip", has_text=label).first.click()


def save(page, panel: str) -> None:
    page.locator(f"{panel} button[type=submit]").first.click()
    page.wait_for_load_state("networkidle")


# ---------------------------------------------------------------------------
# A + B — Järgmine tegevus, created and then edited
# ---------------------------------------------------------------------------


def test_a_next_action_can_be_stated_as_a_month_and_reads_as_one(page, base_url):
    """§35 A. *oktoober 2026*, chosen and then read back off the page."""
    sign_in(page, base_url, MARTIN)
    create_matter(page, base_url, "Kuu täpsusega järgmine tegevus")

    open_next_action_form(page)
    page.locator("#lisa-jargmine [name='text']").fill("Koosta arvamus")
    choose(page, "#lisa-jargmine", "Kuu")
    page.locator("#lisa-jargmine [name=next_month]").select_option("10")
    page.locator("#lisa-jargmine [name=next_year]").fill("2026")
    save(page, "#lisa-jargmine")

    expect(page.locator(".curact__date")).to_contain_text("oktoober 2026")
    # The anchor, as a day. It is what the database holds and it is not a fact.
    expect(page.locator(".curact")).not_to_contain_text("01.10.2026")


def test_the_edit_path_can_state_a_quarter_on_an_existing_step(page, base_url):
    """§35 B. `Muuda` is the same form, so it must offer the same four chips."""
    sign_in(page, base_url, MARTIN)
    create_matter(page, base_url, "Kvartali täpsusega muudatus")

    open_next_action_form(page)
    page.locator("#lisa-jargmine [name='text']").fill("Koosta arvamus")
    page.locator("#id_target_date").fill(_future(7))
    save(page, "#lisa-jargmine")

    # `Muuda` inside PRAEGUNE TEGEVUS carries the same `#lisa-jargmine` id as
    # the launcher chip does when no step is open, which is what lets one
    # helper open either host (e2e/conftest.py `open_next_action_form`).
    open_next_action_form(page)
    choose(page, "#lisa-jargmine", "Kvartal")
    page.locator("#lisa-jargmine [name=next_quarter]").select_option("4")
    page.locator("#lisa-jargmine [name=next_year]").fill("2026")
    save(page, "#lisa-jargmine")

    expect(page.locator(".curact__date")).to_contain_text("IV kvartal 2026")


# ---------------------------------------------------------------------------
# C + D + E — the three structured facts
# ---------------------------------------------------------------------------


def test_an_important_deadline_can_be_stated_as_a_year(page, base_url):
    """§35 C. `Aasta` is the chip that did not exist before this round."""
    sign_in(page, base_url, MARTIN)
    create_matter(page, base_url, "Aasta täpsusega tähtaeg")

    open_add_panel(page, "lisa-tahtaeg")
    page.locator("#lisa-tahtaeg [name=deadline_title]").fill("Ülevõtmise tähtaeg")
    choose(page, "#lisa-tahtaeg", "Aasta")
    page.locator("#lisa-tahtaeg [name=deadline_year]").fill("2027")
    save(page, "#lisa-tahtaeg")

    body = page.locator("#teema-vaade")
    expect(body).to_contain_text("2027")
    expect(body).not_to_contain_text("01.01.2027")


def test_a_commencement_keeps_its_period_across_a_reload(page, base_url):
    """§35 D. Stored honestly, and still honest on the next page load."""
    sign_in(page, base_url, MARTIN)
    create_matter(page, base_url, "Kvartali täpsusega jõustumine")

    open_add_panel(page, "lisa-joustumine")
    page.locator("#lisa-joustumine [name=effective_title]").fill("Pakendiseaduse muudatused")
    choose(page, "#lisa-joustumine", "Kvartal")
    page.locator("#lisa-joustumine [name=effective_quarter]").select_option("4")
    page.locator("#lisa-joustumine [name=effective_year]").fill("2026")
    save(page, "#lisa-joustumine")

    expect(page.locator("#teema-vaade")).to_contain_text("IV kvartal 2026")

    page.reload()
    page.wait_for_load_state("networkidle")
    expect(page.locator("#teema-vaade")).to_contain_text("IV kvartal 2026")
    expect(page.locator("#teema-vaade")).not_to_contain_text("01.10.2026")


def test_a_work_victory_is_refused_without_a_period_and_accepted_with_one(page, base_url):
    """§35 E. Both halves, in the order a person meets them."""
    sign_in(page, base_url, MARTIN)
    create_matter(page, base_url, "Töövõidu periood")

    open_add_panel(page, "lisa-toovoit")
    page.locator("#lisa-toovoit [name=victory_change]").fill("Üleminekuaeg pikendati")
    save(page, "#lisa-toovoit")

    # Refused, and the panel it was refused in is the panel that reopened.
    expect(page.locator("#lisa-toovoit .field__error")).to_be_visible()
    expect(page.locator("#lisa-toovoit [name=victory_change]")).to_have_value(
        "Üleminekuaeg pikendati"
    )

    choose(page, "#lisa-toovoit", "Aasta")
    page.locator("#lisa-toovoit [name=victory_year]").fill("2026")
    save(page, "#lisa-toovoit")

    expect(page.locator("#teema-vaade")).to_contain_text("Üleminekuaeg pikendati")

    page.reload()
    page.wait_for_load_state("networkidle")
    expect(page.locator("#teema-vaade")).to_contain_text("2026")


# ---------------------------------------------------------------------------
# The control itself
# ---------------------------------------------------------------------------


def test_choosing_a_precision_reveals_its_own_control_and_hides_the_others(page, base_url):
    """The chips are not decoration: each one changes what is being asked.

    A `Kvartal` still showing a `Kuupäev` box would be the old panel with more
    chips — somebody would type a day into it and expect that to be the answer.
    """
    sign_in(page, base_url, MARTIN)
    create_matter(page, base_url, "Täpsuse juhtelement")

    open_add_panel(page, "lisa-tahtaeg")
    day = page.locator('#lisa-tahtaeg .precision__group[data-precision-for="day"]')
    quarter = page.locator('#lisa-tahtaeg .precision__group[data-precision-for="quarter"]')
    year = page.locator('#lisa-tahtaeg .precision__group[data-precision-for="year"]')

    expect(day).to_be_visible()
    expect(quarter).to_be_hidden()

    choose(page, "#lisa-tahtaeg", "Kvartal")
    expect(quarter).to_be_visible()
    expect(year).to_be_visible()
    expect(day).to_be_hidden()

    choose(page, "#lisa-tahtaeg", "Aasta")
    expect(year).to_be_visible()
    expect(quarter).to_be_hidden()


def test_the_chips_are_reachable_and_operable_from_the_keyboard(page, base_url):
    """§30. A radio group, so the browser's own contract applies.

    Asserted through the keys rather than through the markup: `role="tablist"`
    would look equally correct in a DOM dump and would owe a keyboard contract
    nobody had written.
    """
    sign_in(page, base_url, MARTIN)
    create_matter(page, base_url, "Täpsus klaviatuurilt")

    open_add_panel(page, "lisa-tahtaeg")
    first = page.locator('#lisa-tahtaeg input[name="deadline_precision"]').first
    first.focus()
    page.keyboard.press("ArrowRight")

    chosen = page.locator('#lisa-tahtaeg input[name="deadline_precision"]:checked')
    expect(chosen).to_have_value("MONTH")
    expect(
        page.locator('#lisa-tahtaeg .precision__group[data-precision-for="month"]')
    ).to_be_visible()


def test_choosing_a_precision_does_not_move_the_launcher(page, base_url):
    """The regression the previous release was about, one level down.

    `LISA TEEMALE` is a stable choice bar: its chips never change size, line or
    position. This feature puts a second row of chips *inside* one of its
    panels, and a chip that grew when chosen would push the form below it — and,
    if the panel grew, the row above it too (docs/adr/0078).
    """
    sign_in(page, base_url, MARTIN)
    create_matter(page, base_url, "Käivitusriba ei liigu")

    open_add_panel(page, "lisa-tahtaeg")
    launcher = page.locator('label[for="lisa-toovoit-valik"]')
    before = launcher.bounding_box()
    chip = page.locator("#lisa-tahtaeg label.precision__chip", has_text="Kvartal").first
    chip_before = chip.bounding_box()

    choose(page, "#lisa-tahtaeg", "Kvartal")

    after = launcher.bounding_box()
    chip_after = chip.bounding_box()
    assert before == after, f"the launcher moved: {before} -> {after}"
    assert chip_before["width"] == chip_after["width"], "the chosen chip changed width"
    assert chip_before["x"] == chip_after["x"], "the chosen chip moved along its row"


def test_the_precision_control_fits_a_phone(page, base_url):
    """§31. 375px, no horizontal scroll, nothing overlapping."""
    sign_in(page, base_url, MARTIN)
    create_matter(page, base_url, "Täpsus telefonis")
    page.set_viewport_size({"width": 375, "height": 812})

    open_add_panel(page, "lisa-tahtaeg")
    choose(page, "#lisa-tahtaeg", "Kvartal")

    overflow = page.evaluate(
        "() => document.documentElement.scrollWidth - document.documentElement.clientWidth"
    )
    assert overflow <= 0, f"the page scrolls sideways by {overflow}px"

    for locator in (
        page.locator("#lisa-tahtaeg .precision__chips"),
        page.locator('#lisa-tahtaeg .precision__group[data-precision-for="quarter"]'),
    ):
        box = locator.bounding_box()
        assert box["x"] >= 0, box
        assert box["x"] + box["width"] <= 375, box


@pytest.mark.parametrize("javascript", [False], ids=["no-js"])
def test_a_precision_can_be_stated_with_scripting_off(browser, base_url, javascript):
    """§35 H, and the reason the control was rebuilt rather than extended.

    The chips this replaces were `<button type=button>` writing into a hidden
    input. With scripting off they did nothing, so the hidden field kept
    `EXACT` and a reader without JavaScript could record only an exact day —
    the precision was not merely inconvenient to state, it was unstateable.

    A radio group posts on its own. The groups are all visible without
    `:has()`-driven CSS or without CSS at all, which is verbose and correct:
    the server reads only the fields the chosen precision needs.
    """
    context = browser.new_context(java_script_enabled=javascript)
    page = context.new_page()
    try:
        sign_in(page, base_url, MARTIN)
        create_matter(page, base_url, "Täpsus ilma skriptita")

        page.locator('label[for="lisa-tahtaeg-valik"]').click()
        page.locator("#lisa-tahtaeg [name=deadline_title]").fill("Ülevõtmise tähtaeg")
        page.locator("#lisa-tahtaeg label.precision__chip", has_text="Aasta").first.click()
        page.locator("#lisa-tahtaeg [name=deadline_year]").fill("2027")
        page.locator("#lisa-tahtaeg button[type=submit]").first.click()
        page.wait_for_load_state("load")

        expect(page.locator("body")).to_contain_text("Ülevõtmise tähtaeg")
        expect(page.locator("body")).to_contain_text("2027")
    finally:
        context.close()
