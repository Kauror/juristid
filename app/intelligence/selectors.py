"""Reads for the Matter page and the three generated department views.

Everything here starts from ``visible_to``. Authorization is applied to each
source **before** anything is filtered, grouped, counted or merged, so a
restricted Matter cannot surface through a total, a year option, a month heading
or an empty-state count (Stage-2G brief 31).

The combined *Olulised tähtajad* calendar is a union, not a copy. A commencement
date appears there as a labelled `Jõustumine` row read live from
``MatterEffectiveDate``; no row is duplicated into a second table, so editing the
commencement moves it in both places at once (Stage-2G brief 47, 48).
"""

from __future__ import annotations

import calendar
from dataclasses import dataclass, field
from datetime import date
from typing import Any
from uuid import UUID

from django.db.models import CharField, Q, QuerySet, Value
from django.utils import timezone

from app.intelligence.enums import EffectiveDateKind, EventKind, WorkVictoryStatus
from app.intelligence.models import (
    MatterEffectiveDate,
    MatterImportantDate,
    MatterWorkVictory,
)
from app.workflow.enums import ESTONIAN_MONTHS, ROMAN_QUARTERS, DatePrecision

#: The `?suund=` values. Words rather than booleans, because these appear in a
#: URL somebody pastes into a message.
UPCOMING = "tulevased"
PAST = "moodunud"
ALL = "koik"
DIRECTIONS: tuple[tuple[str, str], ...] = (
    (UPCOMING, "Tulevased"),
    (PAST, "Möödunud"),
    (ALL, "Kõik"),
)

#: The `?allikad=` values on the combined calendar.
SOURCE_ALL = "koik"
SOURCE_IMPORTANT = "tahtajad"
SOURCE_EFFECTIVE = "joustumised"
CALENDAR_SOURCES: tuple[tuple[str, str], ...] = (
    (SOURCE_ALL, "Kõik sündmused"),
    (SOURCE_IMPORTANT, "Ainult tähtajad"),
    (SOURCE_EFFECTIVE, "Ainult jõustumised"),
)

#: The `?aasta=` sentinel for work victories nobody has dated. A word, for the
#: same reason as above, and resolved before any lookup — a sentinel reaching a
#: `period_date__year=` comparison is a ValidationError, not an empty result
#: (Stage-2E's fourth defect class).
UNKNOWN_PERIOD = "teadmata"

#: The columns the two calendar sources project so they can be combined. Same
#: names, same order, on both sides: a union compares positionally.
CALENDAR_COLUMNS = ("id", "matter_id", "date_value", "period_end", "date_precision", "event_kind")

#: How long the *Jõustuvad aktid* default window looks ahead. Not an archival
#: limit — the database keeps everything and `?suund=koik` shows it
#: (Stage-2G brief 17).
DEFAULT_HORIZON_MONTHS = 12


# ---------------------------------------------------------------------------
# The Matter page
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class MatterIntelligence:
    """Everything the three Matter-detail sections render.

    Assembled in one place so the page cannot ask two differently scoped
    questions about the same Matter.
    """

    upcoming_dates: list[MatterImportantDate]
    past_dates: list[MatterImportantDate]
    effective_dates: list[MatterEffectiveDate]
    work_victories: list[MatterWorkVictory]

    @property
    def has_dates(self) -> bool:
        return bool(self.upcoming_dates or self.past_dates)

    @property
    def has_effective_dates(self) -> bool:
        return bool(self.effective_dates)

    @property
    def has_work_victories(self) -> bool:
        return bool(self.work_victories)


def matter_intelligence(matter: Any, user: Any, today: date | None = None) -> MatterIntelligence:
    """The structured facts of one Matter, scoped to this reader.

    Cancelled records are included and marked rather than hidden: an expectation
    that was called off is part of the file's history, and quietly dropping it
    is how a reader concludes nobody ever recorded anything
    (Stage-2G brief 8, 33).
    """
    from django.utils import timezone

    day = today or timezone.localdate()

    dates = list(
        MatterImportantDate.objects.filter(matter=matter)
        .visible_to(user)
        .select_related("created_by")
    )
    upcoming = [record for record in dates if not record.has_passed(day)]
    past = [record for record in dates if record.has_passed(day)]
    # Model ordering already puts these earliest-first. The past reads better
    # newest-first: what happened most recently is what a reader is looking for.
    past.reverse()

    effective = list(
        MatterEffectiveDate.objects.filter(matter=matter)
        .visible_to(user)
        .select_related("created_by")
    )
    # Known dates in chronological order, then the honest unknowns. Model
    # ordering sorts NULL dates first in PostgreSQL, and a commencement whose
    # date is still being settled is not the *first* thing that happens.
    effective.sort(key=lambda record: (record.date_value is None, record.date_value or day))

    victories = list(
        MatterWorkVictory.objects.filter(matter=matter)
        .visible_to(user)
        .select_related("created_by", "confirmed_by")
    )
    return MatterIntelligence(
        upcoming_dates=upcoming,
        past_dates=past,
        effective_dates=effective,
        work_victories=victories,
    )


# ---------------------------------------------------------------------------
# Shared filtering
# ---------------------------------------------------------------------------


def _direction_q(direction: str, today: date, field: str = "period_end") -> Q:
    """Upcoming and past, decided by the **end** of the period.

    II poolaasta 2027 has not passed on 2 July 2027. Comparing the stored anchor
    instead would say it had, which is precisely the false precision the
    precision vocabulary exists to prevent (Stage-2G brief 52).
    """
    if direction == UPCOMING:
        return Q(**{f"{field}__gte": today})
    if direction == PAST:
        return Q(**{f"{field}__lt": today})
    return Q()


def _year_q(year: int | None, field: str = "date_value") -> Q:
    """Which year an approximate period belongs to.

    Every precision this product offers sits inside one calendar year, so the
    anchor's year *is* the period's year: II poolaasta 2027 is 2027, and there
    is no case where the start and the end disagree. Narrower interval filtering
    is deliberately not offered — a day-level filter over a quarter-level fact
    would answer a question the data cannot support (Stage-2G brief 52).
    """
    if year is None:
        return Q()
    return Q(**{f"{field}__year": year})


def important_date_years(user: Any) -> list[int]:
    """Every year the calendar mentions, newest first.

    Built from the same scoped querysets the page lists, so a year that only a
    restricted Matter uses never appears as an option to somebody who may not
    read it (Stage-2G brief 31).
    """
    years: set[int] = set()
    years.update(
        value
        for value in MatterImportantDate.objects.visible_to(user).values_list(
            "date_value__year", flat=True
        )
        if value is not None
    )
    years.update(
        value
        for value in MatterEffectiveDate.objects.visible_to(user)
        .filter(kind=EffectiveDateKind.KNOWN_DATE)
        .values_list("date_value__year", flat=True)
        if value is not None
    )
    return sorted(years, reverse=True)


def effective_date_years(user: Any) -> list[int]:
    return sorted(
        {
            value
            for value in MatterEffectiveDate.objects.visible_to(user).values_list(
                "date_value__year", flat=True
            )
            if value is not None
        },
        reverse=True,
    )


def work_victory_years(user: Any) -> list[int]:
    return sorted(
        {
            value
            for value in MatterWorkVictory.objects.visible_to(user).values_list(
                "period_date__year", flat=True
            )
            if value is not None
        },
        reverse=True,
    )


# ---------------------------------------------------------------------------
# Olulised tähtajad — the combined calendar
# ---------------------------------------------------------------------------


def _calendar_source(queryset: QuerySet, event_kind: str) -> QuerySet:
    """One side of the union, projected onto the shared columns.

    ``order_by()`` with no arguments is load-bearing. Both models declare a
    ``Meta.ordering``, and an operand that carries its own ORDER BY into a
    compound statement is a SQL syntax error on PostgreSQL. The ordering that
    matters is applied once, to the combined query.
    """
    return (
        queryset.order_by()
        .annotate(event_kind=Value(event_kind, output_field=CharField(max_length=32)))
        .values(*CALENDAR_COLUMNS)
    )


def calendar_rows(
    *,
    user: Any,
    today: date,
    direction: str = UPCOMING,
    year: int | None = None,
    sources: str = SOURCE_ALL,
) -> QuerySet:
    """One ordered, paginatable queryset over both kinds of dated event.

    A union rather than a Python merge, so the count above the list and the rows
    in it come from the same statement and pagination cannot drift. Each side is
    authorized independently first; the union only ever combines rows this
    reader was already entitled to (Stage-2G brief 48).

    Ordering is ``(date_value, period_end, id)``: the period's first day, then
    its last, then the time-sortable primary key. The middle key is what makes
    the rule "among periods beginning on the same day, the narrower one comes
    first" — 01.07.2026, then III kvartal 2026, then II poolaasta 2026 —
    deterministic rather than incidental (Stage-2G brief 10).
    """
    important = MatterImportantDate.objects.visible_to(user).active()
    important = important.filter(_direction_q(direction, today) & _year_q(year))

    effective = (
        MatterEffectiveDate.objects.visible_to(user)
        .active()
        .filter(kind=EffectiveDateKind.KNOWN_DATE)
        .filter(_direction_q(direction, today) & _year_q(year))
    )

    if sources == SOURCE_IMPORTANT:
        return _calendar_source(important, EventKind.IMPORTANT_DATE).order_by(
            "date_value", "period_end", "id"
        )
    if sources == SOURCE_EFFECTIVE:
        return _calendar_source(effective, EventKind.EFFECTIVE_DATE).order_by(
            "date_value", "period_end", "id"
        )
    return (
        _calendar_source(important, EventKind.IMPORTANT_DATE)
        .union(_calendar_source(effective, EventKind.EFFECTIVE_DATE), all=True)
        .order_by("date_value", "period_end", "id")
    )


@dataclass(frozen=True)
class CalendarEntry:
    """One rendered calendar line, and which table it came from.

    ``today`` is carried on the row for the same reason
    :class:`app.matters.work_items.WorkItem` carries it: a Django template
    cannot hand an argument to a property, so a row that had to be told what day
    it is would end up being told twice, differently. Whether a date has passed
    is therefore decided once, here, from the day the page was built — not by a
    comparison written into the template against whatever ``today`` the view
    happened to put in the context.
    """

    event_kind: str
    important_date: MatterImportantDate | None = None
    effective_date: MatterEffectiveDate | None = None
    today: date = field(default_factory=timezone.localdate)

    @property
    def record(self) -> MatterImportantDate | MatterEffectiveDate:
        found = self.important_date or self.effective_date
        assert found is not None  # noqa: S101 - one of the two is always set
        return found

    @property
    def matter(self) -> Any:
        return self.record.matter

    @property
    def display_date(self) -> str:
        return self.record.display_date

    @property
    def is_effective_date(self) -> bool:
        return self.event_kind == EventKind.EFFECTIVE_DATE

    @property
    def is_overdue(self) -> bool:
        """A deadline that has passed. Never a commencement.

        A commencement that has passed is not late — the act came into force,
        which is the thing everybody was waiting for (02-EKRAANID §D).
        """
        end = self.record.period_end
        return not self.is_effective_date and end is not None and end < self.today

    @property
    def has_taken_effect(self) -> bool:
        """*Jõustus* rather than *Jõustub* — tense, not lateness."""
        end = self.record.period_end
        return self.is_effective_date and end is not None and end < self.today

    @property
    def title(self) -> str:
        if self.important_date is not None:
            return self.important_date.title
        assert self.effective_date is not None  # noqa: S101
        return self.effective_date.description

    @property
    def period_label(self) -> str:
        return period_label(self.record.date_value, self.record.date_precision)


def hydrate_calendar(
    rows: list[dict[str, Any]], user: Any, today: date | None = None
) -> list[CalendarEntry]:
    """Turn a page of union rows back into records, in the union's order.

    Two queries rather than one per row, and both re-scoped: the union already
    filtered on visibility, and fetching through ``visible_to`` again costs
    nothing and means no future refactor of this function can quietly become the
    one read path that skipped authorization.
    """
    important_ids = [
        row["id"] for row in rows if row["event_kind"] == EventKind.IMPORTANT_DATE.value
    ]
    effective_ids = [
        row["id"] for row in rows if row["event_kind"] == EventKind.EFFECTIVE_DATE.value
    ]

    important: dict[UUID, MatterImportantDate] = {
        record.pk: record
        for record in MatterImportantDate.objects.visible_to(user)
        .filter(pk__in=important_ids)
        .select_related("matter", "matter__owner")
    }
    effective: dict[UUID, MatterEffectiveDate] = {
        record.pk: record
        for record in MatterEffectiveDate.objects.visible_to(user)
        .filter(pk__in=effective_ids)
        .select_related("matter", "matter__owner")
    }

    day = today or timezone.localdate()
    entries: list[CalendarEntry] = []
    for row in rows:
        if row["event_kind"] == EventKind.IMPORTANT_DATE.value:
            record = important.get(row["id"])
            if record is not None:
                entries.append(
                    CalendarEntry(
                        event_kind=EventKind.IMPORTANT_DATE.value,
                        important_date=record,
                        today=day,
                    )
                )
            continue
        found = effective.get(row["id"])
        if found is not None:
            entries.append(
                CalendarEntry(
                    event_kind=EventKind.EFFECTIVE_DATE.value,
                    effective_date=found,
                    today=day,
                )
            )
    return entries


def period_label(value: date | None, precision: str) -> str:
    """The heading a dated row belongs under.

    Exact and month-precision events group by month; approximate ones keep their
    own period as the heading, because filing *III kvartal 2026* under
    "juuli 2026" would state a month nobody named (Stage-2G brief 10, 18).
    """
    if value is None:
        return ""
    if precision == DatePrecision.YEAR:
        return str(value.year)
    if precision == DatePrecision.HALF_YEAR:
        half = "I" if value.month <= 6 else "II"
        return f"{half} poolaasta {value.year}"
    if precision == DatePrecision.QUARTER:
        return f"{ROMAN_QUARTERS[(value.month - 1) // 3]} kvartal {value.year}"
    return f"{ESTONIAN_MONTHS[value.month - 1]} {value.year}"


@dataclass(frozen=True)
class PeriodGroup:
    label: str
    entries: list[Any]


def group_by_period(entries: list[Any]) -> list[PeriodGroup]:
    """Group an already-ordered list into consecutive period headings.

    Consecutive, not bucketed: the ordering guarantees that everything sharing a
    heading is adjacent, because every anchor for a given precision falls on the
    first day of its own period and a wider period always starts earlier.
    """
    groups: list[PeriodGroup] = []
    for entry in entries:
        label = entry.period_label
        if groups and groups[-1].label == label:
            groups[-1].entries.append(entry)
        else:
            groups.append(PeriodGroup(label=label, entries=[entry]))
    return groups


# ---------------------------------------------------------------------------
# Jõustuvad aktid
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class EffectiveDateEntry:
    """A commencement row, wrapped so it groups like a calendar entry."""

    effective_date: MatterEffectiveDate

    @property
    def period_label(self) -> str:
        return period_label(self.effective_date.date_value, self.effective_date.date_precision)


#: The two commencement kinds that have no date, and never will have one until
#: somebody changes the kind.
UNDATED_KINDS = (EffectiveDateKind.GENERAL_ORDER, EffectiveDateKind.UNKNOWN)

#: `?suund=` on Jõustuvad aktid. The default looks a year ahead; the last value
#: is the honest bucket for a date nobody knows yet, which has no place on a
#: chronological axis at all (Stage-2G brief 17).
HORIZON = "eesolevad"
UNDATED = "tapsustamisel"
EFFECTIVE_DIRECTIONS: tuple[tuple[str, str], ...] = (
    (HORIZON, "Eesolevad 12 kuud"),
    (ALL, "Kõik"),
    (PAST, "Möödunud"),
    (UNDATED, "Kuupäev täpsustamisel"),
)


def horizon_end(today: date) -> date:
    """The last day the default *Jõustuvad aktid* window reaches.

    Twelve months on, clamped to the 28th when the target day does not exist —
    which is only ever 29 February, and clamping is the boring correct answer
    there.
    """
    month_index = today.month - 1 + DEFAULT_HORIZON_MONTHS
    year = today.year + month_index // 12
    month = month_index % 12 + 1
    day = min(today.day, calendar.monthrange(year, month)[1])
    return date(year, month, day)


def effective_dates(
    *, user: Any, today: date, direction: str = HORIZON, year: int | None = None
) -> QuerySet:
    """The generated *Jõustuvad aktid* population.

    Read entirely from ``MatterEffectiveDate``. There is no second list to keep
    in step, so a lawyer who edits a commencement on the Matter has already
    edited this page (Stage-2G brief 16).
    """
    queryset = (
        MatterEffectiveDate.objects.visible_to(user)
        .active()
        .select_related("matter", "matter__owner")
    )
    if direction == UNDATED:
        return queryset.filter(kind__in=UNDATED_KINDS)

    queryset = queryset.filter(kind=EffectiveDateKind.KNOWN_DATE)
    queryset = queryset.filter(_year_q(year))
    if direction == PAST:
        return queryset.filter(period_end__lt=today).order_by("-date_value", "-period_end", "-id")
    if direction == HORIZON:
        return queryset.filter(period_end__gte=today, date_value__lte=horizon_end(today))
    return queryset


def undated_effective_count(user: Any) -> int:
    return (
        MatterEffectiveDate.objects.visible_to(user).active().filter(kind__in=UNDATED_KINDS).count()
    )


# ---------------------------------------------------------------------------
# Töövõidud
# ---------------------------------------------------------------------------


def work_victories(*, user: Any, status: str = "", year: str | int | None = None) -> QuerySet:
    """The generated *Töövõidud* population.

    No productivity statistics and no ranking of colleagues: this is a
    filterable list of what the department claims and what it has confirmed,
    and nothing here divides one number by another (Stage-2G brief 26, 40).
    """
    queryset = MatterWorkVictory.objects.visible_to(user).select_related(
        "matter", "matter__owner", "confirmed_by"
    )
    if status:
        if status not in WorkVictoryStatus.values:
            raise ValueError(f"Unknown work victory status {status!r}")
        queryset = queryset.filter(status=status)

    if year == UNKNOWN_PERIOD:
        # Resolved before any lookup: `period_date__year="teadmata"` is a
        # ValidationError, not an empty page.
        return queryset.filter(period_date__isnull=True)
    if year:
        # A victory with no period stays in *Teadmata periood*. It is never
        # inferred into the selected year (Stage-2G brief 27).
        return queryset.filter(period_date__year=int(year))
    return queryset


def work_victory_counts(user: Any) -> dict[str, int]:
    """How many of each state this reader may see, for the filter labels.

    One scoped count per status rather than one grouped query. The visibility
    predicate joins the collaborators many-to-many, and a grouped count over
    that fan-out counts join rows instead of records — a defect Stage 2E already
    paid for once.
    """
    queryset = MatterWorkVictory.objects.visible_to(user)
    return {value: queryset.filter(status=value).count() for value in WorkVictoryStatus.values}


def has_any_undated_victory(user: Any) -> bool:
    return MatterWorkVictory.objects.visible_to(user).filter(period_date__isnull=True).exists()
