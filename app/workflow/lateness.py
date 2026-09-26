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

When a review comes round — the other boundary, stated here too
----------------------------------------------------------------
A review date is a reminder, not a promise: *«vaatan üle oktoobris»* may
reasonably come round when October begins, while *«plaanis oktoobris»* is missed
only once October is over. They are different questions and the difference is
intentional (ADR 0079 §6), so the review rule is **not** the lateness rule with
a different name — it is its own boundary, and it lives here beside the first
one for the reason the first one does.

It used to be written out five times with two answers. The register's
``?tegevus=ulevaatus``, the ``REVIEW_DUE`` statistic and the model helpers
compared the anchor inclusively; the work surfaces — Minu asjad's
*Ülevaatamiseks* and *Vajab sekkumist*, the manager's Kiirvaade, Osakond's
intervention list, the ripe row styling — re-derived it from the **period end**,
copying §4's DO boundary. A review recorded for *oktoober 2026* was due in
Statistika from 1 October and on every work surface from 1 November, and an
exact review dated today was due in one place and not the other (ENG-040).

The rule, one row per case ADR 0079 distinguishes:

=====================  ==================  ===========================================
kind                   date meaning        comes round
=====================  ==================  ===========================================
``WAIT``/``MONITOR``   ``REVIEW_ON``       the first day of the recorded period — the
                                           day itself for ``EXACT``/``INFERRED``
                                           (ADR 0079 §6, *«vaatan üle oktoobris»*)
``WAIT``/``MONITOR``   ``EXPECTED_AROUND`` the same day. §6 is stated of
                                           ``due_for_review``, which has never read
                                           the date meaning, and its rejected
                                           alternative («make ``due_for_review``
                                           period-end too») is exactly the other
                                           reading. The owner may still choose
                                           period end for an expectation; that would
                                           be a branch here and nowhere else.
``WAIT``/``MONITOR``   ``DEADLINE``        the same day. Not late either way: only
                                           ``DO`` + ``DEADLINE`` can be (§4).
``DO``                 any                 never. A plan is late or not late; it is
                                           not a reminder.
=====================  ==================  ===========================================

``MONTH``/``QUARTER``/``HALF_YEAR``/``YEAR`` differ only in how long the period
is. A review is due on its **first** day and stays due until somebody reviews
it, so *III kvartal 2026* is due from 1 July, *2027* from 1 January 2027.

Two readings again, from one definition: :func:`review_has_come_round` for a
loaded row and :func:`review_due_q` for a queryset. The SQL reading is the same
inversion as :func:`overdue_date_q` — *has this row's period begun?* becomes
*does this row's anchor fall no later than the end of the period today is in, at
that row's own precision?* — and ``tests/test_review_due_rule.py`` asserts the
two return the same set on every surface that counts them.
"""

from __future__ import annotations

from datetime import date

from django.db.models import Q

from app.workflow.dates import InvalidPeriod, period_bounds
from app.workflow.enums import REVIEW_KINDS, DatePrecision

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


# ---------------------------------------------------------------------------
# When a review comes round (ADR 0079 §6)
# ---------------------------------------------------------------------------


def period_start_for(value: date, precision: str) -> date:
    """The first day ``value`` stands for at ``precision``.

    The anchor itself for a normalised row, which is every row the forms and the
    importer write. Computed rather than assumed so that a period whose stored
    day is not its first is still read as the period it names, and falling back
    to the value for a year outside the supported range, as
    :func:`period_end_for` does, so an imported 1987 row cannot turn a work
    surface into a 500.
    """
    try:
        return period_bounds(value, precision)[0]
    except InvalidPeriod:
        return value


def review_has_come_round(value: date | None, precision: str, today: date) -> bool:
    """Whether the period a review date names has begun by ``today``.

    The date half of the review rule, for one loaded row. A review dated today
    has come round today: a reminder for the 24th is for the 24th, not the 25th.
    An undated review has not come round and never will by itself — nobody
    said when (docs/adr/0106).
    """
    if value is None:
        return False
    return period_start_for(value, precision) <= today


def review_due_date_q(today: date) -> Q:
    """The date half of the review rule, for a ``NextAction`` queryset.

    :func:`review_has_come_round` turned inside out, the way
    :func:`overdue_date_q` turns :func:`is_past_period`: a period has begun by
    today exactly when its anchor is no later than the last day of the period
    today falls in, at that row's own precision. Periods of one precision
    partition the calendar, so that is the same question asked with five
    constants. ``target_date IS NULL`` matches no branch, which is the undated
    review never coming round.
    """
    condition = ~Q(date_precision__in=APPROXIMATE_PRECISIONS) & Q(target_date__lte=today)
    for precision in APPROXIMATE_PRECISIONS:
        _, end = period_bounds(today, precision)
        condition |= Q(date_precision=precision, target_date__lte=end)
    return condition


def is_review_due(*, kind: str, value: date | None, precision: str, today: date) -> bool:
    """The whole review rule for one row, status aside: a review kind whose
    period has begun. The caller has already decided the row is open."""
    return kind in REVIEW_KINDS and review_has_come_round(value, precision, today)


def review_due_q(today: date) -> Q:
    """The whole review rule for a queryset, status aside.

    What ``?tegevus=ulevaatus``, the ``REVIEW_DUE`` statistic and
    ``NextAction.objects.due_for_review`` filter on, and what
    ``NextAction.is_due_for_review`` — and through it every work surface's
    ``is_review_ripe`` — answers for a single row. One definition, so the
    figure, the list behind it and the styling of the row cannot disagree again.
    """
    return Q(kind__in=REVIEW_KINDS, target_date__isnull=False) & review_due_date_q(today)
