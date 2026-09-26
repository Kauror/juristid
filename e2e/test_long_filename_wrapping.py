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

from e2e.conftest import MARTIN, create_matter, open_add_panel, sign_in
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
    open_add_panel(page, "arvamus-teiste")
    box = page.locator("#valine-seisukoht-otsi")
    box.click()
    box.type("Näidismi", delay=20)
    page.locator("#valine-seisukoht-tulemused").get_by_role(
        "option", name="Näidisministeerium", exact=True
    ).click()
    page.locator("#arvamus-teiste [name=summary]").fill("Seletuskiri on lisatud.")
    page.locator("#arvamus-teiste input[type=file]").set_input_files(
        {"name": filename, "mimeType": "application/pdf", "buffer": b"%PDF-1.4 synthetic"}
    )
    page.locator("#arvamus-teiste").get_by_role("button", name="Salvesta").click()
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


def test_ordinary_headings_are_laid_out_as_before_at_desktop(page, base_url):
    """`anywhere` breaks inside a word only when the word cannot fit: a normal
    title still occupies one line at 1440."""
    sign_in(page, base_url, MARTIN)
    url = create_matter(page, base_url, unique_title("Tavaline pealkiri"))
    page.set_viewport_size({"width": 1440, "height": 900})
    page.goto(f"{url}muuda/")
    page.wait_for_load_state("networkidle")
    # One line box for the heading's text: a Range over it reports one rect.
    lines = page.locator(".pagehead__title").evaluate(
        """el => { const r = document.createRange(); r.selectNodeContents(el);
                   return new Set([...r.getClientRects()].map(b => Math.round(b.top))).size; }"""
    )
    assert lines == 1
