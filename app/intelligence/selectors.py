"""Reads for the Matter page and the three generated department views.

Everything here starts from ``visible_to``. Authorization is applied to each
source **before** anything is filtered, grouped, counted or merged, so a
restricted Matter cannot surface through a total, a year option, a month heading
or an empty-state count (Stage-2G brief 31).
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from typing import Any

from django.db.models import QuerySet
from django.utils import timezone

from app.intelligence.enums import WorkVictoryStatus
from app.intelligence.models import (
    MatterEffectiveDate,
    MatterImportantDate,
    MatterWorkVictory,
)

#: The `?aasta=` sentinel for work victories nobody has dated. A word, because it
#: appears in a URL somebody pastes into a message, and resolved before any
#: lookup — a sentinel reaching a `period_date__year=` comparison is a
#: ValidationError, not an empty result (Stage-2E's fourth defect class).
UNKNOWN_PERIOD = "teadmata"

#: The internal state a record is in when it is a `Töövõit`.
#:
#: The department no longer keeps *kandidaat* and *kinnitatud töövõit* apart as
#: two things a reader chooses between: a colleague writes down a work victory
#: and it is one (app/intelligence/views.py::add_work_victory). What survives is
#: the internal review of a proposal a *machine* or an import made, which nobody
#: has yet decided on — so the stored state stays, and the ordinary product
#: surface simply shows the population that is actually a work victory.
#:
#: Named here rather than repeated at each call site so the rows, the year
#: options and the figure above them cannot drift into counting three different
#: populations.
VISIBLE_VICTORY_STATUS = WorkVictoryStatus.CONFIRMED


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


def work_victory_years(user: Any, *, status: str = "") -> list[int]:
    """Every year the offered population actually has a record in.

    ``status`` narrows it to the same rows the caller is about to list. The
    Töövõidud page offers a year only if choosing it shows something: a year
    built from rows the page does not display is a filter that empties the
    page it is attached to.
    """
    return sorted(
        {
            value
            for value in work_victories(user=user, status=status).values_list(
                "period_date__year", flat=True
            )
            if value is not None
        },
        reverse=True,
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
