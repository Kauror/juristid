"""Minu statistika — one person's own figures, each credited by a stated rule (FUNC-003).

docs/adr/0223. A page of the signed-in person's own figures, reached from Minu
asjad, beside the «Minu statistika» foldout it extends (`my_work.person_stats`,
`opinion_work`, unchanged). Nobody else's figures are read or shown here, and
nothing is ranked, rated or compared.

Every figure says how it is credited, because the three answers differ:

* **Current responsibility** — the Teema's owner *now* (`Matter.owner`): open
  Teemad, opinions in preparation, opinions due this week. The same
  populations as the foldout, Statistika and Osakond.
* **What you did** — the person a record names as having done it: an entry's
  author, the person who closed a Teema (`MATTER_CLOSED`), published an
  overview (`WEBSITE_OVERVIEW_PUBLISHED`) or added a consultation
  (`ENGAGEMENT_ADDED`), the person who recorded an opinion as sent
  (`Submission.sent_by`, or who ended a request as answered).
* **Responsibility at the time** — the Teema's owner *at the end of that day*,
  rebuilt from Juristid's own trail: the owner when the Teema was created in
  Juristid, then every `MATTER_ASSIGNED` change in order (its stable owner ids
  when the event carries them, else its names, resolved conservatively). An
  imported «välja saadetud» names nobody who sent it, so it is credited to the
  owner at the time.

**Coverage is a stated state, never a guess** (ADR 0223 §4, corrections
25.11.2026). Each monthly value is a :class:`Cell` with one of four states:

* ``VERIFIED`` — the whole month was recorded and every record in it can be
  credited: the count is the month's, and a 0 is a real zero;
* ``PARTIAL`` — recording covers only part of the month (it began inside the
  month, or the month is still running): the count is what was recorded, shown
  with «osaline» and the covered days, never as a whole month;
* ``AT_LEAST`` — records were found, but the month is not fully covered or some
  record in it cannot be credited: a lower bound, shown as «vähemalt N»;
* ``UNKNOWN`` — nothing reliable: «andmed puuduvad».

When recording began is read from evidence, not from the earliest event seen:
the time the migration that created each record's table or event type was
applied to this database (`RECORDED_BY`, `django_migrations.applied`) — the
first moment Juristid could write such a record at all. An empty set of
records before that moment proves nothing and stays unknown.
"""

from __future__ import annotations

import calendar
from collections import Counter
from dataclasses import dataclass, field
from datetime import date, datetime
from enum import StrEnum
from typing import Any

from django.utils import timezone

#: The owner, or the open/closed state, on a day nobody can say.
UNKNOWN = "?"

#: How many calendar months the page shows, the current one included.
MONTHS = 12

#: Credit rules, as the page names them.
CURRENT = "praegune vastutaja"
ACTOR = "sinu tehtud"
AT_THE_TIME = "vastutaja tol ajal"

#: The migrations whose application on this database is the first moment
#: Juristid could write the records each figure reads (ADR 0223 §4). A figure
#: reading two kinds of record is covered from the later of the two.
RECORDED_BY: dict[str, tuple[tuple[str, str], ...]] = {
    # Submission.sent_by, and a request ended as answered (`ended_by`).
    "sent": (("submissions", "0001_initial"), ("matters", "0045_response_deadline_history")),
    "entries": (("matters", "0003_entry"),),
    "closed": (("audit", "0001_initial"),),
    "overviews": (("audit", "0018_website_overview_events"),),
    "engagements": (("audit", "0012_matter_engagement"),),
    # MATTER_ASSIGNED, MATTER_CLOSED and MATTER_REOPENED: the ownership and
    # open/closed trail.
    "trail": (("audit", "0001_initial"),),
}

#: Events that open a Teema, close it on their own day, or retire it at a
#: cut-over whose real stopping day is unknown (`app.audit.enums`).
OPENING = frozenset({"MATTER_REOPENED", "MATTER_PROMOTED", "MATTER_REGISTER_CUTOVER_ACTIVATED"})
CLOSING = frozenset({"MATTER_CLOSED"})
CLOSING_UNDATED = frozenset({"MATTER_HISTORICAL_CUTOVER_CLOSED", "MATTER_REGISTER_CUTOVER_RETIRED"})
STATE_EVENTS = OPENING | CLOSING | CLOSING_UNDATED


def _day(moment: datetime | date | None) -> date | None:
    if moment is None:
        return None
    if isinstance(moment, datetime):
        return timezone.localdate(moment) if timezone.is_aware(moment) else moment.date()
    return moment


def _id(value: Any) -> Any:
    return None if value in (None, "") else str(value)


class Coverage(StrEnum):
    VERIFIED = "verified"
    PARTIAL = "partial"
    AT_LEAST = "at_least"
    UNKNOWN = "unknown"


@dataclass(frozen=True)
class Cell:
    """One monthly value and how much of the month stands behind it."""

    coverage: Coverage
    value: int | None = None
    #: PARTIAL: the days the count covers.
    covered_from: date | None = None
    covered_to: date | None = None

    @property
    def known(self) -> bool:
        return self.coverage is not Coverage.UNKNOWN

    @property
    def text(self) -> str:
        if self.coverage is Coverage.UNKNOWN:
            return "andmed puuduvad"
        if self.coverage is Coverage.AT_LEAST:
            return f"vähemalt {self.value}"
        return str(self.value)

    @property
    def note(self) -> str:
        """What the page prints under the value: which part of the month it covers."""
        if self.coverage is Coverage.AT_LEAST:
            return "ajalugu poolik"
        if self.coverage is not Coverage.PARTIAL or not self.covered_from or not self.covered_to:
            return ""
        began = self.covered_from.day != 1
        last = calendar.monthrange(self.covered_to.year, self.covered_to.month)[1]
        running = self.covered_to.day != last
        start, end = f"{self.covered_from:%d.%m.%Y}", f"{self.covered_to:%d.%m.%Y}"
        if began and running:
            return f"osaline: {start}–{end}"
        if began:
            return f"osaline: alates {start}"
        return f"osaline: kuni {end}"


def cell(month: Month, since: date | None, found: int, *, uncertain: bool = False) -> Cell:
    """The state of a count of ``found`` records in ``month``, recorded from ``since``.

    ``uncertain``: some record in the month could not be credited.
    """
    if since is None or since > month.end or uncertain:
        return Cell(Coverage.AT_LEAST, found) if found else Cell(Coverage.UNKNOWN)
    covered_from = max(since, month.start)
    if covered_from > month.start or not month.complete:
        return Cell(Coverage.PARTIAL, found, covered_from, month.end)
    return Cell(Coverage.VERIFIED, found)


@dataclass
class Ownership:
    """Who owned each Teema when, and whether it was open, as far as the trail can say."""

    #: Per Teema: (first day, owner id | None | UNKNOWN), oldest first.
    segments: dict[Any, list[tuple[date, Any]]] = field(default_factory=dict)
    #: Per Teema: (first day, True | False | UNKNOWN), oldest first.
    states: dict[Any, list[tuple[date, Any]]] = field(default_factory=dict)
    #: Per Teema: the first day it existed at all (arrival, else its year, else creation).
    exists_from: dict[Any, date] = field(default_factory=dict)
    #: The first day the trail was recorded at all; before it no set of Teemad is complete.
    since: date | None = None

    @staticmethod
    def _at(segments: list[tuple[date, Any]] | None, day: date) -> Any:
        # End of day: the last change made on ``day`` decides it.
        if not segments or day < segments[0][0]:
            return UNKNOWN
        found: Any = UNKNOWN
        for start, value in segments:
            if start > day:
                break
            found = value
        return found

    def owner_at(self, matter_id: Any, day: date) -> Any:
        """The owner's id at the end of ``day``; ``None`` for nobody, ``UNKNOWN`` if unsaid."""
        return self._at(self.segments.get(matter_id), day)

    def open_at(self, matter_id: Any, day: date) -> Any:
        """``True``/``False`` at the end of ``day``, ``UNKNOWN`` where the trail is silent."""
        return self._at(self.states.get(matter_id), day)


@dataclass(frozen=True)
class Person:
    """A user as the name fallback needs it: the id and when the account existed."""

    pk: str
    joined: datetime


def _resolver(names: dict[str, list[Person]]) -> Any:
    def resolve(payload: dict[str, Any], side: str, at: datetime | None) -> Any:
        key = "previous_owner_id" if side == "from" else "new_owner_id"
        if key in payload:
            # Written since the corrections round: the id is the person.
            return _id(payload[key])
        name = payload.get(f"{side}_name")
        if name in (None, ""):
            return None
        # An older event holds only a name. It is somebody only when exactly
        # one account carrying that name *today* already existed then: a name a
        # renamed person has left, or one a later account took, is nobody known.
        existed = [p for p in names.get(str(name), []) if at is None or p.joined <= at]
        return existed[0].pk if len(existed) == 1 else UNKNOWN

    return resolve


def build_ownership(
    rows: list[dict[str, Any]],
    events: list[dict[str, Any]],
    names: dict[str, list[Person]],
    today: date,
    *,
    trail_since: date | None,
) -> Ownership:
    """Pure: Teemad, their trail events and people → `Ownership`.

    ``rows`` carry ``pk``, ``owner_id``, ``created_at``, ``received_date``,
    ``reference_year``, ``is_open`` and ``closed_at``; ``events`` carry
    ``matter_id``, ``event_type``, ``occurred_at`` and ``payload``, oldest first
    (``MATTER_ASSIGNED`` and the open/closed events of `STATE_EVENTS`).
    ``trail_since`` is the first day the trail was recorded at all.
    """
    resolve = _resolver(names)
    assigned: dict[Any, list[dict[str, Any]]] = {}
    state_events: dict[Any, list[dict[str, Any]]] = {}
    for event in events:
        kind = event.get("event_type", "MATTER_ASSIGNED")
        target = state_events if kind in STATE_EVENTS else assigned
        target.setdefault(event["matter_id"], []).append(event)
    out = Ownership(since=trail_since)
    for row in rows:
        pk = row["pk"]
        created = _day(row["created_at"]) or today
        received = row["received_date"]
        year = row["reference_year"]
        out.exists_from[pk] = min(
            received or (date(year, 1, 1) if year else None) or created, created
        )
        if trail_since is None:
            continue
        known_from = max(created, trail_since)
        out.segments[pk] = _owner_segments(row, assigned.get(pk, []), resolve, known_from, today)
        out.states[pk] = _state_segments(row, state_events.get(pk, []), known_from)
    return out


def _owner_segments(
    row: dict[str, Any],
    trail: list[dict[str, Any]],
    resolve: Any,
    known_from: date,
    today: date,
) -> list[tuple[date, Any]]:
    owner_now = _id(row["owner_id"])
    if not trail:
        return [(known_from, owner_now)]
    first = trail[0]
    # Before the first hand-over the owner is the one it was handed from.
    holder = resolve(first["payload"] or {}, "from", first["occurred_at"])
    segments: list[tuple[date, Any]] = [(known_from, holder)]
    for event in trail:
        payload = event["payload"] or {}
        if resolve(payload, "from", event["occurred_at"]) != holder or holder == UNKNOWN:
            # The trail does not chain: who held it before this change is not
            # what the previous change said. That stretch is unknown.
            segments[-1] = (segments[-1][0], UNKNOWN)
        holder = resolve(payload, "to", event["occurred_at"])
        segments.append((_day(event["occurred_at"]) or known_from, holder))
    if holder != owner_now:
        # The owner now is not where the trail ends: a change nobody recorded
        # happened at a time nobody knows.
        segments[-1] = (segments[-1][0], UNKNOWN)
        segments.append((today, owner_now))
    return segments


def _state_segments(
    row: dict[str, Any], trail: list[dict[str, Any]], known_from: date
) -> list[tuple[date, Any]]:
    """Open or closed at the end of each day, from the Teema's own closing/reopening events."""
    closed_on = _day(row["closed_at"])
    if not trail:
        if row["is_open"]:
            return [(known_from, True)]
        if closed_on is not None and closed_on >= known_from:
            # Closed here, on a recorded day, by a path that left no event.
            return [(known_from, True), (closed_on, False)]
        if closed_on is not None:
            # It reached Juristid already closed, and the register says since when.
            return [(closed_on, False)]
        # It reached Juristid already closed; since when nobody recorded.
        return [(known_from, False)]
    first = trail[0]["event_type"]
    initial: Any = False if first in OPENING else (UNKNOWN if first in CLOSING_UNDATED else True)
    segments: list[tuple[date, Any]] = [(known_from, initial)]
    for event in trail:
        day = _day(event["occurred_at"]) or known_from
        kind = event["event_type"]
        if kind in CLOSING_UNDATED:
            # A cut-over retired it on the day it ran; when the work really
            # stopped is not recorded, so the stretch before it is unknown.
            segments[-1] = (segments[-1][0], UNKNOWN)
        segments.append((day, kind in OPENING))
    return segments


def recording_since() -> dict[str, date | None]:
    """When this database first could record each figure's records (`RECORDED_BY`). One query."""
    from django.db.migrations.recorder import MigrationRecorder

    wanted = {pair for pairs in RECORDED_BY.values() for pair in pairs}
    applied = {
        (app, name): _day(moment)
        for app, name, moment in MigrationRecorder.Migration.objects.filter(
            app__in={app for app, _ in wanted}, name__in={name for _, name in wanted}
        ).values_list("app", "name", "applied")
    }
    out: dict[str, date | None] = {}
    for key, pairs in RECORDED_BY.items():
        days = [applied.get(pair) for pair in pairs]
        out[key] = None if any(day is None for day in days) else max(d for d in days if d)
    return out


def load_ownership(user: Any, today: date, trail_since: date | None) -> Ownership:
    """`build_ownership` for every real Teema ``user`` may see. Three queries."""
    from app.accounts.models import User
    from app.audit.enums import ChangeEventType
    from app.audit.models import ChangeEvent
    from app.matters.models import Matter

    rows = list(
        Matter.objects.visible_to(user)
        .real_data()
        .values(
            "pk",
            "owner_id",
            "created_at",
            "received_date",
            "reference_year",
            "is_open",
            "closed_at",
        )
    )
    events: list[dict[str, Any]] = [
        dict(event)
        for event in ChangeEvent.objects.filter(
            matter_id__in=[row["pk"] for row in rows],
            event_type__in=[ChangeEventType.MATTER_ASSIGNED, *STATE_EVENTS],
        )
        .order_by("matter_id", "occurred_at", "pk")
        .values("matter_id", "event_type", "occurred_at", "payload")
    ]
    names: dict[str, list[Person]] = {}
    # Every account, the inactive included: a person who left still owned
    # what they owned.
    for pk, name, joined in User.objects.values_list("pk", "display_name", "created_at"):
        if name:
            names.setdefault(name, []).append(Person(str(pk), joined))
    return build_ownership(rows, events, names, today, trail_since=trail_since)


@dataclass(frozen=True)
class Month:
    start: date
    end: date
    label: str

    @property
    def complete(self) -> bool:
        """The month is over: its last day is today or earlier and was its calendar end."""
        return self.end.day == calendar.monthrange(self.end.year, self.end.month)[1]


def months(today: date, count: int = MONTHS) -> list[Month]:
    """The last ``count`` calendar months, newest first; the current one ends today."""
    from app.reporting.opinion_stats import MONTHS_LONG

    out = []
    year, month = today.year, today.month
    for _ in range(count):
        last = calendar.monthrange(year, month)[1]
        out.append(
            Month(
                date(year, month, 1),
                min(date(year, month, last), today),
                f"{MONTHS_LONG[month - 1]} {year}",
            )
        )
        year, month = (year, month - 1) if month > 1 else (year - 1, 12)
    return out


def owned_at(ownership: Ownership, person_id: Any, day: date) -> Cell:
    """Open Teemad ``person_id`` was responsible for at the end of ``day`` (reconstructed).

    A Teema counts when the trail says both that the person owned it and that it
    was open. Where either is unknown for a Teema that might count, or the day
    is before the trail was recorded, the figure is a lower bound
    (``AT_LEAST``), or ``UNKNOWN`` if nothing is certain.
    """
    person = _id(person_id)
    count = uncertain = 0
    for matter_id, first in ownership.exists_from.items():
        if day < first:
            continue
        state = ownership.open_at(matter_id, day)
        if state is False:
            continue
        owner = ownership.owner_at(matter_id, day)
        if owner is None or (owner != UNKNOWN and owner != person):
            continue
        if owner == person and state is True:
            count += 1
        else:
            uncertain += 1
    if uncertain or ownership.since is None or day < ownership.since:
        # Before the trail was recorded, the Teemad Juristid holds are not all
        # there were (some were never imported): no count there is complete.
        return Cell(Coverage.AT_LEAST, count) if count else Cell(Coverage.UNKNOWN)
    return Cell(Coverage.VERIFIED, count)


def credit_opinion(fact: Any, ownership: Ownership) -> Any:
    """Who a sent opinion is credited to: the recorded actor, else the owner that day."""
    if fact.actor_id is not None:
        return _id(fact.actor_id)
    return ownership.owner_at(fact.matter.matter_id, fact.sent_on)


def sent_in_month(
    facts: list[Any], ownership: Ownership, person_id: Any, month: Month, since: date | None
) -> Cell:
    person = _id(person_id)
    count = uncertain = 0
    for fact in facts:
        if not month.start <= fact.sent_on <= month.end:
            continue
        credited = credit_opinion(fact, ownership)
        if credited == UNKNOWN:
            uncertain += 1
        elif credited == person:
            count += 1
    return cell(month, since, count, uncertain=bool(uncertain))


@dataclass(frozen=True)
class Figure:
    label: str
    value: int | None
    basis: str
    url: str = ""


@dataclass(frozen=True)
class Column:
    key: str
    label: str
    basis: str


COLUMNS = (
    Column("sent", "Välja saadetud arvamused", f"{ACTOR}; imporditud arvamus: {AT_THE_TIME}"),
    Column("owned", "Vastutusel avatud teemasid kuu lõpus", f"{AT_THE_TIME}; taastatud"),
    Column("entries", "Sissekanded", ACTOR),
    Column("closed", "Lõpetatud teemad", ACTOR),
    Column("overviews", "Avaldatud ülevaated ja uudised", ACTOR),
    Column("engagements", "Lisatud kaasamised", ACTOR),
)


@dataclass(frozen=True)
class MonthRow:
    month: Month
    cells: tuple[Cell, ...]


@dataclass(frozen=True)
class MyStatistics:
    current: list[Figure]
    columns: tuple[Column, ...]
    rows: list[MonthRow]
    #: Per column key: the first day this database could record it, or ``None``.
    recording: dict[str, date | None]
    today: date

    @property
    def recorded_since(self) -> list[tuple[str, date | None]]:
        """Each column's label and the first day this database could record it."""
        source = {"owned": "trail"}
        return [
            (column.label, self.recording.get(source.get(column.key, column.key)))
            for column in self.columns
        ]


def my_statistics(person: Any, today: date) -> MyStatistics:
    """The page's figures for ``person``, read as ``person``. About a dozen queries."""
    from app.audit.enums import ChangeEventType
    from app.audit.models import ChangeEvent
    from app.matters.models import Entry, Matter
    from app.matters.my_work import _register_url, opinion_work
    from app.reporting import opinions

    shown = months(today)
    oldest = shown[-1].start
    recording = recording_since()
    ownership = load_ownership(person, today, recording["trail"])
    work = opinion_work(person, person, today)
    current = [
        Figure(
            "Avatud teemad",
            Matter.objects.visible_to(person).filter(owner=person, is_open=True).count(),
            CURRENT,
            _register_url(person),
        ),
        Figure("Arvamus koostamisel", work.in_preparation, CURRENT, work.preparation_url),
        Figure("Arvamuse tähtaeg sel nädalal", work.due_this_week, CURRENT, work.due_url),
    ]

    facts = [fact for fact in opinions.opinion_facts(person) if fact.sent_on >= oldest]
    window = timezone.make_aware(datetime(oldest.year, oldest.month, 1))
    entries = Counter(
        (_day(moment) or oldest).replace(day=1)
        for moment in Entry.objects.visible_to(person)
        .filter(author=person, occurred_at__gte=window)
        .values_list("occurred_at", flat=True)
    )
    kinds: dict[str, str] = {
        ChangeEventType.MATTER_CLOSED: "closed",
        ChangeEventType.WEBSITE_OVERVIEW_PUBLISHED: "overviews",
        ChangeEventType.ENGAGEMENT_ADDED: "engagements",
    }
    acted: Counter[tuple[str, date]] = Counter()
    visible = Matter.objects.visible_to(person).values("pk")
    for kind, moment in ChangeEvent.objects.filter(
        actor=person, event_type__in=list(kinds), occurred_at__gte=window, matter_id__in=visible
    ).values_list("event_type", "occurred_at"):
        acted[(kinds[kind], (_day(moment) or oldest).replace(day=1))] += 1

    rows = []
    for month in shown:
        cells = (
            sent_in_month(facts, ownership, person.pk, month, recording["sent"]),
            owned_at(ownership, person.pk, month.end),
            cell(month, recording["entries"], entries[month.start]),
            *(
                cell(month, recording[key], acted[(key, month.start)])
                for key in ("closed", "overviews", "engagements")
            ),
        )
        rows.append(MonthRow(month, cells))
    return MyStatistics(current, COLUMNS, rows, recording, today)
