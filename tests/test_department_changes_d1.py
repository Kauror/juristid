"""*Muudatused* reads a person's deliberate acts, once each (ADR 0159 §2, D1).

The owner's decision D1 (QA remediation 2026-10-14): Muudatused reflects
meaningful user actions. A step change the system made on a person's behalf is
never attributed to them as deliberate — it is counted aside, marked as the
system's — and one operation never reads as several user decisions:

* sending an opinion is not also «määras järgmise tegevuse» for the 30-day
  check the send schedules (QA-DATESVIEWS-01);
* new work written over a current check is one «määras», not two;
* a planned step that is later made current is «määras» once, when planned;
* one closure that ends N steps is one entry, «sulges teema», not N
  «tühistas» rows in the closer's name.

What stays: every audit row, exactly as written — this is presentation only —
and a person's own reschedule, cancellation and chosen next check.
"""

from __future__ import annotations

from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

import pytest
from django.urls import reverse
from django.utils import timezone

from app.audit.enums import ChangeEventType
from app.audit.models import ChangeEvent
from app.core.enums import Visibility
from app.matters import department_changes as dc
from app.matters.services import close_matter, create_matter, reopen_matter
from app.workflow import follow_ups
from app.workflow.enums import ActionStatus, Disposition, FollowUpOutcome
from app.workflow.models import NextAction
from app.workflow.services import (
    add_planned_action,
    cancel_next_action,
    complete_next_action,
    set_next_action,
    set_next_action_for_new_work,
)
from tests import factories
from tests.follow_ups import active_check, mark_sent

pytestmark = pytest.mark.django_db

TALLINN = ZoneInfo("Europe/Tallinn")
PAGE = "/osakond/muudatused/"


def _tegevused(changes: dc.DepartmentChanges) -> dc.CategoryResult:
    return next(result for result in changes.categories if result.key == "tegevused")


def _breakdown(changes: dc.DepartmentChanges) -> dict[str, int]:
    return dict(_tegevused(changes).breakdown)


@pytest.fixture
def ministry():
    return factories.OrganisationFactory(name="Sünteetiline ministeerium D1")


# ---------------------------------------------------------------------------
# System-scheduled steps are not a person's act — and are marked as the system's
# ---------------------------------------------------------------------------


def test_the_scheduled_check_is_counted_aside_as_the_systems(department_head, specialist, ministry):
    matter = create_matter(title="Saadetud", owner=specialist, actor=specialist)
    opinion = mark_sent(matter, specialist, [ministry])

    changes = dc.build_changes(department_head)

    assert _tegevused(changes).count == 0
    assert changes.coverage.automatic_steps == 1
    # The audit row is untouched: it still names the sender and its marker.
    check = active_check(opinion)
    event = ChangeEvent.objects.get(event_type=ChangeEventType.NEXT_ACTION_SET, object_id=check.pk)
    assert event.actor == specialist
    assert event.payload["follow_up"]["automatic"] is True


def test_the_page_says_the_system_made_it(client, department_head, specialist, ministry):
    matter = create_matter(title="Saadetud lehel", owner=specialist, actor=specialist)
    mark_sent(matter, specialist, [ministry])
    client.force_login(department_head)

    body = client.get(PAGE).content.decode()

    assert "data-automatic-steps" in body
    assert "ei loeta kellegi tegevuseks: <b>1</b>" in body
    assert "määras järgmise tegevuse: " not in body


def test_a_next_check_the_person_chose_is_their_act(department_head, specialist, ministry):
    """«Vastust ei ole — kontrollin uuesti» on a day the lawyer picked is a
    step a person set (``automatic: false``), and finishing the check is theirs."""
    matter = create_matter(title="Kontrollin uuesti", owner=specialist, actor=specialist)
    opinion = mark_sent(matter, specialist, [ministry])
    check = active_check(opinion)
    from app.matters.locks import lock_matter_for_write

    locked = lock_matter_for_write(matter.pk)
    follow_ups.complete_check(
        action=follow_ups.locked_check(locked, check.pk),
        outcome=FollowUpOutcome.NO_RESPONSE,
        actor=specialist,
        next_check_on=timezone.localdate() + timedelta(days=14),
    )

    breakdown = _breakdown(dc.build_changes(department_head))
    assert breakdown["määras järgmise tegevuse"] == 1
    assert breakdown["lõpetas järgmise tegevuse"] == 1


def test_a_person_moving_a_check_is_their_act(department_head, specialist, ministry):
    matter = create_matter(title="Kontroll hiljem", owner=specialist, actor=specialist)
    check = active_check(mark_sent(matter, specialist, [ministry]))

    follow_ups.reschedule_check(
        matter=matter,
        action_id=check.pk,
        target_date=timezone.localdate() + timedelta(days=40),
        actor=specialist,
    )

    breakdown = _breakdown(dc.build_changes(department_head))
    assert breakdown["muutis tegevuse kuupäeva"] == 1
    assert breakdown["määras järgmise tegevuse"] == 0


def test_new_work_over_a_check_names_the_new_step_not_the_check(
    department_head, specialist, ministry
):
    matter = create_matter(title="Kiire töö", owner=specialist, actor=specialist)
    check = active_check(mark_sent(matter, specialist, [ministry]))
    NextAction.objects.filter(pk=check.pk).update(status=ActionStatus.OPEN)

    set_next_action_for_new_work(
        matter=matter,
        text="Uus kiireloomuline töö",
        target_date=timezone.localdate() + timedelta(days=1),
        actor=specialist,
    )

    changes = dc.build_changes(department_head)
    assert _breakdown(changes)["määras järgmise tegevuse"] == 1
    # The scheduled check and its return to the plan, both the system's.
    assert changes.coverage.automatic_steps == 2
    # Both audit rows are still there.
    assert (
        ChangeEvent.objects.filter(
            matter=matter, event_type=ChangeEventType.NEXT_ACTION_SET
        ).count()
        == 3
    )


# ---------------------------------------------------------------------------
# A planned step made current is «määras» once
# ---------------------------------------------------------------------------


def test_a_planned_step_made_current_is_set_once(department_head, specialist):
    matter = create_matter(title="Plaaniga", owner=specialist, actor=specialist)
    current = set_next_action(matter=matter, text="Praegune", actor=specialist)
    planned = add_planned_action(
        matter=matter,
        text="Plaanitud",
        target_date=timezone.localdate() + timedelta(days=20),
        actor=specialist,
    )
    complete_next_action(action=current, actor=specialist)
    planned.refresh_from_db()
    assert planned.status == ActionStatus.OPEN

    changes = dc.build_changes(department_head)

    assert _breakdown(changes)["määras järgmise tegevuse"] == 2
    assert _breakdown(changes)["lõpetas järgmise tegevuse"] == 1
    assert changes.coverage.automatic_steps == 1
    assert (
        ChangeEvent.objects.filter(
            object_id=planned.pk, event_type=ChangeEventType.NEXT_ACTION_SET
        ).count()
        == 2
    )


# ---------------------------------------------------------------------------
# One closure is one entry
# ---------------------------------------------------------------------------


def _with_steps(specialist, title: str, planned: int = 2):
    matter = create_matter(title=title, owner=specialist, actor=specialist)
    set_next_action(matter=matter, text="Praegune", actor=specialist)
    for offset in range(planned):
        add_planned_action(
            matter=matter,
            text=f"Plaan {offset}",
            target_date=timezone.localdate() + timedelta(days=10 + offset),
            actor=specialist,
        )
    return matter


def _hand_written_closure(matter, actor, moment: datetime, steps: int, payload: dict) -> None:
    """A closure's audit rows written by hand at ``moment``, each on a real step.

    Real `NextAction` rows, because a step's event is read only when its step
    is one the reader may see (`scope_change_events`, fail-closed).
    """
    for offset in range(steps):
        step = NextAction.objects.create(
            matter=matter,
            text=f"Samm {offset}",
            status=ActionStatus.CANCELLED,
            ended_at=moment,
        )
        ChangeEvent.objects.create(
            event_type=ChangeEventType.NEXT_ACTION_CANCELLED,
            matter=matter,
            actor=actor,
            object_type="workflow.NextAction",
            object_id=step.pk,
            occurred_at=moment + timedelta(milliseconds=offset),
            payload=payload,
        )
    ChangeEvent.objects.create(
        event_type=ChangeEventType.MATTER_CLOSED,
        matter=matter,
        actor=actor,
        occurred_at=moment + timedelta(milliseconds=steps + 5),
    )


def _closure_rows(changes: dc.DepartmentChanges) -> list[dc.ChangeRow]:
    return [row for row in _tegevused(changes).rows if row.event_type == dc.CLOSURE_ROW]


def test_one_closure_is_one_entry_in_the_closers_name(
    department_head, specialist, other_specialist
):
    matter = _with_steps(specialist, "Suletav kolme sammuga")
    before = _tegevused(dc.build_changes(department_head)).count

    close_matter(
        matter=matter,
        disposition=Disposition.COMPLETED,
        actor=other_specialist,
        follow_ups_confirmed=True,
    )

    changes = dc.build_changes(department_head)
    assert _tegevused(changes).count == before + 1
    assert _breakdown(changes)["tühistas järgmise tegevuse"] == 0
    assert _breakdown(changes)[dc.CLOSURE_VERB] == 1
    [row] = _closure_rows(changes)
    assert (row.verb, row.detail) == ("sulges teema", "lõppes 3 tegevust")
    assert row.actor_name == other_specialist.display_name
    assert row.matter_title == "Suletav kolme sammuga"
    assert row.url == reverse("matters:matter_detail", kwargs={"pk": matter.pk})
    # The audit detail is kept: three cancellations, one closure.
    assert (
        ChangeEvent.objects.filter(
            matter=matter, event_type=ChangeEventType.NEXT_ACTION_CANCELLED
        ).count()
        == 3
    )
    assert (
        ChangeEvent.objects.filter(matter=matter, event_type=ChangeEventType.MATTER_CLOSED).count()
        == 1
    )


def test_a_closure_that_ended_one_step_says_one(department_head, specialist):
    matter = _with_steps(specialist, "Üks samm", planned=0)
    close_matter(
        matter=matter,
        disposition=Disposition.COMPLETED,
        actor=specialist,
        follow_ups_confirmed=True,
    )

    [row] = _closure_rows(dc.build_changes(department_head))
    assert row.detail == "lõppes 1 tegevus"


def test_a_closure_that_ended_nothing_adds_no_entry(department_head, specialist):
    matter = create_matter(title="Tühi", owner=specialist, actor=specialist)
    close_matter(
        matter=matter,
        disposition=Disposition.COMPLETED,
        actor=specialist,
        follow_ups_confirmed=True,
    )

    changes = dc.build_changes(department_head)
    assert _closure_rows(changes) == []
    assert dc.CLOSURE_VERB not in _breakdown(changes)


def test_closing_twice_is_two_entries_and_a_cancel_is_still_a_cancel(department_head, specialist):
    matter = _with_steps(specialist, "Kaks korda", planned=1)
    close_matter(
        matter=matter,
        disposition=Disposition.COMPLETED,
        actor=specialist,
        follow_ups_confirmed=True,
    )
    reopen_matter(matter=matter, actor=specialist)
    own = set_next_action(matter=matter, text="Teine ring", actor=specialist)
    cancel_next_action(action=own, actor=specialist, reason="Ei ole enam vaja")
    set_next_action(matter=matter, text="Kolmas ring", actor=specialist)
    close_matter(
        matter=matter,
        disposition=Disposition.COMPLETED,
        actor=specialist,
        follow_ups_confirmed=True,
    )

    changes = dc.build_changes(department_head)
    assert sorted(row.detail for row in _closure_rows(changes)) == [
        "lõppes 1 tegevus",
        "lõppes 2 tegevust",
    ]
    assert _breakdown(changes)["tühistas järgmise tegevuse"] == 1
    assert _breakdown(changes)[dc.CLOSURE_VERB] == 2


def test_two_teemad_closed_are_two_entries(department_head, specialist):
    first = _with_steps(specialist, "Esimene", planned=1)
    second = _with_steps(specialist, "Teine", planned=1)
    for matter in (first, second):
        close_matter(
            matter=matter,
            disposition=Disposition.COMPLETED,
            actor=specialist,
            follow_ups_confirmed=True,
        )

    rows = _closure_rows(dc.build_changes(department_head))
    assert sorted(row.matter_title for row in rows) == ["Esimene", "Teine"]
    assert [row.detail for row in rows] == ["lõppes 2 tegevust", "lõppes 2 tegevust"]


def test_a_closure_marker_on_the_cancellation_is_read_too(department_head, specialist):
    """Robust to a closure writer that marks its cancellations explicitly."""
    matter = create_matter(title="Märgitud", owner=specialist, actor=specialist)
    _hand_written_closure(
        matter, specialist, timezone.now(), 2, {"reason": "Muu sõnastus", "closure": True}
    )

    [row] = _closure_rows(dc.build_changes(department_head))
    assert row.detail == "lõppes 2 tegevust"


def test_a_closure_on_the_repeated_hour_is_dated_that_tallinn_day(department_head, specialist):
    """25.10.2026 03:30 happens twice in Tallinn; the closure's one row reads
    that day inside a one-day window, and not the day before or after."""
    matter = create_matter(title="Kellakeeramine", owner=specialist, actor=specialist)
    # The second 03:30 (fold=1, EET): 00:30 UTC on the 25th.
    moment = datetime(2026, 10, 25, 3, 30, fold=1, tzinfo=TALLINN)
    _hand_written_closure(matter, specialist, moment, 3, {"reason": dc.CLOSURE_CANCEL_REASON})

    def window(stamp: str):
        return dc.build_changes(
            department_head,
            {"periood": "vahemik", "alates": stamp, "kuni": stamp},
            today=date(2026, 10, 28),
        )

    [row] = _closure_rows(window("25.10.2026"))
    assert row.when == date(2026, 10, 25)
    assert row.detail == "lõppes 3 tegevust"
    assert _closure_rows(window("24.10.2026")) == []
    assert _closure_rows(window("26.10.2026")) == []


def test_a_restricted_step_ended_by_closure_is_not_counted_for_a_reader(reader, specialist):
    """Authorization before arithmetic: the reader's entry counts what they may see."""
    reader.capability_overrides = {
        **(reader.capability_overrides or {}),
        "department.view_management": "allow",
    }
    reader.save(update_fields=["capability_overrides"])
    matter = _with_steps(specialist, "Osaliselt piiratud", planned=1)
    NextAction.objects.filter(matter=matter, status=ActionStatus.PLANNED).update(
        visibility_override=Visibility.RESTRICTED
    )
    close_matter(
        matter=matter,
        disposition=Disposition.COMPLETED,
        actor=specialist,
        follow_ups_confirmed=True,
    )

    [row] = _closure_rows(dc.build_changes(reader))
    assert row.detail == "lõppes 1 tegevus"


def test_closure_entries_are_newest_first_among_the_steps(department_head, specialist):
    matter = _with_steps(specialist, "Järjestus", planned=1)
    close_matter(
        matter=matter,
        disposition=Disposition.COMPLETED,
        actor=specialist,
        follow_ups_confirmed=True,
    )
    later = create_matter(title="Hilisem", owner=specialist, actor=specialist)
    set_next_action(matter=later, text="Pärast sulgemist", actor=specialist)

    rows = _tegevused(dc.build_changes(department_head)).rows
    assert [row.event_type for row in rows][:2] == [
        ChangeEventType.NEXT_ACTION_SET,
        dc.CLOSURE_ROW,
    ]
    assert rows == sorted(rows, key=lambda row: row.at, reverse=True)


def test_specialists_still_get_a_404(client, specialist):
    client.force_login(specialist)
    assert client.get(PAGE).status_code == 404


def _steps(matter, count: int, moment: datetime) -> list[NextAction]:
    return [
        NextAction.objects.create(
            matter=matter, text=f"Samm {n}", status=ActionStatus.CANCELLED, ended_at=moment
        )
        for n in range(count)
    ]


def _cancel(matter, actor, step, moment: datetime) -> None:
    ChangeEvent.objects.create(
        event_type=ChangeEventType.NEXT_ACTION_CANCELLED,
        matter=matter,
        actor=actor,
        object_type="workflow.NextAction",
        object_id=step.pk,
        occurred_at=moment,
        payload={"reason": dc.CLOSURE_CANCEL_REASON},
    )


def _closed(matter, actor, moment: datetime, ended: list[NextAction] | None) -> None:
    payload: dict = {"disposition": Disposition.COMPLETED}
    if ended is not None:
        payload["ended"] = {
            "actions": [str(step.pk) for step in ended],
            "overviews": [],
            "waits": [],
        }
    ChangeEvent.objects.create(
        event_type=ChangeEventType.MATTER_CLOSED,
        matter=matter,
        actor=actor,
        occurred_at=moment,
        payload=payload,
    )


def test_the_closure_that_names_its_steps_is_the_key(department_head, specialist):
    """`MATTER_CLOSED.payload.ended.actions` decides, whatever the order the rows
    were written in: here both closures were recorded *before* their steps'
    cancellations, which the ordering rule alone would read as one closure (the
    second) taking all five, then nothing."""
    matter = create_matter(title="Nimetatud sammud", owner=specialist, actor=specialist)
    start = timezone.now() - timedelta(hours=2)
    first = _steps(matter, 2, start)
    second = _steps(matter, 3, start)
    _closed(matter, specialist, start, first)
    for step in first:
        _cancel(matter, specialist, step, start + timedelta(seconds=1))
    _closed(matter, specialist, start + timedelta(hours=1), second)
    for step in second:
        _cancel(matter, specialist, step, start + timedelta(hours=1, seconds=1))

    rows = _closure_rows(dc.build_changes(department_head))
    assert [row.detail for row in rows] == ["lõppes 3 tegevust", "lõppes 2 tegevust"]


def test_without_the_list_the_ordering_rule_still_groups(department_head, specialist):
    """A closure recorded before `ended` existed: the first closing event at or
    after the cancellations is theirs."""
    matter = create_matter(title="Vana sulgemine", owner=specialist, actor=specialist)
    start = timezone.now() - timedelta(hours=2)
    steps = _steps(matter, 2, start)
    for step in steps:
        _cancel(matter, specialist, step, start)
    _closed(matter, specialist, start + timedelta(milliseconds=5), None)

    [row] = _closure_rows(dc.build_changes(department_head))
    assert row.detail == "lõppes 2 tegevust"


def test_a_real_closure_writes_the_ids_it_ended_or_is_grouped_by_order(department_head, specialist):
    """Through the real service: one closure, one row — whether or not this
    tree's `close_matter` records `ended` yet (C1, docs/adr/0152 §7)."""
    matter = _with_steps(specialist, "Päris sulgemine", planned=2)
    close_matter(
        matter=matter,
        disposition=Disposition.COMPLETED,
        actor=specialist,
        follow_ups_confirmed=True,
    )

    [row] = _closure_rows(dc.build_changes(department_head))
    assert row.detail == "lõppes 3 tegevust"


# ---------------------------------------------------------------------------
# A withdrawal's check cancellation is the withdrawal's consequence
# ---------------------------------------------------------------------------


def test_withdrawing_an_opinion_is_not_a_person_cancelling_its_check(
    department_head, specialist, ministry
):
    """Owner, 2026-10-14: the scheduled check that ends because its opinion was
    withdrawn is not a separate «tühistas» in the withdrawer's name. It is the
    system's, counted aside; the audit row stays."""
    from app.submissions.services import withdraw_submission

    matter = create_matter(title="Tagasi võetud", owner=specialist, actor=specialist)
    opinion = mark_sent(matter, specialist, [ministry])
    check = active_check(opinion)
    before = dc.build_changes(department_head).coverage.automatic_steps

    withdraw_submission(submission=opinion, actor=specialist, reason="Saadeti ekslikult")

    changes = dc.build_changes(department_head)
    assert _breakdown(changes)["tühistas järgmise tegevuse"] == 0
    assert _tegevused(changes).count == 0
    assert changes.coverage.automatic_steps == before + 1
    event = ChangeEvent.objects.get(
        event_type=ChangeEventType.NEXT_ACTION_CANCELLED, object_id=check.pk
    )
    assert event.actor == specialist
    assert "follow_up" in event.payload


def test_a_persons_own_cancellation_still_counts_beside_a_withdrawal(
    department_head, specialist, ministry
):
    from app.submissions.services import withdraw_submission

    matter = create_matter(title="Oma tühistus", owner=specialist, actor=specialist)
    opinion = mark_sent(matter, specialist, [ministry])
    withdraw_submission(submission=opinion, actor=specialist, reason="Saadeti ekslikult")
    own = set_next_action(matter=matter, text="Helistan", actor=specialist)
    cancel_next_action(action=own, actor=specialist, reason="Arvamus võeti tagasi")

    breakdown = _breakdown(dc.build_changes(department_head))
    # Even in the same words: a step that watched no opinion is the person's.
    assert breakdown["tühistas järgmise tegevuse"] == 1
    assert breakdown["määras järgmise tegevuse"] == 1
