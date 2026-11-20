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
from tests.lovable_contract.world import AVA, TALLINN, ref

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


@pytest.mark.parametrize(
    "moment",
    [
        dt.datetime(2026, 11, 13, 23, 59, tzinfo=TALLINN),  # the deadline's last minute
        dt.datetime(2026, 11, 14, 0, 1, tzinfo=TALLINN),  # the first minute after it
        dt.datetime(2026, 11, 13, 22, 30, tzinfo=dt.UTC),  # 00:30 in Tallinn, 22:30 UTC
    ],
    ids=["23-59-tallinn", "00-01-tallinn", "22-30-utc"],
)
def test_ls05_the_tallinn_midnight_boundary_writes_nothing(world, moment):
    """The rule no longer reads the day: a night on either side of the
    deadline's midnight leaves the step as it is. Lateness is the lateness rule's
    question (a DO step is late from the day after its date, Tallinn)."""
    from app.notifications.categories import _action_overdue

    matter, drafting = _unanswered(world)
    night = world.run(at=moment)
    assert night.status == SyncRunStatus.SUCCEEDED, night.error
    assert not world.events_of(night).filter(matter=matter).exists()
    drafting.refresh_from_db()
    assert drafting.status == ActionStatus.OPEN and drafting.target_date == DEADLINE
    reader = matter.owner
    assert not _action_overdue(reader, DEADLINE).filter(pk=drafting.pk).exists()
    assert _action_overdue(reader, DEADLINE + dt.timedelta(days=1)).filter(pk=drafting.pk).exists()


def test_ls05_source_changes_after_the_deadline_are_followed(world):
    """Lovable moving the deadline, writing a sentence, and taking it away again."""
    matter, _drafting = _unanswered(world)
    world.run(at=DAY_AFTER)

    def open_step():
        return NextAction.objects.filter(matter=matter, status=ActionStatus.OPEN).values_list(
            "text", "target_date"
        )

    # A new deadline: the drafting step follows the request's new day.
    world.lovable.edit(ref(1), arvamuse_tahtaeg="2026-11-27")
    moved = world.run(at=DAY_AFTER + dt.timedelta(days=1))
    assert moved.status == SyncRunStatus.SUCCEEDED, moved.error
    matter.refresh_from_db()
    assert matter.response_deadline == dt.date(2026, 11, 27)
    assert list(open_step()) == [("Koostan arvamuse", dt.date(2026, 11, 27))]

    # Lovable's own sentence replaces the derived step.
    world.lovable.edit(
        ref(1), jargmiseks="Ootan ministeeriumi vastust", tegevuse_tahtaeg="2026-12-01"
    )
    worded = world.run(at=DAY_AFTER + dt.timedelta(days=2))
    assert worded.status == SyncRunStatus.SUCCEEDED, worded.error
    assert list(open_step()) == [("Ootan ministeeriumi vastust", dt.date(2026, 12, 1))]

    # The sentence removed again after the new deadline passed: the derived
    # drafting step comes back on the request's day, overdue, and stays.
    world.lovable.edit(ref(1), jargmiseks="", tegevuse_tahtaeg=None)
    late = dt.datetime(2026, 11, 29, 0, 15, tzinfo=TALLINN)
    back = world.run(at=late)
    assert back.status == SyncRunStatus.SUCCEEDED, back.error
    assert list(open_step()) == [("Koostan arvamuse", dt.date(2026, 11, 27))]
    again = world.run(at=late + dt.timedelta(days=1))
    assert not world.events_of(again).filter(matter=matter).exists()


def test_ls05_a_new_teema_whose_deadline_had_passed_opens_no_request(world):
    """The documented distinction, unchanged by option B: a passed request is
    never *opened* (approval 11.11 §1; `lovable_reading.read_topic`). A Teema the
    night creates from a Lovable topic whose deadline had already passed gets
    neither a request nor a drafting step — so it is not an outstanding opinion
    obligation in Juristid. A future deadline opens both. (Production 20.11.2026:
    0 open Teemad in this state.) Pinned so a change to it is a decision."""
    from app.matters.work_items import response_obligations
    from tests.lovable_contract.world import lovable_row, source_id

    for number, day in ((11, "2026-11-05"), (12, "2026-11-30")):
        world.lovable.put(
            source_id(ref(number)),
            lovable_row(ref(number), arvamuse_tahtaeg=day, jargmiseks="", tegevuse_tahtaeg=None),
            version=1,
        )
    run = world.run()
    assert run.status == SyncRunStatus.SUCCEEDED, run.error
    passed, future = world.matter(ref(11)), world.matter(ref(12))
    assert passed.response_deadline is None
    assert not NextAction.objects.filter(matter=passed, status=ActionStatus.OPEN).exists()
    assert not response_obligations(passed.owner).filter(pk=passed.pk).exists()
    assert future.response_deadline == dt.date(2026, 11, 30)
    assert response_obligations(future.owner).filter(pk=future.pk).exists()
    # A later night opens nothing on the passed one either.
    later = world.run(at=dt.datetime(2026, 11, 20, 0, 15, tzinfo=TALLINN))
    assert later.status == SyncRunStatus.SUCCEEDED, later.error
    passed.refresh_from_db()
    assert passed.response_deadline is None
    assert not NextAction.objects.filter(matter=passed, status=ActionStatus.OPEN).exists()


def test_ls05_lovable_clearing_the_deadline_or_marking_its_own_initiative_ends_the_step(world):
    """Review of #660: the request ending in Lovable (deadline cleared) and the
    Koda initiative switched on are exits too; switched off again, the request
    and its overdue drafting step are Lovable's again (the approved rule)."""
    matter, drafting = _unanswered(world)
    world.run(at=DAY_AFTER)

    world.lovable.edit(ref(1), arvamuse_tahtaeg=None)
    cleared = world.run(at=DAY_AFTER + dt.timedelta(days=1))
    assert cleared.status == SyncRunStatus.SUCCEEDED, cleared.error
    drafting.refresh_from_db()
    assert drafting.status != ActionStatus.OPEN
    matter.refresh_from_db()
    assert matter.response_deadline is None

    world.lovable.edit(ref(1), arvamuse_tahtaeg=DEADLINE.isoformat(), koja_ettepanek=True)
    koda = world.run(at=DAY_AFTER + dt.timedelta(days=2))
    assert koda.status == SyncRunStatus.SUCCEEDED, koda.error
    assert not NextAction.objects.filter(
        matter=matter, status=ActionStatus.OPEN, text="Koostan arvamuse"
    ).exists()


def test_ls05_a_held_outcome_neither_creates_nor_ends_the_step(world):
    """A sent day in the future is an invalid read (held): no drafting step is
    invented and none is ended on that word (`OUTCOME_HELD`)."""
    matter, drafting = _unanswered(world)
    world.run(at=DAY_AFTER)
    world.lovable.edit(ref(1), valja_kuupaev="2026-12-24")
    held = world.run(at=DAY_AFTER + dt.timedelta(days=1))
    assert held.status in (SyncRunStatus.SUCCEEDED, SyncRunStatus.REVIEW_ONLY), held.error
    drafting.refresh_from_db()
    assert drafting.status == ActionStatus.OPEN
    assert not world.events_of(held).filter(matter=matter).exists()


def test_ls05_a_closure_held_for_juristid_work_keeps_the_step_until_it_closes(world):
    """TL-02: Lovable closes, Juristid-only work holds the closure; the step is
    held with it (nothing written). When the work is done the Teema closes and
    the step ends with it."""
    from app.workflow.services import add_planned_action, cancel_next_action

    matter, drafting = _unanswered(world)
    world.run(at=DAY_AFTER)
    planned = add_planned_action(
        matter=matter, text="Helistan ministeeriumi", target_date=dt.date(2099, 4, 1)
    )
    world.lovable.edit(ref(1), hetkeseis="jõustunud")
    held = world.run(at=DAY_AFTER + dt.timedelta(days=1))
    assert held.status == SyncRunStatus.SUCCEEDED, held.error
    assert world.matter(ref(1)).is_open
    drafting.refresh_from_db()
    assert drafting.status == ActionStatus.OPEN

    cancel_next_action(action=planned, actor=world.person(AVA), reason="Pole vaja")
    closed = world.run(at=DAY_AFTER + dt.timedelta(days=2))
    assert closed.status == SyncRunStatus.SUCCEEDED, closed.error
    assert not world.matter(ref(1)).is_open
    drafting.refresh_from_db()
    assert drafting.status != ActionStatus.OPEN


def test_ls05_a_teema_reopened_in_lovable_with_an_old_unanswered_request(world):
    """Coordinator decision 20.11.2026 (option A): Lovable reopens a Teema whose
    request is unanswered and past its deadline, with no outcome and no next
    step. «Koostan arvamuse» comes back open and overdue on the ORIGINAL source
    deadline; no new request is created and the deadline is not renewed.
    Production 20.11.2026: 0 closed synchronised Teemad exposed."""
    matter, _drafting = _unanswered(world)
    world.lovable.edit(ref(1), hetkeseis="rohkem ei tegele")
    closed = world.run(at=DAY_AFTER)
    assert closed.status == SyncRunStatus.SUCCEEDED, closed.error
    assert not world.matter(ref(1)).is_open

    world.lovable.edit(ref(1), hetkeseis="Riigikogus")
    reopened = world.run(at=dt.datetime(2026, 12, 15, 0, 15, tzinfo=TALLINN))
    assert reopened.status == SyncRunStatus.SUCCEEDED, reopened.error
    assert world.matter(ref(1)).is_open
    steps = list(
        NextAction.objects.filter(matter=matter, status=ActionStatus.OPEN).values_list(
            "text", "target_date"
        )
    )
    assert steps == [("Koostan arvamuse", DEADLINE)]
    matter.refresh_from_db()
    assert matter.response_deadline == DEADLINE  # the original deadline, not renewed
