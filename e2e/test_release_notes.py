"""`/uuendused/` in a real browser: reached from the footer, opened by hand.

Everything about *what* the page says is proved against the source in
`tests/test_release_notes.py`. What only a browser can show is whether the thing
works as a disclosure: whether the newest day really is expanded on arrival,
whether an older one opens when somebody activates it, whether the keyboard gets
there, and — the claim that decides whether this page needs any code at all —
whether all of that still happens with JavaScript switched off.

The last one is not a theoretical nicety. This repository has two accordion
implementations already; the argument for adding no third is that the browser
does it. A test that drove the page with scripting on would never notice the day
somebody wired one up anyway.
"""

from __future__ import annotations

import pytest
from playwright.sync_api import expect

from e2e.conftest import SANDRA, sign_in

pytestmark = pytest.mark.e2e

URL = "/uuendused/"
DAY = ".relnotes__day"


def days(page):
    return page.locator(DAY)


def open_from_the_footer(page, base_url: str):
    sign_in(page, base_url, SANDRA)
    page.goto(f"{base_url}/teemad/")
    page.wait_for_load_state("networkidle")
    page.locator(".app__footer").get_by_role("link", name="Uuendused").click()
    page.wait_for_load_state("networkidle")
    return page


# ---------------------------------------------------------------------------
# Getting there
# ---------------------------------------------------------------------------


def test_the_footer_link_opens_the_release_notes(page, base_url):
    open_from_the_footer(page, base_url)
    assert page.url.endswith(URL)
    expect(page.get_by_role("heading", name="Uuendused", exact=True)).to_be_visible()


def test_the_footer_still_names_the_running_build_beside_the_link(page, base_url):
    """The link is an addition. What the band already said stays said."""
    sign_in(page, base_url, SANDRA)
    page.goto(f"{base_url}/teemad/")
    footer = page.locator(".app__footer")
    expect(footer).to_contain_text("versioon")
    expect(footer.get_by_role("link", name="Uuendused")).to_be_visible()


# ---------------------------------------------------------------------------
# The disclosure
# ---------------------------------------------------------------------------


def test_the_newest_day_starts_expanded_and_the_rest_do_not(page, base_url):
    open_from_the_footer(page, base_url)
    assert days(page).count() >= 2, "the page needs at least two days to prove this"

    expect(days(page).first).to_have_attribute("open", "")
    for index in range(1, days(page).count()):
        assert days(page).nth(index).get_attribute("open") is None


def test_the_open_day_shows_its_changes_and_a_shut_one_does_not(page, base_url):
    open_from_the_footer(page, base_url)
    expect(days(page).first.locator("li").first).to_be_visible()
    expect(days(page).nth(1).locator("li").first).to_be_hidden()


def test_an_older_day_opens_when_it_is_activated(page, base_url):
    open_from_the_footer(page, base_url)
    older = days(page).nth(1)

    older.locator("summary").click()

    expect(older).to_have_attribute("open", "")
    expect(older.locator("li").first).to_be_visible()
    # Opening one does not shut another: this is a list of days, not a
    # single-selection control.
    expect(days(page).first).to_have_attribute("open", "")


def test_the_newest_day_closes_again(page, base_url):
    open_from_the_footer(page, base_url)
    newest = days(page).first

    newest.locator("summary").click()

    assert newest.get_attribute("open") is None
    expect(newest.locator("li").first).to_be_hidden()


def test_the_keyboard_reaches_a_day_and_opens_it(page, base_url):
    """A `<summary>` is focusable and Enter toggles it — for free, if nothing
    has taken the behaviour over."""
    open_from_the_footer(page, base_url)
    older = days(page).nth(1)

    older.locator("summary").focus()
    page.keyboard.press("Enter")

    expect(older).to_have_attribute("open", "")


def test_the_day_is_named_in_words_and_the_count_is_beside_it(page, base_url):
    """The date is text a screen reader reads; the count is supplementary."""
    open_from_the_footer(page, base_url)
    summary = days(page).first.locator("summary")
    assert summary.inner_text().strip()
    expect(summary.locator(".accordion__title")).not_to_be_empty()
    expect(summary.locator(".accordion__summary")).to_contain_text("uuendus")


def test_the_count_matches_the_number_of_changes_under_it(page, base_url):
    open_from_the_footer(page, base_url)
    for index in range(days(page).count()):
        day = days(page).nth(index)
        stated = day.locator(".accordion__summary").inner_text().strip().split()[0]
        assert int(stated) == day.locator("li").count()


# ---------------------------------------------------------------------------
# Without any scripting at all
# ---------------------------------------------------------------------------


def test_the_accordion_needs_no_javascript(browser, base_url):
    """The whole argument for `<details>` over a third accordion component.

    A fresh context with JavaScript disabled, so `app.js` and `ux.js` never run.
    Sign-in is a plain form post and works without them; if that ever stops
    being true this test says so, which is worth knowing on its own.
    """
    context = browser.new_context(java_script_enabled=True)
    signed_in = context.new_page()
    sign_in(signed_in, base_url, SANDRA)
    state = context.storage_state()
    context.close()

    context = browser.new_context(java_script_enabled=False, storage_state=state)
    page = context.new_page()
    try:
        page.goto(f"{base_url}{URL}")
        expect(page.get_by_role("heading", name="Uuendused", exact=True)).to_be_visible()

        newest, older = page.locator(DAY).first, page.locator(DAY).nth(1)
        expect(newest).to_have_attribute("open", "")
        expect(older.locator("li").first).to_be_hidden()

        older.locator("summary").click()
        expect(older.locator("li").first).to_be_visible()
    finally:
        context.close()


# ---------------------------------------------------------------------------
# Layout
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("width", [1440, 1280, 1024, 390])
def test_the_page_stays_usable_at_every_width(page, base_url, width):
    """Nothing overflows sideways, and the day is still operable.

    390px is not a supported workstation width and is included anyway: this is
    the one page somebody plausibly opens on a phone, and a horizontal scrollbar
    on a list of sentences would be a defect nowhere else in the product has.
    """
    open_from_the_footer(page, base_url)
    page.set_viewport_size({"width": width, "height": 900})
    page.wait_for_timeout(120)

    overflow = page.evaluate(
        "() => document.documentElement.scrollWidth - document.documentElement.clientWidth"
    )
    assert overflow <= 1, f"{width}px: the page scrolls sideways by {overflow}px"

    older = days(page).nth(1)
    older.locator("summary").click()
    expect(older.locator("li").first).to_be_visible()
