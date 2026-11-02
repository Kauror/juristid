"""«Majanduspoliitika ülevaade»: the download is the report last previewed (review of PR #585).

A change after the preview — a box ticked off, a Selgitus typed, the topics
moved, the signers changed — and «Laadi Wordi ülevaade» pressed at once does
not download a report nobody saw: the page comes back with the preview brought
up to date and says so, the reader's draft intact, and the next press
downloads exactly it. An untouched first page downloads at once.

The topics are hand-added through «Lisa teema», so the test needs no recorded
event in the period; the period is October 2026, whose action plan exists.
"""

from __future__ import annotations

import io

import pytest
from docx import Document
from playwright.sync_api import expect

from e2e.conftest import MARTIN, create_matter, sign_in, wait_for_htmx
from e2e.titles import unique_title

pytestmark = pytest.mark.e2e

BUILDER = "/statistika/ulevaade/?algus=1.10.2026&lopp=31.10.2026"
STALE = "Ülevaadet on pärast eelvaadet muudetud."


def _composer_with_two_topics(page, base_url):
    sign_in(page, base_url, MARTIN)
    first = unique_title("Ülevaate esimene")
    second = unique_title("Ülevaate teine")
    for title in (first, second):
        create_matter(page, base_url, title)
    page.goto(f"{base_url}{BUILDER}")
    page.wait_for_load_state("networkidle")
    for title in (first, second):
        search = page.locator("input[name=otsing]")
        search.fill(title)
        wait_for_htmx(page)
        results = page.locator("#report-search-results .reportsearch__row", has_text=title)
        results.get_by_role("button").click()
        wait_for_htmx(page)
        expect(page.locator("[data-report-block]", has_text=title)).to_have_count(1)
    return first, second


def _block(page, title):
    return page.locator("[data-report-block]", has_text=title)


def _download(page):
    with page.expect_download() as download_info:
        page.get_by_role("button", name="Laadi Wordi ülevaade").click()
    return Document(io.BytesIO(open(download_info.value.path(), "rb").read()))


def _refused(page):
    page.get_by_role("button", name="Laadi Wordi ülevaade").click()
    page.wait_for_load_state("networkidle")
    expect(page.get_by_role("alert")).to_contain_text(STALE)


def _report_titles(document):
    return [row.cells[0].text for row in document.tables[1].rows[1:]]


def test_an_untouched_preview_downloads_at_once(page, base_url):
    first, second = _composer_with_two_topics(page, base_url)

    document = _download(page)

    assert _report_titles(document) == [first, second]


def test_a_checkbox_changed_after_the_preview_is_refused_then_downloads(page, base_url):
    first, second = _composer_with_two_topics(page, base_url)
    _block(page, second).locator("input[name=kaasa]").uncheck()

    _refused(page)

    expect(_block(page, second).locator("input[name=kaasa]")).not_to_be_checked()
    assert _report_titles(_download(page)) == [first]


def test_a_selgitus_typed_after_the_preview_is_refused_then_downloads(page, base_url):
    first, _ = _composer_with_two_topics(page, base_url)
    box = _block(page, first).locator("textarea")
    box.fill("Kirjutatud pärast eelvaadet.")

    _refused(page)

    expect(_block(page, first).locator("textarea")).to_have_value("Kirjutatud pärast eelvaadet.")
    expect(page.locator("[data-report-preview]")).to_contain_text("Kirjutatud pärast eelvaadet.")
    document = _download(page)
    assert document.tables[1].rows[1].cells[3].text == "Kirjutatud pärast eelvaadet."


def test_topics_moved_without_a_preview_are_refused_then_download(page, base_url):
    first, second = _composer_with_two_topics(page, base_url)
    # What a drag does to the form before its own refresh arrives: the second
    # block, and with it its `teema` input, now comes first.
    page.locator("[data-report-order]").evaluate(
        "list => list.insertBefore(list.lastElementChild, list.firstElementChild)"
    )

    _refused(page)

    expect(page.locator("[data-report-block]").first).to_contain_text(second)
    assert _report_titles(_download(page)) == [second, first]


def test_signers_changed_after_the_preview_are_refused_then_download(page, base_url):
    _composer_with_two_topics(page, base_url)
    page.locator("input[name=allkirjastajad]").fill("Mari Maasikas")

    _refused(page)

    expect(page.locator("input[name=allkirjastajad]")).to_have_value("Mari Maasikas")
    document = _download(page)
    assert [p.text for p in document.paragraphs if p.text][-1] == "Mari Maasikas"


def test_a_refreshed_preview_downloads_what_it_shows(page, base_url):
    first, _ = _composer_with_two_topics(page, base_url)
    _block(page, first).locator("textarea").fill("Vaadatud tekst.")
    page.get_by_role("button", name="Uuenda eelvaadet").click()
    wait_for_htmx(page)
    expect(page.locator("[data-report-preview]")).to_contain_text("Vaadatud tekst.")

    document = _download(page)

    assert document.tables[1].rows[1].cells[3].text == "Vaadatud tekst."
