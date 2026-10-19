"""«Ootame tagasisidet» puts «Koosta kodulehe ülevaade» behind the file (owner, 2026-10-19).

The owner's acceptance walk found the page saying `Ootame tagasisidet` and, in
the same block, `JÄRGMISENA: Koosta kodulehe ülevaade · Alusta`. Once a
`Kaasamine` is filed the overview has been written or will not be, so:

* **A** — while the feedback wait is open, the overview is not suggested; the
  next step that is still valid is;
* **B** — closing the wait (`Lisa tagasiside`) does not bring it back;
* **C** — nothing is written to make that so: the plan row stays `SUGGESTED`,
  no dismissal is recorded, and the docs/adr/0161 proof is unchanged;
* **D** — without a `Kaasamine` the overview is still suggested exactly as
  before, and a current action still shadows every suggestion.
"""

from __future__ import annotations

import pytest
from django.urls import reverse

from app.audit.enums import ChangeEventType
from app.audit.models import ChangeEvent
from app.matters.enums import EngagementKind
from app.matters.models import MatterEngagement
from app.matters.plan_view import RecordedWork
from app.workflow.enums import PlanStepOperation, PlanStepState
from app.workflow.models import MatterPlanStep
from app.workflow.plan import plan_revision, plan_steps_of, seed_standard_plan, skip_plan_step
from tests import factories
from tests.follow_ups import day, et

pytestmark = pytest.mark.django_db

READ, OVERVIEW, SEND = "Tutvu materjaliga", "Koosta kodulehe ülevaade", "Saada Koja arvamus"


@pytest.fixture
def planned(specialist):
    """An open Matter with the standard sequence and «Tutvu materjaliga» dismissed."""
    matter = factories.MatterFactory(owner=specialist)
    seed_standard_plan(matter=matter, actor=specialist)
    read = MatterPlanStep.objects.get(matter=matter, title=READ)
    skip_plan_step(
        step=read, actor=specialist, expected_revision=plan_revision(plan_steps_of(matter))
    )
    return matter


def _post(client, name: str, matter, data: dict):
    return client.post(
        reverse(f"matters:{name}", kwargs={"pk": matter.pk}), data, headers={"HX-Request": "true"}
    )


def _zone(client, matter) -> str:
    response = client.get(reverse("matters:matter_detail", kwargs={"pk": matter.pk}))
    assert response.status_code == 200
    body = response.content.decode()
    return body[body.index('id="praegune-tegevus"') : body.index('id="lisa-teemale"')]


def _suggested(zone: str) -> str | None:
    if 'id="soovitus"' not in zone:
        return None
    block = zone[zone.index('id="soovitus"') :]
    start = block.index('class="curact__suggesttext">') + len('class="curact__suggesttext">')
    return block[start : block.index("</span>", start)]


def _start_round(client, matter) -> MatterEngagement:
    response = _post(
        client,
        "add_engagement_compact",
        matter,
        {"audience": "Liikmed", "occurred_on": et(day(0))},
    )
    assert response.status_code == 200
    return MatterEngagement.objects.get(matter=matter)


def _overview_step(matter) -> MatterPlanStep:
    return MatterPlanStep.objects.get(matter=matter, title=OVERVIEW)


def test_d_without_a_kaasamine_the_overview_is_still_suggested(signed_in, planned):
    assert _suggested(_zone(signed_in, planned)) == OVERVIEW


def test_a_an_open_feedback_wait_takes_precedence_over_the_overview(signed_in, planned):
    engagement = _start_round(signed_in, planned)
    assert engagement.has_open_feedback_wait

    zone = _zone(signed_in, planned)

    assert "Ootame tagasisidet" in zone
    assert OVERVIEW not in zone
    # The next step that is still valid is suggested as before.
    assert _suggested(zone) == SEND


def test_b_closing_the_wait_does_not_bring_the_overview_back(signed_in, planned):
    engagement = _start_round(signed_in, planned)
    saved = _post(
        signed_in,
        "add_engagement_reply",
        planned,
        {"engagement": str(engagement.pk), "summary": "Liikmed vastasid.", "stated_on": ""},
    )
    assert saved.status_code == 200
    engagement.refresh_from_db()
    assert engagement.feedback_closed_at is not None

    zone = _zone(signed_in, planned)

    assert "Ootame tagasisidet" not in zone
    assert OVERVIEW not in zone
    assert _suggested(zone) == SEND


def test_c_nothing_is_written_to_suppress_it(signed_in, planned):
    before = ChangeEvent.objects.filter(event_type=ChangeEventType.PLAN_STEP_SKIPPED).count()

    _start_round(signed_in, planned)
    _zone(signed_in, planned)

    step = _overview_step(planned)
    assert step.state == PlanStepState.SUGGESTED
    assert step.skipped_at is None
    assert (
        ChangeEvent.objects.filter(event_type=ChangeEventType.PLAN_STEP_SKIPPED).count() == before
    )


def test_c_the_proof_the_alert_reads_is_unchanged(planned, specialist):
    """`proves` still says no overview was written — docs/adr/0161 reads it."""
    MatterEngagement.objects.create(
        matter=planned,
        kind=EngagementKind.EMAIL_CAMPAIGN,
        title="Liikmete küsitlus",
        created_by=specialist,
        lifecycle_tracked=True,
    )
    done = RecordedWork.of(planned, specialist)
    step = _overview_step(planned)

    assert step.operation == PlanStepOperation.WEBSITE_OVERVIEW
    assert done.engagement
    assert not done.proves(step)
    assert done.supersedes(step)


def test_d_a_current_action_still_shadows_every_suggestion(signed_in, planned):
    _start_round(signed_in, planned)
    added = _post(
        signed_in, "set_action", planned, {"text": "Helista ministeeriumi", "target_date": ""}
    )
    assert added.status_code == 200

    zone = _zone(signed_in, planned)

    assert "Helista ministeeriumi" in zone
    assert _suggested(zone) is None
