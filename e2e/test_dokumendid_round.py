"""Dokumendid round, 2026-10-26, in a real browser.

The list: one way to the document page (the title), «↑ Lae üles», and the
row's actions side by side. The document page: what the file is, then «Ava
fail» or «Laadi alla» as the primary action, the copy icon, and «Muuda
dokumenti» folded away until asked for — opening by itself when a correction
was refused. Nothing scrolls sideways on a phone.
"""

from __future__ import annotations

import re

import pytest
from playwright.sync_api import expect

from e2e.conftest import MARTIN, READER, create_matter, open_disclosure, sign_in, sign_out
from e2e.test_document_file_links import PDF, _documents, _upload
from e2e.titles import unique_title

pytestmark = pytest.mark.e2e

DOCX = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
LONG = "Valitsuse_kommenteeritud_ettepanek_" + "pikk_failinimi_" * 6 + "lopp.pdf"


def _detail_of(page, base_url, title):
    row = page.locator(".doctable tbody tr", has_text=title)
    href = row.locator(".table__title a").first.get_attribute("href")
    page.goto(f"{base_url}{href}")
    page.wait_for_load_state("networkidle")


@pytest.fixture
def filed(page, base_url):
    sign_in(page, base_url, MARTIN)
    url = create_matter(page, base_url, unique_title("Dokumendid voor"))
    _upload(page, url, "kiri.pdf", "application/pdf", PDF, "Ministeeriumi kiri")
    _upload(page, url, "eelnou.docx", DOCX, b"PK\x03\x04 synthetic", "Eelnou")
    _upload(page, url, LONG, "application/pdf", PDF)
    _documents(page, url)
    return url


def test_the_list_is_file_first_with_one_action_cluster(page, base_url, filed):
    page.set_viewport_size({"width": 1440, "height": 900})
    expect(page.get_by_role("link", name="Vaata sisu")).to_have_count(0)
    upload = page.get_by_role("button", name="↑ Lae üles")
    expect(upload).to_be_visible()
    expect(page.get_by_role("button", name=re.compile("Lae dokument"))).to_have_count(0)
    upload.click()
    expect(page.locator("#lae-dokument")).to_be_visible()

    row = page.locator(".doctable tbody tr", has_text="Ministeeriumi kiri")
    cluster = row.locator(".doctable__actions")
    ava = cluster.get_by_role("link", name=re.compile("^Ava Ministeeriumi kiri"))
    download = cluster.get_by_role("link", name="Tõmba alla Ministeeriumi kiri")
    menu = cluster.locator("summary", has_text="⋯")
    for control in (ava, download, menu):
        expect(control).to_be_visible()
    # Side by side: the three sit within one short horizontal run.
    boxes = [c.bounding_box() for c in (ava, download, menu)]
    assert boxes[2]["x"] + boxes[2]["width"] - boxes[0]["x"] < 140
    assert max(b["y"] for b in boxes) - min(b["y"] for b in boxes) < 12
    # The download icon is a real target and shows focus.
    assert download.bounding_box()["width"] >= 24
    download.focus()
    page.keyboard.press("Shift+Tab")
    page.keyboard.press("Tab")
    expect(download).to_be_focused()
    assert download.evaluate("a => getComputedStyle(a).outlineStyle") != "none"

    # The title is the way to the document page.
    _detail_of(page, base_url, "Ministeeriumi kiri")
    expect(page.get_by_role("heading", level=1)).to_have_text("Ministeeriumi kiri")


def test_a_pdf_page_opens_the_file_in_a_new_tab_and_copies_its_address(page, base_url, filed):
    page.context.grant_permissions(["clipboard-read", "clipboard-write"])
    _detail_of(page, base_url, "Ministeeriumi kiri")
    actions = page.get_by_role("group", name="Faili toimingud")
    opener = actions.get_by_role("link", name="Ava fail — avaneb uues aknas", exact=True)
    expect(opener).to_have_class(re.compile("button--primary"))
    expect(actions.get_by_role("link", name="Laadi alla", exact=True)).to_be_visible()

    # Headless Chromium has no PDF viewer to land in, so the new tab is
    # asserted by its attributes; a text file's popup is followed end to end
    # in e2e/test_document_file_links.py.
    expect(opener).to_have_attribute("target", "_blank")
    expect(opener).to_have_attribute("href", re.compile(r"/ava/$"))

    # By its hook: the accessible name is what the press changes.
    copy = actions.locator("button[data-copy-icon]")
    expect(copy).to_have_accessible_name("Kopeeri link: kiri.pdf")
    copy.focus()
    page.keyboard.press("Shift+Tab")
    page.keyboard.press("Tab")
    expect(copy).to_be_focused()
    assert copy.evaluate("b => getComputedStyle(b).outlineStyle") != "none"
    before = copy.bounding_box()
    page.keyboard.press("Enter")
    expect(copy).to_have_accessible_name("Link kopeeritud")
    assert copy.bounding_box() == before  # the feedback moves nothing
    assert page.evaluate("navigator.clipboard.readText()") == opener.evaluate("a => a.href")


def test_a_word_files_primary_action_downloads(page, base_url, filed):
    _detail_of(page, base_url, "Eelnou")
    actions = page.get_by_role("group", name="Faili toimingud")

    expect(actions.get_by_role("link", name=re.compile("Ava"))).to_have_count(0)
    primary = actions.get_by_role("link", name="Laadi alla", exact=True)
    expect(primary).to_have_class(re.compile("button--primary"))
    with page.expect_download() as download_info:
        primary.click()
    assert download_info.value.suggested_filename == "eelnou.docx"


def test_muuda_dokumenti_is_folded_until_asked_for(page, base_url, filed):
    _detail_of(page, base_url, "Ministeeriumi kiri")
    manage = page.locator("details#muuda-dokumenti")
    title_box = manage.locator("input[name=title]")

    expect(manage).not_to_have_attribute("open", "")
    expect(title_box).to_be_hidden()
    manage.locator(":scope > summary").click()
    expect(title_box).to_be_visible()
    for label in ("Salvesta uus versioon", "Salvesta pealkiri", "Salvesta liik"):
        expect(manage.get_by_role("button", name=label)).to_have_count(1)
    manage.get_by_text("Eemalda dokument").click()
    expect(manage.get_by_role("button", name="Eemalda", exact=True)).to_be_visible()

    # A refused correction lands with its form open and the message above it.
    page.evaluate("document.querySelector('#muuda-dokumenti input[name=title]').required = false")
    title_box.fill("   ")
    manage.get_by_role("button", name="Salvesta pealkiri").click()
    page.wait_for_url(re.compile(r"\?muuda=pealkiri$"))
    expect(page.locator("details#muuda-dokumenti")).to_have_attribute("open", "")
    expect(page.locator(".messages")).to_be_visible()
    # Inside the window, not merely in the DOM (review of #554): the reason
    # for the refusal is the first thing read.
    box = page.locator(".messages").bounding_box()
    assert box is not None and 0 <= box["y"] and box["y"] + box["height"] <= 900
    expect(page.locator("details#muuda-dokumenti input[name=title]")).to_be_visible()


def test_a_long_filename_wraps_and_nothing_scrolls_sideways(page, base_url, filed):
    _documents(page, filed)
    for width in (1440, 1024, 375):
        page.set_viewport_size({"width": width, "height": 900})
        _documents(page, filed)
        assert (
            page.evaluate(
                "document.documentElement.scrollWidth - document.documentElement.clientWidth"
            )
            <= 0
        ), width
        _detail_of(page, base_url, LONG[:20])
        open_disclosure(page.locator("details#dokumendi-andmed"))
        assert (
            page.evaluate(
                "document.documentElement.scrollWidth - document.documentElement.clientWidth"
            )
            <= 0
        ), width
        name = page.locator("#originaal-heading ~ dl dd").first
        label = page.locator("#originaal-heading ~ dl dt").nth(1)
        a, b = name.bounding_box(), label.bounding_box()
        # The filename's cell never runs under the next label.
        overlaps = not (
            a["x"] + a["width"] <= b["x"]
            or b["x"] + b["width"] <= a["x"]
            or a["y"] + a["height"] <= b["y"]
            or b["y"] + b["height"] <= a["y"]
        )
        assert not overlaps, width


def test_a_reader_gets_the_file_and_no_correction_area(page, base_url, filed):
    detail_href = (
        page.locator(".doctable tbody tr", has_text="Ministeeriumi kiri")
        .locator(".table__title a")
        .first.get_attribute("href")
    )
    sign_out(page, base_url)
    sign_in(page, base_url, READER)
    page.goto(f"{base_url}{detail_href}?muuda=pealkiri")

    expect(page.get_by_role("group", name="Faili toimingud")).to_be_visible()
    expect(page.locator("#muuda-dokumenti")).to_have_count(0)
    expect(page.get_by_text("Muuda dokumenti")).to_have_count(0)
