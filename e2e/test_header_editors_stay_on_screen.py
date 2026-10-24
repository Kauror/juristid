"""Every Teema header editor opens inside the window on a tablet.

The 2026-10-23 bug-bounty verifier (V-03) measured the header's «Arvamuse
tähtaeg» editor running off the right edge between 721 and about 900px: the
form ended at x=844 in a 768px window, the page scrolled sideways, and on a
longer header the 📅 trigger sat outside the window, so opening the calendar
scrolled the whole page 121px. Up to 720px the whole meta line was the editors'
anchor; above it each editor hung from its own fact, which at tablet widths
sits about 520px from the left. Every assertion reads the rendered rectangle.
"""

from __future__ import annotations

import pytest
from playwright.sync_api import expect

from e2e.conftest import SANDRA, create_matter, document_overflows, sign_in, unique_title

pytestmark = pytest.mark.e2e

TOLERANCE_PX = 0.5

EDITORS = "#teema-pais .metaline details.inlineedit"


def assert_each_header_editor_on_screen(page, width: int) -> int:
    """Open every header editor in turn; its form and calendar stay in the window."""
    editors = page.locator(EDITORS)
    count = editors.count()
    for index in range(count):
        editor = editors.nth(index)
        summary = editor.locator("summary").first
        name = summary.inner_text().strip() or f"#{index}"
        summary.scroll_into_view_if_needed()
        summary.click()
        form = editor.locator("form.inlineedit__form").first
        expect(form).to_be_visible()
        box = form.bounding_box()
        assert box is not None
        assert box["x"] >= -TOLERANCE_PX, f"{name} at {width}px opens off the left edge"
        assert box["x"] + box["width"] <= width + TOLERANCE_PX, (
            f"{name} at {width}px ends at {box['x'] + box['width']:.0f}px"
        )
        assert not document_overflows(page), f"{name} at {width}px scrolls the page sideways"
        trigger = editor.locator(".datepicker__trigger").first
        if trigger.count():
            tbox = trigger.bounding_box()
            assert tbox is not None and tbox["x"] + tbox["width"] <= width + TOLERANCE_PX, (
                f"{name} at {width}px: the 📅 trigger is off-screen"
            )
            trigger.click()
            panel = editor.locator(".datepicker__panel")
            expect(panel).to_be_visible()
            pbox = panel.bounding_box()
            assert pbox is not None
            assert pbox["x"] >= -TOLERANCE_PX
            assert pbox["x"] + pbox["width"] <= width + TOLERANCE_PX, (
                f"{name} at {width}px: the calendar runs past the right edge"
            )
            assert not document_overflows(page), (
                f"{name} at {width}px: the open calendar scrolls the page sideways"
            )
            assert page.evaluate("window.scrollX") == 0
            page.keyboard.press("Escape")
            expect(panel).to_be_hidden()
        summary.click()
        expect(form).to_be_hidden()
    return count


@pytest.mark.parametrize("width", [768, 820, 1024])
def test_every_header_editor_stays_on_a_tablet(page, base_url, width):
    sign_in(page, base_url, SANDRA)
    url = create_matter(page, base_url, unique_title("Päise redaktor tahvlil"))
    page.set_viewport_size({"width": width, "height": 1000})
    page.goto(url)

    # Without a deadline: Vastutaja, Valdkond, Hetkeseis, Saabus, «+ Arvamuse tähtaeg».
    assert assert_each_header_editor_on_screen(page, width) >= 4

    # With one, the deadline's own (wider, stacked) editor takes that place.
    adder = page.locator(f"{EDITORS}:has(.metaline__add:text('+ Arvamuse tähtaeg'))")
    adder.locator("summary").click()
    adder.locator("input[name=response_deadline]").fill("30.12.2026")
    adder.get_by_role("button", name="Salvesta").click()
    deadline = page.locator("#teema-pais details[data-deadline-editor]")
    expect(deadline).to_have_count(1)
    assert assert_each_header_editor_on_screen(page, width) >= 4

    # Ended, the line grows by the ended one and `+ Arvamuse tähtaeg` moves to
    # its far end — the showcase header whose editor ran past 1024px too.
    deadline.locator("summary").click()
    deadline.locator("select[name=previous_outcome]").select_option("NOT_ANSWERING")
    deadline.get_by_role("button", name="Lõpeta tähtaeg").click()
    expect(deadline).to_have_count(0)
    expect(adder).to_have_count(1)
    assert assert_each_header_editor_on_screen(page, width) >= 4
