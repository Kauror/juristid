"""Personal deadline notifications and the one inbox (docs/adr/0198).

Synthetic data only. The generator is exercised with an explicit ``today``
wherever the clock is not itself the subject; the two DST tests move
`django.utils.timezone.now` instead, because «today» is the Tallinn date.
"""

from __future__ import annotations

import re
from datetime import UTC, date, datetime, timedelta

import pytest
from django.core import mail
from django.core.management import call_command
from django.core.management.base import CommandError
from django.test import Client
from django.urls import reverse

from app.core.enums import Visibility
from app.matters.enums import EngagementKind
from app.matters.models import Matter, MatterEngagement
from app.matters.services import assign_matter
from app.notifications import deadlines, inbox, preferences
from app.notifications.categories import CATEGORIES, ESCALATION_KEY
from app.notifications.email import deliver_email, email_delivery_allowed
from app.notifications.enums import (
    NotificationCategory,
    NotificationKind,
    NotificationPurpose,
    ReminderStage,
    ReminderStatus,
    RetiredReason,
)
from app.notifications.models import (
    CommencementReminder,
    DeadlineNotification,
    NotificationPreference,
    NotificationSettings,
)
from app.notifications.services import reconcile_commencement_reminders
from app.workflow.enums import ActionKind, ActionStatus, DatePrecision, DateSemantics
from app.workflow.services import complete_next_action
from tests import factories
from tests.gate import PASSWORD, apply_shared_gate

pytestmark = pytest.mark.django_db

#: A Monday. Every case names its days relative to it.
TODAY = date(2026, 11, 2)

OPINION = NotificationCategory.OPINION_DEADLINE
ACTION = NotificationCategory.ACTION_DEADLINE


def _run(day: date) -> deadlines.DeadlineReconcileResult:
    return deadlines.reconcile_deadline_notifications(day)


def _rows(**filters) -> list[DeadlineNotification]:
    return list(DeadlineNotification.objects.filter(**filters).order_by("created_at", "pk"))


def _opinion(owner, deadline: date, **extra) -> Matter:
    return factories.MatterFactory(owner=owner, response_deadline=deadline, **extra)


def _step(matter, *, on: date, responsible=None, **extra):
    fields = {
        "kind": ActionKind.DO,
        "date_semantics": DateSemantics.DEADLINE,
        "date_precision": DatePrecision.EXACT,
        "status": ActionStatus.OPEN,
        **extra,
    }
    return factories.NextActionFactory(
        matter=matter, target_date=on, responsible=responsible, **fields
    )


def _feature_started(on: date) -> None:
    """The feature has been delivering since ``on`` (`deadlines.feature_start`).

    One row of an inactive account, never shown, whose `created_at` is that day.
    """
    gone = factories.UserFactory(is_active=False, display_name="Lahkunud kasutaja")
    matter = factories.MatterFactory(owner=None)
    row = DeadlineNotification.objects.create(
        recipient=gone,
        matter=matter,
        category=OPINION,
        source_type="MATTER",
        source_id=matter.pk,
        source_date=on,
        purpose=NotificationPurpose.ON_DAY,
        kind=NotificationKind.ACTION,
        due_on=on,
        retired_at=datetime(2026, 1, 1, tzinfo=UTC),
        retired_reason=RetiredReason.INELIGIBLE,
    )
    DeadlineNotification.objects.filter(pk=row.pk).update(
        created_at=datetime(on.year, on.month, on.day, 9, 0, tzinfo=UTC)
    )


def _personal(**filters) -> list[DeadlineNotification]:
    """Rows of real recipients — without `_feature_started`'s marker row."""
    return [row for row in _rows(**filters) if row.recipient.is_active]


def _live(user, day: date) -> list[inbox.InboxItem]:
    return [item for item in inbox.inbox_for(user, day) if item.type == inbox.TYPE_DEADLINE]


# ---------------------------------------------------------------------------
# The schedule rule, on its own
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("days_to_go", "expected"),
    [(9, None), (8, None), (7, 7), (5, 7), (4, 7), (3, 3), (2, 3), (1, 1), (0, None), (-1, None)],
)
def test_only_the_interval_whose_window_holds_today_is_owed(days_to_go, expected):
    source = TODAY + timedelta(days=days_to_go)
    assert deadlines.owed_advance(source, TODAY, (7, 3, 1)) == expected


def test_a_custom_schedule_follows_the_same_rule():
    source = TODAY + timedelta(days=10)
    assert deadlines.owed_advance(source, TODAY, (14, 2)) == 14
    assert deadlines.owed_advance(TODAY + timedelta(days=2), TODAY, (14, 2)) == 2
    assert deadlines.owed_advance(TODAY + timedelta(days=1), TODAY, (14, 2)) == 2
    assert deadlines.owed_advance(TODAY + timedelta(days=3), TODAY, ()) is None


# ---------------------------------------------------------------------------
# Who is told, and when
# ---------------------------------------------------------------------------


def test_only_the_current_responsible_lawyer_gets_the_advance_notice(
    specialist, other_specialist, department_head
):
    matter = _opinion(specialist, TODAY + timedelta(days=7))

    result = _run(TODAY)

    (row,) = _rows()
    assert result.created == 1
    assert (row.recipient, row.matter, row.category) == (specialist, matter, OPINION)
    assert (row.purpose, row.offset_days, row.kind) == (
        NotificationPurpose.ADVANCE,
        7,
        NotificationKind.INFO,
    )
    assert row.due_on == TODAY
    # The department head gets nothing before the deadline, nor a colleague.
    assert inbox.inbox_count(department_head, TODAY) == 0
    assert inbox.inbox_count(other_specialist, TODAY) == 0
    assert inbox.inbox_count(specialist, TODAY) == 1


def test_the_seven_and_three_day_notices_are_generated_independently(specialist):
    deadline = TODAY + timedelta(days=7)
    _opinion(specialist, deadline)

    _run(TODAY)
    _run(TODAY + timedelta(days=4))

    seven, three = _rows()
    assert (seven.offset_days, three.offset_days) == (7, 3)
    # The later interval supersedes the earlier one; history is kept.
    assert seven.retired_reason == RetiredReason.SUPERSEDED
    assert three.retired_at is None
    assert [item.pk for item in _live(specialist, TODAY + timedelta(days=4))] == [three.pk]


def test_hiding_the_seven_day_notice_suppresses_neither_the_three_day_nor_the_day(specialist):
    deadline = TODAY + timedelta(days=7)
    _opinion(specialist, deadline)
    _run(TODAY)
    (seven,) = _rows()
    deadlines.dismiss(notification=seven)

    _run(TODAY + timedelta(days=4))
    _run(deadline)

    offsets = [(row.purpose, row.offset_days) for row in _rows()]
    assert offsets == [
        (NotificationPurpose.ADVANCE, 7),
        (NotificationPurpose.ADVANCE, 3),
        (NotificationPurpose.ON_DAY, 0),
    ]
    day_row = _rows(purpose=NotificationPurpose.ON_DAY)[0]
    assert day_row.kind == NotificationKind.ACTION
    assert [item.pk for item in _live(specialist, deadline)] == [day_row.pk]


def test_a_hidden_row_does_not_come_back_after_reruns(specialist):
    _opinion(specialist, TODAY + timedelta(days=5))
    _run(TODAY)
    (row,) = _rows()
    deadlines.dismiss(notification=row)

    for _ in range(3):
        assert _run(TODAY).created == 0

    assert len(_rows()) == 1
    assert _live(specialist, TODAY) == []


def test_repeated_runs_create_nothing_twice(specialist, department_head):
    _feature_started(TODAY - timedelta(days=10))
    _opinion(specialist, TODAY + timedelta(days=3))
    _opinion(specialist, TODAY - timedelta(days=2))
    _step(factories.MatterFactory(owner=specialist), on=TODAY + timedelta(days=1))

    first = _run(TODAY)
    again = _run(TODAY)

    assert first.created > 0
    assert (again.created, again.retired) == (0, 0)
    assert not again.changed


def test_management_gets_one_escalation_the_morning_after_and_never_again(
    specialist, department_head
):
    deadline = TODAY
    matter = _opinion(specialist, deadline)
    _run(TODAY)
    assert _rows(recipient=department_head) == []

    _run(TODAY + timedelta(days=1))
    _run(TODAY + timedelta(days=2))
    _run(TODAY + timedelta(days=5))

    (escalation,) = _rows(recipient=department_head)
    assert (escalation.purpose, escalation.kind, escalation.matter) == (
        NotificationPurpose.ESCALATION,
        NotificationKind.INFO,
        matter,
    )
    assert escalation.due_on == deadline + timedelta(days=1)
    (item,) = _live(department_head, TODAY + timedelta(days=1))
    assert item.label == "Tähtaeg ületatud"
    assert specialist.display_name in item.note
    # The responsible lawyer has the day's own ACTION row, not an escalation.
    assert {row.purpose for row in _rows(recipient=specialist)} == {NotificationPurpose.ON_DAY}


def test_an_escalation_is_not_replayed_for_history(specialist, department_head):
    _opinion(specialist, TODAY - timedelta(days=8))
    _step(factories.MatterFactory(owner=specialist), on=TODAY - timedelta(days=9))

    _run(TODAY)

    assert _rows(recipient=department_head) == []


def test_an_answered_deadline_escalates_to_nobody(specialist, department_head):
    matter = _opinion(specialist, TODAY - timedelta(days=1))
    # An open Järgmiseks other than a WAIT is today's plan: operationally not overdue.
    _step(matter, on=TODAY + timedelta(days=10))

    _run(TODAY)

    assert [r for r in _rows(recipient=department_head) if r.category == OPINION] == []


def test_a_step_deadline_escalates_and_a_review_never_does(specialist, department_head):
    _feature_started(TODAY - timedelta(days=10))
    late = _step(factories.MatterFactory(owner=specialist), on=TODAY - timedelta(days=1))
    _step(
        factories.MatterFactory(owner=specialist),
        on=TODAY - timedelta(days=1),
        kind=ActionKind.WAIT,
        date_semantics=DateSemantics.REVIEW_ON,
    )

    _run(TODAY)

    (row,) = _rows(recipient=department_head)
    assert (row.category, row.source_id) == (ACTION, late.pk)


def test_management_may_turn_the_escalation_off(specialist, department_head):
    _opinion(specialist, TODAY - timedelta(days=1))
    choices = preferences.choices_for(department_head).preferences
    choices[ESCALATION_KEY] = preferences.Preference(enabled=False, offsets=(), on_day=False)
    preferences.save_choices(department_head, choices, paused=False)

    _run(TODAY)

    assert _rows(recipient=department_head) == []


def test_a_responsible_department_head_gets_no_escalation_of_their_own_deadline(department_head):
    _feature_started(TODAY - timedelta(days=10))
    _opinion(department_head, TODAY - timedelta(days=1))

    _run(TODAY)

    assert {row.purpose for row in _rows(recipient=department_head)} == {NotificationPurpose.ON_DAY}


# ---------------------------------------------------------------------------
# Retirement
# ---------------------------------------------------------------------------


def test_a_sent_opinion_retires_the_notice(specialist, organisation):
    from tests.follow_ups import mark_sent

    matter = _opinion(specialist, TODAY + timedelta(days=3))
    _run(TODAY)
    assert len(_live(specialist, TODAY)) == 1

    mark_sent(matter, specialist, [organisation])

    # Out of the list at once, before the worker has run.
    assert _live(specialist, TODAY) == []
    _run(TODAY)
    (row,) = _rows()
    assert row.retired_reason == RetiredReason.DISCHARGED


def test_a_completed_step_retires_its_notice(specialist):
    action = _step(factories.MatterFactory(owner=specialist), on=TODAY + timedelta(days=1))
    _run(TODAY)

    complete_next_action(action=action, actor=specialist)

    assert _live(specialist, TODAY) == []
    _run(TODAY)
    assert _rows()[0].retired_reason == RetiredReason.DISCHARGED


def test_a_moved_deadline_retires_the_old_rows_and_starts_a_new_schedule(specialist):
    matter = _opinion(specialist, TODAY + timedelta(days=3))
    _run(TODAY)
    Matter.objects.filter(pk=matter.pk).update(response_deadline=TODAY + timedelta(days=6))

    _run(TODAY)

    old, new = _rows()
    assert (old.source_date, old.retired_reason) == (
        TODAY + timedelta(days=3),
        RetiredReason.DATE_CHANGED,
    )
    assert (new.source_date, new.offset_days, new.retired_at) == (
        TODAY + timedelta(days=6),
        7,
        None,
    )


def test_reassignment_moves_eligibility_without_replaying_old_intervals(
    specialist, other_specialist
):
    deadline = TODAY + timedelta(days=7)
    matter = _opinion(specialist, deadline)
    _run(TODAY)
    (old_row,) = _rows()
    deadlines.dismiss(notification=old_row)

    later = TODAY + timedelta(days=5)  # two days to go: the 3-day interval is current
    assign_matter(matter=matter, owner=other_specialist, actor=specialist)
    _run(later)

    old_row.refresh_from_db()
    assert old_row.retired_reason == RetiredReason.REASSIGNED
    (new_row,) = _rows(recipient=other_specialist)
    # Only the interval that is current — never the 7-day one the old owner had.
    assert (new_row.offset_days, new_row.dismissed_at) == (3, None)
    assert [item.pk for item in _live(other_specialist, later)] == [new_row.pk]
    assert _live(specialist, later) == []


def test_an_unowned_or_inactive_owner_gets_no_personal_row(specialist):
    _opinion(None, TODAY + timedelta(days=3))
    gone = factories.UserFactory(is_active=False)
    _opinion(gone, TODAY + timedelta(days=3))

    _run(TODAY)

    assert _rows() == []


def test_a_step_notifies_its_own_responsible_person_and_falls_back_to_the_owner(
    specialist, other_specialist
):
    """Owner decision D2."""
    named = _step(
        factories.MatterFactory(owner=specialist),
        on=TODAY + timedelta(days=1),
        responsible=other_specialist,
    )
    unnamed = _step(factories.MatterFactory(owner=specialist), on=TODAY + timedelta(days=1))
    left = factories.UserFactory(is_active=False)
    orphaned = _step(
        factories.MatterFactory(owner=specialist), on=TODAY + timedelta(days=1), responsible=left
    )

    _run(TODAY)

    assert {(row.recipient_id, row.source_id) for row in _rows()} == {
        (other_specialist.pk, named.pk),
        (specialist.pk, unnamed.pk),
        (specialist.pk, orphaned.pk),
    }


def test_approximate_dates_never_produce_a_row(specialist):
    month = TODAY.replace(day=1) + timedelta(days=31)
    _step(
        factories.MatterFactory(owner=specialist),
        on=month.replace(day=1),
        date_precision=DatePrecision.MONTH,
    )
    _step(
        factories.MatterFactory(owner=specialist),
        on=TODAY + timedelta(days=1),
        date_precision=DatePrecision.QUARTER,
    )

    _run(TODAY)
    _run(month.replace(day=1))

    assert _rows() == []


def test_a_planned_step_is_not_a_deadline_yet(specialist):
    matter = factories.MatterFactory(owner=specialist)
    _step(matter, on=TODAY + timedelta(days=3))
    _step(matter, on=TODAY + timedelta(days=1), status=ActionStatus.PLANNED)

    _run(TODAY)

    assert [row.source_date for row in _rows()] == [TODAY + timedelta(days=3)]


# ---------------------------------------------------------------------------
# Preferences
# ---------------------------------------------------------------------------


def _set(user, key, **changes):
    current = preferences.choices_for(user)
    chosen = dict(current.preferences)
    base = chosen[key]
    chosen[key] = preferences.Preference(
        enabled=changes.get("enabled", base.enabled),
        offsets=changes.get("offsets", base.offsets),
        on_day=changes.get("on_day", base.on_day),
    )
    preferences.save_choices(user, chosen, paused=changes.get("paused", current.paused))


def test_a_disabled_category_generates_nothing_and_hides_its_info_rows(specialist):
    _opinion(specialist, TODAY + timedelta(days=3))
    _opinion(specialist, TODAY)
    _run(TODAY)
    assert len(_live(specialist, TODAY)) == 2

    _set(specialist, OPINION, enabled=False)

    kinds = [item.kind for item in _live(specialist, TODAY)]
    # The day's ACTION row is work that is still true; the INFO row is hidden.
    assert kinds == [NotificationKind.ACTION]
    _opinion(specialist, TODAY + timedelta(days=1))
    assert _run(TODAY).created == 0
    # Nothing was retired by switching off.
    assert not DeadlineNotification.objects.filter(retired_at__isnull=False).exists()

    _set(specialist, OPINION, enabled=True)
    assert len(_live(specialist, TODAY)) == 2


def test_pause_generates_nothing_hides_everything_and_deletes_no_business_fact(specialist):
    matter = _opinion(specialist, TODAY + timedelta(days=3))
    _run(TODAY)
    _set(specialist, OPINION, paused=True)

    assert _live(specialist, TODAY) == []
    _opinion(specialist, TODAY + timedelta(days=2))
    assert _run(TODAY).created == 0
    matter.refresh_from_db()
    assert matter.response_deadline == TODAY + timedelta(days=3)
    assert DeadlineNotification.objects.count() == 1

    _set(specialist, OPINION, paused=False)
    assert len(_live(specialist, TODAY)) == 1


def test_pause_leaves_commencement_checks_and_uus_asi(specialist, other_specialist):
    matter = factories.MatterFactory(owner=None, title="Pausi ajal määratud")
    assign_matter(matter=matter, owner=specialist, actor=other_specialist)
    factories.EffectiveDateFactory(
        matter=factories.MatterFactory(owner=specialist), date_value=TODAY, period_end=TODAY
    )
    reconcile_commencement_reminders(TODAY)
    _set(specialist, OPINION, paused=True)

    types = sorted(item.type for item in inbox.inbox_for(specialist, TODAY))

    assert types == [inbox.TYPE_ASSIGNMENT, inbox.TYPE_COMMENCEMENT]


def test_custom_offsets_and_the_day_switch(specialist):
    deadline = TODAY + timedelta(days=14)
    _opinion(specialist, deadline)
    _set(specialist, OPINION, offsets=(14, 2), on_day=False)

    _run(TODAY)
    _run(deadline - timedelta(days=1))
    _run(deadline)

    assert [(row.purpose, row.offset_days) for row in _rows()] == [
        (NotificationPurpose.ADVANCE, 14),
        (NotificationPurpose.ADVANCE, 2),
    ]


def test_only_explicit_choices_are_stored_and_reset_forgets_them(specialist):
    choices = preferences.choices_for(specialist)
    preferences.save_choices(specialist, dict(choices.preferences), paused=False)
    assert not NotificationPreference.objects.filter(user=specialist).exists()
    assert not NotificationSettings.objects.filter(user=specialist).exists()

    _set(specialist, NotificationCategory.IMPORTANT_DATE, enabled=True)
    assert NotificationPreference.objects.filter(user=specialist).count() == 1

    preferences.reset_choices(specialist)
    assert not NotificationPreference.objects.filter(user=specialist).exists()


def test_the_defaults_are_the_agreed_ones():
    defaults = {key: preferences.default_preference(key) for key in CATEGORIES}
    assert defaults[OPINION] == preferences.Preference(True, (7, 3, 1), True)
    assert defaults[NotificationCategory.FEEDBACK_DEADLINE] == preferences.Preference(
        True, (3,), True
    )
    assert defaults[ACTION] == preferences.Preference(True, (3, 1), True)
    assert defaults[NotificationCategory.COMMENCEMENT_CHECK] == preferences.Preference(
        True, (7,), False
    )
    assert not defaults[NotificationCategory.IMPORTANT_DATE].enabled
    assert not defaults[NotificationCategory.REVIEW_DATE].enabled


# ---------------------------------------------------------------------------
# The other categories
# ---------------------------------------------------------------------------


def test_a_feedback_round_is_told_three_days_ahead_and_on_the_day(specialist):
    matter = factories.MatterFactory(owner=specialist)
    MatterEngagement.objects.create(
        matter=matter,
        kind=EngagementKind.EMAIL_CAMPAIGN,
        title="Liikmete küsitlus",
        created_by=specialist,
        lifecycle_tracked=True,
        feedback_deadline=TODAY + timedelta(days=3),
    )

    _run(TODAY)
    _run(TODAY + timedelta(days=3))

    assert [(row.category, row.purpose, row.offset_days) for row in _rows()] == [
        (NotificationCategory.FEEDBACK_DEADLINE, NotificationPurpose.ADVANCE, 3),
        (NotificationCategory.FEEDBACK_DEADLINE, NotificationPurpose.ON_DAY, 0),
    ]


def test_an_important_date_is_off_by_default_and_informational_kinds_never_notify(specialist):
    from app.intelligence.enums import ImportantDateKind

    matter = factories.MatterFactory(owner=specialist)
    factories.ImportantDateFactory(
        matter=matter, date_value=TODAY + timedelta(days=1), period_end=TODAY + timedelta(days=1)
    )
    factories.ImportantDateFactory(
        matter=matter,
        kind=ImportantDateKind.PLANNED_COMMENCEMENT,
        date_value=TODAY + timedelta(days=1),
        period_end=TODAY + timedelta(days=1),
    )
    _run(TODAY)
    assert _rows() == []

    _set(specialist, NotificationCategory.IMPORTANT_DATE, enabled=True)
    _run(TODAY)

    (row,) = _rows()
    assert (row.category, row.offset_days) == (NotificationCategory.IMPORTANT_DATE, 1)


def test_a_review_date_is_information_never_late_and_never_escalated(specialist, department_head):
    _set(specialist, NotificationCategory.REVIEW_DATE, enabled=True)
    _step(
        factories.MatterFactory(owner=specialist),
        on=TODAY,
        kind=ActionKind.MONITOR,
        date_semantics=DateSemantics.REVIEW_ON,
    )

    _run(TODAY)
    _run(TODAY + timedelta(days=2))

    (row,) = _rows()
    assert (row.kind, row.purpose) == (NotificationKind.INFO, NotificationPurpose.ON_DAY)
    (item,) = _live(specialist, TODAY + timedelta(days=2))
    assert item.label == "On aeg üle vaadata"
    assert item.can_hide
    for word in ("hilinenud", "ületatud", "möödas"):
        assert word not in f"{item.label} {item.date_label}".lower()


def test_a_commencement_is_announced_ahead_and_checked_on_the_day(specialist):
    """Owner decision D1: no seven-day `CommencementReminder` is generated any more."""
    day = TODAY + timedelta(days=7)
    record = factories.EffectiveDateFactory(
        matter=factories.MatterFactory(owner=specialist), date_value=day, period_end=day
    )

    _run(TODAY)
    reconcile_commencement_reminders(TODAY)

    (row,) = _rows()
    assert (row.category, row.offset_days, row.kind) == (
        NotificationCategory.COMMENCEMENT_CHECK,
        7,
        NotificationKind.INFO,
    )
    assert not CommencementReminder.objects.exists()
    (item,) = _live(specialist, TODAY)
    assert "jõustus" not in f"{item.label} {item.date_label}"
    assert item.url.endswith("#joustumine")

    _run(day)
    reconcile_commencement_reminders(day)

    # On the day: the check is the «Vajab tegevust» item, and no deadline row.
    assert [r.purpose for r in _rows()] == [NotificationPurpose.ADVANCE]
    (check,) = CommencementReminder.objects.filter(effective_date=record)
    assert check.stage == ReminderStage.ON_DATE
    types = [(i.type, i.kind) for i in inbox.inbox_for(specialist, day)]
    assert types == [(inbox.TYPE_COMMENCEMENT, NotificationKind.ACTION)]


def test_a_legacy_seven_day_check_keeps_working_and_is_superseded_on_the_day(specialist):
    day = TODAY + timedelta(days=3)
    record = factories.EffectiveDateFactory(
        matter=factories.MatterFactory(owner=specialist), date_value=day, period_end=day
    )
    legacy = CommencementReminder.objects.create(
        effective_date=record,
        source_date=day,
        stage=ReminderStage.SEVEN_DAYS,
        due_on=day - timedelta(days=7),
    )

    reconcile_commencement_reminders(TODAY)
    _run(TODAY)
    legacy.refresh_from_db()
    assert legacy.status == ReminderStatus.OPEN
    # The legacy row is the advance prompt for that date: no second one beside it.
    assert _rows() == []
    assert inbox.inbox_count(specialist, TODAY) == 1

    reconcile_commencement_reminders(day)
    legacy.refresh_from_db()
    assert legacy.status == ReminderStatus.SUPERSEDED


# ---------------------------------------------------------------------------
# One inbox: the bell, the block and the list agree
# ---------------------------------------------------------------------------


def _world(specialist, other_specialist):
    _opinion(specialist, TODAY + timedelta(days=3), title="Läheneva tähtajaga teema")
    _opinion(specialist, TODAY, title="Tänase tähtajaga teema")
    factories.EffectiveDateFactory(
        matter=factories.MatterFactory(owner=specialist, title="Jõustuv teema"),
        date_value=TODAY,
        period_end=TODAY,
    )
    assign_matter(
        matter=factories.MatterFactory(owner=None, title="Saabunud teema"),
        owner=specialist,
        actor=other_specialist,
    )
    deadlines.reconcile_deadline_notifications()
    reconcile_commencement_reminders()


@pytest.fixture
def now_is_today(monkeypatch):
    """Pin the Tallinn date to TODAY for the pages, which ask the clock."""
    moment = datetime(2026, 11, 2, 9, 0, tzinfo=UTC)
    monkeypatch.setattr("django.utils.timezone.now", lambda: moment)
    return TODAY


def _count_items(html: str) -> int:
    return html.count("data-inbox-item")


def test_the_bell_the_block_and_the_list_count_the_same_items(
    client, specialist, other_specialist, now_is_today
):
    _world(specialist, other_specialist)
    client.force_login(specialist)

    bell = client.get(reverse("notifications:bell"))
    block = client.get(reverse("matters:my_work")).content.decode()
    listing = client.get(reverse("notifications:list")).content.decode()

    expected = len(inbox.inbox_for(specialist))
    assert expected == 4
    assert bell.context["count"] == expected == inbox.inbox_count(specialist)
    assert _count_items(block) == expected
    assert _count_items(listing) == expected
    assert 'aria-label="Teavitused"' in block
    for heading in ("Vajab tegevust", "Teadmiseks"):
        assert heading in listing


def test_counts_are_scoped_to_the_account(client, specialist, other_specialist, now_is_today):
    _world(specialist, other_specialist)
    client.force_login(other_specialist)

    assert client.get(reverse("notifications:bell")).context["count"] == 0
    assert _count_items(client.get(reverse("notifications:list")).content.decode()) == 0


def test_the_order_is_by_date_with_action_before_information(specialist, other_specialist):
    _opinion(specialist, TODAY + timedelta(days=3), title="B")
    _opinion(specialist, TODAY, title="A")
    _run(TODAY)
    _set(specialist, NotificationCategory.REVIEW_DATE, enabled=True)
    _step(
        factories.MatterFactory(owner=specialist, title="C"),
        on=TODAY,
        kind=ActionKind.WAIT,
        date_semantics=DateSemantics.REVIEW_ON,
    )
    _run(TODAY)

    items = _live(specialist, TODAY)

    assert [(item.title, item.kind) for item in items] == [
        ("A", NotificationKind.ACTION),
        ("C", NotificationKind.INFO),
        ("B", NotificationKind.INFO),
    ]


def test_reading_and_hiding_are_different_and_neither_completes_work(
    client, specialist, now_is_today
):
    matter = _opinion(specialist, TODAY + timedelta(days=3), title="Loetav teema")
    deadlines.reconcile_deadline_notifications()
    (row,) = _rows()
    client.force_login(specialist)

    # Opening the Teema is a GET: it marks nothing.
    assert client.get(reverse("matters:matter_detail", kwargs={"pk": matter.pk})).status_code == 200
    row.refresh_from_db()
    assert row.read_at is None
    listing = client.get(reverse("notifications:list")).content.decode()
    assert "notifrow--unread" in listing

    response = client.post(
        reverse("notifications:read", kwargs={"notification_id": row.pk}), {"tagasi": "teavitused"}
    )
    assert response.status_code == 302
    row.refresh_from_db()
    assert row.read_at is not None and row.dismissed_at is None
    listing = client.get(reverse("notifications:list")).content.decode()
    assert "Loetav teema" in listing
    assert "notifrow--unread" not in listing

    response = client.post(
        reverse("notifications:hide", kwargs={"notification_id": row.pk}), {"tagasi": "minu_asjad"}
    )
    assert response["Location"] == reverse("matters:my_work") + "#teavitused"
    row.refresh_from_db()
    assert row.dismissed_at is not None
    assert "Loetav teema" not in client.get(reverse("notifications:list")).content.decode()
    matter.refresh_from_db()
    assert matter.response_deadline == TODAY + timedelta(days=3)


def test_an_action_row_cannot_be_hidden(client, specialist, now_is_today):
    _opinion(specialist, TODAY)
    deadlines.reconcile_deadline_notifications()
    (row,) = _rows()
    assert row.kind == NotificationKind.ACTION
    client.force_login(specialist)

    response = client.post(reverse("notifications:hide", kwargs={"notification_id": row.pk}))

    assert response.status_code == 404
    row.refresh_from_db()
    assert row.dismissed_at is None


def test_reading_or_hiding_writes_no_change_event(client, specialist, now_is_today):
    from app.audit.models import ChangeEvent

    _opinion(specialist, TODAY + timedelta(days=3))
    deadlines.reconcile_deadline_notifications()
    (row,) = _rows()
    before = ChangeEvent.objects.count()
    client.force_login(specialist)

    client.post(reverse("notifications:read", kwargs={"notification_id": row.pk}))
    client.post(reverse("notifications:hide", kwargs={"notification_id": row.pk}))

    assert ChangeEvent.objects.count() == before


def test_a_colleagues_desk_carries_none_of_their_notifications(
    client, specialist, department_head, now_is_today
):
    _opinion(specialist, TODAY + timedelta(days=3), title="Kolleegi teavitus")
    deadlines.reconcile_deadline_notifications()
    (row,) = _rows()
    client.force_login(department_head)

    page = client.get(reverse("matters:person_work", kwargs={"pk": specialist.pk}))
    html = page.content.decode()
    assert page.status_code == 200
    assert 'aria-label="Teavitused"' not in html
    assert "data-inbox-item" not in html
    assert reverse("notifications:read", kwargs={"notification_id": row.pk}) not in html

    for name in ("notifications:read", "notifications:hide"):
        response = client.post(reverse(name, kwargs={"notification_id": row.pk}))
        assert response.status_code == 404
    row.refresh_from_db()
    assert (row.read_at, row.dismissed_at) == (None, None)


def test_an_administrator_without_a_business_role_sees_nothing(client, administrator, now_is_today):
    matter = factories.MatterFactory(
        owner=administrator, response_deadline=TODAY, visibility=Visibility.RESTRICTED
    )
    DeadlineNotification.objects.create(
        recipient=administrator,
        matter=matter,
        category=OPINION,
        source_type="MATTER",
        source_id=matter.pk,
        source_date=TODAY,
        purpose=NotificationPurpose.ON_DAY,
        kind=NotificationKind.ACTION,
        due_on=TODAY,
    )
    client.force_login(administrator)

    assert inbox.inbox_count(administrator) == 0
    assert inbox.inbox_for(administrator) == []
    listing = client.get(reverse("notifications:list")).content.decode()
    assert matter.title not in listing
    assert _count_items(listing) == 0


def test_a_restricted_matter_reaches_no_reader_who_may_not_see_it(specialist, now_is_today):
    """The read path re-applies `visible_to`: a row on a Matter the reader cannot
    read contributes no count and no title — here, a tombstoned Teema."""
    matter = _opinion(specialist, TODAY + timedelta(days=3), visibility=Visibility.RESTRICTED)
    deadlines.reconcile_deadline_notifications()
    assert inbox.inbox_count(specialist) == 1

    Matter.all_objects.filter(pk=matter.pk).update(deleted_at=datetime(2026, 11, 2, tzinfo=UTC))

    assert inbox.inbox_count(specialist) == 0
    assert inbox.inbox_for(specialist) == []


def test_another_persons_row_is_a_404(client, specialist, other_specialist, now_is_today):
    _opinion(specialist, TODAY + timedelta(days=3))
    deadlines.reconcile_deadline_notifications()
    (row,) = _rows()
    client.force_login(other_specialist)

    assert (
        client.post(reverse("notifications:read", kwargs={"notification_id": row.pk})).status_code
        == 404
    )


# ---------------------------------------------------------------------------
# E-mail: a stored wish, never sent
# ---------------------------------------------------------------------------


def test_nothing_is_ever_emailed_under_the_shared_gate(settings, specialist):
    apply_shared_gate(settings)
    NotificationSettings.objects.create(user=specialist, email_requested=True)
    _opinion(specialist, TODAY + timedelta(days=3))

    _run(TODAY)
    gate = Client()
    gate.post(reverse("accounts:shared_gate"), {"password": PASSWORD})
    gate.force_login(specialist)
    gate.get(reverse("notifications:list"))

    assert mail.outbox == []
    assert not email_delivery_allowed()
    assert deliver_email(specialist, _rows()[0]) is False


def test_email_stays_refused_until_both_switches_are_on(settings, specialist):
    settings.AUTH_MODE = "local_password"
    assert not email_delivery_allowed()
    settings.NOTIFICATION_EMAIL_ENABLED = True
    assert email_delivery_allowed()
    assert deliver_email(specialist, None) is False
    assert mail.outbox == []


# ---------------------------------------------------------------------------
# The settings page
# ---------------------------------------------------------------------------


def test_the_settings_page_saves_only_what_differs_and_restores_defaults(client, specialist):
    client.force_login(specialist)
    page = client.get(reverse("notifications:settings")).content.decode()
    assert "Arvamuse tähtaeg" in page
    assert "E-posti teavitused lisanduvad isikupõhise sisselogimisega." in page
    assert re.search(r'id="notif-email"[^>]*disabled', page)
    assert ESCALATION_KEY not in page  # not a management holder

    form = {}
    for key, category in CATEGORIES.items():
        default = preferences.default_preference(key)
        if default.enabled:
            form[f"{key}-enabled"] = "1"
        form[f"{key}-offsets"] = [str(offset) for offset in default.offsets]
        if default.on_day:
            form[f"{key}-on_day"] = "1"
        assert category.label
    form[f"{OPINION}-offsets"] = ["14", "1", "99"]  # 99 is not allowed and is dropped
    response = client.post(reverse("notifications:settings"), form)

    assert response.status_code == 302
    (stored,) = NotificationPreference.objects.filter(user=specialist)
    assert (stored.category, stored.offsets) == (OPINION, [14, 1])

    client.post(reverse("notifications:reset_settings"))
    assert not NotificationPreference.objects.filter(user=specialist).exists()


def test_a_reader_may_keep_their_own_settings(client, reader):
    client.force_login(reader)
    response = client.post(reverse("notifications:settings"), {"paused": "1"})
    assert response.status_code == 302
    assert NotificationSettings.objects.get(user=reader).paused


def test_the_escalation_switch_is_offered_to_management_only(client, department_head):
    client.force_login(department_head)
    page = client.get(reverse("notifications:settings")).content.decode()
    assert f'name="{ESCALATION_KEY}-enabled"' in page


def test_the_list_empty_state_points_to_the_settings(client, specialist):
    client.force_login(specialist)
    page = client.get(reverse("notifications:list")).content.decode()
    assert "Praegu ei ole ühtegi teavitust." in page
    assert reverse("notifications:settings") in page


# ---------------------------------------------------------------------------
# The calendar
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("utc_moment", "tallinn_day"),
    [
        # Last Sunday of October: 00:30 Tallinn (UTC+3) is still 24 October in UTC.
        (datetime(2026, 10, 24, 21, 30, tzinfo=UTC), date(2026, 10, 25)),
        # Last Sunday of March: 00:30 Tallinn (UTC+2) is still 27 March in UTC.
        (datetime(2027, 3, 27, 22, 30, tzinfo=UTC), date(2027, 3, 28)),
    ],
)
def test_today_is_the_tallinn_date_across_daylight_saving(
    monkeypatch, specialist, utc_moment, tallinn_day
):
    monkeypatch.setattr("django.utils.timezone.now", lambda: utc_moment)
    # Seven Tallinn days ahead; eight from the UTC date, which would owe nothing.
    _opinion(specialist, tallinn_day + timedelta(days=7))

    result = deadlines.reconcile_deadline_notifications()

    assert result.today == tallinn_day
    (row,) = _rows()
    assert (row.offset_days, row.due_on) == (7, tallinn_day)


# ---------------------------------------------------------------------------
# The worker
# ---------------------------------------------------------------------------


def test_the_worker_command_runs_both_reconciles_and_check_reports_owed_work(capsys, specialist):
    _opinion(specialist, TODAY + timedelta(days=3))

    with pytest.raises(CommandError):
        call_command("generate_commencement_reminders", "--check", "--today", TODAY.isoformat())
    assert not DeadlineNotification.objects.exists()

    call_command("generate_commencement_reminders", "--today", TODAY.isoformat())
    out = capsys.readouterr().out
    assert "Jõustumise meeldetuletused 2026-11-02" in out
    assert "Tähtaegade teavitused 2026-11-02: Loodud 1" in out

    call_command("generate_commencement_reminders", "--check", "--today", TODAY.isoformat())


def test_the_generator_never_touches_business_rows(specialist):
    from app.audit.models import ChangeEvent

    matter = _opinion(specialist, TODAY + timedelta(days=1))
    action = _step(factories.MatterFactory(owner=specialist), on=TODAY + timedelta(days=1))
    events = ChangeEvent.objects.count()

    _run(TODAY)
    _run(TODAY + timedelta(days=5))

    matter.refresh_from_db()
    action.refresh_from_db()
    assert matter.response_deadline == TODAY + timedelta(days=1)
    assert action.status == ActionStatus.OPEN
    assert ChangeEvent.objects.count() == events


def test_the_migration_is_additive_and_leaves_commencement_history_alone():
    """`notifications/0002` creates three tables and alters nothing that exists, so
    every `CommencementReminder` row — and its vocabulary — survives it as it was."""
    import importlib

    from django.db import migrations

    module = importlib.import_module(
        "app.notifications.migrations.0002_deadline_notifications_and_preferences"
    )
    operations = module.Migration.operations
    assert operations
    assert all(isinstance(op, migrations.CreateModel) for op in operations)
    assert {op.name for op in operations} == {
        "DeadlineNotification",
        "NotificationPreference",
        "NotificationSettings",
    }
    assert set(ReminderStage.values) == {"SEVEN_DAYS", "ON_DATE"}


# ---------------------------------------------------------------------------
# Review round of 2026-10-30
# ---------------------------------------------------------------------------


def test_handed_away_and_back_the_same_day_brings_the_current_row_back(
    specialist, other_specialist
):
    """P2-1: the retired row holds the slot, so it is revived rather than lost."""
    matter = _opinion(specialist, TODAY + timedelta(days=3))
    _run(TODAY)
    (row,) = _rows()
    deadlines.mark_read(notification=row)

    assign_matter(matter=matter, owner=other_specialist, actor=specialist)
    _run(TODAY)
    row.refresh_from_db()
    assert row.retired_reason == RetiredReason.REASSIGNED
    assign_matter(matter=matter, owner=specialist, actor=other_specialist)
    result = _run(TODAY)

    row.refresh_from_db()
    assert (row.retired_at, row.retired_reason, row.read_at) == (None, "", None)
    assert result.revived == 1
    assert [item.pk for item in _live(specialist, TODAY)] == [row.pk]
    assert len(_rows(recipient=specialist)) == 1


def test_a_date_moved_away_and_back_the_same_day_is_owed_again(specialist):
    matter = _opinion(specialist, TODAY + timedelta(days=3))
    _run(TODAY)
    Matter.objects.filter(pk=matter.pk).update(response_deadline=TODAY + timedelta(days=30))
    _run(TODAY)
    Matter.objects.filter(pk=matter.pk).update(response_deadline=TODAY + timedelta(days=3))

    _run(TODAY)

    (row,) = _rows()
    assert row.retired_at is None
    assert len(_live(specialist, TODAY)) == 1


def test_a_hidden_row_stays_hidden_after_a_hand_over_back(specialist, other_specialist):
    matter = _opinion(specialist, TODAY + timedelta(days=3))
    _run(TODAY)
    (row,) = _rows()
    deadlines.dismiss(notification=row)
    assign_matter(matter=matter, owner=other_specialist, actor=specialist)
    _run(TODAY)
    assign_matter(matter=matter, owner=specialist, actor=other_specialist)

    _run(TODAY)

    row.refresh_from_db()
    assert row.dismissed_at is not None
    assert _live(specialist, TODAY) == []


def test_an_expired_interval_is_never_revived(specialist, other_specialist):
    """Only today's interval comes back: the 7-day row of the old owner stays retired."""
    matter = _opinion(specialist, TODAY + timedelta(days=7))
    _run(TODAY)
    assign_matter(matter=matter, owner=other_specialist, actor=specialist)
    later = TODAY + timedelta(days=5)
    _run(later)
    assign_matter(matter=matter, owner=specialist, actor=other_specialist)

    _run(later)

    rows = {(row.offset_days, row.retired_reason) for row in _rows(recipient=specialist)}
    assert rows == {(7, RetiredReason.REASSIGNED), (3, "")}


def test_the_escalation_names_the_steps_own_responsible_person(
    specialist, other_specialist, department_head
):
    """P2-2: owner decision D2 decides who is named, not the Matter's owner."""
    _feature_started(TODAY - timedelta(days=10))
    _step(
        factories.MatterFactory(owner=specialist, title="Kolleegi samm"),
        on=TODAY - timedelta(days=1),
        responsible=other_specialist,
    )
    _step(
        factories.MatterFactory(owner=None, title="Vastutajata teema samm"),
        on=TODAY - timedelta(days=1),
        responsible=specialist,
    )
    _run(TODAY)

    notes = {item.title: item.note for item in _live(department_head, TODAY)}

    assert notes == {
        "Kolleegi samm": f"Vastutaja {other_specialist.display_name}",
        "Vastutajata teema samm": f"Vastutaja {specialist.display_name}",
    }


def test_the_inbox_names_responsible_people_in_a_bounded_number_of_queries(
    specialist, department_head, django_assert_max_num_queries
):
    _feature_started(TODAY - timedelta(days=10))
    for index in range(4):
        _step(
            factories.MatterFactory(owner=specialist),
            on=TODAY - timedelta(days=1),
            responsible=factories.UserFactory(display_name=f"Sammu vastutaja {index}"),
        )
    _run(TODAY)
    inbox.inbox_for(department_head, TODAY)  # warm the per-request caches

    with django_assert_max_num_queries(20):
        items = inbox.inbox_for(department_head, TODAY)

    assert len(items) == 4


def test_the_result_counts_what_was_written(specialist):
    """P3-2: counted from the inserts and updates, not from the table size."""
    _feature_started(TODAY - timedelta(days=10))
    _opinion(specialist, TODAY + timedelta(days=3))
    _opinion(specialist, TODAY + timedelta(days=1))

    first = _run(TODAY)
    second = _run(TODAY)

    assert (first.created, first.revived) == (2, 0)
    assert (second.created, second.revived, second.retired) == (0, 0, 0)


def test_the_first_run_does_not_deliver_last_weeks_history(specialist, department_head):
    """P3-3: on an empty table, a deadline missed before the feature existed
    owes neither an on-the-day row nor an escalation."""
    _opinion(specialist, TODAY - timedelta(days=3))
    _step(factories.MatterFactory(owner=specialist), on=TODAY - timedelta(days=2))

    _run(TODAY)

    assert _personal() == []
    assert deadlines.feature_start(TODAY) == TODAY


def test_after_the_start_a_worker_down_for_two_days_still_delivers_once(
    specialist, department_head
):
    started = TODAY - timedelta(days=10)
    _feature_started(started)
    deadline = TODAY - timedelta(days=2)
    _opinion(specialist, deadline)

    _run(TODAY)
    _run(TODAY)

    assert deadlines.feature_start(TODAY) == started
    assert {(row.recipient_id, row.purpose) for row in _personal()} == {
        (specialist.pk, NotificationPurpose.ON_DAY),
        (department_head.pk, NotificationPurpose.ESCALATION),
    }


def test_a_removed_round_is_neither_shown_nor_counted(specialist):
    from django.utils import timezone as django_timezone

    matter = factories.MatterFactory(owner=specialist)
    engagement = MatterEngagement.objects.create(
        matter=matter,
        kind=EngagementKind.EMAIL_CAMPAIGN,
        title="Eemaldatav kaasamine",
        created_by=specialist,
        lifecycle_tracked=True,
        feedback_deadline=TODAY + timedelta(days=3),
    )
    _run(TODAY)
    assert inbox.inbox_count(specialist, TODAY) == 1

    MatterEngagement.objects.filter(pk=engagement.pk).update(removed_at=django_timezone.now())

    assert inbox.inbox_count(specialist, TODAY) == 0
    assert _live(specialist, TODAY) == []


def test_a_cancelled_step_is_neither_shown_nor_counted(specialist):
    action = _step(factories.MatterFactory(owner=specialist), on=TODAY + timedelta(days=1))
    _run(TODAY)
    assert inbox.inbox_count(specialist, TODAY) == 1

    type(action).objects.filter(pk=action.pk).update(status=ActionStatus.CANCELLED)

    assert inbox.inbox_count(specialist, TODAY) == 0
    _run(TODAY)
    assert _rows()[0].retired_reason == RetiredReason.DISCHARGED


@pytest.mark.parametrize("order", ["hide-then-run", "run-then-hide"])
def test_hiding_and_the_generator_leave_exactly_one_hidden_row(specialist, order):
    _opinion(specialist, TODAY + timedelta(days=3))
    _run(TODAY)
    (row,) = _rows()

    if order == "hide-then-run":
        deadlines.dismiss(notification=row)
        _run(TODAY)
    else:
        _run(TODAY)
        deadlines.dismiss(notification=row)
    _run(TODAY)

    (only,) = _rows()
    assert only.pk == row.pk
    assert only.dismissed_at is not None
    assert _live(specialist, TODAY) == []


# ---------------------------------------------------------------------------
# Owner decision of 2026-10-30: the day itself hides under a Järgmiseks
# ---------------------------------------------------------------------------


def test_the_opinion_day_row_hides_under_a_jargmiseks_and_returns_when_it_ends(specialist):
    """ADR 0050 precedence for «Vajab tegevust»; the advance notice is unaffected."""
    _feature_started(TODAY - timedelta(days=10))
    matter = _opinion(specialist, TODAY)
    _run(TODAY)
    (day_row,) = _personal(purpose=NotificationPurpose.ON_DAY)
    deadlines.mark_read(notification=day_row)
    step = _step(matter, on=TODAY + timedelta(days=20))

    # Hidden and uncounted at once, before the worker has run.
    assert _live(specialist, TODAY) == []
    assert inbox.inbox_count(specialist, TODAY) == 0
    _run(TODAY)
    day_row.refresh_from_db()
    assert day_row.retired_reason == RetiredReason.INELIGIBLE

    later = TODAY + timedelta(days=2)
    complete_next_action(action=step, actor=specialist)
    result = _run(later)

    day_row.refresh_from_db()
    assert result.revived == 1
    assert (day_row.retired_at, day_row.read_at) == (None, None)
    assert [item.pk for item in _live(specialist, later)] == [day_row.pk]


def test_no_day_row_is_generated_under_a_jargmiseks(specialist):
    _feature_started(TODAY - timedelta(days=10))
    matter = _opinion(specialist, TODAY)
    _step(matter, on=TODAY + timedelta(days=20))

    _run(TODAY)

    assert _personal() == []


def test_a_cancelled_jargmiseks_brings_the_day_row(specialist):
    _feature_started(TODAY - timedelta(days=10))
    matter = _opinion(specialist, TODAY - timedelta(days=1))
    step = _step(matter, on=TODAY + timedelta(days=20))
    _run(TODAY)
    assert _personal() == []

    type(step).objects.filter(pk=step.pk).update(status=ActionStatus.CANCELLED)
    _run(TODAY)

    (row,) = _personal(recipient=specialist)
    assert (row.purpose, row.kind) == (NotificationPurpose.ON_DAY, NotificationKind.ACTION)


def test_a_wait_does_not_hide_the_day_row(specialist):
    _feature_started(TODAY - timedelta(days=10))
    matter = _opinion(specialist, TODAY)
    _step(
        matter,
        on=TODAY + timedelta(days=20),
        kind=ActionKind.WAIT,
        date_semantics=DateSemantics.REVIEW_ON,
    )

    _run(TODAY)

    assert [row.purpose for row in _personal()] == [NotificationPurpose.ON_DAY]


def test_the_advance_opinion_notice_still_shows_under_a_jargmiseks(specialist):
    matter = _opinion(specialist, TODAY + timedelta(days=3))
    _step(matter, on=TODAY + timedelta(days=20))

    _run(TODAY)

    (row,) = _rows()
    assert (row.purpose, row.offset_days) == (NotificationPurpose.ADVANCE, 3)
    assert len(_live(specialist, TODAY)) == 1


# ---------------------------------------------------------------------------
# The worker also takes the daily Muudatused photograph (docs/adr/0159 §7)
# ---------------------------------------------------------------------------


def test_a_worker_turn_takes_the_daily_snapshot_once(monkeypatch):
    from app.notifications.management.commands import generate_commencement_reminders as worker
    from app.reporting.models import OperationalMatterSnapshot
    from app.reporting.selectors import snapshots

    owner = factories.UserFactory()
    factories.MatterFactory(owner=owner)
    # 04:00 Tallinn on 2 November 2026 (EET, UTC+2): past the 03:00 start.
    moment = datetime(2026, 11, 2, 2, 0, tzinfo=UTC)
    real = snapshots.capture_daily_if_missing
    monkeypatch.setattr(snapshots, "capture_daily_if_missing", lambda: real(moment))

    first = [result for result in worker._each_turn() if result.changed]
    second = [result for result in worker._each_turn() if result.changed]

    assert any(isinstance(result, worker._SnapshotTurn) for result in first)
    assert not any(isinstance(result, worker._SnapshotTurn) for result in second)
    assert OperationalMatterSnapshot.objects.filter(snapshot_date=date(2026, 11, 2)).exists()


def test_a_failed_snapshot_does_not_stop_the_reconciles(monkeypatch):
    from django.db import DatabaseError

    from app.notifications.management.commands import generate_commencement_reminders as worker
    from app.reporting.selectors import snapshots

    def broken() -> bool:
        raise DatabaseError("snapshot down")

    monkeypatch.setattr(snapshots, "capture_daily_if_missing", broken)

    results = worker._each_turn()

    assert len(results) == 2
    assert not any(isinstance(result, worker._SnapshotTurn) for result in results)
