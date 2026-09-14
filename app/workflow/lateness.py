"""When a planned step is genuinely late, as one rule read two ways.

Why this module exists
----------------------
``NextAction`` stores a date and a :class:`~app.workflow.enums.DatePrecision`.
For an approximate precision the stored date is **the first day of the period**
— an anchor for ordering and querying, never something anybody committed to
(:mod:`app.workflow.dates`). Lateness compared that anchor against today, so a
step planned for *september 2026* was reported as overdue on 2 September: the
lawyer had the whole month, and the product said they had missed it on day two.

The rule this module states instead: **an approximate plan becomes late only
once its whole period has ended.** *september 2026* is late on 1 October and
not before; *III kvartal 2026* likewise; *2027* on 1 January 2028.

Two readings, deliberately side by side
---------------------------------------
Lateness is asked as a Python question about one loaded row
(:func:`is_past_period`, behind ``NextAction.is_overdue``) and as a SQL question
about a whole population (:func:`overdue_date_q`, behind
``NextAction.objects.overdue()`` and the three other queries that count the same
thing). A predicate answered in two places is how a page's summary line starts
disagreeing with the list underneath it, so both readings are built here from
the same arithmetic and ``tests/test_approximate_lateness.py`` asserts they
return the same set.

The SQL reading is the same rule turned inside out. Python asks *has this row's
period ended?*; SQL cannot call :func:`~app.workflow.dates.period_bounds` per
row, so it asks *does this row's anchor fall before the period today is in, at
that row's own precision?* Periods of one precision partition the calendar and
an anchor always lies inside its own period, so the two questions have the same
answer — and the SQL one needs only five constants, computed once from ``today``.

``EXACT`` and ``INFERRED`` are days, not periods: ``INFERRED`` records that the
value was read out of free text, not that the day is vague. Both keep the
comparison they always had, which is why neither appears in
:data:`APPROXIMATE_PRECISIONS`.

This module does **not** touch ``due_for_review``. A review date is a reminder,
not a promise: *«vaatan üle oktoobris»* may reasonably come round when October
begins, while *«plaanis oktoobris»* is missed only once October is over. They
are different questions and the difference is intentional (ADR 0079).
"""

from __future__ import annotations

from datetime import date

from django.db.models import Q

from app.workflow.dates import InvalidPeriod, period_bounds
from app.workflow.enums import DatePrecision

#: The precisions whose stored date stands for a span of days rather than one.
#:
#: Written out rather than derived as "everything except EXACT and INFERRED",
#: because this tuple is also the SQL reading's ``NOT IN`` list: a precision
#: nobody thought about must fall into the exact-day branch and be compared to
#: today, not silently drop out of every branch and become un-late for ever.
APPROXIMATE_PRECISIONS: tuple[str, ...] = (
    DatePrecision.MONTH.value,
    DatePrecision.QUARTER.value,
    DatePrecision.HALF_YEAR.value,
    DatePrecision.YEAR.value,
)


def period_end_for(value: date, precision: str) -> date:
    """The last day ``value`` stands for at ``precision``.

    The anchor itself for a day, the end of the month/quarter/half/year for a
    period. A stored year outside :mod:`app.workflow.dates`'s supported range
    falls back to the anchor rather than raising: a 1987 row imported from the
    register is evidence, and it may not turn a work surface into a 500.
    """
    try:
        return period_bounds(value, precision)[1]
    except InvalidPeriod:
        return value


def is_past_period(value: date | None, precision: str, today: date) -> bool:
    """Whether the whole period ``value`` names has ended before ``today``.

    The day itself is never past: a deadline is the last day it may be done on.
    """
    if value is None:
        return False
    return period_end_for(value, precision) < today


def days_past_period(value: date | None, precision: str, today: date) -> int:
    """Days since the period ended. ``0`` for anything not yet past.

    Counted from the period's **last day**, so a September plan is one day late
    on 1 October rather than thirty — thirty would be a tally of a failure that
    did not happen (master specification 18.8).
    """
    if value is None:
        return 0
    end = period_end_for(value, precision)
    return (today - end).days if end < today else 0


def overdue_date_q(today: date) -> Q:
    """The date half of "genuinely late", for a ``NextAction`` queryset.

    Only the date. Which kinds may be late at all is
    :func:`~app.workflow.models.NextActionQuerySet.overdue`'s business and is
    stated once there, beside the two constants that name it.
    """
    condition = ~Q(date_precision__in=APPROXIMATE_PRECISIONS) & Q(target_date__lt=today)
    for precision in APPROXIMATE_PRECISIONS:
        start, _ = period_bounds(today, precision)
        condition |= Q(date_precision=precision, target_date__lt=start)
    return condition
