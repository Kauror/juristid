"""One review rule, read the same way by every surface that counts it (ENG-040).

The defect this closes
----------------------
«Review due» had two definitions. The register's ``?tegevus=ulevaatus``, the
``REVIEW_DUE`` statistic and ``NextAction.is_due_for_review`` compared the
anchor inclusively — a review for *oktoober 2026* came round on 1 October, an
exact one on its own day. Every work surface re-derived ripeness from the
**period end**, copying the DO-lateness boundary (ADR 0079 §4): Minu asjad's
*Ülevaatamiseks* and *Vajab sekkumist*, the manager's Kiirvaade, Osakond's
intervention list and the ripe styling of a row. On 24.9.2026 the register and
Statistika said five reviews were due while *Ülevaatamiseks* listed one; an
exact review was a day late on every work surface, a month review a month, a
quarter a quarter.

ADR 0079 §6 decided the boundary deliberately and the other way from §4: a
reminder may come round when its period opens, while a plan is missed only
when its period is over. That rule now lives once, in
:mod:`app.workflow.lateness`, and this module holds every reader to it.

What this module holds
----------------------
* the boundary per precision, for both review kinds and every date meaning
  they carry — a review date (``REVIEW_ON``, and ``DEADLINE`` on a review
  kind) comes round when its period opens, an expectation (``EXPECTED_AROUND``)
  on its period's last day (owner's decision of 2026-10-29, docs/adr/0195) —
  and the Python and SQL readings agreeing on it;
* set parity between ``WORK_RIPE``, ``?tegevus=ulevaatus``, the ``REVIEW_DUE``
  statistic, ``due_for_review`` and the row predicate, on the last day of a
  quarter and on the first day of the next — including reviews dated today;
* that the work surfaces built on ripeness (``WORK_NEEDS_ATTENTION``, the
  portfolio row, Kiirvaade, Osakond's intervention list) read the same rule;
* that an approximate review is printed at its precision on Osakond, never as
  its anchor day;
* a grep guard against a sixth copy.

Every date is frozen: each reader is handed the day, and the two that read the
clock themselves are given a patched one.
"""

from __future__ import annotations

import ast
import datetime
from pathlib import Path

import pytest
from django.urls import reverse
from django.utils import timezone

from app.matters import my_work
from app.matters import overview as ov
from app.matters import work_items as wi
from app.matters.models import Matter
from app.matters.selectors import REVIEW_DUE, filter_by_next_action
from app.reporting.context import ReportingContext, parse_period
from app.reporting.selectors.activity import review_due
from app.workflow.enums import ActionKind, ActionStatus, DatePrecision, DateSemantics
from app.workflow.lateness import (
    expectation_due_date_q,
    expectation_has_come_round,
    is_review_due,
    review_due_date_q,
    review_due_q,
    review_has_come_round,
)
from app.workflow.models import NextAction
from app.workflow.services import set_next_action
from tests import factories

pytestmark = pytest.mark.django_db

WAIT = ActionKind.WAIT
MONITOR = ActionKind.MONITOR
DO = ActionKind.DO
REVIEW_ON = DateSemantics.REVIEW_ON
EXPECTED = DateSemantics.EXPECTED_AROUND
DEADLINE = DateSemantics.DEADLINE

LAST_DAY_OF_Q3 = datetime.date(2026, 9, 30)
FIRST_DAY_OF_Q4 = datetime.date(2026, 10, 1)


def _d(year: int, month: int, day: int) -> datetime.date:
    return datetime.date(year, month, day)


# ---------------------------------------------------------------------------
# The boundary, precision by precision, for both review meanings
# ---------------------------------------------------------------------------

#: ``(precision, stored, day, due)``. Anchors are the first day of their period,
#: which is what the forms and the importer store. The days are the ones a wrong
#: rule gets wrong: the day before the period opens, its first day (where the
#: period-end rule said «not yet»), a day inside it, its last day and the day
#: after.
BOUNDARIES = [
    # A day is due on that day — a reminder for the 24th is for the 24th.
    (DatePrecision.EXACT, _d(2026, 9, 24), _d(2026, 9, 23), False),
    (DatePrecision.EXACT, _d(2026, 9, 24), _d(2026, 9, 24), True),
    (DatePrecision.EXACT, _d(2026, 9, 24), _d(2026, 9, 25), True),
    (DatePrecision.INFERRED, _d(2026, 9, 24), _d(2026, 9, 23), False),
    (DatePrecision.INFERRED, _d(2026, 9, 24), _d(2026, 9, 24), True),
    # «oktoober 2026» comes round on 1 October (ADR 0079 §6), not 1 November.
    (DatePrecision.MONTH, _d(2026, 10, 1), _d(2026, 9, 30), False),
    (DatePrecision.MONTH, _d(2026, 10, 1), _d(2026, 10, 1), True),
    (DatePrecision.MONTH, _d(2026, 10, 1), _d(2026, 10, 31), True),
    (DatePrecision.MONTH, _d(2026, 10, 1), _d(2026, 11, 1), True),
    # «IV kvartal 2026» on 1 October.
    (DatePrecision.QUARTER, _d(2026, 10, 1), _d(2026, 9, 30), False),
    (DatePrecision.QUARTER, _d(2026, 10, 1), _d(2026, 10, 1), True),
    (DatePrecision.QUARTER, _d(2026, 10, 1), _d(2026, 12, 31), True),
    # «II poolaasta 2026» on 1 July — historical, still read.
    (DatePrecision.HALF_YEAR, _d(2026, 7, 1), _d(2026, 6, 30), False),
    (DatePrecision.HALF_YEAR, _d(2026, 7, 1), _d(2026, 7, 1), True),
    # «2027» on 1 January 2027.
    (DatePrecision.YEAR, _d(2027, 1, 1), _d(2026, 12, 31), False),
    (DatePrecision.YEAR, _d(2027, 1, 1), _d(2027, 1, 1), True),
    (DatePrecision.YEAR, _d(2027, 1, 1), _d(2027, 12, 31), True),
    # A period whose stored day is not its first is still the period it names:
    # «oktoober 2026» stored on the 15th is due on the 1st, in both readings.
    (DatePrecision.MONTH, _d(2026, 10, 15), _d(2026, 10, 1), True),
    (DatePrecision.MONTH, _d(2026, 10, 15), _d(2026, 9, 30), False),
    # A 1987 register row is outside the supported period range. It is read as
    # its anchor rather than raising, in both readings.
    (DatePrecision.MONTH, _d(1987, 3, 1), _d(2026, 9, 30), True),
]

#: An expectation is somebody else's timing, not an instruction to review: it
#: comes round on the **last** day of the period it names — the day itself for
#: a day — and not merely because the period began (owner's decision of
#: 2026-10-29, docs/adr/0195).
EXPECTATION_BOUNDARIES = [
    (DatePrecision.EXACT, _d(2026, 9, 24), _d(2026, 9, 23), False),
    (DatePrecision.EXACT, _d(2026, 9, 24), _d(2026, 9, 24), True),
    (DatePrecision.EXACT, _d(2026, 9, 24), _d(2026, 9, 25), True),
    (DatePrecision.INFERRED, _d(2026, 9, 24), _d(2026, 9, 24), True),
    # «oktoober 2026»: not on 1 October, not on the 29th — on the 31st.
    (DatePrecision.MONTH, _d(2026, 10, 1), _d(2026, 10, 1), False),
    (DatePrecision.MONTH, _d(2026, 10, 1), _d(2026, 10, 29), False),
    (DatePrecision.MONTH, _d(2026, 10, 1), _d(2026, 10, 30), False),
    (DatePrecision.MONTH, _d(2026, 10, 1), _d(2026, 10, 31), True),
    (DatePrecision.MONTH, _d(2026, 10, 1), _d(2026, 11, 1), True),
    # «IV kvartal 2026» on 31 December.
    (DatePrecision.QUARTER, _d(2026, 10, 1), _d(2026, 10, 29), False),
    (DatePrecision.QUARTER, _d(2026, 10, 1), _d(2026, 12, 30), False),
    (DatePrecision.QUARTER, _d(2026, 10, 1), _d(2026, 12, 31), True),
    (DatePrecision.QUARTER, _d(2026, 10, 1), _d(2027, 1, 1), True),
    (DatePrecision.HALF_YEAR, _d(2026, 7, 1), _d(2026, 12, 30), False),
    (DatePrecision.HALF_YEAR, _d(2026, 7, 1), _d(2026, 12, 31), True),
    # «2027» on 31 December 2027.
    (DatePrecision.YEAR, _d(2027, 1, 1), _d(2027, 1, 1), False),
    (DatePrecision.YEAR, _d(2027, 1, 1), _d(2027, 12, 30), False),
    (DatePrecision.YEAR, _d(2027, 1, 1), _d(2027, 12, 31), True),
    # Stored mid-period, still the period it names.
    (DatePrecision.MONTH, _d(2026, 10, 15), _d(2026, 10, 30), False),
    (DatePrecision.MONTH, _d(2026, 10, 15), _d(2026, 10, 31), True),
    (DatePrecision.MONTH, _d(1987, 3, 1), _d(2026, 9, 30), True),
]

#: Both review kinds with each date meaning, and the boundary each follows.
#: A review date — and a ``DEADLINE`` on a review kind, which is never late
#: (§4) — comes round when its period opens (ADR 0079 §6).
REVIEW_PAIRS = [
    (WAIT, REVIEW_ON),
    (MONITOR, REVIEW_ON),
    (WAIT, DEADLINE),
    (MONITOR, DEADLINE),
]
EXPECTATION_PAIRS = [(WAIT, EXPECTED), (MONITOR, EXPECTED)]


@pytest.mark.parametrize(("precision", "stored", "day", "due"), BOUNDARIES)
def test_the_date_half_comes_round_when_the_period_opens(precision, stored, day, due):
    assert review_has_come_round(stored, precision, day) is due


@pytest.mark.parametrize(("precision", "stored", "day", "due"), EXPECTATION_BOUNDARIES)
def test_an_expectation_comes_round_on_the_last_day_of_its_period(precision, stored, day, due):
    assert expectation_has_come_round(stored, precision, day) is due


@pytest.mark.parametrize(("kind", "semantics"), REVIEW_PAIRS)
@pytest.mark.parametrize(("precision", "stored", "day", "due"), BOUNDARIES)
def test_the_row_and_the_queryset_answer_the_boundary_the_same_way(
    normal_matter, kind, semantics, precision, stored, day, due
):
    action = factories.NextActionFactory(
        matter=normal_matter,
        text="Vaatan üle",
        kind=kind,
        date_semantics=semantics,
        target_date=stored,
        date_precision=precision,
        status=ActionStatus.OPEN,
    )

    assert action.is_due_for_review(day) is due
    assert (action in NextAction.objects.due_for_review(day)) is due
    assert NextAction.objects.filter(review_due_date_q(day), pk=action.pk).exists() is due
    assert (
        is_review_due(kind=kind, value=stored, precision=precision, today=day, semantics=semantics)
        is due
    ), "the pure predicate disagrees with the model"


@pytest.mark.parametrize(("kind", "semantics"), EXPECTATION_PAIRS)
@pytest.mark.parametrize(("precision", "stored", "day", "due"), EXPECTATION_BOUNDARIES)
def test_an_expectation_row_and_its_queryset_answer_the_same_way(
    normal_matter, kind, semantics, precision, stored, day, due
):
    action = factories.NextActionFactory(
        matter=normal_matter,
        text="Vastus oodatav",
        kind=kind,
        date_semantics=semantics,
        target_date=stored,
        date_precision=precision,
        status=ActionStatus.OPEN,
    )

    assert action.is_due_for_review(day) is due
    assert (action in NextAction.objects.due_for_review(day)) is due
    assert NextAction.objects.filter(expectation_due_date_q(day), pk=action.pk).exists() is due
    assert (
        is_review_due(kind=kind, value=stored, precision=precision, today=day, semantics=semantics)
        is due
    )
    # Never late, whatever the day: an expectation is not a promise (ADR 0011).
    assert action.is_overdue(day) is False


@pytest.mark.parametrize("semantics", [DEADLINE, EXPECTED, REVIEW_ON])
def test_a_do_is_never_a_review_whatever_its_date_means(normal_matter, semantics):
    """A plan is late or not late. It is not a reminder that comes round."""
    action = factories.NextActionFactory(
        matter=normal_matter,
        kind=DO,
        date_semantics=semantics,
        target_date=_d(2026, 9, 1),
        date_precision=DatePrecision.MONTH,
        status=ActionStatus.OPEN,
    )
    long_after = _d(2029, 1, 1)

    assert action.is_due_for_review(long_after) is False
    assert action not in NextAction.objects.due_for_review(long_after)


def test_an_undated_review_never_comes_round_by_itself(normal_matter):
    action = factories.NextActionFactory(
        matter=normal_matter,
        kind=WAIT,
        date_semantics=EXPECTED,
        target_date=None,
        status=ActionStatus.OPEN,
    )

    assert action.is_due_for_review(_d(2029, 1, 1)) is False
    assert not NextAction.objects.filter(review_due_q(_d(2029, 1, 1))).exists()


def test_a_finished_review_is_not_due(normal_matter):
    action = factories.NextActionFactory(
        matter=normal_matter,
        kind=MONITOR,
        date_semantics=REVIEW_ON,
        target_date=_d(2026, 9, 1),
        status=ActionStatus.COMPLETED,
    )

    assert action.is_due_for_review(_d(2026, 10, 1)) is False
    assert action not in NextAction.objects.due_for_review(_d(2026, 10, 1))


# ---------------------------------------------------------------------------
# Parity: every surface counts the same rows
# ---------------------------------------------------------------------------

#: ``(name, kind, semantics, precision, stored, due on 30.9, due on 1.10)``.
#: One Matter each — a Matter carries at most one open step.
POPULATION = [
    ("exact-yesterday", WAIT, REVIEW_ON, DatePrecision.EXACT, _d(2026, 9, 29), True, True),
    ("exact-30.9", MONITOR, REVIEW_ON, DatePrecision.EXACT, _d(2026, 9, 30), True, True),
    ("exact-1.10", WAIT, EXPECTED, DatePrecision.EXACT, _d(2026, 10, 1), False, True),
    ("exact-2.10", MONITOR, REVIEW_ON, DatePrecision.EXACT, _d(2026, 10, 2), False, False),
    ("inferred-1.10", WAIT, REVIEW_ON, DatePrecision.INFERRED, _d(2026, 10, 1), False, True),
    ("month-sept", MONITOR, REVIEW_ON, DatePrecision.MONTH, _d(2026, 9, 1), True, True),
    ("month-oct-review", WAIT, REVIEW_ON, DatePrecision.MONTH, _d(2026, 10, 1), False, True),
    # An expectation is not looked at because its month began (docs/adr/0195).
    ("month-oct-expected", WAIT, EXPECTED, DatePrecision.MONTH, _d(2026, 10, 1), False, False),
    ("month-nov", MONITOR, EXPECTED, DatePrecision.MONTH, _d(2026, 11, 1), False, False),
    # On its quarter's last day, and after.
    ("quarter-q3", WAIT, EXPECTED, DatePrecision.QUARTER, _d(2026, 7, 1), True, True),
    ("quarter-q4", MONITOR, REVIEW_ON, DatePrecision.QUARTER, _d(2026, 10, 1), False, True),
    ("quarter-q1-27", WAIT, REVIEW_ON, DatePrecision.QUARTER, _d(2027, 1, 1), False, False),
    ("half-2", MONITOR, EXPECTED, DatePrecision.HALF_YEAR, _d(2026, 7, 1), False, False),
    ("half-2-review", MONITOR, REVIEW_ON, DatePrecision.HALF_YEAR, _d(2026, 7, 1), True, True),
    ("year-2026", WAIT, REVIEW_ON, DatePrecision.YEAR, _d(2026, 1, 1), True, True),
    ("year-2027", MONITOR, REVIEW_ON, DatePrecision.YEAR, _d(2027, 1, 1), False, False),
    ("undated-wait", WAIT, EXPECTED, DatePrecision.EXACT, None, False, False),
    ("late-plan", DO, DEADLINE, DatePrecision.EXACT, _d(2026, 9, 1), False, False),
    ("vague-plan", DO, EXPECTED, DatePrecision.MONTH, _d(2026, 9, 1), False, False),
]


def _population(owner, day):
    """The rows above on one owner's desk; the Matter pks due on ``day``."""
    column = 5 if day == LAST_DAY_OF_Q3 else 6
    expected: set = set()
    for index, row in enumerate(POPULATION):
        name, kind, semantics, precision, stored = row[:5]
        matter = factories.MatterFactory(
            owner=owner,
            title=f"Ülevaatus {name}",
            reference_year=2026,
            reference_number=800 + index,
        )
        set_next_action(
            matter=matter,
            text=name,
            kind=kind,
            date_semantics=semantics,
            target_date=stored,
            date_precision=precision,
            responsible=owner,
            actor=owner,
        )
        if row[column]:
            expected.add(matter.pk)
    return expected


@pytest.fixture(params=[LAST_DAY_OF_Q3, FIRST_DAY_OF_Q4], ids=["30.9.2026", "1.10.2026"])
def day(request, monkeypatch):
    """The frozen day. The readers are handed it; the clock agrees with them."""
    monkeypatch.setattr(timezone, "localdate", lambda *args, **kwargs: request.param)
    return request.param


@pytest.mark.parametrize("viewer_name", ["specialist", "department_head"])
def test_every_surface_counts_the_same_reviews(request, specialist, day, viewer_name):
    """`WORK_RIPE` == `?tegevus=ulevaatus` == `REVIEW_DUE` == the row predicate.

    Sets, not counts: a count would hide two rows moving in opposite directions.
    The population holds a review dated today on both days, a month and a
    quarter that open on 1 October, and periods already running — every case
    the period-end rule used to put behind the register.
    """
    expected = _population(specialist, day)
    viewer = request.getfixturevalue(viewer_name)

    items = wi.work_items(viewer, today=day)
    ripe_rows = {item.matter_id for item in items if item.is_review_ripe}
    work_ripe = wi.work_population_ids(viewer, wi.WORK_RIPE, today=day, items=items)
    register = set(
        filter_by_next_action(
            Matter.objects.visible_to(viewer), viewer, REVIEW_DUE, day
        ).values_list("pk", flat=True)
    )
    statistic = review_due(
        ReportingContext(
            viewer=viewer, period=parse_period("koik", day), today=day, now=timezone.now()
        )
    )
    model_sql = set(NextAction.objects.due_for_review(day).values_list("matter_id", flat=True))
    model_python = {
        action.matter_id
        for action in NextAction.objects.filter(status=ActionStatus.OPEN)
        if action.is_due_for_review(day)
    }

    assert ripe_rows == expected
    assert work_ripe == expected
    assert register == expected
    assert model_sql == expected
    assert model_python == expected
    assert statistic.value == len(expected)


def test_the_register_chip_selects_the_same_reviews_over_http(client, specialist, day):
    """`/teemad/?tegevus=ulevaatus`, read off the page the statistic links to."""
    expected = _population(specialist, day)
    client.force_login(specialist)

    response = client.get(reverse("matters:matter_list"), {"olek": "avatud", "tegevus": REVIEW_DUE})

    assert response.status_code == 200
    listed = {matter.pk for matter in response.context["page"].object_list}
    assert listed == expected


def test_needs_attention_holds_every_ripe_review_and_nothing_merely_ahead(specialist, day):
    """`Vajab sekkumist`'s dated half is overdue ∪ ripe — the same ripe."""
    expected = _population(specialist, day)
    items = wi.work_items(specialist, today=day)

    attention = {
        item.matter_id
        for item in wi.work_population_items(items, wi.WORK_NEEDS_ATTENTION, day)
        if item.action_kind in (WAIT, MONITOR)
    }

    assert attention == expected


def test_the_portfolio_and_kiirvaade_read_the_same_ripeness(specialist, department_head, day):
    """`PortfolioRow.needs_attention` and the manager's «Vajab sekkumist» count."""
    expected = _population(specialist, day)
    late = set(NextAction.objects.overdue(day).values_list("matter_id", flat=True))

    page = my_work.build_my_work(department_head, day, subject=specialist)

    assert page.portfolio is not None
    flagged = {row.matter.pk for row in page.portfolio.all_rows if row.needs_attention}
    assert flagged == expected | late
    desk = wi.work_items(department_head, today=day, responsible=specialist)
    attention = wi.work_population_ids(
        department_head, wi.WORK_NEEDS_ATTENTION, today=day, items=desk, responsible=specialist
    )
    quick = next(row for row in page.quick if row.label == "Vajab sekkumist")
    assert expected <= attention
    assert quick.value == len(attention)


def test_osakond_lists_every_ripe_review(specialist, department_head, day):
    expected = _population(specialist, day)
    items = wi.work_items(department_head, today=day)

    rows = ov.intervention_rows(department_head, day, items)

    assert {row.matter.pk for row in rows if row.reason == ov.REASON_RIPE} == expected


# ---------------------------------------------------------------------------
# Osakond prints a period as a period
# ---------------------------------------------------------------------------


def _one(owner, *, kind, semantics, precision, stored, title):
    matter = factories.MatterFactory(owner=owner, title=title)
    set_next_action(
        matter=matter,
        text=title,
        kind=kind,
        date_semantics=semantics,
        target_date=stored,
        date_precision=precision,
        responsible=owner,
        actor=owner,
    )
    return matter


@pytest.mark.parametrize(
    ("precision", "stored", "shown", "fake_day"),
    [
        (DatePrecision.MONTH, _d(2026, 9, 1), "september 2026", "01.09"),
        (DatePrecision.QUARTER, _d(2026, 7, 1), "III kvartal 2026", "01.07"),
        (DatePrecision.YEAR, _d(2026, 1, 1), "2026", "01.01"),
    ],
)
def test_an_approximate_review_is_printed_at_its_precision(
    specialist, department_head, precision, stored, shown, fake_day
):
    """`VAATAN ÜLE 01.09` for *september 2026* named a day nobody chose.

    ADR 0079 §2 makes the anchor internal and §3 says every surface prints a
    date at the precision it was recorded to — Osakond included.
    """
    day = _d(2026, 10, 5)
    matter = _one(
        specialist,
        kind=MONITOR,
        semantics=REVIEW_ON,
        precision=precision,
        stored=stored,
        title="Perioodi ülevaatus",
    )

    rows = ov.intervention_rows(department_head, day, wi.work_items(department_head, today=day))
    row = next(row for row in rows if row.matter.pk == matter.pk)

    assert row.reason == ov.REASON_RIPE
    assert row.meaning == f"{wi.MEANING_REVIEW} {shown}"
    assert fake_day not in row.meaning


def test_an_exact_review_still_reads_as_its_day(specialist, department_head):
    day = _d(2026, 10, 5)
    matter = _one(
        specialist,
        kind=WAIT,
        semantics=REVIEW_ON,
        precision=DatePrecision.EXACT,
        stored=_d(2026, 9, 24),
        title="Täpne ülevaatus",
    )

    rows = ov.intervention_rows(department_head, day, wi.work_items(department_head, today=day))
    row = next(row for row in rows if row.matter.pk == matter.pk)

    assert row.meaning == f"{wi.MEANING_REVIEW} 24.09"


def test_a_late_approximate_plan_is_not_printed_as_its_last_day(specialist, department_head):
    """The overdue branch of the same cell printed *september 2026* as `30.09`."""
    day = _d(2026, 10, 5)
    matter = _one(
        specialist,
        kind=DO,
        semantics=DEADLINE,
        precision=DatePrecision.MONTH,
        stored=_d(2026, 9, 1),
        title="Kuu plaan",
    )

    rows = ov.intervention_rows(department_head, day, wi.work_items(department_head, today=day))
    row = next(row for row in rows if row.matter.pk == matter.pk)

    assert row.reason == ov.REASON_OVERDUE
    assert row.meaning == f"{wi.MEANING_DEADLINE} september 2026"
    assert "30.09" not in row.meaning


# ---------------------------------------------------------------------------
# Minu asjad: a review dated today is ripe today, and offers the review
# ---------------------------------------------------------------------------


def test_a_review_dated_today_is_offered_for_review_on_minu_asjad(client, specialist, monkeypatch):
    """The exact-today case the period-end rule missed by one day."""
    today = _d(2026, 9, 24)
    monkeypatch.setattr(timezone, "localdate", lambda *args, **kwargs: today)
    matter = _one(
        specialist,
        kind=WAIT,
        semantics=REVIEW_ON,
        precision=DatePrecision.EXACT,
        stored=today,
        title="Tänane ülevaatus",
    )
    client.force_login(specialist)

    body = client.get(reverse("matters:my_work")).content.decode()

    # The row, from its own opening tag to the next one: ripe-styled, and its
    # menu offers the review. Where the link lands is ENG-021's, and is held by
    # `tests/test_wait_monitor_review_path.py`.
    start = body.index(f'href="/teemad/{matter.pk}/"')
    opening = body.rindex('<div class="workrow2', 0, start)
    row = body[opening : body.find('<div class="workrow2', start)]
    assert "workrow2--review" in row
    assert "Vaatasin üle…" in row


# ---------------------------------------------------------------------------
# The class guard
# ---------------------------------------------------------------------------


def test_no_module_compares_a_review_date_with_today_on_its_own():
    """A grep, because the sixth copy will be written somewhere none of these look.

    Five surfaces had the review comparison written out, with two answers. They
    now read :func:`app.workflow.lateness.review_due_q` or
    ``NextAction.is_due_for_review``; a line filtering a review kind by
    ``target_date__lte`` anywhere else is a new definition.
    """
    root = Path(__file__).resolve().parent.parent / "app"
    offenders = []
    for path in root.rglob("*.py"):
        if path.name == "lateness.py":
            continue
        lines = path.read_text(encoding="utf-8").splitlines()
        for number, line in enumerate(lines, 1):
            if "target_date__lte" not in line:
                continue
            window = "\n".join(lines[max(0, number - 7) : number + 6])
            if any(
                marker in window
                for marker in ("REVIEW_KINDS", "ActionKind.WAIT", "ActionKind.MONITOR")
            ):
                offenders.append(f"{path.relative_to(root)}:{number}")

    assert not offenders, (
        "a module compares a review date against a day of its own: "
        + ", ".join(offenders)
        + ". Use `app.workflow.lateness.review_due_q` (ADR 0079 §6)."
    )


#: The review kinds by name. `app/workflow/enums.py` is the one place allowed to
#: list them, as `REVIEW_KINDS` and `DEADLINE_KEEPING_KINDS`.
_REVIEW_KIND_NAMES = frozenset({"WAIT", "MONITOR", "SOURCE"})

#: Hand-listed kinds this guard knowingly leaves, each with its reason. Keyed by
#: the module and the exact literal on the flagged line, so a second listing in
#: the same module is still caught.
_HAND_LISTING_ALLOWED = {
    # The 12-topic evidence pilot's package validator (docs/adr/0173) names the
    # kinds its frozen package format carried. SOURCE postdates that format, and
    # reading `REVIEW_KINDS` there would add a new refusal to a finished
    # importer rather than restate the same rule.
    ("legacy_import/lovable_pilot.py", '{"WAIT", "MONITOR"}'),
}


def _kind_name(node: ast.AST) -> str | None:
    """The kind a literal element names, or ``None``.

    ``ActionKind.WAIT``, ``enums.ActionKind.WAIT`` and either one's ``.value``
    give ``"WAIT"``; a string constant is itself.
    """
    if isinstance(node, ast.Attribute) and node.attr == "value":
        return _kind_name(node.value) if isinstance(node.value, ast.Attribute) else None
    if isinstance(node, ast.Attribute):
        owner = node.value
        if (isinstance(owner, ast.Name) and owner.id == "ActionKind") or (
            isinstance(owner, ast.Attribute) and owner.attr == "ActionKind"
        ):
            return node.attr
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return node.value
    return None


def _is_member(node: ast.AST) -> bool:
    """``ActionKind.X`` or ``ActionKind.X.value``, never a bare string."""
    return not isinstance(node, ast.Constant) and _kind_name(node) is not None


def _literal_names(node: ast.AST) -> set[str] | None:
    """The kinds a literal tuple, list or set names; ``None`` for anything else."""
    if not isinstance(node, ast.Tuple | ast.List | ast.Set):
        return None
    return {name for name in map(_kind_name, node.elts) if name}


def _names_review_kinds(found: set[str] | None, *, at_least: int) -> bool:
    """Names ``at_least`` review kinds and is not the whole vocabulary (no ``DO``)."""
    return found is not None and len(found & _REVIEW_KIND_NAMES) >= at_least and "DO" not in found


def _is_kind_lookup(field: str) -> bool:
    """``kind__in`` or a relation's ``…__kind__in``, never ``record_kind__in``."""
    return field == "kind__in" or field.endswith("__kind__in")


def _is_kind_field(field: str) -> bool:
    return field == "kind" or field.endswith("__kind")


def _mentions_kind(node: ast.AST) -> bool:
    """Whether an expression reads a kind: ``x.kind``, ``kind``, ``x.get("kind")``."""
    for part in ast.walk(node):
        if isinstance(part, ast.Attribute) and part.attr == "kind":
            return True
        if isinstance(part, ast.Name) and part.id == "kind":
            return True
        if isinstance(part, ast.Constant) and part.value == "kind":
            return True
    return False


def _q_kind_names(node: ast.AST) -> set[str]:
    """The kinds an ``Q(kind=…) | Q(kind=…)`` chain names."""
    if isinstance(node, ast.BinOp) and isinstance(node.op, ast.BitOr):
        return _q_kind_names(node.left) | _q_kind_names(node.right)
    if isinstance(node, ast.Call):
        found = set()
        for keyword in node.keywords:
            if keyword.arg and _is_kind_field(keyword.arg):
                name = _kind_name(keyword.value)
                if name:
                    found.add(name)
        return found
    return set()


def _hand_listed_review_kinds(tree: ast.AST) -> list[int]:
    """Line numbers where review kinds are listed by hand instead of read.

    The shapes, each the way the next copy is likeliest to be written:

    * a literal collection of two or more ``ActionKind`` review members, used
      anywhere (``(ActionKind.WAIT, ActionKind.MONITOR)``, ``.value`` included);
    * any literal handed to a kind lookup (``kind__in=…`` as a keyword or as
      ``**{"kind__in": …}``) naming even **one** review kind, strings included:
      ``kind__in=(ActionKind.WAIT,)`` is `DEADLINE_KEEPING_KINDS` written out;
    * a membership test against a literal (``kind in {"WAIT", "MONITOR"}``)
      naming two review kinds, or one when the left side reads a kind;
    * a ``Q(kind=WAIT) | Q(kind=MONITOR)`` chain naming two review kinds.

    A collection that also names ``DO`` is the whole vocabulary, not a review
    rule, and is left alone; so is a lookup on another field (``record_kind__in``).
    """
    flagged: set[int] = set()
    for node in ast.walk(tree):
        if (
            isinstance(node, ast.Tuple | ast.List | ast.Set)
            and node.elts
            and all(_is_member(element) for element in node.elts)
            and _names_review_kinds(_literal_names(node), at_least=2)
        ):
            flagged.add(node.lineno)
        if (
            isinstance(node, ast.keyword)
            and node.arg
            and _is_kind_lookup(node.arg)
            and _names_review_kinds(_literal_names(node.value), at_least=1)
        ):
            flagged.add(node.value.lineno)
        if isinstance(node, ast.Dict):
            for key, value in zip(node.keys, node.values, strict=True):
                if (
                    isinstance(key, ast.Constant)
                    and isinstance(key.value, str)
                    and _is_kind_lookup(key.value)
                    and _names_review_kinds(_literal_names(value), at_least=1)
                ):
                    flagged.add(value.lineno)
        if isinstance(node, ast.Compare):
            for op, right in zip(node.ops, node.comparators, strict=True):
                if not isinstance(op, ast.In | ast.NotIn):
                    continue
                needed = 1 if _mentions_kind(node.left) else 2
                if _names_review_kinds(_literal_names(right), at_least=needed):
                    flagged.add(node.lineno)
        if (
            isinstance(node, ast.BinOp)
            and isinstance(node.op, ast.BitOr)
            and _names_review_kinds(_q_kind_names(node), at_least=2)
        ):
            flagged.add(node.lineno)
    return sorted(flagged)


def test_no_module_lists_the_review_kinds_by_hand():
    """Regression: DL-01. `REVIEW_KINDS` is the one list of review kinds.

    The notification population once filtered ``kind__in=(ActionKind.WAIT,
    ActionKind.MONITOR)``; r54 added ``ActionKind.SOURCE`` to `REVIEW_KINDS`,
    and that hand-written pair silently left it out (2026-11-11 audit). The
    grep above only looks at lines comparing ``target_date__lte``, so it could
    not see it. This one reads every module's syntax tree instead.
    """
    root = Path(__file__).resolve().parent.parent / "app"
    allowed = root / "workflow" / "enums.py"
    offenders = []
    for path in root.rglob("*.py"):
        if path == allowed or "migrations" in path.parts:
            continue
        source = path.read_text(encoding="utf-8")
        lines = source.splitlines()
        relative = path.relative_to(root).as_posix()
        for number in _hand_listed_review_kinds(ast.parse(source)):
            if any(
                module == relative and literal in lines[number - 1]
                for module, literal in _HAND_LISTING_ALLOWED
            ):
                continue
            offenders.append(f"{relative}:{number}")

    assert not offenders, (
        "a module lists review kinds by hand: "
        + ", ".join(offenders)
        + ". Use `app.workflow.enums.REVIEW_KINDS` (or `DEADLINE_KEEPING_KINDS`)."
    )


def test_every_allowed_hand_listing_is_still_there():
    """An allowance that outlives its line is a hole in the guard; remove it then."""
    root = Path(__file__).resolve().parent.parent / "app"
    for module, literal in _HAND_LISTING_ALLOWED:
        assert literal in (root / module).read_text(encoding="utf-8"), (module, literal)


@pytest.mark.parametrize(
    ("source", "caught"),
    [
        # Collections of members, used anywhere.
        ("q.filter(kind__in=(ActionKind.WAIT, ActionKind.MONITOR))", True),
        ("q.filter(kind__in=[ActionKind.MONITOR, ActionKind.SOURCE])", True),
        ("KINDS = (ActionKind.WAIT, ActionKind.MONITOR)", True),
        ("KINDS = {enums.ActionKind.WAIT, enums.ActionKind.SOURCE}", True),
        ("KINDS = (ActionKind.WAIT.value, ActionKind.MONITOR.value)", True),
        # Kind lookups: one review kind is enough.
        ("q.exclude(kind__in=(ActionKind.WAIT,))", True),
        ('Q(action__kind__in=("WAIT", "MONITOR"))', True),
        ('q.filter(kind__in=["SOURCE"])', True),
        ('q.filter(**{"kind__in": ("WAIT", "MONITOR")})', True),
        ('q.filter(**{"action__kind__in": [ActionKind.WAIT]})', True),
        # Membership tests.
        ('if step.get("kind") in {"WAIT", "MONITOR"}: pass', True),
        ("if action.kind in (ActionKind.WAIT,): pass", True),
        ("if action.kind not in [ActionKind.WAIT.value, ActionKind.SOURCE.value]: pass", True),
        ('if label in ("WAIT", "MONITOR"): pass', True),
        # Q chains.
        ("q.filter(Q(kind=ActionKind.WAIT) | Q(kind=ActionKind.MONITOR))", True),
        ('Q(kind="WAIT") | Q(kind="SOURCE") | Q(pk=1)', True),
        # Left alone.
        ("q.filter(kind__in=REVIEW_KINDS)", False),
        ("q.exclude(kind__in=DEADLINE_KEEPING_KINDS)", False),
        ("q.filter(kind__in=(ActionKind.DO, ActionKind.WAIT, ActionKind.MONITOR))", False),
        ("q.filter(kind=ActionKind.WAIT)", False),
        ('LABELS = ("WAIT", "MONITOR")', False),
        ('q.filter(record_kind__in=("WAIT", "MONITOR"))', False),
        ('q.filter(record_kind__in=["SOURCE"])', False),
        ("if action.kind in REVIEW_KINDS: pass", False),
        ('if label in ("WAIT",): pass', False),
        ("q.filter(Q(kind=ActionKind.WAIT) | Q(status=ActionStatus.OPEN))", False),
        ('if step.get("kind") in {"DO", "WAIT", "MONITOR"}: pass', False),
    ],
)
def test_the_hand_listing_guard_catches_what_it_should(source, caught):
    """Regression: DL-01. The guard itself, against the shapes it exists for."""
    assert bool(_hand_listed_review_kinds(ast.parse(source))) is caught
