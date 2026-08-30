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

from e2e.conftest import HEAD, MARTIN, SANDRA, sign_in

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
    _open(page, base_url, SANDRA, "/minu-asjad/", width)
    _shoot(page, f"minu-too-{width}")
    _no_horizontal_overflow(page)


def test_minu_too_states_what_every_date_means(page, base_url):
    """The rule a screenshot cannot check.

    Waiting is not lateness. A WAIT or MONITOR whose review date has passed is
    amber and worded *ülevaatamiseks küps*; only a DO deadline and an
    Oluline tähtaeg are ever red (master specification 18.8).
    """
    _open(page, base_url, SANDRA, "/minu-asjad/", 1440)

    # Every rendered row carries its meaning in words beside the date.
    meanings = page.locator(".workrow2__meaning")
    rows = page.locator(".workrow2")
    assert meanings.count() == rows.count()

    for index in range(meanings.count()):
        text = meanings.nth(index).inner_text().strip()
        assert text, "a work row rendered a date with no stated meaning"


def test_a_ripe_review_is_never_styled_as_late(page, base_url):
    _open(page, base_url, SANDRA, "/minu-asjad/", 1440)

    ripe = page.locator(".workrow2--review")
    for index in range(ripe.count()):
        row = ripe.nth(index)
        assert "workrow2--overdue" not in (row.get_attribute("class") or "")
        assert "TÄHTAEG" not in row.locator(".workrow2__meaning").inner_text()


def test_the_range_control_is_in_the_url(page, base_url):
    """A window a lawyer chose has to survive a refresh and paste into a message."""
    _open(page, base_url, SANDRA, "/minu-asjad/?kuni=koik", 1440)

    assert "kuni=koik" in page.url
    _shoot(page, "minu-too-koik-tahtajad")


# --- Osakond --------------------------------------------------------------


@pytest.mark.parametrize("width", WIDTHS)
@pytest.mark.parametrize("scope", ["osakond", "valdkonniti"])
def test_osakond(page, base_url, scope, width):
    _open(page, base_url, HEAD, f"/osakond/?vaade={scope}", width)
    _shoot(page, f"osakond-{scope}-{width}")
    _no_horizontal_overflow(page)


def test_the_scopes_are_links_not_a_client_side_tab_strip(page, base_url):
    _open(page, base_url, HEAD, "/osakond/", 1440)

    tabs = page.get_by_role("navigation", name="Ülevaate ulatus").get_by_role("link")
    assert tabs.count() == 2

    tabs.nth(1).click()
    page.wait_for_load_state("networkidle")

    assert "vaade=valdkonniti" in page.url


def test_no_seis_figure_leads_nowhere(page, base_url):
    """No dead-end numbers, and no misleading ones either.

    Every figure that carries a link is a promise that a list exists behind it,
    so a `#` or an empty `href` is a dead end. Exactly one figure carries no
    link at all: «arvamust välja · 7 p» counts a seven-day window the Arvamused
    workspace cannot narrow to, so it states the number and offers nothing
    rather than opening a longer list — an honest number beats a link to a
    different one (docs/adr/0049 §4, DS-24).
    """
    _open(page, base_url, HEAD, "/osakond/", 1440)

    figures = page.locator(".seis__figure")
    assert figures.count() == 6, figures.count()

    unlinked = []
    for index in range(figures.count()):
        figure = figures.nth(index)
        caption = figure.locator(".seis__caption").inner_text().strip()
        href = figure.get_attribute("href")
        if href is None:
            unlinked.append(caption)
            continue
        assert href and href != "#", f"{caption} leads nowhere"

    assert unlinked == ["arvamust välja · 7 p"], unlinked


def test_the_area_accordion_is_a_real_disclosure(page, base_url):
    """A caret somebody can reach with a keyboard, not a decorative glyph."""
    _open(page, base_url, HEAD, "/osakond/?vaade=valdkonniti", 1440)

    rows = page.locator(".arearow")
    if not rows.count():
        pytest.skip("the seeded world has no area with open work")

    first = rows.first
    assert first.get_attribute("open") is not None, "the first area does not open on arrival"
    _shoot(page, "osakond-valdkonniti-avatud")


def test_a_reader_sees_no_restricted_title_on_the_department_view(page, base_url):
    """A reader is outside the legal team (docs/adr/0042).

    The page aggregates colleagues; that must not widen what anybody may read.
    A lawyer reads the department either way, so only a reader can show it.
    """
    from app.core.management.commands.seed_e2e_data import RESTRICTED_TITLE
    from e2e.conftest import READER

    _open(page, base_url, READER, "/osakond/?vaade=osakond", 1440)

    assert RESTRICTED_TITLE not in page.content()


# --- the locked Minu asjad contract ---------------------------------------
#
# Two decisions the department has settled and which must not drift back. They
# are asserted here rather than left to `minu-too.png` because a baseline
# approves whatever it was last regenerated from: a screenshot cannot say
# whether ten rows is the rule or an accident of the fixture, and a background
# creeping back onto one band is a few hundred pixels a reviewer would pass.


def test_the_overdue_band_shows_ten_rows_and_holds_the_rest_inline(page, base_url):
    """*Üle tähtaja*: the ten oldest on screen, the remainder behind «Näita veel N».

    Martin, not Sandra: his is the one timeline in the seeded world deep enough
    to overflow the band (`seed_e2e_data._overdue_depth`), and keeping the depth
    off the baselined persona is what stops this fixture rewriting
    `minu-too.png`.

    Visibility is Playwright's, not the markup's. The rows behind a closed
    `<details>` are in the HTML — that is the whole point of the disclosure, and
    what makes it a slice of one list rather than a second query — so a test
    that counted `.workrow2` elements would report thirteen and prove nothing.
    """
    from app.core.management.commands.seed_e2e_data import OVERDUE_MILESTONE_PREFIX

    _open(page, base_url, MARTIN, "/minu-asjad/", 1440)

    band = page.locator("section.workband--ule_tahtaja")
    assert band.count() == 1, "Martin's timeline has no Üle tähtaja band"

    # The heading counts the whole population, not the visible slice.
    assert band.locator(".workband__count").inner_text().strip() == "13"

    on_screen = band.locator(".workrow2:visible")
    assert on_screen.count() == 10, f"{on_screen.count()} overdue rows are visible, not ten"

    # Oldest first: the milestones are numbered from the furthest past, so the
    # visible ten are 01…10 in that order and the eleventh is behind the fold.
    titles = [
        on_screen.nth(index).locator(".workrow2__action").inner_text().strip()
        for index in range(10)
    ]
    assert titles == [f"{OVERDUE_MILESTONE_PREFIX} {n:02d}" for n in range(1, 11)]

    disclosure = band.locator("details.pw-more")
    assert disclosure.count() == 1
    summary = disclosure.locator("summary").inner_text()
    assert "Näita veel 3" in summary, summary

    disclosure.locator("summary").click()
    page.wait_for_timeout(120)

    opened = band.locator(".workrow2:visible")
    assert opened.count() == 13, f"{opened.count()} rows after opening the disclosure, not thirteen"
    revealed = [
        opened.nth(index).locator(".workrow2__action").inner_text().strip()
        for index in range(10, 13)
    ]
    assert revealed[:2] == [f"{OVERDUE_MILESTONE_PREFIX} {n:02d}" for n in (11, 12)]
    # And the count above the list never moved.
    assert band.locator(".workband__count").inner_text().strip() == "13"


def test_hiljem_sits_on_the_same_surface_as_the_other_bands(page, base_url):
    """*Hiljem* is a band of the timeline, not a panel dropped into it.

    It once carried `background: var(--surface-panel)` and read as a separate
    card at the foot of the page. All four bands share one surface; what marks
    *Hiljem* out is its title colour and the range control in its head.

    Equality between two approved surfaces rather than a hard-coded colour, so
    the assertion survives a palette change and fails only on the thing it is
    here to catch: a background rule reappearing on this one band.
    """
    _open(page, base_url, MARTIN, "/minu-asjad/", 1440)

    later = page.locator("section.workband--hiljem")
    ordinary = page.locator("section.workband--jargmised_30_paeva")
    assert later.count() == 1, "the seeded world no longer renders Hiljem for Martin"
    assert ordinary.count() == 1, "the seeded world no longer renders Järgmised 30 päeva"

    def surface(locator):
        return locator.evaluate(
            "el => { const s = getComputedStyle(el);"
            " return [s.backgroundColor, s.backgroundImage]; }"
        )

    assert surface(later) == surface(ordinary), (
        "Hiljem no longer shares the ordinary band surface — a background rule "
        "has come back onto .workband--hiljem"
    )
