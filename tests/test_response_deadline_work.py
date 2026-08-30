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

import pytest
from django.db import connection
from django.test.utils import CaptureQueriesContext
from django.utils import timezone

from app.core.dates import format_estonian_date
from app.core.enums import Visibility
from app.intelligence.services import add_important_date
from app.matters import work_items as wi
from app.matters.department_dashboard import seis_figures, upcoming_groups
from app.matters.enums import MatterOrigin, RecordMode
from app.matters.my_work import build_my_work
from app.matters.overview import deadline_groups
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
    assert item.action_kind_label == ""
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
# Ülevaade → Tähtajad
# ---------------------------------------------------------------------------


def _groups(user, today):
    items = wi.work_items(user, today=today)
    return {group.key: group for group in deadline_groups(items, today)}


def test_it_joins_the_ulevaade_calendar_groups(specialist):
    """This week, from one date and nothing else."""
    anchor = _wednesday()
    matter = _matter(specialist, deadline=anchor + timedelta(days=1))

    groups = _groups(specialist, anchor)

    assert matter.pk in {item.matter_id for item in groups["sel_nadalal"].items}


def test_the_ulevaade_far_group_holds_a_distant_response_deadline(specialist):
    anchor = _wednesday()
    matter = _matter(specialist, deadline=anchor + timedelta(days=200), title="Kauge tähtaeg")

    groups = _groups(specialist, anchor)

    assert matter.pk in {item.matter_id for item in groups["kaugemal"].items}


def test_the_ulevaade_count_and_its_drill_through_hold_the_same_matter(specialist):
    """The count moves and the list behind it holds the Matter that moved it.

    Not rendered text — the population the group's own ``?too=`` link resolves
    to, asserted equal to the rows the group counted.
    """
    anchor = _wednesday()
    before = _groups(specialist, anchor)["sel_nadalal"]
    matter = _matter(specialist, deadline=anchor + timedelta(days=1), title="Uus tähtaeg")
    after = _groups(specialist, anchor)["sel_nadalal"]

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
    """A response deadline, a milestone and a DO deadline are different facts.

    The chronological list says so — that is what a work list is for. A figure
    that promises a count of *Matters* must not double, and that is what
    ``work_population_ids`` guarantees by reducing to Matter primary keys.
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

    assert len(rows) == 3
    assert {item.source_type for item in rows} == {
        wi.SOURCE_RESPONSE_DEADLINE,
        wi.SOURCE_IMPORTANT_DEADLINE,
        wi.SOURCE_NEXT_ACTION,
    }
    assert len({item.object_id for item in rows}) == 3

    ids = wi.work_population_ids(specialist, wi.WORK_DEADLINE_THIS_WEEK, today=anchor, items=items)
    assert ids == {matter.pk}
    assert _register(specialist, anchor, too=wi.WORK_DEADLINE_THIS_WEEK) == {matter.pk}


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
