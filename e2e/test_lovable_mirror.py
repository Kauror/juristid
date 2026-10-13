"""The Lovable mirror preview in a real browser (docs/adr/0157).

The seeded world imports no Lovable snapshot — no export of any kind belongs in
the browser fixtures — so what a browser can prove here is the gate and the
empty page: the administrator reaches it and reads the coverage and mapping,
a lawyer gets the 404 every internal tool answers with, and the page offers
nothing to press.
"""

from __future__ import annotations

import pytest
from playwright.sync_api import expect

from e2e.conftest import ADMIN, SANDRA, sign_in

pytestmark = pytest.mark.e2e

URL = "/haldus/lovable/"


def test_the_administrator_opens_the_mirror_and_it_is_read_only(page, base_url):
    sign_in(page, base_url, ADMIN)
    page.goto(f"{base_url}{URL}")

    expect(page.get_by_role("heading", level=1, name="Lovable'i peegel")).to_be_visible()
    expect(page.get_by_text("Ühtki Lovable'i hetktõmmist ei ole imporditud.")).to_be_visible()
    expect(page.get_by_text("Otsevärskendus Lovable'ist on välja lülitatud")).to_be_visible()
    expect(page.get_by_role("heading", name="Väljade kaardistus")).to_be_visible()
    expect(page.locator("main form")).to_have_count(0)
    expect(page.locator("main button")).to_have_count(0)


def test_a_lawyer_gets_a_404_for_the_mirror(page, base_url):
    sign_in(page, base_url, SANDRA)
    response = page.goto(f"{base_url}{URL}")
    assert response is not None and response.status == 404
