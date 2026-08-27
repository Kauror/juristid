"""Osakonna töö, in a browser.

What a number *means* is proved against the database in
`tests/test_department_dashboard.py`. What only a browser shows is whether the
gate really holds for a person who types the URL, whether the page reads as
oversight rather than as a ranking, whether a count on the page opens the list
it claims to, and whether nine columns of numbers can be read on a laptop
without the page itself scrolling sideways.

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
    OVERDUE_TITLE,
    RESTRICTED_TITLE,
)
from e2e.conftest import ADMIN, HEAD, MARTIN, READER, SANDRA, go_to, sign_in, sign_out

pytestmark = pytest.mark.e2e

WORK_PATH = "/osakonna-too/"
NAV_NAME = "Osakond"


def figure(page, caption: str):
    """One number on the Seis strip, located by the words printed under it."""
    return page.locator(".seis__figure").filter(has_text=caption).first


def figure_value(page, caption: str) -> int:
    return int(figure(page, caption).locator(".seis__number").inner_text().strip())


def team_row(page, name: str):
    return page.locator(".uxstat__row").filter(has_text=name).first


def reveal_secondary_navigation(page) -> None:
    """Open the "Veel" disclosure if this viewport has one."""
    trigger = page.locator(".topnav__trigger")
    if trigger.count() and trigger.is_visible():
        trigger.click()


def open_work(page, base_url) -> None:
    page.goto(f"{base_url}{WORK_PATH}")
    page.wait_for_load_state("networkidle")


# =========================================================================
# The gate
# =========================================================================


def test_a_specialist_is_not_offered_the_page(page, base_url):
    sign_in(page, base_url, SANDRA)
    # Opened first, because the secondary destinations live behind it below
    # 1560px: asserting an absence against a closed disclosure would pass for
    # the department head too, and prove nothing.
    reveal_secondary_navigation(page)
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
    reveal_secondary_navigation(page)
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
    go_to(page, NAV_NAME)
    page.wait_for_url(f"{base_url}{WORK_PATH}")

    expect(page.get_by_role("heading", name="Osakonna töö", exact=True)).to_be_visible()
    screenshots(page, "osakonna-too")


# =========================================================================
# Seis
# =========================================================================


def test_a_review_date_reached_is_never_counted_as_a_missed_deadline(page, base_url):
    """The distinction the whole work model rests on.

    The seeded world holds exactly one genuinely late DO and one WAIT whose
    review date has passed. If those two ever land in the same number, waiting on
    a ministry starts reading as a failure and the department stops believing the
    queue (specification 18.8).
    """
    sign_in(page, base_url, HEAD)
    open_work(page, base_url)

    assert figure_value(page, "üle tähtaja") == 1


def test_the_overdue_figure_reaches_the_matter_that_is_actually_late(page, base_url):
    sign_in(page, base_url, HEAD)
    open_work(page, base_url)

    figure(page, "üle tähtaja").click()
    page.wait_for_load_state("networkidle")
    expect(page.get_by_role("link", name=OVERDUE_TITLE)).to_be_visible()


def test_work_with_nobody_on_it_is_counted_and_reachable(page, base_url):
    sign_in(page, base_url, HEAD)
    open_work(page, base_url)

    counted = figure_value(page, "vastutajata")
    assert counted >= 1

    figure(page, "vastutajata").click()
    page.wait_for_load_state("networkidle")
    expect(page.locator(".pagehead__context")).to_have_text(f"{counted} teemat")


def test_the_wording_the_handoff_settled_on_is_what_is_on_screen(page, base_url):
    """«läbi vaatamata», never «triaaž». «Muutusteta 30 p», never «seisma jäänud»."""
    sign_in(page, base_url, HEAD)
    open_work(page, base_url)

    body = page.content()
    assert "läbi vaatamata" in body
    assert "Muutusteta 30 p" in body
    lowered = body.casefold()
    assert "triaaž" not in lowered
    assert "seisma jäänud" not in lowered


# =========================================================================
# Meeskond
# =========================================================================


def test_the_team_table_lists_lawyers_and_never_ranks_them(page, base_url, screenshots):
    sign_in(page, base_url, HEAD)
    open_work(page, base_url)

    expect(team_row(page, SANDRA.display_name)).to_be_visible()
    expect(team_row(page, MARTIN.display_name)).to_be_visible()

    body = page.content().casefold()
    for forbidden in ("töökoormus", "tulemuslikkus", "produktiivsus", "edetabel"):
        assert forbidden not in body, f"{forbidden!r} would make this a staff evaluation"

    screenshots(page, "osakonna-too-meeskond")


def test_the_lawyers_are_listed_alphabetically(page, base_url):
    """Ordering is the guard against a leaderboard, so it is asserted."""
    sign_in(page, base_url, HEAD)
    open_work(page, base_url)

    names = [
        name.strip()
        for name in page.locator(".uxstat__row .uxteam__name").all_inner_texts()
        # The unassigned pile and the total are not people and sit last.
        if name.strip() not in ("Vastutajata", "Kokku")
    ]
    names = [name.replace("· sina", "").strip() for name in names]
    assert names, "the team table rendered no lawyers at all"
    assert names == sorted(names)


def test_a_lawyers_open_count_opens_exactly_that_list(page, base_url):
    """Every number is a promise that a list exists behind it."""
    sign_in(page, base_url, HEAD)
    open_work(page, base_url)

    row = team_row(page, MARTIN.display_name)
    expected = int(row.locator(".uxstat__num").first.inner_text().strip())

    row.click()
    page.wait_for_load_state("networkidle")
    expect(page.locator(".pagehead__context")).to_have_text(f"{expected} teemat")


def test_a_departed_colleague_holding_live_work_is_surfaced_not_hidden(page, base_url):
    """Dropping the row would take an open file off the page that finds them."""
    sign_in(page, base_url, HEAD)
    open_work(page, base_url)

    row = team_row(page, FORMER_NAME)
    expect(row).to_be_visible()
    expect(row.locator(".badge")).to_be_visible()


def test_the_departed_colleague_is_not_offered_as_somebody_to_choose(page, base_url):
    """Resolvable in history, never selectable for new work."""
    page.goto(f"{base_url}/konto/arendus-sisselogimine/")
    page.wait_for_load_state("networkidle")
    assert FORMER_NAME not in page.content()


def test_nine_columns_scroll_inside_their_block_rather_than_moving_the_page(page, base_url):
    """The one horizontal scroll in the application, and it is deliberate.

    A manager on a laptop must be able to read the last column, and the page
    itself must never move sideways to let them (design handoff, Osakond §2).
    """
    sign_in(page, base_url, HEAD)

    # 1024 is where the nine columns still fit: the block is the width of the
    # main column and the grid's minimum is 220 + 8 x 72 + gaps. What this test
    # is about is what happens when they do *not* fit, so it is measured at 720
    # as well — where the block must scroll and the page must not.
    for width in (1024, 720):
        page.set_viewport_size({"width": width, "height": 900})
        open_work(page, base_url)
        assert not page.evaluate(
            "() => document.documentElement.scrollWidth > document.documentElement.clientWidth + 1"
        ), f"the page must not scroll horizontally at {width}px"

    assert page.evaluate(
        "() => { const t = document.querySelector('.uxstat');"
        " return t.scrollWidth > t.clientWidth; }"
    ), "at 720px the team table must be the thing that scrolls"


# =========================================================================
# Eesolev and Tehtud
# =========================================================================


def test_the_period_control_lives_in_the_url(page, base_url):
    """A report somebody is reading can be sent to somebody else."""
    sign_in(page, base_url, HEAD)
    open_work(page, base_url)

    page.get_by_role("link", name="30 päeva", exact=True).click()
    page.wait_for_load_state("networkidle")
    assert "periood=30" in page.url
    expect(page.locator(".uxchip.is-selected").filter(has_text="30 päeva")).to_be_visible()


def test_a_custom_period_is_a_real_date_control(page, base_url):
    sign_in(page, base_url, HEAD)
    open_work(page, base_url)

    page.get_by_text("Vali periood…", exact=True).click()
    field = page.locator("input[name=alates]")
    expect(field).to_be_visible()
    field.fill("1.1.2026")
    page.locator("input[name=kuni]").fill("31.12.2026")
    page.get_by_role("button", name="Näita").click()
    page.wait_for_load_state("networkidle")
    assert "periood=vahemik" in page.url


def test_an_upcoming_group_opens_exactly_its_own_window(page, base_url):
    """`kõik N →` is a promise about the list behind it."""
    sign_in(page, base_url, HEAD)
    open_work(page, base_url)

    link = page.locator(".uxdl__all").first
    if not link.count():
        pytest.skip("the seeded world holds no upcoming deadlines today")
    expected = int(link.inner_text().split()[1])

    link.click()
    page.wait_for_load_state("networkidle")
    expect(page.locator(".pagehead__context")).to_have_text(f"{expected} teemat")


# =========================================================================
# Restricted content
# =========================================================================


def test_the_head_counts_restricted_work_and_a_reader_does_not(page, base_url):
    """One Matter, two readers, two correct answers.

    The head sees it because DEPARTMENT_HEAD is entitled to; a reader does not,
    because a reader is outside the legal team (docs/adr/0042). Asserted in a browser
    because a leak here would be a leak in rendering, not in a query.
    """
    sign_in(page, base_url, HEAD)
    page.goto(f"{base_url}/teemad/?olek=koik")
    page.wait_for_load_state("networkidle")
    expect(page.get_by_text(RESTRICTED_TITLE).first).to_be_visible()
    sign_out(page, base_url)

    sign_in(page, base_url, READER)
    page.goto(f"{base_url}/teemad/?olek=koik")
    page.wait_for_load_state("networkidle")
    assert RESTRICTED_TITLE not in page.content()

    response = page.goto(f"{base_url}{WORK_PATH}")
    assert response is not None
    assert response.status == 404


def test_the_former_owners_matter_reaches_its_own_page(page, base_url):
    sign_in(page, base_url, HEAD)
    open_work(page, base_url)

    team_row(page, FORMER_NAME).click()
    page.wait_for_load_state("networkidle")
    page.get_by_role("link", name=FORMER_OWNER_TITLE).first.click()
    page.wait_for_load_state("networkidle")
    expect(page.get_by_role("heading", name=FORMER_OWNER_TITLE)).to_be_visible()
