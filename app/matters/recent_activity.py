"""*Viimased tegevused* — what happened lately on one person's desk (Minu asjad).

One chronological list, newest first, that replaced two older blocks: the rail's
*Viimati muudetud* (one row per Matter, its latest activity) and the main
column's *Minu viimased sissekanded* (the person's own entries). Neither
answered the question a lawyer back from a day out actually asks — *what moved
on my files, and what did I write elsewhere?* — and between them they told
half of it twice.

Who is in it
------------
``subject`` is whose desk this is; ``user`` reads it. Two populations, unioned:

1. **Substantive work on the Matters the subject currently owns**, whoever did
   it — a colleague's step on my file is exactly what I need to see.
2. **Entries the subject wrote on Matters somebody else owns** — the old
   *Minu viimased sissekanded*, which is the way back to «where did I write
   that down?».

An entry the subject wrote on their own Matter satisfies both and is read once:
both halves are one ``OR`` on one query.

What counts
-----------
**The selection is `app.matters.activity`'s, not the audit log's.** The same
record types *Viimane tegevus* reads, scoped and filtered by the very querysets
it builds (`activity.work_sources`), each on its own business date — so this
list cannot call something work that the register column does not, and an
administrative write (a title edit, an owner change, a document upload) or a
machine write (an import, an enrichment, a step an importer set) is no more an
activity here than there. ``Matter.updated_at`` is never read: it is a
statement about the row, not about a person (activity.py, «Four questions that
are not this one»). Unlike *Viimane tegevus*, there is no ``NATIVE_RECORD``
fallback either: that prints something true about a file with nothing else
known, and is not an activity.

Several distinct activities on one Matter are several rows. *Viimati muudetud*
showed only the latest per Matter, which hid a colleague's step under the
subject's own note written an hour later.

**One act, one row.** A composer save can write an entry, finish a step and set
the next one, which is three records and three audit rows. The records are
grouped on the ``operation_id`` their `ChangeEvent` rows share
(`app.audit.operations`) and printed as one row naming what the save did. A
record with no operation stands alone, as it does in `Teema käik`. A step
change the system made on a person's behalf — the follow-up check a send
schedules, a check put back on the plan, a step a closure ended — is the
consequence of another row, not an act of its own, and is left out on the
same markers Osakond's *Muudatused* reads, which the workflow services write
and `app.workflow.step_markers` names (`system_step_change_q`). A replaced
step's ``SUPERSEDED`` end is the replacement's «muudetud», never a row of its
own.

Who did it
----------
The actor is the person the record itself names — an entry's author, a step's
``created_by`` / ``ended_by``, a send's ``sent_by``, a closure's
``closed_by``, the person who recorded a `Kaasamine`, a `Märge`, a `Väline
seisukoht` or published an `Ülevaade`. It is printed only when it is somebody
other than the subject: on my own desk «Ireen · Järgmine tegevus muudetud» says
something, «Kaur · Sissekanne lisatud» does not. An imported or synchronised
record has no actor and gets none — an arrival date, a OneNote page's own
timestamps and anything an importer wrote are printed with their source label
and no name (`BASIS_LABELS`). Nobody is invented.

Authorization
-------------
Every population starts at ``full_matters(user)`` — `visible_to` on the Matter —
and every child record is read through `_readable`, the child's own visibility
rule, so a step restricted below a Matter the reader can open produces no row,
no count and no actor name. An entry the subject wrote on a Matter this reader
may not open is not here either: authorship is not authorization.

Bounded
-------
One capped query per source — :data:`FEED_LIMIT` newest of each — plus one
query for the audit rows that group them. The merged list is cut to the same
cap, which the page says out loud rather than offering a total it never read.
"""

from __future__ import annotations

from dataclasses import dataclass, field, replace
from datetime import date, datetime, time, timedelta
from typing import Any

from django.db.models import BooleanField, Case, F, Q, Value, When
from django.db.models.functions import Coalesce, Greatest, TruncDate
from django.utils import timezone

from app.audit.enums import ChangeEventType
from app.audit.models import ChangeEvent
from app.core.dates import format_estonian_date, start_of_local_day
from app.matters import work_items as wi
from app.matters.activity import BASIS_LABELS, ActivityBasis, work_sources
from app.matters.models import Matter
from app.matters.timeline import DAY_DATED_ENTRY_KINDS
from app.workflow.dates import format_at_precision, is_approximate
from app.workflow.enums import ActionStatus, DatePrecision
from app.workflow.step_markers import system_step_change_q

#: How many rows are on screen before the rest go behind «Näita veel N ▾».
FEED_VISIBLE = 10

#: How many rows the page reads at all, per source and merged. Past this the
#: page says it shows the latest fifty; the Matters themselves hold the rest.
FEED_LIMIT = 50

#: Which audit rows speak for which record, so a record can be tied to the
#: operation it was written in and to the markers that make a step change
#: automatic. Matched on ``ChangeEvent.object_id``, which every one of these
#: services records as the record's own id (`audit.services.record_change_event`).
_ENTRY = "entry"
_SUBMISSION = "submission"
_ACTION_SET = "action_set"
_ACTION_ENDED = "action_ended"
_ENGAGEMENT = "engagement"
_DEVELOPMENT = "development"
_OVERVIEW = "overview"
_POSITION = "position"
_CLOSURE = "closure"

_EVENTS_OF: dict[str, tuple[str, ...]] = {
    _ENTRY: (ChangeEventType.ENTRY_ADDED,),
    _SUBMISSION: (ChangeEventType.SUBMISSION_SENT,),
    _ACTION_SET: (ChangeEventType.NEXT_ACTION_SET,),
    _ACTION_ENDED: (ChangeEventType.NEXT_ACTION_COMPLETED, ChangeEventType.NEXT_ACTION_CANCELLED),
    _ENGAGEMENT: (ChangeEventType.ENGAGEMENT_ADDED,),
    _DEVELOPMENT: (ChangeEventType.PROCEDURAL_DEVELOPMENT_RECORDED,),
    _OVERVIEW: (ChangeEventType.WEBSITE_OVERVIEW_PUBLISHED,),
    _POSITION: (ChangeEventType.EXTERNAL_POSITION_RECORDED,),
    _CLOSURE: (ChangeEventType.MATTER_CLOSED,),
}

#: The step's own words in this list. `BASIS_LABELS` has one word for every
#: step change («Järgmiseks muudetud») because *Viimane tegevus* names the latest
#: fact only; a list of several says which change it was.
ACTION_SET_LABEL = "Järgmine tegevus määratud"
ACTION_REPLACED_LABEL = "Järgmine tegevus muudetud"
ACTION_DONE_LABEL = "Järgmine tegevus tehtud"
ACTION_CANCELLED_LABEL = "Järgmine tegevus tühistatud"
ENTRY_LABEL = "Sissekanne lisatud"


@dataclass(frozen=True)
class _Fact:
    """One record that counts, before it is grouped into a row."""

    matter: Matter
    #: Where it sorts. A moment for a timestamped record; the end of its day
    #: for a dated one, so a dated fact sits above the work written that day
    #: — the reading order `Teema käik` uses (`timeline._end_of_day`).
    moment: datetime
    day: date
    label: str
    actor: Any | None = None
    precision: str = DatePrecision.EXACT.value
    #: Whether ``moment`` carries a real time of day a person could read.
    timed: bool = False
    family: str = ""
    object_id: Any = None


@dataclass(frozen=True)
class ActivityRow:
    """One line of *Viimased tegevused*: which Teema, when, who, and what."""

    matter: Matter
    moment: datetime
    #: How the date reads in the row: `28.10`, `täna 14:05`, `oktoober 2026`.
    when: str
    #: The ISO day for the `<time>` element.
    day: date
    label: str
    #: The person, when it was somebody other than the desk's owner; else ``None``.
    actor: Any | None = None


@dataclass
class RecentActivity:
    rows: list[ActivityRow] = field(default_factory=list)
    #: Whether the read hit :data:`FEED_LIMIT`, so the page says it shows the
    #: latest N rather than implying it holds everything.
    at_cap: bool = False

    @property
    def preview(self) -> list[ActivityRow]:
        return self.rows[:FEED_VISIBLE]

    @property
    def rest(self) -> list[ActivityRow]:
        return self.rows[FEED_VISIBLE:]

    @property
    def remaining(self) -> int:
        return len(self.rest)

    @property
    def limit(self) -> int:
        return FEED_LIMIT


def _end_of_day(day: date) -> datetime:
    return timezone.make_aware(datetime.combine(day, time.max))


def _local(moment: datetime) -> datetime:
    return timezone.localtime(moment) if timezone.is_aware(moment) else moment


def _moment_fact(
    matter: Matter, moment: datetime, label: str, *, timed: bool = True, **extra: Any
) -> _Fact:
    local = _local(moment)
    return _Fact(matter=matter, moment=moment, day=local.date(), label=label, timed=timed, **extra)


def _dated_fact(
    matter: Matter,
    day: date,
    label: str,
    precision: str = DatePrecision.EXACT.value,
    **extra: Any,
) -> _Fact:
    return _Fact(
        matter=matter,
        moment=_end_of_day(day),
        day=day,
        label=label,
        precision=precision or DatePrecision.EXACT.value,
        **extra,
    )


def when_label(fact_day: date, *, today: date, moment: datetime | None, precision: str) -> str:
    """The date as the row prints it.

    A period is spelled as a period — *oktoober 2026*, never `1.10`, which would
    name a day nobody named (docs/adr/0079 §3). A day reads `28.10`, with the
    year only when it is not this one. Today reads «täna», and the hour joins it
    only where the record holds a real one: an hour is worth reading for what
    happened this morning, and noise for last month.
    """
    if is_approximate(precision):
        return format_at_precision(fact_day, precision)
    if fact_day == today:
        if moment is not None:
            return f"täna {_local(moment):%H:%M}"
        return "täna"
    if fact_day.year == today.year:
        return f"{fact_day.day}.{fact_day.month}"
    return format_estonian_date(fact_day)


def _facts(user: Any, subject: Any, today: date) -> list[_Fact]:
    """Every source, each capped and newest first. One query per source."""
    sources = work_sources(user, today)
    visible = wi.full_matters(user)
    owned = visible.filter(owner=subject).values("pk")
    tomorrow = start_of_local_day(today + timedelta(days=1))
    limit = FEED_LIMIT
    facts: list[_Fact] = []

    # Entries: on the subject's Matters, or written by the subject on anybody's
    # Matter this reader may open. One query, so one entry is one fact.
    entries = (
        sources.entries.filter(
            Q(matter__in=owned) | Q(author=subject, matter__in=visible.values("pk")),
            occurred_at__lt=tomorrow,
        )
        .select_related("matter", "author")
        .order_by("-occurred_at", "-pk")[:limit]
    )
    for entry in entries:
        local = _local(entry.occurred_at)
        day_dated = entry.kind in DAY_DATED_ENTRY_KINDS and local.time() == time.min
        if day_dated:
            facts.append(
                _dated_fact(
                    entry.matter,
                    local.date(),
                    ENTRY_LABEL,
                    actor=entry.author,
                    family=_ENTRY,
                    object_id=entry.pk,
                )
            )
        else:
            facts.append(
                _moment_fact(
                    entry.matter,
                    entry.occurred_at,
                    ENTRY_LABEL,
                    actor=entry.author,
                    family=_ENTRY,
                    object_id=entry.pk,
                )
            )

    for submission in (
        sources.submissions.filter(matter__in=owned, sent_at__lt=tomorrow)
        .select_related("matter", "sent_by")
        .order_by("-sent_at", "-pk")[:limit]
    ):
        facts.append(
            _moment_fact(
                submission.matter,
                submission.sent_at,
                BASIS_LABELS[ActivityBasis.SUBMISSION],
                actor=submission.sent_by,
                family=_SUBMISSION,
                object_id=submission.pk,
            )
        )

    # A step set by a person, and a step a person finished or called off: one
    # read, ordered on the later of the two moments that count, so the newest
    # fifty of either are in it. A superseded step ended because its
    # replacement was set, and that set is the act («Järgmine tegevus
    # muudetud»); its end is not a second one.
    set_counts = Q(created_by__isnull=False, created_at__lt=tomorrow)
    end_counts = (
        Q(ended_by__isnull=False, ended_at__lt=tomorrow)
        & ~Q(status=ActionStatus.SUPERSEDED)
        & ~Q(ended_at=None)
    )
    actions = (
        (sources.actions_set | sources.actions_ended)
        .filter(matter__in=owned)
        .filter(set_counts | end_counts)
        .annotate(
            counted_set=Case(When(set_counts, then=F("created_at")), default=None),
            counted_end=Case(When(end_counts, then=F("ended_at")), default=None),
        )
        .select_related("matter", "created_by", "ended_by")
        .order_by(Greatest("counted_set", "counted_end").desc(nulls_last=True), "-pk")[:limit]
    )
    for action in actions:
        if action.counted_set is not None:
            facts.append(
                _moment_fact(
                    action.matter,
                    action.counted_set,
                    ACTION_SET_LABEL,
                    actor=action.created_by,
                    family=_ACTION_SET,
                    object_id=action.pk,
                )
            )
        if action.counted_end is not None:
            label = (
                ACTION_DONE_LABEL
                if action.status == ActionStatus.COMPLETED
                else ACTION_CANCELLED_LABEL
            )
            facts.append(
                _moment_fact(
                    action.matter,
                    action.counted_end,
                    label,
                    actor=action.ended_by,
                    family=_ACTION_ENDED,
                    object_id=action.pk,
                )
            )

    # The three records that may be dated to a period, each on its own
    # business date at its own precision (docs/adr/0079, 0121, 0122). The
    # actor is the person who recorded it.
    for engagement in (
        sources.engagements.filter(matter__in=owned)
        .select_related("matter", "created_by")
        .order_by("-occurred_on", "-created_at")[:limit]
    ):
        facts.append(
            _dated_fact(
                engagement.matter,
                engagement.occurred_on,
                BASIS_LABELS[ActivityBasis.ENGAGEMENT],
                engagement.occurred_on_precision,
                actor=engagement.created_by,
                family=_ENGAGEMENT,
                object_id=engagement.pk,
            )
        )
    for development in (
        sources.developments.filter(matter__in=owned)
        .select_related("matter", "created_by")
        .order_by("-occurred_on", "-created_at")[:limit]
    ):
        facts.append(
            _dated_fact(
                development.matter,
                development.occurred_on,
                BASIS_LABELS[ActivityBasis.DEVELOPMENT],
                development.occurred_on_precision,
                actor=development.created_by,
                family=_DEVELOPMENT,
                object_id=development.pk,
            )
        )
    for position in (
        sources.positions.filter(matter__in=owned)
        .select_related("matter", "created_by")
        .order_by("-stated_on", "-created_at")[:limit]
    ):
        facts.append(
            _dated_fact(
                position.matter,
                position.stated_on,
                BASIS_LABELS[ActivityBasis.EXTERNAL_POSITION],
                position.stated_on_precision,
                actor=position.created_by,
                family=_POSITION,
                object_id=position.pk,
            )
        )
    for overview in (
        sources.overviews.filter(matter__in=owned)
        .select_related("matter", "published_by")
        .order_by("-published_on", "-created_at")[:limit]
    ):
        facts.append(
            _dated_fact(
                overview.matter,
                overview.published_on,
                BASIS_LABELS[ActivityBasis.WEBSITE_OVERVIEW],
                actor=overview.published_by,
                family=_OVERVIEW,
                object_id=overview.pk,
            )
        )

    # The Matter's own two facts. A closure names who closed it, when anybody
    # did; an arrival date is the source's and has no person behind it.
    # One read for both, ordered on the later of the two.
    closed = Q(closed_at__isnull=False, closed_at__lt=tomorrow)
    received = Q(received_date__isnull=False, received_date__lte=today)
    own_facts = (
        visible.filter(owner=subject)
        .filter(closed | received)
        .annotate(
            counted_closed=Case(When(closed, then=TruncDate("closed_at")), default=None),
            counted_received=Case(When(received, then=F("received_date")), default=None),
        )
        .select_related("closed_by")
        .order_by(Greatest("counted_closed", "counted_received").desc(nulls_last=True), "-pk")[
            :limit
        ]
    )
    for matter in own_facts:
        if matter.counted_closed is not None and matter.closed_at is not None:
            facts.append(
                _moment_fact(
                    matter,
                    matter.closed_at,
                    BASIS_LABELS[ActivityBasis.CLOSURE],
                    actor=matter.closed_by,
                    family=_CLOSURE,
                    object_id=matter.pk,
                )
            )
        if matter.counted_received is not None and matter.received_date is not None:
            facts.append(
                _dated_fact(matter, matter.received_date, BASIS_LABELS[ActivityBasis.RECEIVED])
            )

    # Archived OneNote pages: source metadata, never a person. A page's
    # modification is the later and better fact; its creation is a fact of its
    # own only on a different day.
    pages = (
        sources.pages.filter(matter__in=owned)
        .filter(
            Q(source_page__source_modified_at__lt=tomorrow)
            | Q(source_page__source_created_at__lt=tomorrow)
        )
        .select_related("matter", "source_page")
        .order_by(
            Coalesce("source_page__source_modified_at", "source_page__source_created_at").desc(
                nulls_last=True
            ),
            "-pk",
        )[:limit]
    )
    for link in pages:
        page = link.source_page
        modified = page.source_modified_at if page.source_modified_at else None
        created = page.source_created_at if page.source_created_at else None
        if modified is not None and modified < tomorrow:
            facts.append(
                _moment_fact(
                    link.matter,
                    modified,
                    BASIS_LABELS[ActivityBasis.ONENOTE_MODIFIED],
                    timed=False,
                )
            )
        if created is not None and created < tomorrow:
            if modified is None or _local(created).date() != _local(modified).date():
                facts.append(
                    _moment_fact(
                        link.matter,
                        created,
                        BASIS_LABELS[ActivityBasis.ONENOTE_CREATED],
                        timed=False,
                    )
                )
    return facts


@dataclass(frozen=True)
class _EventFacts:
    operation_id: Any = None
    automatic: bool = False
    replaced: bool = False


def _event_facts(facts: list[_Fact]) -> dict[tuple[str, Any], _EventFacts]:
    """The audit row behind each record: its operation and whether it was automatic.

    One query over the audit rows of the records already selected — every one
    of them is inside the reader's boundary, so nothing is read about anything
    else. Only three things are taken from each row, and nothing it says is
    printed.
    """
    ids = {fact.object_id for fact in facts if fact.family}
    if not ids:
        return {}
    # Narrowed by Matter as well: `object_id` carries no index of its own, and
    # `(matter, occurred_at)` does (`audit_change_matter_time`), so the read is
    # bounded by the Matters on this list rather than by the audit table.
    matters = {fact.matter.pk for fact in facts if fact.family}
    family_of = {event_type: family for family, types in _EVENTS_OF.items() for event_type in types}
    rows = (
        ChangeEvent.objects.filter(
            matter_id__in=matters, object_id__in=ids, event_type__in=list(family_of)
        )
        .annotate(
            is_automatic=Case(
                When(system_step_change_q(), then=Value(True)),
                default=Value(False),
                output_field=BooleanField(),
            )
        )
        .order_by("occurred_at", "created_at")
        .values_list("object_id", "event_type", "operation_id", "is_automatic", "payload")
    )
    found: dict[tuple[str, Any], _EventFacts] = {}
    for object_id, event_type, operation_id, automatic, payload in rows:
        family = family_of[event_type]
        key = (family, object_id)
        # A step's *set* is its first `NEXT_ACTION_SET`: a later one on the
        # same row is a planned step being promoted or put back, which is the
        # system's. Its *end* is its last completion or cancellation.
        if family == _ACTION_SET and key in found:
            continue
        found[key] = _EventFacts(
            operation_id=operation_id,
            automatic=bool(automatic),
            replaced=bool((payload or {}).get("replaced")),
        )
    return found


def _joined(labels: list[str]) -> str:
    """«Sissekanne lisatud, järgmine tegevus tehtud» — one sentence for one act."""
    unique = list(dict.fromkeys(labels))
    head, tail = unique[0], unique[1:]
    rest = [label[:1].lower() + label[1:] if label[1:2].islower() else label for label in tail]
    return ", ".join([head, *rest])


def build_recent_activity(user: Any, subject: Any, today: date | None = None) -> RecentActivity:
    """*Viimased tegevused* for ``subject``'s desk, as ``user`` may see it."""
    today = today or timezone.localdate()
    facts = _facts(user, subject, today)
    events = _event_facts(facts)

    kept: list[_Fact] = []
    for fact in facts:
        event = events.get((fact.family, fact.object_id)) if fact.family else None
        if event is not None and event.automatic:
            continue
        if event is not None and event.replaced and fact.family == _ACTION_SET:
            fact = replace(fact, label=ACTION_REPLACED_LABEL)
        kept.append(fact)

    # One act, one row: the facts of an operation on one Matter become one row,
    # dated at the latest of them and described by all of them, in the order
    # the act would be told.
    grouped: dict[Any, list[_Fact]] = {}
    order: list[Any] = []
    for fact in kept:
        event = events.get((fact.family, fact.object_id)) if fact.family else None
        key: Any
        if event is not None and event.operation_id is not None:
            key = ("op", fact.matter.pk, event.operation_id)
        else:
            key = ("one", id(fact))
        if key not in grouped:
            grouped[key] = []
            order.append(key)
        grouped[key].append(fact)

    rows: list[ActivityRow] = []
    for key in order:
        members = sorted(grouped[key], key=lambda fact: fact.moment)
        latest = members[-1]
        actor = next((fact.actor for fact in members if fact.actor is not None), None)
        if actor is not None and getattr(actor, "pk", None) == getattr(subject, "pk", object()):
            actor = None
        rows.append(
            ActivityRow(
                matter=latest.matter,
                moment=latest.moment,
                when=when_label(
                    latest.day,
                    today=today,
                    moment=latest.moment if latest.timed else None,
                    precision=latest.precision,
                ),
                day=latest.day,
                label=_joined([fact.label for fact in members]),
                actor=actor,
            )
        )

    rows.sort(key=lambda row: (row.moment, row.matter.title), reverse=True)
    return RecentActivity(rows=rows[:FEED_LIMIT], at_cap=len(rows) >= FEED_LIMIT)
