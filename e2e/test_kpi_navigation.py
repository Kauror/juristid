"""Clicking a number on Ülevaade lands on the rows that number counted.

Proved against the database elsewhere. What only a browser shows is whether the
promise survives the round trip a person actually makes: read a figure, click
it, and find the register already filtered — rather than an unfiltered list and
a filter to rebuild by hand.

Every test asserts the same three things, because any one of them alone can pass
while the experience is broken:

* the register says it is showing exactly the card's number;
* a visible chip says *why*, so the reader is not looking at a list they cannot
  explain or escape;
* clearing that chip gives back a larger register.

`?leht=` is deliberately not paged through. Twenty-five rows fit on a page and
the seeded world is far smaller, so the count element is the honest total either
way — and a test that scraped rows would be asserting the pager, not the filter.
"""

from __future__ import annotations

import re

import pytest
from playwright.sync_api import expect

from e2e.conftest import HEAD, sign_in

pytestmark = pytest.mark.e2e

#: The cards a lawyer clicks, by the label printed on them. Matched loosely so
#: rewording the copy does not break the navigation test — what is asserted is
#: the behaviour, not the sentence.
CARDS = {
    "active": "Aktiivsed teemad",
    "deadlines": "Arvamuse tähtaeg",
    "drafting": "Arvamusi koostamisel",
    # The seeded world carries one genuinely late DO + DEADLINE, which is what
    # makes this card worth driving rather than asserting 0 == 0
    # (app/core/management/commands/seed_e2e_data.py, OVERDUE_TITLE).
    "overdue": "Tähtaeg möödas",
    "unassigned": "Vastutajata",
}


def card(page, label: str):
    return page.locator(".statcard").filter(has_text=label).first


def card_count(page, label: str) -> int:
    return int(card(page, label).locator(".statcard__value").inner_text().strip())


def shown_total(page) -> int:
    """What the register says it is showing, from its own live count element."""
    text = page.locator(".registercount").inner_text()
    match = re.search(r"\d+", text)
    assert match, f"the register printed no count: {text!r}"
    return int(match.group(0))


def open_card(page, base_url, label: str) -> int:
    """Read a card, click it, and return the number it claimed."""
    page.goto(f"{base_url}/ulevaade/")
    expect(card(page, label)).to_be_visible()
    claimed = card_count(page, label)
    card(page, label).click()
    page.wait_for_url(re.compile(r"/teemad/\?"))
    page.wait_for_load_state("networkidle")
    return claimed


@pytest.mark.parametrize("key", sorted(CARDS))
def test_a_card_opens_the_register_already_filtered(page, base_url, key):
    """The whole complaint, in one assertion per card.

    The register must arrive holding the card's own number *and* say which
    filter produced it. A count that happens to match with no chip on screen is
    the failure this replaced — an unfiltered register that coincidentally has
    the same number of rows.
    """
    sign_in(page, base_url, HEAD)
    claimed = open_card(page, base_url, CARDS[key])

    assert shown_total(page) == claimed, f"{key}: the card and the list disagree"
    expect(page.locator(".filterchip")).not_to_have_count(0)


@pytest.mark.parametrize("key", sorted(CARDS))
def test_the_filter_a_card_applied_can_be_cleared(page, base_url, key):
    """Arriving from a KPI must not be a dead end.

    "Tühjenda kõik" returns to the ordinary register, which is how somebody who
    clicked the wrong number gets back to their work.
    """
    sign_in(page, base_url, HEAD)
    open_card(page, base_url, CARDS[key])
    filtered = shown_total(page)

    page.locator(".filterchip--clear").click()
    page.wait_for_load_state("networkidle")

    assert shown_total(page) >= filtered
    expect(page.locator(".filterchip--clear")).to_have_count(0)


def test_the_back_button_returns_to_the_dashboard(page, base_url):
    """Nothing about this navigation depends on session state.

    The filter lives in the URL, so Back is Back — a filter held in a cookie is
    a page that cannot be shared and cannot be reproduced from a bug report.
    """
    sign_in(page, base_url, HEAD)
    open_card(page, base_url, CARDS["unassigned"])
    page.go_back()
    expect(page.locator(".statcard").first).to_be_visible()


def test_drafting_opens_the_unsent_opinion_and_not_the_sent_one(page, base_url, screenshots):
    """The card whose link was most obviously wrong, on its real rows.

    The seeded world holds one register row with a blank VÄLJA cell and one with
    a mark in it, so this fails if the filter collapses to "every current
    register row" (app/core/management/commands/seed_e2e_data.py).
    """
    from app.core.management.commands.seed_e2e_data import DRAFTING_SENT_TITLE, DRAFTING_TITLE

    sign_in(page, base_url, HEAD)
    open_card(page, base_url, CARDS["drafting"])
    screenshots(page, "kpi-arvamusi-koostamisel")

    rows = page.locator(".table--register tbody tr")
    expect(rows.filter(has_text=DRAFTING_TITLE)).to_have_count(1)
    expect(rows.filter(has_text=DRAFTING_SENT_TITLE)).to_have_count(0)


def test_overdue_opens_the_late_matter_and_not_a_passed_review(page, base_url):
    """A WAIT past its review date is due for a look, never late.

    Both rows exist in the seeded world, so a filter that collected reviews as
    well as deadlines would fail here rather than merely be too generous
    (master specification 18.8).
    """
    from app.core.management.commands.seed_e2e_data import OVERDUE_TITLE, REVIEW_DUE_TITLE

    sign_in(page, base_url, HEAD)
    open_card(page, base_url, CARDS["overdue"])

    rows = page.locator(".table--register tbody tr")
    expect(rows.filter(has_text=OVERDUE_TITLE)).to_have_count(1)
    expect(rows.filter(has_text=REVIEW_DUE_TITLE)).to_have_count(0)


def test_unassigned_opens_the_matter_with_no_owner(page, base_url):
    from app.core.management.commands.seed_e2e_data import UNASSIGNED_TITLE

    sign_in(page, base_url, HEAD)
    open_card(page, base_url, CARDS["unassigned"])

    rows = page.locator(".table--register tbody tr")
    expect(rows.filter(has_text=UNASSIGNED_TITLE)).to_have_count(1)


def test_the_migration_state_card_is_gone(page, base_url):
    """It counted how far the cutover had got, not a problem anybody can act on.

    A prominent KPI nobody can move is a KPI people learn to read past, and the
    whole card row loses credibility with it.
    """
    sign_in(page, base_url, HEAD)
    page.goto(f"{base_url}/ulevaade/")
    expect(page.locator(".statcard").filter(has_text="Järgmine tegevus puudub")).to_have_count(0)


def test_no_card_subtitle_names_a_spreadsheet_column(page, base_url):
    """Subtitles explain the metric, never where the data came from."""
    sign_in(page, base_url, HEAD)
    page.goto(f"{base_url}/ulevaade/")
    notes = " ".join(page.locator(".statcard__note").all_inner_texts())
    for jargon in ("VÄLJA", "VASTUTAJA", "Registripõhine"):
        assert jargon not in notes
