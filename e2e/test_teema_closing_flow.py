"""Closing a Teema, in a real browser.

The domain suite proves the rules; this proves that a person can actually
perform them.

**The panel asks two questions since the approved Teema target**: `Kuidas lõppes`
and an optional `Lõppsõna`. Seven-new-recipients-in-one-save and the chip that
removes a mistyped one went with the sent-opinion half of the closure — closing a
Matter is not a claim that an opinion was sent, and requiring the PDF made the
commonest closure impossible to record honestly (docs/adr/0074 §10). Both are
still proven through `compose_update` in `tests/test_teema_closing_flow.py`,
which is where that half of the contract now lives.

What is left is what only a browser can show: that the panel opens, that its
chips are a single-select group over the field the server validates, that one
`Salvesta` closes the file, and that a refusal comes back where the reader is.

Everything here is synthetic.
"""

from __future__ import annotations

import pytest
from playwright.sync_api import expect

from e2e.conftest import MARTIN, create_matter, open_add_panel, open_composer, sign_in

pytestmark = pytest.mark.e2e


def open_closing_panel(page):
    open_add_panel(page, "lisa-lopeta")
    panel = page.locator("#lisa-lopeta")
    expect(panel).to_have_attribute("open", "")
    # No confirmation box to tick. Answering the panel is the request to close,
    # and the panel's own `Salvesta` commits that and nothing else — there is no
    # shared save left to mean six things (pilot QA F-02, docs/adr/0075 §2).
    expect(page.locator("#id_close_matter")).to_have_count(0)
    return panel


def save_and_expect_ok(page):
    with page.expect_response(
        lambda response: "/lisa/lopeta/" in response.url and response.request.method == "POST"
    ) as caught:
        page.locator("#lisa-lopeta button[type=submit]").click()
    saved = caught.value
    assert saved.status == 200, f"the closure save was refused: {saved.status}"
    page.wait_for_load_state("networkidle")


def test_the_closing_panel_asks_only_the_approved_questions(page, base_url):
    """Two questions, and none of the four the target retired."""
    sign_in(page, base_url, MARTIN)
    create_matter(page, base_url, "Lõpetamise brauserikatse: küsimused")

    panel = open_closing_panel(page)

    expect(panel).to_contain_text("Kuidas lõppes")
    for label in ("Jõustus", "Menetlus lõppes", "Loobuti"):
        expect(panel.locator(".uxchip", has_text=label)).to_have_count(1)
    expect(panel.locator("[name=closing_words]")).to_be_visible()
    expect(panel).to_contain_text("valikuline")
    expect(panel).to_contain_text("Teema läheb arhiivi. Avatud järgmised sammud tühistatakse.")

    # And the four that went with the sent-opinion half.
    for gone in ("[name=final_file]", "[name=final_sent_on]", "[name=work_victory]"):
        expect(page.locator(gone)).to_have_count(0)


def test_nothing_is_chosen_until_somebody_chooses(page, base_url):
    """An unanswered `Kuidas lõppes` has to be representable, or opening the
    panel would post a closure from the next ordinary save (pilot QA F-02)."""
    sign_in(page, base_url, MARTIN)
    create_matter(page, base_url, "Lõpetamise brauserikatse: vastamata")

    panel = open_closing_panel(page)

    expect(panel.locator("input[name=disposition]")).to_have_value("")
    expect(panel.locator(".uxchip.is-selected")).to_have_count(0)

    panel.locator(".uxchip", has_text="Loobuti").click()

    expect(panel.locator("input[name=disposition]")).to_have_value("MONITORING_STOPPED")
    expect(panel.locator(".uxchip.is-selected")).to_have_count(1)


def test_one_save_closes_the_file_and_leaves_a_readable_past(page, base_url):
    sign_in(page, base_url, MARTIN)
    create_matter(page, base_url, "Lõpetamise brauserikatse: üks salvestus")

    # The narrative first, as its own save, because the closure no longer
    # borrows a body from another operation (docs/adr/0075 §9).
    open_composer(page)
    page.locator("#lisa-marge .composer__body").fill("Menetlus lõppes ministeeriumis.")
    page.locator("#lisa-marge button[type=submit]").click()
    page.wait_for_load_state("networkidle")

    panel = open_closing_panel(page)
    panel.locator(".uxchip", has_text="Menetlus lõppes").click()
    panel.locator("[name=closing_words]").fill("Eelnõu langes ära.")

    save_and_expect_ok(page)

    # The file is closed, and says so beside its own title.
    expect(page.locator(".badge--state")).to_contain_text("Suletud")
    # There is no writable workspace on a closed Matter…
    expect(page.locator("#lisa-teemale")).to_have_count(0)
    expect(page.get_by_text("Mida tegid?", exact=True)).to_have_count(0)
    # …and the history is still readable.
    expect(page.locator("#ajalugu-loend")).to_contain_text("Menetlus lõppes ministeeriumis")


def test_a_refused_closure_comes_back_in_an_open_panel(page, base_url):
    """`Lõppsõna` alone is an answer only a closure is asked, so it asks to
    close — and the missing half is refused where the reader is looking."""
    sign_in(page, base_url, MARTIN)
    create_matter(page, base_url, "Lõpetamise brauserikatse: keeldumine")

    panel = open_closing_panel(page)
    panel.locator("[name=closing_words]").fill("Midagi juhtus.")
    page.locator("#lisa-lopeta button[type=submit]").click()
    page.wait_for_load_state("networkidle")

    expect(page.locator("#lisa-lopeta")).to_have_attribute("open", "")
    expect(page.locator("#lisa-lopeta")).to_contain_text("Vali, kuidas teema lõppes")
    # Its own panel and no other: a refusal answers itself (docs/adr/0075 §2).
    expect(page.locator("#lisa-marge")).not_to_have_attribute("open", "")
    expect(page.locator("#lisa-lopeta [name=closing_words]")).to_have_value("Midagi juhtus.")
    expect(page.locator(".badge--state")).to_contain_text("Avatud")
