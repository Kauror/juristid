"""`Soovitatud järgmisena` in a browser (docs/adr/0141).

One walk through the flow the Teema page keeps after the visible `Tööplaan`
went: a new Teema suggests its first step; `Alusta` makes it the current step
and the suggestion goes; `✓ Tehtud` finishes it and the next is suggested; `×`
dismisses that one for good, a reload keeps it dismissed, and the one after it
is suggested.
"""

from __future__ import annotations

import re

import pytest
from playwright.sync_api import expect

from e2e.conftest import SANDRA, finish_current_action, sign_in, unique_title, wait_for_htmx

pytestmark = pytest.mark.e2e


def _suggestion(page):
    return page.locator("#praegune-tegevus #soovitus .curact__suggesttext")


def test_a_suggestion_is_started_finished_and_the_next_dismissed(page, base_url):
    sign_in(page, base_url, SANDRA)
    page.goto(f"{base_url}/teemad/uus/")
    page.fill("#id_title", unique_title("Soovituse brauserikatse"))
    page.get_by_role("button", name="Loo teema").click()
    page.wait_for_url(re.compile(r"/teemad/[0-9a-f-]{36}/$"))
    page.wait_for_load_state("networkidle")

    zone = page.locator("#praegune-tegevus")
    expect(page.locator("#tooplaan")).to_have_count(0)
    expect(zone).to_contain_text("Soovitatud järgmisena")
    expect(_suggestion(page)).to_have_text("Tutvu materjaliga")
    expect(zone.get_by_role("button", name="Eemalda soovitus")).to_be_visible()

    # `Alusta`: the step's own words, no day.
    zone.locator("#alusta-samm > summary").click()
    zone.locator("#alusta-samm button[type=submit]").click()
    wait_for_htmx(page)
    expect(zone.locator(".curact__text")).to_have_text("Tutvu materjaliga")
    expect(zone.locator("#soovitus")).to_have_count(0)

    # `✓ Tehtud`: the next suggestion follows.
    finish_current_action(page, "Lugesin materjali läbi.")
    expect(_suggestion(page)).to_have_text("Koosta kodulehe ülevaade")

    # `×`: gone for good, and the one after it is suggested.
    zone.get_by_role("button", name="Eemalda soovitus").click()
    wait_for_htmx(page)
    expect(_suggestion(page)).to_have_text("Kaasa liikmeid / küsi tagasisidet")
    page.reload()
    page.wait_for_load_state("networkidle")
    expect(_suggestion(page)).to_have_text("Kaasa liikmeid / küsi tagasisidet")
    expect(page.locator("#praegune-tegevus")).not_to_contain_text("Koosta kodulehe ülevaade")

    # The manual way is always there. Leave the Teema with an open step, as
    # every file this suite creates must (e2e/conftest.py `give_first_step`).
    expect(page.locator("#praegune-tegevus")).to_contain_text("+ Lisa tegevus")
    page.locator("#praegune-tegevus #alusta-samm > summary").click()
    page.locator("#praegune-tegevus #alusta-samm button[type=submit]").click()
    wait_for_htmx(page)
    expect(page.locator("#praegune-tegevus .curact__text")).to_have_text(
        "Kaasa liikmeid / küsi tagasisidet"
    )
