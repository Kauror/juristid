"""QA remediation C1: closure confirms the work it showed; reopening restarts nothing itself.

* QA-WORKFLOW-01 — a record removed with «Kustuta» is not live work at closure.
* QA-WORKFLOW-02 — the confirmation is the digest of the work the warning
  counted; work that changed after it is refused again (docs/adr/0152 §7).
* Owner decision D9 — reopening never revives a check the closure cancelled; a
  person plans it again with «Planeeri järelkontroll» (docs/adr/0146 §8).
"""

from __future__ import annotations

import pytest
from django.urls import reverse

from app.audit.enums import ChangeEventType
from app.audit.models import ChangeEvent
from app.core.invariants import check_domain_invariants
from app.matters.closure_work import LIVE_WORK_CHANGED, live_work_at_closure
from app.matters.enums import EngagementKind, WebsiteOverviewStatus
from app.matters.removal import remove_matter_record
from app.matters.services import (
    add_engagement,
    change_stage,
    create_matter,
    plan_website_overview,
    reopen_matter_into_stage,
)
from app.organisations.models import Organisation
from app.submissions.services import withdraw_submission
from app.workflow.enums import ActionStatus, FollowUpOutcome, FollowUpState
from app.workflow.follow_ups import (
    CLOSED_WITH_MATTER,
    FOLLOW_UP_DATE_IN_THE_PAST,
    FOLLOW_UP_NOT_RESTARTABLE,
    FollowUpClosureUnconfirmed,
    complete_check,
    locked_check,
    restart_follow_up,
)
from app.workflow.models import NextAction, OpinionFollowUp, StageVocabulary
from app.workflow.services import add_planned_action, cancel_next_action, set_next_action
from tests.follow_ups import day, et, send_koja_arvamus
from tests.refusals import refused

pytestmark = pytest.mark.django_db


def stage(key: str) -> StageVocabulary:
    return StageVocabulary.objects.get(key=key)


def filed(owner):
    return create_matter(
        title="C1 sulgemine", actor=owner, owner=owner, stage=stage("consultation")
    )


@pytest.fixture
def ministry(db) -> Organisation:
    return Organisation.objects.create(name="C1 Näidisministeerium")


def warning_digest(matter, actor) -> str:
    """Ask to close as a person does, and return the digest the refusal names."""
    with pytest.raises(FollowUpClosureUnconfirmed) as caught:
        change_stage(matter=matter, stage=stage("in_force"), actor=actor)
    return caught.value.digest


def close_confirmed(matter, actor) -> None:
    change_stage(
        matter=matter,
        stage=stage("in_force"),
        actor=actor,
        follow_ups_confirmed=warning_digest(matter, actor),
    )
    matter.refresh_from_db()
    assert not matter.is_open


def closed_with_a_check(owner, ministry):
    matter = filed(owner)
    submission = send_koja_arvamus(matter, owner, [ministry], day(-3))
    follow_up = OpinionFollowUp.objects.get(submission=submission)
    check = NextAction.objects.get(follow_up=follow_up, status=ActionStatus.PLANNED)
    close_confirmed(matter, owner)
    follow_up.refresh_from_db()
    assert follow_up.state == FollowUpState.CANCELLED
    return matter, submission, follow_up, check


def reopen(matter, actor) -> None:
    reopen_matter_into_stage(matter=matter, stage=stage("consultation"), actor=actor)
    matter.refresh_from_db()
    assert matter.is_open


# ---------------------------------------------------------------------------
# QA-WORKFLOW-02 — the digest
# ---------------------------------------------------------------------------


def test_a_matching_digest_closes_and_the_closure_records_what_it_ended(specialist):
    matter = filed(specialist)
    step = set_next_action(matter=matter, text="Praegune", actor=specialist)
    planned = add_planned_action(matter=matter, text="Plaan", target_date=day(5), actor=specialist)
    overview = plan_website_overview(matter=matter, actor=specialist)
    engagement = add_engagement(
        matter=matter,
        kind=EngagementKind.values[0],
        title="Ootel",
        occurred_on=day(0),
        actor=specialist,
    )

    close_confirmed(matter, specialist)

    closed = ChangeEvent.objects.get(matter=matter, event_type=ChangeEventType.MATTER_CLOSED)
    assert closed.payload["ended"] == {
        "actions": sorted([str(step.pk), str(planned.pk)]),
        "overviews": [str(overview.pk)],
        "waits": [str(engagement.pk)],
    }


def test_the_digest_names_the_rows_not_only_their_counts(specialist, other_specialist):
    """Same counts, different work: one planned step replaced by another."""
    matter = filed(specialist)
    first = add_planned_action(matter=matter, text="Esimene", target_date=day(4), actor=specialist)
    digest = warning_digest(matter, specialist)
    before = live_work_at_closure(matter)

    cancel_next_action(action=first, actor=other_specialist, reason="Asendatud")
    second = add_planned_action(
        matter=matter, text="Kolleegi uus plaan", target_date=day(6), actor=other_specialist
    )
    after = live_work_at_closure(matter)
    assert (after.planned, after.current) == (before.planned, before.current)
    assert after.digest != digest

    with refused(LIVE_WORK_CHANGED) as caught:
        change_stage(
            matter=matter, stage=stage("in_force"), actor=specialist, follow_ups_confirmed=digest
        )
    assert caught.value.digest == after.digest
    matter.refresh_from_db()
    second.refresh_from_db()
    assert matter.is_open
    assert second.status == ActionStatus.PLANNED


def test_work_added_between_warning_and_confirm_is_not_ended(
    specialist, other_specialist, ministry
):
    matter = filed(specialist)
    set_next_action(matter=matter, text="Praegune", actor=specialist)
    digest = warning_digest(matter, specialist)

    # A colleague sends Koja arvamus: a check is scheduled after the warning.
    submission = send_koja_arvamus(matter, other_specialist, [ministry], day(-1))

    with refused(LIVE_WORK_CHANGED):
        change_stage(
            matter=matter, stage=stage("in_force"), actor=specialist, follow_ups_confirmed=digest
        )
    matter.refresh_from_db()
    assert matter.is_open
    assert OpinionFollowUp.objects.get(submission=submission).is_monitoring


def test_a_digest_from_another_matter_confirms_nothing_here(specialist):
    one, two = filed(specialist), filed(specialist)
    for matter in (one, two):
        set_next_action(matter=matter, text="Praegune", actor=specialist)
    assert live_work_at_closure(one).digest != live_work_at_closure(two).digest

    with pytest.raises(FollowUpClosureUnconfirmed):
        change_stage(
            matter=two,
            stage=stage("in_force"),
            actor=specialist,
            follow_ups_confirmed=warning_digest(one, specialist),
        )
    two.refresh_from_db()
    assert two.is_open


def test_finishing_the_step_into_closure_with_a_digest_closes_once(signed_in, specialist):
    """The early check excludes the step the save finishes; the digest it drew holds."""
    matter = filed(specialist)
    current = set_next_action(matter=matter, text="Loe eelnõu", actor=specialist)
    add_planned_action(matter=matter, text="Plaan", target_date=day(3), actor=specialist)
    url = reverse("matters:complete_current_action", kwargs={"pk": matter.pk})
    data = {"action_id": str(current.pk), "body": "Valmis.", "stage": str(stage("in_force").pk)}

    asked = signed_in.post(url, data, HTTP_HX_REQUEST="true")
    assert asked.status_code == 400
    digest = asked["X-Closure-Digest"]
    assert f'value="{digest}"' in asked.content.decode()

    done = signed_in.post(
        url, {**data, "confirm_follow_up_closure": digest}, HTTP_HX_REQUEST="true"
    )
    assert done.status_code == 200
    matter.refresh_from_db()
    assert not matter.is_open
    assert (
        ChangeEvent.objects.filter(matter=matter, event_type=ChangeEventType.MATTER_CLOSED).count()
        == 1
    )


# ---------------------------------------------------------------------------
# QA-WORKFLOW-01 — removed rows
# ---------------------------------------------------------------------------


def test_a_closed_matter_with_removed_open_rows_breaks_no_invariant(specialist):
    matter = filed(specialist)
    engagement = add_engagement(
        matter=matter,
        kind=EngagementKind.values[0],
        title="Eemaldatud",
        occurred_on=day(0),
        actor=specialist,
    )
    overview = plan_website_overview(matter=matter, actor=specialist)
    remove_matter_record(
        matter_id=matter.pk, kind_key="kaasamine", record_id=engagement.pk, actor=specialist
    )
    remove_matter_record(
        matter_id=matter.pk, kind_key="ulevaade", record_id=overview.pk, actor=specialist
    )

    change_stage(matter=matter, stage=stage("in_force"), actor=specialist)

    overview.refresh_from_db()
    assert overview.status == WebsiteOverviewStatus.PLANNED
    kinds = {finding.kind for finding in check_domain_invariants().findings}
    assert "closed-matter-planned-website-overview" not in kinds
    assert "closed-matter-open-feedback-wait" not in kinds


# ---------------------------------------------------------------------------
# D9 — reopening, and «Planeeri järelkontroll»
# ---------------------------------------------------------------------------


def test_reopening_leaves_the_cancelled_check_cancelled(specialist, ministry):
    matter, _submission, follow_up, check = closed_with_a_check(specialist, ministry)

    reopen(matter, specialist)

    follow_up.refresh_from_db()
    check.refresh_from_db()
    assert follow_up.state == FollowUpState.CANCELLED
    assert follow_up.end_reason == CLOSED_WITH_MATTER
    assert check.status == ActionStatus.CANCELLED
    assert not NextAction.objects.filter(follow_up=follow_up, status__in=("OPEN", "PLANNED"))


def test_restart_plans_exactly_one_new_check_and_keeps_the_cancelled_one(specialist, ministry):
    matter, _submission, follow_up, check = closed_with_a_check(specialist, ministry)
    reopen(matter, specialist)
    cancelled_events = ChangeEvent.objects.filter(
        object_id=check.pk, event_type=ChangeEventType.NEXT_ACTION_CANCELLED
    ).count()

    new = restart_follow_up(
        matter=matter, follow_up_id=follow_up.pk, target_date=day(10), actor=specialist
    )

    follow_up.refresh_from_db()
    check.refresh_from_db()
    assert new.pk != check.pk
    assert new.status == ActionStatus.PLANNED
    assert new.target_date == day(10)
    assert new.text == check.text
    assert new.follow_up_id == follow_up.pk
    assert new.responsible_id == matter.owner_id
    assert follow_up.state == FollowUpState.MONITORING
    assert follow_up.ended_at is None
    # The cancelled check and its history are untouched.
    assert check.status == ActionStatus.CANCELLED
    assert (
        ChangeEvent.objects.filter(
            object_id=check.pk, event_type=ChangeEventType.NEXT_ACTION_CANCELLED
        ).count()
        == cancelled_events
    )
    event = ChangeEvent.objects.get(object_id=new.pk, event_type=ChangeEventType.NEXT_ACTION_SET)
    block = event.payload["follow_up"]
    assert block["automatic"] is False
    assert block["restarted"] is True
    assert block["after"] == str(check.pk)
    assert block["previous"]["state"] == FollowUpState.CANCELLED
    assert block["previous"]["end_reason"] == CLOSED_WITH_MATTER
    assert NextAction.objects.filter(follow_up=follow_up, status="PLANNED").count() == 1
    assert not [f for f in check_domain_invariants().findings if f.kind.startswith("follow-up-")]


def test_a_repeated_restart_post_plans_nothing_more(signed_in, specialist, ministry):
    matter, _submission, follow_up, _check = closed_with_a_check(specialist, ministry)
    reopen(matter, specialist)
    url = reverse(
        "matters:restart_follow_up", kwargs={"pk": matter.pk, "follow_up_id": follow_up.pk}
    )

    page = signed_in.get(reverse("matters:matter_detail", kwargs={"pk": matter.pk}))
    assert "Planeeri järelkontroll" in page.content.decode()
    assert url in page.content.decode()

    first = signed_in.post(url, {"target_date": et(day(9))}, HTTP_HX_REQUEST="true")
    second = signed_in.post(url, {"target_date": et(day(12))}, HTTP_HX_REQUEST="true")

    assert first.status_code == 200
    assert second.status_code == 200
    live = NextAction.objects.filter(follow_up=follow_up, status__in=("OPEN", "PLANNED"))
    assert live.count() == 1
    assert live.get().target_date == day(9)
    assert (
        ChangeEvent.objects.filter(
            matter=matter,
            event_type=ChangeEventType.NEXT_ACTION_SET,
            payload__follow_up__restarted=True,
        ).count()
        == 1
    )
    # Once planned again, the offer is gone.
    assert "jarelkontroll-uuesti-" not in second.content.decode()


def test_nothing_is_offered_before_closure_or_while_closed(signed_in, specialist, ministry):
    matter = filed(specialist)
    send_koja_arvamus(matter, specialist, [ministry], day(-3))
    detail = reverse("matters:matter_detail", kwargs={"pk": matter.pk})
    assert "jarelkontroll-uuesti-" not in signed_in.get(detail).content.decode()

    close_confirmed(matter, specialist)
    assert "jarelkontroll-uuesti-" not in signed_in.get(detail).content.decode()


def test_a_restart_on_a_closed_matter_or_in_the_past_is_refused(specialist, ministry):
    from app.matters.locks import CLOSED_MATTER_REFUSAL

    matter, _submission, follow_up, _check = closed_with_a_check(specialist, ministry)
    with refused(CLOSED_MATTER_REFUSAL):
        restart_follow_up(
            matter=matter, follow_up_id=follow_up.pk, target_date=day(5), actor=specialist
        )
    reopen(matter, specialist)
    with refused(FOLLOW_UP_DATE_IN_THE_PAST):
        restart_follow_up(
            matter=matter, follow_up_id=follow_up.pk, target_date=day(-1), actor=specialist
        )
    assert not NextAction.objects.filter(follow_up=follow_up, status="PLANNED").exists()


def test_a_withdrawn_opinion_is_not_restarted(specialist, ministry):
    matter, submission, follow_up, _check = closed_with_a_check(specialist, ministry)
    reopen(matter, specialist)
    withdraw_submission(submission=submission, actor=specialist, reason="Asendatakse")

    with refused(FOLLOW_UP_NOT_RESTARTABLE):
        restart_follow_up(
            matter=matter, follow_up_id=follow_up.pk, target_date=day(5), actor=specialist
        )


def test_a_follow_up_not_ended_by_closure_is_not_restarted(specialist, ministry):
    """Only «Teema suleti» — an answered check is not reopened by this door."""
    matter = filed(specialist)
    submission = send_koja_arvamus(matter, specialist, [ministry], day(-3))
    follow_up = OpinionFollowUp.objects.get(submission=submission)
    check = NextAction.objects.get(follow_up=follow_up, status=ActionStatus.PLANNED)
    from django.db import transaction

    from app.matters.locks import lock_open_matter_for_business_write

    with transaction.atomic():
        locked = lock_open_matter_for_business_write(matter.pk)
        complete_check(
            action=locked_check(locked, check.pk),
            outcome=FollowUpOutcome.RESPONSE_RECEIVED,
            actor=specialist,
        )

    with refused(FOLLOW_UP_NOT_RESTARTABLE):
        restart_follow_up(
            matter=matter, follow_up_id=follow_up.pk, target_date=day(5), actor=specialist
        )


def test_a_reader_is_offered_nothing_and_may_not_restart(client, reader, specialist, ministry):
    matter, _submission, follow_up, _check = closed_with_a_check(specialist, ministry)
    reopen(matter, specialist)
    client.force_login(reader)
    url = reverse(
        "matters:restart_follow_up", kwargs={"pk": matter.pk, "follow_up_id": follow_up.pk}
    )

    response = client.post(url, {"target_date": et(day(5))}, HTTP_HX_REQUEST="true")

    assert response.status_code in (403, 404)
    assert not NextAction.objects.filter(follow_up=follow_up, status="PLANNED").exists()
