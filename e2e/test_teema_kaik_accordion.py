"""`Teema käik` as an accordion, in a real browser, at the widths it is read at.

`tests/test_teema_kaik_accordion.py` proves what the server sends and the
stylesheet's contract with it. This proves what only a layout engine and a
running script can say (docs/adr/0074 §14, amended 2026-09-27):

* every row arrives closed, as one line, with no box around it; the detail —
  body, files, `Muuda` — is out of sight until the row is opened;
* pressing a row opens it, the chevron turns, the detail and its controls are
  there and work, and pressing it again closes it;
* one row is open at a time, and the open row is the only panel on the list —
  an accent edge for a primary row, a neutral one for a secondary row;
* a primary line is semibold and a secondary line regular and lighter, and no
  date is bold;
* the toggle is a real button: keyboard, name, `aria-expanded`;
* a link to a row opens it, a row with nothing behind its line has no toggle,
  and with scripting off every row reads open;
* a refused and then an accepted `+ Lisa fail` re-render the whole column and
  the row being worked on stays open through both;
* nothing overlaps the spine and nothing pushes the page sideways at 1440,
  1024, 768 or 375.

Opening a row writes nothing, so every test but the last reads the seeded open
Matter; the last files its own. Everything here is synthetic.
"""

from __future__ import annotations

import re

import pytest
from playwright.sync_api import expect

from e2e.conftest import (
    KAIK_ROW,
    MARTIN,
    SANDRA,
    create_matter,
    open_kaik_row,
    open_matter,
    record_marge,
    sign_in,
    unique_title,
    wait_for_htmx,
)

pytestmark = pytest.mark.e2e

OPEN_TITLE = (
    "Tavaline avatud teema kõigile nähtav — pakendiseaduse ja sellega seonduvalt "
    "teiste seaduste muutmise seaduse eelnõu väljatöötamiskavatsus"
)

WIDTHS = (1440, 1024, 768, 375)

#: What the browser resolved each row to. Read off the laid-out page, so a
#: rule that stopped matching — a lost `#teema-vaade-wrap` scope, a renamed
#: class — fails here rather than passing on the markup.
ROWS = """rows => rows.map(row => {
  const body = row.querySelector('.uxtl__body');
  const style = getComputedStyle(body);
  const toggle = row.querySelector('.uxtl__toggle');
  const line = row.querySelector('.uxtl__line').getBoundingClientRect();
  const head = row.querySelector('.uxtl__mswhat, .uxtl__author, .uxtl__did');
  const date = row.querySelector('.uxtl__msdate');
  const b = body.getBoundingClientRect();
  return {
    text: row.textContent.replace(/\\s+/g, ' ').trim().slice(0, 80),
    primary: row.classList.contains('uxtl__item--primary'),
    open: row.classList.contains('uxtl__item--open'),
    expanded: toggle ? toggle.getAttribute('aria-expanded') : null,
    toggleShown: !!toggle && toggle.getClientRects().length > 0,
    border: style.borderTopColor,
    borderWidth: parseFloat(style.borderTopWidth),
    background: style.backgroundColor,
    height: b.height, left: b.left, right: b.right,
    spineRight: line.right,
    weight: head ? Number(getComputedStyle(head).fontWeight) : null,
    ink: head ? getComputedStyle(head).color : null,
    dateWeight: date ? Number(getComputedStyle(date).fontWeight) : null,
  };
})"""

RESOLVE_BORDER_COLOUR = """token => {
  const scope = document.querySelector('#teema-vaade-wrap');
  const probe = document.createElement('span');
  probe.style.borderTop = `1px solid var(${token})`;
  scope.appendChild(probe);
  const colour = getComputedStyle(probe).borderTopColor;
  probe.remove();
  return colour;
}"""

TRANSPARENT = ("rgba(0, 0, 0, 0)", "transparent")


def rows(page) -> list[dict]:
    return page.locator(KAIK_ROW).evaluate_all(ROWS)


def row(page, words: str):
    return page.locator(KAIK_ROW).filter(has_text=words).first


def toggle_of(article):
    return article.locator(".uxtl__toggle").first


def assert_no_sideways_scroll(page) -> None:
    extra = page.evaluate(
        "() => document.documentElement.scrollWidth - document.documentElement.clientWidth"
    )
    assert extra <= 0, f"the page scrolls sideways by {extra}px"


@pytest.fixture
def seeded(page, base_url):
    sign_in(page, base_url, SANDRA)
    open_matter(page, base_url, OPEN_TITLE)
    expect(page.locator(KAIK_ROW).first).to_be_attached()
    return page


# ---------------------------------------------------------------------------
# Closed by default, flat, and quiet where it should be
# ---------------------------------------------------------------------------


def test_every_row_arrives_closed_and_unboxed(seeded):
    page = seeded
    drawn = rows(page)
    assert len(drawn) >= 6, drawn

    for r in drawn:
        assert not r["open"], r
        assert r["expanded"] == "false", r
        assert r["border"] in TRANSPARENT, r
        assert r["background"] in TRANSPARENT, r

    # The detail of every row is in the page and out of sight.
    history = page.locator("#ajalugu-loend")
    for selector in (".uxtl__file", ".uxtl__entry", ".uxtl__mssub", ".uxtl__editactions"):
        found = history.locator(selector)
        assert found.count(), selector
        for index in range(found.count()):
            expect(found.nth(index)).to_be_hidden()

    # A closed row is one line: nothing on the list is taller than a line and
    # its padding at this width.
    assert max(r["height"] for r in drawn) < 40, drawn


def test_only_the_blue_dot_rows_read_as_prominent(seeded):
    page = seeded
    drawn = rows(page)
    primary = [r for r in drawn if r["primary"]]
    secondary = [r for r in drawn if not r["primary"]]
    assert primary and secondary

    for r in primary:
        assert r["weight"] == 600, r
    for r in secondary:
        assert r["weight"] == 400, r
    # Lighter, too: every secondary line is set in one ink, and it is not the
    # primary line's.
    assert len({r["ink"] for r in secondary}) == 1, secondary
    assert {r["ink"] for r in secondary}.isdisjoint({r["ink"] for r in primary})
    # No date is bold, on either level.
    assert {r["dateWeight"] for r in drawn if r["dateWeight"] is not None} == {400}


# ---------------------------------------------------------------------------
# Opening, closing, one at a time
# ---------------------------------------------------------------------------


def test_pressing_a_row_opens_it_and_pressing_again_closes_it(seeded):
    page = seeded
    opinion = row(page, "Arvamus välja")
    toggle = toggle_of(opinion)
    chevron = toggle.locator(".uxtl__chevron")
    closed_turn = chevron.evaluate("el => getComputedStyle(el).transform")

    toggle.click()
    expect(toggle).to_have_attribute("aria-expanded", "true")
    expect(opinion).to_have_class(re.compile(r"\buxtl__item--open\b"))
    expect(opinion.locator(".uxtl__file").first).to_be_visible()
    muuda = opinion.get_by_role("button", name=re.compile("^Muuda"))
    expect(muuda).to_be_visible()
    page.wait_for_timeout(200)
    assert chevron.evaluate("el => getComputedStyle(el).transform") != closed_turn

    # The panel's controls work: `Muuda` swaps in its editor and `Tühista`
    # swaps it back, and the row stays open through both.
    muuda.click()
    editor = opinion.locator("form.uxtl__editform")
    expect(editor).to_be_visible()
    editor.get_by_role("button", name="Tühista").click()
    wait_for_htmx(page)
    expect(editor).to_have_count(0)
    expect(toggle).to_have_attribute("aria-expanded", "true")
    expect(opinion.get_by_role("button", name=re.compile("^Muuda"))).to_be_visible()

    # Pressing the line again closes it, and the detail goes with it. A real
    # pointer at the headline: the toggle is laid over the line, so the press
    # lands on the button, which is the point.
    headline = opinion.locator(".uxtl__mswhat").first.bounding_box()
    assert headline is not None
    page.mouse.click(headline["x"] + 4, headline["y"] + headline["height"] / 2)
    expect(toggle).to_have_attribute("aria-expanded", "false")
    expect(opinion.locator(".uxtl__file").first).to_be_hidden()
    expect(opinion.locator(".uxtl__editactions").first).to_be_hidden()
    page.wait_for_timeout(200)
    assert chevron.evaluate("el => getComputedStyle(el).transform") == closed_turn


def test_one_row_is_open_at_a_time_and_it_is_the_only_panel(seeded):
    page = seeded
    accent = page.evaluate(RESOLVE_BORDER_COLOUR, "--accent-border")
    neutral = page.evaluate(RESOLVE_BORDER_COLOUR, "--border-default")

    toggle_of(row(page, "Arvamus välja")).click()
    drawn = rows(page)
    (opened,) = [r for r in drawn if r["open"]]
    assert opened["primary"] and opened["border"] == accent, opened
    assert opened["background"] not in TRANSPARENT
    assert all(r["border"] in TRANSPARENT for r in drawn if not r["open"])

    engagement = row(page, "Kaasamine:")
    toggle_of(engagement).click()
    expect(toggle_of(row(page, "Arvamus välja"))).to_have_attribute("aria-expanded", "false")
    drawn = rows(page)
    (opened,) = [r for r in drawn if r["open"]]
    assert not opened["primary"] and opened["border"] == neutral, opened
    assert all(r["border"] in TRANSPARENT for r in drawn if not r["open"])
    # A secondary row's own controls are reachable once it is open.
    expect(engagement.get_by_role("button", name=re.compile("^Muuda"))).to_be_visible()
    expect(engagement.get_by_text("Kustuta", exact=True).first).to_be_visible()


def test_the_pressed_row_stays_under_the_pointer_when_another_closes(seeded):
    """Opening a row lower down closes the one above it; the pressed line is
    held where it was rather than jumping up by the height that closed."""
    page = seeded
    page.set_viewport_size({"width": 1440, "height": 700})
    toggle_of(row(page, "Arvamus välja")).click()
    lower = toggle_of(row(page, "Töövõit"))
    lower.scroll_into_view_if_needed()
    before = lower.bounding_box()["y"]
    lower.click()
    expect(lower).to_have_attribute("aria-expanded", "true")
    assert abs(lower.bounding_box()["y"] - before) <= 1


# ---------------------------------------------------------------------------
# A real disclosure button
# ---------------------------------------------------------------------------


def test_the_toggle_is_a_named_button_the_keyboard_can_use(seeded):
    page = seeded
    button = page.get_by_role("button", name=re.compile(r"^Arvamus välja \d"))
    expect(button).to_have_count(1)
    expect(button).to_have_attribute("aria-expanded", "false")

    button.focus()
    assert page.evaluate("() => document.activeElement.classList.contains('uxtl__toggle')")
    page.keyboard.press("Enter")
    expect(button).to_have_attribute("aria-expanded", "true")
    opinion = row(page, "Arvamus välja")
    expect(opinion.locator(".uxtl__file").first).to_be_visible()

    # Tab goes on into the open panel's own controls, in reading order.
    page.keyboard.press("Tab")
    assert page.evaluate("() => document.activeElement.textContent.trim()") == "Muuda"

    button.focus()
    page.keyboard.press("Space")
    expect(button).to_have_attribute("aria-expanded", "false")

    # The focus ring is drawn around the row the button covers.
    button.focus()
    page.keyboard.press("Shift+Tab")
    page.keyboard.press("Tab")
    outline = button.evaluate("el => getComputedStyle(el).outlineStyle")
    assert outline != "none"


def test_a_row_with_nothing_behind_its_line_has_no_toggle(seeded):
    created = row(seeded, "Teema loodud")
    expect(created.locator(".uxtl__toggle")).to_be_hidden()
    expect(created.locator(".uxtl__msdate")).to_be_visible()


def test_a_link_to_a_row_opens_it(seeded, base_url):
    """`#sissekanne-…` is what a search hit on an entry scrolls to; a hit on a
    closed row would land on a line with none of the words that matched."""
    page = seeded
    entry = row(page, "Avalik sissekanne")
    anchor = entry.get_attribute("id")
    assert anchor and anchor.startswith("sissekanne-")
    page.goto(f"{page.url.split('#')[0]}#{anchor}")
    page.reload()
    page.wait_for_load_state("networkidle")
    entry = page.locator(f"#{anchor}")
    expect(toggle_of(entry)).to_have_attribute("aria-expanded", "true")
    expect(entry.get_by_text("Avalik sissekanne, mida kõik näevad.")).to_be_visible()


def test_without_scripting_every_row_reads_open(browser, browser_context_args, base_url):
    context = browser.new_context(**browser_context_args, java_script_enabled=False)
    page = context.new_page()
    try:
        sign_in_without_script(page, base_url)
        open_matter(page, base_url, OPEN_TITLE)
        history = page.locator("#ajalugu-loend")
        expect(history.locator(".uxtl__file").first).to_be_visible()
        expect(history.get_by_text("Avalik sissekanne, mida kõik näevad.")).to_be_visible()
        toggles = history.locator(".uxtl__toggle")
        assert toggles.count()
        for index in range(toggles.count()):
            expect(toggles.nth(index)).to_be_hidden()
    finally:
        context.close()


def sign_in_without_script(page, base_url: str) -> None:
    """The development sign-in is a plain form, so it works with scripting off."""
    page.goto(f"{base_url}/konto/arendus-sisselogimine/")
    page.get_by_label(MARTIN.display_name, exact=False).check()
    page.get_by_role("button", name="Logi sisse").click()
    page.wait_for_url(f"{base_url}/minu-asjad/")


# ---------------------------------------------------------------------------
# Every width
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("width", WIDTHS)
def test_an_open_row_fits_beside_the_spine_at_every_width(seeded, width):
    page = seeded
    page.set_viewport_size({"width": width, "height": 900})
    page.wait_for_timeout(100)
    assert_no_sideways_scroll(page)

    for words in ("Arvamus välja", "Kaasamine:"):
        article = row(page, words)
        open_kaik_row(article)
        page.wait_for_timeout(100)
        assert_no_sideways_scroll(page)
        (opened,) = [r for r in rows(page) if r["open"]]
        # The panel sits to the right of the spine, never over it, and inside
        # the viewport.
        assert opened["left"] > opened["spineRight"], opened
        assert opened["right"] <= width, opened
        # What it opened onto is on screen and pressable.
        muuda = article.get_by_role("button", name=re.compile("^Muuda"))
        expect(muuda).to_be_visible()
        box = muuda.bounding_box()
        assert box is not None and box["x"] + box["width"] <= width
        at = page.evaluate(
            "([x, y]) => document.elementFromPoint(x, y).closest('button')?.textContent.trim()",
            [box["x"] + box["width"] / 2, box["y"] + box["height"] / 2],
        )
        assert at == "Muuda", (width, words, at)


# ---------------------------------------------------------------------------
# A column swap keeps the row being worked on open
# ---------------------------------------------------------------------------


def test_a_refused_then_accepted_file_keeps_its_row_open(page, base_url, tmp_path):
    """`+ Lisa fail` answers by re-rendering the whole column, refused or not;
    the row it was pressed on comes back open both times, with the refusal in
    it and then with the file in it.

    The `Märge` row is saved through `add_note` by `record_marge`, since
    `+ Lisa · Tavaline` left on 2026-10-07 (docs/adr/0143)."""
    sign_in(page, base_url, SANDRA)
    create_matter(page, base_url, unique_title("Käigu akordion"))
    assert record_marge(page, "Rääkisin ministeeriumiga") == 200

    marge = row(page, "Rääkisin ministeeriumiga")
    expect(toggle_of(marge)).to_have_attribute("aria-expanded", "false")
    open_kaik_row(marge)
    marge.get_by_role("button", name=re.compile(r"^\+ Lisa fail")).click()
    editor = marge.locator("form.uxtl__editform")
    expect(editor).to_be_visible()

    # Refused: nothing chosen.
    editor.get_by_role("button", name="Salvesta", exact=True).click()
    wait_for_htmx(page)
    marge = row(page, "Rääkisin ministeeriumiga")
    expect(toggle_of(marge)).to_have_attribute("aria-expanded", "true")
    expect(marge.locator(".field__error").first).to_be_visible()

    # Accepted.
    paper = tmp_path / "kohtumise-protokoll.pdf"
    paper.write_bytes(b"%PDF-1.4 synthetic minutes")
    marge.locator("form.uxtl__editform input[type=file]").set_input_files(str(paper))
    marge.locator("form.uxtl__editform").get_by_role("button", name="Salvesta", exact=True).click()
    wait_for_htmx(page)
    marge = row(page, "Rääkisin ministeeriumiga")
    expect(toggle_of(marge)).to_have_attribute("aria-expanded", "true")
    expect(marge.get_by_role("link", name=re.compile("kohtumise-protokoll.pdf"))).to_be_visible()
    assert len([r for r in rows(page) if r["open"]]) == 1
