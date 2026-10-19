"""Independent QA 2026-10-14 — workflow area: closing and reopening a Teema (ADR 0152, 0146 §8).

Probes written against main 81a7d6e4. `xfail(strict=True)` marked a reproduced
defect; plain tests pin behaviour the suite did not cover before.

QA remediation C1 (2026-10-14) fixed QA-WORKFLOW-01 and -02: their strict xfails
are ordinary regression guards now, and the two evidence probes that pinned the
defects are inverted (each says why). A closure confirmation is the digest the
warning drew (docs/adr/0152 §7), so ``header_stage`` posts what it is given.
"""

from __future__ import annotations

from datetime import timedelta

import pytest
from django.urls import reverse
from django.utils import timezone

from app.matters.closure_work import LIVE_WORK_CHANGED, LIVE_WORK_LABEL, live_work_at_closure
from app.matters.enums import EngagementKind, ResponseDeadlineOutcome, WebsiteOverviewStatus
from app.matters.models import Matter, MatterEngagement, MatterResponseDeadline
from app.matters.removal import remove_matter_record
from app.matters.services import (
    add_engagement,
    change_stage,
    create_matter,
    matter_field_revision,
    plan_website_overview,
    reopen_matter_into_stage,
)
from app.organisations.models import Organisation
from app.workflow.enums import ActionStatus
from app.workflow.follow_ups import FollowUpClosureUnconfirmed
from app.workflow.models import NextAction, OpinionFollowUp, StageVocabulary
from app.workflow.services import add_planned_action, set_next_action
from tests.follow_ups import day, send_koja_arvamus

pytestmark = pytest.mark.django_db


def stage(key: str) -> StageVocabulary:
    return StageVocabulary.objects.get(key=key)


def filed(owner, **extra) -> Matter:
    return create_matter(
        title="QA sulgemine", actor=owner, owner=owner, stage=stage("awaiting_entry"), **extra
    )


def header_stage(client, matter, key, *, confirm="", revision=None):
    """The header's stage choice; ``confirm`` is what its confirming button posts —
    the digest a refusal named (``response["X-Closure-Digest"]``)."""
    data = {
        "stage": str(stage(key).pk),
        "revision": revision if revision is not None else matter_field_revision(matter, "stage"),
    }
    if confirm:
        data["confirm_follow_up_closure"] = confirm
    return client.post(
        reverse("matters:update_field", kwargs={"pk": matter.pk, "field": "stage"}),
        data,
        HTTP_HX_REQUEST="true",
    )


# ---------------------------------------------------------------------------
# QA-WORKFLOW-01 — a removed record is still «live work» at closure
# ---------------------------------------------------------------------------


def test_a_removed_consultation_is_not_live_work_at_closure(specialist):
    matter = filed(specialist)
    engagement = add_engagement(
        matter=matter,
        kind=EngagementKind.values[0],
        title="Kogemata lisatud kaasamine",
        occurred_on=timezone.localdate(),
        actor=specialist,
    )
    remove_matter_record(
        matter_id=matter.pk, kind_key="kaasamine", record_id=engagement.pk, actor=specialist
    )
    assert not MatterEngagement.objects.visible_to(specialist).filter(matter=matter).exists()

    # Nothing live is visible anywhere; closing must not ask about a wait.
    assert live_work_at_closure(matter).is_empty
    change_stage(matter=matter, stage=stage("in_force"), actor=specialist)
    matter.refresh_from_db()
    assert not matter.is_open


def test_a_removed_planned_overview_is_not_live_work_at_closure(signed_in, specialist):
    matter = filed(specialist)
    overview = plan_website_overview(matter=matter, actor=specialist)
    remove_matter_record(
        matter_id=matter.pk, kind_key="ulevaade", record_id=overview.pk, actor=specialist
    )

    response = header_stage(signed_in, matter, "in_force")

    body = response.content.decode()
    assert "kodulehe ülevaade" not in body
    matter.refresh_from_db()
    assert not matter.is_open


def test_removed_rows_are_neither_counted_nor_ended_by_closure(specialist):
    """Was the evidence probe for QA-WORKFLOW-01 (it asserted the removed rows were
    counted, ``(1, 1)``). Inverted with the fix: removal still leaves the stored
    shape — wait open, plan PLANNED — but closure neither counts nor ends them."""
    from app.audit.models import ChangeEvent

    matter = filed(specialist)
    engagement = add_engagement(
        matter=matter,
        kind=EngagementKind.values[0],
        title="Eemaldatud kaasamine",
        occurred_on=timezone.localdate(),
        actor=specialist,
    )
    overview = plan_website_overview(matter=matter, actor=specialist)
    for kind, row in (("kaasamine", engagement), ("ulevaade", overview)):
        remove_matter_record(matter_id=matter.pk, kind_key=kind, record_id=row.pk, actor=specialist)
    engagement.refresh_from_db()
    overview.refresh_from_db()

    # Removal leaves the wait open and the plan PLANNED — the counted shape.
    assert engagement.removed_at is not None and engagement.feedback_closed_at is None
    assert overview.removed_at is not None and overview.status == WebsiteOverviewStatus.PLANNED
    work = live_work_at_closure(matter)
    assert (work.overviews, work.waits) == (0, 0)
    assert work.is_empty

    change_stage(matter=matter, stage=stage("in_force"), actor=specialist)
    matter.refresh_from_db()
    assert not matter.is_open
    engagement.refresh_from_db()
    overview.refresh_from_db()
    assert engagement.feedback_closed_at is None
    assert overview.status == WebsiteOverviewStatus.PLANNED
    # No event about a removed row was written by the closure.
    assert not ChangeEvent.objects.filter(
        object_id__in=[engagement.pk, overview.pk],
        event_type__in=["ENGAGEMENT_FEEDBACK_CLOSED", "WEBSITE_OVERVIEW_CANCELLED"],
    ).exists()


# ---------------------------------------------------------------------------
# QA-WORKFLOW-02 — a confirmation given for one set of work closes over another
# ---------------------------------------------------------------------------


def test_a_confirmation_does_not_cover_work_added_after_the_warning(
    client, specialist, other_specialist
):
    matter = filed(specialist)
    set_next_action(matter=matter, text="Praegune samm", actor=specialist)
    client.force_login(specialist)

    # 1. A asks to close; told about exactly one thing: the current step.
    warned = header_stage(client, matter, "in_force")
    assert warned.status_code == 400
    body = warned.content.decode()
    assert "praegune tegevus" in body
    assert "planeeritud" not in body and "järelkontroll" not in body
    revision = matter_field_revision(matter, "stage")
    digest = warned["X-Closure-Digest"]
    assert f'name="confirm_follow_up_closure" value="{digest}"' in body

    # 2. Meanwhile B plans a step and sends Koja arvamus (which schedules a check).
    planned = add_planned_action(
        matter=matter,
        text="Kolleegi plaan",
        target_date=timezone.localdate() + timedelta(days=7),
        actor=other_specialist,
    )
    ministry = Organisation.objects.create(name="QA Näidisministeerium")
    submission = send_koja_arvamus(matter, other_specialist, [ministry], day(-1))

    # 3. A presses the confirming button the warning drew.
    confirmed = header_stage(client, matter, "in_force", confirm=digest, revision=revision)

    # Expected: refused again, naming what is now live; nothing of B's ended.
    matter.refresh_from_db()
    planned.refresh_from_db()
    assert matter.is_open, f"closed over unseen work (status {confirmed.status_code})"
    assert planned.status == ActionStatus.PLANNED
    assert OpinionFollowUp.objects.get(submission=submission).is_monitoring
    # Re-asked: the renewed warning says the work changed and names what is live now.
    assert confirmed.status_code == 400
    renewed = confirmed.content.decode()
    assert LIVE_WORK_CHANGED in renewed
    assert "1 planeeritud tegevus" in renewed and "järelkontroll" in renewed
    assert confirmed["X-Closure-Digest"] != digest

    # 4. Confirming what is live now closes, and ends B's work — seen this time.
    closed = header_stage(
        client, matter, "in_force", confirm=confirmed["X-Closure-Digest"], revision=revision
    )
    assert closed.status_code == 200
    matter.refresh_from_db()
    planned.refresh_from_db()
    assert not matter.is_open
    assert planned.status == ActionStatus.CANCELLED


def test_a_bare_confirmation_ends_no_colleague_work(client, specialist, other_specialist):
    """Was ``test_confirmed_closure_ends_unseen_colleague_work_evidence``, which pinned
    QA-WORKFLOW-02 (a bare ``on`` closed over a colleague's planned step). Inverted
    with the fix: a confirmation that names no digest — the old ``on`` — covers
    nothing, and the colleague's step survives."""
    matter = filed(specialist)
    set_next_action(matter=matter, text="Praegune samm", actor=specialist)
    client.force_login(specialist)
    header_stage(client, matter, "in_force")
    revision = matter_field_revision(matter, "stage")
    planned = add_planned_action(
        matter=matter,
        text="Kolleegi plaan",
        target_date=timezone.localdate() + timedelta(days=7),
        actor=other_specialist,
    )

    refused = header_stage(client, matter, "in_force", confirm="on", revision=revision)

    assert refused.status_code == 400
    # Not «changed since the warning» — no warning's digest was posted at all.
    assert LIVE_WORK_CHANGED not in refused.content.decode()
    matter.refresh_from_db()
    planned.refresh_from_db()
    assert matter.is_open
    assert planned.status == ActionStatus.PLANNED


# ---------------------------------------------------------------------------
# Several kinds at once, then reopen — what stays ended (ADR 0152, 0146 §8, 0131 §12)
# ---------------------------------------------------------------------------


def test_closing_with_everything_live_then_reopening_leaves_no_contradiction(signed_in, specialist):
    matter = filed(
        specialist,
        response_deadline=timezone.localdate() + timedelta(days=20),
        response_requested_at=timezone.now(),
    )
    step = set_next_action(matter=matter, text="Praegune", actor=specialist)
    planned = add_planned_action(
        matter=matter,
        text="Plaan",
        target_date=timezone.localdate() + timedelta(days=5),
        actor=specialist,
    )
    overview = plan_website_overview(matter=matter, actor=specialist)
    engagement = add_engagement(
        matter=matter,
        kind=EngagementKind.values[0],
        title="Ootel kaasamine",
        occurred_on=timezone.localdate(),
        actor=specialist,
    )
    ministry = Organisation.objects.create(name="QA ministeerium 2")
    submission = send_koja_arvamus(matter, specialist, [ministry], day(-2))
    follow_up = OpinionFollowUp.objects.get(submission=submission)
    assert follow_up.is_monitoring

    refused = header_stage(signed_in, matter, "in_force")
    assert refused.status_code == 400
    assert LIVE_WORK_LABEL in refused.content.decode()
    matter.refresh_from_db()
    assert matter.is_open

    header_stage(signed_in, matter, "in_force", confirm=refused["X-Closure-Digest"])
    matter.refresh_from_db()
    assert not matter.is_open
    for row in (step, planned):
        row.refresh_from_db()
        assert row.status == ActionStatus.CANCELLED
    overview.refresh_from_db()
    engagement.refresh_from_db()
    follow_up.refresh_from_db()
    assert overview.status == WebsiteOverviewStatus.CANCELLED
    assert engagement.feedback_closed_at is not None
    assert not follow_up.is_monitoring
    assert not NextAction.objects.filter(
        matter=matter, status__in=(ActionStatus.OPEN, ActionStatus.PLANNED)
    ).exists()
    assert live_work_at_closure(matter).is_empty

    # Reopen: nothing is revived (0146 §8, services.close_open_feedback_waits_for_closure).
    reopen_matter_into_stage(matter=matter, stage=stage("awaiting_entry"), actor=specialist)
    matter.refresh_from_db()
    follow_up.refresh_from_db()
    assert matter.is_open
    assert not follow_up.is_monitoring
    assert not NextAction.objects.filter(
        matter=matter, status__in=(ActionStatus.OPEN, ActionStatus.PLANNED)
    ).exists()
    # The deadline the file closed with ended as CLOSED (not answered) unless kept.
    assert matter.response_deadline is None
    assert MatterResponseDeadline.objects.filter(
        matter=matter, outcome=ResponseDeadlineOutcome.CLOSED
    ).exists()

    # The Teema page after reopening must not claim a live check or step.
    page = signed_in.get(reverse("matters:matter_detail", kwargs={"pk": matter.pk}))
    assert page.status_code == 200


def test_closing_with_nothing_live_asks_nothing_through_http(signed_in, specialist):
    matter = filed(specialist)

    response = header_stage(signed_in, matter, "in_force")

    # Nothing to ask about, so the header saves at once and redraws: a 200
    # fragment, not a confirmation step and not a redirect.
    assert response.status_code == 200
    assert LIVE_WORK_LABEL not in response.content.decode()
    matter.refresh_from_db()
    assert not matter.is_open


def test_a_restricted_step_is_counted_but_never_quoted_to_the_closer(client, specialist, reader):
    """The warning is reader-blind by design (0152 §2) and must not quote words."""
    from app.core.enums import Visibility

    matter = filed(specialist)
    step = set_next_action(matter=matter, text="Salajane sammu tekst", actor=specialist)
    NextAction.objects.filter(pk=step.pk).update(visibility_override=Visibility.RESTRICTED)

    with pytest.raises(FollowUpClosureUnconfirmed) as caught:
        change_stage(matter=matter, stage=stage("in_force"), actor=specialist)

    assert "praegune tegevus" in str(caught.value)
    assert "Salajane" not in str(caught.value)
