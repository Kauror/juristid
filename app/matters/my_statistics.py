"""Minu statistika — one person's own figures, each credited by a stated rule (FUNC-003).

docs/adr/0223. A page of the signed-in person's own figures, reached from Minu
asjad, beside the «Minu statistika» foldout it extends (`my_work.person_stats`,
`opinion_work`, unchanged). Nobody else's figures are read or shown here, and
nothing is ranked, rated or compared.

Every figure says how it is credited, because the three answers differ:

* **Current responsibility** — the Teema's owner *now* (`Matter.owner`): open
  Teemad, opinions in preparation, opinions due this week. The same
  populations as the foldout and Osakond.
* **What you did** — the person a record names as having done it: an entry's
  author, the person who closed a Teema (`MATTER_CLOSED`), published an
  overview (`WEBSITE_OVERVIEW_PUBLISHED`) or added a consultation
  (`ENGAGEMENT_ADDED`), the person who recorded an opinion as sent
  (`Submission.sent_by`, or who ended a request as answered).
* **Responsibility at the time** — the Teema's owner *on that day*, rebuilt
  from Juristid's own trail: the owner when the Teema was created in Juristid,
  then every `MATTER_ASSIGNED` change in order. An imported «välja saadetud»
  names nobody who sent it, so it is credited to the owner at the time.

The current owner never inherits another person's history: a Teema handed over
in March counts for its earlier owner in February.

**What cannot be rebuilt is unknown, never zero.** A month is shown only when
every record that could change it can be credited; otherwise it is
«andmed puuduvad». The boundary comes from the records themselves, not from the
first event: a Teema that existed before Juristid recorded it (an imported one,
received in 2019) has no known owner for the months before it arrived here, so
every month it spans before then is unknown; an assignment trail whose names do
not chain, or name somebody who cannot be told apart from another person, makes
that stretch unknown too. Actions are recorded only since Juristid was first
used by a person (the earliest change event with an actor), so earlier months
of «what you did» are unknown as well.
"""

from __future__ import annotations

import calendar
from collections import Counter
from dataclasses import dataclass, field
from datetime import date, datetime
from typing import Any

from django.utils import timezone

#: The owner on a day nobody can say (see the module docstring).
UNKNOWN = "?"

#: How many calendar months the page shows, the current one included.
MONTHS = 12

#: Credit rules, as the page names them.
CURRENT = "praegune vastutaja"
ACTOR = "sinu tehtud"
AT_THE_TIME = "vastutaja tol ajal"


def _day(moment: datetime | date | None) -> date | None:
    if moment is None:
        return None
    if isinstance(moment, datetime):
        return timezone.localdate(moment) if timezone.is_aware(moment) else moment.date()
    return moment


@dataclass
class Ownership:
    """Who owned each Teema when, as far as Juristid's own trail can say."""

    #: Per Teema: (first day, owner id | None | UNKNOWN), oldest first.
    segments: dict[Any, list[tuple[date, Any]]] = field(default_factory=dict)
    #: Per Teema: the days it could have had an owner at all (first, last).
    spans: dict[Any, tuple[date, date]] = field(default_factory=dict)

    def owner_at(self, matter_id: Any, day: date) -> Any:
        """The owner's id on ``day``, ``None`` for nobody, ``UNKNOWN`` if it cannot be said."""
        segments = self.segments.get(matter_id)
        if not segments or day < segments[0][0]:
            return UNKNOWN
        owner: Any = UNKNOWN
        for start, who in segments:
            if start > day:
                break
            owner = who
        return owner


def _resolver(names: dict[str, list[Any]]) -> Any:
    def resolve(name: Any) -> Any:
        if name in (None, ""):
            return None
        found = names.get(str(name), [])
        return found[0] if len(found) == 1 else UNKNOWN

    return resolve


def build_ownership(
    rows: list[dict[str, Any]],
    events: list[dict[str, Any]],
    names: dict[str, list[Any]],
    today: date,
) -> Ownership:
    """Pure: Teemad (``rows``), their ``MATTER_ASSIGNED`` events and user names → `Ownership`.

    ``rows`` carry ``pk``, ``owner_id``, ``created_at``, ``received_date``,
    ``reference_year``, ``is_open`` and ``closed_at``; ``events`` carry
    ``matter_id``, ``occurred_at`` and ``payload`` (``from_name``/``to_name``,
    the only names the event stores), oldest first.
    """
    resolve = _resolver(names)
    by_matter: dict[Any, list[dict[str, Any]]] = {}
    for event in events:
        by_matter.setdefault(event["matter_id"], []).append(event)
    out = Ownership()
    for row in rows:
        created = _day(row["created_at"]) or today
        trail = by_matter.get(row["pk"], [])
        segments: list[tuple[date, Any]] = []
        if not trail:
            segments.append((created, row["owner_id"]))
        else:
            # Before the first hand-over the owner is the one it was handed from.
            holder = resolve((trail[0]["payload"] or {}).get("from_name"))
            segments.append((created, holder))
            for event in trail:
                payload = event["payload"] or {}
                if resolve(payload.get("from_name")) != holder or holder == UNKNOWN:
                    # The names do not chain: who held it before this change is
                    # not what the previous change said. That stretch is unknown.
                    segments[-1] = (segments[-1][0], UNKNOWN)
                holder = resolve(payload.get("to_name"))
                segments.append((_day(event["occurred_at"]) or created, holder))
            if holder != row["owner_id"]:
                # The owner now is not where the trail ends: a change nobody
                # recorded happened at a time nobody knows.
                segments[-1] = (segments[-1][0], UNKNOWN)
                segments.append((today, row["owner_id"]))
        out.segments[row["pk"]] = segments
        received = row["received_date"]
        year = row["reference_year"]
        first = received or (date(year, 1, 1) if year else None) or created
        closed = _day(row["closed_at"])
        if row["is_open"]:
            last = today
        else:
            # A Teema closed before it reached Juristid closed at the latest then.
            last = closed or created
        out.spans[row["pk"]] = (min(first, created), max(last, first))
    return out


def load_ownership(user: Any, today: date) -> Ownership:
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
            event_type=ChangeEventType.MATTER_ASSIGNED,
        )
        .order_by("matter_id", "occurred_at", "pk")
        .values("matter_id", "occurred_at", "payload")
    ]
    names: dict[str, list[Any]] = {}
    for pk, name in User.objects.values_list("pk", "display_name"):
        if name:
            names.setdefault(name, []).append(pk)
    return build_ownership(rows, events, names, today)


@dataclass(frozen=True)
class Month:
    start: date
    end: date
    label: str


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


def owned_at_month_end(ownership: Ownership, person_id: Any, month: Month) -> int | None:
    """Teemad ``person_id`` owned on the month's last day; ``None`` if that cannot be said."""
    count = 0
    for matter_id, (first, last) in ownership.spans.items():
        if not first <= month.end <= last:
            continue
        owner = ownership.owner_at(matter_id, month.end)
        if owner == UNKNOWN:
            return None
        if owner == person_id:
            count += 1
    return count


def credit_opinion(fact: Any, ownership: Ownership) -> Any:
    """Who a sent opinion is credited to: the recorded actor, else the owner that day."""
    if fact.actor_id is not None:
        return fact.actor_id
    return ownership.owner_at(fact.matter.matter_id, fact.sent_on)


def sent_in_month(
    facts: list[Any], ownership: Ownership, person_id: Any, month: Month
) -> int | None:
    count = 0
    for fact in facts:
        if not month.start <= fact.sent_on <= month.end:
            continue
        credited = credit_opinion(fact, ownership)
        if credited == UNKNOWN:
            return None
        if credited == person_id:
            count += 1
    return count


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
    Column("owned", "Vastutusel teemasid kuu lõpus", AT_THE_TIME),
    Column("entries", "Sissekanded", ACTOR),
    Column("closed", "Lõpetatud teemad", ACTOR),
    Column("overviews", "Avaldatud ülevaated ja uudised", ACTOR),
    Column("engagements", "Lisatud kaasamised", ACTOR),
)


@dataclass(frozen=True)
class MonthRow:
    month: Month
    cells: tuple[int | None, ...]


@dataclass(frozen=True)
class MyStatistics:
    current: list[Figure]
    columns: tuple[Column, ...]
    rows: list[MonthRow]
    #: The first day Juristid recorded a person's action, or ``None``.
    actions_since: date | None
    #: The first month whose «at the time» figures can all be said, or ``None``.
    owned_since: date | None


def my_statistics(person: Any, today: date) -> MyStatistics:
    """The page's figures for ``person``, read as ``person``. About a dozen queries."""
    from django.db.models import Min

    from app.audit.enums import ChangeEventType
    from app.audit.models import ChangeEvent
    from app.matters.models import Entry, Matter
    from app.matters.my_work import _register_url, opinion_work
    from app.reporting import opinions

    shown = months(today)
    oldest = shown[-1].start
    ownership = load_ownership(person, today)
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
    first_action = ChangeEvent.objects.filter(actor__isnull=False).aggregate(
        first=Min("occurred_at")
    )["first"]
    actions_since = _day(first_action)
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
        recorded = actions_since is not None and month.start >= actions_since
        cells = (
            sent_in_month(facts, ownership, person.pk, month),
            owned_at_month_end(ownership, person.pk, month),
            entries[month.start] if recorded else None,
            acted[("closed", month.start)] if recorded else None,
            acted[("overviews", month.start)] if recorded else None,
            acted[("engagements", month.start)] if recorded else None,
        )
        rows.append(MonthRow(month, cells))
    # The «since» is the oldest month from which every later month is known.
    owned_since = None
    for row in rows:
        if row.cells[1] is None:
            break
        owned_since = row.month.start
    return MyStatistics(current, COLUMNS, rows, actions_since, owned_since)
