"""Saatja and Adressaat in a real browser, on one open form.

Everything here is about what happens *before* a round trip. The server does the
same ordering for a bound form — a refused save must not drop the sender back
under the historical shortlist — and `tests/test_counterparty_promotion.py`
pins that half. What only a browser can answer is whether ticking a chip moves
anything at all while the person is still filling the form in, and whether a
name they are in the middle of typing can be reused as the answer to the
question beside it.

Three properties, and the middle one is the one worth breaking a build over:

* the sender chosen here **becomes** the addressee, visibly, with no reload —
  and does so without unfolding the Adressaat disclosure, because a page that
  opened a section because it had answered a question itself would be reacting
  to its own writing;
* an addressee somebody picked by hand is not disturbed by anything they
  subsequently do to Saatja;
* the same typed name, used for both, is one institution when it is saved.

The first of those reverses what this file used to assert. Until
docs/adr/0069 the sender was moved to the front of the addressee choices and
deliberately never selected; the risk that reasoning named is real, and the
trade it made was wrong — see the ADR.
"""

from __future__ import annotations

import uuid

import pytest
from playwright.sync_api import expect

from e2e.conftest import MARTIN, sign_in

pytestmark = pytest.mark.e2e

CREATE_PATH = "/teemad/uus/"

MINISTRY = "Näidisministeerium"


def a_new_name() -> str:
    """A body the catalogue provably does not hold yet.

    Unique per call, because these tests *create* institutions and the browser
    suite shares one database — so a fixed name is in the catalogue from the
    moment this file has run once, and the provisional chip then correctly
    refuses to appear for it. The second run would fail on the application
    behaving exactly as it should, which is the worst kind of flake to read.
    """
    return f"Euroopa Näidiskomisjon {uuid.uuid4().hex[:8]}"


SENDER_FIELD = "fieldset.senderpick"
ADDRESSEE_QUICK = '[data-clears="addressee_organisation"]'
ADDRESSEE_DISCLOSURE = "[data-addressee-disclosure]"


def create_form(page, base_url) -> None:
    sign_in(page, base_url, MARTIN)
    page.set_viewport_size({"width": 1440, "height": 900})
    page.goto(f"{base_url}{CREATE_PATH}")
    page.wait_for_load_state("networkidle")


def open_addressee(page) -> None:
    """Open the Adressaat disclosure, which is closed on every fresh visit.

    A closed `<details>` keeps its contents in the document — every `read`
    assertion in this file works through it untouched — but Playwright will not
    *click* what nobody can see, so anything answering Adressaat by hand has to
    open it first. That is also what the person does.
    """
    disclosure = page.locator(ADDRESSEE_DISCLOSURE)
    if not disclosure.evaluate("node => node.open"):
        disclosure.locator("> summary").click()


def _summary(page) -> str:
    """What the collapsed disclosure says: «Adressaat», or «Adressaat · X»."""
    return " ".join((page.locator(f"{ADDRESSEE_DISCLOSURE} > summary").inner_text() or "").split())


def _addressee_labels(page) -> list[str]:
    """The addressee chips in the quick row, in the order they are rendered."""
    return [
        (text or "").strip().rstrip("×").strip()
        for text in page.locator(f"{ADDRESSEE_QUICK} > label.chip .chip__name").all_text_contents()
    ]


def _chosen_addressees(page) -> int:
    """How many *institutions* are selected as the addressee.

    The blank «Määramata» option is a radio in the same group and it is checked
    whenever nothing else is — that is what makes an addressee chosen by mistake
    unchoosable again (`MatterCreateForm.addressee_organisation`, `blank=True`).
    Counting every checked radio would therefore count "no answer" as an answer,
    which is the opposite of what these tests are asking about.
    """
    return page.locator('input[name="addressee_organisation"]').evaluate_all(
        "nodes => nodes.filter(node => node.checked && node.value !== '').length"
    )


def _pick_some_addressee(page) -> tuple:
    """Choose an addressee by hand, whichever one the catalogue happens to offer.

    Deliberately not named. Every browser test in this suite shares one database
    and earlier tests create institutions, so which bodies land in the Adressaat
    shortlist and which fall into the long tail depends on what has already run.
    A test that clicked a named chip passed alone and failed in a full run —
    which is a defect in the test, not in the page, and the property being
    checked ("an answer given by hand survives") is about *any* answer.

    Returns the input and the name, so the caller can assert on both.
    """
    open_addressee(page)
    # Never the provisional chip. It sits at the front of this very row when a
    # new sender is being typed, and picking it would be choosing the typed name
    # again rather than choosing a body from the catalogue — which is the
    # opposite of what every caller here means by "by hand".
    chips = page.locator(f"{ADDRESSEE_QUICK} > label.chip:not([data-provisional-addressee])")
    for index in range(chips.count()):
        chip = chips.nth(index)
        radio = chip.locator("input")
        # By value, not by label. «Määramata» is a real radio in this group with
        # an empty value — it is what makes an addressee chosen by mistake
        # unchoosable again — and matching it on its rendered text is one
        # whitespace difference away from selecting "no answer" and then
        # asserting that the answer survived.
        if not (radio.get_attribute("value") or "").strip():
            continue
        name = (chip.inner_text() or "").strip().rstrip("×").strip()
        if name == MINISTRY:
            # The sender these tests promote. Choosing it would make "the
            # promotion moved something" and "the answer survived" the same
            # assertion, which is no assertion at all.
            continue
        value = radio.get_attribute("value")
        radio.click()
        # Returned bound to the *value*, never to the position it was found at.
        # Promotion works by relocating the chosen sender's option to the front
        # of this row, so an `nth(i)` locator silently starts pointing at a
        # different control the moment the thing under test does its job — and
        # the test then reports that the answer was lost when it was only
        # moved past.
        return (
            page.locator(f'input[name="addressee_organisation"][value="{value}"]'),
            name,
        )
    raise AssertionError("the Adressaat quick row offered nothing to choose by hand")


def _tick_sender(page, name: str) -> None:
    page.locator(f"{SENDER_FIELD} label.chip", has_text=name).first.click()


def skip_without_a_long_tail(page, base_url) -> None:
    """The Saatja disclosure only exists once the catalogue outgrows the shortlist.

    The seeded browser world holds two institutions and the sender shortlist is
    deliberately *filled* to eight from the catalogue rather than left short
    (`organisations_by_usage`), so there is no tail here and the disclosure is
    correctly not rendered at all.

    Creating nine more to force one was tried and withdrawn. Every browser test
    in this suite shares one database, so a test that enlarges the catalogue
    enlarges it for every test that runs afterwards — including
    `test_matter_form_ux.py` and `test_sender_free_entry.py`, which measure the
    sender control on the assumption that it has no tail. A fixture that makes
    unrelated files fail depending on execution order is worse than a gap in
    coverage, and this particular gap is small: the *structure* — search inside
    the disclosure, `Uus saatja` outside it, the count on the summary — is
    asserted against real server-rendered HTML in
    `tests/test_counterparty_promotion.py`, and `chipdetails` itself is the
    component Adressaat has used since it was written
    (`e2e/test_addressee_free_entry.py`).

    What is genuinely browser-only here is the promotion and the provisional
    chip below, and those need no tail at all.
    """
    if page.locator(f"{SENDER_FIELD} details.chipdetails").count() == 0:
        pytest.skip(
            "the seeded catalogue fits inside the sender shortlist, so this world "
            "renders no Saatja disclosure to measure"
        )


# ---------------------------------------------------------------------------
# The disclosure
# ---------------------------------------------------------------------------


def test_the_sender_shortlist_is_visible_and_the_catalogue_is_behind_a_door(page, base_url):
    """The shape, as somebody arriving at the page meets it."""
    create_form(page, base_url)
    skip_without_a_long_tail(page, base_url)

    sender = page.locator(SENDER_FIELD)
    # Chips, immediately, with no interaction.
    expect(sender.locator("> .chiprow > label.chip").first).to_be_visible()
    # The catalogue's search is not on the page until the door is opened.
    expect(sender.locator('input[type="search"]')).to_be_hidden()
    # And the box for a body that is not in the catalogue is, always.
    expect(sender.locator('input[name="sender_name"]')).to_be_visible()


def test_opening_the_disclosure_reveals_the_search_and_the_rest(page, base_url):
    create_form(page, base_url)
    skip_without_a_long_tail(page, base_url)

    sender = page.locator(SENDER_FIELD)
    summary = sender.locator("summary.chipdetails__summary")
    expect(summary).to_contain_text("Vali nimekirjast")
    summary.click()

    expect(sender.locator('input[type="search"]')).to_be_visible()


def test_the_search_inside_the_disclosure_filters_the_catalogue(page, base_url):
    """It filters what exists and posts nothing, which is the whole distinction.

    Typing «Näidis» here must narrow a list; it must never become an institution
    called «Näidis». That is what `Uus saatja` is for, and the two controls are
    deliberately separate (app/matters/forms.py `sender_name_field`).
    """
    create_form(page, base_url)
    skip_without_a_long_tail(page, base_url)

    sender = page.locator(SENDER_FIELD)
    sender.locator("summary.chipdetails__summary").click()
    box = sender.locator('input[type="search"]')
    box.fill("zzz-nothing-matches-this")

    catalogue = sender.locator(".chipdetails__body .chiprow > label.chip")
    expect(catalogue.locator("visible=true")).to_have_count(0)

    # And the filter box is not a form field.
    expect(box).to_have_attribute("type", "search")
    assert box.get_attribute("name") is None


# ---------------------------------------------------------------------------
# The sender chosen here is the answer there
# ---------------------------------------------------------------------------


def test_adressaat_arrives_folded_away_and_unanswered(page, base_url):
    """A fresh visit: one pill, no chips, nothing answered."""
    create_form(page, base_url)

    disclosure = page.locator(ADDRESSEE_DISCLOSURE)
    expect(disclosure).to_be_visible()
    assert not disclosure.evaluate("node => node.open"), "Adressaat is unfolded on arrival"
    expect(page.locator(f"{ADDRESSEE_QUICK} > label.chip").first).to_be_hidden()
    assert _summary(page) == "Adressaat"


def test_choosing_a_sender_answers_adressaat_without_a_reload(page, base_url):
    """The reported case, in the browser, before any round trip (§14).

    Asserted on the summary rather than only on the radio, because the summary
    is what somebody actually sees: the disclosure stays closed, so a value that
    was set and not announced would be a page that had answered a question
    silently.
    """
    create_form(page, base_url)

    before = _addressee_labels(page)
    assert before, "no addressee chips rendered at all"
    assert _chosen_addressees(page) == 0, "something was already answered on arrival"

    _tick_sender(page, MINISTRY)

    after = _addressee_labels(page)
    assert after[0] == MINISTRY, (
        f"after choosing {MINISTRY} as Saatja the first Adressaat chip is {after[0]!r}"
    )
    assert _chosen_addressees(page) == 1
    assert _summary(page) == f"Adressaat · {MINISTRY}"


def test_answering_adressaat_does_not_unfold_it(page, base_url):
    """§16. Filling a field in for somebody is a kindness; opening a section at
    them because you did is not."""
    create_form(page, base_url)
    _tick_sender(page, MINISTRY)

    assert not page.locator(ADDRESSEE_DISCLOSURE).evaluate("node => node.open"), (
        "answering Adressaat from Saatja unfolded the Adressaat disclosure"
    )


def test_the_answered_addressee_is_offered_once_and_never_drawn_twice(page, base_url):
    """One radio, moved — never a second one drawn.

    A copy would post `addressee_organisation` twice and leave the browser
    deciding which one counted; a move keeps one control per organisation, which
    is what the radio group's own cardinality requires.
    """
    create_form(page, base_url)
    _tick_sender(page, MINISTRY)

    ministry_radios = page.locator(f'{ADDRESSEE_QUICK} label.chip:has-text("{MINISTRY}") input')
    expect(ministry_radios).to_have_count(1)
    assert _chosen_addressees(page) == 1


def test_opening_adressaat_and_choosing_another_body_overrides_the_default(page, base_url):
    """§15. The default is a default, and the person is right there to change it."""
    create_form(page, base_url)
    _tick_sender(page, MINISTRY)
    assert _summary(page) == f"Adressaat · {MINISTRY}"

    chosen, name = _pick_some_addressee(page)

    expect(chosen).to_be_checked()
    assert name != MINISTRY
    assert _summary(page) == f"Adressaat · {name}"
    assert _chosen_addressees(page) == 1


def test_an_addressee_chosen_by_hand_survives_a_change_of_sender(page, base_url):
    """The failure that would be silent, plausible and only visible on the record.

    Display order may change under somebody. Their answer may not — and since
    the default writes into the same control, "may not" now covers a write as
    well as a re-sort.
    """
    create_form(page, base_url)

    chosen, name = _pick_some_addressee(page)
    expect(chosen).to_be_checked()

    _tick_sender(page, MINISTRY)

    expect(chosen).to_be_checked()
    assert _summary(page) == f"Adressaat · {name}"
    assert _addressee_labels(page)[0] == MINISTRY, "the sender was not moved to the front at all"


def test_unticking_the_sender_takes_the_answer_it_supplied_with_it(page, base_url):
    """§8, in the browser: a default never stands on nothing."""
    create_form(page, base_url)

    _tick_sender(page, MINISTRY)
    assert _chosen_addressees(page) == 1

    _tick_sender(page, MINISTRY)

    assert _chosen_addressees(page) == 0, "the answer outlived the sender it came from"
    assert _summary(page) == "Adressaat"


def test_unticking_the_sender_leaves_a_chosen_addressee_alone(page, base_url):
    """The same act, after somebody has answered. Theirs stays (§8, second half)."""
    create_form(page, base_url)

    chosen, name = _pick_some_addressee(page)
    _tick_sender(page, MINISTRY)
    _tick_sender(page, MINISTRY)

    expect(chosen).to_be_checked()
    assert _summary(page) == f"Adressaat · {name}"


# ---------------------------------------------------------------------------
# A body that does not exist yet
# ---------------------------------------------------------------------------


def test_a_newly_typed_sender_becomes_the_addressee_too(page, base_url):
    """No primary key, and still the answer beside it.

    There is no `Organisation` row until `Loo teema` creates one, so this chip
    stands for a name rather than a record — and it fills the free-text
    `Uus adressaat` control, which is the path that already exists for exactly
    this (task §5).
    """
    create_form(page, base_url)
    typed = a_new_name()

    page.locator('input[name="sender_name"]').fill(typed)

    provisional = page.locator("[data-provisional-addressee]")
    expect(provisional).to_have_count(1)
    expect(provisional).to_contain_text(typed)
    assert _addressee_labels(page)[0] == typed
    expect(provisional.locator("input")).to_be_checked()
    expect(page.locator("#id_addressee_name")).to_have_value(typed)
    # And nothing from the catalogue is selected, because the answer is the name.
    assert _chosen_addressees(page) == 0
    assert _summary(page) == f"Adressaat · {typed}"


def test_clearing_the_typed_sender_takes_the_answer_away_with_it(page, base_url):
    """And takes back only what it wrote. A name typed by hand is theirs."""
    create_form(page, base_url)

    sender_box = page.locator('input[name="sender_name"]')
    typed = a_new_name()
    sender_box.fill(typed)
    expect(page.locator("#id_addressee_name")).to_have_value(typed)

    sender_box.fill("")

    expect(page.locator("[data-provisional-addressee]")).to_have_count(0)
    expect(page.locator("#id_addressee_name")).to_have_value("")
    assert _summary(page) == "Adressaat"


def test_a_typed_sender_that_already_exists_answers_with_the_row(page, base_url):
    """One institution, one chip — and the chip is what gets chosen.

    `Uus saatja` is a box for a body the catalogue does not hold, but people
    type names into it that it does. A spelling already on the page must answer
    with that row rather than with a second chip saying the same word, or the
    form would be offering one institution twice.
    """
    create_form(page, base_url)

    page.locator('input[name="sender_name"]').fill(MINISTRY)

    expect(page.locator("[data-provisional-addressee]")).to_have_count(0)
    assert _chosen_addressees(page) == 1
    assert _summary(page) == f"Adressaat · {MINISTRY}"
    expect(page.locator("#id_addressee_name")).to_have_value("")


def test_choosing_a_real_addressee_releases_the_typed_one(page, base_url):
    """The two controls answer one question, so only one of them may hold it."""
    create_form(page, base_url)

    page.locator('input[name="sender_name"]').fill(a_new_name())
    expect(page.locator("[data-provisional-addressee] input")).to_be_checked()

    _pick_some_addressee(page)

    expect(page.locator("[data-provisional-addressee] input")).not_to_be_checked()
    expect(page.locator("#id_addressee_name")).to_have_value("")


# ---------------------------------------------------------------------------
# And it saves as one institution
# ---------------------------------------------------------------------------


def test_one_typed_name_used_for_both_saves_as_one_institution(page, base_url):
    """The end of the journey, driven the way somebody would drive it.

    `tests/test_counterparty_promotion.py` asserts the row count directly. This
    asserts what a person sees afterwards: the Teema names that institution as
    the sender *and* as the addressee, which is only possible if both relations
    point at one row.
    """
    create_form(page, base_url)
    typed = a_new_name()

    page.get_by_label("Pealkiri").fill("Vastus komisjonile")
    page.locator('input[name="sender_name"]').fill(typed)
    # Nothing is clicked under Adressaat: naming the sender is what answers it,
    # and this test is the journey somebody actually walks.
    page.get_by_role("button", name="Loo teema").click()
    page.wait_for_load_state("networkidle")

    body = page.locator("body")
    expect(body).to_contain_text("Vastus komisjonile")
    # Named on both sides of the record.
    assert body.inner_text().count(typed) >= 2, (
        "the new institution does not appear as both sender and addressee"
    )
