"""Workflow correctness round B (2026-10-23): defects where two systems meet.

Each test names the defect it pins. They are regression tests for confirmed
behaviour, not a new specification.
"""

from __future__ import annotations

from datetime import date, timedelta

import pytest
from django.urls import reverse
from django.utils import timezone

from app.audit.enums import ChangeEventType
from app.audit.models import ChangeEvent
from app.matters.workspace import complete_current_action
from app.workflow.enums import ActionStatus, DatePrecision
from app.workflow.models import NextAction
from app.workflow.services import (
    add_planned_action,
    change_planned_action,
    set_next_action,
    set_next_action_for_new_work,
)
from tests.follow_ups import send_koja_arvamus
from tests.test_closure_confirms_live_work import filed, stage

pytestmark = pytest.mark.django_db


def _day(offset: int) -> date:
    return timezone.localdate() + timedelta(days=offset)


@pytest.fixture
def with_current(normal_matter, specialist):
    set_next_action_for_new_work(
        matter=normal_matter, text="Loe eelnõu läbi", target_date=_day(2), actor=specialist
    )
    return normal_matter


def _month_anchor() -> date:
    """The anchor a «<month> <year>» period is stored on: its first day, months ahead."""
    later = timezone.localdate() + timedelta(days=95)
    return later.replace(day=1)


# ---------------------------------------------------------------------------
# A planned action known only to a period keeps that period through `Muuda`
# ---------------------------------------------------------------------------


def test_rewording_a_month_planned_action_does_not_invent_an_exact_day(specialist, with_current):
    """`Muuda` on «<month> <year>» that changes only the words keeps the month.

    The edit form offers a day and is pre-filled with the period's anchor; a
    save that leaves that day alone has not chosen the 1st of the month.
    """
    anchor = _month_anchor()
    planned = add_planned_action(
        matter=with_current,
        text="Koonda tagasiside",
        target_date=anchor,
        date_precision=DatePrecision.MONTH,
        actor=specialist,
    )

    changed = change_planned_action(
        matter=with_current,
        action_id=planned.pk,
        text="Koonda liikmete tagasiside",
        target_date=anchor,
        actor=specialist,
    )

    assert changed.date_precision == DatePrecision.MONTH
    assert changed.target_date == anchor


def test_rewording_through_the_teema_form_keeps_the_month(client, specialist, with_current):
    client.force_login(specialist)
    anchor = _month_anchor()
    planned = add_planned_action(
        matter=with_current,
        text="Koonda tagasiside",
        target_date=anchor,
        date_precision=DatePrecision.MONTH,
        actor=specialist,
    )

    response = client.post(
        reverse(
            "matters:change_planned_action",
            kwargs={"pk": with_current.pk, "action_id": planned.pk},
        ),
        {"text": "Koonda liikmete tagasiside", "target_date": anchor.isoformat()},
        headers={"HX-Request": "true"},
    )

    assert response.status_code == 200
    replacement = NextAction.objects.get(matter=with_current, status=ActionStatus.PLANNED)
    assert replacement.text == "Koonda liikmete tagasiside"
    assert replacement.date_precision == DatePrecision.MONTH


def test_choosing_another_day_for_a_month_planned_action_makes_it_exact(specialist, with_current):
    anchor = _month_anchor()
    planned = add_planned_action(
        matter=with_current,
        text="Koonda tagasiside",
        target_date=anchor,
        date_precision=DatePrecision.MONTH,
        actor=specialist,
    )

    changed = change_planned_action(
        matter=with_current,
        action_id=planned.pk,
        text="Koonda tagasiside",
        target_date=anchor + timedelta(days=9),
        actor=specialist,
    )

    assert changed.date_precision == DatePrecision.EXACT


# ---------------------------------------------------------------------------
# A completion whose stage closes the Matter promotes no planned action
# ---------------------------------------------------------------------------


def test_a_closing_completion_does_not_promote_a_planned_action(client, specialist):
    """`✓ Tehtud` with «Jõustunud» closes the file; the planned row is only cancelled.

    Promoting it first wrote `NEXT_ACTION_SET` (``promoted``) inside the save,
    and `Teema käik` drew «Järgmine samm – …» under the row that ended the
    Matter, for a step that was cancelled in the same transaction.
    """
    matter = filed(specialist)
    step = set_next_action(matter=matter, text="Viimane samm", actor=specialist)
    planned = add_planned_action(
        matter=matter, text="Hiljem tehtav samm", target_date=_day(4), actor=specialist
    )

    complete_current_action(
        matter=matter,
        author=specialist,
        action_id=step.pk,
        body="Määrus jõustus.",
        stage=stage("in_force"),
        follow_ups_confirmed=True,
    )

    matter.refresh_from_db()
    planned.refresh_from_db()
    assert not matter.is_open
    assert planned.status == ActionStatus.CANCELLED
    assert not ChangeEvent.objects.filter(
        matter=matter,
        event_type=ChangeEventType.NEXT_ACTION_SET,
        payload__contains={"promoted": True},
    ).exists()
    client.force_login(specialist)
    body = client.get(reverse("matters:matter_detail", kwargs={"pk": matter.pk})).content.decode()
    assert '<span class="uxtl__nexttext">Hiljem tehtav samm</span>' not in body


def test_a_closing_koja_arvamus_that_finishes_the_step_does_not_promote(
    client, specialist, organisation
):
    """`+ Koja arvamus` with `Märgi praegune tegevus tehtuks` and a closing stage.

    The same rule as `✓ Tehtud` above, on the opinion's own save path: it
    promoted the earliest planned row and the closure then cancelled it,
    leaving a `NEXT_ACTION_SET` (``promoted``) and «Järgmine samm – …» in
    `Tegevused` under the save that ended the Matter (bounty A-02).
    """
    matter = filed(specialist)
    step = set_next_action(matter=matter, text="Koostan arvamuse", actor=specialist)
    planned = add_planned_action(
        matter=matter, text="Kohtumine liikmetega", target_date=_day(4), actor=specialist
    )

    send_koja_arvamus(
        matter,
        specialist,
        [organisation],
        timezone.localdate(),
        complete_action_id=step.pk,
        stage=stage("in_force"),
        follow_ups_confirmed=True,
    )

    matter.refresh_from_db()
    step.refresh_from_db()
    planned.refresh_from_db()
    assert not matter.is_open
    assert step.status == ActionStatus.COMPLETED
    assert planned.status == ActionStatus.CANCELLED
    assert not ChangeEvent.objects.filter(
        matter=matter,
        event_type=ChangeEventType.NEXT_ACTION_SET,
        payload__contains={"promoted": True},
    ).exists()
    client.force_login(specialist)
    body = client.get(reverse("matters:matter_detail", kwargs={"pk": matter.pk})).content.decode()
    assert '<span class="uxtl__nexttext">Kohtumine liikmetega</span>' not in body


def test_a_koja_arvamus_that_keeps_the_matter_open_still_promotes(specialist, organisation):
    matter = filed(specialist)
    step = set_next_action(matter=matter, text="Koostan arvamuse", actor=specialist)
    planned = add_planned_action(
        matter=matter, text="Kohtumine liikmetega", target_date=_day(4), actor=specialist
    )

    send_koja_arvamus(
        matter, specialist, [organisation], timezone.localdate(), complete_action_id=step.pk
    )

    planned.refresh_from_db()
    assert planned.status == ActionStatus.OPEN


def test_a_completion_that_keeps_the_matter_open_still_promotes(specialist):
    matter = filed(specialist)
    step = set_next_action(matter=matter, text="Praegune samm", actor=specialist)
    planned = add_planned_action(
        matter=matter, text="Järgmine planeeritud", target_date=_day(4), actor=specialist
    )

    complete_current_action(matter=matter, author=specialist, action_id=step.pk, body="Tehtud.")

    planned.refresh_from_db()
    assert planned.status == ActionStatus.OPEN


# ---------------------------------------------------------------------------
# A repeated `+ Määra järgmine tegevus` save files the step once
# ---------------------------------------------------------------------------


def _add_step(client, matter, text: str, day: date):
    return client.post(
        reverse("matters:add_planned_action", kwargs={"pk": matter.pk}),
        {"text": text, "target_date": day.isoformat()},
        headers={"HX-Request": "true"},
    )


def _live(matter) -> list[tuple[str, str]]:
    return list(
        NextAction.objects.filter(
            matter=matter, status__in=(ActionStatus.OPEN, ActionStatus.PLANNED)
        )
        .order_by("created_at", "pk")
        .values_list("text", "status")
    )


def test_a_double_submitted_first_step_is_not_also_planned(client, specialist, normal_matter):
    """The second POST used to find the first one's step open and plan a copy."""
    client.force_login(specialist)

    first = _add_step(client, normal_matter, "Helista ministeeriumi", _day(5))
    second = _add_step(client, normal_matter, "Helista ministeeriumi", _day(5))

    assert (first.status_code, second.status_code) == (200, 200)
    assert _live(normal_matter) == [("Helista ministeeriumi", ActionStatus.OPEN)]


def test_a_double_submitted_planned_step_is_planned_once(client, specialist, with_current):
    client.force_login(specialist)

    _add_step(client, with_current, "Koonda tagasiside", _day(9))
    _add_step(client, with_current, "Koonda tagasiside", _day(9))

    assert _live(with_current) == [
        ("Loe eelnõu läbi", ActionStatus.OPEN),
        ("Koonda tagasiside", ActionStatus.PLANNED),
    ]


def test_a_different_day_or_sentence_is_another_planned_step(specialist, with_current):
    add_planned_action(
        matter=with_current, text="Koonda tagasiside", target_date=_day(9), actor=specialist
    )
    add_planned_action(
        matter=with_current, text="Koonda tagasiside", target_date=_day(16), actor=specialist
    )
    add_planned_action(
        matter=with_current, text="Saada kokkuvõte", target_date=_day(9), actor=specialist
    )

    assert len(_live(with_current)) == 4
