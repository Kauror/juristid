"""«teemat menetluses» and the explanation beside it, in a real browser.

The owner's review of 2026-10-12 renamed Minu asjad's first figure and gave it
a quiet `i` with «Menetluses olev teema» behind it (components/seis.html,
`help`; static/js/app.js, `bindSeisHelp`). What only a browser can say:

* the explanation opens on hover, on keyboard focus and on a tap, closes on
  Escape and on a tap elsewhere, and is the button's accessible description;
* it opens inside the window at 375 px, moves nothing in the strip, and reads
  at AA in both themes;
* the figure is still one link to the same register list, and nothing
  interactive is nested in it.

The static half — wording, markup, the other strips — is
tests/test_feedback_and_my_work_ux_oct12.py.
"""

from __future__ import annotations

import pytest
from playwright.sync_api import expect

from e2e.conftest import MARTIN, sign_in

pytestmark = pytest.mark.e2e

#: The «teemat menetluses» figure's own item. Most Minu asjad figures carry an
#: explanation since the UX package of 2026-10-28, so the item is picked by the
#: explanation it holds rather than by being the only one.
ITEM = ".seis__item[data-seishelp]:has(#seis-selgitus-menetluses)"
FIGURE = f"{ITEM} a.seis__figure"
INFO = f"{ITEM} .seis__info"
TIP = "#seis-selgitus-menetluses"


def _open(page, base_url: str, width: int = 1440) -> None:
    sign_in(page, base_url, MARTIN)
    page.set_viewport_size({"width": width, "height": 900})
    page.goto(f"{base_url}/minu-asjad/")
    page.wait_for_load_state("networkidle")


def _boxes(page) -> list[tuple[float, float, float, float]]:
    return [
        (box["x"], box["y"], box["width"], box["height"])
        for box in (figure.bounding_box() for figure in page.locator(".seis__figure").all())
        if box
    ]


def test_hover_opens_it_and_moves_nothing(page, base_url):
    _open(page, base_url)
    count = int(page.locator(FIGURE).locator(".seis__number").inner_text())
    caption = "teema menetluses" if count == 1 else "teemat menetluses"
    expect(page.locator(FIGURE).locator(".seis__caption")).to_have_text(caption)
    expect(page.locator(TIP)).to_be_hidden()
    before = _boxes(page)

    page.locator(INFO).hover()

    tip = page.locator(TIP)
    expect(tip).to_be_visible()
    expect(tip).to_contain_text("Menetluses olev teema")
    expect(tip).to_contain_text("millega tuleb veel tegeleda")
    assert _boxes(page) == before, "opening the explanation moved the strip"

    page.mouse.move(5, 5)
    expect(tip).to_be_hidden()


def test_keyboard_focus_opens_it_and_escape_closes_it(page, base_url):
    _open(page, base_url)
    page.locator(FIGURE).focus()
    page.keyboard.press("Tab")

    info = page.locator(INFO)
    expect(info).to_be_focused()
    expect(page.locator(TIP)).to_be_visible()
    # Its accessible name and its description, as a screen reader is given them.
    expect(info).to_have_accessible_name("Selgitus: Menetluses olev teema")
    description = page.locator(TIP).evaluate(
        "node => node.textContent.replace(/\\s+/g, ' ').trim()"
    )
    assert description.startswith("Menetluses olev teema")
    expect(info).to_have_accessible_description(description)

    page.keyboard.press("Escape")
    expect(page.locator(TIP)).to_be_hidden()
    expect(info).to_be_focused()

    # Leaving and coming back opens it again.
    page.keyboard.press("Shift+Tab")
    page.keyboard.press("Tab")
    expect(page.locator(TIP)).to_be_visible()


def test_the_figure_is_still_one_link_to_the_same_list(page, base_url):
    _open(page, base_url)
    nested = page.evaluate(
        "() => document.querySelectorAll("
        "'.seis a a, .seis a button, .seis button a, .seis button button').length"
    )
    assert nested == 0
    figure = page.locator(FIGURE)
    assert figure.evaluate("node => node.tagName") == "A"
    expected = figure.locator(".seis__number").inner_text().strip()
    href = figure.get_attribute("href")
    assert href and "olek=avatud" in href and "liik=FULL" in href and "vastutaja=" in href

    # Opening the explanation first must not take the click away from the link.
    page.locator(INFO).click()
    expect(page.locator(TIP)).to_be_visible()
    figure.click()
    page.wait_for_load_state("networkidle")

    assert "/teemad/" in page.url
    expect(page.locator(".registercount strong")).to_have_text(expected)


def test_a_tap_opens_it_inside_a_phone_screen(browser, base_url):
    context = browser.new_context(
        viewport={"width": 375, "height": 812}, has_touch=True, is_mobile=True
    )
    page = context.new_page()
    try:
        sign_in(page, base_url, MARTIN)
        page.goto(f"{base_url}/minu-asjad/")
        page.wait_for_load_state("networkidle")
        tip = page.locator(TIP)
        expect(tip).to_be_hidden()

        page.locator(INFO).tap()

        expect(tip).to_be_visible()
        expect(page.locator(INFO)).to_have_attribute("aria-expanded", "true")
        box = tip.bounding_box()
        assert box is not None
        assert box["x"] >= 0 and box["x"] + box["width"] <= 375, box
        overflow = page.evaluate(
            "() => document.documentElement.scrollWidth - document.documentElement.clientWidth"
        )
        assert overflow <= 1, f"the open explanation scrolls the page sideways by {overflow}px"

        page.locator("h1").first.tap()
        expect(tip).to_be_hidden()
        expect(page.locator(INFO)).to_have_attribute("aria-expanded", "false")
    finally:
        context.close()


@pytest.mark.parametrize("theme", ["dark", "light"])
def test_it_reads_in_both_themes(page, base_url, theme):
    from e2e.test_theme_contrast import AUDIT

    page.add_init_script(
        f"try {{ window.localStorage.setItem('juristid-theme', '{theme}') }} catch (e) {{}}"
    )
    _open(page, base_url)
    assert page.evaluate("() => document.documentElement.dataset.theme") == theme
    page.locator(INFO).hover()
    expect(page.locator(TIP)).to_be_visible()

    failing = [found for found in page.evaluate(AUDIT) if "seis__" in found["element"]]
    assert not failing, f"{theme}: under AA: {failing}"
