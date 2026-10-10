"""`Saabunud → lisa`: Saatja through the one organisation picker, in a real browser.

Until 2026-10-10 this page drew the older `sender_control.html` with plain
checkboxes, so a recorded alias such as «MKM» found the ministry on `Uus teema`
and `Muuda teemat` and nothing here — on the page where a ministry's letter
actually lands. Product decision D-4 put the same control on all three sender
pages (AUD-011, docs/adr/0073 amendment of 2026-10-10).

What only a browser can answer is whether the workflow is reachable: typing an
alias shows the right chip, choosing it files the Matter with that sender, and a
brand-new name proposed with `+` survives the save — the chip bug fixed on
2026-10-08 (`e2e/test_organisation_after_swap.py`, docs/adr/0144 §4) must not
come back on this page — and becomes the Matter's sender, created once. The
server-side rules are pinned in `tests/test_unified_sender_picker.py`.

The seeded world's ministry carries the abbreviation «NÄIDISMIN»
(`seed_e2e_data`), so no fixture has to invent an alias.
"""

from __future__ import annotations

import re
import sys
import uuid
from pathlib import Path

import pytest
from playwright.sync_api import expect

from e2e.conftest import MARTIN, sign_in
from e2e.titles import unique_title

pytestmark = pytest.mark.e2e

REPOSITORY_ROOT = Path(__file__).resolve().parent.parent

INTAKE_PATH = "/saabunud/lisa/"
#: The picker id `intake.html` gives Saatja; every id inside it derives from it.
SENDER = "saabunud-saatja"

MINISTRY = "Näidisministeerium"
MINISTRY_ALIAS = "NÄIDISMIN"


def a_new_name() -> str:
    """A body the catalogue provably does not hold yet (the suite shares one DB)."""
    return f"Saabunud Näidisamet {uuid.uuid4().hex[:8]}"


@pytest.fixture
def letter_pdf(tmp_path: Path) -> Path:
    sys.path.insert(0, str(REPOSITORY_ROOT))
    from tests.synthetic_corpus import government_pdf

    path = tmp_path / "saabunud-kaaskiri.pdf"
    path.write_bytes(government_pdf())
    return path


def intake_form(page, base_url) -> None:
    sign_in(page, base_url, MARTIN)
    page.set_viewport_size({"width": 1440, "height": 900})
    page.goto(f"{base_url}{INTAKE_PATH}")
    page.wait_for_load_state("networkidle")


def box(page):
    return page.locator(f"#{SENDER}-otsi")


def results(page):
    return page.locator(f"#{SENDER}-tulemused")


def option_names(page) -> list[str]:
    return [
        (text or "").strip() for text in results(page).locator("[role=option]").all_text_contents()
    ]


def search(page, term: str) -> None:
    control = box(page)
    control.click()
    control.fill("")
    control.type(term, delay=20)


def chip_names(page, *, only_visible: bool = True) -> list[str]:
    return page.locator(f"#{SENDER}-valik label.chip").evaluate_all(
        "(nodes, visibleOnly) => nodes"
        ".filter(node => !visibleOnly || !node.hidden)"
        ".map(node => { const n = node.querySelector('.chip__name');"
        " return (n ? n.textContent : '').replace(/\\s*×$/, '').trim(); })",
        only_visible,
    )


def chosen_names(page) -> list[str]:
    return page.locator(f"#{SENDER}-valik label.chip").evaluate_all(
        "nodes => nodes"
        ".filter(node => { const i = node.querySelector('input');"
        " return i && i.checked && !!i.name; })"
        ".map(node => { const n = node.querySelector('.chip__name');"
        " return (n ? n.textContent : '').replace(/\\s*×$/, '').trim(); })"
    )


def file_it(page, letter: Path, title: str) -> None:
    """Attach the letter, name it and save; refuse loudly if the form refused."""
    page.set_input_files("input[name='uploads']", str(letter))
    page.fill("input[name='title']", title)
    page.get_by_role("button", name="Salvesta", exact=True).click()
    page.wait_for_load_state("domcontentloaded")
    complaints = page.locator(".field__error, .formerror").all_inner_texts()
    assert not complaints, f"Saabunud refused the save: {complaints}"
    page.wait_for_url(re.compile(r"/teemad/[0-9a-f-]{36}/$"))


def test_saabunud_draws_the_one_picker_and_not_the_old_control(page, base_url):
    intake_form(page, base_url)

    expect(box(page)).to_be_visible()
    expect(box(page)).to_have_attribute("placeholder", "Otsi või lisa asutus…")
    expect(page.locator(f"#{SENDER}-valik [data-orgfind-add]")).to_be_attached()
    assert page.get_by_text("Vali nimekirjast", exact=False).count() == 0
    assert page.locator("#id_sender_name").count() == 0, "the old «Uus saatja» box is back"


def test_a_recorded_alias_finds_the_ministry_on_saabunud(page, base_url):
    intake_form(page, base_url)

    search(page, MINISTRY_ALIAS)

    assert option_names(page) == [MINISTRY]


def test_a_sender_chosen_by_its_alias_is_the_filed_matters_sender(page, base_url, letter_pdf):
    intake_form(page, base_url)

    search(page, MINISTRY_ALIAS)
    results(page).get_by_role("option", name=MINISTRY, exact=True).click()
    assert chosen_names(page) == [MINISTRY]
    assert MINISTRY in chip_names(page)

    title = unique_title("Saabunud lühendiga")
    file_it(page, letter_pdf, title)

    expect(page.get_by_role("heading", name=title)).to_be_visible()
    assert MINISTRY in page.locator("main").inner_text()


def test_a_new_sender_added_with_plus_survives_the_save_and_is_created_once(
    page, base_url, letter_pdf
):
    """The 2026-10-08 chip bug, asked on this page: the chip must not vanish on save."""
    intake_form(page, base_url)
    name = a_new_name()

    search(page, name)
    page.locator(f"#{SENDER}-valik [data-orgfind-add]").click()

    provisional = page.locator(f"#{SENDER}-valik [data-orgfind-provisional]")
    expect(provisional).to_be_visible()
    assert name in (provisional.inner_text() or "")
    expect(box(page)).to_have_value("")

    title = unique_title("Saabunud uue saatjaga")
    file_it(page, letter_pdf, title)

    expect(page.get_by_role("heading", name=title)).to_be_visible()
    assert name in page.locator("main").inner_text(), "the typed sender did not reach the Teema"

    # Exactly once: the next filer finds one body under that name, reachable
    # through the same search, and no provisional duplicate.
    page.goto(f"{base_url}{INTAKE_PATH}")
    page.wait_for_load_state("networkidle")
    assert chip_names(page, only_visible=False).count(name) == 1
    search(page, name)
    assert option_names(page) == [name]
