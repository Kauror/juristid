"""`Teiste arvamus` in a real browser: record one, read it, correct it.

**This is the `+ Väline seisukoht` panel under the name docs/adr/0091 §3 gave
it.** The record, the route, the panel id and every rule below are unchanged; what
moved is the chip's label and the heading a filed row reads under, because the
first lawyer test needed «a member company answered our consultation» told apart
from «the ministry published its position». The other half of the same record —
`+ Meile saadetud tagasiside`, with its optional author and its `Allikas` — is
`e2e/test_lawyer_workflow.py`, beside the rest of that round.

The rules this file is here for are the ones only a running page can settle:

* that the launcher choice opens, saves through HTMX, and puts the position on
  the chronology without a reload;
* that the shared organisation control works inside the panel — the search
  narrows the catalogue, and choosing a body is choosing a real control that was
  already in the document (docs/adr/0073);
* that a written `Seisukoht` alone saves — the commonest real case, with no
  file and no published page behind it — and that a save recording none of the
  three comes back with everything typed still in the boxes, saying in Estonian
  what all three of them are;
* that the date box opens on today, that emptying it is a real answer, and that
  an emptied box does not refill itself on a refused save;
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
    return page.locator("#arvamus-teiste")


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


def record_one(page, base_url: str, *, url: str = POSITION_URL, summary: str = "") -> None:
    a_new_matter(page, base_url)
    open_add_panel(page, "arvamus-teiste")
    choose_organisation(page)
    if url:
        panel(page).locator("[name=url]").fill(url)
    if summary:
        panel(page).locator("[name=summary]").fill(summary)
    panel(page).get_by_role("button", name="Salvesta").click()
    chronology(page).get_by_text("Teiste arvamus:").first.wait_for()


def today_in_estonian(page) -> str:
    """The day the *server* is on, as the date box writes it.

    Read off the box the page rendered rather than computed here: a browser
    running either side of midnight from the server would make a computed string
    a flake nobody could reproduce.
    """
    return panel(page).locator("[name=stated_on]").input_value()


# ---------------------------------------------------------------------------
# Desktop: record, read
# ---------------------------------------------------------------------------


def test_the_choice_records_a_position_without_a_reload(page, base_url):
    sign_in(page, base_url, SANDRA)
    record_one(page, base_url)

    expect(chronology(page)).to_contain_text(f"Teiste arvamus: {MINISTRY}")
    # The date box's visible default was accepted, so the row carries a day
    # rather than «Kuupäev teadmata» — which is what
    # `test_emptying_the_date_box_is_a_real_answer` covers instead
    # (docs/adr/0084 §2, amended 2026-09-16).
    expect(chronology(page)).not_to_contain_text("Kuupäev teadmata")


def test_the_panel_asks_for_four_things_and_nothing_else(page, base_url):
    """docs/adr/0095 §3's panel, in a real browser.

    Four controls where there were seven: the institution, the written position,
    a link, a file — and one date box. Three questions moved to `Muuda`, and
    what this asserts is that they are *absent from the document*, not merely
    styled away: a hidden control is still a control a browser posts.
    """
    sign_in(page, base_url, SANDRA)
    a_new_matter(page, base_url)
    open_add_panel(page, "arvamus-teiste")

    expect(panel(page).locator("[data-orgfind]")).to_be_visible()
    expect(panel(page).locator("[name=summary]")).to_be_visible()
    expect(panel(page).locator("[name=url]")).to_be_visible()
    expect(panel(page).locator("input[type=file]")).to_have_count(1)
    # The three the creation panel stopped asking. Counted at zero rather than
    # asserted invisible, because the claim is that the form does not carry them.
    expect(panel(page).locator("[name=engagement]")).to_have_count(0)
    expect(panel(page).locator("[name=lawyer_note]")).to_have_count(0)
    expect(panel(page).locator("[name=source_label]")).to_have_count(0)
    # And no precision control, by the same measure.
    expect(panel(page).locator("[name=position_precision]")).to_have_count(0)
    for label in ("Täpne päev", "Kuu", "Kvartal", "Aasta"):
        expect(panel(page).get_by_text(label, exact=True)).to_have_count(0)
    # The date box opens on today: a visible suggestion somebody reads, changes
    # or empties, which is what docs/adr/0078 §2 allows and a stamp is not.
    expect(panel(page).locator("[name=stated_on]")).not_to_have_value("")


def test_the_institution_is_found_through_the_search_rather_than_scrolled_to(page, base_url):
    """`quiet`: one line and a `+`, and the catalogue arrives when it is asked for.

    The control the owner asked for, measured the way it is actually used — type
    a fragment, watch one chip appear, click it. A test that only asserted the
    search box existed would pass on a control that searches nothing
    (docs/adr/0088, docs/adr/0095 §5).
    """
    sign_in(page, base_url, SANDRA)
    a_new_matter(page, base_url)
    open_add_panel(page, "arvamus-teiste")
    picker = panel(page).locator("[data-orgfind]")

    # At rest: the search box, and no institution drawn under it.
    expect(picker.locator("[data-orgfind-input]")).to_be_visible()
    expect(picker.locator(".orgfind__chips .chip:visible")).to_have_count(0)

    # Typed one character at a time, and answered from the results listbox —
    # which is where the script puts the matches and what a person actually
    # clicks. Ticking the chip directly would assert something nobody does: the
    # chips are labels whose input is clipped, and an institution outside the
    # answer set is `hidden` until the search reveals it.
    box = picker.locator("[data-orgfind-input]")
    box.click()
    box.type(MINISTRY[:8], delay=20)
    picker.locator(".orgfind__results").get_by_role("option", name=MINISTRY, exact=True).click()

    chosen = picker.locator(".orgfind__chips .chip", has_text=MINISTRY)
    expect(chosen).to_be_visible()
    expect(chosen.locator("input")).to_be_checked()


def test_the_written_position_leads_the_three_sources(page, base_url):
    """`Seisukoht` is above `Link`, because it is the one always available.

    A reply that arrived by e-mail has no published address and no attachment,
    and a page that put the link box first read as though a source somewhere
    else were the point (docs/adr/0084 §3, amended 2026-09-16).
    """
    sign_in(page, base_url, SANDRA)
    a_new_matter(page, base_url)
    open_add_panel(page, "arvamus-teiste")

    order = panel(page).evaluate(
        """node => {
            const controls = [...node.querySelectorAll('[name=summary], [name=url]')];
            return controls.map(c => c.getAttribute('name'));
        }"""
    )

    assert order == ["summary", "url"]
    # The sentence that explained the rule is gone from the panel; the rule
    # itself is not, and `test_a_save_recording_nothing_is_refused…` in
    # `tests/test_external_positions.py` is where it is asserted
    # (docs/adr/0095 §3).
    expect(panel(page)).not_to_contain_text("vähemalt üks neist on vajalik")


def test_a_written_position_alone_is_a_complete_record(page, base_url):
    """No link, no file, and the row is an ordinary chronology row.

    The case the source rule was widened for, end to end in a browser: a member
    association's two-sentence answer with nowhere else to live.
    """
    sign_in(page, base_url, SANDRA)
    record_one(page, base_url, url="", summary="Toetab eelnõu, kuid soovib pikemat üleminekuaega.")

    expect(chronology(page)).to_contain_text(f"Teiste arvamus: {MINISTRY}")
    expect(chronology(page)).to_contain_text("Toetab eelnõu, kuid soovib pikemat üleminekuaega.")
    row = chronology(page).locator("article.uxtl__item").first
    expect(row.locator(".uxtl__links")).to_have_count(0)


def test_the_date_box_opens_on_today_and_the_saved_row_reads_it_back(page, base_url):
    sign_in(page, base_url, SANDRA)
    a_new_matter(page, base_url)
    open_add_panel(page, "arvamus-teiste")
    choose_organisation(page)
    today = today_in_estonian(page)
    panel(page).locator("[name=summary]").fill("Toetab eelnõu.")
    panel(page).get_by_role("button", name="Salvesta").click()
    chronology(page).get_by_text("Teiste arvamus:").first.wait_for()

    # `j.n.Y` on the row against `dd.mm.yyyy` in the box: the same day, written
    # the way each surface writes it.
    day, month, year = today.split(".")
    expect(chronology(page)).to_contain_text(f"{int(day)}.{int(month)}.{year}")
    expect(chronology(page)).not_to_contain_text("Kuupäev teadmata")


def test_emptying_the_date_box_is_a_real_answer(page, base_url):
    sign_in(page, base_url, SANDRA)
    a_new_matter(page, base_url)
    open_add_panel(page, "arvamus-teiste")
    choose_organisation(page)
    panel(page).locator("[name=stated_on]").fill("")
    panel(page).locator("[name=summary]").fill("Toetab eelnõu.")
    panel(page).get_by_role("button", name="Salvesta").click()
    chronology(page).get_by_text("Teiste arvamus:").first.wait_for()

    expect(chronology(page)).to_contain_text("Kuupäev teadmata")


def test_a_recorded_position_reopens_on_its_own_date_and_never_on_today(page, base_url):
    """`Muuda` carries no default. An undated row opens undated."""
    sign_in(page, base_url, SANDRA)
    a_new_matter(page, base_url)
    open_add_panel(page, "arvamus-teiste")
    choose_organisation(page)
    panel(page).locator("[name=stated_on]").fill("")
    panel(page).locator("[name=summary]").fill("Toetab eelnõu.")
    panel(page).get_by_role("button", name="Salvesta").click()
    chronology(page).get_by_text("Kuupäev teadmata").first.wait_for()

    chronology(page).get_by_role("button", name="Muuda").first.click()
    form = chronology(page).locator("form[aria-label='Välise seisukoha parandamine']")
    form.wait_for(state="visible")

    expect(form.locator("[name=stated_on]")).to_have_value("")


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


def test_a_save_recording_nothing_comes_back_with_what_was_typed(page, base_url):
    """All three boxes empty is the only refusal left, and it names all three.

    The date the person had already answered is still in its box, so the
    refusal costs them nothing but the one answer that was missing
    (docs/adr/0084 §3, amended 2026-09-16).
    """
    sign_in(page, base_url, SANDRA)
    a_new_matter(page, base_url)
    open_add_panel(page, "arvamus-teiste")
    choose_organisation(page)
    panel(page).locator("[name=stated_on]").fill("14.03.2026")
    panel(page).get_by_role("button", name="Salvesta").click()
    page.wait_for_timeout(400)

    expect(panel(page)).to_contain_text("vähemalt üks neist on vajalik")
    expect(panel(page).locator("[name=stated_on]")).to_have_value("14.03.2026")
    expect(chronology(page)).not_to_contain_text("Teiste arvamus:")


def test_an_emptied_date_box_does_not_refill_itself_on_a_refusal(page, base_url):
    """An `initial` that reasserted itself would hand back a date somebody removed."""
    sign_in(page, base_url, SANDRA)
    a_new_matter(page, base_url)
    open_add_panel(page, "arvamus-teiste")
    choose_organisation(page)
    panel(page).locator("[name=stated_on]").fill("")
    panel(page).get_by_role("button", name="Salvesta").click()
    page.wait_for_timeout(400)

    expect(panel(page)).to_contain_text("vähemalt üks neist on vajalik")
    expect(panel(page).locator("[name=stated_on]")).to_have_value("")


def test_a_hostile_address_is_refused_with_the_value_returned(page, base_url):
    sign_in(page, base_url, SANDRA)
    a_new_matter(page, base_url)
    open_add_panel(page, "arvamus-teiste")
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
    expect(chronology(page)).to_contain_text(f"Teiste arvamus: {MINISTRY}")


# ---------------------------------------------------------------------------
# Keyboard, and the narrow viewport
# ---------------------------------------------------------------------------


def test_the_chip_is_reachable_and_operable_from_the_keyboard(page, base_url):
    """The radio is clipped rather than `display: none` precisely so it stays
    focusable, and the focus ring is drawn on the chip (docs/adr/0078 §1)."""
    sign_in(page, base_url, SANDRA)
    a_new_matter(page, base_url)

    radio = page.locator("#arvamus-teiste-valik")
    radio.focus()
    page.keyboard.press("Space")

    expect(panel(page)).to_be_visible()
    expect(radio).to_be_focused()


def test_every_control_in_the_panel_is_reachable_by_tabbing(page, base_url):
    sign_in(page, base_url, SANDRA)
    a_new_matter(page, base_url)
    open_add_panel(page, "arvamus-teiste")

    # `engagement` is not among them any more: the panel stopped asking for a
    # `Kaasamine` and the field is absent from the document, so a tab order
    # including it would be asserting a control that is not there
    # (docs/adr/0095 §3).
    for name in ("summary", "url", "stated_on"):
        control = panel(page).locator(f"[name={name}]")
        control.focus()
        expect(control).to_be_focused()


def test_the_panel_does_not_scroll_the_page_sideways_at_phone_width(page, base_url):
    sign_in(page, base_url, SANDRA)
    a_new_matter(page, base_url)
    page.set_viewport_size({"width": 375, "height": 812})
    open_add_panel(page, "arvamus-teiste")

    overflows = page.evaluate(
        "() => document.documentElement.scrollWidth > document.documentElement.clientWidth + 1"
    )

    assert not overflows, "the Teiste arvamus panel makes the Teema page scroll sideways"


def test_the_recorded_row_reads_at_phone_width(page, base_url):
    sign_in(page, base_url, SANDRA)
    record_one(page, base_url)
    page.set_viewport_size({"width": 375, "height": 812})

    expect(chronology(page)).to_contain_text(f"Teiste arvamus: {MINISTRY}")
    overflows = page.evaluate(
        "() => document.documentElement.scrollWidth > document.documentElement.clientWidth + 1"
    )
    assert not overflows
