"""One control for «which institution?», in a real browser.

`tests/test_unified_organisation_picker.py` proves what a GET and a POST can
prove: the control posts what it used to, a refused save comes back holding the
answer, the catalogue is one catalogue, and nothing creates an `Organisation`
before `Loo teema` says so. None of that is the thing this round changed.

What it changed is an interaction, and an interaction is only true in a browser.
Saatja and Adressaat each used to ask one question three ways — a row of quick
chips, a `<details>` reading «Vali nimekirjast (N)» with a search box inside it,
and a separate «Uus saatja» / «Uus adressaat» box somewhere else again — and the
person had to decide which of the three they were on before typing a letter.
There is one way now (docs/adr/0073):

    Otsi kõigepealt olemasolevat. Kui seda ei ole, lisa sama välja kaudu uus.

Five properties this file exists for, and the last two are the ones that would
be expensive to get wrong:

* **search first, and it searches at once.** No Enter, no button, no disclosure
  and no reload between typing and seeing what the catalogue holds.
* **typing is not creating.** A name in the box is a query. Only `+` proposes an
  institution, and even then nothing is created until the Teema is saved — which
  is asserted here the way somebody would find out: by looking for the body in
  the catalogue afterwards.
* **`+` on a name the catalogue already holds selects that body.** Canonically
  or through a recorded alias, and in a browser this has to be visible *before*
  the save, or the person spends the next minute wondering whether they have
  just created a duplicate ministry.
* **an answer is visible.** Whatever put it there — a click, the search, `+`,
  the intake reader, or a refused save — it is a chip somebody can see and
  undo.
* **the sender still answers Adressaat**, and a person still outranks that.

**This suite shares one database with every other browser file and does not
grow the catalogue.** The seeded world holds two institutions, which is enough
for every property above; what it cannot show is a body that is *hidden* at rest
because the eight-chip shortlist did not reach it. Creating nine more bodies to
force that was considered and rejected: the catalogue this file leaves behind is
the catalogue `e2e/test_uus_teema_row_composition.py` measures and
`e2e/test_ui_regression.py` photographs, so a fixture that enlarged it would
make unrelated files depend on execution order. The hidden-tail rendering is
pinned against real server HTML in
`tests/test_unified_organisation_picker.py::test_the_bodies_outside_the_shortlist_arrive_out_of_sight`
instead, and the *mechanism* — a query hides the quick choices and offers
matches — is proved here, where it is the same code path either way.
"""

from __future__ import annotations

import re
import uuid

import pytest
from playwright.sync_api import expect

from e2e.conftest import MARTIN, sign_in

pytestmark = pytest.mark.e2e

CREATE_PATH = "/teemad/uus/"

MINISTRY = "Näidisministeerium"
PARTNER = "Näidisettevõtete liit"
#: The ministry's recorded abbreviation, seeded beside it. An alias is somebody's
#: decision that two spellings name one body, and it is the one search signal
#: that is not on the page as a label (`seed_e2e_data`).
MINISTRY_ALIAS = "NÄIDISMIN"

PLACEHOLDER = "Otsi või lisa asutus…"

SENDER = "saatja"
ADDRESSEE = "adressaat"

ADDRESSEE_DISCLOSURE = "[data-addressee-disclosure]"


def a_new_name() -> str:
    """A body the catalogue provably does not hold yet.

    Unique per call, because these tests *create* institutions and the browser
    suite shares one database — so a fixed name is in the catalogue from the
    moment this file has run once, and «pressing + proposed a new body» would
    then correctly stop being true.
    """
    return f"Euroopa Näidisamet {uuid.uuid4().hex[:8]}"


# ---------------------------------------------------------------------------
# Reaching the control
# ---------------------------------------------------------------------------


def create_form(page, base_url) -> None:
    sign_in(page, base_url, MARTIN)
    page.set_viewport_size({"width": 1440, "height": 900})
    page.goto(f"{base_url}{CREATE_PATH}")
    page.wait_for_load_state("networkidle")


def open_addressee(page) -> None:
    """Open the Adressaat disclosure, which is closed on every fresh visit.

    A closed `<details>` keeps its contents in the document, so every read in
    this file works through it untouched — but Playwright will not *click* what
    nobody can see, so anything answering Adressaat by hand opens it first. That
    is also what the person does (docs/adr/0069).
    """
    disclosure = page.locator(ADDRESSEE_DISCLOSURE)
    if not disclosure.evaluate("node => node.open"):
        disclosure.locator("> summary").click()


def box(page, field: str):
    return page.locator(f"#{field}-otsi")


def add_button(page, field: str):
    return page.locator(f"#{field}-valik [data-orgfind-add]")


def results(page, field: str):
    return page.locator(f"#{field}-tulemused")


def option_names(page, field: str) -> list[str]:
    return [
        (text or "").strip()
        for text in results(page, field).locator("[role=option]").all_text_contents()
    ]


def search(page, field: str, term: str) -> None:
    """Type into the box the way a person does — one key at a time.

    `fill` sets the value and fires one `input`, which is enough for this
    control and not enough for the claim «it searches while you type». `type`
    with a delay is what proves there is no Enter, no debounce long enough to
    notice, and no button between the keystroke and the list.
    """
    control = box(page, field)
    control.click()
    control.fill("")
    control.type(term, delay=20)


def chip_names(page, field: str, *, only_visible: bool = True) -> list[str]:
    """The chips in one picker, by the label somebody reads.

    `hidden` is on the label rather than on the input — the chip is what is
    shown or not shown — so visibility is asked of the chip and the `×` is
    trimmed off the name it carries.
    """
    return page.locator(f"#{field}-valik label.chip").evaluate_all(
        "(nodes, visibleOnly) => nodes"
        ".filter(node => !visibleOnly || !node.hidden)"
        ".map(node => { const n = node.querySelector('.chip__name');"
        " return (n ? n.textContent : '').replace(/\\s*×$/, '').trim(); })",
        only_visible,
    )


def chosen_names(page, field: str) -> list[str]:
    """Every institution currently answered in one picker.

    The blank «Määramata» radio is checked whenever nothing else is — that is
    what makes an addressee chosen by mistake unchoosable again — so it is
    excluded by value rather than by label, which is one whitespace difference
    away from counting "no answer" as an answer.
    """
    return page.locator(f"#{field}-valik label.chip").evaluate_all(
        "nodes => nodes"
        ".filter(node => { const i = node.querySelector('input');"
        " return i && i.checked && (!i.name || i.value !== ''); })"
        ".map(node => { const n = node.querySelector('.chip__name');"
        " return (n ? n.textContent : '').replace(/\\s*×$/, '').trim(); })"
    )


def choose_result(page, field: str, name: str) -> None:
    search(page, field, name[:8])
    results(page, field).get_by_role("option", name=name, exact=True).click()


def add_typed(page, field: str, name: str) -> None:
    search(page, field, name)
    add_button(page, field).click()


def summary(page) -> str:
    return " ".join((page.locator(f"{ADDRESSEE_DISCLOSURE} > summary").inner_text() or "").split())


def manual_flag(page) -> str:
    return page.locator("[data-addressee-manual]").input_value()


def catalogue_holds(page, base_url, name: str) -> bool:
    """Whether the catalogue holds one body, asked through the UI and nothing else.

    The browser suite has no database access on purpose — a test that could
    query around the interface could not notice the interface disagreeing with
    the register — so «was this created?» is asked by reopening the form and
    reading the picker, which renders every institution there is. That is also
    the answer the next person filing a Teema would get.
    """
    page.goto(f"{base_url}{CREATE_PATH}")
    page.wait_for_load_state("networkidle")
    return name in chip_names(page, SENDER, only_visible=False)


def file_the_teema(page, title: str) -> None:
    page.locator("#id_title").fill(title)
    page.get_by_role("button", name="Loo teema").click()
    page.wait_for_load_state("domcontentloaded")
    complaints = page.locator(".field__error, .formerror").all_inner_texts()
    assert not complaints, f"the form refused: {complaints}"
    page.wait_for_url(re.compile(r"/teemad/[0-9a-f-]{36}/$"))


# ---------------------------------------------------------------------------
# A — what somebody meets
# ---------------------------------------------------------------------------


def test_the_sender_field_opens_with_a_search_box_and_quick_choices(page, base_url):
    """The shape, top to bottom, as it arrives (task §3, §5)."""
    create_form(page, base_url)

    expect(box(page, SENDER)).to_be_visible()
    expect(box(page, SENDER)).to_have_attribute("placeholder", PLACEHOLDER)
    expect(add_button(page, SENDER)).to_be_visible()
    expect(add_button(page, SENDER)).to_be_disabled()
    expect(results(page, SENDER)).to_be_hidden()

    assert MINISTRY in chip_names(page, SENDER)
    assert PARTNER in chip_names(page, SENDER)


def test_the_search_box_is_above_the_quick_choices(page, base_url):
    """«Search first» is the decision, and geometry is where it is true or not.

    A field that rendered the chips above the box would satisfy every other
    assertion in this file while shipping the shape this round replaced.
    """
    create_form(page, base_url)

    field = box(page, SENDER).bounding_box()
    chips = page.locator(f"#{SENDER}-valik label.chip").first.bounding_box()

    assert field and chips
    assert field["y"] + field["height"] <= chips["y"] + 1, (
        f"the search box is not above the quick choices: {field} vs {chips}"
    )


def test_the_add_button_is_attached_to_the_box_and_the_same_height(page, base_url):
    """One control, not a box with a button somewhere near it (task §18)."""
    create_form(page, base_url)

    field = box(page, SENDER).bounding_box()
    button = add_button(page, SENDER).bounding_box()

    assert field and button
    assert abs(field["height"] - button["height"]) <= 1, "the + is not the height of the box"
    gap = button["x"] - (field["x"] + field["width"])
    assert 0 <= gap <= 8, f"the + is not attached to the box: {gap}px away"
    assert button["width"] >= 28 and button["height"] >= 28, "the + is too small to hit"


def test_the_retired_controls_are_nowhere_on_the_page(page, base_url):
    """«Vali nimekirjast» and the separate new-name boxes are gone (task §12).

    Read off the rendered page, which is where `<noscript>` correctly does not
    reach: the fallback still carries both, and a browser running this test
    never parses it.
    """
    create_form(page, base_url)
    open_addressee(page)

    body = page.locator("form.createform").inner_text()

    assert "Vali nimekirjast" not in body
    assert "Uus saatja" not in body
    assert "Uus adressaat" not in body
    assert page.locator(f"#{SENDER}-valik details").count() == 0
    assert page.locator(f"#{ADDRESSEE}-valik details").count() == 0


# ---------------------------------------------------------------------------
# B — the search
# ---------------------------------------------------------------------------


def test_typing_filters_at_once_with_nothing_else_pressed(page, base_url):
    """No Enter, no button, no disclosure, no reload (task §6)."""
    create_form(page, base_url)

    search(page, SENDER, "näidismin")

    expect(results(page, SENDER)).to_be_visible()
    assert option_names(page, SENDER) == [MINISTRY]
    # The quick choices give way to the results rather than sitting above them.
    assert PARTNER not in chip_names(page, SENDER)


def test_a_recorded_alias_finds_the_body_it_names(page, base_url):
    """«MKM» is not on the page as a label, and still has to find the ministry.

    Aliases reach the browser as `data-aliases`, already normalised by the
    server, so nothing here re-implements what an institution's identity is
    (task §6, §21).
    """
    create_form(page, base_url)

    search(page, SENDER, MINISTRY_ALIAS)

    assert option_names(page, SENDER) == [MINISTRY]


def test_diacritics_and_case_do_not_hide_a_body(page, base_url):
    """«naidis» finds «Näidis…», the way the server's own matcher folds them."""
    create_form(page, base_url)

    search(page, SENDER, "naidismin")

    assert option_names(page, SENDER) == [MINISTRY]


def test_choosing_a_result_selects_the_existing_body_and_clears_the_query(page, base_url):
    """Selection is a tick on a control that already existed (task §7, §17)."""
    create_form(page, base_url)

    choose_result(page, SENDER, PARTNER)

    assert chosen_names(page, SENDER) == [PARTNER]
    expect(box(page, SENDER)).to_have_value("")
    expect(results(page, SENDER)).to_be_hidden()
    # The quick choices are back, with the answer among them.
    assert MINISTRY in chip_names(page, SENDER)
    assert PARTNER in chip_names(page, SENDER)


def test_a_chosen_body_stays_visible_while_the_next_one_is_searched_for(page, base_url):
    """Task §8: an answer is never hidden by somebody looking for another."""
    create_form(page, base_url)
    choose_result(page, SENDER, PARTNER)

    search(page, SENDER, "näidismin")

    assert PARTNER in chip_names(page, SENDER), "the chosen sender disappeared during a search"


# ---------------------------------------------------------------------------
# C — naming a body the catalogue does not hold
# ---------------------------------------------------------------------------


def test_typing_a_name_creates_nothing(page, base_url):
    """The invariant, asked the way somebody would find out (task §25).

    Typed, looked at, arrowed through, abandoned. The catalogue is read back
    through the form itself, because the browser suite has no other way to ask
    and because that is the answer the next person filing a Teema would get.
    """
    create_form(page, base_url)
    name = a_new_name()

    search(page, SENDER, name)
    box(page, SENDER).press("ArrowDown")
    box(page, SENDER).press("Escape")

    assert not catalogue_holds(page, base_url, name), "typing created an institution"


def test_the_add_button_proposes_the_typed_name_as_a_chip(page, base_url):
    """`+` is the explicit «this is a body you do not have» (task §9).

    Still nothing in the catalogue: what the chip stands for is a *provisional*
    answer in the form, and the row is created by the ordinary resolver inside
    the save's own transaction or not at all.
    """
    create_form(page, base_url)
    name = a_new_name()

    add_typed(page, SENDER, name)

    provisional = page.locator(f"#{SENDER}-valik [data-orgfind-provisional]")
    expect(provisional).to_be_visible()
    assert name in (provisional.inner_text() or "")
    expect(box(page, SENDER)).to_have_value("")
    assert not catalogue_holds(page, base_url, name), "pressing + created an institution"


def test_a_teema_filed_with_a_typed_sender_creates_the_body_once(page, base_url):
    """The save owns creation, and the resolver owns what the name means."""
    create_form(page, base_url)
    name = a_new_name()
    add_typed(page, SENDER, name)

    file_the_teema(page, f"Uue asutuse kiri {uuid.uuid4().hex[:6]}")

    assert name in page.locator("main").inner_text()
    assert catalogue_holds(page, base_url, name)


def test_the_add_button_reuses_a_body_the_catalogue_already_holds(page, base_url):
    """Task §10: `+` on an existing spelling is that institution, never a second row.

    Decided on the server; shown here, because a person who pressed `+` on a
    ministry that already exists should be able to see that they have selected
    it rather than proposing a duplicate.
    """
    create_form(page, base_url)

    add_typed(page, SENDER, MINISTRY)

    assert chosen_names(page, SENDER) == [MINISTRY]
    assert page.locator(f"#{SENDER}-valik [data-orgfind-provisional]").count() == 0, (
        "an existing body was proposed as a new one"
    )


def test_the_add_button_reuses_a_body_named_by_a_recorded_alias(page, base_url):
    """The same, through the spelling somebody actually typed."""
    create_form(page, base_url)

    add_typed(page, SENDER, MINISTRY_ALIAS)

    assert chosen_names(page, SENDER) == [MINISTRY]
    assert page.locator(f"#{SENDER}-valik [data-orgfind-provisional]").count() == 0


# ---------------------------------------------------------------------------
# D — Saatja holds several bodies, Adressaat holds one
# ---------------------------------------------------------------------------


def test_several_senders_can_be_selected_through_the_search(page, base_url):
    """A Matter really can arrive from two bodies at once (ADR 0025, task §7)."""
    create_form(page, base_url)

    choose_result(page, SENDER, MINISTRY)
    choose_result(page, SENDER, PARTNER)

    assert sorted(chosen_names(page, SENDER)) == sorted([MINISTRY, PARTNER])


def test_a_typed_sender_does_not_replace_a_chosen_one(page, base_url):
    """`+` adds; it never takes an answer away (task §7, §30 D)."""
    create_form(page, base_url)
    choose_result(page, SENDER, MINISTRY)
    name = a_new_name()

    add_typed(page, SENDER, name)

    assert MINISTRY in chosen_names(page, SENDER)
    assert name in chosen_names(page, SENDER)


def test_the_addressee_keeps_exactly_one_answer(page, base_url):
    """One value, so choosing a second replaces the first (task §7)."""
    create_form(page, base_url)
    open_addressee(page)

    choose_result(page, ADDRESSEE, MINISTRY)
    choose_result(page, ADDRESSEE, PARTNER)

    assert chosen_names(page, ADDRESSEE) == [PARTNER]


# ---------------------------------------------------------------------------
# E — Adressaat keeps its fold and loses its nesting
# ---------------------------------------------------------------------------


def test_adressaat_arrives_folded_and_opens_onto_the_search_box(page, base_url):
    """The #169 decision stands; what is inside it is one control now (task §3)."""
    create_form(page, base_url)

    assert not page.locator(ADDRESSEE_DISCLOSURE).evaluate("node => node.open")
    assert summary(page) == "Adressaat"

    open_addressee(page)

    expect(box(page, ADDRESSEE)).to_be_visible()
    expect(box(page, ADDRESSEE)).to_have_attribute("placeholder", PLACEHOLDER)
    field = box(page, ADDRESSEE).bounding_box()
    chips = page.locator(f"#{ADDRESSEE}-valik label.chip").first.bounding_box()
    assert field and chips
    assert field["y"] + field["height"] <= chips["y"] + 1


def test_choosing_an_addressee_by_hand_is_a_manual_answer(page, base_url):
    """The override flag is what makes «Määramata beside a sender» reachable.

    It is set by the picker announcing a person-driven answer, not by
    `event.isTrusted` — which is the right test for a click on a radio and the
    wrong one for a control a person reaches through a search result
    (task §15, static/js/app.js `bindOrganisationPickers`).
    """
    create_form(page, base_url)
    open_addressee(page)

    choose_result(page, ADDRESSEE, PARTNER)

    assert chosen_names(page, ADDRESSEE) == [PARTNER]
    assert manual_flag(page) == "1"


def test_adding_a_typed_addressee_is_a_manual_answer(page, base_url):
    create_form(page, base_url)
    open_addressee(page)
    name = a_new_name()

    add_typed(page, ADDRESSEE, name)

    assert chosen_names(page, ADDRESSEE) == [name]
    assert manual_flag(page) == "1"


def test_searching_the_addressee_and_giving_up_is_not_an_answer(page, base_url):
    """Task §15: opening, focusing, typing and clearing are not decisions.

    This is the half of the override rule that is easy to lose. If merely
    looking counted, somebody who typed three letters into Adressaat and thought
    better of it would have silently switched off the sender default for the
    rest of the form.
    """
    create_form(page, base_url)
    open_addressee(page)

    search(page, ADDRESSEE, "näidis")
    box(page, ADDRESSEE).fill("")

    assert manual_flag(page) == ""
    assert chosen_names(page, ADDRESSEE) == []


# ---------------------------------------------------------------------------
# F — the Saatja is the Adressaat
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("how", ["chip", "search"])
def test_choosing_a_sender_answers_adressaat(page, base_url, how):
    """The load-bearing rule, through both ways of choosing an existing body.

    `e2e/test_counterparty_selection.py` owns this property; what is asserted
    here is that the *new control* reaches it — a picker that rebuilt the chip
    instead of ticking the one in the document, or swallowed the `change` event,
    would break the default silently (task §14).
    """
    create_form(page, base_url)

    if how == "chip":
        page.locator(f"#{SENDER}-valik label.chip", has_text=MINISTRY).first.click()
    else:
        choose_result(page, SENDER, MINISTRY)

    assert chosen_names(page, ADDRESSEE) == [MINISTRY]
    assert summary(page) == f"Adressaat · {MINISTRY}"
    assert not page.locator(ADDRESSEE_DISCLOSURE).evaluate("node => node.open"), (
        "answering Adressaat unfolded it"
    )
    assert manual_flag(page) == "", "a derived answer was recorded as somebody's own"


def test_a_typed_sender_becomes_the_typed_addressee(page, base_url):
    """A body with no primary key yet still answers the question beside it.

    There is no row until `Loo teema` runs, so the same *spelling* becomes the
    addressee through the control that exists for exactly that — and both
    resolve to one `Organisation` inside one transaction (task §14).
    """
    create_form(page, base_url)
    name = a_new_name()

    add_typed(page, SENDER, name)

    assert summary(page) == f"Adressaat · {name}"
    open_addressee(page)
    assert chosen_names(page, ADDRESSEE) == [name]


def test_taking_back_a_typed_sender_takes_the_answer_with_it(page, base_url):
    """A default never stands on nothing (task §8)."""
    create_form(page, base_url)
    name = a_new_name()
    add_typed(page, SENDER, name)
    assert summary(page) == f"Adressaat · {name}"

    page.locator(f"#{SENDER}-valik [data-orgfind-provisional]").click()

    assert summary(page) == "Adressaat"
    assert chosen_names(page, SENDER) == []


def test_an_addressee_chosen_by_hand_survives_a_change_of_sender(page, base_url):
    """Task §15, in the order that breaks it: answer, then change Saatja."""
    create_form(page, base_url)
    choose_result(page, SENDER, MINISTRY)
    open_addressee(page)
    choose_result(page, ADDRESSEE, PARTNER)

    choose_result(page, SENDER, PARTNER)
    page.locator(f"#{SENDER}-valik label.chip", has_text=MINISTRY).first.click()

    assert chosen_names(page, ADDRESSEE) == [PARTNER]
    assert summary(page) == f"Adressaat · {PARTNER}"


# ---------------------------------------------------------------------------
# H — the intake reader answers through the same control
# ---------------------------------------------------------------------------


def test_a_body_the_reader_ticks_while_it_is_out_of_sight_becomes_visible(page, base_url):
    """Task §16, and the case the old rendering could not serve.

    The reader hands the browser an `Organisation` primary key and ticks the
    control carrying it (`fill` in static/js/app.js). That control exists for
    every institution — which is the point of rendering the whole catalogue —
    but on an ordinary visit most of them are out of sight, and an answer
    nobody can see is an answer they cannot correct.

    The seeded world holds two institutions, so neither is *ever* out of sight
    at rest and the honest way to reach the state is to put one there: a query
    that matches only the other hides it. What follows is exactly what the
    reader does — set `checked`, dispatch `change` — and the picker has to bring
    the chip back. `e2e/test_integration_169_172.py` drives the real reader over
    a real file; this isolates the half that depends on where the chip was.
    """
    create_form(page, base_url)

    # `Näidisettevõtete liit` out of sight behind a query that only the ministry
    # matches, and then answered anyway.
    search(page, SENDER, "näidismin")
    assert PARTNER not in chip_names(page, SENDER)

    # `evaluate` rather than a click, because a hidden control is not clickable
    # and the reader does not click: it sets `checked` and dispatches the event.
    page.locator("#saatja-valik label.chip", has_text=PARTNER).first.evaluate(
        "chip => { const input = chip.querySelector('input');"
        " input.checked = true;"
        " input.dispatchEvent(new Event('change', {bubbles: true})); }"
    )

    assert PARTNER in chip_names(page, SENDER), "a sender the reader ticked stayed out of sight"
    assert chosen_names(page, SENDER) == [PARTNER]
    # And #169 still answers Adressaat from it, which is the seam a synthetic
    # event is entitled to cross (docs/adr/0069).
    assert summary(page) == f"Adressaat · {PARTNER}"
    assert manual_flag(page) == "", "a machine was recorded as having made somebody's choice"


# ---------------------------------------------------------------------------
# I — the keyboard
# ---------------------------------------------------------------------------


def test_the_results_are_reachable_and_choosable_by_keyboard(page, base_url):
    """Arrow to a result, Enter to take it (task §19)."""
    create_form(page, base_url)

    search(page, SENDER, "näidis")
    assert len(option_names(page, SENDER)) >= 2
    box(page, SENDER).press("ArrowDown")
    first = page.locator(f"#{SENDER}-tulemused [aria-selected=true]").inner_text().strip()
    box(page, SENDER).press("Enter")

    assert chosen_names(page, SENDER) == [first]
    expect(results(page, SENDER)).to_be_hidden()


def test_enter_on_a_highlighted_result_never_adds_a_new_body(page, base_url):
    """The precedence that must not be got wrong (task §19).

    Somebody typing «näidis» and arrowing onto `Näidisministeerium` is choosing
    it. An Enter that fell through to «add new» would file a second institution
    called «näidis» — and an unguarded Enter in a search box inside this form
    would file the Teema.
    """
    create_form(page, base_url)

    search(page, SENDER, "näidis")
    box(page, SENDER).press("ArrowDown")
    # Whichever body the ranking put first — both seeded institutions match this
    # prefix, and which of them leads is `localeCompare`'s answer rather than
    # this test's business. What is this test's business is that Enter took a
    # result at all.
    highlighted = page.locator(f"#{SENDER}-tulemused [aria-selected=true]").inner_text().strip()
    box(page, SENDER).press("Enter")

    assert page.locator(f"#{SENDER}-valik [data-orgfind-provisional]").count() == 0
    assert chosen_names(page, SENDER) == [highlighted]
    # Still on the form: Enter did not submit it.
    assert page.url.endswith(CREATE_PATH)


def test_escape_closes_the_results_and_keeps_the_answers(page, base_url):
    create_form(page, base_url)
    choose_result(page, SENDER, PARTNER)

    search(page, SENDER, "näidis")
    box(page, SENDER).press("Escape")

    expect(results(page, SENDER)).to_be_hidden()
    assert chosen_names(page, SENDER) == [PARTNER]


def test_tab_reaches_the_add_button_and_space_uses_it(page, base_url):
    """The `+` is a real button and is the next stop after the box (task §19)."""
    create_form(page, base_url)
    name = a_new_name()
    search(page, SENDER, name)

    page.keyboard.press("Tab")
    focused = page.evaluate("() => document.activeElement.hasAttribute('data-orgfind-add')")
    assert focused, "Tab from the search box does not reach the +"
    page.keyboard.press("Enter")

    assert name in chosen_names(page, SENDER)


# ---------------------------------------------------------------------------
# Accessibility
# ---------------------------------------------------------------------------


def test_the_box_announces_itself_as_a_combobox_only_once_it_is_one(page, base_url):
    """Roles are set by the script, never written into the template.

    Markup announcing a listbox that nothing can open describes behaviour the
    page does not have — a screen reader would offer a collapsed list that never
    opens (templates/base.html, task §20).
    """
    create_form(page, base_url)
    control = box(page, SENDER)

    expect(control).to_have_attribute("role", "combobox")
    expect(control).to_have_attribute("aria-expanded", "false")
    expect(control).to_have_attribute("aria-controls", f"{SENDER}-tulemused")

    search(page, SENDER, "näidismin")

    expect(control).to_have_attribute("aria-expanded", "true")
    expect(results(page, SENDER)).to_have_attribute("role", "listbox")
    box(page, SENDER).press("ArrowDown")
    assert control.get_attribute("aria-activedescendant"), "the active result is not announced"


def test_both_add_buttons_say_which_field_they_belong_to(page, base_url):
    """«+» alone is not a name. Task §20."""
    create_form(page, base_url)
    open_addressee(page)

    expect(page.get_by_role("button", name="Lisa uus saatja", exact=True)).to_have_count(1)
    expect(page.get_by_role("button", name="Lisa uus adressaat", exact=True)).to_have_count(1)


# ---------------------------------------------------------------------------
# J — the widths this application is used at
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("width", [1440, 1024, 768, 420])
def test_the_picker_never_takes_the_page_sideways(page, base_url, width):
    """Chips wrap; the box and its button stay on one row and inside it."""
    create_form(page, base_url)
    page.set_viewport_size({"width": width, "height": 900})
    open_addressee(page)
    search(page, SENDER, "näidis")

    overflow = page.evaluate(
        "() => document.documentElement.scrollWidth - document.documentElement.clientWidth"
    )
    assert overflow <= 1, f"the page scrolls sideways by {overflow}px at {width}"

    for field in (SENDER, ADDRESSEE):
        control = box(page, field).bounding_box()
        button = add_button(page, field).bounding_box()
        assert control and button
        assert abs(control["y"] - button["y"]) <= 4, (
            f"the + left the search row at {width} in {field}"
        )
        assert button["x"] + button["width"] <= width + 1
