"""Visual regression for the screens this branch restored.

What this suite is for
----------------------
The structural suite beside it asserts the rules the design states. This one
asserts that nothing else moved: a stray `display: flex`, a padding token
changed one step, a rule that stops applying because a brace closed early — none
of which any assertion about the DOM would notice, and all of which are how this
UI drifted in the first place.

Why it is narrow
----------------
Ten scenarios, chosen because each is a component family rather than a page:
the shell, the register, a Matter header, a Matter in a special state, the
position surface, the evidence surface, the create form, the search results, a
generated reading surface, and a refused save. A screenshot of every route would
lock in a hundred baselines that nobody re-reads, and a suite nobody re-reads
approves a bad design as efficiently as a good one.

Determinism
-----------
The seeded world computes its dates from today, so the *numbers* on these pages
change daily. Those are masked — the numeric date text only, never the label
beside it or the surface it sits on — and the semantics they carry (overdue is
danger-coloured, a passed review is not) are asserted in `test_ui_shell.py`
instead, where they can be checked rather than looked at. The build stamp in the
footer is masked for the same reason.

Baselines
---------
Committed under `e2e/baselines/`, produced by this same job on this same
container image: a screenshot taken on a developer machine would differ in font
rasterisation on every pixel of every glyph. To (re)generate, run the browser
job with `E2E_UPDATE_BASELINES=1` and commit what lands in the artifact.

A missing baseline skips rather than fails, so a new scenario does not turn the
build red before anybody has had a chance to look at what it captured. A missing
baseline is reported by name.

One trap worth knowing before adding a browser test
---------------------------------------------------
This step runs *after* the functional browser suite, against the same database,
so every Matter that suite creates is in these renderings. A new test that files
a Matter therefore lengthens the register, the dashboard's attention table and
Minu töö — and nine baselines go red for a reason that has nothing to do with
CSS. That is worth the coupling (the alternative is a second seeded world nobody
keeps in step), but it means "the visual suite failed" should be read against
what changed in the functional suite before anybody goes looking at stylesheets.
"""

from __future__ import annotations

import os
import pathlib

import pytest

from app.core.management.commands.seed_e2e_data import ARCHIVE_TITLE, OPEN_TITLE
from e2e.conftest import SANDRA, sign_in

pytestmark = pytest.mark.e2e

BASELINE_DIR = pathlib.Path(__file__).parent / "baselines"
CANDIDATE_DIR = pathlib.Path(os.environ.get("E2E_SCREENSHOT_DIR", "artifacts/screenshots"))
UPDATING = os.environ.get("E2E_UPDATE_BASELINES") == "1"

#: Chromium's own anti-aliasing is not bit-stable between runs on the same
#: image, so an exactly-equal comparison would flake. The tolerance is per
#: channel and deliberately tight: it absorbs a rasterisation wobble on a glyph
#: edge and nothing else. A moved element, a changed colour or a lost rule all
#: differ by far more than this on far more than 0.2% of the page.
CHANNEL_TOLERANCE = 24
MAX_DIFFERING_FRACTION = 0.002

#: Everything whose text is derived from the clock. Masked as narrowly as
#: possible: the date's digits, not the cell, the label or the row — so the
#: register still proves it renders a date meaning beside every date, and the
#: Matter header still proves its facts strip is one line of values.
#:
#: Every one of these renders a fixed-width `dd.mm.yyyy`, so masking the pixels
#: is enough: the boxes do not change size from one day to the next and the
#: layout around them does not move.
#:
#: Nothing here names a whole cell or column. A mask paints over the element it
#: matches, so naming a `<td>` class takes the `<th>` with it and the baseline
#: stops showing that the column exists at all — which is why the dashboard's
#: date cells are `<time>` elements instead. Counts are not masked either: the
#: seeded world computes its dates from the same `today` the page renders on, so
#: the figures are stable even where the strings beside them are not.
CLOCK_DEPENDENT = [
    ".app__footer",
    ".dateline",
    ".workrow__date",
    ".factrow__date",
    ".table__lastactivity .muted",
    # The Teema header's one deadline, the Järgmiseks row's date, the sent
    # strip's dates and the accordions' "N kirjet · viimane <date>" summaries.
    # The redesign moved every one of these, and a mask selector that stops
    # matching does not fail — it silently unmasks a value that changes daily,
    # and every baseline goes red the next morning.
    ".metaline__item--deadline .inlineedit__trigger",
    ".nextrow__date",
    ".sentstrip__date",
    ".accordion__summary",
    ".railcard__value--date",
    # A date control's value renders in the control, and the create form's
    # Saabus defaults to today. Scoped to that form: unscoped, the selector also
    # matched the register's filter inputs, which sit inside a *closed*
    # disclosure — a closed <details> child still has a box, so Playwright
    # painted mask rectangles across the rows underneath and three register
    # baselines came back with obscured rows. Masks can damage a page, so a mask
    # selector is as much a thing to review as the page itself.
    #
    # `.dateinput` rather than `input[type=date]`: the native control is gone
    # from the ordinary UI, because it renders in the browser's locale and put
    # `mm/dd/yyyy` on an Estonian form (app/core/widgets.py). A mask selector
    # that stopped matching would not fail — it would silently unmask a value
    # that changes daily, and every baseline would go red the next morning.
    ".createform .dateinput",
    "time",
]

STYLE_FIXTURE = """
  *, *::before, *::after {
    transition: none !important;
    animation: none !important;
    caret-color: transparent !important;
  }
  /* The bar and the table head are sticky, which paints them across the middle
     of a full-page capture and hides what is behind them. */
  .topbar, .table thead th { position: static !important; }
"""


def capture(page, name: str, *, full_page: bool = True, clip_to: str | None = None) -> bytes:
    page.add_style_tag(content=STYLE_FIXTURE)
    page.wait_for_load_state("networkidle")
    masks = [page.locator(selector) for selector in CLOCK_DEPENDENT]
    target = page.locator(clip_to) if clip_to else page
    image = target.screenshot(
        # Prefixed, because the rest of the browser suite writes its own
        # screenshots into the same artifact directory and two of the names
        # collide.
        path=str(CANDIDATE_DIR / f"visual-{name}.png"),
        mask=masks,
        mask_color="#101418",
        **({"full_page": full_page} if clip_to is None else {}),
    )
    return image


def compare(name: str, candidate: bytes) -> None:
    """Fail when the rendered page differs from its committed baseline."""
    from io import BytesIO

    from PIL import Image, ImageChops

    baseline_path = BASELINE_DIR / f"{name}.png"
    if UPDATING:
        BASELINE_DIR.mkdir(parents=True, exist_ok=True)
        baseline_path.write_bytes(candidate)
        pytest.skip(f"baseline written: {baseline_path.name}")
    if not baseline_path.is_file():
        pytest.skip(
            f"no committed baseline for {name!r} — take it from the browser-artifacts "
            f"upload, or rerun the job with E2E_UPDATE_BASELINES=1"
        )

    expected = Image.open(baseline_path).convert("RGB")
    actual = Image.open(BytesIO(candidate)).convert("RGB")
    assert actual.size == expected.size, (
        f"{name}: the page is now {actual.size}, baseline is {expected.size}"
    )

    difference = ImageChops.difference(actual, expected)
    beyond_tolerance = difference.convert("L").point(
        lambda value: 255 if value > CHANNEL_TOLERANCE else 0
    )
    differing = sum(1 for pixel in beyond_tolerance.getdata() if pixel)
    fraction = differing / (expected.width * expected.height)

    if fraction > MAX_DIFFERING_FRACTION:
        CANDIDATE_DIR.mkdir(parents=True, exist_ok=True)
        difference.save(CANDIDATE_DIR / f"{name}.diff.png")
        pytest.fail(
            f"{name}: {fraction:.4%} of pixels differ from the baseline "
            f"(limit {MAX_DIFFERING_FRACTION:.2%}). "
            f"The rendering and the difference are in the browser artifacts. "
            f"If the change is intended, regenerate the baseline."
        )


def signed_in(page, base_url: str, path: str, width: int = 1440, height: int = 900):
    sign_in(page, base_url, SANDRA)
    page.set_viewport_size({"width": width, "height": height})
    page.goto(f"{base_url}{path}")
    page.wait_for_load_state("networkidle")
    return page


def open_matter(page, base_url: str, title: str, tab: str = ""):
    """Open a named Matter from the register.

    Named rather than "the first row": the register's default ordering put the
    archive record first, so the overview scenario and the special-state
    scenario captured byte-identical pages and one of them proved nothing.

    The link is followed rather than clicked because the table head is sticky
    and can sit over the first row — right for reading, unhelpful for a capture
    whose subject is the page after it.
    """
    signed_in(page, base_url, f"/teemad/?olek=koik&q={title.split()[0]}")
    link = page.get_by_role("link", name=title, exact=False).first
    assert link.count(), f"the register does not hold {title!r}"
    page.goto(f"{base_url}{link.get_attribute('href')}")
    page.wait_for_load_state("networkidle")
    if tab:
        page.get_by_role("link", name=tab).click()
        page.wait_for_load_state("networkidle")
    return page


# ---------------------------------------------------------------------------
# 1. The shell, at every supported width
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("width", [1440, 1366, 1280, 1024])
def test_shell(page, base_url, width):
    """The bar itself, clipped: it is the component every screen inherits."""
    signed_in(page, base_url, "/teemad/", width=width)
    compare(f"shell-{width}", capture(page, f"shell-{width}", clip_to=".topbar"))


# ---------------------------------------------------------------------------
# 2–10. The screens
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("width", [1440, 1280])
def test_register(page, base_url, width):
    signed_in(page, base_url, "/teemad/", width=width)
    compare(f"teemad-{width}", capture(page, f"teemad-{width}"))


def test_register_with_the_narrowing_panel_open(page, base_url):
    """The panel that used to resolve to a single 240px column."""
    signed_in(page, base_url, "/teemad/")
    page.locator(".filterpanel__trigger").click()
    page.wait_for_timeout(120)
    compare("teemad-filter", capture(page, "teemad-filter"))


def test_matter_overview(page, base_url):
    open_matter(page, base_url, OPEN_TITLE)
    compare("teema-ulevaade", capture(page, "teema-ulevaade"))


def test_matter_header_only(page, base_url):
    """The band on its own: identity, state, facts and tabs, and how tall."""
    open_matter(page, base_url, OPEN_TITLE)
    compare("teema-pais", capture(page, "teema-pais", clip_to=".matterhead"))


def test_matter_in_a_special_state(page, base_url):
    """Archive: an imported record whose known uncertainty is kept."""
    open_matter(page, base_url, ARCHIVE_TITLE)
    compare("teema-arhiiv", capture(page, "teema-arhiiv"))


def test_matter_opinions(page, base_url):
    """`Arvamused` on one Matter — reached from the position block, not a tab."""
    open_matter(page, base_url, OPEN_TITLE)
    page.get_by_role("link", name="Arvamused →").click()
    page.wait_for_load_state("networkidle")
    compare("teema-seisukoht", capture(page, "teema-seisukoht"))


def test_matter_composer_expanded(page, base_url):
    """Every progressive disclosure open at once.

    The one state a screenshot is genuinely better at than an assertion: five
    optional blocks, each of which is a form, and the question is whether the
    composer still reads as one surface when a lawyer has opened all of them.
    """
    open_matter(page, base_url, OPEN_TITLE)
    for chip in ("+ Manus", "+ Oluline tähtaeg", "+ Kaasamine", "+ Lõpeta teema"):
        page.locator(".disclosure-chip", has_text=chip).click()
    page.wait_for_timeout(120)
    compare("teema-koostaja", capture(page, "teema-koostaja", clip_to=".composer"))


def test_matter_closed(page, base_url):
    """A closed Matter: readable past, no writable next step, no composer."""
    signed_in(page, base_url, "/teemad/?olek=suletud")
    link = page.locator(".table__titlelink").first
    if not link.count():
        pytest.skip("the seeded world holds no closed Matter")
    page.goto(f"{base_url}{link.get_attribute('href')}")
    page.wait_for_load_state("networkidle")
    compare("teema-suletud", capture(page, "teema-suletud"))


def test_matter_at_1024(page, base_url):
    """The rail folds under the content and the reading order does not change."""
    open_matter(page, base_url, OPEN_TITLE)
    page.set_viewport_size({"width": 1024, "height": 900})
    page.wait_for_load_state("networkidle")
    compare("teema-1024", capture(page, "teema-1024"))


def test_matter_documents(page, base_url):
    open_matter(page, base_url, OPEN_TITLE, tab="Dokumendid")
    compare("teema-dokumendid", capture(page, "teema-dokumendid"))


def test_create_matter_form(page, base_url):
    signed_in(page, base_url, "/teemad/uus/")
    compare("uus-teema", capture(page, "uus-teema"))


def test_create_matter_refused(page, base_url):
    """A refused save: the error beside the field, the layout intact.

    Past the browser's own required-field check, because the server's refusal
    is the state this scenario exists to lock. Without `noValidate` the click
    never left the page and the "refused" baseline was a pristine form wearing
    the wrong name — found in the integration content review, not by the suite,
    which is exactly why baselines get read before they get committed.
    """
    signed_in(page, base_url, "/teemad/uus/")
    page.locator("form.createform").evaluate("form => form.noValidate = true")
    page.get_by_role("button", name="Loo teema").click()
    page.wait_for_load_state("networkidle")
    compare("uus-teema-viga", capture(page, "uus-teema-viga"))


def test_search_results(page, base_url):
    signed_in(page, base_url, "/otsing/?q=eeln%C3%B5u")
    compare("otsing", capture(page, "otsing"))


def test_watchlist(page, base_url):
    """Jälgimine: a newer surface, built from the same components."""
    signed_in(page, base_url, "/olulised-tahtajad/")
    compare("jalgimine", capture(page, "jalgimine"))


def test_dashboard(page, base_url):
    """Ülevaade after the final-register integration.

    The composition this locks is the one the cutover asked for: headline
    cards that count rows, dense fixed-column tables, and a responsibility
    rail whose rows are deliberately not links (ADR 0021). Statcard values
    are masked — several count seeded rows whose dates are computed relative
    to today, so the digits may move while the composition may not.
    """
    signed_in(page, base_url, "/ulevaade/")
    compare("ulevaade", capture(page, "ulevaade"))


def test_my_work(page, base_url):
    """Minu töö: the DO bands, the wait column, and the Excelist context line
    on a Matter that has no structured next step."""
    signed_in(page, base_url, "/minu-too/")
    compare("minu-too", capture(page, "minu-too"))
