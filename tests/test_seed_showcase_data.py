"""The six-package showcase world (`seed_showcase_data`).

The showcase is a fixture people read in production, so the seeder is tested
like an operator command: what it refuses, what one run creates, what a second
run does not, and whether the worlds it builds satisfy the same rules the
product enforces.
"""

from __future__ import annotations

import pytest
from django.core.management import call_command
from django.core.management.base import CommandError
from django.utils import timezone

from app.core.enums import Visibility
from app.core.invariants import check_domain_invariants
from app.core.management.commands.seed_showcase_data import (
    TITLE_JAATME,
    TITLE_KLIIMA,
    TITLE_KUTSE,
    TITLE_PIIRATUD,
    TITLE_TAIKS,
    TITLE_TAKS,
    TITLE_TOOTASU,
)
from app.matters.enums import (
    MatterDataClass,
    ResponseDeadlineOutcome,
    WebsiteOverviewKind,
    WebsiteOverviewStatus,
)
from app.matters.models import (
    Matter,
    MatterEngagement,
    MatterResponseDeadline,
    MatterWebsiteOverview,
)
from app.related_materials.models import MatterRelation
from app.workflow.enums import ActionStatus, Disposition
from app.workflow.models import NextAction
from tests import factories

pytestmark = pytest.mark.django_db

ALL_TITLES = [
    TITLE_KLIIMA,
    TITLE_JAATME,
    TITLE_TAIKS,
    TITLE_TAKS,
    TITLE_TOOTASU,
    TITLE_KUTSE,
    TITLE_PIIRATUD,
]


@pytest.fixture
def lawyers(db):
    head = factories.DepartmentHeadFactory(display_name="Marko Näidis")
    specialist = factories.UserFactory(display_name="Ireen Näidis")
    return head, specialist


def seed() -> None:
    call_command("seed_showcase_data", verbosity=0)


def by_title(title: str) -> Matter:
    return Matter.objects.get(title=title)


def test_one_run_creates_all_six_packages_as_test_data(lawyers):
    seed()

    assert Matter.objects.count() == 7
    for title in ALL_TITLES:
        assert Matter.objects.filter(title=title).exists(), title
    # Every business Matter is TEST; the command cannot create REAL data.
    assert Matter.objects.exclude(data_class=MatterDataClass.TEST).count() == 0


def test_rerun_does_not_duplicate_packages(lawyers):
    seed()
    before = {
        "matters": Matter.objects.count(),
        "actions": NextAction.objects.count(),
        "deadlines": MatterResponseDeadline.objects.count(),
        "relations": MatterRelation.objects.count(),
    }
    seed()
    assert Matter.objects.count() == before["matters"]
    assert NextAction.objects.count() == before["actions"]
    assert MatterResponseDeadline.objects.count() == before["deadlines"]
    assert MatterRelation.objects.count() == before["relations"]


def test_current_work_package_has_dated_current_and_three_planned(lawyers):
    seed()
    kliima = by_title(TITLE_KLIIMA)
    current = NextAction.objects.filter(matter=kliima, status=ActionStatus.OPEN)
    assert current.count() == 1
    assert current.get().target_date is not None
    planned = NextAction.objects.filter(
        matter=kliima, status=ActionStatus.PLANNED, follow_up__isnull=True
    )
    assert planned.count() == 3
    assert all(action.target_date is not None for action in planned)
    # Two of the planned rows are deliberately long enough to wrap.
    assert sum(1 for action in planned if len(action.text) > 120) >= 2
    # And every opinion the package sends is being checked on, by its own
    # `Arvamuse järelkontroll` beside those three (docs/adr/0146).
    for submission in kliima.submissions.filter(status="SENT"):
        assert (
            NextAction.objects.filter(
                follow_up__submission=submission, status=ActionStatus.PLANNED
            ).count()
            == 1
        )


def test_consultation_package_waits_for_feedback(lawyers):
    seed()
    jaatme = by_title(TITLE_JAATME)
    assert jaatme.is_open
    round_ = MatterEngagement.objects.get(matter=jaatme)
    assert round_.feedback_received == ""
    assert round_.feedback_deadline is not None
    assert round_.feedback_deadline > timezone.localdate()
    assert round_.smaily_url
    assert round_.alchemer_url
    overview = MatterWebsiteOverview.objects.get(matter=jaatme)
    assert overview.kind == WebsiteOverviewKind.OVERVIEW
    assert overview.status == WebsiteOverviewStatus.PUBLISHED


def test_continuation_package_links_predecessor_to_open_successor(lawyers):
    seed()
    taks = by_title(TITLE_TAKS)
    taiks = by_title(TITLE_TAIKS)
    assert not taks.is_open
    assert taks.disposition == Disposition.SUPERSEDED
    assert taks.superseded_by_id == taiks.pk
    assert taiks.is_open
    pair = {taks.pk, taiks.pk}
    assert any(
        {relation.matter_a_id, relation.matter_b_id} == pair
        for relation in MatterRelation.objects.all()
    )


def test_repeat_deadline_package_has_answered_history_and_active_request(lawyers):
    seed()
    tootasu = by_title(TITLE_TOOTASU)

    # Deadline B: current, in the future, from a fresh request.
    assert tootasu.response_deadline is not None
    assert tootasu.response_deadline > timezone.localdate()
    assert tootasu.response_requested_at is not None

    # Deadline A: in history, ANSWERED, by a submission of this Matter.
    history = MatterResponseDeadline.objects.filter(matter=tootasu)
    answered = history.filter(outcome=ResponseDeadlineOutcome.ANSWERED)
    assert answered.count() == 1
    row = answered.get()
    assert row.submission is not None
    assert row.submission.matter_id == tootasu.pk
    # The old opinion answered A; nothing marks it an answer to B.
    assert row.deadline != tootasu.response_deadline


def test_full_dossier_package_distinguishes_overview_from_news(lawyers):
    seed()
    kutse = by_title(TITLE_KUTSE)
    kinds = set(MatterWebsiteOverview.objects.filter(matter=kutse).values_list("kind", flat=True))
    assert WebsiteOverviewKind.OVERVIEW in kinds
    assert WebsiteOverviewKind.NEWS in kinds
    round_ = MatterEngagement.objects.get(matter=kutse)
    assert round_.feedback_received  # the round is completed


def test_restricted_package_is_permission_safe(lawyers):
    seed()
    piiratud = by_title(TITLE_PIIRATUD)
    assert piiratud.visibility == Visibility.RESTRICTED

    specialist = factories.UserFactory(display_name="Teine Jurist")
    reader = factories.ReaderFactory()
    administrator = factories.AdministratorFactory()

    visible_to_specialist = Matter.objects.visible_to(specialist)
    assert piiratud in visible_to_specialist

    for outsider in (reader, administrator):
        scope = Matter.objects.visible_to(outsider)
        assert piiratud not in scope
        # The restricted side of the relation must not surface either.
        assert not any(
            piiratud.pk in (relation.matter_a_id, relation.matter_b_id)
            for relation in MatterRelation.objects.filter(matter_a__in=scope, matter_b__in=scope)
        )


def test_undated_current_action_on_the_restricted_package(lawyers):
    seed()
    piiratud = by_title(TITLE_PIIRATUD)
    current = NextAction.objects.get(matter=piiratud, status=ActionStatus.OPEN)
    assert current.target_date is None
    planned = NextAction.objects.filter(matter=piiratud, status=ActionStatus.PLANNED)
    assert planned.count() == 1
    assert planned.get().target_date is not None


def test_seeded_world_satisfies_domain_invariants(lawyers):
    seed()
    report = check_domain_invariants()
    assert report.ok, [f"{f.kind}: {f.subject} {f.detail}" for f in report.findings]


def test_refuses_a_database_holding_business_data(lawyers):
    factories.MatterFactory(title="Päris teema", data_class=MatterDataClass.REAL)
    with pytest.raises(CommandError, match="non-TEST"):
        seed()
    assert Matter.objects.count() == 1


def test_refuses_a_real_data_instance_without_operator_intent(lawyers, settings):
    settings.REAL_DATA_ALLOWED = True
    with pytest.raises(CommandError, match="operator-intent"):
        seed()
    assert Matter.objects.count() == 0


def test_operator_intent_unlocks_a_real_data_instance(lawyers, settings):
    settings.REAL_DATA_ALLOWED = True
    call_command("seed_showcase_data", operator_intent="showcase-world", verbosity=0)
    assert Matter.objects.count() == 7
    assert Matter.objects.exclude(data_class=MatterDataClass.TEST).count() == 0
