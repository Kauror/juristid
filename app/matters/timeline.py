"""The professional timeline.

One projection over two sources: authored ``Entry`` records and the selected
``ChangeEvent`` rows that a lawyer would actually want to see. There is no third
history model, and ``SecurityAuditEvent`` never appears here — access and
download traces are a compliance record, not professional chronology
(master specification 16.5).

One save, one line
------------------

A composer save is one thing a person did, and it can legitimately write five
canonical records: a note, a captured file, a superseded next action, a
consultation and a closure. Rendering five lines for it is what turned the
chronology into an audit log — "Järgmiseks määratud", "Tõendiversioon lisatud",
"Sissekanne lisatud", one under the other, for a single click.

So the events are *grouped*, never suppressed. Every underlying
``ChangeEvent`` still exists, still says exactly what it said, and is still
readable through the technical history; what changes is that the ones sharing an
``operation_id`` render as one item with one sentence describing what the person
did. Rows written outside a composer save carry no operation and stand alone,
which is what they have always been (Teema redesign §11.1).
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from django.db import models

from app.audit.enums import ChangeEventType
from app.audit.models import ChangeEvent
from app.matters.models import Entry, Matter

#: Events worth a line in the chronology. Field-level noise is deliberately
#: absent: a lawyer scrolling six months of work does not need to see that a
#: deadline was corrected by a day, and burying the meeting notes under that
#: kind of traffic is how a timeline stops being read.
TIMELINE_EVENT_TYPES: tuple[str, ...] = (
    ChangeEventType.MATTER_CREATED,
    ChangeEventType.MATTER_ASSIGNED,
    ChangeEventType.MATTER_STAGE_CHANGED,
    ChangeEventType.NEXT_ACTION_SET,
    ChangeEventType.NEXT_ACTION_COMPLETED,
    ChangeEventType.SUBMISSION_SENT,
    ChangeEventType.SUBMISSION_WITHDRAWN,
    ChangeEventType.EVIDENCE_VERSION_ADDED,
    ChangeEventType.MATTER_CLOSED,
    ChangeEventType.MATTER_REOPENED,
)

#: Facts that never earn a line of their own, but do earn a clause when they
#: were part of one composer save.
#:
#: `Kaasamine` and `Oluline tähtaeg` each have a section on the Matter page that
#: shows the fact in a form a reader can act on, and echoing every one of them
#: into the narrative is the noise those sections exist to avoid — which is why
#: they are not in `TIMELINE_EVENT_TYPES` and why adding one from its own
#: control still writes nothing here (Stage-2G brief 34, Agent-F brief 20).
#:
#: But "Marko lisas märkuse ja määras järgmise sammu" is a description of what
#: somebody did, and if that same save also recorded a members' survey then
#: leaving it out makes the sentence wrong. So these are read, grouped, and
#: contribute a clause — never a row (Teema redesign §11.1, §21).
GROUPED_ONLY_EVENT_TYPES: tuple[str, ...] = (
    ChangeEventType.IMPORTANT_DATE_ADDED,
    ChangeEventType.ENGAGEMENT_ADDED,
)

#: An entry the composer just created also produces an ENTRY_ADDED change event.
#: The entry itself is the richer of the two, so the event is not rendered
#: again — otherwise every note would appear twice. It is still *read*, because
#: it is what says which operation the entry belongs to.
SUPPRESSED_WHEN_ENTRY_SHOWN: frozenset[str] = frozenset(
    {ChangeEventType.ENTRY_ADDED, ChangeEventType.ENTRY_EDITED}
)

#: How one composer save is described, in the order the clauses read. Estonian
#: third person, because the line begins with the person's name: "Marko lisas
#: märkuse ja määras järgmise sammu."
_CLAUSES: tuple[tuple[str, str], ...] = (
    (ChangeEventType.EVIDENCE_VERSION_ADDED, "lisas dokumendi"),
    (ChangeEventType.NEXT_ACTION_SET, "määras järgmise sammu"),
    (ChangeEventType.NEXT_ACTION_COMPLETED, "märkis eelmise sammu tehtuks"),
    (ChangeEventType.IMPORTANT_DATE_ADDED, "lisas olulise tähtaja"),
    (ChangeEventType.ENGAGEMENT_ADDED, "lisas kaasamise"),
    (ChangeEventType.SUBMISSION_SENT, "märkis arvamuse saadetuks"),
    (ChangeEventType.MATTER_CLOSED, "lõpetas teema"),
)


@dataclass(frozen=True)
class TimelineItem:
    """One rendered line. ``occurred_at`` is what the reader sees.

    ``events`` carries every change event that belongs to the same professional
    action, including the one in ``event``. For a standalone row it holds that
    single event; for a composer save it holds all of them, and ``summary_verbs``
    is the sentence they add up to.
    """

    occurred_at: datetime
    created_at: datetime
    sort_key: str
    item_type: str
    entry: Entry | None = None
    event: ChangeEvent | None = None
    events: tuple[ChangeEvent, ...] = ()
    summary_verbs: tuple[str, ...] = ()

    @property
    def is_entry(self) -> bool:
        return self.entry is not None

    @property
    def is_grouped(self) -> bool:
        """Whether this line stands for more than one canonical record."""
        return len(self.events) > 1 or bool(self.entry and self.events)

    @property
    def summary_sentence(self) -> str:
        """ "lisas märkuse ja määras järgmise sammu", or an empty string.

        Built from the verbs rather than stored, so a save that wrote three
        records reads as one sentence and a save that wrote one reads as
        nothing at all — the entry card already says what it is.
        """
        verbs = list(self.summary_verbs)
        if not verbs:
            return ""
        if len(verbs) == 1:
            return verbs[0]
        return f"{', '.join(verbs[:-1])} ja {verbs[-1]}"


@dataclass
class _Group:
    """Accumulator for one operation while the page is being assembled."""

    entry: Entry | None = None
    events: list[ChangeEvent] = field(default_factory=list)


def _verbs_for(entry: Entry | None, events: list[ChangeEvent]) -> tuple[str, ...]:
    seen = {event.event_type for event in events}
    verbs: list[str] = []
    if entry is not None:
        verbs.append("lisas märkuse")
    verbs.extend(phrase for event_type, phrase in _CLAUSES if event_type in seen)
    return tuple(verbs)


def matter_timeline(
    *, matter: Matter, user: Any, limit: int = 50, offset: int = 0
) -> tuple[list[TimelineItem], bool]:
    """Return one page of the timeline, newest first.

    Entries are filtered through their own visibility so a restricted entry
    inside an otherwise visible Matter stays hidden. The change-event stream is
    scoped to this Matter, which the caller has already proven the user may
    read.

    Returns the page and whether more items exist.
    """
    # Fetch one extra of each so "is there more" needs no second count query.
    window = offset + limit + 1

    entries = list(
        Entry.objects.filter(matter=matter)
        .visible_to(user)
        .select_related("author", "organisation")
        .chronological()[:window]
    )

    # ENTRY_ADDED is fetched and not rendered. It is the only thing that says
    # which operation an entry belongs to — the Entry table carries no such
    # column, because an entry is business content and an operation is an audit
    # fact about how it was written.
    events = list(
        ChangeEvent.objects.filter(matter=matter)
        .filter(
            models.Q(event_type__in=TIMELINE_EVENT_TYPES)
            | models.Q(event_type__in=SUPPRESSED_WHEN_ENTRY_SHOWN)
            # A grouped-only fact is read only when it belongs to a save. On its
            # own it is not chronology and the query does not return it, so the
            # section that owns it stays the single place it is reported.
            | models.Q(event_type__in=GROUPED_ONLY_EVENT_TYPES, operation_id__isnull=False)
        )
        .select_related("actor")
        .order_by("-occurred_at", "-created_at", "-id")[: window * 3]
    )

    entry_operations: dict[Any, uuid.UUID] = {
        event.object_id: event.operation_id
        for event in events
        if event.event_type == ChangeEventType.ENTRY_ADDED and event.operation_id is not None
    }
    renderable = [event for event in events if event.event_type not in SUPPRESSED_WHEN_ENTRY_SHOWN]

    groups: dict[uuid.UUID, _Group] = {}
    items: list[TimelineItem] = []

    for entry in entries:
        operation = entry_operations.get(entry.pk)
        if operation is None:
            items.append(
                TimelineItem(
                    occurred_at=entry.occurred_at,
                    created_at=entry.created_at,
                    sort_key=str(entry.id),
                    item_type=entry.kind,
                    entry=entry,
                )
            )
            continue
        groups.setdefault(operation, _Group()).entry = entry

    for event in renderable:
        operation = event.operation_id
        if operation is None:
            items.append(
                TimelineItem(
                    occurred_at=event.occurred_at,
                    created_at=event.created_at,
                    sort_key=str(event.id),
                    item_type=event.event_type,
                    event=event,
                    events=(event,),
                )
            )
            continue
        groups.setdefault(operation, _Group()).events.append(event)

    for group in groups.values():
        # The event stream is ordered newest first, so the last one appended is
        # the earliest — and a save's own moment is when it started.
        ordered = list(reversed(group.events))
        anchor: Any = group.entry or (ordered[0] if ordered else None)
        if anchor is None:  # pragma: no cover — a group always has one or the other
            continue
        head = ordered[0] if ordered else None
        items.append(
            TimelineItem(
                occurred_at=(
                    group.entry.occurred_at if group.entry is not None else ordered[0].occurred_at
                ),
                created_at=anchor.created_at,
                sort_key=str(anchor.id),
                item_type=group.entry.kind if group.entry is not None else ordered[0].event_type,
                entry=group.entry,
                event=head,
                events=tuple(ordered),
                summary_verbs=_verbs_for(group.entry, ordered),
            )
        )

    # Deterministic ordering: the visible time first, then when it was recorded,
    # then the time-sortable id. Without the last two, two things written in the
    # same minute could swap places between page loads and pagination could
    # repeat or skip a line.
    items.sort(key=lambda item: (item.occurred_at, item.created_at, item.sort_key), reverse=True)

    page = items[offset : offset + limit]
    has_more = len(items) > offset + limit
    return page, has_more
