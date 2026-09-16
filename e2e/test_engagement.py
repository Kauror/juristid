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
    # Both dates arrive pre-filled and visible: today, and a week out. A reader
    # can see what is about to be saved before saving it, which is the whole
    # difference from the version that stamped a date behind their back.
    assert panel(page).locator("[name=occurred_on]").input_value(), (
        "the engagement date opens empty, so today is being applied out of sight"
    )
    assert panel(page).locator("[name=feedback_deadline]").input_value(), (
        "the reply-by date opens empty, so a round would file as waiting on nothing"
    )
    # And its own save, which commits this operation and nothing else
    # (docs/adr/0075 §2).
    expect(panel(page).locator("button[type=submit]")).to_have_count(1)


def test_the_reply_by_spans_write_into_the_box_beside_them(page, base_url):
    """`1 nädal` · `2 nädalat` · `1 kuu` — chips over the field that is submitted.

    The chip stores nothing of its own: it writes the day into
    `feedback_deadline`, which is what the server reads, and the label then grows
    to carry the date it landed on so nobody sets a collection day they did not
    read. The same contract `Järgmine tegevus`'s quick dates have, on the panel
    that replaced the kind chips (docs/adr/0086 §2).
    """
    sign_in(page, base_url, SANDRA)
    open_scratch_matter(page, base_url)
    open_panel(page)

    field = panel(page).locator("[name=feedback_deadline]")
    default = field.input_value()
    for label in ("1 nädal", "2 nädalat", "1 kuu"):
        expect(panel(page).locator("[data-quickdate]", has_text=label)).to_have_count(1)

    panel(page).locator("[data-quickdate]", has_text="1 kuu").click()

    assert field.input_value() != default, "the span wrote nothing into the box"
    chosen = panel(page).locator("[data-quickdate].is-selected")
    expect(chosen).to_have_count(1)
    expect(chosen).to_contain_text("1 kuu →")


def test_two_saves_write_the_note_and_the_engagement_separately(page, base_url):
    """Two intentions, two saves, and the chronology shows both.

    This reverses what the composer's single save proved. A note and a
    consultation are different things somebody chose to record, and the surface
    now asks which before it asks anything else (docs/adr/0075 §2).
    """
    sign_in(page, base_url, SANDRA)
    open_scratch_matter(page, base_url)

    open_composer(page)
    page.locator("#lisa-marge .composer__body").fill("Küsisin liikmetelt tagasisidet.")
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
    # The round is waiting, because the panel's reply-by date defaults to a week
    # out and nothing here cleared it (docs/adr/0086 §2, §3).
    expect(chronology(page)).to_contain_text("Ootame tagasisidet kuni")
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
