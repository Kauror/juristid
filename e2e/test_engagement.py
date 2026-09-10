"""`Kaasamine` in a real browser: record an engagement, then correct it.

The two things only a browser can check here are that the disclosures actually
open — the section is collapsed by default so a Matter page that is read costs
the height of its rows and nothing more — and that the HTMX swap puts the new
row on the page without a reload.

**These write to the restricted Matter, not the ordinary one.** The screenshot
suite opens `OPEN_TITLE`, and a section that grew while these ran would make
that baseline depend on test order. The seeded engagement the baseline shows
lives on `OPEN_TITLE`; everything written here happens somewhere the camera
never points.
"""

from __future__ import annotations

import re

from playwright.sync_api import expect

from app.core.management.commands.seed_e2e_data import ARCHIVE_TITLE, RESTRICTED_TITLE
from e2e.conftest import SANDRA, sign_in


def open_kaasamine(page):
    """The Kaasamine section, which no longer opens because it is never shut.

    The 2026-09 refinement made it a section of the facts panel rather than an
    accordion of its own: it is a dated fact about the file like the deadlines
    above it, and it shows its rows without being asked
    (docs/matter-page-refinement.md).

    Kept as a function, and kept under this name, so every test below still
    reads as "work inside Kaasamine" and the change is stated once rather than
    twenty times.
    """
    return page.locator("#kaasamine")


def composer(page):
    """The `+ Lisa kaasamine` disclosure — now the only way to the add form."""
    return page.locator("#kaasamine [data-engagement-composer]")


def title_box(page):
    return add_form(page).locator('input[name="title"]')


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

    Nothing in the browser suite writes an engagement here, and nothing needs
    to: these two tests only open a section and look (Kaasamine one-click §18).
    """
    page.goto(f"{base_url}/teemad/?olek=koik&q=Arhiiviteema")
    page.wait_for_load_state("networkidle")
    page.get_by_role("link", name=ARCHIVE_TITLE, exact=False).first.click()
    page.wait_for_load_state("networkidle")


def add_form(page):
    """The add form, not a row's edit form; both carry the same field names."""
    return page.locator("#kaasamine form[data-engagement-add]")


def test_one_click_opens_the_form_when_nothing_is_recorded(page, base_url):
    """The primary regression. Two clicks used to be needed for the first record.

    It is still one, and the section around it no longer costs a click of its
    own: the rows are visible on arrival and `+ Lisa kaasamine` is the single
    gesture between a reader and the form.
    """
    sign_in(page, base_url, SANDRA)
    open_empty_matter(page, base_url)

    section = page.locator("#kaasamine")
    expect(section).to_be_visible()
    expect(section.get_by_role("heading", name="Kaasamine")).to_be_visible()
    assert section.get_attribute("data-engagement-count") == "0"
    expect(title_box(page)).to_be_hidden()

    composer(page).locator("summary").click()

    expect(title_box(page)).to_be_visible()


def test_the_keyboard_opens_the_form(page, base_url):
    """Tab to the control, press Enter, and the form is there (§15)."""
    sign_in(page, base_url, SANDRA)
    open_empty_matter(page, base_url)

    trigger = composer(page).locator("summary")
    trigger.focus()
    expect(trigger).to_be_focused()
    page.keyboard.press("Enter")

    expect(title_box(page)).to_be_visible()


def test_an_engagement_can_be_added_and_then_corrected(page, base_url):
    sign_in(page, base_url, SANDRA)
    open_scratch_matter(page, base_url)

    # The header action rather than the section, because this test is about the
    # save and not about the empty state: `+ Lisa` opens the composer whether
    # the scratch Matter still holds nothing or already holds what an earlier
    # run put there.
    composer(page).locator("summary").click()
    expect(title_box(page)).to_be_visible()

    # The three approved options. `WEB_CALL` is still a valid stored value and
    # every historical row carrying it still reads; the creation control does
    # not offer it (Teema redesign §14).
    add_form(page).locator('select[name="kind"]').select_option("SURVEY")
    add_form(page).locator('input[name="title"]').fill("Liikmete kaasamiskutse")
    add_form(page).locator('input[name="url"]').fill("https://www.koda.ee/kaasamine/naidis")
    add_form(page).locator('input[name="occurred_on"]').fill("15.9.2026")
    add_form(page).locator('textarea[name="note"]').fill("Vastuseid ootame septembrini.")
    add_form(page).get_by_role("button", name="Lisa kaasamine").click()

    # The swap replaces the overview column; the row is there without a reload.
    # Scoped to the row, not to the section. The collapsed line summarises the
    # latest engagement — type, title and date — so a section-wide text locator
    # now matches the summary as well as the row it summarises.
    section = open_kaasamine(page)
    row = section.locator(".factrow").first
    expect(row.get_by_text("Liikmete kaasamiskutse", exact=True)).to_be_visible()
    expect(row.get_by_text("15.9.2026")).to_be_visible()
    expect(row.get_by_text("www.koda.ee")).to_be_visible()

    # And correcting it edits the same record rather than adding a second.
    # Scoped to the row itself: `has=` on a section-rooted locator matched the
    # add form as readily as the edit one, which is how this first picked an
    # empty field and reported the wrong value.
    before = section.locator(".factrow").count()
    row = section.locator(".factrow").first
    row.get_by_text("Muuda").click()
    row_form = row.locator("form")
    title = row_form.locator('input[name="title"]')
    expect(title).to_have_value("Liikmete kaasamiskutse")
    title.fill("Liikmete kaasamiskutse — parandatud")
    row_form.get_by_role("button", name="Salvesta").click()

    expect(
        page.locator("#kaasamine .factrow").first.get_by_text("Liikmete kaasamiskutse — parandatud")
    ).to_be_visible()
    assert page.locator("#kaasamine .factrow").count() == before


def test_an_engagement_link_never_opens_without_noopener(page, base_url):
    """A link to an external campaign is still a link somebody else controls."""
    sign_in(page, base_url, SANDRA)
    open_scratch_matter(page, base_url)

    composer(page).locator("summary").click()
    expect(title_box(page)).to_be_visible()
    add_form(page).locator('input[name="title"]').fill("Väline küsitlus")
    add_form(page).locator('input[name="url"]').fill("https://survey.example.invalid/s/1")
    add_form(page).get_by_role("button", name="Lisa kaasamine").click()

    link = page.locator("#kaasamine").get_by_role("link", name="Väline küsitlus")
    expect(link).to_be_visible()
    expect(link).to_have_attribute("rel", re.compile("noopener"))
    expect(link).to_have_attribute("rel", re.compile("noreferrer"))
    expect(link).to_have_attribute("target", "_blank")


# ---------------------------------------------------------------------------
# With records on the section
#
# The scratch Matter starts empty and the tests above fill it, so these make
# sure of a record themselves rather than depending on the file's order. A
# browser test that only passes as part of a full run is a browser test nobody
# can reproduce while fixing it.
# ---------------------------------------------------------------------------


def with_one_record(page, base_url: str):
    """Open the scratch Matter, having made sure it holds at least one row."""
    open_scratch_matter(page, base_url)
    section = page.locator("#kaasamine")
    if section.get_attribute("data-engagement-count") != "0":
        return section

    open_kaasamine(page)
    title_box(page).fill("Olemasolev kaasamiskutse")
    add_form(page).get_by_role("button", name="Lisa kaasamine").click()
    expect(page.locator("#kaasamine .factrow").first).to_be_visible()
    open_scratch_matter(page, base_url)
    return page.locator("#kaasamine")


def test_the_records_are_readable_without_opening_anything(page, base_url):
    """The records are what a reader came for, and they no longer cost a click.

    This used to assert that opening the section showed the records rather than
    the form. The section does not open any more — it is a section of the facts
    panel — so what is asserted is the same thing one step earlier: the rows are
    on the page, and the form still waits to be asked for (§19).
    """
    sign_in(page, base_url, SANDRA)
    section = with_one_record(page, base_url)

    expect(section.locator(".factrow").first).to_be_visible()
    # Here, and shut. Nothing auto-opens once there is something to read.
    expect(composer(page)).to_have_count(1)
    expect(title_box(page)).to_be_hidden()

    section.get_by_text("+ Lisa kaasamine").click()
    expect(title_box(page)).to_be_visible()


def test_the_add_action_opens_the_form_with_records_present(page, base_url):
    """One action, and the form is ready to type into (§20)."""
    sign_in(page, base_url, SANDRA)
    with_one_record(page, base_url)

    composer(page).locator("summary").click()

    expect(title_box(page)).to_be_visible()
    # An explicit Add may take the focus (§14).
    expect(add_form(page).locator('select[name="kind"]')).to_be_focused()


def test_the_add_action_is_operable_from_the_keyboard(page, base_url):
    """Reachable by Tab, and Enter on it opens what it names (§15)."""
    sign_in(page, base_url, SANDRA)
    with_one_record(page, base_url)

    composer(page).locator("summary").focus()
    expect(composer(page).locator("summary")).to_be_focused()
    page.keyboard.press("Enter")

    expect(title_box(page)).to_be_visible()


def test_a_saved_record_stays_visible_and_the_composer_closes(page, base_url):
    """The reader must see what they just made, not an emptied form (§21)."""
    sign_in(page, base_url, SANDRA)
    with_one_record(page, base_url)
    composer(page).locator("summary").click()

    title_box(page).fill("Teine kaasamiskutse")
    add_form(page).get_by_role("button", name="Lisa kaasamine").click()

    # Any row, not the first one. The list is newest-dated-first and an undated
    # record sorts below every dated one, so `.first` asserted the ordering
    # rather than the save — and reported a save that had in fact happened as a
    # record that never arrived.
    expect(
        page.locator("#kaasamine .factrow").get_by_text("Teine kaasamiskutse", exact=True).first
    ).to_be_visible()
    expect(composer(page)).to_have_count(1)
    expect(title_box(page)).to_be_hidden()

    # And another one can be added straight away.
    composer(page).locator("summary").click()
    expect(title_box(page)).to_be_visible()
    expect(title_box(page)).to_have_value("")


def test_a_refused_save_keeps_both_open_with_the_reason_and_the_values(page, base_url):
    """Never make somebody reopen a disclosure to find out why a save failed (§22).

    The date, because it is the one field the browser will not police on its way
    out: `required` stops an empty title in the browser, so a server refusal a
    person can actually reach is an unreadable date.
    """
    sign_in(page, base_url, SANDRA)
    with_one_record(page, base_url)
    composer(page).locator("summary").click()

    title_box(page).fill("Vigane kuupäev")
    add_form(page).locator('input[name="occurred_on"]').fill("32.13.2026")
    add_form(page).get_by_role("button", name="Lisa kaasamine").click()

    expect(add_form(page).locator(".field__error")).to_be_visible()
    assert composer(page).get_attribute("open") is not None
    expect(title_box(page)).to_be_visible()
    expect(title_box(page)).to_have_value("Vigane kuupäev")
    expect(add_form(page).locator('input[name="occurred_on"]')).to_have_value("32.13.2026")
