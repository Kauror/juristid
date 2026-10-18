"""Escape closes the innermost thing that is open, and only that (JUX-007).

A `LISA TEEMALE` panel closes on Escape (static/js/app.js, the inline-editor
keys), which is right when the cursor is in one of its fields. The 2026-10-17
audit found it also closing when Escape was meant for a popup *inside* the
panel: the organisation suggestions of Lisa tagasiside, or a calendar. The
popup's own handler had already dealt with the key and said so with
`preventDefault()`, and the panel's handler did not ask — so one key shut both,
and the person's typing vanished from view with the panel.

The panel's handler now leaves a key alone once something closer has handled
it. A second Escape, with nothing else open, still closes the panel.
"""

from __future__ import annotations

import pytest
from playwright.sync_api import expect

from e2e.conftest import (
    FEEDBACK_PICKER,
    SANDRA,
    create_matter,
    open_add_panel,
    open_feedback_form,
    sign_in,
    unique_title,
)

pytestmark = pytest.mark.e2e


@pytest.fixture
def matter(page, base_url):
    sign_in(page, base_url, SANDRA)
    create_matter(page, base_url, unique_title("Escape hüpikus"))


def test_escape_in_the_organisation_suggestions_keeps_the_panel_open(page, matter):
    open_feedback_form(page)
    pick = page.locator("#kaasamine-tagasiside-valik")
    expect(pick).to_be_checked()

    search = page.locator(f"#{FEEDBACK_PICKER}-otsi")
    search.click()
    search.fill("Näidis")
    results = page.locator(f"#{FEEDBACK_PICKER}-tulemused")
    expect(results).to_be_visible()

    page.keyboard.press("Escape")
    expect(results).to_be_hidden()
    expect(pick).to_be_checked()
    expect(page.locator("#kaasamine-tagasiside form")).to_be_visible()
    expect(search).to_have_value("Näidis")

    # Nothing else open: the same key now closes the panel, as designed.
    page.keyboard.press("Escape")
    expect(pick).not_to_be_checked()


def test_a_handled_escape_never_closes_the_panel(page, matter):
    """Whatever inside the form handles Escape first, the panel respects it.

    The calendar is the other popup the audit named; its own handler calls
    `preventDefault()` (e2e/test_datepicker_keyboard.py). This drives the
    contract directly, so it holds for any popup that follows it.
    """
    open_add_panel(page, "marge-muu")
    pick = page.locator("#marge-muu-valik")
    expect(pick).to_be_checked()
    description = page.locator("#marge-muu form [name=description]")
    description.fill("Sisestatud tekst jääb nähtavale.")
    description.evaluate(
        """box => box.addEventListener('keydown', (event) => {
            if (event.key === 'Escape') event.preventDefault();
        })"""
    )
    description.press("Escape")
    expect(pick).to_be_checked()
    expect(description).to_be_visible()
