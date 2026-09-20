"""`Kaasamine` in a real browser: record a consultation from the composer.

The approved Teema target removed the standalone `Kaasamine` section and put
recording one behind the composer chip `+ Kaasamine`, beside the four other
progressive panels, saved by the composer's one `Salvesta`. This file follows it
there (docs/adr/0074 §9).

What only a browser can check is the same as before, moved: that the disclosure
actually opens, that its chip group is single-select against the hidden field the
server validates, that the one save writes the note *and* the engagement, and
that the HTMX swap puts the result on the page without a reload.

**These write to the restricted Matter, not the ordinary one.** The screenshot
suite opens `OPEN_TITLE`, and a chronology that grew while these ran would make
that baseline depend on test order. Everything written here happens somewhere the
camera never points.
"""

from __future__ import annotations

from playwright.sync_api import expect

from app.core.management.commands.seed_e2e_data import ARCHIVE_TITLE, RESTRICTED_TITLE
from e2e.conftest import SANDRA, open_add_panel, open_composer, sign_in


def panel(page):
    """The `+ Kaasamine` panel under `LISA TEEMALE`, closed until asked for."""
    return page.locator("#lisa-kaasamine")


def open_panel(page):
    """Open it, and return it. Idempotent, so a test can call it twice."""
    open_add_panel(page, "lisa-kaasamine")
    return panel(page)


def open_scratch_matter(page, base_url: str) -> None:
    """Sandra's restricted Matter — writable by her, and never screenshotted."""
    page.goto(f"{base_url}/teemad/?olek=koik&q=Konfidentsiaalne")
    page.wait_for_load_state("networkidle")
    page.get_by_role("link", name=RESTRICTED_TITLE, exact=False).first.click()
    page.wait_for_load_state("networkidle")


def open_empty_matter(page, base_url: str) -> None:
    """The archive record, which holds no `Kaasamine` and never will.

    The zero state cannot be read off the scratch Matter: the tests below write
    to it, so it is empty exactly once per seeded world and only until the first
    of them runs. That made the primary regression test pass in a full run and
    fail on its own — the shape of test nobody can reproduce while fixing it.
    """
    page.goto(f"{base_url}/teemad/?olek=koik&q=Arhiiviteema")
    page.wait_for_load_state("networkidle")
    page.get_by_role("link", name=ARCHIVE_TITLE, exact=False).first.click()
    page.wait_for_load_state("networkidle")


def chronology(page):
    return page.locator("#ajalugu-loend")


def test_the_matter_page_carries_no_standalone_kaasamine_section(page, base_url):
    """The section is gone from the page, not hidden on it.

    A Matter with no consultation used to spend a heading and a line saying so
    on every load; one that had them showed a standing list of dated facts the
    chronology now carries (TEEMA_TARGET_SPEC §F).
    """
    sign_in(page, base_url, SANDRA)
    open_empty_matter(page, base_url)

    expect(page.locator("#kaasamine")).to_have_count(0)
    expect(page.locator(".factspanel")).to_have_count(0)
    expect(page.get_by_text("+ Lisa kaasamine")).to_have_count(0)


def test_the_panel_opens_from_the_launcher_and_asks_the_four_simplified_questions(page, base_url):
    """`Keda kaasati`, two dates, the feedback box — and no `Liik`, no `Täpsus`.

    **docs/adr/0086 §1, §2.** The panel used to open on a row of `Liik` chips
    and a four-way precision control, so the first two decisions a lawyer made
    were a classification nothing read back and a precision an as-it-happens
    round never needs. Both are gone. What is left is who was engaged, when it
    happened, by when answers were asked for, and what came back — plus the
    optional count and the two provider pointers, which cost a reader nothing
    when they are empty (docs/adr/0027, amended 2026-09-12).

    The old five-field form is still not back: no generic `Link`, no `Märkus`.
    """
    sign_in(page, base_url, SANDRA)
    open_scratch_matter(page, base_url)

    expect(panel(page)).not_to_be_visible()
    open_panel(page)

    expect(panel(page).locator("[name=audience]")).to_be_visible()
    expect(panel(page).locator("[name=response_count]")).to_be_visible()
    expect(panel(page).locator("[name=feedback_received]")).to_be_visible()
    expect(panel(page).locator("[name=smaily_url]")).to_be_visible()
    expect(panel(page).locator("[name=alchemer_url]")).to_be_visible()
    # The two retired controls, and the questions the target never asked. `url`
    # in particular: the two named pointers are beside the generic one, not a
    # rename of it.
    expect(panel(page).locator("[name=kind]")).to_have_count(0)
    expect(panel(page).locator("[name=engagement_precision]")).to_have_count(0)
    expect(panel(page).locator(".precision__chips")).to_have_count(0)
    expect(panel(page).locator("[name=url]")).to_have_count(0)
    expect(panel(page).locator("[name=note]")).to_have_count(0)
    # `Kaasamise kuupäev` arrives pre-filled and visible with today: a reader can
    # see what is about to be saved before saving it, which is the whole
    # difference from the version that stamped a date behind their back.
    assert panel(page).locator("[name=occurred_on]").input_value(), (
        "the engagement date opens empty, so today is being applied out of sight"
    )
    # **`Tagasisidet ootame kuni` is not here at all**, which is where
    # docs/adr/0086 §2 finally lands. Recording that Koda asked somebody
    # something is a completed act; a reply-by date turned every one of them into
    # a managed wait with a work item and a second act to end it. Emptying the
    # default was the first answer and it was not enough — an empty box is still
    # a question a lawyer reads and skips on every round they file. Opening a
    # wait is `Ootan tagasisidet` on the round's own row now (docs/adr/0091 §2).
    expect(panel(page).locator("[name=feedback_deadline]")).to_have_count(0)
    expect(panel(page).get_by_text("Tagasisidet ootame kuni")).to_have_count(0)
    # And its own save, which commits this operation and nothing else
    # (docs/adr/0075 §2).
    expect(panel(page).locator("button[type=submit]")).to_have_count(1)


def test_the_wait_is_a_separate_act_on_the_rounds_own_row(page, base_url):
    """`Ootan tagasisidet` — the only place a wait is opened, and its spans.

    Filing a consultation and deciding the file is waiting on an answer are two
    acts, and only the second one puts a row on somebody's desk. So the capture
    panel above asks nothing about it and this disclosure asks one question
    (docs/adr/0091 §2).

    The three chips travelled with the question. Each stores nothing of its own:
    it writes the day into `feedback_deadline`, which is what the server reads,
    and the label then grows to carry the date it landed on so nobody sets a
    reply-by day they did not read — the contract `Järgmine tegevus`'s quick
    dates have (docs/adr/0086 §2).
    """
    sign_in(page, base_url, SANDRA)
    open_scratch_matter(page, base_url)

    open_panel(page)
    panel(page).locator("[name=audience]").fill("ootuse proov")
    panel(page).locator("button[type=submit]").click()
    page.wait_for_load_state("networkidle")

    row = chronology(page).locator(
        ".uxtl__ms-body", has=page.locator(".uxtl__mswhat", has_text="ootuse proov")
    )
    expect(row).to_have_count(1)
    # Nothing is waiting yet: the round was filed and that is all it did.
    expect(row).not_to_contain_text("Ootame tagasisidet kuni")

    row.get_by_text("Ootan tagasisidet", exact=True).click()
    field = row.locator("[name=feedback_deadline]")
    expect(field).to_have_count(1)
    default = field.input_value()
    for label in ("1 nädal", "2 nädalat", "1 kuu"):
        expect(row.locator("[data-quickdate]", has_text=label)).to_have_count(1)

    row.locator("[data-quickdate]", has_text="1 kuu").click()

    assert field.input_value() != default, "the span wrote nothing into the box"
    chosen = row.locator("[data-quickdate].is-selected")
    expect(chosen).to_have_count(1)
    expect(chosen).to_contain_text("1 kuu →")

    row.get_by_role("button", name="Salvesta ootus").click()
    page.wait_for_load_state("networkidle")

    # The round is waiting now, and the act is not offered a second time —
    # moving a deadline somebody set is a correction and lives on `Muuda`.
    waiting = chronology(page).locator(
        ".uxtl__ms-body", has=page.locator(".uxtl__mswhat", has_text="ootuse proov")
    )
    expect(waiting).to_contain_text("Ootame tagasisidet kuni")
    expect(waiting.get_by_text("Ootan tagasisidet", exact=True)).to_have_count(0)
    expect(waiting.get_by_text("Lõpeta kaasamine", exact=True)).to_have_count(1)


def test_two_saves_write_the_note_and_the_engagement_separately(page, base_url):
    """Two intentions, two saves, and the chronology shows both.

    This reverses what the composer's single save proved. A note and a
    consultation are different things somebody chose to record, and the surface
    now asks which before it asks anything else (docs/adr/0075 §2).
    """
    sign_in(page, base_url, SANDRA)
    open_scratch_matter(page, base_url)

    open_composer(page)
    page.locator("#id_marge_title").fill("Küsisin liikmetelt tagasisidet.")
    page.locator("#lisa-marge button[type=submit]").click()
    page.wait_for_load_state("networkidle")

    open_panel(page)
    panel(page).locator("[name=audience]").fill("liikmed")
    panel(page).locator("[name=response_count]").fill("9")
    panel(page).locator("button[type=submit]").click()
    page.wait_for_load_state("networkidle")

    # The engagement, as a milestone row carrying its count and its wait.
    milestone = chronology(page).locator(".uxtl__mswhat", has_text="Kaasamine: liikmed")
    expect(milestone).to_have_count(1)
    expect(chronology(page)).to_contain_text("Vastuseid 9")
    # And **no channel**, because the panel no longer asks for one: every row it
    # writes is `Muu`, and printing «Muu» would be the chronology stating a
    # classification nobody chose (docs/adr/0086 §1).
    expect(chronology(page)).not_to_contain_text("Muu ·")
    # And **not** waiting: the panel never asked, so this round is a completed act
    # on the file rather than an open activity on somebody's desk. Setting the
    # date is still exactly what opens a wait — it is just an act of its own now,
    # `Ootan tagasisidet`, which `e2e/test_engagement_correction.py::
    # test_a_waiting_round_is_finished_on_its_own_row` files a round to prove
    # (docs/adr/0086 §2, §3, narrowed by docs/adr/0091 §2).
    #
    # **Read on this round's own row, not on the whole chronology.** These tests
    # share one scratch Matter, and the test above deliberately leaves a waiting
    # round on it — so a page-wide assertion here would be measuring that round
    # and would depend on the order the two ran in.
    expect(
        chronology(page).locator(
            ".uxtl__ms-body", has=page.locator(".uxtl__mswhat", has_text="Kaasamine: liikmed")
        )
    ).not_to_contain_text("Ootame tagasisidet kuni")
    # The note, as a work row of its own.
    expect(chronology(page).locator(".richtext").first).to_contain_text(
        "Küsisin liikmetelt tagasisidet"
    )
    # One act, one line: the audit event does not also print a clause.
    expect(chronology(page)).not_to_contain_text("lisas kaasamise")

    # The consultation itself did **not** become a strip milestone. A round is a
    # canonical record, a chronology row and detail information; it is not
    # automatically a major procedural act (docs/adr/0074 §12.1). What *does*
    # draw a column is its reply-by date, and only that (docs/adr/0083 §1).
    expect(page.locator(".tl-step__what", has_text="Kaasamine: liikmed")).to_have_count(0)
    # The strip is still drawn, and still says what it always said about this
    # Matter — so the assertion above is about the source, not about a strip
    # that stopped rendering.
    expect(page.locator(".tl-step__what", has_text="Alustatud")).to_have_count(1)


def test_an_engagement_with_no_audience_is_refused_with_the_panel_open(page, base_url):
    """A refusal inside a panel nobody can see is a refusal nobody reads."""
    sign_in(page, base_url, SANDRA)
    open_scratch_matter(page, base_url)
    open_panel(page)

    panel(page).locator("[name=response_count]").fill("3")
    panel(page).locator("button[type=submit]").click()
    page.wait_for_load_state("networkidle")

    expect(panel(page)).to_be_visible()
    expect(panel(page)).to_contain_text("Kirjuta, keda kaasati")
    # With the count still in it, and no other panel opened on its behalf.
    expect(panel(page).locator("[name=response_count]")).to_have_value("3")
    expect(page.locator("#lisa-marge")).not_to_be_visible()


def test_an_uncounted_engagement_says_nothing_about_responses(page, base_url):
    """NULL is «nobody counted», which is not «nobody answered» — so the row
    states the kind and stops (docs/adr/0074 §5)."""
    sign_in(page, base_url, SANDRA)
    open_scratch_matter(page, base_url)
    open_panel(page)

    panel(page).locator("[name=audience]").fill("kaubandusvaldkonna töögrupp")
    panel(page).locator("button[type=submit]").click()
    page.wait_for_load_state("networkidle")

    row = chronology(page).locator(
        ".uxtl__item", has=page.locator("text=Kaasamine: kaubandusvaldkonna töögrupp")
    )
    expect(row.first).to_be_visible()
    expect(row.first).not_to_contain_text("Vastuseid")


# -- the provider pointers (2026-09-12) --------------------------------------

SMAILY_URL = "https://sendsmaily.net/api/campaigns/9182"
ALCHEMER_URL = "https://survey.alchemer.eu/s3/7710021/pakendiseadus"


def test_both_provider_links_are_saved_and_read_back_by_their_provider_name(page, base_url):
    """`+ Kaasamine` with a mailing and a questionnaire, end to end.

    The chronology names the tool and not the address. A campaign URL is mostly
    a recipient token; the link is where it goes, and «Smaily» is what a reader
    and a screen reader get.
    """
    sign_in(page, base_url, SANDRA)
    open_scratch_matter(page, base_url)
    open_panel(page)

    audience = "toiduainetööstuse liikmed"
    panel(page).locator("[name=audience]").fill(audience)
    panel(page).locator("[name=smaily_url]").fill(SMAILY_URL)
    panel(page).locator("[name=alchemer_url]").fill(ALCHEMER_URL)
    panel(page).locator("button[type=submit]").click()
    page.wait_for_load_state("networkidle")

    row = chronology(page).locator(".uxtl__item", has_text=f"Kaasamine: {audience}").first
    expect(row).to_be_visible()

    smaily = row.get_by_role("link", name="Smaily")
    alchemer = row.get_by_role("link", name="Alchemer")
    expect(smaily).to_have_attribute("href", SMAILY_URL)
    expect(alchemer).to_have_attribute("href", ALCHEMER_URL)
    expect(smaily).to_have_attribute("rel", "noopener noreferrer")
    # The address is never printed as copy beside the name.
    assert SMAILY_URL not in row.inner_text()


def test_a_link_typed_with_no_audience_is_answered_where_the_answer_belongs(page, base_url):
    """The two changes of this round meeting each other (task §6).

    Somebody pastes a Smaily address, forgets `Keda kaasati`, and saves. Three
    things have to happen and the old behaviour managed none of them: the panel
    stays open, the refusal names the box that is missing, and the cursor goes
    there. The address they typed comes back with it — silently dropping a URL
    somebody pasted is the worst of the available answers.
    """
    sign_in(page, base_url, SANDRA)
    open_scratch_matter(page, base_url)
    open_panel(page)

    panel(page).locator("[name=smaily_url]").fill(SMAILY_URL)
    panel(page).locator("button[type=submit]").click()
    page.wait_for_load_state("networkidle")

    expect(panel(page)).to_be_visible()
    expect(panel(page)).to_contain_text("Kirjuta, keda kaasati")
    expect(panel(page).locator("[name=smaily_url]")).to_have_value(SMAILY_URL)

    focused = page.evaluate("() => document.activeElement && document.activeElement.name")
    assert focused == "audience", f"the cursor went to {focused!r} rather than the empty box"


def test_a_link_that_is_not_a_web_address_is_refused_under_its_own_box(page, base_url):
    """The service's rule, reported where it was typed rather than as a 400."""
    sign_in(page, base_url, SANDRA)
    open_scratch_matter(page, base_url)
    open_panel(page)

    panel(page).locator("[name=audience]").fill("liikmed")
    panel(page).locator("[name=alchemer_url]").fill("javascript:alert(1)")
    panel(page).locator("button[type=submit]").click()
    page.wait_for_load_state("networkidle")

    expect(panel(page)).to_be_visible()
    expect(panel(page)).to_contain_text("Link peab algama")


def test_an_engagement_with_no_links_shows_no_empty_link_row(page, base_url):
    """A record that has neither reads exactly as it read before this round."""
    sign_in(page, base_url, SANDRA)
    open_scratch_matter(page, base_url)
    open_panel(page)

    audience = "linkideta kaasamine"
    panel(page).locator("[name=audience]").fill(audience)
    panel(page).locator("button[type=submit]").click()
    page.wait_for_load_state("networkidle")

    row = chronology(page).locator(".uxtl__item", has_text=f"Kaasamine: {audience}").first
    expect(row).to_be_visible()
    expect(row.locator(".uxtl__links")).to_have_count(0)


def test_a_file_attached_to_lopeta_kaasamine_survives_the_save(page, base_url):
    """The whole round, in a browser: record, wait, finish with the answer attached.

    Only this path exercises the defect. `Lõpeta kaasamine` declares a file
    control, the view bound the form with the POST body alone, and a Django form
    bound without its files sees no upload — so the picker worked, the save
    succeeded, the row re-rendered, and the PDF a member sent in existed nowhere.
    No error, no warning, and nothing in `Dokumendid` to notice it by.

    A server test that posts to the endpoint catches it too and is the faster
    guard; this one is here because the defect lived in the gap between what the
    page offers and what the request carries, and that gap is what a browser
    crosses.
    """
    sign_in(page, base_url, SANDRA)
    open_scratch_matter(page, base_url)

    open_panel(page)
    panel(page).locator("[name=audience]").fill("faili proov")
    panel(page).locator("button[type=submit]").click()
    page.wait_for_load_state("networkidle")

    row = chronology(page).locator(
        ".uxtl__ms-body", has=page.locator(".uxtl__mswhat", has_text="faili proov")
    )
    row.get_by_text("Ootan tagasisidet", exact=True).click()
    row.locator("[data-quickdate]", has_text="1 kuu").click()
    row.get_by_role("button", name="Salvesta ootus").click()
    page.wait_for_load_state("networkidle")

    waiting = chronology(page).locator(
        ".uxtl__ms-body", has=page.locator(".uxtl__mswhat", has_text="faili proov")
    )
    waiting.get_by_text("Lõpeta kaasamine", exact=True).click()
    waiting.locator("[name=feedback_received]").fill("Liidu vastus tuli kirjaga.")
    waiting.locator("input[type=file]").first.set_input_files(
        {
            "name": "liidu-vastuskiri.pdf",
            "mimeType": "application/pdf",
            "buffer": b"%PDF-1.4 liidu vastus",
        }
    )
    waiting.get_by_role("button", name="Salvesta ja lõpeta").click()
    page.wait_for_load_state("networkidle")

    finished = chronology(page).locator(
        ".uxtl__ms-body", has=page.locator(".uxtl__mswhat", has_text="faili proov")
    )
    expect(finished).to_contain_text("Liidu vastus tuli kirjaga.")
    # The round is over, so the control that could upload again is gone.
    expect(finished.get_by_text("Lõpeta kaasamine", exact=True)).to_have_count(0)

    # **The filename arrives on the next render, and that is the swap target's
    # documented shape rather than a second defect.** The completion answers with
    # `engagement_row.html` — the record's own region — and a milestone's files
    # render beside that element, so that a correction cannot move the row or
    # grow a second line in the chronology (`_engagement_row`,
    # `timeline_items.html`). Showing the new file without a reload means swapping
    # that region out of band, which is a presentation change and not this fix.
    page.reload()
    page.wait_for_load_state("networkidle")

    # The evidence, on this round's own chronology item rather than loose on the
    # Matter, and linked to the exact bytes rather than named in a sentence.
    item = chronology(page).locator(
        ".uxtl__item", has=page.locator(".uxtl__mswhat", has_text="faili proov")
    )
    attachment = item.get_by_role("link", name="liidu-vastuskiri.pdf")
    expect(attachment).to_have_count(1)
    assert attachment.first.get_attribute("href"), "the filename is text, not a link to evidence"


def test_finishing_a_round_with_no_file_is_unchanged(page, base_url):
    """The commonest completion of all: the deadline passed and nothing came back.

    Binding the form with its files must not turn an empty picker into a
    refusal — `EngagementFeedbackForm` requires nothing, deliberately, because a
    completion that demanded prose would make «keegi ei vastanud» the one result
    a lawyer could not file (docs/adr/0086 §6).
    """
    sign_in(page, base_url, SANDRA)
    open_scratch_matter(page, base_url)

    open_panel(page)
    panel(page).locator("[name=audience]").fill("tühja vastuse proov")
    panel(page).locator("button[type=submit]").click()
    page.wait_for_load_state("networkidle")

    row = chronology(page).locator(
        ".uxtl__ms-body", has=page.locator(".uxtl__mswhat", has_text="tühja vastuse proov")
    )
    row.get_by_text("Ootan tagasisidet", exact=True).click()
    row.locator("[data-quickdate]", has_text="1 kuu").click()
    row.get_by_role("button", name="Salvesta ootus").click()
    page.wait_for_load_state("networkidle")

    waiting = chronology(page).locator(
        ".uxtl__ms-body", has=page.locator(".uxtl__mswhat", has_text="tühja vastuse proov")
    )
    waiting.get_by_text("Lõpeta kaasamine", exact=True).click()
    waiting.locator("[name=feedback_received]").fill("Keegi ei vastanud.")
    waiting.get_by_role("button", name="Salvesta ja lõpeta").click()
    page.wait_for_load_state("networkidle")

    finished = chronology(page).locator(
        ".uxtl__ms-body", has=page.locator(".uxtl__mswhat", has_text="tühja vastuse proov")
    )
    expect(finished).to_contain_text("Keegi ei vastanud.")
    expect(finished.get_by_text("Lõpeta kaasamine", exact=True)).to_have_count(0)
