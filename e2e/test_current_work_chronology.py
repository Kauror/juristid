"""`PRAEGUNE TEGEVUS` in date order, in a real browser (#525).

The Django half is tests/test_current_work_chronology.py. What only a browser
can answer is the out-of-band half: `Lõpeta kaasamine` swaps the round's own
chronology row, and the zone's list rides along re-sorted. The current step's
element must be *kept* (`hx-preserve`), so what the lawyer has typed into its
`Muuda` survives — and the DOM, not only the picture, must be in the new order.

Every test writes to a Matter it creates. State is read off the page only.
"""

from __future__ import annotations

from datetime import date, timedelta

import pytest
from playwright.sync_api import expect

from e2e.conftest import (
    SANDRA,
    chronology,
    create_matter,
    open_add_panel,
    open_kaik_row,
    open_next_action_form,
    sign_in,
    start_first_step,
    wait_for_htmx,
)
from e2e.titles import unique_title

pytestmark = pytest.mark.e2e


def _et(days: int) -> str:
    when = date.today() + timedelta(days=days)
    return f"{when.day}.{when.month}.{when.year}"


def _add_round(page, audience: str, days: int) -> None:
    open_add_panel(page, "kaasamine-alusta")
    form = page.locator("#kaasamine-alusta")
    form.locator("[name=audience]").fill(audience)
    form.locator("[name=feedback_deadline]").fill(_et(days))
    form.locator("button[type=submit]").click()
    wait_for_htmx(page)


def _row_kinds(page) -> list[str]:
    return page.locator("#praegune-ootused > li").evaluate_all(
        "rows => rows.map(row => row.dataset.workRow)"
    )


def test_rows_read_by_day_and_a_rounds_answer_reorders_them_keeping_typed_text(page, base_url):
    sign_in(page, base_url, SANDRA)
    create_matter(page, base_url, unique_title("Kronoloogia eelnõu"), owner=SANDRA)
    start_first_step(page, days=12, text="Koostan arvamuse")
    _add_round(page, "Varasem voor", 10)
    _add_round(page, "Hilisem voor", 15)

    # The owner's case: the round due on the 10th reads above the step due on
    # the 12th, and the round due on the 15th below it.
    expect(page.locator("#praegune-ootused > li")).to_have_count(3)
    assert _row_kinds(page) == ["wait", "current", "wait"]
    first = page.locator("#praegune-ootused > li").first
    expect(first).to_contain_text(_et(10))
    expect(first).to_contain_text("Ootame tagasisidet")

    # Something typed into the step's `Muuda`, not saved.
    open_next_action_form(page)
    text = page.locator("#lisa-jargmine [name='text']")
    text.fill("Koostan arvamuse ja kooskõlastan juhatusega")

    # `Lõpeta kaasamine` on the earlier round's own chronology row.
    row = chronology(page).locator(
        ".uxtl__ms-body", has=page.locator(".uxtl__mswhat", has_text="Varasem voor")
    )
    open_kaik_row(row)
    row = chronology(page).locator(
        ".uxtl__ms-body", has=page.locator(".uxtl__mswhat", has_text="Varasem voor")
    )
    row.get_by_text("Lõpeta kaasamine", exact=True).click()
    row.locator("[name=feedback_received]").fill("Liikmed vastasid kirjaga.")
    row.get_by_role("button", name="Salvesta", exact=True).click()
    wait_for_htmx(page)

    # The finished round has left; the step now leads, in the DOM itself.
    expect(page.locator("#praegune-ootused > li")).to_have_count(2)
    assert _row_kinds(page) == ["current", "wait"]
    expect(page.locator("#praegune-ootused > li").last).to_contain_text(_et(15))
    # The step's own element was kept, with what was typed in it.
    expect(page.locator("#lisa-jargmine [name='text']")).to_have_value(
        "Koostan arvamuse ja kooskõlastan juhatusega"
    )
    expect(page.locator("#praegune-ootused #tehtud")).to_have_count(1)
