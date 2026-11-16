"""«Lisa meeldetuletus»: a person's own reminder on a Teema (owner, 2026-11-16).

What the feature promises, in tests:

* set from a Minu asjad row's ⋯ menu through a CSRF POST, for a day not in the
  past, only on a Teema the person may see;
* nobody else can read, unread or remove it — another person gets a 404;
* from its day it is in the inbox (the bell's number and `inbox_for`), before
  that it is listed under «Tulevased meeldetuletused» on `/teavitused/`;
* reading moves it to the history, unreading brings it back, and «Märgi kõik
  loetuks» spares one that arrived after the boundary;
* a Teema restricted since takes its reminders out of every list;
* it is presentation state: no `ChangeEvent` is ever written;
* the row's «!» marker is on the person's own Minu asjad only.
"""

from __future__ import annotations

from datetime import timedelta

import pytest
from django.urls import reverse
from django.utils import timezone

from app.audit.models import ChangeEvent
from app.matters.models import Matter
from app.matters.services import create_matter
from app.notifications import acknowledgement, feed, inbox, personal
from app.notifications.models import PersonalReminder
from app.workflow.enums import ActionKind, DateSemantics
from app.workflow.services import set_next_action
from tests import factories

pytestmark = pytest.mark.django_db


@pytest.fixture
def today():
    return timezone.localdate()


def _written(day) -> str:
    return f"{day.day:02d}.{day.month:02d}.{day.year}"


def _reminder(user, matter, day, note: str = "") -> PersonalReminder:
    return PersonalReminder.objects.create(recipient=user, matter=matter, remind_on=day, note=note)


def _create_url(matter) -> str:
    return reverse("notifications:personal_create", kwargs={"matter_id": matter.pk})


def _url(name: str, row: PersonalReminder) -> str:
    return reverse(f"notifications:{name}", kwargs={"pk": row.pk})


def _personal_items(user, today) -> list[inbox.InboxItem]:
    return [item for item in inbox.inbox_for(user, today) if item.type == inbox.TYPE_PERSONAL]


# ---------------------------------------------------------------------------
# Setting one
# ---------------------------------------------------------------------------


def test_a_valid_post_sets_the_reminder_and_returns_to_minu_asjad(client, specialist, today):
    matter = factories.MatterFactory(owner=specialist, title="Meeldetuletatav teema")
    client.force_login(specialist)
    day = today + timedelta(days=3)

    response = client.post(
        _create_url(matter),
        {
            "remind_on": _written(day),
            "note": "  Helista ministeeriumi  ",
            "tagasi": "minu_asjad_rida",
        },
    )

    assert response.status_code == 302
    assert response["Location"] == reverse("matters:my_work")
    row = PersonalReminder.objects.get()
    assert (row.recipient, row.matter, row.remind_on) == (specialist, matter, day)
    assert row.note == "Helista ministeeriumi"
    assert row.read_at is None


def test_return_to_teavitused(client, specialist, today):
    matter = factories.MatterFactory(owner=specialist)
    client.force_login(specialist)

    response = client.post(
        _create_url(matter), {"remind_on": _written(today), "tagasi": "teavitused"}
    )

    assert response.status_code == 302
    assert response["Location"] == reverse("notifications:list")
    assert PersonalReminder.objects.count() == 1


def test_a_past_day_is_refused(client, specialist, today):
    matter = factories.MatterFactory(owner=specialist)
    client.force_login(specialist)

    response = client.post(
        _create_url(matter),
        {"remind_on": _written(today - timedelta(days=1)), "tagasi": "minu_asjad_rida"},
        follow=True,
    )

    assert PersonalReminder.objects.count() == 0
    assert "Meeldetuletuse kuupäev ei saa olla minevikus." in response.content.decode()


def test_a_malformed_day_is_refused(client, specialist):
    matter = factories.MatterFactory(owner=specialist)
    client.force_login(specialist)

    response = client.post(_create_url(matter), {"remind_on": "homme", "tagasi": "teavitused"})

    assert response.status_code == 302
    assert PersonalReminder.objects.count() == 0


def _hide(matter) -> None:
    """Take ``matter`` out of `Matter.objects.visible_to` for every reader.

    Since docs/adr/0181 every business reader sees RESTRICTED Teemad too, so a
    Teema nobody may see any more is a deleted one (as in
    tests/test_ui01_notification_state.py).
    """
    Matter.all_objects.filter(pk=matter.pk).update(deleted_at=timezone.now())


def test_a_teema_the_person_may_not_see_is_a_404(client, specialist, other_specialist, today):
    hidden = factories.MatterFactory(owner=other_specialist)
    _hide(hidden)
    assert not Matter.objects.visible_to(specialist).filter(pk=hidden.pk).exists()
    client.force_login(specialist)

    response = client.post(_create_url(hidden), {"remind_on": _written(today)})

    assert response.status_code == 404
    assert PersonalReminder.objects.count() == 0


def test_an_account_without_business_read_gets_a_404(client, administrator, specialist, today):
    matter = factories.MatterFactory(owner=specialist)
    client.force_login(administrator)

    response = client.post(_create_url(matter), {"remind_on": _written(today)})

    assert response.status_code == 404
    assert PersonalReminder.objects.count() == 0


def test_the_service_refuses_a_hidden_teema_too(specialist, other_specialist, today):
    from app.core.errors import DomainError

    hidden = factories.MatterFactory(owner=other_specialist)
    _hide(hidden)

    with pytest.raises(DomainError):
        personal.create(user=specialist, matter=hidden, remind_on=today, note="", today=today)


def test_a_get_is_not_allowed(client, specialist):
    matter = factories.MatterFactory(owner=specialist)
    client.force_login(specialist)

    assert client.get(_create_url(matter)).status_code == 405


# ---------------------------------------------------------------------------
# Nobody else's
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("name", ["personal_read", "personal_unread", "personal_remove"])
def test_another_person_cannot_touch_someone_elses_reminder(
    client, specialist, other_specialist, today, name
):
    matter = factories.MatterFactory(owner=specialist)
    row = _reminder(specialist, matter, today)
    if name == "personal_unread":
        personal.mark_read(row)
    row.refresh_from_db()
    before = (row.read_at, row.updated_at)
    client.force_login(other_specialist)

    response = client.post(_url(name, row), {"tagasi": "teavitused"})

    assert response.status_code == 404
    row.refresh_from_db()
    assert (row.read_at, row.updated_at) == before


# ---------------------------------------------------------------------------
# Due, upcoming, read, unread, removed
# ---------------------------------------------------------------------------


def test_a_reminder_due_today_is_in_the_inbox(specialist, today):
    matter = factories.MatterFactory(owner=specialist, title="Tänane meeldetuletus")
    before = inbox.inbox_count(specialist, today)
    _reminder(specialist, matter, today, note="Vaata eelnõu üle")

    assert inbox.inbox_count(specialist, today) == before + 1
    [item] = _personal_items(specialist, today)
    assert item.title == "Tänane meeldetuletus"
    assert item.label == inbox.PERSONAL_LABEL
    assert item.note == "Vaata eelnõu üle"
    assert item.matter_id == matter.pk
    assert item.is_unread and item.can_read
    assert item.read_url == reverse("notifications:personal_read", kwargs={"pk": item.pk})


def test_a_future_reminder_is_not_in_the_inbox_but_listed_as_upcoming(client, specialist, today):
    matter = factories.MatterFactory(owner=specialist, title="Tuleva meeldetuletuse teema")
    row = _reminder(specialist, matter, today + timedelta(days=5))
    client.force_login(specialist)

    assert inbox.inbox_count(specialist, today) == 0
    assert _personal_items(specialist, today) == []
    html = client.get(reverse("notifications:list")).content.decode()
    assert "Tulevased meeldetuletused" in html
    upcoming = html[html.index("Tulevased meeldetuletused") :]
    assert "Tuleva meeldetuletuse teema" in upcoming
    assert _url("personal_remove", row) in upcoming
    assert "Eemalda" in upcoming


def test_reading_moves_it_to_the_history_and_unreading_brings_it_back(client, specialist, today):
    matter = factories.MatterFactory(owner=specialist, title="Loetav meeldetuletus")
    row = _reminder(specialist, matter, today)
    client.force_login(specialist)

    response = client.post(_url("personal_read", row), {"tagasi": "minu_asjad_rida"})

    assert response.status_code == 302
    assert response["Location"] == reverse("matters:my_work")
    row.refresh_from_db()
    assert row.read_at is not None
    assert inbox.inbox_count(specialist, today) == 0
    assert _personal_items(specialist, today) == []
    history = [
        item for item in feed.history_page(specialist).items if item.type == inbox.TYPE_PERSONAL
    ]
    assert [item.title for item in history] == ["Loetav meeldetuletus"]
    assert history[0].state == inbox.STATE_READ and history[0].can_unread
    assert feed.lookup(specialist, inbox.TYPE_PERSONAL, row.pk) is not None

    response = client.post(_url("personal_unread", row), {"tagasi": "teavitused"})

    assert response.status_code == 302
    row.refresh_from_db()
    assert row.read_at is None
    assert inbox.inbox_count(specialist, today) == 1
    assert [item.pk for item in _personal_items(specialist, today)] == [row.pk]
    assert not [
        item for item in feed.history_page(specialist).items if item.type == inbox.TYPE_PERSONAL
    ]


def test_an_upcoming_reminder_cannot_be_marked_unread(specialist, today):
    matter = factories.MatterFactory(owner=specialist)
    row = _reminder(specialist, matter, today + timedelta(days=2))

    assert personal.mark_unread(row) is False


def test_acknowledgement_reads_and_unreads_a_personal_reminder(specialist, today):
    matter = factories.MatterFactory(owner=specialist)
    row = _reminder(specialist, matter, today)

    resolved = acknowledgement.resolve(specialist, inbox.TYPE_PERSONAL, row.pk)
    assert resolved is not None and resolved.pk == row.pk
    assert acknowledgement.mark_read(specialist, inbox.TYPE_PERSONAL, resolved)
    assert inbox.inbox_count(specialist, today) == 0
    resolved.refresh_from_db()
    assert acknowledgement.mark_unread(specialist, inbox.TYPE_PERSONAL, resolved)
    assert inbox.inbox_count(specialist, today) == 1


def test_acknowledgement_resolves_nobody_elses(specialist, other_specialist, today):
    matter = factories.MatterFactory(owner=specialist)
    row = _reminder(specialist, matter, today)

    assert acknowledgement.resolve(other_specialist, inbox.TYPE_PERSONAL, row.pk) is None


def test_mark_all_read_spares_a_reminder_that_arrived_after_the_boundary(specialist, today):
    first = _reminder(specialist, factories.MatterFactory(owner=specialist), today)
    until = timezone.now()
    later = _reminder(specialist, factories.MatterFactory(owner=specialist), today)
    # Written after the panel was drawn: its `arrived_at` is past the boundary.
    PersonalReminder.objects.filter(pk=later.pk).update(updated_at=until + timedelta(minutes=1))
    PersonalReminder.objects.filter(pk=first.pk).update(updated_at=until - timedelta(minutes=1))
    later.refresh_from_db()
    assert personal.arrived_at(later) > until

    result = acknowledgement.mark_all_read(specialist, until=until, today=today)

    first.refresh_from_db()
    later.refresh_from_db()
    assert first.read_at is not None
    assert later.read_at is None
    assert result.marked == 1
    assert inbox.inbox_count(specialist, today) == 1


def test_mark_all_read_leaves_an_upcoming_one_alone(specialist, today):
    row = _reminder(
        specialist, factories.MatterFactory(owner=specialist), today + timedelta(days=1)
    )

    acknowledgement.mark_all_read(specialist, until=timezone.now(), today=today)

    row.refresh_from_db()
    assert row.read_at is None


def test_remove_deletes_it(client, specialist, today):
    matter = factories.MatterFactory(owner=specialist)
    row = _reminder(specialist, matter, today + timedelta(days=4))
    client.force_login(specialist)

    response = client.post(_url("personal_remove", row), {"tagasi": "teavitused"})

    assert response.status_code == 302
    assert response["Location"] == reverse("notifications:list")
    assert not PersonalReminder.objects.filter(pk=row.pk).exists()


def test_a_teema_gone_from_view_takes_its_reminders_out_of_every_list(
    client, specialist, other_specialist, today
):
    matter = factories.MatterFactory(owner=other_specialist, title="Hiljem piiratud teema")
    due = _reminder(specialist, matter, today)
    read = _reminder(specialist, matter, today)
    personal.mark_read(read)
    ahead = _reminder(specialist, matter, today + timedelta(days=3))
    assert inbox.inbox_count(specialist, today) == 1

    _hide(matter)
    assert not Matter.objects.visible_to(specialist).filter(pk=matter.pk).exists()

    assert inbox.inbox_count(specialist, today) == 0
    assert _personal_items(specialist, today) == []
    assert list(personal.upcoming(specialist, today)) == []
    assert not [
        item for item in feed.history_page(specialist).items if item.type == inbox.TYPE_PERSONAL
    ]
    assert feed.lookup(specialist, inbox.TYPE_PERSONAL, read.pk) is None
    client.force_login(specialist)
    assert "Hiljem piiratud teema" not in client.get(reverse("notifications:list")).content.decode()
    for name, row in (
        ("personal_read", due),
        ("personal_unread", read),
        ("personal_remove", ahead),
    ):
        assert client.post(_url(name, row), {"tagasi": "teavitused"}).status_code == 404


def test_no_change_event_is_written(client, specialist, today):
    matter = factories.MatterFactory(owner=specialist)
    client.force_login(specialist)
    events = ChangeEvent.objects.count()

    client.post(_create_url(matter), {"remind_on": _written(today), "tagasi": "teavitused"})
    row = PersonalReminder.objects.get()
    client.post(_url("personal_read", row), {"tagasi": "teavitused"})
    client.post(_url("personal_unread", row), {"tagasi": "teavitused"})
    acknowledgement.mark_all_read(specialist, until=timezone.now(), today=today)
    client.post(_url("personal_remove", row), {"tagasi": "teavitused"})

    assert ChangeEvent.objects.count() == events
    assert PersonalReminder.objects.count() == 0


# ---------------------------------------------------------------------------
# The row's «!» marker and the ⋯ menu entry
# ---------------------------------------------------------------------------


def _dated_matter(owner, today, title: str):
    matter = create_matter(title=title, owner=owner, reference_year=2026)
    set_next_action(
        matter=matter,
        text="Valmistan ette vastuse",
        kind=ActionKind.DO,
        date_semantics=DateSemantics.DEADLINE,
        target_date=today + timedelta(days=3),
        actor=owner,
    )
    return matter


def test_own_minu_asjad_marks_the_row_with_a_due_reminder(client, specialist, today):
    matter = _dated_matter(specialist, today, "Meeldetuletusega rida")
    _reminder(specialist, matter, today, note="Ära unusta")
    client.force_login(specialist)

    html = client.get(reverse("matters:my_work")).content.decode()

    assert "Meeldetuletusega rida" in html
    assert "rownotice__mark" in html
    assert "Ära unusta" in html
    assert "Lisa meeldetuletus" in html
    assert _create_url(matter) in html
    assert 'value="minu_asjad_rida"' in html


def test_own_minu_asjad_has_no_marker_without_an_unread_item(client, specialist, today):
    matter = _dated_matter(specialist, today, "Rida ilma teavituseta")
    _reminder(specialist, matter, today + timedelta(days=1))
    client.force_login(specialist)

    html = client.get(reverse("matters:my_work")).content.decode()

    assert "Rida ilma teavituseta" in html
    assert 'class="rownotice"' in html
    assert "rownotice__mark" not in html


def test_a_colleagues_desk_has_no_marker_and_no_reminder_menu(
    client, specialist, department_head, today
):
    matter = _dated_matter(specialist, today, "Kolleegi laua rida")
    # Both people have a due reminder on the row's Teema; neither shows here.
    _reminder(specialist, matter, today)
    _reminder(department_head, matter, today)
    client.force_login(department_head)

    response = client.get(reverse("matters:person_work", kwargs={"pk": specialist.pk}))

    assert response.status_code == 200
    html = response.content.decode()
    assert "Kolleegi laua rida" in html
    assert "rownotice" not in html
    assert "Lisa meeldetuletus" not in html
    assert _create_url(matter) not in html
