"""*Muudatused* — what changed in the department's policy work over a period.

The read model behind the department-wide change view (docs/adr/0159). It
answers one question — **which states moved** in the selected window — and it
is deliberately not a second answer to *Tehtud*:

* **Tehtud** counts *outputs*: an opinion that went out, a Teema that ended, a
  confirmed work victory, an entry written (`department_dashboard.build_digest`).
* **Muudatused** counts *state changes*: a Teema filed, a `Hetkeseis` moved, a
  file handed to somebody, a next step set, finished, called off or re-dated,
  a closed Teema reopened.

The outputs are not counted a second time here. :attr:`DepartmentChanges.outputs`
is the very `Digest` *Tehtud* builds for the same `Period`, so the two sections
print one number for one fact; a closure in particular stays *Tehtud*'s, because
an event count («someone pressed Sulge») and a state count («closed now, closed
in the window») differ the moment a Teema is closed and reopened in one week.

Three kinds of knowledge, kept apart
------------------------------------

**Authoritative event history.** Every category is a curated set of
`ChangeEvent` types written by the service that made the change, in the same
transaction. Only rows with an `actor` count: an importer, a cutover, a backfill
and the excel pilot write theirs with nobody behind them, and a batch that
materialised a register row is not a colleague changing the work. Those rows are
counted separately (:attr:`Coverage.system_events`) rather than dropped in
silence.

**A person's act, not the system's in their name** (ADR 0159 §2 amendment, D1).
Some step changes are written with the person as actor although nobody chose
them — the follow-up check a send schedules, a planned step made current, a
check put back on the plan. Their payload says so; they are counted aside
(:attr:`Coverage.automatic_steps`), never as «määras». And a closure that ended
N steps is one entry, «sulges teema», not N «tühistas»; the audit rows stay N.

**Snapshot-derived comparison.** Two figures — open Teemad and Teemad without a
Vastutaja — read off `OperationalMatterSnapshot` on the last photograph at or
before each end of the window. They are a *comparison of two states*, never a
count of changes, and the dates they are from are printed with them. Where no
photograph exists the comparison says so; nothing is reconstructed from today.

**Coverage limits.** What the record cannot know: changes before the first
recorded act, the history an imported Teema had before it arrived, and a
`Hetkeseis` whose start nobody recorded (docs/adr/0131 §3). Each is a number on
the page rather than a footnote, because «0 hetkeseisu muutust» over a fortnight
the system was not recording means something else entirely.

Authorization before arithmetic
-------------------------------

Every population begins inside the reader's boundary. Matters through
`matter_visibility_q` on one resolved scope; child events (a `NextAction`) through
:func:`app.audit.visibility.scope_change_events`, which asks the *child's* own
visibility — a step restricted below its Matter never reaches a count or a row.
Counts are computed over that population, never over a wider one filtered later.

Every query is bounded: one grouped count, a fixed handful of KPI, per-person,
coverage and snapshot aggregates, and one page of the change table — no query
per row, and none that grows with the data.

KPIs, filters and the change table
----------------------------------

Since docs/adr/0159 §7 (amendment of 2026-10-30) the page also carries KPI
cards (:func:`_kpis`, :func:`work_state_kpis`), a table by the Teema's
*current* owner (:func:`_people`) and one paginated, sortable change table
(:func:`_detail_table`) in place of the five per-category lists, all narrowed
by one filter set (:class:`ChangeFilters`) applied after authorization and
before any aggregate. «Enne» and «Pärast» come only from what the event
recorded (:func:`_before_after`).
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field, replace
from datetime import date, datetime
from typing import Any
from urllib.parse import urlencode

from django.core.paginator import Paginator
from django.db.models import Case, Count, IntegerField, Max, Min, Q, QuerySet, Value, When
from django.urls import reverse
from django.utils import timezone

from app.audit.enums import AuthMethod, ChangeEventType
from app.audit.models import ChangeEvent
from app.audit.provenance import actor_label
from app.audit.visibility import scope_change_events
from app.core.authorization import apply as apply_scope
from app.core.authorization import matter_visibility_q, scope_for_user
from app.core.dates import format_estonian_date, local_day_range, read_flexible_date
from app.matters import department_dashboard as dd
from app.matters.enums import MatterOrigin, RecordMode, StageEpisodeOrigin
from app.matters.models import Matter, MatterStageEpisode
from app.workflow.step_markers import automatic_step_q, closure_cancelled_q

#: The windows this view offers, in the order the control draws them. The
#: parser is Tehtud's (`department_dashboard.period_from`), so «7 päeva» starts
#: on the same day in both sections.
PERIOD_CHOICES: tuple[tuple[str, str], ...] = (
    (dd.PERIOD_7, "7 päeva"),
    (dd.PERIOD_14, "14 päeva"),
    (dd.PERIOD_30, "30 päeva"),
)

#: Tehtud's words for a refused range (`department_dashboard.custom_range_refusal`).
CUSTOM_RANGE_INCOMPLETE = dd.CUSTOM_RANGE_INCOMPLETE
CUSTOM_RANGE_UNREADABLE = dd.CUSTOM_RANGE_UNREADABLE
CUSTOM_RANGE_REVERSED = dd.CUSTOM_RANGE_REVERSED


@dataclass(frozen=True)
class Category:
    """One kind of state change, and the event types that are it."""

    key: str
    label: str
    #: The one-word badge a row carries, so a mixed list still reads by kind.
    badge: str
    #: Event type → the past-tense phrase a row reads as.
    verbs: tuple[tuple[str, str], ...]

    @property
    def event_types(self) -> tuple[str, ...]:
        return tuple(event_type for event_type, _ in self.verbs)


NEW_MATTERS = Category(
    key="uued",
    badge="Uus",
    label="Uued teemad",
    verbs=((ChangeEventType.MATTER_CREATED, "avas teema"),),
)
STAGE_CHANGES = Category(
    key="hetkeseis",
    badge="Hetkeseis",
    label="Hetkeseisu muutused",
    verbs=((ChangeEventType.MATTER_STAGE_CHANGED, "muutis hetkeseisu"),),
)
TRANSFERS = Category(
    key="vastutaja",
    badge="Vastutaja",
    label="Vastutaja muutused",
    verbs=((ChangeEventType.MATTER_ASSIGNED, "määras vastutaja"),),
)
NEXT_ACTIONS = Category(
    key="tegevused",
    badge="Tegevus",
    label="Järgmised tegevused",
    verbs=(
        (ChangeEventType.NEXT_ACTION_SET, "määras järgmise tegevuse"),
        (ChangeEventType.NEXT_ACTION_COMPLETED, "lõpetas järgmise tegevuse"),
        (ChangeEventType.NEXT_ACTION_CANCELLED, "tühistas järgmise tegevuse"),
        (ChangeEventType.NEXT_ACTION_RESCHEDULED, "muutis tegevuse kuupäeva"),
    ),
)
REOPENINGS = Category(
    key="taasavatud",
    badge="Taasavatud",
    label="Taasavatud teemad",
    verbs=((ChangeEventType.MATTER_REOPENED, "avas teema uuesti"),),
)

#: Every category, in page order. `NEXT_ACTION_REVIEWED` is deliberately absent:
#: «Vaatasin üle» confirms a step without changing it, and a state-change view
#: that counted it would report movement where there was none.
CATEGORIES: tuple[Category, ...] = (
    NEW_MATTERS,
    STAGE_CHANGES,
    TRANSFERS,
    NEXT_ACTIONS,
    REOPENINGS,
)

#: Every event type this view reads. A type reaches the page only by being
#: named in a category above — the same whitelist discipline as the change log.
CURATED_EVENT_TYPES: frozenset[str] = frozenset(
    event_type for category in CATEGORIES for event_type in category.event_types
)

_VERBS: dict[str, str] = {
    event_type: verb for category in CATEGORIES for event_type, verb in category.verbs
}
_BADGES: dict[str, str] = {
    event_type: category.badge for category in CATEGORIES for event_type in category.event_types
}

#: A person's `MATTER_CLOSED` is read as the one closure row (ADR 0159 §7,
#: review of 2026-10-30): one closure is one change whether or not it ended
#: open steps. It is not a type of its own in any category's breakdown — the
#: closure row is «sulges teema» in «Järgmised tegevused», as D1 drew it.
CLOSURE_EVENT = ChangeEventType.MATTER_CLOSED

_CATEGORY_OF: dict[str, Category] = {
    event_type: category for category in CATEGORIES for event_type in category.event_types
}
_CATEGORY_OF[CLOSURE_EVENT] = NEXT_ACTIONS


def _category_types(category: Category) -> tuple[str, ...]:
    """The event types a category's rows are read from, the closing event included."""
    if category is NEXT_ACTIONS:
        return (*category.event_types, CLOSURE_EVENT)
    return category.event_types


CATEGORY_KEYS: frozenset[str] = frozenset(category.key for category in CATEGORIES)

#: What a reader is shown where a value was not recorded.
UNKNOWN = "teadmata"
#: The actor column of a row nobody's act wrote (never a counted act).
SYSTEM_ACTOR = "süsteem"


@dataclass(frozen=True)
class ChangeRow:
    """One change, as a reader scans it: when, who, what, on which Teema."""

    at: datetime
    event_type: str
    badge: str
    verb: str
    detail: str
    actor_name: str
    initials: str
    matter_title: str
    url: str
    #: How the row's actor arrived (`AuthMethod`); "" on a row written before
    #: that was recorded (docs/adr/0180).
    auth_method: str = ""
    #: The category the row belongs to (`Category.key`) and its label.
    category_key: str = ""
    category_label: str = ""
    #: «Enne» and «Pärast», only as the event recorded them (`_before_after`):
    #: «teadmata» where nothing recorded the earlier value, "" where nothing
    #: recorded the later one. Never reconstructed from today's data.
    before: str = ""
    after: str = ""
    #: The Teema's reference and its *current* Vastutaja — today's fact, which
    #: the table says in its heading («Praegune vastutaja»).
    matter_reference: str = ""
    owner_name: str = ""

    @property
    def when(self) -> date:
        return timezone.localtime(self.at).date()

    @property
    def actor_label(self) -> str:
        """The name, and — for a persona chosen behind the shared gate — that it was chosen."""
        return actor_label(self.actor_name, self.auth_method)

    @property
    def actor_display(self) -> str:
        """The «Tegija» cell: the person, or «süsteem» for a row with nobody behind it."""
        return self.actor_label or SYSTEM_ACTOR

    @property
    def change(self) -> str:
        """The «Muudatus» cell: the act, and for a closure how many steps it ended."""
        if self.event_type == CLOSURE_ROW and self.detail:
            return f"{self.verb}: {self.detail}"
        return self.verb


@dataclass
class CategoryResult:
    """One category's count over the filtered population (every filter but «Liik»).

    The rows are no longer listed per category: they are the one paginated
    detail table (:class:`DetailTable`), which «Liik» narrows to one category.
    """

    category: Category
    count: int = 0
    #: Per event type, for a category that holds several (next actions).
    breakdown: list[tuple[str, int]] = field(default_factory=list)
    #: Whether «Liik» currently selects this category, and the URL that does.
    active: bool = False
    query: str = ""

    @property
    def key(self) -> str:
        return self.category.key

    @property
    def label(self) -> str:
        return self.category.label


@dataclass(frozen=True)
class SnapshotFigure:
    """One state, photographed at both ends of the window."""

    label: str
    start: int | None
    end: int | None

    @property
    def delta(self) -> int | None:
        if self.start is None or self.end is None:
            return None
        return self.end - self.start


@dataclass
class SnapshotComparison:
    """Two photographs, and what they say — or why there is nothing to say."""

    start_date: date | None = None
    end_date: date | None = None
    figures: list[SnapshotFigure] = field(default_factory=list)
    #: The window's last day, to say which side of it the end photograph is on.
    period_end: date | None = None
    #: «Tähtaja ületanud» and «Vajab sekkumist» on the same two photographs
    #: (D4). ``None`` at a boundary whose rows did not record the flag — every
    #: row captured before 2026-10-30, or a reader whose scope may not read it.
    overdue: SnapshotFigure | None = None
    attention: SnapshotFigure | None = None

    @property
    def available(self) -> bool:
        return self.start_date is not None and self.end_date is not None

    @property
    def end_inside_window(self) -> bool:
        """The end photograph is from inside the window (its last day's morning
        or earlier), so the last day's changes are not in it."""
        return (
            self.end_date is not None
            and self.period_end is not None
            and self.end_date <= self.period_end
        )


@dataclass
class Coverage:
    """What this window's record cannot know, as numbers."""

    #: The earliest act a person recorded, over the reader's population. None
    #: when nothing has been recorded at all.
    recorded_since: date | None = None
    #: Curated events in the window with no person behind them — imports,
    #: cutovers, backfills. Not counted above.
    system_events: int = 0
    #: Step changes in the window the system made on a person's behalf — a
    #: follow-up check scheduled by a send, a planned step made current, a check
    #: put back on the plan or re-dated by a corrected send date. Their audit
    #: rows name the person; they are not counted as anyone's act (D1).
    automatic_steps: int = 0
    #: Teemad that arrived by import in the window. Not «new»: they existed
    #: before they were copied in.
    imported_matters: int = 0
    #: Open Teemad whose current `Hetkeseis` began at a moment nobody recorded.
    stage_start_unknown: int = 0

    def starts_after(self, period: dd.Period) -> bool:
        """Whether the window reaches back before anything was recorded."""
        return self.recorded_since is None or self.recorded_since > period.start

    def covers_none_of(self, period: dd.Period) -> bool:
        """Whether the whole window ends before anything was recorded.

        Then a count of 0 would be a claim the record cannot make: nobody was
        recording, so the window's changes are unknown, not absent.
        """
        return self.recorded_since is None or self.recorded_since > period.end


# ---------------------------------------------------------------------------
# Filters (docs/adr/0159, amendment of 2026-10-30)
# ---------------------------------------------------------------------------

OWNER_PARAM = "vastutaja"
#: `?vastutaja=puudub`: Teemad nobody carries — the register's own word.
OWNER_NONE = "puudub"
CATEGORY_PARAM = "liik"
SEARCH_PARAM = "otsi"
SORT_PARAM = "sort"
PEOPLE_SORT_PARAM = "isikud"
PAGE_PARAM = "leht"

#: Detail rows per page.
PAGE_SIZE = 50
#: A search longer than this is cut, not refused.
SEARCH_MAX = 200

#: The detail table's sortable columns → their ordering, ascending.
DETAIL_SORTS: dict[str, tuple[str, ...]] = {
    "aeg": ("occurred_at", "created_at", "pk"),
    "teema": ("matter__title", "-occurred_at", "pk"),
    "liik": ("category_order", "-occurred_at", "pk"),
}
DEFAULT_DETAIL_SORT = "-aeg"

#: The per-person table's sortable columns.
PEOPLE_SORTS: tuple[str, ...] = ("nimi", "teemad", "muudatused", "uued", "arvamused", "lopetatud")
DEFAULT_PEOPLE_SORT = "-teemad"


def _read_sort(raw: str, keys: Any, default: str) -> str:
    value = (raw or "").strip()
    return value if value.lstrip("-") in keys and value.count("-") <= 1 else default


@dataclass(frozen=True)
class ChangeFilters:
    """What the reader narrowed the page to, read from GET and never trusted.

    Every value that cannot be read falls back to «no filter» (an unknown
    Vastutaja, an unknown «Liik», a non-number page) rather than to an error:
    a hand-edited link shows the whole page, never a 500.
    """

    #: "" (anyone), :data:`OWNER_NONE`, or a Vastutaja's primary key as text.
    owner: str = ""
    category: str = ""
    search: str = ""
    sort: str = DEFAULT_DETAIL_SORT
    people_sort: str = DEFAULT_PEOPLE_SORT
    page: str = "1"

    @property
    def narrows_matters(self) -> bool:
        """Whether a filter narrows the *Teemad* (Vastutaja, search) — which
        the outputs and the work-state figures then follow."""
        return bool(self.owner or self.search)

    @property
    def active(self) -> bool:
        return bool(self.owner or self.category or self.search)

    def params(self, period: dd.Period, **overrides: str) -> dict[str, str]:
        """This page's URL parameters, so a sort or a page keeps every filter."""
        values = {
            **_period_params(period),
            OWNER_PARAM: self.owner,
            CATEGORY_PARAM: self.category,
            SEARCH_PARAM: self.search,
            SORT_PARAM: "" if self.sort == DEFAULT_DETAIL_SORT else self.sort,
            PEOPLE_SORT_PARAM: "" if self.people_sort == DEFAULT_PEOPLE_SORT else self.people_sort,
        }
        values.update(overrides)
        return {key: value for key, value in values.items() if value}


_PERIOD_KEYS = frozenset({dd.PERIOD_PARAM, dd.PERIOD_START_PARAM, dd.PERIOD_END_PARAM})


def _period_params(period: dd.Period) -> dict[str, str]:
    if period.key in dict(dd.PERIOD_NAMES):
        return {dd.PERIOD_PARAM: period.key}
    return _range_params(period)


def read_filters(params: Any, owner_keys: frozenset[str]) -> ChangeFilters:
    """The filters from GET, each one checked against what it may be."""
    owner = (params.get(OWNER_PARAM) or "").strip()
    if owner != OWNER_NONE and owner not in owner_keys:
        owner = ""
    category = (params.get(CATEGORY_PARAM) or "").strip()
    if category not in CATEGORY_KEYS:
        category = ""
    search = " ".join((params.get(SEARCH_PARAM) or "").split())[:SEARCH_MAX]
    return ChangeFilters(
        owner=owner,
        category=category,
        search=search,
        sort=_read_sort(params.get(SORT_PARAM), DETAIL_SORTS, DEFAULT_DETAIL_SORT),
        people_sort=_read_sort(params.get(PEOPLE_SORT_PARAM), PEOPLE_SORTS, DEFAULT_PEOPLE_SORT),
        page=(params.get(PAGE_PARAM) or "1").strip(),
    )


@dataclass(frozen=True)
class OwnerChoice:
    value: str
    label: str
    selected: bool


@dataclass(frozen=True)
class SortHeading:
    """One sortable column heading: its link and its `aria-sort`."""

    key: str
    label: str
    query: str
    direction: str  # "ascending" / "descending" / "none"


def _sort_headings(
    filters: ChangeFilters,
    period: dd.Period,
    columns: tuple[tuple[str, str], ...],
    *,
    param: str,
    current: str,
    descending_first: frozenset[str],
    keep_page: bool,
) -> dict[str, SortHeading]:
    headings = {}
    active_key = current.lstrip("-")
    for key, label in columns:
        if key == active_key:
            direction = "descending" if current.startswith("-") else "ascending"
            target = key if current.startswith("-") else f"-{key}"
        else:
            direction = "none"
            target = f"-{key}" if key in descending_first else key
        overrides = {param: target}
        if not keep_page:
            overrides[PAGE_PARAM] = ""
        headings[key] = SortHeading(
            key=key,
            label=label,
            query=urlencode(filters.params(period, **overrides)),
            direction=direction,
        )
    return headings


# ---------------------------------------------------------------------------
# KPIs, the per-person table, the detail table
# ---------------------------------------------------------------------------

#: The three things a figure can count, said beside its number so a reader
#: never adds topics to events.
UNIT_TOPICS = "teemat"
UNIT_EVENTS = "muudatust"
UNIT_OPINIONS = "arvamust"

#: Why a past boundary of a work-state figure is unknown.
NOT_MEASURED = "ei mõõdetud"
WORK_STATE_UNMEASURED = (
    "Perioodi alguse või lõpu hetktõmmis ei salvestanud seda seisu, seega muutust ei mõõdetud."
)
SNAPSHOT_UNAVAILABLE = "Perioodi algusest ja lõpust ei ole kahte erinevat hetktõmmist."
SNAPSHOT_DEPARTMENT_WIDE = "Hetktõmmiste võrdlus on kogu osakonna kohta; filtrid seda ei kitsenda."
#: The end photograph is the window's last day's morning: that day's changes
#: are not in it (the next morning's photograph does not exist yet).
SNAPSHOT_END_INSIDE = (
    "Lõpuseis on perioodi viimase päeva hommikust; selle päeva muudatused ei ole sees."
)


def _snapshot_note(snapshots: SnapshotComparison, narrowed: bool) -> str:
    notes = []
    if snapshots.end_inside_window:
        notes.append(SNAPSHOT_END_INSIDE)
    if narrowed:
        notes.append(SNAPSHOT_DEPARTMENT_WIDE)
    return " ".join(notes)


@dataclass(frozen=True)
class Kpi:
    """One card at the top of the page.

    ``value`` is a count over the window (or, for a work-state card, today's
    value). ``start`` / ``end`` are the two photographs' figures for a card
    that compares states; ``compared`` says the card is such a card, so a
    missing photograph reads «ei mõõdetud» rather than 0.
    """

    key: str
    label: str
    unit: str
    value: int | None = None
    compared: bool = False
    start: int | None = None
    end: int | None = None
    #: What the card's number is — always printed under it.
    definition: str = ""
    #: Why the comparison is missing, or whose population it is.
    note: str = ""
    #: Counted from the change record, so «—» in a window nobody was recording.
    from_events: bool = False

    @property
    def delta(self) -> int | None:
        if self.start is None or self.end is None:
            return None
        return self.end - self.start

    @property
    def measured(self) -> bool:
        return self.delta is not None


@dataclass(frozen=True)
class PersonRow:
    """One Vastutaja's line: the current owner of the Teemad, never the actor."""

    key: str
    name: str
    inactive: bool = False
    unowned: bool = False
    matters: int = 0
    events: int = 0
    new: int = 0
    sent: int = 0
    closed: int = 0
    #: This page narrowed to the person (`?vastutaja=`).
    query: str = ""

    @property
    def label(self) -> str:
        return f"{self.name} (mitteaktiivne)" if self.inactive else self.name


@dataclass
class DetailTable:
    """One page of the change rows, newest first unless sorted otherwise."""

    rows: list[ChangeRow] = field(default_factory=list)
    #: Django's `Page`, for the pagination component.
    page: Any = None
    count: int = 0
    query_string: str = ""
    headings: dict[str, SortHeading] = field(default_factory=dict)


@dataclass
class DepartmentChanges:
    period: dd.Period
    #: A refused custom range, in words; the window then fell back to the default.
    period_error: str = ""
    categories: list[CategoryResult] = field(default_factory=list)
    coverage: Coverage = field(default_factory=Coverage)
    snapshots: SnapshotComparison = field(default_factory=SnapshotComparison)
    #: Tehtud's own digest for the same window — the outputs, counted once.
    outputs: dd.Digest | None = None
    filters: ChangeFilters = field(default_factory=ChangeFilters)
    owner_choices: list[OwnerChoice] = field(default_factory=list)
    kpis: list[Kpi] = field(default_factory=list)
    people: list[PersonRow] = field(default_factory=list)
    people_headings: dict[str, SortHeading] = field(default_factory=dict)
    #: The per-person table's totals — equal to the KPIs by construction.
    people_total: PersonRow | None = None
    detail: DetailTable = field(default_factory=DetailTable)

    @property
    def total(self) -> int:
        return sum(result.count for result in self.categories)

    @property
    def is_empty(self) -> bool:
        return self.total == 0

    @property
    def unrecorded(self) -> bool:
        """No part of the window was recorded: its counts are unknown («—»)."""
        return self.coverage.covers_none_of(self.period)

    @property
    def partial_coverage(self) -> bool:
        """The window starts before the first recorded change and ends after it."""
        return self.coverage.starts_after(self.period) and not self.unrecorded

    @property
    def off_chip(self) -> bool:
        """A named window this page draws no chip for — «Viimased 13 nädalat»,
        «Aasta» — so the period control names it as the selected one."""
        return not (
            self.period.is_custom
            or self.period_error
            or self.period.key in {key for key, _ in PERIOD_CHOICES}
        )

    @property
    def row_date_format(self) -> str:
        """Row dates carry the year when the window is not this year's alone."""
        this_year = timezone.localdate().year
        same = self.period.start.year == self.period.end.year == this_year
        return "d.m" if same else "d.m.Y"

    @property
    def period_options(self) -> list[dd.PeriodOption]:
        """The three named windows. None is selected while a typed range was
        refused, so the page never claims the reader chose the fallback.

        A window chip keeps the filters (Vastutaja, Liik, search, sorts) and
        starts again from the first page."""
        return [
            dd.PeriodOption(
                key=key,
                label=label,
                active=self.period.key == key and not self.period_error,
                query=urlencode(
                    {
                        dd.PERIOD_PARAM: key,
                        **{
                            name: value
                            for name, value in self.filters.params(self.period).items()
                            if name not in _PERIOD_KEYS
                        },
                    }
                ),
            )
            for key, label in PERIOD_CHOICES
        ]

    @property
    def clear_filters_query(self) -> str:
        """«Tühjenda filtrid»: the same window, nothing else."""
        return urlencode(_period_params(self.period))

    @property
    def all_categories_query(self) -> str:
        return urlencode(self.filters.params(self.period, **{CATEGORY_PARAM: "", PAGE_PARAM: ""}))

    @property
    def tehtud_url(self) -> str:
        """*Tehtud* on the identical window, where the outputs are listed."""
        return tehtud_url(self.period)


# ---------------------------------------------------------------------------
# The window
# ---------------------------------------------------------------------------


def changes_period(params: Any, today: date | None = None) -> tuple[dd.Period, str]:
    """The window the URL asks for, and why a custom range was refused if it was.

    Named windows and a valid range go through Tehtud's parser unchanged. An
    unknown key is the default, silently, as `kind_from` treats an unknown
    kind. A *custom* range that cannot be read is different: the reader typed
    it, so the page says what was wrong instead of quietly showing a week.
    """
    today = today or timezone.localdate()
    key = (params.get(dd.PERIOD_PARAM) or dd.PERIOD_7).strip()
    if key == dd.PERIOD_CUSTOM:
        start = read_flexible_date(params.get(dd.PERIOD_START_PARAM))
        end = read_flexible_date(params.get(dd.PERIOD_END_PARAM))
        error = dd.custom_range_refusal(start, end)
        if error or start.value is None or end.value is None:
            return dd.period_from({}, today), error
        return dd.Period(dd.PERIOD_CUSTOM, start.value, end.value), ""
    # Every window Tehtud's parser names, not only the three chips drawn here:
    # the same `periood` must give the same dates on both pages (ADR 0159 §5).
    if key not in dict(dd.PERIOD_NAMES):
        key = dd.PERIOD_7
    return dd.period_from({dd.PERIOD_PARAM: key}, today), ""


def _range_params(period: dd.Period) -> dict[str, str]:
    return {
        dd.PERIOD_PARAM: dd.PERIOD_CUSTOM,
        dd.PERIOD_START_PARAM: format_estonian_date(period.start),
        dd.PERIOD_END_PARAM: format_estonian_date(period.end),
    }


def changes_url(period: dd.Period) -> str:
    """This view on Tehtud's window — the «Muudatused →» link in its heading.

    A named window — 7 / 14 / 30 päeva, «Viimased 13 nädalat», «Aasta» —
    travels as its key, which this view reads with Tehtud's parser; a custom
    range as its two dates. Either way the link lands on the section's window.
    """
    named = dict(dd.PERIOD_NAMES)
    params = {dd.PERIOD_PARAM: period.key} if period.key in named else _range_params(period)
    return f"{reverse('matters:department_changes')}?{urlencode(params)}"


def tehtud_url(period: dd.Period) -> str:
    """Tehtud on this view's window, its section addressed by `#tehtud`."""
    params = dd._page_params(
        period_key=period.key,
        kind=dd.KIND_ALL,
        start=period.start,
        end=period.end,
    )
    if period.key == dd.PERIOD_14:
        # Tehtud's parser reads the fortnight; its control just does not offer it.
        params[dd.PERIOD_PARAM] = dd.PERIOD_14
    return f"{reverse('matters:department')}?{urlencode(params)}#tehtud"


# ---------------------------------------------------------------------------
# Populations
# ---------------------------------------------------------------------------


def _visible_full_matters(user: Any) -> QuerySet[Matter]:
    """The reader's current-work Matters — the population every count starts in.

    FULL only: an archive row is the register's past, not the department's
    work, and Tehtud draws the same line for its closures.
    """
    scope = scope_for_user(user)
    return apply_scope(Matter.objects.all(), matter_visibility_q(scope)).filter(
        record_mode=RecordMode.FULL
    )


def _scoped_events(
    user: Any, matters: QuerySet[Matter], *, with_closures: bool = False
) -> QuerySet[ChangeEvent]:
    """Curated events on visible Matters whose own subject is visible too.

    Not yet narrowed to a window or to people: coverage reads the same
    population for its earliest act and its system rows, so the boundary is
    written once.
    """
    types = CURATED_EVENT_TYPES | {CLOSURE_EVENT} if with_closures else CURATED_EVENT_TYPES
    return scope_change_events(
        ChangeEvent.objects.filter(
            event_type__in=types,
            matter__in=matters.values("pk"),
        ),
        user,
    )


def _in_window(events: QuerySet[ChangeEvent], period: dd.Period) -> QuerySet[ChangeEvent]:
    # On a Europe/Tallinn day from the first to the last, both included: a
    # change at 00:30 on the first day is inside a window that starts that day,
    # one at 23:59 on the last day is inside one that ends it. A half-open range
    # on the timestamp (`local_day_range`), which the `occurred_at` index can
    # answer, rather than a cast of every audit row to a local date (D-01).
    return events.filter(local_day_range("occurred_at", period.start, period.end))


def matter_filter_q(filters: ChangeFilters, prefix: str = "") -> Q:
    """Vastutaja and search as one condition on a Teema reached by ``prefix``.

    `otsi` matches the title (case-insensitive) or, when it reads as one, the
    familiar ``YYYY_N`` reference. Written once and applied to *whichever*
    population a figure counts — the visible Teemad, a sent opinion's Teema —
    so a filter only ever narrows that population and never changes its kind
    (the outputs keep Tehtud's own definition, archive Teemad included).
    """
    condition = Q()
    if filters.owner == OWNER_NONE:
        condition &= Q(**{f"{prefix}owner__isnull": True})
    elif filters.owner:
        condition &= Q(**{f"{prefix}owner_id": uuid.UUID(filters.owner)})
    if filters.search:
        found = Q(**{f"{prefix}title__icontains": filters.search})
        reference = Matter.parse_reference(filters.search)
        if reference is not None:
            found |= Q(
                **{
                    f"{prefix}reference_year": reference[0],
                    f"{prefix}reference_number": reference[1],
                }
            )
        condition &= found
    return condition


def _filtered_matters(matters: QuerySet[Matter], filters: ChangeFilters) -> QuerySet[Matter]:
    """The visible Teemad, narrowed by Vastutaja and search — never widened.

    Applied to the already-authorized population, so a search can only ever
    find a Teema the reader may see.
    """
    return matters.filter(matter_filter_q(filters)) if filters.narrows_matters else matters


def _owner_choices(matters: QuerySet[Matter]) -> list[Any]:
    """Who the Vastutaja filter offers: the department's active workers, and
    anybody else who currently owns a Teema the reader may see (an inactive
    colleague's files are still somebody's). One query."""
    from django.contrib.auth import get_user_model

    from app.accounts.selectors import department_workers

    return list(
        get_user_model()
        .objects.filter(
            Q(pk__in=department_workers().values("pk"))
            | Q(pk__in=matters.exclude(owner__isnull=True).values("owner"))
        )
        .order_by("display_name")
        .only("pk", "display_name", "is_active")
    )


#: The events that make a Matter stop being current work. A step ended by a
#: closure belongs to the first of these written on its Matter at or after it
#: (the closure writes its steps' cancellations first, then itself).
CLOSING_EVENT_TYPES: tuple[str, ...] = (
    ChangeEventType.MATTER_CLOSED,
    ChangeEventType.MATTER_HISTORICAL_CUTOVER_CLOSED,
    ChangeEventType.MATTER_REGISTER_CUTOVER_RETIRED,
)

#: The pseudo event type of a closure's one row in «Järgmised tegevused».
CLOSURE_ROW = "NEXT_ACTIONS_ENDED_BY_CLOSURE"
CLOSURE_VERB = "sulges teema"


def _person_acts(events: QuerySet[ChangeEvent]) -> QuerySet[ChangeEvent]:
    """A colleague's change, and for a new Teema one filed here, not imported.

    A step change the system made on the person's behalf
    (`step_markers.automatic_step_q`) is not theirs, though the row names them
    as its actor.
    """
    return (
        events.filter(actor__isnull=False)
        .exclude(
            Q(event_type=ChangeEventType.MATTER_CREATED) & ~Q(matter__origin=MatterOrigin.NATIVE)
        )
        .exclude(automatic_step_q())
    )


def _detail(event: ChangeEvent) -> str:
    """The before → after a row prints, from the event's own scalar payload.

    Only labels and names the service wrote for exactly this; never free text,
    never a step's own words.
    """
    payload = event.payload or {}
    if event.event_type == ChangeEventType.MATTER_STAGE_CHANGED:
        before = payload.get("from_label") or "määramata"
        after = payload.get("to_label") or "määramata"
        return f"{before} → {after}"
    if event.event_type == ChangeEventType.MATTER_ASSIGNED:
        before = payload.get("from_name")
        after = payload.get("to_name") or "vastutajata"
        return f"{before} → {after}" if before else f"→ {after}"
    return ""


def _iso_day(value: Any, missing: str) -> str:
    """An ISO date the event recorded, in Estonian; ``missing`` for a recorded none."""
    if not value:
        return missing
    try:
        return format_estonian_date(date.fromisoformat(str(value)))
    except ValueError:
        return UNKNOWN


def _before_after(event: ChangeEvent) -> tuple[str, str]:
    """«Enne» and «Pärast», only from what the event itself recorded.

    The evidence rule (docs/adr/0159, amendment of 2026-10-30): a value is
    printed only when the writing service put it in the payload for exactly
    this — the stage labels, the Vastutaja names, a re-dating's two days, the
    day a new step was set for. Where nothing recorded the earlier value it is
    «teadmata»; where nothing recorded the later one the cell is empty. Nothing
    is reconstructed from today's data, and no free text is read.

    A key the writer recorded as empty is a recorded value: no stage
    («määramata»), nobody («vastutajata»), no date («kuupäevata»). A new Teema
    had no earlier state at all, so its «Enne» is «—», not «teadmata».
    """
    payload = event.payload or {}
    kind = event.event_type
    if kind == ChangeEventType.MATTER_STAGE_CHANGED:
        before = (payload.get("from_label") or "määramata") if "from_label" in payload else UNKNOWN
        after = (payload.get("to_label") or "määramata") if "to_label" in payload else ""
        return before, after
    if kind == ChangeEventType.MATTER_ASSIGNED:
        before = (payload.get("from_name") or "vastutajata") if "from_name" in payload else UNKNOWN
        after = (payload.get("to_name") or "vastutajata") if "to_name" in payload else ""
        return before, after
    if kind == ChangeEventType.NEXT_ACTION_RESCHEDULED:
        before = _iso_day(payload.get("from"), "kuupäevata") if "from" in payload else UNKNOWN
        after = _iso_day(payload.get("to"), "kuupäevata") if "to" in payload else ""
        return before, after
    if kind == ChangeEventType.NEXT_ACTION_SET:
        after = (
            _iso_day(payload.get("target_date"), "kuupäevata") if "target_date" in payload else ""
        )
        return UNKNOWN, after
    if kind == ChangeEventType.MATTER_CREATED:
        return "—", ""
    return UNKNOWN, ""


def _row(event: ChangeEvent) -> ChangeRow:
    actor = event.actor
    matter = event.matter
    category = _CATEGORY_OF.get(event.event_type)
    before, after = _before_after(event)
    owner = matter.owner if matter is not None else None
    return ChangeRow(
        at=event.occurred_at,
        event_type=event.event_type,
        badge=_BADGES.get(event.event_type, NEXT_ACTIONS.badge),
        verb=_VERBS.get(event.event_type, CLOSURE_VERB),
        detail=_detail(event),
        actor_name=actor.display_name if actor is not None else "",
        initials=actor.initials if actor is not None else "··",
        matter_title=matter.title if matter is not None else "",
        url=reverse("matters:matter_detail", kwargs={"pk": matter.pk}) if matter else "",
        auth_method=event.auth_method,
        category_key=category.key if category is not None else "",
        category_label=category.label if category is not None else "",
        before=before,
        after=after,
        matter_reference=matter.display_reference if matter is not None else "",
        owner_name=owner.display_name if owner is not None else "",
    )


def _closure_groups(
    closure_acts: QuerySet[ChangeEvent], closing_acts: QuerySet[ChangeEvent]
) -> dict[Any, dict[str, Any]]:
    """One group per closure, however many steps it ended — none included —
    keyed by the group's *representative* event.

    **One closure is one change** (D1, and the review of 2026-10-30). A
    person's `MATTER_CLOSED` in the window is the closure's row itself, and the
    step cancellations it caused fold into it («lõppes N tegevust»; a closure
    with no open step is the same one row with N = 0). Which closure a
    cancellation belongs to:

    1. **the closure that names it** — `MATTER_CLOSED.payload.ended.actions`
       lists the ids of the steps that closure ended (docs/adr/0152 §7);
    2. otherwise the first closing event on its Matter at or after it (a
       closure writes its steps' cancellations first, then itself) — the rule
       for closures recorded before that list existed, and for cutovers;
    3. otherwise it stands alone rather than being merged with a stranger.

    A group whose closing event is not a person's act in this window (a
    cutover, an unrecorded closer) is represented by its latest cancellation,
    ties broken by primary key so a page is the same page every time. The
    representative makes the grouping something the database can sort and
    paginate. Bounded: the cancellations, the closing events on their Matters,
    and the window's person-made closures — three queries.
    """
    cancels = list(
        closure_acts.order_by().values(
            "pk", "matter", "actor", "object_id", "occurred_at", "auth_method"
        )
    )
    own_closings = {
        closing["pk"]: closing
        for closing in closing_acts.order_by().values(
            "pk", "matter", "actor", "occurred_at", "auth_method"
        )
    }
    named: dict[str, Any] = {}
    by_matter: dict[Any, list[Any]] = {}
    if cancels:
        for closing in (
            ChangeEvent.objects.filter(
                matter__in={cancel["matter"] for cancel in cancels},
                event_type__in=CLOSING_EVENT_TYPES,
            )
            .order_by("occurred_at", "created_at", "pk")
            .values("pk", "matter", "occurred_at", "payload")
        ):
            by_matter.setdefault(closing["matter"], []).append(closing)
            ended = (closing["payload"] or {}).get("ended")
            actions = ended.get("actions") if isinstance(ended, dict) else None
            for action_id in actions if isinstance(actions, list) else ():
                named.setdefault(str(action_id), closing["pk"])

    def closure_of(cancel: Any) -> Any:
        if cancel["object_id"] is not None and str(cancel["object_id"]) in named:
            return named[str(cancel["object_id"])]
        for closing in by_matter.get(cancel["matter"], ()):
            if closing["occurred_at"] >= cancel["occurred_at"]:
                return closing["pk"]
        return cancel["pk"]

    def order(event: Any) -> tuple[Any, str]:
        return (event["occurred_at"], str(event["pk"]))

    grouped: dict[Any, dict[str, Any]] = {}
    for pk, own in own_closings.items():
        grouped[pk] = {
            "n": 0,
            "at": own["occurred_at"],
            "rep": pk,
            "auth_method": own["auth_method"],
        }
    for cancel in cancels:
        closure = closure_of(cancel)
        if closure in own_closings:
            grouped[closure]["n"] += 1
            continue
        key = (closure, cancel["matter"], cancel["actor"])
        group = grouped.setdefault(
            key, {"n": 0, "at": None, "rep": None, "auth_method": "", "_order": None}
        )
        group["n"] += 1
        # One closure is one request, so its cancellations share a method; a
        # shared-gate one anywhere in the group is enough to say so.
        if cancel["auth_method"] == AuthMethod.SHARED_GATE or not group["auth_method"]:
            group["auth_method"] = cancel["auth_method"]
        if group["_order"] is None or order(cancel) > group["_order"]:
            group["_order"] = order(cancel)
            group["at"] = cancel["occurred_at"]
            group["rep"] = cancel["pk"]
    return {group["rep"]: group for group in grouped.values()}


def _closure_row(event: ChangeEvent, group: dict[str, Any]) -> ChangeRow:
    """A closure's one row: «sulges teema», and «lõppes N tegevust» when it ended any."""
    base = _row(event)
    ended = group["n"]
    return replace(
        base,
        at=group["at"],
        event_type=CLOSURE_ROW,
        badge=NEXT_ACTIONS.badge,
        verb=CLOSURE_VERB,
        detail=f"lõppes {ended} {'tegevus' if ended == 1 else 'tegevust'}" if ended else "",
        auth_method=group["auth_method"],
        before=UNKNOWN,
        after="",
    )


def _categories(
    plain: QuerySet[ChangeEvent],
    closures: dict[Any, dict[str, Any]],
    filters: ChangeFilters,
    period: dd.Period,
) -> list[CategoryResult]:
    """Each category's count — one grouped query; the closures are already read."""
    counts = {
        row["event_type"]: row["n"]
        for row in plain.order_by().values("event_type").annotate(n=Count("pk"))
    }
    results = []
    for category in CATEGORIES:
        count = sum(counts.get(event_type, 0) for event_type in category.event_types)
        breakdown = [
            (verb, counts.get(event_type, 0))
            for event_type, verb in category.verbs
            if len(category.verbs) > 1
        ]
        if category is NEXT_ACTIONS and closures:
            # A closure that ended steps is one entry here, under the closer's
            # name and in words that say it was the closure (D1).
            count += len(closures)
            breakdown.append((CLOSURE_VERB, len(closures)))
        results.append(
            CategoryResult(
                category=category,
                count=count,
                breakdown=breakdown,
                active=filters.category == category.key,
                query=urlencode(
                    filters.params(period, **{CATEGORY_PARAM: category.key, PAGE_PARAM: ""})
                ),
            )
        )
    return results


def _coverage(
    matters: QuerySet[Matter], events: QuerySet[ChangeEvent], period: dd.Period
) -> Coverage:
    in_window = _in_window(events, period)
    return Coverage(
        recorded_since=_first_day(events.filter(actor__isnull=False)),
        system_events=in_window.filter(actor__isnull=True).count(),
        automatic_steps=in_window.filter(automatic_step_q(), actor__isnull=False).count(),
        imported_matters=in_window.filter(event_type=ChangeEventType.MATTER_CREATED)
        .exclude(matter__origin=MatterOrigin.NATIVE)
        .count(),
        stage_start_unknown=MatterStageEpisode.objects.filter(
            is_current=True,
            matter__in=matters.filter(is_open=True).values("pk"),
        )
        .exclude(origin=StageEpisodeOrigin.RECORDED)
        .count(),
    )


def _first_day(events: QuerySet[ChangeEvent]) -> date | None:
    first = events.aggregate(first=Min("occurred_at"))["first"]
    return timezone.localtime(first).date() if first is not None else None


def _snapshot_comparison(user: Any, period: dd.Period) -> SnapshotComparison:
    """Open and unowned Teemad on the last photograph at or before each end —
    and, since D4, «Tähtaja ületanud» and «Vajab sekkumist» on the same two.

    Which days were photographed is a fact about the capture job, not about any
    record, so the dates are chosen over the whole table; the counts are then
    read through `visible_snapshots`, which authorizes every row against its
    live Matter. Only Matter-level columns and the two capture-time memberships
    are read.

    A membership is measured at a boundary only when **every** row of that
    day's photograph recorded it — a capture from before the columns existed
    holds NULL, and a partial count would be a number the record cannot make —
    and when the reader's scope may read it (`visible_snapshots`).
    """
    from app.reporting.models import OperationalMatterSnapshot
    from app.reporting.selectors.snapshots import visible_snapshots

    def last_on_or_before(day: date) -> date | None:
        return (
            OperationalMatterSnapshot.objects.filter(snapshot_date__lte=day)
            .order_by()
            .aggregate(last=Max("snapshot_date"))["last"]
        )

    def first_after(day: date) -> date | None:
        return (
            OperationalMatterSnapshot.objects.filter(snapshot_date__gt=day)
            .order_by()
            .aggregate(first=Min("snapshot_date"))["first"]
        )

    # The capture runs early in the morning, so a photograph shows the state
    # *before* its day's work. The start's own morning is therefore the state
    # the window began from; the end is the next morning's photograph — the
    # state after the window's last day — when it exists, and otherwise the
    # last one inside the window, which the page labels as the last day's
    # morning (its changes are not in it).
    after_end = first_after(period.end)
    comparison = SnapshotComparison(
        start_date=last_on_or_before(period.start),
        end_date=after_end if after_end is not None else last_on_or_before(period.end),
    )
    comparison.period_end = period.end
    if comparison.start_date == comparison.end_date:
        # One photograph cannot be compared with itself: the window holds no
        # capture after the one its start falls on, or none at all.
        comparison.start_date = None
    start, end = comparison.start_date, comparison.end_date
    if start is None or end is None:
        return comparison

    # Days whose capture did not record the memberships — a fact about the
    # capture, read unscoped like the dates themselves.
    unrecorded_days = set(
        OperationalMatterSnapshot.objects.filter(snapshot_date__in=(start, end))
        .filter(Q(is_overdue__isnull=True) | Q(needs_attention__isnull=True))
        .order_by()
        .values_list("snapshot_date", flat=True)
        .distinct()
    )

    rows = visible_snapshots(user).filter(snapshot_date__in=(start, end))
    totals: dict[tuple[date, str], int] = {}
    for row in (
        rows.order_by()
        .values("snapshot_date")
        .annotate(
            open=Count("pk"),
            unowned=Count("pk", filter=Q(owner__isnull=True)),
            overdue=Count("pk", filter=Q(visible_is_overdue=True)),
            overdue_unknown=Count("pk", filter=Q(visible_is_overdue__isnull=True)),
            attention=Count("pk", filter=Q(visible_needs_attention=True)),
            attention_unknown=Count("pk", filter=Q(visible_needs_attention__isnull=True)),
        )
    ):
        day = row["snapshot_date"]
        totals[(day, "open")] = int(row["open"])
        totals[(day, "unowned")] = int(row["unowned"])
        totals[(day, "overdue")] = int(row["overdue"])
        totals[(day, "attention")] = int(row["attention"])
        if row["overdue_unknown"] or row["attention_unknown"]:
            unrecorded_days.add(day)

    comparison.figures = [
        SnapshotFigure(
            label=label,
            # A photograph with none of the reader's Teemad on it is a zero —
            # the capture ran that day and found nothing they may see.
            start=totals.get((start, column), 0),
            end=totals.get((end, column), 0),
        )
        for label, column in (
            ("Avatud teemasid", "open"),
            ("Vastutajata teemasid", "unowned"),
        )
    ]

    def membership(label: str, column: str) -> SnapshotFigure:
        return SnapshotFigure(
            label=label,
            start=None if start in unrecorded_days else totals.get((start, column), 0),
            end=None if end in unrecorded_days else totals.get((end, column), 0),
        )

    comparison.overdue = membership("Tähtaja ületanud", "overdue")
    comparison.attention = membership("Vajab sekkumist", "attention")
    return comparison


# ---------------------------------------------------------------------------
# KPIs
# ---------------------------------------------------------------------------


def _event_totals(detail: QuerySet[ChangeEvent]) -> dict[str, int]:
    """The three event-based KPIs, over the detail table's own population.

    * **Muudetud teemad** — ``COUNT(DISTINCT matter)`` over the person-made
      curated events in the window, after every exclusion the categories make
      (system rows, imports, automatic steps), a person's closure included as
      its one row (its Teema counts once, as any Teema).
    * **Muudatusi** — those events counted, a closure once whether or not it
      ended open steps (`_closure_groups`).
    * **Uued teemad** — the person-made ``MATTER_CREATED`` among them, which
      `_person_acts` has already limited to ``MatterOrigin.NATIVE``.

    «Liik» narrows all three (they are the events the table lists), as do
    Vastutaja and search.
    """
    totals = detail.order_by().aggregate(
        matters=Count("matter", distinct=True),
        events=Count("pk"),
        new=Count("pk", filter=Q(event_type=ChangeEventType.MATTER_CREATED)),
    )
    return {key: int(value or 0) for key, value in totals.items()}


def _outputs(
    user: Any, period: dd.Period, filters: ChangeFilters
) -> tuple[QuerySet[Any], QuerySet[Matter]]:
    """*Tehtud*'s two canonical output populations, narrowed with the Teemad.

    * **Välja saadetud arvamused** — `department_dashboard.sent_submissions`,
      one per submission (never per audit row), counted whole rather than at
      the digest's :data:`department_dashboard.DIGEST_LIMIT`.
    * **Lõpetatud teemad** — `department_dashboard.closed_matters`, *Tehtud*'s
      «suletud».

    Unfiltered they are exactly Tehtud's numbers — `sent_submissions` reads
    every visible sent opinion, on an archive Teema too (an imported send).
    Vastutaja and search narrow that same population by its Teema's owner and
    title; «Liik» does not — an opinion and a closure are outputs, not change
    categories (docs/adr/0159 §2).
    """
    sent = dd.sent_submissions(user, since=period.start, until=period.end)
    closed = dd.closed_matters(user, since=period.start, until=period.end)
    if filters.narrows_matters:
        # The filter's condition on each output's own Teema — never a join to
        # the FULL-only population the events read, which would quietly drop a
        # sent opinion on an archive Teema the unfiltered card counts.
        sent = sent.filter(matter_filter_q(filters, "matter__"))
        closed = closed.filter(matter_filter_q(filters))
    return sent, closed


def work_state_kpis(
    user: Any,
    filtered: QuerySet[Matter],
    filters: ChangeFilters,
    snapshots: SnapshotComparison,
    *,
    today: date,
) -> list[Kpi]:
    """«Tähtaja ületanud» and «Vajab sekkumist»: today's value, and the window's
    change where both photographs recorded it.

    Isolated on purpose — the one place these two cards are computed, so the
    source of either half can change without touching the page.

    **Today's value** is the live canonical population, for this reader:
    «Tähtaja ületanud» is `work_items.WORK_OVERDUE` (the Matters of
    `overdue_items`), «Vajab sekkumist» is `WORK_NEEDS_ATTENTION` (overdue ∪
    ripe for review ∪ no next action ∪ no owner) — the figures Osakond and the
    register print. Narrowed by Vastutaja and search (current owner); «Liik»
    does not apply.

    **Start → end** is the snapshot comparison's (D4): the capture-time
    memberships on the photograph at or before each end of the window,
    department-wide. A boundary whose photograph did not record the membership
    — every capture before 2026-10-30 — is «ei mõõdetud», never 0.
    """
    from app.matters import work_items as wi

    items = wi.work_items(user, today=today)
    overdue_ids = wi.work_population_ids(user, wi.WORK_OVERDUE, today=today, items=items)
    attention_ids = wi.work_population_ids(user, wi.WORK_NEEDS_ATTENTION, today=today, items=items)
    current = filtered.order_by().aggregate(
        overdue=Count("pk", filter=Q(pk__in=overdue_ids)),
        attention=Count("pk", filter=Q(pk__in=attention_ids)),
    )

    def card(key: str, label: str, figure: SnapshotFigure | None, definition: str) -> Kpi:
        start = figure.start if figure is not None else None
        end = figure.end if figure is not None else None
        if not snapshots.available:
            note = SNAPSHOT_UNAVAILABLE
        elif start is None or end is None:
            note = WORK_STATE_UNMEASURED
        else:
            note = _snapshot_note(snapshots, filters.narrows_matters)
        return Kpi(
            key=key,
            label=label,
            unit=UNIT_TOPICS,
            value=int(current[key.split("-")[0]] or 0),
            compared=True,
            start=start,
            end=end,
            definition=definition,
            note=note,
        )

    return [
        card(
            "attention",
            "Tähelepanu vajavad",
            snapshots.attention,
            "Praegu «Vajab sekkumist»: hilinenud, ülevaatamise aeg käes, "
            "järgmise tegevuseta või vastutajata.",
        ),
        card(
            "overdue",
            "Tähtaja ületanud",
            snapshots.overdue,
            "Praegu üle tähtaja: tegevus, arvamuse tähtaeg, oluline tähtaeg või tagasiside ootus.",
        ),
    ]


def _kpis(
    totals: dict[str, int],
    sent: int,
    closed: int,
    snapshots: SnapshotComparison,
    filters: ChangeFilters,
    work_state: list[Kpi],
) -> list[Kpi]:
    open_figure = snapshots.figures[0] if snapshots.figures else None
    if not snapshots.available:
        open_note = SNAPSHOT_UNAVAILABLE
    else:
        open_note = _snapshot_note(snapshots, filters.active)
    return [
        Kpi(
            "matters",
            "Muudetud teemad",
            UNIT_TOPICS,
            totals["matters"],
            definition="Eri teemad, milles keegi perioodil midagi muutis.",
            from_events=True,
        ),
        Kpi(
            "events",
            "Muudatusi",
            UNIT_EVENTS,
            totals["events"],
            definition="Inimeste tehtud muudatused; üks sulgemine on üks muudatus.",
            from_events=True,
        ),
        Kpi(
            "new",
            "Uued teemad",
            UNIT_TOPICS,
            totals["new"],
            definition="Siin loodud teemad; imporditud ei ole uued.",
            from_events=True,
        ),
        Kpi(
            "sent",
            "Välja saadetud arvamused",
            UNIT_OPINIONS,
            sent,
            definition="Saadetud arvamused, üks arvamus üks kord — nagu Tehtud.",
        ),
        Kpi(
            "closed",
            "Lõpetatud teemad",
            UNIT_TOPICS,
            closed,
            definition="Perioodil suletud ja praegu suletud teemad — nagu Tehtud.",
        ),
        Kpi(
            "open",
            "Menetluses olevad teemad",
            UNIT_TOPICS,
            compared=True,
            start=open_figure.start if open_figure is not None else None,
            end=open_figure.end if open_figure is not None else None,
            definition="Avatud teemad perioodi alguse ja lõpu hetktõmmisel.",
            note=open_note,
        ),
        *work_state,
    ]


# ---------------------------------------------------------------------------
# The per-person table and the detail table
# ---------------------------------------------------------------------------

PEOPLE_COLUMNS: tuple[tuple[str, str], ...] = (
    ("nimi", "Vastutaja"),
    ("teemad", "Muudetud teemad"),
    ("muudatused", "Muudatusi"),
    ("uued", "Uued teemad"),
    ("arvamused", "Saadetud arvamused"),
    ("lopetatud", "Lõpetatud teemad"),
)
_PEOPLE_FIELD = {
    "teemad": "matters",
    "muudatused": "events",
    "uued": "new",
    "arvamused": "sent",
    "lopetatud": "closed",
}
UNOWNED_LABEL = "Vastutajata"


def _people(
    detail: QuerySet[ChangeEvent],
    sent: QuerySet[Any],
    closed: QuerySet[Matter],
    filters: ChangeFilters,
    period: dd.Period,
) -> tuple[list[PersonRow], PersonRow]:
    """One line per **current** Vastutaja of the Teemad (`Matter.owner`).

    Never the event's actor: work Marko did on a file Kati now carries counts on
    Kati's line, because this table answers «whose files moved», the question
    Meeskond's columns answer too (`department_dashboard._matters_changed_by_owner`).
    «Vastutajata» is its own line; an inactive owner keeps a named line marked
    «(mitteaktiivne)».

    Every column partitions its KPI's population by that one key, so each sums
    to the KPI exactly: a Teema has one current owner or none, so its distinct
    count lands on one line. Four queries: events, opinions, closures, people.
    """
    from django.contrib.auth import get_user_model

    lines: dict[Any, dict[str, int]] = {}

    def line(owner_id: Any) -> dict[str, int]:
        return lines.setdefault(
            owner_id, {"matters": 0, "events": 0, "new": 0, "sent": 0, "closed": 0}
        )

    for row in (
        detail.order_by()
        .values("matter__owner")
        .annotate(
            matters=Count("matter", distinct=True),
            events=Count("pk"),
            new=Count("pk", filter=Q(event_type=ChangeEventType.MATTER_CREATED)),
        )
    ):
        target = line(row["matter__owner"])
        for key in ("matters", "events", "new"):
            target[key] = int(row[key])
    # Re-wrapped and unordered for the reason `department_dashboard._by_owner`
    # gives: an incoming ordering or DISTINCT leaks into the GROUP BY.
    from app.submissions.models import Submission

    for sent_row in (
        Submission.objects.filter(pk__in=sent.values("pk"))
        .order_by()
        .values("matter__owner")
        .annotate(n=Count("pk"))
    ):
        line(sent_row["matter__owner"])["sent"] = int(sent_row["n"])
    for closed_row in (
        Matter.objects.filter(pk__in=closed.values("pk"))
        .order_by()
        .values("owner")
        .annotate(n=Count("pk"))
    ):
        line(closed_row["owner"])["closed"] = int(closed_row["n"])

    people = get_user_model().objects.in_bulk([key for key in lines if key is not None])
    rows = []
    for owner_id, figures in lines.items():
        person = people.get(owner_id) if owner_id is not None else None
        key = str(owner_id) if owner_id is not None else OWNER_NONE
        rows.append(
            PersonRow(
                key=key,
                name=person.display_name if person is not None else UNOWNED_LABEL,
                inactive=person is not None and not person.is_active,
                unowned=owner_id is None,
                query=urlencode(filters.params(period, **{OWNER_PARAM: key, PAGE_PARAM: ""})),
                **figures,
            )
        )

    sort = filters.people_sort
    column = sort.lstrip("-")
    descending = sort.startswith("-")
    if column == "nimi":
        rows.sort(key=lambda row: (row.unowned, row.name.casefold()), reverse=descending)
    else:
        attribute = _PEOPLE_FIELD[column]
        rows.sort(key=lambda row: row.name.casefold())
        rows.sort(key=lambda row: getattr(row, attribute), reverse=descending)

    total = PersonRow(
        key="",
        name="Kokku",
        matters=sum(row.matters for row in rows),
        events=sum(row.events for row in rows),
        new=sum(row.new for row in rows),
        sent=sum(row.sent for row in rows),
        closed=sum(row.closed for row in rows),
    )
    return rows, total


DETAIL_COLUMNS: tuple[tuple[str, str], ...] = (
    ("aeg", "Aeg"),
    ("teema", "Teema"),
    ("liik", "Liik"),
)


def _detail_table(
    detail: QuerySet[ChangeEvent],
    closures: dict[Any, dict[str, Any]],
    filters: ChangeFilters,
    period: dd.Period,
) -> DetailTable:
    """One page of rows: two queries (count, page) whatever the period holds.

    Sorted in the database — by time, by Teema title or by category — so the
    page is the right fifty of the whole population, not the first fifty
    re-sorted. A closure is its representative event's row (`_closure_groups`).
    """
    column = filters.sort.lstrip("-")
    descending = filters.sort.startswith("-")
    fields = DETAIL_SORTS[column]
    if column == "aeg" and descending:
        ordering = tuple(f"-{name}" for name in fields)
    elif descending:
        ordering = (f"-{fields[0]}", *fields[1:])
    else:
        ordering = fields
    order_of = {category.key: index for index, category in enumerate(CATEGORIES)}
    queryset = (
        detail.select_related("actor", "matter", "matter__owner")
        .annotate(
            category_order=Case(
                *(
                    When(
                        event_type__in=_category_types(category),
                        then=Value(order_of[category.key]),
                    )
                    for category in CATEGORIES
                ),
                default=Value(len(CATEGORIES)),
                output_field=IntegerField(),
            )
        )
        .order_by(*ordering)
    )
    page = Paginator(queryset, PAGE_SIZE).get_page(filters.page)
    rows = [
        _closure_row(event, closures[event.pk]) if event.pk in closures else _row(event)
        for event in page.object_list
    ]
    return DetailTable(
        rows=rows,
        page=page,
        count=page.paginator.count,
        query_string=urlencode(filters.params(period, **{PAGE_PARAM: ""})),
        headings=_sort_headings(
            filters,
            period,
            DETAIL_COLUMNS,
            param=SORT_PARAM,
            current=filters.sort,
            descending_first=frozenset({"aeg"}),
            keep_page=False,
        ),
    )


# ---------------------------------------------------------------------------
# The view model
# ---------------------------------------------------------------------------


def build_changes(
    user: Any,
    params: Any = None,
    *,
    today: date | None = None,
    with_outputs: bool = True,
) -> DepartmentChanges:
    """Everything the change view prints, for one reader, window and filter set.

    ``user`` is who the page is authorized as. Whether this reader may open
    the view at all is the caller's question (docs/adr/0159 §4); this builds
    nothing beyond what ``user`` may see either way.

    **One scoped population.** The visible current-work Teemad
    (`matter_visibility_q`), narrowed by Vastutaja and search, then their
    curated events through `scope_change_events` — authorization before any
    filter and every filter before any aggregate — feed the category counts,
    the KPIs, the per-person table and the detail rows alike. «Liik» narrows
    the KPIs, the people and the rows; the category counts beside it keep
    every category so the reader can switch.
    """
    params = params if params is not None else {}
    today = today or timezone.localdate()
    period, error = changes_period(params, today)
    matters = _visible_full_matters(user)
    owner_users = _owner_choices(matters)
    filters = read_filters(params, frozenset(str(person.pk) for person in owner_users))
    filtered = _filtered_matters(matters, filters)

    events = _scoped_events(user, matters)
    all_acts = _person_acts(_in_window(_scoped_events(user, filtered, with_closures=True), period))
    acts = all_acts.exclude(event_type=CLOSURE_EVENT)
    closures = _closure_groups(
        acts.filter(closure_cancelled_q()), all_acts.filter(event_type=CLOSURE_EVENT)
    )
    plain = acts.exclude(closure_cancelled_q())
    detail = all_acts.filter(
        (~closure_cancelled_q() & ~Q(event_type=CLOSURE_EVENT)) | Q(pk__in=list(closures))
    )
    if filters.category:
        category = next(item for item in CATEGORIES if item.key == filters.category)
        detail = detail.filter(event_type__in=_category_types(category))

    snapshots = _snapshot_comparison(user, period)
    sent, closed = _outputs(user, period, filters)
    people, people_total = _people(detail, sent, closed, filters, period)
    return DepartmentChanges(
        period=period,
        period_error=error,
        categories=_categories(plain, closures, filters, period),
        coverage=_coverage(matters, events, period),
        snapshots=snapshots,
        outputs=dd.build_digest(user, period) if with_outputs else None,
        filters=filters,
        owner_choices=[
            OwnerChoice(
                value=str(person.pk),
                label=f"{person.display_name} (mitteaktiivne)"
                if not person.is_active
                else person.display_name,
                selected=filters.owner == str(person.pk),
            )
            for person in owner_users
        ],
        kpis=_kpis(
            _event_totals(detail),
            sent.count(),
            closed.count(),
            snapshots,
            filters,
            work_state_kpis(user, filtered, filters, snapshots, today=today),
        ),
        people=people,
        people_total=people_total,
        people_headings=_sort_headings(
            filters,
            period,
            PEOPLE_COLUMNS,
            param=PEOPLE_SORT_PARAM,
            current=filters.people_sort,
            descending_first=frozenset(_PEOPLE_FIELD),
            keep_page=True,
        ),
        detail=_detail_table(detail, closures, filters, period),
    )
