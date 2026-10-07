"""The follow-up UX round, in the browser (owner's brief, 2026-10-07).

A  `+ Ülevaade / uudis` — the link first, and what the server says about it
   shown before the save; an answer for an address no longer in the box is
   dropped, and a title somebody typed is kept.
C  Long work rows — the date in its own column, the text wrapping under itself,
   the controls on their own row, a hairline between planned rows, and no
   sideways scroll at the narrow width.
D  `+ Lisa` — four choices, no `Tavaline`; a new `Arvamuse tähtaeg` saved.
F  `L` — `✓ Tehtud` beside a task, `+ Lisa tegevus` without one.

The rules themselves are in tests/test_followup_ux_round.py.
"""

from __future__ import annotations

import json
import re
import time
from datetime import timedelta

import pytest
from django.utils import timezone
from playwright.sync_api import expect

from e2e.conftest import (
    MARTIN,
    add_panel_chip,
    create_matter,
    open_add_panel,
    set_next_step,
    sign_in,
    wait_for_htmx,
)
from e2e.titles import unique_title

NEWS_URL = "https://www.koda.ee/et/uudised/koja-hinnangul-naidis"
OVERVIEW_URL = "https://www.koda.ee/et/meie-moju/hetkel-kasil/avalda-arvamust-naidis"
LONG = (
    "Lorem ipsum dolor sit amet, consectetur adipiscing elit, sed do eiusmod tempor "
    "incididunt ut labore et dolore magna aliqua, ut enim ad minim veniam quis nostrud"
)


def _et(days: int) -> str:
    day = timezone.localdate() + timedelta(days=days)
    return f"{day.day}.{day.month}.{day.year}"


def _answers(page, replies: dict[str, dict], delays: dict[str, float] | None = None) -> None:
    """Answer `preview_website_overview` here, so no test contacts koda.ee.

    The JSON is the endpoint's own shape — the address echoed back, which is
    what the page checks an answer against; ``delays`` holds an answer back,
    which is how a slow reply for an older address is made to arrive last. An
    address not listed answers as another site does: no kind, no title.
    """

    def handle(route):
        posted = route.request.post_data_buffer.decode("utf-8", "replace")
        found = re.search(r'name="url"\s+(\S*)', posted)
        address = found.group(1) if found else ""
        reply = replies.get(address, {"kind": "", "title": "", "title_status": ""})
        if delays and address in delays:
            time.sleep(delays[address])
        route.fulfill(
            status=200, content_type="application/json", body=json.dumps({"url": address, **reply})
        )

    page.route("**/lisa/koduleht/eelvaade/", handle)


def _publication_form(page):
    open_add_panel(page, "lisa-koduleht")
    return page.locator("#lisa-koduleht form")


# ---------------------------------------------------------------------------
# A. `+ Ülevaade / uudis`
# ---------------------------------------------------------------------------


def test_a_the_link_comes_first_and_the_preview_fills_the_form(page, base_url):
    sign_in(page, base_url, MARTIN)
    create_matter(page, base_url, unique_title("Eelvaade"), owner=MARTIN)
    _answers(
        page,
        {NEWS_URL: {"kind": "NEWS", "title": "Koja hinnangul näidis", "title_status": "found"}},
    )

    form = _publication_form(page)
    labels = [
        text.split("\n")[0].rstrip("*").strip()
        for text in form.locator(".cx-f__lab").all_inner_texts()
    ]
    assert [label.casefold() for label in labels[:4]] == ["link", "kuupäev", "pealkiri", "liik"]
    expect(form.locator("[name=published_on]")).to_have_value(_et(0))

    form.locator("[name=url]").fill(NEWS_URL)
    form.locator("[name=url]").dispatch_event("change")

    # Before the save: the title, and `Uudis` chosen.
    expect(form.locator("[name=overview_title]")).to_have_value("Koja hinnangul näidis")
    expect(form.locator("[name=kind][value=NEWS]")).to_be_checked()
    form.locator("[name=overview_title]").fill("Koja hinnangul näidis — parandatud")
    expect(form.locator("[name=overview_title]")).to_have_value(
        "Koja hinnangul näidis — parandatud"
    )


def test_a_a_slow_answer_for_an_older_address_never_overwrites_a_newer_one(page, base_url):
    sign_in(page, base_url, MARTIN)
    create_matter(page, base_url, unique_title("Eelvaate võidujooks"), owner=MARTIN)
    _answers(
        page,
        {
            OVERVIEW_URL: {
                "kind": "OVERVIEW",
                "title": "Vana aadressi pealkiri",
                "title_status": "found",
            },
            NEWS_URL: {"kind": "NEWS", "title": "Uue aadressi pealkiri", "title_status": "found"},
        },
        delays={OVERVIEW_URL: 2.0},
    )
    form = _publication_form(page)
    address = form.locator("[name=url]")

    address.fill(OVERVIEW_URL)
    address.dispatch_event("change")
    address.fill(NEWS_URL)
    address.dispatch_event("change")
    expect(form.locator("[name=overview_title]")).to_have_value("Uue aadressi pealkiri")
    page.wait_for_timeout(2500)

    expect(form.locator("[name=overview_title]")).to_have_value("Uue aadressi pealkiri")
    expect(form.locator("[name=kind][value=NEWS]")).to_be_checked()


def test_a_a_typed_title_is_kept_and_an_unknown_address_asks(page, base_url):
    sign_in(page, base_url, MARTIN)
    create_matter(page, base_url, unique_title("Eelvaade ja oma pealkiri"), owner=MARTIN)
    _answers(page, {NEWS_URL: {"kind": "NEWS", "title": "Lehe pealkiri", "title_status": "found"}})
    form = _publication_form(page)

    form.locator("[name=overview_title]").fill("Minu pealkiri")
    form.locator("[name=url]").fill(NEWS_URL)
    form.locator("[name=url]").dispatch_event("change")
    expect(form.locator("[name=kind][value=NEWS]")).to_be_checked()
    expect(form.locator("[name=overview_title]")).to_have_value("Minu pealkiri")

    # Another site states no kind: the one this preview chose is un-chosen.
    form.locator("[name=url]").fill("https://www.mkm.ee/uudised/naidis")
    form.locator("[name=url]").dispatch_event("change")
    expect(form.locator("[name=kind]:checked")).to_have_count(0)
    form.get_by_role("button", name="Lisa ülevaade / uudis").click()
    wait_for_htmx(page)
    expect(page.locator("#lisa-koduleht")).to_contain_text("Vali, kas see on ülevaade või uudis.")


# ---------------------------------------------------------------------------
# C. Long rows
# ---------------------------------------------------------------------------


def _box(locator) -> dict:
    return locator.evaluate(
        "(e) => { const r = e.getBoundingClientRect();"
        " return {left: r.left, right: r.right, top: r.top, bottom: r.bottom}; }"
    )


@pytest.mark.parametrize("width", [1440, 375])
def test_c_long_rows_keep_their_columns_and_their_controls(page, base_url, width):
    page.set_viewport_size({"width": width, "height": 1000})
    sign_in(page, base_url, MARTIN)
    create_matter(page, base_url, unique_title("Pikad read"), owner=MARTIN)
    set_next_step(page, f"Koosta mustand — {LONG}", _et(2))
    for days, words in (
        (9, f"Saada vastus ministeeriumile — {LONG}"),
        (11, f"Helista juristile — {LONG}"),
    ):
        open_add_panel(page, "lisa-planeeritud")
        page.locator("#lisa-planeeritud [name=text]").fill(words)
        page.locator("#lisa-planeeritud [name=target_date]").fill(_et(days))
        page.locator("#lisa-planeeritud button[type=submit]").click()
        page.wait_for_load_state("networkidle")

    zone = page.locator("#praegune-tegevus")
    task = zone.locator(".curact__task").first
    date, text = _box(task.locator(".curact__date")), _box(task.locator(".curact__text"))
    assert date["right"] <= text["left"], (date, text)
    assert text["bottom"] - text["top"] > 30, "the long current action does not wrap"

    rows = zone.locator(".curact__plannedrow")
    expect(rows).to_have_count(2)
    first, second = _box(rows.nth(0)), _box(rows.nth(1))
    assert first["bottom"] <= second["top"] + 1
    for index in range(2):
        row = rows.nth(index)
        bounds = _box(row)
        row_date, row_text = (
            _box(row.locator(".curact__planneddate")),
            _box(row.locator(".curact__plannedtext")),
        )
        assert row_date["right"] <= row_text["left"]
        assert row_text["bottom"] - row_text["top"] > 30, "a long planned action does not wrap"
        for control in (
            row.locator(".curact__edit > summary"),
            row.locator(".curact__dismiss button"),
        ):
            box = _box(control)
            assert bounds["top"] <= box["top"] and box["bottom"] <= bounds["bottom"], (
                index,
                box,
                bounds,
            )
    border = rows.nth(1).evaluate("(e) => getComputedStyle(e).borderTopStyle")
    assert border == "solid"
    assert (
        page.evaluate("document.documentElement.scrollWidth - document.documentElement.clientWidth")
        <= 0
    )


# ---------------------------------------------------------------------------
# D. `+ Lisa`
# ---------------------------------------------------------------------------


def test_d_lisa_has_four_choices_and_saves_a_new_deadline(page, base_url):
    sign_in(page, base_url, MARTIN)
    create_matter(page, base_url, unique_title("Uus arvamuse tähtaeg"), owner=MARTIN)

    add_panel_chip(page, "lisa-marge").click()
    chips = page.locator("#lisa-marge .cx-panels--sub > label.disclosure-chip").all_inner_texts()
    assert [chip.strip() for chip in chips] == [
        "Arvamuse tähtaeg",
        "Oluline tähtaeg",
        "Jõustumine",
        "Töövõit",
    ]
    expect(page.locator("#marge-tavaline")).to_have_count(0)

    open_add_panel(page, "marge-arvamuse-tahtaeg")
    page.locator("#id_response_deadline_date").fill(_et(30))
    page.locator("#marge-arvamuse-tahtaeg button[type=submit]").click()
    wait_for_htmx(page)

    expect(page.locator("#teema-pais")).to_contain_text(f"Arvamuse tähtaeg {_et(30)}")
    # A current request now: the panel — `Arvamuse tähtaeg` is still the chosen
    # child — names it instead of drawing a second form.
    add_panel_chip(page, "lisa-marge").click()
    expect(
        page.locator("#marge-arvamuse-tahtaeg [data-current-response-deadline]")
    ).to_contain_text(_et(30))
    expect(page.locator("#marge-arvamuse-tahtaeg form")).to_have_count(0)


# ---------------------------------------------------------------------------
# F. `L`
# ---------------------------------------------------------------------------


def test_f_l_opens_lisa_tegevus_without_a_task_and_tehtud_with_one(page, base_url):
    sign_in(page, base_url, MARTIN)
    create_matter(page, base_url, unique_title("L-klahv"), owner=MARTIN)

    page.locator("body").press("l")
    expect(page.locator("#lisa-jargmine")).to_have_attribute("open", "")
    assert page.evaluate("() => !!document.activeElement.closest('#lisa-jargmine')")
    expect(page.locator("#lisa-marge")).to_be_hidden()

    set_next_step(page, "Loe eelnõu läbi", _et(3))
    page.locator("body").press("l")
    assert page.evaluate("() => document.activeElement.id") == "id_praegune_body"
    expect(page.locator("#lisa-marge")).to_be_hidden()
