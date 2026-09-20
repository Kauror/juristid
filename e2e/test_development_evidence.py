"""`+ Lisa tõend` on a `Menetluse areng`, in a real browser.

`tests/test_development_evidence.py` holds the rules and runs everywhere
cheaply. This file holds the ones only a rendered page can settle:

* that the action is *on the chronology row*, beside `Muuda` and not inside it,
  so the two acts are two controls a lawyer can tell apart;
* that pressing it opens a picker and nothing else — no `Sündmus` box, no date,
  no `Juristi märkus` — because a surface that offered those would be `Muuda`
  widened, which is the one thing this feature must not become;
* that the file a person chooses appears **under that step** when the answer
  comes back, rather than somewhere in the Matter's general document list;
* that the paper already on the step is still there beside it;
* and that a second `+ Lisa tõend` on the same row works, because a proceeding
  produces paper for months.

**Everything here happens on a Matter the test creates**, for the reason
`e2e/test_substantive_history.py` gives: the screenshot suite opens its own
titles, and a history that grew while these ran would make a baseline depend on
test order.
"""

from __future__ import annotations

from datetime import date, timedelta

import pytest
from playwright.sync_api import expect

from e2e.conftest import MARTIN, create_matter, open_add_panel, sign_in, unique_title

pytestmark = pytest.mark.e2e

HEADLINE = "Ministeerium saatis uue eelnõu versiooni"
FIRST_FILE = "esimene-eelnou.pdf"
LATER_FILE = "parandatud-eelnou.pdf"
THIRD_FILE = "komisjoni-tekst.pdf"


def _estonian(on: date) -> str:
    return f"{on.day}.{on.month}.{on.year}"


def _pdf(name: str) -> dict:
    return {"name": name, "mimeType": "application/pdf", "buffer": b"%PDF-1.4 synthetic evidence"}


def _file_a_development(page, base_url: str) -> str:
    """One step, with one paper already on it, through the real panel."""
    sign_in(page, base_url, MARTIN)
    url = create_matter(page, base_url, unique_title("Tõendi lisamine"))
    open_add_panel(page, "marge-tavaline")
    form = page.locator("#marge-tavaline")
    form.locator("[name=title]").fill(HEADLINE)
    form.locator("[name=occurred_on]").fill(_estonian(date.today() - timedelta(days=3)))
    form.locator("input[type=file]").set_input_files(_pdf(FIRST_FILE))
    form.get_by_role("button", name="Salvesta", exact=True).click()
    page.wait_for_load_state("networkidle")
    # The record, not the network: the save swaps the whole view, and an idle
    # that lands before the replacement would let the next step act on the page
    # the click was made on.
    page.get_by_text(HEADLINE).first.wait_for()
    return url


def _row(page):
    """The chronology article this development draws, files and all."""
    return page.locator("article.uxtl__item").filter(has_text=HEADLINE)


def _action(page, suffix: str):
    """One of the row's two action buttons, by its id rather than its name.

    Both are named **by reference** — the button's own word, then the headline
    element above it — so a chronology showing a dozen of these does not put
    «Märge: …» into the document twice. That makes the accessible name
    «+ Lisa tõend Märge: …», which is right for a screen reader and
    useless as a locator. The id is what identifies the control
    (`development_row.html`).
    """
    return _row(page).locator(f"button.uxtl__edit[id$='-{suffix}']")


def test_the_row_offers_lisa_toend_beside_muuda(page, base_url: str):
    """Two acts, two controls. Neither is reachable from inside the other."""
    _file_a_development(page, base_url)

    expect(_action(page, "muuda")).to_have_count(1)
    expect(_action(page, "muuda")).to_have_text("Muuda")
    expect(_action(page, "toend")).to_have_count(1)
    expect(_action(page, "toend")).to_have_text("+ Lisa tõend")


def test_the_picker_asks_for_files_and_nothing_about_the_record(page, base_url: str):
    """`Muuda` widened is exactly what this must not be, asserted from the page.

    If this form ever grew a `Sündmus` box, a date or a `Juristi märkus`, one
    press would be able to change what the step says while attaching a paper to
    it — and «what changed» would stop being answerable from one event.
    """
    _file_a_development(page, base_url)

    _action(page, "toend").click()
    form = page.locator("form[aria-label='Tõendi lisamine menetluse arengule']")
    form.wait_for()

    expect(form.locator("input[type=file]")).to_have_count(1)
    expect(form.locator("[name=title]")).to_have_count(0)
    expect(form.locator("[name=occurred_on]")).to_have_count(0)
    expect(form.locator("[name=note]")).to_have_count(0)
    # And it says which step it is attaching to, so nobody has to remember.
    expect(form).to_contain_text(HEADLINE)


def test_cancelling_the_picker_leaves_the_row_as_it_was(page, base_url: str):
    """Leaving is a re-read of what the record says, and writes nothing."""
    _file_a_development(page, base_url)

    _action(page, "toend").click()
    form = page.locator("form[aria-label='Tõendi lisamine menetluse arengule']")
    form.wait_for()
    form.get_by_role("button", name="Tühista", exact=True).click()

    expect(page.locator("form[aria-label='Tõendi lisamine menetluse arengule']")).to_have_count(0)
    row = _row(page)
    expect(row).to_contain_text(HEADLINE)
    expect(row.get_by_role("link", name=FIRST_FILE)).to_have_count(1)
    expect(row.get_by_role("link", name=LATER_FILE)).to_have_count(0)


def test_a_chosen_file_comes_back_under_the_step_it_supports(page, base_url: str):
    """The whole point, and the reason the answer is the column and not the row.

    The files a development carries are drawn by the chronology *around* the
    swap target the correction uses, so an answer that returned only that target
    would come back looking exactly as it did before the upload. It returns the
    column, and this is the assertion that says so in the only terms that matter:
    the new paper is on this row, and the old one is still beside it.
    """
    _file_a_development(page, base_url)

    _action(page, "toend").click()
    form = page.locator("form[aria-label='Tõendi lisamine menetluse arengule']")
    form.wait_for()
    form.locator("input[type=file]").set_input_files(_pdf(LATER_FILE))
    form.get_by_role("button", name="Lisa tõend", exact=True).click()
    page.wait_for_load_state("networkidle")

    row = _row(page)
    expect(row.get_by_role("link", name=LATER_FILE)).to_have_count(1)
    # Additive: the paper that arrived with the step is still on it.
    expect(row.get_by_role("link", name=FIRST_FILE)).to_have_count(1)
    # And not a chronology line of its own — a file is not an event.
    expect(page.locator("article.uxtl__item").filter(has_text=HEADLINE)).to_have_count(1)
    # The step itself is untouched.
    expect(row).to_contain_text(HEADLINE)


def test_a_second_paper_can_be_added_to_the_same_step(page, base_url: str):
    """A proceeding produces paper for months, and nothing here is one-shot."""
    _file_a_development(page, base_url)

    for name in (LATER_FILE, THIRD_FILE):
        _action(page, "toend").click()
        form = page.locator("form[aria-label='Tõendi lisamine menetluse arengule']")
        form.wait_for()
        form.locator("input[type=file]").set_input_files(_pdf(name))
        form.get_by_role("button", name="Lisa tõend", exact=True).click()
        page.wait_for_load_state("networkidle")
        _row(page).get_by_role("link", name=name).first.wait_for()

    row = _row(page)
    for name in (FIRST_FILE, LATER_FILE, THIRD_FILE):
        expect(row.get_by_role("link", name=name)).to_have_count(1)


def test_the_row_offers_no_way_to_remove_a_paper(page, base_url: str):
    """Evidence is additive and immutable, asserted where a lawyer would look."""
    _file_a_development(page, base_url)

    row = _row(page)
    for word in ("Eemalda", "Kustuta", "Eemalda tõend"):
        expect(row.get_by_role("button", name=word, exact=False)).to_have_count(0)
