"""Escape closes a disclosure opened from its own trigger, and focus stays put.

The 2026-10-23 bug-bounty (B-05) opened three disclosures from the keyboard —
the header's ⋯ «Rohkem tegevusi», the header's inline date editors and the
register's «Täpsem otsing» — and pressed Escape with the focus where opening
had left it, on the `<summary>`. None closed. The inline editors answered
Escape only from inside their form, the ⋯ menu was missing from the handler
that closes `Veel`, and «Täpsem otsing» had no handler at all.

Each one is opened with Enter here, closed with Escape, and the focus is
expected back on (still on) the trigger that opened it. The last test keeps
JUX-007: an Escape meant for a calendar inside the panel closes only the
calendar (e2e/test_escape_closes_only_the_popup.py).
"""

from __future__ import annotations

import pytest
from playwright.sync_api import expect

from e2e.conftest import SANDRA, create_matter, record_marge, sign_in, unique_title

pytestmark = pytest.mark.e2e


def open_with_enter_and_escape(holder) -> None:
    trigger = holder.locator("> summary")
    trigger.focus()
    trigger.press("Enter")
    expect(holder).to_have_js_property("open", True)
    holder.page.keyboard.press("Escape")
    expect(holder).to_have_js_property("open", False)
    expect(trigger).to_be_focused()


@pytest.fixture
def matter(page, base_url):
    sign_in(page, base_url, SANDRA)
    return create_matter(page, base_url, unique_title("Escape avaja"))


def test_escape_closes_the_header_menu(page, matter):
    # The ⋯ menu is drawn only for a Teema with files.
    status = record_marge(page, "Kiri lisatud", files=(("kiri.pdf", b"%PDF-1.4 kiri"),))
    assert status == 200, status
    open_with_enter_and_escape(page.locator("details.headmenu"))


def test_escape_closes_the_header_inline_editors(page, matter):
    for field in ("received_date", "response_deadline"):
        page.goto(matter)
        editor = page.locator(f"#teema-pais details.inlineedit:has(input[name={field}])").first
        open_with_enter_and_escape(editor)


def test_escape_closes_tapsem_otsing(page, base_url):
    sign_in(page, base_url, SANDRA)
    page.goto(f"{base_url}/teemad/")
    open_with_enter_and_escape(page.locator("details.filterpanel"))


def test_escape_in_a_calendar_inside_tapsem_otsing_closes_only_the_calendar(page, base_url):
    sign_in(page, base_url, SANDRA)
    page.goto(f"{base_url}/teemad/")
    panel = page.locator("details.filterpanel")
    panel.locator("> summary").click()
    picker = panel.locator(".datepicker").first
    picker.locator(".datepicker__trigger").click()
    calendar = picker.locator(".datepicker__panel")
    expect(calendar).to_be_visible()

    page.keyboard.press("Escape")
    expect(calendar).to_be_hidden()
    expect(panel).to_have_js_property("open", True)
