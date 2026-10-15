"""`+ Lisa · Muu` in a browser (docs/adr/0097, amendment of 2026-10-15).

The fifth `+ Lisa` choice opens from the keyboard, refuses a save without a
description while keeping what was typed, and saves a description, a stated past
day (`Millal?`, empty until filled), a link and two files as one «Muu» row in
`Teema käik` — dated and ordered by that day, the link named by its host, the
files linked — with the files under `Dokumendid`. Nothing about the Matter's work
changes: `PRAEGUNE TEGEVUS` reads as it did. Its `Muuda` corrects the link.

State is read off the page only (e2e/conftest.py).
"""

from __future__ import annotations

from datetime import date, timedelta

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


def _et(on: date) -> str:
    return f"{on.day}.{on.month}.{on.year}"


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

    # The real save: description, a past day, link, two files. `Millal?` starts empty.
    millal = form.locator('[name="occurred_on"]')
    expect(millal).to_have_value("")
    past = date.today() - timedelta(days=20)
    millal.fill(_et(past))
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
    # Dated by the stated day.
    expect(row.locator("time.uxtl__time").first).to_have_text(_et(past))
    link = row.locator("a.uxtl__link")
    expect(link).to_have_attribute("href", "https://www.example.ee/taust")
    expect(link).to_contain_text("example.ee")
    expect(link).to_have_attribute("target", "_blank")
    expect(row.locator("a.uxtl__file", has_text="taust-1.pdf")).to_have_count(1)
    expect(row.locator("a.uxtl__file", has_text="taust-2.pdf")).to_have_count(1)
    # No task, no stage: the current-work zone reads as it did.
    assert page.locator("#praegune-tegevus").inner_text() == work_before

    # Ordered by the stated day: a second `Muu` dated today goes above it, because
    # the chronology runs newest first — whichever was saved last.
    open_add_panel(page, "marge-muu")
    form = page.locator("#marge-muu form")
    form.locator('[name="description"]').fill("Tänane lisamaterjal.")
    form.locator('[name="occurred_on"]').fill(_et(date.today()))
    assert _save(page, form) == 200
    page.reload()
    page.wait_for_load_state("networkidle")
    # `textContent`: rows render as closed accordions, so `inner_text` would
    # leave out the description the row is matched by.
    texts = page.locator("#ajalugu-loend article.uxtl__item").evaluate_all(
        "els => els.map(e => e.textContent)"
    )
    today_at = next(i for i, t in enumerate(texts) if "Tänane lisamaterjal." in t)
    past_at = next(i for i, t in enumerate(texts) if "Ministeeriumi taustaanalüüs" in t)
    assert today_at < past_at, texts
    days = page.locator("#ajalugu-loend article.uxtl__item time.uxtl__time").evaluate_all(
        "els => els.map(e => e.getAttribute('datetime').slice(0, 10))"
    )
    assert days == sorted(days, reverse=True), days
    assert past.isoformat() in days and date.today().isoformat() in days, days

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
    form.locator('[name="occurred_on"]').fill(_et(date.today() - timedelta(days=3)))
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
