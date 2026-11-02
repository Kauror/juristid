"""«Märgi loetuks» on a commencement check is the reader's own receipt (ADR 0188, 2026-11-02).

A `CommencementReminder` is shared — its recipient is whoever is responsible
now — so reading it must neither answer it nor end it for anybody else. These
tests hold the four ways that could go wrong: a new owner inheriting somebody
else's reading, one manager's reading clearing an unowned prompt for the others,
a moved date staying read, and a generator rerun or restart undoing a reading.
"""

from __future__ import annotations

from datetime import timedelta

import pytest
from django.urls import reverse
from django.utils import timezone

from app.audit.models import ChangeEvent
from app.intelligence.enums import EffectiveDateKind
from app.intelligence.services import update_effective_date
from app.matters.models import Matter
from app.notifications import inbox, selectors, services
from app.notifications.enums import ReminderStatus
from app.notifications.models import CommencementReminder, CommencementReminderPersonalState
from app.workflow.enums import DatePrecision
from tests import factories

pytestmark = pytest.mark.django_db


def _due(owner=None, *, matter=None):
    matter = matter or factories.MatterFactory(owner=owner)
    day = timezone.localdate()
    record = factories.EffectiveDateFactory(
        matter=matter, date_value=day, period_end=day, description="Põhiosa jõustub"
    )
    services.reconcile_commencement_reminders()
    return CommencementReminder.objects.get(effective_date=record, status=ReminderStatus.OPEN)


def _commencement_items(user):
    return [item for item in inbox.inbox_for(user) if item.type == inbox.TYPE_COMMENCEMENT]


def test_reading_records_no_check_no_event_and_leaves_the_teema_alone(client):
    owner = factories.UserFactory()
    reminder = _due(owner)
    matter = reminder.effective_date.matter
    before = (matter.is_open, matter.stage_id, matter.updated_at)
    client.force_login(owner)

    client.post(reverse("notifications:reminder_read", kwargs={"pk": reminder.pk}))

    reminder.refresh_from_db()
    assert reminder.status == ReminderStatus.OPEN
    assert (reminder.resolved_at, reminder.resolved_by, reminder.resolution_note) == (
        None,
        None,
        "",
    )
    assert not ChangeEvent.objects.filter(event_type__startswith="COMMENCEMENT_CHECK_").exists()
    matter = Matter.objects.get(pk=matter.pk)
    assert (matter.is_open, matter.stage_id, matter.updated_at) == before
    assert _commencement_items(owner) == []
    assert inbox.inbox_count(owner) == 0


def test_a_new_owner_does_not_inherit_the_previous_owners_reading():
    first = factories.UserFactory()
    second = factories.UserFactory()
    reminder = _due(first)
    services.mark_reminder_read(reminder=reminder, reader=first)

    Matter.objects.filter(pk=reminder.effective_date.matter_id).update(owner=second)

    assert [item.pk for item in _commencement_items(second)] == [reminder.pk]
    assert inbox.inbox_count(second) == 1
    assert _commencement_items(first) == []


def test_one_managers_reading_leaves_the_unowned_prompt_for_the_others():
    head = factories.DepartmentHeadFactory()
    other_head = factories.DepartmentHeadFactory()
    reminder = _due(matter=factories.MatterFactory(owner=None))
    assert len(_commencement_items(head)) == 1

    services.mark_reminder_read(reminder=reminder, reader=head)

    assert _commencement_items(head) == []
    assert [item.pk for item in _commencement_items(other_head)] == [reminder.pk]
    # Osakond's summary is the department's open work, not one person's inbox.
    assert len(selectors.department_unowned_reminders(head)) == 1


def test_a_specialist_cannot_read_an_unowned_prompt(client):
    reminder = _due(matter=factories.MatterFactory(owner=None))
    client.force_login(factories.UserFactory())

    response = client.post(reverse("notifications:reminder_read", kwargs={"pk": reminder.pk}))

    assert response.status_code == 404
    assert not CommencementReminderPersonalState.objects.exists()


def test_a_moved_date_is_asked_about_afresh():
    owner = factories.UserFactory()
    reminder = _due(owner)
    services.mark_reminder_read(reminder=reminder, reader=owner)
    record = reminder.effective_date
    tomorrow = timezone.localdate() + timedelta(days=1)

    update_effective_date(
        record=record,
        kind=EffectiveDateKind.KNOWN_DATE,
        date_value=tomorrow,
        period_end=tomorrow,
        date_precision=DatePrecision.EXACT,
        description=record.description,
        actor=owner,
    )
    services.reconcile_commencement_reminders(tomorrow)

    fresh = CommencementReminder.objects.get(effective_date=record, status=ReminderStatus.OPEN)
    assert fresh.pk != reminder.pk
    assert [
        item.pk for item in inbox.inbox_for(owner, tomorrow) if item.type == "commencement"
    ] == [fresh.pk]


def test_a_generator_rerun_does_not_bring_a_read_prompt_back():
    owner = factories.UserFactory()
    reminder = _due(owner)
    services.mark_reminder_read(reminder=reminder, reader=owner)

    services.reconcile_commencement_reminders()
    services.reconcile_commencement_reminders()

    assert CommencementReminder.objects.filter(effective_date=reminder.effective_date).count() == 1
    assert _commencement_items(owner) == []


def test_repeated_reads_store_one_receipt():
    owner = factories.UserFactory()
    reminder = _due(owner)

    for _ in range(3):
        services.mark_reminder_read(reminder=reminder, reader=owner)

    assert (
        CommencementReminderPersonalState.objects.filter(reminder=reminder, user=owner).count() == 1
    )


def test_a_deferred_prompt_stays_out_until_due_then_returns_unless_read():
    owner = factories.UserFactory()
    reminder = _due(owner)
    today = timezone.localdate()
    services.defer_reminder(reminder=reminder, actor=owner, until=today + timedelta(days=2))

    assert _commencement_items(owner) == []
    later = today + timedelta(days=2)
    assert [i.pk for i in inbox.inbox_for(owner, later) if i.type == "commencement"] == [
        reminder.pk
    ]

    services.mark_reminder_read(reminder=reminder, reader=owner)
    assert [i for i in inbox.inbox_for(owner, later) if i.type == "commencement"] == []


def test_the_bell_the_rail_and_the_list_agree_after_reading(client):
    owner = factories.UserFactory()
    kept = _due(owner)
    read = _due(owner)
    client.force_login(owner)

    client.post(
        reverse("notifications:reminder_read", kwargs={"pk": read.pk}), {"tagasi": "minu_asjad"}
    )

    listing = client.get(reverse("notifications:list")).content.decode()
    rail = client.get(reverse("matters:my_work")).content.decode()
    bell = client.get(reverse("notifications:bell")).content.decode()
    # By the item's own action, not the title: Minu asjad lists owned Teemad too.
    for html in (listing, rail):
        assert reverse("notifications:reminder_read", kwargs={"pk": kept.pk}) in html
        assert reverse("notifications:reminder_read", kwargs={"pk": read.pk}) not in html
    assert inbox.inbox_count(owner) == 1
    assert ">1<" in bell


def test_reading_a_stale_page_after_the_prompt_was_answered_elsewhere_is_a_404(client):
    """A prompt answered historically (e.g. «Kontrollitud» before its retirement)
    keeps its meaning and is not reopened by a late «Märgi loetuks»."""
    owner = factories.UserFactory()
    reminder = _due(owner)
    services.acknowledge_reminder(reminder=reminder, actor=owner)
    client.force_login(owner)

    response = client.post(reverse("notifications:reminder_read", kwargs={"pk": reminder.pk}))

    assert response.status_code == 404
    reminder.refresh_from_db()
    assert reminder.status == ReminderStatus.ACKNOWLEDGED
    assert not reminder.personal_states.exists()


# ---------------------------------------------------------------------------
# «Lükka edasi» is personal too (owner's correction of 2026-11-02)
# ---------------------------------------------------------------------------


def _items_on(user, day):
    return [item.pk for item in inbox.inbox_for(user, day) if item.type == "commencement"]


def test_one_managers_postponement_leaves_the_prompt_with_the_others():
    head = factories.DepartmentHeadFactory()
    other_head = factories.DepartmentHeadFactory()
    reminder = _due(matter=factories.MatterFactory(owner=None))
    today = timezone.localdate()

    services.defer_for_reader(reminder=reminder, reader=head, until=today + timedelta(days=3))

    assert _items_on(head, today) == []
    assert _items_on(other_head, today) == [reminder.pk]
    assert inbox.inbox_count(other_head) == 1
    # Osakond's summary is the department's work, not one person's inbox.
    assert len(selectors.department_unowned_reminders(head)) == 1
    reminder.refresh_from_db()
    assert reminder.deferred_until is None


def test_a_colleagues_postponement_does_not_hide_the_owners_prompt():
    owner = factories.UserFactory()
    head = factories.DepartmentHeadFactory()
    reminder = _due(owner)
    today = timezone.localdate()
    # The head may not even reach the owner's prompt through the route; the
    # service is called directly to show the state is per person regardless.
    services.defer_for_reader(reminder=reminder, reader=head, until=today + timedelta(days=3))

    assert _items_on(owner, today) == [reminder.pk]


def test_a_personal_postponement_returns_on_its_date():
    owner = factories.UserFactory()
    reminder = _due(owner)
    today = timezone.localdate()

    services.defer_for_reader(reminder=reminder, reader=owner, until=today + timedelta(days=2))

    assert _items_on(owner, today + timedelta(days=1)) == []
    assert _items_on(owner, today + timedelta(days=2)) == [reminder.pk]
    assert [row.pk for row in selectors.deferred_reminders_for(owner, today=today)] == [reminder.pk]


def test_postponing_again_replaces_the_date_and_reading_wins():
    owner = factories.UserFactory()
    reminder = _due(owner)
    today = timezone.localdate()

    services.defer_for_reader(reminder=reminder, reader=owner, until=today + timedelta(days=5))
    services.defer_for_reader(reminder=reminder, reader=owner, until=today + timedelta(days=1))
    assert reminder.personal_states.get().deferred_until == today + timedelta(days=1)

    services.mark_reminder_read(reminder=reminder, reader=owner)
    assert _items_on(owner, today + timedelta(days=10)) == []
    assert selectors.deferred_reminders_for(owner, today=today) == []


def test_a_global_deferral_recorded_before_keeps_applying_to_everybody():
    """Deferrals made while «Lükka edasi» was shared keep their recorded meaning."""
    owner = factories.UserFactory()
    reminder = _due(owner)
    today = timezone.localdate()
    services.defer_reminder(reminder=reminder, actor=owner, until=today + timedelta(days=2))

    assert _items_on(owner, today) == []
    assert [row.pk for row in selectors.deferred_reminders_for(owner, today=today)] == [reminder.pk]
    assert _items_on(owner, today + timedelta(days=2)) == [reminder.pk]


def test_postponing_records_no_check_and_no_event():
    owner = factories.UserFactory()
    reminder = _due(owner)

    services.defer_for_reader(
        reminder=reminder, reader=owner, until=timezone.localdate() + timedelta(days=2)
    )

    reminder.refresh_from_db()
    assert (reminder.status, reminder.deferred_until, reminder.resolved_at) == (
        ReminderStatus.OPEN,
        None,
        None,
    )
    assert not ChangeEvent.objects.filter(event_type__startswith="COMMENCEMENT_CHECK_").exists()
