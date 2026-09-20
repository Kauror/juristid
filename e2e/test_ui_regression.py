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

A mask covers glyphs and nothing else. Where a clock-derived value shares a line
with content, or is followed by anything on the same row, its *width* is that
content's position — and a mask is exactly as wide as whatever is inside it. So
those values are rewritten to a canonical string before the capture as well:
`NORMALISED_TEXT`, declared per scenario in `REQUIRED_NORMALISATIONS`, and
proved against the calendar rather than against today in the last section of
this file.

A second table, `SCENARIO_NORMALISED_TEXT`, holds the case the first cannot: a
date slot whose *class* renders a clock value in one row and a date the fixture
chose in the next. `.tl-step__date` and `.uxtl__msdate` are that. The seeded
closed Matter's «Alustatud» and «Lõpetatud» are the day the run happened, and so
are the open Matter's «Alustatud» / «Koja arvamus» and its «Teema loodud» /
«Arvamus välja» — `created_at` and `sent_at`, stamped by the seeding
transaction. Beside them on the same page, through the same two classes, are
«Jõustumine 1.1.2028» and a Kaasamine in May, which `seed_e2e_data` chose and
which belong in the baseline.

So an entry says which capture it applies to, and — where one capture prints
both meanings — which milestone by name. A baseline goes red for a seeded date
that moved, and not because midnight passed. The table itself carries the
contract in full.

Inside a table it is not a line that moves but the whole grid. An auto-layout
table sizes every column from its content, so one cell that gains a character
resizes its column and every other column redistributes to pay for it, on every
row. Masking that cell covers the digits and changes nothing about the sizing,
which is how `teema-dokumendid` came to differ by 0.2111% on the morning
`9.9.2026` became `10.9.2026`. `capture` refuses to photograph such a value:
`assert_no_clock_value_sizes_a_column` is the rule that a clock value inside an
auto-layout table is held still rather than merely covered.

Baselines
---------
Committed under `e2e/baselines/`, produced by this same job on this same
container image: a screenshot taken on a developer machine would differ in font
rasterisation on every pixel of every glyph.

To retake one, push with `E2E_UPDATE_BASELINES` left at `"0"`, let this step
fail, and copy the scenarios you meant to change out of that same run's
`test-report-visual` upload — `capture` writes `visual-<name>.png` from the same
bytes a baseline write would use, so a candidate is byte-identical to a
regeneration. `E2E_UPDATE_BASELINES=1` is for the first baseline of a brand-new
scenario and nothing else: it rewrites *every* file and skips the comparison, so
a run with it on refreshes scenarios nobody looked at and reports nothing.

A missing baseline **fails**, by name. It used to skip, so that a brand-new
scenario would not turn the build red before anybody had looked at what it
captured — and the Teema rebuild then spent a whole round with ten scenarios
green *because they were skipping*, which is the failure mode this whole step
exists to prevent. The first run of a new scenario is red by design: it writes
the candidate into `test-report-visual`, somebody looks at it, and it is
committed. That costs one round and buys the property that a green visual job
means every scenario in it actually compared.

What these renderings do and do not depend on
--------------------------------------------
This step used to run after the functional browser suite against the database it
had spent fourteen minutes writing to, so a new test that filed a Matter turned
nine baselines red. It does not any more: `visual` is its own job with its own
PostgreSQL, its own migrations and its own `seed_e2e_data`, and nothing runs
before the first screenshot. A page here therefore depends on exactly two
things — the code, and what day it is. The second is what the masks and
`NORMALISED_TEXT` are for, and it is the one that goes wrong quietly.
"""

from __future__ import annotations

import os
import pathlib

import pytest

from app.core.management.commands.seed_e2e_data import ARCHIVE_TITLE, OPEN_TITLE
from e2e.conftest import (
    DESKTOP_VIEWPORT,
    SANDRA,
    open_add_panel,
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
    ".curact__date",
    # `Excelist` and the register snapshot label beside an imported instruction.
    ".curact__flag",
    # The outstanding `Arvamuse tähtaeg` stated under the task — «20.09 · 25 p
    # üle». Both halves move with the clock: the day is `short_day_month` and
    # the count is measured from today.
    #
    ".curact__oweddate",
    # The register's rendering of the same fact, in the Kuupäev cell under the
    # date the row plans on.
    ".dateowed__value",
    # The `Ajajoon` head's entry count, which every functional test that writes
    # a note increments.
    #
    # The preview pill beside it — `.uxtl__previewnext`, the current step's date
    # repeated in a summary line — and the folded system run's date span are
    # both gone from the page: the approved target's head is the label and the
    # count, and there are no folded runs (docs/adr/0074 §14, §16).
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
    # The sent date on a Dokumendid opinion row. Covered by the bare `time`
    # below as well; named explicitly because it is normalised by this exact
    # selector, and a selector that appears in one list and not the other is how
    # the two drift apart (docs/adr/0061).
    ".doctable__sent time",
    # The Dokumendid table's `Kuupäev` cell, named for the same reason and one
    # step further: this one is inside an auto-layout table, so covering it
    # holds nothing still at all. See `EVIDENCE_DATE` and the section at the
    # foot of this file.
    ".doctable .table__date time",
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
    # text-node scan does not see it. The composer capture had been drifting one
    # day at a time since the day it was taken.
    #
    # Scoped to the attachment block, not `.composer .dateinput`: the deadline
    # and closing blocks hold date controls that are *empty*, and masking an
    # empty control paints out the one thing that baseline exists to show —
    # what the three disclosures look like when a lawyer opens them all. When
    # the block is shut it is `hidden`, so the input has no box and nothing is
    # painted anywhere else.
    # `+ Manus` is gone and its date box with it; the workspace's own dates are
    # empty at rest and masking an empty control paints out the one thing the
    # `teema-lisa` baseline exists to show (docs/adr/0074 §6, docs/adr/0075 §2).
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

#: The Dokumendid row's «Saadetud <date> · <ministeerium>».
#:
#: New with docs/adr/0061, and normalised rather than merely masked for the
#: reason the module docstring gives: the recipient sits on the same line, after
#: the date, so the mask's *width* is that name's position. `29.8.2026 19:35` is
#: 15 characters whatever the seed stamped, and the ministry stops moving.
#:
#: Required on both scenarios that render it, because the seeded world sends an
#: opinion on `OPEN_TITLE` before any test runs — so an absence here means the
#: markup moved, not that the world happened to be quiet.
OPINION_ROW_SENT = (".doctable__sent time",)

#: The Dokumendid table's `Kuupäev` cell — `Document.created_at`, in `j.n.Y`.
#:
#: `created_at` is `auto_now_add`, so on the seeded world this is the wall clock
#: of the run itself: every file in the table carries the day CI ran, and the
#: column is as wide as that string. See the section at the foot of this file
#: for why a mask cannot hold it — the digits are covered and the *column* is
#: not, and `.doctable` is laid out `auto`.
#:
#: Held still at eight characters, because that is the shape the committed
#: baselines hold: they were adopted on the sixth of September, and `j.n.Y` on a
#: single-digit day of a single-digit month is `6.9.2026`. `.table__date` sets
#: tabular figures, so the width is a function of the character count alone and
#: *which* eight characters they are cannot matter — measured rather than
#: assumed: this scenario's CI rendering on 2026-09-09, three days and three
#: different digits later, differed from the committed baseline by nought
#: pixels, and the one taken the next morning by 2,736.
EVIDENCE_DATE = (".doctable .table__date time",)

#: Minu asjad's horizon control — «Kuni oktoober ▾», «Kuni november ▾».
#:
#: Scoped to the band, because `.rangepicker__trigger` is also the department
#: page's «Järjesta: …» control, whose label is a sort order and is not
#: clock-derived at all. Normalising that one would freeze real content.
#:
#: The menu inside the `<details>` lists every month it offers, and is
#: deliberately not named: it is `position: absolute` inside a shut disclosure,
#: so it paints nothing and moves nothing.
HORIZON_LABEL = (".workband--hiljem .rangepicker__trigger",)

#: The register's «Tähtaeg sel kuul · N» saved view.
#:
#: The chip is one text node — label and count together — so the whole chip is
#: normalised rather than a count element that does not exist. The label half is
#: constant, so what is actually being held still is the digit.
#:
#: Reached through its text rather than by position: `saved_view_definitions`
#: returns four chips and only this one is clock-derived, and matching on the
#: label survives the four being reordered.
MONTH_VIEW_CHIP = ('.uxviews .uxchip:has-text("Tähtaeg sel kuul")',)

#: The closed banner's «(29.8.2026)», already masked and already required.
CLOSED_ON = (".banner--closed .banner__text .muted",)

#: The seeded closed Matter's own two days, on the two surfaces of `teema-suletud`
#: that print them: the process strip's «Alustatud» / «Lõpetatud» dates, and the
#: `Ajajoon` milestones «Teema suletud» and «Teema loodud».
#:
#: All four are one pair of facts. `seed_e2e_data` creates `CLOSED_TITLE` and
#: closes it in the same transaction, so `Matter.created_at`, `Matter.closed_at`
#: and the two `MATTER_CREATED` / `MATTER_CLOSED` change events are all stamped
#: with the wall clock of the run — and every one of them renders through
#: `format_estonian_date`, which is `j.n.Y`. The page therefore prints the day CI
#: ran, four times, and prints it in the one format that changes *length*: the
#: product does not zero-pad, so `12.9.2026` is nine characters and `1.10.2026`
#: is nine too but a different set of advances, and `9.9.2026` is eight.
#:
#: These four were the last clock values on this capture that nothing held: they
#: are not `<time>` elements, so the bare `time` mask never reached them, and
#: they are not in `CLOCK_DEPENDENT` under any other name. Unmasked and
#: unnormalised, every one of them is in the committed baseline as the digits of
#: the morning it was taken.
#:
#: **Scenario-scoped, and this is the whole reason `SCENARIO_NORMALISED_TEXT`
#: exists.** Both classes render on the open Matter too, where *some* of what
#: they hold is nothing of the kind: `teema-ulevaade` prints «Jõustumine
#: 1.1.2028» and «Kaasamine … 12.5.2026» through the same two selectors, and
#: those are dates the fixture chose. A global entry would rewrite them into a
#: canonical, move three baselines that have nothing to do with this, and — far
#: worse — take real, fixture-chosen content out of the comparison, which is
#: exactly the masking-too-much failure the rest of this module is written
#: against. The open Matter's *own* run-day slots are held still by name
#: instead: see `STRIP_RUN_DAY` and `CHRONOLOGY_RUN_DAY` below.
CLOSED_MATTER_DAYS = (".tl-step__date", ".uxtl__msdate")

#: The two process-strip columns whose date is the day the run happened, named
#: by the label beside them rather than by the class they share.
#:
#: `seed_e2e_data` creates `OPEN_TITLE` and sends its one opinion in the same
#: seeding transaction, so `Matter.created_at` and `Submission.sent_at` are both
#: the wall clock of the run — and `Alustatud` and `Koja arvamus` are the two
#: columns `process_steps` derives from exactly those two records. Both print
#: through `format_estonian_date`, `j.n.Y`, which does not zero-pad: `9.9.2026`
#: is eight characters and `12.9.2026` is nine.
#:
#: The other two columns on this strip are **not** here and must not be. They
#: are the Matter's two commencements — «Jõustumine 27.9.2027 / 1.1.2028», from
#: `add_effective_date` — and a strip that had frozen them would stop comparing
#: the one thing `teema-kaik` exists to compare, which is that a known future
#: milestone is drawn exactly like a completed one (docs/adr/0074 §12.2).
#:
#: Scoped by `.tl-step__what` and not by position. The columns are ordered by
#: date, so a fixture that gave this Matter a `Arvamuse tähtaeg` would renumber
#: them and an `:nth-child` would quietly start holding a seeded date still; the
#: label is what actually identifies the milestone, and `process_timeline.py`
#: declares both of these as constants.
STRIP_RUN_DAY = (
    '.tl-step:has(.tl-step__what:text-is("Alustatud")) .tl-step__date',
    '.tl-step:has(.tl-step__what:text-is("Koja arvamus")) .tl-step__date',
)

#: The chronology's two run-day milestones, named the same way and for the same
#: reason: they are the other rendering of the same two records.
#:
#: `Teema loodud` is the `MATTER_CREATED` audit event and `Arvamus välja` is the
#: sent `Submission`, so both are stamped by the seeding run — the strip calls
#: them `Alustatud` and `Koja arvamus`, the chronology calls them these, and
#: they are one pair of facts printed twice (`app/matters/timeline.py`).
#:
#: The two chronology dates that stay in the baseline are the ones a person
#: chose: «Kaasamine … 12.5.2026», which `seed_e2e_data` passes as
#: `occurred_on=date(2026, 5, 12)`, and the `Töövõit`'s reporting period. The
#: engagement's date is the whole content of the row `teema-ajajoon` compares,
#: so holding it still would photograph a canonical instead of the fixture.
CHRONOLOGY_RUN_DAY = (
    '.uxtl__ms:has(.uxtl__mswhat:text-is("Teema loodud")) .uxtl__msdate',
    '.uxtl__ms:has(.uxtl__mswhat:text-is("Arvamus välja")) .uxtl__msdate',
)

#: `12.9.2026` — the canonical visual day, and the same one `teema-suletud`
#: already holds.
#:
#: One string for every scenario in the table below, because these are all the
#: same fact: this world was seeded today. Two canonicals would put two
#: different "today"s in one baseline set, and the first person to compare
#: `teema-kaik` against `teema-suletud` would be reading a contradiction the
#: product cannot produce. It is a value `format_estonian_date` really renders —
#: the twelfth of September.
CANONICAL_RUN_DAY = "12.9.2026"

#: The Ajajoon summary's «29.8», the `<time>` the timeline preview leads with.
#:
#: The `Ajajoon` head's preview quote and its date are both gone: the approved
#: target's head is the label and the count (docs/adr/0074 §16).
TIMELINE_PREVIEW_ON: tuple[str, ...] = ()

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
#: Everywhere else a masked value sits in a fixed cell or at the end of its
#: line, where a moving box edge leaves a sliver of unpainted background and
#: moves nothing — the drift this suite has always accepted, and measured at a
#: pixel or six.
#:
#: Three more joined on 2026-09-01, each measured against a CI rendering of the
#: same tree the committed baselines came from rather than reasoned about. All
#: three are *calendar* drift rather than daily drift, which is why they went
#: unnoticed: a value that only moves on the first of a month leaves a baseline
#: correct for four weeks and then makes somebody else's unrelated pull request
#: red.
#:
#: «Tähtaeg sel kuul · N» is the one that nearly did it. `saved_view_definitions`
#: builds that chip from `_month_bounds(today)`, so a seeded deadline four days
#: out is next month's until the month turns and this month's afterwards. In
#: Barlow's proportional figures `1` is narrower than `0`, so the chip shrinks
#: and «Vastutajata», «Kogu osakond» and «+ Salvesta …» all move with it:
#: 2,471 pixels, 0.1516% of `teemad-1440` against a 0.2% limit, and 83% of
#: everything that was drifting on that baseline.
#:
#: «Kuni oktoober ▾» is `default_horizon`, which is today plus a fixed number of
#: months, so the month name changes on the first and the names differ in width.
#: The control is right-aligned at the end of its band header, so only its own
#: left edge moves — 260 pixels, and the whole of `minu-too`'s drift.
#:
#: «(29.8.2026)» and «29.8» are the case this very docstring predicted and did
#: not act on: both are masked, `j.n.Y` and `j.n` drop leading zeros, and on the
#: first of the month `1.9.2026` is narrower than `29.8.2026`. The *mask* then
#: shrinks, which is not a value peeking out — it is a rectangle that stops
#: covering pixels it used to cover, and where something shares the line it
#: takes that with it. Together 0.0831% of `teema-suletud`, and all but 7 pixels
#: of it is the Ajajoon quote sliding 4px left behind a narrower `<time>`.
NORMALISED_TEXT: tuple[tuple[str, str], ...] = (
    (OPINION_SENT[0], "29.8.2026 19:35"),
    # The month the committed baselines already hold, so stabilising this moves
    # no image. It is a value `default_horizon` really produces — in August.
    (HORIZON_LABEL[0], "Kuni oktoober ▾"),
    # Today's real value, and the one `teemad-1280`'s freshly taken baseline
    # already holds. Choosing `0` instead would have been equally stable and
    # would have moved one baseline more.
    (MONTH_VIEW_CHIP[0], "Tähtaeg sel kuul · 1"),
    (CLOSED_ON[0], "(29.8.2026)"),
    (OPINION_ROW_SENT[0], "29.8.2026 19:35"),
    # Eight characters, because that is what the committed baselines hold and
    # tabular figures make the count the whole of it. A ten-character canonical
    # would be exactly as stable and would move two baselines to get there.
    (EVIDENCE_DATE[0], "6.9.2026"),
)

#: The same mechanism, for a value whose *selector* is not scenario-specific.
#:
#: `NORMALISED_TEXT` is applied to every capture, which is right for every entry
#: in it: each of those selectors names one element that renders on a handful of
#: pages and is clock-derived on all of them. `.tl-step__date` and
#: `.uxtl__msdate` are not like that. They are the process strip's and the
#: chronology's date slots, and *what* they hold is a property of the record
#: behind the row rather than of the class: the same two classes print a
#: `created_at` the run stamped a moment ago, a commencement in 2028 and a
#: consultation held in May, all on one page.
#:
#: The contract, stated once because everything below is an instance of it:
#:
#: 1. **Scenario-scoped normalisation is required.** A capture may hold a date
#:    slot still only where *that* capture renders a clock value in it, and must
#:    leave the identical markup alone on every other capture.
#: 2. **Some of these slots hold meaningful seeded dates, and those stay in the
#:    baseline.** «Jõustumine 27.9.2027 / 1.1.2028», «Kaasamine … 12.5.2026»
#:    and the `Töövõit`'s reporting period are what `seed_e2e_data` chose, and
#:    they are the content the Matter captures exist to compare. Freezing them
#:    would take real, fixture-chosen content out of the comparison — the
#:    masking-too-much failure the rest of this module is written against.
#: 3. **`teema-suletud` is scoped by scenario alone; every other Matter capture
#:    is scoped by selector as well.** On the closed Matter *every* slot in both
#:    classes is a run-day value — `created_at` and `closed_at`, stamped in one
#:    seeding transaction — so the bare class is exactly the right scope there.
#:    The open and archive Matters mix run-day and seeded dates in one list, so
#:    their entries name the milestone by its own label instead
#:    (`STRIP_RUN_DAY`, `CHRONOLOGY_RUN_DAY`).
#: 4. **A visual test should fail for a seeded-date regression, and not because
#:    midnight passed.** Both halves are asserted, not trusted: the section at
#:    the foot of this file proves that the run-day slots come out the same on
#:    every calendar day *and* that the seeded ones come out untouched, and
#:    `REQUIRED_NORMALISATIONS` turns a selector that stops matching into a
#:    failed capture rather than a baseline that quietly goes stale.
#:
#: Anything whose *selector* can carry the scoping on its own belongs in
#: `NORMALISED_TEXT` above, where it is one list to read; this table is for the
#: case where the page renders one class in two meanings at once.
_STRIP_AND_CHRONOLOGY_RUN_DAYS = (*STRIP_RUN_DAY, *CHRONOLOGY_RUN_DAY)

SCENARIO_NORMALISED_TEXT: dict[str, tuple[tuple[str, str], ...]] = {
    # The closed Matter: created and closed by the run, so both classes hold a
    # run-day value in every slot and the bare class is the correct scope.
    "teema-suletud": tuple((selector, CANONICAL_RUN_DAY) for selector in CLOSED_MATTER_DAYS),
    # The open Matter, clipped to the process strip and to the chronology. Each
    # clip carries only the slots that are inside it, so a selector declared
    # here is a selector that capture really renders — which is what lets
    # `REQUIRED_NORMALISATIONS` insist on all of them.
    "teema-kaik": tuple((selector, CANONICAL_RUN_DAY) for selector in STRIP_RUN_DAY),
    "teema-ajajoon": tuple((selector, CANONICAL_RUN_DAY) for selector in CHRONOLOGY_RUN_DAY),
    # The same open Matter, whole, at both widths. These two carry the strip and
    # the chronology together, and they were drifting exactly as the clipped
    # pair were — more quietly, because a few hundred differing pixels is a far
    # smaller fraction of a full page than of a 1,400×120 strip. A stale
    # baseline nobody can see is worse than a red one: the cost lands on
    # whoever's unrelated change finally pushes the total past the limit.
    "teema-ulevaade": tuple(
        (selector, CANONICAL_RUN_DAY) for selector in _STRIP_AND_CHRONOLOGY_RUN_DAYS
    ),
    "teema-1024": tuple(
        (selector, CANONICAL_RUN_DAY) for selector in _STRIP_AND_CHRONOLOGY_RUN_DAYS
    ),
    # The archive row, which has one of the four and only one. It is a
    # register-archive record, so `process_steps` gives it no `Alustatud` — an
    # imported row's `created_at` is a fact about a migration — and it draws no
    # strip at all; it never sent an opinion either. What it does have is the
    # `MATTER_CREATED` event this seeding run wrote, which prints «Teema loodud»
    # with today's date exactly as the other two Matters do. Declaring the
    # absent three would fail every capture, which is `REQUIRED_NORMALISATIONS`
    # working rather than a reason to widen the entry.
    "teema-arhiiv": ((CHRONOLOGY_RUN_DAY[0], CANONICAL_RUN_DAY),),
}


def normalisations_for(name: str) -> tuple[tuple[str, str], ...]:
    """Every (selector, canonical) pair that applies to one scenario.

    The two tables are one contract and are read as one everywhere — by the
    capture, by the declaration check below, and by the tests at the foot of
    this file. A second reader that forgot the scenario-scoped half is how the
    halves would drift apart.
    """
    return (*NORMALISED_TEXT, *SCENARIO_NORMALISED_TEXT.get(name, ()))


#: What each scenario's capture may not silently stop *normalising*.
#:
#: The exact hazard `REQUIRED_MASKS` exists for, one mechanism along. A
#: Playwright selector that matches nothing is not an error: `eval_on_selector_all`
#: rewrites zero elements and the run stays green — with the real clock value
#: back on the page and the baseline red on the first of next month, for a
#: change that has nothing to do with whoever is looking at it.
#:
#: So a scenario that depends on a value being held still says so, and `capture`
#: refuses to take the screenshot when the element is not there.
#:
#: `OPINION_SENT` and `CLOSED_ON` are also in `REQUIRED_MASKS`, which already
#: asserts the same elements. Declared here anyway: the rule is «every
#: normalisation a scenario depends on is declared», and a rule with two
#: exceptions is a rule nobody applies to the fifth entry.
REQUIRED_NORMALISATIONS: dict[str, tuple[str, ...]] = {
    "minu-too": HORIZON_LABEL,
    "minu-too-3440": HORIZON_LABEL,
    "teemad-1280": (*OPINION_SENT, *MONTH_VIEW_CHIP),
    "teemad-1440": (*OPINION_SENT, *MONTH_VIEW_CHIP),
    "teemad-3440": (*OPINION_SENT, *MONTH_VIEW_CHIP),
    "teemad-filter": (*OPINION_SENT, *MONTH_VIEW_CHIP),
    # The seeded closed Matter is closed with an entry, so the banner date is on
    # this page every run. It is not required anywhere else: the banner belongs
    # to a closed Matter, and requiring an element that can legitimately be
    # absent turns a quiet week into a visual failure.
    #
    # The timeline preview is no longer among them. It is the *closed*
    # chronology's line — the last thing somebody wrote, the step that is owed —
    # and the 2026-09 refinement hides it while the section is open, which it is
    # on arrival. Nothing on this capture renders that date any more, so a mask
    # for it would cover no pixels and requiring it would fail every run
    # (design handoff I11, docs/matter-page-refinement.md).
    #
    # The process strip's two dates and the two `Ajajoon` milestones join it for
    # the same reason it is here: the seeded closed Matter is created and closed
    # by the run, so all four are on this page every time, and an absence means
    # the markup moved rather than that the world was quiet.
    "teema-suletud": (*CLOSED_ON, *CLOSED_MATTER_DAYS),
    # The open Matter's own two records, on the four captures that render them.
    # `seed_e2e_data` creates this Matter and sends its opinion on every run, so
    # each of these is on its page every time: an absence means the markup or
    # the projection moved, which is the failure this declaration buys.
    #
    # Named per capture rather than as one set, because the clipped pair really
    # do hold only half each — `teema-kaik` photographs `.tl-strip` and
    # `teema-ajajoon` photographs `#ajalugu-loend`. Requiring the other half
    # there would fail every run for a value that is genuinely not in the image.
    "teema-kaik": STRIP_RUN_DAY,
    "teema-ajajoon": CHRONOLOGY_RUN_DAY,
    "teema-ulevaade": _STRIP_AND_CHRONOLOGY_RUN_DAYS,
    "teema-1024": _STRIP_AND_CHRONOLOGY_RUN_DAYS,
    # The archive row draws no process strip and has sent no opinion, so
    # «Teema loodud» is the whole of what it renders from the run's clock.
    "teema-arhiiv": (CHRONOLOGY_RUN_DAY[0],),
    # The seeded world sends one opinion on `OPEN_TITLE`, so both of these
    # render a `Saadetud <date>` under a filename on every run — and both are
    # the same evidence table, so both carry a `Kuupäev` column whose width is
    # where the other three columns start. `teema-arvamused` is this same page
    # filtered to `Arvamus`: it drifted 0.0913% overnight on the run that took
    # `teema-dokumendid` past the limit — the same defect with fewer rows to
    # differ on, and therefore with nothing to say so.
    "teema-dokumendid": (*OPINION_ROW_SENT, *EVIDENCE_DATE),
    "teema-arvamused": (*OPINION_ROW_SENT, *EVIDENCE_DATE),
    # The two Teema captures that used to render a folded system run are not
    # here any more. The approved target has no folded run: those events are
    # milestones in their own right — `Teema loodud`, `Hetkeseis: …` — or
    # ordinary work, each on its own line (docs/adr/0074 §14).
}


def _assert_every_required_normalisation_is_declared() -> None:
    """Per scenario, rather than against the union of the two tables.

    A selector required on one capture and normalised only on another holds
    nothing still on the capture that declared it, and a union would not notice.
    Run at import, because a declaration that does not hold should stop the
    module rather than fail one test in it.
    """
    for scenario, selectors in REQUIRED_NORMALISATIONS.items():
        assert not set(selectors) - {selector for selector, _ in normalisations_for(scenario)}, (
            f"{scenario}: a required normalisation is neither in NORMALISED_TEXT "
            f"nor in this scenario's SCENARIO_NORMALISED_TEXT, so nothing holds "
            f"it still"
        )


_assert_every_required_normalisation_is_declared()

REQUIRED_MASKS: dict[str, tuple[str, ...]] = {
    "minu-too": (".workband--entries .foldout__meta", *PORTFOLIO_WHEN),
    "minu-too-3440": (".workband--entries .foldout__meta", *PORTFOLIO_WHEN),
    "teemad-1280": OPINION_SENT,
    "teemad-1440": OPINION_SENT,
    "teemad-3440": OPINION_SENT,
    "teemad-filter": OPINION_SENT,
    "teema-suletud": (".banner--closed .banner__text .muted",),
    # The folded system run's date span and `+ Manus`'s date box were required
    # here until the approved target retired both surfaces (docs/adr/0074 §6,
    # §14). Nothing replaced them: the milestone rows that took the run's place
    # carry a date the `time` selector already paints, and the composer's own
    # date boxes are empty at rest.
    # Unlike `.interrow__detail` above, these three are required. They are not
    # rows of a capped list that a busy world can push off the end: `new_matters`
    # and `reporting` in `app/matters/overview.py` both return a fixed list of
    # `CountRow`s, so the row renders whatever the count is — nought included —
    # for as long as the scope is `Kogu osakond`, which is the scope both these
    # scenarios capture. If one stops matching, the markup moved and the
    # selector has to follow it.
    "osakond": OSAKOND_WEEK_COUNTS,
    "osakond-3440": OSAKOND_WEEK_COUNTS,
    "teema-dokumendid": (*OPINION_ROW_SENT, *EVIDENCE_DATE),
    "teema-arvamused": (*OPINION_ROW_SENT, *EVIDENCE_DATE),
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


def normalise_clock_text(page, name: str) -> None:
    """Replace every clock-derived value this scenario cannot let move.

    Its own function rather than four lines inside `capture` because the
    contract it implements is tested directly: `test_ui_regression`'s own
    section at the bottom of this file drives it over the month names and the
    counts that actually occur, and asserts they all come out the same width.
    Testing it through a screenshot would only ever test the month CI happens
    to run in, which is the defect this exists to fix.

    `name` decides two things, not one. It has always said which normalisations
    this capture may not silently stop making; since `SCENARIO_NORMALISED_TEXT`
    it also says which ones apply at all, so a rewrite declared for one scenario
    leaves the identical markup untouched on every other.
    """
    required = REQUIRED_NORMALISATIONS.get(name, ())
    for selector, canonical in normalisations_for(name):
        elements = page.locator(visible(selector))
        count = elements.count()
        assert count or selector not in required, (
            f"{name}: the clock normalisation {selector!r} matches nothing on this "
            f"page. Either the markup moved and the selector needs following, or "
            f"this scenario no longer renders that value and the entry should go. "
            f"Left unmatched it rewrites nothing and stays green, and the real "
            f"value goes back into the baseline — where it holds until the month "
            f"turns and somebody else's unrelated change goes red for it."
        )
        # Only a value that is actually there may be replaced. Masking already
        # hides whatever this element says, and normalising on top of that would
        # hide one thing more: an element that had stopped rendering its value
        # at all. Empty, the box would shrink and the baseline would go red —
        # which is the signal. Writing the canonical string into it would paint
        # over that signal with a date that never was.
        for index in range(count):
            assert elements.nth(index).inner_text().strip(), (
                f"{name}: {selector!r} matched an element with no text, so there "
                f"is no clock-derived value here to hold still. Normalising it "
                f"would write {canonical!r} into a baseline as though the page "
                f"had rendered it."
            )
        page.eval_on_selector_all(
            selector,
            """(elements, text) => {
                for (const element of elements) {
                    element.textContent = text
                    // What `assert_no_clock_value_sizes_a_column` reads. It has
                    // to ask about the element rather than about the selector:
                    // `time` and `.doctable .table__date time` are two entries
                    // in `CLOCK_DEPENDENT` and one element on the page, and a
                    // value held still through either of them is held still.
                    element.setAttribute("data-e2e-held-still", "")
                }
            }""",
            canonical,
        )


# Only the *auto* case is a hazard below. `table-layout: fixed` sizes the columns
# from the first row's declared widths and ignores every other cell in the table,
# which is why the register — `.table--register`, fixed, seven columns with pixel
# widths — has never drifted on a date, and the evidence table drifts on two
# mornings a month.
def assert_no_clock_value_sizes_a_column(page, name: str, *, root: str | None = None) -> None:
    """Refuse to photograph a clock value that decides how wide a column is.

    A mask paints over an element. It does not take the element out of the
    layout, and inside an auto-layout table the layout is not a line but a
    grid: the browser sizes every column from the widest content in it, so a
    cell that gains one character widens its own column and every other column
    gives up the width to pay for it — on every row, in both directions, and
    nowhere near the mask.

    `teema-dokumendid` is the case that produced this. Its `Kuupäev` cell holds
    `Document.created_at` in `j.n.Y`, which drops leading zeros, so the string
    went from eight characters to nine overnight and 2,736 pixels differed
    against a limit of 2,592 — none of them the covered digits, all of them
    `ROLL`, `KUUPÄEV` and `LISAS` standing 3 to 4 pixels to the left of where
    the baseline had them.

    So this is the rule rather than a note somebody has to remember: a clock
    value in an auto-layout table is normalised, not merely masked. It cannot
    be satisfied by widening a mask, and it fails on the run that introduces the
    hazard rather than on the morning the calendar finds it — which is the
    difference between the author of a change seeing it and somebody else's
    unrelated pull request going red for it.

    Scoped to what is actually photographed. A clipped capture holds one
    component, and a table elsewhere on the page is not in the image.
    """
    scope = page.locator(root) if root else page
    offenders: list[str] = []
    for selector in CLOCK_DEPENDENT:
        for text in scope.locator(visible(selector)).evaluate_all(
            """(nodes) => nodes
                .filter((node) => {
                    const table = node.closest("table")
                    if (!table) return false
                    if (getComputedStyle(table).tableLayout === "fixed") return false
                    return !node.hasAttribute("data-e2e-held-still")
                })
                .map((node) => node.textContent.trim().slice(0, 40))"""
        ):
            offenders.append(f"{selector} -> {text!r}")
    assert not offenders, (
        f"{name}: {len(offenders)} clock value(s) size a column of an auto-layout "
        f"table and are only masked: {sorted(set(offenders))}. The mask covers the "
        f"digits; the text still sizes the column, and every other column in that "
        f"table moves when it changes length. Hold it still instead — an entry in "
        f"`NORMALISED_TEXT` with a canonical of the shape the committed baseline "
        f"holds — rather than covering more of the page."
    )


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
    normalise_clock_text(page, name)
    assert_no_clock_value_sizes_a_column(page, name, root=clip_to)
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
        pytest.fail(
            f"{name}: no committed baseline, so this scenario is not covered. "
            f"The candidate is in this run's `test-report-visual` upload as "
            f"`screenshots/visual-{name}.png` — look at it, and commit it to "
            f"`e2e/baselines/{name}.png`. Do not reach for "
            f"E2E_UPDATE_BASELINES=1: it rewrites every other baseline too."
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


def signed_in_matter(page, base_url: str, title: str, tab: str = ""):
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
    signed_in_matter(page, base_url, OPEN_TITLE)
    compare("teema-ulevaade", capture(page, "teema-ulevaade"))


def test_matter_header_only(page, base_url):
    """The band on its own: identity, state, facts and tabs, and how tall."""
    signed_in_matter(page, base_url, OPEN_TITLE)
    compare("teema-pais", capture(page, "teema-pais", clip_to=".matterhead"))


def test_matter_in_a_special_state(page, base_url):
    """Archive: an imported record whose known uncertainty is kept."""
    signed_in_matter(page, base_url, ARCHIVE_TITLE)
    compare("teema-arhiiv", capture(page, "teema-arhiiv"))


def test_matter_opinions(page, base_url):
    """A Matter's opinions, which are its files filtered to `Arvamus`.

    The separate per-Matter Arvamused page is retired: an opinion is a document,
    the rail links straight to the letter, and the management that is not a file
    row lives in the `Arvamused` block under the table (docs/adr/0061).

    Reached through the retired address on purpose. It is the one scenario in
    this suite that is *also* a route assertion — the baseline is worthless if
    the redirect stops working — and what it captures is the state a saved
    bookmark opens onto.
    """
    signed_in_matter(page, base_url, OPEN_TITLE)
    page.goto(f"{page.url.rstrip('/')}/seisukoht/")
    page.wait_for_load_state("networkidle")
    compare("teema-arvamused", capture(page, "teema-arvamused"))


def test_matter_add_to_matter_zone(page, base_url):
    """`LISA TEEMALE` with one operation open.

    The one state a screenshot is genuinely better at than an assertion: a chip
    row of seven choices with exactly one of them expanded into a form beneath
    it. An open panel claims the full row while the chips keep their line, and
    «the layout does not break» is exactly the claim a baseline can hold and an
    assertion cannot (docs/adr/0075 §2).

    **One open, not seven.** Opening one closes the others, which is the
    behaviour this zone is defined by — a capture of seven expanded panels would
    photograph a state the page does not have.
    """
    signed_in_matter(page, base_url, OPEN_TITLE)
    open_add_panel(page, "marge-tahtaeg")
    _at_rest(page)
    compare("teema-lisa", capture(page, "teema-lisa", clip_to=".addzone"))


def test_matter_current_action_zone(page, base_url):
    """`PRAEGUNE TEGEVUS` — the task, its date, `Muuda`, and the one box that
    finishes it. A baseline because the claim is a *proportion*: the task has to
    read as the prominent thing and the answer box as its answer
    (docs/adr/0075 §3)."""
    signed_in_matter(page, base_url, OPEN_TITLE)
    page.locator("#praegune-tegevus").wait_for(state="visible")
    _at_rest(page)
    compare("teema-praegune", capture(page, "teema-praegune", clip_to="#praegune-tegevus"))


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
    signed_in_matter(page, base_url, OPEN_TITLE)
    page.set_viewport_size({"width": 1024, "height": 900})
    page.wait_for_load_state("networkidle")
    compare("teema-1024", capture(page, "teema-1024"))


def _at_rest(page):
    """Take the pointer off whatever was just clicked, and settle.

    A click leaves the mouse where it landed, and a row that paints an action
    under the cursor comes back hovered in one capture and at rest in another,
    for no reason a reader of the baseline could see.
    """
    page.mouse.move(0, 0)
    page.wait_for_timeout(120)


# The three `kaasamine-*` captures are retired with the section they clipped.
#
# `kaasamine-tyhi`, `kaasamine-kirjed` and `kaasamine-lisa` clipped `#kaasamine`
# — a standing section with its own empty state, its own row list and its own
# add form. The approved target has none of it: recording a consultation is the
# `+ Kaasamine` panel `teema-lisa` captures, and reading one is a chronology row
# inside `teema-ulevaade`. A clipped baseline of an element the page does not
# render is a baseline that can only ever skip
# (TEEMA_TARGET_SPEC §F, docs/adr/0074 §9).
#
# `teema-koostaja` went the same way in the round after it, and for the same
# reason. It clipped `.composer` — one open form over five panels and one shared
# `Salvesta` — and that surface is superseded by two zones with one save each.
# `teema-praegune` and `teema-lisa` above are what replaced it, and neither is a
# rename: they photograph different elements making different claims
# (docs/adr/0075 §2, §3).
#
# What replaced all four is covered rather than dropped: the behaviour those
# tests drove is in `e2e/test_engagement.py` and `e2e/test_teema_workspace.py`,
# which follow the capability to its new surface.


def test_the_process_strip_is_the_first_thing_in_the_ajajoon(page, base_url):
    """`Menetluse tähtajad` — the dated points, before anything is scrolled.

    A capture rather than an assertion because what is being locked is a
    *proportion*: the columns share the width evenly however many there are, and
    the accent connector runs from each dot to the next with `:last-child`
    drawing none, so the rail ends at the rightmost dot (TEEMA_TARGET_SPEC §D).

    **One dot state, not three.** `is-current` and `is-todo` went with the
    sources that produced them, and a known future milestone — this Matter's two
    commencements — is drawn exactly like a completed one. What this baseline
    now has to catch is a future column quietly acquiring a muted state, a ring
    or an `N p` suffix (docs/adr/0074 §12.2, §12.4).
    """
    signed_in_matter(page, base_url, OPEN_TITLE)
    # Asserted rather than skipped past. The strip is drawn from the milestones
    # this Matter really has, and `seed_e2e_data` gives this one several — so an
    # absent strip is the projection or the seed having moved, which is the
    # thing this scenario exists to notice. A skip here would have hidden it
    # behind a green lane.
    assert page.locator(".tl-strip").count() == 1, (
        "the seeded open Matter renders no `.tl-strip`. Either "
        "`app/matters/process_timeline.py` stopped projecting its milestones or "
        "the seed stopped creating them; both are regressions in what this "
        "scenario photographs (TEEMA_TARGET_SPEC §D)."
    )
    _at_rest(page)
    compare("teema-kaik", capture(page, "teema-kaik", clip_to=".tl-strip"))


def test_the_chronology_shows_its_two_row_kinds(page, base_url):
    """A 12px accent dot for what happened to the file, a 6px muted one for work
    somebody did on it — and no third (TEEMA_TARGET_SPEC §E)."""
    signed_in_matter(page, base_url, OPEN_TITLE)
    _at_rest(page)
    compare("teema-ajajoon", capture(page, "teema-ajajoon", clip_to="#ajalugu-loend"))


def test_matter_documents(page, base_url):
    signed_in_matter(page, base_url, OPEN_TITLE, tab="Dokumendid")
    compare("teema-dokumendid", capture(page, "teema-dokumendid"))


def test_create_matter_form(page, base_url):
    signed_in(page, base_url, "/teemad/uus/")
    compare("uus-teema", capture(page, "uus-teema"))


def test_matter_edit_form(page, base_url):
    """`Muuda teemat`, which follows `Uus teema` as of docs/adr/0096 §1.

    The scenario this surface never had, added in the round that made the two
    pages one design: the whole point of §1 is that a reader can compare them,
    and a baseline for one of the two is half a comparison.

    `OPEN_TITLE`'s own edit page, so the capture holds real pre-filled values
    rather than the empty form `uus-teema` already covers.
    """
    signed_in_matter(page, base_url, OPEN_TITLE)
    page.goto(f"{page.url}muuda/")
    page.wait_for_load_state("networkidle")
    _at_rest(page)
    compare("teema-muuda", capture(page, "teema-muuda"))


def test_matter_delete_confirmation(page, base_url):
    """`Kustuta teema`, the one irreversible page in the product.

    Captured because «unmistakable» is a claim about how it looks, and because
    a red box that quietly stopped being red would be the one regression here
    that costs a record. A GET, so nothing is deleted by taking the picture.
    """
    signed_in_matter(page, base_url, OPEN_TITLE)
    page.goto(f"{page.url}kustuta/")
    page.wait_for_load_state("networkidle")
    _at_rest(page)
    compare("teema-kustuta", capture(page, "teema-kustuta"))


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


def test_release_notes(page, base_url):
    """Uuendused: the day accordions, the newest open and the rest shut.

    One scenario, and it is a composition rather than a page of data: the day
    heading, the count beside it, the caret, the open day's bullets and the shut
    rows under it. That composition is the whole product decision — one
    disclosure per day — and it is exactly the kind of thing no DOM assertion
    notices going wrong.

    Nothing on it is clock-derived, which is unusual enough to be worth saying.
    The dates come from a file in the image, so they hold still by construction
    and are deliberately **not** `<time>` elements: the mask list paints over
    every `<time>` on a captured page, and painting over these would leave a
    baseline showing a list of empty rectangles
    (`templates/core/release_notes.html`). The one value here that does move is
    the build stamp, and `.app__footer` has covered that since this suite began.
    """
    signed_in(page, base_url, "/uuendused/")
    compare("uuendused", capture(page, "uuendused"))


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


#: The closed-disclosure shapes on Osakond, and the element whose presence
#: proves each one is actually holding something back on this run.
#:
#: Both keep their content *inside* the shut `<details>`, which is the shape
#: that yields a box for every hidden child — unlike `tbody.uxextra`, which is
#: `display: none` and was always harmless. Eesolev joined the list when each
#: deadline window became a disclosure of its own: every row it hides carries a
#: `.uxdl__date`, and that selector is in CLOCK_DEPENDENT, so it is now the
#: largest instance of the case this test exists for rather than a new one.
CLOSED_DISCLOSURES: tuple[tuple[str, str, str], ...] = (
    (
        "details.pw-more:not([open])",
        ".interrow__reason",
        "Osakond no longer hides intervention rows behind «Näita veel N ▾». "
        "Either the section stopped capping its preview, or the seeded world "
        "dropped below the cap",
    ),
    (
        "details.uxdl:not([open])",
        ".uxdl__row",
        "Osakond's Eesolev no longer holds its deadline rows in a shut "
        "disclosure. Either the windows stopped being `<details>`, or they now "
        "arrive open, or the seeded world has no upcoming deadline at all",
    ),
)


def test_a_closed_disclosure_contributes_no_masks(page, base_url):
    """What a shut `<details>` is *holding* must not paint over the page.

    Not a screenshot: the damage this guards against is invisible to
    `compare()` in the only way that matters, because a mask painted in the
    page's own colour looks exactly like a baseline that was taken correctly.
    So it is asserted where it can be read — every mask this suite paints
    resolves to something a reader can see.

    Osakond is the scenario because it is the one that broke, and it now
    carries both shapes: «Näita veel N ▾» over the intervention list, and every
    *Eesolev* window over its own deadlines. The presence assertion keeps each
    honest: if the seeded world ever stops filling one, this test would be
    measuring nothing there, and it says so rather than passing.

    **A shut disclosure's summary is on screen.** Only its body is not, and the
    distinction is the whole assertion — it is what widening this test to the
    deadline windows found. `.uxdl__range` is a clock value that renders *in*
    the summary: it must be masked, and masking it damages nothing, because
    there is a reader looking at the glyphs it covers. The intervention
    disclosure gets away with the cruder question only because «Näita veel N ▾»
    happens to carry no clock value; asking it of a summary that does would have
    demanded the mask be removed from something visible, which is the opposite
    of what this file is for.

    So the matches are partitioned rather than counted: everything inside the
    shut element, minus what sits in its summary, must be nothing.
    """
    signed_in(page, base_url, "/osakond/")
    for disclosure, evidence, complaint in CLOSED_DISCLOSURES:
        assert page.locator(f"{disclosure} {evidence}").count(), (
            f"{complaint}, so this test no longer exercises that case — move the "
            f"assertion to whichever surface still hides rows."
        )
        for selector in CLOCK_DEPENDENT:
            # A strict subset of `inside`, so the subtraction is a count of the
            # matches that are in the body — the part nobody can see.
            inside = page.locator(f"{disclosure} {visible(selector)}").count()
            on_summary = page.locator(f"{disclosure} > summary {visible(selector)}").count()
            assert inside - on_summary == 0, (
                f"{selector!r} matches {inside - on_summary} element(s) in the "
                f"body of {disclosure!r} — which nobody can see — and Playwright "
                f"would paint a rectangle for each, in the page's own colour, "
                f"over whatever happens to be underneath."
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


# ---------------------------------------------------------------------------
# The normalisation contract, tested against the calendar rather than today
# ---------------------------------------------------------------------------
#
# Everything above this line is a screenshot taken on the day CI happened to
# run. That is exactly what could not prove the fix these tests guard: a
# baseline captured in August is correct in August whether or not anything holds
# the month name still, and only goes red on the first of September — by which
# time it is somebody else's pull request that is red.
#
# So the contract is asserted directly. The fixtures below are the real markup
# of the three components, driven over the values the product really produces
# across a month boundary, with no server and no database. They compare each
# variant against the others rather than against a committed image, so nothing
# here depends on which fonts the machine has.


#: The horizon label at four different month lengths, including the shortest
#: («mai», not offered by `HORIZON_CHOICES` today but a real `ESTONIAN_MONTHS`
#: entry) and the longest.
HORIZON_VARIANTS = (
    "Kuni oktoober ▾",
    "Kuni november ▾",
    "Kuni detsember ▾",
    "Kuni jaanuar ▾",
    "Kuni mai ▾",
    "Kõik tähtajad ▾",
)

#: The counts «Tähtaeg sel kuul» actually takes as the month turns. `0` and `1`
#: are the pair measured on the seeded world; the two-digit case is included
#: because a busier month is not a different kind of problem.
MONTH_CHIP_VARIANTS = (
    "Tähtaeg sel kuul · 0",
    "Tähtaeg sel kuul · 1",
    "Tähtaeg sel kuul · 2",
    "Tähtaeg sel kuul · 11",
)

#: `j.n.Y`, which is what makes this one bite: the product does not zero-pad, so
#: the first of a month is materially narrower than the twenty-ninth.
CLOSED_ON_VARIANTS = ("(29.8.2026)", "(1.9.2026)", "(31.12.2026)", "(1.1.2027)")

#: `j.n`, the Ajajoon preview's own format. Same defect one field shorter.
TIMELINE_ON_VARIANTS = ("29.8", "1.9", "31.12", "1.1")

#: `format_estonian_date`, over the days the seeded closed Matter's own dates
#: really take. It is `j.n.Y` with no zero-padding, so the string is eight
#: characters on a single-digit day of a single-digit month, nine on most days
#: and ten at the end of a long month — and the pair either side of each
#: boundary is what a baseline taken on one of them and read on the other has to
#: survive.
CLOSED_MATTER_DAY_VARIANTS = (
    "12.9.2026",
    "9.9.2026",
    "30.9.2026",
    "1.10.2026",
    "31.12.2026",
    "1.1.2027",
)

#: The open Matter's two dates through the same two classes, so the scoping test
#: below is driven by real content rather than by a placeholder. Both are
#: `seed_e2e_data`'s own choices — a commencement in 2028 and a Kaasamine in May
#: — and neither moves when the calendar does.
FIXTURE_DAY_VARIANTS = ("1.1.2028", "12.5.2026")

#: A flex row, so the probe's x position is a direct readout of how wide the
#: element before it is. Without it the two would stack and a width change would
#: be invisible — which is how a screenshot suite misses this class in the first
#: place.
_ROW = "display:flex;align-items:center;gap:8px;width:max-content;font:16px sans-serif"


def _fixture(page, body: str) -> None:
    page.set_content(f'<div id="row" style="{_ROW}">{body}<span id="probe">·</span></div>')


def _geometry(page, selector: str) -> tuple[str, float, float]:
    """What the capture has to hold still: the text, its width, and what follows.

    The text is the part that actually decides it — two elements rendering the
    same string render the same pixels under any font, which is why this can be
    asserted on a machine whose fonts are not CI's. The two measurements are
    here because they are what a reader needs in the failure message: «142 and
    133.12, and the next element starts 8.88px further along» is the defect,
    where «two different strings» is only its cause.

    They also cover what a string comparison cannot: an element rewritten to the
    right text by something that nonetheless moved. The reverse gap is real and
    is why the text is compared too — this fixture falls back to a generic
    sans-serif with tabular figures, where `· 0` and `· 1` measure the same, and
    a geometry-only test would pass on the very pair that started this. Barlow's
    figures are proportional and they do not.
    """
    element = page.locator(selector)
    box = element.bounding_box()
    probe = page.locator("#probe").bounding_box()
    assert box and probe
    return element.inner_text().strip(), round(box["width"], 2), round(probe["x"], 2)


def _holds_still(page, build, variants, selector: str, scenario: str = "") -> None:
    seen = set()
    for variant in variants:
        _fixture(page, build(variant))
        normalise_clock_text(page, scenario)
        seen.add(_geometry(page, selector))
    assert len(seen) == 1, (
        f"{selector!r} did not come out the same for every variant: {sorted(seen)}. "
        f"Each entry is (text, own width, where the next element starts) — two "
        f"entries means a baseline taken in one month is red in another."
    )


def test_the_horizon_label_is_the_same_width_in_every_month(page):
    """«Kuni november» is wider than «Kuni oktoober», and must stop being."""
    _holds_still(
        page,
        lambda label: (
            '<section class="workband workband--hiljem" style="display:contents">'
            f'<details class="rangepicker"><summary class="rangepicker__trigger">{label}'
            "</summary></details></section>"
        ),
        HORIZON_VARIANTS,
        HORIZON_LABEL[0],
    )


def test_the_month_view_chip_is_the_same_width_at_every_count(page):
    """`0` and `1` are not the same width, and this chip has three chips after it."""
    _holds_still(
        page,
        lambda text: f'<div class="uxviews"><a class="uxchip">{text}</a></div>',
        MONTH_CHIP_VARIANTS,
        MONTH_VIEW_CHIP[0],
    )


def test_the_closed_banner_date_is_the_same_width_on_any_day(page):
    """The mask is sized to this element. A narrower date is a smaller mask."""
    _holds_still(
        page,
        lambda date: (
            '<div class="banner banner--closed"><p class="banner__text">'
            f'<span class="muted">{date}</span></p></div>'
        ),
        CLOSED_ON_VARIANTS,
        CLOSED_ON[0],
    )


# The `Ajajoon` preview's date had a geometry test of its own: the mask's width
# was the quote's position, so a narrower date dragged a line of real content
# with it. Both the preview and its quote left the head with the approved target
# — `AJAJOON` and `{n} kirjet` is the whole of it now (docs/adr/0074 §16) — so
# there is no element left to hold still. The harness it used is unchanged and
# still serves every other clock value on the page.


# ---------------------------------------------------------------------------
# The closed Matter's own two days, and the scoping that keeps them its own
# ---------------------------------------------------------------------------
#
# Unlike everything above, these two are *not* masked. They are ordinary dark
# text on the surface, in the baseline as glyphs, and the whole of what they
# have to do is read the same on every morning — so the assertion is the text
# and the geometry together, which is what `_holds_still` already measures.


def _closed_matter_fixture(page, body: str) -> None:
    """`teema-suletud`'s three normalised elements, one of them varying.

    All three, because this drives `normalise_clock_text` under the real
    scenario name and `REQUIRED_NORMALISATIONS` refuses a capture whose declared
    values are not on the page. That guard is the point of the mechanism and not
    something to route around, so the fixture satisfies it instead: the banner
    is rendered after the probe, where it cannot enter the measurement.
    """
    page.set_content(
        f'<div id="row" style="{_ROW}">{body}<span id="probe">·</span></div>'
        '<div class="banner banner--closed"><p class="banner__text">'
        '<span class="muted">(29.8.2026)</span></p></div>'
    )


def _closed_matter_days(day: str) -> str:
    """The process strip's date and the chronology milestone's, both that day.

    Both, in one fixture, because that is how the page renders them: `created_at`
    and `closed_at` are stamped in the same seeding transaction, so the strip and
    the chronology print the same string and a fixture that varied only one of
    them would be measuring a state the product never has.
    """
    return f'<span class="tl-step__date">{day}</span><span class="uxtl__msdate">{day}</span>'


@pytest.mark.parametrize("selector", CLOSED_MATTER_DAYS)
def test_the_closed_matter_days_are_the_same_width_on_any_day(page, selector):
    """The seeded closed Matter is created and closed by the run that renders it.

    So `Alustatud`, `Lõpetatud`, `Teema suletud` and `Teema loodud` all print the
    morning CI ran, in `j.n.Y` — eight characters on `9.9.2026`, nine on
    `12.9.2026`, ten on `1.10.2026`. Nothing covers them and nothing sized them,
    which is why the committed baseline holds the digits of the day it was taken
    and every later morning differs from it.
    """
    seen = set()
    for day in CLOSED_MATTER_DAY_VARIANTS:
        _closed_matter_fixture(page, _closed_matter_days(day))
        normalise_clock_text(page, "teema-suletud")
        seen.add(_geometry(page, selector))
    assert len(seen) == 1, (
        f"{selector!r} did not come out the same for every day: {sorted(seen)}. "
        f"Each entry is (text, own width, where the next element starts) — two "
        f"entries means the baseline holds one morning's digits and is red on "
        f"another's."
    )


@pytest.mark.parametrize("selector", CLOSED_MATTER_DAYS)
def test_a_closed_matter_day_really_does_move_without_the_normalisation(page, selector):
    """The hazard itself, before anything is asked to hold it still.

    Without this the test above would pass on a fixture that could not move at
    all — a class that stopped rendering a date, a selector that matches
    nothing — and would go on passing after somebody removed the entry it is
    guarding.
    """
    seen = set()
    for day in CLOSED_MATTER_DAY_VARIANTS:
        _closed_matter_fixture(page, _closed_matter_days(day))
        seen.add(_geometry(page, selector))
    assert len(seen) == len(CLOSED_MATTER_DAY_VARIANTS), (
        f"{selector!r} rendered {len(seen)} distinct geometries for "
        f"{len(CLOSED_MATTER_DAY_VARIANTS)} different days: {sorted(seen)}. Either "
        f"the fixture stopped modelling the element or the date stopped changing "
        f"— and if the drift is really gone, the test above proves nothing."
    )


# ---------------------------------------------------------------------------
# The open and archive Matters, where one class holds both meanings at once
# ---------------------------------------------------------------------------
#
# The closed Matter above is the easy half: every slot in both classes is a
# run-day value, so the bare class is the right scope. The open Matter is the
# hard half and the reason `STRIP_RUN_DAY` and `CHRONOLOGY_RUN_DAY` name
# milestones rather than classes — its strip prints `Alustatud` and
# `Koja arvamus` from records this run stamped, and two commencements the
# fixture chose, through one class; its chronology does the same with
# `Teema loodud` / `Arvamus välja` against a Kaasamine in May and a `Töövõit`.
#
# Three properties are asserted here, and all three are needed. That the
# run-day slots come out identical on any calendar day; that they really do
# move without the normalisation, so the first test cannot pass vacuously; and
# that the seeded slots beside them are untouched — which is the one a future
# fixture change could quietly break, by giving a seeded date a label the
# scoping happens to match.


def _open_matter_fixture(page, day: str) -> None:
    """The open Matter's six date slots, in the two shapes the page renders.

    Every slot, not only the varying ones, because the scoping is the thing
    under test: a fixture holding just the two run-day dates would pass under a
    global `.uxtl__msdate` entry, which is precisely the mistake this exists to
    refuse. `display:contents` on the two wrappers puts the leaf spans directly
    in the flex row, so `#probe`'s x is a readout of the whole line — any slot
    that moves, moves it.

    The seeded values are `seed_e2e_data`'s own: `add_effective_date` in 2028
    and `add_engagement(occurred_on=date(2026, 5, 12))`.
    """
    page.set_content(
        f'<div id="row" style="{_ROW}">'
        '<span class="tl-step" style="display:contents">'
        f'<span class="tl-step__what">Alustatud</span>'
        f'<span class="tl-step__date">{day}</span></span>'
        '<span class="tl-step" style="display:contents">'
        f'<span class="tl-step__what">Koja arvamus</span>'
        f'<span class="tl-step__date">{day}</span></span>'
        '<span class="tl-step" style="display:contents">'
        '<span class="tl-step__what">Jõustumine</span>'
        '<span class="tl-step__date">1.1.2028</span></span>'
        '<p class="uxtl__ms" style="display:contents">'
        '<span class="uxtl__mswhat">Teema loodud</span>'
        f'<span class="uxtl__msdate">{day}</span></p>'
        '<p class="uxtl__ms" style="display:contents">'
        '<span class="uxtl__mswhat">Arvamus välja</span>'
        f'<span class="uxtl__msdate">{day}</span></p>'
        '<p class="uxtl__ms" style="display:contents">'
        '<span class="uxtl__mswhat">Kaasamine: Liikmete kaasamiskutse</span>'
        '<span class="uxtl__msdate">12.5.2026</span></p>'
        '<span id="probe">·</span></div>'
    )


#: The seeded dates on that fixture, each with the selector that reaches it and
#: what it has to still say afterwards. Reached through the same
#: label-scoped shape the normalisation uses, so a scoping mistake shows up as
#: this test failing rather than as a selector that silently matches nothing.
SEEDED_MATTER_DATES = (
    ('.tl-step:has(.tl-step__what:text-is("Jõustumine")) .tl-step__date', "1.1.2028"),
    ('.uxtl__ms:has(.uxtl__mswhat:has-text("Kaasamine")) .uxtl__msdate', "12.5.2026"),
)

#: The captures that render the open Matter's own strip and chronology, and are
#: therefore the ones whose normalisation has to be narrow.
OPEN_MATTER_SCENARIOS = ("teema-ulevaade", "teema-1024")


@pytest.mark.parametrize("selector", _STRIP_AND_CHRONOLOGY_RUN_DAYS)
def test_the_open_matter_run_days_are_the_same_width_on_any_day(page, selector):
    """`created_at` and `sent_at`, both stamped by the run that renders them.

    `seed_e2e_data` creates `OPEN_TITLE` and sends its one opinion in the same
    transaction, so `Alustatud`, `Koja arvamus`, `Teema loodud` and
    `Arvamus välja` all print the morning CI ran — eight characters on
    `9.9.2026`, nine on `12.9.2026`, ten on `1.10.2026`. Nothing covers them and
    nothing sized them, which is why a baseline adopted on one morning differs
    from every later one.
    """
    seen = set()
    for day in CLOSED_MATTER_DAY_VARIANTS:
        _open_matter_fixture(page, day)
        normalise_clock_text(page, "teema-ulevaade")
        seen.add(_geometry(page, selector))
    assert len(seen) == 1, (
        f"{selector!r} did not come out the same for every day: {sorted(seen)}. "
        f"Each entry is (text, own width, where the next element starts) — two "
        f"entries means the baseline holds one morning's digits and is red on "
        f"another's."
    )


@pytest.mark.parametrize("selector", _STRIP_AND_CHRONOLOGY_RUN_DAYS)
def test_an_open_matter_run_day_really_does_move_without_the_normalisation(page, selector):
    """The hazard itself, before anything is asked to hold it still.

    Without this the test above would pass on a fixture that could not move at
    all — a class that stopped rendering a date, a selector that matches nothing
    — and would go on passing after somebody removed the entry it is guarding.
    """
    seen = set()
    for day in CLOSED_MATTER_DAY_VARIANTS:
        _open_matter_fixture(page, day)
        seen.add(_geometry(page, selector))
    assert len(seen) == len(CLOSED_MATTER_DAY_VARIANTS), (
        f"{selector!r} rendered {len(seen)} distinct geometries for "
        f"{len(CLOSED_MATTER_DAY_VARIANTS)} different days: {sorted(seen)}. Either "
        f"the fixture stopped modelling the element or the date stopped changing "
        f"— and if the drift is really gone, the test above proves nothing."
    )


@pytest.mark.parametrize(("selector", "seeded"), SEEDED_MATTER_DATES)
@pytest.mark.parametrize("scenario", OPEN_MATTER_SCENARIOS)
def test_the_seeded_matter_dates_survive_every_matter_normalisation(
    page, scenario, selector, seeded
):
    """The narrowness, asserted rather than trusted — and in both directions.

    «Jõustumine 1.1.2028» and «Kaasamine … 12.5.2026» reach the page through the
    very classes the run-day slots use, and they are what `teema-ulevaade`,
    `teema-1024`, `teema-ajajoon` and `teema-kaik` exist to compare: a
    commencement drawn exactly like a completed milestone, and a consultation on
    the day it was held. Freezing either into a canonical would take real
    content out of four baselines and leave the suite green while it happened.

    Two ways that could start happening, and this refuses both. `teema-suletud`
    normalises the bare `.tl-step__date` and `.uxtl__msdate`; if that entry ever
    stopped being scoped to its own scenario, it would reach this fixture and
    rewrite these two. And a widened `STRIP_RUN_DAY` / `CHRONOLOGY_RUN_DAY` — a
    label dropped, `:text-is` loosened to `:has-text` — would match a seeded row
    directly. Either way the rewrite is asked for here and has to decline.
    """
    for day in CLOSED_MATTER_DAY_VARIANTS:
        _open_matter_fixture(page, day)
        normalise_clock_text(page, scenario)
        assert page.locator(selector).inner_text().strip() == seeded, (
            f"{selector!r} was rewritten on {scenario!r}, where it holds a date "
            f"the fixture chose. Normalising it there hides the one thing that "
            f"baseline is comparing."
        )


@pytest.mark.parametrize("selector", CLOSED_MATTER_DAYS)
def test_the_closed_matter_days_are_left_alone_on_every_other_scenario(page, selector):
    """The closed Matter's *unscoped* entries stay its own.

    `teema-suletud` is the one capture whose every date slot is a run-day value,
    so its entries name the bare classes. That is only safe while the scoping by
    scenario holds: the same two classes are on every other Matter capture, and
    three of them carry dates the fixture chose.

    Asked for under `teema-pais`, which renders the Matter header and declares no
    normalisation of its own — so nothing here can be satisfied by a scenario's
    own entry, and a leak from `teema-suletud` is the only thing that could
    rewrite these.
    """
    for day in FIXTURE_DAY_VARIANTS:
        _closed_matter_fixture(page, _closed_matter_days(day))
        normalise_clock_text(page, "teema-pais")
        assert page.locator(selector).inner_text().strip() == day, (
            f"{selector!r} was rewritten under a scenario that declares nothing, "
            f"so `teema-suletud`'s entry is reaching captures it does not name."
        )


def test_a_scenario_normalisation_that_stops_matching_fails_the_capture(page):
    """`REQUIRED_NORMALISATIONS`, for the scenario-scoped half of the contract.

    A rename of `.tl-step__date` would rewrite nothing, raise nothing and leave
    `teema-suletud` green — with the run's own date back in the capture and the
    baseline red on the next morning that spells it differently. This is the
    assertion that makes that a failure on the day the markup moves, rather than
    on somebody else's unrelated pull request a fortnight later.
    """
    _closed_matter_fixture(page, '<span class="uxtl__msdate">12.9.2026</span>')
    with pytest.raises(AssertionError, match="matches nothing on this page"):
        normalise_clock_text(page, "teema-suletud")


def test_a_scenario_normalisation_is_declared_by_the_scenario_it_names():
    """The two tables are one contract, and neither may carry the other's entry.

    Two ways this goes quiet. A selector in `SCENARIO_NORMALISED_TEXT` that its
    own scenario does not require is a rewrite nothing guards, so a rename makes
    it stop applying and says nothing. A selector in *both* tables is applied to
    every capture whatever the scenario map says, which is the global freeze the
    scoping exists to avoid — and it would read, from the scoped entry alone, as
    though it were narrow.
    """
    for scenario, entries in SCENARIO_NORMALISED_TEXT.items():
        scoped = {selector for selector, _ in entries}
        assert scoped <= set(REQUIRED_NORMALISATIONS.get(scenario, ())), scenario
        assert not scoped & {selector for selector, _ in NORMALISED_TEXT}, scenario


def test_a_normalisation_that_stops_matching_fails_the_capture(page):
    """The whole point of `REQUIRED_NORMALISATIONS`.

    A Playwright selector that matches nothing rewrites nothing and raises
    nothing, so a markup rename would put the real clock value back into the
    baseline and leave the run green until the month turned. This is the
    assertion that makes that a failure on the day the markup moves.
    """
    _fixture(page, "<span>nothing this scenario needs</span>")
    with pytest.raises(AssertionError, match="matches nothing on this page"):
        normalise_clock_text(page, "minu-too")


def test_every_scenario_that_renders_a_clock_value_declares_it(page):
    """The two dictionaries are the contract; this is that they stay one.

    `REQUIRED_MASKS` and `REQUIRED_NORMALISATIONS` name scenarios independently,
    and a scenario that gains a normalisation but not its declaration is exactly
    the silent case above. Asserted rather than trusted: both selectors are
    reachable from the same names, so a typo in either is a failing test rather
    than a mask that never paints.
    """
    for scenario, selectors in REQUIRED_NORMALISATIONS.items():
        assert set(selectors) <= {selector for selector, _ in normalisations_for(scenario)}, (
            scenario
        )
    for scenario, selectors in REQUIRED_MASKS.items():
        assert set(selectors) <= set(CLOCK_DEPENDENT), scenario


#: `TimelineRow.span`, in the two shapes `short_range` can produce. Every one is
#: zero-padded — `short_day_month` formats `{day:02d}.{month:02d}` — which is
#: the whole reason this element may be masked rather than normalised.
#:
#: Split in two because a single day and a range are five characters and eleven,
#: and no mask makes those the same width. They do not need to be: what a run
#: covers is decided by the seed's own event ordering, not by which morning the
#: capture runs, so a scenario stays in one shape and only the digits inside it
#: advance. Each tuple is therefore one day's worth of drift, tested for the
#: property that matters — the box does not move.
TIMELINE_SPAN_VARIANTS = ("31.08", "01.09", "31.12", "01.01")
TIMELINE_SPAN_RANGE_VARIANTS = ("27.08–31.08", "28.08–01.09", "29.12–31.12", "30.12–01.01")


def _box_holds_still(page, build, variants, selector: str) -> None:
    """Like `_holds_still`, for an element whose *text* is allowed to change.

    The distinction is the one this whole module is built on. A normalised
    element has to come out reading the same, because the baseline holds its
    glyphs. A masked element does not: the baseline holds a rectangle of
    `#101418` where it was, so the only thing that has to hold still is the
    rectangle — its width, and where the next element starts.

    Asserting the text as well would be asserting the opposite of what the mask
    is for, and would make this test fail on precisely the input it exists to
    accept.
    """
    seen = set()
    for variant in variants:
        _fixture(page, build(variant))
        seen.add(_geometry(page, selector)[1:])
    assert len(seen) == 1, (
        f"{selector!r} did not keep its box for every variant: {sorted(seen)}. "
        f"Each entry is (own width, where the next element starts) — two entries "
        f"means the mask is a different size on a different day, and the pixels "
        f"it stops covering go into somebody else's diff."
    )


# The four folded-system-run geometry tests are retired with the row they
# measured.
#
# They proved that `.uxtl__sysrow`'s date span kept its box on any day, across a
# multi-day run, and that `:nth-child(2)` took the date and nothing else — all
# of it in service of a mask over «Teema loodud 25.08, tegevusi 3 … 31.08». The
# approved target has no folded run: those events are milestones in their own
# right or ordinary work, each on its own line, and each carries a plain `<time>`
# the bare `time` selector already paints (docs/adr/0074 §14).
#
# Nothing about the *method* went with them. `assert_no_clock_value_sizes_a_column`
# below and the box-stability harness above are untouched, and the milestone
# dates that replaced this row are covered by the same `CLOCK_DEPENDENT` rule
# every other date on the page is.


# ---------------------------------------------------------------------------
# A clock value that sizes a table column
# ---------------------------------------------------------------------------
#
# Everything above holds a *line* still: a masked value with content beside it,
# where the mask's own width is that content's position. A table is the same
# defect one order of magnitude larger, and it is the one this section exists
# for.
#
# `.doctable` has no `table-layout`, so it is laid out `auto` and every column
# is sized from the widest thing in it. The `Kuupäev` cell holds
# `document.created_at` in `j.n.Y`, which the seed writes at `auto_now_add` —
# the wall clock of the run. `j.n.Y` drops leading zeros, so the string gains a
# character on the tenth of the month and loses it again on the first:
#
#     9.9.2026   ->  10.9.2026
#     31.8.2026  ->   1.9.2026
#
# `.table__date` sets `font-variant-numeric: tabular-nums`, so *which* digits
# are on the page never matters and the character count is the whole of it. One
# figure's advance then resizes the column, and every other column in the table
# redistributes to pay for it — in both directions, across every row.
#
# The mask is no help at all here. It paints over the glyphs; the text is still
# in the layout, and what moves is not the masked box but the three columns
# beside it. Measured on unmodified main between two CI runs of the same commit:
# the `Kuupäev` mask went from 50px at x=904 to 57px at x=894, `ROLL`,
# `KUUPÄEV` and `LISAS` all moved with it, `FAIL` did not, and 0.2111% of
# `teema-dokumendid` differed against a 0.20% limit. The rendering on the
# previous day differed from the committed baseline by nought pixels.

#: The dates either side of the two boundaries where `j.n.Y` changes length,
#: plus the year boundary, where it does both at once.
EVIDENCE_DATE_VARIANTS = (
    "9.9.2026",
    "10.9.2026",
    "31.8.2026",
    "1.9.2026",
    "31.12.2026",
    "1.1.2027",
)


def _document_table(stamp: str, *, layout: str = "auto") -> str:
    """The evidence table, as `matter_documents.html` writes it.

    Four columns and one row, with the classes the product uses and no
    stylesheet: the redistribution is the browser's own table algorithm, not
    anything `app.css` does, so a fixture that loaded it would be testing the
    same thing less legibly on a machine whose fonts are not CI's.

    `tabular-nums` is set inline for the same reason it is set in the product:
    it is what makes this a character-count problem rather than a which-digits
    problem, and a fixture without it would fail for the wrong reason.

    The width is fixed, because that is the shape the defect takes on the real
    page — the table fills its container, so a column that grows is paid for by
    the columns beside it rather than by the page getting wider.
    """
    return (
        f'<table class="table doctable" style="width:620px;table-layout:{layout};'
        'border-collapse:collapse;font:16px sans-serif">'
        "<thead><tr>"
        '<th id="head-fail">Fail</th>'
        '<th id="head-roll">Roll</th>'
        '<th id="head-kuupaev" class="table__date">Kuupäev</th>'
        '<th id="head-lisas">Lisas</th>'
        "</tr></thead>"
        "<tbody><tr>"
        '<td class="table__title">arvamus-2026.asice</td>'
        "<td>Arvamus</td>"
        '<td class="table__date" style="white-space:nowrap;'
        f'font-variant-numeric:tabular-nums"><time>{stamp}</time></td>'
        '<td><span id="probe">Martin</span></td>'
        "</tr></tbody></table>"
    )


def _column_geometry(page) -> tuple:
    """Where every column starts, and how wide the date in it is.

    The column origins are the assertion. The date's own width is here because
    it is what a reader needs in the failure message — «the cell got 7px wider»
    explains «three columns moved», where three moved columns on their own only
    say that something did.
    """
    heads = tuple(
        round(page.locator(f"#{name}").bounding_box()["x"], 2)
        for name in ("head-fail", "head-roll", "head-kuupaev", "head-lisas")
    )
    date = page.locator(EVIDENCE_DATE[0])
    return round(date.bounding_box()["width"], 2), heads


def test_a_date_that_gains_a_digit_really_does_move_the_document_table(page):
    """The hazard itself, before anything is asked to hold it still.

    Without this the test below would pass on a fixture that could not move at
    all — a table with fixed columns, a date that never changes length, a
    selector that matches nothing — and would go on passing after somebody
    removed the thing it is guarding.
    """
    seen = set()
    for stamp in ("9.9.2026", "10.9.2026"):
        page.set_content(_document_table(stamp))
        seen.add(_column_geometry(page))
    assert len(seen) == 2, (
        f"the same table at 9.9.2026 and 10.9.2026 laid out identically: {seen}. "
        f"Either the fixture stopped modelling `.doctable` or the browser stopped "
        f"sizing auto-layout columns from their content — and if the hazard is "
        f"really gone, the test below is no longer proving anything."
    )


def test_the_document_table_date_cannot_move_its_columns(page):
    """The same table on any morning is the same layout.

    A mask over the `Kuupäev` cell hides the digits and leaves the column
    sizing exactly where it was, so this has to be held still rather than
    covered: the text is replaced with one of the same shape before the
    capture, and the three columns beside it stop moving.

    Driven over both boundaries `j.n.Y` has — the tenth of a month and the
    first — because a value that only moves twice a month is a baseline that is
    correct for a fortnight and then makes somebody else's pull request red.
    """
    seen = set()
    for stamp in EVIDENCE_DATE_VARIANTS:
        page.set_content(_document_table(stamp))
        normalise_clock_text(page, "")
        seen.add((page.locator(EVIDENCE_DATE[0]).inner_text().strip(), *_column_geometry(page)))
    assert len(seen) == 1, (
        f"{EVIDENCE_DATE[0]!r} did not come out the same for every date: "
        f"{sorted(seen)}. Each entry is (text, the date's own width, where the "
        f"four columns start) — two entries means the table is a different "
        f"layout on the tenth of the month than on the ninth, and every "
        f"baseline that renders it goes red for a change nobody made."
    )


def test_a_clock_value_that_sizes_a_column_fails_the_capture(page):
    """The rule, rather than a note somebody has to remember on the next table.

    `REQUIRED_MASKS` and `REQUIRED_NORMALISATIONS` both catch a selector that
    stopped matching. Neither catches a value that was never declared — and
    that is the shape this defect had: the `Kuupäev` cell was covered by the
    bare `time` mask from the day the tab shipped, so nothing was missing and
    nothing failed, right up until the tenth of a month.
    """
    page.set_content(_document_table("10.9.2026"))
    with pytest.raises(AssertionError, match="size a column"):
        assert_no_clock_value_sizes_a_column(page, "teema-dokumendid")


def test_a_clock_value_that_is_held_still_passes_the_capture(page):
    """The other half, so the guard is not simply always angry.

    Normalisation is what satisfies it. Not a wider mask, not a larger
    tolerance: the element still has to be as wide tomorrow as it is today.
    """
    page.set_content(_document_table("10.9.2026"))
    normalise_clock_text(page, "")
    assert_no_clock_value_sizes_a_column(page, "teema-dokumendid")


def test_a_fixed_layout_table_is_not_a_hazard(page):
    """Why the register was never in this, and why the rule says `auto`.

    `.table--register` declares its column widths, so the browser sizes them
    from the declaration and never looks at a cell. A date inside it may be
    masked and left at that — which is what four register baselines have done
    across every month boundary since they were taken.
    """
    page.set_content(_document_table("10.9.2026", layout="fixed"))
    assert_no_clock_value_sizes_a_column(page, "teemad-1440")


def test_the_column_guard_only_looks_at_what_is_photographed(page):
    """A clipped capture holds one component, and the rest of the page is not in it.

    Asserted because the alternative is a guard that fails a Kaasamine
    screenshot for a table three thousand pixels below it, which is how a rule
    stops being applied and starts being worked around.
    """
    page.set_content(f'<div id="clip">nothing clock-derived</div>{_document_table("10.9.2026")}')
    assert_no_clock_value_sizes_a_column(page, "kaasamine-kirjed", root="#clip")
    with pytest.raises(AssertionError, match="size a column"):
        assert_no_clock_value_sizes_a_column(page, "kaasamine-kirjed")
