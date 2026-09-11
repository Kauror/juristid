"""420 px, on the surfaces the existing sweeps do not reach.

Hands-on QA could only get the window down to 500 px, so the widths below that
are the ones a person has never actually looked at. Three sweeps already exist
and are not repeated here:

* `e2e/test_ux_pass.py` walks `/osakond/`, `/minu-asjad/` and `/teemad/` down to
  375 px, and holds the Matter workspace at 375;
* `e2e/test_teema_workspace.py` operates `PRAEGUNE TEGEVUS` and one opened
  `LISA TEEMALE` panel at 420 px;
* `e2e/test_matter_form_ux.py`, `test_addressee_free_entry.py`,
  `test_oigusakt_row.py` and `test_date_ux.py` hold `Uus teema` at 420 px.

What none of them touches: the file affordance *inside* an opened add panel,
the `Dokumendid` page at all, `Muuda teemat` at all, and the register's filter
panel while it is open. Those are this file, at 420 px exactly, because that is
the width the brief names.

The assertion is always the same pair — the document does not scroll sideways,
and the control in question is really on the screen rather than merely present
in the DOM. A zero-width or off-canvas element satisfies `to_be_visible` in
Playwright far more often than people expect, so every check reads a bounding
box.
"""

from __future__ import annotations

import pytest
from playwright.sync_api import expect

from e2e.conftest import MARTIN, SANDRA, create_matter, open_add_panel, open_matter, sign_in

NARROW = {"width": 420, "height": 900}

#: The seeded open Matter, spelled as `seed_e2e_data` writes it. Copied
#: rather than imported for the reason `e2e/conftest.py` gives: this
#: directory deliberately imports no application code.
OPEN_TITLE = (
    "Tavaline avatud teema kõigile nähtav — pakendiseaduse ja sellega seonduvalt "
    "teiste seaduste muutmise seaduse eelnõu väljatöötamiskavatsus"
)


def overflows(page) -> bool:
    return page.evaluate(
        "() => document.documentElement.scrollWidth > document.documentElement.clientWidth + 1"
    )


def inside(page, locator, *, width: int = 420) -> bool:
    """On the screen, and within it - not merely attached to the document."""
    box = locator.bounding_box()
    if box is None:
        return False
    return (
        box["width"] > 0
        and box["height"] > 0
        and box["x"] >= -1
        and box["x"] + box["width"] <= width + 1
    )


def open_opinions(page):
    """Open `Arvamused` and return its summary.

    The block is inside `<details class="accordion accordion--opinions">`,
    closed at rest. A closed `<details>` still gives its descendants a bounding
    box while reporting them invisible, so a width assertion taken without
    opening it is measuring a layout nobody can see.
    """
    accordion = page.locator("details.accordion--opinions").first
    assert accordion.count(), "Dokumendid no longer has an Arvamused accordion"
    summary = accordion.locator("summary").first
    if not accordion.evaluate("node => node.open"):
        summary.click()
        page.wait_for_timeout(200)
    assert accordion.evaluate("node => node.open"), "the Arvamused accordion would not open"
    return summary


# ---------------------------------------------------------------------------
# The file affordance inside an opened add panel
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("panel", ("lisa-marge", "lisa-tahtaeg", "lisa-toovoit"))
def test_an_opened_add_panel_offers_its_file_control_inside_420px(page, base_url, panel):
    """#180 gave six operations a file affordance; this is where it has to fit.

    The panel itself is asserted at 420 px by `test_teema_workspace.py`. The
    upload control inside it is not, and it is the widest thing in the form -
    a drop area with a label, a button and a list of chosen filenames beside
    each other.
    """
    sign_in(page, base_url, MARTIN)
    page.set_viewport_size(NARROW)
    create_matter(page, base_url, f"Kitsas lisamine: {panel}")

    open_add_panel(page, panel)

    assert not overflows(page), f"{panel} makes the Teema page scroll sideways at 420px"
    drop = page.locator(f"#{panel} .cx-drop").first
    assert drop.count(), f"{panel} offers no file affordance"
    assert inside(page, drop), f"{panel}'s file area is outside the 420px viewport"

    chooser = page.locator(f"#{panel} input[type=file]").first
    assert chooser.count(), f"{panel} has a drop area with no file input behind it"


def test_attaching_a_file_in_a_narrow_panel_keeps_the_page_inside_itself(page, base_url):
    """The filename is the part that can push a narrow layout open.

    A chosen file is echoed back by name, and a long Estonian filename is
    exactly the string that turns a tidy column into a horizontal scrollbar.
    """
    sign_in(page, base_url, MARTIN)
    page.set_viewport_size(NARROW)
    create_matter(page, base_url, "Kitsas failimanus")
    open_add_panel(page, "lisa-marge")

    page.locator("#lisa-marge input[type=file]").first.set_input_files(
        {
            "name": "Majandus-ja-kommunikatsiooniministeeriumi-vastuskiri-2026.pdf",
            "mimeType": "application/pdf",
            "buffer": b"%PDF-1.4 kitsas",
        }
    )

    assert not overflows(page), "a chosen filename makes the Teema page scroll sideways at 420px"


# ---------------------------------------------------------------------------
# Dokumendid, including the opinion area
# ---------------------------------------------------------------------------


def test_the_documents_page_and_its_opinion_area_fit_420px(page, base_url):
    """Not in any existing sweep, and it carries the widest table in the app."""
    sign_in(page, base_url, SANDRA)
    page.set_viewport_size(NARROW)
    matter_url = open_matter(page, base_url, OPEN_TITLE)

    page.goto(f"{matter_url}dokumendid/")
    page.wait_for_load_state("networkidle")

    assert not overflows(page), "Dokumendid scrolls sideways at 420px"

    # And with the opinion accordion open, which is where this page's widest
    # rows are: a filename, a badge, a date and an addressee on one line.
    summary = open_opinions(page)

    assert not overflows(page), "the open Arvamused accordion scrolls Dokumendid sideways at 420px"
    assert inside(page, summary), "the Arvamused summary sits outside the 420px viewport"


def test_the_register_a_send_disclosure_fits_420px(page, base_url):
    """#182's form: nine fields, and the narrowest place they are ever shown.

    The candidate is uploaded here rather than taken from the seeded world.
    `Registreeri saatmine` only appears while some opinion file has no
    Submission accounting for it, and the seeded Matter's opinion files all do
    - so a test that looked for the disclosure would have skipped, and a
    skipped test asserts nothing about the width it was written for.
    """
    sign_in(page, base_url, MARTIN)
    page.set_viewport_size(NARROW)
    create_matter(page, base_url, "Kitsas saatmise registreerimine")
    page.goto(f"{page.url}dokumendid/")
    page.wait_for_load_state("networkidle")

    # The panel starts `hidden` and is revealed from the toolbar, so its file
    # input is attached but not operable until somebody asks for it.
    page.locator('[data-reveals="lae-dokument"]').first.click()
    page.locator("#lae-dokument select[name=role]").first.wait_for(state="visible")

    page.locator("#lae-dokument input[type=file][name=upload]").first.set_input_files(
        {
            "name": "Koja-arvamus-pakendiseaduse-eelnou-kohta.pdf",
            "mimeType": "application/pdf",
            "buffer": b"%PDF-1.4 arvamus",
        }
    )
    page.locator("#lae-dokument select[name=role]").first.select_option("KODA_SUBMISSION_FINAL")
    page.locator("#lae-dokument button[type=submit]").first.click()
    page.wait_for_load_state("networkidle")

    # `Arvamused` is an accordion and it is closed at rest, so everything
    # inside it reports a bounding box and no visibility. Measured while
    # writing this: the summary below resolves, has a box of 346x17 at x=37,
    # and `is_visible()` is False - which is what a closed `<details>` looks
    # like, not what a layout defect looks like.
    open_opinions(page)

    # The disclosure's own summary, by exact text. A substring `get_by_text`
    # also matches the block's explanatory hint and the submit button inside
    # the form, neither of which opens anything.
    trigger = page.locator("summary.disclosure__summary").filter(has_text="+ Registreeri saatmine")
    assert trigger.count(), "an unaccounted-for opinion file offers no registration"
    trigger.first.click()
    page.wait_for_timeout(200)

    assert not overflows(page), "the send-registration form scrolls Dokumendid sideways at 420px"
    saadetud = page.locator("#id_saadetud-sent_on").first
    assert saadetud.count(), "the form no longer asks for Saadetud"
    assert inside(page, saadetud), "the Saadetud field sits outside the 420px viewport"


# ---------------------------------------------------------------------------
# Muuda teemat
# ---------------------------------------------------------------------------


def test_muuda_teemat_fits_420px_including_its_organisation_control(page, base_url):
    """The edit form is the longest form in the product and is swept nowhere.

    Deliberately tolerant about *which* organisation control it finds: this
    asserts that whatever `Muuda teemat` shows for Saatja fits a phone, not
    which widget it is - that is a product decision and belongs to the branch
    that makes it.
    """
    sign_in(page, base_url, SANDRA)
    page.set_viewport_size(NARROW)
    matter_url = open_matter(page, base_url, OPEN_TITLE)

    page.goto(f"{matter_url}muuda/")
    page.wait_for_load_state("networkidle")

    assert not overflows(page), "Muuda teemat scrolls sideways at 420px"
    expect(page.get_by_role("button", name="Salvesta", exact=False).first).to_be_visible()

    sender = page.locator("[data-orgpicker], #id_sender_name, select[name=source_organisations]")
    if sender.count():
        assert inside(page, sender.first), "the Saatja control is outside the 420px viewport"


# ---------------------------------------------------------------------------
# Teemad filters, open
# ---------------------------------------------------------------------------


def test_the_register_filter_panel_fits_420px_while_it_is_open(page, base_url):
    """The closed register is swept by `test_ux_pass.py`; the open panel is not."""
    sign_in(page, base_url, SANDRA)
    page.set_viewport_size(NARROW)
    page.goto(f"{base_url}/teemad/?olek=koik")
    page.wait_for_load_state("networkidle")

    panel = page.locator("#tapsem-otsing")
    assert panel.count(), "the register no longer has a `Täpsem otsing` panel"
    if not panel.evaluate("node => node.open"):
        panel.locator("summary").first.click()
        page.wait_for_timeout(200)
    assert panel.evaluate("node => node.open"), "the filter panel would not open"

    assert not overflows(page), "the open filter panel scrolls Teemad sideways at 420px"
