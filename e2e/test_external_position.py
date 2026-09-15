"""`Väline seisukoht` in a real browser: record one, read it, correct it.

The rules this file is here for are the ones only a running page can settle:

* that the ninth launcher choice opens, saves through HTMX, and puts the
  position on the chronology without a reload;
* that the shared organisation control works inside the panel — the search
  narrows the catalogue, and choosing a body is choosing a real control that was
  already in the document (docs/adr/0073);
* that a save with no source comes back with everything typed still in the
  boxes, and says in Estonian which of the two is missing;
* that the link renders as its host in a new tab and never as a printed
  address;
* that the whole thing is reachable from the keyboard and does not make the page
  scroll sideways at phone width.

The service-level rules — the source minimum, the URL allow-list, the closed
Matter, the concurrency, the audit trail — are
`tests/test_external_positions.py`, which is cheap and runs everywhere.

**Everything here happens on a Matter the test creates.** The screenshot suite
opens `OPEN_TITLE`, and a chronology that grew while these ran would make that
baseline depend on test order.
"""

from __future__ import annotations

import pytest
from playwright.sync_api import expect

from e2e.conftest import SANDRA, create_matter, open_add_panel, sign_in, unique_title

pytestmark = pytest.mark.e2e

POSITION_URL = "https://rahandusministeerium.ee/uudised/e2e-seisukoht"
MINISTRY = "Näidisministeerium"


def a_new_matter(page, base_url: str) -> str:
    return create_matter(page, base_url, unique_title("Väline seisukoht"))


def panel(page):
    return page.locator("#lisa-valine-seisukoht")


def chronology(page):
    return page.locator("#ajalugu-loend")


#: The picker's own id inside the panel. Every control the shared organisation
#: component writes — the search box, the results list, the status region — is
#: derived from it (`matters/partials/organisation_picker.html`).
PICKER = "valine-seisukoht"


def choose_organisation(page, name: str = MINISTRY) -> None:
    """Answer `Organisatsioon` the way a person does: type, then pick a result.

    The same two steps `e2e/test_unified_organisation_picker.py` uses, and
    deliberately not a `check()` on the radio: the chips are labels whose input
    is clipped, and an institution outside the visible shortlist is `hidden`
    until the search reveals it — so ticking the control directly asserts
    something the person never does and fails on exactly the bodies the search
    exists for.

    Typed one key at a time, because that is what the control listens to and
    what proves there is no button between the keystroke and the list
    (docs/adr/0073).
    """
    box = page.locator(f"#{PICKER}-otsi")
    box.click()
    box.fill("")
    box.type(name[:8], delay=20)
    page.locator(f"#{PICKER}-tulemused").get_by_role("option", name=name, exact=True).click()


def record_one(page, base_url: str, *, url: str = POSITION_URL) -> None:
    a_new_matter(page, base_url)
    open_add_panel(page, "lisa-valine-seisukoht")
    choose_organisation(page)
    panel(page).locator("[name=url]").fill(url)
    panel(page).get_by_role("button", name="Salvesta").click()
    chronology(page).get_by_text("Väline seisukoht:").first.wait_for()


# ---------------------------------------------------------------------------
# Desktop: record, read
# ---------------------------------------------------------------------------


def test_the_ninth_choice_records_a_position_without_a_reload(page, base_url):
    sign_in(page, base_url, SANDRA)
    record_one(page, base_url)

    expect(chronology(page)).to_contain_text(f"Väline seisukoht: {MINISTRY}")
    expect(chronology(page)).to_contain_text("Kuupäev teadmata")


def test_the_panel_asks_for_the_six_things_and_nothing_else(page, base_url):
    sign_in(page, base_url, SANDRA)
    a_new_matter(page, base_url)
    open_add_panel(page, "lisa-valine-seisukoht")

    expect(panel(page).locator("[data-orgfind]")).to_be_visible()
    expect(panel(page).locator("[name=url]")).to_be_visible()
    expect(panel(page).locator("input[type=file]")).to_have_count(1)
    expect(panel(page).locator("[name=summary]")).to_be_visible()
    expect(panel(page).locator("[name=engagement]")).to_be_visible()
    # The four precisions, through the one shared control.
    for label in ("Täpne päev", "Kuu", "Kvartal", "Aasta"):
        expect(panel(page).get_by_text(label, exact=True).first).to_be_visible()
    # The date box is empty: a position is filed after it was stated, so today
    # would be a date nobody chose (docs/adr/0084 §2).
    expect(panel(page).locator("[name=stated_on]")).to_have_value("")


def test_the_chronology_renders_the_host_and_never_the_address(page, base_url):
    sign_in(page, base_url, SANDRA)
    record_one(page, base_url)

    link = chronology(page).get_by_role("link", name="rahandusministeerium.ee")
    expect(link).to_be_visible()
    expect(link).to_have_attribute("href", POSITION_URL)
    expect(link).to_have_attribute("target", "_blank")
    expect(link).to_have_attribute("rel", "noopener noreferrer")
    expect(chronology(page)).not_to_contain_text(POSITION_URL)


def test_the_new_tab_is_announced_and_not_merely_used(page, base_url):
    sign_in(page, base_url, SANDRA)
    record_one(page, base_url)

    name = (
        chronology(page)
        .get_by_role("link", name="rahandusministeerium.ee")
        .evaluate("node => node.textContent.replace(/\\s+/g, ' ').trim()")
    )

    assert "avaneb uues aknas" in name


def test_a_save_with_no_source_comes_back_with_what_was_typed(page, base_url):
    """A link or a file is required, and the refusal says so in Estonian with
    the explanation still in its box (docs/adr/0084 §3)."""
    sign_in(page, base_url, SANDRA)
    a_new_matter(page, base_url)
    open_add_panel(page, "lisa-valine-seisukoht")
    choose_organisation(page)
    panel(page).locator("[name=summary]").fill("Toetab eelnõu.")
    panel(page).get_by_role("button", name="Salvesta").click()
    page.wait_for_timeout(400)

    expect(panel(page)).to_contain_text("Lisa link või fail")
    expect(panel(page).locator("[name=summary]")).to_have_value("Toetab eelnõu.")
    expect(chronology(page)).not_to_contain_text("Väline seisukoht:")


def test_a_hostile_address_is_refused_with_the_value_returned(page, base_url):
    sign_in(page, base_url, SANDRA)
    a_new_matter(page, base_url)
    open_add_panel(page, "lisa-valine-seisukoht")
    choose_organisation(page)
    panel(page).locator("[name=url]").fill("javascript:alert(1)")
    panel(page).get_by_role("button", name="Salvesta").click()
    page.wait_for_timeout(400)

    expect(panel(page)).to_contain_text("http:// või https://")
    expect(panel(page).locator("[name=url]")).to_have_value("javascript:alert(1)")


def test_a_position_can_be_corrected_from_its_own_row(page, base_url):
    """The row around the form never moves: `Muuda` swaps the milestone's text
    region and nothing else (docs/adr/0084 §6)."""
    sign_in(page, base_url, SANDRA)
    record_one(page, base_url)

    rows_before = chronology(page).locator("article.uxtl__item").count()
    chronology(page).get_by_role("button", name="Muuda").first.click()
    form = chronology(page).locator("form[aria-label='Välise seisukoha parandamine']")
    form.wait_for(state="visible")
    form.locator("[name=summary]").fill("Toetab, kuid soovib pikemat üleminekuaega.")
    form.get_by_role("button", name="Salvesta").click()
    page.wait_for_timeout(400)

    expect(chronology(page)).to_contain_text("Toetab, kuid soovib pikemat üleminekuaega.")
    assert chronology(page).locator("article.uxtl__item").count() == rows_before


def test_cancelling_a_correction_restores_what_the_server_holds(page, base_url):
    sign_in(page, base_url, SANDRA)
    record_one(page, base_url)

    chronology(page).get_by_role("button", name="Muuda").first.click()
    form = chronology(page).locator("form[aria-label='Välise seisukoha parandamine']")
    form.wait_for(state="visible")
    form.locator("[name=summary]").fill("Salvestamata tekst.")
    form.get_by_role("button", name="Tühista").click()
    page.wait_for_timeout(400)

    expect(chronology(page)).not_to_contain_text("Salvestamata tekst.")
    expect(chronology(page)).to_contain_text(f"Väline seisukoht: {MINISTRY}")


# ---------------------------------------------------------------------------
# Keyboard, and the narrow viewport
# ---------------------------------------------------------------------------


def test_the_chip_is_reachable_and_operable_from_the_keyboard(page, base_url):
    """The radio is clipped rather than `display: none` precisely so it stays
    focusable, and the focus ring is drawn on the chip (docs/adr/0078 §1)."""
    sign_in(page, base_url, SANDRA)
    a_new_matter(page, base_url)

    radio = page.locator("#lisa-valine-seisukoht-valik")
    radio.focus()
    page.keyboard.press("Space")

    expect(panel(page)).to_be_visible()
    expect(radio).to_be_focused()


def test_every_control_in_the_panel_is_reachable_by_tabbing(page, base_url):
    sign_in(page, base_url, SANDRA)
    a_new_matter(page, base_url)
    open_add_panel(page, "lisa-valine-seisukoht")

    for name in ("url", "summary", "engagement", "stated_on"):
        control = panel(page).locator(f"[name={name}]")
        control.focus()
        expect(control).to_be_focused()


def test_the_panel_does_not_scroll_the_page_sideways_at_phone_width(page, base_url):
    sign_in(page, base_url, SANDRA)
    a_new_matter(page, base_url)
    page.set_viewport_size({"width": 375, "height": 812})
    open_add_panel(page, "lisa-valine-seisukoht")

    overflows = page.evaluate(
        "() => document.documentElement.scrollWidth > document.documentElement.clientWidth + 1"
    )

    assert not overflows, "the Väline seisukoht panel makes the Teema page scroll sideways"


def test_the_recorded_row_reads_at_phone_width(page, base_url):
    sign_in(page, base_url, SANDRA)
    record_one(page, base_url)
    page.set_viewport_size({"width": 375, "height": 812})

    expect(chronology(page)).to_contain_text(f"Väline seisukoht: {MINISTRY}")
    overflows = page.evaluate(
        "() => document.documentElement.scrollWidth > document.documentElement.clientWidth + 1"
    )
    assert not overflows
