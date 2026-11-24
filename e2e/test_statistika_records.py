"""Statistika's record cards open what explains them (UX-008, docs/adr/0214 §7).

The links themselves — each tied period, the lawyer and the trend range carried
— are proved against the database in `tests/test_statistika_chart_spec_records`.
What only a browser shows is that a card is one click target that lands on the
period whose figure equals the record.
"""

from __future__ import annotations

import re

import pytest
from playwright.sync_api import expect

from e2e.conftest import MARTIN, sign_in

pytestmark = pytest.mark.e2e


def test_the_record_year_card_opens_that_year_and_its_count_matches(page, base_url):
    sign_in(page, base_url, MARTIN)
    page.goto(f"{base_url}/statistika/")

    card = page.locator("a.statrecord").filter(has_text="Välja saadetud arvamuste rekordaasta")
    expect(card).to_have_count(1)
    record = int(re.match(r"\d+", card.locator(".statrecord__value").inner_text()).group())
    year = card.locator(".statrecord__when").inner_text().split(",")[0].strip()

    card.click()

    page.wait_for_url(re.compile(rf"periood=aasta&aasta={year}.*#stat-kuud$"))
    tile = page.locator(".stattile").filter(has_text="Välja saadetud arvamused")
    expect(tile.locator(".stattile__value")).to_have_text(str(record))
    expect(page.locator("#stat-kuud")).to_be_in_viewport()
