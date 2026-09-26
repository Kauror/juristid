"""Who an opinion went to, in a real browser (ENG-041, ENG-061).

The rules are pinned against the database in `tests/test_send_requires_addressee.py`
and `tests/test_saaja_means_addressee.py`. What only a running page can answer is
whether a person meets them where they work:

* **ENG-041.** The draft row's `Märgi saadetuks` asks `Adressaadid`. Pressed with
  nobody chosen, the send is refused beside the box with the focus on it and the
  draft stays a draft — and a post that skips the browser's own check is refused
  by the server too. With an addressee it goes out, and the file row names them.
* **ENG-061.** On `/arvamused/`, `Saaja` means the addressee: filtering by the
  addressee finds the letter and its `Adressaat` cell reads exactly that name,
  while an organisation only copied in is neither offered nor matched.

**Everything happens on Matters and organisations this file creates.** The
screenshot suite opens `OPEN_TITLE`, and the seeded ministry is an addressee on
the seeded world's own sent opinion — so an assertion that an organisation is
*not* offered as a `Saaja` needs one no other file can have written to.
"""

from __future__ import annotations

import re

import pytest
from playwright.sync_api import expect

from e2e.conftest import SANDRA, give_first_step, sign_in, unique_title

pytestmark = pytest.mark.e2e

MINISTRY = "Näidisministeerium"
REFUSAL = "Saadetuks märkimiseks on vaja vähemalt üht adressaati."


def _new_matter(page, base_url: str, title: str, *, new_sender: str | None = None) -> str:
    """File a Teema through `Uus teema`, optionally naming a body the catalogue lacks.

    Naming one is how this file puts an organisation in the catalogue that no
    other file can have addressed (`e2e/test_sender_free_entry.py`).
    """
    page.goto(f"{base_url}/teemad/uus/")
    page.wait_for_load_state("networkidle")
    page.fill("#id_title", title)
    if new_sender is not None:
        box = page.locator("#saatja-otsi")
        box.click()
        box.fill(new_sender)
        page.locator("#saatja-valik [data-orgfind-add]").click()
    give_first_step(page)
    page.get_by_role("button", name="Loo teema").click()
    page.wait_for_url(re.compile(r"/teemad/[0-9a-f-]{36}/$"))
    complaints = page.locator(".field__error, .formerror, .message--error").all_inner_texts()
    assert not complaints, f"the form refused: {complaints}"
    return page.url


def _opinion_block(page, matter_url: str):
    """`Dokumendid`, with the `Arvamused` accordion open."""
    page.goto(f"{matter_url.rstrip('/')}/dokumendid/")
    page.wait_for_load_state("networkidle")
    block = page.locator("#arvamuste-haldus")
    if not block.evaluate("node => node.open"):
        block.locator("summary.accordion__head").click()
    return block


def _draft_with_its_file(
    page, matter_url: str, title: str, *, addressee: str = "", copied: str = ""
):
    """`+ Uus arvamus`, then `Lisa fail` — a draft that is ready to be sent."""
    block = _opinion_block(page, matter_url)
    block.locator("details.disclosure").filter(has_text="+ Uus arvamus").locator("summary").click()
    page.locator("#id_arvamus-title").fill(title)
    if addressee:
        page.locator("#id_arvamus-recipients").select_option(label=addressee)
    if copied:
        page.locator("#id_arvamus-for_information").select_option(label=copied)
    page.get_by_role("button", name="Loo arvamus").click()
    page.wait_for_load_state("networkidle")

    draft = page.locator(".draftrow", has_text=title)
    expect(draft).to_have_count(1)
    draft.get_by_label("Vali lõplik saadetud fail").set_input_files(
        files=[
            {
                "name": "koja-arvamus.pdf",
                "mimeType": "application/pdf",
                "buffer": b"%PDF-1.4 synthetic final opinion",
            }
        ]
    )
    draft.get_by_role("button", name="Lisa fail").click()
    page.wait_for_load_state("networkidle")
    draft = page.locator(".draftrow", has_text=title)
    expect(draft.get_by_role("button", name="Märgi saadetuks")).to_be_visible()
    return draft


def _focused_id(page) -> str:
    return page.evaluate("() => document.activeElement && document.activeElement.id")


# ---------------------------------------------------------------------------
# ENG-041 — a send names who it went to
# ---------------------------------------------------------------------------


def test_a_send_with_no_addressee_is_refused_and_one_with_an_addressee_goes_out(page, base_url):
    sign_in(page, base_url, SANDRA)
    matter_url = _new_matter(page, base_url, unique_title("Adressaadita saatmine"))
    title = unique_title("Adressaadita arvamus")
    draft = _draft_with_its_file(page, matter_url, title)

    # Labelled: the box is reachable by its own name, inside its own row.
    addressees = draft.get_by_label("Adressaadid")
    expect(addressees).to_have_count(1)
    expect(addressees).to_have_attribute("required", "")
    assert addressees.evaluate("select => select.selectedOptions.length") == 0

    # 1. Nothing chosen: the browser refuses beside the box and puts the focus
    #    on it. Nothing is posted, so the page does not move.
    before = page.url
    draft.get_by_role("button", name="Märgi saadetuks").click()
    page.wait_for_load_state("networkidle")
    assert page.url == before
    assert addressees.evaluate("select => select.validity.valueMissing")
    assert addressees.evaluate("select => select.validationMessage")
    assert _focused_id(page) == addressees.get_attribute("id")

    # 2. A post that skips the browser's check: the server refuses in words
    #    (not by colour alone), lands back on this draft's row, and the draft is
    #    still a draft that can be sent.
    draft.locator("form.draftrow__form--send").evaluate("form => form.noValidate = true")
    draft.get_by_role("button", name="Märgi saadetuks").click()
    page.wait_for_load_state("networkidle")
    expect(page.locator(".message--error")).to_contain_text(REFUSAL)
    assert re.search(r"#arvamus-[0-9a-f-]{36}$", page.url), page.url
    draft = page.locator(".draftrow", has_text=title)
    expect(draft).to_be_visible()
    expect(draft.get_by_role("button", name="Märgi saadetuks")).to_be_visible()
    # The file row does not claim a send: no `Saadetud …` under its name.
    expect(
        page.locator("tr", has_text="koja-arvamus.pdf").locator(".doctable__sent")
    ).to_have_count(0)

    # 3. An addressee chosen, and the send submitted from the keyboard.
    draft.get_by_label("Adressaadid").select_option(label=MINISTRY)
    draft.get_by_role("button", name="Märgi saadetuks").focus()
    page.keyboard.press("Enter")
    page.wait_for_load_state("networkidle")

    expect(page.locator(".message--error")).to_have_count(0)
    expect(page.locator(".draftrow", has_text=title)).to_have_count(0)
    row = page.locator("tr", has_text="koja-arvamus.pdf")
    expect(row.locator(".badge--opinion")).to_have_text("Arvamus")
    expect(row.locator(".doctable__sent")).to_contain_text(MINISTRY)


def test_the_send_opens_on_the_drafts_own_addressee(page, base_url):
    """The common case is one click: the draft already says who it is for."""
    sign_in(page, base_url, SANDRA)
    matter_url = _new_matter(page, base_url, unique_title("Adressaadiga saatmine"))
    title = unique_title("Adressaadiga arvamus")
    draft = _draft_with_its_file(page, matter_url, title, addressee=MINISTRY)

    chosen = draft.get_by_label("Adressaadid").evaluate(
        "select => [...select.selectedOptions].map(option => option.textContent.trim())"
    )
    assert chosen == [MINISTRY]

    draft.get_by_role("button", name="Märgi saadetuks").click()
    page.wait_for_load_state("networkidle")

    row = page.locator("tr", has_text="koja-arvamus.pdf")
    expect(row.locator(".doctable__sent")).to_contain_text(MINISTRY)


# ---------------------------------------------------------------------------
# ENG-061 — `Saaja` on /arvamused/ means the addressee
# ---------------------------------------------------------------------------


def test_the_register_saaja_is_the_addressee_and_never_a_copy(page, base_url):
    sign_in(page, base_url, SANDRA)
    addressee = unique_title("Adressaatamet")
    copied = unique_title("Koopialiit")
    # Two bodies nobody else has written to, put in the catalogue the way a
    # lawyer does it: as the Saatja of a Teema they file.
    matter_url = _new_matter(page, base_url, unique_title("Saaja teema"), new_sender=addressee)
    _new_matter(page, base_url, unique_title("Koopia teema"), new_sender=copied)

    title = unique_title("Saaja arvamus")
    draft = _draft_with_its_file(page, matter_url, title, addressee=addressee, copied=copied)
    copied_pk = page.locator("#id_arvamus-for_information option", has_text=copied).get_attribute(
        "value"
    )
    draft.get_by_role("button", name="Märgi saadetuks").click()
    page.wait_for_load_state("networkidle")
    expect(
        page.locator("tr", has_text="koja-arvamus.pdf").locator(".doctable__sent")
    ).to_contain_text(addressee)

    page.goto(f"{base_url}/arvamused/")
    page.wait_for_load_state("networkidle")
    saaja = page.locator("select[name='saaja']")
    # Offered as a `Saaja` because it was written to; the copy is not offered.
    expect(saaja.locator("option", has_text=addressee)).to_have_count(1)
    expect(saaja.locator("option", has_text=copied)).to_have_count(0)

    saaja.select_option(label=addressee)
    # The filter form's own `Otsi`: the header search carries a button of the
    # same name, hidden but in the accessibility tree.
    page.locator("form:has(select[name='saaja'])").get_by_role("button", name="Otsi").click()
    page.wait_for_load_state("networkidle")
    row = page.locator("table tbody tr", has_text=title)
    expect(row).to_have_count(1)
    # The `Adressaat` cell is the fifth column, and it reads the addressee
    # exactly — no copy, and no trailing comma a copy used to leave behind.
    assert row.locator("td").nth(4).inner_text().strip() == addressee

    # Asked for the copy by address, the register finds nothing for it.
    page.goto(f"{base_url}/arvamused/?saaja={copied_pk}")
    page.wait_for_load_state("networkidle")
    expect(page.locator("table tbody tr", has_text=title)).to_have_count(0)
