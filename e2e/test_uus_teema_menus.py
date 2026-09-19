"""`Valdkonnad` and `Hetkeseis` as menus, in the browser (docs/adr/0094 §2).

`tests/test_uus_teema_ux_corrections.py` pins what the server renders, binds and
writes. Four things it cannot say, and they are the whole reason the lawyers
asked for this change:

* **opening a menu must not move the form.** That is a claim about layout, and
  a class name in the markup is not one — a stylesheet that stopped positioning
  the panel would leave every server-side assertion green and put the defect
  straight back. So it is measured: the y-position of the field *after* the menu,
  before and after opening it;
* **Escape, a click outside, and closing on an answer** are script, and a script
  either runs or it does not;
* **a multi-select menu has to survive being answered** — several times, with the
  earlier answers intact;
* **it has to work at 375px**, which is the only width where a compact control
  can turn out to be a cramped one.

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
    HETKESEIS_MENU,
    MARTIN,
    VALDKONNAD_MENU,
    open_hetkeseis,
    open_valdkond,
    sign_in,
)

pytestmark = pytest.mark.e2e

CREATE_PATH = "/teemad/uus/"

#: Every width the round was asked to hold. 375 and 420 are phones, 768 is the
#: tablet breakpoint, 1440 is the department's laptop.
WIDTHS = [375, 420, 768, 1440]

AREAS = 'input[name="policy_areas"]'
STAGES = 'input[name="stage"]'


def create_form(page, base_url, width: int = 1440) -> None:
    sign_in(page, base_url, MARTIN)
    page.set_viewport_size({"width": width, "height": 900})
    page.goto(f"{base_url}{CREATE_PATH}")
    page.wait_for_load_state("networkidle")
    expect(page.get_by_role("heading", name="Uus teema")).to_be_visible()


def document_top(page, selector: str) -> float:
    """Where an element sits on the *document*, not in the viewport.

    Scroll-independent on purpose: opening a menu near the bottom of a tall form
    can move the viewport without moving anything on the page, and a viewport
    coordinate would report that as the form having shifted.
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


def trigger_of(page, menu: str):
    return page.locator(menu).locator("> summary")


def is_open(page, menu: str) -> bool:
    return page.locator(menu).evaluate("node => node.open")


# ---------------------------------------------------------------------------
# The measurable promise: a menu overlays, a fold does not
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("width", WIDTHS)
def test_opening_valdkonnad_does_not_push_the_form_down(page, base_url, width):
    """The whole complaint, as one number.

    `Valdkonnad` was a `<details>` whose contents folded into the page: opening
    it pushed `Hetkeseis`, `Õigusakt`, `Menetluse link` and the submit button
    down while the reader was still ticking boxes, and shutting it pulled them
    back up. The vocabulary is twenty-two chips, so the movement was hundreds of
    pixels.

    `Hetkeseis` is the field immediately below, so it is the one that would have
    moved. `Loo teema` is checked too, because a rule that only held for the
    nearest neighbour would not be an overlay at all.
    """
    create_form(page, base_url, width)

    before = (document_top(page, HETKESEIS_MENU), document_top(page, ".createform__actions"))
    open_valdkond(page)
    expect(page.locator(VALDKONNAD_MENU)).to_have_attribute("open", "")
    after = (document_top(page, HETKESEIS_MENU), document_top(page, ".createform__actions"))

    assert after == pytest.approx(before, abs=1.0), (
        f"opening Valdkonnad moved the form at {width}px: {before} -> {after}"
    )


@pytest.mark.parametrize("width", WIDTHS)
def test_opening_hetkeseis_does_not_push_the_form_down(page, base_url, width):
    """The same promise for the eleven stages, measured on what follows them."""
    create_form(page, base_url, width)

    instruments = '.createform__row:has(input[name="legal_instruments"])'
    before = (document_top(page, instruments), document_top(page, ".createform__actions"))
    open_hetkeseis(page)
    expect(page.locator(HETKESEIS_MENU)).to_have_attribute("open", "")
    after = (document_top(page, instruments), document_top(page, ".createform__actions"))

    assert after == pytest.approx(before, abs=1.0), (
        f"opening Hetkeseis moved the form at {width}px: {before} -> {after}"
    )


def test_the_panel_really_is_over_the_field_below_it(page, base_url, screenshots):
    """Not merely «the form did not move» — the panel has to be *on top*.

    A panel rendered with zero height, or clipped to nothing, would satisfy the
    measurement above and be no use to anybody. So this asserts the opposite
    error: the open panel has a real box, and that box overlaps the row it is
    covering.

    This is also where the **open** state is photographed into the CI artifact
    directory, because a `uus-teema` baseline can only ever show the page at
    rest and the open panel is the whole change (docs/adr/0094 §2).
    """
    create_form(page, base_url)
    open_valdkond(page)

    panel = page.locator(f"{VALDKONNAD_MENU} .chipmenu__panel").bounding_box()
    below = page.locator(HETKESEIS_MENU).bounding_box()
    assert panel is not None and below is not None
    assert panel["height"] > 40, f"the open panel is {panel['height']}px tall"
    assert panel["y"] + panel["height"] > below["y"], (
        "the open panel stops above the field it is meant to cover"
    )
    screenshots(page, "valdkonnad-menuu-avatud")

    page.keyboard.press("Escape")
    open_hetkeseis(page)
    screenshots(page, "hetkeseis-menuu-avatud")


# ---------------------------------------------------------------------------
# Valdkonnad — several answers in one visit
# ---------------------------------------------------------------------------


def test_several_areas_can_be_ticked_without_the_menu_closing(page, base_url):
    """The point of a multi-select menu, and the thing a single-select must not do."""
    create_form(page, base_url)
    open_valdkond(page)

    boxes = page.locator(AREAS)
    if boxes.count() < 3:
        pytest.skip("this world has fewer than three policy areas")

    for index in range(3):
        boxes.nth(index).click()
        assert is_open(page, VALDKONNAD_MENU), f"the menu closed after ticking area {index}"

    for index in range(3):
        expect(boxes.nth(index)).to_be_checked()
    assert "· 3" in (trigger_of(page, VALDKONNAD_MENU).inner_text() or "")


def test_unticking_one_area_leaves_the_others(page, base_url):
    """Selecting an already-selected option removes it, and only it."""
    create_form(page, base_url)
    open_valdkond(page)

    boxes = page.locator(AREAS)
    if boxes.count() < 3:
        pytest.skip("this world has fewer than three policy areas")

    for index in range(3):
        boxes.nth(index).click()
    boxes.nth(1).click()

    expect(boxes.nth(0)).to_be_checked()
    expect(boxes.nth(1)).not_to_be_checked()
    expect(boxes.nth(2)).to_be_checked()
    assert "· 2" in (trigger_of(page, VALDKONNAD_MENU).inner_text() or "")


def test_reopening_shows_what_is_already_chosen(page, base_url):
    """Shutting a menu is not answering it again."""
    create_form(page, base_url)
    open_valdkond(page)

    first = page.locator(AREAS).first
    first.click()
    trigger_of(page, VALDKONNAD_MENU).click()
    assert not is_open(page, VALDKONNAD_MENU)

    open_valdkond(page)
    expect(first).to_be_checked()
    assert "· 1" in (trigger_of(page, VALDKONNAD_MENU).inner_text() or "")


# ---------------------------------------------------------------------------
# Hetkeseis — one answer, and the question is over
# ---------------------------------------------------------------------------


def test_picking_a_stage_closes_the_menu_and_names_it_on_the_trigger(page, base_url):
    """A single-select menu is answered once, so it shuts itself."""
    create_form(page, base_url)
    open_hetkeseis(page)

    radios = page.locator(STAGES)
    if radios.count() < 3:
        pytest.skip("this world offers fewer than two real stages")

    # Index 0 is the named blank option; the ones after it are real stages.
    label = (
        page.locator('label.chip:has(input[name="stage"]), span.chip:has(input[name="stage"])')
        .nth(2)
        .locator(".chip__name")
        .inner_text()
    ).strip()
    radios.nth(2).click()

    assert not is_open(page, HETKESEIS_MENU), "the single-select menu stayed open once answered"
    assert label and label in (trigger_of(page, HETKESEIS_MENU).inner_text() or "")


def test_a_second_stage_replaces_the_first(page, base_url):
    """One value, which is what a radio group already promises.

    Reopened between the two picks, because the menu closed itself on the first
    — which is the assertion directly above and the reason this one has to say
    so out loud.
    """
    create_form(page, base_url)

    radios = page.locator(STAGES)
    if radios.count() < 4:
        pytest.skip("this world offers fewer than three real stages")

    open_hetkeseis(page)
    radios.nth(1).click()
    open_hetkeseis(page)
    radios.nth(2).click()

    expect(radios.nth(2)).to_be_checked()
    expect(radios.nth(1)).not_to_be_checked()


def test_the_stage_trigger_starts_on_maaramata(page, base_url):
    """«Määramata» is a real answer and the one a fresh form holds.

    Nothing is invented: the trigger reports the option the form itself has
    selected. A blank trigger over a selected «Määramata» would hide the state
    most files are actually in (docs/adr/0094 §2.2).
    """
    create_form(page, base_url)

    assert "Määramata" in (trigger_of(page, HETKESEIS_MENU).inner_text() or "")
    expect(page.locator(STAGES).first).to_be_checked()


# ---------------------------------------------------------------------------
# Opening and closing
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("menu", [VALDKONNAD_MENU, HETKESEIS_MENU], ids=["valdkonnad", "hetkeseis"])
def test_escape_closes_the_menu_and_gives_the_trigger_back_the_cursor(page, base_url, menu):
    """Otherwise a keyboard user who opened it by mistake tabs through
    twenty-two checkboxes to get out."""
    create_form(page, base_url)

    trigger = trigger_of(page, menu)
    trigger.click()
    assert is_open(page, menu)

    page.keyboard.press("Escape")
    page.wait_for_timeout(120)

    assert not is_open(page, menu)
    assert page.evaluate(
        "s => document.activeElement"
        " === document.querySelector(s).querySelector(':scope > summary')",
        menu,
    ), "Escape did not put the cursor back on the trigger"


@pytest.mark.parametrize("menu", [VALDKONNAD_MENU, HETKESEIS_MENU], ids=["valdkonnad", "hetkeseis"])
def test_a_click_outside_closes_the_menu(page, base_url, menu):
    """A fold may be left open; a menu left open covers the form under it."""
    create_form(page, base_url)

    trigger_of(page, menu)
    trigger_of(page, menu).click()
    assert is_open(page, menu)

    page.get_by_role("heading", name="Uus teema").click()
    page.wait_for_timeout(120)

    assert not is_open(page, menu), "the menu stayed open over the form"


@pytest.mark.parametrize("menu", [VALDKONNAD_MENU, HETKESEIS_MENU], ids=["valdkonnad", "hetkeseis"])
def test_the_trigger_opens_from_the_keyboard_and_reports_its_state(page, base_url, menu):
    """One tab stop, Enter and Space, and an expanded state something can read.

    `aria-expanded` is written by the script rather than by the template, so
    that it exists only where something is keeping it true: a `<summary>`
    already exposes the state natively, and a server-rendered copy would go
    stale the moment somebody toggled the element with scripting off
    (docs/adr/0094 §2).
    """
    create_form(page, base_url)

    trigger = trigger_of(page, menu)
    expect(trigger).to_have_attribute("aria-expanded", "false")

    trigger.focus()
    page.keyboard.press("Enter")
    assert is_open(page, menu)
    expect(trigger).to_have_attribute("aria-expanded", "true")

    page.keyboard.press("Escape")
    page.wait_for_timeout(120)
    expect(trigger).to_have_attribute("aria-expanded", "false")

    page.keyboard.press(" ")
    assert is_open(page, menu)
    expect(trigger).to_have_attribute("aria-expanded", "true")


def test_an_open_panel_really_does_cover_the_control_under_it(page, base_url):
    """The overlay, asserted from the direction that makes it inconvenient.

    An open `Valdkonnad` panel covers the `Hetkeseis` trigger, so that trigger
    cannot be clicked *through* it — a click there lands on the panel, which is
    what an overlay means and what every dropdown does. Stated here because it
    is the one behaviour of this design that costs the reader something, and
    because a test that could click through would be evidence the panel was not
    over anything.

    The way out is the way out of any menu: Escape, or a click somewhere that is
    not the panel. Both are asserted above, and both are what the next test
    walks.
    """
    create_form(page, base_url)
    open_valdkond(page)

    covered = page.evaluate(
        """() => {
            const trigger = document.querySelector(
              'details.chipmenu[data-chipmenu-single] > summary');
            const box = trigger.getBoundingClientRect();
            const hit = document.elementFromPoint(
              box.left + box.width / 2, box.top + box.height / 2);
            return hit === trigger ? null : (hit && hit.className) || 'nothing';
        }"""
    )
    assert covered is not None, "the open panel is not over the field below it"


def test_answering_one_menu_and_then_the_other_is_two_ordinary_steps(page, base_url):
    """The journey a lawyer actually walks, with nothing clicked through.

    Tick the areas, dismiss the menu, open the next one, answer it. Escape is
    used for the dismissal because it is the keyboard route and the one that
    also returns the cursor; a click on the page background does the same and is
    asserted separately.
    """
    create_form(page, base_url)

    areas = page.locator(AREAS)
    if not areas.count():
        pytest.skip("this world has no policy areas")

    open_valdkond(page)
    areas.first.click()
    page.keyboard.press("Escape")
    page.wait_for_timeout(120)
    assert not is_open(page, VALDKONNAD_MENU)

    radios = page.locator(STAGES)
    if radios.count() < 3:
        pytest.skip("this world offers fewer than two real stages")

    open_hetkeseis(page)
    radios.nth(2).click()
    page.wait_for_timeout(120)

    assert not is_open(page, HETKESEIS_MENU)
    expect(radios.nth(2)).to_be_checked()
    # And the first answer survived being left behind.
    expect(areas.first).to_be_checked()
    assert "· 1" in (trigger_of(page, VALDKONNAD_MENU).inner_text() or "")


# ---------------------------------------------------------------------------
# Narrow widths
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("width", WIDTHS)
def test_an_open_menu_never_takes_the_document_sideways(page, base_url, width):
    """The failure an overlay makes easy and a fold made impossible.

    A panel wider than its row scrolls the *document* horizontally without
    lengthening it, so nothing that measures height would notice — and a
    horizontally scrolling document is the one thing no surface here may
    produce (QA-08).
    """
    create_form(page, base_url, width)
    assert overflow(page) <= 1, f"the page already scrolls sideways at {width}px"

    # One at a time, and shut between: an open panel covers the trigger below
    # it, so clicking straight from one menu to the next lands on the panel
    # rather than on the trigger — which is what an overlay means.
    for menu in (VALDKONNAD_MENU, HETKESEIS_MENU):
        trigger_of(page, menu).click()
        page.wait_for_timeout(80)
        assert is_open(page, menu)
        assert overflow(page) <= 1, f"an open menu scrolls the page sideways at {width}px"
        page.keyboard.press("Escape")
        page.wait_for_timeout(80)


@pytest.mark.parametrize("width", [375, 420])
def test_both_triggers_and_an_open_panel_stay_inside_a_phone(page, base_url, width):
    """On the screen and within it, rather than merely attached to it."""
    create_form(page, base_url, width)

    for menu in (VALDKONNAD_MENU, HETKESEIS_MENU):
        trigger = trigger_of(page, menu).bounding_box()
        assert trigger is not None
        assert trigger["x"] >= -1 and trigger["x"] + trigger["width"] <= width + 1, (
            f"a trigger hangs off the screen at {width}px: {trigger}"
        )

    open_valdkond(page)
    panel = page.locator(f"{VALDKONNAD_MENU} .chipmenu__panel").bounding_box()
    assert panel is not None
    assert panel["x"] >= -1 and panel["x"] + panel["width"] <= width + 1, (
        f"the open panel hangs off the screen at {width}px: {panel}"
    )

    # And the chips inside it are reachable: a panel that fits by clipping its
    # own contents is a panel nobody can answer.
    first = page.locator(f"{VALDKONNAD_MENU} label.chip").first.bounding_box()
    assert first is not None
    assert first["width"] > 0 and first["height"] >= 24, first


@pytest.mark.parametrize("width", [375, 420])
def test_a_long_vocabulary_scrolls_inside_its_own_panel(page, base_url, width):
    """Bounded height and internal scrolling, rather than a panel past the fold.

    `Hetkeseis` deliberately has no cap — a scroll container clips the
    `.stagehelp` bubbles hanging off its chips — so this is asserted of
    `Valdkonnad` alone, which is where the twenty-two chips are.
    """
    create_form(page, base_url, width)
    open_valdkond(page)

    row = page.locator(f"{VALDKONNAD_MENU} .chipmenu__panel .chiprow")
    metrics = row.evaluate(
        "node => ({ client: node.clientHeight, scroll: node.scrollHeight,"
        "  overflow: getComputedStyle(node).overflowY })"
    )
    assert metrics["overflow"] in ("auto", "scroll"), metrics
    assert metrics["client"] <= 200, metrics
    if metrics["scroll"] > metrics["client"]:
        row.evaluate("node => { node.scrollTop = node.scrollHeight; }")
        assert row.evaluate("node => node.scrollTop") > 0, "the chip row does not actually scroll"
