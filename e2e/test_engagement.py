"""`Kaasamine` in a real browser: record an engagement, then correct it.

The two things only a browser can check here are that the disclosures actually
open — the section is collapsed by default so a Matter page that is read costs
the height of its rows and nothing more — and that the HTMX swap puts the new
row on the page without a reload.
"""

from __future__ import annotations

import re

from playwright.sync_api import expect

from e2e.conftest import SANDRA, sign_in
from e2e.test_ui_shell import open_first_matter


def test_the_kaasamine_section_is_on_the_matter_page(page, base_url):
    sign_in(page, base_url, SANDRA)
    open_first_matter(page, base_url)

    section = page.locator("#kaasamine")
    expect(section).to_be_visible()
    expect(section.get_by_role("heading", name="Kaasamine")).to_be_visible()
    # Collapsed until asked for: the section is read far more often than written.
    expect(section.get_by_text("+ Lisa kaasamine")).to_be_visible()
    expect(section.locator('input[name="title"]')).to_be_hidden()


def test_an_engagement_can_be_added_and_then_corrected(page, base_url):
    sign_in(page, base_url, SANDRA)
    open_first_matter(page, base_url)

    section = page.locator("#kaasamine")
    section.get_by_text("+ Lisa kaasamine").click()
    expect(section.locator('input[name="title"]')).to_be_visible()

    section.locator('select[name="kind"]').select_option("WEB_CALL")
    section.locator('input[name="title"]').fill("Liikmete kaasamiskutse")
    section.locator('input[name="url"]').fill("https://www.koda.ee/kaasamine/naidis")
    section.locator('input[name="occurred_on"]').fill("2026-09-15")
    section.locator('textarea[name="note"]').fill("Vastuseid ootame septembrini.")
    section.get_by_role("button", name="Lisa kaasamine").click()

    # The swap replaces the overview column; the row is there without a reload.
    row = page.locator("#kaasamine").get_by_text("Liikmete kaasamiskutse")
    expect(row).to_be_visible()
    expect(page.locator("#kaasamine").get_by_text("15.09.2026")).to_be_visible()
    expect(page.locator("#kaasamine").get_by_text("www.koda.ee")).to_be_visible()

    # And correcting it edits the same record rather than adding a second.
    page.locator("#kaasamine").get_by_text("Muuda").first.click()
    title = page.locator("#kaasamine").locator('input[name="title"]').first
    expect(title).to_have_value("Liikmete kaasamiskutse")
    title.fill("Liikmete kaasamiskutse — parandatud")
    page.locator("#kaasamine").get_by_role("button", name="Salvesta").first.click()

    expect(
        page.locator("#kaasamine").get_by_text("Liikmete kaasamiskutse — parandatud")
    ).to_be_visible()
    assert page.locator("#kaasamine .factrow").count() == 1


def test_an_engagement_link_never_opens_without_noopener(page, base_url):
    """A link to an external campaign is still a link somebody else controls."""
    sign_in(page, base_url, SANDRA)
    open_first_matter(page, base_url)

    section = page.locator("#kaasamine")
    section.get_by_text("+ Lisa kaasamine").click()
    section.locator('input[name="title"]').fill("Väline küsitlus")
    section.locator('input[name="url"]').fill("https://survey.example.invalid/s/1")
    section.get_by_role("button", name="Lisa kaasamine").click()

    link = page.locator("#kaasamine").get_by_role("link", name="Väline küsitlus")
    expect(link).to_be_visible()
    expect(link).to_have_attribute("rel", re.compile("noopener"))
    expect(link).to_have_attribute("rel", re.compile("noreferrer"))
    expect(link).to_have_attribute("target", "_blank")
