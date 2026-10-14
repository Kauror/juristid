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


#: The reason `workflow.services.end_open_action_for_closure` writes on every
#: step a closure ends. Read, never written, here.
CLOSURE_CANCEL_REASON = "Teema suleti"

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


def _automatic_q() -> Q:
    """Step changes a service made on its own, which nobody chose (ADR 0159 §2, D1).

    Each is recognised by the marker its writer puts in the payload, so the
    audit row itself stays as it is:

    * the first opinion follow-up check, scheduled by recording the send
      (``follow_up.automatic``, docs/adr/0146 §10) — the send is Tehtud's;
    * a current check put back on the plan because new work was written over
      it (``returned_to_plan: true``; the new work's own event carries the id
      as a string and stays a person's act);
    * a planned step made current because the current one was finished
      (``promoted``) — it was «määras» once, when it was planned, and the
      finishing is its own «lõpetas»;
    * a check's day recalculated from a corrected send date (``recalculated``).
    """
    # JSON containment (`@>`), never a key lookup: a missing key compares NULL,
    # and `exclude()` over a NULL drops the row it should keep.
    return (
        Q(
            event_type=ChangeEventType.NEXT_ACTION_SET,
            payload__contains={"follow_up": {"automatic": True}},
        )
        | Q(
            event_type=ChangeEventType.NEXT_ACTION_SET,
            payload__contains={"returned_to_plan": True},
        )
        | Q(event_type=ChangeEventType.NEXT_ACTION_SET, payload__contains={"promoted": True})
        | Q(
            event_type=ChangeEventType.NEXT_ACTION_RESCHEDULED,
            payload__contains={"recalculated": True},
        )
    )


def _closure_q() -> Q:
    """A step ended because its Matter was closed, not cancelled on its own.

    The reason the closure helper writes; a ``closure`` key, should a closure
    writer come to mark its cancellations explicitly, is read the same way.
    """
    return Q(event_type=ChangeEventType.NEXT_ACTION_CANCELLED) & (
        Q(payload__contains={"reason": CLOSURE_CANCEL_REASON}) | Q(payload__has_key="closure")
    )


def _person_acts(events: QuerySet[ChangeEvent]) -> QuerySet[ChangeEvent]:
    """A colleague's change, and for a new Teema one filed here, not imported.

    A step change the system made on the person's behalf (`_automatic_q`) is
    not theirs, though the row names them as its actor.
    """
    return (
        events.filter(actor__isnull=False)
        .exclude(
            Q(event_type=ChangeEventType.MATTER_CREATED) & ~Q(matter__origin=MatterOrigin.NATIVE)
        )
        .exclude(_automatic_q())
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


def _closure_rows(closure_acts: QuerySet[ChangeEvent]) -> list[ChangeRow]:
    """One row per closure that ended steps, however many it ended (D1).

    The cancellations stay in the audit history one by one; this only reads
    them as the one act they were. Which closure a cancellation belongs to:

    1. **the closure that names it** — `MATTER_CLOSED.payload.ended.actions`
       lists the ids of the steps that closure ended (docs/adr/0152 §7);
    2. otherwise the first closing event on its Matter at or after it (a
       closure writes its steps' cancellations first, then itself) — the rule
       for closures recorded before that list existed, and for cutovers;
    3. otherwise it stands alone rather than being merged with a stranger.

    Bounded: the cancellations, the closing events on their Matters, then
    their Matters and people — four queries, whatever the number of rows.
    """
    from django.contrib.auth import get_user_model

    cancels = list(
        closure_acts.order_by().values("pk", "matter", "actor", "object_id", "occurred_at")
    )
    if not cancels:
        return []
    closings = list(
        ChangeEvent.objects.filter(
            matter__in={cancel["matter"] for cancel in cancels},
            event_type__in=CLOSING_EVENT_TYPES,
        )
        .order_by("occurred_at", "created_at", "pk")
        .values("pk", "matter", "occurred_at", "payload")
    )
    named: dict[str, Any] = {}
    by_matter: dict[Any, list[Any]] = {}
    for closing in closings:
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

    grouped: dict[tuple[Any, Any, Any], dict[str, Any]] = {}
    for cancel in cancels:
        key = (closure_of(cancel), cancel["matter"], cancel["actor"])
        group = grouped.setdefault(
            key, {"matter": cancel["matter"], "actor": cancel["actor"], "n": 0, "at": None}
        )
        group["n"] += 1
        if group["at"] is None or cancel["occurred_at"] > group["at"]:
            group["at"] = cancel["occurred_at"]
    groups = sorted(grouped.values(), key=lambda group: group["at"], reverse=True)
    if not groups:
        return []
    matters = Matter.objects.in_bulk({group["matter"] for group in groups})
    people = get_user_model().objects.in_bulk({group["actor"] for group in groups})
    rows = []
    for group in groups:
        matter = matters.get(group["matter"])
        actor = people.get(group["actor"])
        ended = group["n"]
        rows.append(
            ChangeRow(
                at=group["at"],
                event_type=CLOSURE_ROW,
                badge=NEXT_ACTIONS.badge,
                verb=CLOSURE_VERB,
                detail=f"lõppes {ended} {'tegevus' if ended == 1 else 'tegevust'}",
                actor_name=actor.display_name if actor is not None else "",
                initials=actor.initials if actor is not None else "··",
                matter_title=matter.title if matter is not None else "",
                url=(reverse("matters:matter_detail", kwargs={"pk": matter.pk}) if matter else ""),
            )
        )
    return rows


def _categories(
    acts: QuerySet[ChangeEvent], closure_acts: QuerySet[ChangeEvent]
) -> list[CategoryResult]:
    counts = {
        row["event_type"]: row["n"]
        for row in acts.order_by().values("event_type").annotate(n=Count("pk"))
    }
    closures = _closure_rows(closure_acts)
    results = []
    for category in CATEGORIES:
        rows = [
            _row(event)
            for event in acts.filter(event_type__in=category.event_types)
            .select_related("actor", "matter")
            .order_by("-occurred_at", "-created_at")[:ROW_LIMIT]
        ]
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
            rows = sorted(rows + closures, key=lambda row: row.at, reverse=True)[:ROW_LIMIT]
        results.append(
            CategoryResult(category=category, count=count, breakdown=breakdown, rows=rows)
        )
    return results


def _coverage(
    matters: QuerySet[Matter], events: QuerySet[ChangeEvent], period: dd.Period
) -> Coverage:
    in_window = _in_window(events, period)
    return Coverage(
        recorded_since=_first_day(events.filter(actor__isnull=False)),
        system_events=in_window.filter(actor__isnull=True).count(),
        automatic_steps=in_window.filter(_automatic_q(), actor__isnull=False).count(),
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
        categories=_categories(acts.exclude(_closure_q()), acts.filter(_closure_q())),
        coverage=_coverage(matters, events, period),
        snapshots=_snapshot_comparison(user, period),
        outputs=dd.build_digest(user, period) if with_outputs else None,
    )
