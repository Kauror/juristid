"""Reopening a workspace panel across the save that replaces it.

`#lisa-jargmine` is two controls, not one: while no step is open it is the
`+ Järgmine tegevus` chip in `LISA TEEMALE`, and once a step exists it is the
`Muuda` disclosure in PRAEGUNE TEGEVUS. A successful save crosses exactly
between them — the chip and its panel go, the disclosure arrives closed — and
`e2e/conftest.py::open_add_panel` is the one helper that has to survive the
crossing.

**What this pins is the helper's synchronisation, not the product.** The save
below is deliberately *not* awaited: `open_next_action_form` is called while the
POST is still on the wire, which is the state a
`wait_for_load_state("networkidle")` on a loaded CI runner can leave a test in
and the state that made
`test_date_precision.py::test_the_edit_path_can_state_a_quarter_on_an_existing_step`
fail once on an exact-main run and pass on a rerun. Against the previous helper
this scenario reproduces that failure every time: it read the doomed panel,
called it open, returned in 0.04s, and the replacement then arrived closed — a
correctly rendered workspace with the form hidden inside it.

The journey itself — edit, quarter, save, reopen, read back — is
`e2e/test_date_precision.py`, and is deliberately not repeated here.
"""

from __future__ import annotations

from datetime import date, timedelta

import pytest
from playwright.sync_api import expect

from e2e.conftest import MARTIN, create_matter, open_next_action_form, sign_in

pytestmark = pytest.mark.e2e


def test_the_next_action_panel_reopens_while_its_save_is_still_in_flight(page, base_url):
    sign_in(page, base_url, MARTIN)
    create_matter(page, base_url, "Paneel avaneb salvestuse järel")

    open_next_action_form(page)
    page.locator("#lisa-jargmine [name='text']").fill("Koosta arvamus")
    target = date.today() + timedelta(days=7)
    page.locator("#id_target_date").fill(f"{target.day}.{target.month}.{target.year}")
    # No wait of any kind after the click. The helper is being asked to open a
    # panel that the response now on its way is about to replace.
    page.locator("#lisa-jargmine button[type=submit]").first.click()

    open_next_action_form(page)

    # The host it crossed to, open, with the form a person would now type into.
    edit = page.locator("details#lisa-jargmine")
    expect(edit).to_have_count(1)
    assert edit.evaluate("node => node.open"), "the Muuda disclosure came back shut"
    expect(page.locator("#lisa-jargmine [name='text']")).to_have_value("Koosta arvamus")
    expect(
        page.locator("#lisa-jargmine label.precision__chip", has_text="Kvartal").first
    ).to_be_visible()
