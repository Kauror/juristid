"""Keyboard focus is visible, and never hidden under the sticky bar.

ENG-096: «Mida tegid?» on the open step (`#id_praegune_body`) borrowed
`composer__body`, whose own outline is removed on the assumption that a
`.composer` card draws the ring — and the completion form is not one. Focus
changed no pixel (WCAG 2.4.7).

ENG-097: the topbar is sticky and 48px tall above 860px, and nothing told the
browser, so a control reached by Tab — Shift+Tab above all — could scroll into
view underneath it (WCAG 2.4.11). `html` now carries `scroll-padding-top`, and
the few targets that had the bar's height in their own `scroll-margin-top` carry
only their extra gap.
"""

from __future__ import annotations

import pytest
from playwright.sync_api import expect

from app.core.management.commands.seed_e2e_data import OPEN_TITLE
from e2e.conftest import SANDRA, open_matter, sign_in

pytestmark = pytest.mark.e2e

DESKTOP = {"width": 1440, "height": 700}

#: How far below the bar a keyboard-reached control may land, generously: the
#: padding is the bar plus 8px, and a control may be tall.
OBSCURED = """() => {
    const bar = document.querySelector("header.topbar, .topbar");
    const active = document.activeElement;
    if (!bar || !active || active === document.body || bar.contains(active)) return null;
    const barBottom = bar.getBoundingClientRect().bottom;
    const box = active.getBoundingClientRect();
    if (box.width === 0 && box.height === 0) return null;
    // Wholly under the bar: its bottom edge is above the bar's bottom edge.
    return box.bottom <= barBottom ? active.outerHTML.slice(0, 120) : null;
}"""


def test_the_mida_tegid_box_shows_that_it_has_focus(page, base_url):
    sign_in(page, base_url, SANDRA)
    page.set_viewport_size(DESKTOP)
    open_matter(page, base_url, OPEN_TITLE)
    box = page.locator("#id_praegune_body")
    expect(box).to_be_visible()
    box.scroll_into_view_if_needed()

    before = box.screenshot()
    style_before = box.evaluate("el => getComputedStyle(el).outlineStyle")
    box.focus()
    style_after = box.evaluate(
        "el => [getComputedStyle(el).outlineStyle, getComputedStyle(el).outlineWidth]"
    )
    after = box.screenshot()

    assert style_after[0] != "none" and style_after[1] != "0px", (style_before, style_after)
    assert before != after, "focusing «Mida tegid?» changed no pixel"


@pytest.mark.parametrize("where", ["matter", "register"])
@pytest.mark.parametrize("key", ["Tab", "Shift+Tab"])
def test_no_keyboard_focus_lands_under_the_sticky_bar(page, base_url, where, key):
    sign_in(page, base_url, SANDRA)
    page.set_viewport_size(DESKTOP)
    if where == "matter":
        open_matter(page, base_url, OPEN_TITLE)
    else:
        page.goto(f"{base_url}/teemad/?olek=koik")
        page.wait_for_load_state("networkidle")

    if key == "Shift+Tab":
        # From the last control on the page, walking up — the direction that
        # hid focus.
        page.evaluate(
            """() => {
                const all = [...document.querySelectorAll(
                    'a[href], button, input, select, textarea, [tabindex]:not([tabindex="-1"])'
                )].filter((el) => el.offsetParent !== null && !el.disabled);
                all[all.length - 1].focus();
            }"""
        )
    hidden: list[str] = []
    for _ in range(60):
        page.keyboard.press(key)
        found = page.evaluate(OBSCURED)
        if found:
            hidden.append(found)
    assert hidden == [], hidden[:5]


def test_a_fragment_jump_lands_below_the_bar_without_a_doubled_gap(page, base_url):
    """`scroll-padding-top` and `scroll-margin-top` add; the margins were rebalanced."""
    sign_in(page, base_url, SANDRA)
    page.set_viewport_size(DESKTOP)
    page.goto(f"{base_url}/uuendused/")
    page.wait_for_load_state("networkidle")
    days = page.locator("details.relnotes__day")
    if days.count() < 3:
        pytest.skip("not enough release-note days to scroll to one")
    # The third day: far enough down to need a scroll, with enough page under
    # it that the browser can bring it to the top.
    target = days.nth(2)
    target.evaluate("el => { el.id = el.id || 'e2e-paev'; }")
    anchor = target.get_attribute("id")
    page.evaluate(f"location.hash = '#{anchor}'")
    page.wait_for_timeout(300)

    bar_bottom = page.evaluate("document.querySelector('.topbar').getBoundingClientRect().bottom")
    top = target.evaluate("el => el.getBoundingClientRect().top")
    assert top >= bar_bottom, (top, bar_bottom)
    # Bar (48) + padding gap (8) + the day's own 8px: about 64px, as before the
    # change. A doubled gap would put it past 110px.
    assert top <= bar_bottom + 40, (top, bar_bottom)
