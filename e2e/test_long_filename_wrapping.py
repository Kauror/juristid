"""An underscore-joined filename and a long compound title do not widen the page (ENG-094).

The QA-10 class, on the surfaces its two sweeps did not reach. An ordinary file
from a ministry is often one token — `Eelnou_seletuskiri_lisa_…_2026.pdf` — and
three places sized themselves to it:

* the chronology's file link, `#teema-vaade-wrap .uxtl__file` (inline-flex);
* the document page's heading, `.pagehead__title` in a flex row;
* `Muuda teemat`'s context line, `.pagehead__context`, carrying the Matter title.

Measured the way `e2e/test_timeline_long_token_wrapping.py` measures: the
element and the document, at every width, on a fresh load — and the whole name
must still be there, because wrapping may not shorten what somebody filed.
"""

from __future__ import annotations

import pytest

from e2e.conftest import (
    AVALDATUD_MUJAL,
    FEEDBACK_PICKER,
    MARTIN,
    choose_organisation,
    create_matter,
    open_feedback_form,
    sign_in,
)
from e2e.titles import unique_title

pytestmark = pytest.mark.e2e

LONG_FILE = "Eelnou_seletuskiri_" + "_".join(["lisa"] * 24) + "_versioon_2026.pdf"
#: A compound the way Estonian legislative titles really are, and then some.
LONG_WORD = "Pakendiseaduse" + "muutmisetapiline" * 5
NEEDLE_FILE = "Eelnou_seletuskiri"
WIDTHS = (320, 375, 420, 768, 1440)
ROUNDING = 1

MEASURE = """
([selector, needle]) => {
  const el = [...document.querySelectorAll(selector)]
      .find(e => (e.textContent || '').includes(needle));
  const doc = document.documentElement;
  if (!el) return {found: false, docClient: doc.clientWidth, docScroll: doc.scrollWidth};
  return {found: true, text: el.textContent || '', elClient: el.clientWidth,
          elScroll: el.scrollWidth, docClient: doc.clientWidth, docScroll: doc.scrollWidth};
}
"""


def _readings(page, url: str, selector: str, needle: str) -> list[dict]:
    out = []
    for width in WIDTHS:
        page.set_viewport_size({"width": width, "height": 900})
        page.goto(url)
        page.wait_for_load_state("networkidle")
        reading = page.evaluate(MEASURE, [selector, needle])
        reading.update(width=width, selector=selector)
        out.append(reading)
    page.set_viewport_size({"width": 1440, "height": 900})
    return out


def _holds(readings: list[dict], whole: str) -> None:
    for r in readings:
        assert r["found"], f"{r['selector']} was not on the page at {r['width']}px"
        assert r["docScroll"] <= r["docClient"] + ROUNDING, (
            f"{r['selector']} at {r['width']}px took the page to {r['docScroll']}px "
            f"against {r['docClient']}px"
        )
        assert r["elScroll"] <= r["elClient"] + ROUNDING, (
            f"{r['selector']} at {r['width']}px holds {r['elScroll']}px in {r['elClient']}px"
        )
        assert whole in r["text"].replace("\n", "").replace(" ", ""), (
            f"{r['selector']} at {r['width']}px no longer holds the whole value"
        )


def _file_a_position_with(page, filename: str) -> None:
    # «Teiste arvamus» is `Lisa tagasiside` · «Avaldatud mujal» since docs/adr/0170.
    form = open_feedback_form(page, AVALDATUD_MUJAL)
    choose_organisation(page, FEEDBACK_PICKER)
    form.locator("[name=summary]").fill("Seletuskiri on lisatud.")
    form.locator("input[type=file]").first.set_input_files(
        {"name": filename, "mimeType": "application/pdf", "buffer": b"%PDF-1.4 synthetic"}
    )
    form.get_by_role("button", name="Salvesta", exact=True).click()
    page.wait_for_load_state("networkidle")
    page.wait_for_function(
        """needle => [...document.querySelectorAll('.uxtl__file')]
               .some(el => (el.textContent || '').includes(needle))""",
        arg=NEEDLE_FILE,
    )


def test_a_long_filename_does_not_widen_the_teema_or_the_document_page(page, base_url):
    sign_in(page, base_url, MARTIN)
    url = create_matter(page, base_url, unique_title("Pikk failinimi"))
    _file_a_position_with(page, LONG_FILE)

    _holds(_readings(page, url, "#teema-vaade-wrap .uxtl__file", NEEDLE_FILE), LONG_FILE)

    page.goto(f"{url}dokumendid/")
    page.wait_for_load_state("networkidle")
    detail = page.locator("a[href*='/dokumendid/']").filter(has_text=NEEDLE_FILE).first
    href = detail.get_attribute("href") if detail.count() else None
    if href is None:
        detail = page.locator(".doctable a[href^='/dokumendid/']").first
        href = detail.get_attribute("href")
    assert href, "no document detail link on Dokumendid"
    _holds(_readings(page, base_url + href, ".pagehead__title", NEEDLE_FILE), LONG_FILE)


def test_a_long_compound_title_does_not_widen_muuda_teemat(page, base_url):
    sign_in(page, base_url, MARTIN)
    title = unique_title(LONG_WORD)
    url = create_matter(page, base_url, title)

    _holds(_readings(page, f"{url}muuda/", ".pagehead__context", LONG_WORD), LONG_WORD)
    # And the Teema page's own heading, which the same title fills.
    _holds(_readings(page, url, ".matterhead__title", LONG_WORD), LONG_WORD)


def test_a_long_compound_title_does_not_widen_minu_asjad(page, base_url):
    """The owner's desk lists the same title in several bands.

    The one-line rows clip it, but the «Järgmise tegevuseta» rail shows it
    whole, and did not wrap it: on 2026-10-11 a CI shard that ran this file
    before `e2e/test_work_surfaces_review.py` found Minu asjad scrolling 225px
    sideways at 375px. That rail lists the quietest Matters first, so a title
    filed now may fall past its limit in a busy world. The whole page is
    therefore measured at every width with the title on it (the newest event
    always is), and every title the rail does show must be one that wraps.
    """
    sign_in(page, base_url, MARTIN)
    create_matter(page, base_url, unique_title(LONG_WORD))
    for width in WIDTHS:
        page.set_viewport_size({"width": width, "height": 900})
        page.goto(f"{base_url}/minu-asjad/")
        page.wait_for_load_state("networkidle")
        assert LONG_WORD in (page.locator("main").text_content() or ""), (
            f"the long title is nowhere on Minu asjad at {width}px; this proves nothing"
        )
        width_of = page.evaluate(
            "() => [document.documentElement.scrollWidth, document.documentElement.clientWidth]"
        )
        assert width_of[0] <= width_of[1] + ROUNDING, (
            f"Minu asjad at {width}px is {width_of[0]}px wide in {width_of[1]}px"
        )
        wraps = page.locator(".quietrow__title").evaluate_all(
            "els => els.map(el => getComputedStyle(el).overflowWrap)"
        )
        assert all(value == "anywhere" for value in wraps), wraps
    page.set_viewport_size({"width": 1440, "height": 900})


def test_ordinary_headings_are_laid_out_as_before_at_desktop(page, base_url):
    """`anywhere` breaks inside a word only when the word cannot fit: a normal
    title still occupies one line at 1440."""
    sign_in(page, base_url, MARTIN)
    url = create_matter(page, base_url, unique_title("Harilik pealkiri"))
    page.set_viewport_size({"width": 1440, "height": 900})
    page.goto(f"{url}muuda/")
    page.wait_for_load_state("networkidle")
    # One line box for the heading's text: a Range over it reports one rect.
    lines = page.locator(".pagehead__title").evaluate(
        """el => { const r = document.createRange(); r.selectNodeContents(el);
                   return new Set([...r.getClientRects()].map(b => Math.round(b.top))).size; }"""
    )
    assert lines == 1
