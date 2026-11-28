"""«Selle nädala ülevaade» — what happened and what is still owed, this calendar week (r97).

A read side only: nothing here writes, fetches or asks an AI anything. One scope
decides what is counted, and the figure and the list behind it come from the
**same function**, so a number can never describe a different set of records than
the page it opens:

* ``SELF`` — a person's own desk. History is credited the way Minu statistika
  credits it (`my_statistics.credit_opinion`: the recorded sender, else the owner
  *on the sent day*, else unknown). The current and the previous week are cells of
  **one** `my_statistics.load_series` read.
* ``DEPARTMENT`` — Osakond, for the department head only. Distinct records of every
  Teema the head may read; no per-person totals are added together and nothing is
  ranked.
* ``COLLEAGUE`` — a head reading a colleague's desk. Operational work only (the
  seven-day list): a colleague's personal historical statistics are not part of
  that workspace (r97 R97-C03).

Actions are counted from the `NextAction` rows by what ended them
(`classify_ended`) and attributed to ``ended_by``. A moved day is never *tehtud*
(ADR 0235). Reviews are `NEXT_ACTION_REVIEWED` events, each one counted, because
a person may legitimately review the same waiting step more than once.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, timedelta
from typing import Any
from urllib.parse import urlencode

from django.db.models import QuerySet
from django.urls import reverse
from django.utils import timezone

from app.core.dates import format_estonian_date, local_day_range
from app.matters import stat_periods
from app.matters import work_items as wi
from app.matters.dashboard import drafting_matters
from app.matters.models import Matter
from app.matters.my_dates import (
    CERTAINTY_EXACT,
    EffectiveRow,
    deadline_rows,
    effective_rows,
)
from app.matters.my_statistics import (
    Cell,
    Coverage,
    SeriesData,
    arrivals_series,
    cell,
    created_since,
    credit_opinion,
    load_series,
    recording_since,
    reliable_since,
)
from app.workflow.enums import REVIEW_KINDS, ActionKind, ActionStatus, DateSemantics
from app.workflow.models import NextAction

SELF, DEPARTMENT, COLLEAGUE = "self", "department", "colleague"

#: Outcomes of an action; the key is the `?naitaja=` of the records page.
DONE, REVIEWED, POSTPONED, DATE_CHANGED, SUPERSEDED, CANCELLED, OUTSTANDING = (
    "tehtud",
    "ule-vaadatud",
    "edasi-lukatud",
    "tahtaega-muudetud",
    "asendatud",
    "tuhistatud",
    "tegemata",
)

ACTION_LABELS: dict[str, str] = {
    DONE: "Tehtud",
    REVIEWED: "Üle vaadatud",
    POSTPONED: "Edasi lükatud",
    DATE_CHANGED: "Tähtaega muudetud",
    SUPERSEDED: "Asendatud",
    CANCELLED: "Tühistatud",
    OUTSTANDING: "Tegemata",
}
#: What a figure counts, where it is not an action: said for a screen reader.
ACTION_UNITS: dict[str, str] = {REVIEWED: "ülevaatust"}

SENT, NEW, OWED, LATE = "saadetud-arvamused", "uued-teemad", "taitmata-nouded", "tahtaja-ulatanud"
#: What the record *says*, never that the act came into force
#: (`my_dates.COMMENCEMENT_PASSED`): an exact date that has come, an exact date still
#: ahead, and an approximate one (past or future, never presented as confirmed).
ARRIVED, UPCOMING, PLANNED = "kuupaev-saabunud", "eesolevad-joustumised", "ligikaudsed-joustumised"
HISTORY_KINDS = (DONE, REVIEWED, POSTPONED, DATE_CHANGED, SUPERSEDED, CANCELLED)
COMMENCEMENT_KINDS = (ARRIVED, UPCOMING, PLANNED)
RECORD_TITLES: dict[str, str] = {
    **ACTION_LABELS,
    SENT: "Saadetud arvamused",
    NEW: "Uued teemad",
    OWED: "Täitmata arvamuse nõuded sel nädalal",
    LATE: "Varasemast üle tähtaja arvamuse nõuded",
    ARRIVED: "Kuupäev saabunud",
    UPCOMING: "Eesolevad jõustumised",
    PLANNED: "Ligikaudsed jõustumised",
}

PREVIEW = 3
HORIZON_DAYS = 7
SCOPE_PARAM = "ulatus"
DEPARTMENT_VALUE = "osakond"
#: The preview and its «Vaata kõiki» page read the same window.
PREVIEW_DAYS = 365

#: Incoming-monitoring figures of Minu statistika, in its order.
_MONITORING = (
    ("registrid", "Registrite dokumendid"),
    ("kutsed", "Riigikogu kutsed"),
    ("arutelud", "Riigikogu arutelud"),
    ("mainimised", "Koja mainimised"),
    ("valitsus", "Valitsuse päevakord"),
)


@dataclass(frozen=True)
class Rec:
    """One counted record. ``key`` is its identity: two records never share one."""

    key: str
    matter: Matter | None
    day: date | None
    what: str = ""
    note: str = ""
    actor: str = ""
    url: str = ""
    owner: str = ""


@dataclass(frozen=True)
class WeekFigure:
    key: str
    label: str
    cell: Cell
    url: str = ""
    tone: str = ""
    unit: str = ""

    @property
    def text(self) -> str:
        return self.cell.text

    @property
    def accessible(self) -> str:
        return f"{self.label}: {self.cell.spoken} {self.unit}".strip()


@dataclass(frozen=True)
class SevenDayRow:
    when: date
    relative: str
    matter: Matter
    what: str
    category: str
    url: str
    status: str
    is_review: bool
    is_late: bool
    owner: str = ""


@dataclass(frozen=True)
class PreviewRow:
    matter: Matter
    day: date | None
    note: str = ""
    url: str = ""
    owner: str = ""


@dataclass
class WeekOverview:
    scope: str
    start: date
    end: date
    previous_start: date
    previous_end: date
    opinions: list[WeekFigure] = field(default_factory=list)
    actions: list[WeekFigure] = field(default_factory=list)
    commencements: list[WeekFigure] = field(default_factory=list)
    previous: list[WeekFigure] = field(default_factory=list)
    monitoring: list[WeekFigure] = field(default_factory=list)
    seven_days: list[SevenDayRow] = field(default_factory=list)
    seven_days_url: str = ""
    recent_opinions: list[PreviewRow] = field(default_factory=list)
    recent_opinions_url: str = ""
    new_matters: list[PreviewRow] = field(default_factory=list)
    new_matters_url: str = ""
    upcoming_commencements: list[EffectiveRow] = field(default_factory=list)
    commencements_url: str = ""
    statistics_url: str = ""
    previous_statistics_url: str = ""

    @property
    def has_history(self) -> bool:
        """Whether the history blocks exist at all (they never do on a colleague's desk)."""
        return self.scope in (SELF, DEPARTMENT)

    @property
    def is_department(self) -> bool:
        return self.scope == DEPARTMENT


# ---------------------------------------------------------------------------
# The context every figure and every list is read through
# ---------------------------------------------------------------------------


@dataclass
class Ctx:
    viewer: Any
    scope: str
    subject: Any
    today: date
    week: Any
    last: Any
    data: SeriesData | None = None
    #: The sent opinions, read once per request (the page may hand its own read in).
    facts: list[Any] | None = None
    #: First day each history figure is reliably recorded (`reliable_since`).
    reliable: dict[str, date | None] = field(default_factory=dict)
    #: One request's reads, by what was asked: a figure and the preview or list that
    #: shares its population never read it twice. Per request, per reader — never
    #: shared between users, so it cannot go stale or leak.
    memo: dict[Any, Any] = field(default_factory=dict)

    @property
    def person_id(self) -> Any:
        return None if self.subject is None else str(self.subject.pk)


def make_ctx(
    viewer: Any, scope: str, subject: Any, today: date, *, facts: list[Any] | None = None
) -> Ctx:
    week = stat_periods.current(today)
    ctx = Ctx(viewer, scope, subject, today, week, week.previous, facts=facts)
    if scope == SELF:
        ctx.data = load_series(subject, today, ctx.last.start, week.until)
        ctx.reliable = ctx.data.reliable
    elif scope == DEPARTMENT:
        ctx.reliable = reliable_since(viewer, recording_since())
    return ctx


def _span(start: date, end: date) -> stat_periods.Span:
    return stat_periods.Span(start, end)


def _name(user: Any) -> str:
    return "" if user is None else str(user.get_short_name() or user.display_name)


def _matter_url(pk: Any) -> str:
    return reverse("matters:matter_detail", kwargs={"pk": pk})


# ---------------------------------------------------------------------------
# Actions — counted from the rows, by what ended them
# ---------------------------------------------------------------------------


def classify_ended(row: NextAction) -> str:
    """What ending ``row`` was. The one place that tells the outcomes apart.

    COMPLETED is the only work done. A SUPERSEDED row whose successor is the same
    step — same text, kind, date meaning, date precision and person — is a **date
    change**: later is *edasi lükatud*, any other change of day is *tähtaega
    muudetud*. A successor that differs in anything else is a different action
    taking its place (*asendatud*), and so is a re-save that changed nothing.
    """
    if row.status == ActionStatus.COMPLETED:
        return DONE
    if row.status == ActionStatus.CANCELLED:
        return CANCELLED
    if row.status != ActionStatus.SUPERSEDED:
        return ""
    successor = row.replaced_by
    if successor is None or (
        successor.text,
        successor.kind,
        successor.date_semantics,
        successor.date_precision,
        successor.responsible_id,
    ) != (row.text, row.kind, row.date_semantics, row.date_precision, row.responsible_id):
        return SUPERSEDED
    if successor.target_date == row.target_date:
        return SUPERSEDED
    if (
        row.target_date is not None
        and successor.target_date is not None
        and successor.target_date > row.target_date
    ):
        return POSTPONED
    return DATE_CHANGED


def _ended_rows(ctx: Ctx, span: Any) -> list[NextAction]:
    key = ("ended", span.start, span.end)
    if key not in ctx.memo:
        ctx.memo[key] = list(_ended_in(ctx, span))
    return ctx.memo[key]


def _ended_in(ctx: Ctx, span: Any) -> QuerySet[NextAction]:
    rows = (
        NextAction.objects.visible_to(ctx.viewer)
        .filter(local_day_range("ended_at", span.start, span.end), ended_by__isnull=False)
        .select_related("matter", "replaced_by", "ended_by")
    )
    if ctx.scope == SELF:
        rows = rows.filter(ended_by=ctx.subject)
    return rows.order_by("-ended_at", "pk")


def action_recs(ctx: Ctx, kind: str, span: Any) -> list[Rec]:
    """The records behind one *Muud tegevused* figure, in the scope of ``ctx``."""
    if kind == REVIEWED:
        from app.audit.enums import ChangeEventType
        from app.audit.models import ChangeEvent

        events = ChangeEvent.objects.filter(
            local_day_range("occurred_at", span.start, span.end),
            event_type=ChangeEventType.NEXT_ACTION_REVIEWED,
            matter__in=Matter.objects.visible_to(ctx.viewer).values("pk"),
            actor__isnull=False,
        ).exclude(payload__has_any_keys=["provenance_source", "provenance", "operation"])
        if ctx.scope == SELF:
            events = events.filter(actor=ctx.subject)
        found = list(events.select_related("actor", "matter").order_by("-occurred_at", "pk"))
        steps = {
            a.pk: a
            for a in NextAction.objects.visible_to(ctx.viewer).filter(
                pk__in=[e.object_id for e in found if e.object_id]
            )
        }
        out = []
        for event in found:
            action = steps.get(event.object_id)
            if action is None:
                continue
            moved = event.payload or {}
            out.append(
                Rec(
                    key=f"event:{event.pk}",
                    matter=event.matter,
                    day=timezone.localtime(event.occurred_at).date(),
                    what=action.text,
                    note=f"{moved.get('from') or '—'} → {moved.get('to') or '—'}",
                    actor=_name(event.actor),
                    url=_matter_url(event.matter_id),
                )
            )
        return out
    out = []
    for row in _ended_rows(ctx, span):
        if classify_ended(row) != kind or row.ended_at is None:
            continue
        out.append(
            Rec(
                key=f"action:{row.pk}",
                matter=row.matter,
                day=timezone.localtime(row.ended_at).date(),
                what=row.text,
                note=_change_note(row, kind),
                actor=_name(row.ended_by),
                url=_matter_url(row.matter_id),
            )
        )
    return out


def _change_note(row: NextAction, kind: str) -> str:
    if kind in (POSTPONED, DATE_CHANGED) and row.replaced_by is not None:
        was, now = row.target_date, row.replaced_by.target_date
        before = format_estonian_date(was) if was else "—"
        after = format_estonian_date(now) if now else "—"
        return f"{before} → {after}"
    return ACTION_LABELS.get(kind, "")


def outstanding_recs(ctx: Ctx, items: list[wi.WorkItem]) -> list[Rec]:
    """DO deadlines that are late or fall due by Sunday. A review date is never one."""
    out = []
    for item in items:
        if not item.is_action or item.when is None:
            continue
        if item.action_kind != ActionKind.DO or item.date_semantics != DateSemantics.DEADLINE:
            continue
        if item.is_overdue or item.when <= ctx.week.end:
            out.append(
                Rec(
                    key=f"action:{item.object_id}",
                    matter=item.matter,
                    day=item.when,
                    what=item.text,
                    note="Üle tähtaja" if item.is_overdue else "",
                    url=item.matter_url,
                    owner=_name(item.matter.owner) if ctx.scope == DEPARTMENT else "",
                )
            )
    return out


# ---------------------------------------------------------------------------
# Opinions
# ---------------------------------------------------------------------------


def _opinion_key(fact: Any) -> str:
    """The opinion's own identity: its Submission, answered request or imported outcome."""
    return f"opinion:{fact.record_id or f'{fact.matter.matter_id}:{fact.sent_on}:{fact.source}'}"


def sent_recs(ctx: Ctx, span: Any) -> list[Rec]:
    """Opinions sent in ``span``: one person's by Minu statistika's credit, or all of them."""
    from app.reporting import opinions

    if ctx.facts is None:
        ctx.facts = ctx.data.facts if ctx.data is not None else opinions.opinion_facts(ctx.viewer)
    facts = ctx.facts
    chosen = []
    for fact in facts:
        if not span.start <= fact.sent_on <= span.end:
            continue
        if ctx.scope == SELF and (
            ctx.data is None or credit_opinion(fact, ctx.data.ownership) != ctx.person_id
        ):
            continue
        chosen.append(fact)
    matters = {
        m.pk: m
        for m in Matter.objects.visible_to(ctx.viewer)
        .filter(pk__in={f.matter.matter_id for f in chosen})
        .select_related("owner")
    }
    out = [
        Rec(
            key=_opinion_key(fact),
            matter=matters[fact.matter.matter_id],
            day=fact.sent_on,
            what=fact.matter.title,
            note="Lovable'i teatatud" if fact.source == opinions.SOURCE_IMPORTED else "",
            url=_matter_url(fact.matter.matter_id),
            owner=_name(matters[fact.matter.matter_id].owner) if ctx.scope == DEPARTMENT else "",
        )
        for fact in chosen
        if fact.matter.matter_id in matters
    ]
    return sorted(out, key=lambda r: (r.day or date.min, r.key), reverse=True)


def owed_recs(ctx: Ctx, *, before_week: bool) -> list[Rec]:
    """Open opinion requests (an open `Arvamuse tähtaeg`) that have not been answered.

    A Teema carries at most one open request (`Matter.response_deadline`); earlier
    ones are ended `MatterResponseDeadline` rows. So this is one record per open
    request, identified by Teema and deadline — and a Teema with a draft but no
    recorded request is a Teema-level workload (the strip's «koostamisel»), not a
    request, and is not counted here.
    """
    if "requests" not in ctx.memo:
        rows = drafting_matters(ctx.viewer).filter(
            response_deadline__isnull=False, response_deadline__lte=ctx.week.end
        )
        if ctx.scope == SELF:
            rows = rows.filter(owner=ctx.subject)
        ctx.memo["requests"] = list(
            rows.select_related("owner").order_by("response_deadline", "pk")
        )
    found = [
        m for m in ctx.memo["requests"] if (m.response_deadline < ctx.week.start) == before_week
    ]
    return [
        Rec(
            key=f"request:{m.pk}:{m.response_deadline}",
            matter=m,
            day=m.response_deadline,
            what="Arvamuse tähtaeg",
            url=_matter_url(m.pk),
            owner=_name(m.owner) if ctx.scope == DEPARTMENT else "",
        )
        for m in found
    ]


def new_recs(ctx: Ctx, span: Any) -> list[Rec]:
    """Teemad that arrived in ``span``.

    On a person's desk: the *Uued teemad* figure of Minu statistika (Teemad that
    came into their responsibility, closed ones included). In the department:
    Teemad received in the span, closed ones included.
    """
    if ctx.scope == SELF and ctx.data is not None:
        # The arrival events themselves, for any window: the ownership trail is
        # complete, and the creation events are read for this span by the one
        # definition `my_statistics.created_since`. An arrival is a Teema and a
        # day, so a Teema that returns to the person later is a second arrival.
        key = ("arrivals", span.start)
        if key not in ctx.memo:
            ctx.memo[key] = arrivals_series(
                ctx.data.ownership,
                created_since(ctx.subject, span.start),
                ctx.person_id,
                ctx.reliable.get("trail"),
            )
        found = ctx.memo[key].within(span)
        matters = {
            m.pk: m
            for m in Matter.objects.visible_to(ctx.viewer).filter(
                pk__in=[r.matter_id for r in found]
            )
        }
        return sorted(
            (
                Rec(
                    key=f"arrival:{r.matter_id}:{r.day}",
                    matter=matters[r.matter_id],
                    day=r.day,
                    what=r.what,
                    url=_matter_url(r.matter_id),
                )
                for r in found
                if r.matter_id in matters
            ),
            key=lambda r: (r.day or date.min, r.key),
            reverse=True,
        )
    rows = (
        Matter.objects.visible_to(ctx.viewer)
        .real_data()
        .filter(received_date__gte=span.start, received_date__lte=span.end)
        .select_related("owner")
        .order_by("-received_date", "pk")
    )
    return [
        Rec(
            key=f"matter:{m.pk}",
            matter=m,
            day=m.received_date,
            url=_matter_url(m.pk),
            owner=_name(m.owner),
        )
        for m in rows
    ]


# ---------------------------------------------------------------------------
# Commencements
# ---------------------------------------------------------------------------


def _eff_rows(ctx: Ctx) -> list[EffectiveRow]:
    if ctx.scope == COLLEAGUE:
        return []
    return effective_rows(
        ctx.viewer, ctx.today, owner=None if ctx.scope == DEPARTMENT else ctx.subject
    )


def commencement_buckets(ctx: Ctx, rows: list[EffectiveRow]) -> dict[str, list[EffectiveRow]]:
    """This week's commencements by what is known of them. Precision is never invented."""
    out: dict[str, list[EffectiveRow]] = {ARRIVED: [], UPCOMING: [], PLANNED: []}
    for row in rows:
        if row.start is None or row.end is None:
            continue
        if row.start > ctx.week.end or row.end < ctx.week.start:
            continue
        if row.certainty != CERTAINTY_EXACT:
            if row.certainty == "ligikaudne":
                out[PLANNED].append(row)
        elif row.end <= ctx.today:
            out[ARRIVED].append(row)
        else:
            out[UPCOMING].append(row)
    return out


def commencement_recs(ctx: Ctx, kind: str, rows: list[EffectiveRow] | None = None) -> list[Rec]:
    found = commencement_buckets(ctx, rows if rows is not None else _eff_rows(ctx)).get(kind, [])
    return [
        Rec(
            key=f"effective:{r.record.pk}",
            matter=r.matter,
            day=r.start,
            what=r.what,
            note=f"{r.display_date} · {r.certainty_label}",
            url=r.matter_url,
            owner=_name(r.matter.owner) if ctx.scope == DEPARTMENT else "",
        )
        for r in found
    ]


# ---------------------------------------------------------------------------
# The one resolver the records page and the figures share
# ---------------------------------------------------------------------------


def records(
    ctx: Ctx,
    kind: str,
    span: Any,
    items: list[wi.WorkItem] | None = None,
    rows: list[EffectiveRow] | None = None,
) -> list[Rec]:
    key = ("records", kind, span.start, span.end)
    if key not in ctx.memo:
        ctx.memo[key] = _records(ctx, kind, span, items, rows)
    return ctx.memo[key]


def _records(
    ctx: Ctx,
    kind: str,
    span: Any,
    items: list[wi.WorkItem] | None,
    rows: list[EffectiveRow] | None,
) -> list[Rec]:
    if kind in HISTORY_KINDS:
        return action_recs(ctx, kind, span)
    if kind == SENT:
        return sent_recs(ctx, span)
    if kind == NEW:
        return new_recs(ctx, span)
    if kind == OWED:
        return owed_recs(ctx, before_week=False)
    if kind == LATE:
        return owed_recs(ctx, before_week=True)
    if kind in COMMENCEMENT_KINDS:
        return commencement_recs(ctx, kind, rows)
    if kind == OUTSTANDING:
        if items is None:
            items = wi.work_items(
                ctx.viewer,
                today=ctx.today,
                responsible=None if ctx.scope == DEPARTMENT else ctx.subject,
            )
        return outstanding_recs(ctx, items)
    return []


def records_url(kind: str, week_start: date, *, department: bool = False) -> str:
    params = {"naitaja": kind, "nadal": week_start}
    if department:
        params[SCOPE_PARAM] = DEPARTMENT_VALUE
    return reverse("matters:my_week_records") + "?" + urlencode(params)


def week_start_of(raw: str | None, today: date) -> date:
    try:
        value = date.fromisoformat(raw or "")
    except ValueError:
        return stat_periods.current(today).start
    if value > today or value < stat_periods.FIRST_DAY:
        return stat_periods.current(today).start
    return value - timedelta(days=value.weekday())


def preview_window(today: date) -> stat_periods.Span:
    return _span(max(today - timedelta(days=PREVIEW_DAYS), stat_periods.FIRST_DAY), today)


# ---------------------------------------------------------------------------
# The overview
# ---------------------------------------------------------------------------


def build_week_overview(ctx: Ctx, *, items: list[wi.WorkItem]) -> WeekOverview:
    """Assemble the overview for ``ctx``. ``items`` is the work model the page already read."""
    week, last = ctx.week, ctx.last
    dept = ctx.scope == DEPARTMENT
    overview = WeekOverview(
        scope=ctx.scope,
        start=week.start,
        end=week.end,
        previous_start=last.start,
        previous_end=last.end,
    )
    if ctx.scope == COLLEAGUE:
        _seven_days(ctx, overview, items)
        return overview

    span_now = _span(week.start, week.end)
    span_last = _span(last.start, last.end)
    stats_path = reverse("matters:my_statistics")
    eff_rows = _eff_rows(ctx)

    def figure(
        key: str,
        label: str,
        kind: str,
        span: Any,
        *,
        history: str = "",
        tone: str = "",
        unit: str = "",
    ) -> WeekFigure:
        found = records(ctx, kind, span, items, eff_rows)
        value = (
            cell(span, ctx.reliable.get(history), len(found))
            if history
            else Cell(Coverage.VERIFIED, len(found))
        )
        return WeekFigure(
            key,
            label,
            value,
            records_url(kind, span.start, department=dept),
            tone if value.exact and value.value else "",
            unit,
        )

    # --- Arvamused ---------------------------------------------------------
    if ctx.scope == SELF and ctx.data is not None:
        # Coverage from the same series Minu statistika reads; the list behind it
        # is `sent_recs`, which credits with the same `credit_opinion`.
        sent = WeekFigure(
            "sent",
            "Saadetud",
            ctx.data.series["saadetud"].cell(week.counted),
            records_url(SENT, week.start),
            unit="arvamust",
        )
    else:
        sent = figure("sent", "Saadetud", SENT, span_now, unit="arvamust")
    overview.opinions = [
        sent,
        figure("owed", "Täitmata nõuded sel nädalal", OWED, span_now, unit="nõuet"),
        figure("late", "Varasemast üle tähtaja", LATE, span_now, tone="danger", unit="nõuet"),
    ]

    # --- Muud tegevused ----------------------------------------------------
    overview.actions = [
        figure(
            key,
            ACTION_LABELS[key],
            key,
            span_now,
            history="actions",
            unit=ACTION_UNITS.get(key, "tegevust"),
        )
        for key in HISTORY_KINDS
    ]
    overview.actions.append(
        figure(
            OUTSTANDING,
            ACTION_LABELS[OUTSTANDING],
            OUTSTANDING,
            span_now,
            tone="danger",
            unit="tegevust",
        )
    )

    # --- Jõustumised -------------------------------------------------------
    buckets = commencement_buckets(ctx, eff_rows)
    for key, label in (
        (ARRIVED, "Kuupäev saabunud"),
        (UPCOMING, "Eesolevad"),
        (PLANNED, "Ligikaudsed"),
    ):
        overview.commencements.append(
            WeekFigure(
                key,
                label,
                Cell(Coverage.VERIFIED, len(buckets[key])),
                records_url(key, week.start, department=dept),
                unit="jõustumist",
            )
        )
    overview.upcoming_commencements = [
        r for r in eff_rows if r.start is not None and r.end is not None and r.end >= ctx.today
    ][:PREVIEW]
    overview.commencements_url = reverse("matters:my_effective_dates") if not dept else ""

    # --- Eelmine nädal -----------------------------------------------------
    if ctx.scope == SELF and ctx.data is not None:
        series = ctx.data.series
        overview.previous = [
            WeekFigure(
                "p-sent",
                "Saadetud arvamused",
                series["saadetud"].cell(last.counted),
                records_url(SENT, last.start),
                unit="arvamust",
            ),
            WeekFigure(
                "p-new",
                "Uued teemad",
                series["uued"].cell(last.counted),
                records_url(NEW, last.start),
                unit="teemat",
            ),
            WeekFigure(
                "p-closed",
                "Lõpetatud teemad",
                series["lopetatud"].cell(last.counted),
                last.url(stats_path, naitaja="lopetatud"),
                unit="teemat",
            ),
            figure(
                "p-done", "Tehtud tegevused", DONE, span_last, history="actions", unit="tegevust"
            ),
        ]
        overview.statistics_url = week.url(stats_path)
        overview.previous_statistics_url = last.url(stats_path)
        for key, label in _MONITORING:
            found = series[key].cell(week.counted) if key in series else None
            if found is not None and found.value:
                overview.monitoring.append(
                    WeekFigure(key, label, found, week.url(stats_path, naitaja=key))
                )
    else:
        overview.previous = [
            figure("p-sent", "Saadetud arvamused", SENT, span_last, unit="arvamust"),
            figure("p-new", "Uued teemad", NEW, span_last, unit="teemat"),
            figure(
                "p-done", "Tehtud tegevused", DONE, span_last, history="actions", unit="tegevust"
            ),
        ]

    # --- 7 päeva -----------------------------------------------------------
    _seven_days(ctx, overview, items)

    # --- Previews: the same populations as the «Vaata kõiki» pages -----------
    window = preview_window(ctx.today)
    for attr, kind in (("recent_opinions", SENT), ("new_matters", NEW)):
        previews = [
            PreviewRow(r.matter, r.day, r.note, r.url, owner=r.owner)
            for r in records(ctx, kind, window)[:PREVIEW]
            if r.matter is not None
        ]
        setattr(overview, attr, previews)
        params = {"naitaja": kind, "alates": window.start}
        if dept:
            params[SCOPE_PARAM] = DEPARTMENT_VALUE
        setattr(
            overview,
            f"{attr}_url",
            reverse("matters:my_week_records") + "?" + urlencode(params),
        )
    return overview


def _seven_days(ctx: Ctx, overview: WeekOverview, items: list[wi.WorkItem]) -> None:
    horizon = ctx.today + timedelta(days=HORIZON_DAYS - 1)
    rows = deadline_rows(
        ctx.viewer,
        ctx.today,
        responsible=None if ctx.scope == DEPARTMENT else ctx.subject,
        items=items,
    )
    out = []
    for drow in rows:
        if drow.due > horizon:
            continue
        item = drow.item
        review = item.action_kind in REVIEW_KINDS or (
            item.is_action and item.date_semantics == DateSemantics.REVIEW_ON
        )
        late = bool(item.is_overdue) and not review
        if drow.due < ctx.today and not late:
            continue  # a passed review date is a review, not a past obligation
        out.append(
            SevenDayRow(
                when=drow.due,
                relative=drow.relative,
                matter=drow.matter,
                what=drow.what or drow.category_label,
                category=drow.category_label,
                url=drow.matter_url,
                status="Üle tähtaja" if late else ("Ülevaatus" if review else ""),
                is_review=review,
                is_late=late,
                owner=_name(drow.matter.owner) if ctx.scope == DEPARTMENT else "",
            )
        )
    out.sort(key=lambda r: (not r.is_late, r.when, str(r.matter.title)))
    overview.seven_days = out
    overview.seven_days_url = reverse("matters:my_deadlines") if ctx.scope == SELF else ""


__all__ = [
    "ACTION_LABELS",
    "COLLEAGUE",
    "DEPARTMENT",
    "SELF",
    "Ctx",
    "WeekOverview",
    "build_week_overview",
    "classify_ended",
    "make_ctx",
    "records",
    "records_url",
    "week_start_of",
]
