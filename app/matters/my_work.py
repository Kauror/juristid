"""Minu töö — one chronological answer to "what do I do now".

The page has exactly one organising question, and it is *when do I need to care
about this again*. That has one answer per piece of work and it is a date,
whatever the mode: a ministry's reply expected on Thursday and an opinion due on
Thursday are both Thursday's problem. So there is one timeline, and the mode
chip beside each date says what the date means rather than which column it
belongs in.

Two consequences shape everything below.

**No dated work is exiled to a rail.** The earlier design put future WAIT and
MONITOR in separate blocks on the right, which made a lawyer read two lists and
merge them in their head — the merge this page exists to do for them. The rail
now holds only what genuinely has no place in time: a Matter that has gone quiet
because nobody set a next step, and an action nobody has dated.

**Nothing here is anybody else's work.** Every population is filtered to the
signed-in person before it is counted, and the responsibility rules differ by
source on purpose: a NextAction belongs to its ``responsible``, an
``Oluline tähtaeg`` to the Matter's current owner (app/matters/work_items.py).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, timedelta
from typing import Any

from django.utils import timezone

from app.matters import work_items as wi
from app.matters.activity import activity_of, annotate_last_activity
from app.matters.models import Entry, Matter
from app.workflow.enums import ESTONIAN_MONTHS

#: How far the *Hiljem* band reaches when nobody has chosen otherwise: the end
#: of the month two months out. On 24 August that is 31 October, which is the
#: window the design's range control names — far enough to plan an autumn
#: consultation round, near enough that the band stays readable.
DEFAULT_HORIZON_MONTHS = 2

#: How many months the range control offers beyond the default.
HORIZON_CHOICES = 4

#: The query parameter the range lives in. In the URL rather than the session,
#: so a refresh, the back button and a pasted link all show the same page.
HORIZON_PARAM = "kuni"
HORIZON_ALL = "koik"

#: Caps. The heading above each list states the honest total either way.
RAIL_LIMIT = 8
ENTRY_LIMIT = 6


def _month_end(year: int, month: int) -> date:
    if month == 12:
        return date(year, 12, 31)
    return date(year, month + 1, 1) - timedelta(days=1)


def _shift_months(anchor: date, months: int) -> tuple[int, int]:
    index = anchor.year * 12 + (anchor.month - 1) + months
    return index // 12, index % 12 + 1


@dataclass(frozen=True)
class HorizonOption:
    key: str
    label: str
    active: bool

    @property
    def query(self) -> str:
        return f"{HORIZON_PARAM}={self.key}"


@dataclass(frozen=True)
class Horizon:
    """How far ahead the timeline looks, and what the control calls it."""

    key: str
    label: str
    until: date | None

    @property
    def is_all(self) -> bool:
        return self.until is None


def default_horizon(today: date) -> Horizon:
    year, month = _shift_months(today, DEFAULT_HORIZON_MONTHS)
    return Horizon(
        key=f"{year}-{month:02d}",
        label=f"Kuni {ESTONIAN_MONTHS[month - 1]}",
        until=_month_end(year, month),
    )


def horizon_from(value: str | None, today: date) -> Horizon:
    """The window a query parameter asks for, or the default.

    Anything unrecognised falls back rather than raising or emptying the list: a
    mistyped URL should show the page, not a convincing empty timeline.
    """
    if value == HORIZON_ALL:
        return Horizon(key=HORIZON_ALL, label="Kõik tähtajad", until=None)
    if value:
        try:
            year, month = (int(part) for part in value.split("-", 1))
            if 1 <= month <= 12 and 2000 <= year <= 2100:
                return Horizon(
                    key=f"{year}-{month:02d}",
                    label=f"Kuni {ESTONIAN_MONTHS[month - 1]}",
                    until=_month_end(year, month),
                )
        except (ValueError, TypeError):
            pass
    return default_horizon(today)


def horizon_options(today: date, selected: Horizon) -> list[HorizonOption]:
    options: list[HorizonOption] = []
    for offset in range(HORIZON_CHOICES):
        year, month = _shift_months(today, offset)
        key = f"{year}-{month:02d}"
        options.append(
            HorizonOption(
                key=key,
                label=f"Kuni {ESTONIAN_MONTHS[month - 1]}",
                active=key == selected.key,
            )
        )
    options.append(HorizonOption(key=HORIZON_ALL, label="Kõik tähtajad", active=selected.is_all))
    return options


# ---------------------------------------------------------------------------
# The rail
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class QuietMatter:
    """An open Matter of mine that nobody has given a next step.

    ``silent_days`` is measured rather than described, because "44 p vaikust" is
    what makes the difference between a file that is merely undated and one that
    has been forgotten.
    """

    matter: Matter
    silent_days: int | None

    @property
    def silence(self) -> str:
        if self.silent_days is None:
            return "viimane tegevus teadmata"
        return f"viimane tegevus {self.silent_days} p tagasi"


def quiet_matters(user: Any, today: date, limit: int = RAIL_LIMIT) -> tuple[list[QuietMatter], int]:
    """My open Matters with no active NextAction, quietest first.

    ``annotate_last_activity`` rather than reading each Matter's timeline: a
    rail block showing "viimane tegevus 44 p tagasi" for eight rows must not
    fetch eight two-hundred-entry chronologies to do it (§27).
    """
    queryset = annotate_last_activity(
        wi.matters_without_action(user, owner=user).select_related("stage", "owner"), user
    )
    total = queryset.count()
    rows: list[QuietMatter] = []
    for matter in queryset.order_by("updated_at")[:limit]:
        # `activity_of` reads the annotations and never queries, which is what
        # makes eight rows eight reads of memory rather than eight timelines.
        activity = activity_of(matter)
        rows.append(
            QuietMatter(
                matter=matter,
                silent_days=(today - activity.occurred_on).days if activity else None,
            )
        )
    return rows, total


def undated_items(user: Any, limit: int = RAIL_LIMIT) -> tuple[list[wi.WorkItem], int]:
    """My open actions carrying no date at all — TEEN, OOTAN and JÄLGIN together.

    Not split into separate Ootan and Jälgin blocks. "No idea when" is one
    condition whatever the mode, and three short lists of two rows each is three
    headings for six facts.
    """
    queryset = wi.undated_actions(user, responsible=user)
    total = queryset.count()
    today = timezone.localdate()
    rows = [wi.action_item(action, today) for action in queryset.order_by("matter__title")[:limit]]
    return rows, total


def recent_entries(user: Any, limit: int = ENTRY_LIMIT) -> list[Entry]:
    """What I wrote lately. Collapsed by default: a way back, not a second timeline."""
    return list(
        Entry.objects.visible_to(user)
        .filter(author=user)
        .select_related("matter")
        .chronological()[:limit]
    )


# ---------------------------------------------------------------------------
# The whole page
# ---------------------------------------------------------------------------


@dataclass
class MyWork:
    today: date
    horizon: Horizon
    horizons: list[HorizonOption] = field(default_factory=list)
    bands: list[wi.WorkBand] = field(default_factory=list)
    quiet: list[QuietMatter] = field(default_factory=list)
    quiet_total: int = 0
    undated: list[wi.WorkItem] = field(default_factory=list)
    undated_total: int = 0
    entries: list[Entry] = field(default_factory=list)
    open_matters: int = 0
    overdue: int = 0
    week: int = 0
    beyond_horizon: int = 0

    @property
    def has_work(self) -> bool:
        return bool(self.bands)

    @property
    def total(self) -> int:
        """Every dated item the timeline is showing.

        Counted off the bands rather than recomputed, so the figure and the
        list it describes cannot disagree — and an item is in exactly one
        band, so nothing is counted twice.
        """
        return sum(band.count for band in self.bands)

    @property
    def last_entry(self) -> Entry | None:
        return self.entries[0] if self.entries else None


def build_my_work(user: Any, today: date | None = None, horizon: Horizon | None = None) -> MyWork:
    """Assemble the page from one read of the shared work model.

    The header's counts are taken from the same list the bands are built from,
    so the summary and the timeline cannot disagree — and ``N üle tähtaja``
    counts only what is genuinely late: my overdue DO deadlines and the
    ``Oluline tähtaeg`` on Matters I own. A passed review date is never in it.
    """
    today = today or timezone.localdate()
    horizon = horizon or default_horizon(today)

    # Unbounded, because the header has to count what falls beyond the window in
    # order to offer "Näita kaugemaid tähtaegu" honestly.
    everything = wi.work_items(user, today=today, responsible=user)
    week_end = wi.end_of_iso_week(today)
    bands = wi.band_items(everything, today, week_end=week_end, horizon=horizon.until)

    banded = {item.object_id for band in bands for item in band.items}
    beyond = sum(1 for item in everything if item.object_id not in banded)

    quiet, quiet_total = quiet_matters(user, today)
    undated, undated_total = undated_items(user)

    return MyWork(
        today=today,
        horizon=horizon,
        horizons=horizon_options(today, horizon),
        bands=bands,
        quiet=quiet,
        quiet_total=quiet_total,
        undated=undated,
        undated_total=undated_total,
        entries=recent_entries(user),
        open_matters=wi.open_matters(user).filter(owner=user).count(),
        overdue=len(wi.overdue_items(everything)),
        week=len(wi.week_items(everything, today, week_end)),
        beyond_horizon=beyond,
    )
