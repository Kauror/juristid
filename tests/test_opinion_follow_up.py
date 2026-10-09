"""`Arvamuse järelkontroll` — the check on a sent opinion (docs/adr/0146).

What is held, by section of the owner's brief:

* **creation** — every interactive send schedules exactly one check, 30
  calendar days after the recorded sending date, on the Matter's owner, linked
  to that exact opinion; drafts, the archive apply and earlier sends schedule
  nothing;
* **integrity** — a repeated or refused send schedules nothing more, a failed
  scheduling takes the send with it, and the current and planned actions, the
  response-deadline link and the plan are untouched;
* **dates and ownership** — the day moves freely, in place, planned or current,
  before or after it is due; the check follows the file; a corrected sending
  date moves only a check nobody moved;
* **completion** — a typed outcome: an answer, no answer and the next day the
  lawyer chose, or the end with a reason — atomically, and no generic control
  can finish, rewrite or remove a check;
* **closure and withdrawal** — closing needs confirmation and keeps history;
  reopening recreates nothing; a withdrawn opinion's check ends with it.
"""

from __future__ import annotations

import datetime as dt

import pytest
from django.db import IntegrityError, transaction
from django.utils import timezone

from app.audit.enums import ChangeEventType
from app.audit.models import ChangeEvent
from app.core.enums import Visibility
from app.matters import workspace
from app.matters.services import (
    assign_matter,
    change_stage,
    create_matter,
    reopen_matter_into_stage,
)
from app.submissions.enums import RecipientRole, SentAtPrecision, SubmissionKind, SubmissionStatus
from app.submissions.services import (
    SEND_NEEDS_ADDRESSEE,
    attach_final_evidence,
    correct_sent_opinion,
    create_submission,
    mark_submission_sent_on_open_matter,
    register_sent_opinion,
    sent_opinion_revision,
    withdraw_submission,
)
from app.workflow import follow_ups
from app.workflow.enums import ActionStatus, FollowUpOutcome, FollowUpState
from app.workflow.follow_ups import (
    FOLLOW_UP_CLOSURE_WARNING,
    FOLLOW_UP_DATE_IN_THE_PAST,
    FOLLOW_UP_HAS_ITS_OWN_FORM,
    FOLLOW_UP_TEXT_MANY,
    FOLLOW_UP_TEXT_ONE,
    MONITORING_END_NEEDS_REASON,
    NEXT_CHECK_NEEDS_DATE,
    FollowUpClosureUnconfirmed,
    complete_check,
    first_check_day,
    locked_check,
    reschedule_check,
    schedule_first_check,
)
from app.workflow.models import MatterPlanStep, NextAction, OpinionFollowUp, StageVocabulary
from app.workflow.services import (
    add_planned_action,
    cancel_planned_action,
    change_planned_action,
    complete_next_action,
    set_next_action_for_new_work,
)
from tests import factories
from tests.factories import send_opinion_through_services
from tests.follow_ups import (
    PDF,
    active_check,
    checks_of,
    day,
    follow_up_of,
    mark_sent,
    register_sent,
    send_koja_arvamus,
)
from tests.refusals import refused

pytestmark = pytest.mark.django_db


@pytest.fixture
def ministry():
    return factories.OrganisationFactory(name="Sünteetiline ministeerium")


@pytest.fixture
def committee():
    return factories.OrganisationFactory(name="Sünteetiline komisjon")


def _stage(key: str) -> StageVocabulary:
    return StageVocabulary.objects.get(key=key)


def _staged_matter(owner):
    return create_matter(
        title="Pakendiseaduse muudatus", actor=owner, owner=owner, stage=_stage("consultation")
    )


def _complete(check, outcome, actor, **kw):
    return workspace.check_opinion_follow_up(
        matter=check.matter, author=actor, action_id=check.pk, outcome=outcome, **kw
    )


# ---------------------------------------------------------------------------
# Creation
# ---------------------------------------------------------------------------


def test_a_formal_opinion_from_koja_arvamus_schedules_one_check(
    normal_matter, specialist, ministry
):
    sent_on = day(-2)

    opinion = send_koja_arvamus(normal_matter, specialist, [ministry], sent_on)

    follow_up = follow_up_of(opinion)
    assert follow_up.sent_on == sent_on
    assert follow_up.first_due_on == sent_on + dt.timedelta(days=30)
    assert follow_up.state == FollowUpState.MONITORING
    (check,) = checks_of(opinion)
    assert check.status == ActionStatus.PLANNED
    assert check.target_date == sent_on + dt.timedelta(days=30)
    assert check.text == FOLLOW_UP_TEXT_ONE
    assert check.responsible == specialist
    assert check.follow_up_outcome == ""
    event = ChangeEvent.objects.get(event_type=ChangeEventType.NEXT_ACTION_SET, object_id=check.pk)
    assert event.payload["planned"] is True
    assert event.payload["follow_up"]["automatic"] is True
    assert event.payload["follow_up"]["submission"] == str(opinion.pk)


@pytest.mark.parametrize(
    "kind",
    [
        SubmissionKind.SUPPLEMENTARY_OPINION,
        SubmissionKind.PARLIAMENTARY_SUBMISSION,
        SubmissionKind.JOINT_LETTER,
    ],
)
def test_every_kind_marked_sent_gets_its_own_check(normal_matter, specialist, ministry, kind):
    opinion = mark_sent(normal_matter, specialist, [ministry], kind=kind)

    check = active_check(opinion)
    assert check.target_date == timezone.localdate() + dt.timedelta(days=30)
    assert follow_up_of(opinion).submission_id == opinion.pk


def test_registreeri_saatmine_schedules_from_the_registered_day(
    normal_matter, specialist, ministry
):
    opinion = register_sent(normal_matter, specialist, [ministry], day(-3))

    assert active_check(opinion).target_date == day(27)


def test_two_opinions_on_one_matter_keep_two_independent_checks(
    normal_matter, specialist, ministry
):
    first = register_sent(normal_matter, specialist, [ministry], day(-7), title="Esimene")
    second = send_koja_arvamus(normal_matter, specialist, [ministry], day(0))

    first_check, second_check = active_check(first), active_check(second)
    assert first_check.target_date == day(23)
    assert second_check.target_date == day(30)
    assert first_check.follow_up_id != second_check.follow_up_id

    _complete(first_check, FollowUpOutcome.RESPONSE_RECEIVED, specialist)

    second_check.refresh_from_db()
    assert second_check.status == ActionStatus.PLANNED
    assert second_check.target_date == day(30)
    assert follow_up_of(second).state == FollowUpState.MONITORING


def test_several_addressees_make_one_check_in_the_plural(
    normal_matter, specialist, ministry, committee
):
    opinion = send_koja_arvamus(normal_matter, specialist, [ministry, committee], day(0))

    (check,) = checks_of(opinion)
    assert check.text == FOLLOW_UP_TEXT_MANY
    assert OpinionFollowUp.objects.filter(submission=opinion).count() == 1


def test_a_copy_for_information_is_not_an_addressee(normal_matter, specialist, ministry, committee):
    draft = create_submission(
        matter=normal_matter,
        title="Arvamus",
        actor=specialist,
        recipients=[ministry],
        for_information=[committee],
    )
    attach_final_evidence(
        submission=draft,
        content=PDF,
        original_filename="a.pdf",
        mime_type="application/pdf",
        actor=specialist,
    )
    draft.refresh_from_db()

    opinion = mark_submission_sent_on_open_matter(submission=draft, actor=specialist)

    assert active_check(opinion).text == FOLLOW_UP_TEXT_ONE
    assert opinion.recipient_rows.filter(role=RecipientRole.FOR_INFORMATION).count() == 1


def test_the_first_check_is_thirty_calendar_days_later():
    # The owner's example: sent 8 October 2026, first check 7 November 2026.
    assert first_check_day(dt.date(2026, 10, 8)) == dt.date(2026, 11, 7)
    assert first_check_day(dt.date(2026, 1, 31)) == dt.date(2026, 3, 2)
    assert first_check_day(dt.date(2028, 2, 1)) == dt.date(2028, 3, 2)  # leap year


def test_a_weekend_or_a_holiday_is_not_skipped(normal_matter, specialist, ministry):
    # 7 November 2026 is a Saturday; 24 December 2026 is jõululaupäev.
    assert dt.date(2026, 11, 7).isoweekday() == 6
    assert first_check_day(dt.date(2026, 11, 24)) == dt.date(2026, 12, 24)

    opinion = register_sent(normal_matter, specialist, [ministry], dt.date(2026, 10, 8))

    assert active_check(opinion).target_date == dt.date(2026, 11, 7)


def test_a_send_registered_now_for_an_earlier_day_is_already_due(
    normal_matter, specialist, ministry
):
    opinion = register_sent(normal_matter, specialist, [ministry], day(-45))

    check = active_check(opinion)
    assert check.target_date == day(-15)
    assert check.status == ActionStatus.PLANNED
    assert check.is_overdue()
    assert check.days_late == 15


def test_opinions_sent_before_this_release_are_not_backfilled(normal_matter, specialist, ministry):
    earlier = send_opinion_through_services(normal_matter, specialist)
    assert earlier.status == SubmissionStatus.SENT

    later = send_koja_arvamus(normal_matter, specialist, [ministry], day(0))

    assert not OpinionFollowUp.objects.filter(submission=earlier).exists()
    assert OpinionFollowUp.objects.filter(submission__matter=normal_matter).count() == 1
    assert follow_up_of(later)


def test_the_archive_path_schedules_nothing(normal_matter, specialist, ministry):
    """`register_sent_opinion` below the open-Matter wrapper is the archive's door."""
    from app.documents.enums import DocumentRole
    from app.documents.services import add_evidence_version, create_document

    document = create_document(
        matter=normal_matter, title="Ajalooline", role=DocumentRole.KODA_SUBMISSION_FINAL
    )
    version = add_evidence_version(
        document=document,
        content=PDF,
        original_filename="vana.pdf",
        mime_type="application/pdf",
    )

    historical = register_sent_opinion(
        document=document,
        version=version,
        title="Ajalooline arvamus",
        recipients=[ministry],
        sent_at=timezone.make_aware(dt.datetime(2019, 3, 4)),
        sent_at_precision=SentAtPrecision.DATE,
    )

    assert historical.status == SubmissionStatus.SENT
    assert not OpinionFollowUp.objects.exists()
    assert not NextAction.objects.filter(matter=normal_matter).exists()


def test_no_importer_reaches_the_scheduler():
    """The archive apply, the register importers and the seed commands never schedule a check.

    One module is the sanctioned exception, and it is named rather than matched:
    the 2026 Excel operational pilot schedules the first check of an imported
    opinion on a still-active file, behind its own gates (docs/adr/0148 §8). No
    other importer may join it without a decision of its own.
    """
    from pathlib import Path

    root = Path(__file__).resolve().parents[1] / "app"
    sanctioned = {root / "legacy_import" / "excel_pilot.py"}
    for path in [*root.glob("legacy_import/**/*.py"), *root.glob("core/management/**/*.py")]:
        if path in sanctioned:
            continue
        source = path.read_text(encoding="utf-8")
        assert "schedule_first_check" not in source, path
        assert "schedule_follow_up_of" not in source, path
    assert "schedule_first_check" in (root / "legacy_import" / "excel_pilot.py").read_text(
        encoding="utf-8"
    ), "the sanctioned exception no longer exists; remove it from this guard"


def test_a_draft_schedules_nothing(normal_matter, specialist, ministry):
    draft = create_submission(
        matter=normal_matter, title="Mustand", actor=specialist, recipients=[ministry]
    )
    attach_final_evidence(
        submission=draft,
        content=PDF,
        original_filename="m.pdf",
        mime_type="application/pdf",
        actor=specialist,
    )

    assert not OpinionFollowUp.objects.exists()
    assert not NextAction.objects.filter(matter=normal_matter).exists()


def test_a_restricted_opinion_gives_its_check_the_restriction(normal_matter, specialist, ministry):
    from app.documents.enums import DocumentRole
    from app.documents.services import add_evidence_version, create_document

    document = create_document(
        matter=normal_matter,
        title="Piiratud kiri",
        role=DocumentRole.KODA_SUBMISSION_FINAL,
        visibility_override=Visibility.RESTRICTED,
        created_by=specialist,
    )
    version = add_evidence_version(
        document=document,
        content=PDF,
        original_filename="p.pdf",
        mime_type="application/pdf",
        uploaded_by=specialist,
    )
    from app.submissions.services import register_sent_opinion_on_open_matter

    opinion = register_sent_opinion_on_open_matter(
        document=document,
        version=version,
        title="Piiratud arvamus",
        actor=specialist,
        recipients=[ministry],
        sent_at=timezone.make_aware(dt.datetime.combine(day(0), dt.time.min)),
        sent_at_precision=SentAtPrecision.DATE,
    )

    assert opinion.visibility_override == Visibility.RESTRICTED
    assert active_check(opinion).visibility_override == Visibility.RESTRICTED


# ---------------------------------------------------------------------------
# Integrity
# ---------------------------------------------------------------------------


def test_a_second_press_of_margi_saadetuks_schedules_nothing_more(
    normal_matter, specialist, ministry
):
    opinion = mark_sent(normal_matter, specialist, [ministry])

    with refused("Arvamus on juba saadetud."):
        mark_submission_sent_on_open_matter(submission=opinion, actor=specialist)

    assert OpinionFollowUp.objects.count() == 1
    assert NextAction.objects.filter(follow_up__isnull=False).count() == 1


def test_scheduling_twice_writes_the_first_check_once(normal_matter, specialist, ministry):
    opinion = mark_sent(normal_matter, specialist, [ministry])
    first = active_check(opinion)

    again = schedule_first_check(submission=opinion, actor=specialist)

    assert again == first
    assert NextAction.objects.filter(follow_up__isnull=False).count() == 1


def test_a_second_press_of_the_same_koja_arvamus_form_records_nothing_twice(
    normal_matter, specialist, ministry
):
    import uuid

    token = uuid.uuid4()
    send_koja_arvamus(normal_matter, specialist, [ministry], day(0), once_token=token)

    with refused(workspace.ALREADY_SAVED):
        send_koja_arvamus(normal_matter, specialist, [ministry], day(0), once_token=token)

    assert OpinionFollowUp.objects.count() == 1


def test_the_database_holds_one_follow_up_per_opinion(normal_matter, specialist, ministry):
    opinion = mark_sent(normal_matter, specialist, [ministry])

    with pytest.raises(IntegrityError), transaction.atomic():
        OpinionFollowUp.objects.create(submission=opinion, sent_on=day(0), first_due_on=day(30))


def test_the_database_holds_one_active_check_per_follow_up(normal_matter, specialist, ministry):
    opinion = mark_sent(normal_matter, specialist, [ministry])
    check = active_check(opinion)

    with pytest.raises(IntegrityError), transaction.atomic():
        NextAction.objects.create(
            matter=normal_matter,
            text=check.text,
            target_date=day(40),
            status=ActionStatus.PLANNED,
            follow_up=check.follow_up,
        )


def test_a_refused_send_schedules_nothing(normal_matter, specialist):
    draft = create_submission(matter=normal_matter, title="Adressaadita", actor=specialist)
    attach_final_evidence(
        submission=draft,
        content=PDF,
        original_filename="x.pdf",
        mime_type="application/pdf",
        actor=specialist,
    )
    draft.refresh_from_db()

    with refused(SEND_NEEDS_ADDRESSEE):
        mark_submission_sent_on_open_matter(submission=draft, actor=specialist, addressees=[])

    draft.refresh_from_db()
    assert draft.status == SubmissionStatus.DRAFT
    assert not OpinionFollowUp.objects.exists()


def test_a_failed_scheduling_takes_the_send_with_it(
    normal_matter, specialist, ministry, monkeypatch
):
    draft = create_submission(
        matter=normal_matter, title="Arvamus", actor=specialist, recipients=[ministry]
    )
    attach_final_evidence(
        submission=draft,
        content=PDF,
        original_filename="y.pdf",
        mime_type="application/pdf",
        actor=specialist,
    )
    draft.refresh_from_db()

    def broken(**_kwargs):
        raise RuntimeError("synthetic scheduling failure")

    monkeypatch.setattr(follow_ups, "schedule_first_check", broken)

    with pytest.raises(RuntimeError), transaction.atomic():
        mark_submission_sent_on_open_matter(submission=draft, actor=specialist)

    draft.refresh_from_db()
    assert draft.status == SubmissionStatus.DRAFT
    assert draft.sent_at is None
    assert not ChangeEvent.objects.filter(
        event_type=ChangeEventType.SUBMISSION_SENT, object_id=draft.pk
    ).exists()


def test_the_current_and_planned_actions_survive_a_send(normal_matter, specialist, ministry):
    current = set_next_action_for_new_work(
        matter=normal_matter, text="Loe eelnõu", target_date=day(2), actor=specialist
    )
    early = add_planned_action(
        matter=normal_matter, text="Kohtu ministeeriumiga", target_date=day(10), actor=specialist
    )
    late = add_planned_action(
        matter=normal_matter, text="Kokkuvõte liikmetele", target_date=day(60), actor=specialist
    )
    before = {
        row.pk: (row.status, row.text, row.target_date)
        for row in NextAction.objects.filter(matter=normal_matter)
    }

    opinion = send_koja_arvamus(normal_matter, specialist, [ministry], day(0))

    after = {
        row.pk: (row.status, row.text, row.target_date)
        for row in NextAction.objects.filter(matter=normal_matter, follow_up__isnull=True)
    }
    assert after == before
    assert (
        NextAction.objects.filter(matter=normal_matter, status=ActionStatus.OPEN).get() == current
    )
    assert {early.pk, late.pk} <= set(
        NextAction.objects.filter(matter=normal_matter, status=ActionStatus.PLANNED).values_list(
            "pk", flat=True
        )
    )
    assert active_check(opinion).status == ActionStatus.PLANNED


def test_finishing_the_current_step_with_the_send_promotes_by_the_usual_rule(
    normal_matter, specialist, ministry
):
    current = set_next_action_for_new_work(
        matter=normal_matter, text="Koostan arvamuse", target_date=day(1), actor=specialist
    )
    planned = add_planned_action(
        matter=normal_matter, text="Esitle juhatusele", target_date=day(50), actor=specialist
    )

    opinion = send_koja_arvamus(
        normal_matter, specialist, [ministry], day(0), complete_action_id=current.pk
    )

    current.refresh_from_db()
    planned.refresh_from_db()
    check = active_check(opinion)
    assert current.status == ActionStatus.COMPLETED
    # The earliest planned action becomes current — here the check, due in 30
    # days, before the presentation in 50 (docs/adr/0143 §A4).
    assert check.status == ActionStatus.OPEN
    assert planned.status == ActionStatus.PLANNED


def test_the_response_deadline_link_is_unchanged(normal_matter, specialist, ministry):
    from app.matters.enums import ResponseDeadlineOutcome
    from app.matters.models import MatterResponseDeadline
    from app.matters.response_deadlines import change_response_deadline, deadline_revision

    change_response_deadline(matter=normal_matter, deadline=day(4), actor=specialist)
    normal_matter.refresh_from_db()

    opinion = send_koja_arvamus(
        normal_matter,
        specialist,
        [ministry],
        day(0),
        answers_deadline=deadline_revision(normal_matter),
    )

    (ended,) = MatterResponseDeadline.objects.filter(matter=normal_matter)
    assert ended.outcome == ResponseDeadlineOutcome.ANSWERED
    assert ended.submission == opinion
    assert follow_up_of(opinion)


def test_no_plan_step_is_completed_by_a_send(specialist, ministry):
    from app.workflow.plan import seed_standard_plan

    matter = factories.MatterFactory(owner=specialist)
    seed_standard_plan(matter=matter, actor=specialist)
    before = dict(MatterPlanStep.objects.filter(matter=matter).values_list("pk", "state"))

    send_koja_arvamus(matter, specialist, [ministry], day(0))

    assert dict(MatterPlanStep.objects.filter(matter=matter).values_list("pk", "state")) == before


# ---------------------------------------------------------------------------
# Dates and ownership
# ---------------------------------------------------------------------------


def test_the_day_moves_earlier_or_later_before_it_is_due(normal_matter, specialist, ministry):
    opinion = mark_sent(normal_matter, specialist, [ministry])
    check = active_check(opinion)

    reschedule_check(
        matter=normal_matter, action_id=check.pk, target_date=day(45), actor=specialist
    )
    reschedule_check(matter=normal_matter, action_id=check.pk, target_date=day(5), actor=specialist)

    moved = active_check(opinion)
    assert moved.pk == check.pk
    assert moved.target_date == day(5)
    assert moved.responsible == specialist
    assert moved.follow_up_id == check.follow_up_id
    assert follow_up_of(opinion).first_due_on == day(30)
    events = ChangeEvent.objects.filter(
        event_type=ChangeEventType.NEXT_ACTION_RESCHEDULED, object_id=check.pk
    ).order_by("occurred_at")
    assert [(e.payload["from"], e.payload["to"]) for e in events] == [
        (day(30).isoformat(), day(45).isoformat()),
        (day(45).isoformat(), day(5).isoformat()),
    ]
    assert NextAction.objects.filter(follow_up__submission=opinion).count() == 1


def test_the_day_moves_after_it_is_overdue(normal_matter, specialist, ministry):
    opinion = register_sent(normal_matter, specialist, [ministry], day(-60))
    check = active_check(opinion)
    assert check.is_overdue()

    reschedule_check(matter=normal_matter, action_id=check.pk, target_date=day(7), actor=specialist)

    check.refresh_from_db()
    assert check.target_date == day(7)
    assert not check.is_overdue()


def test_a_current_check_moves_too(normal_matter, specialist, ministry):
    opinion = mark_sent(normal_matter, specialist, [ministry])
    check = active_check(opinion)
    NextAction.objects.filter(pk=check.pk).update(status=ActionStatus.OPEN)

    reschedule_check(matter=normal_matter, action_id=check.pk, target_date=day(3), actor=specialist)

    check.refresh_from_db()
    assert (check.status, check.target_date) == (ActionStatus.OPEN, day(3))


def test_a_day_in_the_past_is_refused(normal_matter, specialist, ministry):
    check = active_check(mark_sent(normal_matter, specialist, [ministry]))

    with refused(FOLLOW_UP_DATE_IN_THE_PAST):
        reschedule_check(
            matter=normal_matter, action_id=check.pk, target_date=day(-1), actor=specialist
        )


def test_the_generic_muuda_does_not_rewrite_a_check(normal_matter, specialist, ministry):
    check = active_check(mark_sent(normal_matter, specialist, [ministry]))

    with refused(FOLLOW_UP_HAS_ITS_OWN_FORM):
        change_planned_action(
            matter=normal_matter,
            action_id=check.pk,
            text="Midagi muud",
            target_date=day(9),
            actor=specialist,
        )
    NextAction.objects.filter(pk=check.pk).update(status=ActionStatus.OPEN)
    with refused(FOLLOW_UP_HAS_ITS_OWN_FORM):
        workspace.change_current_action(
            matter=normal_matter, actor=specialist, action_id=check.pk, text="Midagi muud"
        )
    check.refresh_from_db()
    assert check.text == FOLLOW_UP_TEXT_ONE


def test_a_reassigned_matter_takes_its_pending_check_and_keeps_the_history(
    normal_matter, specialist, other_specialist, ministry
):
    opinion = mark_sent(normal_matter, specialist, [ministry])
    first = active_check(opinion)
    _complete(first, FollowUpOutcome.NO_RESPONSE, specialist, next_check_on=day(14))

    assign_matter(matter=normal_matter, owner=other_specialist, actor=specialist)

    first.refresh_from_db()
    second = active_check(opinion)
    assert second.responsible == other_specialist
    assert first.ended_by == specialist
    assert first.responsible == specialist
    assert NextAction.objects.filter(follow_up__submission=opinion).count() == 2


def test_a_corrected_sending_date_moves_a_check_nobody_moved(normal_matter, specialist, ministry):
    opinion = register_sent(normal_matter, specialist, [ministry], day(-3))
    check = active_check(opinion)

    correct_sent_opinion(
        submission=opinion,
        sent_at=timezone.make_aware(dt.datetime.combine(day(-10), dt.time.min)),
        sent_at_precision=SentAtPrecision.DATE,
        summary=opinion.summary,
        kind=opinion.kind,
        actor=specialist,
        expected_revision=sent_opinion_revision(opinion),
    )

    check.refresh_from_db()
    follow_up = follow_up_of(opinion)
    assert check.target_date == day(20)
    assert (follow_up.sent_on, follow_up.first_due_on) == (day(-10), day(20))
    event = ChangeEvent.objects.get(
        event_type=ChangeEventType.NEXT_ACTION_RESCHEDULED, object_id=check.pk
    )
    assert event.payload["recalculated"] is True
    assert event.payload["from"] == day(27).isoformat()


def test_a_day_a_lawyer_chose_survives_a_sending_date_correction(
    normal_matter, specialist, ministry
):
    opinion = register_sent(normal_matter, specialist, [ministry], day(-3))
    check = active_check(opinion)
    reschedule_check(
        matter=normal_matter, action_id=check.pk, target_date=day(12), actor=specialist
    )
    opinion.refresh_from_db()

    correct_sent_opinion(
        submission=opinion,
        sent_at=timezone.make_aware(dt.datetime.combine(day(-10), dt.time.min)),
        sent_at_precision=SentAtPrecision.DATE,
        summary=opinion.summary,
        kind=opinion.kind,
        actor=specialist,
        expected_revision=sent_opinion_revision(opinion),
    )

    check.refresh_from_db()
    assert check.target_date == day(12)
    assert follow_up_of(opinion).first_due_on == day(27)


def test_correcting_title_recipients_or_kind_schedules_no_second_check(
    normal_matter, specialist, ministry, committee
):
    opinion = register_sent(normal_matter, specialist, [ministry], day(-3))

    correct_sent_opinion(
        submission=opinion,
        sent_at=opinion.sent_at,
        sent_at_precision=opinion.sent_at_precision,
        summary="Parandatud kokkuvõte",
        kind=SubmissionKind.SUPPLEMENTARY_OPINION,
        addressees=[ministry, committee],
        actor=specialist,
        expected_revision=sent_opinion_revision(opinion),
    )

    assert OpinionFollowUp.objects.filter(submission=opinion).count() == 1
    assert NextAction.objects.filter(follow_up__submission=opinion).count() == 1
    assert active_check(opinion).target_date == day(27)


# ---------------------------------------------------------------------------
# Completion
# ---------------------------------------------------------------------------


def test_vastus_saabunud_finishes_only_the_chosen_check(normal_matter, specialist, ministry):
    first = register_sent(normal_matter, specialist, [ministry], day(-5), title="Esimene")
    second = mark_sent(normal_matter, specialist, [ministry])
    check = active_check(first)

    result = _complete(
        check, FollowUpOutcome.RESPONSE_RECEIVED, specialist, body="Ministeerium vastas kirjaga."
    )

    check.refresh_from_db()
    assert check.status == ActionStatus.COMPLETED
    assert check.follow_up_outcome == FollowUpOutcome.RESPONSE_RECEIVED
    assert check.ended_by == specialist
    assert follow_up_of(first).state == FollowUpState.RESPONSE_RECEIVED
    assert active_check(second).status == ActionStatus.PLANNED
    assert result.action is None
    assert "Vastus saabunud. Ministeerium vastas kirjaga." in result.entry.body
    normal_matter.refresh_from_db()
    assert normal_matter.is_open
    assert not NextAction.objects.filter(follow_up__submission=first, status__in=follow_ups.ACTIVE)


def test_a_received_answer_carries_its_file(normal_matter, specialist, ministry):
    from tests.follow_ups import pdf

    check = active_check(mark_sent(normal_matter, specialist, [ministry]))

    result = _complete(
        check,
        FollowUpOutcome.RESPONSE_RECEIVED,
        specialist,
        body="Vastus lisatud.",
        uploads=[pdf("ministeeriumi_vastus.pdf")],
    )

    (document,) = result.documents
    assert document.matter == normal_matter
    assert document.current_version.original_filename == "ministeeriumi_vastus.pdf"


def test_no_response_needs_the_next_day(normal_matter, specialist, ministry):
    check = active_check(mark_sent(normal_matter, specialist, [ministry]))

    with refused(NEXT_CHECK_NEEDS_DATE):
        _complete(check, FollowUpOutcome.NO_RESPONSE, specialist)

    check.refresh_from_db()
    assert check.status == ActionStatus.PLANNED


def test_no_response_records_the_check_and_plans_the_next_on_the_chosen_day(
    normal_matter, specialist, ministry
):
    opinion = mark_sent(normal_matter, specialist, [ministry])
    first = active_check(opinion)

    result = _complete(first, FollowUpOutcome.NO_RESPONSE, specialist, next_check_on=day(21))

    first.refresh_from_db()
    second = active_check(opinion)
    assert first.status == ActionStatus.COMPLETED
    assert first.follow_up_outcome == FollowUpOutcome.NO_RESPONSE
    assert result.action == second
    assert second.target_date == day(21)
    assert second.status == ActionStatus.PLANNED
    assert second.follow_up_id == first.follow_up_id
    assert second.text == first.text
    assert follow_up_of(opinion).state == FollowUpState.MONITORING
    event = ChangeEvent.objects.get(event_type=ChangeEventType.NEXT_ACTION_SET, object_id=second.pk)
    assert event.payload["follow_up"] == {
        "id": str(first.follow_up_id),
        "submission": str(opinion.pk),
        "automatic": False,
        "check": 2,
        "after": str(first.pk),
    }


def test_no_response_is_one_transaction(normal_matter, specialist, ministry, monkeypatch):
    opinion = mark_sent(normal_matter, specialist, [ministry])
    first = active_check(opinion)

    def broken(_follow_up):
        raise RuntimeError("synthetic failure while planning the next check")

    monkeypatch.setattr(follow_ups, "_completed_checks", broken)

    with pytest.raises(RuntimeError), transaction.atomic():
        _complete(first, FollowUpOutcome.NO_RESPONSE, specialist, next_check_on=day(14))

    first.refresh_from_db()
    assert first.status == ActionStatus.PLANNED
    assert first.follow_up_outcome == ""
    assert NextAction.objects.filter(follow_up__submission=opinion).count() == 1


def test_a_monitoring_sequence_keeps_every_check(normal_matter, specialist, ministry):
    opinion = mark_sent(normal_matter, specialist, [ministry])

    _complete(active_check(opinion), FollowUpOutcome.NO_RESPONSE, specialist, next_check_on=day(10))
    _complete(active_check(opinion), FollowUpOutcome.NO_RESPONSE, specialist, next_check_on=day(20))
    _complete(active_check(opinion), FollowUpOutcome.RESPONSE_RECEIVED, specialist, body="Vastati.")

    outcomes = [(row.status, row.follow_up_outcome, row.target_date) for row in checks_of(opinion)]
    assert outcomes == [
        (ActionStatus.COMPLETED, FollowUpOutcome.NO_RESPONSE, day(30)),
        (ActionStatus.COMPLETED, FollowUpOutcome.NO_RESPONSE, day(10)),
        (ActionStatus.COMPLETED, FollowUpOutcome.RESPONSE_RECEIVED, day(20)),
    ]
    assert follow_up_of(opinion).state == FollowUpState.RESPONSE_RECEIVED


def test_ending_monitoring_needs_a_reason(normal_matter, specialist, ministry):
    check = active_check(mark_sent(normal_matter, specialist, [ministry]))

    with refused(MONITORING_END_NEEDS_REASON):
        _complete(check, FollowUpOutcome.MONITORING_ENDED, specialist, body="   ")

    check.refresh_from_db()
    assert check.status == ActionStatus.PLANNED


def test_ending_monitoring_schedules_nothing_and_keeps_the_reason(
    normal_matter, specialist, ministry
):
    opinion = mark_sent(normal_matter, specialist, [ministry])
    _complete(active_check(opinion), FollowUpOutcome.NO_RESPONSE, specialist, next_check_on=day(9))

    _complete(
        active_check(opinion),
        FollowUpOutcome.MONITORING_ENDED,
        specialist,
        body="Eelnõu võeti menetlusest tagasi.",
    )

    follow_up = follow_up_of(opinion)
    assert follow_up.state == FollowUpState.ENDED
    assert follow_up.end_reason == "Eelnõu võeti menetlusest tagasi."
    assert follow_up.ended_by == specialist
    assert not NextAction.objects.filter(
        follow_up=follow_up, status__in=(ActionStatus.OPEN, ActionStatus.PLANNED)
    ).exists()
    assert NextAction.objects.filter(follow_up=follow_up).count() == 2
    normal_matter.refresh_from_db()
    assert normal_matter.is_open


def test_finishing_a_current_check_promotes_the_next_planned_action(
    normal_matter, specialist, ministry
):
    opinion = mark_sent(normal_matter, specialist, [ministry])
    check = active_check(opinion)
    NextAction.objects.filter(pk=check.pk).update(status=ActionStatus.OPEN)
    later = add_planned_action(
        matter=normal_matter, text="Kokkuvõte juhatusele", target_date=day(40), actor=specialist
    )

    _complete(NextAction.objects.get(pk=check.pk), FollowUpOutcome.RESPONSE_RECEIVED, specialist)

    later.refresh_from_db()
    assert later.status == ActionStatus.OPEN


def test_finishing_a_planned_check_promotes_nothing(normal_matter, specialist, ministry):
    current = set_next_action_for_new_work(
        matter=normal_matter, text="Loe eelnõu", target_date=day(50), actor=specialist
    )
    check = active_check(mark_sent(normal_matter, specialist, [ministry]))

    _complete(check, FollowUpOutcome.RESPONSE_RECEIVED, specialist)

    current.refresh_from_db()
    assert current.status == ActionStatus.OPEN
    assert current.target_date == day(50)


def test_no_generic_control_finishes_or_removes_a_check(normal_matter, specialist, ministry):
    opinion = mark_sent(normal_matter, specialist, [ministry])
    check = active_check(opinion)

    with refused(FOLLOW_UP_HAS_ITS_OWN_FORM):
        workspace.complete_planned_action(
            matter=normal_matter, author=specialist, action_id=check.pk, body="Tehtud."
        )
    with refused(FOLLOW_UP_HAS_ITS_OWN_FORM):
        cancel_planned_action(matter=normal_matter, action_id=check.pk, actor=specialist)

    NextAction.objects.filter(pk=check.pk).update(status=ActionStatus.OPEN)
    with refused(FOLLOW_UP_HAS_ITS_OWN_FORM):
        workspace.complete_current_action(
            matter=normal_matter, author=specialist, action_id=check.pk, body="Tehtud."
        )
    with refused(FOLLOW_UP_HAS_ITS_OWN_FORM):
        complete_next_action(action=NextAction.objects.get(pk=check.pk), actor=specialist)
    with refused(FOLLOW_UP_HAS_ITS_OWN_FORM):
        send_koja_arvamus(
            normal_matter, specialist, [ministry], day(0), complete_action_id=check.pk
        )

    check.refresh_from_db()
    assert check.status == ActionStatus.OPEN
    assert follow_up_of(opinion).state == FollowUpState.MONITORING


def test_the_database_refuses_a_completed_check_without_an_outcome(
    normal_matter, specialist, ministry
):
    check = active_check(mark_sent(normal_matter, specialist, [ministry]))

    with pytest.raises(IntegrityError), transaction.atomic():
        NextAction.objects.filter(pk=check.pk).update(
            status=ActionStatus.COMPLETED, ended_at=timezone.now()
        )


def test_a_stale_tab_naming_a_finished_check_is_refused(normal_matter, specialist, ministry):
    opinion = mark_sent(normal_matter, specialist, [ministry])
    check = active_check(opinion)
    _complete(check, FollowUpOutcome.RESPONSE_RECEIVED, specialist)

    with refused(follow_ups.FOLLOW_UP_CHANGED):
        _complete(check, FollowUpOutcome.NO_RESPONSE, specialist, next_check_on=day(5))
    with refused(follow_ups.FOLLOW_UP_CHANGED):
        reschedule_check(
            matter=normal_matter, action_id=check.pk, target_date=day(5), actor=specialist
        )


def test_new_work_over_a_current_check_sends_it_back_to_the_plan(
    normal_matter, specialist, ministry
):
    opinion = mark_sent(normal_matter, specialist, [ministry])
    check = active_check(opinion)
    NextAction.objects.filter(pk=check.pk).update(status=ActionStatus.OPEN)

    new = set_next_action_for_new_work(
        matter=normal_matter, text="Uus kiireloomuline töö", target_date=day(1), actor=specialist
    )

    check.refresh_from_db()
    assert new.status == ActionStatus.OPEN
    assert check.status == ActionStatus.PLANNED
    assert check.replaced_by_id is None
    assert follow_up_of(opinion).state == FollowUpState.MONITORING


# ---------------------------------------------------------------------------
# Closure, reopening and withdrawal
# ---------------------------------------------------------------------------


def test_closing_with_a_pending_check_needs_confirmation(specialist, ministry):
    matter = _staged_matter(specialist)
    opinion = mark_sent(matter, specialist, [ministry])

    with refused(FOLLOW_UP_CLOSURE_WARNING):
        change_stage(matter=matter, stage=_stage("monitoring_stopped"), actor=specialist)

    matter.refresh_from_db()
    assert matter.is_open
    assert matter.stage.key == "consultation"
    assert active_check(opinion).status == ActionStatus.PLANNED


def test_a_confirmed_closure_cancels_the_check_and_keeps_its_history(specialist, ministry):
    matter = _staged_matter(specialist)
    opinion = mark_sent(matter, specialist, [ministry])
    _complete(active_check(opinion), FollowUpOutcome.NO_RESPONSE, specialist, next_check_on=day(7))

    change_stage(
        matter=matter,
        stage=_stage("monitoring_stopped"),
        actor=specialist,
        follow_ups_confirmed=True,
    )

    matter.refresh_from_db()
    assert not matter.is_open
    done, cancelled = checks_of(opinion)
    assert done.status == ActionStatus.COMPLETED
    assert done.follow_up_outcome == FollowUpOutcome.NO_RESPONSE
    assert cancelled.status == ActionStatus.CANCELLED
    assert cancelled.follow_up_id == done.follow_up_id
    follow_up = follow_up_of(opinion)
    assert follow_up.state == FollowUpState.CANCELLED
    assert follow_up.end_reason == "Teema suleti"
    opinion.refresh_from_db()
    assert opinion.status == SubmissionStatus.SENT
    event = ChangeEvent.objects.get(
        event_type=ChangeEventType.NEXT_ACTION_CANCELLED, object_id=cancelled.pk
    )
    assert event.payload["follow_up"]["submission"] == str(opinion.pk)


def test_a_matter_without_a_check_closes_as_it_always_did(specialist):
    matter = _staged_matter(specialist)

    change_stage(matter=matter, stage=_stage("in_force"), actor=specialist)

    matter.refresh_from_db()
    assert not matter.is_open


def test_a_save_that_sends_and_closes_asks_first_and_stores_nothing(specialist, ministry):
    from app.documents.models import Document

    matter = _staged_matter(specialist)

    with refused(FOLLOW_UP_CLOSURE_WARNING):
        send_koja_arvamus(matter, specialist, [ministry], day(0), stage=_stage("in_force"))

    assert not Document.objects.filter(matter=matter).exists()
    matter.refresh_from_db()
    assert matter.is_open

    opinion = send_koja_arvamus(
        matter,
        specialist,
        [ministry],
        day(0),
        stage=_stage("in_force"),
        follow_ups_confirmed=True,
    )

    matter.refresh_from_db()
    assert not matter.is_open
    (check,) = checks_of(opinion)
    assert check.status == ActionStatus.CANCELLED
    assert follow_up_of(opinion).state == FollowUpState.CANCELLED


def test_the_current_step_s_stage_move_asks_before_closing(specialist, ministry):
    matter = _staged_matter(specialist)
    mark_sent(matter, specialist, [ministry])
    current = set_next_action_for_new_work(
        matter=matter, text="Loe eelnõu", target_date=day(2), actor=specialist
    )

    with refused(FOLLOW_UP_CLOSURE_WARNING):
        workspace.complete_current_action(
            matter=matter,
            author=specialist,
            action_id=current.pk,
            body="Lugesin läbi.",
            stage=_stage("in_force"),
        )

    current.refresh_from_db()
    assert current.status == ActionStatus.OPEN


def test_reopening_does_not_bring_cancelled_checks_back(specialist, ministry):
    matter = _staged_matter(specialist)
    opinion = mark_sent(matter, specialist, [ministry])
    change_stage(
        matter=matter, stage=_stage("in_force"), actor=specialist, follow_ups_confirmed=True
    )
    matter.refresh_from_db()

    reopen_matter_into_stage(matter=matter, stage=_stage("consultation"), actor=specialist)

    matter.refresh_from_db()
    assert matter.is_open
    assert not NextAction.objects.filter(
        follow_up__submission=opinion, status__in=(ActionStatus.OPEN, ActionStatus.PLANNED)
    ).exists()
    assert follow_up_of(opinion).state == FollowUpState.CANCELLED


def test_no_check_is_created_on_a_closed_matter(specialist, ministry):
    matter = _staged_matter(specialist)
    change_stage(matter=matter, stage=_stage("in_force"), actor=specialist)
    matter.refresh_from_db()
    historical = send_opinion_through_services(matter, specialist)

    from app.matters.locks import CLOSED_MATTER_REFUSAL

    with refused(CLOSED_MATTER_REFUSAL):
        schedule_first_check(submission=historical, actor=specialist)
    assert not OpinionFollowUp.objects.exists()


def test_withdrawing_an_opinion_ends_its_check_and_keeps_the_done_ones(
    normal_matter, specialist, ministry
):
    opinion = mark_sent(normal_matter, specialist, [ministry])
    other = send_koja_arvamus(normal_matter, specialist, [ministry], day(0))
    _complete(active_check(opinion), FollowUpOutcome.NO_RESPONSE, specialist, next_check_on=day(5))

    withdraw_submission(submission=opinion, actor=specialist, reason="Asendatakse uuega")

    done, cancelled = checks_of(opinion)
    assert done.status == ActionStatus.COMPLETED
    assert cancelled.status == ActionStatus.CANCELLED
    follow_up = follow_up_of(opinion)
    assert follow_up.state == FollowUpState.CANCELLED
    assert follow_up.end_reason == follow_ups.WITHDRAWN_OPINION
    assert active_check(other).status == ActionStatus.PLANNED


def test_a_replacement_sent_later_gets_its_own_check(normal_matter, specialist, ministry):
    withdrawn = mark_sent(normal_matter, specialist, [ministry])
    withdraw_submission(submission=withdrawn, actor=specialist, reason="Viga tekstis")

    replacement = mark_sent(normal_matter, specialist, [ministry], title="Parandatud arvamus")

    assert active_check(replacement).target_date == day(30)
    assert follow_up_of(withdrawn).state == FollowUpState.CANCELLED


def test_the_locked_check_belongs_to_its_own_matter(specialist, ministry):
    one = factories.MatterFactory(owner=specialist)
    other = factories.MatterFactory(owner=specialist)
    check = active_check(mark_sent(one, specialist, [ministry]))

    with refused(follow_ups.FOLLOW_UP_CHANGED):
        locked_check(other, check.pk)


def test_an_outcome_outside_the_vocabulary_is_refused(normal_matter, specialist, ministry):
    check = active_check(mark_sent(normal_matter, specialist, [ministry]))

    with refused(follow_ups.FOLLOW_UP_NEEDS_OUTCOME):
        complete_check(action=check, outcome="MAYBE", actor=specialist)


def test_the_closure_refusal_is_its_own_kind():
    assert str(FollowUpClosureUnconfirmed()) == FOLLOW_UP_CLOSURE_WARNING


# ---------------------------------------------------------------------------
# Deletion and the integrity verifier
# ---------------------------------------------------------------------------


def test_a_teema_with_checks_can_still_be_deleted(normal_matter, specialist, ministry):
    from app.matters.deletion import delete_matter

    opinion = mark_sent(normal_matter, specialist, [ministry])
    _complete(active_check(opinion), FollowUpOutcome.NO_RESPONSE, specialist, next_check_on=day(3))

    delete_matter(matter=normal_matter, actor=specialist)

    assert not OpinionFollowUp.objects.exists()
    assert not NextAction.objects.filter(matter=normal_matter).exists()


def test_the_verifier_finds_nothing_in_an_ordinary_monitoring_round(specialist, ministry):
    from app.core.invariants import check_domain_invariants

    matter = _staged_matter(specialist)
    answered = mark_sent(matter, specialist, [ministry])
    pending = send_koja_arvamus(matter, specialist, [ministry], day(0))
    _complete(active_check(answered), FollowUpOutcome.NO_RESPONSE, specialist, next_check_on=day(4))
    _complete(active_check(answered), FollowUpOutcome.RESPONSE_RECEIVED, specialist)
    assert active_check(pending)

    report = check_domain_invariants()

    assert not [finding for finding in report.findings if finding.kind.startswith("follow-up-")]


def test_the_verifier_reports_a_lost_check(normal_matter, specialist, ministry):
    from app.core.invariants import check_domain_invariants

    opinion = mark_sent(normal_matter, specialist, [ministry])
    check = active_check(opinion)
    # Around every service: the check ends and the monitoring is left as it was.
    NextAction.objects.filter(pk=check.pk).update(
        status=ActionStatus.CANCELLED, ended_at=timezone.now()
    )

    kinds = {finding.kind for finding in check_domain_invariants().findings}

    assert "follow-up-monitoring-without-live-check" in kinds


def test_the_release_still_serving_survives_the_migration(normal_matter, specialist):
    """A `NextAction` written without the two new columns — as c60e2d73 writes
    one between `migrate` and the swap — is valid under every new constraint."""
    from django.db import connection

    with connection.cursor() as cursor:
        cursor.execute(
            """
            INSERT INTO workflow_nextaction (
                id, created_at, updated_at, visibility_override, matter_id, text, kind,
                date_semantics, target_date, date_precision, source_text, status
            )
            VALUES (gen_random_uuid(), now(), now(), '', %s, 'Vana väljalase', 'DO',
                    'DEADLINE', %s, 'EXACT', '', 'PLANNED')
            RETURNING follow_up_id, follow_up_outcome
            """,
            [normal_matter.pk, day(5)],
        )
        assert cursor.fetchone() == (None, "")


def test_withdrawing_an_opinion_whose_check_is_current_promotes_the_next(
    normal_matter, specialist, ministry
):
    opinion = mark_sent(normal_matter, specialist, [ministry])
    check = active_check(opinion)
    NextAction.objects.filter(pk=check.pk).update(status=ActionStatus.OPEN)
    later = add_planned_action(
        matter=normal_matter, text="Kohtumine ministeeriumis", target_date=day(15), actor=specialist
    )

    withdraw_submission(submission=opinion, actor=specialist, reason="Asendatakse")

    later.refresh_from_db()
    check.refresh_from_db()
    assert check.status == ActionStatus.CANCELLED
    assert later.status == ActionStatus.OPEN


def test_whoever_may_close_a_matter_may_see_every_check_on_it():
    """The closure warning is reader-blind (docs/adr/0146 §8), and that is safe
    because every role that may write — and so close — also sees restricted
    work. If the two sets ever part, the warning would tell somebody a restricted
    opinion exists, and this fails first."""
    from app.core.authorization import ROLES_WITH_BUSINESS_WRITE, ROLES_WITH_RESTRICTED_ACCESS

    assert ROLES_WITH_BUSINESS_WRITE <= ROLES_WITH_RESTRICTED_ACCESS
