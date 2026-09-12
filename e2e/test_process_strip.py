"""`Teema käik` — the sparse process strip, in a real browser.

Three labels and no more: `Alustatud`, `Koja arvamus`, `Lõpetatud`. The Python
suite proves the projection; what only a browser can show is that the strip is
still a strip at every width — one column, two, three, several identical
`Koja arvamus` columns, or none at all — and that none of the five retired
sources has left a label behind on the page a person actually opens
(docs/adr/0074 §12.1).

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
from datetime import date

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
#: and the rest are its structured facts.
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
    """No sideways scroll, and every column on the screen."""
    assert not overflows(page), f"the Teema page scrolls sideways at {width}px"
    steps = page.locator(".tl-step")
    for index in range(steps.count()):
        box = steps.nth(index).bounding_box()
        assert box is not None, f"strip column {index} has no box at {width}px"
        assert box["width"] > 0 and box["height"] > 0, (
            f"strip column {index} collapsed to nothing at {width}px"
        )
        assert box["x"] >= -1 and box["x"] + box["width"] <= width + 1, (
            f"strip column {index} sits outside the {width}px viewport"
        )


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
    dates, three commencements and a `Töövõit`. Exactly one of those is on the
    strip, and it is none of them.

    `Hetkeseis` is asserted *present* in the header in the same breath, because
    «the label is gone from the page» and «the label is gone from the strip» are
    different claims and only the second one is wanted.
    """
    sign_in(page, base_url, MARTIN)
    page.set_viewport_size({"width": width, "height": 900})
    open_matter(page, base_url, OPEN_TITLE)

    expect(strip(page)).to_have_count(1)
    # Two columns, because `seed_e2e_data` also files a sent opinion on this
    # Matter for the Statistika world. Everything else it carries draws nothing.
    assert labels(page) == ["Alustatud", "Koja arvamus"]
    for gone in RETIRED:
        expect(strip(page).get_by_text(gone, exact=False)).to_have_count(0)
    # No countdown either: the `N p` suffix went with the future sources.
    assert not COUNTDOWN.search(strip(page).inner_text()), "the strip still counts down"
    # And `Hetkeseis` is where it is stated, which is the header.
    expect(page.locator(".metaline")).to_contain_text("Hetkeseis")
    expect(page.locator(".metaline")).to_contain_text("Kooskõlastusringil")
    assert_fits(page, width)
