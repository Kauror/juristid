"""The historical corpus, in a browser.

Everything about the importer is proved elsewhere, against a synthetic archive
built at test time. What only a browser shows is whether the result is *usable*:
whether a 2019 case file reads as a case file, whether the page's own XML stays
a download rather than becoming markup, whether a restricted Matter's history is
as unreachable as the Matter, and whether an operator can settle a match in one
place with the evidence in front of them (Stage-2D brief 79).

Deliberately few tests, for the same reason as the rest of the browser suite:
these exist for failures that live between layers, and visual assertions cost
more than they catch (docs/adr/0010).
"""

from __future__ import annotations

import pytest
from playwright.sync_api import expect

from app.core.management.commands.seed_e2e_data import (
    CANDIDATE_PAGE_TITLE,
    HISTORICAL_FILENAME,
    HISTORICAL_INTRODUCTION,
    HISTORICAL_PAGE_TITLE,
    OPEN_TITLE,
    RESTRICTED_TITLE,
)
from e2e.conftest import ADMIN, MARTIN, READER, SANDRA, sign_in, sign_out

pytestmark = pytest.mark.e2e


def open_the_matter(page, base_url: str, title: str) -> None:
    page.goto(f"{base_url}/teemad/")
    page.get_by_role("link", name=title).first.click()
    page.wait_for_load_state("networkidle")


def test_the_overview_says_the_history_exists_without_becoming_it(page, base_url, screenshots):
    """Three numbers and a way in. A lawyer reads Ülevaade in three seconds."""
    sign_in(page, base_url, MARTIN)
    open_the_matter(page, base_url, OPEN_TITLE)

    section = page.locator("section[aria-labelledby='ajalooline-materjal-heading']")
    expect(section).to_be_visible()
    expect(section).to_contain_text("OneNote")
    # The narrative itself is on its own page, not spilled onto the overview.
    expect(section).not_to_contain_text(HISTORICAL_INTRODUCTION)

    screenshots(page, "historical-overview")
    section.get_by_role("link", name="Vaata ajaloolist materjali").click()
    expect(page.get_by_role("heading", name=HISTORICAL_PAGE_TITLE)).to_be_visible()


def test_the_case_file_keeps_the_file_in_the_sentence_that_introduces_it(
    page, base_url, screenshots
):
    """The whole argument for this rendering, as one assertion.

    "Ettepaneku eestikeelne variant" means nothing three paragraphs above the
    PDF it introduces, and an alphabetical attachment list at the bottom would
    throw the relationship away (Stage-2D brief 22, 31).
    """
    sign_in(page, base_url, MARTIN)
    open_the_matter(page, base_url, OPEN_TITLE)
    page.get_by_role("link", name="Vaata ajaloolist materjali").click()

    body = page.locator(".casefile")
    expect(body).to_be_visible()
    text = body.inner_text()
    assert text.index(HISTORICAL_INTRODUCTION) < text.index(HISTORICAL_FILENAME)

    screenshots(page, "historical-case-file")


def test_the_pages_own_xml_is_a_download_and_never_markup(page, base_url):
    """OneNote's markup is a stored file like any other: offered, not rendered."""
    sign_in(page, base_url, MARTIN)
    open_the_matter(page, base_url, OPEN_TITLE)
    page.get_by_role("link", name="Vaata ajaloolist materjali").click()

    assert "one:Page" not in page.content()

    page.get_by_role("group").filter(has_text="Tehnilised andmed").click()
    download_link = page.get_by_role("link", name="Laadi alla lähte-XML")
    if download_link.count():
        with page.expect_download() as download:
            download_link.click()
        assert download.value.suggested_filename.endswith(".xml")


def test_the_historical_material_is_listed_under_dokumendid(page, base_url):
    sign_in(page, base_url, MARTIN)
    open_the_matter(page, base_url, OPEN_TITLE)
    page.get_by_role("link", name="Dokumendid").click()

    section = page.get_by_role("region", name="Ajalooline OneNote")
    expect(section).to_be_visible()
    expect(section.get_by_role("link", name=HISTORICAL_PAGE_TITLE)).to_be_visible()


def test_a_restricted_matters_history_is_as_unreachable_as_the_matter(page, base_url):
    """Old material is not less confidential for being old (Stage-2D brief 60)."""
    sign_in(page, base_url, SANDRA)
    page.goto(f"{base_url}/teemad/")
    expect(page.get_by_role("link", name=RESTRICTED_TITLE).first).to_be_visible()
    sign_out(page, base_url)

    sign_in(page, base_url, READER)
    page.goto(f"{base_url}/teemad/")
    expect(page.get_by_role("link", name=RESTRICTED_TITLE)).to_have_count(0)

    # And nothing in the register hints that a hidden file has history.
    page.goto(f"{base_url}/otsing/?q=konfidentsiaalne")
    expect(page.get_by_text(RESTRICTED_TITLE)).to_have_count(0)


def test_an_operator_settles_a_match_with_the_evidence_in_front_of_them(
    page, base_url, screenshots
):
    """Both sides on one card, and the buttons beside them.

    A reviewer who has to open two other pages to decide will decide badly, or
    not at all — 535 times (Stage-2D brief 39, 40).
    """
    sign_in(page, base_url, ADMIN)
    page.goto(f"{base_url}/haldus/ajaloo-ulevaatus/")

    card = page.locator(".reviewcard").filter(has_text=CANDIDATE_PAGE_TITLE)
    expect(card).to_be_visible()
    expect(card).to_contain_text("Alkoholiaktsiisi eelnõu")  # the register side
    expect(card).to_contain_text("2019_44")
    screenshots(page, "historical-review-queue")

    card.get_by_role("button", name="Taust").click()
    page.wait_for_load_state("networkidle")

    settled = page.locator(".reviewcard").filter(has_text=CANDIDATE_PAGE_TITLE)
    expect(settled).to_have_count(0)


def test_the_review_queue_is_not_in_a_lawyers_way(page, base_url):
    """Migration work, under Admin. 535 pending decisions is not a feature.

    Unlinked *and* unreachable: this route can create Matters, so hiding it from
    the navigation is not the control.
    """
    sign_in(page, base_url, MARTIN)
    expect(page.get_by_role("navigation").get_by_text("ülevaatus")).to_have_count(0)

    response = page.goto(f"{base_url}/haldus/ajaloo-ulevaatus/")
    assert response is not None and response.status == 404
