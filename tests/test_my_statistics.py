"""Minu statistika (FUNC-003, docs/adr/0223): own figures, each credited by a stated rule.

The rebuild of ownership and open/closed state at the time is pure and tested on
synthetic trails first: a mid-month hand-over, a Lovable-provenance one, a name
that does not chain, a name two people carry, a trail that ends on someone
else, a Teema that existed before Juristid did, stable owner ids beside names
(a renamed person, a name a later account took, an unassignment), closing on a
month's last day, closing and reopening, a cut-over closing with no real day,
two hand-overs on one day. Then the coverage states (verified zero, partial,
at least, unknown) and the page: self only, the foldout's link and its stated
rule, the figures equal to the existing services, and a bounded number of
queries. Finally `assign_matter` itself: every hand-over now stores both
owners' ids next to their names.
"""

from __future__ import annotations

import datetime as dt
import re
import uuid
from pathlib import Path

import pytest
from django.db import connection
from django.test.utils import CaptureQueriesContext
from django.urls import NoReverseMatch, reverse
from django.utils import timezone

from app.accounts.models import User
from app.audit.enums import ChangeEventType
from app.audit.models import ChangeEvent
from app.matters import my_statistics as ms
from app.matters.models import Matter
from app.matters.my_work import opinion_work
from app.matters.services import LOVABLE_SYNC_PROVENANCE, assign_matter
from app.reporting.opinions import SOURCE_IMPORTED, SOURCE_SUBMISSION, MatterFacts, OpinionFact
from app.submissions.models import Submission
from tests import factories
from tests.report_builder_support import sent

TODAY = dt.date(2026, 11, 20)
A, B, C = "id-ann", "id-bert", "id-carl"
LONG_AGO = dt.datetime(2020, 1, 1, tzinfo=dt.UTC)
NAMES = {
    "Ann": [ms.Person(A, LONG_AGO)],
    "Bert": [ms.Person(B, LONG_AGO)],
    "Carl": [ms.Person(C, LONG_AGO)],
    "Kaks": [ms.Person("id-1", LONG_AGO), ms.Person("id-2", LONG_AGO)],
}
#: The trail has been recorded since long before any synthetic Teema here.
SINCE = dt.date(2020, 1, 1)
V, P, AL, U = (
    ms.Coverage.VERIFIED,
    ms.Coverage.PARTIAL,
    ms.Coverage.AT_LEAST,
    ms.Coverage.UNKNOWN,
)


def _at(day: dt.date, hour: int = 12) -> dt.datetime:
    return dt.datetime(day.year, day.month, day.day, hour, tzinfo=dt.UTC)


def row(pk, owner, created, *, received=None, year=None, is_open=True, closed=None):
    return {
        "pk": pk,
        "owner_id": owner,
        "created_at": _at(created),
        "received_date": received,
        "reference_year": year,
        "is_open": is_open,
        "closed_at": _at(closed) if closed else None,
    }


def handed(pk, day, from_name, to_name, *, hour=12, **extra):
    return {
        "matter_id": pk,
        "event_type": "MATTER_ASSIGNED",
        "occurred_at": _at(day, hour),
        "payload": {"from_name": from_name, "to_name": to_name, **extra},
    }


def state(pk, day, kind, *, hour=12):
    return {"matter_id": pk, "event_type": kind, "occurred_at": _at(day, hour), "payload": {}}


def build(rows, events=(), names=None, *, since=SINCE):
    return ms.build_ownership(
        list(rows), list(events), NAMES if names is None else names, TODAY, trail_since=since
    )


def month(year: int, number: int) -> ms.Month:
    return next(m for m in ms.months(TODAY, 24) if (m.start.year, m.start.month) == (year, number))


def owned(built, person, year, number) -> tuple[ms.Coverage, int | None]:
    found = ms.owned_at(built, person, month(year, number).end)
    return found.coverage, found.value


# ---------------------------------------------------------------------------
# Ownership at the time, rebuilt from synthetic trails
# ---------------------------------------------------------------------------


def test_a_hand_over_mid_month_credits_each_owner_with_their_own_days():
    built = build(
        [row(1, B, dt.date(2026, 1, 1), received=dt.date(2026, 1, 1))],
        [handed(1, dt.date(2026, 3, 15), "Ann", "Bert")],
    )

    assert built.owner_at(1, dt.date(2026, 3, 14)) == A
    assert built.owner_at(1, dt.date(2026, 3, 15)) == B
    # The current owner does not inherit February.
    assert owned(built, A, 2026, 2) == (V, 1)
    assert owned(built, B, 2026, 2) == (V, 0)
    assert owned(built, B, 2026, 3) == (V, 1)


def test_a_new_lawyer_who_receives_a_colleagues_teemad_is_not_credited_with_their_past():
    # Carl joined in October and got Ann's two Teemad on 2.11.
    rows = [row(n, C, dt.date(2026, 1, 1), received=dt.date(2026, 1, 1)) for n in (1, 2)]
    events = [handed(n, dt.date(2026, 11, 2), "Ann", "Carl") for n in (1, 2)]
    built = build(rows, events)
    facts = [_fact(1, dt.date(2026, 9, 10)), _fact(2, dt.date(2026, 11, 10))]

    assert owned(built, C, 2026, 10) == (V, 0)
    assert owned(built, A, 2026, 10) == (V, 2)
    assert owned(built, C, 2026, 11) == (V, 2)
    # Ann's September opinion stays Ann's; Carl's own November one is his.
    assert ms.sent_in_month(facts, built, C, month(2026, 9), SINCE).value == 0
    assert ms.sent_in_month(facts, built, A, month(2026, 9), SINCE).value == 1
    assert ms.sent_in_month(facts, built, C, month(2026, 11), SINCE).value == 1


def test_a_lovable_provenance_hand_over_counts_like_any_other():
    built = build(
        [row(1, C, dt.date(2026, 1, 1))],
        [handed(1, dt.date(2026, 6, 2), "Ann", "Carl", provenance_source="LOVABLE_SYNC")],
    )

    assert built.owner_at(1, dt.date(2026, 5, 31)) == A
    assert built.owner_at(1, dt.date(2026, 6, 30)) == C


def test_a_teema_that_existed_before_juristid_makes_those_months_unknown_not_zero():
    built = build([row(1, A, dt.date(2026, 11, 3), received=dt.date(2025, 4, 1))])

    assert built.owner_at(1, dt.date(2026, 10, 31)) == ms.UNKNOWN
    assert owned(built, A, 2026, 10) == (U, None)
    assert owned(built, A, 2026, 11) == (V, 1)
    # A month before the Teema arrived at all is not about it: a real zero.
    assert owned(built, A, 2025, 3) == (V, 0)


def test_a_known_teema_beside_an_unknown_one_is_a_lower_bound_not_hidden():
    built = build(
        [
            row(1, A, dt.date(2026, 1, 3), received=dt.date(2026, 1, 3)),
            row(2, A, dt.date(2026, 11, 3), received=dt.date(2025, 4, 1)),
        ]
    )

    # Teema 1 is certainly mine on 31.10; Teema 2's October is unknown.
    assert owned(built, A, 2026, 10) == (AL, 1)
    assert ms.owned_at(built, A, month(2026, 10).end).text == "vähemalt 1"


def test_a_teema_closed_before_it_reached_juristid_counts_nowhere_after_it_closed():
    built = build([row(1, A, dt.date(2026, 11, 3), year=2024, is_open=False)])

    # Before it arrived here: unknown whether it was open, and whose.
    assert owned(built, A, 2025, 6) == (U, None)
    assert built.open_at(1, dt.date(2026, 11, 3)) is False
    assert owned(built, A, 2026, 11) == (V, 0)


def test_an_imported_teema_with_a_recorded_closing_day_is_known_closed_since_then():
    built = build(
        [row(1, A, dt.date(2026, 11, 3), year=2024, is_open=False, closed=dt.date(2025, 2, 10))]
    )

    assert built.open_at(1, dt.date(2025, 2, 9)) == ms.UNKNOWN
    assert owned(built, A, 2025, 1) == (U, None)
    # Closed on 10.02.2025 by the register's own date: nothing to count after it.
    assert owned(built, A, 2025, 3) == (V, 0)


def test_names_that_do_not_chain_leave_that_stretch_unknown():
    built = build(
        [row(1, C, dt.date(2026, 1, 1))],
        [
            handed(1, dt.date(2026, 3, 1), "Ann", "Bert"),
            # From Carl, but the trail said Bert held it: Bert's stretch is unknown.
            handed(1, dt.date(2026, 5, 1), "Carl", "Carl"),
        ],
    )

    assert built.owner_at(1, dt.date(2026, 2, 1)) == A
    assert built.owner_at(1, dt.date(2026, 4, 1)) == ms.UNKNOWN
    assert built.owner_at(1, dt.date(2026, 6, 1)) == C


def test_a_name_two_people_carry_is_nobody_known():
    built = build([row(1, A, dt.date(2026, 1, 1))], [handed(1, dt.date(2026, 4, 1), "Kaks", "Ann")])

    assert built.owner_at(1, dt.date(2026, 2, 1)) == ms.UNKNOWN
    assert owned(built, A, 2026, 2) == (U, None)
    assert built.owner_at(1, dt.date(2026, 5, 1)) == A


def test_a_trail_that_ends_on_someone_else_than_the_owner_now_is_unknown_until_today():
    built = build([row(1, C, dt.date(2026, 1, 1))], [handed(1, dt.date(2026, 3, 1), "Ann", "Bert")])

    assert built.owner_at(1, dt.date(2026, 2, 1)) == A
    assert built.owner_at(1, dt.date(2026, 9, 1)) == ms.UNKNOWN
    assert built.owner_at(1, TODAY) == C


def test_an_unowned_stretch_is_nobodys():
    built = build([row(1, A, dt.date(2026, 1, 1))], [handed(1, dt.date(2026, 3, 1), None, "Ann")])

    assert built.owner_at(1, dt.date(2026, 2, 1)) is None
    assert owned(built, A, 2026, 2) == (V, 0)


def test_two_hand_overs_on_one_day_leave_the_last_owner_at_the_end_of_that_day():
    built = build(
        [row(1, C, dt.date(2026, 1, 1))],
        [
            handed(1, dt.date(2026, 4, 30), "Ann", "Bert", hour=9),
            handed(1, dt.date(2026, 4, 30), "Bert", "Carl", hour=15),
        ],
    )

    assert built.owner_at(1, dt.date(2026, 4, 29)) == A
    assert built.owner_at(1, dt.date(2026, 4, 30)) == C
    assert owned(built, B, 2026, 4) == (V, 0)
    assert owned(built, C, 2026, 4) == (V, 1)


def test_a_month_before_the_trail_was_recorded_is_never_a_verified_zero():
    # Recording began on 14.09.2026; the Teema arrived in 2026. December 2025
    # holds no Teema Juristid knows of — which proves nothing.
    built = build(
        [row(1, A, dt.date(2026, 9, 14), received=dt.date(2026, 2, 1))],
        since=dt.date(2026, 9, 14),
    )

    assert owned(built, A, 2025, 12) == (U, None)
    assert owned(built, A, 2026, 10) == (V, 1)


def test_nothing_is_known_when_the_trail_was_never_recorded():
    built = build([row(1, A, dt.date(2026, 1, 1))], since=None)

    assert owned(built, A, 2026, 6) == (U, None)


# ---------------------------------------------------------------------------
# Stable owner ids beside the names (ADR 0223 §6)
# ---------------------------------------------------------------------------


def test_an_events_owner_ids_are_preferred_to_its_names():
    # The names say Kaks (two people) and Ann; the ids say exactly who.
    built = build(
        [row(1, A, dt.date(2026, 1, 1))],
        [
            handed(
                1,
                dt.date(2026, 4, 1),
                "Kaks",
                "Ann",
                previous_owner_id="id-2",
                new_owner_id=A,
            )
        ],
    )

    assert built.owner_at(1, dt.date(2026, 2, 1)) == "id-2"
    assert built.owner_at(1, dt.date(2026, 5, 1)) == A


def test_a_renamed_person_keeps_their_events_that_carry_ids():
    # Ann is now called «Ann Kask»; the event wrote her old name and her id.
    names = {"Ann Kask": [ms.Person(A, LONG_AGO)], "Carl": [ms.Person(C, LONG_AGO)]}
    built = build(
        [row(1, C, dt.date(2026, 1, 1))],
        [handed(1, dt.date(2026, 6, 1), "Ann", "Carl", previous_owner_id=A, new_owner_id=C)],
        names,
    )

    assert built.owner_at(1, dt.date(2026, 5, 31)) == A
    assert owned(built, A, 2026, 5) == (V, 1)


def test_an_old_name_only_event_of_a_since_renamed_person_is_not_force_matched():
    names = {"Ann Kask": [ms.Person(A, LONG_AGO)], "Carl": [ms.Person(C, LONG_AGO)]}
    built = build(
        [row(1, C, dt.date(2026, 1, 1))], [handed(1, dt.date(2026, 6, 1), "Ann", "Carl")], names
    )

    assert built.owner_at(1, dt.date(2026, 5, 31)) == ms.UNKNOWN
    assert owned(built, A, 2026, 5) == (U, None)


def test_a_name_a_later_account_took_does_not_claim_an_older_event():
    # «Ann» today is a newcomer whose account was created after the hand-over.
    joined_later = dt.datetime(2026, 9, 1, tzinfo=dt.UTC)
    names = {"Ann": [ms.Person("id-newcomer", joined_later)], "Carl": [ms.Person(C, LONG_AGO)]}
    built = build(
        [row(1, C, dt.date(2026, 1, 1))], [handed(1, dt.date(2026, 6, 1), "Ann", "Carl")], names
    )

    assert built.owner_at(1, dt.date(2026, 5, 31)) == ms.UNKNOWN
    assert owned(built, "id-newcomer", 2026, 5) == (U, None)


def test_an_unassignment_with_ids_leaves_the_teema_nobodys():
    built = build(
        [row(1, None, dt.date(2026, 1, 1))],
        [handed(1, dt.date(2026, 6, 1), "Ann", None, previous_owner_id=A, new_owner_id=None)],
    )

    assert built.owner_at(1, dt.date(2026, 5, 31)) == A
    assert built.owner_at(1, dt.date(2026, 6, 30)) is None
    assert owned(built, A, 2026, 6) == (V, 0)


# ---------------------------------------------------------------------------
# Open or closed at the time
# ---------------------------------------------------------------------------


def test_a_teema_closed_on_a_months_last_day_is_not_open_work_at_that_months_end():
    built = build(
        [row(1, A, dt.date(2026, 1, 1), is_open=False, closed=dt.date(2026, 4, 30))],
        [state(1, dt.date(2026, 4, 30), "MATTER_CLOSED")],
    )

    assert owned(built, A, 2026, 3) == (V, 1)
    assert owned(built, A, 2026, 4) == (V, 0)


def test_a_closed_and_reopened_teema_is_not_counted_while_it_was_closed():
    built = build(
        [row(1, A, dt.date(2026, 1, 1), closed=dt.date(2026, 3, 10))],
        [
            state(1, dt.date(2026, 3, 10), "MATTER_CLOSED"),
            state(1, dt.date(2026, 5, 4), "MATTER_REOPENED"),
        ],
    )

    assert owned(built, A, 2026, 2) == (V, 1)
    assert owned(built, A, 2026, 3) == (V, 0)
    assert owned(built, A, 2026, 4) == (V, 0)
    assert owned(built, A, 2026, 5) == (V, 1)
    assert owned(built, A, 2026, 11) == (V, 1)


def test_reopened_and_closed_again_counts_only_the_reopened_stretch():
    built = build(
        [row(1, A, dt.date(2026, 1, 1), is_open=False, closed=dt.date(2026, 8, 3))],
        [
            state(1, dt.date(2026, 3, 10), "MATTER_CLOSED"),
            state(1, dt.date(2026, 5, 4), "MATTER_REOPENED"),
            state(1, dt.date(2026, 8, 3), "MATTER_CLOSED"),
        ],
    )

    assert [owned(built, A, 2026, n)[1] for n in (2, 3, 4, 5, 6, 7, 8, 9)] == [
        1,
        0,
        0,
        1,
        1,
        1,
        0,
        0,
    ]


def test_a_cut_over_closing_leaves_the_stretch_before_it_unknown():
    built = build(
        [row(1, A, dt.date(2026, 1, 1), is_open=False)],
        [state(1, dt.date(2026, 6, 15), "MATTER_HISTORICAL_CUTOVER_CLOSED")],
    )

    # When the work really stopped is not recorded: not claimed as workload.
    assert owned(built, A, 2026, 3) == (U, None)
    assert owned(built, A, 2026, 7) == (V, 0)


# ---------------------------------------------------------------------------
# Coverage states
# ---------------------------------------------------------------------------


def test_a_fully_recorded_month_with_nothing_in_it_is_a_verified_zero():
    found = ms.cell(month(2026, 9), dt.date(2026, 1, 1), 0)

    assert (found.coverage, found.value, found.text, found.note) == (V, 0, "0", "")


def test_a_month_recording_began_inside_shows_what_was_recorded_marked_partial():
    found = ms.cell(month(2026, 9), dt.date(2026, 9, 14), 3)

    assert (found.coverage, found.value) == (P, 3)
    assert found.covered_from == dt.date(2026, 9, 14)
    assert found.note == "osaline: alates 14.09.2026"


def test_the_running_month_is_partial_up_to_today():
    found = ms.cell(month(2026, 11), dt.date(2026, 1, 1), 2)

    assert (found.coverage, found.value) == (P, 2)
    assert found.note == "osaline: kuni 20.11.2026"


def test_a_running_month_recording_began_in_names_both_ends():
    assert ms.cell(month(2026, 11), dt.date(2026, 11, 5), 0).note == (
        "osaline: 05.11.2026–20.11.2026"
    )


def test_a_month_before_recording_is_unknown_and_records_found_there_a_lower_bound():
    empty = ms.cell(month(2026, 3), dt.date(2026, 9, 14), 0)
    found = ms.cell(month(2026, 3), dt.date(2026, 9, 14), 2)

    assert (empty.coverage, empty.text) == (U, "andmed puuduvad")
    assert (found.coverage, found.text, found.note) == (AL, "vähemalt 2", "ajalugu poolik")


def test_recording_starts_are_read_from_the_migrations_that_created_the_records(db):
    since = ms.recording_since()

    assert set(since) == set(ms.RECORDED_BY)
    # Every migration named exists, so on a migrated database each start is known.
    assert all(isinstance(day, dt.date) for day in since.values()), since


EVENT_OF = {
    "closed": "MATTER_CLOSED",
    "overviews": "WEBSITE_OVERVIEW_PUBLISHED",
    "engagements": "ENGAGEMENT_ADDED",
    "trail": "MATTER_ASSIGNED",
}


@pytest.mark.parametrize("key", sorted(EVENT_OF))
def test_each_event_figure_is_dated_by_the_first_migration_that_knows_its_event_type(key):
    migrations = sorted((Path(__file__).parents[1] / "app" / "audit" / "migrations").glob("0*.py"))
    quoted = re.compile(rf"[\"']{EVENT_OF[key]}[\"']")
    first = next(p.stem for p in migrations if quoted.search(p.read_text("utf-8")))

    assert ms.RECORDED_BY[key] == (("audit", first),)


# ---------------------------------------------------------------------------
# Sent opinions: the recorded actor, else the owner that day
# ---------------------------------------------------------------------------


def _fact(pk, on, *, actor=None, source=SOURCE_IMPORTED):
    matter = MatterFacts(pk, f"2026_{pk}", "Näidisseadus", 2026, None, "x", False, False, "")
    return OpinionFact(matter=matter, sent_on=on, deadline=None, source=source, actor_id=actor)


def test_an_opinion_is_credited_to_who_sent_it_not_to_the_owner():
    built = build([row(1, B, dt.date(2026, 1, 1))])
    facts = [_fact(1, dt.date(2026, 4, 2), actor=A, source=SOURCE_SUBMISSION)]

    assert ms.sent_in_month(facts, built, A, month(2026, 4), SINCE).value == 1
    assert ms.sent_in_month(facts, built, B, month(2026, 4), SINCE).value == 0


def test_an_imported_opinion_goes_to_the_owner_on_its_day():
    built = build([row(1, B, dt.date(2026, 1, 1))], [handed(1, dt.date(2026, 5, 1), "Ann", "Bert")])
    facts = [_fact(1, dt.date(2026, 4, 2)), _fact(1, dt.date(2026, 6, 2))]

    assert ms.sent_in_month(facts, built, A, month(2026, 4), SINCE).value == 1
    assert ms.sent_in_month(facts, built, B, month(2026, 4), SINCE).value == 0
    assert ms.sent_in_month(facts, built, B, month(2026, 6), SINCE).value == 1


def test_an_opinion_nobody_can_credit_makes_the_month_a_lower_bound_never_complete():
    built = build(
        [
            row(1, A, dt.date(2026, 11, 2), received=dt.date(2025, 1, 1)),
            row(2, A, dt.date(2026, 1, 1)),
        ]
    )
    nobody = [_fact(1, dt.date(2026, 3, 2))]
    mine_too = [*nobody, _fact(2, dt.date(2026, 3, 9), actor=A, source=SOURCE_SUBMISSION)]

    assert ms.sent_in_month(nobody, built, A, month(2026, 3), SINCE).coverage == U
    found = ms.sent_in_month(mine_too, built, A, month(2026, 3), SINCE)
    assert (found.coverage, found.value) == (AL, 1)
    # An empty month that was recorded in full is a real zero.
    assert ms.sent_in_month(nobody, built, A, month(2026, 4), SINCE).coverage == V


def test_an_empty_month_before_opinions_were_recorded_is_not_a_zero():
    built = build([row(1, A, dt.date(2026, 1, 1))])

    found = ms.sent_in_month([], built, A, month(2026, 2), dt.date(2026, 9, 14))

    assert (found.coverage, found.text) == (U, "andmed puuduvad")


def test_months_are_the_last_twelve_newest_first_the_current_one_ending_today():
    shown = ms.months(TODAY)

    assert len(shown) == 12
    assert (shown[0].start, shown[0].end) == (dt.date(2026, 11, 1), TODAY)
    assert not shown[0].complete and shown[1].complete
    assert (shown[-1].start, shown[-1].end) == (dt.date(2025, 12, 1), dt.date(2025, 12, 31))
    assert shown[0].label == "november 2026"


# ---------------------------------------------------------------------------
# The page
# ---------------------------------------------------------------------------


def _assigned(matter, day, from_name, to_name, **ids):
    return ChangeEvent.objects.create(
        matter=matter,
        event_type=ChangeEventType.MATTER_ASSIGNED,
        occurred_at=_at(day),
        object_type="matters.matter",
        object_id=matter.pk,
        payload={"from_name": from_name, "to_name": to_name, **ids},
    )


def _recorded_since(monkeypatch, day):
    monkeypatch.setattr(ms, "recording_since", lambda: dict.fromkeys(ms.RECORDED_BY, day))


def _cells(response, column: str) -> dict[dt.date, ms.Cell]:
    stats = response.context["stats"]
    index = [c.key for c in stats.columns].index(column)
    return {r.month.start: r.cells[index] for r in stats.rows}


def _month_start(today: dt.date, back: int) -> dt.date:
    day = today.replace(day=1)
    for _ in range(back):
        day = (day - dt.timedelta(days=1)).replace(day=1)
    return day


@pytest.fixture
def recorded_long_ago(monkeypatch):
    """The test database's migrations ran today; these pages need a trail from before."""
    _recorded_since(monkeypatch, dt.date(2020, 1, 1))


@pytest.fixture
def me(db):
    return factories.UserFactory(display_name="Mina Näidis")


@pytest.fixture
def colleague(db):
    return factories.UserFactory(display_name="Kolleeg Näidis")


@pytest.mark.django_db
def test_the_page_is_ones_own_and_reached_from_the_foldout(client, me, colleague):
    factories.MatterFactory(owner=me, title="Näidisseadus minu")
    client.force_login(me)

    page = client.get(reverse("matters:my_statistics"))
    desk = client.get(reverse("matters:my_work")).content.decode()

    assert page.status_code == 200
    assert reverse("matters:my_statistics") == "/minu-asjad/statistika/"
    assert f'href="{reverse("matters:my_statistics")}"' in desk
    assert "Kolleeg" not in page.content.decode()


@pytest.mark.django_db
def test_the_foldout_and_the_page_each_state_their_own_attribution_rule(client, me):
    client.force_login(me)

    desk = client.get(reverse("matters:my_work")).content.decode()
    page = client.get(reverse("matters:my_statistics")).content.decode()

    assert "Siinsed arvud: teema praegune vastutaja." in desk
    assert "loeb sinu tehtut ja vastutust tol ajal" in desk
    assert "Statistika ja Minu asjade kokkuvõte loevad iga teema selle" in page
    assert "<b>praegusele vastutajale</b>" in page
    assert "ilma et kumbki oleks vale" in page
    # No ranking words, no comparison with anybody.
    for word in ("edetabel", "koht ", "keskmine", "parim", "võrreldes"):
        assert word not in page.lower(), word


@pytest.mark.django_db
def test_there_is_no_address_for_anybody_elses(client, me, colleague):
    head = factories.DepartmentHeadFactory()
    client.force_login(head)

    with pytest.raises(NoReverseMatch):
        reverse("matters:my_statistics", args=[colleague.pk])
    assert client.get(f"/inimesed/{colleague.pk}/statistika/").status_code == 404
    assert client.get(f"/minu-asjad/statistika/{colleague.pk}/").status_code == 404
    # A colleague's desk does not offer it either.
    desk = client.get(reverse("matters:person_work", args=[colleague.pk])).content.decode()
    assert reverse("matters:my_statistics") not in desk
    # And a parameter naming somebody changes nothing: it is the head's own page.
    mine = client.get(reverse("matters:my_statistics")).context["stats"]
    theirs = client.get(reverse("matters:my_statistics") + f"?kasutaja={colleague.pk}")
    assert theirs.context["stats"] == mine


@pytest.mark.django_db
@pytest.mark.parametrize("parameter", ["kasutaja", "vastutaja", "jurist", "isik", "user", "pk"])
def test_no_url_parameter_shows_another_personas_figures(client, me, colleague, parameter):
    today = timezone.localdate()
    for number in range(3):
        matter = factories.MatterFactory(owner=colleague, title=f"Näidisseadus teise {number}")
        factories.EntryFactory(matter=matter, author=colleague)
        ChangeEvent.objects.create(
            matter=matter,
            event_type=ChangeEventType.MATTER_CLOSED,
            occurred_at=_at(today),
            actor=colleague,
            object_type="matters.matter",
            object_id=matter.pk,
            payload={},
        )
    client.force_login(me)

    plain = client.get(reverse("matters:my_statistics")).context["stats"]
    for value in (colleague.pk, colleague.display_name):
        response = client.get(reverse("matters:my_statistics"), {parameter: str(value)})
        assert response.context["stats"] == plain
        assert "Kolleeg Näidis" not in response.content.decode()
    figures = {figure.label: figure.value for figure in plain.current}
    assert figures["Avatud teemad"] == 0
    newest = plain.rows[0].cells
    assert all((c.value or 0) == 0 for c in newest), newest


@pytest.mark.django_db
def test_a_signed_out_reader_is_sent_to_sign_in(client):
    assert client.get(reverse("matters:my_statistics")).status_code == 302


@pytest.mark.django_db
def test_current_figures_equal_the_existing_services(client, me, colleague):
    factories.MatterFactory(owner=me, title="Näidisseadus 1")
    factories.MatterFactory(owner=me, title="Näidisseadus 2")
    factories.MatterFactory(owner=colleague, title="Näidisseadus 3")
    client.force_login(me)

    stats = client.get(reverse("matters:my_statistics")).context["stats"]

    work = opinion_work(me, me, timezone.localdate())
    figures = {figure.label: figure for figure in stats.current}
    assert figures["Avatud teemad"].value == 2
    assert figures["Arvamus koostamisel"].value == work.in_preparation
    assert figures["Arvamuse tähtaeg sel nädalal"].value == work.due_this_week
    assert {figure.basis for figure in stats.current} == {ms.CURRENT}


@pytest.mark.django_db
def test_an_opinion_i_sent_on_a_teema_a_colleague_owns_now_is_mine(
    client, me, colleague, capture_evidence
):
    today = timezone.localdate()
    matter = factories.MatterFactory(owner=colleague, title="Näidisseadus üle antud")
    submission = sent(matter, capture_evidence, day=today)
    Submission.objects.filter(pk=submission.pk).update(sent_by=me)

    client.force_login(me)
    mine = client.get(reverse("matters:my_statistics")).context["stats"].rows[0].cells[0]
    client.force_login(colleague)
    theirs = client.get(reverse("matters:my_statistics")).context["stats"].rows[0].cells[0]

    assert (mine.value, theirs.value) == (1, 0)


@pytest.mark.django_db
def test_a_genuine_zero_reads_0_and_months_before_recording_read_andmed_puuduvad(
    client, me, monkeypatch
):
    today = timezone.localdate()
    began = _month_start(today, 3)
    _recorded_since(monkeypatch, began)
    client.force_login(me)

    response = client.get(reverse("matters:my_statistics"))
    entries = _cells(response, "entries")

    assert entries[_month_start(today, 1)].coverage == V
    assert entries[_month_start(today, 1)].text == "0"
    assert entries[began].coverage == V
    assert entries[_month_start(today, 4)].coverage == U
    body = response.content.decode()
    assert 'data-coverage="verified">0<' in body
    assert "andmed puuduvad" in body


@pytest.mark.django_db
def test_the_running_month_and_a_month_recording_began_in_are_marked_partial(
    client, me, monkeypatch
):
    today = timezone.localdate()
    began = _month_start(today, 2).replace(day=14)
    _recorded_since(monkeypatch, began)
    matter = factories.MatterFactory(owner=me, title="Näidisseadus osaline")
    factories.EntryFactory(matter=matter, author=me, occurred_at=_at(began + dt.timedelta(days=3)))
    client.force_login(me)

    response = client.get(reverse("matters:my_statistics"))
    entries = _cells(response, "entries")

    current = entries[today.replace(day=1)]
    assert current.coverage == P and current.note.startswith("osaline: ")
    partial = entries[began.replace(day=1)]
    assert (partial.coverage, partial.value) == (P, 1)
    assert partial.note == f"osaline: alates {began:%d.%m.%Y}"
    assert f"osaline: alates {began:%d.%m.%Y}" in response.content.decode()


@pytest.mark.django_db
def test_entries_found_before_recording_began_are_shown_as_at_least(client, me, monkeypatch):
    today = timezone.localdate()
    _recorded_since(monkeypatch, today.replace(day=1))
    old = _month_start(today, 5)
    matter = factories.MatterFactory(owner=me, title="Näidisseadus vana")
    factories.EntryFactory(matter=matter, author=me, occurred_at=_at(old.replace(day=8)))
    client.force_login(me)

    response = client.get(reverse("matters:my_statistics"))

    found = _cells(response, "entries")[old]
    assert (found.coverage, found.text) == (AL, "vähemalt 1")
    assert "vähemalt 1" in response.content.decode()


@pytest.mark.django_db
def test_a_reassignment_in_the_trail_credits_the_earlier_owner(
    client, me, colleague, recorded_long_ago
):
    today = timezone.localdate()
    start = _month_start(today, 3)
    matter = factories.MatterFactory(
        owner=colleague, title="Näidisseadus antud", received_date=start
    )
    ChangeEvent.objects.filter(matter=matter).update(occurred_at=_at(start))
    Matter.all_objects.filter(pk=matter.pk).update(created_at=_at(start))
    # The accounts existed before the hand-over a name-only event records.
    User.objects.filter(pk__in=[me.pk, colleague.pk]).update(created_at=_at(start))
    _assigned(matter, today.replace(day=1), "Mina Näidis", "Kolleeg Näidis")
    client.force_login(me)

    owned_cells = _cells(client.get(reverse("matters:my_statistics")), "owned")

    last = owned_cells[_month_start(today, 1)]
    assert (last.coverage, last.value) == (V, 1)  # mine at the end of last month
    assert owned_cells[today.replace(day=1)].value == 0  # handed over this month


@pytest.mark.django_db
def test_a_renamed_lawyer_keeps_the_history_written_with_ids(
    client, me, colleague, recorded_long_ago
):
    today = timezone.localdate()
    start = _month_start(today, 3)
    matter = factories.MatterFactory(
        owner=colleague, title="Näidisseadus nimi", received_date=start
    )
    Matter.all_objects.filter(pk=matter.pk).update(created_at=_at(start))
    _assigned(
        matter,
        today.replace(day=1),
        "Mina Näidis",
        "Kolleeg Näidis",
        previous_owner_id=str(me.pk),
        new_owner_id=str(colleague.pk),
    )
    me.display_name = "Mina Uusnimi"
    me.save(update_fields=["display_name"])
    client.force_login(me)

    owned_cells = _cells(client.get(reverse("matters:my_statistics")), "owned")

    assert owned_cells[_month_start(today, 1)].value == 1


@pytest.mark.django_db
def test_a_renamed_lawyers_old_name_only_event_reads_unknown_not_someone_elses(
    client, me, colleague, recorded_long_ago
):
    today = timezone.localdate()
    start = _month_start(today, 3)
    matter = factories.MatterFactory(
        owner=colleague, title="Näidisseadus vana nimi", received_date=start
    )
    Matter.all_objects.filter(pk=matter.pk).update(created_at=_at(start))
    User.objects.filter(pk__in=[me.pk, colleague.pk]).update(created_at=_at(start))
    _assigned(matter, today.replace(day=1), "Mina Näidis", "Kolleeg Näidis")
    me.display_name = "Mina Uusnimi"
    me.save(update_fields=["display_name"])
    # A newcomer now carries the old name; the old event is not theirs.
    newcomer = factories.UserFactory(display_name="Mina Näidis")
    client.force_login(newcomer)
    theirs = _cells(client.get(reverse("matters:my_statistics")), "owned")
    client.force_login(me)
    mine = _cells(client.get(reverse("matters:my_statistics")), "owned")

    assert theirs[_month_start(today, 1)].coverage == U
    assert mine[_month_start(today, 1)].coverage == U


@pytest.mark.django_db
def test_a_closed_and_reopened_teema_on_the_page(client, me, recorded_long_ago):
    today = timezone.localdate()
    start = _month_start(today, 4)
    matter = factories.MatterFactory(owner=me, title="Näidisseadus taasavatud", received_date=start)
    Matter.all_objects.filter(pk=matter.pk).update(created_at=_at(start))
    for back, kind in ((3, ChangeEventType.MATTER_CLOSED), (1, ChangeEventType.MATTER_REOPENED)):
        ChangeEvent.objects.create(
            matter=matter,
            event_type=kind,
            occurred_at=_at(_month_start(today, back).replace(day=5)),
            actor=me,
            object_type="matters.matter",
            object_id=matter.pk,
            payload={},
        )
    client.force_login(me)

    owned_cells = _cells(client.get(reverse("matters:my_statistics")), "owned")

    assert [owned_cells[_month_start(today, back)].value for back in (4, 3, 2, 1, 0)] == [
        1,
        0,
        0,
        1,
        1,
    ]


@pytest.mark.django_db
def test_the_page_reads_a_bounded_number_of_queries(client, me, colleague):
    client.force_login(me)

    def count() -> int:
        with CaptureQueriesContext(connection) as queries:
            assert client.get(reverse("matters:my_statistics")).status_code == 200
        return len(queries)

    factories.MatterFactory(owner=me, title="Näidisseadus a")
    small = count()
    for number in range(8):
        matter = factories.MatterFactory(owner=me, title=f"Näidisseadus {number}")
        _assigned(matter, timezone.localdate(), "Kolleeg Näidis", "Mina Näidis")
        factories.EntryFactory(matter=matter, author=me)
    large = count()

    assert large == small, (small, large)
    assert large <= 40


@pytest.mark.django_db
def test_no_other_persons_name_is_rendered(client, me, colleague):
    matter = factories.MatterFactory(owner=colleague, title="Näidisseadus teise")
    _assigned(matter, timezone.localdate(), "Mina Näidis", "Kolleeg Näidis")
    client.force_login(me)

    body = client.get(reverse("matters:my_statistics")).content.decode()

    assert "Kolleeg Näidis" not in body
    assert str(uuid.UUID(str(colleague.pk))) not in body


# ---------------------------------------------------------------------------
# assign_matter: every hand-over stores both owners' ids beside their names
# ---------------------------------------------------------------------------


def _last_assignment(matter) -> dict:
    return (
        ChangeEvent.objects.filter(matter=matter, event_type=ChangeEventType.MATTER_ASSIGNED)
        .order_by("-occurred_at", "-pk")
        .first()
        .payload
    )


@pytest.mark.django_db
def test_an_interactive_hand_over_stores_both_ids_and_both_names(me, colleague):
    matter = factories.MatterFactory(owner=me, title="Näidisseadus käsitsi")

    assign_matter(matter=matter, owner=colleague, actor=me)

    payload = _last_assignment(matter)
    assert payload["previous_owner_id"] == str(me.pk)
    assert payload["new_owner_id"] == str(colleague.pk)
    assert (payload["from_name"], payload["to_name"]) == ("Mina Näidis", "Kolleeg Näidis")


@pytest.mark.django_db
def test_a_synchronisation_hand_over_stores_the_ids_with_its_provenance(me, colleague):
    matter = factories.MatterFactory(owner=me, title="Näidisseadus sünk")

    assign_matter(
        matter=matter,
        owner=colleague,
        actor=None,
        provenance={"provenance_source": LOVABLE_SYNC_PROVENANCE, "sync_run": "näidis"},
    )

    payload = _last_assignment(matter)
    assert payload["provenance_source"] == LOVABLE_SYNC_PROVENANCE
    assert (payload["previous_owner_id"], payload["new_owner_id"]) == (
        str(me.pk),
        str(colleague.pk),
    )


@pytest.mark.django_db
def test_an_unassignment_stores_no_new_owner_id(me):
    matter = factories.MatterFactory(owner=me, title="Näidisseadus vabaks")

    assign_matter(matter=matter, owner=None, actor=me)

    payload = _last_assignment(matter)
    assert payload["previous_owner_id"] == str(me.pk)
    assert payload["new_owner_id"] is None and payload["to_name"] is None


@pytest.mark.django_db
def test_a_hand_over_to_an_inactive_person_keeps_their_id(me):
    """An inactive owner arrives only through an operation — a synchronised or
    backfilled historical owner, which carries ``provenance``. A colleague cannot
    hand current work to a switched-off account (`assign_matter`,
    ASSIGNMENT_RECIPIENT_NOT_ASSIGNABLE on the assignment-integrity branch)."""
    gone = factories.UserFactory(display_name="Lahkunud Näidis", is_active=False)
    matter = factories.MatterFactory(owner=me, title="Näidisseadus lahkunu")

    assign_matter(
        matter=matter, owner=gone, actor=None, provenance={"source": "synthetic-operation"}
    )
    built = ms.load_ownership(me, timezone.localdate(), dt.date(2020, 1, 1))

    assert _last_assignment(matter)["new_owner_id"] == str(gone.pk)
    assert built.owner_at(matter.pk, timezone.localdate()) == str(gone.pk)


@pytest.mark.django_db
def test_older_name_only_events_are_left_as_they_were(me, colleague):
    matter = factories.MatterFactory(owner=colleague, title="Näidisseadus vana")
    old = _assigned(matter, timezone.localdate(), "Mina Näidis", "Kolleeg Näidis")

    assign_matter(matter=matter, owner=me, actor=me)
    old.refresh_from_db()

    assert old.payload == {"from_name": "Mina Näidis", "to_name": "Kolleeg Näidis"}
    assert not re.search(r"_owner_id", str(old.payload))
