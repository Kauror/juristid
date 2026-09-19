"""`Valdkonnad` and `Hetkeseis`, drawn at rest, in the browser (docs/adr/0096 §2).

This file used to measure a menu: that opening one overlaid the form, that
Escape and a click outside closed it, and that a multi-select survived being
answered. The owner's live audit reversed that shape — the lawyers asked for the
visible chips back — so it measures the promise the new shape makes instead.

The filename is kept on purpose. `ci_sharding.partition` splits this suite by
file, so renaming one re-partitions every shard and changes which scenarios
share a world; that has cost CI rounds before and would cost them here for a
cosmetic gain.

`tests/test_uus_teema_ux_corrections.py` pins what the server renders, binds and
writes. Four things it cannot say, and they are why this file exists:

* **every choice is clickable without anything being opened first.** Markup is
  not layout: a stylesheet that clipped the row to nothing, or a `display: none`
  left behind by the retired menu, would leave every server-side assertion green
  and put the click straight back;
* **answering one control does not move the next.** That was the lawyers'
  original complaint about the fold, and it is the one property both the menu
  and this shape have to keep — a chip row does not grow when a box is ticked,
  and this is what proves it;
* **several areas can be ticked, and unticking one leaves the rest**;
* **it has to work at 375px**, which is the only width where a wrapped chip row
  can turn out to be a horizontal scrollbar.

What is deliberately *not* here: the vocabulary, the withdrawals, the `Muu`
semantics and what a POST stores. Those have owners
(`e2e/test_uus_teema_manual_first.py`, `tests/test_uus_teema_redesign.py`), and
restating them in a browser is paying fourteen minutes for an answer a test
client gives in milliseconds.
"""

from __future__ import annotations

import pytest
from playwright.sync_api import expect

from e2e.conftest import (
    HETKESEIS_FIELD,
    MARTIN,
    VALDKONNAD_FIELD,
    sign_in,
)

pytestmark = pytest.mark.e2e

CREATE_PATH = "/teemad/uus/"

#: Every width the round was asked to hold. 375 and 420 are phones, 768 is the
#: tablet breakpoint, 1440 is the department's laptop.
WIDTHS = [375, 420, 768, 1440]

AREAS = 'input[name="policy_areas"]'
STAGES = 'input[name="stage"]'
INSTRUMENTS = 'input[name="legal_instruments"]'
OIGUSAKT_FIELD = 'fieldset:has(> .chiprow input[name="legal_instruments"])'


def create_form(page, base_url, width: int = 1440) -> None:
    sign_in(page, base_url, MARTIN)
    page.set_viewport_size({"width": width, "height": 900})
    page.goto(f"{base_url}{CREATE_PATH}")
    page.wait_for_load_state("networkidle")
    expect(page.get_by_role("heading", name="Uus teema")).to_be_visible()


def document_top(page, selector: str) -> float:
    """Where an element sits on the *document*, not in the viewport.

    Scroll-independent on purpose: clicking a chip near the bottom of a tall
    form can move the viewport without moving anything on the page, and a
    viewport coordinate would report that as the form having shifted.
    """
    top = page.evaluate(
        "s => { const n = document.querySelector(s);"
        "  return n ? n.getBoundingClientRect().top + window.scrollY : null; }",
        selector,
    )
    assert top is not None, f"{selector} is not on the page"
    return top


def overflow(page) -> float:
    return page.evaluate(
        "() => document.documentElement.scrollWidth - document.documentElement.clientWidth"
    )


# ---------------------------------------------------------------------------
# The promise: no click stands between a reader and an answer
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("width", WIDTHS)
def test_every_classification_is_readable_without_opening_anything(page, base_url, width):
    """The whole of the correction, at every width.

    Not the absence of a class name — the server suite owns that — but the
    thing a class name is supposed to produce: the group is on the screen, its
    first choice is on the screen, and nothing had to be pressed to get there.
    """
    create_form(page, base_url, width)

    for field, control in (
        (VALDKONNAD_FIELD, AREAS),
        (HETKESEIS_FIELD, STAGES),
    ):
        block = page.locator(field).first
        expect(block).to_be_visible()
        chips = page.locator(f"{field} .chip").first
        expect(chips).to_be_visible()
        assert page.locator(control).count() > 0


def test_a_chip_can_be_ticked_straight_away(page, base_url):
    """One click, not two. The click that was removed is the whole feature."""
    create_form(page, base_url)

    areas = page.locator(AREAS)
    if not areas.count():
        pytest.skip("this world has no policy areas")

    areas.first.click()
    expect(areas.first).to_be_checked()


def test_the_group_is_named_where_a_reader_can_see_it(page, base_url):
    """The legend is visible again.

    It was `visually-hidden` inside the menu's panel, because the trigger above
    already said the word. There is no trigger now, so hiding it would leave a
    row of chips with nothing naming them.
    """
    create_form(page, base_url)

    expect(page.locator(VALDKONNAD_FIELD).locator("legend").first).to_be_visible()
    expect(page.locator(HETKESEIS_FIELD).locator("legend").first).to_be_visible()


# ---------------------------------------------------------------------------
# Answering one control does not move the next
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("width", WIDTHS)
def test_ticking_an_area_does_not_move_the_field_below_it(page, base_url, width):
    """The lawyers' original complaint, kept as a guard against the next shape.

    The fold failed this — opening it pushed `Hetkeseis`, `Õigusakt` and the
    submit button down the page while somebody was still ticking boxes. The menu
    passed it by leaving the flow. A chip row passes it by not changing size,
    which is the cheapest way of all to pass it and the easiest to break with a
    `:checked` rule that changes a chip's padding.
    """
    create_form(page, base_url, width)

    areas = page.locator(AREAS)
    if not areas.count():
        pytest.skip("this world has no policy areas")

    before = document_top(page, HETKESEIS_FIELD)
    areas.first.click()
    page.wait_for_timeout(120)
    after = document_top(page, HETKESEIS_FIELD)

    assert abs(after - before) <= 1, f"{width}px: Hetkeseis moved {after - before}px"


def test_picking_a_stage_does_not_move_oigusakt(page, base_url):
    """The same rule one row down, on the single-select side."""
    create_form(page, base_url)

    radios = page.locator(STAGES)
    if radios.count() < 3:
        pytest.skip("this world offers fewer than two real stages")

    if not page.locator(INSTRUMENTS).count():
        pytest.skip("this world has no legal instrument vocabulary")

    # `document_top` runs `querySelector`, which is CSS and not Playwright's
    # selector language — `>> nth=0` is a syntax error there rather than a
    # miss. The fieldset is a plain CSS `:has()` and is the thing that would
    # move anyway.
    before = document_top(page, OIGUSAKT_FIELD)
    radios.nth(2).click()
    page.wait_for_timeout(120)
    after = document_top(page, OIGUSAKT_FIELD)

    expect(radios.nth(2)).to_be_checked()
    assert abs(after - before) <= 1


# ---------------------------------------------------------------------------
# Multi-select stays multi-select
# ---------------------------------------------------------------------------


def test_several_areas_can_be_ticked(page, base_url):
    """`Matter.policy_areas` holds several, so the control has to let you say so."""
    create_form(page, base_url)

    areas = page.locator(AREAS)
    if areas.count() < 3:
        pytest.skip("this world offers fewer than three policy areas")

    for index in range(3):
        areas.nth(index).click()

    for index in range(3):
        expect(areas.nth(index)).to_be_checked()


def test_unticking_one_area_leaves_the_others(page, base_url):
    """The half that a control which merely *accepts* several can still fail."""
    create_form(page, base_url)

    areas = page.locator(AREAS)
    if areas.count() < 3:
        pytest.skip("this world offers fewer than three policy areas")

    for index in range(3):
        areas.nth(index).click()
    areas.nth(1).click()

    expect(areas.nth(0)).to_be_checked()
    expect(areas.nth(1)).not_to_be_checked()
    expect(areas.nth(2)).to_be_checked()


def test_a_second_stage_replaces_the_first(page, base_url):
    """One value, and the control says so by being radios."""
    create_form(page, base_url)

    radios = page.locator(STAGES)
    if radios.count() < 3:
        pytest.skip("this world offers fewer than two real stages")

    radios.nth(1).click()
    radios.nth(2).click()

    expect(radios.nth(1)).not_to_be_checked()
    expect(radios.nth(2)).to_be_checked()


def test_the_muu_box_appears_beside_the_vocabulary_when_it_is_ticked(page, base_url):
    """`Muu` is a chip among the chips, and its box opens under them.

    The box lived outside the menu's panel, because a box inside a panel that
    shuts is a box nobody can finish. There is no panel now, so it sits in the
    fieldset it belongs to — and it still has to appear the moment the chip is
    ticked, which is what a person does next.
    """
    create_form(page, base_url)

    box = page.locator("#valdkond-muu-tekst")
    expect(box).to_be_hidden()

    page.locator("#valdkond-muu input[type=checkbox]").click()
    expect(box).to_be_visible()
    expect(page.locator("#id_policy_area_other")).to_be_visible()


# ---------------------------------------------------------------------------
# The keyboard
# ---------------------------------------------------------------------------


def test_the_vocabulary_is_walkable_from_the_keyboard(page, base_url):
    """A radio group is one tab stop and the arrows move within it.

    Native behaviour, and that is the point: what the menu added — Escape, a
    click outside, a trigger to focus — is behaviour a plain fieldset never
    needed, so removing the script removed nothing a keyboard depended on.
    """
    create_form(page, base_url)

    radios = page.locator(STAGES)
    if radios.count() < 3:
        pytest.skip("this world offers fewer than two real stages")

    radios.nth(1).focus()
    page.keyboard.press("ArrowDown")

    expect(radios.nth(2)).to_be_checked()


def test_an_area_can_be_ticked_with_the_space_bar(page, base_url):
    """The checkbox half of the same rule."""
    create_form(page, base_url)

    areas = page.locator(AREAS)
    if not areas.count():
        pytest.skip("this world has no policy areas")

    areas.first.focus()
    page.keyboard.press("Space")

    expect(areas.first).to_be_checked()


# ---------------------------------------------------------------------------
# Narrow widths
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("width", WIDTHS)
def test_the_chip_rows_never_take_the_document_sideways(page, base_url, width):
    """A wrapped row that does not wrap is a horizontal scrollbar.

    The failure mode a chip row makes easy: one long Estonian policy-area label
    that will not break, and the whole document scrolls sideways at 375px
    without anything looking wrong at 1440.
    """
    create_form(page, base_url, width)

    assert overflow(page) <= 1, f"{width}px: document overflows by {overflow(page)}px"


@pytest.mark.parametrize("width", WIDTHS)
def test_every_chip_stays_inside_the_viewport(page, base_url, width):
    """And no individual chip hangs off the right edge.

    Stronger than the document measurement above, which a chip clipped by an
    ancestor's `overflow: hidden` would pass while being unreadable.
    """
    create_form(page, base_url, width)

    widest = page.evaluate(
        "s => Math.max(0, ...Array.from(document.querySelectorAll(s))"
        "  .map(n => n.getBoundingClientRect().right))",
        f"{VALDKONNAD_FIELD} .chip, {HETKESEIS_FIELD} .chip",
    )
    assert widest <= width + 1, f"{width}px: a chip reaches {widest}px"


@pytest.mark.parametrize("width", [375, 420])
def test_the_whole_classification_block_is_reachable_on_a_phone(page, base_url, width):
    """Every chip can be scrolled to and clicked, not merely rendered.

    The measurement the retired panel needed a scroll cap for. Drawn in the flow
    there is nothing to cap — the page is simply taller — and the thing worth
    proving is that the last chip in the longest vocabulary is still clickable.
    """
    create_form(page, base_url, width)

    areas = page.locator(AREAS)
    if areas.count() < 2:
        pytest.skip("this world has fewer than two policy areas")

    last = areas.nth(areas.count() - 1)
    last.scroll_into_view_if_needed()
    last.click()

    expect(last).to_be_checked()
    assert overflow(page) <= 1
