"""Agent E (independent verifier): the QA-13 focus landing traps nothing.

After the last relation is removed, focus lands on the «Seotud materjalid»
heading (made focusable with ``tabindex=-1``). From there the keyboard must be
able to leave in both directions: Shift+Tab goes back before the section and
Tab goes on past it, and a later Tab cycle never returns to the heading (it is
not in the Tab order). A swap *elsewhere* on the page must not use the
section's remembered landing.
"""

from __future__ import annotations

import pytest
from playwright.sync_api import expect

from e2e.conftest import MARTIN, create_matter, sign_in, wait_for_htmx
from e2e.titles import unique_title

pytestmark = pytest.mark.e2e

WHERE = """() => {
  const a = document.activeElement;
  return {
    id: a ? a.id : null,
    inSection: !!(a && a.closest && a.closest('#seotud-materjalid')),
    tag: a ? a.tagName.toLowerCase() : null,
  };
}"""


def _press(page, locator) -> None:
    locator.focus()
    page.keyboard.press("Enter")
    wait_for_htmx(page)


def test_heading_landing_is_not_a_trap(page, base_url):
    sign_in(page, base_url, MARTIN)
    other = unique_title("Lõksuta seotud teema")
    create_matter(page, base_url, other)
    here = create_matter(page, base_url, unique_title("Lõksuta siht"))
    page.goto(here)
    page.wait_for_load_state("networkidle")
    section = page.locator("#seotud-materjalid")
    disclosure = section.locator("#lisa-seotud-teema")
    if disclosure.evaluate("node => !node.open"):
        _press(page, disclosure.locator("> summary"))
    page.locator("#seotud-teema-q").fill(other)
    button = page.locator("[data-related-picker-results] .factrow").filter(has_text=other)
    _press(page, button.get_by_role("button", name="Seo teemaga"))
    rows = section.locator("[data-related-confirmed] [data-related-matter]")
    expect(rows).to_have_count(1)
    _press(page, rows.nth(0).locator(".relatedmaterials__remove button"))
    expect(rows).to_have_count(0)
    page.wait_for_function(
        "() => document.activeElement && document.activeElement !== document.body"
    )
    assert page.evaluate(WHERE)["id"] == "seotud-materjalid-heading"

    page.keyboard.press("Shift+Tab")
    back = page.evaluate(WHERE)
    assert back["id"] != "seotud-materjalid-heading", back

    # Tab forward from the page start: the heading is never a Tab stop.
    page.locator("body").focus()
    seen = set()
    for _ in range(200):
        page.keyboard.press("Tab")
        now = page.evaluate(WHERE)
        assert now["id"] != "seotud-materjalid-heading", now
        seen.add((now["id"], now["tag"]))
    assert len(seen) > 5
