"""«Seotud materjalid» in a real browser.

What only a browser can prove here: that the Matter page loads with the
suggestions unopened, that opening them swaps the section in place with the
reasons on the cards, that «Seo teemaga» moves a candidate out of the
suggestions and into `Seotud teemad` on *both* Matters, that «Ei ole seotud»
survives a reload and comes back through «Näita peidetud», that an earlier
opinion and an archive letter become background without anything else
changing, and that a reader sees no controls and no restricted relation
(docs/adr/0061).

Every Matter these tests create carries its own suffix so a suggestion
asserted here is the one this test made, whatever else the seeded world holds
about `pakendiseadus`.
"""

from __future__ import annotations

import re

import pytest
from playwright.sync_api import expect

from app.core.management.commands.seed_e2e_data import (
    ARCHIVE_LETTERS,
    RESTRICTED_TITLE,
    SUBMISSION_TITLE,
)
from e2e.conftest import MARTIN, READER, create_matter, sign_in, sign_out

pytestmark = pytest.mark.e2e

ARCHIVE_LETTER_TITLE = ARCHIVE_LETTERS[0][0]


def section(page):
    return page.locator("#seotud-materjalid")


def open_suggestions(page) -> None:
    section(page).locator("[data-related-suggest]").click()
    expect(page.locator("[data-related-suggestions]")).to_be_visible()


def matter_card(page, title: str):
    return page.locator("[data-related-matter-suggestions] .relatedcard").filter(has_text=title)


def material_card(page, title: str):
    return page.locator("[data-related-material-suggestions] .relatedcard").filter(has_text=title)


def confirmed_row(page, title: str):
    return page.locator("[data-related-confirmed] .factrow").filter(has_text=title)


def background_row(page, title: str):
    return page.locator("[data-related-background] .factrow").filter(has_text=title)


def test_the_matter_page_loads_with_suggestions_unopened(page, base_url):
    sign_in(page, base_url, MARTIN)
    create_matter(page, base_url, "Pakendiseaduse muutmise seaduse eelnõu (seotud A0)")

    expect(section(page)).to_be_visible()
    expect(section(page).get_by_role("heading", name="Seotud materjalid")).to_be_visible()
    expect(section(page).locator("[data-related-suggest]")).to_have_text("Võimalikud seosed")
    expect(page.locator("[data-related-suggestions]")).to_have_count(0)
    # Secondary: below the structured facts, above the chronology.
    order = page.evaluate(
        "() => [...document.querySelectorAll('#seotud-materjalid, #ajajoon, .accordion--timeline')]"
        ".map(node => node.id || node.className)"
    )
    assert order and order[0] == "seotud-materjalid"


def test_opening_suggestions_explains_and_linking_shows_both_sides(page, base_url):
    sign_in(page, base_url, MARTIN)
    title_a = "Pakendiseaduse muutmise seaduse eelnõu (seotud A1)"
    title_b = "Pakendiseaduse ja jäätmeseaduse muutmise seadus (seotud B1)"
    url_a = create_matter(page, base_url, title_a)
    url_b = create_matter(page, base_url, title_b)

    page.goto(url_b)
    open_suggestions(page)
    card = matter_card(page, title_a)
    expect(card).to_be_visible()
    expect(card.locator(".relatedcard__reasons")).to_contain_text("Sama õigusakt: pakendiseadus")
    assert "%" not in card.inner_text()

    card.get_by_role("button", name="Seo teemaga").click()

    expect(page.locator("[data-related-notice]")).to_contain_text("Teemad on seotud.")
    expect(confirmed_row(page, title_a)).to_be_visible()
    expect(matter_card(page, title_a)).to_have_count(0)

    page.goto(url_a)
    expect(confirmed_row(page, title_b)).to_be_visible()
    expect(page.locator("[data-related-suggestions]")).to_have_count(0)


def test_dismissing_hides_across_a_reload_and_restoring_brings_it_back(page, base_url):
    sign_in(page, base_url, MARTIN)
    title_c = "Töölepingu seaduse muutmise seaduse eelnõu (seotud C2)"
    title_d = "Töölepingu seaduse ja töötervishoiu seaduse muutmine (seotud D2)"
    create_matter(page, base_url, title_c)
    url_d = create_matter(page, base_url, title_d)

    page.goto(url_d)
    open_suggestions(page)
    matter_card(page, title_c).get_by_role("button", name="Ei ole seotud").click()
    expect(page.locator("[data-related-notice]")).to_contain_text("Soovitus on peidetud.")
    expect(matter_card(page, title_c)).to_have_count(0)

    page.reload()
    page.wait_for_load_state("networkidle")
    open_suggestions(page)
    expect(matter_card(page, title_c)).to_have_count(0)

    page.locator("[data-related-show-hidden]").click()
    hidden = page.locator("[data-related-hidden] .relatedcard").filter(has_text=title_c)
    expect(hidden).to_be_visible()
    hidden.get_by_role("button", name="Taasta soovitus").click()

    expect(page.locator("[data-related-notice]")).to_contain_text("Soovitus on taastatud.")
    expect(matter_card(page, title_c)).to_be_visible()


def test_an_earlier_opinion_becomes_background_and_stays_where_it_was(page, base_url):
    sign_in(page, base_url, MARTIN)
    url = create_matter(page, base_url, "Pakendiseaduse eelnõu taustmaterjali proov (seotud E3)")

    page.goto(url)
    open_suggestions(page)
    card = material_card(page, SUBMISSION_TITLE)
    expect(card).to_be_visible()
    expect(card.locator(".relatedcard__label")).to_have_text("Varasem arvamus")
    expect(card.locator(".relatedcard__reasons")).to_contain_text("Sama õigusakt: pakendiseadus")
    source_href = card.locator(".relatedcard__title").get_attribute("href") or ""
    assert re.search(r"/teemad/[0-9a-f-]{36}/seisukoht/$", source_href), source_href

    card.get_by_role("button", name="Lisa taustmaterjaliks").click()

    expect(page.locator("[data-related-notice]")).to_contain_text("Taustmaterjal on lisatud.")
    row = background_row(page, SUBMISSION_TITLE)
    expect(row).to_be_visible()
    expect(row.locator(".factrow__kind")).to_contain_text("Arvamus")
    # «Ava» still opens the opinion on the Matter it was sent for.
    assert row.get_by_role("link", name="Ava").get_attribute("href") == source_href
    expect(material_card(page, SUBMISSION_TITLE)).to_have_count(0)

    row.get_by_role("button", name="Eemalda").click()
    expect(page.locator("[data-related-notice]")).to_contain_text("Taustmaterjal on eemaldatud.")
    expect(background_row(page, SUBMISSION_TITLE)).to_have_count(0)


def test_archive_material_opens_through_the_archive_and_files_no_link(page, base_url):
    sign_in(page, base_url, MARTIN)
    title_f = "Näidisseaduse muutmise eelnõu (seotud F4)"
    create_matter(page, base_url, title_f)

    open_suggestions(page)
    card = material_card(page, ARCHIVE_LETTER_TITLE)
    expect(card).to_be_visible()
    expect(card.locator(".relatedcard__label")).to_have_text("Arhiivimaterjal")
    expect(card.locator(".relatedcard__reasons")).to_contain_text("Sama õigusakt: näidisseadus")

    card.get_by_role("button", name="Lisa taustmaterjaliks").click()

    row = background_row(page, ARCHIVE_LETTER_TITLE)
    expect(row).to_be_visible()
    href = row.get_by_role("link", name="Ava").get_attribute("href") or ""
    assert re.fullmatch(r"/haldus/arvamuste-arhiiv/[0-9a-f-]{36}/", href), href
    assert "opinion-archive/" not in page.content()

    # The archive detail page: the letter opens, and it is *not* filed onto F.
    page.goto(f"{base_url}{href}")
    page.wait_for_load_state("networkidle")
    expect(page.get_by_text(ARCHIVE_LETTER_TITLE).first).to_be_visible()
    assert title_f not in page.content()


def test_a_manual_link_to_a_restricted_matter_is_invisible_to_a_reader(page, base_url):
    sign_in(page, base_url, MARTIN)
    title_g = "Käsitsi seotud teema (seotud G5)"
    url_g = create_matter(page, base_url, title_g)

    section(page).get_by_text("Lisa seotud teema").click()
    search = section(page).get_by_label("Otsi teemat")
    search.fill("Konfidentsiaalne")
    search.press("Enter")
    result = page.locator("[data-related-picker-results] .factrow").filter(
        has_text=RESTRICTED_TITLE
    )
    expect(result).to_be_visible()
    result.get_by_role("button", name="Seo teemaga").click()
    expect(confirmed_row(page, RESTRICTED_TITLE)).to_be_visible()

    sign_out(page, base_url)
    sign_in(page, base_url, READER)
    page.goto(url_g)
    page.wait_for_load_state("networkidle")

    expect(section(page)).to_be_visible()
    body = page.content()
    assert RESTRICTED_TITLE not in body
    assert "Seotud teemad" not in body
    for control in ("Seo teemaga", "Lisa seotud teema", "Ei ole seotud"):
        assert control not in body
    # Reading is still allowed: the suggestions open, with no buttons on them.
    open_suggestions(page)
    expect(page.locator("[data-related-suggestions] button")).to_have_count(0)
