"""`+ Lisa · Muu` in a browser (docs/adr/0097, amendment of 2026-10-15).

The fifth `+ Lisa` choice opens from the keyboard, refuses a save without a
description while keeping what was typed, and saves a description with a link
and two files as one «Muu» row in `Teema käik` — the link named by its host, the
files linked — with the files under `Dokumendid`. Nothing about the Matter's work
changes: `PRAEGUNE TEGEVUS` reads as it did. Its `Muuda` corrects the link.

State is read off the page only (e2e/conftest.py).
"""

from __future__ import annotations

import pytest
from playwright.sync_api import expect

from e2e.conftest import (
    SANDRA,
    create_matter,
    open_add_panel,
    open_kaik_row,
    sign_in,
    wait_for_htmx,
)
from e2e.titles import unique_title

pytestmark = pytest.mark.e2e

PDF = b"%PDF-1.4 synthetic supporting material"


def _save(page, form) -> int:
    with page.expect_response(
        lambda r: r.url.endswith("/lisa/muu/") and r.request.method == "POST"
    ) as answer:
        form.get_by_role("button", name="Salvesta", exact=True).click()
    wait_for_htmx(page)
    return answer.value.status


def test_muu_opens_from_the_keyboard_and_saves_a_supporting_entry(page, base_url):
    sign_in(page, base_url, SANDRA)
    create_matter(page, base_url, unique_title("Muu materjal"))
    work_before = page.locator("#praegune-tegevus").inner_text()

    # Keyboard: the family first, then the `Muu` radio with Space.
    open_add_panel(page, "lisa-marge")
    radio = page.locator("#marge-muu-valik")
    radio.focus()
    page.keyboard.press("Space")
    form = page.locator("#marge-muu form")
    expect(form).to_be_visible()
    expect(radio).to_be_focused()
    expect(form.locator('[name="description"]')).to_have_attribute(
        "placeholder", "Kirjelda, mida lisad ja miks see on teema jaoks oluline."
    )

    # A link without a description: the browser's own `required` check stops the
    # submit before any request (the server refusal is tests/test_lisa_muu.py),
    # and what was typed stays in the form.
    form.locator('[name="url"]').fill("https://www.example.ee/taust")
    posts: list[str] = []
    page.on("request", lambda r: posts.append(r.url) if r.method == "POST" else None)
    form.get_by_role("button", name="Salvesta", exact=True).click()
    description = form.locator('[name="description"]')
    assert description.evaluate("el => el.validity.valueMissing") is True
    assert not [u for u in posts if u.endswith("/lisa/muu/")]
    expect(form.locator('[name="url"]')).to_have_value("https://www.example.ee/taust")

    # The real save: description, link, two files.
    form.locator('[name="description"]').fill("Ministeeriumi taustaanalüüs eelnõu kohta.")
    form.locator('input[type="file"]').set_input_files(
        [
            {"name": "taust-1.pdf", "mimeType": "application/pdf", "buffer": PDF + b"1"},
            {"name": "taust-2.pdf", "mimeType": "application/pdf", "buffer": PDF + b"2"},
        ]
    )
    assert _save(page, form) == 200

    page.reload()
    page.wait_for_load_state("networkidle")
    row = page.locator("article.uxtl__item", has_text="Ministeeriumi taustaanalüüs")
    expect(row).to_have_count(1)
    expect(row.locator(".uxtl__kind", has_text="Muu")).to_have_count(1)
    link = row.locator("a.uxtl__link")
    expect(link).to_have_attribute("href", "https://www.example.ee/taust")
    expect(link).to_contain_text("example.ee")
    expect(link).to_have_attribute("target", "_blank")
    expect(row.locator("a.uxtl__file", has_text="taust-1.pdf")).to_have_count(1)
    expect(row.locator("a.uxtl__file", has_text="taust-2.pdf")).to_have_count(1)
    # No task, no stage: the current-work zone reads as it did.
    assert page.locator("#praegune-tegevus").inner_text() == work_before

    # The files are documents of the Teema.
    page.goto(page.url.rstrip("/") + "/dokumendid/")
    page.wait_for_load_state("networkidle")
    expect(page.get_by_text("taust-1.pdf").first).to_be_visible()
    expect(page.get_by_text("taust-2.pdf").first).to_be_visible()


def test_muuda_corrects_the_link_of_a_muu_entry(page, base_url):
    sign_in(page, base_url, SANDRA)
    create_matter(page, base_url, unique_title("Muu lingi parandus"))
    open_add_panel(page, "marge-muu")
    form = page.locator("#marge-muu form")
    form.locator('[name="description"]').fill("Viide liidu kommentaarile.")
    form.locator('[name="url"]').fill("https://vana.example.ee/kommentaar")
    assert _save(page, form) == 200
    page.reload()
    page.wait_for_load_state("networkidle")

    row = page.locator("article.uxtl__item", has_text="Viide liidu kommentaarile.")
    open_kaik_row(row)
    row.get_by_role("button", name="Muuda").click()
    wait_for_htmx(page)
    url_box = row.locator('input[name="url"]')
    expect(url_box).to_have_value("https://vana.example.ee/kommentaar")
    url_box.fill("https://uus.example.ee/kommentaar")
    with page.expect_response(
        lambda r: "/sissekanne/" in r.url and r.request.method == "POST"
    ) as answer:
        row.get_by_role("button", name="Salvesta").click()
    assert answer.value.status == 200
    wait_for_htmx(page)

    expect(row.locator("a.uxtl__link")).to_have_attribute(
        "href", "https://uus.example.ee/kommentaar"
    )
    expect(row.locator("a.uxtl__link")).to_contain_text("uus.example.ee")
