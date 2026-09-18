"""The rest of the surfaces a pasted address can take sideways (QA-10b).

QA-10 fixed `.richtext`, which is where a `Märge` body is rendered, and
`e2e/test_long_token_wrapping.py` holds that proof. The follow-up investigation
asked whether the same 179-character word does the same thing elsewhere, and
named five candidates. They were reproduced one at a time, in the real surface,
with the document measured beside the element — because «this element is wide»
and «the page scrolls» are different claims and only the second one is the defect
a reader meets.

What the measurement found, on `main` at f62884a, at 375 / 420 / 768 / 1440:

===================  ===================================  ======================
surface              horizontal document scroll before    verdict
===================  ===================================  ======================
`.uxtl__mswhat`      522 / 477 / 149 / 0                  vulnerable, fixed
`.uxtl__mssub`       505 / 460 / 132 / 0                  vulnerable, fixed
`.uxtl__msnote`      517 / 472 / 144 / 0                  vulnerable, fixed
`.curact__task`      488 / 443 / 115 / 0                  vulnerable, fixed
`.result__snippet`   0 / 0 / 0 / 0                        clipped, see below
===================  ===================================  ======================

**`.result__snippet` was the one that was not moving the page**, and measuring the
document alone would have reported it clean and closed the question. What it was
doing instead is worse for a reader and invisible to a scroll test: `.card--tight`
around it is `overflow: hidden`, so at 375px the element reported 311px of box
against 989px of content and the missing 678px was simply cut off — no scrollbar,
no ellipsis, and no way to see the rest of the sentence the search had just
matched. It is fixed here for that reason, and asserted differently: the element
must hold its own content, rather than the document must not scroll.

**And the measurement found a sixth surface the candidate list did not name.**
Fixing `.curact__task` left the same page still scrolling 493px, because an open
step is rendered twice — once on the `Praegu` card and once as the
`.uxtl__nexttext` pill on the development that set it — and that pill had no rule
of its own at all. A change that stopped at the five would have been a stylesheet
edit with nothing to show for it on the page it was written for.

Nothing else was touched. `.uxtl__msdate`, `.uxtl__nextlabel` and `.uxtl__nextmode`
carry formatted dates and fixed vocabulary, so no text a person writes reaches
them; they are left alone deliberately rather than swept in with a global rule.

**The assertions are widths, never a computed style.** Reading `overflow-wrap`
back off the element would prove the rule is written down and nothing about
whether it works — the defect is a layout, so this file measures layouts. The same
choice `e2e/test_long_token_wrapping.py` makes, for the same reason.
"""

from __future__ import annotations

from datetime import date, timedelta

import pytest

from e2e.conftest import (
    MARTIN,
    create_matter,
    open_add_panel,
    open_composer,
    sign_in,
    unique_title,
)

pytestmark = pytest.mark.e2e

#: The paste from the QA-10 reproduction, verbatim: 179 characters with nothing
#: in them a browser may break at. Kept identical to the `.richtext` file's token
#: on purpose — the two are the same defect, and a second, gentler string here
#: would make a green result here mean less than a green result there.
PASTED_LINK = (
    "https://eur-lex.europa.eu/legal-content/ET/TXT/HTML/"
    "?uri=CELEX%3A52026PC0142&qid=1758170000000&from=ET&locale=et"
    "&someVeryLongTrackingParameter=abcdefghijklmnopqrstuvwxyz0123456789"
)

#: The tail of the token: long enough that no seeded row can be carrying it, and
#: distinctive enough to pick this file's element out of a chronology that has
#: several of the same class on it.
NEEDLE = "someVeryLongTrackingParameter"

#: 375 is the QA viewport and the narrowest the product claims; 420 is the width
#: the responsive brief names; 768 is the tablet breakpoint; 1440 is the desktop
#: control, where the token is shorter than the content column — so a desktop
#: layout that moved would mean the rule reached further than the word it was
#: written for.
WIDTHS = (375, 420, 768, 1440)

#: Sub-pixel layout lets a box report one more pixel than its content. The same
#: tolerance `e2e/test_long_token_wrapping.py` and `e2e/test_integration_420px.py`
#: allow, copied rather than imported for the reason those two give: neither file
#: should be able to change the other's tolerance by accident.
ROUNDING = 1

#: One element at a time rather than «the widest thing on the page», so a failure
#: names the surface that moved.
MEASURE = """
([selector, needle]) => {
  const el = [...document.querySelectorAll(selector)]
      .find(e => (e.textContent || '').includes(needle));
  const doc = document.documentElement;
  if (!el) return {found: false, docClient: doc.clientWidth, docScroll: doc.scrollWidth};
  return {
    found: true,
    text: el.textContent || '',
    elClient: el.clientWidth,
    elScroll: el.scrollWidth,
    docClient: doc.clientWidth,
    docScroll: doc.scrollWidth,
  };
}
"""


def _estonian(on: date) -> str:
    return f"{on.day}.{on.month}.{on.year}"


def _readings(page, url: str, selector: str) -> list[dict]:
    """The element and the document, at every width, on a fresh load.

    Re-navigating rather than only resizing: the Teema page swaps regions over
    HTMX, and a layout measured after a resize alone can be one the browser has
    not finished reflowing. A load at the target width is the state a reader
    actually arrives in.
    """
    out = []
    for width in WIDTHS:
        page.set_viewport_size({"width": width, "height": 900})
        page.goto(url)
        page.wait_for_load_state("networkidle")
        reading = page.evaluate(MEASURE, [selector, NEEDLE])
        reading["width"] = width
        reading["selector"] = selector
        out.append(reading)
    page.set_viewport_size({"width": 1440, "height": 900})
    return out


def _the_page_does_not_scroll_sideways(readings: list[dict]) -> None:
    for r in readings:
        assert r["found"], (
            f"{r['selector']} carrying the pasted address was not on the page at {r['width']}px"
        )
        assert r["docScroll"] <= r["docClient"] + ROUNDING, (
            f"{r['selector']} at {r['width']}px took the document to {r['docScroll']}px "
            f"against a viewport of {r['docClient']}px"
        )
        assert r["elScroll"] <= r["elClient"] + ROUNDING, (
            f"{r['selector']} at {r['width']}px holds {r['elScroll']}px of content in a "
            f"{r['elClient']}px box"
        )


def _the_whole_address_is_still_there(readings: list[dict]) -> None:
    """Wrapping may not shorten: the address is what the person pasted."""
    for r in readings:
        assert PASTED_LINK in r["text"], (
            f"{r['selector']} at {r['width']}px no longer holds the whole address — "
            "wrapping must not truncate or elide"
        )


def _file_a_development(page, url: str, **fields: str) -> None:
    """One `+ Menetluse areng`, and a wait on the record rather than the network.

    The save swaps `#teema-vaade` wholesale, so the `networkidle` that follows the
    click can be the idle *before* the replacement lands — the flake
    `e2e/test_long_token_wrapping.py` records paying for once already. Waiting
    until the text is rendered is the only signal that means the record exists.
    """
    open_add_panel(page, "lisa-menetluse-areng")
    form = page.locator("#lisa-menetluse-areng")
    for name, value in fields.items():
        form.locator(f"[name={name}]").fill(value)
    form.get_by_role("button", name="Salvesta areng").click()
    page.wait_for_load_state("networkidle")
    marker = fields["title"].split(" ")[-1]
    page.wait_for_function(
        """marker => [...document.querySelectorAll('.uxtl__mswhat')]
               .some(el => (el.textContent || '').includes(marker))""",
        arg=marker,
    )
    page.goto(url)
    page.wait_for_load_state("networkidle")


# ---------------------------------------------------------------------------
# The development row: three authored boxes, and the step it opened
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "selector",
    [".uxtl__mswhat", ".uxtl__msnote", ".curact__task", ".uxtl__nexttext"],
)
def test_a_development_carrying_a_pasted_address_does_not_widen_the_page(
    page, base_url: str, selector: str
):
    """Every region of one development row, each asserted under its own name.

    Parametrised rather than folded into one assertion precisely because
    `.curact__task` and `.uxtl__nexttext` render the *same* sentence: fixing the
    card and leaving the pill is the half-fix this file exists to make impossible
    to repeat, and a single «the step does not overflow» would have passed on it.

    One Matter per case rather than a shared one, because the browser suite runs
    against a world that is never reset between files: a fixture holding a Matter
    across parameters would make each case depend on the one before it.
    """
    sign_in(page, base_url, MARTIN)
    url = create_matter(page, base_url, unique_title("Pikk viide"))
    _file_a_development(
        page,
        url,
        title=f"Ministeerium saatis eelnõu: {PASTED_LINK}",
        occurred_on=_estonian(date.today() - timedelta(days=2)),
        note=f"Vaata ka {PASTED_LINK}",
        next_text=f"Loen uue versiooni läbi {PASTED_LINK}",
        next_date=_estonian(date.today() + timedelta(days=4)),
    )

    readings = _readings(page, url, selector)
    _the_page_does_not_scroll_sideways(readings)
    _the_whole_address_is_still_there(readings)


# ---------------------------------------------------------------------------
# The milestone's second line
# ---------------------------------------------------------------------------


def test_a_valine_seisukoht_summary_does_not_widen_the_page(page, base_url: str):
    """`.uxtl__mssub` — what somebody else's position says, in this office's file.

    Its own Matter because the sub-line's content comes from a different record
    from the ones above, and a page carrying both would not say which of them the
    document was following.
    """
    sign_in(page, base_url, MARTIN)
    url = create_matter(page, base_url, unique_title("Pikk viide seisukohas"))

    open_add_panel(page, "lisa-valine-seisukoht")
    box = page.locator("#valine-seisukoht-otsi")
    box.click()
    # Typed a key at a time, the way the picker is driven everywhere else: the
    # chips are labels whose input is clipped, so ticking the control directly
    # asserts something a person never does (e2e/test_external_position.py).
    box.type("Näidismi", delay=20)
    page.locator("#valine-seisukoht-tulemused").get_by_role(
        "option", name="Näidisministeerium", exact=True
    ).click()
    page.locator("#lisa-valine-seisukoht [name=summary]").fill(
        f"Nende põhjendus on siin {PASTED_LINK}"
    )
    page.locator("#lisa-valine-seisukoht").get_by_role("button", name="Salvesta").click()
    page.wait_for_load_state("networkidle")
    page.wait_for_function(
        """needle => [...document.querySelectorAll('.uxtl__mssub')]
               .some(el => (el.textContent || '').includes(needle))""",
        arg=NEEDLE,
    )

    readings = _readings(page, url, ".uxtl__mssub")
    _the_page_does_not_scroll_sideways(readings)
    _the_whole_address_is_still_there(readings)


# ---------------------------------------------------------------------------
# The search snippet: a different defect, and a different assertion
# ---------------------------------------------------------------------------


def test_a_search_snippet_is_not_clipped_by_the_card_around_it(page, base_url: str):
    """`.result__snippet` — the document was never the symptom here.

    `.card--tight` is `overflow: hidden`, so the page measured clean at every
    width while 678px of the excerpt was cut off at 375px. The assertion is
    therefore the element's own box against its own content: whatever the reader
    searched for, the sentence it was found in has to be readable.

    The document is measured beside it, as the guard that this fix did not trade a
    clipped excerpt for a scrolling page.
    """
    sign_in(page, base_url, MARTIN)
    create_matter(page, base_url, unique_title("Pikk viide margmes"))
    open_composer(page)
    page.locator("#lisa-marge .composer__body").fill(f"Vaata eelnõu siit: {PASTED_LINK}")
    page.locator("#lisa-marge button[type=submit]").click()
    page.wait_for_load_state("networkidle")
    page.wait_for_function(
        """needle => [...document.querySelectorAll('.richtext')]
               .some(el => (el.textContent || '').includes(needle))""",
        arg=NEEDLE,
    )

    readings = _readings(page, f"{base_url}/otsing/?q=eelnõu", ".result__snippet")
    _the_page_does_not_scroll_sideways(readings)


# ---------------------------------------------------------------------------
# The control: ordinary prose is unchanged
# ---------------------------------------------------------------------------


def test_ordinary_prose_in_a_development_still_occupies_one_line(page, base_url: str):
    """`anywhere` is a last resort, and a sentence with room to spare keeps it.

    The risk of this rule is not that it fails to wrap; it is that it starts
    breaking words that had somewhere better to break. So a development whose
    every word fits the narrowest column this product claims is filed here, and
    its headline is required to fit its own box at every width — which it cannot
    do if `anywhere` has begun splitting ordinary Estonian mid-word.
    """
    sign_in(page, base_url, MARTIN)
    url = create_matter(page, base_url, unique_title("Tavaline lause"))
    _file_a_development(
        page,
        url,
        title="Ministeerium saatis eelnõu teisele ringile",
        occurred_on=_estonian(date.today() - timedelta(days=1)),
    )

    for width in WIDTHS:
        page.set_viewport_size({"width": width, "height": 900})
        page.goto(url)
        page.wait_for_load_state("networkidle")
        reading = page.evaluate(MEASURE, [".uxtl__mswhat", "teisele ringile"])
        assert reading["found"], f"the ordinary headline was not on the page at {width}px"
        assert reading["elScroll"] <= reading["elClient"] + ROUNDING
        assert reading["docScroll"] <= reading["docClient"] + ROUNDING
    page.set_viewport_size({"width": 1440, "height": 900})
