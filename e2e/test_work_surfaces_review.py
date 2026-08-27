"""Renderings of the rebuilt Minu töö and Ülevaade, for human review.

Not a regression suite. These take one screenshot per scenario into the CI
artifact directory and assert only the handful of things a picture cannot show
on its own — that a passed review date is amber rather than red, that the two
Ülevaade scopes are ordinary links, that no row overflows at 1280.

It exists because the development machine has no database and cannot open a
browser, so this job is the only place these pages are ever seen before somebody
approves them. Deliberately separate from `test_ui_regression.py`: that suite
compares against committed baselines, and these pages are being redesigned —
their baselines are expected to differ and will be regenerated once the design
is approved.
"""

from __future__ import annotations

import os
import pathlib

import pytest

from e2e.conftest import HEAD, SANDRA, sign_in

pytestmark = pytest.mark.e2e

SHOT_DIR = pathlib.Path(os.environ.get("E2E_SCREENSHOT_DIR", "artifacts/screenshots"))

#: The two widths the design is specified at. 1440 is the primary; 1280 is the
#: one that has to keep working, and the one where a rail narrows rather than a
#: column becoming unreadable.
WIDTHS = (1440, 1280)

STYLE_FIXTURE = """
  *, *::before, *::after {
    transition: none !important;
    animation: none !important;
    caret-color: transparent !important;
  }
  .topbar { position: static !important; }
"""


def _shoot(page, name: str) -> None:
    SHOT_DIR.mkdir(parents=True, exist_ok=True)
    page.add_style_tag(content=STYLE_FIXTURE)
    page.wait_for_load_state("networkidle")
    page.screenshot(path=str(SHOT_DIR / f"{name}.png"), full_page=True)


def _open(page, base_url: str, persona, path: str, width: int):
    sign_in(page, base_url, persona)
    page.set_viewport_size({"width": width, "height": 1000})
    page.goto(f"{base_url}{path}")
    page.wait_for_load_state("networkidle")


def _no_horizontal_overflow(page) -> None:
    """The page may scroll down. It must never scroll sideways.

    Measured rather than eyeballed: a row that runs half a pixel past the
    viewport is invisible in a screenshot and obvious to somebody using the
    product on a 1280 laptop.
    """
    overflow = page.evaluate(
        "() => document.documentElement.scrollWidth - document.documentElement.clientWidth"
    )
    assert overflow <= 1, f"page scrolls horizontally by {overflow}px"


# --- Minu töö -------------------------------------------------------------


@pytest.mark.parametrize("width", WIDTHS)
def test_minu_too(page, base_url, width):
    _open(page, base_url, SANDRA, "/minu-too/", width)
    _shoot(page, f"minu-too-{width}")
    _no_horizontal_overflow(page)


def test_minu_too_states_what_every_date_means(page, base_url):
    """The rule a screenshot cannot check.

    Waiting is not lateness. A WAIT or MONITOR whose review date has passed is
    amber and worded *ülevaatamiseks küps*; only a DO deadline and an
    Oluline tähtaeg are ever red (master specification 18.8).
    """
    _open(page, base_url, SANDRA, "/minu-too/", 1440)

    # Every rendered row carries its meaning in words beside the date.
    meanings = page.locator(".workrow2__meaning")
    rows = page.locator(".workrow2")
    assert meanings.count() == rows.count()

    for index in range(meanings.count()):
        text = meanings.nth(index).inner_text().strip()
        assert text, "a work row rendered a date with no stated meaning"


def test_a_ripe_review_is_never_styled_as_late(page, base_url):
    _open(page, base_url, SANDRA, "/minu-too/", 1440)

    ripe = page.locator(".workrow2--review")
    for index in range(ripe.count()):
        row = ripe.nth(index)
        assert "workrow2--overdue" not in (row.get_attribute("class") or "")
        assert "TÄHTAEG" not in row.locator(".workrow2__meaning").inner_text()


def test_the_range_control_is_in_the_url(page, base_url):
    """A window a lawyer chose has to survive a refresh and paste into a message."""
    _open(page, base_url, SANDRA, "/minu-too/?kuni=koik", 1440)

    assert "kuni=koik" in page.url
    _shoot(page, "minu-too-koik-tahtajad")


# --- Ülevaade -------------------------------------------------------------


@pytest.mark.parametrize("width", WIDTHS)
@pytest.mark.parametrize("scope", ["osakond", "valdkonniti"])
def test_ulevaade(page, base_url, scope, width):
    _open(page, base_url, HEAD, f"/ulevaade/?vaade={scope}", width)
    _shoot(page, f"ulevaade-{scope}-{width}")
    _no_horizontal_overflow(page)


def test_the_scopes_are_links_not_a_client_side_tab_strip(page, base_url):
    _open(page, base_url, HEAD, "/ulevaade/", 1440)

    tabs = page.get_by_role("navigation", name="Ülevaate ulatus").get_by_role("link")
    assert tabs.count() == 2

    tabs.nth(1).click()
    page.wait_for_load_state("networkidle")

    assert "vaade=valdkonniti" in page.url


def test_every_seis_figure_is_a_link(page, base_url):
    """No dead-end numbers. Every figure is a promise that a list exists."""
    _open(page, base_url, HEAD, "/ulevaade/", 1440)

    figures = page.locator(".seis__figure")
    assert figures.count() >= 4
    for index in range(figures.count()):
        href = figures.nth(index).get_attribute("href")
        assert href and href != "#", "a Seis figure leads nowhere"


def test_the_area_accordion_is_a_real_disclosure(page, base_url):
    """A caret somebody can reach with a keyboard, not a decorative glyph."""
    _open(page, base_url, HEAD, "/ulevaade/?vaade=valdkonniti", 1440)

    rows = page.locator(".arearow")
    if not rows.count():
        pytest.skip("the seeded world has no area with open work")

    first = rows.first
    assert first.get_attribute("open") is not None, "the first area does not open on arrival"
    _shoot(page, "ulevaade-valdkonniti-avatud")


def test_a_specialist_sees_no_restricted_title_on_the_department_view(page, base_url):
    """Martin is not a participant in Sandra's restricted Matter.

    The page aggregates colleagues; that must not widen what anybody may read.
    """
    from app.core.management.commands.seed_e2e_data import RESTRICTED_TITLE
    from e2e.conftest import MARTIN

    _open(page, base_url, MARTIN, "/ulevaade/?vaade=osakond", 1440)

    assert RESTRICTED_TITLE not in page.content()
