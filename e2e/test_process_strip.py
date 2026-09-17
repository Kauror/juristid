"""`Teema käik` — the sparse process strip, in a real browser.

Five labels and no more: `Alustatud`, `Koja arvamus`, `Arvamuse tähtaeg`,
`Jõustumine`, `Lõpetatud`. The Python suite proves the projection; what only a
browser can show is that the strip is still a strip at every width — one column,
two, several identical `Koja arvamus` columns, a five-column file, or none at
all — and that none of the retired sources has left a label behind on the page a
person actually opens (docs/adr/0074 §12.1, §12.4).

The two known *future* columns are the reason half of this file exists. A
`Arvamuse tähtaeg` three weeks out and a `Jõustumine` next year are drawn with
the same dot and the same weight as a completed act, and the longest label on the
strip is now a future one — so «does it still fit at 420» is a question about the
destination rather than about the beginning.

The widths are 1440, 1024 and 420, which is the set the strip has always been
measured at. The geometry assertion is always the same pair: the document does
not scroll sideways, and every visible column really is inside the viewport
rather than merely attached to the document — a zero-width or off-canvas element
satisfies `to_be_visible` far more often than people expect.

Everything here is synthetic. The Matters this file creates are its own; the
seeded world is only *read*, because the strip is clipped by a visual baseline
taken from the seeded open Matter and a file that closed or sent on it would move
that baseline from three viewports away.
"""

from __future__ import annotations

import re
from datetime import date, timedelta

import pytest
from playwright.sync_api import expect

from e2e.conftest import (
    MARTIN,
    add_panel_is_open,
    create_matter,
    open_add_panel,
    open_matter,
    sign_in,
)

pytestmark = pytest.mark.e2e

WIDTHS = [1440, 1024, 420]

#: The `N p` countdown the strip used to print on a future milestone.
COUNTDOWN = re.compile(r"\d+\s+p")

#: The seeded Matters, spelled as `seed_e2e_data` writes them. Copied rather
#: than imported for the reason `e2e/conftest.py` gives: this directory
#: deliberately imports no application code.
OPEN_TITLE = (
    "Tavaline avatud teema kõigile nähtav — pakendiseaduse ja sellega seonduvalt "
    "teiste seaduste muutmise seaduse eelnõu väljatöötamiskavatsus"
)
ARCHIVE_TITLE = "Arhiiviteema 2014 sünteetiline registrikirje"

#: Every label the strip used to draw and no longer may. `Kooskõlastusringil` is
#: the seeded Matter's `Hetkeseis`, `Kaasamiskutse veebis` its engagement kind,
#: and the two `Eelnõu`/`Eeldatav` rows are its watched `MatterImportantDate`
#: records, which stayed out when the commencements came back.
#:
#: `Jõustub` is the **chronology's** tensed wording for a commencement and is
#: retired from the strip specifically: the column is the noun `Jõustumine`, so
#: that a rail does not rename itself on the day a date goes past. «põhiosa» is
#: that record's «mis jõustub» — real, and secondary, so it reads as the
#: column's `title` and never as visible text (docs/adr/0074 §12.4).
RETIRED = [
    "praegu",
    "Loodud",
    "Kooskõlastusringil",
    "Kaasamiskutse veebis",
    "Eelnõu eeldatav kooskõlastusring",
    "Eeldatav VTK avalikustamine",
    "Jõustub",
    "põhiosa",
]


def overflows(page) -> bool:
    return page.evaluate(
        "() => document.documentElement.scrollWidth > document.documentElement.clientWidth + 1"
    )


def strip(page):
    return page.locator(".tl-strip")


def labels(page) -> list[str]:
    return [text.strip() for text in page.locator(".tl-step__what").all_inner_texts()]


def dates_drawn(page) -> list[str]:
    """Every column's date, in the order the strip draws them."""
    return [text.strip() for text in page.locator(".tl-step__date").all_inner_texts()]


def started_on(page) -> str:
    """The day the *server* believes it is, read off the Matter just created.

    Not `date.today()` in the test process. The `Saadetud` field is the one
    value in this file that has to be *today*: a registered send may not be in
    the future, and a Matter this file creates is created *now*, so today is the
    only day that puts a send after its own Matter's `Alustatud`. Anything
    earlier is a back-fill, which is a real shape and is pinned in
    `tests/test_teema_approved_target.py` rather than photographed here.

    The application answers in `Europe/Tallinn` and the browser server is a
    different process from pytest, which on CI runs in UTC. For the three hours
    a day those two calendars disagree, a `date.today()` read here is yesterday
    to the server — so the send lands *before* the `Alustatud` it was meant to
    follow, the strip sorts it first, and seven tests fail over a date nobody
    typed. Read as a module-level constant it was worse still: the import
    happens minutes before the assertion, so the two could straddle midnight on
    their own.

    `Alustatud` is the Matter's own beginning as that server stamped it, in the
    `j.n.Y` form `parse_estonian_date` accepts, and it is exactly the value the
    send must not precede — so the comparison is between two readings of one
    clock instead of two clocks.
    """
    drawn = dates_drawn(page)
    assert drawn, "the strip drew no dated column, so there is no server day to read"
    assert labels(page)[0] == "Alustatud", (
        f"the leftmost column is not the beginning: {labels(page)}"
    )
    return drawn[0]


def assert_fits(page, width: int) -> None:
    """No sideways scroll, every column real, and nothing clipped away.

    **The document never scrolls sideways; the rail may scroll itself.** Below
    720px `grid-auto-columns` takes a 96px floor and `.tl-strip` becomes its own
    `overflow-x: auto` container, so a file with more milestones than the width
    holds — five at 420, where four fit — keeps every one of them at a legible
    width and the reader reaches the rightmost by scrolling the rail: never
    dropped steps, never abbreviated nonsense, and never a horizontally
    scrolling page (TEEMA_TARGET_SPEC §H, `static/css/app.css` @media
    max-width 720).

    So «inside the viewport» is the wrong measurement once the rail scrolls. It
    is what a three-column strip happened to satisfy, and it fails a five-column
    one for doing exactly what the design says. What is asserted instead is that
    nothing is *lost*: every column has a real box, the rail really scrolls when
    its content is wider than its box rather than clipping it, and the last
    column is fully inside the rail once it is scrolled to the end.
    """
    assert not overflows(page), f"the Teema page scrolls sideways at {width}px"
    rail = strip(page)
    if not rail.count():
        return

    geometry = rail.evaluate(
        "node => ({ overflowX: getComputedStyle(node).overflowX,"
        " clientWidth: node.clientWidth, scrollWidth: node.scrollWidth,"
        " left: node.getBoundingClientRect().left,"
        " right: node.getBoundingClientRect().right })"
    )
    scrolls = geometry["scrollWidth"] > geometry["clientWidth"] + 1
    if scrolls:
        assert geometry["overflowX"] == "auto", (
            f"the strip is wider than its box at {width}px and clips instead of "
            f"scrolling (overflow-x: {geometry['overflowX']})"
        )
        assert geometry["right"] <= width + 1, (
            f"the strip's own box sits outside the {width}px viewport"
        )

    steps = page.locator(".tl-step")
    for index in range(steps.count()):
        box = steps.nth(index).bounding_box()
        assert box is not None, f"strip column {index} has no box at {width}px"
        assert box["width"] > 0 and box["height"] > 0, (
            f"strip column {index} collapsed to nothing at {width}px"
        )
        if not scrolls:
            assert box["x"] >= -1 and box["x"] + box["width"] <= width + 1, (
                f"strip column {index} sits outside the {width}px viewport"
            )

    if not scrolls:
        return

    # Scrolled to the end, the rightmost column is fully inside the rail. A
    # column that stayed outside it after this would be one the reader cannot
    # get to at all, which is the failure «it scrolls» would otherwise hide.
    rail.evaluate("node => { node.scrollLeft = node.scrollWidth; }")
    page.wait_for_timeout(120)
    last = steps.nth(steps.count() - 1).bounding_box()
    assert last is not None
    assert last["x"] >= geometry["left"] - 1, (
        f"the last column is off the left of the rail at {width}px"
    )
    assert last["x"] + last["width"] <= geometry["right"] + 1, (
        f"the last column is unreachable at {width}px even scrolled to the end"
    )
    rail.evaluate("node => { node.scrollLeft = 0; }")


def et(days: int) -> str:
    """A date the way a lawyer types one: `7.9.2026` (app/core/dates.py)."""
    when = date.today() + timedelta(days=days)
    return f"{when.day}.{when.month}.{when.year}"


def create_matter_with_deadline(page, base_url: str, title: str, *, deadline: str) -> str:
    """`Uus teema` with an `Arvamuse tähtaeg` filled in.

    A local variant of `conftest.create_matter` rather than a new keyword on the
    shared helper: the deadline is what this file is about and nothing else asks
    for it, and the form field is the same one `test_lawyer_workflow.py` fills.
    """
    page.goto(f"{base_url}/teemad/uus/")
    page.wait_for_load_state("networkidle")
    page.fill("#id_title", title)
    page.fill("#id_response_deadline", deadline)
    page.get_by_role("button", name="Loo teema").click()
    page.wait_for_url(re.compile(r"/teemad/[0-9a-f-]{36}/$"))
    return page.url


def add_a_commencement(page, *, what: str, when: str) -> None:
    """Record a `Jõustumine` through the `+ Jõustumine` panel a person uses.

    **Every locator is resolved fresh, and the save is awaited on the wire.**
    Each workspace save swaps `#teema-vaade` wholesale, so a node captured
    before the swap is detached by the time it is clicked — which is exactly the
    race `open_add_panel` clicks three times for, one element further along.
    Three commencements in a row is the first thing here that saves twice, and
    it found it: `Salvesta` resolved, then «element was detached from the DOM».
    """
    open_add_panel(page, "lisa-joustumine")
    field = page.locator("#lisa-joustumine #id_effective_on")
    field.wait_for(state="visible")
    page.locator("#lisa-joustumine #id_effective_title").fill(what)
    field.fill(when)
    with page.expect_response(
        lambda response: "/lisa/joustumine/" in response.url and response.request.method == "POST"
    ) as caught:
        page.locator("#lisa-joustumine button[type=submit]").first.click()
    assert caught.value.status == 200, f"the commencement was refused: {caught.value.status}"
    page.wait_for_load_state("networkidle")


def close_the_matter(page, label: str = "Menetlus lõppes") -> None:
    """Close the open Matter through the panel a person uses."""
    open_add_panel(page, "lisa-lopeta")
    panel = page.locator("#lisa-lopeta")
    assert add_panel_is_open(page, "lisa-lopeta")
    panel.locator(".uxchip", has_text=label).click()
    with page.expect_response(
        lambda response: "/lisa/lopeta/" in response.url and response.request.method == "POST"
    ) as caught:
        page.locator("#lisa-lopeta button[type=submit]").click()
    assert caught.value.status == 200, f"the closure was refused: {caught.value.status}"
    page.wait_for_load_state("networkidle")
    expect(page.locator(".badge--state")).to_contain_text("Suletud")


def register_a_send(page, matter_url: str, *, filename: str, sent_on: str) -> None:
    """Upload an opinion file and register that it went out, on `Dokumendid`.

    The real two-step act, because that is the only thing that produces a SENT
    `Submission` — and a SENT Submission is the only thing that produces a
    `Koja arvamus` column. A file uploaded as `Arvamus` and left unaccounted for
    draws nothing, which is §8 of the brief and is asserted below.
    """
    page.goto(f"{matter_url}dokumendid/")
    page.wait_for_load_state("networkidle")

    page.locator('[data-reveals="lae-dokument"]').first.click()
    page.locator("#lae-dokument select[name=role]").first.wait_for(state="visible")
    page.locator("#lae-dokument input[type=file][name=upload]").first.set_input_files(
        {"name": filename, "mimeType": "application/pdf", "buffer": b"%PDF-1.4 arvamus"}
    )
    page.locator("#lae-dokument select[name=role]").first.select_option("KODA_SUBMISSION_FINAL")
    page.locator("#lae-dokument button[type=submit]").first.click()
    page.wait_for_load_state("networkidle")

    accordion = page.locator("details.accordion--opinions").first
    if not accordion.evaluate("node => node.open"):
        accordion.locator("summary").first.click()
        page.wait_for_timeout(200)

    trigger = page.locator("summary.disclosure__summary").filter(has_text="+ Registreeri saatmine")
    assert trigger.count(), "an unaccounted-for opinion file offers no registration"
    trigger.first.click()
    page.wait_for_timeout(200)

    form = page.locator("#id_saadetud-sent_on").locator("xpath=ancestor::form[1]")
    # Every required answer, explicitly. `title`, `document`, `sent_on` and
    # `recipients` are all required since #182: the form refuses to infer any of
    # them, which is the whole of R2-01.
    form.locator("#id_saadetud-document").select_option(index=0)
    form.locator("#id_saadetud-title").fill(filename.removesuffix(".pdf"))
    form.locator("#id_saadetud-sent_on").fill(sent_on)
    # The addressee control offers the seeded institutions; any one of them makes
    # this a real send. Which one it is belongs to the intake tests.
    form.locator("#id_saadetud-recipients").select_option(index=0)
    form.locator("button[type=submit]").first.click()
    page.wait_for_load_state("networkidle")


# ---------------------------------------------------------------------------
# None, one, two, three — at three widths
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("width", WIDTHS)
def test_a_bare_imported_matter_draws_no_strip_at_all(page, base_url, width):
    """**No milestones.** An imported archive row has no business-start fact, no
    sent opinion and no recorded closure day, so there is no strip — not an
    empty grid under a heading, which would read as a data-quality problem."""
    sign_in(page, base_url, MARTIN)
    page.set_viewport_size({"width": width, "height": 900})
    open_matter(page, base_url, ARCHIVE_TITLE)

    expect(strip(page)).to_have_count(0)
    assert_fits(page, width)


@pytest.mark.parametrize("width", WIDTHS)
def test_a_new_matter_draws_one_column_and_no_connector(page, base_url, width):
    """**One milestone.** `Alustatud`, alone, and `:last-child` draws no
    connector — so the single column is the whole grammar."""
    sign_in(page, base_url, MARTIN)
    page.set_viewport_size({"width": width, "height": 900})
    create_matter(page, base_url, f"Käiguriba üks verstapost {width}")

    expect(strip(page)).to_have_count(1)
    assert labels(page) == ["Alustatud"]
    assert_fits(page, width)
    connector = page.locator(".tl-step").first.evaluate(
        "node => getComputedStyle(node, '::before').display"
    )
    assert connector == "none", "the only column draws a connector to nothing"


@pytest.mark.parametrize("width", WIDTHS)
def test_a_closed_matter_draws_two_columns(page, base_url, width):
    """**Two milestones.** `Alustatud · Lõpetatud`, and the label is the name of
    the step — the `Disposition` is the column's `title`, not its heading."""
    sign_in(page, base_url, MARTIN)
    page.set_viewport_size({"width": width, "height": 900})
    create_matter(page, base_url, f"Käiguriba kaks verstaposti {width}")
    close_the_matter(page)

    assert labels(page) == ["Alustatud", "Lõpetatud"]
    for outcome in ("Menetlus lõppes", "Jõustus", "Loobuti"):
        expect(page.locator(".tl-step__what", has_text=outcome)).to_have_count(0)
    # The `title` carries the **stored** vocabulary's own label, which is what
    # `matter_banner.html` and `rail.html` already print for a closure. The chip
    # a person clicks says `Menetlus lõppes`; the `Disposition` it writes is
    # `INITIATIVE_WITHDRAWN`, whose canonical label is `Algataja loobus`
    # (docs/adr/0074 §10). One reading of a closure, not a strip-local second
    # one.
    expect(page.locator('.tl-step[title="Algataja loobus"]')).to_have_count(1)
    assert_fits(page, width)


@pytest.mark.parametrize("width", WIDTHS)
def test_the_whole_vocabulary_fits_on_one_row(page, base_url, width):
    """**Three milestones**, in procedural order, at every width."""
    sign_in(page, base_url, MARTIN)
    page.set_viewport_size({"width": width, "height": 900})
    matter_url = create_matter(page, base_url, f"Käiguriba kolm verstaposti {width}")
    register_a_send(page, matter_url, filename="Koja-arvamus.pdf", sent_on=started_on(page))

    page.goto(matter_url)
    page.wait_for_load_state("networkidle")
    assert labels(page) == ["Alustatud", "Koja arvamus"], (
        "an uploaded file alone must not draw a milestone, and a registered send must"
    )

    close_the_matter(page)

    assert labels(page) == ["Alustatud", "Koja arvamus", "Lõpetatud"]
    assert_fits(page, width)


# ---------------------------------------------------------------------------
# The known destination — §12.4
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("width", WIDTHS)
def test_a_new_matter_reads_a_beginning_and_a_destination(page, base_url, width):
    """**Alustatud + Arvamuse tähtaeg.** The shape a file has on the day it is
    opened, and the one this amendment exists for: a known start and the known
    dated point its first phase is heading for.

    `Arvamuse tähtaeg` is the longest label the strip can draw, so 420 is where
    it either fits or wraps into the column beside it.
    """
    sign_in(page, base_url, MARTIN)
    page.set_viewport_size({"width": width, "height": 900})
    create_matter_with_deadline(page, base_url, f"Käiguriba tähtaeg {width}", deadline=et(21))

    assert labels(page) == ["Alustatud", "Arvamuse tähtaeg"]
    assert dates_drawn(page)[-1] == et(21), dates_drawn(page)
    # A future column claims nothing about the past and counts down to nothing.
    assert not COUNTDOWN.search(strip(page).inner_text()), "the strip still counts down"
    for gone in ("praegu", "Lõpp", "Plaanis", "Järgmiseks"):
        expect(strip(page).get_by_text(gone, exact=False)).to_have_count(0)
    assert_fits(page, width)


@pytest.mark.parametrize("width", WIDTHS)
def test_a_deadline_and_a_sent_opinion_read_in_date_order(page, base_url, width):
    """**Arvamuse tähtaeg + Koja arvamus.** An opinion sent today against a
    deadline three weeks out puts the send on the left — chronology, not a fixed
    rail, and no claim either way about whether the answer was on time."""
    sign_in(page, base_url, MARTIN)
    page.set_viewport_size({"width": width, "height": 900})
    matter_url = create_matter_with_deadline(
        page, base_url, f"Käiguriba tähtaeg ja arvamus {width}", deadline=et(21)
    )
    register_a_send(page, matter_url, filename="Koja-arvamus.pdf", sent_on=started_on(page))

    page.goto(matter_url)
    page.wait_for_load_state("networkidle")

    assert labels(page) == ["Alustatud", "Koja arvamus", "Arvamuse tähtaeg"]
    boxes = [
        page.locator(".tl-step").nth(index).bounding_box()
        for index in range(page.locator(".tl-step").count())
    ]
    assert boxes[1]["x"] < boxes[2]["x"], "the send and the deadline columns overlap"
    assert_fits(page, width)


@pytest.mark.parametrize("width", WIDTHS)
def test_a_commencement_is_the_rightmost_destination(page, base_url, width):
    """**Arvamuse tähtaeg + Jõustumine.** The rightmost column becomes
    `Jõustumine` because its date is later — the deadline is not erased by it,
    and «mis jõustub» is the column's `title` rather than its heading."""
    sign_in(page, base_url, MARTIN)
    page.set_viewport_size({"width": width, "height": 900})
    create_matter_with_deadline(page, base_url, f"Käiguriba jõustumine {width}", deadline=et(21))
    add_a_commencement(page, what="põhiosa", when=et(400))

    assert labels(page) == ["Alustatud", "Arvamuse tähtaeg", "Jõustumine"]
    expect(page.locator('.tl-step[title="põhiosa"]')).to_have_count(1)
    # The noun, never the chronology's tensed wording.
    for tensed in ("Jõustub", "Jõustus"):
        expect(page.locator(".tl-step__what", has_text=tensed)).to_have_count(0)
    assert_fits(page, width)


def test_a_long_strip_scrolls_itself_and_never_the_page(page, base_url):
    """**420px, §H.** Where the rail stops fitting, and what it does then.

    Four columns are exactly what 420px holds — the seeded Matter's own shape,
    and `396 / 4` is a whisker over the 96px floor. A **fifth** does not fit, and
    the design's answer is neither to drop one nor to abbreviate it: below 720px
    `grid-auto-columns` takes that floor and `.tl-strip` becomes its own
    `overflow-x: auto` container. Every milestone stays, at a legible width, and
    the reader reaches the rightmost by scrolling the rail rather than the page
    (TEEMA_TARGET_SPEC §H).

    Five columns from a deadline and three commencements, which is a real shape
    — one law commencing in stages against one answer deadline — and needs no
    file upload to reach.
    """
    sign_in(page, base_url, MARTIN)
    page.set_viewport_size({"width": 420, "height": 900})
    create_matter_with_deadline(page, base_url, "Käiguriba kitsas rada", deadline=et(21))
    add_a_commencement(page, what="põhiosa", when=et(400))
    add_a_commencement(page, what="osad sätted", when=et(600))
    add_a_commencement(page, what="register", when=et(800))

    assert labels(page) == [
        "Alustatud",
        "Arvamuse tähtaeg",
        "Jõustumine",
        "Jõustumine",
        "Jõustumine",
    ]
    narrow = strip(page).evaluate(
        "node => ({ overflowX: getComputedStyle(node).overflowX,"
        " clientWidth: node.clientWidth, scrollWidth: node.scrollWidth })"
    )
    assert narrow["overflowX"] == "auto"
    assert narrow["scrollWidth"] > narrow["clientWidth"], (
        "the rail is not scrollable, so its fifth column is unreachable"
    )
    assert not overflows(page), "the rail took the whole document sideways with it"
    # Nothing dropped and nothing clipped: every column has a real box, and the
    # last one is fully inside the rail once it is scrolled to the end.
    assert_fits(page, 420)

    # And the rule does not fire where it is not needed: the same five columns
    # fit at 1024, so the rail is not a scroller there.
    page.set_viewport_size({"width": 1024, "height": 900})
    page.wait_for_timeout(200)
    wide = strip(page).evaluate(
        "node => ({ clientWidth: node.clientWidth, scrollWidth: node.scrollWidth })"
    )
    assert wide["scrollWidth"] <= wide["clientWidth"] + 1, (
        "five columns overflow at 1024, where they are supposed to fit"
    )


@pytest.mark.parametrize("width", WIDTHS)
def test_a_five_column_file_still_fits_on_one_row(page, base_url, width):
    """**The longest realistic strip.** Everything a file can carry at once.

    «On one row» is the claim at 1440 and 1024, where five columns fit. At 420
    it is one *scrollable* row: `assert_fits` measures the rail's own scroll
    box there, and the page still does not move sideways (TEEMA_TARGET_SPEC §H).

    Closed while its response deadline is still ahead of it, which puts
    `Arvamuse tähtaeg` to the *right* of `Lõpetatud`. That reads oddly and it is
    deliberate: the date was set and was never withdrawn, and having the strip
    decide that closing a file discharges an external deadline would invent a
    rule the domain has not recorded (docs/adr/0074 §12.4).
    """
    sign_in(page, base_url, MARTIN)
    page.set_viewport_size({"width": width, "height": 900})
    matter_url = create_matter_with_deadline(
        page, base_url, f"Käiguriba viis verstaposti {width}", deadline=et(21)
    )
    register_a_send(page, matter_url, filename="Koja-arvamus.pdf", sent_on=started_on(page))
    page.goto(matter_url)
    page.wait_for_load_state("networkidle")
    add_a_commencement(page, what="põhiosa", when=et(400))
    close_the_matter(page)

    assert labels(page) == [
        "Alustatud",
        "Koja arvamus",
        "Lõpetatud",
        "Arvamuse tähtaeg",
        "Jõustumine",
    ]
    assert_fits(page, width)
    # Five columns, four connectors, and the rail still ends at the rightmost
    # dot rather than running off the edge of it.
    last = page.locator(".tl-step").last.evaluate(
        "node => getComputedStyle(node, '::before').display"
    )
    assert last == "none", "the rightmost column draws a connector to nothing"


@pytest.mark.parametrize("width", WIDTHS)
def test_two_sent_opinions_draw_two_identical_columns(page, base_url, width):
    """**Several `Koja arvamus` columns.** A supplementary opinion months after
    the first is a second real procedural act, so the strip says so twice —
    which is also the narrowest thing it ever has to fit."""
    sign_in(page, base_url, MARTIN)
    page.set_viewport_size({"width": width, "height": 900})
    matter_url = create_matter(page, base_url, f"Käiguriba kaks arvamust {width}")
    began = started_on(page)
    register_a_send(page, matter_url, filename="Koja-arvamus.pdf", sent_on=began)
    register_a_send(page, matter_url, filename="Koja-taiendav-arvamus.pdf", sent_on=began)

    page.goto(matter_url)
    page.wait_for_load_state("networkidle")

    # Two columns with the same label and the same date, which is the narrowest
    # thing the strip ever has to fit and the one case where «is it one column or
    # two» can only be answered by measuring. *Which* order two sends fall in is
    # a projection question and is asserted in
    # `tests/test_teema_approved_target.py`, where a Matter's creation can be
    # backdated far enough for two distinct send days to be realistic.
    assert labels(page) == ["Alustatud", "Koja arvamus", "Koja arvamus"]
    assert dates_drawn(page) == [began, began, began], dates_drawn(page)
    boxes = [
        page.locator(".tl-step").nth(index).bounding_box()
        for index in range(page.locator(".tl-step").count())
    ]
    assert boxes[1]["x"] < boxes[2]["x"], "the two sent-opinion columns overlap"
    assert_fits(page, width)


# ---------------------------------------------------------------------------
# The five retired sources, on the Matter that carries every one of them
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("width", WIDTHS)
def test_no_retired_source_has_left_a_label_on_the_strip(page, base_url, width):
    """The seeded open Matter carries a `Hetkeseis`, an engagement, two watched
    dates, three commencements and a `Töövõit`. Two of those reach the strip —
    the commencements that have a date — and the rest draw nothing.

    `Hetkeseis` is asserted *present* in the header in the same breath, because
    «the label is gone from the page» and «the label is gone from the strip» are
    different claims and only the second one is wanted.
    """
    sign_in(page, base_url, MARTIN)
    page.set_viewport_size({"width": width, "height": 900})
    open_matter(page, base_url, OPEN_TITLE)

    expect(strip(page)).to_have_count(1)
    # Four columns. `seed_e2e_data` files a sent opinion on this Matter for the
    # Statistika world, and records two commencements with known dates plus one
    # `GENERAL_ORDER` — «jõustub üldises korras», which is a statement about
    # what is *not* known and therefore has no position on a rail. The two
    # watched dates, the engagement, the stage and the `Töövõit` draw nothing.
    assert labels(page) == ["Alustatud", "Koja arvamus", "Jõustumine", "Jõustumine"]
    for gone in RETIRED:
        expect(strip(page).get_by_text(gone, exact=False)).to_have_count(0)
    # No countdown either: the `N p` suffix went with the future sources.
    assert not COUNTDOWN.search(strip(page).inner_text()), "the strip still counts down"
    # And `Hetkeseis` is where it is stated, which is the header.
    expect(page.locator(".metaline")).to_contain_text("Hetkeseis")
    expect(page.locator(".metaline")).to_contain_text("Kooskõlastusringil")
    assert_fits(page, width)


# ---------------------------------------------------------------------------
# Reached, today, ahead
# ---------------------------------------------------------------------------
#
# The reported reading: a Matter created this morning, with an answer due later
# in the month and a commencement in October, drew three identical filled dots
# and looked like a file that had already been through all three. The words are
# unchanged — this is entirely about how the rail and the dots are drawn
# (docs/adr/0074 §12.2, amended 2026-09-12).


def states(page) -> list[str]:
    """Each column's drawn temporal state, in the order they are drawn."""
    return page.locator(".tl-step").evaluate_all(
        "nodes => nodes.map(node => (node.className.match(/tl-step--(\w+)/) || [null, 'none'])[1])"
    )


def reaches(page) -> list[str]:
    """Each column's `--tl-reach`, as the stylesheet actually resolves it."""
    return page.locator(".tl-step").evaluate_all(
        "nodes => nodes.map(node => getComputedStyle(node).getPropertyValue('--tl-reach').trim())"
    )


def test_a_new_matter_does_not_read_as_two_things_that_already_happened(page, base_url):
    """`Alustatud` today and a deadline ahead are drawn differently.

    Before this, the two dots were identical and the rail between them was solid
    accent — which says the deadline has been reached, on the day the file was
    opened.
    """
    sign_in(page, base_url, MARTIN)
    create_matter_with_deadline(page, base_url, "Alustatud täna, tähtaeg ees", deadline=et(18))

    assert labels(page) == ["Alustatud", "Arvamuse tähtaeg"]
    assert states(page) == ["today", "future"]
    # Nothing reached, so no accent rail between them.
    assert reaches(page)[0] == "0%"


def test_a_future_column_is_visibly_quieter_than_a_reached_one(page, base_url):
    """Not merely a different class — a different colour, resolved by the browser.

    A rule that never matched, or a token that resolved to nothing, would leave
    the classes on the page and the two dots identical. This is the assertion a
    stylesheet change cannot pass by accident.
    """
    sign_in(page, base_url, MARTIN)
    create_matter_with_deadline(page, base_url, "Saavutatud ja tulevane", deadline=et(30))

    dots = page.locator(".tl-step__dot")
    colours = dots.evaluate_all(
        "nodes => nodes.map(node => getComputedStyle(node).backgroundColor)"
    )
    assert colours[0] != colours[1], (
        f"the reached dot and the future dot are drawn the same: {colours}"
    )
    borders = dots.evaluate_all("nodes => nodes.map(node => getComputedStyle(node).borderColor)")
    assert borders[0] != borders[1], f"the two dots share a border colour: {borders}"


def test_the_rail_is_two_colours_with_the_boundary_where_today_is(page, base_url):
    """The connector says how far along today has got, not merely which side.

    A deadline a month out, on a Matter started today, puts the boundary at the
    very beginning of the segment; what matters is that the rail is a gradient
    with a hard stop at all rather than one flat accent line.
    """
    sign_in(page, base_url, MARTIN)
    create_matter_with_deadline(page, base_url, "Rööbas kahes värvis", deadline=et(30))

    rail = page.locator(".tl-step").first.evaluate(
        "node => getComputedStyle(node, '::before').backgroundImage"
    )
    assert "gradient" in rail, f"the connector is not a two-colour rail: {rail}"


def test_a_column_dated_today_says_so_without_relying_on_colour(page, base_url):
    """`aria-current="date"` on today, «Tulevikus» on what is ahead.

    A screen reader gets neither the blue nor the grey, so a strip that carried
    the distinction only in colour would read as identical milestones — the same
    defect, for the readers least able to work around it.
    """
    sign_in(page, base_url, MARTIN)
    create_matter_with_deadline(page, base_url, "Täna ja tulevikus", deadline=et(21))

    expect(page.locator('.tl-step[aria-current="date"]')).to_have_count(1)
    expect(page.locator(".tl-step--future .visually-hidden")).to_have_text("Tulevikus")
    # And the visible labels are untouched by either.
    assert labels(page) == ["Alustatud", "Arvamuse tähtaeg"]


def test_the_retired_countdown_grammar_has_not_come_back_with_the_colour(page, base_url):
    """`praegu`, `N p` and «the current step» stay retired.

    This amendment is presentation over the same five milestones. What ADR 0074
    §12.1 removed was a *domain* grammar — a step the file was standing on, a
    sixty-day horizon, a countdown — and none of it is what a muted dot is.
    """
    sign_in(page, base_url, MARTIN)
    create_matter_with_deadline(page, base_url, "Ilma loenduseta", deadline=et(9))

    text = strip(page).inner_text()
    assert not COUNTDOWN.search(text), text
    for gone in ("praegu", "is-current", "is-todo"):
        assert gone not in strip(page).inner_html()


@pytest.mark.parametrize("width", WIDTHS)
def test_the_temporal_rail_survives_the_narrow_scroller(page, base_url, width):
    """420px is the case the two-colour rail could break.

    The fill is the step's own `::before`, so it moves with the column when the
    rail scrolls — nothing is positioned against the viewport, and there is no
    extra grid column. The existing geometry oracle still has to pass, scrolled
    to the end and back.
    """
    sign_in(page, base_url, MARTIN)
    page.set_viewport_size({"width": width, "height": 900})
    url = create_matter_with_deadline(page, base_url, f"Kitsas rööbas {width}", deadline=et(24))
    add_a_commencement(page, what="põhiosa", when=et(60))
    add_a_commencement(page, what="osad sätted", when=et(200))
    page.goto(url)
    page.wait_for_load_state("networkidle")

    assert states(page) == ["today", "future", "future", "future"]
    assert_fits(page, width)

    # Scrolled to the end, the last column is still drawn as a future one: the
    # state travels with the column rather than with its position on screen.
    rail = strip(page)
    rail.evaluate("node => { node.scrollLeft = node.scrollWidth; }")
    page.wait_for_timeout(120)
    assert states(page)[-1] == "future"


# ---------------------------------------------------------------------------
# docs/adr/0083 — `Tagasiside tähtaeg`, the sixth label
# ---------------------------------------------------------------------------


def record_a_round(page, matter_url: str, *, audience: str, deadline: str) -> None:
    """One `+ Kaasamine` with a reply-by date, through the panel a lawyer uses."""
    page.goto(matter_url)
    page.wait_for_load_state("networkidle")
    open_add_panel(page, "lisa-kaasamine")
    panel = page.locator("#lisa-kaasamine")
    panel.locator("[name=audience]").fill(audience)
    panel.locator("[name=feedback_deadline]").fill(deadline)
    # Wait for the POST itself, not for `networkidle`. The panel saves through
    # HTMX and swaps `#teema-vaade`; `networkidle` can return before the swap
    # lands, and the strip read afterwards is then the one from before the save.
    with page.expect_response(
        lambda response: "/lisa/kaasamine/" in response.url and response.request.method == "POST"
    ) as caught:
        panel.get_by_role("button", name="Salvesta").click()
    assert caught.value.status == 200, f"the round was refused: {caught.value.status}"
    page.wait_for_load_state("networkidle")


@pytest.mark.parametrize("width", WIDTHS)
def test_a_reply_by_date_draws_its_own_column(page, base_url, width):
    """The whole point of docs/adr/0083 §1: visible without scrolling.

    A round that asked members to answer by a named day is a dated point the
    file is heading for, exactly as `Arvamuse tähtaeg` is — and until this it
    could only be read by scrolling into the chronology.
    """
    sign_in(page, base_url, MARTIN)
    page.set_viewport_size({"width": width, "height": 900})
    matter_url = create_matter_with_deadline(
        page, base_url, f"Käiguriba tagasiside {width}", deadline=et(40)
    )
    record_a_round(page, matter_url, audience="liikmed", deadline=et(14))

    assert labels(page) == ["Alustatud", "Tagasiside tähtaeg", "Arvamuse tähtaeg"]
    # Two deadlines, two different words: what was asked of members, and what
    # Koda owes. A strip that merged them would promote one into the other.
    assert "Tagasiside tähtaeg" in strip(page).inner_text()
    assert "Arvamuse tähtaeg" in strip(page).inner_text()
    # No urgency asserted, exactly as for every other column.
    assert not COUNTDOWN.search(strip(page).inner_text()), "the strip counts down"
    assert_fits(page, width)


@pytest.mark.parametrize("width", WIDTHS)
def test_several_rounds_draw_several_columns_and_still_fit(page, base_url, width):
    """Several per Matter is ordinary — a file runs more than one round.

    The columns are told apart by «Keda kaasati» in the `title`, which is the
    same mechanism that separates two `Jõustumine` columns and costs the strip
    no extra width.
    """
    sign_in(page, base_url, MARTIN)
    page.set_viewport_size({"width": width, "height": 900})
    matter_url = create_matter_with_deadline(
        page, base_url, f"Käiguriba mitu vooru {width}", deadline=et(40)
    )
    record_a_round(page, matter_url, audience="liikmed", deadline=et(10))
    record_a_round(page, matter_url, audience="töögrupp", deadline=et(20))

    assert labels(page) == [
        "Alustatud",
        "Tagasiside tähtaeg",
        "Tagasiside tähtaeg",
        "Arvamuse tähtaeg",
    ]
    titles = page.locator(".tl-step[title]").evaluate_all(
        "nodes => nodes.map(node => node.getAttribute('title'))"
    )
    assert "liikmed" in titles and "töögrupp" in titles, titles
    assert_fits(page, width)


def test_a_round_with_no_reply_by_date_draws_nothing(page, base_url):
    """docs/adr/0074 §12.1 stands: the engagement itself is not a milestone."""
    sign_in(page, base_url, MARTIN)
    page.set_viewport_size({"width": 1440, "height": 900})
    matter_url = create_matter_with_deadline(
        page, base_url, "Käiguriba kaasamine tähtajata", deadline=et(40)
    )
    page.goto(matter_url)
    page.wait_for_load_state("networkidle")
    open_add_panel(page, "lisa-kaasamine")
    panel = page.locator("#lisa-kaasamine")
    panel.locator("[name=audience]").fill("liikmed")
    # **Cleared, not left alone.** The panel pre-fills `Tagasisidet ootame kuni`
    # with a week out since docs/adr/0086 §2, so a round saved without touching
    # that box *does* carry a dated point and *would* draw a column. Emptying it
    # is what «a consultation with no reply-by date» now means, and it is the
    # state docs/adr/0083 §1 says draws nothing.
    panel.locator("[name=feedback_deadline]").fill("")
    with page.expect_response(
        lambda response: "/lisa/kaasamine/" in response.url and response.request.method == "POST"
    ) as caught:
        panel.get_by_role("button", name="Salvesta").click()
    assert caught.value.status == 200
    page.wait_for_load_state("networkidle")

    assert labels(page) == ["Alustatud", "Arvamuse tähtaeg"]
    assert "Tagasiside tähtaeg" not in strip(page).inner_text()


# ---------------------------------------------------------------------------
# Which clock this file reads — driven over the calendar, not waited for
# ---------------------------------------------------------------------------
#
# `started_on` exists because pytest and the application are two processes with
# two calendars. On CI the runner is UTC and the server answers in
# `Europe/Tallinn`, so for the three hours after 21:00 UTC every day the two
# disagree by a day — and on 2026-09-16 a run that began at 20:54 UTC and
# asserted at 21:01 dated seven sends yesterday, which put `Koja arvamus` to the
# left of the `Alustatud` it was supposed to follow.
#
# Waiting for midnight is not a test. What is asserted below is the property
# that makes midnight uninteresting: the day this file types into `Saadetud` is
# read from the page, and no calendar the test process can be given moves it.


class _Column:
    """One `all_inner_texts()` answer."""

    def __init__(self, texts: list[str]) -> None:
        self._texts = texts

    def all_inner_texts(self) -> list[str]:
        return list(self._texts)


class _StubStrip:
    """Exactly as much of a page as `started_on` is allowed to touch.

    An unexpected selector raises rather than returning an empty list: the point
    of the stub is that it fails if the helper ever starts reading something
    else, not that it quietly answers whatever it is asked.
    """

    def __init__(self, columns: list[tuple[str, str]]) -> None:
        self._columns = columns

    def locator(self, selector: str):
        if selector == ".tl-step__date":
            return _Column([drawn for _, drawn in self._columns])
        if selector == ".tl-step__what":
            return _Column([what for what, _ in self._columns])
        raise AssertionError(f"started_on read an unexpected selector: {selector}")


class _ProcessDay(date):
    """`date`, with a today this process chose. Set per test by `monkeypatch`."""

    day_in_force = date(2026, 9, 16)

    @classmethod
    def today(cls) -> date:
        return cls.day_in_force


@pytest.mark.parametrize(
    "process_day",
    [
        # The hour the failure happened: the server has turned over, pytest has
        # not.
        date(2026, 9, 16),
        # And the mirror image, which a differently configured runner produces.
        date(2026, 9, 18),
        # A calendar with nothing to do with the server's at all.
        date(2019, 1, 1),
    ],
)
def test_the_send_day_is_the_server_s_whatever_day_this_process_thinks_it_is(
    monkeypatch, process_day
):
    """The same strip, read under three test-process calendars, one answer."""
    monkeypatch.setattr(_ProcessDay, "day_in_force", process_day)
    monkeypatch.setitem(globals(), "date", _ProcessDay)

    # The patch really is in force — otherwise the assertion below would hold
    # for a helper that does read this clock, and prove nothing.
    assert et(0) == f"{process_day.day}.{process_day.month}.{process_day.year}"

    page = _StubStrip([("Alustatud", "17.9.2026"), ("Arvamuse tähtaeg", "8.10.2026")])
    assert started_on(page) == "17.9.2026"


def test_a_strip_with_no_beginning_is_a_failure_rather_than_a_date():
    """A send dated off a strip whose first column is not `Alustatud` would be
    dated off whatever that column is — a deadline three weeks out, and a
    refused save. The helper says so instead of guessing."""
    with pytest.raises(AssertionError):
        started_on(_StubStrip([("Arvamuse tähtaeg", "8.10.2026")]))
    with pytest.raises(AssertionError):
        started_on(_StubStrip([]))


def test_no_day_in_this_file_is_decided_at_import():
    """The constants that caused it are gone and may not come back.

    `TODAY_ET` and `TODAY_LABEL` were read once, when pytest imported this
    module — minutes before the assertions that used them, which is how a single
    test run came to straddle midnight on its own. `et` still reads the clock,
    deliberately: it is only ever asked for a day nine or more ahead, where a
    calendar that is off by one is still unambiguously the future.
    """
    at_import = [
        name
        for name, value in globals().items()
        if isinstance(value, (str, date)) and name.isupper() and name.startswith("TODAY")
    ]
    assert at_import == [], at_import
