"""Minu statistika in a browser (FUNC-003, docs/adr/0223 §11).

The crediting and coverage rules are proved in `tests/test_my_statistics`. Here:
the foldout leads to the page, the period bar steps back and the URL keeps it,
a card selects the chart and the records, and the ⓘ opens its definition.
"""

from __future__ import annotations

import re

import pytest
from playwright.sync_api import expect

from e2e.conftest import SANDRA, sign_in

pytestmark = pytest.mark.e2e


def test_the_foldout_leads_to_ones_own_statistics_for_a_period(page, base_url):
    sign_in(page, base_url, SANDRA)

    page.get_by_role("link", name="Kõik minu statistika →").click()
    page.wait_for_url(f"{base_url}/minu-asjad/statistika/")
    expect(page.get_by_role("heading", name="Minu statistika")).to_be_visible()
    expect(page.locator(".mystats__periodlabel")).to_have_text("See nädal")
    expect(page.locator(".mystats__card")).to_have_count(13)
    expect(page.locator(".mystats__card.is-selected")).to_have_count(1)

    # ‹ steps one week back, and the URL keeps the period.
    page.get_by_role("link", name="Eelmine periood").click()
    page.wait_for_url(re.compile(r"periood=nadal&algus="))
    expect(page.locator(".mystats__periodlabel")).to_have_text("Eelmine nädal")

    # A card selects the chart and the list below it.
    page.locator('[data-figure="sissekanded"] .mystats__cardlink').click()
    page.wait_for_url(re.compile(r"naitaja=sissekanded"))
    expect(page.locator('[data-figure="sissekanded"]')).to_have_class(re.compile("is-selected"))
    expect(page.locator("#mystats-joonis")).to_have_text("Sissekanded")
    expect(page.locator(".mystats__cols .statcol")).to_have_count(7)

    # A present-state figure in a past week is not invented.
    expect(page.locator('[data-figure="ule-tahtaja"] .mystats__value')).to_have_text("—")

    # The ⓘ opens its one-sentence definition.
    page.get_by_role("button", name="Selgitus: Sissekanded").click()
    expect(page.locator("#mystats-selgitus-sissekanded")).to_be_visible()
