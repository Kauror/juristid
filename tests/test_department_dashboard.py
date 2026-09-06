"""Osakond's read model, and what the restored portfolio does to the other surfaces.

Three things are being proved here and they are worth naming separately.

**The manager boundary holds.** The page itself is every reader's — it replaced
Ülevaade — but Meeskond and Tehtud are the department head's, and for anybody
else they are not built at all. The route-level contract is
`tests/test_department_page.py`; what is asserted here is the read model behind
those sections.

**The numbers are the same numbers.** The head's counts come from the same
selectors the rest of the page uses, so a Matter cannot be overdue in the strip
and not in the table. Where a count links to a list, the list holds exactly
those rows.

**Nothing became a ranking.** The lawyer table is alphabetical, carries no
score, and is never summed into one figure per person.
"""

from __future__ import annotations

import datetime as dt

import pytest
from django.utils import timezone

from app.core.authorization import DEPARTMENT_VIEWER, is_department_head
from app.legacy_import.current_register import apply_promotion_plan, build_promotion_plan
from app.legacy_import.owner_backfill import apply_backfill_plan, build_backfill_plan
from app.matters import dashboard as overview
from app.matters.department import build_department
from app.matters.department_dashboard import (
    TEAM_COLUMNS,
    incoming_rail,
    seis_figures,
    team_rows,
)
from app.matters.models import Matter
from app.matters.selectors import my_active_matters
from app.workflow.enums import ActionKind, DateSemantics
from app.workflow.services import set_next_action
from tests import factories
from tests.synthetic_portfolio import (
    CURRENT_YEAR,
    HISTORICAL,
    OWNED_CANDIDATE,
    RESTRICTED,
    Portfolio,
    build_portfolio,
)

pytestmark = pytest.mark.django_db

WORK_URL = "/osakond/"


@pytest.fixture
def portfolio() -> Portfolio:
    """The synthetic world after Stage 2F has done its work.

    Both operations, in the order an operator would run them: owners first, so
    the promotion can report what it is about to activate, then the promotion.
    """
    built = build_portfolio()
    apply_backfill_plan(build_backfill_plan())
    apply_promotion_plan(build_promotion_plan(year=CURRENT_YEAR))
    return built


# =========================================================================
# The role gate
# =========================================================================


def test_the_role_helper_recognises_only_the_department_head() -> None:
    assert is_department_head(factories.DepartmentHeadFactory())
    assert not is_department_head(factories.UserFactory())
    assert not is_department_head(factories.AdministratorFactory())
    assert not is_department_head(factories.AdministratorFactory(is_superuser=True))
    assert not is_department_head(DEPARTMENT_VIEWER)
    assert not is_department_head(None)


def test_a_departed_head_no_longer_opens_the_page() -> None:
    """Deactivating an account is the whole of removing this access."""
    head = factories.DepartmentHeadFactory(is_active=False)
    assert not is_department_head(head)


def test_the_head_reaches_the_page(client, portfolio: Portfolio) -> None:
    client.force_login(portfolio.people.head)
    response = client.get(WORK_URL)
    assert response.status_code == 200
    assert "Osakond" in response.content.decode()


def test_a_specialist_reaches_the_page_without_the_manager_sections(
    client, portfolio: Portfolio
) -> None:
    """The page is no longer manager-only; two of its sections still are.

    It replaced Ülevaade, which every reader could open, so refusing it would
    take the department view away from the people who had it. What stayed
    behind the role is Meeskond and Tehtud (docs/adr/0049).
    """
    client.force_login(portfolio.people.sandra)
    response = client.get(WORK_URL)
    assert response.status_code == 200
    body = response.content.decode()
    assert "uxstat" not in body
    assert "Tehtud" not in body
    assert "Vajab sekkumist" in body


def test_a_technical_administrator_reads_the_page_but_not_the_team(
    client, portfolio: Portfolio
) -> None:
    """Technical administration is not business access, and never was."""
    client.force_login(factories.AdministratorFactory())
    assert "uxstat" not in client.get(WORK_URL).content.decode()


def test_a_superuser_does_not_inherit_the_team_table(client, portfolio: Portfolio) -> None:
    client.force_login(factories.AdministratorFactory(is_superuser=True))
    assert "uxstat" not in client.get(WORK_URL).content.decode()


def test_a_reader_does_not_reach_the_team_table(client, portfolio: Portfolio) -> None:
    from app.accounts.enums import UserRole

    client.force_login(factories.UserFactory(role=UserRole.READER))
    assert "uxstat" not in client.get(WORK_URL).content.decode()


def test_the_navigation_offers_one_department_destination(client, portfolio: Portfolio) -> None:
    """One item, the same one, whatever the role.

    There were two: a universal `Ülevaade` and a head-only `Osakond` inside
    «Veel», which is one reader being offered two answers to one question.
    """
    for person in (portfolio.people.sandra, portfolio.people.head):
        client.force_login(person)
        body = client.get(WORK_URL).content.decode()
        assert body.count('href="/osakond/"') == 1
        assert ">Ülevaade<" not in body
        assert ">Osakonna töö<" not in body


# =========================================================================
# Restricted content
# =========================================================================


def test_the_head_counts_a_restricted_matter_because_the_role_entitles_it(
    portfolio: Portfolio,
) -> None:
    """Not because this page decided so — DEPARTMENT_HEAD already carries it.

    Asserted on the team table, which is where the page counts Matters now: the
    same person's row, read by three people. Both lawyers agree since
    docs/adr/0042; somebody outside the legal team is short by exactly the
    restricted file.
    """
    sandra = portfolio.people.sandra
    restricted = Matter.objects.get(title=RESTRICTED)
    assert restricted.owner == sandra

    seen_by_head = cell_of(portfolio.people.head, sandra.display_name, "open").value
    seen_by_lawyer = cell_of(portfolio.people.martin, sandra.display_name, "open").value
    seen_by_reader = cell_of(portfolio.people.reader, sandra.display_name, "open").value
    assert seen_by_head == seen_by_lawyer
    assert seen_by_head == seen_by_reader + 1


def test_a_restricted_matter_the_head_can_count_is_absent_for_an_unrelated_specialist(
    portfolio: Portfolio,
) -> None:
    """The same number, three readers — by design.

    Both lawyers count it since docs/adr/0042; somebody outside the legal team
    does not, which is the scoping this asserts.
    """
    restricted = Matter.objects.get(title=RESTRICTED)
    assert restricted.owner == portfolio.people.sandra

    head_total = overview.active_matters(portfolio.people.head).filter(pk=restricted.pk).count()
    lawyer_total = overview.active_matters(portfolio.people.martin).filter(pk=restricted.pk).count()
    reader_total = overview.active_matters(portfolio.people.reader).filter(pk=restricted.pk).count()
    assert head_total == 1
    assert lawyer_total == 1
    assert reader_total == 0


def test_a_specialist_sees_their_own_restricted_matter(portfolio: Portfolio) -> None:
    restricted = Matter.objects.get(title=RESTRICTED)
    assert overview.active_matters(portfolio.people.sandra).filter(pk=restricted.pk).count() == 1


# =========================================================================
# Meeskond
# =========================================================================


def people_rows(user):
    """The team table without the unassigned pile and the total."""
    return [row for row in team_rows(user) if not row.is_unassigned and not row.is_total]


def cell_of(user, name: str, column: str):
    """One person's cell in one named column, located by the column list."""
    index = next(
        position
        for position, (key, _label, _group, _sep) in enumerate(TEAM_COLUMNS)
        if key == column
    )
    row = next(row for row in team_rows(user) if row.name == name)
    return row.cells[index]


def test_the_team_lists_current_caseworkers_alphabetically(portfolio: Portfolio) -> None:
    """Oversight, not a leaderboard. Ordering is the guard."""
    names = [row.name for row in people_rows(portfolio.people.head)]
    assert names == sorted(names)


def test_the_team_includes_the_head_and_every_active_specialist(portfolio: Portfolio) -> None:
    names = {row.name for row in people_rows(portfolio.people.head)}
    for person in [*portfolio.people.specialists, portfolio.people.head]:
        assert person.display_name in names


def test_the_reader_is_marked_on_their_own_row(portfolio: Portfolio) -> None:
    rows = {row.name: row for row in people_rows(portfolio.people.head)}
    assert rows[portfolio.people.head.display_name].is_self
    assert not rows[portfolio.people.sandra.display_name].is_self


def test_a_departed_colleague_appears_only_while_they_still_hold_live_work(
    portfolio: Portfolio,
) -> None:
    """Surfacing the anomaly beats hiding the Matter.

    Dropping the row would take an open file off the one page whose job is to
    find open files, so the row stays and says why it is there.
    """
    former = portfolio.people.former
    rows = {row.name: row for row in people_rows(portfolio.people.head)}
    assert former.display_name not in rows, "an archive-only owner is not on today's team"

    # Give the departed colleague an open FULL Matter and they reappear, flagged.
    Matter.objects.filter(title=OWNED_CANDIDATE).update(owner=former)
    rows = {row.name: row for row in people_rows(portfolio.people.head)}
    assert rows[former.display_name].is_former is True


def test_work_nobody_carries_has_its_own_row(portfolio: Portfolio) -> None:
    """It is on nobody's personal list by definition, which is why it is here."""
    unassigned = next(row for row in team_rows(portfolio.people.head) if row.is_unassigned)
    assert unassigned.cells[0].value >= 1


def test_the_open_count_matches_the_list_it_links_to(client, portfolio: Portfolio) -> None:
    """A number that links to a list is a promise about that list."""
    client.force_login(portfolio.people.head)
    cell = cell_of(portfolio.people.head, portfolio.people.sandra.display_name, "open")
    response = client.get(cell.url)
    assert response.status_code == 200
    assert response.context["page"].paginator.count == cell.value


def test_the_three_history_columns_carry_no_link_they_cannot_keep(
    portfolio: Portfolio,
) -> None:
    """The register lists Matters by their current state, so a link from a
    column counting last week would open a list that does not match it."""
    row = people_rows(portfolio.people.head)[0]
    for column in ("changed", "sent_week", "sent_year"):
        assert cell_of(portfolio.people.head, row.name, column).url == ""


def test_an_overdue_figure_is_only_ever_a_late_do_action(portfolio: Portfolio) -> None:
    """A review date reached is not a missed deadline (specification 18.8)."""
    head = portfolio.people.head
    today = timezone.localdate()
    matter = Matter.objects.get(title=OWNED_CANDIDATE)

    set_next_action(
        matter=matter,
        text="Vaata menetlust uuesti",
        kind=ActionKind.WAIT,
        date_semantics=DateSemantics.REVIEW_ON,
        target_date=today - dt.timedelta(days=5),
        actor=head,
    )

    figures = {figure.key: figure.value for figure in seis_figures(head, today)}
    assert figures["overdue"] == 0, "waiting past a review date is not overdue"


def test_a_genuinely_late_action_is_counted_as_overdue(portfolio: Portfolio) -> None:
    head = portfolio.people.head
    today = timezone.localdate()
    matter = Matter.objects.get(title=OWNED_CANDIDATE)

    set_next_action(
        matter=matter,
        text="Saada arvamus",
        kind=ActionKind.DO,
        date_semantics=DateSemantics.DEADLINE,
        target_date=today - dt.timedelta(days=2),
        actor=head,
    )

    figures = {figure.key: figure.value for figure in seis_figures(head, today)}
    assert figures["overdue"] == 1


def test_the_head_page_and_ulevaade_agree_about_what_is_overdue(portfolio: Portfolio) -> None:
    head = portfolio.people.head
    today = timezone.localdate()
    set_next_action(
        matter=Matter.objects.get(title=OWNED_CANDIDATE),
        text="Saada arvamus",
        kind=ActionKind.DO,
        date_semantics=DateSemantics.DEADLINE,
        target_date=today - dt.timedelta(days=2),
        actor=head,
    )

    figures = {figure.key: figure.value for figure in seis_figures(head, today)}
    overview_cards = {card.key: card.count for card in overview.summary_cards(head, today)}
    assert figures["overdue"] == overview_cards["overdue"]


def test_the_team_table_does_not_query_once_per_lawyer(
    django_assert_max_num_queries, portfolio: Portfolio
) -> None:
    """Nine grouped counts, one for the people, and a few scope lookups.

    A budget rather than an exact number, because the interesting property is
    that it does not move with the headcount. Six more colleagues here; a
    per-lawyer-per-column implementation would be into the hundreds. The real
    department is small enough that the naive shape would work and still be
    wrong — a query count that grows when somebody is hired is a page that
    degrades exactly when it matters (Stage-2F brief 47).

    Was 30, measured at 10. The nine grouped counts and the read of the people
    are still there; what left is the scope lookup the docstring counted as "a
    few", which PERF-01 stopped asking once per read. 14 keeps the ceiling well
    under the shape it exists to catch: seven lawyers here, so a per-lawyer
    implementation lands far above it whatever the constant part costs.
    """
    for index in range(6):
        factories.UserFactory(display_name=f"Lisajurist {index}")

    with django_assert_max_num_queries(14):
        list(team_rows(portfolio.people.head))


# =========================================================================
# The page
# =========================================================================


def test_the_total_row_reconciles_with_the_risk_strip(portfolio: Portfolio) -> None:
    """Where two numbers on this page count the same population, they agree.

    The head is the one reader who looks at both, and two figures that ought to
    match and do not is how somebody stops believing either. Guaranteed by
    construction — the total is the sum of the rows above it rather than a tenth
    set of queries — and asserted so the construction cannot quietly change.
    """
    today = timezone.localdate()
    work = build_department(portfolio.people.head, is_head=True, today=today)
    total = next(row for row in work.team if row.is_total)
    unassigned = next(row for row in work.team if row.is_unassigned)
    figures = {figure.key: figure.value for figure in work.seis}

    index = {key: position for position, (key, _l, _g, _s) in enumerate(TEAM_COLUMNS)}
    assert total.cells[index["overdue"]].value == figures["overdue"]
    assert total.cells[index["week"]].value == figures["week"]
    assert unassigned.cells[index["open"]].value == figures["unassigned"]
    assert total.cells[index["open"]].value == work.open_matters


def test_the_incoming_rail_and_the_strip_agree_about_unreviewed_work(
    portfolio: Portfolio,
) -> None:
    """The same filter appears in two places and must not give two answers."""
    today = timezone.localdate()
    figures = {figure.key: figure.value for figure in seis_figures(portfolio.people.head, today)}
    rail = {row.label: row for row in incoming_rail(portfolio.people.head, today)}

    assert rail["Uut läbi vaatamata"].count == figures["unreviewed"]


def test_the_page_renders_every_section(client, portfolio: Portfolio) -> None:
    client.force_login(portfolio.people.head)
    body = client.get(WORK_URL).content.decode()
    # No «SEIS» label in front of the strip and «Vajab sekkumist» in the rail:
    # the figures caption themselves, and the wording is the one 01-EHITUSJUHIS
    # §4 settles (docs/design-v2-compatibility.md, DS-01, DS-11).
    for heading in ("Meeskond", "Eesolev", "Tehtud", "Vajab sekkumist", "Aruandlus"):
        assert heading in body


def test_the_page_uses_the_words_the_handoff_settled_on(client, portfolio: Portfolio) -> None:
    """«läbi vaatamata», never «triaaž»; «Muutusteta 30 p», never «seisma jäänud».

    Both replacements name a state of the file. The words they replace name a
    process nobody here runs, and a judgement about the person carrying it.
    """
    client.force_login(portfolio.people.head)
    body = client.get(WORK_URL).content.decode()

    assert "läbi vaatamata" in body
    assert "Muutusteta 30 p" in body
    lowered = body.casefold()
    assert "triaaž" not in lowered
    assert "seisma jäänud" not in lowered


def test_the_page_never_ranks_or_scores_anybody(client, portfolio: Portfolio) -> None:
    """The words that would make this a staff evaluation, absent by assertion."""
    client.force_login(portfolio.people.head)
    body = client.get(WORK_URL).content.decode().casefold()
    for forbidden in ("töökoormus", "tulemuslikkus", "produktiivsus", "edetabel", "punktisumma"):
        assert forbidden not in body


# =========================================================================
# What the restored portfolio does to the existing surfaces
# =========================================================================


def test_ulevaade_shows_the_restored_current_work(portfolio: Portfolio) -> None:
    """Both operations together are what makes the existing page useful."""
    head = portfolio.people.head
    before = overview.active_matters(head).count()
    assert before >= 4

    owners = {row.label: row.count for row in overview.owner_inventory(head)}
    assert portfolio.people.sandra.display_name in owners
    assert owners["Vastutajata"] >= 1


def test_minu_too_shows_a_specialist_their_own_promoted_matters(portfolio: Portfolio) -> None:
    sandra = portfolio.people.sandra
    titles = {matter.title for matter in my_active_matters(sandra)}
    assert OWNED_CANDIDATE in titles


def test_minu_too_does_not_show_one_specialists_work_to_another(portfolio: Portfolio) -> None:
    titles = {matter.title for matter in my_active_matters(portfolio.people.martin)}
    assert OWNED_CANDIDATE not in titles


def test_a_matter_with_no_next_action_is_still_in_the_owner_inventory(
    portfolio: Portfolio,
) -> None:
    """`Järgmiseks puudub` is current data quality, not a reason to hide a file."""
    sandra = portfolio.people.sandra
    matter = Matter.objects.get(title=OWNED_CANDIDATE)
    assert not matter.next_actions.exists()
    assert matter in list(my_active_matters(sandra))


def test_an_archive_matter_is_not_current_work(portfolio: Portfolio) -> None:
    former = portfolio.people.former
    titles = {matter.title for matter in my_active_matters(former)}
    assert HISTORICAL not in titles, "an archive record is history, not a work queue"
