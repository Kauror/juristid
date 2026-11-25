"""Monitooring on a phone, at every text size (r93 integration).

The UX corrections round (25.11.2026) reported `/monitooring/` scrolling
sideways at 375px «at every size», blaming `table.meta`. That table is Django's
DEBUG «Page not found» page, which a persona without Monitooring receives on a
development server; production renders `templates/404.html`, which fits
(`tests/test_r93_integration.py`). The Monitooring pages themselves, the new
Riigikogu section included, are held here to the rule every page keeps: the
document never scrolls sideways, at Väike, Tavaline, Suur and Väga suur.
"""

from __future__ import annotations

import pytest

from e2e.conftest import HEAD, document_overflows, sign_in

pytestmark = pytest.mark.e2e

KEY = "juristid-text-size"
SIZES = ("vaike", None, "suur", "vaga-suur")
PAGES = (
    "/monitooring/eelnoud/",
    "/monitooring/valitsus/",
    "/monitooring/registrid/",
    "/monitooring/riigikogu/",
    "/monitooring/riigikogu/?komisjonid=koik",
)


@pytest.mark.parametrize("size", SIZES, ids=[s or "tavaline" for s in SIZES])
def test_no_monitooring_page_scrolls_sideways_on_a_phone(page, base_url, size):
    page.set_viewport_size({"width": 375, "height": 812})
    sign_in(page, base_url, HEAD)
    if size is None:
        page.evaluate(f"() => window.localStorage.removeItem('{KEY}')")
    else:
        page.evaluate(f"() => window.localStorage.setItem('{KEY}', '{size}')")
    wide = []
    for path in PAGES:
        page.goto(f"{base_url}{path}")
        page.wait_for_load_state("networkidle")
        assert page.evaluate("() => document.documentElement.getAttribute('data-text-size')") == (
            size
        )
        if document_overflows(page):
            wide.append(path)
    assert wide == []
