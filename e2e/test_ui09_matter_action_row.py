"""UI-09 in a real browser: one action row under `PRAEGUNE TEGEVUS`.

What only a running page can settle (the markup, order and permissions are
`tests/test_ui09_matter_action_row.py`):

* the six controls read in order, left to right and line by line, at desktop
  and phone widths — wrapping moves a control only to the start of the next
  line — and `Lõpeta teema` is last, at the right end of its line;
* opening a choice in the row moves none of its chips and does not displace
  the step's toolbar above it;
* the row works on a Matter with no current action: `+ Lisa tegevus` opens the
  next-step editor there;
* a closed Matter and a read-only reader get no row.
"""

from __future__ import annotations

import itertools
from urllib.parse import quote

import pytest
from playwright.sync_api import expect

from e2e.conftest import READER, SANDRA, create_matter, sign_in, unique_title

pytestmark = pytest.mark.e2e

ROW_ORDER = [
    "+ Lisa tegevus",
    "+ Ülevaade / uudis",
    "+ Kaasamine",
    "+ Koja arvamus / kohtumine",
    "+ Lisa",
    "Lõpeta teema",
]

#: The seeded closed file (app/core/management/commands/seed_e2e_data.py).
CLOSED_TITLE = "Lõpetatud sünteetiline teema"
#: A seeded open file with a current step, read but never written here.
REVIEW_DUE_TITLE = "Ootamise ülevaatuse aeg on käes"


def _open_seeded(page, base_url: str, title: str) -> None:
    """A seeded Teema, found in the register by its whole title.

    `open_matter` searches the first word only, and «Lõpetatud» also matches
    every file a long-lived browser world has closed since it was seeded.
    """
    page.goto(f"{base_url}/teemad/?olek=koik&q={quote(title)}")
    page.wait_for_load_state("networkidle")
    link = page.get_by_role("link", name=title, exact=True).first
    page.goto(f"{base_url}{link.get_attribute('href')}")
    page.wait_for_load_state("networkidle")


def _chips(page) -> list[dict]:
    return page.evaluate(
        """() => Array.from(
            document.querySelectorAll('#lisa-teemale > .cx-panels > label.disclosure-chip')
        ).map((chip) => {
            const b = chip.getBoundingClientRect();
            return {text: chip.textContent.trim(), x: Math.round(b.x), y: Math.round(b.y),
                    right: Math.round(b.right)};
        })"""
    )


@pytest.mark.parametrize("width", [1440, 768, 390])
def test_the_row_reads_in_order_and_lopeta_teema_ends_it(page, base_url, width):
    sign_in(page, base_url, SANDRA)
    page.set_viewport_size({"width": width, "height": 900})
    create_matter(page, base_url, unique_title("UI-09 tegevusrida"))

    chips = _chips(page)
    assert [chip["text"] for chip in chips] == ROW_ORDER
    # Reading order is DOM order: a chip is either to the right of the one
    # before it on the same line, or on a later line.
    for before, after in itertools.pairwise(chips):
        assert after["y"] > before["y"] or (after["y"] == before["y"] and after["x"] > before["x"])
    # `Lõpeta teema` is at the right end of its line: nothing on that line is
    # right of it, and it reaches the row's right edge.
    last = chips[-1]
    row = page.locator("#lisa-teemale > .cx-panels").bounding_box()
    assert abs((row["x"] + row["width"]) - last["right"]) <= 2
    overflow = page.evaluate(
        "() => document.documentElement.scrollWidth - document.documentElement.clientWidth"
    )
    assert overflow <= 1, f"the page scrolls sideways by {overflow}px at {width}"


def test_opening_a_choice_moves_no_chip_and_not_the_toolbar(page, base_url):
    sign_in(page, base_url, SANDRA)
    _open_seeded(page, base_url, REVIEW_DUE_TITLE)
    toolbar = page.locator("#praegune-tegevus .curact__tools")
    resting_toolbar = toolbar.bounding_box()
    resting = _chips(page)

    for radio in ("lisa-planeeritud", "lisa-kaasamine", "teema-lopeta", "lisa-marge"):
        page.locator(f'#lisa-teemale label[for="{radio}-valik"]').click()
        expect(page.locator(f"#{radio}")).to_be_visible()
        assert _chips(page) == resting, f"opening #{radio} moved a chip"
        assert toolbar.bounding_box() == resting_toolbar, f"opening #{radio} moved the toolbar"
    # One choice at a time in the row, as before.
    expect(page.locator("#lisa-kaasamine")).to_be_hidden()
    # Nothing was opened above it.
    for panel in ("tehtud", "vaatasin-ule", "lisa-jargmine"):
        expect(page.locator(f"#{panel}")).to_be_hidden()


def test_lisa_tegevus_works_with_no_current_action(page, base_url):
    sign_in(page, base_url, SANDRA)
    create_matter(page, base_url, unique_title("UI-09 tegevuseta"))
    expect(page.locator("#praegune-tegevus")).to_contain_text("Järgmine samm on määramata")

    page.locator('#lisa-teemale label[for="lisa-jargmine-valik"]').click()
    editor = page.locator("#lisa-jargmine")
    editor.locator("[name='text']").fill("Helista ministeeriumile")
    with page.expect_response(
        lambda response: response.url.endswith("/jargmiseks/") and response.request.method == "POST"
    ) as caught:
        editor.get_by_role("button", name="Salvesta", exact=True).click()
    assert caught.value.status == 200
    page.wait_for_load_state("networkidle")

    zone = page.locator("#praegune-tegevus")
    expect(zone.locator(".curact__text")).to_have_text("Helista ministeeriumile")
    # Now a step is current: the toolbar is on its line, and the row's
    # `+ Lisa tegevus` plans a dated action beside it.
    expect(zone.locator("[data-curact-toggle]")).to_have_count(2)
    expect(page.locator('#lisa-teemale label[for="lisa-planeeritud-valik"]')).to_have_text(
        "+ Lisa tegevus"
    )


def test_a_closed_matter_has_no_row(page, base_url):
    sign_in(page, base_url, SANDRA)
    _open_seeded(page, base_url, CLOSED_TITLE)
    expect(page.locator("#praegune-tegevus")).to_contain_text("teema on suletud")
    expect(page.locator("#lisa-teemale")).to_have_count(0)
    expect(page.locator("#praegune-tegevus [data-curact-toggle]")).to_have_count(0)


def test_a_reader_has_no_row_and_no_toolbar(page, base_url):
    sign_in(page, base_url, READER)
    _open_seeded(page, base_url, REVIEW_DUE_TITLE)
    expect(page.locator("#praegune-tegevus .curact__text")).to_have_text(
        "Ootame ministeeriumi vastust"
    )
    expect(page.locator("#lisa-teemale")).to_have_count(0)
    expect(page.locator("#praegune-tegevus [data-curact-toggle]")).to_have_count(0)
    expect(page.locator("#praegune-tegevus form")).to_have_count(0)
