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

from e2e.conftest import MARTIN, create_matter, open_add_panel, open_matter, sign_in

pytestmark = pytest.mark.e2e

WIDTHS = [1440, 1024, 420]

#: Today, as the `Saadetud` field reads it. A registered send may not be in the
#: future, and a Matter this file creates is created *now* — so today is the only
#: day that puts a send after its own Matter's `Alustatud`. Anything earlier is a
#: back-fill, which is a real shape and is pinned in
#: `tests/test_teema_approved_target.py` rather than photographed here.
TODAY_ET = date.today().strftime("%d.%m.%Y")
TODAY_LABEL = f"{date.today().day}.{date.today().month}.{date.today().year}"

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
    expect(panel).to_have_attribute("open", "")
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
    register_a_send(page, matter_url, filename="Koja-arvamus.pdf", sent_on=TODAY_ET)

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
    dates = [text.strip() for text in page.locator(".tl-step__date").all_inner_texts()]
    assert dates[-1] == et(21), dates
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
    register_a_send(page, matter_url, filename="Koja-arvamus.pdf", sent_on=TODAY_ET)

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
    register_a_send(page, matter_url, filename="Koja-arvamus.pdf", sent_on=TODAY_ET)
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
    register_a_send(page, matter_url, filename="Koja-arvamus.pdf", sent_on=TODAY_ET)
    register_a_send(page, matter_url, filename="Koja-taiendav-arvamus.pdf", sent_on=TODAY_ET)

    page.goto(matter_url)
    page.wait_for_load_state("networkidle")

    # Two columns with the same label and the same date, which is the narrowest
    # thing the strip ever has to fit and the one case where «is it one column or
    # two» can only be answered by measuring. *Which* order two sends fall in is
    # a projection question and is asserted in
    # `tests/test_teema_approved_target.py`, where a Matter's creation can be
    # backdated far enough for two distinct send days to be realistic.
    assert labels(page) == ["Alustatud", "Koja arvamus", "Koja arvamus"]
    dates = [text.strip() for text in page.locator(".tl-step__date").all_inner_texts()]
    assert dates == [TODAY_LABEL, TODAY_LABEL, TODAY_LABEL], dates
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
