"""The reduced classification area, in a real browser.

`tests/test_lawyer_classification_round.py` proves what a POST proves: which
fields exist, what is derived, what an edit may not rewrite, and that a stage
closes nothing. None of that needs a browser.

What does:

* **the page stops asking two of the questions**, and stops asking them
  visibly — a field that only a reading of the HTML can tell you has gone is not
  what the feedback was about;
* **the order reads as one block**: Saatja, Valdkond, Hetkeseis, Õigusakt, with
  nothing between them (docs/adr/0090 §7);
* **the reviewed vocabularies fit** at 1440 and at 420, keyboard-only, with no
  horizontal overflow — ten Õigusakt chips is more than that row held before;
* **the whole journey saves**, and the Teema that comes back says what was
  chosen. Scenarios A, B and F of the brief, walked rather than asserted.

`e2e/test_oigusakt_row.py` owns the Õigusakt row's own geometry and its `Muu`
reveal; `e2e/test_matter_form_ux.py` owns how a chip control reads. This file
owns the questions and their order.
"""

from __future__ import annotations

import re
import uuid

import pytest
from playwright.sync_api import expect

from e2e.conftest import (
    HETKESEIS_MENU,
    MARTIN,
    VALDKONNAD_MENU,
    open_hetkeseis,
    sign_in,
)

pytestmark = pytest.mark.e2e

CREATE_PATH = "/teemad/uus/"

MINISTRY = "Näidisministeerium"

#: The reviewed Hetkeseis vocabulary, in the order `workflow/0007` seeds it.
#: Restated here rather than read from the page, because "the page offers what
#: the department reviewed" is the claim and a list taken from the page would
#: assert it against itself.
STAGES = (
    "Idee",
    "Kooskõlastusringil",
    "Valitsuses",
    "Riigikogus",
    "Jõustumise ootel",
    "Jõustunud",
    "Eesti seisukoht koostamisel",
    "ELi menetluses",
    "ELi õiguse ülevõtmise ootel",
    "Muu",
)

#: The reviewed Õigusakt vocabulary, likewise.
INSTRUMENTS = (
    "VTK",
    "Seadus",
    "Määrus",
    "Koja ettepanek või pöördumine",
    "Strateegia, arengukava või tegevuskava",
    "Muu siseriiklik",
    "ELi konsultatsioon",
    "ELi direktiiv",
    "ELi määrus",
    "Muu ELi dokument",
)

STAGE_FIELD = 'fieldset.field:has(input[name="stage"])'
INSTRUMENT_FIELD = 'fieldset.field:has(input[name="legal_instruments"])'
SENDER_FIELD = 'fieldset.field:has(input[name="sender_name"])'
#: `Valdkonnad` and `Hetkeseis` answer through a menu since docs/adr/0094 §2,
#: so the *row* is the trigger and the fieldset lives inside the panel. Order and
#: overflow are about where the reader looks, so both are measured on the whole
#: `<details>`; the chip vocabularies are read inside it.
VALDKOND_FIELD = VALDKONNAD_MENU
STAGE_ROW = HETKESEIS_MENU


def create_form(page, base_url, width: int = 1440) -> None:
    sign_in(page, base_url, MARTIN)
    page.set_viewport_size({"width": width, "height": 900})
    page.goto(f"{base_url}{CREATE_PATH}")
    page.wait_for_load_state("networkidle")


def chip_names(page, field: str) -> list[str]:
    """The chip words in one field, in document order, without the clear mark."""
    return page.locator(f"{field} label.chip, {field} span.chip").evaluate_all(
        "nodes => nodes.map(node => {"
        "  const named = node.querySelector('.chip__name');"
        "  return ((named ? named.textContent : node.textContent) || '')"
        "    .replace(/\\s*×$/, '').trim(); })"
    )


def file_it(page, title: str) -> None:
    """Save the form with a first step, because every Teema this suite leaves
    behind is somebody else's fixture (`e2e/test_addressee_free_entry.py`).

    `Arvamuse tähtaeg` is how a Teema gets its first step now: `Järgmiseks` and
    its `Millal?` chips are off this page, and the one date establishes
    `Koostan arvamuse` (docs/adr/0094 §5, §6). Typed rather than picked from a
    chip, because there is no chip — and a fixed future date keeps this
    independent of the day the suite runs.
    """
    page.locator("#id_title").fill(title)
    page.fill("#id_response_deadline", "31.12.2027")
    page.get_by_role("button", name="Loo teema").click()
    page.wait_for_load_state("networkidle")
    complaints = page.locator(".field__error, .formerror").all_inner_texts()
    assert not complaints, f"the form refused: {complaints}"
    page.wait_for_url(re.compile(r"/teemad/[0-9a-f-]{36}/$"))


# ---------------------------------------------------------------------------
# The questions the page asks
# ---------------------------------------------------------------------------


def test_the_page_asks_neither_menetlusliik_nor_adressaat(page, base_url):
    """Gone from the page, not merely from the label (docs/adr/0090 §4, §5)."""
    create_form(page, base_url)

    body = page.locator("form.createform").inner_text()
    assert "Menetlusliik" not in body
    assert "Adressaat" not in body
    assert page.locator('[name="track"]').count() == 0
    assert page.locator('[name="addressee_organisation"]').count() == 0
    assert page.locator('[name="addressee_name"]').count() == 0
    assert page.locator("[data-addressee-disclosure]").count() == 0


def test_the_classification_block_reads_in_the_reviewed_order(page, base_url):
    """Saatja, Valdkond, Hetkeseis, Õigusakt — measured, not read off the DOM.

    A row can be a later sibling and still paint above, so where somebody reads
    it is the claim.
    """
    create_form(page, base_url)

    tops = []
    for selector in (SENDER_FIELD, VALDKOND_FIELD, STAGE_ROW, INSTRUMENT_FIELD):
        box = page.locator(selector).first.bounding_box()
        assert box is not None, f"{selector} has no box"
        tops.append(box["y"])

    assert tops == sorted(tops), f"the classification block reads out of order: {tops}"


def test_the_reviewed_vocabularies_are_what_the_page_offers(page, base_url):
    create_form(page, base_url)

    # The panel is in the document whether or not the menu is open, so the
    # vocabulary can be read without clicking anything.
    stages = chip_names(page, STAGE_FIELD)
    # Django's named blank option comes first and is a real answer here.
    assert stages[0] == "Määramata"
    assert tuple(stages[1:]) == STAGES

    assert tuple(chip_names(page, INSTRUMENT_FIELD)) == INSTRUMENTS


def test_the_stage_row_offers_no_closure_disguised_as_a_stage(page, base_url):
    """`Hetkeseis` answers where the *external* process stands, and only that.

    The feedback asked for «Rohkem ei tegele» here. It belongs to
    `Disposition.MONITORING_STOPPED` — a statement about this office rather than
    about the process — and ADR 0032 keeps the two apart on purpose. A chip
    meaning the second would put two questions in one column
    (docs/adr/0090 §1).

    Read off the chip *names*, not the fieldset's text: every chip carries the
    department's own explanation as a tooltip, and the sentence about `Idee`
    ends «…me rohkem ei tegele selle teemaga edasi» — so a substring search over
    the whole row finds those words in a chip that is not about them.
    """
    create_form(page, base_url)

    names = chip_names(page, STAGE_FIELD)
    assert "Rohkem ei tegele" not in names
    assert "Koda ei tegele edasi" not in names


# ---------------------------------------------------------------------------
# The journey — scenarios A, B and F
# ---------------------------------------------------------------------------


def test_an_ordinary_incoming_draft_files_and_reads_back(page, base_url):
    """Scenario A. Saatja, Valdkond, Hetkeseis, Õigusakt, and nothing else asked."""
    create_form(page, base_url)

    box = page.locator("#saatja-otsi")
    box.click()
    box.fill("Näidismin")
    page.locator("#saatja-tulemused").get_by_role("option", name=MINISTRY, exact=True).click()

    open_hetkeseis(page)
    page.locator(f"{STAGE_FIELD} label.chip, {STAGE_FIELD} span.chip").filter(
        has_text="Kooskõlastusringil"
    ).first.click()
    page.locator(f"{INSTRUMENT_FIELD} label.chip", has_text="Seadus").first.click()

    title = f"Tavaline saabunud eelnõu {uuid.uuid4().hex[:8]}"
    file_it(page, title)

    # Scenario F, on the Teema that came back: one obvious answer to «kes selle
    # meile saatis?», under one word (docs/adr/0090 §6).
    rail = page.locator("#teema-andmed")
    expect(rail.locator(".railcard__key").filter(has_text="Saatja")).to_have_count(1)
    expect(rail.locator(".railcard__key").filter(has_text="Kellelt")).to_have_count(0)
    values = rail.locator(".railcard__value")
    expect(values.filter(has_text=MINISTRY)).to_have_count(1)
    expect(values.filter(has_text="Seadus")).to_have_count(1)
    # `Menetlusliik` is a row here and it is *unanswered*: the page asked
    # nothing about it and nothing was inferred from `Seadus` (docs/adr/0090
    # §4). The rail renders an unanswered editable fact as «+ Lisa».
    menetlusliik = rail.locator(".railcard__row").filter(has_text="Menetlusliik")
    expect(menetlusliik).to_have_count(1)
    expect(menetlusliik).to_contain_text("+ Lisa")
    expect(page.locator(".metaline")).to_contain_text("Kooskõlastusringil")


def test_an_eu_matter_needs_no_second_european_question(page, base_url):
    """Scenario B. The EU-ness is in the type, and nothing is guessed from it."""
    create_form(page, base_url)

    open_hetkeseis(page)
    page.locator(f"{STAGE_FIELD} label.chip, {STAGE_FIELD} span.chip").filter(
        has_text="ELi menetluses"
    ).first.click()
    page.locator(f"{INSTRUMENT_FIELD} label.chip", has_text="ELi direktiiv").first.click()

    title = f"ELi direktiivi ettepanek {uuid.uuid4().hex[:8]}"
    file_it(page, title)

    # The *values* the rail states, not the whole card: the Menetlusliik row is
    # an inline editor whose `<select>` carries every `Track` label, so a text
    # assertion over the card would find «ELi algatus» in an option nobody
    # chose — and that is precisely the mistake this test is about.
    rail = page.locator("#teema-andmed")
    values = rail.locator(".railcard__value")
    expect(values.filter(has_text="ELi direktiiv")).to_have_count(1)
    # The type says the file is European. `Menetlusliik` stays unanswered,
    # because no instrument type entails a procedure (docs/adr/0090 §4).
    expect(values.filter(has_text="ELi algatus")).to_have_count(0)
    expect(values.filter(has_text="ELi õiguse ülevõtmine")).to_have_count(0)


def test_joustunud_files_an_open_teema(page, base_url):
    """Scenario E, seen: an act in force is a stage and not a closure.

    Scenario D — *Koda ei tegele edasi* — is a `Disposition` and reaches the
    record through `Lõpeta teema`, which `e2e/test_teema_closing_flow.py` drives.
    Nothing on this page asks it (docs/adr/0090 §1).
    """
    create_form(page, base_url)

    open_hetkeseis(page)
    page.locator(f"{STAGE_FIELD} label.chip, {STAGE_FIELD} span.chip").filter(
        has_text="Jõustunud"
    ).first.click()

    title = f"Jõustunud seadus {uuid.uuid4().hex[:8]}"
    file_it(page, title)

    expect(page.locator(".metaline")).to_contain_text("Jõustunud")
    header = page.locator("#teema-pais").inner_text()
    assert "Lõpetatud" not in header, "choosing the stage closed the Teema"
    assert "Arhiiv" not in header


# ---------------------------------------------------------------------------
# Scenario G — 420px and the keyboard
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("width", [1440, 1024, 420])
def test_the_classification_rows_never_take_the_page_sideways(page, base_url, width):
    """Ten stages and ten instruments, wrapped rather than scrolled."""
    create_form(page, base_url, width)

    overflow = page.evaluate(
        "() => document.documentElement.scrollWidth - document.documentElement.clientWidth"
    )
    assert overflow <= 1, f"the page scrolls sideways by {overflow}px at {width}"

    for selector in (STAGE_ROW, INSTRUMENT_FIELD):
        field = page.locator(selector).first.bounding_box()
        row = page.locator(f".createform__row:has({selector})").first.bounding_box()
        assert field is not None and row is not None
        assert field["width"] <= row["width"] + 2, f"{selector} is wider than its row at {width}px"

    # And with the menus open, which is the state a fold never had: an overlay
    # that is wider than its row would take the document sideways without
    # lengthening it, so nothing above would catch it (docs/adr/0094 §2).
    open_hetkeseis(page)
    page.locator(VALDKONNAD_MENU).locator("> summary").click()
    opened = page.evaluate(
        "() => document.documentElement.scrollWidth - document.documentElement.clientWidth"
    )
    assert opened <= 1, f"an open menu scrolls the page sideways by {opened}px at {width}"


@pytest.mark.parametrize("width", [1440, 420])
def test_both_vocabularies_are_answerable_from_the_keyboard(page, base_url, width):
    """Scenario G. Tab to the chip, Space to take it — at both widths.

    A radio group is one tab stop and a checkbox group is one per box, which is
    what the two controls promise about their data. What matters here is that
    neither needs a mouse.
    """
    create_form(page, base_url, width)

    # The trigger is one tab stop and opens on Space, which is what keeps the
    # menu reachable without a mouse (docs/adr/0094 §2).
    trigger = page.locator(HETKESEIS_MENU).locator("> summary")
    trigger.focus()
    page.keyboard.press(" ")
    expect(page.locator(HETKESEIS_MENU)).to_have_attribute("open", "")

    stage = page.locator(f'{STAGE_FIELD} input[type="radio"]').nth(2)
    stage.focus()
    page.keyboard.press(" ")
    expect(stage).to_be_checked()

    instrument = page.locator(f'{INSTRUMENT_FIELD} input[type="checkbox"]').first
    instrument.focus()
    page.keyboard.press(" ")
    expect(instrument).to_be_checked()
    page.keyboard.press(" ")
    expect(instrument).not_to_be_checked()
