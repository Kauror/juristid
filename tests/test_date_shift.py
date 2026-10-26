"""«Nihuta kuupäeva» — an internal task's day, moved by a day, a week or a month.

docs/adr/0143, amendment of 2026-10-26. The control is a faster way into the
ordinary `Muuda`, so the tests that matter are the ones about what it must *not*
do: reassign the step, drop its restriction or plan step, touch a statutory or
recorded date, move a step somebody else changed in another tab, move twice on a
double press, or move anything for somebody who may not write.
"""

from __future__ import annotations

from datetime import date, timedelta

import pytest
from django.urls import reverse
from django.utils import timezone

from app.audit.enums import ChangeEventType
from app.audit.models import ChangeEvent
from app.core.enums import Visibility
from app.matters import date_shift
from app.matters import work_items as wi
from app.workflow.enums import (
    ActionKind,
    ActionStatus,
    DatePrecision,
    DateSemantics,
    Disposition,
)
from app.workflow.follow_ups import schedule_first_check
from app.workflow.models import MatterPlanStep, NextAction
from app.workflow.services import add_planned_action, set_next_action
from tests import factories
from tests.refusals import refused

pytestmark = pytest.mark.django_db

MY_WORK = reverse("matters:my_work")


def current(matter, day, *, text="Saadan arvamuse", actor=None, **extra):
    return set_next_action(
        matter=matter,
        text=text,
        kind=extra.pop("kind", ActionKind.DO),
        date_semantics=extra.pop("date_semantics", DateSemantics.DEADLINE),
        target_date=day,
        actor=actor or matter.owner,
        **extra,
    )


def planned(matter, day, *, text="Hiljem tehtav", actor=None):
    return add_planned_action(
        matter=matter, text=text, target_date=day, actor=actor or matter.owner
    )


def shift(action, key, *, actor=None, source=None, proposed=None):
    return date_shift.shift_action_date(
        matter=action.matter,
        action_id=action.pk,
        actor=actor or action.matter.owner,
        shift=key,
        source_date=source or action.target_date,
        proposed_date=proposed,
    )


def post(client, action, key, *, source=None, back=MY_WORK, proposed=None):
    data = {
        "nihe": key,
        "kuupaev": (source or action.target_date).isoformat(),
        "tagasi": back,
    }
    if proposed is not None:
        data[f"uus_{key}"] = proposed.isoformat()
    return client.post(
        reverse(
            "matters:shift_action_date", kwargs={"pk": action.matter_id, "action_id": action.pk}
        ),
        data,
    )


# ---------------------------------------------------------------------------
# A. the arithmetic — from the stored day, calendar months
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("stored", "key", "expected"),
    [
        (date(2026, 11, 5), "paev", date(2026, 11, 6)),
        (date(2026, 11, 5), "nadal", date(2026, 11, 12)),
        (date(2026, 11, 5), "kuu", date(2026, 12, 5)),
        (date(2027, 1, 31), "kuu", date(2027, 2, 28)),
        (date(2028, 1, 31), "kuu", date(2028, 2, 29)),
        (date(2028, 2, 29), "kuu", date(2028, 3, 29)),
        (date(2026, 12, 31), "paev", date(2027, 1, 1)),
        (date(2026, 12, 28), "nadal", date(2027, 1, 4)),
        (date(2026, 12, 15), "kuu", date(2027, 1, 15)),
        (date(2026, 8, 31), "kuu", date(2026, 9, 30)),
    ],
)
def test_the_new_day_is_computed_from_the_stored_one(stored, key, expected):
    assert date_shift.shifted(stored, key) == expected


def test_the_three_choices_are_offered_in_order():
    assert [(c.label, c.display) for c in date_shift.choices(date(2026, 11, 5))] == [
        ("+1 päev", "6.11.2026"),
        ("+1 nädal", "12.11.2026"),
        ("+1 kuu", "5.12.2026"),
    ]


def test_an_unknown_move_is_refused(specialist, normal_matter):
    action = current(normal_matter, date(2026, 11, 5))

    with refused(date_shift.SHIFT_UNKNOWN):
        shift(action, "aasta")


# ---------------------------------------------------------------------------
# B. the canonical edit — current and planned
# ---------------------------------------------------------------------------


def test_a_current_step_moves_through_the_canonical_edit_and_keeps_everything_else(
    specialist, other_specialist
):
    matter = factories.MatterFactory(owner=specialist)
    step = MatterPlanStep.objects.create(matter=matter, title="Tutvu materjaliga", position=0)
    action = current(
        matter,
        date(2026, 11, 5),
        responsible=other_specialist,
        plan_step=step,
        source_text="Registrist: saada arvamus",
    )
    NextAction.objects.filter(pk=action.pk).update(visibility_override=Visibility.RESTRICTED)
    action.refresh_from_db()

    result = shift(action, "nadal", actor=specialist)

    action.refresh_from_db()
    new = result.action
    assert (result.old_date, result.new_date) == (date(2026, 11, 5), date(2026, 11, 12))
    assert result.message == "Kuupäev muudetud: 5.11 → 12.11."
    assert new.status == ActionStatus.OPEN and new.target_date == date(2026, 11, 12)
    assert new.date_precision == DatePrecision.EXACT
    assert (new.text, new.kind, new.date_semantics) == (
        "Saadan arvamuse",
        ActionKind.DO,
        DateSemantics.DEADLINE,
    )
    # Not reassigned to the Matter's owner: the colleague still carries it.
    assert new.responsible == other_specialist
    assert new.plan_step_id == step.pk
    assert new.visibility_override == Visibility.RESTRICTED
    assert new.source_text == "Registrist: saada arvamus"
    assert new.matter_id == matter.pk
    assert action.status == ActionStatus.SUPERSEDED
    assert action.replaced_by_id == new.pk
    assert action.target_date == date(2026, 11, 5)
    event = ChangeEvent.objects.filter(event_type=ChangeEventType.NEXT_ACTION_SET).latest("pk")
    assert event.payload["replaced"] == str(action.pk)
    assert event.payload["target_date"] == "2026-11-12"
    assert event.actor == specialist


def test_a_planned_step_moves_and_the_current_one_is_untouched(specialist, normal_matter):
    now = current(normal_matter, date(2026, 11, 3), text="Praegune")
    later = planned(normal_matter, date(2026, 11, 20), text="Planeeritud")
    step = MatterPlanStep.objects.create(matter=normal_matter, title="Samm", position=0)
    NextAction.objects.filter(pk=later.pk).update(
        plan_step=step, visibility_override=Visibility.RESTRICTED
    )
    later.refresh_from_db()

    result = shift(later, "kuu")

    later.refresh_from_db()
    now.refresh_from_db()
    new = result.action
    assert new.status == ActionStatus.PLANNED and new.target_date == date(2026, 12, 20)
    assert new.text == "Planeeritud" and new.responsible == later.responsible
    assert new.plan_step_id == step.pk
    assert new.visibility_override == Visibility.RESTRICTED
    assert later.status == ActionStatus.SUPERSEDED and later.replaced_by_id == new.pk
    assert now.status == ActionStatus.OPEN and now.target_date == date(2026, 11, 3)


def test_moving_writes_nothing_but_the_step(specialist):
    """No meeting, note, stage move, closure or other date changes."""
    matter = factories.MatterFactory(owner=specialist, response_deadline=date(2026, 11, 30))
    effective = factories.EffectiveDateFactory(matter=matter, date_value=date(2027, 1, 1))
    important = factories.ImportantDateFactory(matter=matter)
    action = current(matter, date(2026, 11, 5))
    before = set(ChangeEvent.objects.values_list("pk", flat=True))
    stage = matter.stage_id

    shift(action, "paev")

    matter.refresh_from_db()
    effective.refresh_from_db()
    important_before = (important.date_value, important.status)
    important.refresh_from_db()
    assert matter.response_deadline == date(2026, 11, 30)
    assert matter.is_open and matter.stage_id == stage
    assert effective.date_value == date(2027, 1, 1)
    assert (important.date_value, important.status) == important_before
    new_events = ChangeEvent.objects.exclude(pk__in=before)
    assert list(new_events.values_list("event_type", flat=True)) == [
        ChangeEventType.NEXT_ACTION_SET
    ]
    assert NextAction.objects.filter(matter=matter, status=ActionStatus.OPEN).count() == 1
    assert not NextAction.objects.filter(matter=matter, status=ActionStatus.COMPLETED).exists()


# ---------------------------------------------------------------------------
# C. stale, repeated and refused
# ---------------------------------------------------------------------------


def test_a_second_press_does_not_move_twice(specialist, normal_matter):
    action = current(normal_matter, date(2026, 11, 5))
    shift(action, "nadal")

    with refused(date_shift.SHIFT_STALE):
        shift(action, "nadal", source=date(2026, 11, 5))

    assert NextAction.objects.get(matter=normal_matter, status=ActionStatus.OPEN).target_date == (
        date(2026, 11, 12)
    )


def test_a_day_changed_in_another_tab_is_refused(specialist, normal_matter):
    action = current(normal_matter, date(2026, 11, 5))
    NextAction.objects.filter(pk=action.pk).update(target_date=date(2026, 11, 7))
    count = NextAction.objects.count()

    with refused(date_shift.SHIFT_STALE):
        shift(action, "paev", source=date(2026, 11, 5))

    assert NextAction.objects.count() == count


def test_a_proposed_day_that_disagrees_with_the_server_is_refused(specialist, normal_matter):
    action = current(normal_matter, date(2026, 11, 5))

    with refused(date_shift.SHIFT_STALE):
        shift(action, "paev", proposed=date(2026, 11, 8))


def test_a_finished_step_is_refused(specialist, normal_matter):
    action = current(normal_matter, date(2026, 11, 5))
    NextAction.objects.filter(pk=action.pk).update(
        status=ActionStatus.COMPLETED, ended_at=timezone.now()
    )

    with refused(date_shift.SHIFT_STALE):
        shift(action, "paev")


def test_a_closed_matter_is_refused(specialist, normal_matter):
    action = current(normal_matter, date(2026, 11, 5))
    type(normal_matter).objects.filter(pk=normal_matter.pk).update(
        is_open=False, disposition=Disposition.COMPLETED, closed_at=timezone.now()
    )

    with refused(date_shift.SHIFT_STALE):
        shift(action, "paev")


@pytest.mark.parametrize(
    ("kind", "semantics"),
    [
        (ActionKind.WAIT, DateSemantics.EXPECTED_AROUND),
        (ActionKind.MONITOR, DateSemantics.REVIEW_ON),
    ],
)
def test_a_review_date_is_not_moved_here(specialist, normal_matter, kind, semantics):
    action = current(normal_matter, date(2026, 11, 5), kind=kind, date_semantics=semantics)

    with refused(date_shift.SHIFT_NOT_ELIGIBLE):
        shift(action, "paev")


def test_an_approximate_step_is_not_moved_here(specialist, normal_matter):
    action = current(
        normal_matter, date(2026, 11, 1), date_precision=DatePrecision.MONTH, text="Novembris"
    )

    with refused(date_shift.SHIFT_NOT_ELIGIBLE):
        shift(action, "paev")


def test_a_step_with_nobody_responsible_is_not_moved_here(specialist, normal_matter):
    action = current(normal_matter, date(2026, 11, 5))
    NextAction.objects.filter(pk=action.pk).update(responsible=None)

    with refused(date_shift.SHIFT_NOT_ELIGIBLE):
        shift(action, "paev")


def test_an_opinion_follow_up_check_is_not_moved_here(specialist, normal_matter):
    submission = factories.send_opinion_through_services(normal_matter, specialist)
    check = schedule_first_check(submission=submission, actor=specialist)
    assert check is not None and check.target_date is not None

    with refused(date_shift.SHIFT_NOT_ELIGIBLE):
        shift(check, "paev")


# ---------------------------------------------------------------------------
# D. the route — authorization and the round trip
# ---------------------------------------------------------------------------


def test_the_route_moves_and_returns_to_the_same_desk_and_period(client, specialist, normal_matter):
    action = current(normal_matter, date(2026, 11, 5))
    client.force_login(specialist)
    back = f"{MY_WORK}?kuni=koik&kalender=kuu&kalendri_aeg=2026-11"

    response = post(client, action, "nadal", back=back, proposed=date(2026, 11, 12))

    assert response.status_code == 302
    assert response["Location"] == back
    new = NextAction.objects.get(matter=normal_matter, status=ActionStatus.OPEN)
    assert new.target_date == date(2026, 11, 12)
    page = client.get(back).content.decode()
    assert "Kuupäev muudetud: 5.11 → 12.11." in page


@pytest.mark.parametrize(
    "back",
    ["https://example.com/minu-asjad/", "//example.com/", "/teemad/", "minu-asjad", ""],
)
def test_the_return_address_is_only_ever_a_desk(client, specialist, normal_matter, back):
    action = current(normal_matter, date(2026, 11, 5))
    client.force_login(specialist)

    response = post(client, action, "paev", back=back)

    assert response["Location"] == MY_WORK


def test_a_colleagues_desk_is_a_valid_return(client, department_head, specialist, normal_matter):
    action = current(normal_matter, date(2026, 11, 5))
    client.force_login(department_head)
    back = reverse("matters:person_work", kwargs={"pk": specialist.pk})

    response = post(client, action, "paev", back=back)

    assert response["Location"] == back
    assert NextAction.objects.get(matter=normal_matter, status=ActionStatus.OPEN).responsible == (
        specialist
    )


def test_a_refusal_says_why_and_moves_nothing(client, specialist, normal_matter):
    action = current(normal_matter, date(2026, 11, 5))
    client.force_login(specialist)

    response = post(client, action, "paev", source=date(2026, 11, 4))

    assert response.status_code == 302
    page = client.get(MY_WORK).content.decode()
    assert "Kuupäeva ei muudetud" in page
    assert NextAction.objects.get(pk=action.pk).target_date == date(2026, 11, 5)


@pytest.mark.parametrize("who", ["reader", "administrator"])
def test_a_reader_or_an_administrator_cannot_move_a_date(client, request, normal_matter, who):
    action = current(normal_matter, date(2026, 11, 5))
    client.force_login(request.getfixturevalue(who))

    assert post(client, action, "paev").status_code == 404
    assert NextAction.objects.get(pk=action.pk).target_date == date(2026, 11, 5)


def test_an_action_of_another_matter_is_not_found(client, specialist, normal_matter):
    action = current(normal_matter, date(2026, 11, 5))
    other = factories.MatterFactory(owner=specialist)
    client.force_login(specialist)

    response = client.post(
        reverse("matters:shift_action_date", kwargs={"pk": other.pk, "action_id": action.pk}),
        {"nihe": "paev", "kuupaev": "2026-11-05", "tagasi": MY_WORK},
    )

    assert response.status_code == 404
    assert NextAction.objects.get(pk=action.pk).status == ActionStatus.OPEN


def test_get_is_not_allowed(client, specialist, normal_matter):
    action = current(normal_matter, date(2026, 11, 5))
    client.force_login(specialist)

    response = client.get(
        reverse(
            "matters:shift_action_date", kwargs={"pk": normal_matter.pk, "action_id": action.pk}
        )
    )

    assert response.status_code == 405


# ---------------------------------------------------------------------------
# E. where it is offered
# ---------------------------------------------------------------------------


def _row_menu_offers(body: str, text: str) -> bool:
    """Whether the work row carrying ``text`` offers «Nihuta kuupäeva» in its menu."""
    start = body.index(text)
    end = body.find("data-workrow", start)
    chunk = body[start:] if end == -1 else body[start:end]
    return "Nihuta kuupäeva" in chunk


def test_minu_asjad_offers_it_only_on_eligible_rows(client, specialist):
    today = timezone.localdate()
    do_matter = factories.MatterFactory(owner=specialist)
    current(do_matter, today + timedelta(days=3), text="Teen ise ära")
    wait_matter = factories.MatterFactory(owner=specialist)
    current(
        wait_matter,
        today + timedelta(days=3),
        text="Ootan ministeeriumi vastust",
        kind=ActionKind.WAIT,
        date_semantics=DateSemantics.EXPECTED_AROUND,
    )
    factories.MatterFactory(
        owner=specialist, title="Ainult arvamuse tähtaeg", response_deadline=today + timedelta(3)
    )
    client.force_login(specialist)

    body = client.get(MY_WORK).content.decode()

    assert _row_menu_offers(body, "Teen ise ära")
    assert not _row_menu_offers(body, "Ootan ministeeriumi vastust")
    assert body.count("data-dateshift>") == 1
    assert "+1 nädal" in body and "Muuda täpsemalt" in body


def test_the_work_item_rule_matches_the_service(specialist, normal_matter):
    action = current(normal_matter, date(2026, 11, 5))
    items = wi.work_items(specialist, today=date(2026, 11, 1), responsible=specialist)

    (item,) = [i for i in items if i.object_id == action.pk]
    assert item.is_date_shiftable
    assert date_shift.is_shiftable(action)
    assert item.full_edit_url.endswith("#lisa-jargmine")


def test_a_reader_is_not_offered_the_control(client, reader):
    """Even on a row that is the reader's own: no business write, no control."""
    matter = factories.MatterFactory(owner=reader)
    factories.NextActionFactory(
        matter=matter,
        text="Lugeja rida",
        target_date=timezone.localdate() + timedelta(days=3),
        responsible=reader,
    )
    client.force_login(reader)

    body = client.get(MY_WORK).content.decode()

    assert "Lugeja rida" in body
    assert "Nihuta kuupäeva" not in body
