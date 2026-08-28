"""Who may read these facts, who may write them, and who may confirm a victory.

Two separate questions, and the tests keep them separate.

**Reading** is the existing derived-visibility rule: a structured fact on a
RESTRICTED Matter is itself restricted, and the restriction must hold before
anything is counted, grouped or offered as a filter option. A total that changes
when a restricted record exists is a disclosure even when no row is rendered
(Stage-2G brief 31).

**Writing** is a business role. The department maintains these lists together, so
authorship is not narrowed to the Matter owner — but a reader may not write, a
technical administrator gains nothing from being one, and the shared-gate
sentinel is not a person and can never be an actor (Stage-2G brief 25, 29).
"""

from __future__ import annotations

from datetime import date

import pytest
from django.urls import reverse

from app.core.authorization import (
    DEPARTMENT_VIEWER,
    may_review_work_victory,
    may_write_business_content,
)
from app.core.enums import Visibility
from app.intelligence import selectors
from app.intelligence.enums import WorkVictoryStatus
from app.intelligence.services import (
    add_effective_date,
    add_important_date,
    add_work_victory_candidate,
)
from app.workflow.dates import year_bounds
from app.workflow.enums import DatePrecision
from tests import factories

pytestmark = pytest.mark.django_db

FUTURE = date(2030, 5, 20)


@pytest.fixture
def strict_client():
    """A client that actually checks the CSRF token.

    Django's test client disables the check by default, so a suite that only
    used the default client would prove nothing about the protection the write
    routes rely on.
    """
    from django.test import Client

    return Client(enforce_csrf_checks=True)


@pytest.fixture
def world(specialist, other_specialist, department_head):
    """One normal Matter and one restricted Matter, each carrying all three facts."""
    normal = factories.MatterFactory(owner=specialist)
    restricted = factories.MatterFactory(owner=specialist, visibility=Visibility.RESTRICTED)

    for matter, label in ((normal, "avalik"), (restricted, "piiratud")):
        add_important_date(
            matter=matter,
            title=f"Tähtaeg {label}",
            date_value=FUTURE,
            period_end=FUTURE,
            actor=specialist,
        )
        add_effective_date(
            matter=matter,
            description=f"jõustumine {label}",
            date_value=FUTURE,
            period_end=FUTURE,
            actor=specialist,
        )
        start, end = year_bounds(2029)
        add_work_victory_candidate(
            matter=matter,
            title=f"Töövõit {label}",
            period_date=start,
            period_end=end,
            date_precision=DatePrecision.YEAR,
            actor=specialist,
        )
    return {"normal": normal, "restricted": restricted}


# -- reading ----------------------------------------------------------------


def test_the_owner_sees_both_matters(world, specialist):
    rows = selectors.calendar_rows(user=specialist, today=date(2030, 1, 1), direction=selectors.ALL)
    assert rows.count() == 4  # two milestones and two commencements


def test_an_unrelated_specialist_sees_neither_the_row_nor_the_count(world, reader):
    rows = selectors.calendar_rows(user=reader, today=date(2030, 1, 1), direction=selectors.ALL)
    assert rows.count() == 2

    entries = selectors.hydrate_calendar(list(rows), reader)
    assert all("piiratud" not in entry.title for entry in entries)


def test_the_department_head_sees_restricted_records_by_role(world, department_head):
    rows = selectors.calendar_rows(
        user=department_head, today=date(2030, 1, 1), direction=selectors.ALL
    )
    assert rows.count() == 4


def test_a_technical_administrator_gains_no_business_sight(world, administrator):
    rows = selectors.calendar_rows(
        user=administrator, today=date(2030, 1, 1), direction=selectors.ALL
    )
    assert rows.count() == 2


def test_a_collaborator_reaches_the_restricted_matters_facts(world, other_specialist):
    world["restricted"].collaborators.add(other_specialist)
    rows = selectors.calendar_rows(
        user=other_specialist, today=date(2030, 1, 1), direction=selectors.ALL
    )
    assert rows.count() == 4


def test_the_year_options_do_not_leak_a_restricted_matters_year(specialist, reader):
    restricted = factories.MatterFactory(owner=specialist, visibility=Visibility.RESTRICTED)
    add_important_date(
        matter=restricted,
        title="Ainult siin",
        date_value=date(2042, 3, 1),
        period_end=date(2042, 3, 1),
        actor=specialist,
    )

    assert 2042 in selectors.important_date_years(specialist)
    assert 2042 not in selectors.important_date_years(reader)


def test_the_work_victory_counts_are_scoped_before_they_are_counted(world, specialist, reader):
    """Scoped, and the scope is now the department for a lawyer.

    Both lawyers count the same two candidates, because both may read both
    Matters. The scoping itself is still load-bearing and still asserted — by a
    reader, who may read neither the restricted Matter nor its claim.
    """
    assert selectors.work_victory_counts(specialist)[WorkVictoryStatus.CANDIDATE.value] == 2
    assert selectors.work_victory_counts(reader)[WorkVictoryStatus.CANDIDATE.value] == 1


def test_the_undated_commencement_count_is_scoped(specialist, reader):
    from app.intelligence.enums import EffectiveDateKind

    restricted = factories.MatterFactory(owner=specialist, visibility=Visibility.RESTRICTED)
    add_effective_date(matter=restricted, kind=EffectiveDateKind.GENERAL_ORDER, actor=specialist)

    assert selectors.undated_effective_count(specialist) == 1
    assert selectors.undated_effective_count(reader) == 0


def test_the_shared_gate_viewer_sees_normal_records_only(world):
    rows = selectors.calendar_rows(
        user=DEPARTMENT_VIEWER, today=date(2030, 1, 1), direction=selectors.ALL
    )
    assert rows.count() == 2


# -- writing ----------------------------------------------------------------


def test_who_may_add_business_content(specialist, department_head, administrator, superuser):
    reader = factories.UserFactory(role="READER")

    assert may_write_business_content(specialist) is True
    assert may_write_business_content(department_head) is True
    assert may_write_business_content(reader) is False
    assert may_write_business_content(administrator) is False
    assert may_write_business_content(superuser) is False
    assert may_write_business_content(DEPARTMENT_VIEWER) is False
    assert may_write_business_content(None) is False


def test_only_the_department_head_may_review_a_work_victory(
    specialist, department_head, administrator
):
    assert may_review_work_victory(department_head) is True
    assert may_review_work_victory(specialist) is False
    assert may_review_work_victory(administrator) is False
    assert may_review_work_victory(DEPARTMENT_VIEWER) is False


def test_an_inactive_user_may_not_write(specialist):
    specialist.is_active = False
    assert may_write_business_content(specialist) is False


# -- the write routes -------------------------------------------------------


def _add_url(matter):
    return reverse("intelligence:add_important_date", kwargs={"matter_id": matter.pk})


def test_a_reader_gets_no_write_route(client, world):
    reader = factories.UserFactory(role="READER")
    client.force_login(reader)

    response = client.post(
        _add_url(world["normal"]),
        {"title": "Ei tohiks", "precision": "YEAR", "year": "2030"},
    )
    # 404, not the 403 this module used to answer. Business-write refusals
    # are one answer across the application now, and it is the one that
    # tells a reader nothing about what exists for somebody else
    # (app/core/decorators.py, AUTH-002).
    assert response.status_code == 404
    assert world["normal"].important_dates.count() == 1


def test_an_administrator_gets_no_write_route(client, world, administrator):
    client.force_login(administrator)
    response = client.post(
        _add_url(world["normal"]),
        {"title": "Ei tohiks", "precision": "YEAR", "year": "2030"},
    )
    # 404, not the 403 this module used to answer. Business-write refusals
    # are one answer across the application now, and it is the one that
    # tells a reader nothing about what exists for somebody else
    # (app/core/decorators.py, AUTH-002).
    assert response.status_code == 404


def test_a_specialist_may_write_on_a_matter_they_do_not_own(client, world, other_specialist):
    """Collaborative by design: these lists were shared in OneNote too."""
    client.force_login(other_specialist)
    response = client.post(
        _add_url(world["normal"]),
        {"title": "Kolleegi lisatud tähtaeg", "precision": "YEAR", "year": "2030"},
    )

    assert response.status_code == 302
    record = world["normal"].important_dates.get(title="Kolleegi lisatud tähtaeg")
    assert record.created_by == other_specialist


def test_a_restricted_matter_is_writable_by_any_lawyer(client, world, other_specialist):
    """Collaborative write, on the wider set of Matters docs/adr/0042 opened.

    This asserted 404 until the department-wide decision. `may_write_business_content`
    was always role-based and already said ownership is not the write boundary,
    so what changed here is the set of Matters a lawyer can reach, not the rule
    applied to them: a specialist may now work a colleague's RESTRICTED file
    exactly as they could already work their NORMAL one.
    """
    client.force_login(other_specialist)
    response = client.post(
        _add_url(world["restricted"]),
        {"title": "Kolleegi lisatud", "precision": "YEAR", "year": "2030"},
    )
    assert response.status_code in (200, 302), response.status_code


def test_a_restricted_matter_is_not_writable_by_a_reader(client, world, reader):
    """404 rather than 403: a 403 would confirm the Matter exists."""
    client.force_login(reader)
    response = client.post(
        _add_url(world["restricted"]),
        {"title": "Ei tohiks", "precision": "YEAR", "year": "2030"},
    )
    assert response.status_code == 404


def test_a_specialist_cannot_confirm_a_work_victory_through_the_route(client, world, specialist):
    record = world["normal"].work_victories.first()
    client.force_login(specialist)
    response = client.post(
        reverse(
            "intelligence:confirm_work_victory",
            kwargs={"matter_id": world["normal"].pk, "pk": record.pk},
        )
    )

    assert response.status_code == 403
    record.refresh_from_db()
    assert record.status == WorkVictoryStatus.CANDIDATE


def test_the_department_head_can_confirm_through_the_route(client, world, department_head):
    record = world["normal"].work_victories.first()
    client.force_login(department_head)
    response = client.post(
        reverse(
            "intelligence:confirm_work_victory",
            kwargs={"matter_id": world["normal"].pk, "pk": record.pk},
        )
    )

    assert response.status_code == 302
    record.refresh_from_db()
    assert record.status == WorkVictoryStatus.CONFIRMED
    assert record.confirmed_by == department_head


def test_an_anonymous_post_is_bounced_to_sign_in(client, world):
    response = client.post(
        _add_url(world["normal"]),
        {"title": "Ei tohiks", "precision": "YEAR", "year": "2030"},
    )
    assert response.status_code == 302
    assert "/konto/" in response["Location"]
    assert world["normal"].important_dates.count() == 1


def test_csrf_protection_is_enforced_on_a_write(strict_client, world, specialist):
    strict_client.force_login(specialist)
    response = strict_client.post(
        _add_url(world["normal"]),
        {"title": "Ilma tokenita", "precision": "YEAR", "year": "2030"},
    )
    assert response.status_code == 403
