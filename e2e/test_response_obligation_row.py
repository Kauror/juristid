"""The plan and the obligation, as two lines a reader can actually tell apart.

`tests/test_response_obligation_display.py` holds what each surface *says*. What
only a browser answers is whether the second line reads as a note under the
first or as a competing instruction beside it — and whether adding a line to a
nine-column register cell costs the page its layout on a laptop or a phone.

The world is seeded, not built here. `SUPERSEDED_DEADLINE_TITLE` is the exact
case this reading exists for and `seed_e2e_data` has carried it since Stage 2F:

    response_deadline   200 days past, nothing sent, no `VÄLJA` mark
    Järgmiseks          «Jälgin menetluse jätkumist», 40 days ahead

Under the operational rule that file is **not late** — its lawyer said what
happens next — and `e2e/test_department_page.py` asserts exactly that about the
same row. What it also is, and what nothing said until now, is unanswered: the
ministry is still waiting. Both readings, on one row, is the thing to look at.

The dates are deliberately not asserted as strings. `200 p üle` is stable — the
seed anchors it — but the day itself moves every morning, and a browser test
that pinned it would be red by Thursday.
"""

from __future__ import annotations

import pytest
from playwright.sync_api import expect

from app.core.management.commands.seed_e2e_data import (
    SUPERSEDED_DEADLINE_DAYS,
    SUPERSEDED_DEADLINE_TITLE,
)
from e2e.conftest import SANDRA, sign_in

pytestmark = pytest.mark.e2e

#: Every row, every status — the same scope `e2e/test_register_columns.py` reads,
#: and for its reason: the browser suite shares one seeded database per shard, so
#: page one is whatever the files before this one happened to file.
ALL_ROWS = "/teemad/?olek=koik&kaupa=koik"

OWED_LABEL = "Arvamuse tähtaeg"
LATE = f"{SUPERSEDED_DEADLINE_DAYS} p üle"


def open_register(page, base_url: str, query: str = ALL_ROWS) -> None:
    page.goto(f"{base_url}{query}")
    page.wait_for_load_state("networkidle")


def seeded_row(page):
    """The one register row for the seeded precedence case."""
    row = page.locator(".table--register tbody tr").filter(
        has=page.get_by_role("link", name=SUPERSEDED_DEADLINE_TITLE, exact=False)
    )
    expect(row).to_have_count(1)
    return row


def font_size(locator) -> float:
    return float(locator.evaluate("node => getComputedStyle(node).fontSize").removesuffix("px"))


def open_seeded_teema(page, base_url: str) -> None:
    """Follow the seeded row's own link, rather than clicking it.

    The register's table head is sticky and can sit over a row, so a click is a
    coin-toss that fails on a narrow viewport and passes on a wide one —
    `e2e/conftest.open_matter` carries the same reasoning.
    """
    href = (
        seeded_row(page).get_by_role("link", name=SUPERSEDED_DEADLINE_TITLE).get_attribute("href")
    )
    assert href, "the seeded row has no title link"
    page.goto(f"{base_url}{href}")
    page.wait_for_load_state("networkidle")


def document_overflows(page) -> bool:
    return page.evaluate(
        "() => document.documentElement.scrollWidth > document.documentElement.clientWidth + 1"
    )


# ---------------------------------------------------------------------------
# 1. The register row says both things, in the right order
# ---------------------------------------------------------------------------


def test_the_row_states_the_plan_and_then_what_is_still_owed(page, base_url):
    sign_in(page, base_url, SANDRA)
    open_register(page, base_url)
    cell = seeded_row(page).locator("td.table__date").first

    # The plan, unchanged: the step's own date, under the step's own meaning.
    expect(cell.locator(".datemeaning").first).to_have_text("Vaatan üle")
    # And the obligation, second.
    owed = cell.locator(".dateowed")
    expect(owed).to_have_count(1)
    expect(owed).to_contain_text(OWED_LABEL)
    expect(owed).to_contain_text(LATE)


def test_the_second_line_is_subordinate_to_the_first(page, base_url):
    """Smaller, and underneath. A second date at the same weight would leave the
    column asking the reader which of two instructions to follow."""
    sign_in(page, base_url, SANDRA)
    open_register(page, base_url)
    cell = seeded_row(page).locator("td.table__date").first

    primary = cell.locator(".dateline").first
    secondary = cell.locator(".dateowed")
    expect(primary).to_be_visible()
    expect(secondary).to_be_visible()

    top = primary.bounding_box()
    below = secondary.bounding_box()
    assert top is not None and below is not None
    assert below["y"] >= top["y"] + top["height"] - 1, "the obligation must sit under the plan"
    assert font_size(cell.locator(".dateowed__label")) < font_size(primary)


def test_the_row_is_still_not_in_the_registers_late_work(page, base_url):
    """The operational reading, re-asserted from the other side.

    The file carries «200 p üle» on its second line and is *not* late work: the
    `?too=` population is the plan's, and this round did not touch it. A row that
    started appearing here because it grew a sentence would be the defect
    ADR 0050 fixed, arriving through a template.
    """
    sign_in(page, base_url, SANDRA)
    open_register(page, base_url, "/teemad/?too=hilinenud&olek=koik&kaupa=koik")

    expect(
        page.locator(".table--register tbody tr").filter(
            has=page.get_by_role("link", name=SUPERSEDED_DEADLINE_TITLE, exact=False)
        )
    ).to_have_count(0)


def test_sorting_by_kuupaev_follows_the_plan_and_not_the_owed_date(page, base_url):
    """A 200-day-old deadline on the row must not drag it to the top of an
    ascending Kuupäev sort. The column sorts `register_display_date`, which is
    the step's date — the secondary line is text in a cell and nothing else."""
    sign_in(page, base_url, SANDRA)
    open_register(page, base_url, "/teemad/?jarjestus=kuupaev_asc&olek=koik&kaupa=koik")

    titles = [
        text.strip()
        for text in page.locator(".table--register tbody tr td.table__title").all_inner_texts()
    ]
    index = next(i for i, text in enumerate(titles) if SUPERSEDED_DEADLINE_TITLE in text)
    assert index > 0, "a row planned forty days out cannot be the earliest date on the register"


# ---------------------------------------------------------------------------
# 2. The extra line costs the page nothing
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("width", [1440, 1280, 1024, 420])
def test_the_register_never_scrolls_sideways_with_the_second_line_on_it(page, base_url, width):
    page.set_viewport_size({"width": width, "height": 900})
    sign_in(page, base_url, SANDRA)
    open_register(page, base_url)
    expect(seeded_row(page).locator(".dateowed")).to_be_visible()

    assert not document_overflows(page), f"the register scrolls sideways at {width}px"


@pytest.mark.parametrize("width", [1440, 420])
def test_the_second_line_stays_inside_its_own_cell(page, base_url, width):
    """The one layout failure a row-height change can hide: a line that renders
    fine and overhangs the column it belongs to."""
    page.set_viewport_size({"width": width, "height": 900})
    sign_in(page, base_url, SANDRA)
    open_register(page, base_url)
    cell = seeded_row(page).locator("td.table__date").first

    box = cell.bounding_box()
    owed = cell.locator(".dateowed").bounding_box()
    assert box is not None and owed is not None
    assert owed["x"] >= box["x"] - 1
    assert owed["x"] + owed["width"] <= box["x"] + box["width"] + 1


# ---------------------------------------------------------------------------
# 3. Teema says the same two things, in the same order
# ---------------------------------------------------------------------------


def test_the_teema_workspace_states_the_obligation_under_the_task(page, base_url):
    sign_in(page, base_url, SANDRA)
    open_register(page, base_url)
    open_seeded_teema(page, base_url)

    zone = page.locator("#praegune-tegevus")
    task = zone.locator(".curact__task")
    owed = zone.locator(".curact__owed")
    expect(task).to_contain_text("Jälgin menetluse jätkumist")
    expect(owed).to_contain_text(OWED_LABEL)
    expect(owed).to_contain_text(LATE)

    above = task.bounding_box()
    below = owed.bounding_box()
    assert above is not None and below is not None
    assert below["y"] >= above["y"] + above["height"] - 1
    assert font_size(zone.locator(".curact__owedlabel")) < font_size(zone.locator(".curact__text"))


def test_a_narrow_teema_keeps_the_obligation_on_the_page(page, base_url):
    page.set_viewport_size({"width": 420, "height": 900})
    sign_in(page, base_url, SANDRA)
    open_register(page, base_url)
    open_seeded_teema(page, base_url)

    owed = page.locator("#praegune-tegevus .curact__owed")
    expect(owed).to_be_visible()
    assert not document_overflows(page), "the Teema workspace scrolls sideways at 420px"
