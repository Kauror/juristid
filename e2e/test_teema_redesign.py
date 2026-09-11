"""The approved Teema design states, in a real browser.

The domain suite proves the rules; this proves the *page*. Each scenario below
is one of the states the design was approved in, and each is checked for the
thing that state exists to demonstrate — not for its pixels, which the visual
suite locks separately.

The fixtures are made through the UI rather than seeded, deliberately. A closed
Matter created by pressing "Lõpeta teema" is evidence that the flow works; a
closed Matter written into the seed is evidence that the fixture works. The cost
is that these tests lengthen the register for whatever runs after them, which is
the coupling `test_ui_regression.py` already documents.
"""

from __future__ import annotations

from datetime import date, timedelta

import pytest
from playwright.sync_api import expect

from app.core.management.commands.seed_e2e_data import OPEN_TITLE
from e2e.conftest import MARTIN, SANDRA, create_matter, open_composer, open_matter, sign_in

pytestmark = pytest.mark.e2e


def _future(days: int) -> str:
    value = date.today() + timedelta(days=days)
    return f"{value.day}.{value.month}.{value.year}"


def document_overflows(page) -> bool:
    return page.evaluate(
        "() => document.documentElement.scrollWidth > document.documentElement.clientWidth + 1"
    )


# ---------------------------------------------------------------------------
# A. a normal active Matter
# ---------------------------------------------------------------------------


def test_a_normal_matter_answers_everything_above_the_fold(page, base_url):
    """What is this, who owns it, where does it stand, what happens next.

    Measured against the fold rather than asserted as present: "it is on the
    page" was true of the old design too, three screens down.
    """
    sign_in(page, base_url, SANDRA)
    open_matter(page, base_url, OPEN_TITLE)

    fold = page.viewport_size["height"]
    # The composer is a disclosure, so what has to be above the fold is the row
    # that opens it. What it opens *into* is measured by the 1024 test below,
    # where the reading order is what matters (design handoff 1d).
    for selector in (
        ".matterhead__title",
        ".metaline",
        ".uxnext",
        # The composer itself, open. It was the collapsed prompt until the
        # approved target, which is the one-line summary the open box replaced
        # (docs/adr/0074 §3).
        "details.composer .composer__body",
    ):
        box = page.locator(selector).first.bounding_box()
        assert box is not None, f"{selector} did not render"
        assert box["y"] < fold, f"{selector} starts below the fold at {box['y']}px"

    # The chronology is open, and the documents are a tab away.
    # Open by default since the v2 rebuild: the first page of the chronology is
    # what a lawyer opens the file for, and closing it made every visit cost a
    # click before the page said anything (02-EKRAANID §C). Still a <details>,
    # so it closes.
    expect(page.locator("#ajajoon")).to_have_attribute("open", "")
    expect(page.locator(".tabs__tab")).to_have_count(2)


def test_the_summary_is_written_and_read_in_the_same_place(page, base_url):
    """No dialog, no page change, no heading over two sentences."""
    sign_in(page, base_url, MARTIN)
    create_matter(page, base_url, "Lühikokkuvõtte brauserikatse")

    expect(page.get_by_text("Mida see teema ettevõtjatele tähendab?")).to_be_visible()
    page.locator(".summary__trigger").click()
    page.locator("#id_brief_summary").fill(
        "Eelnõu paneks digiplatvormidele kvartaalse aruandluskohustuse müüjate tehingute kohta."
    )
    page.locator(".summary__form").get_by_role("button", name="Salvesta").click()
    page.wait_for_load_state("networkidle")

    expect(page.locator(".summary__text")).to_contain_text("digiplatvormidele")
    # Still on the Matter page, and the paragraph is where the prompt was.
    assert "/teemad/" in page.url
    summary = page.locator(".summary").bounding_box()
    tabs = page.locator(".tabs").bounding_box()
    assert summary["y"] < tabs["y"], "the summary moved out of the header band"


# ---------------------------------------------------------------------------
# B. a low-data Matter
# ---------------------------------------------------------------------------


def test_a_low_data_matter_is_short_and_deliberate(page, base_url):
    """No summary, no next step, no engagement, no opinion.

    The old page answered this state with four labelled sections reporting four
    absences. The check is height: a Matter with nothing on it must be shorter
    than one with everything.
    """
    sign_in(page, base_url, MARTIN)
    create_matter(page, base_url, "Tühi teema ilma igasuguse sisuta")

    for absence in (
        "Olulisi tähtaegu pole lisatud.",
        "Jõustumise infot pole lisatud.",
        "Töövõite ega kandidaate pole lisatud.",
        "Saabunud materjalid",
    ):
        expect(page.get_by_text(absence, exact=False)).to_have_count(0)

    expect(page.locator(".uxnext")).to_contain_text("Järgmine samm on määramata")
    # `Kaasamine` used to carry the last of the absence sentences in a collapsed
    # summary line, and then a standing section with a label and an add control.
    # The approved target has neither: recording one is a composer panel, and an
    # empty Matter says nothing about consultations at all (docs/adr/0074 §9).
    expect(page.locator(".accordion__summary--empty")).to_have_count(0)
    expect(page.get_by_text("Kaasamist ei ole kirja pandud")).to_have_count(0)
    expect(page.locator("#kaasamine")).to_have_count(0)
    expect(page.locator(".factspanel")).to_have_count(0)
    expect(page.locator("#cx-kaasamine")).to_have_count(1)

    # Generous, and still far below what four labelled absences cost: this
    # catches a regression into the old shape, not a precise budget.
    height = page.evaluate("() => document.body.scrollHeight")
    assert height < 2200, f"an empty Matter renders {height}px tall"


# ---------------------------------------------------------------------------
# C. an information-heavy Matter
# ---------------------------------------------------------------------------


def test_a_busy_matter_still_opens_on_what_to_do_next(page, base_url):
    """Many entries, and the page still starts with the question, not the file's
    memory."""
    sign_in(page, base_url, MARTIN)
    url = create_matter(page, base_url, "Mahukas teema paljude sissekannetega")

    for index in range(12):
        page.goto(url)
        open_composer(page)
        page.locator(".composer__body").fill(f"Sissekanne number {index} sünteetilises maailmas.")
        page.locator("[data-composer-submit]").click()
        page.wait_for_load_state("networkidle")

    page.goto(url)
    timeline = page.locator("#ajajoon")
    # Open by default since the v2 rebuild: the first page of the chronology is
    # what a lawyer opens the file for, and closing it made every visit cost a
    # click before the page said anything (02-EKRAANID §C). Still a <details>,
    # so it closes.
    expect(timeline).to_have_attribute("open", "")
    # Its summary line still says how much there is and when it last moved.
    expect(timeline.locator(".uxtl__count")).to_contain_text("kirjet")

    # What the fold test is actually about: a Matter with two hundred entries
    # still opens on what to do next, not on its history. The chronology is
    # below the next step, however many entries it holds.
    fold = page.viewport_size["height"]
    assert page.locator(".uxnext").bounding_box()["y"] < fold
    assert timeline.bounding_box()["y"] > page.locator(".uxnext").bounding_box()["y"]
    assert page.locator("details.composer .composer__body").bounding_box()["y"] < fold


# ---------------------------------------------------------------------------
# D + E. closing a Matter, and the closed Matter afterwards
# ---------------------------------------------------------------------------


def test_closing_happens_in_the_composer_and_leaves_a_readable_past(page, base_url):
    sign_in(page, base_url, MARTIN)
    url = create_matter(page, base_url, "Lõpetatav teema brauserikatsest")

    # A next step first, so the closure has something to end.
    open_composer(page)
    page.locator(".composer__body").fill("Esitan Koja arvamuse ministeeriumile.")
    page.locator("[name='next_text']").fill("Esitada arvamus ministeeriumile")
    page.locator("#id_next_date").fill(_future(5))
    page.locator("[data-composer-submit]").click()
    page.wait_for_load_state("networkidle")
    expect(page.locator(".uxnext__text")).to_have_text("Esitada arvamus ministeeriumile")

    # Closing is a composer panel, not a box in the rail.
    expect(page.locator(".rail").get_by_text("Sulge teema")).to_have_count(0)
    open_composer(page)
    page.locator("#cx-lopeta > summary").click()
    expect(page.locator("#cx-lopeta")).to_have_attribute("open", "")

    # No confirmation box: answering the section is the request (pilot QA F-02).
    expect(page.locator("#id_close_matter")).to_have_count(0)
    expect(page.locator("[data-composer-submit]")).to_have_text("Salvesta")

    # `Kuidas lõppes` is three chips over the field the server validates, and
    # nothing is chosen until somebody chooses (docs/adr/0074 §10).
    page.locator("#cx-lopeta .uxchip", has_text="Jõustus").click()
    expect(page.locator("#cx-lopeta input[name=disposition]")).to_have_value("COMPLETED")
    # No confirmation box, no second narrative box, and no work-victory
    # decision: closing a file is not a claim that anything was won, and
    # `+ Töövõit` records a win without closing anything.
    expect(page.locator("#id_closure_reason")).to_have_count(0)
    expect(page.locator("[name=work_victory]")).to_have_count(0)
    page.locator(".composer__body").fill("Menetlus lõppes; töö on tehtud.")
    # The server's own answer, not what the page looks like afterwards. A save
    # that is refused and a save that quietly did nothing leave an identical
    # screen, and the difference is the whole question here.
    with page.expect_response(
        lambda response: "/sissekanne/" in response.url and response.request.method == "POST"
    ) as caught:
        page.locator("[data-composer-submit]").click()
    saved = caught.value
    assert saved.status == 200, f"the closure save was refused: {saved.status}"
    page.wait_for_load_state("networkidle")
    expect(page.locator(".formerror")).to_have_count(0)
    expect(page.locator(".composer .field__error")).to_have_count(0)

    # The header followed the closure out of band, so the page does not come
    # back from its own save calling an archived Matter `Avatud`
    # (docs/adr/0074 §10, app/matters/views.py `_render_overview`).
    expect(page.locator(".badge--state")).to_contain_text("Suletud")

    # -- E. the closed Matter -------------------------------------------
    page.goto(url)
    expect(page.locator(".badge--closed")).to_be_visible()
    # The banner quotes the one narrative the save carried, not a second box.
    expect(page.locator(".banner--closed")).to_contain_text("Menetlus lõppes; töö on tehtud.")
    expect(page.locator(".uxnext")).to_contain_text("teema on suletud")
    # No writable next step and no composer at all.
    expect(page.locator("#teema-koostaja")).to_have_count(0)
    # The past stays readable, and is open on arrival. The head no longer quotes
    # the newest entry, so the words are on the page exactly once
    # (docs/adr/0074 §16).
    expect(page.locator(".richtext").get_by_text("Menetlus lõppes; töö on tehtud.")).to_be_visible()


# ---------------------------------------------------------------------------
# F. documents
# ---------------------------------------------------------------------------


def test_evidence_and_working_references_look_like_opposites(page, base_url):
    sign_in(page, base_url, MARTIN)
    url = create_matter(page, base_url, "Dokumentide brauserikatse")
    page.goto(f"{url}dokumendid/")

    expect(page.get_by_text("Sellel teemal ei ole veel dokumente.")).to_be_visible()

    # An upload asks for the role with the file, before anything is committed.
    page.locator(".docempty").get_by_role("button", name="↑ Lae dokument").click()
    panel = page.locator("#lae-dokument")
    expect(panel).to_be_visible()
    expect(panel.locator("select[name='role']")).to_be_visible()
    panel.locator("input[name='upload']").set_input_files(
        files=[
            {
                "name": "eelnou.pdf",
                "mimeType": "application/pdf",
                "buffer": b"%PDF-1.4 synthetic draft",
            }
        ]
    )
    panel.locator("select[name='role']").select_option("INCOMING_AUTHORITY")
    panel.get_by_role("button", name="Salvesta dokument").click()
    page.wait_for_load_state("networkidle")

    page.goto(f"{url}dokumendid/")
    # Scoped to the table: "Saabunud ametlik dokument" is also an <option> in
    # the role filter and in the upload form, and an unscoped text locator finds
    # the hidden one first.
    row = page.locator(".doctable tbody tr").first
    expect(row).to_contain_text("eelnou.pdf")
    expect(row).to_contain_text("Saabunud ametlik dokument")

    # A SharePoint reference is not evidence, and does not look like it.
    # (No facts rail on this tab: browsing files is the task, and it gets the
    # width.)
    expect(page.locator(".teemadocs .rail")).to_have_count(0)
    working = page.locator("#toodokumendid")
    expect(working).not_to_have_attribute("open", "")
    working.locator(".accordion__head").click()
    # The form is a shared panel, revealed rather than always open: three
    # controls point at it and there is one of it.
    working.locator(".disclosure-chip", has_text="+ SharePointi viide").click()
    page.locator("#sharepointi-viide").locator("#id_title").fill("Arvamuse_töödokument.docx")
    page.locator("#sharepointi-viide").locator("#id_web_url").fill(
        "https://example.invalid/sites/oigus/arvamus.docx"
    )
    page.locator("#sharepointi-viide").get_by_role("button", name="Lisa viide").click()
    page.wait_for_load_state("networkidle")

    row = page.locator(".sharepointrow").first
    expect(row).to_contain_text("Arvamuse_töödokument.docx")
    style = row.locator(".badge--sharepoint").evaluate(
        "element => getComputedStyle(element).borderTopStyle"
    )
    assert style == "dashed", (
        "a working reference lost the border style that says it is not evidence"
    )


# ---------------------------------------------------------------------------
# G. 1024 px
# ---------------------------------------------------------------------------


def test_at_1024_the_rail_folds_under_and_nothing_scrolls_sideways(page, base_url):
    sign_in(page, base_url, SANDRA)
    page.set_viewport_size({"width": 1024, "height": 900})
    open_matter(page, base_url, OPEN_TITLE)

    main = page.locator(".teemamain").bounding_box()
    rail = page.locator(".rail").bounding_box()
    assert rail["y"] >= main["y"] + main["height"] - 1, "the rail did not fold under the content"
    assert not document_overflows(page)

    # The reading order of the main column is unchanged: the next step, then
    # the composer, then the chronology.
    order = [
        page.locator(selector).first.bounding_box()["y"]
        for selector in (".uxnext", ".composer", "#ajajoon")
    ]
    assert order == sorted(order), "the reading order changed at 1024px"

    # `Koja arvamus` is a rail block, so at this width it arrives with the
    # rail — under the whole main column rather than inside it. That is the
    # point of folding the rail rather than reflowing its cards into the
    # content (Teema QA §1).
    opinion = page.locator("#koja-arvamus").first.bounding_box()
    assert opinion["y"] >= rail["y"] - 1, "the opinion card left the rail at 1024px"


def _overlap(a, b) -> bool:
    """Do two bounding boxes share any area? Half a pixel of slack each way."""
    return (
        a["x"] < b["x"] + b["width"] - 0.5
        and b["x"] < a["x"] + a["width"] - 0.5
        and a["y"] < b["y"] + b["height"] - 0.5
        and b["y"] < a["y"] + a["height"] - 0.5
    )


def test_at_420_the_drop_area_leaves_the_corner_and_at_1440_it_keeps_it(page, base_url):
    """The one clause of the approved design that is deliberately not copied.

    `TEEMA_TARGET_420.png` shows `.cx-drop--corner` still absolutely positioned
    at 420 px, painted over «+1 nädal» and «+2 nädalat» — and the spec that
    ships with it names that a prototype defect and asks for the drop to become
    a normal-flow full-width row under the chips instead (TEEMA_TARGET_SPEC
    §H, docs/adr/0074 §19). So here the implementation is deliberately better
    than its own reference screenshot, and that is exactly the claim no baseline
    can hold: there is no approved picture of the corrected state to compare
    against, only a rule.

    Both halves, because the fix is conditional. Below 720 px the drop leaves
    the corner; at 1440 px it must still be in it, which is the half a
    narrow-width rule written at the wrong specificity would quietly take with
    it — the defect this round measured and moved the rules to the end of
    `app.css` to stop (docs/adr/0074 §19).
    """
    sign_in(page, base_url, SANDRA)
    page.set_viewport_size({"width": 420, "height": 900})
    open_matter(page, base_url, OPEN_TITLE)

    # Open on arrival, so the row is on the page without a click
    # (TEEMA_TARGET_SPEC §C.2).
    drop = page.locator(".cx-drop--corner")
    expect(drop).to_be_visible()
    assert drop.evaluate("n => getComputedStyle(n).position") == "static", (
        "at 420px the drop area is still absolutely positioned — this is the "
        "prototype defect the spec asks not to reproduce"
    )

    box = drop.bounding_box()
    quick = page.locator("[data-quickdate]")
    chips = [(chip.inner_text().strip(), chip.bounding_box()) for chip in quick.all()]
    assert chips, "the quick-date chips are gone from the composer"
    for label, chip in chips:
        assert not _overlap(box, chip), f"the drop area is painted over «{label}» at 420px"
        assert box["y"] >= chip["y"] + chip["height"] - 1, (
            f"the drop area sits beside or above «{label}» at 420px rather than under the chips"
        )

    row = page.locator(".uxcomp__row").first.bounding_box()
    assert box["width"] >= row["width"] * 0.9, (
        f"the drop area is {box['width']:.0f}px in a {row['width']:.0f}px row — still a "
        f"corner affordance. Below 720px it is a full-width row of its own"
    )
    assert not document_overflows(page), "the Matter page scrolls sideways at 420px"

    # And the rail is last, under the chronology, rather than gone
    # (TEEMA_TARGET_SPEC §H: nothing is hidden at any width).
    rail = page.locator(".rail").bounding_box()
    history = page.locator("#ajajoon").bounding_box()
    assert rail["y"] >= history["y"] + history["height"] - 1, (
        "at 420px the rail did not fold under the chronology"
    )

    # The desktop half, on the same page.
    page.set_viewport_size({"width": 1440, "height": 900})
    page.wait_for_timeout(120)
    assert drop.evaluate("n => getComputedStyle(n).position") == "absolute", (
        "the narrow-width rule took the desktop corner with it"
    )
    box = drop.bounding_box()
    chips = [chip.bounding_box() for chip in page.locator("[data-quickdate]").all()]
    assert all(not _overlap(box, chip) for chip in chips), (
        "at 1440px the corner drop overlaps the quick-date chips"
    )
    assert box["x"] > max(chip["x"] + chip["width"] for chip in chips), (
        "at 1440px the drop area is not at the right end of the «Millal?» row"
    )


# ---------------------------------------------------------------------------
# The QA correction round
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("width", [1440, 1280, 1024])
def test_muuda_teemat_fits_every_width_the_department_uses(page, base_url, width):
    """The edit page at the three widths this is used at.

    No screenshot: the assertion is that nothing scrolls sideways and that every
    control is reachable, which is what a person actually notices. A baseline
    would say the same thing less precisely and would have to be regenerated
    whenever a label changed (Teema QA §11).
    """
    sign_in(page, base_url, MARTIN)
    page.set_viewport_size({"width": width, "height": 900})
    matter_url = open_matter(page, base_url, OPEN_TITLE)

    page.get_by_role("link", name="Muuda teemat").click()
    page.wait_for_load_state("networkidle")
    expect(page.get_by_role("heading", name="Muuda teemat")).to_be_visible()

    assert not document_overflows(page), f"the edit page scrolled sideways at {width}px"

    # Every field is on the page and inside it. The paired rows are a grid that
    # stacks below ~1024px, so this is the check that the stacking actually
    # happens rather than the row overflowing (static/css/app.css,
    # `.createform__pair`).
    #
    # `#id_owner` is a chip group since the v2 rebuild — one input per option,
    # so the first of them is `#id_owner_0` — and the two pages now share one
    # visual language (02-EKRAANID §C).
    page_width = page.evaluate("() => document.documentElement.clientWidth")
    for field in ("#id_title", "#id_owner_0", "#id_received_date", "#id_response_deadline"):
        box = page.locator(field).bounding_box()
        assert box is not None, f"{field} is not rendered at {width}px"
        assert box["x"] >= -1, f"{field} starts off the left edge at {width}px"
        assert box["x"] + box["width"] <= page_width + 1, f"{field} runs off at {width}px"

    # Loobu goes back to the Matter without saving.
    page.get_by_role("link", name="Loobu").click()
    page.wait_for_load_state("networkidle")
    assert page.url.rstrip("/") == matter_url.rstrip("/")


def test_muuda_teemat_saves_the_whole_record_at_once(page, base_url):
    """One form, one save, and the Matter page says the new facts back."""
    sign_in(page, base_url, MARTIN)
    url = create_matter(page, base_url, "Vale pealkiri, mis parandatakse")

    page.get_by_role("link", name="Muuda teemat").click()
    page.wait_for_load_state("networkidle")

    page.fill("#id_title", "Parandatud pealkiri")
    page.fill("#id_brief_summary", "Mida see ettevõtete jaoks tähendab.")
    page.fill("#id_response_deadline", _future(21))
    page.get_by_role("button", name="Salvesta").click()
    page.wait_for_load_state("networkidle")

    assert page.url.rstrip("/") == url.rstrip("/")
    expect(page.get_by_role("heading", name="Parandatud pealkiri")).to_be_visible()
    expect(page.locator(".summary__text")).to_contain_text("Mida see ettevõtete jaoks tähendab.")

    # And the provenance the page showed was read-only: no control posted it.
    page.get_by_role("link", name="Muuda teemat").click()
    page.wait_for_load_state("networkidle")
    expect(page.get_by_text("Muutumatu")).to_be_visible()
    expect(page.locator("[name='origin']")).to_have_count(0)


# ---------------------------------------------------------------------------
# Keyboard and focus
# ---------------------------------------------------------------------------


def test_ctrl_enter_saves_and_every_shortcut_has_a_button(page, base_url):
    sign_in(page, base_url, MARTIN)
    url = create_matter(page, base_url, "Klaviatuuri brauserikatse")

    open_composer(page)
    page.locator(".composer__body").fill("Salvestatud klaviatuurilt.")
    page.locator(".composer__body").press("ControlOrMeta+Enter")
    page.wait_for_load_state("networkidle")

    # Scoped to the entry body: the accordion quotes the newest entry in its own
    # summary line, so the words appear twice on the page now
    # (design handoff 1b).
    expect(page.locator(".richtext").get_by_text("Salvestatud klaviatuurilt.")).to_be_visible()

    # The visible equivalent is the button itself. The `Ctrl + Enter` hint that
    # used to sit beside it went with the approved target's action row, which is
    # a spacer and one `Salvesta` — AGENTS.md asks every shortcut to have an
    # obvious click equivalent, and that is the control, not a caption naming
    # the shortcut (TEEMA_TARGET_SPEC §C.5, docs/adr/0074 §3).
    page.goto(url)
    open_composer(page)
    expect(page.locator(".composer .composer__hint")).to_have_count(0)
    expect(page.locator("[data-composer-submit]")).to_be_visible()


def test_the_next_step_row_sends_you_to_the_composer(page, base_url):
    """One place a next step is written, and the row points at it.

    On a Matter with a step that is «Muuda»; on one without, the row says so
    quietly and stops there. «Määra allpool ↓» is gone — a button whose entire
    content was an arrow pointing at the control directly underneath it said
    what the layout already says, and the composer now asks `Järgmiseks` by
    name (ADR 0052 §13).
    """
    sign_in(page, base_url, MARTIN)
    create_matter(page, base_url, "Fookuse brauserikatse")

    expect(page.locator(".uxnext")).to_contain_text("Järgmine samm on määramata")
    expect(page.get_by_role("button", name="Määra allpool ↓")).to_have_count(0)

    open_composer(page)
    page.locator("[name='next_text']").fill("Koostada arvamuse mustand")
    page.locator("#id_next_date").fill(_future(4))
    page.locator("[data-composer-submit]").click()
    page.wait_for_load_state("networkidle")

    # Now there is a step, and the row's own control focuses the one box that
    # writes one rather than opening a second editor beside it.
    page.get_by_role("button", name="Muuda").click()
    expect(page.locator(".composer__body")).to_be_focused()
