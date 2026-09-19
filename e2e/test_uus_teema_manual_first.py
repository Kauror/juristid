"""`Uus teema`, made manual again — in the browser a lawyer actually uses.

`tests/test_uus_teema_manual_first.py` pins the same three changes on the
server, where most of them are decidable. Three things are not, and they are
what this file is for (docs/adr/0088):

* a control that is *in the document* and a control that is *on the screen* are
  the same thing to a GET and different things to a person. Every assertion
  below reads visibility or a bounding box rather than presence;
* «type and the matching bodies appear» is a script, and a script either runs
  or it does not;
* the fold, the summary and the search have to work by keyboard and at the
  width the department's laptops are not, which is the only place a compact
  control can turn out to be a cramped one.
"""

from __future__ import annotations

import pytest
from playwright.sync_api import expect

from e2e.conftest import MARTIN, VALDKONNAD_FIELD, open_valdkond, sign_in

pytestmark = pytest.mark.e2e

CREATE_PATH = "/teemad/uus/"
NARROW = {"width": 420, "height": 900}

VALDKOND = VALDKONNAD_FIELD
SENDER_CHIPS = "#saatja-valik label.chip"


def create_form(page, base_url, viewport=None) -> None:
    sign_in(page, base_url, MARTIN)
    page.set_viewport_size(viewport or {"width": 1440, "height": 900})
    page.goto(f"{base_url}{CREATE_PATH}")
    expect(page.get_by_role("heading", name="Uus teema")).to_be_visible()


def overflows(page) -> bool:
    return page.evaluate(
        "() => document.documentElement.scrollWidth > document.documentElement.clientWidth + 1"
    )


def on_screen(page, locator, *, width: int) -> bool:
    """On the screen and within it, rather than merely attached to it."""
    box = locator.bounding_box()
    if box is None:
        return False
    return (
        box["width"] > 0
        and box["height"] > 0
        and box["x"] >= -1
        and box["x"] + box["width"] <= width + 1
    )


# ---------------------------------------------------------------------------
# The reading is not on this page
# ---------------------------------------------------------------------------


def test_choosing_a_file_shows_no_suggestion_panel(page, base_url, tmp_path):
    """The withdrawal, in the state that used to produce the panel.

    A server-side assertion about a page with no file proves the panel is not
    rendered *unconditionally*. This is the other half: a real file goes up
    through the real staging route, and nothing appears above the fields.
    """
    attachment = tmp_path / "kaaskiri.pdf"
    attachment.write_bytes(
        b"%PDF-1.4\n1 0 obj<</Type/Catalog>>endobj\ntrailer<</Root 1 0 R>>\n%%EOF\n"
    )

    create_form(page, base_url)
    page.locator("#id_files").set_input_files([str(attachment)])
    expect(page.locator(".dropzone__file")).to_have_count(1)

    # Long enough for the poll that used to run every 1.2 s to have run several
    # times, and for the reader to have finished a one-page PDF.
    page.wait_for_timeout(4000)

    expect(page.locator(".suggpanel")).to_have_count(0)
    expect(page.locator(".intakepanel__reading")).to_have_count(0)
    expect(page.locator("[data-prefill-for]")).to_have_count(0)
    assert page.locator("#id_title").input_value() == "", (
        "something wrote into the title box while nobody was looking"
    )


# ---------------------------------------------------------------------------
# Saatja
# ---------------------------------------------------------------------------


def test_saatja_is_an_empty_box_until_somebody_types(page, base_url):
    """Nothing under the field, and the catalogue one keystroke away."""
    create_form(page, base_url)

    assert page.locator(f"{SENDER_CHIPS}:not([hidden])").count() == 0
    assert page.locator(SENDER_CHIPS).count() > 0, "the catalogue is not in the document at all"

    box = page.locator("#saatja-otsi")
    box.click()
    box.fill("näidis")

    results = page.locator("#saatja-tulemused")
    expect(results).to_be_visible()
    assert results.locator("[role=option]").count() > 0


def test_a_sender_chosen_by_keyboard_alone_becomes_a_visible_chip(page, base_url):
    """The whole path, without a pointer: type, arrow down, Enter."""
    create_form(page, base_url)

    box = page.locator("#saatja-otsi")
    box.click()
    box.type("näidis")
    page.keyboard.press("ArrowDown")
    page.keyboard.press("Enter")

    chosen = page.locator(f"{SENDER_CHIPS}:not([hidden])")
    expect(chosen.first).to_be_visible()
    assert chosen.count() == 1, "choosing one sender should show one sender"
    expect(box).to_have_value("")


def test_the_quiet_sender_field_fits_a_narrow_screen(page, base_url):
    """A compact control that pushed the page sideways would be a worse one."""
    create_form(page, base_url, NARROW)

    assert not overflows(page)
    assert on_screen(page, page.locator("#saatja-otsi"), width=420)

    box = page.locator("#saatja-otsi")
    box.click()
    box.fill("näidis")
    expect(page.locator("#saatja-tulemused")).to_be_visible()
    assert not overflows(page), "the results list took the page sideways"


# ---------------------------------------------------------------------------
# Valdkond
# ---------------------------------------------------------------------------


def test_valdkond_is_drawn_at_rest_and_needs_no_opening(page, base_url):
    """The vocabulary is on the page, and so is every one of its chips.

    Three shapes have stood here: drawn permanently, folded behind a
    `<details>` (docs/adr/0088 §3), and a `chipmenu` overlaying the form
    (docs/adr/0094 §2). The owner's live audit put it back to the first, and
    what this asserts is the difference that matters to a reader — the chips are
    visible without anything being pressed (docs/adr/0096 §2).
    """
    create_form(page, base_url)

    block = page.locator(VALDKOND).first
    expect(block).to_be_visible()
    assert block.locator("summary").count() == 0, "Valdkonnad grew a trigger again"
    expect(page.locator('label.chip:has(input[name="policy_areas"])').first).to_be_visible()


def test_the_count_beside_the_label_says_how_many_have_been_chosen(page, base_url):
    """The answer, where the question is, and it keeps up with the ticking.

    The count moved from a menu trigger to the `field__count` badge beside the
    legend, which is where `Õigusakt` and `Sildid` have always carried theirs.
    Ticking twice also proves the control is multi-select: `Matter.policy_areas`
    holds several and nothing closes after the first.
    """
    create_form(page, base_url)
    open_valdkond(page)

    chips = page.locator('label.chip:has(input[name="policy_areas"])')
    count = page.locator('[data-chipcount-for="policy_areas"]')

    chips.nth(0).click()
    expect(count).to_contain_text("1")

    chips.nth(1).click()
    expect(count).to_contain_text("2")

    # And unticking leaves the other one alone.
    chips.nth(0).click()
    expect(count).to_contain_text("1")
    expect(chips.nth(1).locator("input")).to_be_checked()


def test_the_vocabulary_is_choosable_by_keyboard(page, base_url):
    """A checkbox group is one tab stop per box, and Space takes one.

    There is no trigger to reach first any more, which is the point: what the
    menu added — Enter to open, Escape to shut, a focus stop of its own — is
    behaviour a plain fieldset never needed (docs/adr/0096 §2).
    """
    create_form(page, base_url)

    first = page.locator('input[name="policy_areas"]').first
    first.focus()
    page.keyboard.press("Space")
    expect(first).to_be_checked()


def test_the_vocabulary_fits_a_narrow_screen(page, base_url):
    """Twenty-one labels and `Muu`, wrapped, at the width nobody looks at.

    Drawn in the flow the page is simply taller, so the claim is about width:
    no chip may hang off the right edge and the document may not scroll
    sideways.
    """
    create_form(page, base_url, NARROW)
    open_valdkond(page)

    assert not overflows(page)
    assert on_screen(page, page.locator(VALDKOND).first, width=420)
    assert on_screen(
        page, page.locator('label.chip:has(input[name="policy_areas"])').first, width=420
    )
