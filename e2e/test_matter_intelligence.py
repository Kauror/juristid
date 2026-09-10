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

from app.core.management.commands.seed_e2e_data import (
    MACHINE_CANDIDATE,
    OPEN_TITLE,
    RESTRICTED_TITLE,
)
from e2e.conftest import (
    ADMIN,
    HEAD,
    MARTIN,
    READER,
    SANDRA,
    create_matter,
    go_to,
    sign_in,
)

pytestmark = pytest.mark.e2e

#: Added by the tests below rather than by the seed, so each one owns the record
#: it changes and the file's tests do not depend on each other's order.
CORRECTED = "Parandatud sõnastusega tähtaeg"


def open_the_matter(page, base_url: str, title: str) -> None:
    page.goto(f"{base_url}/teemad/")
    page.get_by_role("link", name=title).first.click()
    page.wait_for_load_state("networkidle")


def section(page, name: str):
    """One of the three fact sections, by its heading."""
    return page.get_by_role("region", name=name)


def add_form(page):
    """The add form the Matter page opens under the section it writes into.

    Every interaction with it is scoped through here, and that is not tidiness.
    The Matter page carries a composer with a period control of its own — its
    own «Kuupäev», «Kvartal», «Aasta» and «Täpsus» — so an unscoped
    `get_by_label("Kuupäev")` matches two controls and Playwright refuses to
    act on either (docs/adr/0065).
    """
    return page.locator(".factslot")


#: The two controls the 2026-09 refinement moved into the composer's action row
#: — one place from which a fact is added. Same routes, same target, same
#: conditions; what changed is that reaching them opens the composer first
#: (docs/matter-page-refinement.md).
COMPOSER_CHIPS = ("+ Jõustumine", "+ Töövõit")


def open_add_form(page, label: str):
    """Click one of the four add controls and wait for its form to arrive.

    No `wait_for_load_state`: nothing navigates. The assertion that the form is
    on screen is both the wait and half of what these tests are checking.

    Two of the four now live in the composer, which is closed on a page that is
    read rather than written to. Opening it is not part of what these tests are
    about, so it happens here rather than in nine of them.
    """
    if label in COMPOSER_CHIPS:
        composer = page.locator("#teema-koostaja")
        if composer.get_attribute("open") is None:
            composer.locator("summary.uxcomp__collapsed").click()
    page.get_by_role("link", name=label, exact=True).click()
    form = add_form(page)
    expect(form).to_be_visible()
    return form


def expect_chip(page, label: str):
    """A composer chip is present, with the composer opened to look at it."""
    composer = page.locator("#teema-koostaja")
    if composer.get_attribute("open") is None:
        composer.locator("summary.uxcomp__collapsed").click()
    return expect(page.get_by_role("link", name=label, exact=True))


def open_register(page, base_url: str, query: str = "") -> None:
    """Teemad, narrowed by the structured facts this file is about.

    The three generated department pages this used to open are retired: a
    Teema is found in the register now, and `Töövõit` and `Jõustumine` narrow
    it like any other dimension (docs/adr/0071).
    """
    page.goto(f"{base_url}/teemad/{query}")
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

    section(page, "Olulised tähtajad").get_by_role("link", name="+ Lisa tähtaeg").click()
    page.wait_for_load_state("networkidle")
    page.get_by_label("Mis on oodata").fill("Riigikogu esimene lugemine")
    page.get_by_label("Täpne kuupäev").check()
    page.get_by_label("Kuupäev", exact=True).fill("14.3.2030")
    page.get_by_role("button", name="Salvesta").click()
    page.wait_for_load_state("networkidle")

    watched = section(page, "Olulised tähtajad")
    expect(watched.get_by_text("Riigikogu esimene lugemine")).to_be_visible()
    expect(watched.get_by_text("14.3.2030")).to_be_visible()


def test_a_quarter_is_captured_and_rendered_as_a_quarter(page, base_url, screenshots):
    """The property the whole precision vocabulary exists for.

    Choosing *Kvartali täpsusega* must not produce 01.04.2030 anywhere on the
    page. The stored anchor is how the database sorts, never something anybody
    committed to (master specification 3.5).
    """
    sign_in(page, base_url, MARTIN)
    open_the_matter(page, base_url, OPEN_TITLE)

    section(page, "Olulised tähtajad").get_by_role("link", name="+ Lisa tähtaeg").click()
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

    section(page, "Olulised tähtajad").get_by_role("link", name="+ Lisa tähtaeg").click()
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
    """One law, several dates. This is the model's reason to exist.

    Added without leaving the Matter: `+ Lisa jõustumine` on a populated
    section opens the form under the list it joins, and the save comes back as
    that same list with the new row in it (docs/adr/0065).
    """
    sign_in(page, base_url, MARTIN)
    open_the_matter(page, base_url, OPEN_TITLE)
    where = page.url

    form = open_add_form(page, "+ Lisa jõustumine")
    form.get_by_label("Mis jõustub").fill("hilisemad sätted")
    form.get_by_label("Teadaolev kuupäev").check()
    form.get_by_label("Kuupäev", exact=True).fill("2032-01-01")
    form.get_by_role("button", name="Salvesta").click()

    commencements = section(page, "Jõustumine")
    expect(commencements.get_by_text("hilisemad sätted")).to_be_visible()
    expect(commencements.get_by_text("põhiosa")).to_be_visible()
    expect(commencements.get_by_text("osad sätted")).to_be_visible()
    # Never left, and the form closed behind the save.
    assert page.url == where
    expect(add_form(page)).to_have_count(0)
    screenshots(page, "teema-mitu-joustumist")


def test_an_unknown_commencement_reads_as_a_statement_not_a_gap(page, base_url):
    sign_in(page, base_url, MARTIN)
    open_the_matter(page, base_url, OPEN_TITLE)

    expect(section(page, "Jõustumine").get_by_text("Jõustub üldises korras").first).to_be_visible()


def test_a_general_order_form_hides_the_date_control(page, base_url):
    """The conditional behaviour still runs on markup HTMX put on the page.

    `bindPeriodFields` is re-run on `htmx:afterSwap`, so the control narrows
    inside the accordion exactly as it did on the standalone form — and it
    narrows *this* form's fields rather than the composer's, which shares the
    class and sits a few hundred pixels above it (static/js/app.js).
    """
    sign_in(page, base_url, MARTIN)
    open_the_matter(page, base_url, OPEN_TITLE)

    form = open_add_form(page, "+ Lisa jõustumine")
    form.get_by_label("Jõustub üldises korras").check()

    expect(form.get_by_label("Kuupäev", exact=True)).to_be_hidden()

    form.get_by_label("Mis jõustub").fill("teine rakendusmäärus")
    form.get_by_role("button", name="Salvesta").click()
    expect(section(page, "Jõustumine").get_by_text("teine rakendusmäärus")).to_be_visible()


# -- work victories ---------------------------------------------------------


def test_a_person_adding_a_victory_gets_a_confirmed_one(page, base_url, screenshots):
    """One click, and the row is what the person said it was.

    Not a candidate awaiting somebody's agreement: a colleague who may write on
    this Matter has already made that judgement, and the second step asked them
    to seek approval for a decision they had just taken.
    """
    sign_in(page, base_url, MARTIN)
    open_the_matter(page, base_url, OPEN_TITLE)

    form = open_add_form(page, "+ Lisa töövõit")
    expect(form.get_by_role("heading", name="Lisa töövõit")).to_be_visible()
    # The form talks about a Töövõit, never about confirming or proposing one.
    expect(form.locator(".cardnote")).to_have_text("Kirje lisatakse töövõiduna sinu nimel.")

    form.get_by_label("Töövõit", exact=True).fill("Erisus jäi eelnõusse sisse")
    # `exact=True`, because "Poolaasta täpsusega" contains "Aasta täpsusega".
    form.get_by_label("Aasta täpsusega", exact=True).check()
    form.get_by_label("Aasta", exact=True).fill("2030")
    form.get_by_role("button", name="Salvesta töövõit").click()

    row = (
        section(page, "Töövõidud")
        .get_by_role("listitem")
        .filter(has_text="Erisus jäi eelnõusse sisse")
    )
    expect(row.get_by_text("2030")).to_be_visible()
    # No state chip: the section already says Töövõidud, and this row is one.
    expect(row.get_by_text("Kinnitatud töövõit")).to_have_count(0)
    expect(row.locator(".victorystate")).to_have_count(0)
    # And no second click was available to make, because there is nothing left
    # to decide about this row.
    expect(row.get_by_role("link", name="Kinnita töövõiduks")).to_have_count(0)
    screenshots(page, "teema-toovoit")


def test_a_specialist_has_no_confirmation_control(page, base_url):
    """Adding is theirs. Adjudicating a proposal somebody else made is not."""
    sign_in(page, base_url, MARTIN)
    open_the_matter(page, base_url, OPEN_TITLE)

    expect(page.get_by_role("link", name="Kinnita töövõiduks")).to_have_count(0)


def test_the_department_head_confirms_a_proposed_candidate(page, base_url, screenshots):
    """The review path, on the kind of row that still arrives as a candidate.

    Seeded rather than created here: the manual form no longer produces a
    candidate, so the only honest source of one is a machine or an import.
    """
    sign_in(page, base_url, HEAD)

    open_the_matter(page, base_url, OPEN_TITLE)

    row = section(page, "Töövõidud").get_by_role("listitem").filter(has_text=MACHINE_CANDIDATE)
    expect(row.get_by_text("Töövõidu kandidaat")).to_be_visible()
    row.get_by_role("link", name="Kinnita töövõiduks").click()
    page.wait_for_load_state("networkidle")

    expect(page.get_by_role("heading", name="Kinnita töövõiduks")).to_be_visible()
    expect(page.get_by_text(MACHINE_CANDIDATE)).to_be_visible()
    page.get_by_role("button", name="Kinnita töövõiduks").click()
    page.wait_for_load_state("networkidle")

    # Reviewed, so it stops being a proposal and becomes an ordinary Töövõit:
    # the chip and the control that acted on it both go.
    confirmed = (
        section(page, "Töövõidud").get_by_role("listitem").filter(has_text=MACHINE_CANDIDATE)
    )
    expect(confirmed.locator(".victorystate")).to_have_count(0)
    expect(confirmed.get_by_role("link", name="Kinnita töövõiduks")).to_have_count(0)
    screenshots(page, "teema-kinnitatud-toovoit")

    # Whether a candidate counts as a Töövõit is `VISIBLE_VICTORY_STATUS`'s
    # answer and is asserted against the database — this Matter carries a
    # confirmed victory of its own, so no register query could tell the two
    # rows apart (tests/test_teemad_consolidation.py).


# -- adding a fact without leaving the Matter -------------------------------
#
# `+ Jõustumine` and `+ Töövõit` are two or three fields about something the
# reader is already looking at, and they used to cost a trip to a page of their
# own and a trip back. They open in place now; the route, the form and the
# service behind them did not change (docs/adr/0065).


EMPTY_MATTER = "Jõustumiseta ja töövõiduta sünteetiline teema"


def test_the_empty_state_opens_the_commencement_form_in_place(page, base_url, screenshots):
    """A Matter with nothing recorded offers two chips, and one of them opens.

    Its own Matter, created through the real form: the seeded one already
    carries commencements and work victories, and the empty state is a
    different affordance in a different place.
    """
    sign_in(page, base_url, MARTIN)
    where = create_matter(page, base_url, EMPTY_MATTER)

    expect_chip(page, "+ Jõustumine").to_be_visible()
    form = open_add_form(page, "+ Jõustumine")

    expect(form.get_by_role("heading", name="Lisa jõustumine")).to_be_visible()
    expect(form.get_by_label("Mis jõustub")).to_be_visible()
    assert page.url == where, "the add form must not be a page of its own"
    screenshots(page, "teema-joustumine-kohapeal")


def test_a_refused_commencement_stays_open_with_what_was_typed(page, base_url):
    """The failure this interaction exists for.

    A commencement that happens «üldises korras» may not carry a date. Refusing
    it on a page of its own put the person somewhere they then had to leave
    again; refused in place, the words are still in the boxes and the reason is
    beside them.
    """
    sign_in(page, base_url, MARTIN)
    where = create_matter(page, base_url, "Tagasi lükatud jõustumisega teema")

    form = open_add_form(page, "+ Jõustumine")
    form.get_by_label("Mis jõustub").fill("vastuoluline säte")
    form.get_by_label("Teadaolev kuupäev").check()
    form.get_by_label("Kuupäev", exact=True).fill("1.1.2032")
    form.get_by_label("Jõustub üldises korras").check()
    form.get_by_role("button", name="Salvesta").click()

    expect(form.get_by_text("ainult teadaoleva jõustumise")).to_be_visible()
    expect(form.get_by_label("Mis jõustub")).to_have_value("vastuoluline säte")
    assert page.url == where
    # And nothing was written.
    expect(page.get_by_role("region", name="Jõustumine")).to_have_count(0)


def test_closing_the_form_writes_nothing_and_leaves_the_matter_as_it_was(page, base_url):
    sign_in(page, base_url, MARTIN)
    where = create_matter(page, base_url, "Loobutud jõustumisega teema")

    form = open_add_form(page, "+ Jõustumine")
    form.get_by_label("Mis jõustub").fill("ei salvestata")
    form.get_by_role("link", name="Sulge").click()

    expect(add_form(page)).to_have_count(0)
    expect(page.get_by_role("region", name="Jõustumine")).to_have_count(0)
    expect_chip(page, "+ Jõustumine").to_be_visible()
    assert page.url == where


def test_a_commencement_saved_in_place_appears_on_the_matter(page, base_url):
    """The wording is deliberately unlike the seeded commencements.

    A record written here is a real record, and it reaches the department-wide
    `Jõustuvad aktid` page beside every other one. `põhiosa` is how the seeded
    commencement is described and how another test in this file addresses it,
    so a second row containing that word makes *that* test a strict-mode
    violation rather than this one a failure.
    """
    sign_in(page, base_url, MARTIN)
    where = create_matter(page, base_url, "Kohapeal salvestatud jõustumisega teema")

    form = open_add_form(page, "+ Jõustumine")
    form.get_by_label("Mis jõustub").fill("kohapealt kirja pandud rakendussäte")
    form.get_by_label("Teadaolev kuupäev").check()
    form.get_by_label("Kuupäev", exact=True).fill("1.1.2032")
    form.get_by_role("button", name="Salvesta").click()

    commencements = section(page, "Jõustumine")
    expect(commencements.get_by_text("kohapealt kirja pandud rakendussäte")).to_be_visible()
    expect(commencements.get_by_role("listitem")).to_have_count(1)
    expect(add_form(page)).to_have_count(0)
    assert page.url == where


def test_the_work_victory_form_opens_in_place_and_its_period_control_narrows(page, base_url):
    """The empty state's second chip, and the conditional fields inside it."""
    sign_in(page, base_url, MARTIN)
    where = create_matter(page, base_url, "Kohapeal lisatud töövõiduga teema")

    form = open_add_form(page, "+ Töövõit")
    expect(form.get_by_role("heading", name="Lisa töövõit")).to_be_visible()

    form.get_by_label("Kvartali täpsusega").check()
    expect(form.get_by_label("Kuupäev", exact=True)).to_be_hidden()
    expect(form.get_by_label("Kvartal", exact=True)).to_be_visible()

    form.get_by_label("Töövõit", exact=True).fill("Erisus jäi sisse")
    form.get_by_label("Kvartal", exact=True).select_option("2")
    form.get_by_label("Aasta", exact=True).fill("2031")
    form.get_by_role("button", name="Salvesta töövõit").click()

    victories = section(page, "Töövõidud")
    expect(victories.get_by_text("Erisus jäi sisse")).to_be_visible()
    expect(victories.get_by_text("II kvartal 2031")).to_be_visible()
    expect(add_form(page)).to_have_count(0)
    assert page.url == where


def test_a_refused_work_victory_stays_open_with_what_was_typed(page, base_url):
    sign_in(page, base_url, MARTIN)
    where = create_matter(page, base_url, "Tagasi lükatud töövõiduga teema")

    form = open_add_form(page, "+ Töövõit")
    form.get_by_label("Töövõit", exact=True).fill("Poolik töövõit")
    form.get_by_label("Kvartali täpsusega").check()
    form.get_by_label("Kvartal", exact=True).select_option("2")
    # No year beside the quarter, which is the one thing a quarter needs.
    form.get_by_role("button", name="Salvesta töövõit").click()

    expect(form.get_by_label("Töövõit", exact=True)).to_have_value("Poolik töövõit")
    expect(page.get_by_role("region", name="Töövõidud")).to_have_count(0)
    assert page.url == where


def test_only_one_add_form_is_open_at_a_time(page, base_url, screenshots):
    """The accordion, both ways round.

    Nothing is stored to remember which was open: the block renders with at
    most one form in it, so opening either one is what closes the other.
    """
    sign_in(page, base_url, MARTIN)
    create_matter(page, base_url, "Kahe lisamisvormiga teema")

    open_add_form(page, "+ Jõustumine")
    expect(page.locator("#faktivorm-joustumine")).to_be_visible()
    expect(page.locator("#faktivorm-toovoit")).to_have_count(0)

    open_add_form(page, "+ Töövõit")
    expect(page.locator("#faktivorm-toovoit")).to_be_visible()
    expect(page.locator("#faktivorm-joustumine")).to_have_count(0)
    screenshots(page, "teema-toovoit-kohapeal")

    open_add_form(page, "+ Jõustumine")
    expect(page.locator("#faktivorm-joustumine")).to_be_visible()
    expect(page.locator("#faktivorm-toovoit")).to_have_count(0)


def test_a_reader_is_offered_no_inline_add_control(page, base_url):
    """Adding is a business write, and inlining the form did not widen it."""
    sign_in(page, base_url, READER)
    open_the_matter(page, base_url, OPEN_TITLE)

    for label in ("+ Jõustumine", "+ Lisa jõustumine", "+ Töövõit", "+ Lisa töövõit"):
        expect(page.get_by_role("link", name=label, exact=True)).to_have_count(0)
    expect(add_form(page)).to_have_count(0)


# -- finding these facts in the register ------------------------------------
#
# The three generated department pages are gone. What they answered — which
# files carry a Töövõit, which carry a Jõustumine, and when — is answered by the
# register, where it composes with every other dimension a lawyer already knows
# (docs/adr/0071).
#
# Deliberately few, like the rest of this suite. The populations, the period
# semantics and the authorization are proved against the database in
# `tests/test_teemad_consolidation.py`; what only a browser shows is that the
# controls are on the panel, that choosing one navigates, and that the chip
# above the rows says what was chosen.


def test_the_panel_offers_both_facts_as_filters(page, base_url, screenshots):
    sign_in(page, base_url, MARTIN)
    go_to(page, "Teemad")
    page.wait_for_url(f"{base_url}/teemad/")

    panel = page.locator("#tapsem-otsing")
    panel.locator("summary.filterpanel__trigger").click()

    # Located by the parameter each control submits rather than by its
    # accessible name, and not for convenience: every field in this panel wraps
    # its `<select>` inside the `<label>`, so the computed name is the legend
    # *plus every option's text* — `get_by_label("Töövõit", exact=True)` matches
    # nothing and the loose form matches by accident. The visible legend is
    # asserted separately below, which is the half a reader actually reads.
    expect(panel.locator("select[name='toovoit']")).to_be_visible()
    expect(panel.locator("select[name='joustumine']")).to_be_visible()
    expect(panel.locator("input[name='joustub_alates']")).to_be_visible()
    expect(panel.locator("input[name='joustub_kuni']")).to_be_visible()
    expect(panel.get_by_text("Töövõit", exact=True).first).to_be_visible()
    expect(panel.get_by_text("Jõustumine", exact=True).first).to_be_visible()
    screenshots(page, "teemad-struktuursed-filtrid")


def test_choosing_toovoit_narrows_the_register_and_says_so(page, base_url):
    """The whole round trip a reader makes: choose, submit, read the chip."""
    sign_in(page, base_url, MARTIN)
    open_register(page, base_url, "?olek=koik")

    panel = page.locator("#tapsem-otsing")
    panel.locator("summary.filterpanel__trigger").click()
    panel.locator("select[name='toovoit']").select_option("on")
    panel.get_by_role("button", name="Filtreeri").click()
    page.wait_for_load_state("networkidle")

    assert "toovoit=on" in page.url
    # The chip above the rows, which is how a reader knows what narrowed them.
    chip = page.locator(".filterbar--chips .filterchip").filter(has_text="Töövõit")
    expect(chip.first).to_contain_text("Töövõiduga")
    expect(page.get_by_role("link", name=OPEN_TITLE).first).to_be_visible()


def test_a_register_row_still_opens_its_matter(page, base_url):
    sign_in(page, base_url, MARTIN)
    open_register(page, base_url, "?joustumine=on&olek=koik")

    page.get_by_role("link", name=OPEN_TITLE).first.click()
    page.wait_for_load_state("networkidle")

    expect(page.get_by_role("heading", name=OPEN_TITLE)).to_be_visible()


# -- authorization ----------------------------------------------------------


@pytest.mark.parametrize("query", ["?toovoit=on", "?joustumine=on"])
def test_a_restricted_matters_facts_never_reach_the_register_filters(page, base_url, query):
    """Not the row, not the title, not the Matter behind it.

    A `READER` has no relationship to Sandra's restricted Matter, so nothing
    about it may appear — and, crucially, its *absence* from `puudub` must not
    disclose it either. The filter scopes the child table before the existence
    test contributes anything (Stage-2G brief 31, docs/adr/0071).
    """
    sign_in(page, base_url, READER)
    open_register(page, base_url, f"{query}&olek=koik")

    expect(page.get_by_text("Konfidentsiaalne")).to_have_count(0)
    expect(page.get_by_text(RESTRICTED_TITLE)).to_have_count(0)


def test_the_owner_does_see_her_own_restricted_facts(page, base_url):
    sign_in(page, base_url, SANDRA)
    open_register(page, base_url, "?joustumine=on&olek=koik")

    expect(page.get_by_role("link", name=RESTRICTED_TITLE).first).to_be_visible()


def test_a_technical_administrator_sees_no_restricted_facts(page, base_url):
    """Technical administration is not business access (specification 5.2)."""
    sign_in(page, base_url, ADMIN)
    open_register(page, base_url, "?joustumine=on&olek=koik")

    expect(page.get_by_text(RESTRICTED_TITLE)).to_have_count(0)


def test_an_administrator_has_no_write_controls(page, base_url):
    sign_in(page, base_url, ADMIN)
    open_the_matter(page, base_url, OPEN_TITLE)

    expect(page.get_by_role("link", name="+ Lisa tähtaeg")).to_have_count(0)
    expect(page.get_by_role("link", name="+ Lisa töövõit")).to_have_count(0)


def test_the_department_head_sees_restricted_facts_by_role(page, base_url):
    sign_in(page, base_url, HEAD)
    open_register(page, base_url, "?joustumine=on&olek=koik")

    expect(page.get_by_role("link", name=RESTRICTED_TITLE).first).to_be_visible()


def test_an_important_deadline_is_on_its_owners_own_page(page, base_url, screenshots):
    """The third fact, on the surface it moved to.

    There is no department-wide deadline list any more. An `Oluline tähtaeg`
    belongs to whoever owns the file, so it is on that person's Minu asjad and
    on nobody else's (docs/adr/0071).
    """
    sign_in(page, base_url, SANDRA)
    page.goto(f"{base_url}/minu-asjad/")
    page.wait_for_load_state("networkidle")

    expect(page.get_by_text("Konfidentsiaalne tähtaeg").first).to_be_visible()
    screenshots(page, "minu-asjad-oluline-tahtaeg")

    sign_in(page, base_url, ADMIN)
    page.goto(f"{base_url}/minu-asjad/")
    page.wait_for_load_state("networkidle")
    expect(page.get_by_text("Konfidentsiaalne tähtaeg")).to_have_count(0)
