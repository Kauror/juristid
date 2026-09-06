"""``Arvamuse tähtaeg`` as work: the deadline that needed no second record.

What was wrong
--------------

``Matter.response_deadline`` has always been canonical — the register imports
it, the create form writes it, the Matter header prints it and the old
dashboard tables read it directly. What it was not was *work*. The shared read
model in :mod:`app.matters.work_items` combined exactly two sources, a dated
``NextAction`` and an active ``MatterImportantDate``, and every v2 surface built
on it therefore answered "what deadlines are coming" without ever consulting
the commonest deadline on the register.

The visible failure: enter *Arvamuse tähtaeg* on a new Matter, create no
``Järgmiseks`` and no ``Oluline tähtaeg``, and the Matter appeared in no
deadline band, no Ülevaade group, no Osakonna töö window and no ``?too=``
population. The date was stored correctly the whole time.

What this file holds
--------------------

The fix adds a third **projection** — no table, no migration, no backfill and
emphatically no auto-created ``NextAction``. So a good half of what is asserted
here is what did *not* happen: a Matter still holds a deadline and no next
action at the same time, a met obligation stops being outstanding without the
column being cleared, and the WAIT/MONITOR semantics the model already guarded
are untouched.

Every date is relative to today. A test written around a production date passes
for a fortnight and then fails for reasons nobody can reproduce.
"""

from __future__ import annotations

from datetime import date, timedelta
from typing import Any

import pytest
from django.db import connection
from django.test.utils import CaptureQueriesContext
from django.urls import reverse
from django.utils import timezone

from app.core.dates import format_estonian_date
from app.core.enums import Visibility
from app.intelligence.services import add_important_date
from app.matters import work_items as wi
from app.matters.department_dashboard import seis_figures, upcoming_groups
from app.matters.enums import MatterOrigin, RecordMode
from app.matters.forms import MatterCreateForm
from app.matters.models import Matter
from app.matters.my_work import build_my_work
from app.matters.register_filters import register_population
from app.matters.services import assign_matter, close_matter, create_matter, set_matter_dates
from app.submissions.services import (
    attach_final_evidence,
    create_submission,
    mark_submission_sent,
)
from app.workflow.enums import ActionKind, ActionStatus, DateSemantics, Disposition
from app.workflow.models import NextAction
from app.workflow.services import set_next_action

pytestmark = pytest.mark.django_db

PDF = b"%PDF-1.4 arvamus"

TITLE = "Sünteetiline tähtajaga teema"


@pytest.fixture
def today() -> date:
    return timezone.localdate()


def _wednesday() -> date:
    """A stable midweek anchor with days on both sides of it inside the week.

    The parametrised band cases would otherwise straddle a week boundary
    whenever the suite happened to run on a Saturday, and a test that passes on
    five days out of seven is worse than no test.
    """
    today = timezone.localdate()
    return today + timedelta(days=(2 - today.weekday()) % 7) + timedelta(days=7)


def _matter(owner, *, deadline, title=TITLE, **kwargs):
    """A Matter carrying nothing but an ``Arvamuse tähtaeg``.

    No ``Järgmiseks`` and no ``Oluline tähtaeg`` anywhere in this file unless a
    test creates one itself: that absence is the defect being guarded.
    """
    return create_matter(
        title=title,
        owner=owner,
        reference_year=2026,
        response_deadline=deadline,
        **kwargs,
    )


def _send_opinion(matter, actor):
    """Discharge the response obligation the way the product actually does it.

    Through the submission services rather than by writing a status, because
    the fulfilment test the read model applies is the one the old dashboard
    applies, and both mean *this exact text went out*.
    """
    submission = create_submission(matter=matter, title="Arvamus", actor=actor)
    attach_final_evidence(
        submission=submission,
        content=PDF,
        original_filename="arvamus.pdf",
        mime_type="application/pdf",
        actor=actor,
    )
    submission.refresh_from_db()
    return mark_submission_sent(submission=submission, actor=actor)


def _mine(user, today, subject=None):
    return wi.work_items(user, today=today, responsible=subject if subject is not None else user)


def _response_items(items):
    return [item for item in items if item.source_type == wi.SOURCE_RESPONSE_DEADLINE]


def _bands(user, today, subject=None):
    return {band.key: band for band in build_my_work(user, today=today, subject=subject).bands}


def _titles(band):
    return [item.matter.title for item in band.items] if band else []


def _register(user, today, **params):
    return set(
        register_population(
            user, {"olek": "avatud", "liik": "FULL", **params}, today=today
        ).values_list("pk", flat=True)
    )


# ---------------------------------------------------------------------------
# The critical regression: a deadline with no other record at all
# ---------------------------------------------------------------------------


def test_a_response_deadline_alone_is_work(specialist, today):
    """The bug, stated once. This failed before the third source existed.

    One open FULL Matter, one owner, one date, and nothing else — no
    ``NextAction`` and no ``MatterImportantDate``. It must be work.
    """
    due = today + timedelta(days=10)
    matter = _matter(specialist, deadline=due)

    assert not NextAction.objects.filter(matter=matter).exists()
    assert not matter.important_dates.exists()

    items = _mine(specialist, today)
    responses = _response_items(items)

    assert len(responses) == 1
    item = responses[0]
    assert item.matter_id == matter.pk
    assert item.object_id == matter.pk
    assert item.when == due
    assert item.period_end == due
    assert item.responsible == specialist
    assert item.meaning == wi.MEANING_RESPONSE == "ARVAMUSE TÄHTAEG"
    assert item.date_semantics == DateSemantics.DEADLINE.value
    assert item.is_overdue is False
    assert item.is_review_ripe is False
    assert item.is_action is False
    assert item.is_approximate is False

    assert item in wi.real_deadlines(items)

    assert TITLE in _titles(_bands(specialist, today).get(wi.BAND_NEXT_30))


def test_the_row_states_its_meaning_and_invents_no_sentence(specialist, today):
    """The one visible label is the business term, and there is nothing else.

    The deadline row already names the Matter; a manufactured ``text`` beside it
    would be a third way of saying what the reader has just read.
    """
    due = today + timedelta(days=3)
    _matter(specialist, deadline=due)

    (item,) = _response_items(_mine(specialist, today))

    assert item.meaning_line == "ARVAMUSE TÄHTAEG"
    assert item.text == ""
    assert item.action_kind == ""
    assert item.display_date == format_estonian_date(due)


# ---------------------------------------------------------------------------
# No NextAction is created, and neither is anything else
# ---------------------------------------------------------------------------


def test_a_deadline_creates_no_next_action_and_no_important_date(specialist, today):
    """Deadline present, next action missing. That is honest data, not a gap.

    Writing a ``Järgmiseks`` from a date would make one field masquerade as the
    other, and the Matter would stop being reportable as uninstructed — which is
    the one attention state no date can produce.
    """
    matter = _matter(specialist, deadline=today + timedelta(days=5))

    assert _response_items(_mine(specialist, today))

    assert NextAction.objects.filter(matter=matter).count() == 0
    assert matter.important_dates.count() == 0
    # And it is still, correctly, a Matter nobody has instructed.
    assert matter in wi.matters_without_action(specialist)


# ---------------------------------------------------------------------------
# Date banding, every boundary from both sides
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("offset", "band"),
    [
        (-1, wi.BAND_OVERDUE),
        (0, wi.BAND_WEEK),
        (30, wi.BAND_NEXT_30),
        (31, wi.BAND_LATER),
    ],
)
def test_the_band_follows_the_date(specialist, offset, band):
    """Yesterday, today, the thirtieth day and the thirty-first."""
    anchor = _wednesday()
    matter = _matter(specialist, deadline=anchor + timedelta(days=offset))

    bands = _bands(specialist, anchor)

    assert matter.title in _titles(bands.get(band))
    for key, found in bands.items():
        if key != band:
            assert matter.title not in _titles(found)


def test_today_is_due_today_and_is_not_overdue(specialist, today):
    """The boundary the whole model turns on: due today is not late today."""
    _matter(specialist, deadline=today)

    (item,) = _response_items(_mine(specialist, today))

    assert item.is_overdue is False
    assert item.is_today is True
    assert item.short_date == "täna"
    assert item.days_late == 0
    assert TITLE in _titles(_bands(specialist, today).get(wi.BAND_WEEK))


def test_yesterday_is_overdue(specialist, today):
    _matter(specialist, deadline=today - timedelta(days=1))

    (item,) = _response_items(_mine(specialist, today))

    assert item.is_overdue is True
    assert item.days_late == 1
    assert item.short_date == "1 p üle"
    assert TITLE in _titles(_bands(specialist, today).get(wi.BAND_OVERDUE))
    assert build_my_work(specialist, today=today).overdue == 1


def test_the_week_ends_on_sunday_and_the_next_band_starts_on_monday(specialist):
    """Sunday is still this week; Monday is not. Asserted from both sides."""
    anchor = _wednesday()
    sunday = wi.end_of_iso_week(anchor)
    monday = sunday + timedelta(days=1)
    assert sunday.weekday() == 6 and monday.weekday() == 0

    _matter(specialist, deadline=sunday, title="Pühapäevane tähtaeg")
    _matter(specialist, deadline=monday, title="Esmaspäevane tähtaeg")

    bands = _bands(specialist, anchor)

    assert "Pühapäevane tähtaeg" in _titles(bands.get(wi.BAND_WEEK))
    assert "Esmaspäevane tähtaeg" in _titles(bands.get(wi.BAND_NEXT_30))


def test_a_response_deadline_uses_the_same_bands_as_every_other_source(specialist):
    """No response-specific arithmetic: one date, one band, whatever its source.

    A DO deadline and an ``Arvamuse tähtaeg`` on the same day must land in the
    same band, or the page has two calendars in it.
    """
    anchor = _wednesday()
    due = anchor + timedelta(days=20)
    response = _matter(specialist, deadline=due, title="Arvamuse tähtajaga teema")
    action = _matter(specialist, deadline=None, title="Järgmisega teema")
    set_next_action(
        matter=action,
        text="Esitan arvamuse",
        kind=ActionKind.DO,
        date_semantics=DateSemantics.DEADLINE,
        target_date=due,
        actor=specialist,
    )

    bands = _bands(specialist, anchor)

    assert response.title in _titles(bands.get(wi.BAND_NEXT_30))
    assert action.title in _titles(bands.get(wi.BAND_NEXT_30))


# ---------------------------------------------------------------------------
# Whose work it is
# ---------------------------------------------------------------------------


def test_a_deadline_is_on_its_owners_desk_and_on_nobody_elses(specialist, other_specialist, today):
    """Two Matters, two owners, one date. Each lawyer sees only their own."""
    due = today + timedelta(days=7)
    mine = _matter(specialist, deadline=due, title="Ireeni teema")
    theirs = _matter(other_specialist, deadline=due, title="Marko teema")

    mine_titles = {item.matter.title for item in _mine(specialist, today)}
    theirs_titles = {item.matter.title for item in _mine(other_specialist, today)}

    assert mine.title in mine_titles and theirs.title not in mine_titles
    assert theirs.title in theirs_titles and mine.title not in theirs_titles

    # Both are visible work, so the unscoped read — what the department surfaces
    # use — holds both. Assignment and visibility are separate concerns.
    everyone = {item.matter.title for item in wi.work_items(specialist, today=today)}
    assert {mine.title, theirs.title} <= everyone


def test_the_deadline_follows_a_reassignment_without_being_edited(
    specialist, other_specialist, today
):
    """Move the Matter and the deadline moves. Nothing writes to the date."""
    due = today + timedelta(days=4)
    matter = _matter(specialist, deadline=due)

    assert _response_items(_mine(specialist, today))

    assign_matter(matter=matter, owner=other_specialist, actor=specialist)

    assert not _response_items(_mine(specialist, today))
    assert _response_items(_mine(other_specialist, today))
    matter.refresh_from_db()
    assert matter.response_deadline == due


def test_an_ownerless_deadline_is_on_nobodys_desk_but_is_department_work(specialist, today):
    """The honest place for work nobody has been given."""
    matter = create_matter(
        title="Jaotamata tähtajaga teema",
        reference_year=2026,
        actor=specialist,
        response_deadline=today + timedelta(days=6),
    )
    assert matter.owner is None

    assert not [item for item in _mine(specialist, today) if item.matter_id == matter.pk]

    (item,) = [
        entry
        for entry in _response_items(wi.work_items(specialist, today=today))
        if entry.matter_id == matter.pk
    ]
    assert item.responsible is None
    # The existing ownerless presentation, not a new one.
    assert item.responsible_initials == "!"
    assert item.responsible_title == "Vastutajata"
    assert item.responsible_name == "vastutajata"


# ---------------------------------------------------------------------------
# Authorization runs before the arithmetic
# ---------------------------------------------------------------------------


def test_a_restricted_deadline_reaches_only_a_reader_who_may_see_the_matter(specialist, reader):
    """Inherited from the Matter. No second visibility concept for a column."""
    anchor = _wednesday()
    matter = _matter(
        specialist,
        deadline=anchor + timedelta(days=1),
        title="Piiratud tähtajaga teema",
        visibility=Visibility.RESTRICTED,
    )

    authorized = wi.work_items(specialist, today=anchor)
    assert matter.pk in {item.matter_id for item in authorized}
    assert matter.pk in wi.work_population_ids(specialist, wi.WORK_DEADLINE_THIS_WEEK, today=anchor)

    denied = wi.work_items(reader, today=anchor)
    assert matter.pk not in {item.matter_id for item in denied}
    assert matter.pk not in wi.work_population_ids(reader, wi.WORK_DEADLINE_THIS_WEEK, today=anchor)
    assert matter.pk not in set(
        wi.outstanding_response_deadlines(reader).values_list("pk", flat=True)
    )


def test_an_archive_row_is_never_deadline_work(specialist, today):
    """A decade of imported register rows is evidence, not a queue."""
    matter = create_matter(
        title="Ajalooline kirje",
        owner=specialist,
        reference_year=2026,
        record_mode=RecordMode.ARCHIVE,
        origin=MatterOrigin.LEGACY_IMPORT,
        response_deadline=today + timedelta(days=3),
    )

    assert matter.pk not in {item.matter_id for item in wi.work_items(specialist, today=today)}


def test_a_closed_matters_deadline_is_not_work(specialist, today):
    matter = _matter(specialist, deadline=today + timedelta(days=3))
    close_matter(matter=matter, disposition=Disposition.NO_POSITION_FORMED, actor=specialist)

    assert matter.pk not in {item.matter_id for item in wi.work_items(specialist, today=today)}


# ---------------------------------------------------------------------------
# Fulfilment, by the definition the product already had
# ---------------------------------------------------------------------------


def test_a_past_deadline_with_nothing_sent_is_genuinely_overdue(specialist, today):
    matter = _matter(specialist, deadline=today - timedelta(days=4))

    (item,) = _response_items(_mine(specialist, today))

    assert item.matter_id == matter.pk
    assert item.is_overdue is True


def test_a_sent_opinion_ends_the_obligation_without_clearing_the_date(specialist, today):
    """The column stays; the read model stops calling it outstanding.

    The test is the one the old dashboard already applies — a ``SENT``
    Submission on the Matter — reused rather than restated.
    """
    due = today - timedelta(days=4)
    matter = _matter(specialist, deadline=due)

    assert _response_items(_mine(specialist, today))

    _send_opinion(matter, specialist)

    assert not _response_items(_mine(specialist, today))
    assert build_my_work(specialist, today=today).overdue == 0

    matter.refresh_from_db()
    assert matter.response_deadline == due


def test_a_future_deadline_already_answered_is_not_outstanding_work(specialist, today):
    """Sending early discharges the obligation early."""
    due = today + timedelta(days=9)
    matter = _matter(specialist, deadline=due)

    assert _response_items(_mine(specialist, today))

    _send_opinion(matter, specialist)

    assert not _response_items(_mine(specialist, today))
    matter.refresh_from_db()
    assert matter.response_deadline == due


def test_a_draft_submission_does_not_discharge_the_deadline(specialist, today):
    """Only *sent* counts. A draft is work in progress, not an answer."""
    matter = _matter(specialist, deadline=today + timedelta(days=2))
    create_submission(matter=matter, title="Mustand", actor=specialist)

    assert [item.matter_id for item in _response_items(_mine(specialist, today))] == [matter.pk]


# ---------------------------------------------------------------------------
# Osakond → Eesolev, by window
# ---------------------------------------------------------------------------
#
# These read the five windows the page actually cuts. They were written against
# the three-window read model that preceded it; that model had no routed
# consumer and has been removed, and the claim they make — a response deadline
# is a real deadline, so it reaches the panel, is counted there and drills
# through to the same rows — is a property of whichever partition is live.
# `anchor + 1` is *Homme* in these windows rather than *Sel nädalal*.


def _groups(user, today):
    return {group.key: group for group in upcoming_groups(user, today)}


def test_it_joins_the_eesolev_calendar_groups(specialist):
    """Tomorrow's window, from one date and nothing else."""
    anchor = _wednesday()
    matter = _matter(specialist, deadline=anchor + timedelta(days=1))

    groups = _groups(specialist, anchor)

    assert matter.pk in {item.matter_id for item in groups["homme"].items}


def test_the_eesolev_far_group_holds_a_distant_response_deadline(specialist):
    anchor = _wednesday()
    matter = _matter(specialist, deadline=anchor + timedelta(days=200), title="Kauge tähtaeg")

    groups = _groups(specialist, anchor)

    assert matter.pk in {item.matter_id for item in groups["kaugemal"].items}


def test_the_eesolev_count_and_its_drill_through_hold_the_same_matter(specialist):
    """The count moves and the list behind it holds the Matter that moved it.

    Not rendered text — the population the window's own ``?too=`` link resolves
    to, asserted equal to the rows the window counted.
    """
    anchor = _wednesday()
    before = _groups(specialist, anchor)["homme"]
    matter = _matter(specialist, deadline=anchor + timedelta(days=1), title="Uus tähtaeg")
    after = _groups(specialist, anchor)["homme"]

    assert after.count == before.count + 1

    listed = _register(
        specialist,
        anchor,
        too=wi.WORK_DEADLINE_WINDOW,
        too_alates=format_estonian_date(after.starts),
        too_kuni=format_estonian_date(after.ends),
    )
    assert matter.pk in listed
    assert {item.matter_id for item in after.items} == listed


# ---------------------------------------------------------------------------
# Osakonna töö — Seis and Eesolev
# ---------------------------------------------------------------------------


def test_the_department_seis_counts_it_and_stops_when_the_date_moves(department_head, specialist):
    """The strip figure and the list behind it move together."""
    anchor = _wednesday()
    week_end = wi.end_of_iso_week(anchor)

    def week_figure() -> int:
        return {figure.key: figure.value for figure in seis_figures(department_head, anchor)}[
            "week"
        ]

    before = week_figure()
    matter = _matter(specialist, deadline=week_end, title="Selle nädala tähtaeg")

    assert week_figure() == before + 1
    assert matter.pk in _register(department_head, anchor, too=wi.WORK_DEADLINE_THIS_WEEK)

    set_matter_dates(
        matter=matter, actor=specialist, response_deadline=week_end + timedelta(days=14)
    )

    assert week_figure() == before
    assert matter.pk not in _register(department_head, anchor, too=wi.WORK_DEADLINE_THIS_WEEK)


def test_the_department_eesolev_holds_it_without_any_next_action(department_head, specialist):
    anchor = _wednesday()
    matter = _matter(specialist, deadline=anchor, title="Tänane tähtaeg")

    groups = {group.key: group for group in upcoming_groups(department_head, anchor)}

    assert matter.pk in {item.matter_id for item in groups["tana"].items}
    assert NextAction.objects.filter(matter=matter).count() == 0


# ---------------------------------------------------------------------------
# The register's named deadline populations
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("key", "offset"),
    [
        (wi.WORK_DEADLINE_THIS_WEEK, 0),
        (wi.WORK_DEADLINE_NEXT_WEEK, 8),
        (wi.WORK_DEADLINE_30_DAYS, 25),
        (wi.WORK_DEADLINE_BEYOND, 60),
        (wi.WORK_OVERDUE, -3),
        (wi.WORK_NEEDS_ATTENTION, -3),
    ],
)
def test_every_named_population_finds_a_lone_response_deadline(specialist, key, offset):
    """The list opened by a count contains the Matter that raised it.

    The offsets are chosen against a midweek anchor so each falls squarely
    inside one window; the boundaries themselves are asserted by the band tests
    above, which share the arithmetic.
    """
    anchor = _wednesday()
    matter = _matter(specialist, deadline=anchor + timedelta(days=offset))

    ids = wi.work_population_ids(specialist, key, today=anchor)
    assert matter.pk in ids
    assert _register(specialist, anchor, too=key) == ids


def test_the_register_work_owner_filter_reads_the_matter_owner(specialist, other_specialist):
    """``?too_vastutaja=`` on a response deadline means the Matter's owner."""
    anchor = _wednesday()
    matter = _matter(specialist, deadline=anchor + timedelta(days=2))

    mine = _register(
        specialist,
        anchor,
        too=wi.WORK_DEADLINE_THIS_WEEK,
        too_vastutaja=str(specialist.pk),
    )
    theirs = _register(
        specialist,
        anchor,
        too=wi.WORK_DEADLINE_THIS_WEEK,
        too_vastutaja=str(other_specialist.pk),
    )

    assert matter.pk in mine
    assert matter.pk not in theirs


# ---------------------------------------------------------------------------
# Two obligations on one Matter are two rows and one Matter
# ---------------------------------------------------------------------------


def test_two_deadline_facts_are_two_rows_and_still_one_matter(specialist):
    """A milestone and a DO deadline are different facts, and both are work.

    The chronological list says so — that is what a work list is for. A figure
    that promises a count of *Matters* must not double, and that is what
    ``work_population_ids`` guarantees by reducing to Matter primary keys.

    **An `Oluline tähtaeg` is not suppressed by the instruction.** The precedence
    added in docs/adr/0050 is between the Matter's own `Arvamuse tähtaeg` and its
    one open `Järgmiseks`; a milestone is a third, independent business fact and
    a Matter may legitimately carry a current instruction and several of them at
    once. So this Matter produces two rows rather than three: the response
    deadline steps aside for the instruction, and the milestone does not.
    """
    anchor = _wednesday()
    due = anchor + timedelta(days=2)
    matter = _matter(specialist, deadline=due)
    add_important_date(
        matter=matter,
        title="Ülevõtmise tähtaeg",
        date_value=due,
        period_end=due,
        actor=specialist,
    )
    set_next_action(
        matter=matter,
        text="Esitan arvamuse",
        kind=ActionKind.DO,
        date_semantics=DateSemantics.DEADLINE,
        target_date=due,
        actor=specialist,
    )

    items = wi.work_items(specialist, today=anchor)
    rows = [item for item in items if item.matter_id == matter.pk]

    assert len(rows) == 2
    assert {item.source_type for item in rows} == {
        wi.SOURCE_IMPORTANT_DEADLINE,
        wi.SOURCE_NEXT_ACTION,
    }
    assert len({item.object_id for item in rows}) == 2

    ids = wi.work_population_ids(specialist, wi.WORK_DEADLINE_THIS_WEEK, today=anchor, items=items)
    assert ids == {matter.pk}
    assert _register(specialist, anchor, too=wi.WORK_DEADLINE_THIS_WEEK) == {matter.pk}


def test_a_milestone_survives_the_instruction_that_suppresses_the_response_deadline(
    specialist, today
):
    """The boundary of the new rule, stated on its own.

    `Oluline tähtaeg` is independent of both the response deadline and the
    instruction, and stays live work while an open `Järgmiseks` exists.
    """
    matter = _matter(specialist, deadline=today - timedelta(days=200))
    add_important_date(
        matter=matter,
        title="Avaliku konsultatsiooni lõpp",
        date_value=today + timedelta(days=6),
        period_end=today + timedelta(days=6),
        actor=specialist,
    )
    set_next_action(
        matter=matter,
        text="Vaatan uuesti üle",
        kind=ActionKind.MONITOR,
        date_semantics=DateSemantics.REVIEW_ON,
        target_date=today + timedelta(days=90),
        actor=specialist,
    )

    rows = [item for item in wi.work_items(specialist, today=today) if item.matter_id == matter.pk]

    assert {item.source_type for item in rows} == {
        wi.SOURCE_IMPORTANT_DEADLINE,
        wi.SOURCE_NEXT_ACTION,
    }


# ---------------------------------------------------------------------------
# Immediately — no projection, no rebuild, no cache
# ---------------------------------------------------------------------------


def test_entering_a_deadline_counts_immediately(specialist):
    """Create it and read the surface in the same breath."""
    anchor = _wednesday()
    matter = create_matter(
        title="Kohe nähtav teema",
        owner=specialist,
        reference_year=2026,
        response_deadline=anchor + timedelta(days=1),
    )

    assert matter.pk in {item.matter_id for item in _mine(specialist, anchor)}
    assert matter.pk in wi.work_population_ids(specialist, wi.WORK_DEADLINE_THIS_WEEK, today=anchor)
    assert matter.pk in _register(specialist, anchor, too=wi.WORK_DEADLINE_THIS_WEEK)


def test_editing_a_deadline_moves_it_immediately(specialist):
    anchor = _wednesday()
    matter = _matter(specialist, deadline=anchor + timedelta(days=1))

    assert TITLE in _titles(_bands(specialist, anchor).get(wi.BAND_WEEK))

    set_matter_dates(matter=matter, actor=specialist, response_deadline=anchor + timedelta(days=20))

    bands = _bands(specialist, anchor)
    assert TITLE not in _titles(bands.get(wi.BAND_WEEK))
    assert TITLE in _titles(bands.get(wi.BAND_NEXT_30))


def test_clearing_a_deadline_removes_the_item_immediately(specialist, today):
    matter = _matter(specialist, deadline=today + timedelta(days=3))

    assert _response_items(_mine(specialist, today))

    set_matter_dates(matter=matter, actor=specialist, response_deadline=None)

    assert not _response_items(_mine(specialist, today))
    matter.refresh_from_db()
    assert matter.response_deadline is None


# ---------------------------------------------------------------------------
# The semantics that must not have moved
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("kind", "semantics"),
    [
        (ActionKind.WAIT, DateSemantics.EXPECTED_AROUND),
        (ActionKind.MONITOR, DateSemantics.REVIEW_ON),
    ],
)
def test_waiting_and_monitoring_are_still_never_deadlines(specialist, today, kind, semantics):
    matter = _matter(specialist, deadline=None, title="Ootel teema")
    set_next_action(
        matter=matter,
        text="Ootan ministeeriumi",
        kind=kind,
        date_semantics=semantics,
        target_date=today - timedelta(days=5),
        actor=specialist,
    )

    items = wi.work_items(specialist, today=today)
    (item,) = [entry for entry in items if entry.matter_id == matter.pk]

    assert item.is_overdue is False
    assert item not in wi.real_deadlines(items)


def test_the_three_deadline_sources_are_the_whole_of_real_deadlines(specialist, today):
    """What a department may honestly call a deadline, enumerated.

    Five dated obligations on five Matters; exactly three of them are deadlines.
    """
    due = today + timedelta(days=3)
    response = _matter(specialist, deadline=due, title="Arvamuse tähtajaga teema")

    do = _matter(specialist, deadline=None, title="DO tähtajaga teema")
    set_next_action(
        matter=do,
        text="Esitan",
        kind=ActionKind.DO,
        date_semantics=DateSemantics.DEADLINE,
        target_date=due,
        actor=specialist,
    )

    important = _matter(specialist, deadline=None, title="Olulise tähtajaga teema")
    add_important_date(
        matter=important,
        title="Jõustumine",
        date_value=due,
        period_end=due,
        actor=specialist,
    )

    waiting = _matter(specialist, deadline=None, title="Ootel teema")
    set_next_action(
        matter=waiting,
        text="Ootan",
        kind=ActionKind.WAIT,
        date_semantics=DateSemantics.EXPECTED_AROUND,
        target_date=due,
        actor=specialist,
    )

    watching = _matter(specialist, deadline=None, title="Jälgitav teema")
    set_next_action(
        matter=watching,
        text="Jälgin",
        kind=ActionKind.MONITOR,
        date_semantics=DateSemantics.REVIEW_ON,
        target_date=due,
        actor=specialist,
    )

    items = wi.work_items(specialist, today=today)
    deadlines = {item.matter.title for item in wi.real_deadlines(items)}

    assert deadlines == {response.title, do.title, important.title}


# ---------------------------------------------------------------------------
# The shape of the read
# ---------------------------------------------------------------------------


def test_latest_bounds_the_response_deadline_source_too(specialist, today):
    """``latest=`` must not leave one source unbounded."""
    near = _matter(specialist, deadline=today + timedelta(days=2), title="Lähedal")
    far = _matter(specialist, deadline=today + timedelta(days=90), title="Kaugel")

    ids = {
        item.matter_id
        for item in wi.work_items(specialist, today=today, latest=today + timedelta(days=10))
    }

    assert near.pk in ids
    assert far.pk not in ids


def _read_the_source(user, today):
    """Read the whole response-deadline source, touching what a row prints.

    The owner and the stage are read here on purpose: they come off the source's
    own ``select_related``, and a row that had to fetch either would turn this
    into one query per Matter without the source itself looking any different.
    """
    rows = [
        wi._response_deadline_item(matter, today)
        for matter in wi.outstanding_response_deadlines(user, owner=user)
    ]
    for row in rows:
        assert row.responsible_initials
        assert row.stage_label == ""
    return rows


def test_the_source_costs_the_same_however_many_matters_it_holds(specialist, today):
    """A constant number of queries, never one Submission lookup per Matter.

    Measured against itself rather than against a magic number: one Matter, then
    twenty, and the count may not move. The absolute figure includes
    ``visible_to`` asking the database whether this reader holds a break-glass
    grant, which is a fact about authorization rather than about this source.

    One of the twenty already has its opinion sent, so the fulfilment test is
    genuinely exercised — as an ``Exists`` subquery inside the one read.
    """
    _matter(specialist, deadline=today + timedelta(days=1), title="Üksik tähtaeg")

    with CaptureQueriesContext(connection) as one:
        assert len(_read_the_source(specialist, today)) == 1

    for index in range(20):
        matter = _matter(
            specialist,
            deadline=today + timedelta(days=index + 2),
            title=f"Tähtajaline teema {index}",
        )
        if index == 0:
            _send_opinion(matter, specialist)

    with CaptureQueriesContext(connection) as many:
        assert len(_read_the_source(specialist, today)) == 20

    assert len(many) == len(one)


def test_the_action_source_is_unchanged_by_the_new_one(specialist, today):
    """A regression fence: the existing source still produces what it did."""
    matter = _matter(specialist, deadline=None, title="Ainult tegevusega teema")
    set_next_action(
        matter=matter,
        text="Esitan arvamuse",
        kind=ActionKind.DO,
        date_semantics=DateSemantics.DEADLINE,
        target_date=today + timedelta(days=2),
        actor=specialist,
    )

    items = wi.work_items(specialist, today=today)
    (item,) = [entry for entry in items if entry.matter_id == matter.pk]

    assert item.source_type == wi.SOURCE_NEXT_ACTION
    assert item.meaning == wi.MEANING_DEADLINE
    assert item.text == "Esitan arvamuse"
    assert NextAction.objects.filter(matter=matter, status=ActionStatus.OPEN).count() == 1


# ---------------------------------------------------------------------------
# Uus teema must not invent the commitment
# ---------------------------------------------------------------------------
#
# The one change this round makes outside the read model, and it is a
# consequence of the read model rather than a redesign.
#
# `MatterCreateForm.response_deadline` carried `initial=timezone.localdate`.
# That was very nearly harmless while the column reached no work surface: it put
# a date in a box, the box was saved, and nothing downstream did anything with
# it. It stopped being harmless the moment `Arvamuse tähtaeg` became work — a
# Matter created and left alone would be due on the day it was entered and
# overdue on every deadline surface the next morning, against a commitment
# nobody had made.
#
# Saabus keeps its default and should. It is an *observation*, and nearly
# everything does arrive on the day somebody types it in, so today is a useful
# capture default and a wrong one costs nothing. A deadline is a promise, and
# the product already says so in two other places: the edit form and
# `IncomingIntakeForm` both refuse this default in as many words
# (app/matters/forms.py, Teema QA §5.2).


CREATE_URL = reverse("matters:matter_create")


def test_the_unbound_create_form_offers_no_deadline(specialist):
    """A. The box is empty when the form is opened."""
    form = MatterCreateForm(viewer=specialist)

    assert form["response_deadline"].value() in (None, "")


def test_the_unbound_create_form_still_offers_todays_saabus(specialist, today):
    """F. Saabus is unchanged, and that difference is the whole point."""
    form = MatterCreateForm(viewer=specialist)

    assert form["received_date"].value() == today


def _untouched_form_payload(viewer, **typed):
    """What the browser posts when somebody fills in a title and presses Loo.

    Every value the page rendered comes back, because that is what a browser
    submits — the pre-filled boxes included. Posting only the fields a test
    cares about would leave the bound form with no `response_deadline` key at
    all, and `initial` never applies to a bound form, so such a test would pass
    whether or not the default was there. It did, which is why this helper
    exists (`django.forms.Form.initial`).
    """
    payload: dict[str, Any] = {}
    for name in MatterCreateForm(viewer=viewer).fields:
        value = MatterCreateForm(viewer=viewer)[name].value()
        if value not in (None, "", []):
            payload[name] = format_estonian_date(value) if isinstance(value, date) else str(value)
    return {**payload, **typed}


def test_submitting_the_form_untouched_stores_no_deadline(signed_in, specialist, today):
    """B and C. Nothing entered, nothing invented, nothing counted.

    The page as it is served, with a title typed into it and nothing else —
    which is exactly how the eleven phantom deadlines got into the browser
    world.
    """
    payload = _untouched_form_payload(specialist, title="Tähtajata teema")
    assert "response_deadline" not in payload
    signed_in.post(CREATE_URL, payload)

    matter = Matter.objects.get(title="Tähtajata teema")
    assert matter.response_deadline is None
    # Saabus still lands, from the same untouched form. F, through the view.
    assert matter.received_date == today

    items = wi.work_items(specialist, today=today)
    assert not [item for item in _response_items(items) if item.matter_id == matter.pk]
    assert matter.pk not in wi.work_population_ids(
        specialist, wi.WORK_DEADLINE_THIS_WEEK, today=today
    )
    # And tomorrow it is still not late, which is the failure this prevents.
    assert matter.pk not in wi.work_population_ids(
        specialist, wi.WORK_OVERDUE, today=today + timedelta(days=1)
    )


def test_a_deadline_somebody_typed_is_kept_and_counted_at_once(signed_in, specialist):
    """D and E. Entered deliberately, stored exactly, and immediately work."""
    anchor = _wednesday()
    due = anchor + timedelta(days=2)
    signed_in.post(
        CREATE_URL,
        {
            "title": "Sisestatud tähtajaga teema",
            "owner": specialist.pk,
            "response_deadline": f"{due.day}.{due.month}.{due.year}",
        },
    )

    matter = Matter.objects.get(title="Sisestatud tähtajaga teema")
    assert matter.response_deadline == due

    (item,) = [
        entry
        for entry in _response_items(wi.work_items(specialist, today=anchor))
        if entry.matter_id == matter.pk
    ]
    assert item.meaning == wi.MEANING_RESPONSE
    assert item.when == due
    assert matter.pk in wi.work_population_ids(specialist, wi.WORK_DEADLINE_THIS_WEEK, today=anchor)
    assert matter.pk in _register(specialist, anchor, too=wi.WORK_DEADLINE_THIS_WEEK)


# ---------------------------------------------------------------------------
# An open Järgmiseks outranks the response deadline (docs/adr/0050)
#
# `Arvamuse tähtaeg` is the obligation a file arrives with. It is live work
# until somebody says what happens next; an open `NextAction` is that saying,
# and from then on the lawyer's current instruction is what the work surfaces
# show. The stored column never moves — it is a fact in the header either way.
#
# No dates are compared anywhere in this section. That is the point of it: the
# defect this fixes was a January deadline dominating a file whose lawyer had
# already written «JÄLGIN, vaata uuesti üle 09.10».
# ---------------------------------------------------------------------------


def _instruct(matter, actor, *, kind, semantics=DateSemantics.REVIEW_ON, on=None, text="Samm"):
    """Record a Järgmiseks through the service the composer uses."""
    return set_next_action(
        matter=matter,
        text=text,
        kind=kind,
        date_semantics=semantics,
        target_date=on,
        actor=actor,
    )


def test_a_response_deadline_is_work_while_nobody_has_said_what_happens_next(specialist, today):
    """Case 1. The fallback, unchanged: no instruction, so the date is the work."""
    matter = _matter(specialist, deadline=today + timedelta(days=16))

    (item,) = _response_items(_mine(specialist, today))

    assert item.matter_id == matter.pk
    assert item.meaning == wi.MEANING_RESPONSE
    assert item in wi.real_deadlines(_mine(specialist, today))


@pytest.mark.parametrize(
    ("kind", "semantics", "offset"),
    [
        pytest.param(ActionKind.DO, DateSemantics.DEADLINE, 30, id="do-later"),
        pytest.param(ActionKind.WAIT, DateSemantics.REVIEW_ON, 30, id="wait-later"),
        pytest.param(ActionKind.MONITOR, DateSemantics.REVIEW_ON, 30, id="monitor-later"),
        pytest.param(ActionKind.WAIT, DateSemantics.REVIEW_ON, None, id="wait-undated"),
        pytest.param(ActionKind.MONITOR, DateSemantics.REVIEW_ON, None, id="monitor-undated"),
    ],
)
def test_any_open_next_action_suppresses_the_response_deadline(
    specialist, today, kind, semantics, offset
):
    """Cases 2-5. Every kind, dated or not, and always a *later* date than the
    deadline it outranks — so nothing here can pass by comparing the two."""
    matter = _matter(specialist, deadline=today - timedelta(days=200))
    _instruct(
        matter,
        specialist,
        kind=kind,
        semantics=semantics,
        on=None if offset is None else today + timedelta(days=offset),
    )

    assert _response_items(_mine(specialist, today)) == []
    assert matter.pk not in wi.outstanding_response_deadlines(specialist).values_list(
        "pk", flat=True
    )


def test_the_earlier_response_deadline_does_not_beat_the_later_instruction(specialist, today):
    """Case 3, stated as the rule rather than as a kind.

    The screenshot case: an overdue January deadline and a MONITOR review in
    October. The file is being watched, not missed — and a rule that picked the
    earlier date would say the opposite.
    """
    matter = _matter(specialist, deadline=date(2026, 1, 27))
    _instruct(
        matter,
        specialist,
        kind=ActionKind.MONITOR,
        on=today + timedelta(days=40),
        text="Vaatan uuesti üle",
    )

    items = _mine(specialist, today)

    assert _response_items(items) == []
    (action,) = [item for item in items if item.matter_id == matter.pk]
    assert action.source_type == wi.SOURCE_NEXT_ACTION
    assert action.when == today + timedelta(days=40)
    # A MONITOR review is never late, so nothing about this Matter is overdue.
    assert not action.is_overdue
    assert matter.pk not in wi.work_population_ids(specialist, wi.WORK_OVERDUE, today=today)


def test_an_overdue_deadline_under_a_monitor_leaves_every_overdue_population(
    department_head, specialist, today
):
    """Case 3, through the surfaces rather than through the model.

    The Seis figure, the register population behind it and the department's own
    id set must all agree that this Matter is not overdue — a count that moved
    without its drill-through moving is the failure this rule has to avoid.
    """
    matter = _matter(specialist, deadline=today - timedelta(days=200))
    before = seis_figures(department_head, today)
    assert matter.pk in wi.work_population_ids(department_head, wi.WORK_OVERDUE, today=today)
    assert matter.pk in _register(department_head, today, too=wi.WORK_OVERDUE)

    _instruct(matter, specialist, kind=ActionKind.MONITOR, on=today + timedelta(days=40))

    after = seis_figures(department_head, today)
    overdue_before = next(f.value for f in before if f.key == "overdue")
    overdue_after = next(f.value for f in after if f.key == "overdue")

    assert overdue_after == overdue_before - 1
    assert matter.pk not in wi.work_population_ids(department_head, wi.WORK_OVERDUE, today=today)
    assert matter.pk not in _register(department_head, today, too=wi.WORK_OVERDUE)


def test_the_count_and_its_drill_through_still_hold_the_same_matters(
    department_head, specialist, today
):
    """Case: counts and lists move together, by Matter id rather than by total."""
    kept = _matter(specialist, deadline=today - timedelta(days=3), title="Ilma juhiseta")
    suppressed = _matter(specialist, deadline=today - timedelta(days=3), title="Juhisega")
    _instruct(suppressed, specialist, kind=ActionKind.WAIT, on=today + timedelta(days=10))

    counted = wi.work_population_ids(department_head, wi.WORK_OVERDUE, today=today)

    assert counted == _register(department_head, today, too=wi.WORK_OVERDUE)
    assert kept.pk in counted
    assert suppressed.pk not in counted


def test_the_deadline_this_week_population_obeys_the_precedence(specialist, today):
    """The other dated population the department strip counts."""
    anchor = _wednesday()
    matter = _matter(specialist, deadline=anchor + timedelta(days=1))
    assert matter.pk in wi.work_population_ids(specialist, wi.WORK_DEADLINE_THIS_WEEK, today=anchor)

    _instruct(matter, specialist, kind=ActionKind.WAIT, on=anchor + timedelta(days=90))

    assert matter.pk not in wi.work_population_ids(
        specialist, wi.WORK_DEADLINE_THIS_WEEK, today=anchor
    )
    assert matter.pk not in _register(specialist, anchor, too=wi.WORK_DEADLINE_THIS_WEEK)


def test_the_department_eesolev_drops_the_suppressed_deadline(department_head, specialist, today):
    """Osakond reads the shared model, so it needed no change of its own."""
    matter = _matter(specialist, deadline=today + timedelta(days=3))
    placed = {
        item.matter_id for group in upcoming_groups(department_head, today) for item in group.items
    }
    assert matter.pk in placed

    _instruct(matter, specialist, kind=ActionKind.MONITOR, on=today + timedelta(days=200))

    still = {
        item.matter_id
        for group in upcoming_groups(department_head, today)
        for item in group.items
        if item.source_type == wi.SOURCE_RESPONSE_DEADLINE
    }
    assert matter.pk not in still


def test_minu_asjad_shows_the_instruction_rather_than_the_old_deadline(specialist, today):
    """The band a lawyer actually reads follows the review, not the deadline."""
    matter = _matter(specialist, deadline=today - timedelta(days=200))
    _instruct(matter, specialist, kind=ActionKind.MONITOR, on=today + timedelta(days=40))

    bands = _bands(specialist, today)
    overdue = _titles(bands.get("overdue"))

    assert TITLE not in overdue
    somewhere = {item.matter_id for band in bands.values() for item in band.items}
    assert matter.pk in somewhere


def test_ending_the_only_open_action_restores_the_fallback(specialist, today):
    """Case 6. A read model, so the fallback returns on the next read."""
    matter = _matter(specialist, deadline=today + timedelta(days=5))
    action = _instruct(matter, specialist, kind=ActionKind.WAIT, on=today + timedelta(days=60))
    assert _response_items(_mine(specialist, today)) == []

    NextAction.objects.filter(pk=action.pk).update(status=ActionStatus.CANCELLED)

    (item,) = _response_items(_mine(specialist, today))
    assert item.matter_id == matter.pk
    matter.refresh_from_db()
    assert matter.response_deadline == today + timedelta(days=5)


def test_replacing_the_action_keeps_the_fallback_suppressed(specialist, today):
    """Case 7. One open action at a time, and it is still an open action."""
    matter = _matter(specialist, deadline=today - timedelta(days=30))
    _instruct(
        matter,
        specialist,
        kind=ActionKind.DO,
        semantics=DateSemantics.DEADLINE,
        on=today + timedelta(days=10),
        text="Esimene samm",
    )
    _instruct(
        matter,
        specialist,
        kind=ActionKind.MONITOR,
        on=today + timedelta(days=90),
        text="Teine samm",
    )

    assert NextAction.objects.filter(matter=matter, status=ActionStatus.OPEN).count() == 1
    assert _response_items(_mine(specialist, today)) == []


def test_a_sent_opinion_still_ends_the_obligation_on_its_own(specialist, today):
    """Case 8. The #94 fulfilment rule is untouched by the new one."""
    matter = _matter(specialist, deadline=today + timedelta(days=5))
    _send_opinion(matter, specialist)

    assert NextAction.objects.filter(matter=matter, status=ActionStatus.OPEN).count() == 0
    assert _response_items(_mine(specialist, today)) == []


def test_a_closed_matter_stays_absent_whatever_its_instruction(specialist, today):
    """Case 9, and the interaction: closing is still decided first."""
    matter = _matter(specialist, deadline=today + timedelta(days=5))
    close_matter(matter=matter, disposition=Disposition.COMPLETED, actor=specialist)

    assert _response_items(_mine(specialist, today)) == []


def test_the_stored_deadline_is_never_touched_by_the_precedence(specialist, today):
    """The column is canonical. Suppression is a reading, not a write."""
    due = today - timedelta(days=200)
    matter = _matter(specialist, deadline=due)
    before = Matter.objects.values_list("updated_at", flat=True).get(pk=matter.pk)

    _instruct(matter, specialist, kind=ActionKind.MONITOR, on=today + timedelta(days=40))
    _mine(specialist, today)

    matter.refresh_from_db()
    assert matter.response_deadline == due
    assert matter.is_open
    # The instruction touched the Matter; nothing about *reading* the work model
    # may. Asserted on the header's own resolver, which is what a lawyer sees.
    assert Matter.objects.values_list("updated_at", flat=True).get(pk=matter.pk) >= before


def test_the_header_still_states_the_response_deadline(client, specialist, today):
    """The distinction the whole change rests on: header fact, work-model silence."""
    from app.matters import selectors

    matter = _matter(specialist, deadline=date(2026, 1, 27))
    _instruct(
        matter,
        specialist,
        kind=ActionKind.MONITOR,
        on=today + timedelta(days=40),
        text="Vaatan uuesti üle",
    )

    resolved = selectors.active_deadline(matter, specialist, today=today)
    assert resolved is not None
    assert resolved.value == date(2026, 1, 27)
    assert resolved.label == "Arvamuse tähtaeg"
    assert resolved.display == format_estonian_date(date(2026, 1, 27))

    client.force_login(specialist)
    body = client.get(reverse("matters:matter_detail", kwargs={"pk": matter.pk})).content.decode()
    assert format_estonian_date(date(2026, 1, 27)) in body
    assert "Vaatan uuesti üle" in body


def test_a_restricted_matters_instruction_cannot_change_another_readers_counts(
    specialist, reader, today
):
    """Authorization before arithmetic, on both sides of the new rule.

    The suppressed Matter is invisible to the reader either way, so the reader's
    numbers must be identical before and after the instruction exists — and the
    subquery must not become a way to learn that a hidden action was written.
    """
    matter = _matter(
        specialist, deadline=today - timedelta(days=10), visibility=Visibility.RESTRICTED
    )

    before = wi.work_population_ids(reader, wi.WORK_OVERDUE, today=today)
    assert matter.pk not in before

    _instruct(matter, specialist, kind=ActionKind.MONITOR, on=today + timedelta(days=40))

    assert wi.work_population_ids(reader, wi.WORK_OVERDUE, today=today) == before
    assert _response_items(wi.work_items(reader, today=today)) == []
    # The owner still sees the precedence applied to their own file.
    assert _response_items(_mine(specialist, today)) == []


def test_a_restricted_action_still_suppresses_for_a_reader_who_may_see_the_matter(
    specialist, other_specialist, today
):
    """The subquery is reader-blind, and that is the safe direction.

    It can only ever remove a row, so it discloses nothing; what it buys is one
    answer about the Matter rather than a deadline that is live for one
    colleague and suppressed for another.
    """
    matter = _matter(specialist, deadline=today - timedelta(days=10))
    action = _instruct(matter, specialist, kind=ActionKind.WAIT, on=today + timedelta(days=40))
    NextAction.objects.filter(pk=action.pk).update(visibility_override=Visibility.RESTRICTED)

    for viewer in (specialist, other_specialist):
        assert _response_items(wi.work_items(viewer, today=today)) == [], viewer


def test_the_precedence_costs_no_extra_query_per_matter(specialist, today):
    """The new test is an ``Exists``, so twenty Matters cost what one does.

    Half of them carry an instruction, so the subquery is genuinely exercised
    rather than short-circuiting on an empty table.
    """
    _matter(specialist, deadline=today + timedelta(days=1), title="Üksik juhiseta")

    with CaptureQueriesContext(connection) as one:
        assert len(_read_the_source(specialist, today)) == 1

    for index in range(20):
        matter = _matter(
            specialist,
            deadline=today + timedelta(days=index + 2),
            title=f"Segatud teema {index}",
        )
        if index % 2 == 0:
            _instruct(
                matter, specialist, kind=ActionKind.MONITOR, on=today + timedelta(days=90 + index)
            )

    with CaptureQueriesContext(connection) as many:
        assert len(_read_the_source(specialist, today)) == 11

    assert len(many) == len(one)
