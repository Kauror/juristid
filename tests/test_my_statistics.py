"""Minu statistika (FUNC-003, docs/adr/0223): own figures, each credited by a stated rule.

The rebuild of ownership at the time is pure and tested on synthetic trails
first: a mid-month hand-over, a Lovable-provenance one, a name that does not
chain, a name two people carry, a trail that ends on someone else, a Teema that
existed before Juristid did. Then the page: self only, the foldout's link, the
figures equal to the existing services, unknown shown as «andmed puuduvad»
rather than 0, and a bounded number of queries.
"""

from __future__ import annotations

import datetime as dt
import uuid

import pytest
from django.db import connection
from django.test.utils import CaptureQueriesContext
from django.urls import NoReverseMatch, reverse
from django.utils import timezone

from app.audit.enums import ChangeEventType
from app.audit.models import ChangeEvent
from app.matters import my_statistics as ms
from app.matters.my_work import opinion_work
from app.reporting.opinions import SOURCE_IMPORTED, SOURCE_SUBMISSION, MatterFacts, OpinionFact
from app.submissions.models import Submission
from tests import factories
from tests.report_builder_support import sent

TODAY = dt.date(2026, 11, 20)
A, B, C = "id-ann", "id-bert", "id-carl"
NAMES = {"Ann": [A], "Bert": [B], "Carl": [C], "Kaks": ["id-1", "id-2"]}


def _at(day: dt.date) -> dt.datetime:
    return dt.datetime(day.year, day.month, day.day, 12, tzinfo=dt.UTC)


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


def handed(pk, day, from_name, to_name, **provenance):
    return {
        "matter_id": pk,
        "occurred_at": _at(day),
        "payload": {"from_name": from_name, "to_name": to_name, **provenance},
    }


def month(year: int, number: int) -> ms.Month:
    return next(m for m in ms.months(TODAY, 24) if (m.start.year, m.start.month) == (year, number))


# ---------------------------------------------------------------------------
# Ownership at the time, rebuilt from synthetic trails
# ---------------------------------------------------------------------------


def test_a_hand_over_mid_month_credits_each_owner_with_their_own_days():
    built = ms.build_ownership(
        [row(1, B, dt.date(2026, 1, 1), received=dt.date(2026, 1, 1))],
        [handed(1, dt.date(2026, 3, 15), "Ann", "Bert")],
        NAMES,
        TODAY,
    )

    assert built.owner_at(1, dt.date(2026, 3, 14)) == A
    assert built.owner_at(1, dt.date(2026, 3, 15)) == B
    # The current owner does not inherit February.
    assert ms.owned_at_month_end(built, A, month(2026, 2)) == 1
    assert ms.owned_at_month_end(built, B, month(2026, 2)) == 0
    assert ms.owned_at_month_end(built, B, month(2026, 3)) == 1


def test_a_lovable_provenance_hand_over_counts_like_any_other():
    built = ms.build_ownership(
        [row(1, C, dt.date(2026, 1, 1))],
        [handed(1, dt.date(2026, 6, 2), "Ann", "Carl", provenance_source="lovable_sync")],
        NAMES,
        TODAY,
    )

    assert built.owner_at(1, dt.date(2026, 5, 31)) == A
    assert built.owner_at(1, dt.date(2026, 6, 30)) == C


def test_a_teema_that_existed_before_juristid_makes_those_months_unknown_not_zero():
    built = ms.build_ownership(
        [row(1, A, dt.date(2026, 11, 3), received=dt.date(2025, 4, 1))],
        [],
        NAMES,
        TODAY,
    )

    assert built.owner_at(1, dt.date(2026, 10, 31)) == ms.UNKNOWN
    assert ms.owned_at_month_end(built, A, month(2026, 10)) is None
    assert ms.owned_at_month_end(built, A, month(2026, 11)) == 1
    # A month before the Teema arrived at all is not about it: a real zero.
    assert ms.owned_at_month_end(built, A, month(2025, 3)) == 0


def test_a_teema_closed_before_it_reached_juristid_spans_only_until_then():
    built = ms.build_ownership(
        [row(1, A, dt.date(2026, 11, 3), year=2024, is_open=False)],
        [],
        NAMES,
        TODAY,
    )

    assert ms.owned_at_month_end(built, A, month(2025, 6)) is None
    # Closed, so after its arrival here nobody owns it as open work any more.
    assert built.spans[1] == (dt.date(2024, 1, 1), dt.date(2026, 11, 3))


def test_names_that_do_not_chain_leave_that_stretch_unknown():
    built = ms.build_ownership(
        [row(1, C, dt.date(2026, 1, 1))],
        [
            handed(1, dt.date(2026, 3, 1), "Ann", "Bert"),
            # From Carl, but the trail said Bert held it: Bert's stretch is unknown.
            handed(1, dt.date(2026, 5, 1), "Carl", "Carl"),
        ],
        NAMES,
        TODAY,
    )

    assert built.owner_at(1, dt.date(2026, 2, 1)) == A
    assert built.owner_at(1, dt.date(2026, 4, 1)) == ms.UNKNOWN
    assert built.owner_at(1, dt.date(2026, 6, 1)) == C


def test_a_name_two_people_carry_is_nobody_known():
    built = ms.build_ownership(
        [row(1, A, dt.date(2026, 1, 1))],
        [handed(1, dt.date(2026, 4, 1), "Kaks", "Ann")],
        NAMES,
        TODAY,
    )

    assert built.owner_at(1, dt.date(2026, 2, 1)) == ms.UNKNOWN
    assert ms.owned_at_month_end(built, A, month(2026, 2)) is None
    assert built.owner_at(1, dt.date(2026, 5, 1)) == A


def test_a_trail_that_ends_on_someone_else_than_the_owner_now_is_unknown_until_today():
    built = ms.build_ownership(
        [row(1, C, dt.date(2026, 1, 1))],
        [handed(1, dt.date(2026, 3, 1), "Ann", "Bert")],
        NAMES,
        TODAY,
    )

    assert built.owner_at(1, dt.date(2026, 2, 1)) == A
    assert built.owner_at(1, dt.date(2026, 9, 1)) == ms.UNKNOWN
    assert built.owner_at(1, TODAY) == C


def test_an_unowned_stretch_is_nobodys():
    built = ms.build_ownership(
        [row(1, A, dt.date(2026, 1, 1))],
        [handed(1, dt.date(2026, 3, 1), None, "Ann")],
        NAMES,
        TODAY,
    )

    assert built.owner_at(1, dt.date(2026, 2, 1)) is None
    assert ms.owned_at_month_end(built, A, month(2026, 2)) == 0


# ---------------------------------------------------------------------------
# Sent opinions: the recorded actor, else the owner that day
# ---------------------------------------------------------------------------


def _fact(pk, on, *, actor=None, source=SOURCE_IMPORTED):
    matter = MatterFacts(pk, f"2026_{pk}", "Näidisseadus", 2026, None, "x", False, False, "")
    return OpinionFact(matter=matter, sent_on=on, deadline=None, source=source, actor_id=actor)


def test_an_opinion_is_credited_to_who_sent_it_not_to_the_owner():
    built = ms.build_ownership([row(1, B, dt.date(2026, 1, 1))], [], NAMES, TODAY)
    facts = [_fact(1, dt.date(2026, 4, 2), actor=A, source=SOURCE_SUBMISSION)]

    assert ms.sent_in_month(facts, built, A, month(2026, 4)) == 1
    assert ms.sent_in_month(facts, built, B, month(2026, 4)) == 0


def test_an_imported_opinion_goes_to_the_owner_on_its_day():
    built = ms.build_ownership(
        [row(1, B, dt.date(2026, 1, 1))],
        [handed(1, dt.date(2026, 5, 1), "Ann", "Bert")],
        NAMES,
        TODAY,
    )
    facts = [_fact(1, dt.date(2026, 4, 2)), _fact(1, dt.date(2026, 6, 2))]

    assert ms.sent_in_month(facts, built, A, month(2026, 4)) == 1
    assert ms.sent_in_month(facts, built, B, month(2026, 4)) == 0
    assert ms.sent_in_month(facts, built, B, month(2026, 6)) == 1


def test_one_opinion_nobody_can_credit_makes_the_month_unknown_and_an_empty_month_is_zero():
    built = ms.build_ownership(
        [row(1, A, dt.date(2026, 11, 2), received=dt.date(2025, 1, 1))], [], NAMES, TODAY
    )
    facts = [_fact(1, dt.date(2026, 3, 2))]

    assert ms.sent_in_month(facts, built, A, month(2026, 3)) is None
    assert ms.sent_in_month(facts, built, A, month(2026, 4)) == 0


def test_months_are_the_last_twelve_newest_first_the_current_one_ending_today():
    shown = ms.months(TODAY)

    assert len(shown) == 12
    assert (shown[0].start, shown[0].end) == (dt.date(2026, 11, 1), TODAY)
    assert (shown[-1].start, shown[-1].end) == (dt.date(2025, 12, 1), dt.date(2025, 12, 31))
    assert shown[0].label == "november 2026"


# ---------------------------------------------------------------------------
# The page
# ---------------------------------------------------------------------------


def _assigned(matter, day, from_name, to_name):
    return ChangeEvent.objects.create(
        matter=matter,
        event_type=ChangeEventType.MATTER_ASSIGNED,
        occurred_at=_at(day),
        object_type="matters.matter",
        object_id=matter.pk,
        payload={"from_name": from_name, "to_name": to_name},
    )


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

    assert (mine, theirs) == (1, 0)


@pytest.mark.django_db
def test_months_before_juristid_recorded_anybodys_action_say_andmed_puuduvad(client, me):
    today = timezone.localdate()
    matter = factories.MatterFactory(owner=me, title="Näidisseadus algus")
    ChangeEvent.objects.create(
        matter=matter,
        event_type=ChangeEventType.MATTER_CLOSED,
        occurred_at=_at(today),
        actor=me,
        object_type="matters.matter",
        object_id=matter.pk,
        payload={},
    )
    client.force_login(me)

    response = client.get(reverse("matters:my_statistics"))
    stats = response.context["stats"]

    first = stats.actions_since
    assert first is not None
    for month_row in stats.rows:
        recorded = month_row.month.start >= first
        assert (month_row.cells[2] is None) == (not recorded), month_row.month.label
    assert "andmed puuduvad" in response.content.decode()


@pytest.mark.django_db
def test_a_reassignment_in_the_trail_credits_the_earlier_owner(client, me, colleague):
    today = timezone.localdate()
    start = (today.replace(day=1) - dt.timedelta(days=70)).replace(day=1)
    matter = factories.MatterFactory(
        owner=colleague, title="Näidisseadus antud", received_date=start
    )
    ChangeEvent.objects.filter(matter=matter).update(occurred_at=_at(start))
    from app.matters.models import Matter

    Matter.all_objects.filter(pk=matter.pk).update(created_at=_at(start))
    _assigned(matter, today.replace(day=1), "Mina Näidis", "Kolleeg Näidis")
    client.force_login(me)

    rows = client.get(reverse("matters:my_statistics")).context["stats"].rows

    by_start = {r.month.start: r.cells[1] for r in rows}
    previous = (today.replace(day=1) - dt.timedelta(days=1)).replace(day=1)
    assert by_start[previous] == 1  # mine at the end of last month
    assert by_start[today.replace(day=1)] == 0  # handed over this month


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
