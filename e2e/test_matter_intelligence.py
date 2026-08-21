"""Structured Matter facts, in a browser.

What a milestone, a commencement or a work victory *means* is proved against the
database in `tests/test_intelligence_*`. What only a browser shows is whether a
lawyer can capture one in seconds, whether the precision control actually
narrows to the fields that answer needs, whether an approximate period survives
the round trip to the screen, and whether the generated department pages really
are generated rather than typed.

Deliberately few, like the rest of the suite: these exist for the failures that
live between layers (docs/adr/0010).

Every locator that could match twice is scoped to its section. The Matter page
now carries three lists with the same control names, and an unscoped
`Muuda` would be Playwright telling us so in the least useful way.
"""

from __future__ import annotations

import pytest
from playwright.sync_api import expect

from app.core.management.commands.seed_e2e_data import OPEN_TITLE, RESTRICTED_TITLE
from e2e.conftest import ADMIN, HEAD, MARTIN, SANDRA, sign_in

pytestmark = pytest.mark.e2e

#: Added by the tests below rather than by the seed, so each one owns the record
#: it changes and the file's tests do not depend on each other's order.
HEAD_CANDIDATE = "Osakonnajuhi kinnitatav kandidaat"
CORRECTED = "Parandatud sõnastusega tähtaeg"


def open_the_matter(page, base_url: str, title: str) -> None:
    page.goto(f"{base_url}/teemad/")
    page.get_by_role("link", name=title).first.click()
    page.wait_for_load_state("networkidle")


def section(page, name: str):
    """One of the three fact sections, by its heading."""
    return page.get_by_role("region", name=name)


def open_watchlist(page, base_url: str, path: str = "olulised-tahtajad", query: str = "") -> None:
    page.goto(f"{base_url}/{path}/{query}")
    page.wait_for_load_state("networkidle")


# -- the Matter page --------------------------------------------------------


def test_the_three_sections_are_on_the_matter_page(page, base_url, screenshots):
    sign_in(page, base_url, MARTIN)
    open_the_matter(page, base_url, OPEN_TITLE)

    expect(page.get_by_role("heading", name="Olulised tähtajad")).to_be_visible()
    expect(page.get_by_role("heading", name="Jõustumine", exact=True)).to_be_visible()
    expect(page.get_by_role("heading", name="Töövõidud", exact=True)).to_be_visible()

    screenshots(page, "teema-struktuursed-faktid")


def test_an_exact_milestone_can_be_added_in_a_few_fields(page, base_url):
    sign_in(page, base_url, MARTIN)
    open_the_matter(page, base_url, OPEN_TITLE)

    section(page, "Olulised tähtajad").get_by_role("link", name="+ Lisa oluline tähtaeg").click()
    page.wait_for_load_state("networkidle")
    page.get_by_label("Mis on oodata").fill("Riigikogu esimene lugemine")
    page.get_by_label("Täpne kuupäev").check()
    page.get_by_label("Kuupäev", exact=True).fill("2030-03-14")
    page.get_by_role("button", name="Salvesta").click()
    page.wait_for_load_state("networkidle")

    watched = section(page, "Olulised tähtajad")
    expect(watched.get_by_text("Riigikogu esimene lugemine")).to_be_visible()
    expect(watched.get_by_text("14.03.2030")).to_be_visible()


def test_a_quarter_is_captured_and_rendered_as_a_quarter(page, base_url, screenshots):
    """The property the whole precision vocabulary exists for.

    Choosing *Kvartali täpsusega* must not produce 01.04.2030 anywhere on the
    page. The stored anchor is how the database sorts, never something anybody
    committed to (master specification 3.5).
    """
    sign_in(page, base_url, MARTIN)
    open_the_matter(page, base_url, OPEN_TITLE)

    section(page, "Olulised tähtajad").get_by_role("link", name="+ Lisa oluline tähtaeg").click()
    page.wait_for_load_state("networkidle")
    page.get_by_label("Mis on oodata").fill("Eeldatav rakendusakti eelnõu")
    page.get_by_label("Kvartali täpsusega").check()

    # The control narrows to what that answer needs. Without scripting every
    # group is visible and the form still works; with it, the exact-date box is
    # out of the way rather than inviting a day nobody named.
    expect(page.get_by_label("Kuupäev", exact=True)).to_be_hidden()
    screenshots(page, "tapsuse-valik-kvartal")

    page.get_by_label("Kvartal", exact=True).select_option("2")
    page.get_by_label("Aasta", exact=True).fill("2030")
    page.get_by_role("button", name="Salvesta").click()
    page.wait_for_load_state("networkidle")

    watched = section(page, "Olulised tähtajad")
    expect(watched.get_by_text("II kvartal 2030")).to_be_visible()
    expect(page.get_by_text("01.04.2030")).to_have_count(0)


def test_a_milestone_can_be_corrected(page, base_url):
    """Adds its own milestone first, so no other test's records move."""
    sign_in(page, base_url, MARTIN)
    open_the_matter(page, base_url, OPEN_TITLE)

    section(page, "Olulised tähtajad").get_by_role("link", name="+ Lisa oluline tähtaeg").click()
    page.wait_for_load_state("networkidle")
    page.get_by_label("Mis on oodata").fill("Esialgse sõnastusega tähtaeg")
    page.get_by_label("Täpne kuupäev").check()
    page.get_by_label("Kuupäev", exact=True).fill("2033-05-05")
    page.get_by_role("button", name="Salvesta").click()
    page.wait_for_load_state("networkidle")

    row = (
        section(page, "Olulised tähtajad")
        .get_by_role("listitem")
        .filter(has_text="Esialgse sõnastusega tähtaeg")
    )
    row.get_by_role("link", name="Muuda").click()
    page.wait_for_load_state("networkidle")
    page.get_by_label("Mis on oodata").fill(CORRECTED)
    page.get_by_role("button", name="Salvesta").click()
    page.wait_for_load_state("networkidle")

    watched = section(page, "Olulised tähtajad")
    expect(watched.get_by_text(CORRECTED)).to_be_visible()
    expect(watched.get_by_text("Esialgse sõnastusega tähtaeg")).to_have_count(0)


def test_a_second_commencement_can_be_added_to_the_same_matter(page, base_url, screenshots):
    """One law, several dates. This is the model's reason to exist."""
    sign_in(page, base_url, MARTIN)
    open_the_matter(page, base_url, OPEN_TITLE)

    section(page, "Jõustumine").get_by_role("link", name="+ Lisa jõustumine").click()
    page.wait_for_load_state("networkidle")
    page.get_by_label("Mis jõustub").fill("hilisemad sätted")
    page.get_by_label("Teadaolev kuupäev").check()
    page.get_by_label("Kuupäev", exact=True).fill("2032-01-01")
    page.get_by_role("button", name="Salvesta").click()
    page.wait_for_load_state("networkidle")

    commencements = section(page, "Jõustumine")
    expect(commencements.get_by_text("hilisemad sätted")).to_be_visible()
    expect(commencements.get_by_text("põhiosa")).to_be_visible()
    expect(commencements.get_by_text("osad sätted")).to_be_visible()
    screenshots(page, "teema-mitu-joustumist")


def test_an_unknown_commencement_reads_as_a_statement_not_a_gap(page, base_url):
    sign_in(page, base_url, MARTIN)
    open_the_matter(page, base_url, OPEN_TITLE)

    expect(section(page, "Jõustumine").get_by_text("Jõustub üldises korras").first).to_be_visible()


def test_a_general_order_form_hides_the_date_control(page, base_url):
    sign_in(page, base_url, MARTIN)
    open_the_matter(page, base_url, OPEN_TITLE)

    section(page, "Jõustumine").get_by_role("link", name="+ Lisa jõustumine").click()
    page.wait_for_load_state("networkidle")
    page.get_by_label("Jõustub üldises korras").check()

    expect(page.get_by_label("Kuupäev", exact=True)).to_be_hidden()

    page.get_by_label("Mis jõustub").fill("teine rakendusmäärus")
    page.get_by_role("button", name="Salvesta").click()
    page.wait_for_load_state("networkidle")
    expect(section(page, "Jõustumine").get_by_text("teine rakendusmäärus")).to_be_visible()


# -- work victories ---------------------------------------------------------


def test_a_new_record_is_saved_as_a_candidate(page, base_url, screenshots):
    sign_in(page, base_url, MARTIN)
    open_the_matter(page, base_url, OPEN_TITLE)

    section(page, "Töövõidud").get_by_role("link", name="+ Lisa töövõidu kandidaat").click()
    page.wait_for_load_state("networkidle")
    page.get_by_label("Töövõit", exact=True).fill("Erisus jäi eelnõusse sisse")
    page.get_by_label("Aasta täpsusega").check()
    page.get_by_label("Aasta", exact=True).fill("2030")
    page.get_by_role("button", name="Salvesta kandidaadina").click()
    page.wait_for_load_state("networkidle")

    row = (
        section(page, "Töövõidud")
        .get_by_role("listitem")
        .filter(has_text="Erisus jäi eelnõusse sisse")
    )
    expect(row.get_by_text("Töövõidu kandidaat")).to_be_visible()
    expect(row.get_by_text("2030")).to_be_visible()
    screenshots(page, "teema-toovoidu-kandidaat")


def test_a_specialist_has_no_confirmation_control(page, base_url):
    sign_in(page, base_url, MARTIN)
    open_the_matter(page, base_url, OPEN_TITLE)

    expect(page.get_by_role("link", name="Kinnita töövõiduks")).to_have_count(0)


def test_the_department_head_confirms_a_candidate(page, base_url, screenshots):
    """Its own candidate, so this test owns the record it changes."""
    sign_in(page, base_url, HEAD)
    open_the_matter(page, base_url, OPEN_TITLE)

    section(page, "Töövõidud").get_by_role("link", name="+ Lisa töövõidu kandidaat").click()
    page.wait_for_load_state("networkidle")
    page.get_by_label("Töövõit", exact=True).fill(HEAD_CANDIDATE)
    # An explicit answer either way. The form will not save a period nobody
    # stated, and it will not quietly record one as unknown either
    # (Stage-2G brief 21, 22).
    page.get_by_label("Teadmata periood").check()
    page.get_by_role("button", name="Salvesta kandidaadina").click()
    page.wait_for_load_state("networkidle")

    row = section(page, "Töövõidud").get_by_role("listitem").filter(has_text=HEAD_CANDIDATE)
    row.get_by_role("link", name="Kinnita töövõiduks").click()
    page.wait_for_load_state("networkidle")

    expect(page.get_by_role("heading", name="Kinnita töövõiduks")).to_be_visible()
    expect(page.get_by_text(HEAD_CANDIDATE)).to_be_visible()
    page.get_by_role("button", name="Kinnita töövõiduks").click()
    page.wait_for_load_state("networkidle")

    confirmed = section(page, "Töövõidud").get_by_role("listitem").filter(has_text=HEAD_CANDIDATE)
    expect(confirmed.get_by_text("Kinnitatud töövõit")).to_be_visible()
    screenshots(page, "teema-kinnitatud-toovoit")


# -- the generated department pages -----------------------------------------


def test_jalgimine_is_one_navigation_item_with_three_views(page, base_url, screenshots):
    sign_in(page, base_url, MARTIN)
    page.get_by_role("link", name="Jälgimine", exact=True).click()
    page.wait_for_url(f"{base_url}/olulised-tahtajad/")

    tabs = page.get_by_label("Jälgimise vaated")
    for label in ("Olulised tähtajad", "Jõustuvad aktid", "Töövõidud"):
        expect(tabs.get_by_role("link", name=label, exact=True)).to_be_visible()

    screenshots(page, "jalgimine-olulised-tahtajad")


def test_the_calendar_shows_both_event_kinds_and_says_which_is_which(page, base_url):
    """One source of truth, two labelled presentations (Stage-2G brief 47)."""
    sign_in(page, base_url, MARTIN)
    open_watchlist(page, base_url, query="?suund=koik")

    watchlist = page.get_by_role("region", name="Olulised tähtajad")
    expect(watchlist.get_by_text("Jõustumine").first).to_be_visible()
    expect(watchlist.get_by_text("Tähtaeg").first).to_be_visible()
    expect(watchlist.get_by_text("Eeldatav VTK avalikustamine")).to_be_visible()


def test_an_approximate_period_keeps_its_precision_on_the_department_page(page, base_url):
    sign_in(page, base_url, MARTIN)
    open_watchlist(page, base_url, query="?suund=koik")

    # The seeded quarter-precision milestone. Its heading is the quarter, not a
    # month somebody's anchor date happened to fall in.
    expect(page.get_by_text("kvartal").first).to_be_visible()


def test_the_source_selector_narrows_the_calendar(page, base_url):
    sign_in(page, base_url, MARTIN)
    open_watchlist(page, base_url, query="?suund=koik&allikad=joustumised")

    watchlist = page.get_by_role("region", name="Olulised tähtajad")
    expect(watchlist.get_by_text("Eeldatav VTK avalikustamine")).to_have_count(0)
    expect(watchlist.get_by_text("Jõustumine").first).to_be_visible()


def test_a_calendar_row_opens_its_matter(page, base_url):
    sign_in(page, base_url, MARTIN)
    open_watchlist(page, base_url, query="?suund=koik")
    page.get_by_role("link", name=OPEN_TITLE).first.click()
    page.wait_for_load_state("networkidle")

    expect(page.get_by_role("heading", name=OPEN_TITLE)).to_be_visible()


def test_the_commencement_page_is_grouped_and_the_undated_are_apart(page, base_url, screenshots):
    sign_in(page, base_url, MARTIN)
    open_watchlist(page, base_url, "joustuvad-aktid", "?suund=koik")

    expect(page.get_by_role("heading", name="Jõustuvad aktid")).to_be_visible()
    expect(page.get_by_text("põhiosa")).to_be_visible()
    screenshots(page, "jalgimine-joustuvad-aktid")

    page.get_by_role("link", name="Kuupäev täpsustamisel", exact=True).click()
    page.wait_for_load_state("networkidle")
    expect(page.get_by_text("Jõustub üldises korras").first).to_be_visible()
    # An undated commencement has no place on a chronological axis, so the
    # dated rows are not mixed into this view.
    expect(page.get_by_text("põhiosa")).to_have_count(0)


def test_the_work_victory_page_filters_by_state(page, base_url, screenshots):
    sign_in(page, base_url, MARTIN)
    open_watchlist(page, base_url, "toovoidud")

    expect(page.get_by_role("heading", name="Töövõidud")).to_be_visible()
    expect(
        page.get_by_text("Koja ettepanek rakendusaja pikendamiseks võeti arvesse")
    ).to_be_visible()
    screenshots(page, "jalgimine-toovoidud")

    page.get_by_role("link", name="Ei realiseerunud", exact=True).click()
    page.wait_for_load_state("networkidle")
    expect(
        page.get_by_text("Koja ettepanek rakendusaja pikendamiseks võeti arvesse")
    ).to_have_count(0)


# -- authorization ----------------------------------------------------------


@pytest.mark.parametrize("path", ["olulised-tahtajad", "joustuvad-aktid", "toovoidud"])
def test_a_restricted_matters_facts_never_reach_the_department_pages(page, base_url, path):
    """Not the row, not the title, not the Matter behind it.

    Martin has no relationship to Sandra's restricted Matter, so nothing about
    it may appear on a page built by combining everybody's records
    (Stage-2G brief 31).
    """
    sign_in(page, base_url, MARTIN)
    open_watchlist(page, base_url, path, "?suund=koik")

    expect(page.get_by_text("Konfidentsiaalne")).to_have_count(0)
    expect(page.get_by_text(RESTRICTED_TITLE)).to_have_count(0)


def test_the_owner_does_see_her_own_restricted_facts(page, base_url):
    sign_in(page, base_url, SANDRA)
    open_watchlist(page, base_url, query="?suund=koik")

    expect(page.get_by_text("Konfidentsiaalne tähtaeg")).to_be_visible()


def test_a_technical_administrator_sees_no_restricted_facts(page, base_url):
    """Technical administration is not business access (specification 5.2)."""
    sign_in(page, base_url, ADMIN)
    open_watchlist(page, base_url, query="?suund=koik")

    expect(page.get_by_text("Konfidentsiaalne tähtaeg")).to_have_count(0)


def test_an_administrator_has_no_write_controls(page, base_url):
    sign_in(page, base_url, ADMIN)
    open_the_matter(page, base_url, OPEN_TITLE)

    expect(page.get_by_role("link", name="+ Lisa oluline tähtaeg")).to_have_count(0)
    expect(page.get_by_role("link", name="+ Lisa töövõidu kandidaat")).to_have_count(0)


def test_the_department_head_sees_restricted_facts_by_role(page, base_url):
    sign_in(page, base_url, HEAD)
    open_watchlist(page, base_url, query="?suund=koik")

    expect(page.get_by_text("Konfidentsiaalne tähtaeg")).to_be_visible()
