"""`Järgmisena?` and «Järgmine samm on määramata» read the Matter's record (docs/adr/0144 §2–§3).

* The suggestion skips a standard step the canonical records already prove
  done — a published Ülevaade, a Kaasamine, recorded feedback, a sent opinion —
  keyed on the step's identity, never its words.
* A repeat `Arvamuse tähtaeg` brings the opinion step back: an earlier opinion
  never answers a later request.
* A dismissal still persists; nothing is written by the reading.
* A round waiting for feedback or a planned action is the next step, so the
  page does not call the next step unset — and `without_next_step` counts the
  same way.
"""

from __future__ import annotations

from datetime import timedelta

import pytest
from django.core.files.uploadedfile import SimpleUploadedFile
from django.urls import reverse
from django.utils import timezone

from app.matters import workspace as ws
from app.matters.enums import EngagementKind, WebsiteOverviewKind
from app.matters.models import Matter
from app.matters.next_step import without_next_step
from app.matters.plan_view import recommendation_for
from app.matters.response_deadlines import deadline_revision, request_response_deadline
from app.workflow.enums import ActionStatus, PlanStepState
from app.workflow.models import MatterPlanStep, NextAction
from app.workflow.plan import plan_revision, plan_steps_of, seed_standard_plan, skip_plan_step
from app.workflow.services import add_planned_action, set_next_action_for_new_work
from tests import factories

pytestmark = pytest.mark.django_db

READ, OVERVIEW, CONSULT, POSITION, SEND = (
    "read-material",
    "website-overview",
    "consult-members",
    "form-position",
    "send-opinion",
)


@pytest.fixture
def planned(specialist):
    matter = factories.MatterFactory(owner=specialist)
    seed_standard_plan(matter=matter, actor=specialist)
    return matter


def _suggested(matter, viewer) -> str | None:
    recommendation = recommendation_for(matter, None, viewer)
    return recommendation.step.template_step_key if recommendation else None


def _overview(matter, author, kind=WebsiteOverviewKind.OVERVIEW):
    ws.add_matter_website_overview(
        matter=matter,
        author=author,
        url="https://www.koda.ee/naidis/ulevaade",
        published_on=timezone.localdate(),
        title="Ülevaade",
        kind=kind,
    )


def _round(matter, author):
    return ws.add_matter_engagement(
        matter=matter,
        author=author,
        audience="Liikmed",
        kind=EngagementKind.SURVEY.value,
        occurred_on=timezone.localdate(),
        feedback_deadline=timezone.localdate() + timedelta(days=10),
    ).record


def _opinion(matter, author, organisation, **kwargs):
    return ws.add_matter_koda_opinion(
        matter=matter,
        author=author,
        upload=SimpleUploadedFile("arvamus.pdf", b"%PDF-1.4 x", content_type="application/pdf"),
        recipients=[organisation],
        sent_on=timezone.localdate(),
        **kwargs,
    ).record


def _zone(client, matter) -> str:
    body = client.get(reverse("matters:matter_detail", kwargs={"pk": matter.pk})).content.decode()
    return body[body.index('id="praegune-tegevus"') : body.index('id="lisa-teemale"')]


# ---------------------------------------------------------------------------
# The suggestion follows the record
# ---------------------------------------------------------------------------


def test_a_fresh_matter_suggests_reading_the_material(planned, specialist):
    assert _suggested(planned, specialist) == READ


def test_a_published_overview_proves_the_reading_and_the_overview(planned, specialist):
    _overview(planned, specialist)
    assert _suggested(planned, specialist) == CONSULT


def test_a_news_item_proves_the_reading_but_not_the_overview(planned, specialist):
    _overview(planned, specialist, kind=WebsiteOverviewKind.NEWS)
    assert _suggested(planned, specialist) == OVERVIEW


def test_an_active_consultation_proves_the_invitation(planned, specialist):
    _overview(planned, specialist)
    _round(planned, specialist)
    assert _suggested(planned, specialist) == POSITION


def test_recorded_feedback_proves_the_consolidation(planned, specialist):
    _overview(planned, specialist)
    engagement = _round(planned, specialist)
    ws.add_engagement_feedback(engagement=engagement, author=specialist, feedback_received="Viis.")
    assert _suggested(planned, specialist) == SEND


def test_a_sent_opinion_leaves_nothing_to_suggest(planned, specialist, organisation):
    _overview(planned, specialist)
    _round(planned, specialist)
    _opinion(planned, specialist, organisation)
    assert _suggested(planned, specialist) is None


def test_a_new_request_needs_new_opinion_work(planned, specialist, organisation):
    _overview(planned, specialist)
    engagement = _round(planned, specialist)
    ws.add_engagement_feedback(engagement=engagement, author=specialist, feedback_received="Viis.")
    request_response_deadline(
        matter=planned, deadline=timezone.localdate() + timedelta(days=5), actor=specialist
    )
    planned.refresh_from_db()
    _opinion(planned, specialist, organisation, answers_deadline=deadline_revision(planned))
    planned.refresh_from_db()
    assert _suggested(planned, specialist) is None

    request_response_deadline(
        matter=planned, deadline=timezone.localdate() + timedelta(days=30), actor=specialist
    )
    planned.refresh_from_db()

    assert _suggested(planned, specialist) == SEND


def test_the_reading_writes_nothing(planned, specialist):
    _overview(planned, specialist)
    before = list(MatterPlanStep.objects.filter(matter=planned).values_list("state", flat=True))

    _suggested(planned, specialist)

    after = list(MatterPlanStep.objects.filter(matter=planned).values_list("state", flat=True))
    assert before == after


def test_a_dismissed_suggestion_stays_dismissed(planned, specialist):
    _overview(planned, specialist)
    step = MatterPlanStep.objects.get(matter=planned, template_step_key=CONSULT)
    skip_plan_step(
        step=step, actor=specialist, expected_revision=plan_revision(plan_steps_of(planned))
    )

    assert MatterPlanStep.objects.get(pk=step.pk).state == PlanStepState.SKIPPED
    assert _suggested(planned, specialist) == POSITION
    assert _suggested(planned, specialist) == POSITION


def test_the_page_heading_is_jargmisena(client, specialist, planned):
    client.force_login(specialist)
    _overview(planned, specialist)

    zone = _zone(client, planned)

    assert "Järgmisena?" in zone
    assert "Soovitatud järgmisena" not in zone
    assert "Kaasa liikmeid" in zone
    assert "Tutvu materjaliga" not in zone


# ---------------------------------------------------------------------------
# Scheduled work is the next step
# ---------------------------------------------------------------------------


def test_a_waiting_round_is_the_next_step_on_the_page(client, specialist, normal_matter):
    client.force_login(specialist)
    _round(normal_matter, specialist)

    zone = _zone(client, normal_matter)

    assert "Ootame tagasisidet" in zone
    assert "Järgmine samm on määramata" not in zone


def test_a_planned_action_is_the_next_step_on_the_page(client, specialist, normal_matter):
    current = set_next_action_for_new_work(
        matter=normal_matter, text="Praegune", target_date=timezone.localdate(), actor=specialist
    )
    add_planned_action(
        matter=normal_matter,
        text="Kohtu ministeeriumiga",
        target_date=timezone.localdate() + timedelta(days=7),
        actor=specialist,
    )
    NextAction.objects.filter(pk=current.pk).update(status=ActionStatus.CANCELLED)
    client.force_login(specialist)

    zone = _zone(client, normal_matter)

    assert "Kohtu ministeeriumiga" in zone
    assert "Järgmine samm on määramata" not in zone


def test_nothing_at_all_is_still_unset(client, specialist, normal_matter):
    client.force_login(specialist)
    assert "Järgmine samm on määramata" in _zone(client, normal_matter)


def test_a_finished_round_is_not_scheduled_work(client, specialist, normal_matter):
    engagement = _round(normal_matter, specialist)
    ws.add_engagement_feedback(engagement=engagement, author=specialist, feedback_received="X")
    client.force_login(specialist)

    assert "Järgmine samm on määramata" in _zone(client, normal_matter)


def test_without_next_step_counts_the_same_way(specialist):
    waiting = factories.MatterFactory(owner=specialist)
    _round(waiting, specialist)
    planned_only = factories.MatterFactory(owner=specialist)
    current = set_next_action_for_new_work(
        matter=planned_only, text="P", target_date=timezone.localdate(), actor=specialist
    )
    add_planned_action(
        matter=planned_only,
        text="Hiljem",
        target_date=timezone.localdate() + timedelta(days=3),
        actor=specialist,
    )
    NextAction.objects.filter(pk=current.pk).update(status=ActionStatus.CANCELLED)
    idle = factories.MatterFactory(owner=specialist)

    found = set(
        without_next_step(Matter.objects.filter(owner=specialist), specialist).values_list(
            "pk", flat=True
        )
    )

    assert idle.pk in found
    assert waiting.pk not in found
    assert planned_only.pk not in found
