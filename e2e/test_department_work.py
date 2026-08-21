"""Osakonna töö, in a browser.

What a number *means* is proved against the database in
`tests/test_department_dashboard.py`. What only a browser shows is whether the
gate really holds for a person who types the URL, whether the page reads as
oversight rather than as a ranking, and whether a count on the page opens the
list it claims to.

The role gate is the reason these exist. A gate that is right in a unit test
and wrong in the template — a navigation item rendered for everybody, a view
whose decorator was lost in a merge — fails in exactly the place no unit test
looks (Stage-2F brief 45).
"""

from __future__ import annotations

import pytest
from playwright.sync_api import expect

from app.core.management.commands.seed_e2e_data import (
    FORMER_NAME,
    FORMER_OWNER_TITLE,
    NO_ACTION_TITLE,
    OVERDUE_TITLE,
    RESTRICTED_TITLE,
    REVIEW_DUE_TITLE,
    UNASSIGNED_TITLE,
)
from e2e.conftest import ADMIN, HEAD, MARTIN, SANDRA, sign_in, sign_out

pytestmark = pytest.mark.e2e

WORK_PATH = "/osakonna-too/"
NAV_NAME = "Osakonna töö"


def card(page, label: str):
    """One summary card, located by the words printed on it."""
    return page.locator(".statcard").filter(has_text=label).first


def card_value(page, label: str) -> int:
    return int(card(page, label).locator(".statcard__value").inner_text().strip())


def lawyer_row(page, name: str):
    return page.locator("table.table tbody tr").filter(has_text=name).first


# =========================================================================
# The gate
# =========================================================================


def test_a_specialist_is_not_offered_the_page(page, base_url):
    sign_in(page, base_url, SANDRA)
    expect(page.get_by_role("link", name=NAV_NAME, exact=True)).to_have_count(0)


def test_a_specialist_who_types_the_url_does_not_reach_it(page, base_url):
    """404, not 403. Telling somebody a page exists but is not theirs is itself
    a disclosure about what the department head is looking at."""
    sign_in(page, base_url, SANDRA)
    response = page.goto(f"{base_url}{WORK_PATH}")
    assert response is not None
    assert response.status == 404


def test_the_technical_administrator_does_not_inherit_the_page(page, base_url):
    """Administering the system is not reading the department's work."""
    sign_in(page, base_url, ADMIN)
    expect(page.get_by_role("link", name=NAV_NAME, exact=True)).to_have_count(0)

    response = page.goto(f"{base_url}{WORK_PATH}")
    assert response is not None
    assert response.status == 404


def test_nobody_signed_in_does_not_reach_the_page(page, base_url):
    """Being past the door is not being somebody in particular.

    In shared-gate mode this is a session that knows the department password
    and has chosen no persona. It lands on the sign-in surface, not here.
    """
    sign_in(page, base_url, SANDRA)
    sign_out(page, base_url)

    page.goto(f"{base_url}{WORK_PATH}")
    page.wait_for_load_state("networkidle")
    assert WORK_PATH not in page.url.split("?")[0]


def test_the_head_is_offered_the_page_and_opens_it(page, base_url, screenshots):
    sign_in(page, base_url, HEAD)
    page.get_by_role("link", name=NAV_NAME, exact=True).click()
    page.wait_for_url(f"{base_url}{WORK_PATH}")

    expect(page.get_by_role("heading", name=NAV_NAME, exact=True)).to_be_visible()
    screenshots(page, "osakonna-too")


# =========================================================================
# What the page says
# =========================================================================


def open_work(page, base_url) -> None:
    page.goto(f"{base_url}{WORK_PATH}")
    page.wait_for_load_state("networkidle")


def test_a_review_date_reached_is_never_counted_as_a_missed_deadline(page, base_url):
    """The distinction the whole work model rests on.

    The seeded world holds exactly one genuinely late DO and exactly one WAIT
    whose review date has passed. If those two ever land in the same number,
    waiting on a ministry starts reading as a failure and the department stops
    believing the queue (specification 18.8).
    """
    sign_in(page, base_url, HEAD)
    open_work(page, base_url)

    assert card_value(page, "Tegevuse tähtaeg möödas") == 1
    assert card_value(page, "Ülevaatus või ootamine vajab pilku") == 1
    expect(card(page, "Ülevaatus või ootamine vajab pilku")).to_contain_text("Ei ole hilinemine")


def test_the_overdue_card_reaches_the_matter_that_is_actually_late(page, base_url):
    sign_in(page, base_url, HEAD)
    open_work(page, base_url)

    card(page, "Tegevuse tähtaeg möödas").click()
    page.wait_for_load_state("networkidle")
    expect(page.get_by_role("link", name=OVERDUE_TITLE)).to_be_visible()
    expect(page.get_by_role("link", name=REVIEW_DUE_TITLE)).to_have_count(0)


def test_matters_with_no_instruction_are_counted_and_listed(page, base_url):
    """`Järgmiseks puudub` is a state to act on, not a hole to fill with a guess."""
    sign_in(page, base_url, HEAD)
    open_work(page, base_url)

    assert card_value(page, "Järgmiseks puudub") >= 1
    card(page, "Järgmiseks puudub").click()
    page.wait_for_load_state("networkidle")
    expect(page.get_by_role("link", name=NO_ACTION_TITLE)).to_be_visible()


def test_work_with_nobody_on_it_is_visible_and_reachable(page, base_url):
    sign_in(page, base_url, HEAD)
    open_work(page, base_url)

    assert card_value(page, "Vastutajata") >= 1
    section = page.locator("section").filter(has_text="Vastutajata teemad").first
    expect(section.get_by_role("link", name=UNASSIGNED_TITLE)).to_be_visible()


def test_upcoming_dates_keep_their_meanings_apart(page, base_url):
    """Four kinds of date share one column and must not read alike."""
    sign_in(page, base_url, HEAD)
    open_work(page, base_url)

    upcoming = page.locator("section").filter(has_text="Lähenevad kuupäevad").first
    expect(upcoming).to_be_visible()
    expect(upcoming).to_contain_text("Tähendus")


# =========================================================================
# The lawyer table
# =========================================================================


def test_the_team_table_lists_lawyers_and_never_ranks_them(page, base_url, screenshots):
    sign_in(page, base_url, HEAD)
    open_work(page, base_url)

    table = page.locator("section").filter(has_text="Juristid").first
    expect(table.get_by_role("row").filter(has_text=SANDRA.display_name)).to_be_visible()
    expect(table.get_by_role("row").filter(has_text=MARTIN.display_name)).to_be_visible()

    body = page.content().casefold()
    for forbidden in ("töökoormus", "tulemuslikkus", "produktiivsus", "edetabel"):
        assert forbidden not in body, f"{forbidden!r} would make this a staff evaluation"

    screenshots(page, "osakonna-too-juristid")


def test_the_lawyers_are_listed_alphabetically(page, base_url):
    """Ordering is the guard against a leaderboard, so it is asserted."""
    sign_in(page, base_url, HEAD)
    open_work(page, base_url)

    section = page.locator("section").filter(has_text="Juristid").first
    names = [name.strip() for name in section.locator(".lawyer__name").all_inner_texts()]
    assert names, "the team table rendered no lawyers at all"
    assert names == sorted(names)


def test_a_lawyers_active_count_opens_exactly_that_list(page, base_url):
    """Every number is a promise that a list exists behind it."""
    sign_in(page, base_url, HEAD)
    open_work(page, base_url)

    row = lawyer_row(page, MARTIN.display_name)
    link = row.locator("td a").first
    expected = int(link.inner_text().strip().rstrip("*"))

    link.click()
    page.wait_for_load_state("networkidle")
    expect(page.locator(".pagehead__context")).to_have_text(f"{expected} teemat")


def test_a_departed_colleague_holding_live_work_is_surfaced_not_hidden(page, base_url):
    """Dropping the row would take an open file off the page that finds them."""
    sign_in(page, base_url, HEAD)
    open_work(page, base_url)

    section = page.locator("section").filter(has_text="Juristid").first
    row = section.get_by_role("row").filter(has_text=FORMER_NAME)
    expect(row).to_be_visible()
    expect(row.locator(".badge")).to_be_visible()


def test_the_departed_colleague_is_not_offered_as_somebody_to_choose(page, base_url):
    """Resolvable in history, never selectable for new work."""
    page.goto(f"{base_url}/konto/arendus-sisselogimine/")
    page.wait_for_load_state("networkidle")
    assert FORMER_NAME not in page.content()


# =========================================================================
# Restricted content
# =========================================================================


def test_the_head_sees_restricted_work_and_a_specialist_does_not(page, base_url):
    """One Matter, two readers, two correct answers.

    The head sees it because DEPARTMENT_HEAD is entitled to; Martin does not,
    because he is neither its owner nor a collaborator. Asserted in a browser
    because a leak here would be a leak in rendering, not in a query.
    """
    sign_in(page, base_url, HEAD)
    open_work(page, base_url)
    expect(page.get_by_text(RESTRICTED_TITLE).first).to_be_visible()
    sign_out(page, base_url)

    sign_in(page, base_url, MARTIN)
    page.goto(f"{base_url}/teemad/")
    page.wait_for_load_state("networkidle")
    assert RESTRICTED_TITLE not in page.content()

    response = page.goto(f"{base_url}{WORK_PATH}")
    assert response is not None
    assert response.status == 404


def test_the_former_owners_matter_reaches_its_own_page(page, base_url):
    sign_in(page, base_url, HEAD)
    open_work(page, base_url)

    page.get_by_role("link", name=FORMER_OWNER_TITLE).first.click()
    page.wait_for_load_state("networkidle")
    expect(page.get_by_role("heading", name=FORMER_OWNER_TITLE)).to_be_visible()
