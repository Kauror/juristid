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

Every query is bounded: one grouped count, one capped row query per category,
and a fixed handful of coverage and snapshot aggregates — no query per row.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime
from typing import Any
from urllib.parse import urlencode

from django.db.models import Count, Max, Min, Q, QuerySet
from django.urls import reverse
from django.utils import timezone

from app.audit.enums import ChangeEventType
from app.audit.models import ChangeEvent
from app.audit.visibility import scope_change_events
from app.core.authorization import apply as apply_scope
from app.core.authorization import matter_visibility_q, scope_for_user
from app.core.dates import format_estonian_date, read_flexible_date
from app.matters import department_dashboard as dd
from app.matters.enums import MatterOrigin, RecordMode, StageEpisodeOrigin
from app.matters.models import Matter, MatterStageEpisode

#: The windows this view offers, in the order the control draws them. The
#: parser is Tehtud's (`department_dashboard.period_from`), so «7 päeva» starts
#: on the same day in both sections.
PERIOD_CHOICES: tuple[tuple[str, str], ...] = (
    (dd.PERIOD_7, "7 päeva"),
    (dd.PERIOD_14, "14 päeva"),
    (dd.PERIOD_30, "30 päeva"),
)

#: How many rows one category lists. The count beside its heading is the whole
#: period's, always; this bounds only what is rendered.
ROW_LIMIT = 50

CUSTOM_RANGE_INCOMPLETE = "Vali vahemiku algus- ja lõppkuupäev."
CUSTOM_RANGE_UNREADABLE = "Vahemiku kuupäev ei ole loetav. Kasuta kuju pp.kk.aaaa."
CUSTOM_RANGE_REVERSED = "Vahemiku algus on pärast selle lõppu."


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

    @property
    def when(self) -> date:
        return timezone.localtime(self.at).date()


@dataclass
class CategoryResult:
    category: Category
    count: int = 0
    #: Per event type, for a category that holds several (next actions).
    breakdown: list[tuple[str, int]] = field(default_factory=list)
    rows: list[ChangeRow] = field(default_factory=list)

    @property
    def key(self) -> str:
        return self.category.key

    @property
    def label(self) -> str:
        return self.category.label

    @property
    def truncated(self) -> bool:
        return self.count > len(self.rows)


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

    @property
    def available(self) -> bool:
        return self.start_date is not None and self.end_date is not None


@dataclass
class Coverage:
    """What this window's record cannot know, as numbers."""

    #: The earliest act a person recorded, over the reader's population. None
    #: when nothing has been recorded at all.
    recorded_since: date | None = None
    #: Curated events in the window with no person behind them — imports,
    #: cutovers, backfills. Not counted above.
    system_events: int = 0
    #: Teemad that arrived by import in the window. Not «new»: they existed
    #: before they were copied in.
    imported_matters: int = 0
    #: Open Teemad whose current `Hetkeseis` began at a moment nobody recorded.
    stage_start_unknown: int = 0

    def starts_after(self, period: dd.Period) -> bool:
        """Whether the window reaches back before anything was recorded."""
        return self.recorded_since is None or self.recorded_since > period.start


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

    @property
    def total(self) -> int:
        return sum(result.count for result in self.categories)

    @property
    def is_empty(self) -> bool:
        return self.total == 0

    @property
    def partial_coverage(self) -> bool:
        """The window reaches back before the first recorded change."""
        return self.coverage.starts_after(self.period)

    @property
    def period_options(self) -> list[dd.PeriodOption]:
        """The three named windows. None is selected while a typed range was
        refused, so the page never claims the reader chose the fallback."""
        return [
            dd.PeriodOption(
                key=key,
                label=label,
                active=self.period.key == key and not self.period_error,
                query=urlencode({dd.PERIOD_PARAM: key}),
            )
            for key, label in PERIOD_CHOICES
        ]

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
        error = ""
        if start.invalid or end.invalid:
            error = CUSTOM_RANGE_UNREADABLE
        elif start.value is None or end.value is None:
            error = CUSTOM_RANGE_INCOMPLETE
        elif start.value > end.value:
            error = CUSTOM_RANGE_REVERSED
        if error or start.value is None or end.value is None:
            return dd.period_from({}, today), error
        return dd.Period(dd.PERIOD_CUSTOM, start.value, end.value), ""
    if key not in {choice for choice, _ in PERIOD_CHOICES}:
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

    A window this view names (7 / 14 / 30) travels as its key; any other —
    Kvartal, Aasta, a custom range — as the two dates it covers, so the link
    never lands on a different period than the section it sits in.
    """
    named = {key for key, _ in PERIOD_CHOICES}
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


def _scoped_events(user: Any, matters: QuerySet[Matter]) -> QuerySet[ChangeEvent]:
    """Curated events on visible Matters whose own subject is visible too.

    Not yet narrowed to a window or to people: coverage reads the same
    population for its earliest act and its system rows, so the boundary is
    written once.
    """
    return scope_change_events(
        ChangeEvent.objects.filter(
            event_type__in=CURATED_EVENT_TYPES,
            matter__in=matters.values("pk"),
        ),
        user,
    )


def _in_window(events: QuerySet[ChangeEvent], period: dd.Period) -> QuerySet[ChangeEvent]:
    # `__date`, in the project's zone (Europe/Tallinn): a change at 00:30 on
    # the first day is inside a window that starts that day, one at 23:59 on
    # the last day is inside one that ends it.
    return events.filter(
        occurred_at__date__gte=period.start,
        occurred_at__date__lte=period.end,
    )


def _person_acts(events: QuerySet[ChangeEvent]) -> QuerySet[ChangeEvent]:
    """A colleague's change, and for a new Teema one filed here, not imported."""
    return events.filter(actor__isnull=False).exclude(
        Q(event_type=ChangeEventType.MATTER_CREATED) & ~Q(matter__origin=MatterOrigin.NATIVE)
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


def _row(event: ChangeEvent) -> ChangeRow:
    actor = event.actor
    matter = event.matter
    return ChangeRow(
        at=event.occurred_at,
        event_type=event.event_type,
        badge=_BADGES[event.event_type],
        verb=_VERBS[event.event_type],
        detail=_detail(event),
        actor_name=actor.display_name if actor is not None else "",
        initials=actor.initials if actor is not None else "··",
        matter_title=matter.title if matter is not None else "",
        url=reverse("matters:matter_detail", kwargs={"pk": matter.pk}) if matter else "",
    )


def _categories(acts: QuerySet[ChangeEvent]) -> list[CategoryResult]:
    counts = {
        row["event_type"]: row["n"]
        for row in acts.order_by().values("event_type").annotate(n=Count("pk"))
    }
    results = []
    for category in CATEGORIES:
        rows = (
            acts.filter(event_type__in=category.event_types)
            .select_related("actor", "matter")
            .order_by("-occurred_at", "-created_at")[:ROW_LIMIT]
        )
        results.append(
            CategoryResult(
                category=category,
                count=sum(counts.get(event_type, 0) for event_type in category.event_types),
                breakdown=[
                    (verb, counts.get(event_type, 0))
                    for event_type, verb in category.verbs
                    if len(category.verbs) > 1
                ],
                rows=[_row(event) for event in rows],
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
    """Open and unowned Teemad on the last photograph at or before each end.

    Which days were photographed is a fact about the capture job, not about any
    record, so the dates are chosen over the whole table; the counts are then
    read through `visible_snapshots`, which authorizes every row against its
    live Matter. Only Matter-level columns are read — the photographed next
    step has visibility of its own and is not needed here.
    """
    from app.reporting.models import OperationalMatterSnapshot
    from app.reporting.selectors.snapshots import visible_snapshots

    def last_on_or_before(day: date) -> date | None:
        return (
            OperationalMatterSnapshot.objects.filter(snapshot_date__lte=day)
            .order_by()
            .aggregate(last=Max("snapshot_date"))["last"]
        )

    comparison = SnapshotComparison(
        start_date=last_on_or_before(period.start),
        end_date=last_on_or_before(period.end),
    )
    if comparison.start_date == comparison.end_date:
        # One photograph cannot be compared with itself: the window holds no
        # capture after the one its start falls on, or none at all.
        comparison.start_date = None
    start, end = comparison.start_date, comparison.end_date
    if start is None or end is None:
        return comparison

    rows = visible_snapshots(user).filter(snapshot_date__in=(start, end))
    totals: dict[tuple[date, str], int] = {}
    for row in (
        rows.order_by()
        .values("snapshot_date")
        .annotate(open=Count("pk"), unowned=Count("pk", filter=Q(owner__isnull=True)))
    ):
        totals[(row["snapshot_date"], "open")] = int(row["open"])
        totals[(row["snapshot_date"], "unowned")] = int(row["unowned"])

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
    return comparison


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
    """Everything the change view prints, for one reader and one window.

    ``user`` is who the page is authorized as. Whether this reader may open
    the view at all is the caller's question (docs/adr/0159 §4); this builds
    nothing beyond what ``user`` may see either way.
    """
    today = today or timezone.localdate()
    period, error = changes_period(params if params is not None else {}, today)
    matters = _visible_full_matters(user)
    events = _scoped_events(user, matters)
    acts = _person_acts(_in_window(events, period))
    return DepartmentChanges(
        period=period,
        period_error=error,
        categories=_categories(acts),
        coverage=_coverage(matters, events, period),
        snapshots=_snapshot_comparison(user, period),
        outputs=dd.build_digest(user, period) if with_outputs else None,
    )
