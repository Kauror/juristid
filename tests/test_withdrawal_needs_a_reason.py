"""Owner decision D7 (2026-10-14): withdrawing a sent opinion needs a reason.

A legitimate sent opinion can be withdrawn even when its Teema is closed. The
reason is required on every withdrawal — server-side in
`withdraw_submission`, and `required` in the form. The original send, its
final evidence and the audit history stay; the Teema is not reopened and no
unrelated workflow state changes.
"""

from __future__ import annotations

import datetime as dt

import pytest
from django.urls import reverse
from django.utils import timezone

from app.audit.enums import ChangeEventType
from app.audit.models import ChangeEvent
from app.documents.models import DocumentVersion
from app.matters.models import MatterResponseDeadline
from app.matters.services import close_matter, create_matter
from app.submissions.enums import SubmissionStatus
from app.submissions.models import Submission
from app.submissions.services import WITHDRAWAL_NEEDS_A_REASON, withdraw_submission
from app.workflow.enums import Disposition, FollowUpState
from app.workflow.models import NextAction, OpinionFollowUp, StageVocabulary
from tests.follow_ups import send_koja_arvamus
from tests.refusals import refused

pytestmark = pytest.mark.django_db


def _sent_opinion(specialist, organisation, *, title: str = "Tagasivõetav arvamus"):
    matter = create_matter(
        title=title,
        actor=specialist,
        owner=specialist,
        stage=StageVocabulary.objects.get(key="consultation"),
        response_deadline=timezone.localdate() + dt.timedelta(days=10),
        response_requested_at=timezone.now(),
    )
    sent = send_koja_arvamus(
        matter, specialist, [organisation], timezone.localdate() - dt.timedelta(days=1)
    )
    return matter, sent


def _withdrawals(matter) -> int:
    return ChangeEvent.objects.filter(
        matter=matter, event_type=ChangeEventType.SUBMISSION_WITHDRAWN
    ).count()


@pytest.mark.parametrize("reason", ["", "   \n "])
def test_the_service_refuses_a_withdrawal_without_a_reason(specialist, organisation, reason):
    matter, sent = _sent_opinion(specialist, organisation)

    with refused(WITHDRAWAL_NEEDS_A_REASON):
        withdraw_submission(submission=sent, actor=specialist, reason=reason)

    sent.refresh_from_db()
    assert sent.status == SubmissionStatus.SENT
    assert _withdrawals(matter) == 0
    assert OpinionFollowUp.objects.get(submission=sent).state == FollowUpState.MONITORING


def test_the_route_refuses_an_empty_reason_and_says_why(signed_in, specialist, organisation):
    matter, sent = _sent_opinion(specialist, organisation)

    response = signed_in.post(
        reverse("submissions:withdraw", kwargs={"pk": sent.pk}),
        {"reason": "", "tagasi": "teema"},
        follow=True,
    )

    sent.refresh_from_db()
    assert sent.status == SubmissionStatus.SENT
    assert _withdrawals(matter) == 0
    assert WITHDRAWAL_NEEDS_A_REASON in response.content.decode()


def test_the_confirmation_asks_for_the_reason(signed_in, specialist, organisation):
    matter, sent = _sent_opinion(specialist, organisation)

    page = signed_in.get(reverse("matters:matter_detail", kwargs={"pk": matter.pk})).content
    body = page.decode()
    field = f'id="arvamus-{sent.pk}-tagasi-pohjus"'
    assert field in body
    tag = body[body.index("<textarea", body.index(field) - 200) : body.index(field) + 200]
    assert 'name="reason"' in tag
    assert "required" in tag
    assert f'for="arvamus-{sent.pk}-tagasi-pohjus">Põhjus</label>' in body


def test_a_sent_opinion_on_a_closed_teema_is_withdrawn_and_nothing_else_moves(
    signed_in, specialist, organisation
):
    matter, sent = _sent_opinion(specialist, organisation, title="Suletud teema arvamusega")
    close_matter(
        matter=matter,
        disposition=Disposition.COMPLETED,
        actor=specialist,
        follow_ups_confirmed=True,
    )
    matter.refresh_from_db()
    before = (
        matter.is_open,
        matter.stage_id,
        matter.disposition,
        matter.disposition_reason,
        matter.closed_at,
        matter.response_deadline,
        matter.response_requested_at,
    )
    actions = dict(NextAction.objects.filter(matter=matter).values_list("pk", "status"))
    follow_ups = dict(OpinionFollowUp.objects.filter(submission=sent).values_list("pk", "state"))
    deadlines = list(MatterResponseDeadline.objects.filter(matter=matter).values())
    evidence = sent.final_version_id
    assert not matter.is_open

    response = signed_in.post(
        reverse("submissions:withdraw", kwargs={"pk": sent.pk}),
        {"reason": "Saadeti vale versioon", "tagasi": "teema"},
    )

    assert response.status_code == 302
    sent.refresh_from_db()
    matter.refresh_from_db()
    assert sent.status == SubmissionStatus.WITHDRAWN
    # The Teema is not reopened and nothing unrelated moved.
    assert (
        matter.is_open,
        matter.stage_id,
        matter.disposition,
        matter.disposition_reason,
        matter.closed_at,
        matter.response_deadline,
        matter.response_requested_at,
    ) == before
    assert dict(NextAction.objects.filter(matter=matter).values_list("pk", "status")) == actions
    assert (
        dict(OpinionFollowUp.objects.filter(submission=sent).values_list("pk", "state"))
        == follow_ups
    )
    assert list(MatterResponseDeadline.objects.filter(matter=matter).values()) == deadlines
    assert not ChangeEvent.objects.filter(
        matter=matter, event_type=ChangeEventType.MATTER_REOPENED
    ).exists()
    # The original send and its evidence stay.
    assert sent.final_version_id == evidence
    assert DocumentVersion.objects.filter(pk=evidence).exists()
    assert Submission.objects.historically_sent().filter(pk=sent.pk).exists()
    # One audit row, carrying the reason, read back in `Teema käik`.
    (event,) = ChangeEvent.objects.filter(
        matter=matter, event_type=ChangeEventType.SUBMISSION_WITHDRAWN
    )
    assert event.payload["reason"] == "Saadeti vale versioon"
    assert event.actor_id == specialist.pk
    page = signed_in.get(reverse("matters:matter_detail", kwargs={"pk": matter.pk})).content
    body = page.decode()
    assert "Arvamus tagasi võetud" in body
    assert "Saadeti vale versioon" in body


def test_a_second_withdrawal_with_a_reason_is_refused_and_writes_nothing(
    signed_in, specialist, organisation
):
    matter, sent = _sent_opinion(specialist, organisation)
    url = reverse("submissions:withdraw", kwargs={"pk": sent.pk})

    signed_in.post(url, {"reason": "Esimene põhjus"})
    signed_in.post(url, {"reason": "Teine põhjus"})

    (event,) = ChangeEvent.objects.filter(
        matter=matter, event_type=ChangeEventType.SUBMISSION_WITHDRAWN
    )
    assert event.payload["reason"] == "Esimene põhjus"
