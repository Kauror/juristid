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

from e2e.conftest import MARTIN, open_valdkond, sign_in

pytestmark = pytest.mark.e2e

CREATE_PATH = "/teemad/uus/"
NARROW = {"width": 420, "height": 900}

VALDKOND = "[data-valdkond-disclosure]"
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


def test_valdkond_arrives_shut_and_opens_when_asked(page, base_url):
    """The vocabulary is behind a door, and the door opens."""
    create_form(page, base_url)

    disclosure = page.locator(VALDKOND)
    expect(disclosure).to_be_visible()
    assert not disclosure.evaluate("node => node.open"), "Valdkonnad is unfolded on arrival"
    expect(page.locator('label.chip:has(input[name="policy_areas"])').first).to_be_hidden()

    open_valdkond(page)

    expect(page.locator('label.chip:has(input[name="policy_areas"])').first).to_be_visible()


def test_the_summary_says_what_has_been_chosen(page, base_url):
    """A shut field that hid the answer would cost a click on every visit."""
    create_form(page, base_url)
    open_valdkond(page)

    chips = page.locator('label.chip:has(input[name="policy_areas"])')
    first = (chips.nth(0).locator(".chip__name").text_content() or "").strip().rstrip("×").strip()
    second = (chips.nth(1).locator(".chip__name").text_content() or "").strip().rstrip("×").strip()

    chips.nth(0).click()
    summary = page.locator(f"{VALDKOND} > summary")
    assert first in (summary.inner_text() or "")

    chips.nth(1).click()
    text = summary.inner_text() or ""
    assert first in text and second in text, f"the summary lost one of the two: {text!r}"


def test_the_vocabulary_is_reachable_and_choosable_by_keyboard(page, base_url):
    """A `<details>` is a button and a region, and Enter is how it opens."""
    create_form(page, base_url)

    summary = page.locator(f"{VALDKOND} > summary")
    summary.focus()
    page.keyboard.press("Enter")
    assert page.locator(VALDKOND).evaluate("node => node.open")

    first = page.locator('input[name="policy_areas"]').first
    first.focus()
    page.keyboard.press("Space")
    expect(first).to_be_checked()


def test_the_open_vocabulary_fits_a_narrow_screen(page, base_url):
    """Nineteen labels and `Muu`, at the width nobody has looked at."""
    create_form(page, base_url, NARROW)
    open_valdkond(page)

    assert not overflows(page)
    assert on_screen(page, page.locator(f"{VALDKOND} > summary"), width=420)
    assert on_screen(
        page, page.locator('label.chip:has(input[name="policy_areas"])').first, width=420
    )
