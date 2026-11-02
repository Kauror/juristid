"""Which recorded events a «Majanduspoliitika ülevaade» may propose (Decision B).

The owner decided the source map on 2026-11-02 and this module is its only
implementation (docs/adr/0199 §1). Every event is something the Chamber
*did*, on a business date the record itself carries, read as the requesting
user:

1. **A send** — `Submission.objects.visible_to(user).historically_sent()`:
   SENT, WITHDRAWN or SUPERSEDED with a ``sent_at``, never a DRAFT. A
   withdrawn or superseded send still went out on its day (docs/adr/0092 §3),
   and says so in its label. An official «märkusi ja ettepanekuid ei ole»
   answer (`NO_OBJECTION_RESPONSE`, docs/adr/0174) is included under its own
   label: it is an answer, not an opinion.
2. **A recorded Chamber activity** — a live `Entry` of kind MEETING, CALL,
   HEARING, WORKING_GROUP or JOINT_COORDINATION whose ``occurred_at`` is not
   in the future. An entry dated ahead is a plan, and the calendar reaching
   it proves nothing. NOTE and OTHER are excluded: a note is narrative about
   the file, and «Muu» is too broad to be read as substantive work without a
   person saying so — the reader adds the topic by hand instead.
3. **A published statement attributable to Koda** — a live PUBLIC_STATEMENT
   `Entry`, and a PUBLISHED `MatterWebsiteOverview` whose kind is
   «Ülevaade» (Koda's own `Hetkel käsil` page, docs/adr/0142 §C) with a
   recorded ``published_on``. An «Uudis» is a news item that may be anybody's
   article *about* Koda, and a page whose day nobody wrote down has no date to
   put in the report (docs/adr/0089 §8).
4. **A historical Excel `VÄLJA` date** — `CurrentRegisterState` with
   ``opinion_sent_state = DATE``. Labelled as what it is, «Exceli
   VÄLJA-kuupäev», with its sheet and row. Suppressed only where the same
   send is canonically recorded: a visible sent `Submission` on that Matter on
   that Tallinn day, or the 2026 pilot's placeholder Submission imported from
   the same sheet and row (`ExcelPilotImport`). Where a send exists on
   another day the two cannot be told apart reliably, so both stay and the
   `VÄLJA` event carries a flag the preview shows. «ei saatnud» and an
   unreadable mark are never events.

Never: an audit event, a creation or modification time, a draft, the absence
of an opinion, another organisation's position (`MatterExternalPosition`, a
feedback reply), or anything planned or cancelled.

**Who it went to (KELLELE)** is the Submission's ADDRESSEE rows only, joined in
organisation-name order — never a «teadmiseks» copy and never a meeting's
counterpart (which goes into the explanation instead). A `VÄLJA` event names
`Matter.addressee_organisation` only when the same register row's `KELLELE`
names exactly that one organisation; otherwise it is blank.

**The explanation (SELGITUS)** is prefilled from human-authored text only: a
Submission's ``summary``; an Entry's ``body`` as plain text, after its
organisation; an overview's title and address. Nothing is generated, nothing
is summarised, and «Kojal ei olnud kommentaare…» is never written for anybody.

Identity is canonical: ``(type, uuid)``, never a title. Every query is scoped
by the model's own ``visible_to`` *and* by the reader's visible, real (not
TEST) Matters, so a restricted child on a visible Matter is absent — not
counted, not flagged, not deduplicated against.
"""

from __future__ import annotations

import hashlib
import uuid
from collections import defaultdict
from collections.abc import Iterable
from dataclasses import dataclass, field
from datetime import date, datetime

from django.db.models import QuerySet
from django.utils import timezone

from app.core.dates import format_estonian_date, local_day_range
from app.core.richtext import plain_text
from app.legacy_import.current_state import CurrentRegisterState
from app.legacy_import.models import ExcelPilotImport
from app.legacy_import.register_semantics import AddresseeCardinality, OpinionSentState
from app.matters.entry_enums import EntryKind
from app.matters.enums import WebsiteOverviewKind
from app.matters.models import Entry, Matter, MatterWebsiteOverview
from app.submissions.enums import SubmissionKind, SubmissionStatus
from app.submissions.models import ADDRESSEE_ROWS, Submission, addressee_prefetch
from app.workflow.dates import format_at_precision
from app.workflow.enums import DatePrecision

#: How long a prefilled or typed explanation may be. A report cell, not an
#: essay; the same bound applies to what a person types (`draft.TEXT_LIMIT`).
TEXT_LIMIT = 2000

#: Recorded Chamber activities (rule 2). NOTE and OTHER are not here: see the
#: module docstring.
ACTIVITY_KINDS: tuple[str, ...] = (
    EntryKind.MEETING,
    EntryKind.CALL,
    EntryKind.HEARING,
    EntryKind.WORKING_GROUP,
    EntryKind.JOINT_COORDINATION,
)

#: Published statements (rule 3).
STATEMENT_KINDS: tuple[str, ...] = (EntryKind.PUBLIC_STATEMENT,)

#: What a `VÄLJA` event is called, everywhere it is shown.
REGISTER_LABEL = "Exceli VÄLJA-kuupäev"

#: What a published overview is called.
OVERVIEW_LABEL = "Ülevaade avaldatud"

#: Later states of a send, said beside its kind so a withdrawn opinion is not
#: read as one that still stands.
LATER_STATUS_NOTES: dict[str, str] = {
    SubmissionStatus.WITHDRAWN: "hiljem tagasi võetud",
    SubmissionStatus.SUPERSEDED: "hiljem asendatud",
}


class EventType:
    """The one-letter prefix of an event's canonical identity."""

    SUBMISSION = "S"
    ENTRY = "E"
    OVERVIEW = "W"
    REGISTER = "V"

    ALL: tuple[str, ...] = (SUBMISSION, ENTRY, OVERVIEW, REGISTER)


#: Within one day, sends first, then activities, statements, the register.
_TYPE_ORDER = {kind: index for index, kind in enumerate(EventType.ALL)}


@dataclass(frozen=True, order=True)
class EventRef:
    """``(type, uuid)`` — an event's identity, and nothing else."""

    type: str
    id: uuid.UUID

    @property
    def key(self) -> str:
        """``S0193…`` — usable in a form field name and a checkbox value."""
        return f"{self.type}{self.id}"

    @classmethod
    def parse(cls, raw: str) -> EventRef | None:
        value = (raw or "").strip()
        if len(value) < 2 or value[0] not in EventType.ALL:
            return None
        try:
            return cls(value[0], uuid.UUID(value[1:]))
        except ValueError:
            return None


@dataclass(frozen=True)
class ReportEvent:
    """One qualifying event, resolved for one reader.

    Every field is read from the canonical record; none is taken from a form.
    ``fingerprint_parts`` is what the block fingerprint hashes — the facts a
    reader saw in the preview and would see in the document.
    """

    ref: EventRef
    matter_id: uuid.UUID
    day: date
    precision: str
    kind_label: str
    recipients: tuple[str, ...] = ()
    source_text: str = ""
    #: Where it came from, for a `VÄLJA` event: «leht 2026, rida 14».
    provenance: str = ""
    #: The preview's warning that this may be the same send as another record.
    uncertainty: str = ""

    @property
    def date_label(self) -> str:
        return format_at_precision(self.day, self.precision)

    @property
    def recipients_label(self) -> str:
        return ", ".join(self.recipients)

    @property
    def sort_key(self) -> tuple[date, int, str]:
        return (self.day, _TYPE_ORDER.get(self.ref.type, 99), str(self.ref.id))

    def fingerprint_parts(self) -> tuple[str, ...]:
        return (
            self.ref.key,
            self.day.isoformat(),
            self.precision,
            self.kind_label,
            "|".join(self.recipients),
            self.source_text,
            self.provenance,
            self.uncertainty,
        )


@dataclass
class MatterEvents:
    """One visible Matter and its qualifying events in the period."""

    matter_id: uuid.UUID
    title: str
    events: list[ReportEvent] = field(default_factory=list)

    @property
    def first_day(self) -> date | None:
        return min((event.day for event in self.events), default=None)


def bounded(text: str, limit: int = TEXT_LIMIT) -> str:
    """``text`` cut to ``limit`` characters, marked when it was cut."""
    text = (text or "").strip()
    if len(text) <= limit:
        return text
    return text[: limit - 1].rstrip() + "…"


def text_digest(text: str) -> str:
    """A short digest of a text, to recognise it again without carrying it."""
    return hashlib.sha256((text or "").encode("utf-8")).hexdigest()[:16]


def readable_matters(user: object) -> QuerySet[Matter]:
    """The Matters a report may name: visible to ``user`` and real data.

    `real_data()` because TEST records «ei kuulu päris aruandlusse»
    (`Matter.data_class`); `visible_to` because nothing is reported that the
    reader may not open. Deleted Matters are already absent (the manager).
    """
    return Matter.objects.visible_to(user).real_data()


def collect(
    user: object,
    start: date,
    end: date,
    *,
    now: datetime | None = None,
    matter_ids: Iterable[uuid.UUID] | None = None,
) -> dict[uuid.UUID, MatterEvents]:
    """Every qualifying event dated ``start``–``end`` (Tallinn, inclusive).

    Keyed by Matter, each Matter's events in date order. ``matter_ids``
    narrows the read to those Matters — how a posted draft's blocks are
    re-resolved — and never widens it past :func:`readable_matters`.
    """
    now = now or timezone.now()
    matters = readable_matters(user)
    if matter_ids is not None:
        matters = matters.filter(pk__in=list(matter_ids))
    by_matter: dict[uuid.UUID, list[ReportEvent]] = defaultdict(list)

    for event in _submission_events(user, matters, start, end):
        by_matter[event.matter_id].append(event)
    for event in _entry_events(user, matters, start, end, now):
        by_matter[event.matter_id].append(event)
    for event in _overview_events(user, matters, start, end):
        by_matter[event.matter_id].append(event)
    for event in _register_events(user, matters, start, end):
        by_matter[event.matter_id].append(event)

    titles = dict(matters.filter(pk__in=list(by_matter)).values_list("pk", "title"))
    return {
        matter_id: MatterEvents(
            matter_id=matter_id,
            title=titles[matter_id],
            events=sorted(events, key=lambda event: event.sort_key),
        )
        for matter_id, events in by_matter.items()
        if matter_id in titles
    }


# ---------------------------------------------------------------------------
# The four sources
# ---------------------------------------------------------------------------


def _submission_label(kind: str, status: str) -> str:
    label = str(SubmissionKind(kind).label) if kind in SubmissionKind.values else str(kind)
    note = LATER_STATUS_NOTES.get(status)
    return f"{label} ({note})" if note else label


def _submission_events(
    user: object, matters: QuerySet[Matter], start: date, end: date
) -> list[ReportEvent]:
    rows = (
        Submission.objects.visible_to(user)
        .historically_sent()
        .filter(local_day_range("sent_at", start, end), matter__in=matters)
        .prefetch_related(addressee_prefetch())
        .order_by("sent_at", "id")
    )
    events = []
    for submission in rows:
        if submission.sent_at is None:  # excluded by historically_sent()
            continue
        events.append(
            ReportEvent(
                ref=EventRef(EventType.SUBMISSION, submission.pk),
                matter_id=submission.matter_id,
                # The Tallinn day, whatever the precision: a DATE send is
                # stored as Tallinn midnight and a report never prints a time.
                day=timezone.localdate(submission.sent_at),
                precision=DatePrecision.EXACT,
                kind_label=_submission_label(submission.kind, submission.status),
                recipients=tuple(
                    row.organisation.name for row in getattr(submission, ADDRESSEE_ROWS)
                ),
                source_text=bounded(submission.summary),
            )
        )
    return events


def _entry_text(entry: Entry) -> str:
    body = plain_text(entry.body)
    if entry.organisation is not None:
        name = entry.organisation.name
        return bounded(f"{name}: {body}" if body else name)
    return bounded(body)


def _entry_events(
    user: object, matters: QuerySet[Matter], start: date, end: date, now: datetime
) -> list[ReportEvent]:
    rows = (
        Entry.objects.visible_to(user)
        .filter(
            local_day_range("occurred_at", start, end),
            kind__in=ACTIVITY_KINDS + STATEMENT_KINDS,
            occurred_at__lte=now,
            matter__in=matters,
        )
        .select_related("organisation")
        .order_by("occurred_at", "id")
    )
    return [
        ReportEvent(
            ref=EventRef(EventType.ENTRY, entry.pk),
            matter_id=entry.matter_id,
            day=timezone.localdate(entry.occurred_at),
            precision=DatePrecision.EXACT,
            kind_label=str(EntryKind(entry.kind).label),
            source_text=_entry_text(entry),
        )
        for entry in rows
    ]


def _overview_events(
    user: object, matters: QuerySet[Matter], start: date, end: date
) -> list[ReportEvent]:
    rows = (
        MatterWebsiteOverview.objects.visible_to(user)
        .published()
        .filter(published_on__gte=start, published_on__lte=end, matter__in=matters)
        .order_by("published_on", "id")
    )
    events = []
    for overview in rows:
        if overview.effective_kind != WebsiteOverviewKind.OVERVIEW:
            continue
        if overview.published_on is None:  # excluded by the filter above
            continue
        text = " — ".join(part for part in (overview.title.strip(), overview.url) if part)
        events.append(
            ReportEvent(
                ref=EventRef(EventType.OVERVIEW, overview.pk),
                matter_id=overview.matter_id,
                day=overview.published_on,
                precision=DatePrecision.EXACT,
                kind_label=OVERVIEW_LABEL,
                source_text=bounded(text),
            )
        )
    return events


def _register_addressee(state: CurrentRegisterState) -> tuple[str, ...]:
    """KELLELE for a `VÄLJA` event, or nothing.

    The canonical addressee is shown only when the register row that holds
    this `VÄLJA` date names exactly one organisation in `KELLELE` and it is
    that organisation — then the canonical field and the row agree that this
    is who the opinion went to. Anything else (several bodies, none, a name
    edited since) is blank rather than a guess.
    """
    organisation = state.matter.addressee_organisation
    if organisation is None or state.addressee_cardinality != AddresseeCardinality.SINGLE:
        return ()
    named = state.addressees
    if len(named) == 1 and named[0].strip().casefold() == organisation.name.strip().casefold():
        return (organisation.name,)
    return ()


def _register_events(
    user: object, matters: QuerySet[Matter], start: date, end: date
) -> list[ReportEvent]:
    states = list(
        CurrentRegisterState.objects.visible_to(user)
        .filter(
            opinion_sent_state=OpinionSentState.DATE,
            opinion_sent_date__gte=start,
            opinion_sent_date__lte=end,
            matter__in=matters,
        )
        .select_related("matter__addressee_organisation")
        .order_by("opinion_sent_date", "id")
    )
    if not states:
        return []
    matter_ids = {state.matter_id for state in states}
    # Every send the reader can see on these Matters, on any day: the same
    # day suppresses, another day flags. A send the reader cannot see does
    # neither, so its existence moves nothing on this page.
    visible_sends = Submission.objects.visible_to(user).historically_sent()
    sends: dict[uuid.UUID, list[tuple[date, str]]] = defaultdict(list)
    for matter_id, sent_at, title in visible_sends.filter(matter_id__in=matter_ids).values_list(
        "matter_id", "sent_at", "title"
    ):
        sends[matter_id].append((timezone.localdate(sent_at), title))
    pilot_rows = set(
        ExcelPilotImport.objects.filter(
            matter_id__in=matter_ids, submission__in=visible_sends
        ).values_list("matter_id", "source_sheet", "source_row_number")
    )

    events = []
    for state in states:
        day = state.opinion_sent_date
        if day is None:  # excluded by the filter above
            continue
        known = sends.get(state.matter_id, [])
        if any(sent_on == day for sent_on, _ in known):
            continue
        if (state.matter_id, state.source_sheet, state.source_row_number) in pilot_rows:
            continue
        uncertainty = ""
        if known:
            nearest_day, nearest_title = min(
                known, key=lambda send: (abs((send[0] - day).days), send[0], send[1])
            )
            uncertainty = (
                f"Võib korrata arvamust «{nearest_title}» ({format_estonian_date(nearest_day)}): "
                "registri kuupäev ja saadetud arvamuse kuupäev erinevad."
            )
        events.append(
            ReportEvent(
                ref=EventRef(EventType.REGISTER, state.pk),
                matter_id=state.matter_id,
                day=day,
                precision=DatePrecision.EXACT,
                kind_label=REGISTER_LABEL,
                recipients=_register_addressee(state),
                provenance=_provenance(state),
                uncertainty=uncertainty,
            )
        )
    return events


def _provenance(state: CurrentRegisterState) -> str:
    parts = []
    if state.source_sheet:
        parts.append(f"leht {state.source_sheet}")
    if state.source_row_number:
        parts.append(f"rida {state.source_row_number}")
    return ", ".join(parts)
