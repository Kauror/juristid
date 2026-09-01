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
from dataclasses import dataclass, field, replace
from datetime import datetime
from typing import Any

from django.db import models

from app.audit.enums import ChangeEventType
from app.audit.models import ChangeEvent
from app.audit.visibility import scope_change_events
from app.matters.entry_enums import EntryKind
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


def _join(verbs: Any) -> str:
    """ "lisas märkuse, lisas dokumendi ja määras järgmise sammu"."""
    parts = list(verbs)
    if not parts:
        return ""
    if len(parts) == 1:
        return parts[0]
    return f"{', '.join(parts[:-1])} ja {parts[-1]}"


#: Which dot the spine draws, and therefore what kind of thing this line is.
#: Four, because there are four answers a reader wants at a glance: somebody
#: wrote something, Koda sent something, somebody met somebody, or the
#: application recorded a change (design handoff 1b).
MARKER_ENTRY = "entry"
MARKER_SENT = "sent"
MARKER_MEETING = "meeting"
MARKER_SYSTEM = "sys"

#: Entry kinds that are a room with people in it. `Istung` and `Töörühm` are
#: meetings whatever the vocabulary calls them, and a spine that marked only
#: `Kohtumine` would draw the same file's three meetings three different ways.
_MEETING_KINDS: frozenset[str] = frozenset(
    {
        EntryKind.MEETING.value,
        EntryKind.HEARING.value,
        EntryKind.WORKING_GROUP.value,
    }
)


@dataclass(frozen=True)
class TimelineNextStep:
    """What one save decided, as its own strip under what the save said.

    The sentence and the date, and no stored kind. `mode` was carried here for
    one round after the chronology stopped printing it, on the reasoning that
    the register and the reporting surfaces still asked for the classification;
    neither does now, and a display read model holding a value with no reader is
    how one comes back (ADR 0052 §6, ADR 0054).

    Read off the `NextAction` the event points at rather than off the event's
    payload, so the date prints at the precision it was recorded to. The payload
    carries an anchor date and no precision, and rendering `01.09` for an
    action somebody recorded as *september 2026* would manufacture a day nobody
    named (master specification 3.5).
    """

    text: str
    date_label: str
    date_value: str

    @property
    def date_line(self) -> str:
        """``21.08``, or empty when the step carries no date.

        The date, with no word in front of it saying which of three things it
        is. `date_label` is still read off the action for anything that wants
        it; this strip sits directly under the sentence it dates and does not
        (ADR 0052 §6).
        """
        return self.date_value


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
    #: What this save decided, when it decided anything. Attached after the
    #: page is assembled, in one query for the whole page.
    next_step: TimelineNextStep | None = None

    @property
    def is_entry(self) -> bool:
        return self.entry is not None

    @property
    def marker(self) -> str:
        """Which dot the spine draws beside this line.

        Presentation only: nothing downstream decides authorization, membership
        or ordering from it. A grouped save with no note is still somebody's
        act and keeps the entry marker, which is the same distinction the muted
        system row exists to make (design handoff 1b).
        """
        if self.entry is not None:
            return MARKER_MEETING if self.entry.kind in _MEETING_KINDS else MARKER_ENTRY
        if self.event is not None and self.event.event_type == ChangeEventType.SUBMISSION_SENT:
            return MARKER_SENT
        return MARKER_ENTRY if self.is_grouped else MARKER_SYSTEM

    @property
    def is_system(self) -> bool:
        """Something the application recorded, rather than something a person
        wrote. These are what collapse into one row when several sit together."""
        return self.marker == MARKER_SYSTEM

    @property
    def kind_label(self) -> str:
        """The badge beside the author. Empty where the sentence says it."""
        if self.entry is not None:
            return str(self.entry.get_kind_display())
        if self.event is not None and self.event.event_type == ChangeEventType.SUBMISSION_SENT:
            return "Väljasaadetud · arvamus"
        return ""

    @property
    def actor(self) -> Any:
        """Whoever the line belongs to, from whichever record carries them."""
        if self.entry is not None and self.entry.author is not None:
            return self.entry.author
        return self.event.actor if self.event is not None else None

    @property
    def excerpt_source(self) -> str:
        """The text the closed accordion quotes. Sanitised HTML from the entry,
        which the template strips — never a summary line, which would quote the
        application back at the reader instead of the colleague."""
        return self.entry.body if self.entry is not None else ""

    @property
    def is_grouped(self) -> bool:
        """Whether this line stands for more than one canonical record."""
        return len(self.events) > 1 or bool(self.entry and self.events)

    @property
    def besides_the_note(self) -> str:
        """What one save did *apart from* writing the note it is showing.

        The line already carries the entry's kind badge and, underneath, the
        note itself — so repeating "lisas märkuse" beside them is the same fact
        three times. Everything else the save did is not visible anywhere else
        on the line and stays: "lisas dokumendi ja lisas kaasamise".

        Empty for a save that only wrote a note, which is the ordinary case
        (Teema redesign §11.1, design handoff 1b).
        """
        return _join(verb for verb in self.summary_verbs if verb != "lisas märkuse")

    @property
    def summary_sentence(self) -> str:
        """ "lisas märkuse ja määras järgmise sammu", or an empty string.

        Built from the verbs rather than stored, so a save that wrote three
        records reads as one sentence and a save that wrote one reads as
        nothing at all — the entry card already says what it is.
        """
        return _join(self.summary_verbs)


@dataclass(frozen=True)
class TimelineRow:
    """One line on screen: either a single item, or a run of system events.

    The chronology is read for what colleagues did. A stage corrected, a date
    moved and a Matter assigned are all true and none of them is why anybody
    opened the page, so several of them sitting together fold into one line that
    says how many there are and offers to show them. Nothing is dropped: the
    run is a `<details>` and everything inside it renders exactly as it did
    (design handoff 1b).
    """

    items: tuple[TimelineItem, ...]

    @property
    def is_run(self) -> bool:
        return len(self.items) > 1

    @property
    def item(self) -> TimelineItem:
        return self.items[0]

    @property
    def count(self) -> int:
        return len(self.items)

    @property
    def kinds(self) -> str:
        """The kinds inside the run, once each, in the order they appear.

        "hetkeseis, arvamuse tähtaeg" rather than a bare count: a reader
        deciding whether to open it needs to know what is in there.
        """
        seen: list[str] = []
        for item in self.items:
            label = str(item.event.get_event_type_display()) if item.event else ""
            if label and label not in seen:
                seen.append(label)
        return ", ".join(seen)

    @property
    def span(self) -> str:
        """``30.07–05.08``. One date when the run covers a single day."""
        from app.core.dates import short_range

        days = [item.occurred_at.date() for item in self.items]
        return short_range(min(days), max(days))


def latest_authored(items: list[TimelineItem]) -> TimelineItem | None:
    """The newest line a colleague wrote, for the closed accordion's quote.

    An authored entry, not merely the newest item. Quoting a stage change back
    at somebody as "the last thing that happened here" is the application
    talking about itself, and the closed row exists to answer *what did we last
    say about this file* (design handoff 1b).
    """
    return next((item for item in items if item.is_entry), None)


#: How many system events have to sit together before folding them is worth it.
#: One on its own is a line; two are a pair the reader has to scroll past.
SYSTEM_RUN_MINIMUM = 2


def collapse_system_runs(items: list[TimelineItem]) -> list[TimelineRow]:
    """Group each run of adjacent system events into one row.

    Adjacency in the rendered order, not in the database. Two stage changes with
    a colleague's note between them are two separate runs, because the note is
    what the reader came for and folding across it would hide the shape of the
    file's month.
    """
    rows: list[TimelineRow] = []
    run: list[TimelineItem] = []

    def flush() -> None:
        if not run:
            return
        if len(run) >= SYSTEM_RUN_MINIMUM:
            rows.append(TimelineRow(tuple(run)))
        else:
            rows.extend(TimelineRow((one,)) for one in run)
        run.clear()

    for item in items:
        if item.is_system:
            run.append(item)
            continue
        flush()
        rows.append(TimelineRow((item,)))
    flush()
    return rows


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


#: What the chronology's `Kõik ▾` control offers.
#:
#: Two axes and nothing finer. "What did people write" and "what happened to the
#: file" are the two questions somebody scrolling six months of work actually
#: has; a filter per event type would be a filter nobody reads
#: (Teema redesign §21).
TIMELINE_FILTER_ALL = "koik"
TIMELINE_FILTER_ENTRIES = "sissekanded"
TIMELINE_FILTER_EVENTS = "sundmused"

TIMELINE_FILTERS: tuple[tuple[str, str], ...] = (
    (TIMELINE_FILTER_ALL, "Kõik"),
    (TIMELINE_FILTER_ENTRIES, "Sissekanded"),
    (TIMELINE_FILTER_EVENTS, "Sündmused"),
)


def matter_timeline(
    *,
    matter: Matter,
    user: Any,
    limit: int = 50,
    offset: int = 0,
    only: str = TIMELINE_FILTER_ALL,
) -> tuple[list[TimelineItem], bool]:
    """Return one page of the timeline, newest first.

    Entries are filtered through their own visibility so a restricted entry
    inside an otherwise visible Matter stays hidden. The change-event stream is
    scoped to this Matter, which the caller has already proven the user may
    read.

    ``only`` filters what is *shown*, never what is grouped: a save that wrote
    a note and set the next step is one action, and the entry filter shows it
    with its facts rather than tearing it in half.

    Returns the page and whether more items exist.
    """
    # Fetch one extra of each so "is there more" needs no second count query.
    window = offset + limit + 1

    entries: list[Entry] = []
    if only != TIMELINE_FILTER_EVENTS:
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
    # Scoped by the child each row is *about*, not only by the Matter it hangs
    # off. `EVIDENCE_VERSION_ADDED` carries a filename, `NEXT_ACTION_SET` the
    # step's text — and a restricted document properly hidden from Dokumendid
    # was still naming itself here, because the row describing it was selected
    # by the Matter alone (AUTH-003, app/audit/visibility.py).
    events = list(
        scope_change_events(ChangeEvent.objects.filter(matter=matter), user)
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
    if only == TIMELINE_FILTER_ENTRIES:
        items = [item for item in items if item.is_entry]

    items.sort(key=lambda item: (item.occurred_at, item.created_at, item.sort_key), reverse=True)

    page = items[offset : offset + limit]
    has_more = len(items) > offset + limit
    return _with_next_steps(page, user), has_more


def _with_next_steps(page: list[TimelineItem], user: Any) -> list[TimelineItem]:
    """Attach «→ … · 21.08» to every save that decided one.

    One query for the whole page, not one per line. The step is read from the
    `NextAction` rows the events point at rather than from the event payloads,
    for two reasons: the payload carries an anchor date and no precision, so an
    action recorded as *september 2026* would print as `01.09`; and the rows go
    through `visible_to`, so a step restricted below its Matter contributes
    nothing here either (AUTH-003, master specification 3.5).
    """
    from app.workflow.models import NextAction

    def action_of(item: TimelineItem) -> Any:
        return next(
            (
                event.object_id
                for event in item.events
                if event.event_type == ChangeEventType.NEXT_ACTION_SET and event.object_id
            ),
            None,
        )

    wanted = {key for key in (action_of(item) for item in page) if key is not None}
    if not wanted:
        return page

    steps = {
        action.pk: TimelineNextStep(
            text=action.text,
            date_label=action.date_label,
            date_value=action.display_date if action.target_date else "",
        )
        for action in NextAction.objects.filter(pk__in=wanted).visible_to(user)
    }

    resolved = []
    for item in page:
        step = steps.get(action_of(item))
        resolved.append(replace(item, next_step=step) if step is not None else item)
    return resolved
