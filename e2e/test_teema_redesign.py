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

import re
from datetime import date, timedelta

import pytest
from playwright.sync_api import expect

from app.core.management.commands.seed_e2e_data import OPEN_TITLE
from e2e.conftest import MARTIN, SANDRA, sign_in

pytestmark = pytest.mark.e2e


def _future(days: int) -> str:
    value = date.today() + timedelta(days=days)
    return f"{value.day}.{value.month}.{value.year}"


def open_matter(page, base_url: str, title: str = OPEN_TITLE):
    """Open a named Matter from the register, following the link rather than
    clicking it: the table head is sticky and can sit over the first row."""
    page.goto(f"{base_url}/teemad/?olek=koik&q={title.split()[0]}")
    page.wait_for_load_state("networkidle")
    link = page.get_by_role("link", name=title, exact=False).first
    assert link.count(), f"the register does not hold {title!r}"
    page.goto(f"{base_url}{link.get_attribute('href')}")
    page.wait_for_load_state("networkidle")
    return page.url


def create_matter(page, base_url: str, title: str) -> str:
    page.goto(f"{base_url}/teemad/uus/")
    page.wait_for_load_state("networkidle")
    page.fill("#id_title", title)
    page.get_by_role("button", name="Loo teema").click()
    page.wait_for_url(re.compile(r"/teemad/[0-9a-f-]{36}/$"))
    return page.url


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
    open_matter(page, base_url)

    fold = page.viewport_size["height"]
    for selector in (
        ".matterhead__title",
        ".metaline",
        ".nextrow",
        ".composer__body",
    ):
        box = page.locator(selector).first.bounding_box()
        assert box is not None, f"{selector} did not render"
        assert box["y"] < fold, f"{selector} starts below the fold at {box['y']}px"

    # The chronology is closed, and the documents are a tab away.
    expect(page.locator("#ajajoon")).not_to_have_attribute("open", "")
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

    expect(page.locator(".nextrow")).to_contain_text("Järgmine samm on määramata")
    expect(page.locator(".accordion__summary--empty")).to_be_visible()

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
        page.locator(".composer__body").fill(f"Sissekanne number {index} sünteetilises maailmas.")
        page.locator("[data-composer-submit]").click()
        page.wait_for_load_state("networkidle")

    page.goto(url)
    timeline = page.locator("#ajajoon")
    expect(timeline).not_to_have_attribute("open", "")
    # Collapsed, it is one line: a count and when it last moved.
    expect(timeline.locator(".accordion__summary")).to_contain_text("kirjet")
    # The entries are in the document — a closed <details> still renders its
    # children — and none of them is painted across the top of the page.
    expect(page.locator(".entrycard").first).to_be_hidden()

    fold = page.viewport_size["height"]
    assert page.locator(".nextrow").bounding_box()["y"] < fold
    assert page.locator(".composer__body").bounding_box()["y"] < fold


# ---------------------------------------------------------------------------
# D + E. closing a Matter, and the closed Matter afterwards
# ---------------------------------------------------------------------------


def test_closing_happens_in_the_composer_and_leaves_a_readable_past(page, base_url):
    sign_in(page, base_url, MARTIN)
    url = create_matter(page, base_url, "Lõpetatav teema brauserikatsest")

    # A next step first, so the closure has something to end.
    page.locator(".composer__body").fill("Esitan Koja arvamuse ministeeriumile.")
    page.locator("#next_kind_DO").check(force=True)
    page.locator("#id_next_date").fill(_future(5))
    page.locator("[data-composer-submit]").click()
    page.wait_for_load_state("networkidle")
    expect(page.locator(".nextrow .modechip--do")).to_be_visible()

    # Closing is a composer action, not a panel in the rail.
    expect(page.locator(".rail").get_by_text("Sulge teema")).to_have_count(0)
    page.locator(".disclosure-chip", has_text="+ Lõpeta teema").click()
    expect(page.locator("#koostaja-lopetamine")).to_be_visible()

    page.locator("#id_close_matter").check()
    # The primary button says what the save will actually do.
    expect(page.locator("[data-composer-submit]")).to_have_text("Lõpeta teema")

    page.locator("#id_disposition").select_option("COMPLETED")
    page.locator("#id_closure_reason").fill("Seadus jõustus muutmata kujul.")
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

    # -- E. the closed Matter -------------------------------------------
    page.goto(url)
    expect(page.locator(".badge--closed")).to_be_visible()
    expect(page.locator(".banner--closed")).to_contain_text("Seadus jõustus muutmata kujul.")
    expect(page.locator(".nextrow")).to_contain_text("teema on suletud")
    # No writable next step and no composer at all.
    expect(page.locator("#teema-koostaja")).to_have_count(0)
    # The past stays readable.
    page.locator("#ajajoon .accordion__head").click()
    expect(page.get_by_text("Menetlus lõppes; töö on tehtud.")).to_be_visible()


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
    open_matter(page, base_url)

    main = page.locator(".teemamain").bounding_box()
    rail = page.locator(".rail").bounding_box()
    assert rail["y"] >= main["y"] + main["height"] - 1, "the rail did not fold under the content"
    assert not document_overflows(page)

    # The reading order of the main column is unchanged: the next step, then
    # the composer, then the chronology.
    order = [
        page.locator(selector).first.bounding_box()["y"]
        for selector in (".nextrow", ".composer", "#ajajoon")
    ]
    assert order == sorted(order), "the reading order changed at 1024px"

    # `Koja seisukoht` is a rail fact now, so at this width it arrives with the
    # rail — under the whole main column rather than inside it. That is the
    # point of folding the rail rather than reflowing its cards into the
    # content (Teema QA §1).
    position = page.locator("#koja-seisukoht").first.bounding_box()
    assert position["y"] >= rail["y"] - 1, "the position card left the rail at 1024px"


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
    matter_url = open_matter(page, base_url)

    page.get_by_role("link", name="Muuda teemat").click()
    page.wait_for_load_state("networkidle")
    expect(page.get_by_role("heading", name="Muuda teemat")).to_be_visible()

    assert not document_overflows(page), f"the edit page scrolled sideways at {width}px"

    # Every field is on the page and inside it. The date pair and the
    # owner/stage/track row are a grid that stacks when a column would fall
    # below 16rem, so this is the check that the stacking actually happens
    # rather than the row overflowing (static/css/app.css, `.fieldrow`).
    page_width = page.evaluate("() => document.documentElement.clientWidth")
    for field in ("#id_title", "#id_owner", "#id_received_date", "#id_response_deadline"):
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


def test_minu_too_is_one_dated_list(page, base_url):
    """All three modes in one chronological list, each saying what it is."""
    sign_in(page, base_url, MARTIN)
    page.goto(f"{base_url}/minu-too/")
    page.wait_for_load_state("networkidle")

    expect(page.get_by_role("heading", name="Ootan ja kontrollin")).to_have_count(0)
    rows = page.locator(".workrow")
    if not rows.count():
        pytest.skip("the seeded world gives this persona no scheduled work")

    # Every row carries a mode chip, so no row's date is ambiguous.
    assert rows.count() == page.locator(".workrow .mode").count()

    # And the bands run forwards in time down the page.
    tops = [
        page.locator(f".workgroup--{key}").first.bounding_box()["y"]
        for key in ("passed", "today", "soon", "later", "undated")
        if page.locator(f".workgroup--{key}").count()
    ]
    assert tops == sorted(tops), "the work bands are out of chronological order"


# ---------------------------------------------------------------------------
# Keyboard and focus
# ---------------------------------------------------------------------------


def test_ctrl_enter_saves_and_every_shortcut_has_a_button(page, base_url):
    sign_in(page, base_url, MARTIN)
    url = create_matter(page, base_url, "Klaviatuuri brauserikatse")

    page.locator(".composer__body").fill("Salvestatud klaviatuurilt.")
    page.locator(".composer__body").press("ControlOrMeta+Enter")
    page.wait_for_load_state("networkidle")

    page.locator("#ajajoon .accordion__head").click()
    expect(page.get_by_text("Salvestatud klaviatuurilt.")).to_be_visible()

    # The visible equivalent is beside the hint.
    page.goto(url)
    expect(page.locator(".composer .composer__hint")).to_be_visible()
    expect(page.locator("[data-composer-submit]")).to_be_visible()


def test_the_next_step_row_sends_you_to_the_composer(page, base_url):
    """One place a next step is written, and the row points at it."""
    sign_in(page, base_url, MARTIN)
    create_matter(page, base_url, "Fookuse brauserikatse")

    page.get_by_role("button", name="Määra allpool ↓").click()
    expect(page.locator(".composer__body")).to_be_focused()
