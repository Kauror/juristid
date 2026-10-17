"""`Kohtumine / arutelu` in a browser (docs/adr/0097, amendment of 2026-10-17).

The opinion family is `+ Koja arvamus / kohtumine`; its second sub-choice opens
from its chip and from the keyboard, refuses a save without a summary while
keeping what was typed, and saves a stated day (`Kuupäev`, empty until filled),
a summary and two files as one «Kohtumine» row in `Teema käik` on that day, with
the files linked and listed under `Dokumendid`. `PRAEGUNE TEGEVUS` reads as it
did. `Koja arvamus` is still the family's default and still opens.

State is read off the page only (e2e/conftest.py).
"""

from __future__ import annotations

from datetime import date, timedelta

import pytest
from playwright.sync_api import expect

from e2e.conftest import (
    SANDRA,
    add_panel_chip,
    create_matter,
    open_add_panel,
    sign_in,
    wait_for_htmx,
)
from e2e.titles import unique_title

pytestmark = pytest.mark.e2e

PDF = b"%PDF-1.4 synthetic meeting material"


def _et(on: date) -> str:
    return f"{on.day}.{on.month}.{on.year}"


def _save(page, form) -> int:
    with page.expect_response(
        lambda r: r.url.endswith("/lisa/kohtumine/") and r.request.method == "POST"
    ) as answer:
        form.get_by_role("button", name="Salvesta", exact=True).click()
    wait_for_htmx(page)
    return answer.value.status


def test_the_family_is_renamed_and_koja_arvamus_is_still_its_default(page, base_url):
    sign_in(page, base_url, SANDRA)
    create_matter(page, base_url, unique_title("Kohtumise pere"))

    chip = page.locator('label[for="lisa-arvamus-valik"]')
    expect(chip).to_have_text("+ Koja arvamus / kohtumine")
    chip.click()
    family = page.locator("#lisa-arvamus")
    expect(family.locator("label.disclosure-chip")).to_have_text(
        ["Koja arvamus", "Kohtumine / arutelu"]
    )
    # `Koja arvamus` opens with the family and is the one chosen.
    expect(page.locator("#arvamus-koja-valik")).to_be_checked()
    expect(page.locator("#arvamus-koja form").first).to_be_visible()
    expect(page.locator("#arvamus-kohtumine form")).to_be_hidden()

    # And it still works as it did: the chip reopens it from the meeting panel.
    open_add_panel(page, "arvamus-kohtumine")
    expect(page.locator("#arvamus-koja form").first).to_be_hidden()
    open_add_panel(page, "arvamus-koja")
    expect(page.locator("#arvamus-koja form").first).to_be_visible()
    expect(add_panel_chip(page, "arvamus-koja")).to_be_visible()


def test_a_meeting_opens_from_the_keyboard_and_saves_on_its_day(page, base_url):
    sign_in(page, base_url, SANDRA)
    create_matter(page, base_url, unique_title("Kohtumine ministeeriumiga"))
    work_before = page.locator("#praegune-tegevus").inner_text()

    # Keyboard: the family first, then the meeting radio with Space.
    open_add_panel(page, "lisa-arvamus")
    radio = page.locator("#arvamus-kohtumine-valik")
    radio.focus()
    page.keyboard.press("Space")
    form = page.locator("#arvamus-kohtumine form")
    expect(form).to_be_visible()
    expect(radio).to_be_focused()
    # Tab moves into the form: its first field is reachable without a mouse.
    day = form.locator('[name="occurred_on"]')
    expect(day).to_have_value("")
    day.focus()
    expect(day).to_be_focused()

    # A day without a summary: the server refuses it in Estonian under the box
    # (no browser bubble, as on every other panel), and the day stays typed.
    past = date.today() - timedelta(days=9)
    day.fill(_et(past))
    assert _save(page, form) == 400
    form = page.locator("#arvamus-kohtumine form")
    expect(form.locator("#id_kohtumine_summary_error")).to_have_text(
        "Kirjuta kohtumise lühikokkuvõte."
    )
    day = form.locator('[name="occurred_on"]')
    summary = form.locator('[name="summary"]')
    expect(day).to_have_value(_et(past))

    # A summary that is empty once sanitised is refused the same way.
    summary.fill("<script>alert(1)</script>")
    assert _save(page, form) == 400
    form = page.locator("#arvamus-kohtumine form")
    expect(form).to_be_visible()
    expect(form.locator("#id_kohtumine_summary_error")).to_have_text(
        "Kirjuta kohtumise lühikokkuvõte."
    )
    day = form.locator('[name="occurred_on"]')
    summary = form.locator('[name="summary"]')
    expect(day).to_have_value(_et(past))

    # The real save: day, summary, two files.
    summary.fill("Kohtumine ministeeriumiga: osalesid Mari ja Jüri, lepiti kokku ajakava.")
    form.locator('input[type="file"]').set_input_files(
        [
            {"name": "kohtumine-1.pdf", "mimeType": "application/pdf", "buffer": PDF + b"1"},
            {"name": "kohtumine-2.pdf", "mimeType": "application/pdf", "buffer": PDF + b"2"},
        ]
    )
    assert _save(page, form) == 200

    page.reload()
    page.wait_for_load_state("networkidle")
    row = page.locator("article.uxtl__item", has_text="Kohtumine ministeeriumiga: osalesid")
    expect(row).to_have_count(1)
    expect(row.locator(".uxtl__kind", has_text="Kohtumine")).to_have_count(1)
    expect(row.locator("time.uxtl__time").first).to_have_text(_et(past))
    expect(row.locator("a.uxtl__file", has_text="kohtumine-1.pdf")).to_have_count(1)
    expect(row.locator("a.uxtl__file", has_text="kohtumine-2.pdf")).to_have_count(1)
    # No task, no stage: the current-work zone reads as it did.
    assert page.locator("#praegune-tegevus").inner_text() == work_before
    # Rows are newest first, and this one sits by its own day.
    days = page.locator("#ajalugu-loend article.uxtl__item time.uxtl__time").evaluate_all(
        "els => els.map(e => e.getAttribute('datetime').slice(0, 10))"
    )
    assert days == sorted(days, reverse=True), days
    assert past.isoformat() in days, days

    # The files are documents of the Teema.
    page.goto(page.url.rstrip("/") + "/dokumendid/")
    page.wait_for_load_state("networkidle")
    expect(page.get_by_text("kohtumine-1.pdf").first).to_be_visible()
    expect(page.get_by_text("kohtumine-2.pdf").first).to_be_visible()
