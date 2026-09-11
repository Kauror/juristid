"""Uus teema's rows, measured.

The change this file guards is composition only. The people row used to be
three fields — Vastutaja, Saatja and a 130px `Andmeklass` column holding one
«Testandmed» checkbox. That checkbox is gone from ordinary `Uus teema`
altogether: this form creates real work and does not ask, so the question and
the column it stood in have both left the page (task §16, §17).

The width goes to Saatja, which is the field on that row with something to do
with it. Saatja now carries a shortlist of about eight chips, a
«Vali nimekirjast» disclosure and a `Uus saatja` box — the Adressaat shape,
applied to the field beside it — and in a third of a row that is cramped.

No field was renamed or reinterpreted, so nothing here asserts behaviour:
`test_addressee_free_entry.py` owns what Adressaat *does*,
`test_counterparty_selection.py` owns the promotion between the two, and
`test_matter_form_ux.py` owns how the choice controls read. This file owns only
where the boxes are, at four widths, and it asserts that with bounding boxes
rather than with a screenshot — a screenshot cannot say which row a field is on.
"""

from __future__ import annotations

import pytest
from playwright.sync_api import expect

from e2e.conftest import MARTIN, sign_in

pytestmark = pytest.mark.e2e

CREATE_PATH = "/teemad/uus/"

#: Each field addressed through a control only it contains, so that a chip, a
#: legend or a disclosure moving inside one of them does not rename it here.
OWNER = 'fieldset.field:has(input[name="owner"])'
SENDER = 'fieldset.field:has(input[name="sender_name"])'
ADDRESSEE = "fieldset.field:has([data-addressee-disclosure])"

#: Adressaat's own disclosure. Since docs/adr/0069 the field is folded away
#: behind it — a sender answers it, so on the ordinary visit there is nothing
#: left to spend a row of chips on — and a closed `<details>` gives its contents
#: no box at all. Everything below that measures the *field* therefore opens it
#: first; what the closed state has to satisfy is only that the pill is on the
#: row and the row does not scroll sideways.
ADDRESSEE_DISCLOSURE = "[data-addressee-disclosure]"

#: The row element itself, whatever modifier it carries this month.
PEOPLE_ROW = f".createform__row:has({OWNER})"
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


def _open_addressee(page) -> None:
    """Unfold Adressaat, so the field inside it has a box to measure."""
    disclosure = page.locator(ADDRESSEE_DISCLOSURE)
    if not disclosure.evaluate("node => node.open"):
        disclosure.locator("> summary").click()


# ---------------------------------------------------------------------------
# The checkbox is gone, and gone from the page rather than merely hidden
# ---------------------------------------------------------------------------


def test_ordinary_uus_teema_asks_nothing_about_test_data(page, base_url):
    """Neither the legend, nor the label, nor the control behind them.

    A control removed from view but left in the document is a control that still
    posts, so this asserts the absence of the input as well as of the words
    somebody would read (task §16).
    """
    _open(page, base_url, 1440)

    expect(page.get_by_text("Andmeklass", exact=True)).to_have_count(0)
    expect(page.get_by_text("Testandmed", exact=True)).to_have_count(0)
    expect(page.locator('[name="is_test_data"]')).to_have_count(0)
    expect(page.locator("#andmeklass-test")).to_have_count(0)


# ---------------------------------------------------------------------------
# Desktop — two fields share one row, and Saatja has the space
# ---------------------------------------------------------------------------


def test_vastutaja_and_saatja_share_one_row(page, base_url):
    """One row, and the same one.

    Their tops are compared to each other rather than to the row's own `y`,
    because `.createform__row` carries 11px of top padding and a border that
    every child starts below — the shared line is 12px into the row box, not at
    its edge.

    Not equal heights: the two are `align-items: start` in a grid track, so a
    Saatja carrying chips and a disclosure is taller than a chip row of names
    and that is correct. What would not be correct is one of them beginning
    further down than the other, which is what a field pushed onto a second line
    looks like as a number.
    """
    _open(page, base_url, 1440)

    row = _box(page, PEOPLE_ROW)
    owner = _box(page, OWNER)
    sender = _box(page, SENDER)

    assert abs(sender["y"] - owner["y"]) <= 2, (
        f"Saatja starts {sender['y'] - owner['y']}px below Vastutaja rather than beside it, "
        "which means it wrapped onto a second line instead of taking a column"
    )
    for name, box in (("Vastutaja", owner), ("Saatja", sender)):
        assert box["y"] >= row["y"] - 2, f"{name} starts above the row it shares"


def test_the_two_fields_read_left_to_right_in_the_intended_order(page, base_url):
    _open(page, base_url, 1440)

    owner = _box(page, OWNER)
    sender = _box(page, SENDER)

    assert owner["x"] + owner["width"] <= sender["x"] + 1, (
        f"Saatja starts at {sender['x']}px, inside a Vastutaja ending at "
        f"{owner['x'] + owner['width']}px"
    )


def test_saatja_took_the_width_the_checkbox_column_was_holding(page, base_url):
    """The point of the change, stated as the thing that would undo it.

    Vastutaja is a fixed 246px and Saatja takes what is left. The `130 + 18` is
    the column and the gap `Andmeklass` used to occupy: Saatja must now be wider
    than it would have been with that column still there, which is what "gained
    the width" means as a number rather than as an adjective.

    A future edit that handed the flexible column to Vastutaja instead would
    still keep both on one row and still order them correctly, and would still
    be wrong.
    """
    _open(page, base_url, 1440)

    row = _box(page, PEOPLE_ROW)
    sender = _box(page, SENDER)["width"]
    owner = _box(page, OWNER)["width"]

    assert sender > owner, f"Saatja ({sender}px) is narrower than Vastutaja ({owner}px)"
    assert sender > row["width"] - 246 - 18 - 130 - 18 + 1, (
        f"Saatja is {sender}px in a {row['width']}px row — no wider than it was "
        "when the row still carried the 130px Andmeklass column"
    )


def test_the_row_holds_exactly_two_fields(page, base_url):
    """A blank third column is the failure this change could plausibly leave.

    Removing the field but not the grid track would keep every assertion above
    passing and leave 130px of nothing at the end of the row.
    """
    _open(page, base_url, 1440)

    row = page.locator(PEOPLE_ROW).first
    expect(row.locator("> fieldset, > label")).to_have_count(2)

    box = _box(page, PEOPLE_ROW)
    sender = _box(page, SENDER)
    trailing = box["x"] + box["width"] - (sender["x"] + sender["width"])
    assert trailing <= 2, f"{trailing}px of empty row after Saatja — a track nothing fills"


# ---------------------------------------------------------------------------
# Desktop — Adressaat is on its own row, and spends it
# ---------------------------------------------------------------------------


def test_adressaat_begins_on_a_row_below_and_costs_it_a_pill(page, base_url):
    """Closed, it is one chip-shaped summary and the row is nearly all air.

    That is the point of folding it: the field is answered by the time somebody
    reaches it, so the height it used to take for a chip row, a disclosure and a
    text box is height spent on a question nobody has to answer (§3).
    """
    _open(page, base_url, 1440)

    people = _box(page, PEOPLE_ROW)
    row = _box(page, ADDRESSEE_ROW)
    pill = _box(page, f"{ADDRESSEE_DISCLOSURE} > summary")

    assert row["y"] >= people["y"] + people["height"] - 2, (
        "the Adressaat row does not begin below the Vastutaja/Saatja row"
    )
    assert pill["width"] < row["width"] / 2, (
        f"the closed Adressaat summary is {pill['width']}px of a {row['width']}px row — "
        "that is a control, not a folded one"
    )


def test_opening_adressaat_gives_it_the_whole_row(page, base_url):
    """`chipdetails--field`: a door standing on its own row opens onto all of it.

    `.chipdetails` is `inline-block`, which is right for a disclosure sitting in
    a row of chips and wrong here — shrink-to-fit would measure the widest chip
    inside and leave the open field an island in a row it owns.
    """
    _open(page, base_url, 1440)
    _open_addressee(page)

    people = _box(page, PEOPLE_ROW)
    row = _box(page, ADDRESSEE_ROW)
    field = _box(page, ADDRESSEE)

    assert abs(field["width"] - row["width"]) <= 2, (
        f"Adressaat is {field['width']}px inside a {row['width']}px row"
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
    """Under the 1080px breakpoint the pair stops being a pair."""
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
def test_the_sender_search_is_not_a_narrow_island_when_stacked(page, base_url, width):
    """The control that opens, opened, on a row it owns.

    This measured the Saatja disclosure until docs/adr/0073 removed it, and the
    failure it guards against is unchanged: whatever the field opens onto must
    not be a narrow panel in a field with the whole row to spend. What opens is
    the results list now, and it is measured against the box above it rather
    than against the field — the box is deliberately bounded at 30rem, which is
    what this form has always allowed a control for naming an institution
    (static/css/app.css `.orgfind__search`).
    """
    _open(page, base_url, width)

    box = page.locator("#saatja-otsi")
    box.click()
    box.fill("näidis")

    field = _box(page, SENDER)
    search = _box(page, f"{SENDER} .orgfind__search")
    panel = _box(page, "#saatja-tulemused")

    assert search["width"] >= min(field["width"], 30 * 16) - 2, (
        f"the Saatja search box is {search['width']}px inside a {field['width']}px "
        f"field at {width}px — a narrow island rather than a control"
    )
    assert abs(panel["width"] - search["width"]) <= 2, (
        f"the results ({panel['width']}px) are not the width of the box "
        f"({search['width']}px) at {width}px"
    )


@pytest.mark.parametrize("width", [1024, 768, 420])
def test_adressaat_stays_full_width_when_stacked(page, base_url, width):
    _open(page, base_url, width)
    _open_addressee(page)

    row = _box(page, ADDRESSEE_ROW)
    field = _box(page, ADDRESSEE)

    assert abs(field["width"] - row["width"]) <= 2, (
        f"Adressaat is {field['width']}px inside a {row['width']}px row at {width}px"
    )
    assert field["width"] > 18 * 16 + 1, (
        f"Adressaat is capped near 18rem at {width}px: {field['width']}px"
    )


@pytest.mark.parametrize("width", [1440, 1024, 768, 420])
def test_the_form_never_scrolls_sideways(page, base_url, width):
    _open(page, base_url, width)

    overflows = page.evaluate(
        "() => document.documentElement.scrollWidth > document.documentElement.clientWidth + 1"
    )
    assert not overflows, f"Uus teema scrolls the page sideways at {width}px"
