"""Independent QA 2026-10-14 — workflow area: two lawyers, stale forms (scenario E).

Sequential stale-form replays through the real views (no threads): person A's page
was drawn, person B changed something, A presses the button A's page drew.
"""

from __future__ import annotations

import pytest
from django.test import Client
from django.urls import reverse
from django.utils import timezone

from app.audit.enums import ChangeEventType
from app.audit.models import ChangeEvent
from app.matters.models import Entry, Matter
from app.matters.services import create_matter, matter_field_revision
from app.workflow.enums import ActionStatus
from app.workflow.models import NextAction, StageVocabulary
from app.workflow.services import set_next_action
from tests import factories

pytestmark = pytest.mark.django_db


def stage(key: str) -> StageVocabulary:
    return StageVocabulary.objects.get(key=key)


def as_user(user) -> Client:
    client = Client(enforce_csrf_checks=False)
    client.force_login(user)
    return client


def field_url(matter, field):
    return reverse("matters:update_field", kwargs={"pk": matter.pk, "field": field})


@pytest.fixture
def head(db):
    # `work.assign` — may hand a Teema to anybody in the department.
    return factories.DepartmentHeadFactory()


@pytest.fixture
def matter(specialist):
    return create_matter(
        title="QA samaaegne", actor=specialist, owner=specialist, stage=stage("awaiting_entry")
    )


# ---------------------------------------------------------------------------
# QA-WORKFLOW-03 — the header's Vastutaja and Saabus editors carry no revision
# ---------------------------------------------------------------------------


@pytest.mark.xfail(
    strict=True,
    reason=(
        "QA-WORKFLOW-03: header Vastutaja editor posts no revision; a stale tab silently "
        "reverses a colleague's reassignment (owner is not in GUARDED_MATTER_FIELDS)"
    ),
)
def test_a_stale_vastutaja_choice_does_not_overwrite_a_colleagues(matter, head, specialist):
    colleague_y = factories.UserFactory()
    colleague_z = factories.UserFactory()
    a, b = as_user(head), as_user(factories.DepartmentHeadFactory())
    # Both pages drawn showing the specialist as Vastutaja.
    assert a.get(reverse("matters:matter_detail", kwargs={"pk": matter.pk})).status_code == 200

    assert b.post(field_url(matter, "owner"), {"owner": str(colleague_y.pk)}).status_code == 200
    matter.refresh_from_db()
    assert matter.owner == colleague_y

    stale = a.post(field_url(matter, "owner"), {"owner": str(colleague_z.pk)})

    matter.refresh_from_db()
    assert stale.status_code == 409
    assert matter.owner == colleague_y


@pytest.mark.xfail(
    strict=True,
    reason="QA-WORKFLOW-03: header Saabus editor posts no revision; a stale tab overwrites",
)
def test_a_stale_saabus_does_not_overwrite_a_colleagues(matter, specialist, other_specialist):
    import datetime as dt

    a, b = as_user(specialist), as_user(other_specialist)
    first = timezone.localdate() - dt.timedelta(days=3)
    second = timezone.localdate() - dt.timedelta(days=10)

    b.post(field_url(matter, "received_date"), {"received_date": f"{first:%d.%m.%Y}"})
    stale = a.post(field_url(matter, "received_date"), {"received_date": f"{second:%d.%m.%Y}"})

    matter.refresh_from_db()
    assert stale.status_code == 409
    assert matter.received_date == first


def test_stale_vastutaja_evidence_last_writer_wins(matter, head):
    """What happens today (evidence for QA-WORKFLOW-03): 200, and the event trail shows two
    reassignments with nothing telling A that B had moved it."""
    y, z = factories.UserFactory(), factories.UserFactory()
    a, b = as_user(head), as_user(factories.DepartmentHeadFactory())
    b.post(field_url(matter, "owner"), {"owner": str(y.pk)})
    response = a.post(field_url(matter, "owner"), {"owner": str(z.pk)})
    matter.refresh_from_db()
    assert response.status_code == 200
    assert matter.owner == z


# ---------------------------------------------------------------------------
# The current step — guarded (passing probes)
# ---------------------------------------------------------------------------


def test_muuda_on_a_step_a_colleague_completed_writes_nothing(matter, specialist, other_specialist):
    step = set_next_action(matter=matter, text="Analüüsi eelnõu", actor=specialist)
    a, b = as_user(specialist), as_user(other_specialist)

    done = b.post(
        reverse("matters:complete_current_action", kwargs={"pk": matter.pk}),
        {"action_id": str(step.pk), "body": "Analüüsitud."},
    )
    assert done.status_code == 200
    step.refresh_from_db()
    assert step.status == ActionStatus.COMPLETED
    before = NextAction.objects.filter(matter=matter).count()

    stale = a.post(
        reverse("matters:set_action", kwargs={"pk": matter.pk}),
        {"action_id": str(step.pk), "text": "Analüüsi eelnõu põhjalikumalt"},
    )

    assert stale.status_code >= 400
    assert NextAction.objects.filter(matter=matter).count() == before
    assert not NextAction.objects.filter(matter=matter, status=ActionStatus.OPEN).exists()


def test_two_muuda_on_one_step_keep_the_first_and_refuse_the_second(
    matter, specialist, other_specialist
):
    step = set_next_action(matter=matter, text="Kirjuta seisukoht", actor=specialist)
    a, b = as_user(specialist), as_user(other_specialist)
    url = reverse("matters:set_action", kwargs={"pk": matter.pk})

    b.post(url, {"action_id": str(step.pk), "text": "B sõnastus"})
    stale = a.post(url, {"action_id": str(step.pk), "text": "A sõnastus"})

    (current,) = NextAction.objects.filter(matter=matter, status=ActionStatus.OPEN)
    assert current.text == "B sõnastus"
    assert stale.status_code >= 400


def test_tehtud_replayed_records_one_result(matter, specialist):
    step = set_next_action(matter=matter, text="Saada kiri", actor=specialist)
    a = as_user(specialist)
    data = {"action_id": str(step.pk), "body": "Kiri saadetud."}
    url = reverse("matters:complete_current_action", kwargs={"pk": matter.pk})

    a.post(url, data)
    replay = a.post(url, data)

    assert replay.status_code >= 400
    completions = ChangeEvent.objects.filter(
        matter=matter, event_type=ChangeEventType.NEXT_ACTION_COMPLETED
    ).count()
    assert completions == 1
    assert Entry.objects.filter(matter=matter, body__contains="Kiri saadetud.").count() <= 1


def test_tehtud_on_a_step_a_colleague_rewrote_is_refused(matter, specialist, other_specialist):
    step = set_next_action(matter=matter, text="Algne samm", actor=specialist)
    a, b = as_user(specialist), as_user(other_specialist)
    b.post(
        reverse("matters:set_action", kwargs={"pk": matter.pk}),
        {"action_id": str(step.pk), "text": "Kolleegi täpsustatud samm"},
    )

    stale = a.post(
        reverse("matters:complete_current_action", kwargs={"pk": matter.pk}),
        {"action_id": str(step.pk), "body": "Tehtud vana sõnastuse järgi."},
    )

    assert stale.status_code >= 400
    (current,) = NextAction.objects.filter(matter=matter, status=ActionStatus.OPEN)
    assert current.text == "Kolleegi täpsustatud samm"


def test_step_edit_and_stage_change_by_two_people_both_survive(
    matter, specialist, other_specialist
):
    step = set_next_action(matter=matter, text="Samm", actor=specialist)
    a, b = as_user(specialist), as_user(other_specialist)
    revision = matter_field_revision(matter, "stage")

    a.post(
        reverse("matters:set_action", kwargs={"pk": matter.pk}),
        {"action_id": str(step.pk), "text": "Samm, täpsustatud"},
    )
    moved = b.post(
        field_url(matter, "stage"),
        {"stage": str(stage("parliament").pk), "revision": revision},
        HTTP_HX_REQUEST="true",
    )

    assert moved.status_code == 200
    matter.refresh_from_db()
    assert matter.stage.key == "parliament"
    (current,) = NextAction.objects.filter(matter=matter, status=ActionStatus.OPEN)
    assert current.text == "Samm, täpsustatud"


def test_a_stale_stage_choice_after_a_colleagues_closure_is_refused(
    matter, specialist, other_specialist
):
    a, b = as_user(specialist), as_user(other_specialist)
    revision = matter_field_revision(matter, "stage")
    b.post(
        field_url(matter, "stage"),
        {"stage": str(stage("in_force").pk), "revision": revision},
        HTTP_HX_REQUEST="true",
    )
    matter.refresh_from_db()
    assert not matter.is_open

    stale = a.post(
        field_url(matter, "stage"),
        {"stage": str(stage("parliament").pk), "revision": revision},
        HTTP_HX_REQUEST="true",
    )

    assert stale.status_code >= 400
    matter.refresh_from_db()
    assert not matter.is_open
    assert matter.stage.key == "in_force"
    assert isinstance(matter, Matter)
