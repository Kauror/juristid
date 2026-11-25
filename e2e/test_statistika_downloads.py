"""Statistika's panel downloads in a browser (UX-005, docs/adr/0214 §8).

The numbers, the scope and the audit row are proved against the database in
`tests/test_statistika_chart_downloads`. What only a browser shows: the menu
hands over a file for each format, and the PNG drawn on a canvas from the SVG is
twice the drawing's size, opaque in one variant and really transparent in the
other — under the page's own Content-Security-Policy.
"""

from __future__ import annotations

import struct
import zlib

import pytest
from playwright.sync_api import expect

from e2e.conftest import MARTIN, open_disclosure, sign_in

pytestmark = pytest.mark.e2e

#: The SVG's own width (app/reporting/chart_svg.WIDTH); the PNG is drawn at 2×.
SVG_WIDTH = 1200


def _png(path: str) -> tuple[int, int, int]:
    """Width, height and the top-left pixel's alpha of an RGBA PNG.

    The first pixel of the first row is stored as itself under every PNG row
    filter (its predictors are all zero), so no unfiltering is needed for it.
    """
    data = open(path, "rb").read()
    assert data[:8] == b"\x89PNG\r\n\x1a\n"
    width, height, depth, colour = struct.unpack(">IIBB", data[16:26])
    assert (depth, colour) == (8, 6), "an 8-bit RGBA image"
    idat, at = b"", 8
    while at < len(data):
        (length,) = struct.unpack(">I", data[at : at + 4])
        if data[at + 4 : at + 8] == b"IDAT":
            idat += data[at + 8 : at + 8 + length]
        at += 12 + length
    raw = zlib.decompress(idat)
    return width, height, raw[4]


def _download(page, menu, label: str, tmp_path) -> str:
    open_disclosure(menu)
    role = "button" if label.startswith("PNG") else "link"
    with page.expect_download() as info:
        menu.get_by_role(role, name=label, exact=True).click()
    download = info.value
    path = str(tmp_path / download.suggested_filename)
    download.save_as(path)
    return path


def test_a_chart_panel_downloads_in_all_four_formats(page, base_url, tmp_path):
    sign_in(page, base_url, MARTIN)
    page.goto(f"{base_url}/statistika/")
    panel = page.locator("section.statpanel").filter(has=page.locator("#stat-kuud"))
    menu = panel.locator("details.statdownload")
    expect(menu).to_have_count(1)

    opaque = _download(page, menu, "PNG", tmp_path)
    clear = _download(page, menu, "PNG (läbipaistev)", tmp_path)
    svg = _download(page, menu, "SVG", tmp_path)
    xlsx = _download(page, menu, "Excel", tmp_path)

    width, height, alpha = _png(opaque)
    assert width == 2 * SVG_WIDTH and height > 400
    assert alpha == 255, "the opaque PNG has a white ground"
    width, _height, alpha = _png(clear)
    assert width == 2 * SVG_WIDTH
    assert alpha == 0, "the transparent PNG's empty corner is transparent"
    assert open(svg, encoding="utf-8").read().startswith("<?xml")
    assert open(xlsx, "rb").read(2) == b"PK"


def test_a_table_panel_offers_excel_only(page, base_url):
    sign_in(page, base_url, MARTIN)
    page.goto(f"{base_url}/statistika/")
    panel = page.locator("section.statpanel").filter(has=page.locator("#stat-liigid"))
    menu = panel.locator("details.statdownload")
    open_disclosure(menu)

    expect(menu.locator(".opinionmenu__item")).to_have_text(["Excel"])
