"""Jõustumised inside Teemad, in a browser (docs/adr/0158).

The unit tests (tests/test_register_commencements.py) hold the buckets, the
calendar placement and the authorization. What only a browser answers is
whether the switch is reachable from the register's own filter, whether a
calendar day narrows the register when clicked, whether the live search keeps
the view, whether `Tühjenda kõik` really returns to the plain register, and
whether a month grid fits a phone.

The seeded world (`seed_e2e_data._intelligence_world`) gives the open Teema
three commencements — «põhiosa» on 27 September next year, «osad sätted» on
1 January the year after, «rakendusmäärus» in general order — and the
restricted Teema one «konfidentsiaalne jõustumine» on the same 27 September,
which a specialist reads by role (docs/adr/0042) and `READER` may not.
Other files in a shard may add more, so every assertion names its own row.
"""

from __future__ import annotations

from datetime import date

import pytest
from playwright.sync_api import expect

from e2e.conftest import MARTIN, READER, SANDRA, document_overflows, sign_in, wait_for_htmx

pytestmark = pytest.mark.e2e

NEXT_YEAR = date.today().year + 1
LIST = "/teemad/?joustumine=on&olek=koik&vaade=joustumised"
SEPTEMBER = f"/teemad/?joustumine=on&olek=koik&vaade=kalender&kuu={NEXT_YEAR}-09"


def results(page):
    return page.locator("#teemad-tulemused")


def test_the_plain_register_has_no_switch(page, base_url):
    sign_in(page, base_url, MARTIN)
    page.goto(f"{base_url}/teemad/")

    expect(page.locator(".registerview")).to_have_count(0)
    expect(page.locator(".table--register")).to_be_visible()


def test_the_filter_offers_the_switch_and_the_calendar_narrows_by_day(page, base_url):
    sign_in(page, base_url, MARTIN)
    page.goto(f"{base_url}/teemad/?joustumine=on&olek=koik")

    switch = page.locator(".registerview")
    expect(switch).to_be_visible()
    # The Matter table is still what the filter shows by default.
    expect(page.locator(".table--register")).to_be_visible()

    switch.get_by_role("link", name="Jõustumiste kalender").click()
    page.wait_for_load_state("networkidle")
    expect(page.locator(".jcal")).to_be_visible()

    page.goto(f"{base_url}{SEPTEMBER}")
    day = page.locator(".jcal__day", has=page.locator("a.jcal__daynum", has_text="27"))
    expect(day).to_contain_text("põhiosa")

    day.locator("a.jcal__daynum").click()
    page.wait_for_load_state("networkidle")
    assert "vaade=joustumised" in page.url
    assert f"joustub_alates=27.9.{NEXT_YEAR}" in page.url
    expect(results(page)).to_contain_text("põhiosa")
    expect(results(page)).not_to_contain_text("osad sätted")
    expect(page.locator(".filterchip", has_text="Jõustub alates")).to_be_visible()

    page.locator(".filterchip--clear").click()
    page.wait_for_load_state("networkidle")
    assert "vaade=" not in page.url
    expect(page.locator(".registerview")).to_have_count(0)


def test_the_list_buckets_and_precision(page, base_url):
    sign_in(page, base_url, MARTIN)
    page.goto(f"{base_url}{LIST}")

    undated = page.locator(".jbucket", has=page.locator("#jbucket-teadmata"))
    expect(undated).to_contain_text("rakendusmäärus")
    expect(undated).to_contain_text("Jõustub üldises korras")
    expect(results(page)).to_contain_text("osad sätted")
    expect(page.locator(".jcover:not([data-status-scope])")).to_contain_text(
        "Kuupäevi ei tuletata pealkirjast"
    )


def test_a_restricted_commencement_reaches_only_those_who_may_read_it(page, base_url):
    sign_in(page, base_url, SANDRA)
    page.goto(f"{base_url}{SEPTEMBER}")
    expect(results(page)).to_contain_text("konfidentsiaalne jõustumine")

    sign_in(page, base_url, READER)
    page.goto(f"{base_url}{SEPTEMBER}")
    expect(page.locator(".jcal")).to_be_visible()
    expect(results(page)).not_to_contain_text("konfidentsiaalne jõustumine")
    page.goto(f"{base_url}{LIST}")
    expect(results(page)).not_to_contain_text("konfidentsiaalne jõustumine")


def test_the_live_search_keeps_the_view(page, base_url):
    sign_in(page, base_url, MARTIN)
    page.goto(f"{base_url}{LIST}")

    page.locator("#teemad-otsing").fill("pakendiseaduse")
    wait_for_htmx(page)
    page.wait_for_function("() => location.search.includes('q=')")

    assert "vaade=joustumised" in page.url
    expect(page.locator(".registerview")).to_be_visible()
    expect(page.locator(".registerview .segmented__option.is-active")).to_have_text(
        "Jõustumiste loend"
    )


def test_the_calendar_fits_a_phone(page, base_url):
    sign_in(page, base_url, MARTIN)
    page.set_viewport_size({"width": 375, "height": 812})
    page.goto(f"{base_url}{SEPTEMBER}")

    expect(page.locator(".jcal")).to_be_visible()
    assert not document_overflows(page)


def test_the_view_says_which_statuses_it_shows_and_narrows_on_request(page, base_url):
    """No `?olek=`: open and closed files, said in words; the link narrows to
    open files and writes `olek=avatud` into the address (docs/adr/0158 §2)."""
    sign_in(page, base_url, MARTIN)
    page.goto(f"{base_url}/teemad/?joustumine=on&vaade=joustumised")

    scope = page.locator("[data-status-scope]")
    expect(scope).to_contain_text("Kuvatud: avatud ja suletud teemad")
    expect(scope).to_contain_text("jõustumiste vaikimisi valik")
    expect(page.locator('nav[aria-label="Kirje seis"] .is-active')).to_contain_text("Kõik")

    scope.get_by_role("link", name="näita ainult avatud").click()
    page.wait_for_load_state("networkidle")
    assert "olek=avatud" in page.url
    assert "vaade=joustumised" in page.url
    expect(page.locator("[data-status-scope]")).to_contain_text("Kuvatud: ainult avatud teemad")
