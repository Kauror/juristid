"""The opinion archive in a browser.

The database suite already proves the boundary from every angle it can reach.
What only a browser proves is the part a database test cannot: that an
administrator can *get to* a held letter through the interface and read it in
the tab, and that a specialist following the same URL is refused rather than
shown a smaller list.

That distinction is the whole reason the archive has its own boundary. The
corpus is real outgoing correspondence with no Matter to inherit a restriction
from, so the refusal has to be the same in the list, the detail page, the header
figures and the file itself — and four surfaces agreeing is exactly the sort of
claim that is easy to believe and hard to verify without opening them.
"""

from __future__ import annotations

import pytest
from playwright.sync_api import expect

from app.core.management.commands.seed_e2e_data import ARCHIVE_LETTERS
from e2e.conftest import ADMIN, MARTIN, sign_in

pytestmark = pytest.mark.e2e

LINKED_TITLE = ARCHIVE_LETTERS[0][1]
UNLINKED_TITLE = ARCHIVE_LETTERS[1][1]
LINKED_SHA = ARCHIVE_LETTERS[0][0]

ARCHIVE_PATH = "/haldus/arvamuste-arhiiv/"


def open_archive(page, base_url):
    page.goto(f"{base_url}{ARCHIVE_PATH}")
    expect(page.get_by_role("heading", name="Arvamuste arhiiv")).to_be_visible()


# -- the boundary ------------------------------------------------------------


def test_a_specialist_following_the_url_is_refused(page, base_url):
    """Refused, not shown an empty archive.

    An empty list would tell a reader the corpus is empty, which is a different
    and untrue statement about what Koda holds.
    """
    sign_in(page, base_url, MARTIN)
    response = page.goto(f"{base_url}{ARCHIVE_PATH}")
    assert response is not None
    assert response.status == 403


def test_an_administrator_reaches_the_archive_from_the_queue(page, base_url):
    """Through the interface, not by knowing the URL.

    The archive was unreachable from anywhere before this stage, which is most
    of why nobody could read the letters.
    """
    sign_in(page, base_url, ADMIN)
    page.goto(f"{base_url}/haldus/arvamuste-ulevaatus/")
    page.get_by_role("link", name="Ava arhiiv").click()
    expect(page.get_by_role("heading", name="Arvamuste arhiiv")).to_be_visible()


# -- finding a letter --------------------------------------------------------


def test_the_coverage_strip_precedes_the_search_box(page, base_url, screenshots):
    """Two thirds of this corpus has no Matter, and the page says so first."""
    sign_in(page, base_url, ADMIN)
    open_archive(page, base_url)
    screenshots(page, "arvamuste-arhiiv")

    context = page.locator(".pagehead__context").first
    expect(context).to_contain_text("kirja")
    expect(context).to_contain_text("teemaga seotud")


def test_a_letter_is_found_by_its_title(page, base_url):
    sign_in(page, base_url, ADMIN)
    open_archive(page, base_url)

    page.get_by_label("Otsi arhiivist").fill("näidisseaduse")
    page.get_by_role("button", name="Otsi arhiivist").click()

    expect(page.get_by_role("link", name=LINKED_TITLE)).to_be_visible()
    expect(page.get_by_role("link", name=UNLINKED_TITLE)).to_have_count(0)


def test_a_letter_is_found_by_its_contents(page, base_url):
    """The reason the second pass and the text extraction exist at all."""
    sign_in(page, base_url, ADMIN)
    open_archive(page, base_url)

    page.get_by_label("Otsi arhiivist").fill("eelnõu")
    page.get_by_role("button", name="Otsi arhiivist").click()
    expect(page.get_by_role("link", name=LINKED_TITLE)).to_be_visible()


def test_a_pasted_hash_finds_the_letter_it_names(page, base_url):
    sign_in(page, base_url, ADMIN)
    open_archive(page, base_url)

    page.get_by_label("Otsi arhiivist").fill(LINKED_SHA)
    page.get_by_role("button", name="Otsi arhiivist").click()
    expect(page.get_by_role("link", name=LINKED_TITLE)).to_be_visible()


def test_an_overlong_query_says_it_was_refused(page, base_url):
    sign_in(page, base_url, ADMIN)
    page.goto(f"{base_url}{ARCHIVE_PATH}?q={'a' * 501}")
    expect(page.locator(".notice")).to_contain_text("tähemärki")
    expect(page.locator(".empty")).to_have_count(0)


# -- reading one --------------------------------------------------------------


def open_letter(page, base_url, title):
    open_archive(page, base_url)
    page.get_by_role("link", name=title).first.click()
    expect(page.get_by_role("heading", name=title)).to_be_visible()


def test_the_detail_page_says_what_the_letter_is_tied_to(page, base_url, screenshots):
    sign_in(page, base_url, ADMIN)
    open_letter(page, base_url, LINKED_TITLE)
    screenshots(page, "arvamuste-arhiiv-kiri")

    expect(page.get_by_role("heading", name="Seotud teemad")).to_be_visible()
    expect(page.get_by_role("heading", name="Esinemised arhiivis")).to_be_visible()


def test_the_link_form_says_it_is_not_an_opinion(page, base_url):
    """A reviewer who thinks they recorded a sent opinion has been misled."""
    sign_in(page, base_url, ADMIN)
    open_letter(page, base_url, UNLINKED_TITLE)

    expect(page.locator(".form__hint")).to_contain_text("ei loo arvamust")


def test_the_letter_opens_in_the_browser(page, base_url):
    sign_in(page, base_url, ADMIN)
    open_letter(page, base_url, LINKED_TITLE)

    link = page.get_by_role("link", name="Ava kiri")
    href = link.get_attribute("href") or ""
    assert "/fail/" in href, href

    response = page.request.get(f"{base_url}{href}")
    assert response.status == 200
    assert response.headers["content-type"].startswith("application/pdf")
    assert response.headers["x-content-type-options"] == "nosniff"
    assert response.headers["content-disposition"].startswith("inline")
    assert "script-src 'none'" in response.headers["content-security-policy"]


def test_the_download_offers_the_hash_rather_than_the_archive_name(page, base_url):
    """The ZIP's names carry recipients and subjects; a header is the wrong
    place for a reader to learn who a letter was to."""
    sign_in(page, base_url, ADMIN)
    open_letter(page, base_url, LINKED_TITLE)

    href = page.get_by_role("link", name="Laadi alla").get_attribute("href") or ""
    response = page.request.get(f"{base_url}{href}")
    disposition = response.headers["content-disposition"]
    assert disposition.startswith("attachment")
    assert LINKED_SHA[:16] in disposition
    assert "Näidisministeerium" not in disposition
