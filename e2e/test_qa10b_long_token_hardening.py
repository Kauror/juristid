"""The sibling surfaces QA-10 measured and deliberately left alone (QA-10b).

QA-10 fixed `.richtext` — the authored bodies on `Teema käik` — and its own
report named five more selectors carrying the same defect class, with the
numbers beside them, under the heading «Reported, not fixed». They were left
because each sits in a different block with its own reasoning and none of them
is the `.richtext` primitive, so widening that selector would have been a guess.
This file is that follow-up, measured rather than guessed.

**Four of the five take the whole page sideways.** Measured on this branch's
parent, with one realistic EUR-Lex address carrying its query string and two
tracking parameters:

    #teema-vaade-wrap .uxtl__msnote   380px of document scroll at 375
    #teema-vaade-wrap .uxtl__mswhat   368px
    #teema-vaade-wrap .uxtl__mssub    368px
    .curact__task / .curact__text     334px

**The fifth does something quieter and worse.** `.result__snippet` never moves
the document — 0px at every width, exactly as QA-10 reported — but the element
overflows *its own box* by 519px at 375, and an ancestor clips it. The page
looks fine and the reader simply cannot see the rest of the excerpt.

**`.uxtl__msnote` is the one that matters most**, and the reason this is not a
cosmetic round. Since QA-05 it renders a `Menetluse areng`'s `Juristi märkus` —
free prose a lawyer types about a procedural step — and pasting the link they
were sent is exactly what somebody does there.

**The assertion is the page, not the stylesheet**, which is the rule
`e2e/test_long_token_wrapping.py` states and the reason it exists: a test
reading `overflow-wrap` off the computed style proves the rule is written down
and nothing about whether it works. The defect is a width, so this measures
widths — the document's, and the element's own box beside it, so a failure says
which of the two moved.

Every surface is measured at four widths, because the defect scaled with the
viewport rather than appearing at one of them. 1440 is the control: the token is
shorter than the desktop content column, so a desktop layout that changed would
mean the rule reached further than the word it was written for.
"""

from __future__ import annotations

import datetime as dt

import pytest

from e2e.conftest import (
    MARTIN,
    create_matter,
    open_add_panel,
    sign_in,
    unique_title,
)

pytestmark = pytest.mark.e2e

#: One realistic address, in the shape QA-10's own reproduction used: a query
#: string and the tracking parameters a newsletter link carries. Nothing in it
#: a browser may break at.
PASTED_LINK = (
    "https://eur-lex.europa.eu/legal-content/ET/TXT/HTML/"
    "?uri=CELEX:32024R1781&qid=1726650000000&utm_source=uudiskiri"
    "&utm_medium=email&utm_campaign=pakendiseadus-2026-09"
)

#: A fragment distinctive enough to find the one element under test among the
#: several each page renders, and short enough to survive wrapping unchanged.
MARK = "CELEX:32024R1781"

#: 375 is the QA viewport and the narrowest the product claims; 420 is the width
#: the responsive brief names; 768 is the tablet breakpoint; 1440 is the desktop
#: control. The same four `e2e/test_long_token_wrapping.py` measures at.
WIDTHS = (375, 420, 768, 1440)

#: Sub-pixel layout means a document can report one more pixel of scroll than
#: content. The convention `e2e/test_long_token_wrapping.py` and
#: `e2e/test_integration_420px.py` both keep, copied rather than imported so
#: neither file can change the other's tolerance by accident.
ROUNDING = 1

#: What the measurement returns for one selector: the document's overflow, and
#: the token-bearing element's own.
MEASURE = """
([selector, mark]) => {
  const doc = document.documentElement;
  const all = [...document.querySelectorAll(selector)];
  const el = all.find((e) => (e.textContent || '').includes(mark)) || null;
  return {
    documentOverflow: doc.scrollWidth - doc.clientWidth,
    found: !!el,
    elementOverflow: el ? el.scrollWidth - el.clientWidth : null,
    elementScroll: el ? el.scrollWidth : null,
    elementClient: el ? el.clientWidth : null,
    text: el ? (el.textContent || '') : '',
  };
}
"""


def _sweep(page, selector: str) -> dict[int, dict]:
    """One selector, measured at every width, with the viewport left at desktop."""
    readings: dict[int, dict] = {}
    for width in WIDTHS:
        page.set_viewport_size({"width": width, "height": 900})
        # The layout has to settle before it is measured; the sweeps in
        # `e2e/test_integration_420px.py` wait the same way.
        page.wait_for_timeout(200)
        readings[width] = page.evaluate(MEASURE, [selector, MARK])
    page.set_viewport_size({"width": 1440, "height": 900})
    return readings


def _assert_holds(readings: dict[int, dict], selector: str) -> None:
    """The page holds, the element holds, and the whole token is still there.

    All three, because a page that stops scrolling sideways is not by itself a
    fix: wrapping must not hide, and the address is what the person pasted.
    """
    for width, r in readings.items():
        assert r["found"], f"{selector} carrying {MARK} is not on the page at {width}px"
        assert r["documentOverflow"] <= ROUNDING, (
            f"{selector}: the page scrolls sideways at {width}px — "
            f"{r['documentOverflow']}px, element {r['elementScroll']}/{r['elementClient']}"
        )
        assert r["elementOverflow"] <= ROUNDING, (
            f"{selector}: the element overflows its own box at {width}px — "
            f"{r['elementOverflow']}px ({r['elementScroll']}/{r['elementClient']})"
        )
        assert MARK in r["text"], f"{selector}: wrapping ate part of the address at {width}px"


def _new_matter(page, base_url: str, prefix: str) -> str:
    """A Matter of this test's own, owned, at desktop width.

    **Filed with an owner**, for the reason `e2e/test_long_token_wrapping.py`
    records: `e2e/test_ux_pass.py` asserts against the one seeded unassigned
    Matter and sorts after this file, so an ownerless Matter left here walks its
    row down the register for every one of them.
    """
    page.set_viewport_size({"width": 1440, "height": 900})
    sign_in(page, base_url, MARTIN)
    return create_matter(page, base_url, unique_title(prefix), owner=MARTIN)


def _past(days: int) -> str:
    return (dt.date.today() - dt.timedelta(days=days)).strftime("%d.%m.%Y")


def _file_a_development(page, *, title: str, note: str) -> None:
    """One `Menetluse areng` through the real panel."""
    open_add_panel(page, "lisa-menetluse-areng")
    page.locator("#lisa-menetluse-areng input[name=title]").fill(title)
    page.locator("#lisa-menetluse-areng input[name=occurred_on]").fill(_past(3))
    page.locator("#lisa-menetluse-areng textarea[name=note]").fill(note)
    with page.expect_response(
        lambda r: "/lisa/menetluse-areng/" in r.url and r.request.method == "POST"
    ) as caught:
        page.locator("#lisa-menetluse-areng button[type=submit]").click()
    assert caught.value.status == 200, f"the development was refused: {caught.value.status}"
    page.wait_for_load_state("networkidle")


def test_a_pasted_link_in_a_juristi_markus_never_scrolls_the_page_sideways(page, base_url):
    """`.uxtl__msnote` — the QA-05 surface, and the one a lawyer types prose into.

    380px of document scroll at 375 before this rule, on a note whose whole
    content is a sentence and the link it refers to.
    """
    _new_matter(page, base_url, "QA10b juristi märkus")
    _file_a_development(
        page,
        title="Ministeerium saatis uue eelnõu versiooni",
        note=f"Versioon ei arvesta meie ettepanekut, vaata {PASTED_LINK}",
    )

    _assert_holds(_sweep(page, "#teema-vaade-wrap .uxtl__msnote"), ".uxtl__msnote")


def test_a_pasted_link_in_a_milestone_headline_never_scrolls_the_page_sideways(page, base_url):
    """`.uxtl__mswhat` — `Mis menetluses juhtus`, which is also free text.

    368px at 375 before. This element never overflowed its own box: it *grew*
    one, to 697px inside a 317px column, and every ancestor being
    `overflow-x: visible` handed that width to the document.
    """
    _new_matter(page, base_url, "QA10b pealkiri")
    _file_a_development(page, title=f"Ministeerium saatis {PASTED_LINK}", note="")

    _assert_holds(_sweep(page, "#teema-vaade-wrap .uxtl__mswhat"), ".uxtl__mswhat")


def test_a_pasted_link_in_a_milestone_subtitle_never_scrolls_the_page_sideways(page, base_url):
    """`.uxtl__mssub` — here a commencement's own title, 368px at 375 before.

    A commencement rather than a deadline because the chronology renders what
    has already happened: a date in the future has no row to measure.
    """
    _new_matter(page, base_url, "QA10b alapealkiri")
    open_add_panel(page, "lisa-joustumine")
    page.locator("#lisa-joustumine input[name=effective_title]").fill(f"Jõustus {PASTED_LINK}")
    page.locator("#lisa-joustumine input[name=effective_on]").fill(_past(15))
    with page.expect_response(lambda r: r.request.method == "POST") as caught:
        page.locator("#lisa-joustumine button[type=submit]").click()
    assert caught.value.status == 200, f"the commencement was refused: {caught.value.status}"
    page.wait_for_load_state("networkidle")

    _assert_holds(_sweep(page, "#teema-vaade-wrap .uxtl__mssub"), ".uxtl__mssub")


def test_a_pasted_link_in_the_current_action_never_scrolls_the_page_sideways(page, base_url):
    """`.curact__task` — `Järgmiseks`, 334px at 375 before.

    Both the flex wrapper the QA report named and `.curact__text` inside it,
    because the wrapper is where the overflow showed and the child is what
    actually holds the words.
    """
    _new_matter(page, base_url, "QA10b järgmiseks")
    open_add_panel(page, "lisa-jargmine")
    page.locator("#lisa-jargmine input[name=text]").fill(f"Loe läbi {PASTED_LINK}")
    deadline = page.locator("#lisa-jargmine input[name=target_date]")
    if deadline.count():
        deadline.fill((dt.date.today() + dt.timedelta(days=5)).strftime("%d.%m.%Y"))
    with page.expect_response(
        lambda r: "/jargmiseks/" in r.url and r.request.method == "POST"
    ) as caught:
        page.locator("#lisa-jargmine button[type=submit]").click()
    assert caught.value.status == 200, f"the next action was refused: {caught.value.status}"
    page.wait_for_load_state("networkidle")

    _assert_holds(_sweep(page, ".curact__task"), ".curact__task")
    _assert_holds(_sweep(page, ".curact__text"), ".curact__text")


def test_a_search_excerpt_does_not_clip_the_address_it_is_quoting(page, base_url):
    """`.result__snippet` — the quiet one, and the reason it is in this round.

    It never moved the document: 0px at every width, before and after, exactly
    as QA-10 reported. What it did was overflow its own box by 519px at 375
    while an ancestor clipped it — so the page looked fine and the reader simply
    could not see the rest of the excerpt they had searched for.

    That is why this test asserts the element's own box and the presence of the
    address, not just the document: on this surface the document was never the
    symptom.
    """
    _new_matter(page, base_url, "QA10b otsingu väljavõte")
    open_add_panel(page, "lisa-marge")
    page.locator("#lisa-marge textarea[name=body]").fill(
        f"Pakendidirektiivi ülevaade {PASTED_LINK} palun vaata läbi"
    )
    with page.expect_response(lambda r: r.request.method == "POST") as caught:
        page.locator("#lisa-marge button[type=submit]").click()
    assert caught.value.status == 200, f"the note was refused: {caught.value.status}"
    page.wait_for_load_state("networkidle")

    page.goto(f"{base_url}/otsing/?q=Pakendidirektiivi")
    page.wait_for_load_state("networkidle")

    _assert_holds(_sweep(page, ".result__snippet"), ".result__snippet")


def test_ordinary_prose_still_breaks_only_at_spaces(page, base_url):
    """The control: `anywhere` is a last resort, not a new way of setting text.

    A headline and a note with room to spare must lay out exactly as they did
    before the rule existed. A break inside a word is taken only when the word
    cannot fit on a line of its own, so a sentence of ordinary Estonian must
    still occupy the same number of lines — measured as height, because that is
    what a changed line count actually does to the page.
    """
    _new_matter(page, base_url, "QA10b tavaline proosa")
    _file_a_development(
        page,
        title="Ministeerium saatis eelnõu teisele kooskõlastusringile",
        note="Uus versioon arvestab meie varasemat ettepanekut osaliselt.",
    )

    heights = """
    () => {
      const pick = (sel) => {
        const el = document.querySelector(sel);
        return el ? Math.round(el.getBoundingClientRect().height) : null;
      };
      return {
        what: pick('#teema-vaade-wrap .uxtl__mswhat'),
        note: pick('#teema-vaade-wrap .uxtl__msnote'),
        documentOverflow:
          document.documentElement.scrollWidth - document.documentElement.clientWidth,
      };
    }
    """
    for width in WIDTHS:
        page.set_viewport_size({"width": width, "height": 900})
        page.wait_for_timeout(200)
        r = page.evaluate(heights)
        assert r["what"], f"no milestone headline at {width}px"
        assert r["note"], f"no lawyer note at {width}px"
        assert r["documentOverflow"] <= ROUNDING, (
            f"ordinary prose scrolls the page at {width}px: {r['documentOverflow']}px"
        )
