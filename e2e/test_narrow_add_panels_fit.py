"""The `LISA TEEMALE` panels fit a 320 px window (JUX-009, JUX-010).

WCAG 1.4.10 asks for no horizontal scrolling at 320 CSS px, which is also what
400 % zoom on a 1280 px window looks like. The 2026-10-17 audit found two ways
the Teema page's add panels broke it:

* **JUX-009.** A file chosen with a long name that has no spaces — the
  ordinary ministry file name, `Eelnou_seletuskiri_lisa_…_2026.pdf` — widened
  the drop box, and with it the form and the page, to about 950 px.
* **JUX-010.** `+ Kaasamine` (Alusta kaasamist, Lisa tagasiside) and
  `Koja arvamus` overflowed by 11 px with nothing chosen at all.

Measured the way `e2e/test_long_filename_wrapping.py` measures: the document's
own scroll width against the window, after the panel has opened.
"""

from __future__ import annotations

import pytest
from playwright.sync_api import expect

from e2e.conftest import SANDRA, create_matter, open_add_panel, sign_in, unique_title

pytestmark = pytest.mark.e2e

NARROW = {"width": 320, "height": 800}
LONG_FILE = "Eelnou_seletuskiri_" + "_".join(["lisa"] * 24) + "_versioon_2026.pdf"


def _overflow(page) -> int:
    return page.evaluate("() => document.documentElement.scrollWidth - window.innerWidth")


@pytest.fixture
def matter(page, base_url):
    sign_in(page, base_url, SANDRA)
    create_matter(page, base_url, unique_title("Kitsas"))
    page.set_viewport_size(NARROW)


@pytest.mark.parametrize(
    "panel",
    ["kaasamine-alusta", "kaasamine-tagasiside", "arvamus-koja"],
)
def test_the_panel_fits_320_px(page, matter, panel):
    open_add_panel(page, panel)
    expect(page.locator(f"#{panel} form")).to_be_visible()
    assert _overflow(page) <= 0, f"{panel} scrolls sideways by {_overflow(page)}px"


@pytest.mark.parametrize("panel", ["marge-muu", "kaasamine-tagasiside"])
def test_a_long_file_name_wraps_inside_the_panel(page, matter, panel):
    open_add_panel(page, panel)
    form = page.locator(f"#{panel} form")
    form.locator('input[type="file"]').set_input_files(
        [{"name": LONG_FILE, "mimeType": "application/pdf", "buffer": b"%PDF-1.4 x"}]
    )
    text = form.locator("[data-filedrop-text]")
    expect(text).to_contain_text("Eelnou_seletuskiri")
    assert _overflow(page) <= 0, f"{panel} scrolls sideways by {_overflow(page)}px"
    # Wrapped, not cut: the whole name is still there.
    assert LONG_FILE in text.inner_text().replace("\n", "")
