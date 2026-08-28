"""The header search's live suggestions, in a real browser.

Everything about *what* may come back is proved against the database in
`tests/test_header_search_suggestions.py`. What only a browser can show is
whether the control works: whether the panel opens while somebody is typing,
whether the arrow keys move through it, whether Enter opens what is highlighted
and whether Escape gives the field back — and whether a slow answer to an old
question can overwrite the answer to the current one.

That last one is the reason this file exists rather than a handful of
assertions bolted onto an existing suite. A stale-response race is invisible in
every screenshot and in every server-side test, it only appears when somebody
types faster than the network, and the way it appears is as a dropdown quietly
showing results for a word that is no longer in the box.
"""

from __future__ import annotations

import re
from urllib.parse import unquote, urlparse

import pytest
from playwright.sync_api import expect

from e2e.conftest import MARTIN, sign_in

pytestmark = pytest.mark.e2e

#: Seeded by `manage.py seed_e2e_data`, and named here rather than queried: a
#: browser test with database access could read around an authorization bug in
#: the interface and never fail (e2e/conftest.py).
#:
#: Every synthetic matter in the seeded world carries this word, so it is the
#: query that fills the dropdown and asks for the sixth row.
CROWDED = "sünteetiline"

#: Matches nothing at all, in any of the tiers.
NOTHING = "xqzvw"

FIELD = "#global-search"
PANEL = "#global-search-results"
OPTION = "#global-search-results a[role=option]"
#: The suggestions themselves, without the way out to the full page.
RESULT = "#global-search-results a[role=option]:not(.searchfield__option--all)"


def field(page):
    return page.locator(FIELD)


def panel(page):
    return page.locator(PANEL)


def options(page):
    return page.locator(OPTION)


def results(page):
    return page.locator(RESULT)


def type_query(page, text: str) -> None:
    """Type into the header field the way a person does, and let it settle.

    `press_sequentially` rather than `fill`, because the debounce and the
    minimum length are properties of a sequence of keystrokes and `fill` sets
    the value in one event.
    """
    field(page).click()
    field(page).press_sequentially(text, delay=30)


def open_suggestions(page, base_url, query: str = CROWDED):
    sign_in(page, base_url, MARTIN)
    type_query(page, query)
    expect(panel(page)).to_be_visible()
    return panel(page)


# -- the panel ---------------------------------------------------------------


def test_one_character_opens_nothing(page, base_url):
    """Below the threshold the field is a field, and asks the server nothing."""
    sign_in(page, base_url, MARTIN)
    type_query(page, "s")
    expect(panel(page)).to_be_hidden()
    expect(field(page)).to_have_attribute("aria-expanded", "false")


def test_two_characters_open_the_suggestions(page, base_url, screenshots):
    sign_in(page, base_url, MARTIN)
    type_query(page, CROWDED[:2])
    expect(panel(page)).to_be_visible()
    expect(field(page)).to_have_attribute("aria-expanded", "true")
    screenshots(page, "header-search-suggestions")


def test_the_results_follow_the_query(page, base_url):
    """A different query gives a different list, without a submit.

    This is the property "live" means, and it is asserted on the content of
    the first row rather than on a request count: what a reader notices is the
    list agreeing with the box.
    """
    sign_in(page, base_url, MARTIN)
    type_query(page, "tavaline")
    expect(panel(page)).to_be_visible()
    expect(results(page).first).to_contain_text(re.compile("tavaline", re.IGNORECASE))

    field(page).fill("")
    field(page).press_sequentially(CROWDED, delay=30)
    expect(panel(page)).to_be_visible()
    expect(results(page).first).to_contain_text(re.compile("ünteetiline", re.IGNORECASE))


def test_never_more_than_five_suggestions(page, base_url):
    """Plus the way out to the full page, which is a different kind of row."""
    open_suggestions(page, base_url)
    expect(results(page)).to_have_count(5)
    expect(page.get_by_role("option", name="Vaata kõiki tulemusi")).to_be_visible()


def test_every_suggestion_leads_with_its_title(page, base_url):
    open_suggestions(page, base_url)
    expect(results(page).locator(".searchfield__optiontitle")).to_have_count(5)


def test_a_suggestion_carries_a_secondary_line_when_it_has_one(page, base_url):
    """Facts the matter already holds, under the title — where there are any.

    Not on every row, deliberately: a seeded archive record has no owner, no
    addressee and no stage, and a second line reading " · · " would be the
    dropdown inventing punctuation to look complete. So the assertion is that
    the line appears where there is something to put in it.
    """
    open_suggestions(page, base_url)
    metas = results(page).locator(".searchfield__optionmeta")
    assert metas.count() >= 1
    assert metas.first.inner_text().strip()


def test_nothing_found_says_so(page, base_url):
    open_suggestions(page, base_url, NOTHING)
    expect(panel(page).get_by_text("Tulemusi ei leitud")).to_be_visible()
    expect(options(page)).to_have_count(0)
    expect(page.locator("#global-search-status")).to_have_text("Tulemusi ei leitud")


# -- the keyboard ------------------------------------------------------------


def test_arrowdown_and_arrowup_move_through_the_list(page, base_url):
    """And what is active is announced, not merely coloured."""
    open_suggestions(page, base_url)
    first = options(page).nth(0)
    second = options(page).nth(1)

    field(page).press("ArrowDown")
    expect(first).to_have_attribute("aria-selected", "true")
    expect(field(page)).to_have_attribute("aria-activedescendant", first.get_attribute("id"))

    field(page).press("ArrowDown")
    expect(second).to_have_attribute("aria-selected", "true")
    expect(first).to_have_attribute("aria-selected", "false")

    field(page).press("ArrowUp")
    expect(first).to_have_attribute("aria-selected", "true")


def test_enter_opens_the_selected_suggestion(page, base_url):
    open_suggestions(page, base_url)
    field(page).press("ArrowDown")
    target = results(page).first.get_attribute("href")
    field(page).press("Enter")
    page.wait_for_url(f"{base_url}{target}")
    expect(panel(page)).to_be_hidden()


def test_enter_with_nothing_selected_still_submits_the_form(page, base_url):
    """The fallback, exercised from the keyboard it has to survive.

    Nothing is highlighted, so this is the ordinary GET the field has always
    done — and it must reach the full results page rather than being swallowed
    by the dropdown.
    """
    open_suggestions(page, base_url)
    field(page).press("Enter")
    page.wait_for_url(re.compile(r"/otsing/\?q="))
    expect(page.get_by_role("heading", name="Otsing")).to_be_visible()


def test_escape_closes_the_panel_and_keeps_the_field(page, base_url):
    open_suggestions(page, base_url)
    field(page).press("Escape")
    expect(panel(page)).to_be_hidden()
    expect(field(page)).to_have_attribute("aria-expanded", "false")
    expect(field(page)).to_be_focused()


def test_ctrl_k_still_focuses_the_search(page, base_url):
    """The shortcut the bar advertises, unchanged by any of this."""
    sign_in(page, base_url, MARTIN)
    # Somewhere that is definitely not the field, and definitely not a control.
    page.locator("h1").first.click()
    page.keyboard.press("Control+k")
    expect(field(page)).to_be_focused()


# -- the pointer -------------------------------------------------------------


def test_clicking_a_suggestion_opens_the_matter(page, base_url):
    open_suggestions(page, base_url)
    target = results(page).first.get_attribute("href")
    results(page).first.click()
    page.wait_for_url(f"{base_url}{target}")
    expect(page.locator("h1")).to_be_visible()


def test_the_way_out_to_the_full_page_carries_the_query(page, base_url):
    open_suggestions(page, base_url)
    page.get_by_role("option", name="Vaata kõiki tulemusi").click()
    page.wait_for_url(re.compile(r"/otsing/\?q="))
    # The query survives the hop, percent-encoded by the browser.
    assert unquote(urlparse(page.url).query) == f"q={CROWDED}"
    expect(page.get_by_role("heading", name="Otsing")).to_be_visible()


def test_clicking_outside_closes_the_panel(page, base_url):
    open_suggestions(page, base_url)
    page.locator("h1").first.click()
    expect(panel(page)).to_be_hidden()


def test_clearing_the_query_closes_the_panel(page, base_url):
    open_suggestions(page, base_url)
    field(page).fill("")
    expect(panel(page)).to_be_hidden()
    expect(field(page)).to_have_attribute("aria-expanded", "false")


# -- the shell it lives in ---------------------------------------------------


def test_the_panel_does_not_push_the_page_sideways(page, base_url):
    """At the narrowest supported width, where the field is a full-width row.

    A panel that measures itself against the viewport instead of its field is a
    horizontal scrollbar on every page — and it is invisible in a screenshot,
    which is why this measures `scrollWidth` instead.
    """
    page.set_viewport_size({"width": 1024, "height": 768})
    open_suggestions(page, base_url)
    assert not page.evaluate(
        "() => document.documentElement.scrollWidth > document.documentElement.clientWidth + 1"
    )
    box = panel(page).bounding_box()
    assert box is not None
    assert box["x"] >= -1
    assert box["x"] + box["width"] <= page.viewport_size["width"] + 1


def test_the_panel_sits_under_its_own_field(page, base_url):
    """Directly below, and about as wide — not a floating box near it."""
    open_suggestions(page, base_url)
    above = field(page).bounding_box()
    below = panel(page).bounding_box()
    assert above is not None and below is not None
    assert 0 <= below["y"] - (above["y"] + above["height"]) <= 12
    assert abs(below["width"] - above["width"]) <= 2


# -- the race ----------------------------------------------------------------


def test_a_late_answer_to_an_old_question_does_not_replace_a_new_one(page, base_url):
    """The one failure a screenshot suite can never catch.

    `fetch` is replaced with one that holds the *first* request back until the
    second has already answered, and then lets it through — the exact ordering
    the version guard exists for. It ignores the abort signal on purpose: an
    abort that lands after the response was parsed is precisely the case that
    would otherwise reach the page.

    The panel must be showing the newer query's answer afterwards, and the
    stale one must never have appeared.
    """
    sign_in(page, base_url, MARTIN)
    page.evaluate(
        """
        () => {
          const real = window.fetch.bind(window);
          window.__stale = { held: null, released: false };
          window.fetch = function (url, options) {
            const isSuggestion = String(url).indexOf('/otsing/soovitused/') === 0
              || String(url).indexOf('soovitused') >= 0;
            if (!isSuggestion) {
              return real(url, options);
            }
            /* The signal is deliberately not forwarded: an aborted request whose
               response was already parsed is what the version guard is for. */
            const answer = real(url, { credentials: 'same-origin' });
            if (!window.__stale.held) {
              window.__stale.held = answer;
              return new Promise(function (resolve, reject) {
                window.__stale.release = function () {
                  answer.then(resolve, reject);
                };
              });
            }
            return answer;
          };
        }
        """
    )

    # The old question. Its answer is now held open.
    type_query(page, "ta")
    page.wait_for_function("() => window.__stale.held !== null")

    # The new question, which answers first.
    field(page).press_sequentially("valine avatud teema", delay=10)
    expect(panel(page)).to_be_visible()
    settled = results(page).first.inner_text()

    # Now let the old one through, and give it every chance to overwrite.
    page.evaluate("() => window.__stale.release && window.__stale.release()")
    page.wait_for_timeout(600)

    expect(panel(page)).to_be_visible()
    assert results(page).first.inner_text() == settled
