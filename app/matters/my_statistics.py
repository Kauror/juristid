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

**Coverage is a stated state, never a guess** (ADR 0223 §4, §7, §11). Each
figure's value for a period is a :class:`Cell` with one of four states:

* ``VERIFIED`` — the whole period lies inside the figure's reliable coverage
  and every record in it can be credited: the count is the period's (so far,
  while it runs), and a 0 is a real zero of *recorded* activities;
* ``PARTIAL`` — reliable coverage began inside the period: what was found is a
  lower bound for the period, shown «≥ N» with an «osaline» hint naming the day;
* ``AT_LEAST`` — the period is not inside reliable coverage, or some record in
  it cannot be credited: a lower bound, «≥ N»;
* ``UNKNOWN`` — nothing reliable and nothing found: «—», never 0.

Every figure has two boundaries (ADR 0223 §10):

* **earliest possible** (`recording_since`): the time the migration that
  created the record's table or event type was applied to this database
  (`RECORDED_BY`, `django_migrations.applied`) — the first moment Juristid
  could write such a record at all. It says when recording became *possible*,
  not that work was recorded from then on;
* **reliable coverage** (`reliable_since`): the day somebody first recorded that
  kind of activity in Juristid as it happened (`RELIABLE_BY`: the first
  `ChangeEvent` of the figure's own event type written by a person — for the
  ownership trail also by the Lovable synchronisation; imports and back-fills
  never count), on a Teema the reader may see, and never earlier than the
  earliest possible day. Where no such record exists the boundary is ``None``:
  not established, and no month of that figure is ever complete.

Only months inside reliable coverage can be ``VERIFIED`` or ``PARTIAL``; before
it, records found are a lower bound and none found is missing data. The page
counts what was recorded in Juristid, not all the work a person actually did.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import date, datetime
from enum import StrEnum
from typing import Any

from django.utils import timezone

#: The owner, or the open/closed state, on a day nobody can say.
UNKNOWN = "?"

#: The migrations whose application on this database is the first moment
#: Juristid could write the records each figure reads (ADR 0223 §4). A figure
#: reading two kinds of record is covered from the later of the two.
RECORDED_BY: dict[str, tuple[tuple[str, str], ...]] = {
    # Submission.sent_by, and a request ended as answered (`ended_by`).
    "sent": (("submissions", "0001_initial"), ("matters", "0045_response_deadline_history")),
    "entries": (("matters", "0003_entry"),),
    "closed": (("audit", "0001_initial"),),
    # `NextAction.ended_by` and the NEXT_ACTION_* events (week overview, r97).
    "actions": (("audit", "0001_initial"),),
    # MATTER_ASSIGNED, MATTER_CLOSED and MATTER_REOPENED: the ownership and
    # open/closed trail.
    "trail": (("audit", "0001_initial"),),
}

#: The event whose first occurrence, written by a person as it happened, is
#: where each figure's reliable coverage begins (ADR 0223 §10). The ownership
#: trail also accepts the Lovable synchronisation's hand-overs: the recorded
#: channel for owner changes made in Lovable.
RELIABLE_BY: dict[str, str] = {
    "sent": "SUBMISSION_SENT",
    "entries": "ENTRY_ADDED",
    "closed": "MATTER_CLOSED",
    "actions": "NEXT_ACTION_COMPLETED",
    "trail": "MATTER_ASSIGNED",
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
    """One figure's value for a span of days, and how much of the span stands behind it."""

    coverage: Coverage
    value: int | None = None
    #: PARTIAL: the day reliable coverage began, inside the span.
    covered_from: date | None = None

    @property
    def known(self) -> bool:
        return self.coverage is not Coverage.UNKNOWN

    @property
    def exact(self) -> bool:
        """The value is the span's whole count (so far): only then is it compared."""
        return self.coverage is Coverage.VERIFIED

    @property
    def text(self) -> str:
        """«12», «≥ 3» (vähemalt 3) or «—». A 0 is printed only when it is real."""
        if self.coverage is Coverage.VERIFIED:
            return str(self.value)
        if self.value:
            return f"≥ {self.value}"
        return "—"

    @property
    def spoken(self) -> str:
        """The value in words, for a screen reader and a tooltip."""
        if self.coverage is Coverage.VERIFIED:
            return str(self.value)
        if self.value:
            return f"vähemalt {self.value}"
        return "andmed puuduvad"


def cell(span: Any, since: date | None, found: int, *, uncertain: bool = False) -> Cell:
    """The state of a count of ``found`` records in ``span`` (``start``..``end``).

    ``since`` is the figure's **reliable coverage** boundary (`reliable_since`),
    not the day recording became possible: ``None`` when it cannot be
    established, and then no span is complete. ``uncertain``: some record in
    the span could not be credited.
    """
    if since is None or since > span.end or uncertain:
        return Cell(Coverage.AT_LEAST, found) if found else Cell(Coverage.UNKNOWN)
    if since > span.start:
        return Cell(Coverage.PARTIAL, found, since)
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
    #: The first day the trail could be recorded at all (earliest possible).
    since: date | None = None
    #: The first day the trail is reliably recorded (`reliable_since`); before
    #: it, or when ``None``, no end-of-day count is complete.
    reliable: date | None = None
    #: Per Teema: its owner now (`Matter.owner`).
    owner_now: dict[Any, Any] = field(default_factory=dict)
    #: Every recorded hand-over: (Teema, day, new owner id | None | UNKNOWN).
    #: Operations that only back-filled an owner the register already had
    #: (``operation`` in the payload) are not arrivals.
    arrivals: list[tuple[Any, date, Any]] = field(default_factory=list)
    #: Per Teema: (reference, title), for the lists behind a figure.
    names: dict[Any, tuple[str, str]] = field(default_factory=dict)

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
    trail_reliable: date | None,
) -> Ownership:
    """Pure: Teemad, their trail events and people → `Ownership`.

    ``rows`` carry ``pk``, ``owner_id``, ``created_at``, ``received_date``,
    ``reference_year``, ``is_open`` and ``closed_at``; ``events`` carry
    ``matter_id``, ``event_type``, ``occurred_at`` and ``payload``, oldest first
    (``MATTER_ASSIGNED`` and the open/closed events of `STATE_EVENTS`).
    ``trail_since`` is the first day the trail could be recorded at all;
    ``trail_reliable`` the first day it is reliably recorded, or ``None``.
    """
    resolve = _resolver(names)
    assigned: dict[Any, list[dict[str, Any]]] = {}
    state_events: dict[Any, list[dict[str, Any]]] = {}
    for event in events:
        kind = event.get("event_type", "MATTER_ASSIGNED")
        target = state_events if kind in STATE_EVENTS else assigned
        target.setdefault(event["matter_id"], []).append(event)
    out = Ownership(since=trail_since, reliable=trail_reliable)
    for row in rows:
        pk = row["pk"]
        out.owner_now[pk] = _id(row["owner_id"])
        for event in assigned.get(pk, []):
            payload = event["payload"] or {}
            if "operation" in payload:
                continue
            day = _day(event["occurred_at"])
            if day is not None:
                out.arrivals.append((pk, day, resolve(payload, "to", event["occurred_at"])))
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


def reliable_since(user: Any, recording: Mapping[str, date | None]) -> dict[str, date | None]:
    """Where each figure's reliable coverage begins (`RELIABLE_BY`). One query.

    The first day somebody recorded that kind of activity in Juristid as it
    happened, on a Teema ``user`` may see, and never before ``recording`` (the
    earliest possible day). ``None`` where no such record exists or recording
    was never possible: coverage is then not established, and nothing is
    invented in its place.
    """
    from django.db.models import Min, Q

    from app.audit.models import ChangeEvent
    from app.audit.provenance import LOVABLE_SYNC_SOURCE
    from app.matters.models import Matter

    # A person's own act. A batch names no actor (imports, the synchronisation)
    # or names its operation or provenance (the owner back-fill run by a person).
    by_a_person = Q(actor__isnull=False) & ~Q(
        payload__has_any_keys=["provenance_source", "provenance", "operation"]
    )
    synchronised = Q(
        event_type=RELIABLE_BY["trail"], payload__provenance_source=LOVABLE_SYNC_SOURCE
    )
    first = {
        kind: _day(moment)
        for kind, moment in ChangeEvent.objects.filter(
            by_a_person | synchronised,
            event_type__in=sorted(set(RELIABLE_BY.values())),
            matter_id__in=Matter.objects.visible_to(user).values("pk"),
        )
        .values("event_type")
        .annotate(first=Min("occurred_at"))
        .values_list("event_type", "first")
    }
    out: dict[str, date | None] = {}
    for key, kind in RELIABLE_BY.items():
        possible, seen = recording.get(key), first.get(kind)
        out[key] = None if possible is None or seen is None else max(possible, seen)
    return out


def load_ownership(
    user: Any, today: date, trail_since: date | None, trail_reliable: date | None
) -> Ownership:
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
            "reference_number",
            "title",
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
    built = build_ownership(
        rows, events, names, today, trail_since=trail_since, trail_reliable=trail_reliable
    )
    for row in rows:
        year, number = row["reference_year"], row["reference_number"]
        reference = f"{year}_{number}" if year is not None and number is not None else ""
        built.names[row["pk"]] = (reference, row["title"] or "")
    return built


def owned_matters(ownership: Ownership, person_id: Any, day: date) -> tuple[list[Any], int]:
    """The Teemad ``person_id`` was known to hold open at the end of ``day``, and how many
    more might have been theirs (owner or open state unknown that day)."""
    person = _id(person_id)
    mine: list[Any] = []
    uncertain = 0
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
            mine.append(matter_id)
        else:
            uncertain += 1
    return mine, uncertain


def owned_at(ownership: Ownership, person_id: Any, day: date) -> Cell:
    """Open Teemad ``person_id`` was responsible for at the end of ``day`` (reconstructed).

    A Teema counts when the trail says both that the person owned it and that it
    was open. Where either is unknown for a Teema that might count, or the day
    is before the trail was reliably recorded, the figure is a lower bound
    (``AT_LEAST``), or ``UNKNOWN`` if nothing is certain.
    """
    mine, uncertain = owned_matters(ownership, person_id, day)
    count = len(mine)
    if uncertain or ownership.reliable is None or day < ownership.reliable:
        # Before the trail is reliably recorded, the Teemad Juristid holds are
        # not all there were (some were never imported) and owner changes may
        # have gone unrecorded: no count there is complete.
        return Cell(Coverage.AT_LEAST, count) if count else Cell(Coverage.UNKNOWN)
    return Cell(Coverage.VERIFIED, count)


def credit_opinion(fact: Any, ownership: Ownership) -> Any:
    """Who a sent opinion is credited to: the recorded actor, else the owner that day."""
    if fact.actor_id is not None:
        return _id(fact.actor_id)
    return ownership.owner_at(fact.matter.matter_id, fact.sent_on)


def sent_in(
    facts: list[Any], ownership: Ownership, person_id: Any, span: Any, since: date | None
) -> Cell:
    """Opinions sent in ``span`` credited to ``person_id`` (`credit_opinion`)."""
    return sent_series(facts, ownership, person_id, since).cell(span)


# ---------------------------------------------------------------------------
# Figures over a period (ADR 0223 §11)
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class Record:
    """One record behind a figure: what happened, on which day, on which Teema."""

    day: date
    matter_id: Any
    what: str = ""
    detail: str = ""
    #: The source's own page or document, for an incoming item.
    source_url: str = ""


@dataclass
class Series:
    """The dated records behind a figure, and how far its counts can be trusted.

    A figure of the person's own work is bounded by ``since`` (its reliable
    coverage). An incoming figure is bounded by ``readings`` instead: the days
    its source was read; a span without one is unknown, never 0.
    """

    records: list[Record] = field(default_factory=list)
    #: Days of records found but not creditable to anybody known.
    uncertain: list[date] = field(default_factory=list)
    since: date | None = None
    readings: set[date] | None = None
    #: Why the figure is not read at all (a switch is off): shown instead of a value.
    off: str = ""

    def within(self, span: Any) -> list[Record]:
        return [record for record in self.records if span.start <= record.day <= span.end]

    def cell(self, span: Any) -> Cell:
        found = len(self.within(span))
        if self.readings is not None:
            # An incoming source: a span it was never read in is unknown, and
            # one that began before its first reading ever is a lower bound.
            if not any(span.start <= day <= span.end for day in self.readings):
                return Cell(Coverage.AT_LEAST, found) if found else Cell(Coverage.UNKNOWN)
            return cell(span, self.since, found)
        doubtful = any(span.start <= day <= span.end for day in self.uncertain)
        return cell(span, self.since, found, uncertain=doubtful)


def sent_series(
    facts: list[Any], ownership: Ownership, person_id: Any, since: date | None
) -> Series:
    person = _id(person_id)
    out = Series(since=since)
    for fact in facts:
        credited = credit_opinion(fact, ownership)
        if credited == UNKNOWN:
            out.uncertain.append(fact.sent_on)
        elif credited == person:
            out.records.append(Record(fact.sent_on, fact.matter.matter_id, "Arvamus saadetud"))
    return out


def arrivals_series(
    ownership: Ownership,
    created: list[tuple[Any, date]],
    person_id: Any,
    since: date | None,
) -> Series:
    """Teemad that became ``person_id``'s: handed to them, or created here with them as owner.

    ``created``: (Teema, day) of every Teema created in Juristid itself. A
    hand-over whose new owner cannot be told apart is a record nobody can
    credit. One Teema arriving twice on one day is one arrival.
    """
    person = _id(person_id)
    out = Series(since=since)
    seen: set[tuple[Any, date]] = set()
    for matter_id, day, holder in sorted(ownership.arrivals, key=lambda item: item[1]):
        if holder == UNKNOWN:
            out.uncertain.append(day)
        elif holder == person and (matter_id, day) not in seen:
            seen.add((matter_id, day))
            out.records.append(Record(day, matter_id, "Sulle üle antud"))
    for matter_id, day in created:
        if (matter_id, day) in seen or ownership.owner_at(matter_id, day) != person:
            continue
        seen.add((matter_id, day))
        out.records.append(Record(day, matter_id, "Loodud sinuga vastutajana"))
    out.records.sort(key=lambda record: record.day)
    return out


# ---------------------------------------------------------------------------
# The page (ADR 0223 §11): two groups of figure cards, a chart, the records
# ---------------------------------------------------------------------------

#: How a figure is counted: dated records (``series``), Teemad held open at the
#: end of a day (``owned``), or a present state only known for today (``present``).
SERIES, OWNED, PRESENT = "series", "owned", "present"
#: Rows the list under the chart shows before «Näita kõiki».
DRILL_LIMIT = 20


@dataclass(frozen=True)
class FigureDef:
    key: str
    label: str
    #: The ⓘ: one sentence, what is counted.
    help: str
    mode: str = SERIES
    #: The coverage boundary it reads (`RECORDED_BY` key); "" for incoming figures.
    coverage: str = ""
    #: The number's tone when it is above 0 («danger» for overdue work).
    tone: str = ""


OWN: tuple[FigureDef, ...] = (
    FigureDef(
        "saadetud",
        "Saadetud arvamused",
        "Arvamused, mille saatjaks on Juristides märgitud sina; imporditud arvamus, millel "
        "saatjat pole, loetakse saatmise päeva vastutajale.",
        coverage="sent",
    ),
    FigureDef(
        "uued",
        "Uued teemad",
        "Teemad, mis said perioodil sinu vastutusele: sulle üle antud või Juristides loodud "
        "sinuga vastutajana.",
        coverage="trail",
    ),
    FigureDef(
        "lopetatud",
        "Lõpetatud teemad",
        "Teemad, mille sina perioodil lõpetasid.",
        coverage="closed",
    ),
    FigureDef(
        "arvamuseta",
        "Ei saatnud arvamust",
        "Sinu perioodil lõpetatud teemad, millele ei olnud lõpetamise päevaks ühtegi "
        "välja saadetud arvamust.",
        coverage="closed",
    ),
    FigureDef(
        "sissekanded",
        "Sissekanded",
        "Sissekanded, mille autor oled sina, toimumise päeva järgi.",
        coverage="entries",
    ),
    FigureDef(
        "menetluses",
        "Menetluses teemad",
        "Avatud teemad, mille vastutaja oled perioodi lõpus (jooksval perioodil täna), "
        "Juristide vastutaja muutmise ajaloo järgi.",
        mode=OWNED,
        coverage="trail",
    ),
    FigureDef(
        "ule-tahtaja",
        "Tähtaja ületanud",
        "Sinu möödunud tähtajaga tegevused ja olulised tähtajad, nagu Minu asjades.",
        mode=PRESENT,
        tone="danger",
    ),
    FigureDef(
        "sekkumist",
        "Vajab sekkumist",
        "Sinu teemad, mille järgmine samm on hiljaks jäänud või ülevaatamise aeg on käes, "
        "nagu Minu asjades.",
        mode=PRESENT,
        tone="warning",
    ),
)

#: Whose Teema an incoming item is on, said in each ⓘ.
_ON_YOURS = " Sinu teema: vastutaja tol päeval, kui ajalugu seda näitab, muidu praegune."

INCOMING_FIGURES: tuple[FigureDef, ...] = (
    FigureDef(
        "registrid",
        "Registrite dokumendid",
        "Dokumendiregistritest sinu teemadele leitud uued dokumendid, leidmise päeva järgi."
        + _ON_YOURS,
    ),
    FigureDef(
        "kutsed",
        "Riigikogu kutsed",
        "Riigikogu komisjonide kirjad kojale eelnõudes, mida sinu teemad jälgivad." + _ON_YOURS,
    ),
    FigureDef(
        "arutelud",
        "Riigikogu arutelud",
        "Komisjonide istungid, mille päevakorras on sinu teema eelnõu, istungi päeva järgi."
        + _ON_YOURS,
    ),
    FigureDef(
        "mainimised",
        "Koja mainimised Riigikogus",
        "Kohad täiskogu stenogrammides ja komisjonide protokollides, kus koda nimetatakse "
        "sinu teema eelnõu juures." + _ON_YOURS,
    ),
    FigureDef(
        "valitsus",
        "Valitsuse päevakord",
        "Valitsuse istungi päevakorrapunktid, mille Juristid sinu teemaga seostas." + _ON_YOURS,
    ),
)

FIGURES = {figure.key: figure for figure in (*OWN, *INCOMING_FIGURES)}
DEFAULT_FIGURE = OWN[0].key

ONLY_TODAY = "Teada ainult tänase seisuga; varasemat seisu Juristid ei salvesta."
NOT_READ = "Sel perioodil seda allikat ei loetud."


@dataclass(frozen=True)
class Card:
    figure: FigureDef
    cell: Cell
    url: str
    selected: bool
    #: The difference from the previous equal period, or ``None`` when no exact
    #: comparison exists (either value is a lower bound or unknown).
    delta: int | None = None
    #: The small hint under the value («osaline», «ainult täna», «lugemata»), and its ⓘ.
    hint: str = ""
    hint_help: str = ""
    #: Shown instead of the value when the figure is not read at all.
    off: str = ""

    @property
    def key(self) -> str:
        return self.figure.key

    @property
    def arrow(self) -> str:
        if self.delta is None:
            return ""
        return "↑" if self.delta > 0 else "↓" if self.delta < 0 else "→"

    @property
    def change(self) -> int | None:
        return None if self.delta is None else abs(self.delta)

    @property
    def tone(self) -> str:
        return self.figure.tone if self.cell.exact and self.cell.value else ""


@dataclass(frozen=True)
class Column:
    """One bar of the chart."""

    label: str
    sublabel: str
    title: str
    text: str
    #: Height in per cent of the plot; 0 draws no bar.
    height: float
    future: bool = False
    #: The value is a lower bound or unknown: drawn faint.
    partial: bool = False
    #: Selecting this column narrows the list under the chart to its days (r97).
    #: Empty for a column nobody measured and for one in the future: a click there
    #: would promise records for a span the product cannot speak about. A column
    #: that *was* measured and holds a genuine 0 is selectable.
    url: str = ""
    selected: bool = False


@dataclass(frozen=True)
class DrillRow:
    day: date | None
    reference: str
    title: str
    url: str
    what: str = ""
    detail: str = ""
    source_url: str = ""


@dataclass(frozen=True)
class Drill:
    rows: list[DrillRow]
    total: int
    more_url: str = ""


@dataclass(frozen=True)
class Group:
    key: str
    title: str
    cards: list[Card]


@dataclass(frozen=True)
class MyStatistics:
    period: Any
    today: date
    groups: list[Group]
    selected: Card
    #: The chart of the selected figure, or ``[]`` with ``chart_note`` saying why not.
    columns: list[Column]
    chart_note: str
    drill: Drill
    #: The bar: (key, label, url, active); a custom period's link is its form.
    kinds: list[tuple[str, str, str, bool]]
    previous_url: str
    next_url: str
    #: Per coverage key: the first day this database could record it, and where
    #: reliable coverage begins (``None``: not established).
    recording: dict[str, date | None] = field(default_factory=dict)
    reliable: dict[str, date | None] = field(default_factory=dict)
    #: The chart column the list is narrowed to (its label), and the link that
    #: clears only that — the period and the figure stay.
    selection: str = ""
    clear_url: str = ""


@dataclass(frozen=True)
class Day:
    """A span of one day, for the helpers that read ``start`` and ``end``."""

    start: date
    end: date


def _hint(card_cell: Cell, since: date | None, span: Any) -> tuple[str, str]:
    """The «osaline» hint of a value that is not the span's whole count, and its ⓘ."""
    if card_cell.coverage is Coverage.VERIFIED:
        return "", ""
    if since is None:
        return "osaline", "Selle tegevuse salvestamise algust Juristides ei saa veel kindlaks teha."
    if since > span.start:
        return "osaline", (
            f"Juristid salvestab seda alates {since:%d.%m.%Y}; varasemad tegevused "
            "ei pruugi olla kirjas."
        )
    return "osaline", "Mõnda kirjet või vastutajat ei saa tol ajal kellelegi omistada."


def _delta(now: Cell, before: Cell) -> int | None:
    if now.exact and before.exact and now.value is not None and before.value is not None:
        return now.value - before.value
    return None


def _matter_url(pk: Any) -> str:
    from django.urls import reverse

    return reverse("matters:matter_detail", args=[pk])


@dataclass(frozen=True)
class SeriesData:
    """Everything one person's figures are read from, loaded once (r97).

    Both `my_statistics` and Minu asjad's week overview take their cells from
    these series, so a week figure on either surface is the same read — and the
    overview asks for the current and the previous week from one load.
    """

    recording: dict[str, date | None]
    reliable: dict[str, date | None]
    ownership: Ownership
    facts: list[Any]
    series: dict[str, Series]


def load_series(person: Any, today: date, first: date, until: date) -> SeriesData:
    """Read the person's records from ``first`` to ``until`` into dated series."""
    from app.audit.enums import ChangeEventType
    from app.audit.models import ChangeEvent
    from app.matters.enums import MatterOrigin
    from app.matters.models import Entry, Matter
    from app.matters.my_statistics_incoming import incoming_series
    from app.reporting import opinions
    from app.riigikogu.bills import switched_on
    from app.workflow.enums import closure_reason_label

    window = timezone.make_aware(datetime(first.year, first.month, first.day))
    recording = recording_since()
    reliable = reliable_since(person, recording)
    ownership = load_ownership(person, today, recording["trail"], reliable["trail"])
    visible = Matter.objects.visible_to(person).values("pk")

    all_facts = opinions.opinion_facts(person)
    facts = [fact for fact in all_facts if first <= fact.sent_on <= until]
    first_sent: dict[Any, date] = {}
    for fact in all_facts:
        known = first_sent.get(fact.matter.matter_id)
        if known is None or fact.sent_on < known:
            first_sent[fact.matter.matter_id] = fact.sent_on

    kinds = dict(Entry._meta.get_field("kind").choices or [])
    entries = Series(since=reliable["entries"])
    for moment, matter_id, kind in (
        Entry.objects.visible_to(person)
        .filter(author=person, occurred_at__gte=window)
        .values_list("occurred_at", "matter_id", "kind")
    ):
        day = _day(moment)
        if day is not None:
            entries.records.append(Record(day, matter_id, str(kinds.get(kind, kind))))

    closed = Series(since=reliable["closed"])
    unanswered = Series(since=reliable["closed"])
    for moment, matter_id, payload in ChangeEvent.objects.filter(
        actor=person,
        event_type=ChangeEventType.MATTER_CLOSED,
        occurred_at__gte=window,
        matter_id__in=visible,
    ).values_list("occurred_at", "matter_id", "payload"):
        day = _day(moment)
        if day is None:
            continue
        reason = closure_reason_label(str((payload or {}).get("disposition") or ""))
        closed.records.append(Record(day, matter_id, "Lõpetatud", reason))
        sent_on = first_sent.get(matter_id)
        if sent_on is None or sent_on > day:
            unanswered.records.append(Record(day, matter_id, "Lõpetatud arvamuseta", reason))

    created: list[tuple[Any, date]] = []
    for matter_id, moment in ChangeEvent.objects.filter(
        event_type=ChangeEventType.MATTER_CREATED,
        occurred_at__gte=window,
        matter_id__in=visible,
        matter__origin=MatterOrigin.NATIVE,
    ).values_list("matter_id", "occurred_at"):
        day = _day(moment)
        if day is not None:
            created.append((matter_id, day))

    series: dict[str, Series] = {
        "saadetud": sent_series(facts, ownership, person.pk, reliable["sent"]),
        "uued": arrivals_series(ownership, created, person.pk, reliable["trail"]),
        "lopetatud": closed,
        "arvamuseta": unanswered,
        "sissekanded": entries,
        **incoming_series(person, ownership, first, until, riigikogu_on=switched_on()),
    }

    return SeriesData(recording, reliable, ownership, all_facts, series)


def my_statistics(
    person: Any,
    today: date,
    period: Any = None,
    selected: str = "",
    *,
    show_all: bool = False,
    path: str = "",
    selected_bucket: str = "",
) -> MyStatistics:
    """The page for ``person``, read as ``person``, for ``period`` (default: this week).

    A bounded number of queries whatever the portfolio: the trail, the opinions,
    the person's own entries and closings, the Teemad created here, the
    incoming sources, and — for the current period only — Minu asjad's work.
    """
    from app.matters import stat_periods

    period = period or stat_periods.current(today)
    selected = selected if selected in FIGURES else DEFAULT_FIGURE
    counted, compared = period.counted, period.comparison
    first = min(period.start, compared.start)
    timezone.make_aware(datetime(first.year, first.month, first.day))

    data = load_series(person, today, first, period.until)
    recording, reliable, ownership, series = (
        data.recording,
        data.reliable,
        data.ownership,
        data.series,
    )

    present: dict[str, tuple[list[DrillRow], int]] = {}
    if period.is_current:
        present = _present_state(person, today)

    def card_for(figure: FigureDef) -> Card:
        url = period.url(path, naitaja=figure.key)
        chosen = figure.key == selected
        if figure.mode == PRESENT:
            if figure.key in present:
                total = present[figure.key][1]
                return Card(figure, Cell(Coverage.VERIFIED, total), url, chosen)
            return Card(
                figure, Cell(Coverage.UNKNOWN), url, chosen, None, "ainult täna", ONLY_TODAY
            )
        if figure.mode == OWNED:
            now = owned_at(ownership, person.pk, period.until)
            before = owned_at(ownership, person.pk, compared.end)
            hint, why = _hint(now, ownership.reliable, Day(period.until, period.until))
            return Card(figure, now, url, chosen, _delta(now, before), hint, why)
        found = series[figure.key]
        if found.off:
            return Card(figure, Cell(Coverage.UNKNOWN), url, chosen, off=found.off)
        now, before = found.cell(counted), found.cell(compared)
        if found.readings is not None and now.coverage is Coverage.PARTIAL:
            hint, why = "osaline", f"Seda allikat loetakse alates {found.since:%d.%m.%Y}."
        elif found.readings is not None:
            hint, why = ("", "") if now.exact else ("lugemata", NOT_READ)
        else:
            hint, why = _hint(now, found.since, counted)
        return Card(figure, now, url, chosen, _delta(now, before), hint, why)

    groups = [
        Group("oma", "Minu töö", [card_for(figure) for figure in OWN]),
        Group("seire", "Sissetulev seire", [card_for(figure) for figure in INCOMING_FIGURES]),
    ]
    chosen = next(card for group in groups for card in group.cards if card.selected)

    bucket = next(
        (
            b
            for b in period.buckets()
            if not b.future and b.span.start.isoformat() == selected_bucket
        ),
        None,
    )
    columns, note = _chart(chosen, period, series, ownership, person.pk, path, bucket)
    drill = _drill(chosen, period, series, ownership, person.pk, present, show_all, path, bucket)

    bar = []
    for key, label in stat_periods.KINDS:
        if key == stat_periods.CUSTOM:
            url = period.url(path, naitaja=selected) if period.kind == key else ""
        else:
            url = stat_periods.current(today, key).url(path, naitaja=selected)
        bar.append((key, label, url, period.kind == key))
    previous_url = period.previous.url(path, naitaja=selected) if period.has_previous else ""
    next_url = period.shifted(1).url(path, naitaja=selected) if period.has_next else ""
    return MyStatistics(
        period=period,
        today=today,
        groups=groups,
        selected=chosen,
        selection=(bucket.label + (" " + bucket.sublabel if bucket.sublabel else ""))
        if bucket
        else "",
        clear_url=period.url(path, naitaja=chosen.figure.key) if bucket else "",
        columns=columns,
        chart_note=note,
        drill=drill,
        kinds=bar,
        previous_url=previous_url,
        next_url=next_url,
        recording=recording,
        reliable=reliable,
    )


def _present_state(person: Any, today: date) -> dict[str, tuple[list[DrillRow], int]]:
    """Minu asjad's own overdue band and «Vajab sekkumist» rows, for today."""
    from app.matters import work_items as wi
    from app.matters.my_work import VIEW_ATTENTION, build_portfolio, default_horizon

    everything = wi.work_items(person, today=today, responsible=person)
    bands = wi.band_items(everything, today, horizon=default_horizon(today).until)
    overdue = next((band for band in bands if band.key == wi.BAND_OVERDUE), None)
    late = [
        DrillRow(
            item.period_end or item.when,
            item.matter.display_reference,
            item.matter.title,
            _matter_url(item.matter.pk),
            item.text,
            item.display_date,
        )
        for item in (overdue.items if overdue else [])
    ]
    portfolio = build_portfolio(person, person, today=today, items=everything, view=VIEW_ATTENTION)
    attention = [
        DrillRow(
            row.action.when if row.action else None,
            row.matter.display_reference,
            row.matter.title,
            _matter_url(row.matter.pk),
            row.action.text if row.action else "",
            "Tähtaeg möödas" if row.action and row.action.is_overdue else "Ülevaatamise aeg",
        )
        for row in portfolio.all_rows
        if row.needs_attention
    ]
    # The band lists its first `BAND_LIMIT` rows; its total is every overdue one.
    return {
        "ule-tahtaja": (late, overdue.total if overdue else 0),
        "sekkumist": (attention, len(attention)),
    }


def _chart(
    card: Card,
    period: Any,
    series: dict[str, Series],
    ownership: Ownership,
    person_id: Any,
    path: str = "",
    chosen: Any = None,
) -> tuple[list[Column], str]:
    """The selected figure by day, week or month (`Period.grain`); future columns empty."""
    figure = card.figure
    if figure.mode == PRESENT:
        return [], ONLY_TODAY
    if card.off:
        return [], card.off
    values: list[tuple[Any, Cell | None]] = []
    for bucket in period.buckets():
        if bucket.future:
            values.append((bucket, None))
            continue
        span = Day(bucket.span.start, min(bucket.span.end, period.today))
        if figure.mode == OWNED:
            values.append((bucket, owned_at(ownership, person_id, span.end)))
        else:
            values.append((bucket, series[figure.key].cell(span)))
    top = max([found.value or 0 for _, found in values if found is not None] + [0])
    columns = []
    for bucket, found in values:
        span_text = (
            f"{bucket.span.start:%d.%m.%Y}"
            if bucket.span.start == bucket.span.end
            else bucket.span.text
        )
        if found is None:
            columns.append(Column(bucket.label, bucket.sublabel, span_text, "", 0.0, future=True))
            continue
        height = 100.0 * (found.value or 0) / top if top else 0.0
        # A measured column — a real count, a lower bound above 0 or a genuine 0 —
        # can be opened; an unmeasured one (UNKNOWN) has no records to open.
        openable = found.coverage is not Coverage.UNKNOWN and figure.mode == SERIES
        columns.append(
            Column(
                bucket.label,
                bucket.sublabel,
                f"{span_text}: {found.spoken}",
                found.text,
                height,
                partial=not found.exact,
                url=(
                    period.url(path, naitaja=figure.key, valik=bucket.span.start.isoformat())
                    + "#mystats-joonis"
                    if openable and path
                    else ""
                ),
                selected=chosen is not None and chosen.span.start == bucket.span.start,
            )
        )
    return columns, ""


def _drill(
    card: Card,
    period: Any,
    series: dict[str, Series],
    ownership: Ownership,
    person_id: Any,
    present: dict[str, tuple[list[DrillRow], int]],
    show_all: bool,
    path: str,
    bucket: Any = None,
) -> Drill:
    """The records behind the selected figure for the period (or one chart column), newest first."""
    figure = card.figure
    rows: list[DrillRow]
    if figure.mode == PRESENT:
        rows, total = present.get(figure.key, ([], 0))
    elif card.off:
        rows, total = [], 0
    elif figure.mode == OWNED:
        mine, _ = owned_matters(ownership, person_id, period.until)
        rows = []
        for pk in mine:
            reference, title = ownership.names.get(pk, ("", ""))
            rows.append(DrillRow(None, reference, title, _matter_url(pk)))
        rows.sort(key=lambda row: (row.reference, row.title))
        total = len(rows)
    else:
        rows = []
        for record in sorted(
            series[figure.key].within(bucket.span if bucket is not None else period.counted),
            key=lambda r: r.day,
            reverse=True,
        ):
            reference, title = ownership.names.get(record.matter_id, ("", ""))
            rows.append(
                DrillRow(
                    record.day,
                    reference,
                    title,
                    _matter_url(record.matter_id),
                    record.what,
                    record.detail,
                    record.source_url,
                )
            )
        total = len(rows)
    if show_all or len(rows) <= DRILL_LIMIT:
        return Drill(rows, total)
    return Drill(rows[:DRILL_LIMIT], total, period.url(path, naitaja=figure.key, kaik="1"))
