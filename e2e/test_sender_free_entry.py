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


def test_the_two_sender_operations_that_matter_are_on_the_page_at_rest(page, base_url):
    """The shortlist and `Uus saatja`, with nothing to open first.

    This used to require the *search* to be at rest on the page too, and to
    assert that the control had no disclosure at all. That decision is
    superseded (ADR 0067): the catalogue and its search moved back behind
    «Vali nimekirjast», where Adressaat has always kept them, because the
    shortlist is filled to eight from the bodies this department actually works
    with and answers the question on almost every visit — so what the permanent
    catalogue bought was a search box and a scrolling list occupying the Saatja
    column every single time.

    What did **not** move is the half of that round which was load-bearing, and
    it is what this test now pins: `Uus saatja` stays outside the disclosure.
    The answer to "the body I need is not on this page" must not itself be
    behind a click, because the workflow that replaced was «abandon this Teema,
    go to Asutused, come back» and nobody performed it — they filed the Teema
    with no sender (ADR 0063).
    """
    sign_in(page, base_url, MARTIN)
    create_form(page, base_url)

    expect(page.locator('input[name="source_organisations"]').first).to_be_visible()
    expect(page.get_by_label("Uus saatja")).to_be_visible()

    # And the search is behind the door rather than beside the chips.
    search = page.locator("[data-choicefilter='saatja-nimekiri'] input")
    if search.count():
        expect(search).to_be_hidden()
        assert page.locator(".senderpick details").count() == 1


def test_the_search_is_a_result_area_that_keeps_what_was_ticked(page, base_url):
    """Type to find, and a ticked body never hides afterwards.

    Three states in one test, because they are one behaviour: at rest the
    result area holds nothing, a query fills it with what matches, and a body
    ticked from it stays on screen when the query stops matching it. That last
    one is the rule that matters — hiding a checkbox does not clear it, so a
    save must never depend on what is on screen.
    """
    sign_in(page, base_url, MARTIN)
    create_form(page, base_url)

    rows = page.locator("#saatja-nimekiri .chip")
    if not rows.count():
        pytest.skip("the seeded catalogue holds no body outside the shortlist")

    # At rest the catalogue is behind «Vali nimekirjast» (ADR 0067), so it is
    # opened before anything inside it is measured.
    expect(rows.first).to_be_hidden()
    page.locator(".senderpick summary.chipdetails__summary").click()

    search = page.locator("[data-choicefilter='saatja-nimekiri'] input")
    name = rows.first.inner_text().strip()
    search.fill(name[:4])
    expect(rows.first).to_be_visible()

    rows.first.locator("input").check()
    search.fill("zzzzz-ei-leidu")

    # Ticked, therefore still visible and still ticked.
    expect(rows.first).to_be_visible()
    expect(rows.first.locator("input")).to_be_checked()


def test_a_sender_can_be_named_on_uus_teema(page, base_url):
    """The workflow the removed sentence used to forbid, performed."""
    sign_in(page, base_url, MARTIN)
    create_form(page, base_url)

    page.fill("#id_title", "Brauseris kirjutatud saatja")
    page.fill("#id_sender_name", TYPED_SENDER)
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
    page.fill("#id_sender_name", TYPED_SENDER)
    page.fill("#id_title", "Sama asutus adressaadina")
    page.fill("#id_next-text", "Kontrollida vastust")
    page.locator("#jargmine-tegevus").get_by_role("button", name="+1 nädal").click()
    page.get_by_role("button", name="Loo teema").click()
    page.wait_for_load_state("networkidle")

    create_form(page, base_url)
    addressees = page.locator('input[name="addressee_organisation"]')
    labels = addressees.evaluate_all(
        "nodes => nodes.map(node => {"
        "  const label = node.closest('label');"
        "  return label ? label.innerText.trim() : '';"
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
    shortlist = page.locator('input[name="source_organisations"]')
    if not shortlist.count():
        pytest.skip("the seeded world offers no sender chips")

    shortlist.first.check()
    expect(badge).to_have_text("1 valitud")

    rows = page.locator("#saatja-nimekiri .chip")
    if not rows.count():
        pytest.skip("the seeded catalogue holds no body outside the shortlist")

    page.locator(".senderpick summary.chipdetails__summary").click()
    search = page.locator("[data-choicefilter='saatja-nimekiri'] input")
    search.fill(rows.first.inner_text().strip()[:4])
    rows.first.locator("input").check()
    expect(badge).to_have_text("2 valitud")
