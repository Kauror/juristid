"""Correcting a filed Sissekanne, in a real browser.

The Python suite proves the rule at the service and at the route: the body
changes, the revision is kept, no second `Entry` appears, a stale form is
refused, and a closed Matter still takes no new work. What only a browser can
show is the half that is htmx.

That `Muuda` turns the row it is on into a form *with the existing text already
in it* — the difference between correcting a paragraph and retyping it. That
saving puts the corrected words back in the same row rather than adding a
second one underneath. That the correction survives a reload, so what was shown
is what was stored. And on a closed Teema, that the page still reads as closed
while the correction lands, with none of the controls that would add something
new.

Everything here is synthetic, and every test files its own Matter. The seeded
world is shared across a shard and never reset between files
(`e2e/conftest.py`), so a test that corrected a seeded entry would be rewriting
a row other files read and `e2e/test_ui_regression.py` photographs.
"""

from __future__ import annotations

import re

import pytest
from playwright.sync_api import expect

from e2e.conftest import (
    MARTIN,
    READER,
    create_matter,
    open_add_panel,
    open_composer,
    open_matter,
    sign_in,
    unique_title,
)

pytestmark = pytest.mark.e2e

#: The seeded open Matter, spelled as `seed_e2e_data` writes it. Copied rather
#: than imported for the reason `e2e/conftest.py` gives: this directory
#: deliberately imports no application code.
OPEN_TITLE = (
    "Tavaline avatud teema kõigile nähtav — pakendiseaduse ja sellega seonduvalt "
    "teiste seaduste muutmise seaduse eelnõu väljatöötamiskavatsus"
)

ORIGINAL = "Ministeerium lubas uue sõnastuse reedeks."
CORRECTED = "Ministeerium lubas uue sõnastuse esmaspäevaks."


def _file_an_entry(page, text: str) -> None:
    """Write one note through the real composer, the way a lawyer would."""
    open_composer(page)
    page.locator("#id_marge_title").fill(text)
    page.locator("#lisa-marge button[type=submit]").click()
    page.wait_for_load_state("networkidle")


def _entry_row(page):
    """The one work entry on a freshly filed Matter."""
    row = page.locator(".uxtl__entry").first
    row.wait_for()
    return row


def _open_the_editor(page):
    row = _entry_row(page)
    row.get_by_role("button", name="Muuda", exact=True).click()
    box = page.locator(".uxtl__editform textarea")
    box.wait_for()
    return box


def _marker_id(page) -> str:
    """The `muudetud` slot belonging to the row under test.

    Derived from the row's own id rather than searched for by its words: the
    element is deliberately empty until there is something to say, so «find the
    text» cannot find it at all in the state this suite most needs to assert.
    """
    entry_id = _entry_row(page).get_attribute("id")
    assert entry_id and entry_id.endswith("-sisu"), entry_id
    return f"{entry_id[: -len('-sisu')]}-muudetud"


def _save(page):
    with page.expect_response(
        lambda response: "/muuda/" in response.url and response.request.method == "POST"
    ) as caught:
        page.locator(".uxtl__editform button[type=submit]").click()
    return caught.value


def test_a_filed_entry_can_be_corrected_in_place(page, base_url):
    """The whole feature, end to end, on an open Teema."""
    sign_in(page, base_url, MARTIN)
    create_matter(page, base_url, unique_title("Paranduse brauserikatse"))
    _file_an_entry(page, ORIGINAL)

    # Entry rows, not chronology rows: a freshly filed Teema already carries a
    # `Teema loodud` milestone, which is a line about the file and not an entry.
    expect(page.locator(".uxtl__entry")).to_have_count(1)

    box = _open_the_editor(page)

    # Prefilled. Retyping a paragraph to fix a date is the thing this avoids.
    assert ORIGINAL in box.input_value()

    box.fill(f"<p>{CORRECTED}</p>")
    saved = _save(page)
    assert saved.status == 200, f"the correction was refused: {saved.status}"
    page.wait_for_load_state("networkidle")

    # Same row, corrected, and no second line underneath it.
    row = _entry_row(page)
    expect(row).to_contain_text(CORRECTED)
    expect(row).not_to_contain_text(ORIGINAL)
    expect(page.locator(".uxtl__entry")).to_have_count(1)
    expect(page.locator(".uxtl__editform")).to_have_count(0)

    # And the row says it has been corrected — in the meta line above the text,
    # which this response reached out of band, and with no second date beside it.
    marker = page.locator(f"#{_marker_id(page)}")
    expect(marker).to_be_visible()
    expect(marker).to_have_text("muudetud")

    # What was shown is what was stored.
    page.reload()
    page.wait_for_load_state("networkidle")
    expect(page.locator(".uxtl__entry").first).to_contain_text(CORRECTED)
    expect(page.locator(".uxtl__entry")).to_have_count(1)
    expect(page.locator(f"#{_marker_id(page)}")).to_be_visible()


def test_tuhista_leaves_the_entry_exactly_as_it_was(page, base_url):
    sign_in(page, base_url, MARTIN)
    create_matter(page, base_url, unique_title("Paranduse brauserikatse: tühista"))
    _file_an_entry(page, ORIGINAL)

    box = _open_the_editor(page)
    box.fill("<p>Seda ei salvestata kunagi.</p>")
    page.locator(".uxtl__editform").get_by_role("button", name="Tühista", exact=True).click()
    page.wait_for_load_state("networkidle")

    row = _entry_row(page)
    expect(row).to_contain_text(ORIGINAL)
    expect(page.locator(".uxtl__editform")).to_have_count(0)
    # Nothing was written, so nothing claims it was: the marker is still hidden.
    expect(page.locator(f"#{_marker_id(page)}")).to_be_hidden()


def test_a_closed_teema_takes_the_correction_and_no_new_work(page, base_url):
    """Both halves of the rule, on one file, in one test.

    A correction that worked because the closed-Matter boundary had been relaxed
    would pass the first half of this and fail the second.
    """
    sign_in(page, base_url, MARTIN)
    create_matter(page, base_url, unique_title("Paranduse brauserikatse: suletud"))
    _file_an_entry(page, ORIGINAL)

    # Close it through the real panel, the way `test_teema_closing_flow` does.
    open_add_panel(page, "teema-lopeta")
    page.locator("#teema-lopeta .uxchip", has_text="Menetlus lõppes").click()
    with page.expect_response(
        lambda response: "/lisa/lopeta/" in response.url and response.request.method == "POST"
    ) as caught:
        page.locator("#teema-lopeta button[type=submit]").click()
    assert caught.value.status == 200
    page.wait_for_load_state("networkidle")

    # The page reads as closed, and offers nothing that would add to it.
    expect(page.locator(".badge--state")).to_contain_text("Suletud")
    expect(page.locator("#lisa-teemale")).to_have_count(0)

    # And the history is still correctable.
    box = _open_the_editor(page)
    assert ORIGINAL in box.input_value()
    box.fill(f"<p>{CORRECTED}</p>")
    saved = _save(page)
    assert saved.status == 200, f"a closed Teema refused a correction: {saved.status}"
    page.wait_for_load_state("networkidle")

    expect(_entry_row(page)).to_contain_text(CORRECTED)
    # Still closed, still no way to add anything new.
    expect(page.locator(".badge--state")).to_contain_text("Suletud")
    expect(page.locator("#lisa-teemale")).to_have_count(0)
    assert re.search(r"/teemad/[0-9a-f-]{36}/$", page.url)


def test_a_reader_is_offered_no_correction(page, base_url):
    """Reading the chronology is not being able to rewrite it."""
    sign_in(page, base_url, READER)
    open_matter(page, base_url, OPEN_TITLE)

    expect(page.locator("#ajalugu-loend")).to_be_visible()
    expect(page.locator("#ajalugu-loend").get_by_text("Avalik sissekanne")).to_be_visible()
    expect(page.get_by_role("button", name="Muuda", exact=True)).to_have_count(0)
