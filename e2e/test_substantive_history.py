"""`Teema käik` and `Menetluse kulg` in a real browser (docs/adr/0092).

`tests/test_substantive_matter_history.py` and `tests/test_legal_process_rail.py`
hold the rules and run everywhere cheaply. This file holds the ones only a
rendered page can settle:

* that a realistic multi-round file reads as a case history rather than as an
  audit log — one row per act, the evidence under the act it evidences, and no
  «lisas dokumendi kell 14:31» line anywhere;
* that `Meile saadetud tagasiside` and `Teiste arvamus` are visibly different
  things, and that the lawyer's note is visibly separate from what the source
  said;
* that one `+ Menetluse areng` save renders as **one** row carrying its stage
  and its step, not as three;
* that «Kuupäev teadmata» is what an undated act prints, and that an approximate
  one prints its period;
* that several `Koja arvamus` rows survive independently;
* that the open `Järgmiseks` reads once, at the top;
* that the rail draws `Praegu`, `Teadmata` and `Võimalik` in words, that a late
  entry does not read as three completed steps, and that `Koda ei tegele edasi`
  is beside the rail rather than on it;
* and that none of it overflows sideways at 420 px or at 375 px.

**Everything here happens on a Matter the test creates.** The screenshot suite
opens its own titles, and a history that grew while these ran would make a
baseline depend on test order.
"""

from __future__ import annotations

import re
from datetime import date, timedelta

import pytest
from playwright.sync_api import expect

from e2e.conftest import (
    SANDRA,
    create_matter,
    open_add_panel,
    open_hetkeseis,
    sign_in,
    unique_title,
)

pytestmark = pytest.mark.e2e

MINISTRY = "Näidisministeerium"


def _estonian(on: date) -> str:
    return f"{on.day}.{on.month}.{on.year}"


def _future(days: int) -> str:
    return _estonian(date.today() + timedelta(days=days))


def _past(days: int) -> str:
    return _estonian(date.today() - timedelta(days=days))


def panel(page, panel_id: str):
    return page.locator(f"#{panel_id}")


def history(page):
    return page.locator("#ajalugu-loend")


def rail(page):
    return page.locator(".lprail")


def choose_organisation(page, picker: str, name: str = MINISTRY) -> None:
    box = page.locator(f"#{picker}-otsi")
    box.click()
    box.fill("")
    box.type(name[:8], delay=20)
    page.locator(f"#{picker}-tulemused").get_by_role("option", name=name, exact=True).click()


def a_new_matter(page, base_url: str, *, stage: str | None = None) -> str:
    return create_matter(page, base_url, unique_title("Teema käik"), stage=stage)


def _matter_with_instrument(page, base_url: str, instrument: str, *, stage: str | None) -> str:
    """A Matter carrying one reviewed `Õigusakt`, filed through the real form.

    The rail chooses its template from `Menetlusliik` where that safely can and
    otherwise from Package A's instrument grouping, and `Uus teema` asks for the
    instrument rather than the track — so this is the journey a lawyer actually
    takes to a file the rail can read (docs/adr/0090 §4, docs/adr/0092 §12).
    """
    page.goto(f"{base_url}/teemad/uus/")
    page.wait_for_load_state("networkidle")
    page.fill("#id_title", unique_title("Menetluse kulg"))
    page.get_by_role("checkbox", name=instrument, exact=True).check()
    if stage is not None:
        # Behind a menu since docs/adr/0094 §2; it shuts itself once answered.
        open_hetkeseis(page)
        page.get_by_role("radio", name=stage, exact=True).check()
    page.get_by_role("button", name="Loo teema").click()
    page.wait_for_url(re.compile(r"/teemad/[0-9a-f-]{36}/$"))
    return page.url


def _record_development(page, *, title: str, occurred_on: str | None, **extra) -> None:
    open_add_panel(page, "marge-tavaline")
    form = panel(page, "marge-tavaline")
    form.locator("[name=title]").fill(title)
    form.locator("[name=occurred_on]").fill(occurred_on or "")
    for name, value in extra.items():
        form.locator(f"[name={name}]").fill(value)
    form.get_by_role("button", name="Salvesta", exact=True).click()
    page.wait_for_load_state("networkidle")


def _record_koda_opinion(page, *, sent_on: str, filename: str) -> None:
    open_add_panel(page, "arvamus-koja")
    form = panel(page, "arvamus-koja")
    form.locator("input[type=file]").set_input_files(
        {"name": filename, "mimeType": "application/pdf", "buffer": b"%PDF-1.4 arvamus"}
    )
    form.locator("[name=sent_on]").fill(sent_on)
    # Through the picker's own search since docs/adr/0095 §1: the catalogue is
    # behind the search box and an institution outside the answer set is
    # `hidden` until it is found, so ticking the control directly would assert
    # an interaction nobody has.
    choose_organisation(page, "koja-adressaat")
    form.get_by_role("button", name="Registreeri arvamus").click()
    page.wait_for_load_state("networkidle")


# ---------------------------------------------------------------------------
# The section, and what it is called
# ---------------------------------------------------------------------------


def test_the_history_section_is_called_teema_kaik(page, base_url):
    sign_in(page, base_url, SANDRA)
    a_new_matter(page, base_url)

    section = page.locator("#ajajoon")
    expect(section.locator(".accordion__title")).to_have_text("Teema käik")
    # The anchor a shared link addresses is deliberately unchanged.
    expect(section).to_have_count(1)


def test_collapsing_the_history_does_not_hide_menetluse_kulg(page, base_url):
    """The reason the rail is a sibling and not a block inside the disclosure.

    Collapsing six months of history is the ordinary thing to do when the
    question is where the bill has got to — and until this round it took the
    answer to that question away with it (docs/adr/0092 §2, amended).
    """
    sign_in(page, base_url, SANDRA)
    _matter_with_instrument(page, base_url, "Seadus", stage="Kooskõlastusringil")

    expect(rail(page)).to_be_visible()
    # The structural fact, in the browser's own tree rather than in the markup.
    expect(page.locator("#ajajoon .lprail")).to_have_count(0)

    page.locator("#ajajoon > summary").click()
    expect(page.locator("#ajajoon")).not_to_have_attribute("open", "")
    expect(history(page)).not_to_be_visible()
    expect(rail(page)).to_be_visible()
    expect(rail(page)).to_contain_text("Menetluse kulg")


def test_the_two_sections_are_headings_of_the_same_level(page, base_url):
    """«Where is this» is not a sub-part of «what happened»."""
    sign_in(page, base_url, SANDRA)
    _matter_with_instrument(page, base_url, "Seadus", stage="Kooskõlastusringil")

    expect(page.get_by_role("heading", name="Teema käik", level=2)).to_have_count(1)
    expect(page.get_by_role("heading", level=2).filter(has_text="Menetluse kulg")).to_have_count(1)


def test_the_current_node_says_which_side_of_its_node_the_file_is_on(page, base_url):
    """`Jõustumine · Praegu` alone cannot tell two real situations apart."""
    sign_in(page, base_url, SANDRA)
    _matter_with_instrument(page, base_url, "Seadus", stage="Jõustumise ootel")

    current = rail(page).locator(".lprail__node--current")
    expect(current).to_contain_text("Jõustumine")
    expect(current).to_contain_text("Praegu")
    expect(current).to_contain_text("Jõustumise ootel")


# ---------------------------------------------------------------------------
# One act, one row
# ---------------------------------------------------------------------------


def test_one_development_save_reads_as_one_act_carrying_its_stage_and_step(page, base_url):
    """Three canonical writes, one row — the defect docs/adr/0092 §6 fixes.

    Before this the ministry's revised draft, the stage it moved the file to and
    the step the lawyer set were three separate lines, so the reason for two of
    them sat two rows away from the fact.
    """
    sign_in(page, base_url, SANDRA)
    a_new_matter(page, base_url)
    open_add_panel(page, "marge-tavaline")

    form = panel(page, "marge-tavaline")
    form.locator("[name=title]").fill("Ministeerium saatis eelnõu uue versiooni")
    form.locator("[name=occurred_on]").fill(_past(2))
    form.locator("[name=stage]").select_option(label="Kooskõlastusringil")
    form.locator("[name=next_text]").fill("Vaatan uue versiooni läbi")
    form.locator("[name=next_date]").fill(_future(4))
    form.get_by_role("button", name="Salvesta", exact=True).click()
    page.wait_for_load_state("networkidle")

    row = (
        history(page)
        .locator("article.uxtl__item")
        .filter(has_text="Ministeerium saatis eelnõu uue versiooni")
    )
    expect(row).to_have_count(1)
    expect(row).to_contain_text("Kooskõlastusringil")
    expect(row).to_contain_text("Vaatan uue versiooni läbi")
    # And not as a second and third row of its own.
    expect(history(page).locator("article.uxtl__item").filter(has_text="Hetkeseis:")).to_have_count(
        0
    )
    expect(
        history(page).locator("article.uxtl__item").filter(has_text="määras järgmise sammu")
    ).to_have_count(0)


def test_the_open_step_reads_once_at_the_top(page, base_url):
    """`PRAEGUNE TEGEVUS` is where an open instruction is read and acted on."""
    sign_in(page, base_url, SANDRA)
    a_new_matter(page, base_url)
    _record_development(
        page,
        title="Eelnõu jõudis Riigikokku",
        occurred_on=_past(3),
        next_text="Kirjutan komisjonile",
        next_date=_future(6),
    )

    expect(page.locator("#praegune-tegevus")).to_contain_text("Kirjutan komisjonile")
    expect(history(page)).not_to_contain_text("määras järgmise sammu")


# ---------------------------------------------------------------------------
# Dates
# ---------------------------------------------------------------------------


def test_an_undated_act_says_kuupaev_teadmata(page, base_url):
    """Scenario D, in the browser. Never the day somebody typed it in."""
    sign_in(page, base_url, SANDRA)
    a_new_matter(page, base_url)
    _record_development(page, title="Eelnõu jõudis Riigikokku", occurred_on=None)

    row = history(page).locator("article.uxtl__item").filter(has_text="Eelnõu jõudis Riigikokku")
    expect(row.locator(".uxtl__msdate")).to_have_text("Kuupäev teadmata")
    expect(row.locator(".uxtl__msdate")).not_to_contain_text(_estonian(date.today()))


def test_an_approximate_act_prints_its_period_and_not_a_day(page, base_url):
    """A month is written down as a month. The anchor never reaches a screen.

    Stated through `Muuda`, which is the surface that still carries the four-way
    `Täpsus` group. `+ Märge` asks for a day or nothing and always writes
    `EXACT`, so it cannot state an approximate period at all — the control
    decides per *record* now, which is where a statement about how well a date
    is known belongs (docs/adr/0097 §6.1).

    The claim is the projection's and is unchanged: a month reaches the screen
    as a month, and the stored anchor never does.
    """
    sign_in(page, base_url, SANDRA)
    a_new_matter(page, base_url)
    _record_development(page, title="Valitsus kiitis eelnõu heaks", occurred_on=None)

    row = (
        history(page).locator("article.uxtl__item").filter(has_text="Valitsus kiitis eelnõu heaks")
    )
    # The **label** is clicked, not the radio: the input is visually clipped and
    # the chip label sits over it, so `check()` on the control is intercepted by
    # the very thing a person actually presses. `e2e/test_date_precision.py`
    # chooses a precision exactly this way.
    #
    # `.uxtl__edit` rather than the accessible name: the button's name is
    # composed by `aria-labelledby` from its own word *and* the headline above
    # it, so an exact match on «Muuda» finds nothing.
    row.locator(".uxtl__edit").first.click()
    form = page.locator(".uxtl__editform")
    form.wait_for()
    form.locator("label.precision__chip", has_text="Kuu").first.click()
    form.locator("[name=areng_month]").select_option(label="Märts")
    form.locator("[name=areng_year]").fill("2026")
    form.get_by_role("button", name="Salvesta", exact=True).click()
    page.wait_for_load_state("networkidle")

    row = (
        history(page).locator("article.uxtl__item").filter(has_text="Valitsus kiitis eelnõu heaks")
    )
    expect(row.locator(".uxtl__msdate")).to_have_text("märts 2026")
    expect(row.locator(".uxtl__msdate")).not_to_contain_text("1.3.2026")


# ---------------------------------------------------------------------------
# Provenance, attribution and evidence
# ---------------------------------------------------------------------------


def test_received_and_discovered_feedback_are_visibly_different_things(page, base_url):
    sign_in(page, base_url, SANDRA)
    a_new_matter(page, base_url)

    open_add_panel(page, "arvamus-tagasiside")
    received = panel(page, "arvamus-tagasiside")
    # Both panels name an institution since docs/adr/0095 §4 — `Allikas` is a
    # `Muuda` control, and the aggregate rows it was built for keep their labels.
    # What tells these two records apart is the chip that was opened, which is
    # the whole point of this test.
    choose_organisation(page, "tagasiside")
    received.locator("[name=summary]").fill("58 vastust; enamik vastu.")
    received.get_by_role("button", name="Salvesta tagasiside").click()
    history(page).get_by_text("Meile saadetud tagasiside:").first.wait_for()

    open_add_panel(page, "arvamus-teiste")
    discovered = panel(page, "arvamus-teiste")
    choose_organisation(page, "valine-seisukoht")
    discovered.locator("[name=summary]").fill("Toetab varianti B.")
    discovered.get_by_role("button", name="Salvesta arvamus").click()
    history(page).get_by_text("Teiste arvamus:").first.wait_for()

    # One organisation, two records, two headings: the provenance is the
    # difference and nothing else is.
    expect(history(page)).to_contain_text(f"Meile saadetud tagasiside: {MINISTRY}")
    expect(history(page)).to_contain_text(f"Teiste arvamus: {MINISTRY}")

    # The lawyer's reading is its own labelled line, never part of the source's.
    # Written through `Muuda`, which is where the box lives now — so this proves
    # the correction path still asks for it and the row still renders it apart
    # from the position (docs/adr/0091 §4, docs/adr/0095 §3).
    row = history(page).locator("article.uxtl__item").filter(has_text="Teiste arvamus:")
    row.get_by_role("button", name="Muuda").first.click()
    correction = row.locator("form[aria-label='Välise seisukoha parandamine']")
    correction.wait_for(state="visible")
    correction.locator("[name=lawyer_note]").fill("Ei arvesta liikmete kulumõjuga.")
    correction.get_by_role("button", name="Salvesta").click()
    history(page).get_by_text("Juristi märkus").first.wait_for()

    row = history(page).locator("article.uxtl__item").filter(has_text="Teiste arvamus:")
    expect(row.locator(".uxtl__msnote")).to_contain_text("Juristi märkus")
    expect(row.locator(".uxtl__msnote")).to_contain_text("kulumõjuga")
    expect(row.locator(".uxtl__mssub")).not_to_contain_text("kulumõjuga")


def test_a_documents_row_is_under_the_act_it_evidences(page, base_url):
    """Not a chronology row of its own with a dot, a date and an upload time."""
    sign_in(page, base_url, SANDRA)
    a_new_matter(page, base_url)
    open_add_panel(page, "marge-tavaline")

    form = panel(page, "marge-tavaline")
    form.locator("[name=title]").fill("Ministeerium saatis eelnõu uue versiooni")
    form.locator("[name=occurred_on]").fill(_past(2))
    form.locator("input[type=file]").set_input_files(
        {"name": "eelnou-v2.pdf", "mimeType": "application/pdf", "buffer": b"%PDF-1.4 eelnou"}
    )
    form.get_by_role("button", name="Salvesta", exact=True).click()
    page.wait_for_load_state("networkidle")

    row = (
        history(page)
        .locator("article.uxtl__item")
        .filter(has_text="Ministeerium saatis eelnõu uue versiooni")
    )
    expect(row).to_have_count(1)
    expect(row.locator(".uxtl__file")).to_have_text("eelnou-v2.pdf")
    expect(history(page)).not_to_contain_text("Tõendiversioon lisatud")


def test_several_koda_opinions_each_keep_their_own_row(page, base_url):
    """Scenario E. No «final opinion» collapse anywhere on the page."""
    sign_in(page, base_url, SANDRA)
    a_new_matter(page, base_url)
    _record_koda_opinion(page, sent_on=_past(30), filename="arvamus_1.pdf")
    _record_koda_opinion(page, sent_on=_past(4), filename="arvamus_2.pdf")

    rows = history(page).locator("article.uxtl__item").filter(has_text="Arvamus välja")
    expect(rows).to_have_count(2)
    expect(history(page)).to_contain_text("arvamus_1.pdf")
    expect(history(page)).to_contain_text("arvamus_2.pdf")


def test_the_technical_log_is_one_link_away(page, base_url):
    """The primary history is clean *because* this page exists."""
    sign_in(page, base_url, SANDRA)
    a_new_matter(page, base_url)
    _record_development(page, title="Eelnõu jõudis Riigikokku", occurred_on=_past(3))

    page.get_by_role("link", name=re.compile("Kõik muudatused")).click()
    page.wait_for_load_state("networkidle")
    expect(page.locator(".changelog")).to_contain_text("Menetluse areng lisatud")
    expect(page.locator("h1")).to_contain_text("Kõik muudatused")


# ---------------------------------------------------------------------------
# `Menetluse kulg`
# ---------------------------------------------------------------------------


def test_a_domestic_rail_names_its_states_in_words(page, base_url):
    sign_in(page, base_url, SANDRA)
    _matter_with_instrument(page, base_url, "Seadus", stage="Kooskõlastusringil")

    expect(rail(page)).to_contain_text("Menetluse kulg")
    expect(rail(page)).to_contain_text("Riigisisene menetlus")
    for label in ("Algus", "Kooskõlastus", "Valitsus", "Riigikogu", "Jõustumine"):
        expect(rail(page).locator(".lprail__what").filter(has_text=label)).to_have_count(1)
    current = rail(page).locator(".lprail__node--current")
    expect(current).to_have_count(1)
    expect(current).to_contain_text("Kooskõlastus")
    expect(current).to_contain_text("Praegu")
    expect(current).to_have_attribute("aria-current", "step")


def test_an_eu_rail_makes_no_domestic_claim(page, base_url):
    """Scenario C. The European pattern, chosen from `Õigusakt` alone.

    **`Jõustumine` and `Ülevõtmine` are two nodes since docs/adr/0098 §6.** They
    shared one — `Ülevõtmine / jõustumine` — and a reader could not tell an act
    that is in force from a directive somebody still has to transpose. An EU act
    being in force does not mean Estonia has transposed it.
    """
    sign_in(page, base_url, SANDRA)
    _matter_with_instrument(page, base_url, "ELi direktiiv", stage="Eesti seisukoht koostamisel")

    expect(rail(page)).to_contain_text("Direktiivi menetlus")
    expect(rail(page)).to_contain_text("ELi menetlus")
    expect(rail(page)).to_contain_text("Eesti seisukoht")
    expect(rail(page)).to_contain_text("Ülevõtmine")
    expect(rail(page)).to_contain_text("Jõustumine")
    expect(rail(page)).not_to_contain_text("Ülevõtmine / jõustumine")
    expect(rail(page)).not_to_contain_text("Riigikogu")
    expect(rail(page)).not_to_contain_text("Valitsus")


def test_a_late_entry_reads_teadmata_and_never_as_three_completed_steps(page, base_url):
    """Scenario B, the rule this component exists for (docs/adr/0092 §13)."""
    sign_in(page, base_url, SANDRA)
    _matter_with_instrument(page, base_url, "Seadus", stage="Riigikogus")

    states = rail(page).locator(".lprail__node")
    expect(states).to_have_count(5)
    expect(rail(page).locator(".lprail__node--unknown")).to_have_count(3)
    expect(rail(page).locator(".lprail__node--current")).to_contain_text("Riigikogu")
    expect(rail(page).locator(".lprail__node--possible")).to_contain_text("Jõustumine")
    expect(rail(page)).to_contain_text("Teadmata")
    expect(rail(page)).to_contain_text("Võimalik")
    expect(rail(page)).not_to_contain_text("Tehtud")


def test_an_explicitly_recorded_earlier_stage_reads_kirjas(page, base_url):
    sign_in(page, base_url, SANDRA)
    _matter_with_instrument(page, base_url, "Seadus", stage="Kooskõlastusringil")
    _record_development(page, title="Eelnõu jõudis Riigikokku", occurred_on=_past(2))

    open_add_panel(page, "marge-tavaline")
    form = panel(page, "marge-tavaline")
    form.locator("[name=title]").fill("Riigikogu võttis menetlusse")
    form.locator("[name=occurred_on]").fill(_past(1))
    form.locator("[name=stage]").select_option(label="Riigikogus")
    form.get_by_role("button", name="Salvesta", exact=True).click()
    page.wait_for_load_state("networkidle")

    recorded = rail(page).locator(".lprail__node--recorded")
    expect(recorded).to_contain_text("Kooskõlastus")
    expect(recorded).to_contain_text("Kirjas")
    expect(rail(page).locator(".lprail__node--current")).to_contain_text("Riigikogu")


def test_koda_stopping_reads_beside_the_rail_and_not_on_it(page, base_url):
    """Scenario G. `Rohkem ei tegele` is a disposition, never a legal node."""
    sign_in(page, base_url, SANDRA)
    url = _matter_with_instrument(page, base_url, "Seadus", stage="Riigikogus")

    # `Loobuti` is what `+ Lõpeta teema` calls `Disposition.MONITORING_STOPPED`;
    # `Koda ei tegele edasi` is what the rail calls the same value. Two surfaces,
    # one stored answer, and this test is about the second reading the first
    # (app/matters/forms.py `COMPOSER_CLOSURE_CHOICES`, docs/adr/0032).
    open_add_panel(page, "teema-lopeta")
    closing = panel(page, "teema-lopeta")
    closing.get_by_role("button", name="Loobuti", exact=True).click()
    closing.locator("button[type=submit]").click()
    page.wait_for_load_state("networkidle")
    page.goto(url)
    page.wait_for_load_state("networkidle")

    expect(rail(page).locator(".lprail__node--current")).to_contain_text("Riigikogu")
    expect(rail(page).locator(".lprail__aside--koda")).to_have_text("Koda ei tegele edasi")
    # And no node was renamed after the disposition.
    expect(rail(page).locator(".lprail__what").filter(has_text="Koda")).to_have_count(0)


def test_a_matter_with_nothing_to_place_draws_no_rail(page, base_url):
    """Absent, not empty. A heading over six «Teadmata» nodes says nothing.

    **The three blocks of the section are independent since docs/adr/0098 §8.**
    A file read against no procedure draws no nodes and is promised no next
    steps; its `Alustatud` is a date it recorded rather than a claim about a
    procedure, and it goes on reading under `Kirjas olevad kuupäevad`.
    """
    sign_in(page, base_url, SANDRA)
    a_new_matter(page, base_url)

    expect(rail(page).locator(".lprail__node")).to_have_count(0)
    expect(rail(page).locator(".lpahead")).to_have_count(0)
    expect(rail(page)).not_to_contain_text("Ees võib olla")
    expect(rail(page)).to_contain_text("Alustatud")
    expect(page.locator("#ajajoon")).not_to_contain_text("Menetluse kulg")


# ---------------------------------------------------------------------------
# Keyboard, and narrow widths
# ---------------------------------------------------------------------------


def test_the_history_and_the_rail_are_reachable_by_keyboard(page, base_url):
    """Every control in the section is in the tab order and operable.

    The section itself is a `<details>` whose summary is focusable, and the two
    links the section adds — the technical log and a captured file — are ordinary
    anchors. Asserted by focusing them rather than by reading the markup.
    """
    sign_in(page, base_url, SANDRA)
    a_new_matter(page, base_url)
    _record_development(page, title="Eelnõu jõudis Riigikokku", occurred_on=_past(3))

    summary = page.locator("#ajajoon > summary")
    summary.focus()
    expect(summary).to_be_focused()
    page.keyboard.press("Enter")
    expect(page.locator("#ajajoon")).not_to_have_attribute("open", "")
    page.keyboard.press("Enter")
    expect(page.locator("#ajajoon")).to_have_attribute("open", "")

    log = page.get_by_role("link", name=re.compile("Kõik muudatused"))
    log.focus()
    expect(log).to_be_focused()


@pytest.mark.parametrize("width", [420, 375])
def test_nothing_overflows_sideways_at_narrow_widths(page, base_url, width):
    """The rail and the history scroll themselves, never the document.

    A procedure with a step missing is a different procedure, so no node is
    dropped or abbreviated at width — the rail's own container scrolls, exactly
    as the dated strip's already does (TEEMA_TARGET_SPEC §H).
    """
    sign_in(page, base_url, SANDRA)
    _matter_with_instrument(page, base_url, "Seadus", stage="Riigikogus")
    _record_development(
        page,
        title="Ministeerium saatis pika pealkirjaga eelnõu uue versiooni",
        occurred_on=_past(2),
    )

    page.set_viewport_size({"width": width, "height": 900})
    page.wait_for_load_state("networkidle")

    overflow = page.evaluate(
        "() => document.documentElement.scrollWidth - document.documentElement.clientWidth"
    )
    assert overflow <= 1, f"the document scrolls sideways by {overflow}px at {width}px"
    # Every node is still drawn, rather than dropped to fit.
    expect(rail(page).locator(".lprail__node")).to_have_count(5)


# ---------------------------------------------------------------------------
# Phase-grouped history, and the road ahead (docs/adr/0098)
# ---------------------------------------------------------------------------


def _phase_headings(page) -> list[str]:
    """The phase names, as written rather than as `text-transform` renders them.

    `inner_text()` reports the *rendered* casing, and these headings are
    uppercased by the stylesheet — so asserting on it would be asserting on the
    stylesheet. What this suite is about is which phases are drawn, in what order.
    """
    return [
        (text or "").strip()
        for text in history(page).locator("h3.uxtl__phase .uxtl__phasename").all_text_contents()
    ]


def _record_phase_development(
    page, *, title: str, occurred_on: str, phase: str, stage: str | None = None
) -> None:
    """One step, filed under a phase, through the panel a lawyer actually uses."""
    open_add_panel(page, "marge-tavaline")
    form = panel(page, "marge-tavaline")
    form.locator("[name=title]").fill(title)
    form.locator("[name=occurred_on]").fill(occurred_on)
    form.locator("[name=process_phase]").select_option(label=phase)
    if stage is not None:
        form.locator("[name=stage]").select_option(label=stage)
    form.get_by_role("button", name="Salvesta", exact=True).click()
    page.wait_for_load_state("networkidle")
    # **Wait for the row, not for the network.** `networkidle` returns while the
    # HTMX swap of `#teema-vaade` is still being applied, so a plain `assert`
    # immediately afterwards reads the DOM the save is about to replace — and
    # comes back exactly one save behind, forever. `expect` retries; the bare
    # assertions these tests make about ordering do not, so they wait here
    # instead (e2e/conftest.py `open_add_panel`).
    expect(history(page)).to_contain_text(title)


def test_the_history_reads_in_phases_in_the_lawyers_own_words(page, base_url):
    """Scenario A, in a browser, filed the way a lawyer files it.

    Two consultation rounds a year apart are two sections. Grouping by phase key
    would have put the bill's round inside the VTK's, which is the defect that
    makes «which round did we answer» unanswerable on exactly the busy files.
    """
    sign_in(page, base_url, SANDRA)
    _matter_with_instrument(page, base_url, "VTK", stage=None)
    _record_phase_development(
        page, title="VTK saadeti kooskõlastusringile", occurred_on="10.02.2025", phase="VTK"
    )
    _record_phase_development(
        page,
        title="Eelnõu saadeti kooskõlastusringile",
        occurred_on="01.09.2025",
        phase="Kooskõlastusring",
    )
    _record_phase_development(
        page,
        title="Riigikogu võttis seaduse vastu",
        occurred_on="02.04.2026",
        phase="Riigikogus",
        stage="Riigikogus",
    )

    assert _phase_headings(page) == ["Riigikogus", "Kooskõlastusring", "VTK"]
    # Newest phase first, and the current one says so in a word rather than in a
    # colour.
    expect(history(page).locator("h3.uxtl__phase").first).to_contain_text("Praegu")


def test_a_phase_heading_carries_the_day_the_phase_began_and_never_today(page, base_url):
    """The business date of the step that opened it. Never a `created_at`."""
    sign_in(page, base_url, SANDRA)
    _matter_with_instrument(page, base_url, "Seadus", stage=None)
    _record_phase_development(
        page,
        title="Eelnõu saadeti kooskõlastusringile",
        occurred_on="09.01.2026",
        phase="Kooskõlastusring",
    )

    heading = history(page).locator("h3.uxtl__phase").first
    expect(heading).to_contain_text("alates 09.01.2026")
    expect(heading).not_to_contain_text(_estonian(date.today()))


def test_a_late_entry_draws_no_earlier_sections_at_all(page, base_url):
    """Scenario C. Absent, not empty — and certainly not ticked."""
    sign_in(page, base_url, SANDRA)
    _matter_with_instrument(page, base_url, "Seadus", stage=None)
    _record_phase_development(
        page,
        title="Eelnõu jõudis Riigikokku",
        occurred_on=_past(10),
        phase="Riigikogus",
        stage="Riigikogus",
    )

    assert _phase_headings(page) == ["Riigikogus"]
    expect(history(page)).not_to_contain_text("Kooskõlastusring")
    expect(history(page)).not_to_contain_text("Valitsuses")


def test_the_road_ahead_says_it_is_possible_in_words(page, base_url):
    """§10. No percentages, no checkmarks, no dates, and never colour alone."""
    sign_in(page, base_url, SANDRA)
    _matter_with_instrument(page, base_url, "Seadus", stage="Kooskõlastusringil")

    ahead = page.locator(".lpahead")
    expect(ahead).to_be_visible()
    expect(ahead).to_contain_text("Ees võib olla")
    expect(ahead).to_contain_text("Valitsuses")
    # A horizon, not a plan: at most three steps on screen.
    assert ahead.locator(".lpahead__steps").first.locator(".lpahead__step").count() <= 3
    # And nothing in it is dated or scored.
    assert not re.search(r"\d{1,2}\.\d{1,2}\.\d{4}", ahead.inner_text())
    assert "%" not in ahead.inner_text()


def test_a_regulation_is_never_offered_the_riigikogu(page, base_url):
    """§10. A `Määrus` is not adopted by Parliament, so it is not a next step."""
    sign_in(page, base_url, SANDRA)
    _matter_with_instrument(page, base_url, "Määrus", stage="Kooskõlastusringil")

    expect(rail(page)).to_contain_text("Määruse menetlus")
    expect(rail(page)).not_to_contain_text("Riigikogus")
    # `Valitsuses` is offered as a step that may not apply, in words.
    expect(rail(page).locator(".lpahead")).to_contain_text("kui menetlus jätkub")


def test_an_eu_regulation_is_never_offered_a_transposition(page, base_url):
    """Test 6. A regulation applies directly, and the rail may not say otherwise."""
    sign_in(page, base_url, SANDRA)
    _matter_with_instrument(page, base_url, "ELi määrus", stage="ELi menetluses")

    expect(rail(page)).to_contain_text("ELi määruse menetlus")
    expect(rail(page)).not_to_contain_text("Ülevõtmine")


def test_the_dated_points_read_inside_menetluse_kulg_and_not_in_the_history(page, base_url):
    """§4. The strip answers «where is this going», so it moved to that section."""
    sign_in(page, base_url, SANDRA)
    a_new_matter(page, base_url)

    expect(rail(page).locator(".lpdates")).to_contain_text("Kirjas olevad kuupäevad")
    expect(rail(page).locator(".tl-strip")).to_be_visible()
    expect(history(page).locator(".tl-strip")).to_have_count(0)


def test_one_save_updates_the_rail_the_headings_and_the_dates_together(page, base_url):
    """§12. No stale pre-save snapshot, anywhere on the page.

    The panel moves `Hetkeseis` through its own locked row and then re-renders a
    column the request read *before* the POST. A rail built from that says the
    ministry sent a new version and the file is still on the round it just left,
    one line apart.
    """
    sign_in(page, base_url, SANDRA)
    _matter_with_instrument(page, base_url, "Seadus", stage="Kooskõlastusringil")
    _record_phase_development(
        page,
        title="Eelnõu jõudis Riigikokku",
        occurred_on=_past(5),
        phase="Riigikogus",
        stage="Riigikogus",
    )

    # One swap, no reload: the rail, the heading and the sentence all moved.
    expect(rail(page).locator(".lprail__nowvalue")).to_have_text("Riigikogus")
    assert _phase_headings(page)[0] == "Riigikogus"
    expect(history(page).locator("h3.uxtl__phase").first).to_contain_text("Praegu")


def test_an_unplaced_row_stays_visible_and_says_it_is_not_a_defect(page, base_url):
    """Test 13. `Etapiga sidumata` is a heading, never a queue of work."""
    sign_in(page, base_url, SANDRA)
    _matter_with_instrument(page, base_url, "Seadus", stage="Kooskõlastusringil")
    _record_phase_development(
        page,
        title="Eelnõu saadeti kooskõlastusringile",
        occurred_on=_past(30),
        phase="Kooskõlastusring",
    )

    # A bare stage edit, through the header's own inline control: proof that a
    # value was recorded, dated by nothing but this application's clock. It
    # contradicts the open interval — the file left `Kooskõlastusring` and
    # nothing says when — so everything after that step becomes unplaceable.
    def stage_control(page):
        """The header's own `Hetkeseis` disclosure, and only that one.

        Five controls in the band share `.inlineedit__trigger`, so the selector
        has to name the one holding the stage select — and the assertion has to
        read the *trigger* rather than the disclosure, because the disclosure
        holds the whole `<option>` list and «contains Riigikogus» is true of it
        before the save as well as after.
        """
        return page.locator('#teema-pais details:has(select[aria-label="Hetkeseis"])')

    control = stage_control(page)
    control.locator(".inlineedit__trigger").click()
    control.locator('select[aria-label="Hetkeseis"]').select_option(label="Riigikogus")
    control.get_by_role("button", name="Salvesta hetkeseisu muudatus").click()
    page.wait_for_load_state("networkidle")
    expect(stage_control(page).locator(".inlineedit__trigger")).to_contain_text("Riigikogus")
    # An opinion sent afterwards: it could belong to either phase, so it belongs
    # to neither until the step that moved the file is written down.
    _record_koda_opinion(page, sent_on=_past(2), filename="arvamus.pdf")

    # `expect`, because it retries: `networkidle` returns while the swap of
    # `#teema-vaade` is still being applied.
    expect(history(page).locator(".uxtl__phase--unplaced")).to_have_count(1)
    assert "Etapiga sidumata" in _phase_headings(page)
    note = history(page).locator(".uxtl__phasenote").last
    expect(note).to_contain_text("Etapp selgub")
    # Not red, not a count, not an icon.
    expect(history(page).locator(".uxtl__phase--unplaced")).not_to_contain_text("!")


def test_the_phase_control_is_beside_the_date_box_and_optional(page, base_url):
    """§7. A proposal on screen, never an invisible guess."""
    sign_in(page, base_url, SANDRA)
    _matter_with_instrument(page, base_url, "Seadus", stage="Kooskõlastusringil")
    open_add_panel(page, "marge-tavaline")

    form = panel(page, "marge-tavaline")
    select = form.locator("[name=process_phase]")
    expect(select).to_be_visible()
    expect(form.locator("label[for$=process_phase]")).to_contain_text("valikuline")
    # Pre-selected on the phase the file's own `Hetkeseis` places it on …
    assert select.input_value() == "kooskolastus"
    # … and clearable, in one click, to an ordinary answer.
    select.select_option(value="")
    assert select.input_value() == ""


def test_the_phase_headings_are_reachable_by_keyboard(page, base_url):
    """§14. Headings are real headings, under the section's own `h2`."""
    sign_in(page, base_url, SANDRA)
    _matter_with_instrument(page, base_url, "Seadus", stage=None)
    _record_phase_development(
        page,
        title="Eelnõu saadeti kooskõlastusringile",
        occurred_on=_past(20),
        phase="Kooskõlastusring",
    )

    heading = history(page).locator("h3.uxtl__phase").first
    expect(heading).to_be_visible()
    assert heading.evaluate("el => el.tagName") == "H3"
    # And its id is stable, so a phase can be linked to.
    assert heading.get_attribute("id").startswith("etapp-")
    # No duplicate ids anywhere on the page this section added to.
    duplicates = page.evaluate(
        "() => { const seen = {}; const dupes = [];"
        " for (const el of document.querySelectorAll('[id]')) {"
        "  if (seen[el.id]) { dupes.push(el.id); } seen[el.id] = true; }"
        " return dupes; }"
    )
    assert duplicates == []


@pytest.mark.parametrize("width", [375, 420, 768, 1440])
def test_the_grouped_history_never_overflows_sideways(page, base_url, width):
    """§14. The rail scrolls itself; the document never scrolls sideways."""
    sign_in(page, base_url, SANDRA)
    _matter_with_instrument(page, base_url, "VTK", stage="Kooskõlastusringil")
    _record_phase_development(
        page, title="VTK saadeti kooskõlastusringile", occurred_on="10.02.2025", phase="VTK"
    )
    _record_phase_development(
        page,
        title="Eelnõu saadeti kooskõlastusringile",
        occurred_on="01.09.2025",
        phase="Kooskõlastusring",
    )
    page.set_viewport_size({"width": width, "height": 900})
    page.wait_for_timeout(120)

    overflow = page.evaluate(
        "() => document.documentElement.scrollWidth - document.documentElement.clientWidth"
    )
    assert overflow <= 0, f"the document scrolls sideways by {overflow}px at {width}px"
    # The history reads vertically: every heading starts at the same x.
    lefts = page.evaluate(
        "() => [...document.querySelectorAll('#ajalugu-loend h3.uxtl__phase')]"
        ".map(el => Math.round(el.getBoundingClientRect().left))"
    )
    assert len(set(lefts)) <= 1, lefts


def test_an_older_phase_collapses_and_reopens_from_the_keyboard(page, base_url):
    """§6 and §14. A three-year file's older rounds get out of the way.

    The control is a real button in the tab order, `aria-expanded` says the
    state, and the heading stays at full legibility when the section is shut —
    what collapses is the content, not the answer to «which phase is this».
    """
    sign_in(page, base_url, SANDRA)
    _matter_with_instrument(page, base_url, "VTK", stage=None)
    _record_phase_development(
        page, title="VTK saadeti kooskõlastusringile", occurred_on="10.02.2025", phase="VTK"
    )
    _record_phase_development(
        page,
        title="Eelnõu saadeti kooskõlastusringile",
        occurred_on="01.09.2025",
        phase="Kooskõlastusring",
    )

    older = history(page).locator("h3.uxtl__phase").filter(has_text="VTK")
    toggle = older.locator(".uxtl__phasetoggle")
    row = history(page).locator("article.uxtl__item").filter(has_text="VTK saadeti")

    # The server sent it open, and the script showed a control that works.
    expect(toggle).to_be_visible()
    expect(toggle).to_have_attribute("aria-expanded", "true")
    expect(row).to_be_visible()

    # Shut from the keyboard: focus it and press Enter.
    toggle.focus()
    page.keyboard.press("Enter")
    expect(toggle).to_have_attribute("aria-expanded", "false")
    expect(row).to_be_hidden()
    # The heading itself stays — a closed section still says which phase it is.
    expect(older).to_be_visible()
    expect(older).to_contain_text("VTK")
    # And the phase above it is untouched.
    expect(
        history(page).locator("article.uxtl__item").filter(has_text="Eelnõu saadeti")
    ).to_be_visible()

    page.keyboard.press("Enter")
    expect(toggle).to_have_attribute("aria-expanded", "true")
    expect(row).to_be_visible()


def test_every_phase_reads_with_no_script_at_all(page, base_url, browser):
    """The history is complete before `ux.js` runs, and the control admits it.

    A button that did nothing without JavaScript would be worse than no button,
    so the server sends it `hidden` and the script shows it. What the server
    sends is every row, expanded.
    """
    sign_in(page, base_url, SANDRA)
    url = _matter_with_instrument(page, base_url, "VTK", stage=None)
    _record_phase_development(
        page, title="VTK saadeti kooskõlastusringile", occurred_on="10.02.2025", phase="VTK"
    )

    context = browser.new_context(java_script_enabled=False)
    try:
        quiet = context.new_page()
        # The dev sign-in is an ordinary form post, so it works without script.
        quiet.goto(f"{base_url}/konto/arendus-sisselogimine/")
        quiet.get_by_label(SANDRA.display_name, exact=False).check()
        quiet.get_by_role("button", name="Logi sisse").click()
        quiet.goto(url)

        assert quiet.locator("#ajalugu-loend article.uxtl__item").count() >= 1
        expect(
            quiet.locator("#ajalugu-loend article.uxtl__item").filter(has_text="VTK saadeti")
        ).to_be_visible()
        expect(quiet.locator("#ajalugu-loend h3.uxtl__phase").first).to_be_visible()
        # The control is there in the markup and deliberately not shown.
        expect(quiet.locator(".uxtl__phasetoggle").first).to_be_hidden()
    finally:
        context.close()
