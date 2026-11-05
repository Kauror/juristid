"""Focus stays in «Seotud materjalid» after a link or an unlink (QA-13).

Every decision in the section answers with the whole section, swapped over the
old one, which destroys the button that had focus. The page's general focus
rule (`static/js/app.js`, `htmx:afterSettle`) then searched the document for its
first landing mark — `Lisa teemale`, near the top of the page — so a keyboard
user who linked or removed one relation in the rail was sent up the page and had
to find the section again.

Now the landing is inside the updated section: the row that took the acted-on
row's place, or the section heading when the list is gone. The notice the
section returns is said through a polite live region that existed before the
swap. Driven with the keyboard throughout: Enter on a focused button, then Tab.
"""

from __future__ import annotations

import pytest
from playwright.sync_api import expect

from e2e.conftest import MARTIN, create_matter, sign_in, wait_for_htmx
from e2e.titles import unique_title

pytestmark = pytest.mark.e2e

ACTIVE = """() => {
  const a = document.activeElement;
  return {
    inSection: !!(a && a.closest && a.closest('#seotud-materjalid')),
    inLisaTeemale: !!(a && a.closest && a.closest('#lisa-teemale')),
    id: a ? a.id : null,
    tag: a ? a.tagName.toLowerCase() : null,
    text: a ? (a.textContent || '').replace(/\\s+/g, ' ').trim() : '',
    row: a && a.closest && a.closest('[data-related-matter]')
      ? a.closest('[data-related-matter]').textContent.replace(/\\s+/g, ' ').trim() : '',
  };
}"""

ANNOUNCED = """() => {
  const region = document.getElementById('seotud-materjalid-teade');
  return region ? {role: region.getAttribute('role'), live: region.getAttribute('aria-live'),
                   text: region.textContent.trim()} : null;
}"""


def _section(page):
    return page.locator("#seotud-materjalid")


def _press(page, locator) -> None:
    """Enter on a focused control, the way a keyboard user activates it."""
    locator.focus()
    page.keyboard.press("Enter")
    wait_for_htmx(page)


def _landed(page) -> dict:
    """Where focus is once the swap has settled.

    `wait_for_htmx` returns when no request is in flight; the focus rule runs on
    `htmx:afterSettle`, a tick later. Before it runs, focus is on `body` (the
    destroyed button's fallback) under either rule, so waiting for anything
    but `body` tells the old landing from the new one without a fixed sleep.
    """
    page.wait_for_function(
        "() => document.activeElement && document.activeElement !== document.body"
    )
    return page.evaluate(ACTIVE)


def _link_by_search(page, title: str) -> None:
    disclosure = _section(page).locator("#lisa-seotud-teema")
    if disclosure.evaluate("node => !node.open"):
        _press(page, disclosure.locator("> summary"))
    page.locator("#seotud-teema-q").fill(title)
    button = page.locator("[data-related-picker-results] .factrow").filter(has_text=title)
    button = button.get_by_role("button", name="Seo teemaga")
    expect(button).to_be_visible()
    _press(page, button)
    expect(_section(page).locator("[data-related-confirmed]")).to_contain_text(title)


def _assert_announced(page, text: str) -> None:
    expect(page.locator("#seotud-materjalid-teade")).to_have_text(text)
    region = page.evaluate(ANNOUNCED)
    assert region["role"] == "status" and region["live"] == "polite", region


def test_focus_stays_in_the_section_after_linking_and_unlinking(page, base_url):
    sign_in(page, base_url, MARTIN)
    first = unique_title("Fookuse esimene seotud teema")
    second = unique_title("Fookuse teine seotud teema")
    create_matter(page, base_url, first)
    create_matter(page, base_url, second)
    here = create_matter(page, base_url, unique_title("Fookuse siht"))
    page.goto(here)
    page.wait_for_load_state("networkidle")
    assert page.locator("#lisa-teemale").count() == 1  # the landing mark the old rule found

    # Link: focus is in the section, not «Lisa teemale», and the notice is said.
    _link_by_search(page, first)
    active = _landed(page)
    assert active["inSection"] and not active["inLisaTeemale"], active
    _assert_announced(page, "Teemad on seotud.")

    _link_by_search(page, second)
    active = _landed(page)
    assert active["inSection"] and not active["inLisaTeemale"], active

    # Unlink one: focus lands on the row that took its place, its title link.
    rows = _section(page).locator("[data-related-confirmed] [data-related-matter]")
    expect(rows).to_have_count(2)
    removed = rows.nth(0).inner_text()
    remaining_title = second if first in removed else first
    _press(page, rows.nth(0).locator(".relatedmaterials__remove button"))
    expect(rows).to_have_count(1)
    active = _landed(page)
    assert active["inSection"] and active["tag"] == "a", active
    assert remaining_title in active["row"], active
    _assert_announced(page, "Seos on eemaldatud.")

    # Unlink the last one: the list is gone, so the section heading takes focus.
    _press(page, rows.nth(0).locator(".relatedmaterials__remove button"))
    expect(rows).to_have_count(0)
    active = _landed(page)
    assert active["id"] == "seotud-materjalid-heading", active
    heading = page.locator("#seotud-materjalid-heading")
    expect(heading).to_have_attribute("tabindex", "-1")

    # No trap: Tab moves on to the section's next control.
    page.keyboard.press("Tab")
    active = _landed(page)
    assert active["id"] != "seotud-materjalid-heading" and active["tag"] in {
        "a",
        "button",
        "summary",
        "input",
    }, active
