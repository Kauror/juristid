"""Õigusakt on Uus teema, measured in a browser.

The record a POST produces is `tests/test_oigusakt_field.py`'s. What only a
browser can settle is here: that the new row is *between* Menetlusliik and
Adressaat and full width at four widths, that ticking `Muu` opens the box and
untick­ing it closes it again, that the keyboard reaches and toggles a chip, and
that the page still does not scroll sideways with one more wrapping row on it.

The acceptance criteria this file works from are §15 of
`docs/oigusakt-uus-teema-design.md`. Where a criterion is about markup rather
than geometry — "the row contains exactly one fieldset", "no `details`" — it is
asserted here anyway, because a criterion split across two suites is a criterion
that gets half-checked.
"""

from __future__ import annotations

import pytest
from playwright.sync_api import expect

from e2e.conftest import MARTIN, sign_in

pytestmark = pytest.mark.e2e

CREATE_PATH = "/teemad/uus/"

#: Each field addressed through a control only it contains, so a chip or a
#: legend moving inside one of them does not rename it here.
INSTRUMENTS = 'fieldset.field:has(input[name="legal_instruments"])'
TRACK = 'fieldset.field:has(input[name="track"])'
ADDRESSEE_DISCLOSURE = "[data-addressee-disclosure]"

INSTRUMENTS_ROW = f".createform__row:has({INSTRUMENTS})"
TRACK_ROW = f".createform__row:has({TRACK})"
ADDRESSEE_ROW = f".createform__row:has({ADDRESSEE_DISCLOSURE})"

MUU_CHIP = "#oigusakt-muu"
MUU_BOX = "#oigusakt-muu-tekst"


def _open(page, base_url, width: int = 1440) -> None:
    sign_in(page, base_url, MARTIN)
    page.set_viewport_size({"width": width, "height": 900})
    page.goto(f"{base_url}{CREATE_PATH}")
    page.wait_for_load_state("networkidle")


def _box(page, selector: str) -> dict:
    box = page.locator(selector).first.bounding_box()
    assert box is not None, f"{selector} has no box"
    return box


# ---------------------------------------------------------------------------
# Placement
# ---------------------------------------------------------------------------


def test_the_row_is_between_menetlusliik_and_adressaat(page, base_url):
    """The approved placement, as vertical position rather than as source order.

    §15 criterion 2. Measured rather than read off the DOM because a row can be
    a later sibling and still paint above — a CSS `order` or a grid placement
    would do it — and where somebody reads it is the decision.
    """
    _open(page, base_url)

    track = _box(page, TRACK_ROW)
    instruments = _box(page, INSTRUMENTS_ROW)
    addressee = _box(page, ADDRESSEE_ROW)

    assert track["y"] + track["height"] <= instruments["y"] + 2, (
        "Õigusakt does not begin below Menetlusliik"
    )
    assert instruments["y"] + instruments["height"] <= addressee["y"] + 2, (
        "Õigusakt does not end above Adressaat"
    )


def test_no_existing_row_was_rearranged_to_make_room(page, base_url):
    """§15 criterion 17, as far as a browser can state it.

    The four classification rows still read in the intended order, and Adressaat
    is still the row after them. What this cannot see — that no row was
    re-paired or re-tracked — `e2e/test_uus_teema_row_composition.py` owns.
    """
    _open(page, base_url)

    tops = [
        _box(page, f".createform__row:has({selector})")["y"]
        for selector in (
            'input[name="policy_areas"]',
            'input[name="stage"]',
            'input[name="track"]',
            'input[name="legal_instruments"]',
            "[data-addressee-disclosure]",
        )
    ]
    assert tops == sorted(tops), f"the classification rows read out of order: {tops}"


@pytest.mark.parametrize("width", [1440, 1024, 768, 420])
def test_the_row_is_full_width_and_holds_one_field(page, base_url, width):
    """§15 criteria 3 and 4.

    Full width at every width because the row is a plain `.createform__row` and
    not a grid — which is a large part of why this placement was chosen: it has
    nothing to stack and no breakpoint of its own.
    """
    _open(page, base_url, width)

    row = _box(page, INSTRUMENTS_ROW)
    field = _box(page, INSTRUMENTS)

    assert abs(field["width"] - row["width"]) <= 2, (
        f"Õigusakt is {field['width']}px inside a {row['width']}px row at {width}px"
    )
    element = page.locator(INSTRUMENTS_ROW).first
    assert element.evaluate("node => node.children.length") == 1, (
        "the row shares itself with another field"
    )
    assert (
        element.evaluate(
            "node => node.classList.contains('createform__pair')"
            " || node.classList.contains('createform__trio')"
        )
        is False
    )


# ---------------------------------------------------------------------------
# The control
# ---------------------------------------------------------------------------


def test_the_control_is_checkbox_chips_with_the_multi_select_affordances(page, base_url):
    """§15 criteria 5 and 7 — and the asymmetry with Menetlusliik above it.

    The count and the clear marks are what say *this one holds several*, and
    Menetlusliik having neither is what stops the two rows reading as one
    question split in two (design §4, §6).
    """
    _open(page, base_url)

    field = page.locator(INSTRUMENTS)
    expect(field.locator("legend.field__label")).to_have_count(1)
    expect(field.locator("div.chiprow")).to_have_count(1)
    expect(field.locator('input[type="radio"]')).to_have_count(0)
    expect(field.locator("select")).to_have_count(0)
    expect(field.locator("details")).to_have_count(0)

    boxes = field.locator('input[type="checkbox"]')
    assert boxes.count() >= 12, "the reviewed vocabulary is not on the page"
    expect(field.locator("span.field__count[data-chipcount-for]")).to_have_count(1)
    expect(field.locator("span.chip__clear")).to_have_count(boxes.count())

    track = page.locator(TRACK)
    expect(track.locator("span.field__count")).to_have_count(0)
    expect(track.locator("span.chip__clear")).to_have_count(0)


def test_every_option_is_visible_at_rest_and_muu_is_last(page, base_url):
    """§15 criteria 6 and 8. No disclosure, nothing hidden, `Muu` at the end."""
    _open(page, base_url)

    chips = page.locator(f"{INSTRUMENTS} label.chip")
    total = chips.count()
    for index in range(total):
        expect(chips.nth(index)).to_be_visible()

    last = chips.nth(total - 1)
    assert "chip--other" in (last.get_attribute("class") or "")
    expect(last).to_have_text("Muu×")
    expect(page.locator(f"{INSTRUMENTS} label.chip--other")).to_have_count(1)


def test_the_count_reads_the_number_chosen(page, base_url):
    """§15 criterion 7, and the order rule of §7 in the same pass.

    The wording is the existing island's, not this field's. §13 of the design
    draws the count as `·3`; `app.js` has written «3 valitud» since Valdkonnad
    gained the affordance, and this row reuses that island unchanged rather than
    giving one field on the page a count that reads differently from the other.
    The design's claim is about *there being* a count; the words belong to the
    control both fields share (`static/js/app.js` `bindChipCounts`).
    """
    _open(page, base_url)

    chips = page.locator(f"{INSTRUMENTS} label.chip")

    def labels() -> list[str]:
        """The chip words, without the clear mark.

        `×` is stripped because it is *supposed* to appear when a chip is
        chosen — that is the affordance §8 requires — and comparing raw text
        would make this test fail for the thing it is not about.
        """
        return [
            chips.nth(index).inner_text().replace("×", "").strip()
            for index in range(chips.count())
        ]

    before = labels()

    count = page.locator(f'{INSTRUMENTS} [data-chipcount-for="legal_instruments"]')
    expect(count).to_have_text("")

    for index in (0, 1, 4):
        chips.nth(index).click()

    expect(count).to_have_text("3 valitud")
    areas = page.locator('[data-chipcount-for="policy_areas"]')
    assert areas.inner_text().strip() == "", "Valdkonnad counted this field's chips"

    assert labels() == before, "chips reordered themselves when they were chosen"


# ---------------------------------------------------------------------------
# Muu
# ---------------------------------------------------------------------------


def test_muu_reveals_and_hides_its_box(page, base_url):
    """§15 criterion 9."""
    _open(page, base_url)

    box = page.locator(MUU_BOX)
    expect(box).to_be_hidden()

    page.locator(MUU_CHIP).click()
    expect(box).to_be_visible()
    expect(box.locator("span.field__label")).to_have_text("Õigusakti liik")
    assert _box(page, MUU_BOX)["width"] <= 30 * 16 + 2, "the reveal is wider than 30rem"

    page.locator(MUU_CHIP).click()
    expect(box).to_be_hidden()


def test_the_reveal_sits_directly_under_the_chip_row(page, base_url):
    _open(page, base_url)
    page.locator(MUU_CHIP).click()

    chiprow = _box(page, f"{INSTRUMENTS} div.chiprow")
    reveal = _box(page, MUU_BOX)

    assert reveal["y"] >= chiprow["y"] + chiprow["height"] - 2
    assert reveal["x"] <= chiprow["x"] + 2, "the reveal is indented away from the row"


def test_a_refused_muu_save_comes_back_open_with_the_error_showing(page, base_url):
    """§15 criterion 14, and design §9's refused state.

    The whole point of rendering the reveal open on the server: the message
    saying why the save was refused is inside it.
    """
    _open(page, base_url)

    page.fill('input[name="title"]', "Refused Õigusakt")
    page.locator(MUU_CHIP).click()
    page.get_by_role("button", name="Loo teema").click()
    page.wait_for_load_state("networkidle")

    expect(page.locator(MUU_BOX)).to_be_visible()
    expect(page.locator(f"{MUU_BOX} span.field__error")).to_be_visible()
    expect(page.locator(f'{MUU_CHIP} input[type="checkbox"]')).to_be_checked()


# ---------------------------------------------------------------------------
# Keyboard and focus
# ---------------------------------------------------------------------------


def test_space_toggles_a_focused_chip_and_the_ring_is_visible(page, base_url):
    """§15 criteria 11 and 12."""
    _open(page, base_url)

    first = page.locator(f'{INSTRUMENTS} input[type="checkbox"]').first
    first.focus()
    page.keyboard.press(" ")
    expect(first).to_be_checked()
    page.keyboard.press(" ")
    expect(first).not_to_be_checked()

    outline = first.evaluate(
        "node => getComputedStyle(node.nextElementSibling).outlineStyle"
        " + ' ' + getComputedStyle(node.nextElementSibling).outlineWidth"
    )
    assert "none" not in outline, f"the focus ring is suppressed: {outline!r}"


def test_the_clear_mark_adds_no_tab_stop(page, base_url):
    """§15 criterion 12. `×` is decorative and never another thing to tab past."""
    _open(page, base_url)

    marks = page.locator(f"{INSTRUMENTS} span.chip__clear")
    assert marks.count() > 0
    for index in range(marks.count()):
        mark = marks.nth(index)
        assert mark.get_attribute("aria-hidden") == "true"
        assert mark.get_attribute("tabindex") is None
        assert mark.evaluate("node => node.tagName") == "SPAN"


# ---------------------------------------------------------------------------
# Responsive
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("width", [1440, 1024, 768, 420])
def test_the_page_never_scrolls_sideways_with_the_row_on_it(page, base_url, width):
    """§15 criterion 15, restated for the row this branch inserted."""
    _open(page, base_url, width)

    overflows = page.evaluate(
        "() => document.documentElement.scrollWidth > document.documentElement.clientWidth + 1"
    )
    assert not overflows, f"Uus teema scrolls the page sideways at {width}px"


@pytest.mark.parametrize("width", [1440, 1024, 768, 420])
def test_the_chips_wrap_rather_than_truncate_or_scroll(page, base_url, width):
    """Labels stay whole at every width, and the row scrolls nowhere."""
    _open(page, base_url, width)

    row = page.locator(f"{INSTRUMENTS} div.chiprow")
    assert row.evaluate("node => getComputedStyle(node).flexWrap") == "wrap"
    assert row.evaluate("node => node.scrollWidth <= node.clientWidth + 1"), (
        f"the chip row scrolls sideways at {width}px"
    )

    chips = page.locator(f"{INSTRUMENTS} label.chip span.chip__name")
    for index in range(chips.count()):
        assert chips.nth(index).evaluate(
            "node => getComputedStyle(node).textOverflow !== 'ellipsis'"
            " && node.scrollWidth <= node.clientWidth + 1"
        ), f"a chip label is truncated at {width}px"


def test_no_disclosure_appears_or_disappears_with_width(page, base_url):
    """The field has no responsive disclosure at any width (design §11)."""
    for width in (1440, 1024, 768, 420):
        _open(page, base_url, width)
        expect(page.locator(f"{INSTRUMENTS} details")).to_have_count(0)
        expect(page.locator(f"{INSTRUMENTS} label.chip").first).to_be_visible()
