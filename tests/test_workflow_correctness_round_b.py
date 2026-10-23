"""Workflow correctness round B (2026-10-23): defects where two systems meet.

Each test names the defect it pins. They are regression tests for confirmed
behaviour, not a new specification.
"""

from __future__ import annotations

from datetime import date, timedelta

import pytest
from django.urls import reverse
from django.utils import timezone

from app.workflow.enums import ActionStatus, DatePrecision
from app.workflow.models import NextAction
from app.workflow.services import (
    add_planned_action,
    change_planned_action,
    set_next_action_for_new_work,
)

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


def test_choosing_another_day_for_a_month_planned_action_makes_it_exact(
    specialist, with_current
):
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
