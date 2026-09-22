"""An undated next step, in a real browser (docs/adr/0106).

`tests/test_undated_next_actions.py` holds the whole contract and runs
everywhere cheaply. This file holds the three things only a rendered page can
settle:

* that the step can be **recorded from the UI** with the day box left alone, and
  that `PRAEGUNE TEGEVUS` then says «Kuupäev määramata» rather than trailing off
  after the sentence or borrowing the overdue colour;
* that it **reaches `Minu asjad`**, which is the claim docs/adr/0105 §4 got
  wrong and refused the whole feature over;
* that «Kuupäev määramata» does not wrap badly or push the page sideways at
  phone width, which is the one risk of adding words to a compact row.

Each test files its own Matter: these write Matter-level facts and the seeded
world is shared across a shard.
"""

from __future__ import annotations

import pytest
from playwright.sync_api import expect

from e2e.conftest import SANDRA, create_matter, open_add_panel, sign_in, unique_title

pytestmark = pytest.mark.e2e

UNDATED_LABEL = "Kuupäev määramata"


def _record_undated_step(page, text: str) -> None:
    """Through `+ Märge`, with the day box left alone — the ordinary route."""
    open_add_panel(page, "marge-tavaline")
    form = page.locator("#marge-tavaline")
    form.locator("[name=next_text]").fill(text)
    form.get_by_role("button", name="Salvesta", exact=True).click()
    page.wait_for_load_state("networkidle")


def test_a_step_saves_with_the_day_box_left_alone(page, base_url):
    """The gesture the round is for: type the sentence, press save."""
    sign_in(page, base_url, SANDRA)
    create_matter(page, base_url, unique_title("Kuupäevata samm"), owner=SANDRA)

    _record_undated_step(page, "Vaatan ministeeriumi vastuse üle")

    current = page.locator("#praegune-tegevus")
    expect(current).to_contain_text("Vaatan ministeeriumi vastuse üle")
    expect(current).to_contain_text(UNDATED_LABEL)
    # Muted, never the overdue grammar: a step with no deadline recorded cannot
    # be late, and a red word here would be the page inventing a problem.
    expect(current.locator(".curact__date--unset")).to_have_count(1)
    expect(current.locator(".curact__date--overdue")).to_have_count(0)
    # And no day was manufactured to fill the cell.
    expect(current.locator(".curact__date")).not_to_contain_text("20")


def test_a_day_can_be_added_afterwards_and_taken_off_again(page, base_url):
    """The round trip, through `Muuda` — the same control either way.

    Not two gestures and not a «remove the date» button: setting, changing and
    clearing a step's day are one act through one form, which is what keeps the
    replacement chain honest (docs/adr/0106 §4).
    """
    sign_in(page, base_url, SANDRA)
    create_matter(page, base_url, unique_title("Kuupäev hiljem"), owner=SANDRA)
    _record_undated_step(page, "Vaatan uue versiooni üle")

    current = page.locator("#praegune-tegevus")
    expect(current).to_contain_text(UNDATED_LABEL)

    # Add one.
    current.locator("#lisa-jargmine > summary").click()
    page.locator("#lisa-jargmine [name='text']").fill("Vaatan uue versiooni üle")
    page.locator("#id_target_date").fill("30.09.2026")
    page.locator("#lisa-jargmine button[type=submit]").click()
    page.wait_for_load_state("networkidle")
    expect(page.locator("#praegune-tegevus")).to_contain_text("30.9.2026")
    expect(page.locator("#praegune-tegevus")).not_to_contain_text(UNDATED_LABEL)

    # And take it back off, by emptying the box the editor opened on.
    page.locator("#praegune-tegevus #lisa-jargmine > summary").click()
    expect(page.locator("#id_target_date")).to_have_value("30.9.2026")
    page.locator("#id_target_date").fill("")
    page.locator("#lisa-jargmine button[type=submit]").click()
    page.wait_for_load_state("networkidle")

    expect(page.locator("#praegune-tegevus")).to_contain_text(UNDATED_LABEL)
    expect(page.locator("#praegune-tegevus")).not_to_contain_text("30.9.2026")


def test_an_undated_step_reaches_minu_asjad(page, base_url):
    """The claim docs/adr/0105 §4 refused the feature over, in a browser.

    «A dateless step appears in nobody's Minu asjad» — it appears in the
    `Kuupäevata` block, which the page has had since it was built.
    """
    sign_in(page, base_url, SANDRA)
    create_matter(page, base_url, unique_title("Kuupäevata Minu asjades"), owner=SANDRA)
    _record_undated_step(page, "Helistan ministeeriumisse")

    page.goto(f"{base_url}/minu-asjad/")
    page.wait_for_load_state("networkidle")

    block = page.locator("section[aria-label='Kuupäevata']")
    expect(block).to_be_visible()
    expect(block).to_contain_text("Helistan ministeeriumisse")
    # It is work, not a warning: nothing in the block claims it is late.
    expect(block).not_to_contain_text("üle aja")
    expect(block).not_to_contain_text("hilinenud")


def test_the_portfolio_row_says_the_day_is_not_recorded(page, base_url):
    """A row ending at the sentence reads as one whose date failed to render."""
    sign_in(page, base_url, SANDRA)
    title = unique_title("Portfelli rida")
    create_matter(page, base_url, title, owner=SANDRA)
    _record_undated_step(page, "Küsin ministeeriumilt selgitust")

    page.goto(f"{base_url}/minu-asjad/")
    page.wait_for_load_state("networkidle")

    row = page.locator(".pw-matter", has_text=title).first
    expect(row).to_contain_text("Küsin ministeeriumilt selgitust")
    expect(row).to_contain_text("kuupäev määramata")
    # And it is not the «no step at all» grammar, which is a gap somebody may
    # want to close — this is a step.
    expect(row.locator(".pw-matter__next--none")).to_have_count(0)


@pytest.mark.parametrize("width", [375, 420, 768, 1440])
def test_the_unset_label_does_not_break_the_layout(page, base_url, width):
    """Adding words to a compact row is the one layout risk this round carries."""
    sign_in(page, base_url, SANDRA)
    create_matter(page, base_url, unique_title("Kuupäevata laius"), owner=SANDRA)
    _record_undated_step(page, "Vaatan ministeeriumi pika vastuse põhjalikult üle")

    page.set_viewport_size({"width": width, "height": 900})
    page.wait_for_timeout(120)

    overflow = page.evaluate(
        "() => document.documentElement.scrollWidth - document.documentElement.clientWidth"
    )
    assert overflow <= 0, f"the document scrolls sideways by {overflow}px at {width}px"
    expect(page.locator("#praegune-tegevus")).to_contain_text(UNDATED_LABEL)
