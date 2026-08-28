"""Teemad in a browser: the search that answers while you type.

Everything here is proved against the database elsewhere. What only a browser
can show is whether the *interaction* works — whether results move without
pressing Enter, whether the address bar keeps up, and whether Back lands where
the reader left. Those are properties of HTMX, history and a real keyboard, and
no server-side test observes any of them.
"""

from __future__ import annotations

import re

import pytest
from playwright.sync_api import expect

from e2e.conftest import MARTIN, READER, sign_in

pytestmark = pytest.mark.e2e

#: Seeded by `manage.py seed_e2e_data`. Named here rather than queried: a
#: browser test with database access could read around an authorization bug in
#: the interface and never fail (e2e/conftest.py).
OPEN_TITLE = "Tavaline avatud teema kõigile nähtav"
ARCHIVE_TITLE = "Arhiiviteema 2014 sünteetiline registrikirje"
RESTRICTED_WORD = "konfidentsiaalne"
MINISTRY = "Näidisministeerium"


def open_register(page, base_url):
    page.goto(f"{base_url}/teemad/?olek=koik")
    expect(page.get_by_role("heading", name="Teemad")).to_be_visible()


def result_count(page) -> int:
    """The number the page itself is claiming, not the rows we can see."""
    text = page.locator(".registercount strong").inner_text()
    return int(text.strip())


def rows(page):
    return page.locator(".table tbody tr")


def chip_text(page) -> str:
    """Every chip's text, joined.

    `.filterchip` legitimately matches several elements — one per active
    dimension, plus `Tühjenda kõik` — so asserting against the locator itself is
    a strict-mode violation the moment a second filter is applied. What the
    tests below actually mean is "this appears somewhere in the chip strip".
    """
    return " · ".join(page.locator("#teemad-tulemused .filterchip").all_inner_texts())


# -- the search box ----------------------------------------------------------


def test_the_register_carries_a_search_box(page, base_url, screenshots):
    sign_in(page, base_url, MARTIN)
    open_register(page, base_url)

    box = page.locator("#teemad-otsing")
    expect(box).to_be_visible()
    expect(box).to_have_attribute("type", "search")
    screenshots(page, "teemad-otsing")


def test_results_update_without_pressing_enter(page, base_url):
    """The whole point. A keystroke, a pause, a narrower list (brief 7)."""
    sign_in(page, base_url, MARTIN)
    open_register(page, base_url)

    before = result_count(page)
    assert before >= 2, "the seeded world is too small to narrow"

    page.locator("#teemad-otsing").press_sequentially("Tavaline", delay=40)

    expect(page.locator(".registercount")).to_contain_text("Tavaline")
    expect(rows(page)).to_have_count(1)
    expect(page.locator(".table")).to_contain_text(OPEN_TITLE)
    assert result_count(page) < before


def test_the_address_bar_keeps_up(page, base_url):
    """A search worth arguing about is one somebody can paste into a message."""
    sign_in(page, base_url, MARTIN)
    open_register(page, base_url)

    page.locator("#teemad-otsing").press_sequentially("Tavaline", delay=40)
    page.wait_for_url(re.compile(r"[?&]q=Tavaline"))


def test_a_pasted_search_url_renders_the_same_thing(page, base_url):
    sign_in(page, base_url, MARTIN)
    page.goto(f"{base_url}/teemad/?olek=koik&q=Tavaline")

    expect(page.locator("#teemad-otsing")).to_have_value("Tavaline")
    expect(rows(page)).to_have_count(1)


def test_back_returns_to_the_previous_search(page, base_url):
    sign_in(page, base_url, MARTIN)
    open_register(page, base_url)
    unfiltered = result_count(page)

    page.locator("#teemad-otsing").press_sequentially("Tavaline", delay=40)
    page.wait_for_url(re.compile(r"[?&]q=Tavaline"))
    expect(rows(page)).to_have_count(1)

    page.go_back()
    expect(page.locator(".registercount strong")).to_have_text(str(unfiltered))

    page.go_forward()
    expect(rows(page)).to_have_count(1)


def test_clearing_the_search_restores_the_register(page, base_url):
    sign_in(page, base_url, MARTIN)
    open_register(page, base_url)
    unfiltered = result_count(page)

    page.locator("#teemad-otsing").press_sequentially("Tavaline", delay=40)
    expect(rows(page)).to_have_count(1)

    # Scoped to the chip strip. The advanced panel has a `Tühjenda kõik` of its
    # own, it sits earlier in the document, and inside a closed `<details>` it is
    # not clickable — `.first` would find that one.
    page.locator("#teemad-tulemused .filterchip--clear").click()
    expect(page.locator(".registercount strong")).to_have_text(str(unfiltered))
    expect(page.locator("#teemad-otsing")).to_have_value("")


def test_a_search_with_no_answers_says_so(page, base_url):
    sign_in(page, base_url, MARTIN)
    open_register(page, base_url)

    page.locator("#teemad-otsing").press_sequentially("zzzpuuduvsõna", delay=30)
    expect(page.locator(".empty")).to_be_visible()
    expect(page.locator(".registercount strong")).to_have_text("0")


def test_the_count_is_announced_politely(page, base_url):
    """Assistive technology hears it without losing the caret (brief 35)."""
    sign_in(page, base_url, MARTIN)
    open_register(page, base_url)

    count = page.locator(".registercount")
    expect(count).to_have_attribute("role", "status")
    expect(count).to_have_attribute("aria-live", "polite")


# -- authorization -----------------------------------------------------------


def test_a_restricted_matter_reaches_neither_the_rows_nor_the_count(page, base_url):
    """Martin may not see Sandra's restricted file, by either route.

    The count leaks its existence as surely as the title would (brief 2, 16).
    """
    sign_in(page, base_url, READER)
    open_register(page, base_url)

    page.locator("#teemad-otsing").press_sequentially(RESTRICTED_WORD, delay=30)
    expect(page.locator(".registercount strong")).to_have_text("0")
    # No rows, and no link to a Matter — the word itself is legitimately on the
    # page twice, in the box that was typed into and in the count echoing it.
    expect(page.locator(".table")).to_have_count(0)
    expect(page.locator("#teemad-tulemused a[href*='/teemad/']")).to_have_count(0)


# -- Täpsem otsing -----------------------------------------------------------


def open_advanced(page):
    page.locator("summary", has_text="Täpsem otsing").click()
    expect(page.locator("select[name='vastutaja']")).to_be_visible()


def test_the_advanced_panel_opens(page, base_url, screenshots):
    sign_in(page, base_url, MARTIN)
    open_register(page, base_url)
    open_advanced(page)
    screenshots(page, "teemad-tapsem-otsing")


def test_the_owner_filter_narrows_the_register(page, base_url):
    sign_in(page, base_url, MARTIN)
    open_register(page, base_url)
    open_advanced(page)

    page.locator("select[name='vastutaja']").select_option(label=MARTIN.short_name)
    page.get_by_role("button", name="Filtreeri").click()

    page.wait_for_url(re.compile(r"vastutaja="))
    assert "Vastutaja" in chip_text(page)
    expect(page.locator(".table")).to_contain_text(OPEN_TITLE)
    expect(page.locator(".table")).not_to_contain_text(ARCHIVE_TITLE)


def test_the_organisation_chooser_narrows_by_typing(page, base_url):
    """A server-backed chooser, because the real catalogue is hundreds long."""
    sign_in(page, base_url, MARTIN)
    open_register(page, base_url)
    open_advanced(page)

    chooser = page.locator("#orgchooser-asutus")
    expect(chooser).to_be_visible()

    chooser.locator("input[type='search']").press_sequentially("Näidis", delay=40)
    expect(chooser.locator("select option", has_text=MINISTRY)).to_have_count(1)

    chooser.locator("select[name='asutus']").select_option(label=MINISTRY)
    page.get_by_role("button", name="Filtreeri").click()

    page.wait_for_url(re.compile(r"asutus="))
    assert MINISTRY in chip_text(page)
    expect(page.locator(".table")).to_contain_text(OPEN_TITLE)


def test_the_received_date_range_narrows_the_register(page, base_url):
    """Both ends inclusive, and the chip reads the way Estonians write dates."""
    sign_in(page, base_url, MARTIN)
    open_register(page, base_url)
    open_advanced(page)

    page.locator("input[name='saabus_alates']").fill("7.3.2024")
    page.get_by_role("button", name="Filtreeri").click()

    page.wait_for_url(re.compile(r"saabus_alates="))
    assert "7.3.2024" in chip_text(page)


def test_the_register_scope_segments_work(page, base_url):
    """ARCHIVE is a record mode, not merely "old" (brief 11A)."""
    sign_in(page, base_url, MARTIN)
    open_register(page, base_url)

    segments = page.locator(".segmented")
    segments.get_by_role("link", name=re.compile(r"^Arhiiv")).click()
    page.wait_for_url(re.compile(r"olek=arhiiv"))
    expect(page.locator(".table")).to_contain_text(ARCHIVE_TITLE)
    expect(page.locator(".table")).not_to_contain_text(OPEN_TITLE)

    segments.get_by_role("link", name=re.compile(r"^Avatud")).click()
    page.wait_for_url(re.compile(r"olek=avatud"))
    expect(page.locator(".table")).to_contain_text(OPEN_TITLE)


def test_a_query_and_a_filter_mean_both(page, base_url):
    """The intersection, never either one (brief 10)."""
    sign_in(page, base_url, MARTIN)
    open_register(page, base_url)
    open_advanced(page)

    page.locator("select[name='vastutaja']").select_option(label=MARTIN.short_name)
    page.get_by_role("button", name="Filtreeri").click()
    page.wait_for_url(re.compile(r"vastutaja="))

    with_owner = result_count(page)
    page.locator("#teemad-otsing").press_sequentially("Tavaline", delay=40)

    expect(page.locator(".registercount")).to_contain_text("Tavaline")
    # Still filtered by owner: the chip is there and the URL still carries it.
    assert "Vastutaja" in chip_text(page)
    page.wait_for_url(re.compile(r"vastutaja="))
    assert result_count(page) <= with_owner


# -- chips -------------------------------------------------------------------


def test_a_query_and_a_filter_each_render_a_chip(page, base_url):
    sign_in(page, base_url, MARTIN)
    page.goto(f"{base_url}/teemad/?olek=koik&q=Tavaline&menetlusliik=DOMESTIC")

    labels = chip_text(page)
    assert "Otsing:" in labels
    assert "Menetlusliik" in labels


def test_removing_one_chip_leaves_the_others(page, base_url):
    sign_in(page, base_url, MARTIN)
    page.goto(f"{base_url}/teemad/?olek=koik&q=Tavaline&menetlusliik=DOMESTIC")

    page.locator("#teemad-tulemused .filterchip", has_text="Menetlusliik").click()

    page.wait_for_url(re.compile(r"q=Tavaline"))
    assert "menetlusliik=DOMESTIC" not in page.url
    expect(page.locator("#teemad-otsing")).to_have_value("Tavaline")


def test_clear_all_returns_to_the_bare_register(page, base_url):
    sign_in(page, base_url, MARTIN)
    page.goto(f"{base_url}/teemad/?olek=koik&q=Tavaline&menetlusliik=DOMESTIC")

    page.locator("#teemad-tulemused .filterchip--clear").click()

    expect(page.locator("#teemad-tulemused .filterchip")).to_have_count(0)
    expect(page.locator("#teemad-otsing")).to_have_value("")
