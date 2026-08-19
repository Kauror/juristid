"""The professional timeline.

One projection over two sources: authored ``Entry`` records and the selected
``ChangeEvent`` rows that a lawyer would actually want to see. There is no third
history model, and ``SecurityAuditEvent`` never appears here — access and
download traces are a compliance record, not professional chronology
(master specification 16.5).
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any

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

#: An entry the composer just created also produces an ENTRY_ADDED change event.
#: The entry itself is the richer of the two, so the event is not rendered
#: again — otherwise every note would appear twice.
SUPPRESSED_WHEN_ENTRY_SHOWN: frozenset[str] = frozenset(
    {ChangeEventType.ENTRY_ADDED, ChangeEventType.ENTRY_EDITED}
)


@dataclass(frozen=True)
class TimelineItem:
    """One rendered line. ``occurred_at`` is what the reader sees."""

    occurred_at: datetime
    created_at: datetime
    sort_key: str
    item_type: str
    entry: Entry | None = None
    event: ChangeEvent | None = None

    @property
    def is_entry(self) -> bool:
        return self.entry is not None


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

    events = list(
        ChangeEvent.objects.filter(matter=matter, event_type__in=TIMELINE_EVENT_TYPES)
        .exclude(event_type__in=SUPPRESSED_WHEN_ENTRY_SHOWN)
        .select_related("actor")
        .order_by("-occurred_at", "-created_at", "-id")[:window]
    )

    items: list[TimelineItem] = [
        TimelineItem(
            occurred_at=entry.occurred_at,
            created_at=entry.created_at,
            sort_key=str(entry.id),
            item_type=entry.kind,
            entry=entry,
        )
        for entry in entries
    ]
    items.extend(
        TimelineItem(
            occurred_at=event.occurred_at,
            created_at=event.created_at,
            sort_key=str(event.id),
            item_type=event.event_type,
            event=event,
        )
        for event in events
    )

    # Deterministic ordering: the visible time first, then when it was recorded,
    # then the time-sortable id. Without the last two, two things written in the
    # same minute could swap places between page loads and pagination could
    # repeat or skip a line.
    items.sort(key=lambda item: (item.occurred_at, item.created_at, item.sort_key), reverse=True)

    page = items[offset : offset + limit]
    has_more = len(items) > offset + limit
    return page, has_more
