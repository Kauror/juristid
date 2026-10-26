"""The owner's acceptance fixes of 2026-10-19, in a browser (docs/adr/0172).

* `+ Lisa · Muu` opens with `Millal?` at today, and a page left open from an
  earlier day opens it at today again — a day somebody picked is kept;
* a saved `Muu` is in `Tegevused` at once, without a reload: at the head of its
  day, above «Teema loodud», its closed line saying what it is about;
* an `Oluline tähtaeg` and a `Töövõit` appear on `Menetluse kulg` as events —
  their kind in words, their title, their day — beside the procedure's stages,
  which keep their names; on a phone the rail scrolls itself, never the page.

State is read off the page only (e2e/conftest.py).
"""

from __future__ import annotations

from datetime import date, timedelta

import pytest
from playwright.sync_api import expect

from e2e.conftest import SANDRA, create_matter, open_add_panel, sign_in, wait_for_htmx
from e2e.titles import unique_title

pytestmark = pytest.mark.e2e


def _et(on: date) -> str:
    return f"{on.day}.{on.month}.{on.year}"


def _save(page, form, path: str) -> int:
    with page.expect_response(
        lambda r: r.url.endswith(path) and r.request.method == "POST"
    ) as answer:
        form.get_by_role("button", name="Salvesta", exact=True).click()
    wait_for_htmx(page)
    return answer.value.status


def test_muu_starts_today_and_reads_at_the_head_of_its_day(page, base_url):
    sign_in(page, base_url, SANDRA)
    create_matter(page, base_url, unique_title("Muu tänane päev"))

    open_add_panel(page, "marge-muu")
    form = page.locator("#marge-muu form")
    millal = form.locator('[name="occurred_on"]')
    expect(millal).to_have_value(_et(date.today()))

    # A page drawn yesterday: the box still holds what it was drawn with, so
    # opening the panel again renews it to today.
    millal.evaluate("box => { box.defaultValue = '1.1.2026'; box.value = '1.1.2026'; }")
    open_add_panel(page, "marge-tahtaeg")
    open_add_panel(page, "marge-muu")
    expect(millal).to_have_value(_et(date.today()))

    # The panel was left open overnight and only the description is typed into:
    # focusing anything in the form renews the day before it can be saved.
    millal.evaluate("box => { box.defaultValue = '1.1.2026'; box.value = '1.1.2026'; }")
    form.locator('[name="description"]').focus()
    expect(millal).to_have_value(_et(date.today()))

    # A day somebody chose is theirs: reopening does not touch it.
    earlier = date.today() - timedelta(days=3)
    millal.fill(_et(earlier))
    open_add_panel(page, "marge-tahtaeg")
    open_add_panel(page, "marge-muu")
    expect(millal).to_have_value(_et(earlier))

    millal.fill(_et(date.today()))
    form.locator('[name="description"]').fill("Ministeeriumi kiri selgitas eelnõu ajakava.")
    assert _save(page, form, "/lisa/muu/") == 200

    # No reload: the swapped-in list already has it, first of today's rows.
    rows = page.locator("#ajalugu-loend article.uxtl__item")
    muu = rows.filter(has_text="Ministeeriumi kiri selgitas")
    expect(muu).to_have_count(1)
    expect(muu.locator(".uxtl__gist")).to_be_visible()
    expect(muu.locator(".uxtl__gist")).to_have_text("Ministeeriumi kiri selgitas eelnõu ajakava.")
    texts = rows.evaluate_all("els => els.map(e => e.textContent)")
    muu_at = next(i for i, t in enumerate(texts) if "Ministeeriumi kiri selgitas" in t)
    created_at = next(i for i, t in enumerate(texts) if "Teema loodud" in t)
    assert muu_at < created_at, texts

    page.reload()
    page.wait_for_load_state("networkidle")
    expect(
        page.locator("#ajalugu-loend article.uxtl__item").filter(has_text="Ministeeriumi kiri")
    ).to_have_count(1)


def test_a_deadline_and_a_victory_are_events_on_the_rail(page, base_url):
    sign_in(page, base_url, SANDRA)
    create_matter(page, base_url, unique_title("Sündmused menetluse kulul"))
    strip = page.locator(".tl-strip")
    phases_before = strip.locator(".tl-step--phase .tl-step__what").all_inner_texts()

    open_add_panel(page, "marge-tahtaeg")
    form = page.locator("#marge-tahtaeg form")
    form.locator('[name="deadline_title"]').fill("isiklik põhjus")
    form.locator('[name="deadline_date"]').fill(_et(date.today() + timedelta(days=2)))
    assert _save(page, form, "/lisa/tahtaeg/") == 200

    open_add_panel(page, "marge-toovoit")
    form = page.locator("#marge-toovoit form")
    form.locator('[name="victory_change"]').fill("Ministeerium loobus aruandluskohustusest")
    form.locator('[name="victory_date"]').fill(_et(date.today()))
    assert _save(page, form, "/lisa/toovoit/") == 200

    strip = page.locator(".tl-strip")
    deadline = strip.locator(".tl-step--event", has_text="isiklik põhjus")
    victory = strip.locator(".tl-step--event", has_text="Ministeerium loobus")
    expect(deadline).to_have_count(1)
    expect(victory).to_have_count(1)
    # Their kind in words, not colour; their own day.
    # An upcoming deadline's kind is no longer written (docs/adr/0100,
    # amendment of 2026-10-26); «Töövõit» still is.
    expect(deadline.locator(".tl-step__type")).to_have_count(0)
    expect(deadline).to_contain_text(_et(date.today() + timedelta(days=2)))
    expect(victory.locator(".tl-step__type")).to_have_text("Töövõit")
    expect(victory).to_contain_text(_et(date.today()))
    # The stages are the stages they were, in the same order.
    assert strip.locator(".tl-step--phase .tl-step__what").all_inner_texts() == phases_before

    # On a phone the rail scrolls itself and the page does not.
    page.set_viewport_size({"width": 375, "height": 812})
    page.reload()
    page.wait_for_load_state("networkidle")
    widths = page.evaluate("() => [document.documentElement.scrollWidth, window.innerWidth]")
    assert widths[0] <= widths[1], widths
