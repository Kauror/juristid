# ruff: noqa: F811 - the fixtures imported from the contract suite are used as arguments
"""QA-NEW-03 / LS-05 (coordinator QA 2026-11-20): an unanswered opinion request
whose deadline was yesterday — current behaviour, pinned, NOT endorsed.

ADR 0213 §10.12 item 3 records LS-05 as OPEN (an owner question). These tests
state what the synchronisation does today, end to end, so whichever rule the
owner chooses has its baseline in one place:

* the derived drafting step «Koostan arvamuse» is cancelled the first night
  after the deadline, with no actor, under the run's provenance;
* the opinion obligation (`response_deadline`) stays, and is overdue on the
  operational surfaces and in the deadline escalation (`_opinion_overdue`);
* Teema käik gets one NEXT_ACTION_CANCELLED whose reason says Lovable has no
  next step — although Lovable never had one: the step was derived;
* a second APPLY night is idempotent: no write, no event.
"""

from __future__ import annotations

import datetime as dt

import pytest

from app.audit.enums import ChangeEventType
from app.legacy_import.lovable_sync_models import SyncRunStatus
from app.workflow.enums import ActionStatus
from app.workflow.models import NextAction
from tests.lovable_contract.fixtures import pilot_on, world  # noqa: F401 - fixtures
from tests.lovable_contract.world import TALLINN, ref

pytestmark = pytest.mark.django_db

DEADLINE = dt.date(2026, 11, 13)
DAY_AFTER = dt.datetime(2026, 11, 14, 0, 15, tzinfo=TALLINN)


def test_ls05_overdue_unanswered_request_today(world):
    from app.matters.work_items import outstanding_response_deadlines
    from app.notifications.categories import _action_overdue, _opinion_overdue

    world.lovable.edit(
        ref(1), arvamuse_tahtaeg=DEADLINE.isoformat(), jargmiseks="", tegevuse_tahtaeg=None
    )
    first = world.run()
    assert first.status == SyncRunStatus.SUCCEEDED, first.error
    matter = world.matter(ref(1))
    drafting = NextAction.objects.get(matter=matter, status=ActionStatus.OPEN)
    assert (drafting.kind, drafting.target_date) == ("DO", DEADLINE)
    reader = matter.owner

    ended = world.run(at=DAY_AFTER)
    assert ended.status == SyncRunStatus.SUCCEEDED, ended.error
    today = DAY_AFTER.date()
    matter.refresh_from_db()

    # The drafting step is cancelled, never completed.
    drafting.refresh_from_db()
    assert drafting.status == ActionStatus.CANCELLED
    assert not NextAction.objects.filter(matter=matter, status=ActionStatus.OPEN).exists()

    # The obligation stays and is overdue where it is the current instruction.
    assert matter.response_deadline == DEADLINE
    assert outstanding_response_deadlines(reader).filter(pk=matter.pk).exists()
    assert _opinion_overdue(reader, today).filter(pk=matter.pk).exists()
    assert not _action_overdue(reader, today).filter(matter=matter).exists()

    # Teema käik: exactly one cancellation, no actor, Lovable's provenance,
    # worded as if Lovable had removed a step it never had.
    events = list(world.events_of(ended).filter(matter=matter))
    assert [e.event_type for e in events] == [ChangeEventType.NEXT_ACTION_CANCELLED]
    assert events[0].actor is None
    assert events[0].payload["reason"] == "Lovable'is pole enam järgmist tegevust."
    assert events[0].payload.get("provenance")

    # A second APPLY night: nothing is written.
    again = world.run(at=DAY_AFTER + dt.timedelta(hours=1))
    assert again.status == SyncRunStatus.SUCCEEDED, again.error
    assert not world.events_of(again).filter(matter=matter).exists()
    assert matter.response_deadline == DEADLINE
