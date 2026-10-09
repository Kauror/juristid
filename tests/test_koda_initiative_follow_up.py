"""The thirty-day check on a Chamber proposal or appeal — trigger A (docs/adr/0151 §5).

**Awaiting the product owner's choice of trigger.** What this pins is the
recommended answer: the outgoing event is the *recorded send* of the proposal,
the one dated, evidenced outbound fact the product has, and every interactive
send already schedules its check at sending date + 30 (ADR 0146). On a Teema the
Chamber started itself the send is recorded as «Koja ettepanek või pöördumine»
and its check asks about the proposal. Nothing else is scheduled: no check on
creation, no fake opinion, no fake deadline.
"""

from __future__ import annotations

import datetime as dt

import pytest
from django.urls import reverse

from app.organisations.models import Organisation
from app.submissions.enums import SubmissionKind
from app.workflow.enums import ActionStatus, FollowUpState, Track
from app.workflow.follow_ups import (
    FOLLOW_UP_PROPOSAL_TEXT_MANY,
    FOLLOW_UP_PROPOSAL_TEXT_ONE,
    FOLLOW_UP_TEXT_ONE,
    schedule_first_check,
)
from app.workflow.models import NextAction, OpinionFollowUp
from tests import factories
from tests.follow_ups import checks_of, day, follow_up_of, send_koja_arvamus

pytestmark = pytest.mark.django_db


@pytest.fixture
def ministry(db):
    return Organisation.objects.create(name="Näidisministeerium")


@pytest.fixture
def committee(db):
    return Organisation.objects.create(name="Näidiskomisjon")


def initiative(owner) -> object:
    return factories.MatterFactory(owner=owner, track=Track.KODA_INITIATIVE)


def test_creating_an_initiative_schedules_nothing(signed_in, specialist):
    """Creation is not proof that anything went out."""
    signed_in.post(
        reverse("matters:matter_create"),
        {"title": "Koja ettepanek ilma saatmiseta", "koda_initiative": "on"},
    )
    assert not OpinionFollowUp.objects.exists()
    assert not NextAction.objects.filter(follow_up__isnull=False).exists()


def test_the_sent_proposal_is_recorded_as_one_and_checked_thirty_days_later(specialist, ministry):
    matter = initiative(specialist)
    sent_on = day(-3)

    proposal = send_koja_arvamus(matter, specialist, [ministry], sent_on)

    assert proposal.kind == SubmissionKind.KODA_PROPOSAL
    follow_up = follow_up_of(proposal)
    assert follow_up.sent_on == sent_on
    assert follow_up.first_due_on == sent_on + dt.timedelta(days=30)
    assert follow_up.state == FollowUpState.MONITORING
    (check,) = checks_of(proposal)
    assert check.status == ActionStatus.PLANNED
    assert check.target_date == sent_on + dt.timedelta(days=30)
    assert check.text == FOLLOW_UP_PROPOSAL_TEXT_ONE
    assert check.responsible == specialist


def test_several_addressees_read_in_the_plural(specialist, ministry, committee):
    proposal = send_koja_arvamus(initiative(specialist), specialist, [ministry, committee], day(-1))
    assert checks_of(proposal)[0].text == FOLLOW_UP_PROPOSAL_TEXT_MANY


def test_an_ordinary_teema_still_sends_an_opinion_and_its_check_is_unchanged(
    specialist, ministry
):
    matter = factories.MatterFactory(owner=specialist)
    opinion = send_koja_arvamus(matter, specialist, [ministry], day(-2))

    assert opinion.kind == SubmissionKind.FORMAL_OPINION
    assert checks_of(opinion)[0].text == FOLLOW_UP_TEXT_ONE


def test_scheduling_twice_is_one_check(specialist, ministry):
    proposal = send_koja_arvamus(initiative(specialist), specialist, [ministry], day(-2))

    schedule_first_check(submission=proposal, actor=specialist)
    schedule_first_check(submission=proposal, actor=specialist)

    assert OpinionFollowUp.objects.filter(submission=proposal).count() == 1
    assert len(checks_of(proposal)) == 1
