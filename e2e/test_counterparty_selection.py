"""Saatja and Adressaat in a real browser, on one open form.

Everything here is about what happens *before* a round trip. The server does the
same ordering for a bound form — a refused save must not drop the sender back
under the historical shortlist — and `tests/test_counterparty_promotion.py`
pins that half. What only a browser can answer is whether ticking a chip moves
anything at all while the person is still filling the form in, and whether a
name they are in the middle of typing can be reused as the answer to the
question beside it.

Three properties, and the middle one is the one worth breaking a build over:

* the sender chosen here is offered first as the addressee;
* it is **not chosen for them**, and an addressee they picked by hand is not
  disturbed by anything they subsequently do to Saatja;
* the same typed name, used for both, is one institution when it is saved.
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


def create_form(page, base_url) -> None:
    sign_in(page, base_url, MARTIN)
    page.set_viewport_size({"width": 1440, "height": 900})
    page.goto(f"{base_url}{CREATE_PATH}")
    page.wait_for_load_state("networkidle")


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
# The sender chosen here comes first there
# ---------------------------------------------------------------------------


def test_choosing_a_sender_moves_it_to_the_front_of_adressaat(page, base_url):
    """The reported case, in the browser, before any round trip."""
    create_form(page, base_url)

    before = _addressee_labels(page)
    assert before, "no addressee chips rendered at all"

    _tick_sender(page, MINISTRY)

    after = _addressee_labels(page)
    assert after[0] == MINISTRY, (
        f"after choosing {MINISTRY} as Saatja the first Adressaat chip is {after[0]!r}"
    )


def test_the_promoted_addressee_is_offered_once_and_not_chosen(page, base_url):
    """Promoted, never selected — and never drawn twice.

    A copy would post `addressee_organisation` twice and leave the browser
    deciding which one counted; a move keeps one control per organisation, which
    is what the radio group's own cardinality requires.
    """
    create_form(page, base_url)
    _tick_sender(page, MINISTRY)

    ministry_radios = page.locator(f'{ADDRESSEE_QUICK} label.chip:has-text("{MINISTRY}") input')
    expect(ministry_radios).to_have_count(1)
    assert _chosen_addressees(page) == 0, (
        "choosing a sender selected an addressee, which nobody asked it to do"
    )


def test_an_addressee_chosen_by_hand_survives_a_change_of_sender(page, base_url):
    """The failure that would be silent, plausible and only visible on the record.

    Display order may change under somebody. Their answer may not.
    """
    create_form(page, base_url)

    chosen, _name = _pick_some_addressee(page)
    expect(chosen).to_be_checked()

    _tick_sender(page, MINISTRY)

    expect(chosen).to_be_checked()
    assert _addressee_labels(page)[0] == MINISTRY, "the promotion did not happen at all"


# ---------------------------------------------------------------------------
# A body that does not exist yet
# ---------------------------------------------------------------------------


def test_a_newly_typed_sender_is_offered_as_an_addressee(page, base_url):
    """No primary key, and still usable as the answer beside it.

    There is no `Organisation` row until `Loo teema` creates one, so this chip
    stands for a name rather than a record — and choosing it fills the
    free-text `Uus adressaat` control, which is the path that already exists for
    exactly this (task §14).
    """
    create_form(page, base_url)
    typed = a_new_name()

    page.locator('input[name="sender_name"]').fill(typed)

    provisional = page.locator("[data-provisional-addressee]")
    expect(provisional).to_be_visible()
    expect(provisional).to_contain_text(typed)
    assert _addressee_labels(page)[0] == typed


def test_choosing_the_typed_sender_as_addressee_fills_the_free_text_box(page, base_url):
    create_form(page, base_url)
    typed = a_new_name()

    page.locator('input[name="sender_name"]').fill(typed)
    page.locator("[data-provisional-addressee] input").click()

    expect(page.locator("#id_addressee_name")).to_have_value(typed)
    # And nothing from the catalogue is selected, because the answer is the name.
    assert _chosen_addressees(page) == 0


def test_clearing_the_typed_sender_takes_the_offer_away_with_it(page, base_url):
    """And takes back only what it wrote. A name typed by hand is theirs."""
    create_form(page, base_url)

    sender_box = page.locator('input[name="sender_name"]')
    typed = a_new_name()
    sender_box.fill(typed)
    page.locator("[data-provisional-addressee] input").click()
    expect(page.locator("#id_addressee_name")).to_have_value(typed)

    sender_box.fill("")

    expect(page.locator("[data-provisional-addressee]")).to_have_count(0)
    expect(page.locator("#id_addressee_name")).to_have_value("")


def test_a_typed_sender_that_already_exists_is_not_offered_twice(page, base_url):
    """One institution, one chip. The catalogue's own row is the offer."""
    create_form(page, base_url)

    page.locator('input[name="sender_name"]').fill(MINISTRY)

    expect(page.locator("[data-provisional-addressee]")).to_have_count(0)


def test_choosing_a_real_addressee_releases_the_typed_one(page, base_url):
    """The two controls answer one question, so only one of them may hold it."""
    create_form(page, base_url)

    page.locator('input[name="sender_name"]').fill(a_new_name())
    page.locator("[data-provisional-addressee] input").click()

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
    page.locator("[data-provisional-addressee] input").click()
    page.get_by_role("button", name="Loo teema").click()
    page.wait_for_load_state("networkidle")

    body = page.locator("body")
    expect(body).to_contain_text("Vastus komisjonile")
    # Named on both sides of the record.
    assert body.inner_text().count(typed) >= 2, (
        "the new institution does not appear as both sender and addressee"
    )
