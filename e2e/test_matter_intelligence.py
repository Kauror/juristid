"""Structured Matter facts, in a browser.

What a milestone, a commencement or a work victory *means* is proved against the
database in `tests/test_intelligence_*`. What only a browser shows is whether a
lawyer can capture one in seconds, whether the precision control actually
narrows to the fields that answer needs, whether an approximate period survives
the round trip to the screen, and whether the register filters really are
generated rather than typed.

**These drive the fragment, not the Teema page.** The approved Teema target
removed the standing facts panel: `+ Jõustumine` and `+ Töövõit` are composer
panels saved by the composer's one `Salvesta`, and reading a fact is the process
strip and the chronology (docs/adr/0074 §7, §8, §15). The section, its four add
forms and their inline behaviour are unchanged and still served by their own
routes, which is the surface this file has followed them to — `?vorm=sulge`
renders it at rest, and every route renders it with one form open.

Deliberately few, like the rest of the suite: these exist for the failures that
live between layers (docs/adr/0010).
"""

from __future__ import annotations

import re

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


#: Where each add control lives, now that none of them is on the Teema page.
FACT_PATHS = {
    "+ Lisa oluline tähtaeg": "olulised-tahtajad/lisa/",
    "+ Oluline tähtaeg": "olulised-tahtajad/lisa/",
    "+ Lisa jõustumine": "joustumine/lisa/",
    "+ Jõustumine": "joustumine/lisa/",
    "+ Lisa töövõit": "toovoidud/lisa/",
    "+ Töövõit": "toovoidud/lisa/",
}

#: The route that renders the panel at rest, which is what `Sulge` asks for.
AT_REST = "joustumine/lisa/?vorm=sulge"


#: The last Matter this page was on, so the fragment routes below can be built
#: from it. Captured rather than passed, because the tests reach a Matter two
#: different ways — through the register and through `Uus teema`.
_MATTER_URL: dict[str, str] = {}

_MATTER_PATH = re.compile(r"^(.*/teemad/[0-9a-fA-F-]{36})/?$")


def remember_matter(page) -> str:
    """Record the Matter page the browser is on, and return its address."""
    match = _MATTER_PATH.match(page.url.split("?")[0].split("#")[0])
    assert match, f"not on a Matter page: {page.url}"
    _MATTER_URL["current"] = match.group(1)
    return match.group(1)


def matter_url(page) -> str:
    return _MATTER_URL["current"]


def open_the_matter(page, base_url: str, title: str) -> None:
    """The Matter itself, and remember where it is for the fragment below."""
    page.set_extra_http_headers({})
    page.goto(f"{base_url}/teemad/")
    page.get_by_role("link", name=title).first.click()
    page.wait_for_load_state("networkidle")
    remember_matter(page)


def open_the_facts(page, base_url: str, title: str | None = None):
    """The structured-fact panel, at rest.

    Requested as HTMX, because that is the only way to it: an ordinary GET to
    the same address is the standalone page the deep link and the no-script
    browser get, and both renderings are deliberately kept (docs/adr/0065).
    """
    if title is not None:
        open_the_matter(page, base_url, title)
    page.set_extra_http_headers({"HX-Request": "true"})
    page.goto(f"{matter_url(page)}/{AT_REST}")
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


def open_add_form(page, label: str):
    """Open one of the four add forms and wait for it to arrive.

    Reached by its own route rather than by clicking a control on the Teema
    page: the approved target has no facts panel, so there is no control there
    to click (docs/adr/0074 §7, §8). The form, its `hx-target`, its refusals and
    its `Sulge` are exactly what they were.
    """
    page.set_extra_http_headers({"HX-Request": "true"})
    page.goto(f"{matter_url(page)}/{FACT_PATHS[label]}")
    page.wait_for_load_state("networkidle")
    form = add_form(page)
    expect(form).to_be_visible()
    return form


def expect_chip(page, label: str):
    """The composer panel that records this fact on the Teema page.

    `+ Jõustumine` and `+ Töövõit` are closed disclosures in the composer's
    progressive row now, not links into a fragment.
    """
    panel = {"+ Jõustumine": "#cx-joustumine", "+ Töövõit": "#cx-toovoit"}[label]
    return expect(page.locator(panel))


def open_register(page, base_url: str, query: str = "") -> None:
    """Teemad, narrowed by the structured facts this file is about.

    The three generated department pages this used to open are retired: a
    Teema is found in the register now, and `Töövõit` and `Jõustumine` narrow
    it like any other dimension (docs/adr/0071).
    """
    page.goto(f"{base_url}/teemad/{query}")
    page.wait_for_load_state("networkidle")


# -- the Matter page --------------------------------------------------------


def test_the_teema_page_reads_its_facts_and_records_them_from_the_composer(
    page, base_url, screenshots
):
    """**The three standing sections are not on the Teema page.**

    A dated milestone reads on the process strip and, once it has happened, in
    the chronology; a commencement and a win are recorded from composer panels
    saved by the one `Salvesta`. The sections themselves are unchanged and are
    asserted on the fragment below (TEEMA_TARGET_SPEC §A, docs/adr/0074 §15).
    """
    sign_in(page, base_url, MARTIN)
    open_the_matter(page, base_url, OPEN_TITLE)

    for gone in ("Olulised tähtajad", "Töövõidud"):
        expect(page.get_by_role("heading", name=gone)).to_have_count(0)
    expect(page.locator(".factspanel")).to_have_count(0)
    expect(page.locator(".tl-strip")).to_be_visible()
    expect_chip(page, "+ Jõustumine").to_have_count(1)
    expect_chip(page, "+ Töövõit").to_have_count(1)
    screenshots(page, "teema-struktuursed-faktid")


def test_the_fragment_still_carries_its_three_sections(page, base_url):
    sign_in(page, base_url, MARTIN)
    open_the_facts(page, base_url, OPEN_TITLE)

    expect(page.get_by_role("heading", name="Olulised tähtajad")).to_be_visible()
    expect(page.get_by_role("heading", name="Jõustumine", exact=True)).to_be_visible()
    expect(page.get_by_role("heading", name="Töövõidud", exact=True)).to_be_visible()


# -- the four fact forms, on the surface they still have --------------------
#
# `open_fact_form` is an ordinary GET, so what these drive is the standalone
# page: the deep link, the bookmark and the browser with scripting off, all of
# which the route has always served and ADR 0065 deliberately kept. Posting to
# it redirects back to the Matter, which is the journey a person without the
# inline path actually makes.


def open_fact_form(page, label: str):
    """The add form on its own page, and the form element inside it."""
    page.set_extra_http_headers({})
    page.goto(f"{matter_url(page)}/{FACT_PATHS[label]}")
    page.wait_for_load_state("networkidle")
    # `.factform` is the standalone page's own form; the shell's sign-out and
    # search forms are the reason this is scoped at all.
    form = page.locator("form.factform")
    expect(form).to_be_visible()
    return form


def test_an_exact_milestone_can_be_added_in_a_few_fields(page, base_url):
    """Four fields and a save — the claim this file exists to keep honest."""
    sign_in(page, base_url, MARTIN)
    create_matter(page, base_url, "Täpse tähtajaga teema")
    remember_matter(page)

    form = open_fact_form(page, "+ Lisa oluline tähtaeg")
    form.get_by_label("Mis on oodata", exact=True).fill("Kooskõlastusringi lõpp")
    form.get_by_label("Kuupäev", exact=True).fill("30.09.2026")
    form.get_by_role("button", name="Salvesta", exact=True).click()
    page.wait_for_load_state("networkidle")

    # It landed, and the route brought the reader back to the Matter — where the
    # milestone reads on the process strip (docs/adr/0074 §12).
    assert page.url.startswith(matter_url(page))
    expect(page.get_by_text("Kooskõlastusringi lõpp").first).to_be_visible()
    expect(page.locator(".tl-strip")).to_be_visible()


def test_a_quarter_is_captured_and_rendered_as_a_quarter(page, base_url, screenshots):
    """The precision control narrows to the fields that answer needs, and the
    period survives the round trip as a period rather than as its anchor day."""
    sign_in(page, base_url, MARTIN)
    create_matter(page, base_url, "Kvartali täpsusega tähtajaga teema")
    remember_matter(page)

    form = open_fact_form(page, "+ Lisa oluline tähtaeg")
    form.get_by_label("Mis on oodata", exact=True).fill("Riigikogu esimene lugemine")
    form.get_by_label("Kvartali täpsusega").check()

    # Narrowed: the day is gone and the quarter is there.
    expect(form.get_by_label("Kuupäev", exact=True)).to_be_hidden()
    expect(form.get_by_label("Kvartal", exact=True)).to_be_visible()
    screenshots(page, "teema-tahtaja-vorm")

    form.get_by_label("Kvartal", exact=True).select_option("1")
    form.get_by_label("Aasta", exact=True).fill("2027")
    form.get_by_role("button", name="Salvesta", exact=True).click()
    page.wait_for_load_state("networkidle")

    # «I kvartal 2027», never «1.1.2027»: rendering the stored anchor would
    # manufacture a day nobody named (master specification 3.5).
    expect(page.get_by_text("I kvartal 2027").first).to_be_visible()


def test_a_general_order_form_hides_the_date_control(page, base_url):
    """«Jõustub üldises korras» is an answer, and it is not a date — so the
    control that would ask for one is not there to be half-filled."""
    sign_in(page, base_url, MARTIN)
    create_matter(page, base_url, "Üldises korras jõustuva teema")
    remember_matter(page)

    form = open_fact_form(page, "+ Lisa jõustumine")
    form.get_by_label("Jõustub üldises korras").check()

    expect(form.get_by_label("Kuupäev", exact=True)).to_be_hidden()


def test_a_refused_commencement_comes_back_with_what_was_typed(page, base_url):
    """A refusal is not a blank form. What the person wrote is still in it."""
    sign_in(page, base_url, MARTIN)
    create_matter(page, base_url, "Tagasi lükatud jõustumisega teema")
    remember_matter(page)

    form = open_fact_form(page, "+ Lisa jõustumine")
    form.get_by_label("Mis jõustub", exact=True).fill("Pakendiseaduse muudatused")
    # A known date with no date is the refusal this form makes.
    form.get_by_role("button", name="Salvesta", exact=True).click()
    page.wait_for_load_state("networkidle")

    expect(page.get_by_label("Mis jõustub", exact=True)).to_have_value("Pakendiseaduse muudatused")


# -- work victories ---------------------------------------------------------


def test_a_person_adding_a_victory_gets_a_confirmed_one(page, base_url, screenshots):
    """One save, and the row is what the person said it was.

    Not a candidate awaiting somebody's agreement: a colleague who may write on
    this Matter has already made that judgement, and the second step asked them
    to seek approval for a decision they had just taken.
    """
    sign_in(page, base_url, MARTIN)
    create_matter(page, base_url, "Käsitsi lisatud töövõiduga teema")
    remember_matter(page)

    form = open_fact_form(page, "+ Lisa töövõit")
    # The page talks about a Töövõit, never about confirming or proposing one.
    expect(page.locator(".cardnote")).to_have_text("Kirje lisatakse töövõiduna sinu nimel.")

    form.get_by_label("Töövõit", exact=True).fill("Erisus jäi eelnõusse sisse")
    # `exact=True`, because "Poolaasta täpsusega" contains "Aasta täpsusega".
    form.get_by_label("Aasta täpsusega", exact=True).check()
    form.get_by_label("Aasta", exact=True).fill("2030")
    form.get_by_role("button", name="Salvesta töövõit").click()
    page.wait_for_load_state("networkidle")
    screenshots(page, "teema-toovoit-kohapeal")

    # It reads on the Matter as an ordinary win, with no proposal state on it.
    expect(page.get_by_text("Erisus jäi eelnõusse sisse").first).to_be_visible()
    expect(page.locator(".victorystate")).to_have_count(0)


def test_a_specialist_has_no_confirmation_control(page, base_url):
    """Adding is theirs; adjudicating somebody else's candidate is not."""
    sign_in(page, base_url, MARTIN)
    open_the_facts(page, base_url, OPEN_TITLE)

    expect(page.get_by_role("link", name="Kinnita töövõiduks")).to_have_count(0)


def test_the_department_head_confirms_a_proposed_candidate(page, base_url, screenshots):
    """The review path, on the kind of row that still arrives as a candidate.

    Seeded rather than created here: the manual form no longer produces a
    candidate, so the only honest source of one is a machine or an import.
    """
    sign_in(page, base_url, HEAD)
    open_the_facts(page, base_url, OPEN_TITLE)

    row = section(page, "Töövõidud").get_by_role("listitem").filter(has_text=MACHINE_CANDIDATE)
    expect(row.get_by_text("Töövõidu kandidaat")).to_be_visible()

    page.set_extra_http_headers({})
    row.get_by_role("link", name="Kinnita töövõiduks").click()
    page.wait_for_load_state("networkidle")

    expect(page.get_by_role("heading", name="Kinnita töövõiduks")).to_be_visible()
    expect(page.get_by_text(MACHINE_CANDIDATE)).to_be_visible()
    page.get_by_role("button", name="Kinnita töövõiduks").click()
    page.wait_for_load_state("networkidle")
    screenshots(page, "teema-kinnitatud-toovoit")

    # Reviewed, so it stops being a proposal and becomes an ordinary Töövõit.
    open_the_facts(page, base_url)
    confirmed = (
        section(page, "Töövõidud").get_by_role("listitem").filter(has_text=MACHINE_CANDIDATE)
    )
    expect(confirmed.locator(".victorystate")).to_have_count(0)
    expect(confirmed.get_by_role("link", name="Kinnita töövõiduks")).to_have_count(0)


def test_a_reader_is_offered_no_way_to_record_a_fact(page, base_url):
    """Authorization did not move with the surface.

    On the Teema page a reader gets no composer at all, so neither panel exists;
    on the fragment they get the sections and none of the add controls.
    """
    sign_in(page, base_url, READER)
    open_the_matter(page, base_url, OPEN_TITLE)

    expect(page.locator("#teema-koostaja")).to_have_count(0)
    expect_chip(page, "+ Jõustumine").to_have_count(0)
    expect_chip(page, "+ Töövõit").to_have_count(0)

    open_the_facts(page, base_url)
    expect(page.get_by_role("link", name="+ Lisa jõustumine", exact=True)).to_have_count(0)
    expect(page.get_by_role("link", name="+ Lisa töövõit", exact=True)).to_have_count(0)


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
