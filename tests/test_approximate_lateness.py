"""An approximate plan is missed only once its whole period has ended.

The defect this closes
----------------------
An approximate ``NextAction`` stores the **first day** of the period it stands
for, because a period has to sort somewhere (:mod:`app.workflow.dates`). Every
lateness comparison then read that anchor as the commitment, so a step planned
for *september 2026* was overdue on 2 September — the lawyer had the whole
month, and four separate surfaces told them they had missed it on day two, one
of them with *«30 p üle»* on 1 October.

The rule now: ``DO`` + ``DEADLINE`` goes late once the **last** day of its
stored period is behind us, and ``days_late`` counts from that day (ADR 0079).
Which kinds may be late at all is unchanged — a ``WAIT`` on a ministry is not a
failure and never was.

What this module holds
----------------------
The boundary matrix per precision, the two readings agreeing on the same
population, and the ``due_for_review`` semantics **not** moving with them. That
last one is the point of §12 of the brief: *«vaatan üle oktoobris»* may become
due when October opens, while *«plaanis oktoobris»* is missed only when October
closes, and a future round that quietly unifies the two would take a decision
that was made deliberately.
"""

from __future__ import annotations

import datetime

import pytest

from app.matters.dashboard import overdue_actions
from app.matters.selectors import filter_by_next_action
from app.workflow.enums import ActionKind, ActionStatus, DatePrecision, DateSemantics
from app.workflow.lateness import days_past_period, overdue_date_q, period_end_for
from app.workflow.models import NextAction
from tests import factories

pytestmark = pytest.mark.django_db


def _action(matter, *, on, precision, kind=ActionKind.DO, semantics=DateSemantics.DEADLINE):
    return factories.NextActionFactory(
        matter=matter,
        text="Koosta arvamus",
        kind=kind,
        date_semantics=semantics,
        target_date=on,
        date_precision=precision,
        status=ActionStatus.OPEN,
    )


# ---------------------------------------------------------------------------
# The boundary, precision by precision
# ---------------------------------------------------------------------------

#: ``(precision, anchor, day, expected)`` — one row per boundary that matters.
#:
#: Every anchor is the first day of its own period, which is what the forms and
#: the importer store. The days are the ones a wrong rule gets wrong: the first
#: day of the period (where the anchor comparison used to fire), its last day,
#: and the first day after it.
BOUNDARIES = [
    # EXACT — unchanged. The deadline is the last day it may be done on.
    (DatePrecision.EXACT, datetime.date(2026, 9, 15), datetime.date(2026, 9, 14), False),
    (DatePrecision.EXACT, datetime.date(2026, 9, 15), datetime.date(2026, 9, 15), False),
    (DatePrecision.EXACT, datetime.date(2026, 9, 15), datetime.date(2026, 9, 16), True),
    # INFERRED is a day that was read out of free text, not a vague day.
    (DatePrecision.INFERRED, datetime.date(2026, 9, 15), datetime.date(2026, 9, 15), False),
    (DatePrecision.INFERRED, datetime.date(2026, 9, 15), datetime.date(2026, 9, 16), True),
    # MONTH — the whole of September, including the 30th.
    (DatePrecision.MONTH, datetime.date(2026, 9, 1), datetime.date(2026, 9, 1), False),
    (DatePrecision.MONTH, datetime.date(2026, 9, 1), datetime.date(2026, 9, 15), False),
    (DatePrecision.MONTH, datetime.date(2026, 9, 1), datetime.date(2026, 9, 30), False),
    (DatePrecision.MONTH, datetime.date(2026, 9, 1), datetime.date(2026, 10, 1), True),
    # A leap February, where the last day is not the one a constant would name.
    (DatePrecision.MONTH, datetime.date(2028, 2, 1), datetime.date(2028, 2, 29), False),
    (DatePrecision.MONTH, datetime.date(2028, 2, 1), datetime.date(2028, 3, 1), True),
    # And the same month in a common year, where the 29th does not exist.
    (DatePrecision.MONTH, datetime.date(2026, 2, 1), datetime.date(2026, 2, 28), False),
    (DatePrecision.MONTH, datetime.date(2026, 2, 1), datetime.date(2026, 3, 1), True),
    # QUARTER — III kvartal runs to 30 September.
    (DatePrecision.QUARTER, datetime.date(2026, 7, 1), datetime.date(2026, 7, 1), False),
    (DatePrecision.QUARTER, datetime.date(2026, 7, 1), datetime.date(2026, 9, 30), False),
    (DatePrecision.QUARTER, datetime.date(2026, 7, 1), datetime.date(2026, 10, 1), True),
    (DatePrecision.QUARTER, datetime.date(2026, 10, 1), datetime.date(2026, 12, 31), False),
    (DatePrecision.QUARTER, datetime.date(2026, 10, 1), datetime.date(2027, 1, 1), True),
    # HALF_YEAR — historical only, and still read correctly.
    (DatePrecision.HALF_YEAR, datetime.date(2027, 1, 1), datetime.date(2027, 6, 30), False),
    (DatePrecision.HALF_YEAR, datetime.date(2027, 1, 1), datetime.date(2027, 7, 1), True),
    (DatePrecision.HALF_YEAR, datetime.date(2027, 7, 1), datetime.date(2027, 12, 31), False),
    (DatePrecision.HALF_YEAR, datetime.date(2027, 7, 1), datetime.date(2028, 1, 1), True),
    # YEAR — 2027 is not missed until 2028 begins.
    (DatePrecision.YEAR, datetime.date(2027, 1, 1), datetime.date(2027, 1, 1), False),
    (DatePrecision.YEAR, datetime.date(2027, 1, 1), datetime.date(2027, 12, 31), False),
    (DatePrecision.YEAR, datetime.date(2027, 1, 1), datetime.date(2028, 1, 1), True),
]

BOUNDARY_IDS = [
    f"{precision}-{anchor.isoformat()}-on-{day.isoformat()}"
    for precision, anchor, day, _ in BOUNDARIES
]


@pytest.mark.parametrize(("precision", "anchor", "day", "expected"), BOUNDARIES, ids=BOUNDARY_IDS)
def test_an_action_is_late_only_once_its_period_has_ended(
    normal_matter, precision, anchor, day, expected
):
    action = _action(normal_matter, on=anchor, precision=precision)

    assert action.is_overdue(day) is expected


@pytest.mark.parametrize(("precision", "anchor", "day", "expected"), BOUNDARIES, ids=BOUNDARY_IDS)
def test_the_queryset_answers_the_boundary_the_same_way(
    normal_matter, precision, anchor, day, expected
):
    """The SQL reading of the same rule, on the same row.

    Not a duplicate of the test above. That one reads a loaded object in
    Python; this one asks PostgreSQL, and the two are separate implementations
    of one rule — which is exactly the pair that drifts.
    """
    action = _action(normal_matter, on=anchor, precision=precision)

    assert (action in NextAction.objects.overdue(day)) is expected


# ---------------------------------------------------------------------------
# days_late
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("precision", "anchor", "day", "expected"),
    [
        (DatePrecision.EXACT, datetime.date(2026, 9, 15), datetime.date(2026, 9, 16), 1),
        (DatePrecision.EXACT, datetime.date(2026, 9, 15), datetime.date(2026, 9, 25), 10),
        # The headline case: one day late on 1 October, not thirty.
        (DatePrecision.MONTH, datetime.date(2026, 9, 1), datetime.date(2026, 10, 1), 1),
        (DatePrecision.MONTH, datetime.date(2026, 9, 1), datetime.date(2026, 9, 30), 0),
        (DatePrecision.QUARTER, datetime.date(2026, 7, 1), datetime.date(2026, 10, 3), 3),
        (DatePrecision.HALF_YEAR, datetime.date(2027, 1, 1), datetime.date(2027, 7, 2), 2),
        (DatePrecision.YEAR, datetime.date(2027, 1, 1), datetime.date(2028, 1, 1), 1),
    ],
)
def test_days_late_is_counted_from_the_last_day_of_the_period(precision, anchor, day, expected):
    assert days_past_period(anchor, precision, day) == expected


def test_the_model_property_counts_from_the_period_end(normal_matter, monkeypatch):
    """`NextAction.days_late` reads the clock itself, so freeze it.

    A month-precision September plan read on 1 October: the anchor is 01.09 and
    the honest answer is **1**, not 30. The property takes no argument on
    purpose — it is read from a template — which is why the day is pinned here
    the way `test_deadline_grouping` pins it.
    """
    from django.utils import timezone

    monkeypatch.setattr(timezone, "localdate", lambda *a, **kw: datetime.date(2026, 10, 1))
    action = _action(normal_matter, on=datetime.date(2026, 9, 1), precision=DatePrecision.MONTH)

    assert action.days_late == 1


def test_the_model_property_is_zero_inside_the_period(normal_matter, monkeypatch):
    from django.utils import timezone

    monkeypatch.setattr(timezone, "localdate", lambda *a, **kw: datetime.date(2026, 9, 30))
    action = _action(normal_matter, on=datetime.date(2026, 9, 1), precision=DatePrecision.MONTH)

    assert action.is_overdue() is False
    assert action.days_late == 0


def test_days_late_is_zero_for_something_not_yet_late():
    inside = datetime.date(2026, 9, 30)

    assert days_past_period(datetime.date(2026, 9, 1), DatePrecision.MONTH, inside) == 0
    assert days_past_period(None, DatePrecision.EXACT, inside) == 0


def test_a_period_end_outside_the_supported_year_range_falls_back_to_the_anchor():
    """An 1987 register row is evidence, not a 500.

    `period_bounds` refuses a year outside 1990–2100 — correct for a form, and
    the wrong answer for a work surface reading a row that already exists.
    """
    ancient = datetime.date(1987, 5, 1)

    assert period_end_for(ancient, DatePrecision.YEAR) == ancient
    assert period_end_for(ancient, DatePrecision.EXACT) == ancient


# ---------------------------------------------------------------------------
# Only DO + DEADLINE, still
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "precision", [DatePrecision.MONTH, DatePrecision.QUARTER, DatePrecision.YEAR]
)
@pytest.mark.parametrize(
    ("kind", "semantics"),
    [
        (ActionKind.WAIT, DateSemantics.EXPECTED_AROUND),
        (ActionKind.WAIT, DateSemantics.REVIEW_ON),
        (ActionKind.MONITOR, DateSemantics.REVIEW_ON),
        (ActionKind.DO, DateSemantics.EXPECTED_AROUND),
        (ActionKind.DO, DateSemantics.REVIEW_ON),
    ],
)
def test_no_other_combination_becomes_late_when_its_period_ends(
    normal_matter, precision, kind, semantics
):
    """Period-end lateness is a new boundary, not a new population.

    A ``WAIT`` whose quarter is over is still not late. Widening the date rule
    without widening the kind rule is the whole point: the list stays a list of
    things somebody actually missed (master specification 18.8).
    """
    action = _action(
        normal_matter,
        on=datetime.date(2026, 7, 1),
        precision=precision,
        kind=kind,
        semantics=semantics,
    )
    long_after = datetime.date(2029, 1, 1)

    assert action.is_overdue(long_after) is False
    assert action not in NextAction.objects.overdue(long_after)


def test_a_dateless_action_is_never_late(normal_matter):
    action = _action(
        normal_matter,
        on=None,
        precision=DatePrecision.EXACT,
        kind=ActionKind.WAIT,
        semantics=DateSemantics.EXPECTED_AROUND,
    )

    assert action.is_overdue(datetime.date(2029, 1, 1)) is False


# ---------------------------------------------------------------------------
# Review dates keep their own boundary
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("day", "expected"),
    [
        (datetime.date(2026, 9, 30), False),
        (datetime.date(2026, 10, 1), True),
        (datetime.date(2026, 10, 20), True),
    ],
)
def test_a_review_date_comes_round_when_its_period_opens(normal_matter, day, expected):
    """§12. *«Vaatan üle oktoobris»* is due when October begins.

    Deliberately **not** the rule above it. A review date is a reminder to look,
    and looking in the first week of the month it names is the behaviour asked
    for; a deadline is a promise, and a promise made for October is kept on any
    day of October. The two boundaries are a month apart on purpose, and this
    test exists so that a later round unifying them has to say why.
    """
    action = _action(
        normal_matter,
        on=datetime.date(2026, 10, 1),
        precision=DatePrecision.MONTH,
        kind=ActionKind.WAIT,
        semantics=DateSemantics.REVIEW_ON,
    )

    assert action.is_due_for_review(day) is expected
    assert (action in NextAction.objects.due_for_review(day)) is expected


# ---------------------------------------------------------------------------
# Every surface that counts lateness agrees
# ---------------------------------------------------------------------------


def _mixed_population(owner):
    """One action per precision per side of its own boundary, on 1 October 2026.

    Each Matter carries at most one open action by database constraint, so this
    is one Matter per row.
    """
    day = datetime.date(2026, 10, 1)
    rows = [
        ("exact-late", DatePrecision.EXACT, datetime.date(2026, 9, 30), True),
        ("exact-today", DatePrecision.EXACT, day, False),
        ("month-over", DatePrecision.MONTH, datetime.date(2026, 9, 1), True),
        ("month-current", DatePrecision.MONTH, datetime.date(2026, 10, 1), False),
        ("quarter-over", DatePrecision.QUARTER, datetime.date(2026, 7, 1), True),
        ("quarter-current", DatePrecision.QUARTER, datetime.date(2026, 10, 1), False),
        ("half-over", DatePrecision.HALF_YEAR, datetime.date(2026, 1, 1), True),
        ("half-current", DatePrecision.HALF_YEAR, datetime.date(2026, 7, 1), False),
        ("year-over", DatePrecision.YEAR, datetime.date(2025, 1, 1), True),
        ("year-current", DatePrecision.YEAR, datetime.date(2026, 1, 1), False),
        ("inferred-late", DatePrecision.INFERRED, datetime.date(2026, 9, 30), True),
        ("inferred-today", DatePrecision.INFERRED, day, False),
    ]
    expected = set()
    for index, (name, precision, anchor, late) in enumerate(rows):
        matter = factories.MatterFactory(
            owner=owner, title=name, reference_year=2026, reference_number=700 + index
        )
        action = _action(matter, on=anchor, precision=precision)
        action.responsible = owner
        action.save(update_fields=["responsible"])
        if late:
            expected.add(action.pk)
    return day, expected


def test_the_queryset_and_the_object_agree_over_a_mixed_population(specialist):
    """Set equality, not two counts that happen to match.

    The failure this guards is the one the brief names: fixing the property and
    leaving the SQL filter reading the anchor. A count would hide it the moment
    two rows moved in opposite directions.
    """
    day, expected = _mixed_population(specialist)
    population = list(NextAction.objects.filter(status=ActionStatus.OPEN))

    from_sql = {action.pk for action in NextAction.objects.overdue(day)}
    from_python = {action.pk for action in population if action.is_overdue(day)}

    assert from_sql == expected
    assert from_python == expected


def test_the_department_dashboard_counts_the_same_rows(specialist, department_head):
    """`dashboard.overdue_actions` is the department head's reading of it."""
    day, expected = _mixed_population(specialist)

    assert {action.pk for action in overdue_actions(department_head, day)} == expected


def test_the_register_chip_selects_the_same_matters(specialist, department_head):
    """`?tegevus=hilinenud` — the list behind the number.

    A chip that selected a wider set than the rows it highlighted is the
    disagreement this whole PR exists to remove.
    """
    day, expected = _mixed_population(specialist)
    late_matters = {action.matter_id for action in NextAction.objects.filter(pk__in=expected)}

    from app.matters.models import Matter

    selected = filter_by_next_action(
        Matter.objects.visible_to(department_head), department_head, "hilinenud", day
    )

    assert {matter.pk for matter in selected} == late_matters


def test_the_statistic_counts_the_same_rows(specialist, department_head):
    """The published `OVERDUE_DO_DEADLINE` figure, through its own context.

    Statistika is the fourth reading of the same population, and the one a
    department head quotes. It had its own ``target_date__lt`` too.
    """
    from django.utils import timezone

    from app.reporting.context import ReportingContext, parse_period
    from app.reporting.selectors.activity import overdue_do_deadline

    day, expected = _mixed_population(specialist)
    context = ReportingContext(
        viewer=department_head,
        period=parse_period("koik", day),
        today=day,
        now=timezone.now(),
    )

    assert overdue_do_deadline(context).value == len(expected)


def _is_banding(path, number: int) -> bool:
    """Whether the six lines above this one claim it bands rather than judges.

    A comment at the call site rather than a list of file/line pairs in here:
    the exemption has to be readable by whoever is editing the code, not by
    whoever is editing the test.
    """
    lines = path.read_text(encoding="utf-8").splitlines()
    window = lines[max(0, number - 7) : number]
    return any("Banding, not lateness" in line for line in window)


def test_the_shared_condition_is_the_one_every_surface_imports():
    """A grep, because the next copy will be written somewhere none of these look.

    Four modules counted "late" with their own ``target_date__lt``. They now
    share :func:`overdue_date_q`, and a fifth copy would be a fifth definition.
    """
    from pathlib import Path

    root = Path(__file__).resolve().parent.parent / "app"
    sources = [
        (path, number, line)
        for path in root.rglob("*.py")
        for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1)
        if "target_date__lt=" in line
    ]
    offenders = [
        f"{path.relative_to(root)}:{number}"
        for path, number, _ in sources
        # `my_work_timeline` bands *every* open action by the day it sorts on,
        # review kinds included. That is a position on a timeline and not a
        # verdict, and it says so at the call site.
        if not _is_banding(path, number)
    ]

    assert not offenders, (
        "a module is comparing an action's anchor against today: "
        + ", ".join(offenders)
        + ". An approximate anchor is not a commitment — use "
        "`app.workflow.lateness.overdue_date_q`."
    )


def test_the_shared_condition_is_a_pure_q_object():
    """`overdue_date_q` must compose with the rest of a filter, not replace it."""
    from django.db.models import Q

    assert isinstance(overdue_date_q(datetime.date(2026, 10, 1)), Q)
