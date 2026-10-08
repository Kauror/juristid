"""The October 8 follow-up round, in a browser (docs/adr/0144).

The parts the unit tests cannot see: a planned row's `✓ Tehtud` from the page,
a Koja arvamus sent as two files through the panel, the summary's width against
the header and the rail, and the cleaned-up forms — each at the widths a lawyer
uses (1440, 1024, 375) without sideways scroll.
"""

from __future__ import annotations

import re
from datetime import date, timedelta

import pytest
from playwright.sync_api import expect

from e2e.conftest import (
    DESKTOP_VIEWPORT,
    SANDRA,
    chronology,
    create_matter,
    document_overflows,
    open_add_panel,
    sign_in,
    start_first_step,
    wait_for_htmx,
)
from e2e.titles import unique_title

pytestmark = pytest.mark.e2e

PDF = b"%PDF-1.4 synthetic"


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


def _box(locator) -> dict:
    return locator.evaluate(
        "el => { const r = el.getBoundingClientRect();"
        " return {left: r.left, right: r.right, top: r.top, bottom: r.bottom}; }"
    )


def test_a_future_planned_action_is_marked_tehtud_from_its_row(page, base_url):
    sign_in(page, base_url, SANDRA)
    create_matter(page, base_url, unique_title("Planeeritud tehtuks"), owner=SANDRA)
    start_first_step(page, text="Loe eelnõu läbi")
    _add_planned(page, "Kohtu ministeeriumiga", 10)
    _add_planned(page, "Saada arvamus", 20)

    zone = page.locator("#praegune-tegevus")
    row = zone.locator(".curact__plannedrow", has_text="Kohtu ministeeriumiga")
    expect(row.locator(".curact__edit > summary", has_text="Tehtud")).to_be_visible()
    expect(row.locator(".curact__edit > summary", has_text="Muuda")).to_be_visible()
    expect(row.locator(".curact__dismiss button")).to_have_attribute(
        "title", "Kustuta planeeritud tegevus"
    )

    row.locator(".curact__edit > summary", has_text="Tehtud").click()
    row.locator("textarea[name=body]").fill("Kohtumist ei toimunud.")
    with page.expect_response(
        lambda r: r.url.endswith("/tehtud/") and r.request.method == "POST"
    ) as caught:
        row.get_by_role("button", name="Salvesta", exact=True).click()
    assert caught.value.status == 200, caught.value.status
    wait_for_htmx(page)

    zone = page.locator("#praegune-tegevus")
    expect(zone.locator(".curact__text")).to_contain_text("Loe eelnõu läbi")
    expect(zone.locator(".curact__plannedrow")).to_have_count(1)
    expect(zone.locator(".curact__plannedrow")).to_contain_text("Saada arvamus")
    expect(chronology(page)).to_contain_text("Kohtumist ei toimunud.")


def test_a_koja_arvamus_goes_out_as_two_files_and_stays_one_opinion(page, base_url):
    sign_in(page, base_url, SANDRA)
    url = create_matter(
        page,
        base_url,
        unique_title("Kahe failiga arvamus"),
        owner=SANDRA,
        sender="Näidisministeerium",
    )

    open_add_panel(page, "arvamus-koja")
    form = page.locator("#arvamus-koja")
    expect(form).to_contain_text("Saadetud failid")
    expect(form).not_to_contain_text("Töödokumendid")
    form.locator("input[name=upload]").set_input_files(
        [
            {"name": "arvamus.pdf", "mimeType": "application/pdf", "buffer": PDF},
            {"name": "lisa.pdf", "mimeType": "application/pdf", "buffer": PDF + b" annex"},
        ]
    )
    rows = form.locator(".uploadqueue__row")
    expect(rows).to_have_count(2)
    rows.nth(1).get_by_role("button", name="Muuda pealkirja: lisa.pdf").click()
    title = rows.nth(1).get_by_role("textbox")
    title.fill("Selgitav lisa")
    title.press("Enter")
    with page.expect_response(
        lambda r: r.url.endswith("/lisa/koja-arvamus/") and r.request.method == "POST"
    ) as caught:
        form.get_by_role("button", name="Salvesta", exact=True).click()
    assert caught.value.status == 200, caught.value.status
    wait_for_htmx(page)

    expect(chronology(page).locator(".uxtl__item", has_text="Arvamus välja")).to_have_count(1)
    rail = page.locator("#koja-arvamus")
    expect(rail.locator(".railcard__row--opinion")).to_have_count(1)
    expect(rail).to_contain_text("arvamus.pdf")
    expect(rail).to_contain_text("lisa.pdf")

    page.goto(f"{url}dokumendid/")
    annex = page.locator("table.doctable tbody tr", has_text="Selgitav lisa")
    expect(annex).to_have_count(1)
    expect(annex.locator(".doctable__filename")).to_contain_text("lisa.pdf")
    expect(annex.locator(".doctable__sent")).to_be_visible()


@pytest.mark.parametrize("width", [1440, 1024, 375])
def test_the_summary_uses_the_left_column(page, base_url, width):
    sign_in(page, base_url, SANDRA)
    page.goto(f"{base_url}/teemad/uus/")
    page.fill("#id_title", unique_title("Laiem kokkuvõte"))
    page.fill("#id_response_deadline", _et(20))
    page.get_by_role("button", name="Salvesta", exact=True).click()
    page.wait_for_url(re.compile(r"/teemad/[0-9a-f-]{36}/$"))
    summary = page.locator("#teema-luhikokkuvote")
    summary.locator(".summary__trigger").click()
    summary.locator("textarea").fill("Pikk kokkuvõte. " * 60)
    summary.get_by_role("button", name="Salvesta", exact=True).click()
    wait_for_htmx(page)

    page.set_viewport_size({"width": width, "height": 900})
    page.reload()
    page.wait_for_load_state("networkidle")
    assert not document_overflows(page), f"sideways scroll at {width}px"

    text = _box(page.locator("#teema-luhikokkuvote"))
    rail = _box(page.locator("#teema-andmed"))
    if width > 1100:
        deadline = _box(page.locator(".metaline__item--deadline"))
        assert text["right"] >= deadline["right"], (text, deadline)
        assert text["right"] <= rail["left"] + 1, (text, rail)
    else:
        # The rail stacks under the column; the summary takes the full width.
        viewport = page.viewport_size or DESKTOP_VIEWPORT
        assert text["right"] >= viewport["width"] - 80, (text, viewport)
    page.set_viewport_size(DESKTOP_VIEWPORT)


@pytest.mark.parametrize("width", [1440, 1024, 375])
def test_the_new_matter_form_ends_with_its_buttons(page, base_url, width):
    sign_in(page, base_url, SANDRA)
    page.set_viewport_size({"width": width, "height": 900})
    page.goto(f"{base_url}/teemad/uus/")

    body = page.locator("main")
    expect(body).not_to_contain_text("Ülejäänud andmeid saab lisada ka hiljem teema lehel.")
    actions = page.locator(".createform__actions")
    expect(actions.get_by_role("button", name="Salvesta", exact=True)).to_be_visible()
    expect(actions.locator(".createform__note")).to_have_count(0)
    assert not document_overflows(page), f"sideways scroll at {width}px"
    page.set_viewport_size(DESKTOP_VIEWPORT)


def test_the_kaasamine_start_form_asks_keda_kaasad(page, base_url):
    sign_in(page, base_url, SANDRA)
    create_matter(page, base_url, unique_title("Keda kaasad"), owner=SANDRA)

    open_add_panel(page, "kaasamine-alusta")
    panel = page.locator("#kaasamine-alusta")
    expect(panel).to_contain_text("Keda kaasad")
    expect(panel).not_to_contain_text("Keda kaasati")
    panel.get_by_role("button", name="Salvesta", exact=True).click()
    wait_for_htmx(page)
    expect(page.locator("#kaasamine-alusta")).to_contain_text("Kirjuta, keda kaasad.")
