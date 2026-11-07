"""UI-01: one recipient-scoped read state for the bell, its panel, `/teavitused/` and Minu asjad.

The owner's brief of 2026-11-06 (§4) in tests:

* opening the panel, and paging it, is a GET and marks nothing read;
* reading, unreading and «Märgi kõik loetuks» are CSRF POSTs that take the
  reader from the session and change only that reader's own state;
* reading is not doing the work: no deadline, current step or Teema lifecycle
  moves, and nobody else's rows change;
* «Märgi kõik loetuks» covers the pages the panel never loaded and spares what
  arrived after the boundary the panel was drawn with — including what a
  running generator had written but not yet committed;
* a re-poll does not bring a read notice back, while a genuinely new interval
  or date still notifies;
* the Minu asjad block lists only what still awaits the reader; the panel and
  the page keep the history;
* under the shared gate, persona X never sees or changes persona Y's state.
"""

from __future__ import annotations

import threading
from datetime import UTC, date, datetime, timedelta

import pytest
from django.db import connection, connections
from django.test import Client
from django.urls import reverse

from app.audit.models import ChangeEvent
from app.matters.models import Matter, MatterAssignmentNotice
from app.matters.services import assign_matter
from app.notifications import acknowledgement, deadlines, feed, inbox
from app.notifications.enums import NotificationKind, NotificationPurpose, ReminderStatus
from app.notifications.models import (
    CommencementReminder,
    CommencementReminderPersonalState,
    DeadlineNotification,
)
from app.notifications.services import reconcile_commencement_reminders
from app.workflow.enums import ActionKind, ActionStatus, DatePrecision, DateSemantics
from app.workflow.models import NextAction
from tests import factories
from tests.gate import PASSWORD, apply_shared_gate

pytestmark = pytest.mark.django_db

#: A Monday; the clock below starts on it, at 11:00 in Tallinn.
TODAY = date(2026, 11, 2)
START = datetime(2026, 11, 2, 9, 0, tzinfo=UTC)

HX = {"HX-Request": "true"}


class Clock:
    """`timezone.now()` that moves only when a test says so."""

    def __init__(self) -> None:
        self.now = START

    def tick(self, seconds: int = 1) -> datetime:
        self.now += timedelta(seconds=seconds)
        return self.now


@pytest.fixture
def clock(monkeypatch):
    moment = Clock()
    monkeypatch.setattr("django.utils.timezone.now", lambda: moment.now)
    # `ChangeEvent.occurred_at` took `timezone.now` itself as its default when
    # the model was defined, so the patch above does not reach it.
    occurred_at = ChangeEvent._meta.get_field("occurred_at")
    monkeypatch.setattr(occurred_at, "_get_default", lambda: moment.now)
    return moment


def _opinion(owner, deadline: date, title: str) -> Matter:
    return factories.MatterFactory(owner=owner, response_deadline=deadline, title=title)


def _run(day: date = TODAY) -> None:
    deadlines.reconcile_deadline_notifications(day)
    reconcile_commencement_reminders(day)


def _world(owner, colleague, clock) -> dict[str, object]:
    """One of each kind of item for ``owner``: an INFO and an ACTION deadline
    notice, a commencement check and an «Uus asi»."""
    info = _opinion(owner, TODAY + timedelta(days=3), "Läheneva tähtajaga teema")
    action = _opinion(owner, TODAY, "Tänase tähtajaga teema")
    commencing = factories.MatterFactory(owner=owner, title="Jõustuv teema")
    factories.EffectiveDateFactory(matter=commencing, date_value=TODAY, period_end=TODAY)
    handed = factories.MatterFactory(owner=None, title="Saabunud teema")
    assign_matter(matter=handed, owner=owner, actor=colleague)
    clock.tick()
    _run()
    clock.tick()
    return {"info": info, "action": action, "commencing": commencing, "handed": handed}


def _row(matter: Matter, recipient) -> DeadlineNotification:
    return DeadlineNotification.objects.get(matter=matter, recipient=recipient)


def _reminder(matter: Matter) -> CommencementReminder:
    return CommencementReminder.objects.get(effective_date__matter=matter)


def _notice(matter: Matter) -> MatterAssignmentNotice:
    return MatterAssignmentNotice.objects.get(matter=matter)


def _state_fingerprint() -> list[tuple]:
    """Every read/unread stamp the four surfaces read, for every account."""
    return [
        *DeadlineNotification.objects.order_by("pk").values_list(
            "pk", "read_at", "dismissed_at", "retired_at"
        ),
        *CommencementReminderPersonalState.objects.order_by("pk").values_list(
            "pk", "read_at", "deferred_until"
        ),
        *MatterAssignmentNotice.objects.order_by("pk").values_list(
            "pk", "viewed_at", "superseded_at"
        ),
    ]


def _business_fingerprint() -> list[tuple]:
    """What reading must never move: the work, the Teemad, the shared prompts, the audit."""
    return [
        *Matter.all_objects.order_by("pk").values_list(
            "pk", "owner_id", "response_deadline", "is_open", "updated_at"
        ),
        *NextAction.objects.order_by("pk").values_list("pk", "status", "target_date", "updated_at"),
        *CommencementReminder.objects.order_by("pk").values_list(
            "pk", "status", "deferred_until", "resolved_at"
        ),
        ("events", ChangeEvent.objects.count()),
    ]


def _panel(client) -> str:
    return client.get(reverse("notifications:panel")).content.decode()


def _boundary_from(html: str) -> str:
    marker = 'name="kuni" value="'
    start = html.index(marker) + len(marker)
    return html[start : html.index('"', start)]


# ---------------------------------------------------------------------------
# Opening is not reading
# ---------------------------------------------------------------------------


def test_opening_and_paging_the_panel_marks_nothing_read(
    client, specialist, other_specialist, clock
):
    world = _world(specialist, other_specialist, clock)
    for number in range(feed.PAGE_SIZE):
        assign_matter(
            matter=factories.MatterFactory(owner=None, title=f"Lisateema {number}"),
            owner=specialist,
            actor=other_specialist,
        )
    before_state, before_business = _state_fingerprint(), _business_fingerprint()
    before_count = inbox.inbox_count(specialist)
    client.force_login(specialist)

    html = _panel(client)
    page = feed.panel_page(specialist)
    assert page.cursor is not None
    further = client.get(reverse("notifications:panel"), {"enne": page.next})
    client.get(reverse("notifications:list"))
    client.get(reverse("notifications:bell"))
    client.get(reverse("notifications:block"))
    client.get(reverse("matters:matter_detail", kwargs={"pk": world["action"].pk}))

    assert further.status_code == 200
    assert html.count("data-notif-state=") == feed.PAGE_SIZE
    assert _state_fingerprint() == before_state
    assert _business_fingerprint() == before_business
    assert inbox.inbox_count(specialist) == before_count


def test_the_panel_lists_unread_first_then_the_history_and_its_links(
    client, specialist, other_specialist, clock
):
    world = _world(specialist, other_specialist, clock)
    deadlines.mark_read(notification=_row(world["info"], specialist))
    client.force_login(specialist)

    html = _panel(client)

    assert "Märgi kõik loetuks" in html
    assert reverse("notifications:settings") in html
    assert reverse("notifications:list") in html and "Kõik teavitused" in html
    unread_at = html.index("Tänase tähtajaga teema")
    read_at = html.index("Läheneva tähtajaga teema")
    assert unread_at < read_at
    assert html.count('data-notif-state="unread"') == inbox.inbox_count(specialist) == 3
    assert html.count('data-notif-state="read"') == 1
    # Each item opens its own Teema at the right section; «Uus asi» through its POST.
    assert f'href="{reverse("matters:matter_detail", kwargs={"pk": world["action"].pk})}' in html
    assert (
        reverse("matters:matter_detail", kwargs={"pk": world["commencing"].pk}) + "#joustumine"
        in html
    )
    notice = _notice(world["handed"])
    assert reverse("matters:open_assignment_notice", kwargs={"notice_id": notice.pk}) in html


# ---------------------------------------------------------------------------
# Reading is not doing the work
# ---------------------------------------------------------------------------


def test_reading_an_action_notice_leaves_the_bell_and_block_and_stays_in_the_history(
    client, specialist, other_specialist, clock
):
    world = _world(specialist, other_specialist, clock)
    row = _row(world["action"], specialist)
    assert row.kind == NotificationKind.ACTION
    client.force_login(specialist)
    before = _business_fingerprint()

    response = client.post(
        reverse("notifications:read", kwargs={"notification_id": row.pk}),
        {"tagasi": "paneel"},
        headers=HX,
    )

    assert response.status_code == 200
    assert 'data-notif-state="read"' in response.content.decode()
    assert "Märgi lugemata" in response.content.decode()
    assert set(response["HX-Trigger"].split(", ")) == {
        "notificationsChanged",
        "notificationsSidebarStale",
        "notificationsPageStale",
    }
    assert inbox.inbox_count(specialist) == 3
    block = client.get(reverse("notifications:block")).content.decode()
    assert "Tänase tähtajaga teema" not in block
    listing = client.get(reverse("notifications:list")).content.decode()
    history = listing[listing.index('id="varasemad"') :]
    assert "Tänase tähtajaga teema" in history
    assert _business_fingerprint() == before
    world["action"].refresh_from_db()
    assert world["action"].response_deadline == TODAY


def test_reading_unreading_and_mark_all_on_an_overdue_escalation_moves_no_work(
    client, specialist, department_head, other_specialist, clock
):
    """The management notice the morning after a missed step deadline (ADR 0198 §3)."""
    feature_matter = factories.MatterFactory(owner=None)
    DeadlineNotification.objects.create(
        recipient=factories.UserFactory(is_active=False),
        matter=feature_matter,
        category="OPINION_DEADLINE",
        source_type="MATTER",
        source_id=feature_matter.pk,
        source_date=TODAY - timedelta(days=10),
        purpose=NotificationPurpose.ON_DAY,
        kind=NotificationKind.ACTION,
        due_on=TODAY - timedelta(days=10),
        retired_at=START - timedelta(days=10),
        retired_reason="INELIGIBLE",
    )
    DeadlineNotification.objects.update(created_at=START - timedelta(days=10))
    matter = factories.MatterFactory(owner=specialist, title="Hilinenud samm")
    step = factories.NextActionFactory(
        matter=matter,
        target_date=TODAY - timedelta(days=1),
        kind=ActionKind.DO,
        date_semantics=DateSemantics.DEADLINE,
        date_precision=DatePrecision.EXACT,
        status=ActionStatus.OPEN,
    )
    clock.tick()
    _run()
    clock.tick()
    escalation = DeadlineNotification.objects.get(
        recipient=department_head, purpose=NotificationPurpose.ESCALATION
    )
    owners_rows = list(
        DeadlineNotification.objects.filter(recipient=specialist).values_list("pk", "read_at")
    )
    before = _business_fingerprint()
    client.force_login(department_head)

    client.post(reverse("notifications:read", kwargs={"notification_id": escalation.pk}))
    clock.tick()
    client.post(reverse("notifications:unread", kwargs={"notification_id": escalation.pk}))
    clock.tick()
    boundary = _boundary_from(_panel(client))
    clock.tick()
    client.post(reverse("notifications:mark_all_read"), {"kuni": boundary, "tagasi": "paneel"})

    escalation.refresh_from_db()
    assert escalation.read_at is not None and escalation.retired_at is None
    assert _business_fingerprint() == before
    step.refresh_from_db()
    assert (step.status, step.target_date) == (ActionStatus.OPEN, TODAY - timedelta(days=1))
    assert (
        list(DeadlineNotification.objects.filter(recipient=specialist).values_list("pk", "read_at"))
        == owners_rows
    )


def test_unread_puts_each_kind_back_in_the_inbox(client, specialist, other_specialist, clock):
    world = _world(specialist, other_specialist, clock)
    client.force_login(specialist)
    row = _row(world["info"], specialist)
    reminder = _reminder(world["commencing"])
    notice = _notice(world["handed"])
    reads = [
        reverse("notifications:read", kwargs={"notification_id": row.pk}),
        reverse("notifications:reminder_read", kwargs={"pk": reminder.pk}),
        reverse("notifications:assignment_read", kwargs={"notice_id": notice.pk}),
    ]
    unreads = [
        reverse("notifications:unread", kwargs={"notification_id": row.pk}),
        reverse("notifications:reminder_unread", kwargs={"pk": reminder.pk}),
        reverse("notifications:assignment_unread", kwargs={"notice_id": notice.pk}),
    ]

    for url in reads:
        assert client.post(url, {"tagasi": "teavitused"}).status_code == 302
    assert inbox.inbox_count(specialist) == 1
    notice.refresh_from_db()
    assert notice.viewed_at is not None
    reminder.refresh_from_db()
    assert reminder.status == ReminderStatus.OPEN

    for url in unreads:
        clock.tick()
        assert client.post(url, {"tagasi": "teavitused"}).status_code == 302
    assert inbox.inbox_count(specialist) == 4
    assert {item.type for item in inbox.inbox_for(specialist)} == {
        inbox.TYPE_DEADLINE,
        inbox.TYPE_COMMENCEMENT,
        inbox.TYPE_ASSIGNMENT,
    }


def test_a_read_uus_asi_stays_read_when_a_newer_one_about_the_teema_awaits(
    specialist, other_specialist, clock
):
    matter = factories.MatterFactory(owner=None, title="Kaks korda üle antud")
    assign_matter(matter=matter, owner=specialist, actor=other_specialist)
    old = MatterAssignmentNotice.objects.get(matter=matter)
    acknowledgement.mark_read(specialist, inbox.TYPE_ASSIGNMENT, old)
    MatterAssignmentNotice.objects.create(
        matter=matter, recipient=specialist, assigned_by=other_specialist
    )
    old.refresh_from_db()

    assert acknowledgement.mark_unread(specialist, inbox.TYPE_ASSIGNMENT, old) is False
    old.refresh_from_db()
    assert old.viewed_at is not None
    assert inbox.inbox_count(specialist) == 1


# ---------------------------------------------------------------------------
# «Märgi kõik loetuks»
# ---------------------------------------------------------------------------


def test_mark_all_reaches_unloaded_pages_and_spares_what_arrived_after_the_boundary(
    client, specialist, other_specialist, clock
):
    world = _world(specialist, other_specialist, clock)
    for number in range(feed.PAGE_SIZE + 5):
        assign_matter(
            matter=factories.MatterFactory(owner=None, title=f"Varasem teema {number}"),
            owner=specialist,
            actor=other_specialist,
        )
    clock.tick()
    client.force_login(specialist)
    html = _panel(client)
    assert html.count("data-notif-state=") == feed.PAGE_SIZE
    boundary = _boundary_from(html)

    # Arrives while the panel is open: a hand-over, and a notice made unread again.
    clock.tick()
    late = factories.MatterFactory(owner=None, title="Hiljem saabunud teema")
    assign_matter(matter=late, owner=specialist, actor=other_specialist)
    row = _row(world["info"], specialist)
    deadlines.mark_read(notification=row)
    clock.tick()
    deadlines.mark_unread(notification=row)
    clock.tick()

    response = client.post(
        reverse("notifications:mark_all_read"), {"kuni": boundary, "tagasi": "paneel"}, headers=HX
    )

    assert response.status_code == 200
    body = response.content.decode()
    # 3 of the world (its INFO notice became unread after the boundary) + 20 hand-overs.
    assert "Loetuks märgitud: 23." in body
    assert "Lugemata jäi 2" in body
    remaining = {item.title for item in inbox.inbox_for(specialist)}
    assert remaining == {"Hiljem saabunud teema", "Läheneva tähtajaga teema"}
    # The answer is a fresh panel, with a fresh boundary that now covers them.
    assert _boundary_from(body) != boundary


def test_mark_all_spares_a_commencement_check_handed_over_after_the_boundary(
    client, specialist, other_specialist, clock
):
    matter = factories.MatterFactory(owner=other_specialist, title="Üle antud jõustumine")
    factories.EffectiveDateFactory(matter=matter, date_value=TODAY, period_end=TODAY)
    _run()
    clock.tick()
    client.force_login(specialist)
    boundary = _boundary_from(_panel(client))
    clock.tick()
    assign_matter(matter=matter, owner=specialist, actor=other_specialist)
    clock.tick()

    client.post(reverse("notifications:mark_all_read"), {"kuni": boundary, "tagasi": "paneel"})

    assert [item.type for item in inbox.inbox_for(specialist)] == [
        inbox.TYPE_COMMENCEMENT,
        inbox.TYPE_ASSIGNMENT,
    ] or {item.type for item in inbox.inbox_for(specialist)} == {
        inbox.TYPE_COMMENCEMENT,
        inbox.TYPE_ASSIGNMENT,
    }


def test_mark_all_reads_a_check_whose_teema_was_only_edited_after_the_boundary(
    client, specialist, other_specialist, clock
):
    """Arrival is the Teema changing hands, not any edit to it (review F-5)."""
    matter = factories.MatterFactory(owner=specialist, title="Muudetud jõustumine")
    factories.EffectiveDateFactory(matter=matter, date_value=TODAY, period_end=TODAY)
    _run()
    clock.tick()
    client.force_login(specialist)
    boundary = _boundary_from(_panel(client))
    clock.tick()
    Matter.objects.filter(pk=matter.pk).update(updated_at=clock.now, title="Uus pealkiri")
    clock.tick()

    response = client.post(
        reverse("notifications:mark_all_read"), {"kuni": boundary, "tagasi": "paneel"}, headers=HX
    )

    assert "Loetuks märgitud: 1." in response.content.decode()
    assert inbox.inbox_count(specialist) == 0


def test_mark_all_without_a_valid_boundary_is_refused_and_marks_nothing(
    client, specialist, other_specialist, clock
):
    _world(specialist, other_specialist, clock)
    client.force_login(specialist)
    before = _state_fingerprint()

    for value in ("", "eile", "2026-11-02T09:00:00"):  # missing, garbage, naive
        plain = client.post(reverse("notifications:mark_all_read"), {"kuni": value})
        assert plain.status_code == 302
        htmx = client.post(
            reverse("notifications:mark_all_read"), {"kuni": value, "tagasi": "paneel"}, headers=HX
        )
        assert htmx.status_code == 400
        assert acknowledgement.BOUNDARY_REFUSED in htmx.content.decode()

    assert _state_fingerprint() == before


def test_a_boundary_from_the_future_is_clamped_to_now(specialist, other_specialist, clock):
    _world(specialist, other_specialist, clock)
    future = (clock.now + timedelta(days=365)).isoformat()

    assert acknowledgement.decode_boundary(future) == clock.now


def test_mark_all_from_the_list_page_redirects_with_its_count(
    client, specialist, other_specialist, clock
):
    _world(specialist, other_specialist, clock)
    client.force_login(specialist)
    listing = client.get(reverse("notifications:list")).content.decode()
    clock.tick()

    response = client.post(
        reverse("notifications:mark_all_read"),
        {"kuni": _boundary_from(listing), "tagasi": "teavitused"},
        follow=True,
    )

    assert response.redirect_chain[-1][0] == reverse("notifications:list")
    assert "Loetuks märgitud: 4." in response.content.decode()
    assert inbox.inbox_count(specialist) == 0


@pytest.mark.django_db(transaction=True, serialized_rollback=True)
@pytest.mark.parametrize("generator", [True, False], ids=["generator", "any-transaction"])
def test_the_boundary_stops_at_a_transaction_that_has_not_committed(
    specialist, other_specialist, generator
):
    """Rows another transaction has written are invisible yet carry an earlier time.

    A second connection — here the deadline generator, holding its lock —
    writes a row, uncommitted. The boundary drawn meanwhile ends at that
    transaction's start, so a «Märgi kõik loetuks» made after it commits leaves
    the row unread. Any open transaction counts, not only a generator's.
    """
    matter = _opinion(specialist, TODAY + timedelta(days=30), "Taustal loodud teema")
    holding = threading.Event()
    release = threading.Event()
    failures: list[BaseException] = []

    def worker() -> None:
        try:
            from django.db import transaction

            with transaction.atomic():
                with connection.cursor() as cursor:
                    if generator:
                        cursor.execute(
                            "SELECT pg_advisory_xact_lock(%s, %s)", list(deadlines.DEADLINE_LOCK)
                        )
                    else:
                        cursor.execute("SELECT 1")
                DeadlineNotification.objects.create(
                    recipient=specialist,
                    matter=matter,
                    category="OPINION_DEADLINE",
                    source_type="MATTER",
                    source_id=matter.pk,
                    source_date=matter.response_deadline,
                    purpose=NotificationPurpose.ADVANCE,
                    offset_days=14,
                    kind=NotificationKind.INFO,
                    due_on=matter.response_deadline - timedelta(days=14),
                )
                holding.set()
                release.wait(timeout=30)
        except BaseException as error:  # pragma: no cover - reported below
            failures.append(error)
            holding.set()
        finally:
            connections.close_all()

    thread = threading.Thread(target=worker)
    thread.start()
    try:
        assert holding.wait(timeout=30)
        drawn = acknowledgement.boundary()
        assert DeadlineNotification.objects.filter(recipient=specialist).count() == 0
    finally:
        release.set()
        thread.join(timeout=30)
    assert not failures
    row = DeadlineNotification.objects.get(recipient=specialist)
    assert row.created_at > drawn

    acknowledgement.mark_all_read(specialist, until=drawn)

    row.refresh_from_db()
    assert row.read_at is None
    # And with nothing running, the boundary is simply now.
    assert acknowledgement.boundary() >= row.created_at


# ---------------------------------------------------------------------------
# Re-polling
# ---------------------------------------------------------------------------


def test_a_repoll_does_not_bring_a_read_notice_back_but_a_new_interval_notifies(
    specialist, monkeypatch
):
    deadline = TODAY + timedelta(days=7)
    _opinion(specialist, deadline, "Seitsme päeva teema")
    _run(TODAY)
    (seven,) = inbox.inbox_for(specialist, TODAY)
    row = DeadlineNotification.objects.get(pk=seven.pk)
    assert row.offset_days == 7
    deadlines.mark_read(notification=row)

    for day in (TODAY, TODAY + timedelta(days=1), TODAY + timedelta(days=3)):
        _run(day)
        assert inbox.inbox_count(specialist, day) == 0
    row.refresh_from_db()
    assert row.read_at is not None

    # Three days to go: the 3-day interval is a new delivery and is unread.
    later = TODAY + timedelta(days=4)
    _run(later)
    (three,) = inbox.inbox_for(specialist, later)
    assert three.pk != row.pk
    assert DeadlineNotification.objects.get(pk=three.pk).offset_days == 3


def test_a_repoll_keeps_a_read_check_read_and_a_moved_date_asks_again(
    specialist, other_specialist, clock
):
    matter = factories.MatterFactory(owner=specialist, title="Jõustumise teema")
    record = factories.EffectiveDateFactory(matter=matter, date_value=TODAY, period_end=TODAY)
    _run()
    reminder = _reminder(matter)
    acknowledgement.mark_read(specialist, inbox.TYPE_COMMENCEMENT, reminder)

    _run()
    _run()
    assert inbox.inbox_count(specialist) == 0

    record.date_value = record.period_end = TODAY - timedelta(days=1)
    record.save()
    _run()
    items = inbox.inbox_for(specialist)
    assert [item.type for item in items] == [inbox.TYPE_COMMENCEMENT]
    assert items[0].pk != reminder.pk


# ---------------------------------------------------------------------------
# The Minu asjad block
# ---------------------------------------------------------------------------


def test_the_block_lists_only_what_awaits_and_vanishes_when_all_is_handled(
    client, specialist, other_specialist, clock
):
    world = _world(specialist, other_specialist, clock)
    client.force_login(specialist)
    row = _row(world["info"], specialist)

    response = client.post(
        reverse("notifications:read", kwargs={"notification_id": row.pk}),
        {"tagasi": "minu_asjad"},
        headers=HX,
    )

    assert response.status_code == 200
    assert response["HX-Trigger"] == "notificationsChanged"
    assert "Läheneva tähtajaga teema" not in response.content.decode()
    assert response.content.decode().count("data-inbox-item") == 3
    page = client.get(reverse("matters:my_work")).content.decode()
    assert 'id="notif-sidebar"' in page

    clock.tick()
    boundary = _boundary_from(_panel(client))
    clock.tick()
    client.post(reverse("notifications:mark_all_read"), {"kuni": boundary})

    assert client.get(reverse("notifications:block")).content.decode().strip() == ""
    page = client.get(reverse("matters:my_work")).content.decode()
    assert 'aria-label="Teavitused"' not in page
    assert "data-inbox-item" not in page


def test_a_refused_postponement_from_the_block_says_why_in_place(
    client, specialist, other_specialist, clock
):
    world = _world(specialist, other_specialist, clock)
    reminder = _reminder(world["commencing"])
    client.force_login(specialist)

    response = client.post(
        reverse("notifications:defer", kwargs={"pk": reminder.pk}),
        {"tagasi": "minu_asjad", "until": "01.01.2020"},
        headers=HX,
    )

    assert response.status_code == 400
    assert 'role="alert"' in response.content.decode()
    assert (
        CommencementReminderPersonalState.objects.filter(deferred_until__isnull=False).count() == 0
    )


def test_a_postponement_from_the_panel_answers_in_place(
    client, specialist, other_specialist, clock
):
    world = _world(specialist, other_specialist, clock)
    reminder = _reminder(world["commencing"])
    client.force_login(specialist)

    response = client.post(
        reverse("notifications:defer", kwargs={"pk": reminder.pk}),
        {"tagasi": "paneel", "until": "05.11.2026"},
        headers=HX,
    )

    assert response.status_code == 200
    body = response.content.decode()
    assert 'data-notif-state="gone"' in body
    assert "kuni 5.11.2026" in body
    assert inbox.TYPE_COMMENCEMENT not in {item.type for item in inbox.inbox_for(specialist)}


# ---------------------------------------------------------------------------
# Whose state, and how it is asked for
# ---------------------------------------------------------------------------


def test_another_persons_items_are_404s_and_mark_all_ignores_a_named_user(
    client, specialist, other_specialist, clock
):
    world = _world(specialist, other_specialist, clock)
    row = _row(world["info"], specialist)
    reminder = _reminder(world["commencing"])
    notice = _notice(world["handed"])
    before = _state_fingerprint()
    client.force_login(other_specialist)

    for url in (
        reverse("notifications:read", kwargs={"notification_id": row.pk}),
        reverse("notifications:unread", kwargs={"notification_id": row.pk}),
        reverse("notifications:reminder_read", kwargs={"pk": reminder.pk}),
        reverse("notifications:reminder_unread", kwargs={"pk": reminder.pk}),
        reverse("notifications:assignment_read", kwargs={"notice_id": notice.pk}),
        reverse("notifications:assignment_unread", kwargs={"notice_id": notice.pk}),
    ):
        assert client.post(url, {"tagasi": "paneel"}, headers=HX).status_code == 404, url
    boundary = _boundary_from(_panel(client))
    clock.tick()
    client.post(
        reverse("notifications:mark_all_read"),
        {"kuni": boundary, "user_id": str(specialist.pk), "recipient": str(specialist.pk)},
    )

    assert _state_fingerprint() == before
    assert inbox.inbox_count(specialist) == 4


def test_the_writes_are_csrf_protected_posts(specialist, other_specialist, clock):
    world = _world(specialist, other_specialist, clock)
    row = _row(world["info"], specialist)
    strict = Client(enforce_csrf_checks=True)
    strict.force_login(specialist)

    for url in (
        reverse("notifications:read", kwargs={"notification_id": row.pk}),
        reverse("notifications:unread", kwargs={"notification_id": row.pk}),
        reverse("notifications:mark_all_read"),
    ):
        assert strict.post(url, {"kuni": clock.now.isoformat()}).status_code == 403
        assert strict.get(url).status_code == 405
    row.refresh_from_db()
    assert row.read_at is None


def test_an_administrator_without_a_business_role_has_an_empty_panel(client, administrator, clock):
    client.force_login(administrator)

    html = _panel(client)

    assert "Teavitusi ei ole." in html
    assert acknowledgement.mark_all_read(administrator, until=clock.now).marked == 0


def test_a_malformed_page_cursor_is_refused(client, specialist):
    client.force_login(specialist)

    huge = "1-" + "9" * 18 + "-0-" + "0" * 32  # past what a datetime holds
    for value in ("", "x", "2-1-0-00", "0-1-9-" + "0" * 32, "0-12-0-zz", huge):
        assert client.get(reverse("notifications:panel"), {"enne": value}).status_code == 400
    assert feed.Cursor.decode(huge) is None


def test_a_malformed_history_cursor_falls_back_to_the_first_page(
    client, specialist, other_specialist, clock
):
    world = _world(specialist, other_specialist, clock)
    deadlines.mark_read(notification=_row(world["info"], specialist))
    client.force_login(specialist)

    for value in ("x", "1-" + "9" * 18 + "-0-" + "0" * 32):
        response = client.get(reverse("notifications:list"), {"varasemad": value})
        assert response.status_code == 200
        assert "Läheneva tähtajaga teema" in response.content.decode().split('id="varasemad"')[1]


def test_paging_walks_every_item_once_unread_first(specialist, other_specialist, clock):
    for number in range(feed.PAGE_SIZE + 4):
        clock.tick()
        assign_matter(
            matter=factories.MatterFactory(owner=None, title=f"Teema {number:02}"),
            owner=specialist,
            actor=other_specialist,
        )
    for notice in MatterAssignmentNotice.objects.order_by("created_at")[:6]:
        acknowledgement.mark_read(specialist, inbox.TYPE_ASSIGNMENT, notice)
    seen: list[str] = []
    states: list[str] = []
    cursor = None
    for _ in range(5):
        page = feed.panel_page(specialist, cursor=cursor, limit=7)
        seen += [item.title for item in page.items]
        states += [item.state for item in page.items]
        cursor = page.cursor
        if cursor is None:
            break

    assert sorted(seen) == sorted(f"Teema {number:02}" for number in range(feed.PAGE_SIZE + 4))
    assert len(seen) == len(set(seen))
    assert states == [inbox.STATE_UNREAD] * 13 + [inbox.STATE_READ] * 6
    unread_titles = seen[:13]
    assert unread_titles == sorted(unread_titles, reverse=True)


def test_a_restricted_teema_takes_its_history_with_it(specialist, other_specialist, clock):
    world = _world(specialist, other_specialist, clock)
    row = _row(world["info"], specialist)
    deadlines.mark_read(notification=row)
    assert "Läheneva tähtajaga teema" in {
        item.title for item in feed.history_page(specialist).items
    }

    Matter.all_objects.filter(pk=world["info"].pk).update(deleted_at=clock.now)

    assert "Läheneva tähtajaga teema" not in {
        item.title for item in feed.history_page(specialist).items
    }
    assert feed.lookup(specialist, inbox.TYPE_DEADLINE, row.pk) is None


# ---------------------------------------------------------------------------
# The shared gate: one persona never reaches another's state
# ---------------------------------------------------------------------------


def test_personas_behind_the_shared_gate_keep_their_own_state(
    client, settings, specialist, other_specialist, clock
):
    apply_shared_gate(settings, PASSWORD)
    world = _world(specialist, other_specialist, clock)
    theirs = _opinion(other_specialist, TODAY + timedelta(days=3), "Kolleegi oma teema")
    _run()
    clock.tick()
    assert client.post(reverse("accounts:shared_gate"), {"password": PASSWORD}).status_code == 302

    # Persona Y (the colleague): reads everything they have.
    client.post(reverse("accounts:act_as"), {"user_id": str(other_specialist.pk)})
    panel = _panel(client)
    assert "Kolleegi oma teema" in panel
    for title in ("Läheneva tähtajaga teema", "Tänase tähtajaga teema", "Saabunud teema"):
        assert title not in panel
    clock.tick()
    client.post(
        reverse("notifications:mark_all_read"),
        {"kuni": _boundary_from(panel), "user_id": str(specialist.pk)},
    )
    assert _row(theirs, other_specialist).read_at is not None
    assert inbox.inbox_count(specialist) == 4

    # Persona X, in the same browser: their own inbox, untouched.
    client.post(reverse("accounts:act_as"), {"user_id": str(specialist.pk)})
    panel = _panel(client)
    assert "Kolleegi oma teema" not in panel
    assert panel.count('data-notif-state="unread"') == 4
    response = client.post(
        reverse("notifications:read", kwargs={"notification_id": _row(theirs, other_specialist).pk})
    )
    assert response.status_code == 404
    bell = client.get(reverse("notifications:bell"))
    assert bell.context["count"] == 4
    assert world["info"].title in client.get(reverse("notifications:block")).content.decode()


def test_no_notification_template_carries_the_applications_focus_landing():
    """`app.js` takes the first `data-focus-after-save` in the document after a swap
    that destroyed focus; the top-bar panel and the rail block must never be it
    (review F-1). They mark their own landings `data-notif-focus`."""
    from pathlib import Path

    from django.conf import settings

    folder = Path(settings.BASE_DIR) / "templates" / "notifications"
    offenders = [
        path.name
        for path in sorted(folder.glob("*.html"))
        if "data-focus-after-save" in path.read_text(encoding="utf-8")
    ]
    assert offenders == []
    assert "data-notif-focus" in (folder / "_panel.html").read_text(encoding="utf-8")
