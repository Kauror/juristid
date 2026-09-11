"""The post-QA round's browser-only halves, and the responsive pass behind them.

Four of the eight findings in this round are interactions, and an interaction is
only true in a browser:

* **R2-10** — the picker's results panel had no way to close except Escape or
  emptying the box, so a reader who searched, chose, and tabbed on left a list
  of institutions standing open over the next field.
* **R2-11** — `N valitud` counted the checkbox group and nothing else, so a body
  named through `+` was visibly chosen and invisibly uncounted.
* **R2-12** — `Muuda teemat` now asks the Organisation question through the same
  control `Uus teema` asks it with. What a POST leaves on the Matter is pinned
  in `tests/test_post_qa_read_surfaces.py`; what is here is that the control on
  the page really is that control, searching, folding and committing the way the
  create form's does.
* **§17** — 1440, 1024 and 420, with 420 exercised rather than reasoned about.

R2-06 through R2-09, R2-13 and R2-14 are server-rendered facts and are pinned
against real HTML in `tests/test_post_qa_read_surfaces.py`. They are not
repeated here: a browser adds nothing to «the page prints this string», and the
one thing it would add is one more file the visual job has to run.
"""

from __future__ import annotations

import re
import uuid

import pytest
from playwright.sync_api import expect

from e2e.conftest import MARTIN, create_matter, sign_in

pytestmark = pytest.mark.e2e

CREATE_PATH = "/teemad/uus/"

MINISTRY = "Näidisministeerium"
MINISTRY_ALIAS = "NÄIDISMIN"

PLACEHOLDER = "Otsi või lisa asutus…"

#: The picker ids each page uses. `Uus teema` and `Muuda teemat` are the same
#: control twice, so every helper below takes one of these and nothing else
#: distinguishes the two pages.
CREATE_SENDER = "saatja"
CREATE_ADDRESSEE = "adressaat"
EDIT_SENDER = "muuda-saatja"
EDIT_ADDRESSEE = "muuda-adressaat"

VIEWPORTS = {
    "wide": {"width": 1440, "height": 900},
    "laptop": {"width": 1024, "height": 800},
    "phone": {"width": 420, "height": 900},
}


def a_new_name() -> str:
    """A body the catalogue provably does not hold yet.

    Unique per call: this suite shares one database, so a fixed name is in the
    catalogue the moment this file has run once and «`+` proposed a new body»
    would correctly stop being true.
    """
    return f"Euroopa Näidisamet {uuid.uuid4().hex[:8]}"


def box(page, field: str):
    return page.locator(f"#{field}-otsi")


def add_button(page, field: str):
    return page.locator(f"#{field}-valik [data-orgfind-add]")


def results(page, field: str):
    return page.locator(f"#{field}-tulemused")


def search(page, field: str, term: str) -> None:
    control = box(page, field)
    control.click()
    control.fill("")
    control.type(term, delay=20)


def chosen_names(page, field: str) -> list[str]:
    """Every institution currently answered in one picker.

    The blank «Määramata» radio is checked whenever nothing else is, so it is
    excluded by value rather than by label.
    """
    return page.locator(f"#{field}-valik label.chip").evaluate_all(
        "nodes => nodes"
        ".filter(node => { const i = node.querySelector('input');"
        " return i && i.checked && (!i.name || i.value !== ''); })"
        ".map(node => { const n = node.querySelector('.chip__name');"
        " return (n ? n.textContent : '').replace(/\\s*×$/, '').trim(); })"
    )


def selected_count(page) -> str:
    return (page.locator('[data-chipcount-for="source_organisations"]').inner_text() or "").strip()


def create_form(page, base_url) -> None:
    sign_in(page, base_url, MARTIN)
    page.set_viewport_size(VIEWPORTS["wide"])
    page.goto(f"{base_url}{CREATE_PATH}")
    page.wait_for_load_state("networkidle")


def edit_form(page, base_url, title: str | None = None) -> str:
    """A Matter of this test's own, opened on `Muuda teemat`.

    Its own rather than a seeded one: these tests change senders and addressees,
    and the seeded Matters are what the visual job photographs.
    """
    sign_in(page, base_url, MARTIN)
    page.set_viewport_size(VIEWPORTS["wide"])
    detail = create_matter(page, base_url, title or f"Parandatav teema {uuid.uuid4().hex[:8]}")
    page.goto(f"{detail}muuda/")
    page.wait_for_load_state("networkidle")
    return detail


# ---------------------------------------------------------------------------
# R2-10 — the results panel closes when focus leaves the picker
# ---------------------------------------------------------------------------


def test_the_results_panel_closes_when_focus_leaves_the_picker(page, base_url):
    """The defect: a list of ministries standing open over the next field.

    Tabbing out of a combobox is the ordinary way to be finished with one, and
    it was the one exit this control did not have.
    """
    create_form(page, base_url)
    search(page, CREATE_SENDER, "Näidis")
    expect(results(page, CREATE_SENDER)).to_be_visible()

    page.locator("#id_title").click()

    expect(results(page, CREATE_SENDER)).to_be_hidden()
    expect(box(page, CREATE_SENDER)).to_have_attribute("aria-expanded", "false")


def test_focus_moving_between_the_two_pickers_closes_only_the_one_left(page, base_url):
    """Two of these on one form, and neither may hold the other's list open."""
    create_form(page, base_url)
    search(page, CREATE_SENDER, "Näidis")
    expect(results(page, CREATE_SENDER)).to_be_visible()

    page.locator("[data-addressee-disclosure] > summary").click()
    search(page, CREATE_ADDRESSEE, "Näidis")

    expect(results(page, CREATE_SENDER)).to_be_hidden()
    expect(results(page, CREATE_ADDRESSEE)).to_be_visible()


def test_clicking_a_result_still_selects_it(page, base_url):
    """The blur fix must not close the list out from under the click.

    This is the regression the naive `blur` handler causes: `mousedown` fires
    before `click`, so a panel that closed on blur removed the option the
    pointer was on its way to.
    """
    create_form(page, base_url)
    search(page, CREATE_SENDER, "Näidismin")
    results(page, CREATE_SENDER).get_by_role("option", name=MINISTRY, exact=True).click()

    assert chosen_names(page, CREATE_SENDER) == [MINISTRY]


def test_pressing_add_still_commits_a_new_name(page, base_url):
    """`+` is inside the picker, so focus moving to it is not focus leaving."""
    create_form(page, base_url)
    name = a_new_name()
    search(page, CREATE_SENDER, name)
    add_button(page, CREATE_SENDER).click()

    assert chosen_names(page, CREATE_SENDER) == [name]


def test_keyboard_selection_still_works(page, base_url):
    """Arrow to a result, Enter to take it. No submit, no duplicate body."""
    create_form(page, base_url)
    search(page, CREATE_SENDER, "Näidismin")
    box(page, CREATE_SENDER).press("ArrowDown")
    expect(box(page, CREATE_SENDER)).to_have_attribute("aria-activedescendant", re.compile(r".+"))
    box(page, CREATE_SENDER).press("Enter")

    assert chosen_names(page, CREATE_SENDER) == [MINISTRY]
    expect(page).to_have_url(re.compile(r"/teemad/uus/$"))


def test_escape_still_closes_the_list(page, base_url):
    create_form(page, base_url)
    search(page, CREATE_SENDER, "Näidis")
    expect(results(page, CREATE_SENDER)).to_be_visible()

    box(page, CREATE_SENDER).press("Escape")

    expect(results(page, CREATE_SENDER)).to_be_hidden()


def test_focus_returning_to_a_held_query_reopens_the_list(page, base_url):
    """Closing on blur must not turn a search somebody came back to into a dead
    control."""
    create_form(page, base_url)
    search(page, CREATE_SENDER, "Näidis")
    page.locator("#id_title").click()
    expect(results(page, CREATE_SENDER)).to_be_hidden()

    box(page, CREATE_SENDER).click()

    expect(results(page, CREATE_SENDER)).to_be_visible()


# ---------------------------------------------------------------------------
# R2-11 — `N valitud` counts what is visibly chosen
# ---------------------------------------------------------------------------


def test_a_provisional_body_is_counted(page, base_url):
    """0 existing + 1 provisional → «1 valitud». It read nothing at all."""
    create_form(page, base_url)
    name = a_new_name()
    search(page, CREATE_SENDER, name)
    add_button(page, CREATE_SENDER).click()

    assert selected_count(page) == "1 valitud"


def test_an_existing_and_a_provisional_body_count_as_two(page, base_url):
    """1 existing + 1 provisional → «2 valitud». It read «1 valitud»."""
    create_form(page, base_url)
    search(page, CREATE_SENDER, "Näidismin")
    results(page, CREATE_SENDER).get_by_role("option", name=MINISTRY, exact=True).click()
    assert selected_count(page) == "1 valitud"

    search(page, CREATE_SENDER, a_new_name())
    add_button(page, CREATE_SENDER).click()

    assert selected_count(page) == "2 valitud"


def test_typing_without_committing_counts_nothing(page, base_url):
    """Typing is not selecting, and the count must keep saying so."""
    create_form(page, base_url)
    search(page, CREATE_SENDER, "Mingi kirjutamata asutus")

    assert selected_count(page) == ""


def test_clearing_the_provisional_chip_takes_it_off_the_count(page, base_url):
    create_form(page, base_url)
    search(page, CREATE_SENDER, a_new_name())
    add_button(page, CREATE_SENDER).click()
    assert selected_count(page) == "1 valitud"

    # `click`, not `uncheck`: the `×` is `aria-hidden` decoration over the chip's
    # own checkbox, and unticking that checkbox *removes the chip* — letting go
    # of a typed name takes the chip with it rather than leaving an unticked one
    # (`bindOrganisationPickers` `syncProvisional`). `uncheck` would then wait
    # for an unchecked state on a node that no longer exists.
    page.locator(f"#{CREATE_SENDER}-valik [data-orgfind-provisional-input]").click()
    expect(page.locator(f"#{CREATE_SENDER}-valik [data-orgfind-provisional]")).to_have_count(0)

    assert selected_count(page) == ""


# ---------------------------------------------------------------------------
# R2-12 — Muuda teemat asks the question with the same control
# ---------------------------------------------------------------------------


def test_the_edit_page_carries_the_unified_picker_for_both_fields(page, base_url):
    edit_form(page, base_url)

    expect(box(page, EDIT_SENDER)).to_be_visible()
    expect(box(page, EDIT_ADDRESSEE)).to_be_visible()
    expect(box(page, EDIT_SENDER)).to_have_attribute("placeholder", PLACEHOLDER)
    expect(box(page, EDIT_SENDER)).to_have_attribute("role", "combobox")


def test_the_edit_page_searches_the_catalogue_by_name(page, base_url):
    edit_form(page, base_url)
    search(page, EDIT_SENDER, "Näidismin")

    expect(
        results(page, EDIT_SENDER).get_by_role("option", name=MINISTRY, exact=True)
    ).to_be_visible()


def test_the_edit_page_searches_by_recorded_alias(page, base_url):
    """«NÄIDISMIN» finds the ministry, which is the one signal not on a label."""
    edit_form(page, base_url)
    search(page, EDIT_SENDER, MINISTRY_ALIAS)

    expect(
        results(page, EDIT_SENDER).get_by_role("option", name=MINISTRY, exact=True)
    ).to_be_visible()


def test_the_edit_page_folds_case_and_diacritics(page, base_url):
    """`naidismin` is the ministry too. Normalisation is the picker's, shared."""
    edit_form(page, base_url)
    search(page, EDIT_SENDER, "naidismin")

    expect(
        results(page, EDIT_SENDER).get_by_role("option", name=MINISTRY, exact=True)
    ).to_be_visible()


def test_the_edit_pickers_popup_closes_on_blur_too(page, base_url):
    edit_form(page, base_url)
    search(page, EDIT_SENDER, "Näidis")
    expect(results(page, EDIT_SENDER)).to_be_visible()

    page.locator("#id_title").click()

    expect(results(page, EDIT_SENDER)).to_be_hidden()


def test_the_edit_page_prepopulates_the_current_sender(page, base_url):
    """Choose, save, reopen: the answer comes back visibly chosen."""
    detail = edit_form(page, base_url)
    search(page, EDIT_SENDER, "Näidismin")
    results(page, EDIT_SENDER).get_by_role("option", name=MINISTRY, exact=True).click()
    page.get_by_role("button", name="Salvesta").click()
    page.wait_for_url(re.compile(r"/teemad/[0-9a-f-]{36}/$"))

    page.goto(f"{detail}muuda/")
    page.wait_for_load_state("networkidle")

    assert chosen_names(page, EDIT_SENDER) == [MINISTRY]


def test_the_edit_page_commits_a_new_body_with_plus(page, base_url):
    detail = edit_form(page, base_url)
    name = a_new_name()
    search(page, EDIT_SENDER, name)
    add_button(page, EDIT_SENDER).click()
    assert chosen_names(page, EDIT_SENDER) == [name]

    page.get_by_role("button", name="Salvesta").click()
    page.wait_for_url(re.compile(r"/teemad/[0-9a-f-]{36}/$"))

    expect(page.locator("#teema-andmed")).to_contain_text(name)
    assert detail


def test_pressing_add_on_an_existing_name_selects_that_body(page, base_url):
    """The duplicate the QA risk names: an exact name typed and `+` pressed.

    It has to be visible *before* the save, or the person spends the next minute
    wondering whether they have just made a second ministry.
    """
    edit_form(page, base_url)
    search(page, EDIT_SENDER, MINISTRY)
    add_button(page, EDIT_SENDER).click()

    assert chosen_names(page, EDIT_SENDER) == [MINISTRY]
    assert page.locator(f"#{EDIT_SENDER}-valik [data-orgfind-provisional]").count() == 0
    assert page.locator(f"#{EDIT_SENDER}-uus").input_value() == ""


def test_pressing_add_on_an_alias_selects_the_canonical_body(page, base_url):
    edit_form(page, base_url)
    search(page, EDIT_SENDER, MINISTRY_ALIAS)
    add_button(page, EDIT_SENDER).click()

    assert chosen_names(page, EDIT_SENDER) == [MINISTRY]
    assert page.locator(f"#{EDIT_SENDER}-uus").input_value() == ""


def test_enter_in_the_edit_search_box_never_submits_the_form(page, base_url):
    """A search box inside a long form. An unguarded Enter would save it."""
    edit_form(page, base_url)
    search(page, EDIT_SENDER, "Näidis")
    box(page, EDIT_SENDER).press("Enter")

    expect(page).to_have_url(re.compile(r"/muuda/$"))


def test_choosing_a_sender_on_the_edit_page_does_not_answer_the_addressee(page, base_url):
    """§11. On `Uus teema` a sender may default the addressee; here it may not —
    both are established facts by the time this page opens."""
    edit_form(page, base_url)
    search(page, EDIT_SENDER, "Näidismin")
    results(page, EDIT_SENDER).get_by_role("option", name=MINISTRY, exact=True).click()

    assert chosen_names(page, EDIT_ADDRESSEE) == []


def test_the_edit_page_counts_a_provisional_sender_too(page, base_url):
    """One picker, one count rule, on whichever page renders it."""
    edit_form(page, base_url)
    search(page, EDIT_SENDER, a_new_name())
    add_button(page, EDIT_SENDER).click()

    assert selected_count(page) == "1 valitud"


# ---------------------------------------------------------------------------
# §17 — 1440, 1024 and 420
# ---------------------------------------------------------------------------


def no_horizontal_overflow(page) -> None:
    overflow = page.evaluate(
        "() => document.documentElement.scrollWidth - document.documentElement.clientWidth"
    )
    assert overflow <= 1, f"the page scrolls sideways by {overflow}px"


@pytest.mark.parametrize("size", list(VIEWPORTS))
def test_the_create_picker_fits_every_width(page, base_url, size):
    create_form(page, base_url)
    page.set_viewport_size(VIEWPORTS[size])
    search(page, CREATE_SENDER, "Näidis")

    expect(results(page, CREATE_SENDER)).to_be_visible()
    no_horizontal_overflow(page)


@pytest.mark.parametrize("size", list(VIEWPORTS))
def test_the_edit_picker_fits_every_width(page, base_url, size):
    edit_form(page, base_url)
    page.set_viewport_size(VIEWPORTS[size])
    search(page, EDIT_SENDER, "Näidis")

    expect(results(page, EDIT_SENDER)).to_be_visible()
    expect(box(page, EDIT_SENDER)).to_be_visible()
    no_horizontal_overflow(page)


@pytest.mark.parametrize("size", list(VIEWPORTS))
def test_teema_andmed_with_oigusakt_and_muu_fits_every_width(page, base_url, size):
    """The two new read surfaces, at the widths §17 names.

    `Õigusakt` with `Muu` and its free text is the longest value this card can
    carry, and `Valdkond` now holds the canonical areas *and* the free text —
    which is exactly the pair that would push a metaline into a sideways scroll.
    """
    detail = edit_form(page, base_url)
    # `Muu` is a real vocabulary row in Õigusakt, and `.chip--other` is the class
    # the template puts on exactly that one — a lookup by the label «Muu» would
    # be a lookup by a word several rows on this form use.
    page.locator("label.chip--other input").check()
    page.fill("#id_legal_instrument_other", "Rohepöörde tegevuskava ja selle rakendusaktid")
    page.fill("#id_policy_area_other", "Ringmajandus ja kliimaneutraalsus")
    page.get_by_role("button", name="Salvesta").click()
    page.wait_for_url(re.compile(r"/teemad/[0-9a-f-]{36}/$"))

    page.set_viewport_size(VIEWPORTS[size])
    page.goto(detail)
    page.wait_for_load_state("networkidle")

    expect(page.locator("#teema-andmed")).to_contain_text("Õigusakt")
    expect(page.locator("#teema-pais")).to_contain_text("Ringmajandus ja kliimaneutraalsus")
    no_horizontal_overflow(page)


@pytest.mark.parametrize("size", list(VIEWPORTS))
def test_the_empty_deadline_editor_fits_every_width_and_starts_blank(page, base_url, size):
    sign_in(page, base_url, MARTIN)
    detail = create_matter(page, base_url, f"Tähtajata teema {uuid.uuid4().hex[:8]}")

    page.set_viewport_size(VIEWPORTS[size])
    page.goto(detail)
    page.wait_for_load_state("networkidle")
    page.get_by_text("+ Tähtaeg").click()

    editor = page.locator('.inlineedit__form input[name="response_deadline"]')
    expect(editor).to_be_visible()
    assert editor.input_value() == ""
    no_horizontal_overflow(page)


@pytest.mark.parametrize("size", list(VIEWPORTS))
def test_the_filtered_documents_view_fits_every_width(page, base_url, size):
    sign_in(page, base_url, MARTIN)
    detail = create_matter(page, base_url, f"Failidega teema {uuid.uuid4().hex[:8]}")

    page.set_viewport_size(VIEWPORTS[size])
    page.goto(f"{detail}dokumendid/?roll=arvamus")
    page.wait_for_load_state("networkidle")

    no_horizontal_overflow(page)
