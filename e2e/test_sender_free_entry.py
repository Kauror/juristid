"""Saatja, typed on the Teema form, in a real browser.

The rules — reuse an exact or alias match, create a genuinely new body, refuse
an ambiguous spelling, union a typed name with the ticked chips, and do all of
it inside the save's own transaction — are pinned against the database in
`tests/test_sender_free_entry.py`. What only a browser can answer is whether the
workflow those rules exist for is reachable: whether somebody who cannot see the
institution among the chips can find it, or name it, without opening anything
and without leaving the half-filled Teema to add it under Asutused first.

That last part is the whole complaint. The old control put the search inside a
closed `<details>` labelled «Vali nimekirjast (15)» and put the sentence saying
creation was impossible inside it too — so the answer to "the body I need is not
here" was visible only to somebody who had already opened the thing that did not
contain it (docs/adr/0063).

Since docs/adr/0073 there is no disclosure and no second box on `Uus teema`: one
field searches the catalogue and the `+` beside it proposes what was typed. The
rules underneath are untouched, which is why this file kept every assertion
about them and changed only how the browser reaches them. `Muuda teemat` and
`Saabunud` still render `sender_control.html`, so the tests below that drive
those surfaces are untouched as well (task §26).
"""

from __future__ import annotations

import pytest
from playwright.sync_api import expect

from e2e.conftest import MARTIN, sign_in

pytestmark = pytest.mark.e2e

CREATE_PATH = "/teemad/uus/"

#: Named once so a rerun against a database an earlier run already touched
#: reuses the same institution instead of inventing a second one.
TYPED_SENDER = "Näidisliitude keskliit"


def create_form(page, base_url) -> None:
    page.goto(f"{base_url}{CREATE_PATH}")
    page.wait_for_load_state("networkidle")


def name_a_new_sender(page, typed: str) -> None:
    """Say «this is a body you do not have», through the one control that does.

    The search box finds what exists; `+` proposes what was typed. Both are the
    same field, which is the whole of docs/adr/0073 — so a test that used to
    `fill("#id_sender_name")` types into the box and presses the button.
    """
    box = page.locator("#saatja-otsi")
    box.click()
    box.fill(typed)
    page.locator("#saatja-valik [data-orgfind-add]").click()


def test_the_one_sender_operation_is_on_the_page_at_rest(page, base_url):
    """Search, quick choices, and a `+` — with nothing to open first.

    This assertion has moved twice and it is worth saying why, because the
    reasoning is the product decision rather than a preference. It first
    required the catalogue and its search to be permanently on the page; ADR
    0067 put them back behind «Vali nimekirjast», on the argument that the
    shortlist answers the question on almost every visit and a permanent
    scrolling list was occupying the Saatja column for nothing. Both rounds
    agreed on the half that was load-bearing — the answer to "the body I need is
    not on this page" must not itself be behind a click, because the workflow
    that replaces is «abandon this Teema, go to Asutused, come back» and nobody
    performs it.

    docs/adr/0073 keeps that half and removes the choice between the other two:
    the search box *is* the box for a body the catalogue does not hold, so there
    is one control, it is first, and nothing is folded away (task §2, §3).
    """
    sign_in(page, base_url, MARTIN)
    create_form(page, base_url)

    expect(page.locator('input[name="source_organisations"]').first).to_be_visible()
    expect(page.locator("#saatja-otsi")).to_be_visible()
    expect(page.get_by_role("button", name="Lisa uus saatja", exact=True)).to_be_visible()

    # And neither retired control is anywhere on the rendered page.
    body = page.locator("form.createform").inner_text()
    assert "Vali nimekirjast" not in body
    assert "Uus saatja" not in body
    assert page.locator(".senderpick details").count() == 0


def test_the_search_is_a_result_area_that_keeps_what_was_ticked(page, base_url):
    """Type to find, and a ticked body never hides afterwards.

    Three states in one test, because they are one behaviour: at rest the result
    area holds nothing, a query fills it with what matches, and a body chosen
    from it stays on screen when the query stops matching it. That last one is
    the rule that matters — hiding a control does not clear it, so a save must
    never depend on what is on screen (task §8).
    """
    sign_in(page, base_url, MARTIN)
    create_form(page, base_url)

    results = page.locator("#saatja-tulemused")
    expect(results).to_be_hidden()

    chips = page.locator("#saatja-valik label.chip")
    name = (chips.first.inner_text() or "").strip().rstrip("×").strip()
    box = page.locator("#saatja-otsi")
    box.click()
    box.fill(name[:5])
    expect(results).to_be_visible()

    results.get_by_role("option", name=name, exact=True).click()
    box.fill("zzzzz-ei-leidu")

    # Chosen, therefore still visible and still chosen.
    chosen = page.locator("#saatja-valik label.chip", has_text=name).first
    expect(chosen).to_be_visible()
    expect(chosen.locator("input")).to_be_checked()


def test_a_sender_can_be_named_on_uus_teema(page, base_url):
    """The workflow the removed sentence used to forbid, performed."""
    sign_in(page, base_url, MARTIN)
    create_form(page, base_url)

    page.fill("#id_title", "Brauseris kirjutatud saatja")
    name_a_new_sender(page, TYPED_SENDER)
    # A next step, for the same reason `test_addressee_free_entry` files one:
    # every Teema this suite leaves behind without one is a permanent row in
    # the department's «järgmise tegevuseta» list, which another file reads.
    page.fill("#id_next-text", "Kontrollida, kas saatja ootab vastust")
    page.locator("#jargmine-tegevus").get_by_role("button", name="+1 nädal").click()
    page.get_by_role("button", name="Loo teema").click()
    page.wait_for_load_state("networkidle")

    complaints = page.locator(".field__error, .formerror, .message--error").all_inner_texts()
    assert not complaints, f"the form refused: {complaints}"
    expect(page.locator(".railcard__value", has_text=TYPED_SENDER).first).to_be_visible()


def test_a_sender_named_here_is_afterwards_an_addressee_anybody_can_choose(page, base_url):
    """One catalogue, which is what the department asked for.

    The body named through Saatja above has to be selectable as an Adressaat on
    the next form somebody opens — not because anything copies it across, but
    because there was only ever one `Organisation` table.
    """
    sign_in(page, base_url, MARTIN)
    create_form(page, base_url)

    # The Teema filed by the test above put this body in the catalogue. Filing
    # it again here would be a second Matter for nothing, so this reads the
    # control rather than the record.
    name_a_new_sender(page, TYPED_SENDER)
    page.fill("#id_title", "Sama asutus adressaadina")
    page.fill("#id_next-text", "Kontrollida vastust")
    page.locator("#jargmine-tegevus").get_by_role("button", name="+1 nädal").click()
    page.get_by_role("button", name="Loo teema").click()
    page.wait_for_load_state("networkidle")

    create_form(page, base_url)
    addressees = page.locator('input[name="addressee_organisation"]')
    # `textContent`, not `innerText`. Adressaat offers the *whole* catalogue —
    # the ranked shortlist inline and every other body inside «Vali nimekirjast»
    # (`MatterCreateForm.addressee_offered`) — and that disclosure is closed
    # when the form opens. `innerText` is layout-aware, so it reads an empty
    # string for a label that is present, correct and simply not painted, which
    # turns «is this body offered?» into «is this body on screen?».
    #
    # The two questions came apart the moment more than one browser file existed
    # in this shard: `addressees_by_usage` falls back to the alphabetical head
    # of the catalogue only while *nothing* has ever been filed as an addressee,
    # so whether this body lands above or below the fold depends on what other
    # tests put in the shared database first. Sharding is a pure function of the
    # collected file set, so adding a file anywhere moves that. The claim here
    # is the one in the docstring — selectable, one `Organisation` table — and
    # that claim is about the form's choices, not about scroll position.
    labels = addressees.evaluate_all(
        "nodes => nodes.map(node => {"
        "  const label = node.closest('label');"
        "  return label ? label.textContent.replace(/\\s+/g, ' ').trim() : '';"
        "})"
    )
    assert any(TYPED_SENDER in label for label in labels), (
        f"{TYPED_SENDER!r} is not offered as an addressee: {labels}"
    )


def test_the_count_beside_the_legend_reads_both_halves_of_the_set(page, base_url):
    """Saatja is one set split across two fields, because a checkbox group
    cannot be rendered in two places without being two fields. A count that
    read only the shortlist said «1 valitud» over two ticked bodies."""
    sign_in(page, base_url, MARTIN)
    create_form(page, base_url)

    badge = page.locator("[data-chipcount-for='source_organisations']")
    shortlist = page.locator('#saatja-valik input[name="source_organisations"]')
    assert shortlist.count(), (
        "the seeded world offers two institutions as sender chips — an empty "
        "locator here is a moved element, not an empty catalogue"
    )

    shortlist.first.check()
    expect(badge).to_have_text("1 valitud")

    # The second body through the search, which is where the two halves of the
    # set actually come apart: everything outside the shortlist posts under
    # `source_organisations_other`, and a badge reading only the first field
    # said «1 valitud» over two ticked bodies.
    others = page.locator("#saatja-valik label.chip").evaluate_all(
        "(nodes, chosen) => nodes"
        ".map(node => { const i = node.querySelector('input');"
        " const n = node.querySelector('.chip__name');"
        " return {checked: i ? i.checked : false,"
        " name: (n ? n.textContent : '').replace(/\\s*×$/, '').trim()}; })"
        ".filter(item => !item.checked && item.name)"
        ".map(item => item.name)",
        None,
    )
    assert others, (
        "the seeded world offers two institutions and this needs the second — "
        "an empty list here is a moved element, not an empty catalogue"
    )

    box = page.locator("#saatja-otsi")
    box.click()
    box.fill(others[0][:5])
    page.locator("#saatja-tulemused").get_by_role("option", name=others[0], exact=True).click()
    expect(badge).to_have_text("2 valitud")
