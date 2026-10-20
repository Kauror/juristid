"""A WAIT never takes an ``Arvamuse tähtaeg`` off the work lists (ADR 0050, amended 2026-10-20).

ADR 0050 lets an open ``Järgmiseks`` outrank the opinion deadline: the lawyer's
own plan is the current work. A WAIT is not Koda's plan for the answer it owes —
«Ootan Riigikogu II lugemist» names what somebody else does next — so the
deadline stays live beside it: in Minu asjad, in the overdue count, in the
header and in the register's work populations. DO and MONITOR keep the
precedence they had. Nothing here discharges the obligation; a sent answer
still does, exactly as before.
"""

from __future__ import annotations

from datetime import timedelta

import pytest
from django.utils import timezone

from app.matters import work_items as wi
from app.matters.my_work import build_my_work
from app.matters.register_filters import register_population
from app.matters.selectors import response_deadline_of
from app.matters.services import create_matter
from app.workflow.enums import ActionKind, DatePrecision, DateSemantics
from app.workflow.services import set_next_action
from tests.factories import send_opinion_through_services as _send_opinion

pytestmark = pytest.mark.django_db

TODAY = timezone.localdate()
WAIT_TEXT = "Ootan Riigikogu II lugemist"


def _teema(owner, *, deadline_in_days):
    return create_matter(
        title="Sünteetiline Riigikogu eelnõu",
        owner=owner,
        reference_year=2026,
        response_deadline=TODAY + timedelta(days=deadline_in_days),
    )


def _wait(matter, actor, *, target_date=None, semantics=DateSemantics.EXPECTED_AROUND):
    return set_next_action(
        matter=matter,
        text=WAIT_TEXT,
        kind=ActionKind.WAIT,
        date_semantics=semantics,
        target_date=target_date,
        date_precision=DatePrecision.EXACT,
        source_text="Mis edasi? Ootan II lugemist Riigikogus",
        responsible=matter.owner,
        actor=actor,
    )


def _rows(user, matter, today=TODAY):
    return [item for item in wi.work_items(user, today=today) if item.matter_id == matter.pk]


def test_an_undated_wait_leaves_the_deadline_on_the_owners_desk(specialist):
    matter = _teema(specialist, deadline_in_days=15)
    _wait(matter, specialist)

    sources = {item.source_type for item in _rows(specialist, matter)}
    assert wi.SOURCE_RESPONSE_DEADLINE in sources
    assert wi.outstanding_response_deadlines(specialist).filter(pk=matter.pk).exists()
    assert wi.response_deadline_is_outstanding(matter, specialist)


def test_a_dated_wait_is_listed_and_the_deadline_beside_it(specialist):
    matter = _teema(specialist, deadline_in_days=15)
    _wait(
        matter,
        specialist,
        target_date=TODAY + timedelta(days=30),
        semantics=DateSemantics.REVIEW_ON,
    )

    rows = _rows(specialist, matter)
    assert {item.source_type for item in rows} == {
        wi.SOURCE_RESPONSE_DEADLINE,
        wi.SOURCE_NEXT_ACTION,
    }
    (wait,) = [item for item in rows if item.source_type == wi.SOURCE_NEXT_ACTION]
    # The wait itself is never a deadline and never late.
    assert wait.is_overdue is False
    assert wait not in wi.real_deadlines(rows)


def test_a_passed_deadline_beside_a_wait_is_overdue_everywhere(specialist):
    matter = _teema(specialist, deadline_in_days=-3)
    _wait(matter, specialist)

    (deadline,) = [
        i for i in _rows(specialist, matter) if i.source_type == wi.SOURCE_RESPONSE_DEADLINE
    ]
    assert deadline.is_overdue
    assert build_my_work(specialist, today=TODAY).overdue == 1
    header = response_deadline_of(matter, specialist, today=TODAY)
    assert header is not None and header.is_overdue
    overdue = register_population(
        specialist, {"olek": "avatud", "liik": "FULL", "too": wi.WORK_OVERDUE}, today=TODAY
    )
    assert overdue.filter(pk=matter.pk).exists()


def test_a_wait_discharges_nothing_and_a_sent_answer_still_does(specialist):
    matter = _teema(specialist, deadline_in_days=15)
    _wait(matter, specialist)
    assert wi.response_obligation_of(matter, specialist, TODAY).is_outstanding

    _send_opinion(matter, specialist)

    assert not wi.response_obligation_of(matter, specialist, TODAY).is_outstanding
    assert wi.SOURCE_RESPONSE_DEADLINE not in {i.source_type for i in _rows(specialist, matter)}


@pytest.mark.parametrize(
    ("kind", "semantics"),
    [(ActionKind.DO, DateSemantics.DEADLINE), (ActionKind.MONITOR, DateSemantics.REVIEW_ON)],
)
def test_do_and_monitor_still_outrank_the_deadline(specialist, kind, semantics):
    """ADR 0050 itself, unchanged for every kind but WAIT."""
    matter = _teema(specialist, deadline_in_days=-3)
    set_next_action(
        matter=matter,
        text="Koostan arvamuse" if kind == ActionKind.DO else "Jälgin menetlust",
        kind=kind,
        date_semantics=semantics,
        target_date=TODAY + timedelta(days=10),
        actor=specialist,
    )

    assert wi.SOURCE_RESPONSE_DEADLINE not in {i.source_type for i in _rows(specialist, matter)}
    assert not wi.response_deadline_is_outstanding(matter, specialist)
    # Still owed — the precedence never said it was met.
    assert wi.response_obligation_of(matter, specialist, TODAY).is_outstanding


def test_a_restricted_wait_hides_nothing_from_a_reader_either(specialist, reader):
    """The step is read through `visible_to`; a WAIT changes nothing for anyone."""
    matter = _teema(specialist, deadline_in_days=15)
    _wait(matter, specialist)
    assert wi.outstanding_response_deadlines(reader).filter(pk=matter.pk).exists() == (
        wi.outstanding_response_deadlines(specialist).filter(pk=matter.pk).exists()
    )
