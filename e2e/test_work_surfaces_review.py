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
# Two decisions the department has settled and which must not drift back:
# *Üle tähtaja* shows its ten oldest rows and holds the rest inline, and
# *Hiljem* sits on the ordinary band surface rather than a panel of its own.
#
# They are asserted here rather than left to `minu-too.png` because a baseline
# approves whatever it was last regenerated from. The Hiljem background is the
# proof of that: removing it did **not** fail the visual suite — the panel
# colour is close enough to the page that the difference stayed inside the
# tolerance — so for as long as the rule existed no committed image would have
# caught it coming back.
#
# What each layer locks:
#
# * the *number* ten, and the ordering under it, are locked in
#   `tests/test_my_work_timeline.py`, which builds thirteen late rows across all
#   three work sources and renders the real page;
# * the *mechanism* — that the preview is capped in a real browser and the rest
#   are genuinely not visible until the disclosure is opened — is locked here;
# * the *surface* is locked here as a computed style.
#
# The seeded world has no timeline deep enough to overflow *Üle tähtaja*, and
# giving it one is not free: a trial run put twelve overdue milestones on
# Martin and broke five `test_matter_intelligence` scenarios, one `test_ui_shell`
# scenario and both Osakond baselines, because the same records feed Jälgimine's
# calendar and Osakond's *Vajab sekkumist*. That is a change to the fixture
# world, not to this page, and it does not belong in a correction to this page.


def test_each_band_caps_its_preview_and_holds_the_rest_inline(page, base_url):
    """Every band on Minu asjad shows at most `BAND_VISIBLE[key]` rows.

    Visibility is Playwright's, not the markup's. The rows behind a closed
    `<details>` are in the HTML — that is what makes the disclosure a slice of
    one list rather than a second query — so a test that counted `.workrow2`
    elements would count them and prove nothing.

    Where a band overflows, the count in its heading stays the whole population
    and «Näita veel N» offers exactly the remainder; opening it brings every row
    on screen. Martin, because his is the timeline that renders three of the
    four bands.
    """
    from app.matters.work_items import BAND_VISIBLE

    _open(page, base_url, MARTIN, "/minu-asjad/", 1440)

    bands = page.locator(".worklayout2__main section.workband")
    seen = 0
    for index in range(bands.count()):
        band = bands.nth(index)
        classes = band.get_attribute("class") or ""
        key = next((k for k in BAND_VISIBLE if f"workband--{k}" in classes), None)
        if key is None:
            continue
        seen += 1
        cap = BAND_VISIBLE[key]
        total = int(band.locator(".workband__count").inner_text().strip())
        on_screen = band.locator(".workrow2:visible")
        expected = total if cap is None else min(total, cap)
        assert on_screen.count() == expected, (
            f"{key}: {on_screen.count()} rows are visible, not {expected} "
            f"(band holds {total}, cap is {cap})"
        )

        disclosure = band.locator("details.pw-more")
        if cap is None or total <= cap:
            assert disclosure.count() == 0, f"{key} hides rows it has room for"
            continue

        # `> summary` rather than `summary`: every row inside the disclosure
        # carries its own ⋯ menu, which is a `<details>` with a summary too.
        assert f"Näita veel {total - cap}" in disclosure.locator("> summary").inner_text()
        disclosure.locator("> summary").click()
        page.wait_for_timeout(120)
        assert band.locator(".workrow2:visible").count() == total
        assert int(band.locator(".workband__count").inner_text().strip()) == total

    assert seen, "Minu asjad rendered no timeline band at all"


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
