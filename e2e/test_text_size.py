"""The text size in a real browser (UX-002 B2, docs/adr/0221).

Haldus → Isiklikud seaded → Kuvamine offers Väike, Tavaline, Suur and Väga
suur. The choice is this browser's (localStorage), is on <html> before the
first paint of every later page, scales the type and nothing else, and
Tavaline is the existing page exactly.
"""

from __future__ import annotations

import pytest
from playwright.sync_api import expect

from e2e.conftest import SANDRA, document_overflows, sign_in

pytestmark = pytest.mark.e2e

KUVAMINE = "/haldus/seaded/"


def _body_size(page) -> str:
    return page.evaluate("() => getComputedStyle(document.body).fontSize")


def test_tavaline_is_the_default_and_the_existing_size(page, base_url):
    sign_in(page, base_url, SANDRA)
    page.goto(f"{base_url}{KUVAMINE}")
    assert page.evaluate("() => document.documentElement.getAttribute('data-text-size')") is None
    assert _body_size(page) == "13px"
    expect(page.locator('[data-text-size-choice="tavaline"]')).to_have_attribute(
        "aria-pressed", "true"
    )


@pytest.mark.parametrize(
    "value, label, body",
    [("vaike", "Väike", "12px"), ("suur", "Suur", "15px"), ("vaga-suur", "Väga suur", "17px")],
)
def test_a_size_is_chosen_kept_and_applied_on_the_next_page(page, base_url, value, label, body):
    sign_in(page, base_url, SANDRA)
    page.goto(f"{base_url}{KUVAMINE}")

    page.get_by_role("button", name=label, exact=True).click()

    expect(page.locator("html")).to_have_attribute("data-text-size", value)
    expect(page.get_by_role("button", name=label, exact=True)).to_have_attribute(
        "aria-pressed", "true"
    )
    assert _body_size(page) == body

    page.goto(f"{base_url}/teemad/")
    # Set by the head script before <body> was parsed, not after load.
    expect(page.locator("html")).to_have_attribute("data-text-size", value)
    assert _body_size(page) == body

    page.goto(f"{base_url}{KUVAMINE}")
    page.get_by_role("button", name="Tavaline", exact=True).click()
    assert page.evaluate("() => document.documentElement.getAttribute('data-text-size')") is None
    assert page.evaluate("() => window.localStorage.getItem('juristid-text-size')") is None
    assert _body_size(page) == "13px"


def test_the_largest_size_scrolls_no_page_sideways_at_320(page, base_url):
    page.set_viewport_size({"width": 320, "height": 800})
    sign_in(page, base_url, SANDRA)
    page.goto(f"{base_url}{KUVAMINE}")
    page.get_by_role("button", name="Väga suur", exact=True).click()
    for path in ("/haldus/", KUVAMINE, "/teemad/", "/minu-asjad/", "/teemad/uus/"):
        page.goto(f"{base_url}{path}")
        page.wait_for_load_state("networkidle")
        assert not document_overflows(page), path
