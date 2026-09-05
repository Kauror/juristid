"""The facts rail, in a real browser.

Everything this file asserts is a property of the *rendered* page, and every
one of them was broken in a way no response-body assertion could see.

`Teema andmed` was more than half again as tall as the facts in it, and the
cause was not spacing. Each editable fact was written as
``<p class="railcard__row">`` wrapping a ``<details>``, and the HTML tree builder
closes an open ``p`` at a ``<details>`` start tag — so the template's one row
became three siblings in the DOM: a label-only paragraph, the disclosure, and an
empty paragraph, each a flex child of the card with its own gap. Six facts came
to 382px where they should have come to about 210, and every value sat on the
line below its own label.

`+ Lisa` looked like a dead control. The disclosure toggled correctly every
time; `.inlineedit__form` is `position: absolute`, the header band anchors it on
`.metafield`, and the rail had no positioned ancestor at all — so the editor's
containing block was the viewport and every one of them opened at x=0, one whole
viewport height down the page.

Structure and geometry rather than screenshots, deliberately. A baseline would
have gone green on both defects — the first because the rail was simply tall,
the second because a closed disclosure photographs identically either way.
"""

from __future__ import annotations

import re
from itertools import pairwise

import pytest
from playwright.sync_api import expect

from e2e.conftest import MARTIN, READER, create_matter, open_matter, sign_in, sign_out

pytestmark = pytest.mark.e2e

#: The Matter the seeded world gives two senders, for the wrapping case.
MULTI_SENDER_TITLE = "Tavaline avatud teema"

OPINION_PDF = {
    "name": "Koja_arvamus.pdf",
    "mimeType": "application/pdf",
    "buffer": b"%PDF-1.4 synthetic opinion",
}


def facts_card(page):
    return page.locator("#teema-andmed .railcard").first


def fact_row(page, key: str):
    """One `label | value` row of `Teema andmed`, by its label."""
    return (
        page.locator("#teema-andmed .railcard__row")
        .filter(has=page.locator(".railcard__key", has_text=re.compile(rf"^{key}$")))
        .first
    )


def fact_value(page, key: str):
    return fact_row(page, key).locator(".railcard__value, .railcard__add").first


def row_geometry(page):
    """Every fact row of `Teema andmed`, in document order, with its gap above."""
    return page.evaluate(
        """() => {
          const card = document.querySelector('#teema-andmed .railcard');
          const rows = Array.from(card.querySelectorAll('.railcard__row'));
          let previousBottom = null;
          return rows.map(row => {
            const box = row.getBoundingClientRect();
            const style = getComputedStyle(row);
            const key = row.querySelector('.railcard__key');
            // The last cell, whatever it is called: `Andmeklass` is a plain
            // span, because nothing about it is editable.
            const value = row.querySelector('.railcard__value, .railcard__add, .railcard__ref')
              || row.lastElementChild;
            const gap = previousBottom === null ? null : box.y - previousBottom;
            previousBottom = box.bottom;
            return {
              key: key ? key.textContent.trim() : null,
              y: box.y,
              height: box.height,
              gapAbove: gap,
              minHeight: style.minHeight,
              height_css: style.height,
              keyTop: key ? key.getBoundingClientRect().y : null,
              valueTop: value ? value.getBoundingClientRect().y : null,
            };
          });
        }"""
    )


def document_overflows(page) -> bool:
    return page.evaluate(
        "() => document.documentElement.scrollWidth > document.documentElement.clientWidth + 1"
    )


def sparse_matter(page, base_url: str, title: str) -> str:
    """A Matter with the shape the QA report described.

    Teemaviide, Saabus and Andmeklass come with the record — a new Matter is
    dated the day it was opened. `Kellele` is filled in through the rail's own
    control, which is both how a lawyer would do it and a second proof that the
    control works. `Menetlusliik` and `Kellelt` are deliberately left empty: the
    whole complaint was that empty facts made the column tall.

    Built here rather than seeded: adding a Matter to the shared world changes
    the register every visual baseline photographs.
    """
    url = create_matter(page, base_url, title)

    row = fact_row(page, "Kellele")
    row.get_by_text("+ Lisa").click()
    row.locator("select[name=addressee_organisation]").select_option(index=1)
    expect(fact_value(page, "Kellele")).not_to_have_text("+ Lisa")

    return url


# ---------------------------------------------------------------------------
# A. `Teema andmed` is one compact facts table
# ---------------------------------------------------------------------------


def test_a_sparse_matter_gives_a_compact_facts_block(page, base_url):
    """The QA report's own Matter: two facts empty, four short, one block.

    Bounded row pitch rather than a fixed total height. The block is allowed to
    grow when content needs it — that is the next test — so what is locked here
    is that *nothing but content* makes it grow.
    """
    sign_in(page, base_url, MARTIN)
    sparse_matter(page, base_url, "Hõre teema tiheduse kontrolliks")

    rows = row_geometry(page)
    keys = [row["key"] for row in rows]
    # `Muu valdkond` sits among the facts because it *is* one. It used to hang
    # off the bottom of the `Sildid` card, which was an accident of layout — it
    # is not a tag and nothing counts it — and when that card was retired it
    # moved here rather than leaving with it (ADR 0052 §15).
    assert keys[:7] == [
        "Teemaviide",
        "Menetlusliik",
        "Kellelt",
        "Kellele",
        "Saabus",
        "Muu valdkond",
        "Andmeklass",
    ], keys

    for row in rows:
        # One short fact is one line. 12.5px text on a 1.35 line-height is
        # ~17px; the ceiling leaves room for the dashed affordance under an
        # editable value and for nothing else.
        assert row["height"] <= 24, f"{row['key']} is {row['height']:.1f}px tall for one line"
        # Nothing reserves height in advance.
        assert row["minHeight"] in ("auto", "0px"), f"{row['key']} has a minimum height"
        assert row["height_css"] != "0px"
        # Label and value are on the same line, not stacked. This is the defect
        # itself: when the row was a paragraph the parser moved the value out.
        assert abs(row["valueTop"] - row["keyTop"]) <= 4, (
            f"{row['key']}: the value is not beside its label"
        )

    gaps = [row["gapAbove"] for row in rows[1:]]
    assert max(gaps) <= 10, f"rows are {max(gaps):.1f}px apart"
    assert min(gaps) >= 0, "rows overlap"

    # And the block's height is its content's height, with nothing unexplained
    # in between: label, rows, gaps and the card's own padding.
    card = facts_card(page).bounding_box()
    content = sum(row["height"] for row in rows) + sum(gaps)
    assert card["height"] - content <= 60, (
        f"the card is {card['height']:.1f}px around {content:.1f}px of facts"
    )


def test_an_empty_fact_costs_no_more_than_a_filled_one(page, base_url):
    """`+ Lisa` is a value, not a block of its own."""
    sign_in(page, base_url, MARTIN)
    sparse_matter(page, base_url, "Tühja välja kõrgus")

    rows = {row["key"]: row for row in row_geometry(page)}

    empty = rows["Menetlusliik"]["height"]
    filled = rows["Saabus"]["height"]
    assert abs(empty - filled) <= 2, (
        f"an empty fact is {empty:.1f}px and a filled one {filled:.1f}px"
    )


# ---------------------------------------------------------------------------
# B. …and grows only when the content does
# ---------------------------------------------------------------------------


def test_a_multi_sender_value_wraps_and_pushes_the_rest_down(page, base_url):
    """Compact is not one line. Several senders wrap; the row grows with them.

    Not clipped, not ellipsised and not overflowing: the column is 300px, the
    list is the fact, and the rows below it move down.
    """
    sign_in(page, base_url, MARTIN)
    open_matter(page, base_url, MULTI_SENDER_TITLE)

    rows = {row["key"]: row for row in row_geometry(page)}
    senders = rows["Kellelt"]
    reference = rows["Teemaviide"]

    assert senders["height"] > reference["height"] + 4, (
        "the sender list did not grow onto a second line"
    )

    # Nothing below it was overlapped or pushed off.
    ordered = row_geometry(page)
    for earlier, later in pairwise(ordered):
        assert later["y"] >= earlier["y"] + earlier["height"] - 1, (
            f"{later['key']} overlaps {earlier['key']}"
        )

    # The whole list is rendered, and it is inside the rail.
    value = fact_value(page, "Kellelt")
    clipped = value.evaluate("n => n.scrollWidth > n.clientWidth + 1")
    assert not clipped, "the sender list is cut off rather than wrapped"
    assert not document_overflows(page)

    rail = page.locator("#teema-andmed").bounding_box()
    box = value.bounding_box()
    assert box["x"] + box["width"] <= rail["x"] + rail["width"] + 1, (
        "the sender list runs outside the rail"
    )


# ---------------------------------------------------------------------------
# C. every `+ Lisa` opens a control somebody can actually use
# ---------------------------------------------------------------------------

#: Each editable fact, with the control its editor opens and how to commit it.
EDITABLE_FACTS = ["Menetlusliik", "Kellelt", "Kellele", "Saabus"]


@pytest.mark.parametrize("key", EDITABLE_FACTS)
def test_the_editor_opens_next_to_the_fact_it_edits(page, base_url, key):
    """The defect, measured: the editor used to open at the page's top-left.

    Three things have to hold for a person to call the control working — the
    disclosure opens, the editor lands beside the trigger that opened it, and
    the point in the middle of its first control belongs to that control rather
    than to whatever is painted over it.
    """
    sign_in(page, base_url, MARTIN)
    create_matter(page, base_url, f"Redigeerimise kontroll · {key}")

    row = fact_row(page, key)
    row.locator("summary.inlineedit__trigger").click()

    form = row.locator("form.inlineedit__form")
    expect(form).to_be_visible()

    trigger = row.locator("summary.inlineedit__trigger").bounding_box()
    box = form.bounding_box()
    assert box["y"] >= trigger["y"], f"{key}: the editor opened above its trigger"
    assert box["y"] - (trigger["y"] + trigger["height"]) <= 24, (
        f"{key}: the editor opened {box['y'] - trigger['y']:.0f}px away from its trigger"
    )
    assert box["x"] >= 0 and box["x"] + box["width"] <= page.viewport_size["width"] + 1, (
        f"{key}: the editor is outside the window"
    )
    assert not document_overflows(page), f"{key}: opening the editor scrolled the page sideways"

    control = form.locator("select, input[type=text], input[type=checkbox]").first
    expect(control).to_be_visible()
    on_top = control.evaluate(
        """n => {
          const r = n.getBoundingClientRect();
          const hit = document.elementFromPoint(r.x + r.width / 2, r.y + r.height / 2);
          return hit === n || n.contains(hit) || (hit && hit.contains(n));
        }"""
    )
    assert on_top, f"{key}: something is painted over the control"


@pytest.mark.parametrize("width", [1440, 1280, 1024, 768, 375])
def test_no_editor_falls_off_the_window_at_any_supported_width(page, base_url, width):
    """The rail's popovers open leftwards, and only while the rail is a column.

    Above 1100px the rail is 300px against the right edge of the window, so an
    editor anchored at its value's left edge runs past it. Below, the rail folds
    under the content and the facts start at the left margin — where the same
    rule put every editor about 100px off the *left* edge instead, which no
    scroll-width assertion can see because overflow to the left never makes a
    scrollbar. Both sides, at both kinds of width.
    """
    sign_in(page, base_url, MARTIN)
    create_matter(page, base_url, f"Redaktori serv {width}")
    page.set_viewport_size({"width": width, "height": 900})

    for key in EDITABLE_FACTS:
        row = fact_row(page, key)
        row.locator("summary.inlineedit__trigger").click()
        box = row.locator("form.inlineedit__form").bounding_box()
        assert box["x"] >= -0.5, f"{key} at {width}px opens {box['x']:.0f}px off the left edge"
        assert box["x"] + box["width"] <= width + 1, (
            f"{key} at {width}px runs {box['x'] + box['width'] - width:.0f}px past the right edge"
        )
        assert not document_overflows(page), f"{key} at {width}px scrolls the page sideways"
        row.locator("summary.inlineedit__trigger").click()


@pytest.mark.parametrize("key", EDITABLE_FACTS)
def test_the_editor_is_reachable_from_the_keyboard(page, base_url, key):
    """A `<details>` rather than script, so the affordance focuses and opens."""
    sign_in(page, base_url, MARTIN)
    create_matter(page, base_url, f"Klaviatuuri kontroll · {key}")

    trigger = fact_row(page, key).locator("summary.inlineedit__trigger")
    trigger.focus()
    assert trigger.evaluate("n => n === document.activeElement")
    page.keyboard.press("Enter")
    expect(fact_row(page, key).locator("form.inlineedit__form")).to_be_visible()


def test_adding_a_menetlusliik_saves_and_the_rail_shows_it(page, base_url):
    sign_in(page, base_url, MARTIN)
    create_matter(page, base_url, "Menetlusliigi lisamine")

    row = fact_row(page, "Menetlusliik")
    expect(fact_value(page, "Menetlusliik")).to_have_text("+ Lisa")
    row.get_by_text("+ Lisa").click()
    row.locator("select[name=track]").select_option("EU_INITIATIVE")

    expect(fact_value(page, "Menetlusliik")).to_have_text("ELi algatus")
    page.reload()
    expect(fact_value(page, "Menetlusliik")).to_have_text("ELi algatus")


def test_adding_a_sender_saves_and_the_rail_shows_it(page, base_url):
    sign_in(page, base_url, MARTIN)
    create_matter(page, base_url, "Saatja lisamine")

    row = fact_row(page, "Kellelt")
    expect(fact_value(page, "Kellelt")).to_have_text("+ Lisa")
    row.get_by_text("+ Lisa").click()
    checkbox = row.get_by_role("checkbox").first
    name = row.locator(".checkitem").first.inner_text().strip()
    checkbox.check()
    row.get_by_role("button", name="Salvesta saatjate muudatus").click()

    expect(fact_value(page, "Kellelt")).to_have_text(name)
    page.reload()
    expect(fact_value(page, "Kellelt")).to_have_text(name)


def test_adding_an_addressee_saves_and_the_rail_shows_it(page, base_url):
    sign_in(page, base_url, MARTIN)
    create_matter(page, base_url, "Adressaadi lisamine")

    row = fact_row(page, "Kellele")
    expect(fact_value(page, "Kellele")).to_have_text("+ Lisa")
    row.get_by_text("+ Lisa").click()
    select = row.locator("select[name=addressee_organisation]")
    chosen = select.locator("option").nth(1).inner_text().strip()
    select.select_option(index=1)

    expect(fact_value(page, "Kellele")).to_have_text(chosen)
    page.reload()
    expect(fact_value(page, "Kellele")).to_have_text(chosen)


def test_adding_a_received_date_saves_and_the_rail_formats_it(page, base_url):
    """A new Matter is dated today, so the empty state is reached by clearing.

    Worth reaching rather than skipping: `Saabus` is one of the four facts the
    QA report named, and an empty one is what a register row imported without a
    date looks like.
    """
    sign_in(page, base_url, MARTIN)
    create_matter(page, base_url, "Saabumise kuupäeva lisamine")

    row = fact_row(page, "Saabus")
    row.locator("summary.inlineedit__trigger").click()
    row.locator("input[name=received_date]").fill("")
    row.get_by_role("button", name="Salvesta saabumise kuupäeva muudatus").click()

    row = fact_row(page, "Saabus")
    expect(fact_value(page, "Saabus")).to_have_text("+ Lisa")
    row.get_by_text("+ Lisa").click()
    row.locator("input[name=received_date]").fill("14.8.2026")
    row.get_by_role("button", name="Salvesta saabumise kuupäeva muudatus").click()

    expect(fact_value(page, "Saabus")).to_have_text("14.8.2026")
    page.reload()
    expect(fact_value(page, "Saabus")).to_have_text("14.8.2026")


def test_a_reader_is_offered_no_editors_at_all(page, base_url):
    sign_in(page, base_url, MARTIN)
    url = sparse_matter(page, base_url, "Lugeja ei redigeeri")
    sign_out(page, base_url)

    sign_in(page, base_url, READER)
    page.goto(url)
    page.wait_for_load_state("networkidle")

    expect(page.locator("#teema-andmed")).to_be_visible()
    expect(page.locator("#teema-andmed .inlineedit__trigger")).to_have_count(0)
    expect(page.locator("#teema-andmed .railcard__add")).to_have_count(0)


# ---------------------------------------------------------------------------
# D. `Koja seisukoht` is gone
# ---------------------------------------------------------------------------


def test_the_koja_seisukoht_block_is_absent(page, base_url):
    sign_in(page, base_url, MARTIN)
    open_matter(page, base_url, MULTI_SENDER_TITLE)

    expect(page.locator("#koja-seisukoht")).to_have_count(0)
    expect(page.get_by_text("Seisukohta ei ole")).to_have_count(0)
    expect(page.get_by_role("link", name="Lisa seisukoht")).to_have_count(0)


def test_the_retired_arvamused_address_lands_on_dokumendid(page, base_url):
    """The old per-Matter address still gets somebody to the opinion material.

    Typed rather than clicked, because nothing links to it any more — that is
    the point of retiring it. What it owes is that a bookmark still works, not
    that a page still exists, so this follows the redirect and checks where it
    lands: Dokumendid, filtered to `Arvamus`, with the opinion workflow on it
    and no trace of the free-text position the surface used to carry
    (docs/adr/0060 §4, §36).
    """
    sign_in(page, base_url, MARTIN)
    url = open_matter(page, base_url, MULTI_SENDER_TITLE)
    page.goto(f"{url.rstrip('/')}/seisukoht/")
    page.wait_for_load_state("networkidle")

    assert "/dokumendid/" in page.url, page.url
    assert "roll=arvamus" in page.url, page.url

    main = page.locator(".teemamain")
    expect(main.get_by_role("heading", name="Arvamused")).to_be_visible()

    for phrase in (
        "Koja seisukoht",
        "Koja seisukohta ei ole veel sõnastatud",
        "Sõnasta Koja seisukoht",
        "Muuda seisukohta",
        "Salvesta seisukoht",
    ):
        expect(main.get_by_text(phrase, exact=False)).to_have_count(0)
    expect(page.locator("#id_position_summary")).to_have_count(0)
    expect(page.locator("#id_rationale_summary")).to_have_count(0)
    assert not document_overflows(page)


# ---------------------------------------------------------------------------
# E and F. `Koja arvamus` is a file, and only a writer may add one
# ---------------------------------------------------------------------------


def upload_an_opinion(page, url: str) -> None:
    """Capture one `Arvamus` through the Dokumendid panel, which is the only one.

    The rail used to carry an upload of its own. It does not: a form in 300px
    beside a file list that already has one is the same control twice, and the
    role is chosen from the same select as every other document's — reading
    `Arvamus`, storing `KODA_SUBMISSION_FINAL` (docs/adr/0060 §7, §18).
    """
    page.goto(f"{url.rstrip('/')}/dokumendid/")
    page.wait_for_load_state("networkidle")
    page.get_by_role("button", name=re.compile("Lae dokument")).first.click()
    panel = page.locator("#lae-dokument")
    panel.locator("input[type=file]").set_input_files(OPINION_PDF)
    panel.locator("select[name=role]").select_option(label="Arvamus")
    panel.get_by_role("button", name="Salvesta dokument").click()
    page.wait_for_load_state("networkidle")


def test_a_writer_can_add_the_chambers_opinion_as_a_file(page, base_url):
    sign_in(page, base_url, MARTIN)
    url = create_matter(page, base_url, "Koja arvamuse lisamine")

    block = page.locator("#koja-arvamus")
    expect(block).to_be_visible()
    expect(block.get_by_text("Arvamust ei ole lisatud.")).to_be_visible()
    # Read-only: no upload, and no way out of the block at all.
    expect(block.locator("input[type=file]")).to_have_count(0)
    expect(block.get_by_text("+ Lisa arvamus")).to_have_count(0)

    upload_an_opinion(page, url)

    # In the file table, badged for what it is. Scoped to the row rather than
    # asked of the page, because `Arvamus` is also an <option> in the upload
    # panel's role picker and a hidden option is not evidence of anything.
    row = page.locator("table tr").filter(has_text="Koja_arvamus.pdf").first
    expect(row).to_be_visible()
    expect(row.locator(".badge--opinion")).to_have_text("Arvamus")
    expect(row).to_contain_text("Arvamus")
    expect(row.get_by_text("Lõplik")).to_have_count(0)
    # A file on the record is not a claim that anything was sent.
    expect(row.locator(".doctable__sent")).to_have_count(0)

    # And the same one document reaches the rail on the Teema page.
    page.goto(url)
    page.wait_for_load_state("networkidle")
    block = page.locator("#koja-arvamus")
    expect(block.get_by_role("link", name=re.compile("Koja_arvamus.pdf"))).to_be_visible()
    expect(block.get_by_text("Arvamust ei ole lisatud.")).to_have_count(0)


def test_a_reader_sees_the_opinion_but_no_way_to_add_one(page, base_url):
    sign_in(page, base_url, MARTIN)
    url = create_matter(page, base_url, "Lugeja näeb arvamust")
    upload_an_opinion(page, url)
    sign_out(page, base_url)

    sign_in(page, base_url, READER)
    page.goto(url)
    page.wait_for_load_state("networkidle")

    block = page.locator("#koja-arvamus")
    expect(block).to_be_visible()
    expect(block.get_by_role("link", name=re.compile("Koja_arvamus.pdf"))).to_be_visible()
    expect(block.locator("input[type=file]")).to_have_count(0)

    page.goto(f"{url.rstrip('/')}/dokumendid/")
    page.wait_for_load_state("networkidle")
    expect(page.get_by_role("button", name=re.compile("Lae dokument"))).to_have_count(0)
    expect(page.get_by_text("+ Uus arvamus")).to_have_count(0)
