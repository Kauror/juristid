# ruff: noqa: F811 - the fixtures imported from the contract suite are used as arguments
"""QA-NEW-03 / LS-05 — owner decision 20.11.2026, option B.

An unanswered opinion request Juristid holds from Lovable, whose deadline has
passed, with no Lovable `jargmiseks` sentence: the derived drafting step
«Koostan arvamuse» **stays open and overdue** until Lovable says sent or «ei
saatnud», or the Teema closes. Before the decision the synchronisation
cancelled it the night after the deadline (with an inaccurate reason in Teema
käik). Measured on production 20.11.2026: 0 Teemad affected (both passed,
unanswered requests carry Lovable's own sentence), so no backlog appears.

* the night after the deadline writes nothing: the step stays, overdue as a
  late DO step (`_action_overdue`), the obligation stays recorded;
* a second night writes nothing (idempotent);
* «ei saatnud», a sent date, or a closure in Lovable ends it — as before.
"""

from __future__ import annotations

import datetime as dt

import pytest

from app.legacy_import.lovable_sync_models import SyncRunStatus
from app.workflow.enums import ActionStatus
from app.workflow.models import NextAction
from tests.lovable_contract.fixtures import pilot_on, world  # noqa: F401 - fixtures
from tests.lovable_contract.world import TALLINN, ref

pytestmark = pytest.mark.django_db

DEADLINE = dt.date(2026, 11, 13)
DAY_AFTER = dt.datetime(2026, 11, 14, 0, 15, tzinfo=TALLINN)


def _unanswered(world):
    world.lovable.edit(
        ref(1), arvamuse_tahtaeg=DEADLINE.isoformat(), jargmiseks="", tegevuse_tahtaeg=None
    )
    first = world.run()
    assert first.status == SyncRunStatus.SUCCEEDED, first.error
    matter = world.matter(ref(1))
    drafting = NextAction.objects.get(matter=matter, status=ActionStatus.OPEN)
    assert (drafting.kind, drafting.text, drafting.target_date) == (
        "DO",
        "Koostan arvamuse",
        DEADLINE,
    )
    return matter, drafting


def test_ls05_the_drafting_step_stays_open_and_overdue(world):
    from app.matters.work_items import outstanding_response_deadlines
    from app.notifications.categories import _action_overdue, _opinion_overdue

    matter, drafting = _unanswered(world)
    reader = matter.owner

    night = world.run(at=DAY_AFTER)
    assert night.status == SyncRunStatus.SUCCEEDED, night.error
    today = DAY_AFTER.date()
    matter.refresh_from_db()
    drafting.refresh_from_db()

    assert drafting.status == ActionStatus.OPEN
    assert matter.response_deadline == DEADLINE
    # Overdue once, as the late drafting step; the obligation stands behind it (ADR 0050).
    assert _action_overdue(reader, today).filter(pk=drafting.pk).exists()
    assert not outstanding_response_deadlines(reader).filter(pk=matter.pk).exists()
    assert not _opinion_overdue(reader, today).filter(pk=matter.pk).exists()
    # Nothing in Teema käik: no cancellation, no new step.
    assert not world.events_of(night).filter(matter=matter).exists()

    again = world.run(at=DAY_AFTER + dt.timedelta(days=7))
    assert again.status == SyncRunStatus.SUCCEEDED, again.error
    assert not world.events_of(again).filter(matter=matter).exists()
    drafting.refresh_from_db()
    assert drafting.status == ActionStatus.OPEN


@pytest.mark.parametrize(
    "lovable_says",
    [
        {"valja": "ei saatnud"},
        {"valja_kuupaev": "2026-11-15"},
        {"hetkeseis": "rohkem ei tegele"},
    ],
    ids=["ei-saatnud", "sent", "closed"],
)
def test_ls05_the_step_ends_when_lovable_resolves_the_request(world, lovable_says):
    matter, drafting = _unanswered(world)
    world.run(at=DAY_AFTER)
    drafting.refresh_from_db()
    assert drafting.status == ActionStatus.OPEN

    world.lovable.edit(ref(1), **lovable_says)
    later = world.run(at=dt.datetime(2026, 11, 16, 0, 15, tzinfo=TALLINN))
    assert later.status == SyncRunStatus.SUCCEEDED, later.error
    drafting.refresh_from_db()
    assert drafting.status != ActionStatus.OPEN
    assert not NextAction.objects.filter(
        matter=matter, status=ActionStatus.OPEN, text="Koostan arvamuse"
    ).exists()
