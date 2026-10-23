"""Minu asjad's menu on a planned row opens that planned row, not the current step.

A Matter can carry a current step and dated planned ones (docs/adr/0143), and
each planned one reaches Minu asjad as a row of its own. Its `Lisa tulemus…` and
`Muuda` linked to `#praegune-tegevus` and `#lisa-jargmine` — the *current*
step's forms — so a lawyer recording the planned call's result finished the
current meeting instead, and the planned call was promoted in its place
(bounty A-01). They now name the planned row's own `✓ Tehtud` and `Muuda`
(docs/adr/0144 §1), which `ux.js` opens and focuses on arrival.

Martin's desk, not Sandra's: every Matter made here stays in its owner's
«Aktiivsed teemad», and `e2e/test_work_surfaces_review.py` measures Sandra's.

The Django tests prove the hrefs; whether arrival opens the right disclosure,
puts the caret in it, and the save finishes the right step is the browser's
answer.
"""

from __future__ import annotations

import re
from datetime import date, timedelta

import pytest
from playwright.sync_api import expect

from e2e.conftest import (
    MARTIN,
    create_matter,
    open_add_panel,
    sign_in,
    start_first_step,
    wait_for_htmx,
)
from e2e.titles import unique_title

pytestmark = pytest.mark.e2e

UUID = r"[0-9a-f-]{36}"


def _et(days: int) -> str:
    on = date.today() + timedelta(days=days)
    return f"{on.day}.{on.month}.{on.year}"


def _add_planned(page, text: str, days: int) -> None:
    open_add_panel(page, "lisa-planeeritud")
    page.locator("#lisa-planeeritud [name=text]").fill(text)
    page.locator("#lisa-planeeritud [name=target_date]").fill(_et(days))
    with page.expect_response(
        lambda r: r.url.endswith("/planeeritud/") and r.request.method == "POST"
    ) as caught:
        page.locator("#lisa-planeeritud").get_by_role("button", name="Salvesta", exact=True).click()
    assert caught.value.status == 200, caught.value.status
    wait_for_htmx(page)


def _follow_menu_item(page, base_url: str, row_text: str, item: str) -> str:
    """Follow `item` in the ⋯ menu of the Minu asjad row reading `row_text`.

    Clicked where the row is on screen. The browser world is one database shared
    by the shard, so the row may sit in a band's inline remainder; then the link
    is followed by address, which is the same navigation and the same `load`.
    """
    page.goto(f"{base_url}/minu-asjad/")
    page.wait_for_load_state("networkidle")
    row = page.locator("[data-workrow]", has_text=row_text)
    expect(row).to_have_count(1)
    link = row.locator("a.rowmenu__item", has_text=item)
    href = link.get_attribute("href") or ""
    if row.is_visible():
        row.locator("summary.rowmenu__trigger").click()
        link.click()
    else:
        page.goto(f"{base_url}{href}")
    page.wait_for_load_state("load")
    return href


def test_lisa_tulemus_on_a_planned_row_records_that_planned_action(page, base_url):
    sign_in(page, base_url, MARTIN)
    title = unique_title("A01 plaanirida")
    create_matter(page, base_url, title, owner=MARTIN)
    current_text = f"Kohtumine ministeeriumiga {title}"
    planned_text = f"Helistan ministeeriumi {title}"
    start_first_step(page, days=9, text=current_text)
    _add_planned(page, planned_text, 3)

    href = _follow_menu_item(page, base_url, planned_text, "Lisa tulemus…")
    match = re.search(rf"#planeeritud-tehtud-({UUID})$", href)
    assert match, f"«Lisa tulemus…» on a planned row names {href!r}"
    planned_pk = match.group(1)

    panel = page.locator(f"#planeeritud-tehtud-{planned_pk}")
    box = panel.locator("textarea[name=body]")
    box.wait_for(state="visible")
    expect(panel).to_have_attribute("open", "")
    expect(box).to_be_focused()
    # The current step's `✓ Tehtud` stays shut: it would finish another step.
    assert not page.locator("#tehtud-valik").is_checked()

    form = panel.locator("form")
    assert (form.get_attribute("hx-post") or "").endswith(f"/planeeritud/{planned_pk}/tehtud/"), (
        form.get_attribute("hx-post")
    )

    box.fill("Helistasin, vastust ootame.")
    with page.expect_response(
        lambda r: (
            r.url.endswith(f"/planeeritud/{planned_pk}/tehtud/") and r.request.method == "POST"
        )
    ) as caught:
        panel.get_by_role("button", name="Salvesta", exact=True).click()
    assert caught.value.status == 200, caught.value.status
    wait_for_htmx(page)

    # The planned call is finished; the meeting is still the current step.
    zone = page.locator("#praegune-tegevus")
    expect(zone.locator(".curact__text")).to_contain_text(current_text)
    expect(zone.locator(".curact__plannedrow", has_text=planned_text)).to_have_count(0)


def test_muuda_on_a_planned_row_opens_that_planned_rows_editor(page, base_url):
    sign_in(page, base_url, MARTIN)
    title = unique_title("A01 plaanimuuda")
    create_matter(page, base_url, title, owner=MARTIN)
    start_first_step(page, days=9, text=f"Kohtumine ministeeriumiga {title}")
    planned_text = f"Helistan ministeeriumi {title}"
    _add_planned(page, planned_text, 3)

    href = _follow_menu_item(page, base_url, planned_text, "Muuda")
    match = re.search(rf"#planeeritud-({UUID})$", href)
    assert match, f"«Muuda» on a planned row names {href!r}"
    planned_pk = match.group(1)

    panel = page.locator(f"#planeeritud-{planned_pk}")
    text_box = panel.locator("[name=text]")
    text_box.wait_for(state="visible")
    expect(panel).to_have_attribute("open", "")
    expect(text_box).to_be_focused()
    expect(text_box).to_have_value(planned_text)
    # Not the current step's `Muuda`.
    expect(page.locator("#lisa-jargmine")).not_to_have_attribute("open", "")
    assert (panel.locator("form").get_attribute("hx-post") or "").endswith(
        f"/planeeritud/{planned_pk}/muuda/"
    )
