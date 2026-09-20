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
    form.get_by_role("button", name="Salvesta areng").click()
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
    form.get_by_role("button", name="Salvesta areng").click()
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
    """A month is written down as a month. The anchor never reaches a screen."""
    sign_in(page, base_url, SANDRA)
    a_new_matter(page, base_url)
    open_add_panel(page, "marge-tavaline")

    form = panel(page, "marge-tavaline")
    form.locator("[name=title]").fill("Valitsus kiitis eelnõu heaks")
    # The shared `Täpsus` control, under `Menetluse areng`'s own POST prefix
    # (`DEVELOPMENT_PREFIX`). The day box keeps the name `occurred_on`; the
    # period selects carry the prefix, which is what lets several of these forms
    # sit on one page without one POST key meaning two dates.
    #
    # The **label** is clicked, not the radio: the input is visually clipped and
    # the chip label sits over it, so `check()` on the control is intercepted by
    # the very thing a person actually presses. `e2e/test_date_precision.py`
    # chooses a precision exactly this way.
    form.locator("label.precision__chip", has_text="Kuu").first.click()
    form.locator("[name=areng_month]").select_option(label="Märts")
    form.locator("[name=areng_year]").fill("2026")
    form.get_by_role("button", name="Salvesta areng").click()
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
    form.get_by_role("button", name="Salvesta areng").click()
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
    """Scenario C. The European template, chosen from `Õigusakt` alone."""
    sign_in(page, base_url, SANDRA)
    _matter_with_instrument(page, base_url, "ELi direktiiv", stage="Eesti seisukoht koostamisel")

    expect(rail(page)).to_contain_text("ELi menetlus")
    expect(rail(page)).to_contain_text("Eesti seisukoht")
    expect(rail(page)).to_contain_text("Ülevõtmine / jõustumine")
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
    form.get_by_role("button", name="Salvesta areng").click()
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
    """Absent, not empty. A heading over five «Teadmata» says nothing."""
    sign_in(page, base_url, SANDRA)
    a_new_matter(page, base_url)

    expect(rail(page)).to_have_count(0)
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
