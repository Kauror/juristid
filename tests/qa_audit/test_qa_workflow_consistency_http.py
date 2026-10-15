"""Independent QA 2026-10-14 — workflow area: the ADR 0161 warning, end to end over HTTP.

tests/test_workflow_consistency.py proves the predicate with service calls and
back-dated rows. This drives the person's real sequence through the views — Uus
teema seeds the plan, `Alusta` starts the step, `LISA TEEMALE → Kaasamine` files
the round, the Teema page warns, then `✓ Tehtud` or `Muuda` — and checks the DB
at each point (nothing completed, moved or created by the warning).
"""

from __future__ import annotations

import pytest
from django.urls import reverse
from django.utils import timezone

from app.matters.models import Matter, MatterEngagement
from app.workflow.consistency import PROOF_ENGAGEMENT, STEP_ALREADY_RECORDED
from app.workflow.enums import ActionStatus
from app.workflow.models import MatterPlanStep, NextAction

pytestmark = pytest.mark.django_db

ALERT = f'data-consistency-alert="{STEP_ALREADY_RECORDED}"'


def page(client, matter) -> str:
    response = client.get(reverse("matters:matter_detail", kwargs={"pk": matter.pk}))
    assert response.status_code == 200
    return response.content.decode()


def filed_with_plan(client, title) -> Matter:
    assert client.post(reverse("matters:matter_create"), {"title": title}).status_code == 302
    matter = Matter.objects.get(title=title)
    assert MatterPlanStep.objects.filter(matter=matter).exists()
    return matter


def start(client, matter, key) -> NextAction:
    step = MatterPlanStep.objects.get(matter=matter, template_step_key=key)
    response = client.post(
        reverse("matters:start_plan_step", kwargs={"pk": matter.pk, "step_id": step.pk}), {}
    )
    assert response.status_code == 200
    return NextAction.objects.get(matter=matter, status=ActionStatus.OPEN)


def file_round(client, matter) -> None:
    today = timezone.localdate()
    response = client.post(
        reverse("matters:add_engagement_compact", kwargs={"pk": matter.pk}),
        {"audience": "Liikmed", "occurred_on": f"{today.day}.{today.month}.{today.year}"},
    )
    assert response.status_code == 200, response.content.decode()[:2000]
    assert MatterEngagement.objects.filter(matter=matter).count() == 1


def test_alusta_then_kaasamine_warns_and_tehtud_clears(signed_in, specialist):
    matter = filed_with_plan(signed_in, "QA järjepidevus Tehtud")
    action = start(signed_in, matter, "consult-members")
    assert ALERT not in page(signed_in, matter)

    file_round(signed_in, matter)
    body = page(signed_in, matter)

    assert body.count(ALERT) == 1
    assert PROOF_ENGAGEMENT in body
    action.refresh_from_db()
    assert action.status == ActionStatus.OPEN  # nothing auto-completed

    done = signed_in.post(
        reverse("matters:complete_current_action", kwargs={"pk": matter.pk}),
        {"action_id": str(action.pk), "body": "Liikmed kaasatud."},
    )
    assert done.status_code == 200
    action.refresh_from_db()
    assert action.status == ActionStatus.COMPLETED
    assert ALERT not in page(signed_in, matter)


def test_alusta_then_kaasamine_warns_and_muuda_clears(signed_in, specialist):
    matter = filed_with_plan(signed_in, "QA järjepidevus Muuda")
    action = start(signed_in, matter, "consult-members")
    file_round(signed_in, matter)
    assert ALERT in page(signed_in, matter)

    edited = signed_in.post(
        reverse("matters:set_action", kwargs={"pk": matter.pk}),
        {"action_id": str(action.pk), "text": "Kaasa ka juristide võrgustik"},
    )
    assert edited.status_code == 200

    (current,) = NextAction.objects.filter(matter=matter, status=ActionStatus.OPEN)
    assert current.pk != action.pk
    assert current.plan_step_id == action.plan_step_id
    assert ALERT not in page(signed_in, matter)


def test_a_hand_written_step_and_the_same_record_draw_no_warning(signed_in, specialist):
    matter = filed_with_plan(signed_in, "QA käsitsi samm")
    signed_in.post(
        reverse("matters:set_action", kwargs={"pk": matter.pk}),
        {"text": "Kaasa liikmeid / küsi tagasisidet"},
    )
    (current,) = NextAction.objects.filter(matter=matter, status=ActionStatus.OPEN)
    assert current.plan_step_id is None

    file_round(signed_in, matter)

    assert ALERT not in page(signed_in, matter)


def test_a_second_specialist_sees_the_same_warning(client, signed_in, specialist, other_specialist):
    matter = filed_with_plan(signed_in, "QA teine jurist")
    start(signed_in, matter, "consult-members")
    file_round(signed_in, matter)

    client.force_login(other_specialist)
    assert page(client, matter).count(ALERT) == 1
