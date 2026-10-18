"""Lisa tagasiside: a disabled choice looks disabled and says why; Tab reaches `Liige`.

Two findings of the 2026-10-17 audit on the same form (docs/adr/0170):

* **JUX-004.** With a round linked, «Avaldatud mujal» is disabled — and it
  looked exactly like an enabled choice: same colours, a pointer cursor, and no
  word about why clicking it did nothing.
* **JUX-005.** `Liige` sat in the organisation row, *before* the provenance
  question that reveals it, so after choosing «Saadeti Kojale» the next Tab went
  on to the round and the text, and `Liige` was reachable only by Shift+Tab.
  The question now comes first and the organisation row — `Liige` still at its
  end, where the owner put it on 2026-10-06 — follows it.
"""

from __future__ import annotations

import pytest
from playwright.sync_api import expect

from e2e.conftest import (
    AVALDATUD_MUJAL,
    SAADETI_KOJALE,
    SANDRA,
    create_matter,
    open_add_panel,
    open_feedback_form,
    sign_in,
    unique_title,
    wait_for_htmx,
)

pytestmark = pytest.mark.e2e

ROUND_NOTE = "Kaasamise vastus saadeti Kojale"


@pytest.fixture
def matter(page, base_url):
    sign_in(page, base_url, SANDRA)
    create_matter(page, base_url, unique_title("Päritolu klaviatuuriga"))


def _start_round(page) -> None:
    open_add_panel(page, "kaasamine-alusta")
    round_form = page.locator("#kaasamine-alusta form")
    round_form.locator("[name=audience]").fill("Liikmed (e-kiri)")
    with page.expect_response(lambda r: r.request.method == "POST"):
        round_form.locator("button[type=submit]").click()
    wait_for_htmx(page)
    expect(page.locator("#praegune-ootused")).to_contain_text("Ootame tagasisidet")


def _chip_style(form, value: str) -> dict:
    return form.locator(f'input[name="provenance"][value="{value}"]').evaluate(
        """input => {
            const name = input.parentElement.querySelector('.chip__name');
            const look = getComputedStyle(name);
            return {
              color: look.color,
              border: look.borderTopColor,
              opacity: look.opacity,
              cursor: look.cursor,
              inputCursor: getComputedStyle(input).cursor,
            };
        }"""
    )


def test_a_disabled_provenance_looks_disabled_and_says_why(page, matter):
    _start_round(page)
    form = open_feedback_form(page)
    note = form.get_by_text(ROUND_NOTE)
    expect(note).to_be_hidden()
    enabled = _chip_style(form, AVALDATUD_MUJAL)

    form.locator("[name=engagement]").select_option(index=1)
    discovered = form.locator(f'input[name="provenance"][value="{AVALDATUD_MUJAL}"]')
    expect(discovered).to_be_disabled()

    disabled = _chip_style(form, AVALDATUD_MUJAL)
    assert disabled["cursor"] == "not-allowed", disabled
    assert disabled["inputCursor"] == "not-allowed", disabled
    assert (disabled["color"], disabled["border"], disabled["opacity"]) != (
        enabled["color"],
        enabled["border"],
        enabled["opacity"],
    ), (enabled, disabled)

    expect(note).to_be_visible()
    note_id = note.get_attribute("id")
    assert note_id
    assert note_id in (discovered.get_attribute("aria-describedby") or "").split()

    form.locator("[name=engagement]").select_option(value="")
    expect(discovered).to_be_enabled()
    expect(note).to_be_hidden()
    assert note_id not in (discovered.get_attribute("aria-describedby") or "").split()


def test_tab_after_saadeti_kojale_reaches_liige(page, matter):
    form = open_feedback_form(page, SAADETI_KOJALE)
    received = form.locator(f'input[name="provenance"][value="{SAADETI_KOJALE}"]')
    received.focus()
    expect(received).to_be_focused()

    member = form.locator('input[name="source_is_member"]')
    reached = []
    for _ in range(4):
        page.keyboard.press("Tab")
        name = page.evaluate("() => document.activeElement.name || document.activeElement.id || ''")
        reached.append(name)
        if name == "source_is_member":
            break
    assert "source_is_member" in reached, reached
    expect(member).to_be_focused()
    # Nothing further down the form came first.
    assert not {"engagement", "summary", "stated_on", "url"} & set(reached), reached
