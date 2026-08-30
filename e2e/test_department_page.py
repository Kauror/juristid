"""Osakond, in a browser.

What a number *means* is proved against the database in
`tests/test_department_page.py`. What only a browser shows is whether the
boundary really holds for a person who types the URL, whether the page reads as
oversight rather than as a ranking, whether a count on the page opens the list
it claims to, and whether nine columns of numbers can be read on a laptop
without the page itself scrolling sideways.

The boundary is the reason these exist, and it moved with the merge: the *page*
is every reader's, because it replaced Ülevaade, and *Meeskond* and *Tehtud*
stay the department head's (ADR 0049). A boundary that is right in a unit test
and wrong in the template — a section rendered for everybody, a role check lost
in a merge — fails in exactly the place no unit test looks (Stage-2F brief 45).
"""

from __future__ import annotations

from urllib.parse import quote

import pytest
from playwright.sync_api import expect

from app.core.management.commands.seed_e2e_data import (
    FORMER_NAME,
    FORMER_OWNER_TITLE,
    OVERDUE_TITLE,
    RESTRICTED_TITLE,
    SUPERSEDED_DEADLINE_TITLE,
)
from e2e.conftest import ADMIN, HEAD, MARTIN, READER, SANDRA, go_to, sign_in, sign_out

pytestmark = pytest.mark.e2e

WORK_PATH = "/osakond/"
LEGACY_PATHS = ("/ulevaade/", "/osakonna-too/")
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


def test_one_department_destination_is_offered_to_everybody(page, base_url):
    """`Osakond`, once, whatever the role.

    There were two items for one question — a universal `Ülevaade` on the bar
    and a head-only `Osakond` inside «Veel» — so a reader had to know which page
    a number lived on before they could look it up (ADR 0049).
    """
    for persona in (SANDRA, HEAD, ADMIN):
        sign_in(page, base_url, persona)
        # Opened first, because the secondary destinations live behind it below
        # 1560px: asserting an absence against a closed disclosure would prove
        # nothing.
        reveal_secondary_navigation(page)
        expect(page.get_by_role("link", name=NAV_NAME, exact=True)).to_have_count(1)
        expect(page.get_by_role("link", name="Ülevaade", exact=True)).to_have_count(0)
        expect(page.get_by_role("link", name="Osakonna töö", exact=True)).to_have_count(0)
        sign_out(page, base_url)


@pytest.mark.parametrize("legacy", LEGACY_PATHS)
def test_an_old_address_still_opens_the_page(page, base_url, legacy):
    """A pasted bookmark lands on the page it described, not on a 404."""
    sign_in(page, base_url, SANDRA)
    response = page.goto(f"{base_url}{legacy}?vaade=valdkonniti")
    assert response is not None
    assert response.status == 200
    assert page.url == f"{base_url}{WORK_PATH}?vaade=valdkonniti"


def test_a_specialist_reads_the_page_without_the_manager_sections(page, base_url):
    """The page is shared; two of its sections are not.

    Refusing the page would have taken the department view away from everybody
    who had Ülevaade, which is a loss of access dressed as a merge.
    """
    sign_in(page, base_url, SANDRA)
    response = page.goto(f"{base_url}{WORK_PATH}")
    assert response is not None
    assert response.status == 200

    expect(page.get_by_role("heading", name="Osakond", exact=True)).to_be_visible()
    expect(page.locator(".uxstat")).to_have_count(0)
    expect(page.locator("[aria-label='Tehtud']")).to_have_count(0)
    expect(page.locator("[aria-label='Vajab sekkumist']")).to_have_count(1)


def test_the_technical_administrator_does_not_inherit_the_manager_sections(page, base_url):
    """Administering the system is not reading the department's team."""
    sign_in(page, base_url, ADMIN)
    page.goto(f"{base_url}{WORK_PATH}")
    page.wait_for_load_state("networkidle")

    expect(page.locator(".uxstat")).to_have_count(0)
    expect(page.locator("[aria-label='Tehtud']")).to_have_count(0)


def test_nobody_signed_in_does_not_reach_the_page(page, base_url):
    """Signed out of the individual mode, this is behind the sign-in surface.

    The shared-gate reader is the separate case and is covered in
    `tests/test_department_page.py`: past the department door with no persona
    the page renders, without either manager section.
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

    expect(page.get_by_role("heading", name="Osakond", exact=True)).to_be_visible()
    expect(page.locator(".uxstat")).to_have_count(1)
    screenshots(page, "osakond")


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


def test_a_superseded_response_deadline_is_not_overdue_anywhere(page, base_url):
    """The regression this branch exists for, read in a browser.

    The seeded world holds a Matter whose `Arvamuse tähtaeg` passed two hundred
    days ago and whose lawyer has since written «JÄLGIN, jälgin menetluse
    jätkumist» with a review forty days ahead. Under the precedence that file is
    not late: its owner said what happens next, and the old deadline went back to
    being a fact in the header (docs/adr/0050).

    Asserted here rather than only against the database because the failure it
    replaces was something a person saw on a page — an eight-month-overdue row
    on a file nobody had neglected.
    """
    sign_in(page, base_url, HEAD)
    open_work(page, base_url)

    # Not in the intervention list, which is where an overdue row would appear.
    rows = page.locator(".interrow").filter(has_text=SUPERSEDED_DEADLINE_TITLE)
    expect(rows).to_have_count(0)

    # And not behind the overdue figure either.
    figure(page, "üle tähtaja").click()
    page.wait_for_load_state("networkidle")
    assert SUPERSEDED_DEADLINE_TITLE not in page.content()


def test_the_superseded_deadline_is_still_a_fact_on_the_teema_header(page, base_url):
    """Header fact, work-model silence — the distinction the rule rests on.

    The old deadline is still printed where a lawyer looks it up, and the current
    instruction is printed below it. Neither contradicts the other: one is what
    the register recorded, the other is what needs attention now.
    """
    sign_in(page, base_url, HEAD)
    page.goto(f"{base_url}/teemad/?olek=koik&q=Vana+t%C3%A4htajaga")
    page.wait_for_load_state("networkidle")
    page.get_by_role("link", name=SUPERSEDED_DEADLINE_TITLE).first.click()
    page.wait_for_load_state("networkidle")

    body = page.content()
    # The recorded response deadline is still stated on the page.
    assert "Arvamuse tähtaeg" in body or "Tähtaeg" in body
    # The current instruction is the monitoring one, and it is not styled late.
    expect(page.locator(".uxnext")).to_be_visible()
    expect(page.locator(".uxnext--overdue")).to_have_count(0)
    assert "Jälgin menetluse jätkumist" in body


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

    screenshots(page, "osakond-meeskond")


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
    """Every number is a promise that a list exists behind it.

    The row opens the person's desk now, not the register: a register row
    answers "what is this Matter", and the question a head clicks a name to ask
    is "what is on this person's desk" (design handoff, Minu asjad §A). The
    promise is unchanged and is asserted one step further along — the desk's
    own «avatud teemat» figure is the same number, and *its* link is the
    register list.
    """
    sign_in(page, base_url, HEAD)
    open_work(page, base_url)

    row = team_row(page, MARTIN.display_name)
    # Every number carries a visually-hidden label naming its column, because
    # the grid is a grid rather than a `<table>` and nothing associates a header
    # with a cell (docs/adr/0042). The digits are what comes after it.
    expected = int(row.locator(".uxstat__num").first.inner_text().split(":")[-1].strip())

    row.click()
    page.wait_for_load_state("networkidle")
    expect(page.get_by_role("heading", name=f"{MARTIN.display_name} · asjad")).to_be_visible()

    figure = page.locator(".seis__figure").filter(has_text="avatud teemat").first
    expect(figure.locator(".seis__number")).to_have_text(str(expected))

    figure.click()
    page.wait_for_load_state("networkidle")
    expect(page.locator(".registercount strong")).to_have_text(str(expected))


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

    Both halves *search* for the title rather than reading the first page of the
    register. The register pages at twelve rows, so the presence half depended on
    the seeded world staying small enough to keep this row on page one — and, far
    worse, the absence half would have passed for a reader who simply could not
    see page two. A query that would return the row if it were visible is the
    only shape in which "it is not there" means anything.
    """
    query = f"{base_url}/teemad/?olek=koik&q={quote(RESTRICTED_TITLE)}"

    sign_in(page, base_url, HEAD)
    page.goto(query)
    page.wait_for_load_state("networkidle")
    expect(page.get_by_text(RESTRICTED_TITLE).first).to_be_visible()
    sign_out(page, base_url)

    sign_in(page, base_url, READER)
    page.goto(query)
    page.wait_for_load_state("networkidle")
    assert RESTRICTED_TITLE not in page.content()

    # The department page is theirs to read now, and the restricted file is
    # still not on it — neither as a title nor inside a count.
    response = page.goto(f"{base_url}{WORK_PATH}")
    assert response is not None
    assert response.status == 200
    assert RESTRICTED_TITLE not in page.content()
    expect(page.locator(".uxstat")).to_have_count(0)


def test_the_former_owners_matter_reaches_its_own_page(page, base_url):
    sign_in(page, base_url, HEAD)
    open_work(page, base_url)

    team_row(page, FORMER_NAME).click()
    page.wait_for_load_state("networkidle")
    page.get_by_role("link", name=FORMER_OWNER_TITLE).first.click()
    page.wait_for_load_state("networkidle")
    expect(page.get_by_role("heading", name=FORMER_OWNER_TITLE)).to_be_visible()
