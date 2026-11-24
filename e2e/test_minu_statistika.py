"""Minu statistika in a browser (FUNC-003, docs/adr/0223).

The crediting rules are proved against hand-built trails and the database in
`tests/test_my_statistics`. Here: the foldout on one's own desk leads to the
page, and a month the trail cannot speak for reads «andmed puuduvad».
"""

from __future__ import annotations

import pytest
from playwright.sync_api import expect

from e2e.conftest import SANDRA, sign_in

pytestmark = pytest.mark.e2e


def test_the_foldout_leads_to_ones_own_statistics(page, base_url):
    sign_in(page, base_url, SANDRA)

    page.get_by_role("link", name="Kõik minu statistika →").click()

    page.wait_for_url(f"{base_url}/minu-asjad/statistika/")
    expect(page.get_by_role("heading", name="Minu statistika")).to_be_visible()
    expect(page.locator(".stattile")).to_have_count(3)
    expect(page.locator(".mystats__table tbody tr")).to_have_count(12)
    expect(page.locator(".mystats__unknown").first).to_have_text("andmed puuduvad")
