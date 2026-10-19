"""Minu asjad can be walked with Tab, and J/K still move the selection.

A work row selects itself when focus enters it, so J/K carry on from wherever
the reader is. That handler used to *move* focus as well — to the row's own
link — so Tab from the link to the row's ⋯ menu was sent straight back to the
link. Keyboard users were stuck on the first row, and the row menus, Aktiivsed
teemad, the Määra links, Märkmed and Viimati muudetud were out of reach
(WCAG 2.1.1, 2.4.3; audit 2026-10-17 JUX-002).

Focus is now moved only by J/K, which is the one place that asks for it.
"""

from __future__ import annotations

import re

import pytest
from playwright.sync_api import expect

from e2e.conftest import SANDRA, sign_in

pytestmark = pytest.mark.e2e

SELECTED = re.compile(r"\bis-selected\b")

#: Generous: every focusable on Sandra's page sits well inside it, and a page
#: that traps focus would repeat the same element long before reaching it.
MAX_TABS = 400


def _focus_is_in(page, selector: str) -> bool:
    return bool(
        page.evaluate(
            "(sel) => !!(document.activeElement && document.activeElement.closest(sel))",
            selector,
        )
    )


def test_tab_walks_past_the_work_rows_to_aktiivsed_teemad(page, base_url):
    sign_in(page, base_url, SANDRA)  # lands on /minu-asjad/

    rows = page.locator("[data-workrow]")
    expect(rows.nth(1)).to_be_visible()  # the persona has several work rows
    first, second = rows.nth(0), rows.nth(1)

    first.locator(".workrow2__link").focus()
    expect(first).to_have_class(SELECTED)

    # Tab from row 1's link reaches row 1's ⋯ — and stays there.
    page.keyboard.press("Tab")
    trigger = first.locator("summary.rowmenu__trigger")
    expect(trigger).to_be_focused()
    expect(first).to_have_class(SELECTED)

    # The next Tab reaches row 2's link, and the selection follows focus.
    page.keyboard.press("Tab")
    expect(second.locator(".workrow2__link")).to_be_focused()
    expect(second).to_have_class(SELECTED)
    expect(first).not_to_have_class(SELECTED)

    # Carrying on, Tab leaves the work list and reaches Aktiivsed teemad.
    previous = None
    for _ in range(MAX_TABS):
        page.keyboard.press("Tab")
        if _focus_is_in(page, ".workband--portfolio"):
            break
        here = page.evaluate("() => document.activeElement && document.activeElement.outerHTML")
        assert here != previous, f"focus did not move on Tab: {here[:160]}"
        previous = here
    else:
        pytest.fail(f"Tab never reached Aktiivsed teemad in {MAX_TABS} presses")

    expect(page.locator(".workband--portfolio :focus")).to_have_count(1)


def test_j_and_k_still_move_the_selection_and_the_focus(page, base_url):
    sign_in(page, base_url, SANDRA)

    rows = page.locator("[data-workrow]")
    expect(rows.nth(1)).to_be_visible()
    first, second = rows.nth(0), rows.nth(1)

    page.keyboard.press("j")
    expect(first).to_have_class(SELECTED)
    expect(first.locator(".workrow2__link")).to_be_focused()

    page.keyboard.press("j")
    expect(second).to_have_class(SELECTED)
    expect(second.locator(".workrow2__link")).to_be_focused()
    expect(first).not_to_have_class(SELECTED)

    page.keyboard.press("k")
    expect(first).to_have_class(SELECTED)
    expect(first.locator(".workrow2__link")).to_be_focused()
    expect(second).not_to_have_class(SELECTED)

    # From the ⋯ menu J moves on to the next row: focusing the trigger kept the
    # selection on its own row, so the keys carry on from where the reader is.
    page.keyboard.press("Tab")
    expect(first.locator("summary.rowmenu__trigger")).to_be_focused()
    page.keyboard.press("j")
    expect(second).to_have_class(SELECTED)
    expect(second.locator(".workrow2__link")).to_be_focused()
