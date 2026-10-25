"""Opening, downloading and sharing a document, in a real browser (QoL 2026-10-25).

- a previewable file's «Ava» opens a new tab on the inline route, and a Word
  file's «Ava» saves it — decided by `app/documents/inline.py`;
- «Kopeeri link» on the document's page puts the absolute address of that exact
  version on the clipboard;
- Dokumendid's «Lae dokument» takes one dropped file, keeps its `Roll` and
  `Pealkiri`, and a second drop replaces the first;
- search offers the file beside its result, never inside the result's link.
"""

from __future__ import annotations

import re

import pytest
from playwright.sync_api import expect

from e2e.conftest import MARTIN, create_matter, sign_in
from e2e.titles import unique_title

pytestmark = pytest.mark.e2e

PDF = b"%PDF-1.4\n1 0 obj << /Type /Catalog >> endobj\ntrailer << /Root 1 0 R >>\n%%EOF\n"

DROP = """([selector, files]) => {
  const target = document.querySelector(selector);
  const transfer = new DataTransfer();
  files.forEach(([name, type, body]) => transfer.items.add(new File([body], name, { type })));
  const init = { bubbles: true, cancelable: true, dataTransfer: transfer };
  target.dispatchEvent(new DragEvent("dragover", init));
  const drop = new DragEvent("drop", init);
  target.dispatchEvent(drop);
  return drop.defaultPrevented;
}"""


def _documents(page, url: str) -> None:
    page.goto(f"{url}dokumendid/")
    page.wait_for_load_state("networkidle")


def _upload(page, url: str, name: str, mime: str, body: bytes, title: str = "") -> None:
    _documents(page, url)
    page.evaluate("document.getElementById('lae-dokument').hidden = false")
    form = page.locator("#lae-dokument form")
    form.locator("input[name=upload]").set_input_files(
        {"name": name, "mimeType": mime, "buffer": body}
    )
    if title:
        form.locator("input[name=title]").fill(title)
    with page.expect_navigation():
        form.get_by_role("button", name="Salvesta", exact=True).click()


def test_a_text_file_opens_in_a_new_tab_and_a_word_file_downloads(page, base_url, tmp_path):
    sign_in(page, base_url, MARTIN)
    url = create_matter(page, base_url, unique_title("Faili avamine"))
    _upload(page, url, "markmed.txt", "text/plain", b"Sunteetilised markmed")
    _upload(
        page,
        url,
        "eelnou.docx",
        "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        b"PK\x03\x04 synthetic",
    )
    _documents(page, url)

    text = page.get_by_role("link", name="Ava markmed.txt", exact=True)
    expect(text).to_have_attribute("target", "_blank")
    expect(text).to_have_attribute("rel", "noopener noreferrer")
    with page.expect_popup() as popup_info:
        text.click()
    popup = popup_info.value
    popup.wait_for_load_state()
    assert re.search(r"/tõend/[0-9a-f-]{36}/ava/$|/t%C3%B5end/[0-9a-f-]{36}/ava/$", popup.url)
    expect(popup.locator("body")).to_contain_text("Sunteetilised markmed")
    popup.close()

    word = page.get_by_role("link", name="Ava eelnou.docx", exact=True)
    assert word.get_attribute("target") is None
    with page.expect_download() as download_info:
        word.click()
    assert download_info.value.suggested_filename == "eelnou.docx"


def test_copy_link_puts_the_versions_absolute_address_on_the_clipboard(page, base_url):
    page.context.grant_permissions(["clipboard-read", "clipboard-write"])
    sign_in(page, base_url, MARTIN)
    url = create_matter(page, base_url, unique_title("Kopeeri link"))
    _upload(page, url, "kiri.pdf", "application/pdf", PDF, "Ministeeriumi kiri")
    _documents(page, url)
    # Inside the row's closed `⋯` menu: followed by its address, not clicked.
    detail = page.locator("a.opinionmenu__item", has_text="Dokumendi leht").first
    page.goto(f"{base_url}{detail.get_attribute('href')}")
    page.wait_for_load_state("networkidle")

    actions = page.get_by_role("group", name="Faili toimingud")
    expected = actions.get_by_role("link", name="Ava", exact=True).evaluate("a => a.href")
    # By its hook, not its name: the name is what the click changes.
    button = actions.locator("button[data-copy-href]")
    expect(button).to_have_accessible_name("Kopeeri link")
    button.click()

    expect(button).to_have_text("Kopeeritud")
    copied = page.evaluate("navigator.clipboard.readText()")
    assert copied == expected
    assert copied.startswith(base_url) and copied.endswith("/ava/")


def test_dokumendid_takes_one_dropped_file_with_its_role_and_title(page, base_url):
    sign_in(page, base_url, MARTIN)
    url = create_matter(page, base_url, unique_title("Dokumendi lohistamine"))
    _documents(page, url)
    page.locator("[data-reveals='lae-dokument']").first.click()
    zone = "#lae-dokument [data-filedrop]"

    assert page.evaluate(DROP, [zone, [["esimene.pdf", "application/pdf", "%PDF-1.4 a"]]])
    assert page.evaluate(DROP, [zone, [["teine.pdf", "application/pdf", "%PDF-1.4 b"]]])

    form = page.locator("#lae-dokument form")
    expect(form.locator("[data-filedrop-text]")).to_have_text("teine.pdf")
    names = form.locator("input[name=upload]").evaluate("i => [...i.files].map(f => f.name)")
    assert names == ["teine.pdf"], "a second drop replaces the first: one file, one document"
    expect(form.locator("[data-upload-queue]")).to_be_hidden()

    form.locator("input[name=title]").fill("Lohistatud kiri")
    with page.expect_navigation():
        form.get_by_role("button", name="Salvesta", exact=True).click()
    _documents(page, url)
    expect(page.locator("table.doctable tbody tr", has_text="Lohistatud kiri")).to_have_count(1)


def test_search_offers_the_file_beside_its_result(page, base_url):
    sign_in(page, base_url, MARTIN)
    word = "Toominguotsing"
    url = create_matter(page, base_url, unique_title("Otsingu fail"))
    _upload(page, url, "toom.pdf", "application/pdf", PDF, f"{word} kiri")

    page.goto(f"{base_url}/otsing/?q={word}")
    page.wait_for_load_state("networkidle")

    row = page.locator(".resultrow", has=page.locator(".resultrow__file")).first
    expect(row.locator("a.result")).to_have_count(1)
    expect(row.locator("a a")).to_have_count(0)
    file = row.get_by_role("link", name="Ava fail toom.pdf")
    expect(file).to_have_attribute("target", "_blank")
    expect(file).to_have_attribute("href", re.compile(r"/ava/$"))
    expect(row.locator("a.result")).to_have_attribute(
        "href", re.compile(r"^/dokumendid/[0-9a-f-]{36}/$")
    )
