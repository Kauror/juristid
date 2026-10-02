"""420 px, on the surfaces the existing sweeps do not reach.

Hands-on QA could only get the window down to 500 px, so the widths below that
are the ones a person has never actually looked at. Three sweeps already exist
and are not repeated here:

* `e2e/test_ux_pass.py` walks `/osakond/`, `/minu-asjad/` and `/teemad/` down to
  375 px, and holds the Matter workspace at 375;
* `e2e/test_teema_workspace.py` operates `PRAEGUNE TEGEVUS` and one opened
  `LISA TEEMALE` panel at 420 px;
* `e2e/test_matter_form_ux.py`, `test_unified_organisation_picker.py`,
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
from e2e.conftest import document_overflows as overflows
from e2e.legacy_opinions import strand_an_opinion_upload

NARROW = {"width": 420, "height": 900}

#: The seeded open Matter, spelled as `seed_e2e_data` writes it. Copied
#: rather than imported for the reason `e2e/conftest.py` gives: this
#: directory deliberately imports no application code.
OPEN_TITLE = (
    "Tavaline avatud teema kõigile nähtav — pakendiseaduse ja sellega seonduvalt "
    "teiste seaduste muutmise seaduse eelnõu väljatöötamiskavatsus"
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


# ---------------------------------------------------------------------------
# The file affordance inside an opened add panel
# ---------------------------------------------------------------------------


def test_every_add_panel_offers_its_file_control_inside_420px(page, base_url):
    """#180 gave six operations a file affordance; this is where it has to fit.

    The panel itself is asserted at 420 px by `test_teema_workspace.py`. The
    upload control inside it is not, and it is the widest thing in the form -
    a drop area with a label, a button and a list of chosen filenames beside
    each other.

    **Three panels on one seeded Matter, and none created.** `PAGE_SIZE` is 12
    and the seeded register holds exactly 12, so every Matter a browser test
    files pushes a seeded row off page one - and `test_register_columns.py`
    sorts after this file and clicks the first row that carries a `Hetkeseis`
    link. Opening a panel writes nothing, so there is no reason to pay a
    register row for it: `open_add_panel` closes whichever was open, which is
    exactly what makes one Matter enough for all three.
    """
    sign_in(page, base_url, SANDRA)
    page.set_viewport_size(NARROW)
    open_matter(page, base_url, OPEN_TITLE)

    for panel in ("lisa-marge", "marge-tahtaeg", "marge-toovoit"):
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

    Also on the seeded Matter: choosing a file is not submitting one, so
    nothing is written and nothing is polluted.
    """
    sign_in(page, base_url, SANDRA)
    page.set_viewport_size(NARROW)
    open_matter(page, base_url, OPEN_TITLE)
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

    # The opinion row is where this page's widest line is now: a filename, a
    # badge, a date and an addressee. The `Arvamused` accordion that used to
    # sit under the table is retired, and a Matter with nothing unfinished has
    # no opinion block at all (docs/adr/0061, amendment of 2026-09-27).
    badge = page.locator(".doctable .badge--opinion").first
    assert badge.count(), "the seeded sent opinion is no longer an Arvamus row"
    assert inside(page, badge), "the Arvamus badge sits outside the 420px viewport"
    assert not page.locator("#lopetamata-arvamused, #arvamuste-haldus").count()


def test_the_register_a_send_disclosure_fits_420px(page, base_url):
    """#182's form: nine fields, and the narrowest place they are ever shown.

    The form survives only as a repair for an older upload: a file filed as
    `Arvamus` through `Lae dokument` before that role left the menu, which no
    send accounts for. The browser cannot make one any more, so the file is
    written server-side through the same services the upload used
    (`e2e/legacy_opinions.py`) — and the form is still the widest thing this
    page can show.

    **The only Matter this file creates**, and it creates one rather than
    stranding an opinion on the seeded Matter, which would change the file
    counts every later test reads off `Dokumendid`. One register row is the
    cheaper of the two contaminations.
    """
    sign_in(page, base_url, MARTIN)
    page.set_viewport_size(NARROW)
    matter_url = create_matter(page, base_url, "Kitsas saatmise registreerimine")
    strand_an_opinion_upload(
        matter_url, filename="Koja-arvamus-pakendiseaduse-eelnou-kohta.pdf", actor_upn=MARTIN.upn
    )
    page.goto(f"{matter_url}dokumendid/")
    page.wait_for_load_state("networkidle")

    block = page.locator("#lopetamata-arvamused")
    assert block.count(), "a stranded opinion upload is not offered as unfinished"
    assert not overflows(page), "Lõpetamata arvamused scrolls Dokumendid sideways at 420px"

    # The disclosure's own summary, by exact text. A substring `get_by_text`
    # also matches the submit button inside the form, which opens nothing.
    trigger = block.locator("summary.disclosure__summary").filter(has_text="Registreeri saatmine")
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


# ---------------------------------------------------------------------------
# Statistika's six-tab nav, which used to take the page with it (QA-08)
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("width", [375, 420, 1024, 1440])
def test_statistika_does_not_scroll_the_document_sideways(page, base_url, width):
    """The nav is a little over 500 px wide, and at 375 the document was 515.

    Every other wide strip in this product stays inside its own scroller — the
    Teema process strip sets `overflow-x: auto` below 700 px for exactly this
    reason — and `.tabs` did not, so the whole page, header and all, slid under
    the thumb (adversarial QA 2026-09-12, QA-08).

    1024 and 1440 are here so the fix cannot be a narrow-width special case that
    quietly changed the desktop row.
    """
    page.set_viewport_size({"width": width, "height": 812})
    sign_in(page, base_url, MARTIN)
    page.goto(f"{base_url}/statistika/")
    page.wait_for_load_state("networkidle")

    client_width, scroll_width = page.evaluate(
        "() => [document.documentElement.clientWidth, document.documentElement.scrollWidth]"
    )

    assert scroll_width <= client_width + 1, (
        f"/statistika/ at {width}px scrolls the document horizontally: "
        f"scrollWidth={scroll_width} against clientWidth={client_width}"
    )


@pytest.mark.parametrize("width", [375, 420])
def test_every_statistika_tab_is_still_reachable_inside_its_own_scroller(page, base_url, width):
    """Kept, not hidden and not abbreviated.

    The scroll is only the right answer if all six tabs are still there and each
    one can be brought into view. A fix that dropped a tab, folded the row into
    two lines or truncated «Andmekvaliteet» would satisfy the assertion above
    and be worse than the defect.
    """
    page.set_viewport_size({"width": width, "height": 812})
    sign_in(page, base_url, MARTIN)
    page.goto(f"{base_url}/statistika/")
    page.wait_for_load_state("networkidle")

    nav = page.locator("nav.tabs")
    tabs = nav.locator("a")
    assert tabs.count() >= 6, tabs.count()

    # The row scrolls itself: it is wider than its box, and the box is inside the
    # viewport.
    assert nav.evaluate("node => node.scrollWidth > node.clientWidth")
    box = nav.bounding_box()
    assert box is not None and box["x"] >= -1 and box["x"] + box["width"] <= width + 1

    # And the last tab can be reached, which is what the scroller is for.
    last = tabs.last
    last.scroll_into_view_if_needed()
    page.wait_for_timeout(120)
    last_box = last.bounding_box()
    assert last_box is not None and last_box["width"] > 0
    assert last_box["x"] + last_box["width"] <= width + 1

    # Not wrapped: one row, so the active tab's underline stays on the heading's
    # own line.
    assert (
        nav.evaluate(
            "node => { const rows = new Set([...node.querySelectorAll('a')]"
            ".map(a => Math.round(a.getBoundingClientRect().top))); return rows.size; }"
        )
        == 1
    )


def test_the_teema_tabs_keep_the_row_they_had(page, base_url):
    """`.tabs` is shared, so the other surface using it is asserted too.

    The rule was added to the class rather than to one page, which is the right
    place for it — and that makes the Teema page's tab row part of the change
    whether it needed it or not.
    """
    page.set_viewport_size(NARROW)
    sign_in(page, base_url, MARTIN)
    open_matter(page, base_url, OPEN_TITLE)

    nav = page.locator("nav.tabs").first
    expect(nav).to_be_visible()
    assert not overflows(page)
    assert (
        nav.evaluate(
            "node => { const rows = new Set([...node.querySelectorAll('a')]"
            ".map(a => Math.round(a.getBoundingClientRect().top))); return rows.size; }"
        )
        == 1
    )
