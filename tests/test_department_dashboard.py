"""Osakonna töö, and what the restored portfolio does to the other surfaces.

Three things are being proved here and they are worth naming separately.

**The role gate holds.** Only the department head reaches the page, and a
reader without the role is told it does not exist rather than that they may not
see it.

**The numbers are the same numbers.** The head's counts come from the same
selectors Ülevaade uses, so a Matter cannot be overdue on one page and not on
the other. Where a count links to a list, the list holds exactly those rows.

**Nothing became a ranking.** The lawyer table is alphabetical, carries no
score, and is never summed into one figure per person.
"""

from __future__ import annotations

import datetime as dt

import pytest
from django.urls import reverse
from django.utils import timezone

from app.core.authorization import DEPARTMENT_VIEWER, is_department_head
from app.legacy_import.current_register import apply_promotion_plan, build_promotion_plan
from app.legacy_import.owner_backfill import apply_backfill_plan, build_backfill_plan
from app.matters import dashboard as overview
from app.matters.department_dashboard import (
    build_department_work,
    lawyer_matrix,
    summary_cards,
    unassigned_matters,
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
    UNASSIGNED,
    Portfolio,
    build_portfolio,
)

pytestmark = pytest.mark.django_db

WORK_URL = "/osakonna-too/"


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
    assert "Osakonna töö" in response.content.decode()


def test_a_specialist_is_told_the_page_does_not_exist(client, portfolio: Portfolio) -> None:
    """404 rather than 403: a 403 confirms the surface exists."""
    client.force_login(portfolio.people.sandra)
    assert client.get(WORK_URL).status_code == 404


def test_a_technical_administrator_does_not_inherit_the_page(client, portfolio: Portfolio) -> None:
    client.force_login(factories.AdministratorFactory())
    assert client.get(WORK_URL).status_code == 404


def test_a_superuser_does_not_inherit_the_page(client, portfolio: Portfolio) -> None:
    client.force_login(factories.AdministratorFactory(is_superuser=True))
    assert client.get(WORK_URL).status_code == 404


def test_a_reader_does_not_reach_the_page(client, portfolio: Portfolio) -> None:
    from app.accounts.enums import UserRole

    client.force_login(factories.UserFactory(role=UserRole.READER))
    assert client.get(WORK_URL).status_code == 404


def test_nobody_signed_in_is_redirected_rather_than_shown_the_page(client) -> None:
    """Including a shared-gate session that has chosen no persona.

    The department password proves somebody is behind the door. It says
    nothing about who, so it cannot reach a surface defined by who you are.
    """
    response = client.get(WORK_URL)
    assert response.status_code == 302
    assert response.headers["Location"].startswith(reverse("accounts:dev_login"))


def test_the_navigation_offers_the_page_only_to_the_head(client, portfolio: Portfolio) -> None:
    client.force_login(portfolio.people.sandra)
    assert WORK_URL not in client.get(reverse("matters:overview")).content.decode()

    client.force_login(portfolio.people.head)
    assert WORK_URL in client.get(reverse("matters:overview")).content.decode()


# =========================================================================
# Restricted content
# =========================================================================


def test_the_head_sees_a_restricted_matter_because_the_role_entitles_it(
    portfolio: Portfolio,
) -> None:
    """Not because this page decided so — DEPARTMENT_HEAD already carries it."""
    work = build_department_work(portfolio.people.head)
    counted = {row.matter.title for row in work.attention}
    counted |= {matter.title for matter in work.incoming}
    assert RESTRICTED in counted


def test_a_restricted_matter_the_head_can_count_is_absent_for_an_unrelated_specialist(
    portfolio: Portfolio,
) -> None:
    """The same number, two readers, two different answers — by design."""
    restricted = Matter.objects.get(title=RESTRICTED)
    assert restricted.owner == portfolio.people.sandra

    head_total = overview.active_matters(portfolio.people.head).filter(pk=restricted.pk).count()
    other_total = overview.active_matters(portfolio.people.martin).filter(pk=restricted.pk).count()
    assert head_total == 1
    assert other_total == 0


def test_a_specialist_sees_their_own_restricted_matter(portfolio: Portfolio) -> None:
    restricted = Matter.objects.get(title=RESTRICTED)
    assert overview.active_matters(portfolio.people.sandra).filter(pk=restricted.pk).count() == 1


# =========================================================================
# The lawyer matrix
# =========================================================================


def test_the_matrix_lists_current_caseworkers_alphabetically(portfolio: Portfolio) -> None:
    """Oversight, not a leaderboard. Ordering is the guard."""
    rows = lawyer_matrix(portfolio.people.head)
    names = [row.display_name for row in rows]
    assert names == sorted(names)


def test_the_matrix_includes_the_head_and_every_active_specialist(portfolio: Portfolio) -> None:
    names = {row.display_name for row in lawyer_matrix(portfolio.people.head)}
    for person in [*portfolio.people.specialists, portfolio.people.head]:
        assert person.display_name in names


def test_a_departed_colleague_appears_only_while_they_still_hold_live_work(
    portfolio: Portfolio,
) -> None:
    """Surfacing the anomaly beats hiding the Matter.

    Dropping the row would take an open file off the one page whose job is to
    find open files, so the row stays and says why it is there.
    """
    former = portfolio.people.former
    rows = {row.display_name: row for row in lawyer_matrix(portfolio.people.head)}
    assert former.display_name not in rows, "an archive-only owner is not on today's team"

    # Give the departed colleague an open FULL Matter and they reappear, flagged.
    Matter.objects.filter(title=OWNED_CANDIDATE).update(owner=former)
    rows = {row.display_name: row for row in lawyer_matrix(portfolio.people.head)}
    assert rows[former.display_name].is_former_member is True


def test_the_active_count_matches_the_list_it_links_to(client, portfolio: Portfolio) -> None:
    """A number that links to a list is a promise about that list."""
    client.force_login(portfolio.people.head)
    row = next(
        r
        for r in lawyer_matrix(portfolio.people.head)
        if r.display_name == portfolio.people.sandra.display_name
    )
    response = client.get(row.active.url)
    assert response.status_code == 200
    assert response.context["page"].paginator.count == row.active.count


def test_an_overdue_count_is_only_ever_a_late_do_action(portfolio: Portfolio) -> None:
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

    cards = {card.key: card.count for card in summary_cards(head, today)}
    assert cards["overdue"] == 0, "waiting past a review date is not overdue"
    assert cards["review_due"] >= 1


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

    cards = {card.key: card.count for card in summary_cards(head, today)}
    assert cards["overdue"] == 1


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

    head_cards = {card.key: card.count for card in summary_cards(head, today)}
    overview_cards = {card.key: card.count for card in overview.summary_cards(head, today)}
    assert head_cards["overdue"] == overview_cards["overdue"]
    assert head_cards["active"] == overview_cards["active"]


def test_the_matrix_does_not_query_once_per_lawyer(
    django_assert_max_num_queries, portfolio: Portfolio
) -> None:
    """Six grouped counts, one for the people, and a few scope lookups.

    A budget rather than an exact number, because the interesting property is
    that it does not move with the headcount. Eleven more colleagues here; a
    per-lawyer-per-metric implementation would be into the seventies. The real
    department is small enough that the naive shape would work and still be
    wrong — a query count that grows when somebody is hired is a page that
    degrades exactly when it matters (Stage-2F brief 47).
    """
    for index in range(6):
        factories.UserFactory(display_name=f"Lisajurist {index}")

    with django_assert_max_num_queries(16):
        list(lawyer_matrix(portfolio.people.head))


# =========================================================================
# Unassigned and the rest of the page
# =========================================================================


def test_unassigned_holds_live_work_only(portfolio: Portfolio) -> None:
    titles = {matter.title for matter in unassigned_matters(portfolio.people.head)}
    assert UNASSIGNED in titles
    assert HISTORICAL not in titles, "a decade of ownerless archive rows is not a queue"


def test_the_page_renders_every_section(client, portfolio: Portfolio) -> None:
    client.force_login(portfolio.people.head)
    body = client.get(WORK_URL).content.decode()
    for heading in (
        "Juristid",
        "Tähelepanu kogu osakonnas",
        "Lähenevad kuupäevad",
        "Vastutajata teemad",
        "Hiljuti saabunud",
    ):
        assert heading in body


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
