"""The workflow-consistency alert in a browser (docs/adr/0161).

One walk: a new Teema suggests «Tutvu materjaliga»; `Alusta` makes it the
current step and no alert is drawn; a `Kaasamine` filed through `LISA TEEMALE`
proves the reading done, and the alert appears under the step — once, with
its reason; `Muuda` («the work goes on») sets the step anew and the alert goes,
while the step stays the current one.
"""

from __future__ import annotations

import re
from datetime import date, timedelta

import pytest
from playwright.sync_api import expect

from e2e.conftest import SANDRA, open_add_panel, set_next_step, sign_in, unique_title, wait_for_htmx

pytestmark = pytest.mark.e2e

ALERT = "[data-consistency-alert]"


def test_a_step_the_record_shows_done_is_flagged_and_muuda_clears_it(page, base_url):
    sign_in(page, base_url, SANDRA)
    page.goto(f"{base_url}/teemad/uus/")
    page.fill("#id_title", unique_title("Kooskõla brauserikatse"))
    page.get_by_role("button", name="Salvesta", exact=True).click()
    page.wait_for_url(re.compile(r"/teemad/[0-9a-f-]{36}/$"))
    page.wait_for_load_state("networkidle")

    zone = page.locator("#praegune-tegevus")
    zone.locator("#alusta-samm > summary").click()
    zone.locator("#alusta-samm button[type=submit]").click()
    wait_for_htmx(page)
    expect(zone.locator(".curact__text")).to_have_text("Tutvu materjaliga")
    expect(page.locator(ALERT)).to_have_count(0)

    # A consultation filed through `LISA TEEMALE`, not from the step.
    open_add_panel(page, "kaasamine-alusta")
    panel = page.locator("#kaasamine-alusta")
    panel.locator("[name=audience]").fill("kooskõla proov")
    panel.locator("button[type=submit]").click()
    page.wait_for_load_state("networkidle")

    alert = page.locator(f"#praegune-tegevus {ALERT}")
    expect(alert).to_have_count(1)
    expect(alert).to_contain_text("Praegune tegevus võib olla juba tehtud")
    expect(alert).to_contain_text("teemale lisatud kaasamine")
    expect(page.locator(ALERT)).to_have_count(1)
    # Nothing was completed by it.
    expect(zone.locator(".curact__text")).to_have_text("Tutvu materjaliga")

    # `Muuda`: the work goes on, said again after the record.
    day = (date.today() + timedelta(days=5)).strftime("%d.%m.%Y")
    set_next_step(page, "Tutvu materjaliga ja kaasamise vastustega", day)
    expect(page.locator("#praegune-tegevus .curact__text")).to_have_text(
        "Tutvu materjaliga ja kaasamise vastustega"
    )
    expect(page.locator(ALERT)).to_have_count(0)
