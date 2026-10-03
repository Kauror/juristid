"""The second user-side correction batch, in the browser (docs/adr/0121).

Three journeys whose truth depends on the page itself: a checkbox that opens its
box by stylesheet, a file chooser posting several files at once, and a
`Kaasamine` whose edit form must ask what its panel asked. The rules behind them
are asserted without a browser in `tests/test_user_correction_batch_two.py`.
"""

from __future__ import annotations

import pytest
from playwright.sync_api import expect

from e2e.conftest import MARTIN, chronology, create_matter, open_kaik_row, sign_in, unique_title
from e2e.test_engagement import open_panel as open_kaasamine
from e2e.test_uus_teema_files import PDF_BYTES, create_with_files

pytestmark = pytest.mark.e2e


# `test_closing_with_muu_records_the_ordinary_work_win` drove `+ Lõpeta teema`,
# retired by docs/adr/0131 §11; the browser proof of closing is
# `e2e/test_teema_closing_flow.py`, and a win is `+ Märge → Töövõit`.


def test_files_chosen_together_are_one_teema_kaik_line(page, base_url, tmp_path):
    """Five files in one `Loo teema` press read «lisas 5 dokumenti», once (§6)."""
    sign_in(page, base_url, MARTIN)
    paths = []
    for n in range(1, 6):
        path = tmp_path / f"koos-{n}.pdf"
        path.write_bytes(PDF_BYTES + str(n).encode())
        paths.append(str(path))

    create_with_files(page, base_url, unique_title("Viis faili korraga"), paths)

    expect(chronology(page).get_by_text("lisas 5 dokumenti")).to_have_count(1)
    expect(chronology(page).get_by_text("lisas dokumendi")).to_have_count(0)


def test_a_kaasamine_edits_what_it_asked_and_its_links_read_as_names(page, base_url):
    """`Muuda` asks what `+ Kaasamine` asks — since docs/adr/0127 §2 that
    includes `Veebileht` and `Märkus` again; a bare host is saved with
    `https://`; the links read «Smaily» and «Alchemer» and nothing else (§4, §5)."""
    sign_in(page, base_url, MARTIN)
    create_matter(page, base_url, unique_title("Kaasamise väljad"))

    form = open_kaasamine(page)
    form.locator("[name=audience]").fill("liikmed")
    form.locator("[name=smaily_url]").fill("www.sendsmaily.net/kampaania")
    form.locator("[name=alchemer_url]").fill("survey.alchemer.eu/s3/97")
    form.get_by_role("button", name="Salvesta", exact=True).click()
    chronology(page).get_by_text("Kaasamine: liikmed").first.wait_for()

    row = chronology(page).locator(".uxtl__item", has_text="Kaasamine: liikmed").first
    open_kaik_row(row)
    links = row.locator("a.uxtl__link")
    expect(links).to_have_count(2)
    expect(links.nth(0)).to_have_attribute("href", "https://www.sendsmaily.net/kampaania")
    assert links.nth(0).evaluate("node => node.firstChild.textContent.trim()") == "Smaily"
    assert links.nth(1).evaluate("node => node.firstChild.textContent.trim()") == "Alchemer"
    # Nothing is drawn after a label.
    assert links.nth(0).evaluate("node => getComputedStyle(node, '::after').content") in (
        "none",
        "",
    )

    row.locator(".uxtl__edit", has_text="Muuda").first.click()
    edit = page.locator(".uxtl__editform")
    edit.wait_for()
    expect(edit.locator("[name=feedback_deadline]")).to_be_visible()
    expect(edit.locator("[name=smaily_url]")).to_be_visible()
    # `Veebileht` and `Märkus` are back on `Muuda`, as on `+ Kaasamine`
    # (docs/adr/0127 §2, reversing docs/adr/0121 §4's removal).
    expect(edit.locator("[name=url]")).to_be_visible()
    expect(edit.locator("[name=note]")).to_be_visible()
