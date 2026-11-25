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
from app.matters import stat_periods as sp
from app.matters.models import Matter
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


def build(rows, events=(), names=None, *, since=SINCE, reliable: object = "since"):
    """``reliable`` defaults to ``since``: the trail reliably recorded from the start."""
    return ms.build_ownership(
        list(rows),
        list(events),
        NAMES if names is None else names,
        TODAY,
        trail_since=since,
        trail_reliable=since if reliable == "since" else reliable,  # type: ignore[arg-type]
    )


def month(year: int, number: int) -> sp.Span:
    """A calendar month, ending today if it is the current one (as the page counts it)."""
    period = sp.parse({"periood": "kuu", "algus": f"{year}-{number:02d}-01"}, TODAY)
    return period.counted


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
    assert ms.sent_in(facts, built, C, month(2026, 9), SINCE).value == 0
    assert ms.sent_in(facts, built, A, month(2026, 9), SINCE).value == 1
    assert ms.sent_in(facts, built, C, month(2026, 11), SINCE).value == 1


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
    assert ms.owned_at(built, A, month(2026, 10).end).spoken == "vähemalt 1"


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


def test_a_trail_recordable_but_not_yet_reliably_recorded_gives_lower_bounds_only():
    # The migration made the trail recordable on 01.01.2026; nobody recorded a
    # hand-over until 14.09.2026. Between the two, known Teemad are a lower bound.
    built = build(
        [row(1, A, dt.date(2026, 1, 2))],
        since=dt.date(2026, 1, 1),
        reliable=dt.date(2026, 9, 14),
    )

    assert owned(built, A, 2026, 6) == (AL, 1)
    assert owned(built, B, 2026, 6) == (U, None)
    assert owned(built, A, 2026, 10) == (V, 1)
    assert owned(built, B, 2026, 10) == (V, 0)
    # Never established: no month-end count is ever complete.
    never = build([row(1, A, dt.date(2026, 1, 2))], since=dt.date(2026, 1, 1), reliable=None)
    assert owned(never, A, 2026, 10) == (AL, 1)
    assert owned(never, B, 2026, 10) == (U, None)


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
# Coverage of a span: a real 0 only inside reliable coverage (ADR 0223 §11)
# ---------------------------------------------------------------------------


def test_a_fully_recorded_span_with_nothing_in_it_is_a_verified_zero():
    found = ms.cell(month(2026, 9), dt.date(2026, 1, 1), 0)

    assert (found.coverage, found.value, found.text, found.exact) == (V, 0, "0", True)


def test_a_span_recording_began_inside_is_a_lower_bound_marked_partial():
    found = ms.cell(month(2026, 9), dt.date(2026, 9, 14), 3)

    assert (found.coverage, found.value, found.text) == (P, 3, "≥ 3")
    assert found.covered_from == dt.date(2026, 9, 14)
    assert not found.exact
    # Nothing found in a partly covered span is not a zero either.
    assert ms.cell(month(2026, 9), dt.date(2026, 9, 14), 0).text == "—"


def test_the_running_period_counts_so_far_and_is_exact():
    found = ms.cell(month(2026, 11), dt.date(2026, 1, 1), 2)

    assert (found.coverage, found.text) == (V, "2")
    assert month(2026, 11).end == TODAY


def test_a_span_before_recording_is_unknown_and_records_found_there_a_lower_bound():
    empty = ms.cell(month(2026, 3), dt.date(2026, 9, 14), 0)
    found = ms.cell(month(2026, 3), dt.date(2026, 9, 14), 2)

    assert (empty.coverage, empty.text, empty.spoken) == (U, "—", "andmed puuduvad")
    assert (found.coverage, found.text, found.spoken) == (AL, "≥ 2", "vähemalt 2")


def test_coverage_that_cannot_be_established_is_never_complete():
    assert ms.cell(month(2026, 9), None, 0).coverage == U
    assert ms.cell(month(2026, 9), None, 4).text == "≥ 4"


def test_an_uncreditable_record_makes_a_covered_span_a_lower_bound():
    found = ms.cell(month(2026, 9), SINCE, 1, uncertain=True)

    assert (found.coverage, found.text) == (AL, "≥ 1")


def test_an_incoming_series_without_a_reading_is_unknown_not_zero():
    read = ms.Series(readings={dt.date(2026, 9, 3)}, since=dt.date(2026, 1, 1))
    span = month(2026, 9)

    assert read.cell(span) == ms.Cell(V, 0)
    assert read.cell(month(2026, 10)).coverage == U
    assert read.cell(month(2026, 10)).text == "—"


def test_an_incoming_source_first_read_inside_a_span_is_a_lower_bound_there():
    found = ms.Series(
        records=[ms.Record(dt.date(2026, 9, 20), 1)],
        readings={dt.date(2026, 9, 14), dt.date(2026, 9, 20)},
        since=dt.date(2026, 9, 14),
    )

    assert (found.cell(month(2026, 9)).coverage, found.cell(month(2026, 9)).text) == (P, "≥ 1")


# ---------------------------------------------------------------------------
# «Uued teemad»: hand-overs to me and Teemad created here with me as owner
# ---------------------------------------------------------------------------


def test_a_hand_over_to_me_is_an_arrival_and_a_back_fill_is_not():
    built = build(
        [row(1, A, dt.date(2026, 1, 1)), row(2, A, dt.date(2026, 1, 1))],
        [
            handed(1, dt.date(2026, 3, 15), "Bert", "Ann"),
            handed(2, dt.date(2026, 3, 16), "Bert", "Ann", operation="backfill_legacy_owners"),
        ],
    )

    found = ms.arrivals_series(built, [], A, SINCE)

    assert [(r.matter_id, r.day) for r in found.records] == [(1, dt.date(2026, 3, 15))]
    assert found.cell(month(2026, 3)).value == 1
    assert ms.arrivals_series(built, [], B, SINCE).cell(month(2026, 3)).value == 0


def test_a_teema_created_here_with_me_as_owner_is_mine_once_per_day():
    built = build(
        [row(1, A, dt.date(2026, 4, 2)), row(2, B, dt.date(2026, 4, 2))],
        [handed(1, dt.date(2026, 4, 2), "", "Ann", hour=13)],
    )
    created = [(1, dt.date(2026, 4, 2)), (2, dt.date(2026, 4, 2))]

    found = ms.arrivals_series(built, created, A, SINCE)

    assert [(r.matter_id, r.day) for r in found.records] == [(1, dt.date(2026, 4, 2))]


def test_a_hand_over_to_somebody_nobody_can_tell_apart_is_a_lower_bound():
    built = build([row(1, A, dt.date(2026, 1, 1))], [handed(1, dt.date(2026, 5, 4), "Ann", "Kaks")])

    found = ms.arrivals_series(built, [], A, SINCE).cell(month(2026, 5))

    assert (found.coverage, found.text) == (U, "—")


def test_recording_starts_are_read_from_the_migrations_that_created_the_records(db):
    since = ms.recording_since()

    assert set(since) == set(ms.RECORDED_BY)
    # Every migration named exists, so on a migrated database each start is known.
    assert all(isinstance(day, dt.date) for day in since.values()), since


EVENT_OF = {
    "closed": "MATTER_CLOSED",
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

    assert ms.sent_in(facts, built, A, month(2026, 4), SINCE).value == 1
    assert ms.sent_in(facts, built, B, month(2026, 4), SINCE).value == 0


def test_an_imported_opinion_goes_to_the_owner_on_its_day():
    built = build([row(1, B, dt.date(2026, 1, 1))], [handed(1, dt.date(2026, 5, 1), "Ann", "Bert")])
    facts = [_fact(1, dt.date(2026, 4, 2)), _fact(1, dt.date(2026, 6, 2))]

    assert ms.sent_in(facts, built, A, month(2026, 4), SINCE).value == 1
    assert ms.sent_in(facts, built, B, month(2026, 4), SINCE).value == 0
    assert ms.sent_in(facts, built, B, month(2026, 6), SINCE).value == 1


def test_an_opinion_nobody_can_credit_makes_the_month_a_lower_bound_never_complete():
    built = build(
        [
            row(1, A, dt.date(2026, 11, 2), received=dt.date(2025, 1, 1)),
            row(2, A, dt.date(2026, 1, 1)),
        ]
    )
    nobody = [_fact(1, dt.date(2026, 3, 2))]
    mine_too = [*nobody, _fact(2, dt.date(2026, 3, 9), actor=A, source=SOURCE_SUBMISSION)]

    assert ms.sent_in(nobody, built, A, month(2026, 3), SINCE).coverage == U
    found = ms.sent_in(mine_too, built, A, month(2026, 3), SINCE)
    assert (found.coverage, found.value) == (AL, 1)
    # An empty month that was recorded in full is a real zero.
    assert ms.sent_in(nobody, built, A, month(2026, 4), SINCE).coverage == V


def test_an_empty_month_before_opinions_were_recorded_is_not_a_zero():
    built = build([row(1, A, dt.date(2026, 1, 1))])

    found = ms.sent_in([], built, A, month(2026, 2), dt.date(2026, 9, 14))

    assert (found.coverage, found.spoken) == (U, "andmed puuduvad")


# ---------------------------------------------------------------------------
# The period bar: parsing, labels, navigation and the chart's columns
# ---------------------------------------------------------------------------

WEDNESDAY = dt.date(2026, 11, 25)


def test_the_default_is_the_current_week_monday_to_sunday():
    period = sp.parse({}, WEDNESDAY)

    assert (period.kind, period.start, period.end) == (
        "nadal",
        dt.date(2026, 11, 23),
        dt.date(2026, 11, 29),
    )
    assert period.label == "See nädal"
    assert period.range_text == "23.11.2026–29.11.2026"
    assert period.until == WEDNESDAY and period.running and period.is_current
    # › is disabled past the current period; ‹ is not.
    assert not period.has_next and period.has_previous


def test_the_previous_week_is_named_and_can_step_forward():
    period = sp.parse({"periood": "nadal", "algus": "2026-11-18"}, WEDNESDAY)

    assert period.start == dt.date(2026, 11, 16)
    assert period.label == "Eelmine nädal"
    assert period.has_next and not period.running
    assert period.shifted(1) == sp.parse({}, WEDNESDAY)
    assert sp.parse({"algus": "2026-11-02"}, WEDNESDAY).label == "45. nädal 2026"


@pytest.mark.parametrize(
    ("kind", "start", "label", "first", "last"),
    [
        ("kuu", "2026-11-25", "November 2026", dt.date(2026, 11, 1), dt.date(2026, 11, 30)),
        ("kvartal", "2026-11-25", "IV kvartal 2026", dt.date(2026, 10, 1), dt.date(2026, 12, 31)),
        ("kvartal", "2026-05-02", "II kvartal 2026", dt.date(2026, 4, 1), dt.date(2026, 6, 30)),
        ("aasta", "2025-03-03", "2025", dt.date(2025, 1, 1), dt.date(2025, 12, 31)),
    ],
)
def test_calendar_periods_snap_to_their_calendar(kind, start, label, first, last):
    period = sp.parse({"periood": kind, "algus": start}, WEDNESDAY)

    assert (period.kind, period.label, period.start, period.end) == (kind, label, first, last)


def test_stepping_crosses_years_and_stops_at_the_current_period():
    january = sp.parse({"periood": "kuu", "algus": "2026-01-01"}, WEDNESDAY)
    first_quarter = sp.parse({"periood": "kvartal", "algus": "2026-01-01"}, WEDNESDAY)

    assert january.previous.label == "Detsember 2025"
    assert first_quarter.previous.label == "IV kvartal 2025"
    assert sp.parse({"periood": "kuu"}, WEDNESDAY).has_next is False
    assert sp.parse({"periood": "aasta", "algus": "2025-01-01"}, WEDNESDAY).has_next is True


@pytest.mark.parametrize(
    "params",
    [
        {"periood": "nädal-x"},
        {"periood": "nadal", "algus": "eile"},
        {"periood": "nadal", "algus": "2027-01-04"},
        {"periood": "nadal", "algus": "1999-01-04"},
    ],
)
def test_anything_the_bar_does_not_understand_is_the_current_week(params):
    assert sp.parse(params, WEDNESDAY) == sp.current(WEDNESDAY)


def test_a_custom_period_is_ordered_and_cut_at_today():
    period = sp.parse({"periood": "muu", "algus": "2026-12-31", "kuni": "2026-10-01"}, WEDNESDAY)

    assert (period.start, period.end) == (dt.date(2026, 10, 1), WEDNESDAY)
    assert period.label == "Valitud periood"
    assert not period.has_next
    assert period.previous.end == dt.date(2026, 9, 30)


def test_the_comparison_is_the_previous_period_to_the_same_point():
    week = sp.parse({}, WEDNESDAY)
    february = sp.parse({"periood": "kuu", "algus": "2026-03-01"}, WEDNESDAY).previous

    # Monday to Wednesday against last Monday to Wednesday.
    assert week.comparison == sp.Span(dt.date(2026, 11, 16), dt.date(2026, 11, 18))
    assert week.comparison_word == "eelmise nädalaga"
    # A whole past month against the whole one before it, however long.
    assert february.comparison == sp.Span(dt.date(2026, 1, 1), dt.date(2026, 1, 28))
    march = sp.parse({"periood": "kuu", "algus": "2026-03-01"}, WEDNESDAY)
    assert march.comparison == sp.Span(dt.date(2026, 2, 1), dt.date(2026, 2, 28))


def test_the_chart_columns_follow_the_period():
    week = sp.parse({}, WEDNESDAY).buckets()
    quarter = sp.parse({"periood": "kvartal"}, WEDNESDAY).buckets()
    year = sp.parse({"periood": "aasta"}, WEDNESDAY).buckets()
    long_custom = sp.parse(
        {"periood": "muu", "algus": "2026-01-01", "kuni": "2026-11-25"}, WEDNESDAY
    )
    short_custom = sp.parse(
        {"periood": "muu", "algus": "2026-11-01", "kuni": "2026-11-20"}, WEDNESDAY
    )

    assert [b.label for b in week] == ["E", "T", "K", "N", "R", "L", "P"]
    assert week[0].sublabel == "23.11"
    assert [b.future for b in week] == [False, False, False, True, True, True, True]
    assert quarter[0].span == sp.Span(dt.date(2026, 10, 1), dt.date(2026, 10, 4))
    assert quarter[0].label == "40. n"
    assert [b.label for b in year][:2] == ["jaan", "veebr"] and len(year) == 12
    assert long_custom.grain == "month" and short_custom.grain == "day"
    assert (
        sp.parse({"periood": "muu", "algus": "2026-09-01", "kuni": "2026-11-01"}, WEDNESDAY).grain
        == "week"
    )


def test_the_period_is_kept_in_the_url():
    period = sp.parse({"periood": "muu", "algus": "2026-10-01", "kuni": "2026-10-31"}, WEDNESDAY)

    url = period.url("/minu-asjad/statistika/", naitaja="sissekanded")

    assert url == (
        "/minu-asjad/statistika/?periood=muu&algus=2026-10-01&kuni=2026-10-31&naitaja=sissekanded"
    )


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


def _recorded_since(monkeypatch, day, *, reliable: bool = True):
    """Recording possible from ``day``; with ``reliable``, reliably covered from it too."""
    monkeypatch.setattr(ms, "recording_since", lambda: dict.fromkeys(ms.RECORDED_BY, day))
    if reliable:
        monkeypatch.setattr(
            ms, "reliable_since", lambda user, recording: dict.fromkeys(ms.RELIABLE_BY, day)
        )


def _month_start(today: dt.date, back: int) -> dt.date:
    day = today.replace(day=1)
    for _ in range(back):
        day = (day - dt.timedelta(days=1)).replace(day=1)
    return day


URL = "/minu-asjad/statistika/"


def _page(client, **params):
    response = client.get(reverse("matters:my_statistics"), params)
    assert response.status_code == 200
    return response


def _month(client, start: dt.date, **params):
    return _page(client, periood="kuu", algus=start.isoformat(), **params)


def _card(response, key: str) -> ms.Card:
    stats = response.context["stats"]
    return next(card for group in stats.groups for card in group.cards if card.key == key)


def _cell(response, key: str) -> ms.Cell:
    return _card(response, key).cell


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
    assert reverse("matters:my_statistics") == URL
    assert f'href="{URL}"' in desk
    assert "Kolleeg" not in page.content.decode()


@pytest.mark.django_db
def test_the_long_explanations_the_owner_removed_are_gone(client, me):
    client.force_login(me)

    desk = client.get(reverse("matters:my_work")).content.decode()
    page = _page(client).content.decode()

    assert "Siinsed arvud" not in desk
    assert "loeb sinu tehtut ja vastutust tol ajal" not in desk
    for gone in (
        "ainult sinu enda andmed",
        "Siin on ainult sinu enda arvud",
        "Kuidas arvestatakse",
        "Vastutaja tol ajal",
        "Kui palju on teada",
        "Salvestatud alates",
        "praegune vastutaja",
    ):
        assert gone not in page, gone
    # No ranking, no comparison with anybody: only with one's own previous period.
    for word in ("edetabel", "keskmine", "parim"):
        assert word not in page.lower(), word


@pytest.mark.django_db
def test_the_page_shows_eight_own_figures_and_five_incoming_ones(client, me):
    client.force_login(me)

    stats = _page(client).context["stats"]

    assert [group.title for group in stats.groups] == ["Minu töö", "Sissetulev seire"]
    assert [card.key for card in stats.groups[0].cards] == [f.key for f in ms.OWN]
    assert len(stats.groups[0].cards) == 8 and len(stats.groups[1].cards) == 5
    # Each card's ⓘ is one short definition.
    for figure in (*ms.OWN, *ms.INCOMING_FIGURES):
        assert 20 < len(figure.help) < 260, figure.key
    assert stats.selected.key == ms.DEFAULT_FIGURE
    assert stats.period.label == "See nädal"


@pytest.mark.django_db
def test_there_is_no_address_for_anybody_elses(client, me, colleague):
    head = factories.DepartmentHeadFactory()
    client.force_login(head)

    with pytest.raises(NoReverseMatch):
        reverse("matters:my_statistics", args=[colleague.pk])
    assert client.get(f"/inimesed/{colleague.pk}/statistika/").status_code == 404
    assert client.get(f"/minu-asjad/statistika/{colleague.pk}/").status_code == 404
    desk = client.get(reverse("matters:person_work", args=[colleague.pk])).content.decode()
    assert URL not in desk
    mine = _page(client).context["stats"]
    theirs = _page(client, kasutaja=str(colleague.pk)).context["stats"]
    assert theirs.groups == mine.groups


@pytest.mark.django_db
@pytest.mark.parametrize("parameter", ["kasutaja", "vastutaja", "jurist", "isik", "user", "pk"])
def test_no_url_parameter_shows_another_personas_figures(client, me, colleague, parameter):
    today = timezone.localdate()
    for number in range(3):
        matter = factories.MatterFactory(owner=colleague, title=f"Näidisseadus teise {number}")
        factories.EntryFactory(matter=matter, author=colleague)
        _event(matter, ChangeEventType.MATTER_CLOSED, today, actor=colleague)
    client.force_login(me)

    plain = _page(client).context["stats"]
    for value in (colleague.pk, colleague.display_name):
        response = _page(client, **{parameter: str(value)})
        assert response.context["stats"].groups == plain.groups
        assert "Kolleeg Näidis" not in response.content.decode()
    assert all((card.cell.value or 0) == 0 for card in plain.groups[0].cards)


@pytest.mark.django_db
def test_a_signed_out_reader_is_sent_to_sign_in(client):
    assert client.get(reverse("matters:my_statistics")).status_code == 302


# -- each figure ---------------------------------------------------------------


@pytest.mark.django_db
def test_an_opinion_i_sent_on_a_teema_a_colleague_owns_now_is_mine(
    client, me, colleague, capture_evidence
):
    today = timezone.localdate()
    matter = factories.MatterFactory(owner=colleague, title="Näidisseadus üle antud")
    submission = sent(matter, capture_evidence, day=today)
    Submission.objects.filter(pk=submission.pk).update(sent_by=me)
    # Sending is recorded in Juristid from today on, so a span with none is a 0.
    _event(matter, ChangeEventType.SUBMISSION_SENT, today, actor=me)

    client.force_login(me)
    mine = _cell(_page(client), "saadetud")
    client.force_login(colleague)
    theirs = _cell(_page(client), "saadetud")

    assert (mine.value, theirs.value) == (1, 0)


@pytest.mark.django_db
def test_entries_and_closings_are_what_i_recorded(client, me, colleague, recorded_long_ago):
    today = timezone.localdate()
    mine = factories.MatterFactory(owner=me, title="Näidisseadus sissekanne")
    factories.EntryFactory(matter=mine, author=me, occurred_at=_at(today))
    factories.EntryFactory(matter=mine, author=colleague, occurred_at=_at(today))
    _event(mine, ChangeEventType.MATTER_CLOSED, today, actor=me, payload={"disposition": "OTHER"})
    client.force_login(me)

    response = _page(client)

    assert _cell(response, "sissekanded") == ms.Cell(V, 1)
    assert _cell(response, "lopetatud") == ms.Cell(V, 1)


@pytest.mark.django_db
def test_a_teema_closed_without_any_opinion_is_counted_and_one_with_an_opinion_is_not(
    client, me, capture_evidence, recorded_long_ago
):
    today = timezone.localdate()
    silent = factories.MatterFactory(owner=me, title="Näidisseadus vaikne")
    answered = factories.MatterFactory(owner=me, title="Näidisseadus vastatud")
    submission = sent(answered, capture_evidence, day=today)
    Submission.objects.filter(pk=submission.pk).update(sent_by=me)
    for matter in (silent, answered):
        _event(
            matter,
            ChangeEventType.MATTER_CLOSED,
            today,
            actor=me,
            payload={"disposition": "NO_POSITION_FORMED"},
        )
    client.force_login(me)

    response = _page(client, naitaja="arvamuseta")

    assert _cell(response, "lopetatud").value == 2
    assert _cell(response, "arvamuseta").value == 1
    rows = response.context["stats"].drill.rows
    assert [row.title for row in rows] == ["Näidisseadus vaikne"]
    assert rows[0].detail == "Seisukohta ei kujundatud"


@pytest.mark.django_db
def test_new_teemad_are_hand_overs_to_me_and_teemad_created_with_me(
    client, me, colleague, recorded_long_ago
):
    today = timezone.localdate()
    handed_over = factories.MatterFactory(owner=me, title="Näidisseadus antud")
    Matter.all_objects.filter(pk=handed_over.pk).update(created_at=_at(_month_start(today, 2)))
    _assigned(
        handed_over,
        today,
        "Kolleeg Näidis",
        "Mina Näidis",
        previous_owner_id=str(colleague.pk),
        new_owner_id=str(me.pk),
    )
    created = factories.MatterFactory(owner=me, title="Näidisseadus loodud")
    _event(created, ChangeEventType.MATTER_CREATED, today, actor=me)
    theirs = factories.MatterFactory(owner=colleague, title="Näidisseadus kolleegi")
    _event(theirs, ChangeEventType.MATTER_CREATED, today, actor=colleague)
    client.force_login(me)

    response = _page(client, naitaja="uued")

    assert _cell(response, "uued") == ms.Cell(V, 2)
    whats = {row.title: row.what for row in response.context["stats"].drill.rows}
    assert whats == {
        "Näidisseadus antud": "Sulle üle antud",
        "Näidisseadus loodud": "Loodud sinuga vastutajana",
    }


@pytest.mark.django_db
def test_open_teemad_at_a_past_periods_end_are_rebuilt_from_the_trail(
    client, me, colleague, recorded_long_ago
):
    today = timezone.localdate()
    start = _month_start(today, 3)
    matter = factories.MatterFactory(
        owner=colleague, title="Näidisseadus antud", received_date=start
    )
    ChangeEvent.objects.filter(matter=matter).update(occurred_at=_at(start))
    Matter.all_objects.filter(pk=matter.pk).update(created_at=_at(start))
    User.objects.filter(pk__in=[me.pk, colleague.pk]).update(created_at=_at(start))
    _assigned(matter, today.replace(day=1), "Mina Näidis", "Kolleeg Näidis")
    client.force_login(me)

    last_month = _month(client, _month_start(today, 1), naitaja="menetluses")
    this_month = _month(client, today.replace(day=1))

    assert _cell(last_month, "menetluses") == ms.Cell(V, 1)
    assert [row.title for row in last_month.context["stats"].drill.rows] == ["Näidisseadus antud"]
    assert _cell(this_month, "menetluses") == ms.Cell(V, 0)
    # One fewer than at the end of the comparison span.
    assert _card(this_month, "menetluses").delta == -1


@pytest.mark.django_db
def test_present_state_figures_equal_minu_asjad_today(client, me):
    from app.matters.my_work import build_my_work

    today = timezone.localdate()
    matter = factories.MatterFactory(owner=me, title="Näidisseadus hiljaks")
    factories.NextActionFactory(
        matter=matter, responsible=me, target_date=today - dt.timedelta(days=3)
    )
    client.force_login(me)

    response = _page(client, naitaja="ule-tahtaja")
    work = build_my_work(me, today)

    overdue = _card(response, "ule-tahtaja")
    assert overdue.cell == ms.Cell(V, work.overdue) and work.overdue >= 1
    assert overdue.tone == "danger"
    attention = next(chip for chip in work.portfolio.chips if chip.key == "sekkumist")
    assert _cell(response, "sekkumist").value == attention.count
    assert [row.title for row in response.context["stats"].drill.rows] == ["Näidisseadus hiljaks"]
    assert response.context["stats"].columns == []


@pytest.mark.django_db
def test_present_state_figures_in_a_past_period_are_unknown_never_invented(client, me):
    today = timezone.localdate()
    matter = factories.MatterFactory(owner=me, title="Näidisseadus hiljaks")
    factories.NextActionFactory(
        matter=matter, responsible=me, target_date=today - dt.timedelta(days=3)
    )
    client.force_login(me)

    response = _month(client, _month_start(today, 1), naitaja="ule-tahtaja")

    for key in ("ule-tahtaja", "sekkumist"):
        card = _card(response, key)
        assert (card.cell.coverage, card.cell.text, card.hint) == (U, "—", "ainult täna")
        assert card.hint_help == ms.ONLY_TODAY
    assert response.context["stats"].drill.rows == []
    assert ms.ONLY_TODAY in response.content.decode()


# -- deltas and coverage -------------------------------------------------------


@pytest.mark.django_db
def test_a_figure_is_compared_with_the_previous_equal_period(client, me, recorded_long_ago):
    today = timezone.localdate()
    last, before = _month_start(today, 1), _month_start(today, 2)
    matter = factories.MatterFactory(owner=me, title="Näidisseadus võrdlus")
    for day in (last.replace(day=2), last.replace(day=3), last.replace(day=4)):
        factories.EntryFactory(matter=matter, author=me, occurred_at=_at(day))
    factories.EntryFactory(matter=matter, author=me, occurred_at=_at(before.replace(day=2)))
    client.force_login(me)

    response = _month(client, last)
    card = _card(response, "sissekanded")

    assert (card.cell.value, card.delta, card.arrow, card.change) == (3, 2, "↑", 2)
    assert "↑ 2 võrreldes eelmise kuuga" in response.content.decode()
    flat = _card(response, "lopetatud")
    assert (flat.delta, flat.arrow) == (0, "→")


@pytest.mark.django_db
def test_no_comparison_is_made_against_a_lower_bound(client, me, monkeypatch):
    today = timezone.localdate()
    began = _month_start(today, 1).replace(day=10)
    _recorded_since(monkeypatch, began)
    matter = factories.MatterFactory(owner=me, title="Näidisseadus algus")
    factories.EntryFactory(matter=matter, author=me, occurred_at=_at(began))
    client.force_login(me)

    response = _month(client, today.replace(day=1))

    assert _card(response, "sissekanded").delta is None


@pytest.mark.django_db
def test_a_period_recording_began_inside_reads_at_least_with_an_osaline_hint(
    client, me, monkeypatch
):
    today = timezone.localdate()
    month_start = _month_start(today, 2)
    began = month_start.replace(day=14)
    _recorded_since(monkeypatch, began)
    matter = factories.MatterFactory(owner=me, title="Näidisseadus osaline")
    factories.EntryFactory(matter=matter, author=me, occurred_at=_at(began + dt.timedelta(days=3)))
    client.force_login(me)

    response = _month(client, month_start)
    card = _card(response, "sissekanded")
    body = response.content.decode()

    assert (card.cell.coverage, card.cell.text, card.hint) == (P, "≥ 1", "osaline")
    assert f"alates {began:%d.%m.%Y}" in card.hint_help
    assert "≥ 1" in body and "osaline" in body
    # Inside coverage the same figure is a plain count, and a 0 is a 0.
    assert _cell(_month(client, _month_start(today, 1)), "sissekanded") == ms.Cell(V, 0)


@pytest.mark.django_db
def test_a_period_before_recording_reads_a_dash_or_at_least_never_zero(client, me, monkeypatch):
    today = timezone.localdate()
    _recorded_since(monkeypatch, today.replace(day=1))
    old = _month_start(today, 5)
    matter = factories.MatterFactory(owner=me, title="Näidisseadus vana")
    factories.EntryFactory(matter=matter, author=me, occurred_at=_at(old.replace(day=8)))
    client.force_login(me)

    found = _cell(_month(client, old), "sissekanded")
    empty = _cell(_month(client, _month_start(today, 4)), "sissekanded")

    assert (found.coverage, found.text) == (AL, "≥ 1")
    assert (empty.coverage, empty.text) == (U, "—")


@pytest.mark.django_db
def test_months_after_the_migration_but_before_reliable_coverage_are_never_a_zero(
    client, me, colleague, monkeypatch
):
    today = timezone.localdate()
    possible = _month_start(today, 6)
    reliable = _month_start(today, 2)
    _recorded_since(monkeypatch, possible, reliable=False)
    theirs = factories.MatterFactory(owner=colleague, title="Näidisseadus kolleegi")
    _event(theirs, ChangeEventType.ENTRY_ADDED, reliable, actor=colleague)
    mine = factories.MatterFactory(owner=me, title="Näidisseadus minu vana")
    factories.EntryFactory(matter=mine, author=me, occurred_at=_at(_month_start(today, 4)))
    client.force_login(me)

    def entries(back: int) -> ms.Cell:
        return _cell(_month(client, _month_start(today, back)), "sissekanded")

    assert (entries(4).coverage, entries(4).text) == (AL, "≥ 1")
    assert (entries(3).coverage, entries(3).text) == (U, "—")
    assert entries(1) == ms.Cell(V, 0)
    assert entries(2).coverage == V
    # A figure nobody has ever recorded in Juristid has no reliable start at all.
    closed = _card(_month(client, _month_start(today, 1)), "lopetatud")
    assert (closed.cell.coverage, closed.hint) == (U, "osaline")
    assert "ei saa veel kindlaks teha" in closed.hint_help


# -- the chart and the records behind it -----------------------------------------


@pytest.mark.django_db
def test_the_chart_is_the_selected_figure_by_day_with_the_future_left_empty(
    client, me, recorded_long_ago
):
    today = timezone.localdate()
    monday = today - dt.timedelta(days=today.weekday())
    matter = factories.MatterFactory(owner=me, title="Näidisseadus joonis")
    factories.EntryFactory(matter=matter, author=me, occurred_at=_at(monday))
    factories.EntryFactory(matter=matter, author=me, occurred_at=_at(monday))
    client.force_login(me)

    stats = _page(client, naitaja="sissekanded").context["stats"]

    assert stats.selected.key == "sissekanded"
    assert [column.label for column in stats.columns] == ["E", "T", "K", "N", "R", "L", "P"]
    assert stats.columns[0].text == "2" and stats.columns[0].height == 100.0
    future = [column for column in stats.columns if column.future]
    assert len(future) == 6 - today.weekday()
    assert all(column.text == "" and column.height == 0 for column in future)


@pytest.mark.django_db
def test_the_records_behind_a_figure_link_to_their_teemad_and_are_capped(
    client, me, recorded_long_ago
):
    today = timezone.localdate()
    matter = factories.MatterFactory(owner=me, title="Näidisseadus palju")
    for _ in range(ms.DRILL_LIMIT + 3):
        factories.EntryFactory(matter=matter, author=me, occurred_at=_at(today))
    client.force_login(me)

    capped = _page(client, naitaja="sissekanded")
    drill = capped.context["stats"].drill
    everything = client.get(drill.more_url).context["stats"].drill

    assert (len(drill.rows), drill.total) == (ms.DRILL_LIMIT, ms.DRILL_LIMIT + 3)
    assert "kaik=1" in drill.more_url and "naitaja=sissekanded" in drill.more_url
    assert f"Näita kõiki ({ms.DRILL_LIMIT + 3})" in capped.content.decode()
    assert len(everything.rows) == ms.DRILL_LIMIT + 3 and everything.more_url == ""
    assert drill.rows[0].url == reverse("matters:matter_detail", args=[matter.pk])
    assert f'href="{drill.rows[0].url}"' in capped.content.decode()


@pytest.mark.django_db
def test_an_unknown_figure_key_falls_back_to_the_default(client, me):
    client.force_login(me)

    assert _page(client, naitaja="kellegi-teise").context["stats"].selected.key == "saadetud"


# -- incoming monitoring -----------------------------------------------------------


def _finding(matter, day, title="Näidisdokument", **extra):
    from app.register_watch.models import TargetOrigin, WatchFinding, WatchSource, WatchTarget

    target, _ = WatchTarget.objects.get_or_create(
        matter=matter,
        source=WatchSource.ADR_MKM,
        identifier=f"näidis-{matter.pk}",
        defaults={"origin": TargetOrigin.PHRASE},
    )
    finding = WatchFinding.objects.create(
        target=target,
        matter=matter,
        external_id=f"{title}-{day}",
        title=title,
        url="https://example.invalid/dokument",
        **extra,
    )
    WatchFinding.objects.filter(pk=finding.pk).update(created_at=_at(day))
    return finding


def _watch_run(day):
    """A nightly run on ``day``, after a first one long ago (the source's coverage start)."""
    from app.register_watch.models import WatchRun

    if not WatchRun.objects.filter(started_at__lt=_at(dt.date(2021, 1, 1))).exists():
        WatchRun.objects.create(
            started_at=_at(dt.date(2020, 1, 1), 1), finished_at=_at(dt.date(2020, 1, 1), 2)
        )
    return WatchRun.objects.create(started_at=_at(day, 1), finished_at=_at(day, 2))


@pytest.mark.django_db
def test_register_documents_on_my_teemad_are_counted_and_link_to_their_source(
    client, me, colleague
):
    today = timezone.localdate()
    mine = factories.MatterFactory(owner=me, title="Näidisseadus minu")
    theirs = factories.MatterFactory(owner=colleague, title="Näidisseadus kolleegi")
    _finding(mine, today, "Minu dokument")
    _finding(mine, today, "Varasem dokument", status="BASELINE")
    _finding(theirs, today, "Teise dokument")
    _watch_run(today)
    client.force_login(me)

    response = _page(client, naitaja="registrid")

    assert _cell(response, "registrid") == ms.Cell(V, 1)
    rows = response.context["stats"].drill.rows
    assert [(row.title, row.what) for row in rows] == [("Näidisseadus minu", "Minu dokument")]
    assert rows[0].source_url == "https://example.invalid/dokument"
    assert "Allikas ↗" in response.content.decode()


@pytest.mark.django_db
def test_an_incoming_source_that_was_not_read_in_the_period_reads_a_dash(client, me):
    factories.MatterFactory(owner=me, title="Näidisseadus minu")
    client.force_login(me)

    card = _card(_page(client), "registrid")

    assert (card.cell.text, card.hint, card.hint_help) == ("—", "lugemata", ms.NOT_READ)
    assert card.delta is None


@pytest.mark.django_db
def test_a_source_first_read_inside_the_period_is_marked_partial(client, me):
    from app.register_watch.models import WatchRun

    today = timezone.localdate()
    mine = factories.MatterFactory(owner=me, title="Näidisseadus minu")
    _finding(mine, today)
    WatchRun.objects.create(started_at=_at(today, 1), finished_at=_at(today, 2))
    client.force_login(me)

    card = _card(_page(client, periood="aasta"), "registrid")

    assert (card.cell.text, card.hint) == ("≥ 1", "osaline")
    assert f"alates {today:%d.%m.%Y}" in card.hint_help


@pytest.mark.django_db
def test_an_incoming_item_goes_to_the_owner_on_its_day(client, me, colleague, recorded_long_ago):
    today = timezone.localdate()
    earlier = _month_start(today, 1).replace(day=5)
    matter = factories.MatterFactory(owner=colleague, title="Näidisseadus üle antud")
    Matter.all_objects.filter(pk=matter.pk).update(created_at=_at(_month_start(today, 3)))
    _assigned(
        matter,
        today.replace(day=1),
        "Mina Näidis",
        "Kolleeg Näidis",
        previous_owner_id=str(me.pk),
        new_owner_id=str(colleague.pk),
    )
    _finding(matter, earlier)
    _watch_run(earlier)
    client.force_login(me)

    assert _cell(_month(client, _month_start(today, 1)), "registrid") == ms.Cell(V, 1)
    client.force_login(colleague)
    assert _cell(_month(client, _month_start(today, 1)), "registrid") == ms.Cell(V, 0)


@pytest.mark.django_db
def test_the_riigikogu_figures_say_when_monitoring_is_switched_off(client, me, settings):
    settings.RIIGIKOGU_MONITORING = False
    client.force_login(me)

    response = _page(client, naitaja="kutsed")

    for key in ("kutsed", "arutelud", "mainimised"):
        card = _card(response, key)
        assert card.off == "Riigikogu jälgimine ei ole sisse lülitatud"
        assert card.cell.value is None
    assert "Riigikogu jälgimine ei ole sisse lülitatud" in response.content.decode()
    assert response.context["stats"].columns == []


@pytest.mark.django_db
def test_riigikogu_invitations_sittings_and_mentions_on_my_bills(client, me, colleague, settings):
    import uuid as uuid_module

    from app.register_watch.models import TargetOrigin, WatchSource, WatchTarget
    from app.riigikogu.enums import RunStatus, Stage
    from app.riigikogu.models import (
        Bill,
        CommitteeAgendaItem,
        Invitation,
        ParliamentDocument,
        StageRun,
        StenogramMention,
    )

    settings.RIIGIKOGU_MONITORING = True
    today = timezone.localdate()
    now = _at(today)
    bill = Bill.objects.create(
        uuid=uuid_module.uuid4(), mark=987, type_code="SE", url="https://example.invalid/eelnou"
    )
    other = Bill.objects.create(uuid=uuid_module.uuid4(), mark=988, type_code="SE")
    mine = factories.MatterFactory(owner=me, title="Näidisseadus eelnõu")
    theirs = factories.MatterFactory(owner=colleague, title="Näidisseadus teise eelnõu")
    for matter, tied in ((mine, bill), (theirs, other)):
        WatchTarget.objects.create(
            matter=matter,
            source=WatchSource.RIIGIKOGU,
            identifier=str(tied.uuid),
            origin=TargetOrigin.LINKED,
        )
    for tied in (bill, other):
        document = ParliamentDocument.objects.create(
            uuid=uuid_module.uuid4(),
            kind="INVITATION",
            title="Kutse",
            bill=tied,
            url="https://example.invalid/kiri",
            first_seen_at=now,
            last_seen_at=now,
        )
        Invitation.objects.create(document=document, committee="Majanduskomisjon")
        CommitteeAgendaItem.objects.create(
            uuid=uuid_module.uuid4(),
            committee="Majanduskomisjon",
            sitting_at=now,
            title="Eelnõu arutelu",
            bill=tied,
            first_seen_at=now,
            last_seen_at=now,
        )
        StenogramMention.objects.create(
            key=f"näidis-{tied.mark}",
            sitting_on=today,
            quote="Kaubanduskoda on öelnud …",
            category="LEGISLATIVE",
            bill=tied,
        )
    for stage in (Stage.INVITATIONS, Stage.AGENDAS, Stage.STENOGRAMS):
        StageRun.objects.create(
            cycle=uuid_module.uuid4(),
            stage=stage,
            status=RunStatus.SUCCEEDED,
            started_at=now,
            finished_at=now,
            window_start=dt.date(2020, 1, 1),
            window_end=today,
        )
    client.force_login(me)

    response = _page(client, naitaja="kutsed")

    for key in ("kutsed", "arutelud", "mainimised"):
        assert _cell(response, key) == ms.Cell(V, 1), key
    rows = response.context["stats"].drill.rows
    assert [row.title for row in rows] == ["Näidisseadus eelnõu"]
    assert rows[0].source_url == "https://example.invalid/kiri"
    assert rows[0].what.startswith("987 SE")


@pytest.mark.django_db
def test_government_agenda_items_tied_to_my_teemad(client, me, colleague):
    from app.register_watch.models import AgendaItem, GovernmentAgenda

    today = timezone.localdate()
    agenda = GovernmentAgenda.objects.create(
        url="https://example.invalid/paevakord", title="Päevakord", session_date=today
    )
    mine = factories.MatterFactory(owner=me, title="Näidisseadus valitsus")
    theirs = factories.MatterFactory(owner=colleague, title="Näidisseadus teise valitsus")
    AgendaItem.objects.create(agenda=agenda, number=1, title="Eelnõu", related_matter=mine)
    AgendaItem.objects.create(agenda=agenda, number=2, title="Teine", related_matter=theirs)
    client.force_login(me)

    response = _page(client, naitaja="valitsus")

    assert _cell(response, "valitsus") == ms.Cell(V, 1)
    assert response.context["stats"].drill.rows[0].source_url == "https://example.invalid/paevakord"
    # A week with no stored agenda was not read: «—», not 0.
    before = _month(client, _month_start(today, 6))
    assert _cell(before, "valitsus").text == "—"


# -- bounds and privacy ------------------------------------------------------------


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
        _finding(matter, timezone.localdate())
    large = count()

    assert large == small, (small, large)
    assert large <= 40


@pytest.mark.django_db
def test_no_other_persons_name_is_rendered(client, me, colleague):
    matter = factories.MatterFactory(owner=colleague, title="Näidisseadus teise")
    _assigned(matter, timezone.localdate(), "Mina Näidis", "Kolleeg Näidis")
    client.force_login(me)

    body = _page(client, naitaja="uued").content.decode()

    assert "Kolleeg Näidis" not in body
    assert str(uuid.UUID(str(colleague.pk))) not in body


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
    built = ms.load_ownership(me, timezone.localdate(), dt.date(2020, 1, 1), dt.date(2020, 1, 1))

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


# ---------------------------------------------------------------------------
# Earliest possible versus reliable coverage (ADR 0223 §10)
# ---------------------------------------------------------------------------


def _event(matter, kind, day, *, actor=None, payload=None):
    return ChangeEvent.objects.create(
        matter=matter,
        event_type=kind,
        occurred_at=_at(day),
        actor=actor,
        object_type="matters.matter",
        object_id=matter.pk,
        payload=payload or {},
    )


@pytest.mark.django_db
def test_reliable_coverage_starts_at_the_first_activity_a_person_recorded(me, colleague):
    possible = dt.date(2026, 1, 1)
    recording = dict.fromkeys(ms.RECORDED_BY, possible)
    matter = factories.MatterFactory(owner=colleague, title="Näidisseadus algus")
    # An import (no actor) and a back-fill run by a person do not establish it.
    _event(matter, ChangeEventType.ENTRY_ADDED, dt.date(2026, 2, 1))
    _event(
        matter,
        ChangeEventType.MATTER_ASSIGNED,
        dt.date(2026, 2, 1),
        actor=colleague,
        payload={"operation": "backfill_legacy_owners"},
    )
    assert ms.reliable_since(me, recording) == dict.fromkeys(ms.RELIABLE_BY)

    # A colleague's own recorded entry does, for the whole department's view.
    _event(matter, ChangeEventType.ENTRY_ADDED, dt.date(2026, 5, 7), actor=colleague)
    # The synchronisation's hand-over is the recorded channel for the trail.
    _event(
        matter,
        ChangeEventType.MATTER_ASSIGNED,
        dt.date(2026, 6, 3),
        payload={"provenance_source": "LOVABLE_SYNC"},
    )
    found = ms.reliable_since(me, recording)
    assert found["entries"] == dt.date(2026, 5, 7)
    assert found["trail"] == dt.date(2026, 6, 3)
    assert found["closed"] is None
    # Never earlier than the day recording became possible.
    later = ms.reliable_since(me, dict.fromkeys(ms.RECORDED_BY, dt.date(2026, 5, 20)))
    assert later["entries"] == dt.date(2026, 5, 20)
