"""Uus teema's rows, measured.

The change this file guards is composition only: `Andmeklass` is one checkbox,
and it used to take a 130px column out of the Adressaat row while the
Vastutaja/Saatja row above ran most of its width empty — Saatja is commonly one
or two chips. The checkbox moved into that empty part, and Adressaat got a whole
row.

No field was added, removed, renamed or reinterpreted, so nothing here asserts
behaviour: `test_addressee_free_entry.py` owns what Adressaat *does*, and
`test_matter_form_ux.py` owns how the choice controls read. This file owns only
where the boxes are, at four widths, and it asserts that with bounding boxes
rather than with a screenshot — a screenshot cannot say which row a field is on.
"""

from __future__ import annotations

import pytest

from e2e.conftest import MARTIN, sign_in

pytestmark = pytest.mark.e2e

CREATE_PATH = "/teemad/uus/"

#: Each field addressed through a control only it contains, so that a chip, a
#: legend or a disclosure moving inside one of them does not rename it here.
OWNER = 'fieldset.field:has(input[name="owner"])'
SENDER = 'fieldset.field:has(input[name="source_organisations"])'
DATA_CLASS = "fieldset.field:has(#andmeklass-test)"
ADDRESSEE = "fieldset.field:has(#id_addressee_name)"

#: The row element itself, whatever modifier it carries this month.
PEOPLE_ROW = f".createform__row:has({DATA_CLASS})"
ADDRESSEE_ROW = f".createform__row:has({ADDRESSEE})"


def create_form(page, base_url) -> None:
    page.goto(f"{base_url}{CREATE_PATH}")
    page.wait_for_load_state("networkidle")


def _box(page, selector: str) -> dict:
    box = page.locator(selector).first.bounding_box()
    assert box is not None, f"{selector} has no box"
    return box


def _open(page, base_url, width: int) -> None:
    sign_in(page, base_url, MARTIN)
    page.set_viewport_size({"width": width, "height": 900})
    create_form(page, base_url)


# ---------------------------------------------------------------------------
# Desktop — the three fields share one row, in one order
# ---------------------------------------------------------------------------


def test_vastutaja_saatja_and_andmeklass_share_one_row(page, base_url):
    """One row, and the same one.

    The three must begin on the same line and end inside the row that holds
    them. Their tops are compared to each other rather than to the row's own
    `y`, because `.createform__row` carries 11px of top padding and a border
    that every child starts below — the shared line is 12px into the row box,
    not at its edge.

    Not equal heights: the three are `align-items: start` in a grid track, so a
    wrapping Saatja is taller than a single checkbox and that is correct. What
    would not be correct is one of them beginning further down than the others,
    which is what a field pushed onto a second line looks like as a number.
    """
    _open(page, base_url, 1440)

    row = _box(page, PEOPLE_ROW)
    boxes = {
        name: _box(page, sel)
        for name, sel in (
            ("Vastutaja", OWNER),
            ("Saatja", SENDER),
            ("Andmeklass", DATA_CLASS),
        )
    }
    top = boxes["Vastutaja"]["y"]

    for name, box in boxes.items():
        assert abs(box["y"] - top) <= 2, (
            f"{name} starts {box['y'] - top}px below Vastutaja rather than beside it, "
            "which means it wrapped onto a second line instead of taking a column"
        )
        assert box["y"] >= row["y"] - 2, f"{name} starts above the row it shares"
        assert box["y"] + box["height"] <= row["y"] + row["height"] + 2, (
            f"{name} runs past the bottom of the row it is meant to share"
        )


def test_the_three_fields_read_left_to_right_in_the_intended_order(page, base_url):
    _open(page, base_url, 1440)

    owner = _box(page, OWNER)
    sender = _box(page, SENDER)
    data_class = _box(page, DATA_CLASS)

    assert owner["x"] + owner["width"] <= sender["x"] + 1, (
        f"Saatja starts at {sender['x']}px, inside a Vastutaja ending at "
        f"{owner['x'] + owner['width']}px"
    )
    assert sender["x"] + sender["width"] <= data_class["x"] + 1, (
        f"Andmeklass starts at {data_class['x']}px, inside a Saatja ending at "
        f"{sender['x'] + sender['width']}px"
    )


def test_saatja_is_the_widest_of_the_three(page, base_url):
    """The point of the move, stated as the thing that would undo it.

    Vastutaja is a fixed 246px and Andmeklass a fixed 130px; Saatja takes what
    is left. A future edit that handed one of the fixed tracks the flexible
    column would still keep all three on one row and still order them correctly,
    and would still be wrong.
    """
    _open(page, base_url, 1440)

    sender = _box(page, SENDER)["width"]
    owner = _box(page, OWNER)["width"]
    data_class = _box(page, DATA_CLASS)["width"]

    assert sender > owner, f"Saatja ({sender}px) is narrower than Vastutaja ({owner}px)"
    assert sender > data_class * 2, (
        f"Saatja ({sender}px) is not meaningfully wider than the Andmeklass "
        f"checkbox column ({data_class}px)"
    )


# ---------------------------------------------------------------------------
# Desktop — Adressaat is on its own row, and spends it
# ---------------------------------------------------------------------------


def test_adressaat_begins_on_a_row_below_and_takes_all_of_it(page, base_url):
    """Below, and undivided.

    The `130px` in the third assertion is the column Andmeklass used to hold:
    Adressaat is no longer paired with it, so it must be wider than the row less
    that column and its gap — which is what "gained the width" means as a number
    rather than as an adjective.
    """
    _open(page, base_url, 1440)

    people = _box(page, PEOPLE_ROW)
    row = _box(page, ADDRESSEE_ROW)
    field = _box(page, ADDRESSEE)

    assert row["y"] >= people["y"] + people["height"] - 2, (
        "the Adressaat row does not begin below the Vastutaja/Saatja row"
    )
    assert abs(field["width"] - row["width"]) <= 2, (
        f"Adressaat is {field['width']}px inside a {row['width']}px row"
    )
    assert field["width"] > row["width"] - 130 - 18 + 1, (
        f"Adressaat is {field['width']}px in a {row['width']}px row — no wider than "
        "it was when it shared the row with the 130px Andmeklass column"
    )
    assert abs(field["width"] - people["width"]) <= 2, (
        f"the Adressaat row ({field['width']}px) is not the same content width as "
        f"the row above it ({people['width']}px)"
    )


# ---------------------------------------------------------------------------
# Narrow — stacked, and nothing is an island
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("width", [1024, 768, 420])
def test_the_row_stacks_and_saatja_keeps_the_width(page, base_url, width):
    """Under the 1080px breakpoint the trio stops being a trio.

    Saatja is the one to measure: a three-column desktop row whose narrow rule
    was forgotten leaves it in a 1fr track of a grid that is still three columns
    wide, which is the "narrow island" this must not become.
    """
    _open(page, base_url, width)

    row = _box(page, PEOPLE_ROW)
    owner = _box(page, OWNER)
    sender = _box(page, SENDER)

    assert abs(sender["width"] - row["width"]) <= 2, (
        f"Saatja is {sender['width']}px inside a {row['width']}px stacked row at {width}px"
    )
    assert sender["y"] > owner["y"] + owner["height"] - 2, (
        f"Saatja is still beside Vastutaja at {width}px rather than under it"
    )


@pytest.mark.parametrize("width", [1024, 768, 420])
def test_adressaat_stays_full_width_when_stacked(page, base_url, width):
    _open(page, base_url, width)

    row = _box(page, ADDRESSEE_ROW)
    field = _box(page, ADDRESSEE)

    assert abs(field["width"] - row["width"]) <= 2, (
        f"Adressaat is {field['width']}px inside a {row['width']}px row at {width}px"
    )
    assert field["width"] > 18 * 16 + 1, (
        f"Adressaat is capped near 18rem at {width}px: {field['width']}px"
    )


@pytest.mark.parametrize("width", [1024, 768, 420])
def test_the_andmeklass_checkbox_stays_usable_when_stacked(page, base_url, width):
    """Compact is allowed here; unreachable is not.

    `createform__field--compact` caps this field at 18rem on a stacked row,
    which is deliberate — a checkbox stretched across a 1400px row reads as the
    most important control on the page. What the cap may not do is put the box
    outside its row or out of reach.
    """
    _open(page, base_url, width)

    row = _box(page, PEOPLE_ROW)
    field = _box(page, DATA_CLASS)
    box = page.locator("#andmeklass-test input[type=checkbox]").first

    assert field["x"] >= row["x"] - 2, "Andmeklass starts left of its row"
    assert field["x"] + field["width"] <= row["x"] + row["width"] + 2, (
        f"Andmeklass runs {field['x'] + field['width'] - row['x'] - row['width']}px "
        f"past its row at {width}px"
    )
    box.check()
    assert box.is_checked(), f"the Andmeklass checkbox cannot be ticked at {width}px"


@pytest.mark.parametrize("width", [1440, 1024, 768, 420])
def test_the_form_never_scrolls_sideways(page, base_url, width):
    _open(page, base_url, width)

    overflows = page.evaluate(
        "() => document.documentElement.scrollWidth > document.documentElement.clientWidth + 1"
    )
    assert not overflows, f"Uus teema scrolls the page sideways at {width}px"
