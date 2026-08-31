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
from e2e.conftest import (
    DESKTOP_VIEWPORT,
    SANDRA,
    open_composer,
    pass_the_gate,
    sign_in,
)

#: The seeded department head, mirroring `seed_e2e_data.PERSONAS`. Named here
#: rather than looked up, because this suite has no database access.
HEAD_NAME = "Testosakonnajuht"

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
#: Masking the pixels is enough for these: the layout around them does not move
#: from one day to the next. Not every one is fixed-width — `j.n` and `j.n.Y`
#: drop the leading zero, so the masked *box* is a character or two narrower on
#: a single-digit day, and the sliver of page behind it is no longer painted.
#: Measured by rewriting the rendered string to another date and recapturing:
#: 1px on Minu töö and 6px on the closed banner, against the ~5,800px a
#: full-page capture may differ by. Unmasked, the same two surfaces moved 341px
#: and 240px. Closing the last few would mean zero-padding a date the product
#: deliberately does not zero-pad, so the box edge is left as it is and the
#: glyphs — the part that actually drifts — are covered.
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
    # The rebuilt work surfaces. Every one of these renders a value derived
    # from today — "10 p üle", "TÄHTAEG 14.08", a feed timestamp — and a mask
    # selector that stops matching does not fail. It silently unmasks a value
    # that changes daily, and the baseline goes red the next morning.
    # The whole reason cell, not its two children. The meaning wraps, so a mask
    # sized to one line leaves the second peeking out — and a value that changes
    # daily peeking past its mask turns every baseline red the next morning.
    ".workrow2__datecell",
    ".interrow__reason",
    ".feedrow__when",
    ".entryline__when",
    ".arealine__date",
    ".quietrow__meta",
    ".disclosure__meta",
    ".factrow__date",
    ".table__lastactivity .muted",
    # The Teema header's one deadline, the Järgmiseks row's date, the sent
    # strip's dates and the accordions' "N kirjet · viimane <date>" summaries.
    # The redesign moved every one of these, and a mask selector that stops
    # matching does not fail — it silently unmasks a value that changes daily,
    # and every baseline goes red the next morning.
    ".metaline__item--deadline .inlineedit__trigger",
    # The Järgmiseks row's date. It used to be a flag naming what the date meant
    # — "TÄHTAEG MÖÖDAS · 6 p" — and is now the date itself, with the day count
    # riding along when the step is late (ADR 0052 §6). Either way it counts
    # from today and changes every morning. The row and the step's own words
    # stay in the baseline.
    ".uxnext__date",
    # `Excelist` and the register snapshot label beside an imported instruction.
    ".uxnext__flag",
    ".railposition__opinion time",
    # The closed timeline's own line. Its quote is content and stays in the
    # baseline — the date in front of it is a `<time>` and is painted by the
    # `time` selector below. What is masked here is the two values that move on
    # their own: the pill carrying the current step's date, and the entry count,
    # which every functional test that writes a note increments.
    #
    # Kaasamine's and Töödokumendid's summaries are content and must stay.
    ".accordion--timeline > summary .uxtl__previewnext",
    ".accordion--timeline > summary .uxtl__count",
    # Osakond's deadline panel. Every row prints "R 28.08" or "täna", and every
    # group header prints the window it holds — all of it computed from today
    # (design handoff 1a). The owner badges and four of the five group names stay
    # in the baseline; the fifth is tomorrow's weekday and carries `data-clock`.
    ".uxdl__date",
    ".uxdl__range",
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
    # ---- Three values the list above missed, each found by rendering the page
    # and asking which selector covered it rather than by reading class names.
    #
    # None of them was ever big enough on its own to cross
    # MAX_DIFFERING_FRACTION, which is the whole reason they survived: a mask
    # that stops matching fails loudly the next morning, but a value that was
    # *never* masked just makes the baseline quietly stale. The cost lands on
    # somebody else — the next unrelated change adds enough differing pixels to
    # push the total over the limit, and their diff shows regions they did not
    # touch. That is how the header-branding round found five stale baselines.
    #
    # Every one is scoped, because the bare class also renders content
    # elsewhere: `.foldout__meta` says "kogu osakond · viimane kuu" on Minu
    # asjad, `.muted` carries register text, and `.interrow__detail` is the
    # next step in words on every row that has one.
    #
    # Minu töö's "viimane 26.8 kell 13:20". The rows *inside* the disclosure
    # are `.entryline__when` and were masked; the summary line above them, which
    # is the only part visible while the disclosure is shut, was not.
    ".workband--entries .foldout__meta",
    # The closed banner's "(26.8.2026)". `closed_at` is set when the Matter is
    # closed, and the browser suite closes one on every run, so this is today's
    # date on every run. The same date in the facts rail is a `<time>` and was
    # masked by that; the banner renders a bare `.muted` span and was not.
    ".banner--closed .banner__text .muted",
    # Osakond's ownerless rows: "arvamuse tähtaeg 31.8.2026", built as one
    # string in `app/matters/overview.py` from `response_deadline`, which the
    # seeded world computes from today. The reason cell beside it was masked;
    # the detail line under the title was not. Scoped by the row offering
    # "Määra →", which is exactly the set of rows whose detail is this string —
    # every other row's detail is its next step, and masking that would take
    # real content out of the baseline.
    #
    # Unlike the two above, this one is deliberately *not* in REQUIRED_MASKS,
    # and the reason is worth keeping: on a freshly seeded world the ownerless
    # row is in the intervention preview, and on the world this suite actually
    # runs against — after the functional suite has filed its Matters — it is
    # pushed off the end of a capped list and never captured at all. Requiring
    # it made the department baseline fail the moment CI ran it, for a reason
    # that is not a
    # defect. Its presence is a function of how many higher-priority rows the
    # rest of the browser suite happens to create, so it can be masked but not
    # depended on: absent it paints nothing, and present it is covered rather
    # than back in the baseline.
    ".interrow:has(.interrow__assign) .interrow__detail",
    # The composer's `Toimus`, which defaults to today exactly as the create
    # form's `Saabus` does. `.createform .dateinput` above was scoped to that
    # one form for a good reason — unscoped it damaged three register
    # baselines — but the scoping is also why the identical control in the
    # composer was left uncovered.
    #
    # Found by comparing the committed baselines against a CI rendering rather
    # than by walking the DOM: this value lives in an `<input value>`, and a
    # text-node scan does not see it. `teema-koostaja` had been drifting one
    # day at a time since the day it was taken.
    #
    # Scoped to the attachment block, not `.composer .dateinput`: the deadline
    # and closing blocks hold date controls that are *empty*, and masking an
    # empty control paints out the one thing that baseline exists to show —
    # what the three disclosures look like when a lawyer opens them all. When
    # the block is shut it is `hidden`, so the input has no box and nothing is
    # painted anywhere else.
    "#koostaja-manus .dateinput",
    # ---- The department page's ISO-week counts (docs/adr/0039, ADR 0049).
    #
    # These are dates that never render as a date. Each is a plain integer
    # computed against a window anchored on *today*, so the seeded world's rows
    # sit at a fixed offset and the count is stable — until the run crosses a
    # Monday and a row that was "last week" is suddenly "this week", with the
    # same database and a different number on the page. `Uusi sellel nädalal`
    # did exactly that between two runs of one branch, from 17 to 18.
    #
    # «tähtaeg sel nädalal» is the sharper case and is why the pair is named
    # again after the merge: `WORK_DEADLINE_THIS_WEEK` runs from *today* to
    # Sunday, so it does not wait for a Monday — it shrinks every morning.
    #
    # Scoped to the value, through the label rather than through position: the
    # rail renders every row as the same `.railrow__key` / `.railrow__value`
    # pair, so the class alone would take all six of the page's rail counts —
    # the Aruandlus year rows beside them, which are *not* clock-derived and are
    # exactly what this baseline should still be checking. Matching on the label
    # text also survives a row being reordered inside its block, which a
    # positional `:nth-child` would not.
    #
    # The label, the row, the block, the borders and the spacing all stay in the
    # comparison. What is painted is one small box per value.
    '.railrow:has(.railrow__key:text-is("Uut sel nädalal")) .railrow__value',
    '.seis__figure:has(.seis__caption:text-is("tähtaeg sel nädalal")) .seis__number',
    # ---- The v2 surfaces, found by walking the rendered DOM for text that
    # looks like a date or a day count and asking which selector already
    # covered it — the same method that found the three above, and the reason
    # this block names four values rather than the two that were obvious.
    #
    # The register's Arvamused block. Its rows became the `submission`
    # component with the v2 design (02-EKRAANID §C), and the sent timestamp
    # went from a fixed-layout table cell to an inline `<dd>` on a meta line.
    # Inside a cell a changed minute stayed inside its column; inline it is a
    # pixel wider and shifts the rest of the line, so the four register
    # baselines drifted 0.34% between two runs of the *same commit* — above the
    # 0.2% limit, which means a baseline taken from one run was red on the
    # next. Reached through the `<dt>` rather than by position, so the label
    # «Saadetud» and the three other facts stay in the baseline and a reordered
    # meta line does not silently unmask anything.
    '.submission__meta div:has(dt:text-is("Saadetud")) dd',
    # Minu asjad's portfolio rows. `.pw-matter__when` is the whole cell because
    # the whole cell is the value — «vaikus 4 p» counts from `today` and
    # «muutus 29.8» is a date the seeded world places relative to it. There is
    # no label beside it to lose.
    ".pw-matter__when",
    # The rail's «Viimati muudetud» dates, same reasoning: the element holds
    # the date and nothing else. What changed is named on the line beside it
    # and stays in the baseline.
    ".pw-event__when",
    # The date at the end of a portfolio row's next step. NOT `.pw-matter__next`
    # — that span opens with the mode chip and the step in the lawyer's own
    # words, which is exactly the business content a baseline exists to check.
    # Only the tail is computed: `day_month` prints the action's date, and
    # `short_date` appends «1 p» or «8 p üle» counted from today, appearing at
    # all only once the date has passed. The template wraps that tail in a bare
    # `data-clock`, which carries no styling and changes nothing a reader sees
    # (templates/matters/partials/portfolio_row.html).
    "[data-clock]",
]

#: Osakond's two ISO-week counts, named once so the scenario entries cannot
#: drift apart.
#:
#: `Sissekandeid sel nädalal` and `Tähtaegu sel nädalal` were two more of these
#: and are no longer on the page: the Aruandlus block they were rows of is the
#: three-row year block now, and the week rows went with the merge (ADR 0049).
#: What replaced them is the Seis strip's «tähtaeg sel nädalal», which is a
#: sharper case than any of the three — `WORK_DEADLINE_THIS_WEEK` runs from
#: *today* to Sunday, so it shrinks every morning rather than only when the run
#: crosses a Monday.
OSAKOND_WEEK_COUNTS = (
    '.railrow:has(.railrow__key:text-is("Uut sel nädalal")) .railrow__value',
    '.seis__figure:has(.seis__caption:text-is("tähtaeg sel nädalal")) .seis__number',
)

#: What each scenario's capture may not silently stop masking.
#:
#: A Playwright mask selector that matches nothing does not fail — it paints no
#: rectangle and the run stays green. That is *why* the three selectors above
#: were missing for as long as they were, and adding them without a guard would
#: leave the next markup rename free to make them stop matching just as quietly.
#:
#: So a scenario that is known to render a clock-derived value declares it, and
#: `capture` refuses to take the screenshot if the element is not there. Only
#: the values found by rendering these pages are listed: this is a check that
#: known masks still bite, not a claim that the list is complete.
#: Only values that are on the page unconditionally belong here. A mask whose
#: element appears or not depending on how many Matters the functional suite
#: filed is still worth painting, but requiring it would turn an unrelated
#: browser test into a visual failure — see the `.interrow__detail` note above.
#: The register's Arvamused block and Minu asjad's portfolio rows, named once
#: for the same reason as the three counts above.
#:
#: Both are required rather than merely masked, because both are the selector
#: whose silence caused this round: the sent timestamp was never masked at all,
#: and the four register baselines it sits on were dying between one run and the
#: next. A rename that quietly stops matching would put them straight back.
#:
#: Neither depends on how busy the world is. The seeded world sends opinions and
#: gives Sandra open Matters before any functional test runs, and both blocks
#: render one element per row — so the only way to stop matching is to move the
#: markup, which is what this is here to catch.
#:
#: `[data-clock]` and `.pw-event__when` are deliberately *not* here. The first
#: renders only for a row whose next step carries a date, the second only when
#: something changed recently; absent, each paints nothing, and requiring
#: either would turn a quiet week into a visual failure.
OPINION_SENT = ('.submission__meta div:has(dt:text-is("Saadetud")) dd',)
PORTFOLIO_WHEN = (".pw-matter__when",)

#: Values that have to be held still, not merely covered.
#:
#: A mask hides glyphs. It does not stop the element being as wide as whatever
#: is inside it, and where that element sits on a line with other facts, its
#: width is *their* position. The register's Arvamused rows are the one place in
#: this suite where that matters: the sent timestamp is an inline `<dd>` on a
#: content-sized meta line, so a minute with narrower digits pulls «Teema»,
#: «Adressaat» and the file link a pixel left and 0.3% of the page differs —
#: measured between two runs of the same commit, and again with the mask in
#: place, which is how this was found rather than assumed.
#:
#: Barlow's tabular figures would fix the minute and not the date: `j.n.Y` drops
#: leading zeros, so `1.9.2026` is six pixels narrower than `29.8.2026` and the
#: line moves again on the first of the month. The product deliberately does not
#: zero-pad, and this suite does not get to ask it to.
#:
#: So the text is replaced with one of the same shape before the capture. The
#: layout in the baseline is then a layout the product really produces, for a
#: value that never changes. Nobody reads the placeholder: the same element is
#: in REQUIRED_MASKS for these scenarios, so a mask that stopped matching would
#: fail the capture rather than write a fictional date into a baseline.
#:
#: Nothing else is normalised. Everywhere else a masked value sits in a fixed
#: cell or at the end of its line, where a moving box edge leaves a sliver of
#: unpainted background and moves nothing — the drift this suite has always
#: accepted, and measured at a pixel or six.
NORMALISED_TEXT: tuple[tuple[str, str], ...] = ((OPINION_SENT[0], "29.8.2026 19:35"),)

REQUIRED_MASKS: dict[str, tuple[str, ...]] = {
    "minu-too": (".workband--entries .foldout__meta", *PORTFOLIO_WHEN),
    "minu-too-3440": (".workband--entries .foldout__meta", *PORTFOLIO_WHEN),
    "teemad-1280": OPINION_SENT,
    "teemad-1440": OPINION_SENT,
    "teemad-3440": OPINION_SENT,
    "teemad-filter": OPINION_SENT,
    "teema-suletud": (".banner--closed .banner__text .muted",),
    # This scenario opens `+ Manus` itself, so the control is always there.
    "teema-koostaja": ("#koostaja-manus .dateinput",),
    # Unlike `.interrow__detail` above, these three are required. They are not
    # rows of a capped list that a busy world can push off the end: `new_matters`
    # and `reporting` in `app/matters/overview.py` both return a fixed list of
    # `CountRow`s, so the row renders whatever the count is — nought included —
    # for as long as the scope is `Kogu osakond`, which is the scope both these
    # scenarios capture. If one stops matching, the markup moved and the
    # selector has to follow it.
    "osakond": OSAKOND_WEEK_COUNTS,
    "osakond-3440": OSAKOND_WEEK_COUNTS,
}

assert not {selector for selectors in REQUIRED_MASKS.values() for selector in selectors} - set(
    CLOCK_DEPENDENT
), "a required mask is not in CLOCK_DEPENDENT, so nothing paints it"

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


def visible(selector: str) -> str:
    """The same selector, restricted to elements that actually paint.

    A mask exists to cover pixels a clock put on the page. An element that
    renders nothing has no pixels to cover, so masking it can only do damage —
    and it did. The v2 «Näita veel N ▾» pattern has two shapes: the tables hide
    their overflow in a `tbody.uxextra { display: none }`, which has no box and
    was always harmless, while Osakond and Minu asjad keep theirs *inside* the
    closed `<details class="pw-more">`. Those rows still answer with a box, so
    Playwright painted one rectangle per hidden row — sixteen of them on the CI
    world — marching down the page over the «Tähtajad» heading, a group label,
    two deadline rows, the «Viimased muudatused» heading and its chips. Because
    `mask_color` is the page's own surface colour, the damage does not look like
    a black box. It looks like text that is not there.

    This is the third time a mask has hurt a page rather than a value: the
    register's filter inputs inside a shut disclosure did it once, the composer's
    empty date controls once. Filtering here rather than writing `:visible` into
    twenty-nine selectors means the next component to grow a disclosure is
    covered without anybody remembering this note.

    It can only ever *remove* masking, never widen it, so no value that was
    covered before stops being covered — unless it was never rendered.
    """
    return f"{selector}:visible"


def capture(page, name: str, *, full_page: bool = True, clip_to: str | None = None) -> bytes:
    page.add_style_tag(content=STYLE_FIXTURE)
    page.wait_for_load_state("networkidle")
    for selector in REQUIRED_MASKS.get(name, ()):
        assert page.locator(visible(selector)).count(), (
            f"{name}: the clock mask {selector!r} matches nothing on this page. "
            f"Either the markup moved and the selector needs following, or this "
            f"scenario no longer renders that value and the entry should go. "
            f"Leaving it unmatched would put a value that changes daily back "
            f"into the baseline, and the run would stay green until somebody "
            f"else's unrelated change went red for it."
        )
    for selector, canonical in NORMALISED_TEXT:
        # Only a value that is actually there may be replaced. Masking already
        # hides whatever this element says, and normalising on top of that would
        # hide one thing more: an element that had stopped rendering its value
        # at all. Empty, the box would shrink and the baseline would go red —
        # which is the signal. Writing the canonical string into it would paint
        # over that signal with a date that never was.
        elements = page.locator(visible(selector))
        for index in range(elements.count()):
            assert elements.nth(index).inner_text().strip(), (
                f"{name}: {selector!r} matched an element with no text, so there "
                f"is no clock-derived value here to hold still. Normalising it "
                f"would write {canonical!r} into a baseline as though the page "
                f"had rendered it."
            )
        page.eval_on_selector_all(
            selector,
            "(elements, text) => { for (const element of elements) element.textContent = text }",
            canonical,
        )
    masks = [page.locator(visible(selector)) for selector in CLOCK_DEPENDENT]
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
    """`Arvamused` on one Matter — reached from the facts rail, not a tab.

    The link moved with the position card. It is `Loe edasi` when there is a
    position to read on and `Lisa seisukoht` when there is not, because one
    destination serving two situations should say which one it is in
    (templates/matters/partials/position_rail.html).
    """
    open_matter(page, base_url, OPEN_TITLE)
    page.locator("#koja-seisukoht .railposition__more").click()
    page.wait_for_load_state("networkidle")
    compare("teema-seisukoht", capture(page, "teema-seisukoht"))


def test_matter_composer_expanded(page, base_url):
    """Every progressive disclosure open at once.

    The one state a screenshot is genuinely better at than an assertion: three
    optional blocks, each of which is a form, and the question is whether the
    composer still reads as one surface when a lawyer has opened all of them.
    """
    open_matter(page, base_url, OPEN_TITLE)
    # The composer is a disclosure now, so the chips inside it are not clickable
    # until it is open (design handoff 1d).
    open_composer(page)
    # Three, not four. `+ Kaasamine` is gone: Kaasamine has one path and it is
    # its own section (Teema QA §8).
    for chip in ("+ Manus", "+ Oluline tähtaeg", "+ Lõpeta teema"):
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


def _kaasamine(page):
    return page.locator("#kaasamine")


def _at_rest(page):
    """Take the pointer off whatever was just clicked, and settle.

    A click leaves the mouse where it landed, and the accordion head paints its
    `+ Lisa` in the link colour on hover — so the first rendering of these came
    back with the header hovered in one capture and at rest in another, for no
    reason a reader of the baseline could see. A baseline should show the state
    the test is named for and not where the mouse happened to stop.
    """
    page.mouse.move(0, 0)
    page.wait_for_timeout(120)


def test_kaasamine_collapsed_with_nothing_recorded(page, base_url):
    """The state a reader arrives at, on a Matter nobody has consulted about.

    Four clipped captures rather than four new full-page ones: the change these
    lock is one section's interaction, and a whole-page baseline per state would
    put four more pages' worth of unrelated layout under review every time
    anything else on Teema moved (Kaasamine one-click §23).

    The archive Matter, because it is the one the browser suite never writes to
    that also holds no engagement — the scratch Matter the interactive tests use
    is empty only until they run.
    """
    open_matter(page, base_url, ARCHIVE_TITLE)
    compare("kaasamine-suletud", capture(page, "kaasamine-suletud", clip_to="#kaasamine"))


def test_kaasamine_open_with_nothing_recorded(page, base_url):
    """One click, and what it opens onto is the form itself."""
    open_matter(page, base_url, ARCHIVE_TITLE)
    _kaasamine(page).locator(".accordion__head").click()
    _at_rest(page)
    compare("kaasamine-tyhi", capture(page, "kaasamine-tyhi", clip_to="#kaasamine"))


def test_kaasamine_open_with_a_record(page, base_url):
    """The records, and the composer waiting behind its own control."""
    open_matter(page, base_url, OPEN_TITLE)
    _kaasamine(page).locator(".accordion__head").click()
    _at_rest(page)
    compare("kaasamine-kirjed", capture(page, "kaasamine-kirjed", clip_to="#kaasamine"))


def test_kaasamine_composer_open_over_a_record(page, base_url):
    """`+ Lisa` from collapsed: the section and the form in one action."""
    open_matter(page, base_url, OPEN_TITLE)
    _kaasamine(page).locator("[data-engagement-add-trigger]").click()
    _at_rest(page)
    compare("kaasamine-lisa", capture(page, "kaasamine-lisa", clip_to="#kaasamine"))


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
    signed_in(page, base_url, "/jalgimine/tahtajad/")
    compare("jalgimine", capture(page, "jalgimine"))


def test_dashboard(page, base_url):
    """Osakond: the department page a specialist reads.

    Renamed from `ulevaade` rather than replaced, because it is the same
    scenario at the same viewport for the same reader — `/ulevaade/` became
    `/osakond/` (ADR 0049) — and the coverage is continuous across the rename.

    The composition this locks is a header band, a one-line Seis strip whose
    every figure is a link, the intervention list, the five deadline groups and
    the three-block facts rail. Signed in as a specialist, so *Meeskond* and
    *Tehtud* are absent: that is the access boundary, and this baseline is one
    of the places it would be visible if it broke.

    Dates are masked, because the seeded world computes them from today; the
    composition may not move.
    """
    signed_in(page, base_url, "/osakond/")
    compare("osakond", capture(page, "osakond"))


def test_a_closed_disclosure_contributes_no_masks(page, base_url):
    """The rows behind «Näita veel N ▾» must not paint over the page.

    Not a screenshot: the damage this guards against is invisible to
    `compare()` in the only way that matters, because a mask painted in the
    page's own colour looks exactly like a baseline that was taken correctly.
    So it is asserted where it can be read — every mask this suite paints
    resolves to something a reader can see.

    Osakond is the scenario because it is the one that broke, and the first
    assertion keeps it honest: if the seeded world ever stops overflowing that
    list, this test is measuring nothing, and it says so rather than passing.
    """
    signed_in(page, base_url, "/osakond/")
    hidden = page.locator("details.pw-more:not([open]) .interrow__reason")
    assert hidden.count(), (
        "Osakond no longer hides intervention rows behind «Näita veel N ▾», so "
        "this test no longer exercises the case it exists for. Either the "
        "section stopped capping its preview, or the seeded world dropped below "
        "the cap — move the assertion to whichever surface still overflows."
    )
    for selector in CLOCK_DEPENDENT:
        inside = page.locator(f"details.pw-more:not([open]) {visible(selector)}")
        assert inside.count() == 0, (
            f"{selector!r} matches {inside.count()} element(s) inside a closed "
            f"disclosure and Playwright would paint a rectangle for each, in the "
            f"page's own colour, over whatever happens to be underneath."
        )


def test_my_work(page, base_url):
    """Minu töö: one chronological timeline and the rail beside it.

    Every mode shares the bands; the rail holds only what has no date. The band
    a row lands in depends on the weekday the job runs, which is why the dates
    themselves are masked and the bands are asserted in Python instead.
    """
    signed_in(page, base_url, "/minu-asjad/")
    compare("minu-too", capture(page, "minu-too"))


# ---- Ultrawide -----------------------------------------------------------
#
# The four above are taken at 1440, the design's primary viewport, and 1440 is
# exactly where the workspace bound does nothing: below 1600 every one of them
# renders as it always did. So none of them can say whether the bound works,
# and the failure it fixes was only ever visible on a monitor none of them
# describe — a QA screenshot at 3440 where a department row put its title at one
# bezel and its owner near the other.
#
# What these lock is the composition at 3440: a bounded workspace in the middle
# of the monitor, outer margin either side, the facts rail flush against the
# content rather than the screen edge. The *relationships* — narrower than the
# viewport, centred, rail inside the workspace — are assertions and live in
# `test_ultrawide_workspace.py`, because a baseline approves a broken layout as
# readily as a correct one. These say the result also looks right.
#
# Statistika is here and is not at 1440, which is deliberate: it is the one
# surface using the centred `.page` container whose content is charts and wide
# tables rather than rows, so it is the one most likely to reveal a bound that
# is right for lists and wrong for everything else.

#: The ultrawide the QA round photographed. Height is the ordinary 900: these
#: are full-page captures, so it sets how much is above the fold and nothing
#: else.
ULTRAWIDE = {"width": 3440, "height": 900}


@pytest.mark.parametrize(
    "name,path",
    [
        ("osakond-3440", "/osakond/"),
        ("minu-too-3440", "/minu-asjad/"),
        ("teemad-3440", "/teemad/"),
        ("statistika-3440", "/statistika/"),
    ],
)
def test_the_bounded_workspace_at_3440(page, base_url, name, path):
    signed_in(page, base_url, path, width=ULTRAWIDE["width"], height=ULTRAWIDE["height"])
    compare(name, capture(page, name))


# ---- Vali kasutaja -------------------------------------------------------
#
# These six run against the *shared-gate* server, because the persona switcher
# only exists in that mode. They are the one part of this suite whose subject is
# an overlay, so four of them are viewport captures rather than full-page ones:
# an absolutely-positioned popover is not inside its parent's bounding box, and
# an element screenshot of the pill would come back without the thing being
# reviewed on it.
#
# The page they are taken on is `/konto/kasutaja/`, which is the only surface in
# the application with no clock-derived content at all. A popover captured over
# the dashboard would carry that page's dates behind it and go red the next
# morning for a reason that has nothing to do with the popover.

#: A bar and a popover, and nothing below them worth capturing.
BAR_VIEWPORT = {"width": 1440, "height": 420}


def _behind_the_gate(page, gate_base_url: str, path: str = "/konto/kasutaja/"):
    pass_the_gate(page, gate_base_url)
    page.set_viewport_size(DESKTOP_VIEWPORT)
    page.goto(f"{gate_base_url}{path}")
    page.wait_for_load_state("networkidle")
    return page


def _open_popover(page):
    page.locator("#persona-pill").click()
    page.wait_for_timeout(50)
    return page


def test_persona_page_with_a_selected_person(page, gate_base_url):
    """A. The list, the active row, and the dashed no-persona choice."""
    _behind_the_gate(page, gate_base_url)
    page.locator("button.personarow").first.click()
    page.wait_for_load_state("networkidle")
    page.goto(f"{gate_base_url}/konto/kasutaja/")
    page.wait_for_load_state("networkidle")
    compare("persona-leht-valitud", capture(page, "persona-leht-valitud"))


def test_persona_page_with_nobody_selected(page, gate_base_url):
    """B. The state a visitor actually lands in."""
    _behind_the_gate(page, gate_base_url)
    compare("persona-leht-ilma", capture(page, "persona-leht-ilma"))


def test_persona_pill_closed(page, gate_base_url):
    """C. The bar carrying a selected persona, popover shut."""
    _behind_the_gate(page, gate_base_url)
    page.locator("button.personarow").first.click()
    page.wait_for_load_state("networkidle")
    page.goto(f"{gate_base_url}/konto/kasutaja/")
    page.set_viewport_size(BAR_VIEWPORT)
    compare(
        "persona-pill-suletud",
        capture(page, "persona-pill-suletud", full_page=False),
    )


def test_persona_popover_open(page, gate_base_url):
    """D. The popover, with the first candidate selected."""
    _behind_the_gate(page, gate_base_url)
    page.locator("button.personarow").first.click()
    page.wait_for_load_state("networkidle")
    page.goto(f"{gate_base_url}/konto/kasutaja/")
    page.set_viewport_size(BAR_VIEWPORT)
    _open_popover(page)
    compare(
        "persona-popover-avatud",
        capture(page, "persona-popover-avatud", full_page=False),
    )


def test_persona_popover_active_row_follows_the_selection(page, gate_base_url):
    """E. A *different* row carries the tick.

    Distinct from D on purpose: it is what proves the active marker follows who
    is selected rather than being pinned to the top of the list, which is the
    defect a single popover baseline would approve.
    """
    _behind_the_gate(page, gate_base_url)
    page.locator("button.personarow", has_text=HEAD_NAME).first.click()
    page.wait_for_load_state("networkidle")
    page.goto(f"{gate_base_url}/konto/kasutaja/")
    page.set_viewport_size(BAR_VIEWPORT)
    _open_popover(page)
    compare(
        "persona-popover-aktiivne",
        capture(page, "persona-popover-aktiivne", full_page=False),
    )


def test_persona_popover_with_nobody_selected(page, gate_base_url):
    """F. The dashed pill and the popover with no tick on a name."""
    _behind_the_gate(page, gate_base_url)
    page.set_viewport_size(BAR_VIEWPORT)
    _open_popover(page)
    compare(
        "persona-ilma-popover",
        capture(page, "persona-ilma-popover", full_page=False),
    )
