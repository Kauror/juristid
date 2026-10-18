"""Workflow-consistency alerts (docs/adr/0161, `app.workflow.consistency`).

One alert: the current step was started from a standard step, and since it was
set the record shows that step done — the `RecordedWork` rule `Järgmisena?`
already reads (docs/adr/0144 §2), asked of the current step and narrowed to
records entered after it. Each clause of the predicate has a positive case and
a guard; the reader's scope decides what may prove a step; and the Teema page
draws it once, under `PRAEGUNE TEGEVUS`, writing nothing.
"""

from __future__ import annotations

from datetime import timedelta

import pytest
from django.core.files.uploadedfile import SimpleUploadedFile
from django.urls import reverse
from django.utils import timezone

from app.core.enums import Visibility
from app.matters import workspace as ws
from app.matters.enums import EngagementKind, RecordMode, WebsiteOverviewKind
from app.matters.models import Matter, MatterEngagement
from app.matters.plan_view import RecordedWork
from app.matters.response_deadlines import request_response_deadline
from app.workflow import consistency
from app.workflow.consistency import (
    PROOF_ENGAGEMENT,
    PROOF_FEEDBACK,
    PROOF_OPINION,
    PROOF_OVERVIEW,
    PROOF_PUBLISHED_ANY,
    STEP_ALREADY_RECORDED,
    current_work_alerts,
    step_already_recorded,
)
from app.workflow.enums import ActionKind, ActionStatus, DateSemantics
from app.workflow.models import MatterPlanStep, NextAction
from app.workflow.plan import STRUCTURED_OPERATIONS, activate_plan_step, seed_standard_plan
from app.workflow.services import set_next_action_for_new_work
from tests import factories
from tests.legacy_plans import seed_version_one_plan, start_as_before_adr_0168

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


def _start(matter, actor, key) -> NextAction:
    """`Alusta` on one standard step, set an hour ago so later records are later."""
    step = MatterPlanStep.objects.get(matter=matter, template_step_key=key)
    # A structured step is current only as it was before docs/adr/0168: the
    # service refuses to start one as a generic action (owner decision D9).
    if step.operation in STRUCTURED_OPERATIONS:
        action = start_as_before_adr_0168(matter=matter, step=step, actor=actor)
    else:
        action = activate_plan_step(matter=matter, step=step, actor=actor)
    NextAction.objects.filter(pk=action.pk).update(created_at=timezone.now() - timedelta(hours=1))
    action.refresh_from_db()
    return action


def _overview(matter, author, kind=WebsiteOverviewKind.OVERVIEW):
    return ws.add_matter_website_overview(
        matter=matter,
        author=author,
        url="https://www.koda.ee/naidis/ulevaade",
        published_on=timezone.localdate(),
        title="Ülevaade",
        kind=kind,
    ).record


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


def _alert(matter, viewer):
    matter.refresh_from_db()
    current = (
        NextAction.objects.visible_to(viewer)
        .filter(matter=matter, status=ActionStatus.OPEN)
        .first()
    )
    return step_already_recorded(matter, current, viewer)


def _zone(client, matter) -> str:
    """`PRAEGUNE TEGEVUS` to the end of its section (a reader has no `LISA TEEMALE`)."""
    body = client.get(reverse("matters:matter_detail", kwargs={"pk": matter.pk})).content.decode()
    start = body.index('id="praegune-tegevus"')
    return body[start : body.index("</section>", start)]


# ---------------------------------------------------------------------------
# Positive: each standard step, proven by the record entered after it was set
# ---------------------------------------------------------------------------


def test_a_consultation_filed_after_the_step_proves_consult_members(planned, specialist):
    _start(planned, specialist, CONSULT)
    _round(planned, specialist)

    alert = _alert(planned, specialist)

    assert alert is not None
    assert alert.key == STEP_ALREADY_RECORDED
    assert alert.reason == PROOF_ENGAGEMENT


def test_a_published_overview_proves_the_overview_step(planned, specialist):
    _start(planned, specialist, OVERVIEW)
    _overview(planned, specialist)

    assert _alert(planned, specialist).reason == PROOF_OVERVIEW


def test_a_sent_opinion_proves_the_send_step(planned, specialist, organisation):
    _start(planned, specialist, SEND)
    _opinion(planned, specialist, organisation)

    assert _alert(planned, specialist).reason == PROOF_OPINION


def test_recorded_feedback_proves_forming_the_position(specialist):
    """A `form-position` step started before version 2 retired it (docs/adr/0168 §5)."""
    matter = factories.MatterFactory(owner=specialist)
    seed_version_one_plan(matter=matter, actor=specialist)
    engagement = _round(matter, specialist)
    _start(matter, specialist, POSITION)
    ws.add_engagement_feedback(engagement=engagement, author=specialist, feedback_received="Viis.")

    assert _alert(matter, specialist).reason == PROOF_FEEDBACK


def test_a_news_item_proves_reading_the_material(planned, specialist):
    _start(planned, specialist, READ)
    _overview(planned, specialist, kind=WebsiteOverviewKind.NEWS)

    assert _alert(planned, specialist).reason == PROOF_PUBLISHED_ANY


# ---------------------------------------------------------------------------
# Guards: no alert
# ---------------------------------------------------------------------------


def test_nothing_recorded_since_the_step_is_no_alert(planned, specialist):
    _start(planned, specialist, CONSULT)

    assert _alert(planned, specialist) is None


def test_a_record_older_than_the_step_proves_nothing(planned, specialist):
    engagement = _round(planned, specialist)
    MatterEngagement.objects.filter(pk=engagement.pk).update(
        created_at=timezone.now() - timedelta(days=2)
    )
    _start(planned, specialist, CONSULT)

    assert _alert(planned, specialist) is None


def test_a_record_that_proves_a_different_step_is_no_alert(planned, specialist):
    _start(planned, specialist, OVERVIEW)
    _round(planned, specialist)

    assert _alert(planned, specialist) is None


def test_a_news_item_does_not_prove_the_overview_step(planned, specialist):
    _start(planned, specialist, OVERVIEW)
    _overview(planned, specialist, kind=WebsiteOverviewKind.NEWS)

    assert _alert(planned, specialist) is None


def test_an_outstanding_request_keeps_the_send_step_open(planned, specialist, organisation):
    _start(planned, specialist, SEND)
    _opinion(planned, specialist, organisation)
    request_response_deadline(
        matter=planned, deadline=timezone.localdate() + timedelta(days=20), actor=specialist
    )

    assert _alert(planned, specialist) is None


def test_a_step_written_by_hand_is_never_judged(normal_matter, specialist):
    """No `plan_step` — no relation to read, and the words are never read."""
    set_next_action_for_new_work(
        matter=normal_matter, text="Kaasa liikmeid / küsi tagasisidet", actor=specialist
    )
    NextAction.objects.filter(matter=normal_matter).update(
        created_at=timezone.now() - timedelta(hours=1)
    )
    _round(normal_matter, specialist)

    assert _alert(normal_matter, specialist) is None


def test_a_wait_is_not_a_task_the_record_finishes(planned, specialist):
    action = _start(planned, specialist, CONSULT)
    NextAction.objects.filter(pk=action.pk).update(
        kind=ActionKind.WAIT, date_semantics=DateSemantics.REVIEW_ON
    )
    _round(planned, specialist)

    assert _alert(planned, specialist) is None


def test_a_planned_step_is_not_the_current_one(planned, specialist):
    """Only `PRAEGUNE TEGEVUS` is asked; a planned row is not current work yet."""
    action = _start(planned, specialist, CONSULT)
    NextAction.objects.filter(pk=action.pk).update(
        status=ActionStatus.PLANNED, target_date=timezone.localdate() + timedelta(days=3)
    )
    _round(planned, specialist)

    assert _alert(planned, specialist) is None
    action.refresh_from_db()
    assert step_already_recorded(planned, action, specialist) is None


def test_a_closed_matter_is_no_alert(planned, specialist):
    action = _start(planned, specialist, CONSULT)
    _round(planned, specialist)
    planned.is_open = False  # as a closure leaves it; only the attribute is read

    assert step_already_recorded(planned, action, specialist) is None


def test_an_archive_row_is_no_alert(planned, specialist):
    action = _start(planned, specialist, CONSULT)
    _round(planned, specialist)
    Matter.objects.filter(pk=planned.pk).update(record_mode=RecordMode.ARCHIVE)
    planned.refresh_from_db()

    assert step_already_recorded(planned, action, specialist) is None


def test_no_current_step_is_no_alert(planned, specialist):
    assert step_already_recorded(planned, None, specialist) is None
    assert current_work_alerts(planned, None, specialist) == []


def test_editing_the_step_after_the_record_clears_the_alert(planned, specialist):
    """`Muuda` sets the step anew, after the record: «the work goes on» is heard."""
    action = _start(planned, specialist, CONSULT)
    _round(planned, specialist)
    MatterEngagement.objects.filter(matter=planned).update(
        created_at=timezone.now() - timedelta(minutes=30)
    )
    assert _alert(planned, specialist) is not None

    replacement = ws.change_current_action(
        matter=planned, actor=specialist, action_id=action.pk, text="Kaasa ka juristide võrgustik"
    )

    assert replacement.plan_step_id == action.plan_step_id
    assert _alert(planned, specialist) is None


# ---------------------------------------------------------------------------
# Authorization: what a reader may not see proves nothing to them
# ---------------------------------------------------------------------------


def test_a_restricted_record_proves_nothing_to_a_reader(planned, specialist, reader):
    _start(planned, specialist, CONSULT)
    engagement = _round(planned, specialist)
    MatterEngagement.objects.filter(pk=engagement.pk).update(
        visibility_override=Visibility.RESTRICTED
    )

    assert _alert(planned, specialist) is not None
    assert _alert(planned, reader) is None


def test_a_restricted_step_is_not_a_readers_current_step(planned, specialist, reader):
    action = _start(planned, specialist, CONSULT)
    NextAction.objects.filter(pk=action.pk).update(visibility_override=Visibility.RESTRICTED)
    _round(planned, specialist)

    assert _alert(planned, specialist) is not None
    assert _alert(planned, reader) is None


def test_the_narrowed_record_is_reader_scoped_too(planned, specialist, reader):
    action = _start(planned, specialist, CONSULT)
    engagement = _round(planned, specialist)
    MatterEngagement.objects.filter(pk=engagement.pk).update(
        visibility_override=Visibility.RESTRICTED
    )

    assert RecordedWork.of(planned, specialist, since=action.created_at).engagement
    assert not RecordedWork.of(planned, reader, since=action.created_at).engagement


def test_since_leaves_the_plain_reading_unchanged(planned, specialist):
    engagement = _round(planned, specialist)
    MatterEngagement.objects.filter(pk=engagement.pk).update(
        created_at=timezone.now() - timedelta(days=2)
    )
    later = timezone.now() - timedelta(days=1)

    assert RecordedWork.of(planned, specialist).engagement
    assert not RecordedWork.of(planned, specialist, since=later).engagement


# ---------------------------------------------------------------------------
# The Teema page: one place, the reason, the remedy for a writer, nothing written
# ---------------------------------------------------------------------------


def test_the_page_draws_the_alert_under_the_current_step(client, planned, specialist):
    _start(planned, specialist, CONSULT)
    _round(planned, specialist)
    client.force_login(specialist)

    zone = _zone(client, planned)

    assert zone.count(f'data-consistency-alert="{STEP_ALREADY_RECORDED}"') == 1
    assert consistency.STEP_ALREADY_RECORDED_TITLE in zone
    assert PROOF_ENGAGEMENT in zone
    assert "«✓ Tehtud»" in zone
    assert zone.index("curact__task") < zone.index("data-consistency-alert")


def test_the_alert_appears_nowhere_else_on_the_page(client, planned, specialist):
    _start(planned, specialist, CONSULT)
    _round(planned, specialist)
    client.force_login(specialist)

    body = client.get(reverse("matters:matter_detail", kwargs={"pk": planned.pk})).content.decode()

    assert body.count("data-consistency-alert") == 1


def test_a_reader_sees_the_reason_without_the_writers_remedy(client, planned, specialist, reader):
    _start(planned, specialist, CONSULT)
    _round(planned, specialist)
    client.force_login(reader)

    zone = _zone(client, planned)

    assert PROOF_ENGAGEMENT in zone
    assert "«✓ Tehtud»" not in zone


def test_a_reader_is_not_told_of_a_restricted_record(client, planned, specialist, reader):
    _start(planned, specialist, CONSULT)
    engagement = _round(planned, specialist)
    MatterEngagement.objects.filter(pk=engagement.pk).update(
        visibility_override=Visibility.RESTRICTED
    )
    client.force_login(reader)

    assert "data-consistency-alert" not in _zone(client, planned)


def test_no_alert_no_markup(client, planned, specialist):
    _start(planned, specialist, CONSULT)
    client.force_login(specialist)

    assert "data-consistency-alert" not in _zone(client, planned)


def test_drawing_the_alert_writes_nothing(client, planned, specialist):
    action = _start(planned, specialist, CONSULT)
    _round(planned, specialist)
    client.force_login(specialist)
    before = list(NextAction.objects.filter(matter=planned).values_list("pk", "status", "text"))
    steps = list(MatterPlanStep.objects.filter(matter=planned).values_list("pk", "state"))

    _zone(client, planned)

    assert list(NextAction.objects.filter(matter=planned).values_list("pk", "status", "text")) == (
        before
    )
    assert list(MatterPlanStep.objects.filter(matter=planned).values_list("pk", "state")) == steps
    action.refresh_from_db()
    assert action.status == ActionStatus.OPEN
