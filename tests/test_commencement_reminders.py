"""«Kontrolli jõustumist» — commencement-review reminders (docs/adr/0188, brief C1–C6).

The generator is exercised with an explicit ``today`` wherever the Tallinn clock
is not itself the subject, so each case says which calendar day it is about. The
one test that is about the clock — the Tallinn/UTC midnight boundary — moves
`django.utils.timezone.now` instead.
"""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta

import pytest
from django.core.management import call_command
from django.core.management.base import CommandError
from django.test import Client
from django.urls import NoReverseMatch, reverse

from app.audit.enums import AuthAssurance, AuthMethod, ChangeEventType
from app.audit.models import ChangeEvent
from app.intelligence.enums import EffectiveDateKind, FactStatus, ImportantDateKind
from app.intelligence.services import cancel_effective_date, update_effective_date
from app.notifications import selectors, services
from app.notifications.enums import ObsoleteReason, ReminderStage, ReminderStatus
from app.notifications.models import CommencementReminder
from app.workflow.enums import DatePrecision
from tests import factories
from tests.gate import PASSWORD, apply_shared_gate
from tests.refusals import refused

pytestmark = pytest.mark.django_db

#: A Monday in November: Europe/Tallinn is UTC+2, so midnight is 22:00 UTC.
TODAY = date(2026, 11, 2)


def _commencement(
    *,
    owner=None,
    on: date | None = None,
    precision: str = DatePrecision.EXACT,
    kind: str = EffectiveDateKind.KNOWN_DATE,
    status: str = FactStatus.ACTIVE,
    description: str = "Põhiosa jõustub",
    matter=None,
):
    matter = matter or factories.MatterFactory(owner=owner)
    day = on if kind == EffectiveDateKind.KNOWN_DATE else None
    end = day
    if day is not None and precision == DatePrecision.MONTH:
        day = day.replace(day=1)
        end = (day.replace(day=28) + timedelta(days=4)).replace(day=1) - timedelta(days=1)
    return factories.EffectiveDateFactory(
        matter=matter,
        kind=kind,
        date_value=day,
        period_end=end,
        date_precision=precision,
        status=status,
        description=description,
    )


def _rows(record) -> list[CommencementReminder]:
    return list(CommencementReminder.objects.filter(effective_date=record).order_by("due_on"))


def _reconcile(day: date):
    return services.reconcile_commencement_reminders(day)


# ---------------------------------------------------------------------------
# The two stages
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("days_ahead", "expected"),
    [
        (8, []),
        (7, [ReminderStage.SEVEN_DAYS]),
        (3, [ReminderStage.SEVEN_DAYS]),
        (1, [ReminderStage.SEVEN_DAYS]),
    ],
)
def test_the_first_prompt_comes_seven_calendar_days_ahead(days_ahead, expected):
    owner = factories.UserFactory()
    record = _commencement(owner=owner, on=TODAY + timedelta(days=days_ahead))

    _reconcile(TODAY)

    rows = _rows(record)
    assert [row.stage for row in rows] == expected
    for row in rows:
        assert row.status == ReminderStatus.OPEN
        assert row.source_date == record.date_value
        assert row.due_on == record.date_value - timedelta(days=7)


def test_on_the_day_an_unanswered_check_is_asked_again_and_replaces_the_first():
    owner = factories.UserFactory()
    day = TODAY + timedelta(days=7)
    record = _commencement(owner=owner, on=day)
    _reconcile(TODAY)

    result = _reconcile(day)

    seven, on_date = _rows(record)
    assert (seven.stage, seven.status) == (ReminderStage.SEVEN_DAYS, ReminderStatus.SUPERSEDED)
    assert (on_date.stage, on_date.status) == (ReminderStage.ON_DATE, ReminderStatus.OPEN)
    assert on_date.due_on == day
    assert (result.created, result.superseded) == (1, 1)
    # One prompt on the desk, not two.
    assert [row.pk for row in selectors.open_reminders_for(owner, today=day)] == [on_date.pk]


def test_answering_from_a_page_drawn_before_the_day_answers_the_day():
    """A seven-day row replaced meanwhile still answers its date: the open sibling closes."""
    owner = factories.UserFactory()
    day = TODAY + timedelta(days=7)
    record = _commencement(owner=owner, on=day)
    _reconcile(TODAY)
    (stale,) = _rows(record)
    _reconcile(day)

    services.acknowledge_reminder(reminder=stale, actor=owner)

    assert {(row.stage, row.status) for row in _rows(record)} == {
        (ReminderStage.SEVEN_DAYS, ReminderStatus.SUPERSEDED),
        (ReminderStage.ON_DATE, ReminderStatus.ACKNOWLEDGED),
    }


def test_a_date_recorded_on_the_day_gets_only_the_on_the_day_prompt():
    record = _commencement(owner=factories.UserFactory(), on=TODAY)

    _reconcile(TODAY)

    assert [row.stage for row in _rows(record)] == [ReminderStage.ON_DATE]


def test_a_generator_that_missed_the_day_still_asks_but_history_does_not_flood():
    recent = _commencement(owner=factories.UserFactory(), on=TODAY - timedelta(days=7))
    historical = _commencement(owner=factories.UserFactory(), on=TODAY - timedelta(days=8))

    _reconcile(TODAY)

    assert [row.stage for row in _rows(recent)] == [ReminderStage.ON_DATE]
    assert _rows(historical) == []


@pytest.mark.parametrize("answer", ["acknowledge", "dismiss"])
def test_an_answered_check_is_not_asked_again_on_the_day(answer):
    owner = factories.UserFactory()
    day = TODAY + timedelta(days=5)
    record = _commencement(owner=owner, on=day)
    _reconcile(TODAY)
    (first,) = _rows(record)
    if answer == "acknowledge":
        services.acknowledge_reminder(reminder=first, actor=owner)
    else:
        services.dismiss_reminder(reminder=first, actor=owner, reason="Akt jäi vastu võtmata.")

    result = _reconcile(day)

    assert result.created == 0
    (only,) = _rows(record)
    assert only.status in (ReminderStatus.ACKNOWLEDGED, ReminderStatus.DISMISSED)
    assert only.resolved_by == owner
    assert selectors.open_reminders_for(owner, today=day) == []


def test_acknowledging_the_on_the_day_prompt_closes_it():
    owner = factories.UserFactory()
    record = _commencement(owner=owner, on=TODAY)
    _reconcile(TODAY)
    (row,) = _rows(record)

    services.acknowledge_reminder(reminder=row, actor=owner)

    row.refresh_from_db()
    assert row.status == ReminderStatus.ACKNOWLEDGED
    assert row.resolved_at is not None
    assert selectors.unresolved_count(owner, today=TODAY) == 0


def test_an_answer_twice_is_refused_and_recorded_once():
    owner = factories.UserFactory()
    record = _commencement(owner=owner, on=TODAY + timedelta(days=2))
    _reconcile(TODAY)
    (row,) = _rows(record)
    services.acknowledge_reminder(reminder=row, actor=owner)

    with refused(services.REMINDER_NOT_OPEN):
        services.acknowledge_reminder(reminder=row, actor=owner)

    assert (
        ChangeEvent.objects.filter(
            event_type=ChangeEventType.COMMENCEMENT_CHECK_ACKNOWLEDGED
        ).count()
        == 1
    )


def test_a_dismissal_needs_a_reason():
    owner = factories.UserFactory()
    record = _commencement(owner=owner, on=TODAY + timedelta(days=2))
    _reconcile(TODAY)
    (row,) = _rows(record)

    with refused(services.DISMISS_NEEDS_REASON):
        services.dismiss_reminder(reminder=row, actor=owner, reason="   ")


# ---------------------------------------------------------------------------
# The source changes underneath
# ---------------------------------------------------------------------------


def test_a_moved_date_retires_the_old_prompt_and_asks_about_the_new_one():
    owner = factories.UserFactory()
    record = _commencement(owner=owner, on=TODAY + timedelta(days=5))
    _reconcile(TODAY)
    (old,) = _rows(record)
    new_day = TODAY + timedelta(days=30)

    update_effective_date(
        record=record,
        kind=EffectiveDateKind.KNOWN_DATE,
        date_value=new_day,
        period_end=new_day,
        date_precision=DatePrecision.EXACT,
        description=record.description,
        actor=owner,
    )

    # Hidden at once, before any generator has run.
    assert selectors.open_reminders_for(owner, today=TODAY) == []
    _reconcile(TODAY)
    old.refresh_from_db()
    assert (old.status, old.obsolete_reason) == (
        ReminderStatus.OBSOLETE,
        ObsoleteReason.DATE_CHANGED,
    )
    assert old.source_date == TODAY + timedelta(days=5)  # history keeps what it was about
    # Nothing for the new date yet: it is 30 days off.
    assert [row.status for row in _rows(record)] == [ReminderStatus.OBSOLETE]

    _reconcile(new_day - timedelta(days=7))
    fresh = [row for row in _rows(record) if row.source_date == new_day]
    assert [(row.stage, row.status) for row in fresh] == [
        (ReminderStage.SEVEN_DAYS, ReminderStatus.OPEN)
    ]


def test_an_acknowledged_date_that_moves_is_asked_about_again():
    owner = factories.UserFactory()
    record = _commencement(owner=owner, on=TODAY + timedelta(days=5))
    _reconcile(TODAY)
    services.acknowledge_reminder(reminder=_rows(record)[0], actor=owner)
    new_day = TODAY + timedelta(days=6)

    update_effective_date(
        record=record,
        kind=EffectiveDateKind.KNOWN_DATE,
        date_value=new_day,
        period_end=new_day,
        date_precision=DatePrecision.EXACT,
        actor=owner,
    )
    _reconcile(TODAY)

    statuses = {(row.source_date, row.status) for row in _rows(record)}
    assert statuses == {
        (TODAY + timedelta(days=5), ReminderStatus.ACKNOWLEDGED),
        (new_day, ReminderStatus.OPEN),
    }


def _move(record, actor, day, precision=DatePrecision.EXACT):
    start, end = day, day
    if precision == DatePrecision.MONTH:
        start = day.replace(day=1)
        end = (start.replace(day=28) + timedelta(days=4)).replace(day=1) - timedelta(days=1)
    update_effective_date(
        record=type(record).objects.get(pk=record.pk),
        kind=EffectiveDateKind.KNOWN_DATE,
        date_value=start,
        period_end=end,
        date_precision=precision,
        description=record.description,
        actor=actor,
    )


def test_a_date_moved_away_and_back_is_asked_about_again():
    """D1 -> D2 -> D1: the retired D1 row stays history and a fresh D1 prompt is owed."""
    owner = factories.UserFactory()
    first = TODAY + timedelta(days=5)
    record = _commencement(owner=owner, on=first)
    _reconcile(TODAY)
    _move(record, owner, TODAY + timedelta(days=40))
    _reconcile(TODAY)

    _move(record, owner, first)
    result = _reconcile(TODAY)

    assert result.created == 1
    rows = [(row.source_date, row.stage, row.status) for row in _rows(record)]
    assert sorted(rows, key=lambda r: r[2]) == [
        (first, ReminderStage.SEVEN_DAYS, ReminderStatus.OBSOLETE),
        (first, ReminderStage.SEVEN_DAYS, ReminderStatus.OPEN),
    ]
    assert len(selectors.open_reminders_for(owner, today=TODAY)) == 1
    # And again, and the day itself still asks: the history only grows.
    assert _reconcile(TODAY).created == 0
    _reconcile(first)
    assert selectors.open_reminders_for(owner, today=first)[0].reminder.stage == (
        ReminderStage.ON_DATE
    )


def test_a_day_made_approximate_and_exact_again_is_asked_about_again():
    """EXACT -> MONTH -> EXACT on the same day."""
    owner = factories.UserFactory()
    day = TODAY + timedelta(days=5)
    record = _commencement(owner=owner, on=day)
    _reconcile(TODAY)
    _move(record, owner, day, precision=DatePrecision.MONTH)
    _reconcile(TODAY)
    assert selectors.open_reminders_for(owner, today=TODAY) == []

    _move(record, owner, day)
    result = _reconcile(TODAY)

    assert result.created == 1
    statuses = sorted(row.status for row in _rows(record))
    assert statuses == [ReminderStatus.OBSOLETE, ReminderStatus.OPEN]
    assert len(selectors.open_reminders_for(owner, today=TODAY)) == 1


def test_an_answered_date_moved_away_and_back_stays_answered():
    owner = factories.UserFactory()
    first = TODAY + timedelta(days=5)
    record = _commencement(owner=owner, on=first)
    _reconcile(TODAY)
    services.acknowledge_reminder(reminder=_rows(record)[0], actor=owner)
    _move(record, owner, TODAY + timedelta(days=40))
    _reconcile(TODAY)

    _move(record, owner, first)
    _reconcile(TODAY)
    _reconcile(first)

    assert selectors.open_reminders_for(owner, today=first) == []


def test_a_date_made_approximate_retires_its_prompt():
    owner = factories.UserFactory()
    day = TODAY + timedelta(days=5)
    record = _commencement(owner=owner, on=day)
    _reconcile(TODAY)
    start = day.replace(day=1)
    end = (start.replace(day=28) + timedelta(days=4)).replace(day=1) - timedelta(days=1)

    update_effective_date(
        record=record,
        kind=EffectiveDateKind.KNOWN_DATE,
        date_value=start,
        period_end=end,
        date_precision=DatePrecision.MONTH,
        actor=owner,
    )
    _reconcile(TODAY)

    assert [(row.status, row.obsolete_reason) for row in _rows(record)] == [
        (ReminderStatus.OBSOLETE, ObsoleteReason.NOT_EXACT)
    ]


def test_a_cancelled_commencement_has_no_open_prompt():
    owner = factories.UserFactory()
    record = _commencement(owner=owner, on=TODAY + timedelta(days=4))
    _reconcile(TODAY)

    cancel_effective_date(record=record, actor=owner, reason="Eelnõu võeti menetlusest tagasi.")

    assert selectors.unresolved_count(owner, today=TODAY) == 0
    _reconcile(TODAY)
    _reconcile(TODAY + timedelta(days=4))
    assert [(row.status, row.obsolete_reason) for row in _rows(record)] == [
        (ReminderStatus.OBSOLETE, ObsoleteReason.NOT_ACTIVE)
    ]
    # The commencement row itself is history, untouched.
    record.refresh_from_db()
    assert record.status == FactStatus.CANCELLED


def test_a_superseded_commencement_has_no_open_prompt():
    owner = factories.UserFactory()
    record = _commencement(owner=owner, on=TODAY + timedelta(days=4))
    _reconcile(TODAY)
    type(record).objects.filter(pk=record.pk).update(status=FactStatus.SUPERSEDED)
    _commencement(owner=owner, matter=record.matter, on=TODAY + timedelta(days=40))

    _reconcile(TODAY)

    assert [(row.status, row.obsolete_reason) for row in _rows(record)] == [
        (ReminderStatus.OBSOLETE, ObsoleteReason.NOT_ACTIVE)
    ]
    assert selectors.open_reminders_for(owner, today=TODAY) == []


def test_a_commencement_removed_from_its_teema_has_no_open_prompt():
    from django.utils import timezone

    owner = factories.UserFactory()
    record = _commencement(owner=owner, on=TODAY + timedelta(days=4))
    _reconcile(TODAY)
    type(record).objects.filter(pk=record.pk).update(removed_at=timezone.now())

    assert selectors.open_reminders_for(owner, today=TODAY) == []
    _reconcile(TODAY)
    assert [row.obsolete_reason for row in _rows(record)] == [ObsoleteReason.REMOVED]


# ---------------------------------------------------------------------------
# Which commencements are eligible at all
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("kind", "precision"),
    [
        (EffectiveDateKind.GENERAL_ORDER, DatePrecision.EXACT),
        (EffectiveDateKind.UNKNOWN, DatePrecision.EXACT),
        (EffectiveDateKind.KNOWN_DATE, DatePrecision.MONTH),
    ],
)
def test_general_order_unknown_and_approximate_commencements_get_no_reminder(kind, precision):
    record = _commencement(
        owner=factories.UserFactory(),
        on=TODAY + timedelta(days=3),
        kind=kind,
        precision=precision,
    )

    for offset in range(0, 40):
        _reconcile(TODAY - timedelta(days=10) + timedelta(days=offset))

    assert _rows(record) == []


def test_a_day_read_out_of_text_counts_as_a_day():
    record = _commencement(
        owner=factories.UserFactory(),
        on=TODAY + timedelta(days=3),
        precision=DatePrecision.INFERRED,
    )

    _reconcile(TODAY)

    assert [row.stage for row in _rows(record)] == [ReminderStage.SEVEN_DAYS]


def test_a_drafts_planned_commencement_is_information_and_gets_no_reminder():
    """ADR 0188 §3: informational milestones generate no work (ADR 0120 amendment)."""
    matter = factories.MatterFactory(owner=factories.UserFactory())
    for kind in (ImportantDateKind.PLANNED_COMMENCEMENT, ImportantDateKind.ESTIMATED_COMMENCEMENT):
        factories.ImportantDateFactory(
            matter=matter,
            kind=kind,
            date_value=TODAY + timedelta(days=3),
            period_end=TODAY + timedelta(days=3),
            date_precision=DatePrecision.EXACT,
        )

    result = _reconcile(TODAY)

    assert result.created == 0
    assert not CommencementReminder.objects.exists()


def test_a_closed_teema_is_still_asked_about():
    owner = factories.UserFactory()
    from app.matters.services import close_matter

    matter = factories.MatterFactory(owner=owner)
    record = _commencement(matter=matter, on=TODAY + timedelta(days=2))
    close_matter(matter=matter, disposition="COMPLETED", actor=owner, follow_ups_confirmed=True)

    _reconcile(TODAY)

    assert len(_rows(record)) == 1
    (row,) = selectors.open_reminders_for(owner, today=TODAY)
    assert row.matter_is_open is False


# ---------------------------------------------------------------------------
# Who is asked
# ---------------------------------------------------------------------------


def test_open_reminders_follow_the_current_vastutaja():
    first = factories.UserFactory()
    second = factories.UserFactory()
    record = _commencement(owner=first, on=TODAY + timedelta(days=3))
    _reconcile(TODAY)
    assert len(selectors.open_reminders_for(first, today=TODAY)) == 1

    type(record.matter).objects.filter(pk=record.matter.pk).update(owner=second)

    assert selectors.open_reminders_for(first, today=TODAY) == []
    assert len(selectors.open_reminders_for(second, today=TODAY)) == 1
    assert selectors.unresolved_count(second, today=TODAY) == 1
    # No second row was generated for the new owner.
    _reconcile(TODAY)
    assert len(_rows(record)) == 1


def test_an_unowned_teema_is_listed_for_department_management_only():
    head = factories.DepartmentHeadFactory()
    specialist = factories.UserFactory()
    record = _commencement(owner=None, on=TODAY + timedelta(days=3))
    _reconcile(TODAY)

    assert [
        row.reminder.effective_date_id
        for row in selectors.department_unowned_reminders(head, today=TODAY)
    ] == [record.pk]
    assert selectors.unresolved_count(head, today=TODAY) == 1
    assert selectors.department_unowned_reminders(specialist, today=TODAY) == []
    assert selectors.unresolved_count(specialist, today=TODAY) == 0
    assert selectors.open_reminders_for(head, today=TODAY) == []


def test_osakond_shows_unowned_prompts_to_the_head_and_not_to_a_specialist(client):
    from django.utils import timezone

    record = _commencement(owner=None, on=timezone.localdate() + timedelta(days=3))
    record.matter.__class__.objects.filter(pk=record.matter.pk).update(
        title="Vastutajata jõustumisega teema"
    )
    services.reconcile_commencement_reminders()

    client.force_login(factories.DepartmentHeadFactory())
    head_page = client.get(reverse("matters:department")).content.decode()
    client.force_login(factories.UserFactory())
    specialist_page = client.get(reverse("matters:department")).content.decode()

    assert "notif-osakond-heading" in head_page
    assert "Vastutajata jõustumisega teema" in head_page
    assert "notif-osakond-heading" not in specialist_page


def test_osakond_has_no_reminder_block_when_nothing_is_unowned(client):
    from django.utils import timezone

    _commencement(owner=factories.UserFactory(), on=timezone.localdate() + timedelta(days=3))
    services.reconcile_commencement_reminders()
    client.force_login(factories.DepartmentHeadFactory())

    page = client.get(reverse("matters:department")).content.decode()

    assert "notif-osakond-heading" not in page


def test_an_inactive_vastutaja_counts_as_nobody():
    head = factories.DepartmentHeadFactory()
    gone = factories.UserFactory()
    record = _commencement(owner=gone, on=TODAY + timedelta(days=3))
    _reconcile(TODAY)
    type(gone).objects.filter(pk=gone.pk).update(is_active=False)

    rows = selectors.department_unowned_reminders(head, today=TODAY)

    assert [row.reminder.effective_date_id for row in rows] == [record.pk]
    assert rows[0].owner_name == ""


# ---------------------------------------------------------------------------
# Deferral
# ---------------------------------------------------------------------------


def test_a_deferred_prompt_is_hidden_until_its_day():
    owner = factories.UserFactory()
    day = TODAY + timedelta(days=4)
    record = _commencement(owner=owner, on=day)
    _reconcile(TODAY)
    (row,) = _rows(record)

    services.defer_reminder(reminder=row, actor=owner, until=TODAY + timedelta(days=2), today=TODAY)

    assert selectors.open_reminders_for(owner, today=TODAY) == []
    assert selectors.unresolved_count(owner, today=TODAY) == 0
    assert len(selectors.deferred_reminders_for(owner, today=TODAY)) == 1
    assert len(selectors.open_reminders_for(owner, today=TODAY + timedelta(days=2))) == 1
    assert ChangeEvent.objects.filter(
        event_type=ChangeEventType.COMMENCEMENT_CHECK_DEFERRED, actor=owner
    ).exists()


def test_a_seven_day_deferral_past_the_date_is_capped_and_the_day_still_asks():
    """Deferring the first prompt beyond D must not swallow the on-the-day prompt."""
    owner = factories.UserFactory()
    day = TODAY + timedelta(days=4)
    record = _commencement(owner=owner, on=day)
    _reconcile(TODAY)
    (row,) = _rows(record)

    deferred = services.defer_reminder(
        reminder=row, actor=owner, until=day + timedelta(days=20), today=TODAY
    )

    assert deferred.deferred_until == day
    event = ChangeEvent.objects.get(event_type=ChangeEventType.COMMENCEMENT_CHECK_DEFERRED)
    assert event.payload["until"] == day.isoformat()
    _reconcile(day)
    on_date = next(r for r in _rows(record) if r.stage == ReminderStage.ON_DATE)
    assert (on_date.status, on_date.deferred_until) == (ReminderStatus.OPEN, None)
    assert [r.pk for r in selectors.open_reminders_for(owner, today=day)] == [on_date.pk]
    assert selectors.unresolved_count(owner, today=day) == 1


def test_the_on_the_day_prompt_may_itself_be_put_off():
    owner = factories.UserFactory()
    record = _commencement(owner=owner, on=TODAY)
    _reconcile(TODAY)
    (row,) = _rows(record)

    deferred = services.defer_reminder(
        reminder=row, actor=owner, until=TODAY + timedelta(days=10), today=TODAY
    )

    assert deferred.deferred_until == TODAY + timedelta(days=10)
    assert selectors.open_reminders_for(owner, today=TODAY) == []


@pytest.mark.parametrize(
    ("until", "message"),
    [
        (TODAY, services.DEFER_NOT_AHEAD),
        (TODAY - timedelta(days=1), services.DEFER_NOT_AHEAD),
        (TODAY + timedelta(days=services.MAX_DEFER_DAYS + 1), services.DEFER_TOO_FAR),
    ],
)
def test_a_deferral_must_be_ahead_and_bounded(until, message):
    owner = factories.UserFactory()
    # The on-the-day prompt: a seven-day one would be capped at its date first.
    record = _commencement(owner=owner, on=TODAY)
    _reconcile(TODAY)

    with refused(message):
        services.defer_reminder(reminder=_rows(record)[0], actor=owner, until=until, today=TODAY)


# ---------------------------------------------------------------------------
# Idempotence, restart and the Tallinn calendar
# ---------------------------------------------------------------------------


def test_a_restart_or_a_second_run_creates_nothing_twice():
    owners = [factories.UserFactory() for _ in range(3)]
    for index, owner in enumerate(owners):
        _commencement(owner=owner, on=TODAY + timedelta(days=index + 1))

    first = _reconcile(TODAY)
    second = _reconcile(TODAY)
    third = services.reconcile_commencement_reminders(TODAY)

    assert first.created == 3
    assert (second.created, second.superseded, second.obsoleted) == (0, 0, 0)
    assert not third.changed
    assert CommencementReminder.objects.count() == 3


def test_the_unique_constraint_backs_the_generator():
    from django.db import IntegrityError, transaction

    record = _commencement(owner=factories.UserFactory(), on=TODAY + timedelta(days=3))
    _reconcile(TODAY)
    (row,) = _rows(record)

    with pytest.raises(IntegrityError), transaction.atomic():
        CommencementReminder.objects.create(
            effective_date=record, source_date=row.source_date, stage=row.stage, due_on=row.due_on
        )


def test_today_is_the_tallinn_date_not_the_utc_one(monkeypatch):
    """22:30 UTC on 1 November is 00:30 on 2 November in Tallinn."""
    from django.utils import timezone as django_timezone

    late_evening_utc = datetime(2026, 11, 1, 22, 30, tzinfo=UTC)
    monkeypatch.setattr(django_timezone, "now", lambda: late_evening_utc)
    # Seven Tallinn days ahead of 2 November; eight UTC days ahead of 1 November.
    record = _commencement(owner=factories.UserFactory(), on=date(2026, 11, 9))

    result = services.reconcile_commencement_reminders()

    assert result.today == date(2026, 11, 2)
    assert [row.stage for row in _rows(record)] == [ReminderStage.SEVEN_DAYS]


def test_just_before_tallinn_midnight_it_is_still_the_previous_day(monkeypatch):
    from django.utils import timezone as django_timezone

    monkeypatch.setattr(django_timezone, "now", lambda: datetime(2026, 11, 1, 21, 59, tzinfo=UTC))
    record = _commencement(owner=factories.UserFactory(), on=date(2026, 11, 9))

    result = services.reconcile_commencement_reminders()

    assert result.today == date(2026, 11, 1)
    assert _rows(record) == []


# ---------------------------------------------------------------------------
# The command
# ---------------------------------------------------------------------------


def test_the_command_prints_one_summary_line_and_check_reports_owed_work(capsys):
    _commencement(owner=factories.UserFactory(), on=TODAY + timedelta(days=3))

    with pytest.raises(CommandError):
        call_command("generate_commencement_reminders", "--check", "--today", TODAY.isoformat())
    assert not CommencementReminder.objects.exists()  # --check writes nothing

    call_command("generate_commencement_reminders", "--today", TODAY.isoformat())
    out = capsys.readouterr().out
    assert "Jõustumise meeldetuletused 2026-11-02: Loodud 1" in out

    call_command(
        "generate_commencement_reminders", "--check", "--quiet", "--today", TODAY.isoformat()
    )
    assert CommencementReminder.objects.count() == 1


# ---------------------------------------------------------------------------
# The routes
# ---------------------------------------------------------------------------


def _due_reminder(owner):
    from django.utils import timezone

    record = _commencement(owner=owner, on=timezone.localdate() + timedelta(days=3))
    services.reconcile_commencement_reminders()
    return _rows(record)[0]


def test_the_list_and_the_bell_show_the_owners_prompt_in_checking_words(client):
    owner = factories.UserFactory()
    reminder = _due_reminder(owner)
    client.force_login(owner)

    page = client.get(reverse("notifications:list")).content.decode()
    bell = client.get(reverse("notifications:bell")).content.decode()

    assert "Kontrolli jõustumist" in page
    assert reminder.effective_date.matter.title in page
    assert "Põhiosa jõustub" in page
    assert "#joustumine" in page
    assert "jõustus" not in page.lower()
    assert ">1<" in bell


def test_the_bell_says_nothing_when_there_is_nothing(client):
    client.force_login(factories.UserFactory())

    bell = client.get(reverse("notifications:bell")).content.decode()

    assert "notifbell__count" not in bell


def test_minu_asjad_carries_the_block_and_a_colleagues_page_does_not(client):
    owner = factories.UserFactory()
    reminder = _due_reminder(owner)
    head = factories.DepartmentHeadFactory()

    client.force_login(owner)
    mine = client.get(reverse("matters:my_work")).content.decode()
    client.force_login(head)
    theirs = client.get(reverse("matters:person_work", kwargs={"pk": owner.pk})).content.decode()

    assert reverse("notifications:defer", kwargs={"pk": reminder.pk}) in mine
    assert reverse("notifications:defer", kwargs={"pk": reminder.pk}) not in theirs


def test_kontrollitud_is_retired_and_a_stale_post_writes_nothing(client):
    """Owner's review of 2026-10-30: no button, no route, nothing hidden."""
    owner = factories.UserFactory()
    reminder = _due_reminder(owner)
    client.force_login(owner)

    with pytest.raises(NoReverseMatch):
        reverse("notifications:acknowledge", kwargs={"pk": reminder.pk})
    response = client.post(f"/teavitused/{reminder.pk}/kontrollitud/", {"tagasi": "minu_asjad"})

    assert response.status_code == 404
    reminder.refresh_from_db()
    assert reminder.status == ReminderStatus.OPEN
    assert not ChangeEvent.objects.filter(event_type__startswith="COMMENCEMENT_CHECK_").exists()
    page = client.get(reverse("matters:my_work")).content.decode()
    assert "Kontrollitud</button>" not in page
    assert "Ametlik allikas on märkimata" not in page


def test_dismissing_from_minu_asjad_closes_it_and_returns(client):
    owner = factories.UserFactory()
    reminder = _due_reminder(owner)
    client.force_login(owner)

    response = client.post(
        reverse("notifications:dismiss", kwargs={"pk": reminder.pk}),
        {"tagasi": "minu_asjad", "reason": "Kontrollisin, jõustub nagu kirjas."},
    )

    assert response.status_code == 302
    assert response["Location"] == reverse("matters:my_work")
    reminder.refresh_from_db()
    assert reminder.status == ReminderStatus.DISMISSED
    # A second press finds nothing open to answer.
    again = client.post(
        reverse("notifications:dismiss", kwargs={"pk": reminder.pk}), {"reason": "Uuesti."}
    )
    assert again.status_code == 404


def test_a_return_address_cannot_be_chosen_by_the_form(client):
    owner = factories.UserFactory()
    reminder = _due_reminder(owner)
    client.force_login(owner)

    response = client.post(
        reverse("notifications:dismiss", kwargs={"pk": reminder.pk}),
        {"tagasi": "https://example.invalid/", "reason": "Ei ole asjakohane."},
    )

    assert response["Location"] == reverse("notifications:list")


def test_defer_and_dismiss_through_the_routes(client):
    from django.utils import timezone

    owner = factories.UserFactory()
    reminder = _due_reminder(owner)
    client.force_login(owner)
    until = timezone.localdate() + timedelta(days=2)

    client.post(
        reverse("notifications:defer", kwargs={"pk": reminder.pk}),
        {"until": f"{until.day}.{until.month}.{until.year}"},
    )
    reminder.refresh_from_db()
    assert reminder.deferred_until == until

    empty = client.post(reverse("notifications:dismiss", kwargs={"pk": reminder.pk}), {})
    assert empty.status_code == 302
    reminder.refresh_from_db()
    assert reminder.status == ReminderStatus.OPEN

    client.post(
        reverse("notifications:dismiss", kwargs={"pk": reminder.pk}),
        {"reason": "Jõustumine on juba kontrollitud teise teema juures."},
    )
    reminder.refresh_from_db()
    assert reminder.status == ReminderStatus.DISMISSED
    assert reminder.resolution_note.startswith("Jõustumine on juba")


@pytest.mark.parametrize(
    "factory", [factories.ReaderFactory, factories.AdministratorFactory], ids=["READER", "ADMIN"]
)
def test_a_non_writer_cannot_answer(client, factory):
    reminder = _due_reminder(factories.UserFactory())
    client.force_login(factory())

    for name, data in (
        ("notifications:defer", {"until": "1.1.2099"}),
        ("notifications:dismiss", {"reason": "Ei."}),
    ):
        response = client.post(reverse(name, kwargs={"pk": reminder.pk}), data)
        assert response.status_code == 404, name

    reminder.refresh_from_db()
    assert (reminder.status, reminder.deferred_until) == (ReminderStatus.OPEN, None)
    assert not ChangeEvent.objects.filter(event_type__startswith="COMMENCEMENT_CHECK_").exists()


def test_a_colleague_may_answer_and_is_recorded_as_the_one_who_did(client):
    owner = factories.UserFactory()
    colleague = factories.UserFactory()
    reminder = _due_reminder(owner)
    client.force_login(colleague)

    client.post(
        reverse("notifications:dismiss", kwargs={"pk": reminder.pk}),
        {"reason": "Kolleegina kontrollitud."},
    )

    reminder.refresh_from_db()
    assert reminder.resolved_by == colleague
    event = ChangeEvent.objects.get(event_type=ChangeEventType.COMMENCEMENT_CHECK_DISMISSED)
    assert event.actor == colleague
    assert event.object_id == reminder.effective_date_id


def test_an_administrator_reads_an_empty_list(client):
    _due_reminder(factories.UserFactory())
    client.force_login(factories.AdministratorFactory())

    page = client.get(reverse("notifications:list"))

    assert page.status_code == 200
    assert "Põhiosa jõustub" not in page.content.decode()
    assert "notifbell__count" not in client.get(reverse("notifications:bell")).content.decode()


def test_anonymous_callers_are_sent_to_sign_in(client):
    reminder = _due_reminder(factories.UserFactory())

    for url in (
        reverse("notifications:list"),
        reverse("notifications:bell"),
    ):
        assert client.get(url).status_code == 302
    response = client.post(
        reverse("notifications:dismiss", kwargs={"pk": reminder.pk}), {"reason": "Anonüümne."}
    )
    assert response.status_code == 302
    reminder.refresh_from_db()
    assert reminder.status == ReminderStatus.OPEN


def test_behind_the_gate_a_persona_less_session_is_sent_to_choose_one(settings):
    apply_shared_gate(settings)
    reminder = _due_reminder(factories.UserFactory())
    client = Client()
    assert client.post(reverse("accounts:shared_gate"), {"password": PASSWORD}).status_code == 302

    for response in (
        client.get(reverse("notifications:list")),
        client.post(
            reverse("notifications:dismiss", kwargs={"pk": reminder.pk}), {"reason": "Ilma."}
        ),
    ):
        assert response.status_code == 302
        assert response["Location"].startswith(reverse("accounts:choose_persona"))


def test_a_shared_gate_check_is_attributed_to_the_chosen_persona_not_verified(settings):
    """ADR 0180: an answer by a picked persona is attributable, not proof of identity."""
    apply_shared_gate(settings)
    persona = factories.UserFactory(display_name="Mari Näidisjurist")
    reminder = _due_reminder(persona)
    client = Client()
    assert client.post(reverse("accounts:shared_gate"), {"password": PASSWORD}).status_code == 302
    assert client.post(reverse("accounts:act_as"), {"user_id": str(persona.pk)}).status_code == 302

    response = client.post(
        reverse("notifications:dismiss", kwargs={"pk": reminder.pk}),
        {"reason": "Kontrollitud valitud isikuna."},
    )

    assert response.status_code == 302
    event = ChangeEvent.objects.get(event_type=ChangeEventType.COMMENCEMENT_CHECK_DISMISSED)
    assert event.actor == persona
    assert event.auth_method == AuthMethod.SHARED_GATE
    assert event.auth_assurance == AuthAssurance.SHARED_SECRET
    assert event.authenticated_principal is None
    assert event.acting_persona == persona
    assert event.payload["date"] == reminder.source_date.isoformat()
    reminder.refresh_from_db()
    assert reminder.resolved_by == persona


def test_the_generator_never_writes_an_answer():
    """The generator writes reminder rows only; it never claims anybody checked."""
    _due_reminder(factories.UserFactory())

    assert not ChangeEvent.objects.filter(event_type__startswith="COMMENCEMENT_CHECK_").exists()
