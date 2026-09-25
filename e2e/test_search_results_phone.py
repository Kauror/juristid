"""Search results are readable on a phone and stay dense on a desktop (ENG-100).

At 375 px a result title was a single ellipsised line of about 135 px — two
words — beside the kind badge and the Hetkeseis, so two results on Teemad whose
titles begin alike could not be told apart. Below 40rem the title now takes
the row and up to two lines, and the badges sit beneath it; at 768 px and wider
the row is the one dense line it was.

Measured, not read off the stylesheet: the page must not scroll sideways at any
width, a phone title must be wider than half the screen and at most two lines
tall, and a desktop title must be one line.
"""

from __future__ import annotations

import pytest

from e2e.conftest import MARTIN, sign_in

QUERY = "/otsing/?q=eeln%C3%B5u"
PHONE = (320, 375, 420)
DESKTOP = (768, 1024, 1440)


def _overflows(page) -> bool:
    return page.evaluate(
        "() => document.documentElement.scrollWidth > document.documentElement.clientWidth + 1"
    )


def _title_metrics(page) -> dict:
    return page.evaluate(
        """() => {
            const title = document.querySelector('.result__title');
            const style = getComputedStyle(title);
            const line = parseFloat(style.lineHeight) || parseFloat(style.fontSize) * 1.4;
            const box = title.getBoundingClientRect();
            return {width: box.width, height: box.height, line: line,
                    whiteSpace: style.whiteSpace, right: box.right};
        }"""
    )


@pytest.mark.parametrize("width", PHONE)
def test_a_phone_title_takes_the_row_and_at_most_two_lines(page, base_url, width):
    page.set_viewport_size({"width": width, "height": 812})
    sign_in(page, base_url, MARTIN)
    page.goto(base_url + QUERY)

    metrics = _title_metrics(page)
    assert not _overflows(page), width
    assert metrics["width"] > width * 0.6, metrics
    assert metrics["right"] <= width + 1, metrics
    assert metrics["height"] <= 2 * metrics["line"] + 2, metrics
    assert metrics["whiteSpace"] == "normal", metrics


@pytest.mark.parametrize("width", DESKTOP)
def test_a_desktop_title_stays_one_dense_line(page, base_url, width):
    page.set_viewport_size({"width": width, "height": 900})
    sign_in(page, base_url, MARTIN)
    page.goto(base_url + QUERY)

    metrics = _title_metrics(page)
    assert not _overflows(page), width
    assert metrics["whiteSpace"] == "nowrap", metrics
    assert metrics["height"] <= metrics["line"] + 2, metrics


@pytest.mark.parametrize("width", (*PHONE, *DESKTOP))
def test_the_full_title_is_offered_where_it_is_cut(page, base_url, width):
    page.set_viewport_size({"width": width, "height": 900})
    sign_in(page, base_url, MARTIN)
    page.goto(base_url + QUERY)

    title = page.locator(".result__title").first
    assert title.get_attribute("title") == title.inner_text().strip()
