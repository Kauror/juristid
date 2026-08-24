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

from app.core.management.commands.seed_e2e_data import RESTRICTED_TITLE
from e2e.conftest import SANDRA, sign_in


def open_kaasamine(page):
    """Open the Kaasamine accordion, which is closed on arrival.

    A Matter that nobody has consulted anybody about costs one quiet line now,
    not a labelled section announcing an absence — so every test that works
    inside it has to open it first (Teema redesign §14, §24).
    """
    section = page.locator("#kaasamine")
    if section.get_attribute("open") is None:
        section.locator(".accordion__head").click()
    return section


def open_scratch_matter(page, base_url: str) -> None:
    """Sandra's restricted Matter — writable by her, and never screenshotted."""
    page.goto(f"{base_url}/teemad/?olek=koik&q=Konfidentsiaalne")
    page.wait_for_load_state("networkidle")
    page.get_by_role("link", name=RESTRICTED_TITLE, exact=False).first.click()
    page.wait_for_load_state("networkidle")


def add_form(page):
    """The add form, not a row's edit form; both carry the same field names."""
    return page.locator("#kaasamine form[data-engagement-add]")


def test_the_kaasamine_section_is_on_the_matter_page(page, base_url):
    sign_in(page, base_url, SANDRA)
    open_scratch_matter(page, base_url)

    section = open_kaasamine(page)
    expect(section).to_be_visible()
    expect(section.get_by_role("heading", name="Kaasamine")).to_be_visible()
    # Collapsed until asked for: the section is read far more often than written.
    expect(section.get_by_text("+ Lisa kaasamine")).to_be_visible()
    expect(add_form(page).locator('input[name="title"]')).to_be_hidden()


def test_an_engagement_can_be_added_and_then_corrected(page, base_url):
    sign_in(page, base_url, SANDRA)
    open_scratch_matter(page, base_url)

    open_kaasamine(page).get_by_text("+ Lisa kaasamine").click()
    expect(add_form(page).locator('input[name="title"]')).to_be_visible()

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
    section = open_kaasamine(page)
    expect(section.get_by_text("Liikmete kaasamiskutse", exact=True)).to_be_visible()
    expect(section.get_by_text("15.9.2026")).to_be_visible()
    expect(section.get_by_text("www.koda.ee")).to_be_visible()

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
        page.locator("#kaasamine").get_by_text("Liikmete kaasamiskutse — parandatud")
    ).to_be_visible()
    assert page.locator("#kaasamine .factrow").count() == before


def test_an_engagement_link_never_opens_without_noopener(page, base_url):
    """A link to an external campaign is still a link somebody else controls."""
    sign_in(page, base_url, SANDRA)
    open_scratch_matter(page, base_url)

    open_kaasamine(page).get_by_text("+ Lisa kaasamine").click()
    add_form(page).locator('input[name="title"]').fill("Väline küsitlus")
    add_form(page).locator('input[name="url"]').fill("https://survey.example.invalid/s/1")
    add_form(page).get_by_role("button", name="Lisa kaasamine").click()

    link = page.locator("#kaasamine").get_by_role("link", name="Väline küsitlus")
    expect(link).to_be_visible()
    expect(link).to_have_attribute("rel", re.compile("noopener"))
    expect(link).to_have_attribute("rel", re.compile("noreferrer"))
    expect(link).to_have_attribute("target", "_blank")
