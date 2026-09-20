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
from collections.abc import Sequence
from dataclasses import dataclass, field, replace
from datetime import date, datetime
from typing import Any

from django.db import models
from django.utils import timezone

from app.audit.enums import ChangeEventType
from app.audit.models import ChangeEvent
from app.audit.visibility import scope_change_events
from app.core.dates import format_estonian_date
from app.matters import selectors
from app.matters.entry_enums import EntryKind
from app.matters.enums import EngagementKind
from app.matters.models import (
    Entry,
    Matter,
    MatterEngagement,
    MatterExternalPosition,
    MatterProceduralDevelopment,
    MatterWebsiteOverview,
)
from app.submissions.enums import RecipientRole
from app.submissions.links import linked_submissions_by_overview
from app.submissions.models import Submission, SubmissionRecipient
from app.workflow.dates import format_at_precision

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
    ChangeEventType.SUBMISSION_WITHDRAWN,
    ChangeEventType.EVIDENCE_VERSION_ADDED,
    ChangeEventType.MATTER_CLOSED,
    ChangeEventType.MATTER_REOPENED,
)

#: **`SUBMISSION_SENT` is deliberately not on that list since docs/adr/0092.**
#:
#: A sent `Koja arvamus` is a canonical record — `Submission` — and it is read
#: as one by :func:`projected_milestones`, like every other structured fact on
#: this page. The event said the same thing from one step further away: its
#: `occurred_at` is the moment somebody pressed the button, which for an opinion
#: reconstructed from the historical register is a fact about the import and not
#: about the letter, and its addressees were a copy in a payload rather than the
#: `SubmissionRecipient` rows the record holds. Reading the record fixes both and
#: removes the last place a business date on this page came out of an audit row
#: (docs/adr/0092 §3).

#: Events that say **which operation wrote a projected record**, and are never
#: rendered themselves.
#:
#: One lawyer act can write several canonical records: `+ Menetluse areng` files
#: the development, moves `Hetkeseis` and sets `Järgmiseks` in one transaction
#: and one `composer_operation`. Three canonical writes, three audit rows, and
#: — until docs/adr/0092 — three chronology lines for one thing a person did.
#:
#: The record itself carries no operation column, deliberately: an operation is
#: an audit fact about how something was written, not a property of the fact
#: (`ChangeEvent.operation_id`). So the row that ties the development to the
#: stage change is its own `PROCEDURAL_DEVELOPMENT_RECORDED` event, read here and
#: rendered nowhere — exactly the role `ENTRY_ADDED` already plays for a note.
#:
#: **Only an explicit operation identifier groups anything.** Not a shared
#: minute, not a shared author, not a similar title, not the same organisation
#: and not a matching filename. There is no time window and no prose matching
#: here, and adding one would be manufacturing a relationship nobody recorded
#: (docs/adr/0092 §6).
RECORD_OPERATION_EVENT_TYPES: tuple[str, ...] = (ChangeEventType.PROCEDURAL_DEVELOPMENT_RECORDED,)

#: What a save decided **as a consequence of the record it was saved with**.
#:
#: A stage change and a next step written inside a `+ Menetluse areng` save are
#: not two further acts; they are what that one act did to the file, and they
#: read as two short lines under its row rather than as two rows of their own.
#:
#: Folded **only** onto a record this reader can actually see. A development
#: restricted below its Matter contributes no row, so its stage change stands
#: alone exactly as a stage change recorded from the header does — which is what
#: it is, and which says nothing about a record nobody may read
#: (AUTH-003, docs/adr/0092 §7).
OPERATION_EFFECT_EVENT_TYPES: tuple[str, ...] = (
    ChangeEventType.MATTER_STAGE_CHANGED,
    ChangeEventType.NEXT_ACTION_SET,
)

#: **Nothing, since the approved target** — and kept as a name so the reasoning
#: survives. The two facts below were read here and rendered as a *clause* on the
#: save that produced them, never a row, because each had a standing section on
#: the Matter page showing it. Those sections are gone and
#: :func:`projected_milestones` now gives each canonical record a row of its own,
#: which makes the clause the duplicate: «Marko lisas märkuse ja lisas kaasamise»
#: directly above «Kaasamine: liikmed» states one act twice. So they are no
#: longer read at all, and the fact is stated exactly once, off the record that
#: owns it (docs/adr/0074 §14, superseding Stage-2G brief 34, Agent-F brief 20).
GROUPED_ONLY_EVENT_TYPES: tuple[str, ...] = ()

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
#: Two verbs went when the approved target gave those facts rows of their own.
#:
#: `lisas olulise tähtaja` and `lisas kaasamise` used to be clauses precisely
#: because the deadline and the consultation had standing sections showing them —
#: a row would have been the same fact twice. Those sections are gone, the
#: canonical records are projected into the chronology as milestones, and the
#: clause is now the duplicate: «Marko lisas märkuse ja lisas kaasamise» directly
#: above «Kaasamine: liikmed» says one act twice (docs/adr/0074 §14).
#:
#: `märkis arvamuse saadetuks` and `lõpetas teema` went for the same reason:
#: `SUBMISSION_SENT` and `MATTER_CLOSED` are milestone rows now.
_CLAUSES: tuple[tuple[str, str], ...] = (
    (ChangeEventType.EVIDENCE_VERSION_ADDED, "lisas dokumendi"),
    (ChangeEventType.NEXT_ACTION_SET, "määras järgmise sammu"),
    (ChangeEventType.NEXT_ACTION_COMPLETED, "märkis eelmise sammu tehtuks"),
)

#: The events that draw a 12 px accent dot: things that happened *to the file*,
#: as opposed to work somebody did on it.
#:
#: Each one takes a row of its own even when it shares a composer operation with
#: an entry, which is the one place this projection deliberately does not group.
#: A save that wrote a note and changed the stage did two separable things to the
#: record, and the target shows them as two rows — the note under its author, the
#: stage as a milestone — rather than as one line with a clause
#: (TEEMA_TARGET_SPEC §E, docs/adr/0074 §14).
MILESTONE_EVENT_TYPES: frozenset[str] = frozenset(
    {
        ChangeEventType.MATTER_CREATED,
        ChangeEventType.MATTER_STAGE_CHANGED,
        ChangeEventType.SUBMISSION_WITHDRAWN,
        ChangeEventType.MATTER_CLOSED,
        ChangeEventType.MATTER_REOPENED,
    }
)

#: What a milestone event is called on the chronology. The target's own words
#: where it has them — `Arvamus välja`, `Teema loodud` — and the audit
#: vocabulary's where it does not, because inventing a second name for an event
#: type is how two surfaces start describing one act differently.
_MILESTONE_LABELS: dict[str, str] = {
    ChangeEventType.MATTER_CREATED.value: "Teema loodud",
}

#: What a sent `Koja arvamus` is called on the chronology.
#:
#: `Arvamus välja` and not `Koja arvamus`: the strip above names the *thing*, and
#: the chronology names what happened to it — «välja» is what happened to the
#: letter. The two words were settled when the strip was built and are not
#: reopened here; what changed in docs/adr/0092 is where the row is read from,
#: not what it is called (app/matters/process_timeline.py `SENT_LABEL`).
SUBMISSION_MILESTONE = "Arvamus välja"

#: What a published or cancelled `Ülevaade / uudis` is called on the chronology,
#: and what its link says.
#:
#: Named here rather than written into `projected_milestones` twice, because the
#: published row and the cancelled one have to agree — a rename that reached one
#: and not the other would put two names for one activity on one page. The link
#: says `Ava ülevaade või uudis` rather than naming a site: since docs/adr/0085
#: §2 the address may be anywhere on the public web, and `Ava kodulehel` would
#: have promised a page on koda.ee that the row no longer guarantees.
WEBSITE_OVERVIEW_MILESTONE = "Ülevaade / uudis"
WEBSITE_OVERVIEW_LINK_LABEL = "Ava ülevaade või uudis"

#: What a published row prints where its publication date would go, when nobody
#: knows what that date is.
#:
#: The same three words a `Väline seisukoht` with no `stated_on` prints, and
#: deliberately the same: they are the same fact about the file — this happened,
#: and the day it happened is not recorded — and two spellings of it on one
#: chronology would read as two different situations. Since docs/adr/0089 §8 a
#: publication may legitimately be in this state.
#:
#: **Never `created_at` and never the day the row happens to sort on.** Printing
#: the day somebody typed the address in, beside «Ülevaade / uudis», would state
#: that the page went up that day — a fact about somebody else's website,
#: invented by this application (docs/adr/0089 §10).
WEBSITE_OVERVIEW_DATE_UNKNOWN = "Kuupäev teadmata"


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
class ChronologyLink:
    """One external address, named by what it is rather than by where it goes.

    `Smaily`, `Alchemer` — the provider, not the URL. A campaign address is
    mostly tracking parameters and a recipient token; printing it would push the
    row apart and put somebody's one-time key on the page in readable text. The
    label says which tool holds the material, and the address is where the link
    goes (docs/adr/0027, amended 2026-09-12).
    """

    label: str
    url: str


@dataclass(frozen=True)
class ChronologyMilestone:
    """A 12 px accent row: something that happened to the file.

    ``what`` is the headline — `Töövõit`, `Hetkeseis: Valitsuses`,
    `Kaasamine: liikmed`. ``sub`` is the optional second line, and ``file_url``
    turns part of it into a link to the exact bytes.

    Milestones carry a **date, never a clock time**. A work entry says when
    somebody wrote it because two notes on one afternoon need separating; a
    commencement or a stage change happened on a day (TEEMA_TARGET_SPEC §E).

    ``links`` is empty for every milestone but a `Kaasamine` that was given one.
    A record whose working material lives in a mailing tool is only useful if a
    colleague can still reach it, and the chronology row is where that
    engagement is read.
    """

    what: str
    display_date: str
    sub: str = ""
    file_url: str = ""
    file_label: str = ""
    links: tuple[ChronologyLink, ...] = ()
    #: **This office's own words, and never the source's.**
    #:
    #: Set by a `Väline seisukoht` and by a `Menetluse areng`, each carrying its
    #: own `Juristi märkus`. It is a
    #: field of its own rather than another clause appended to :attr:`sub`
    #: because the whole reason the column exists is that «MKM toetab varianti B»
    #: and «nende põhjendus ei arvesta liikmete kulumõjuga» must not become one
    #: sentence attributed to the ministry — and a `sub` that concatenated them
    #: would be exactly that, with a separator (docs/adr/0091 §4).
    #:
    #: The surface renders it under :attr:`own_note_label` on its own line.
    #: Nothing here decides how it looks; what is decided here is that it is not
    #: part of the source's statement.
    own_note: str = ""
    #: What that line is labelled — carried on the milestone rather than looked
    #: up by the template.
    #:
    #: Three templates render a milestone row: the generic chronology item, the
    #: `Väline seisukoht` partial and the `Kaasamine` one, each with a context of
    #: its own and the last two swap targets for their own corrections. A label
    #: in the page context would have to be added to every one of them, and the
    #: day somebody added a fourth the line would render with an empty label —
    #: which is the unattributed paragraph this field exists to prevent.
    #: Travelling with the value is the only shape in which it cannot go missing
    #: (docs/adr/0091 §4).
    own_note_label: str = ""


@dataclass(frozen=True)
class ChronologyFile:
    """One attachment under a work entry: its real name, and a link to its bytes.

    The link is to the ``DocumentVersion``, because `documents:download` is keyed
    on the exact bytes — which is what makes a link to evidence a link to
    evidence rather than to whatever the document holds today
    (templates/matters/partials/opinion_rail.html).
    """

    label: str
    url: str


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
    #: Set on a milestone row and on nothing else. It is the whole switch the
    #: chronology template reads: a row either has one and draws the 12 px accent
    #: dot, or it has not and draws the 6 px muted one. There is no third kind
    #: (TEEMA_TARGET_SPEC §E).
    milestone: ChronologyMilestone | None = None
    #: Files this save captured, attached after the page is assembled in one
    #: query, like ``next_step``.
    files: tuple[ChronologyFile, ...] = ()
    #: The canonical record a projected milestone stands for — the engagement,
    #: the deadline, the commencement, the win. Carried so the explicitly
    #: associated files can be read off it in one query for the whole page; a
    #: row projected from a `ChangeEvent` has none (docs/adr/0075 §10).
    record: Any = None
    #: `Hetkeseis → Kooskõlastusringil`, for a save that moved the file while it
    #: recorded something else.
    #:
    #: The stage label off the `MATTER_STAGE_CHANGED` event that shares this
    #: row's operation, and nothing else: no stage is read off the record, off
    #: the title or off the Matter, because the file's *current* stage is not
    #: what this row happened to do to it. Empty on every row whose operation
    #: moved no stage, which is nearly all of them (docs/adr/0092 §6).
    stage_effect: str = ""

    @property
    def is_milestone(self) -> bool:
        return self.milestone is not None

    @property
    def website_overview(self) -> Any:
        """The `Ülevaade / uudis` this row stands for, when it stands for one.

        A named property rather than the template comparing `item_type` to a
        class name: the chronology offers `Paranda link` on exactly these rows,
        and a string comparison in a template is a rename away from silently
        offering it on none of them.
        """
        return self.record if isinstance(self.record, MatterWebsiteOverview) else None

    @property
    def is_entry(self) -> bool:
        return self.entry is not None

    @property
    def external_position(self) -> Any:
        """The `Väline seisukoht` this row stands for, when it stands for one.

        A named property rather than the template comparing `item_type` to a
        class name: the chronology offers `Muuda` on exactly these rows, and a
        string comparison in a template is a rename away from silently offering
        it on none of them — the reasoning `website_overview` above states.
        """
        return self.record if isinstance(self.record, MatterExternalPosition) else None

    @property
    def submission(self) -> Any:
        """The sent `Koja arvamus` this row stands for, when it stands for one.

        A named property rather than a string comparison on `item_type`, for the
        reason `external_position` below states. Since docs/adr/0092 a sent
        opinion is projected from its own `Submission` rather than from the
        `SUBMISSION_SENT` audit event, so the row carries the record and the
        evidence it holds can be read off it.
        """
        from app.submissions.models import Submission

        return self.record if isinstance(self.record, Submission) else None

    @property
    def procedural_development(self) -> Any:
        """The `Menetluse areng` this row stands for, when it stands for one.

        A named property rather than the template comparing `item_type` to a
        class name, for the reason `external_position` above states: the
        chronology offers `Muuda` on exactly these rows, and a string comparison
        in a template is a rename away from silently offering it on none of them.
        """
        return self.record if isinstance(self.record, MatterProceduralDevelopment) else None

    @property
    def is_engagement(self) -> bool:
        """Whether this milestone is a `Kaasamine`, and therefore correctable.

        Decided here rather than in the template, which cannot ask what kind of
        record it is holding without the question being spelled as a string
        comparison somebody renames a model out from under.
        """
        return isinstance(self.record, MatterEngagement)

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
        if self.submission is not None:
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
        if self.submission is not None:
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


def _local_day(value: datetime) -> date:
    """The calendar day a stored moment falls on *for the reader*.

    Timestamps are stored in UTC and this department works in Europe/Tallinn,
    so anything recorded between midnight and three in the morning belongs to
    the day before by the raw value and to the right day by the clock on the
    wall. Both dates the folded summary prints go through here, so the sentence
    and the span beside it can never name two different days for one event.
    """
    return timezone.localtime(value).date()


@dataclass(frozen=True)
class TimelineRow:
    """One line on screen: either a single item, or a run of system events.

    The chronology is read for what colleagues did. A stage corrected, a date
    moved and a Matter assigned are all true and none of them is why anybody
    opened the page, so several of them sitting together fold into one line that
    says how many there are and offers to show them. Nothing is dropped: the
    run is a `<details>` and everything inside it renders exactly as it did
    (design handoff 1b).

    The closed line says when the file started and how much is folded in, and
    no longer enumerates the event types inside it — see :attr:`summary`.
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
    def created_on(self) -> date | None:
        """The day the Matter was created, when this run is the one holding it.

        ``None`` for every later run. A summary that named a creation date on a
        run of ordinary field changes would be stating a fact about the file
        that the run it summarises does not contain.
        """
        for item in self.items:
            for event in item.events:
                if event.event_type == ChangeEventType.MATTER_CREATED:
                    return _local_day(event.occurred_at)
        return None

    @property
    def summary(self) -> str:
        """``Teema loodud 25.08, tegevusi 3``, or ``Tegevusi 3``.

        What a reader deciding whether to open this row can use: when the file
        started, and how much is folded in here. Not what the application
        called each of them.

        The row said ``3 süsteemimuudatust — Järgmiseks määratud,
        Tõendiversioon lisatud, Teema loodud`` until this pass, which is the
        chronology reciting its own event vocabulary at somebody who has no
        reason to know it — and reciting it at the top of every Matter, since
        the file's own creation is folded into a run on nearly all of them. The
        classifications did not go anywhere: they are inside, on each event's
        own line, which is where a reader who wants them is looking
        (`get_event_type_display` in templates/matters/partials/timeline_items.html).
        """
        created = self.created_on
        if created is None:
            return f"Tegevusi {self.count}"
        from app.core.dates import short_day_month

        return f"Teema loodud {short_day_month(created)}, tegevusi {self.count}"

    @property
    def span(self) -> str:
        """``30.07–05.08``. One date when the run covers a single day."""
        from app.core.dates import short_range

        days = [_local_day(item.occurred_at) for item in self.items]
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


def _milestone_for_event(event: ChangeEvent) -> ChronologyMilestone:
    """What an event-derived milestone row says.

    `Hetkeseis: Valitsuses` is composed from the event's own summary, which
    `change_stage` records as the stage label. Everything else is named by the
    target where it has a name and by the audit vocabulary where it does not —
    a second, prettier name for an event type is how two surfaces start
    describing one act differently (TEEMA_TARGET_SPEC §E).
    """
    if event.event_type == ChangeEventType.MATTER_STAGE_CHANGED:
        what = f"Hetkeseis: {event.summary}" if event.summary else "Hetkeseis muudetud"
        sub = ""
    else:
        what = _MILESTONE_LABELS.get(event.event_type, str(event.get_event_type_display()))
        # The summary of a created Matter is its own title, which is the <h1>
        # forty pixels up the page. Every other milestone's summary says
        # something the headline does not.
        sub = "" if event.event_type == ChangeEventType.MATTER_CREATED else (event.summary or "")
    return ChronologyMilestone(
        what=what,
        display_date=format_estonian_date(_local_day(event.occurred_at)),
        sub=sub,
    )


def _end_of_day(day: date) -> datetime:
    """Where a dated fact sorts among the timestamped ones.

    A milestone knows its day and not its hour, so it takes the last moment of
    that day and sits **above** the work entries written on it. That is the
    reading order the target shows: the day's headline first, then what was done
    around it (TEEMA_TARGET_SPEC §E).
    """
    return timezone.make_aware(datetime.combine(day, datetime.max.time()))


#: What the chronology prints where a `Kaasamine` has no date of its own.
#:
#: Named here because the row and its correction form are rendered from two
#: different places and a sentence spelled twice is a sentence that drifts.
ENGAGEMENT_DATE_UNKNOWN = "Kuupäev teadmata"


def engagement_chronology_day(engagement: MatterEngagement) -> date:
    """Where an engagement's row sits in the chronology.

    Its own date when it has one; the day it was written down when it has not.

    **The fallback places the row and never describes it.** A `Kaasamine` with
    no `occurred_on` is «kuupäev teadmata» (docs/adr/0078 §2), and a row that
    cannot be placed cannot be read — so it goes where it was recorded, which
    is the only day this system knows anything about. What must not happen is
    the two answers being confused: :func:`engagement_milestone` prints
    :data:`ENGAGEMENT_DATE_UNKNOWN` for exactly these rows, because printing
    `created_at` beside «Kaasamine: liikmed» states that the consultation
    happened on the day somebody typed it in, which is the invention this
    release exists to remove.

    **An approximate date places the row on its anchor**, which is the first
    day of the period and is exactly what an anchor is for: a month has to sit
    somewhere in a chronology, and its own first day is the only honest choice
    that keeps *september* before *oktoober*. Here too the placement is not the
    description — :func:`engagement_milestone` prints *oktoober 2026*
    (docs/adr/0079 §2, docs/adr/0082 §4).
    """
    return engagement.occurred_on or _local_day(engagement.created_at)


def engagement_milestone(engagement: MatterEngagement) -> ChronologyMilestone:
    """One `Kaasamine` as the chronology row a reader sees.

    Built here rather than inline in :func:`projected_milestones`, because the
    correction form swaps this one row back in place after a save and the two
    renderings have to be the same rendering — a second copy of the `sub`
    composition is a second place for `Vastuseid` to gain a separator or for
    the deadline to lose its label (`app/matters/views.py`, `_engagement_row`).
    """
    # The channel, **for the rows that were asked for one**.
    #
    # `+ Kaasamine` stopped asking which channel a round used, and every row it
    # writes now carries `OTHER` — the column's own default. Printing «Muu»
    # for those would be the chronology stating a classification nobody chose
    # and nothing reads, which is precisely what the panel stopped collecting;
    # printing nothing is the honest rendering of a question that was not put.
    #
    # A historical `Küsitlus`, `Koosolek`, `Kirjade voor` or `Kaasamiskutse
    # veebis` is a real answer somebody gave and still reads exactly as it did.
    # An older row stored as `OTHER` loses a word that carried no information
    # either way (docs/adr/0086 §1).
    parts = []
    if engagement.kind != EngagementKind.OTHER:
        parts.append(str(engagement.get_kind_display()))
    # `Vastuseid 14`, using the panel's own label rather than a sentence
    # composed here. `response_count` is nullable and NULL means «nobody
    # counted», which is not «nobody answered» — so an uncounted engagement
    # says nothing about responses at all (docs/adr/0074 §5).
    if engagement.response_count is not None:
        parts.append(f"Vastuseid {engagement.response_count}")
    # **The reply-by date is deliberately not here.**
    #
    # docs/adr/0078 §3 put it in this string, after the kind and the response
    # count, and that was right while it was one more recorded fact about the
    # round. It is a *state* now — waiting, due, or finished — with three
    # wordings and a colour of its own, so it is rendered as its own line by
    # `matters/partials/engagement_row.html`. Saying it in both places would
    # state one fact twice on one row (docs/adr/0086 §3, §4).
    sub = " · ".join(parts)
    # Read off the row already in hand — no second query, and nothing here
    # for an engagement that carries neither address, so a row that has no
    # links renders no empty container for them.
    links = tuple(
        ChronologyLink(label=label, url=url)
        for label, url in (
            ("Smaily", engagement.smaily_url),
            ("Alchemer", engagement.alchemer_url),
        )
        if url
    )
    return ChronologyMilestone(
        what=f"Kaasamine: {engagement.title}",
        # The date as it was actually known, or the words «kuupäev teadmata» —
        # never the day the row happens to sit on, and never the anchor of a
        # period. `MatterEngagement.display_date` is `format_at_precision`, so a
        # round recorded as *oktoober 2026* reads that here and not `01.10.2026`
        # (docs/adr/0079 §3, docs/adr/0082 §3). See
        # :func:`engagement_chronology_day` for the other half of the rule.
        display_date=engagement.display_date or ENGAGEMENT_DATE_UNKNOWN,
        sub=sub,
        links=links,
    )


#: What the chronology prints where a `Väline seisukoht` has no date of its own.
#:
#: Its own constant beside :data:`ENGAGEMENT_DATE_UNKNOWN` rather than a shared
#: one: the two happen to be the same four words and are answers to different
#: questions — «when did Koda ask» and «when did they say it» — so a single
#: name would make one record's wording change the other's the day either of
#: them is reworded.
EXTERNAL_POSITION_DATE_UNKNOWN = "Kuupäev teadmata"

#: What the chronology calls a `MatterProceduralDevelopment` row.
#:
#: Named here because the projection, the correction partial and a test all have
#: to agree about it — and because the headline is the *step*, with the record's
#: own title after the colon, so a reader scanning a proceeding sees what happened
#: rather than a label (docs/adr/0091 §5).
#:
#: It read «Menetluse areng» until docs/adr/0097 §6. The launcher stopped
#: offering that word and a lawyer who files a `Märge` has to get a `Märge` back
#: — a capture concept that is retired on the way in and printed on the way out
#: is the same two vocabularies with the seam moved one screen along. The
#: **record** is untouched: same table, same rows, same events, same historical
#: values, and every stored title reads exactly as it was written.
DEVELOPMENT_HEADLINE = "Märge"


#: What the chronology calls the line holding the lawyer's own comment.
#:
#: Named here because the template, the correction partial and a test all have to
#: agree about it, and because the label is what does the work: a paragraph of
#: this office's reading of a ministry's position, printed with no label under a
#: headline naming that ministry, is the attribution defect with better line
#: spacing (docs/adr/0091 §4).
LAWYER_NOTE_LABEL = "Juristi märkus"


def external_position_chronology_day(position: MatterExternalPosition) -> date:
    """Where an external position's row sits in the chronology.

    Its own date when it has one; the day it was written down when it has not —
    the rule :func:`engagement_chronology_day` states, for the same reason. A
    row that cannot be placed cannot be read, and the day it was recorded is the
    only day this system knows anything about.

    **The fallback places the row and never describes it.**
    :func:`external_position_milestone` prints
    :data:`EXTERNAL_POSITION_DATE_UNKNOWN` for exactly these rows, because
    printing `created_at` beside «Väline seisukoht: Rahandusministeerium» would
    state that the ministry said it on the day somebody typed it in — a fact
    about another organisation, invented by this application.

    An approximate date places the row on its anchor, which is the first day of
    the period and is exactly what an anchor is for (docs/adr/0079 §2).
    """
    return position.stated_on or _local_day(position.created_at)


def external_position_milestone(position: MatterExternalPosition) -> ChronologyMilestone:
    """One `Väline seisukoht` as the chronology row a reader sees.

    Built here rather than inline in :func:`projected_milestones` because the
    correction form swaps this one row back in place after a save, and the two
    renderings have to be the same rendering — a second copy of the `sub`
    composition is a second place for the `Seisukoht` to gain a separator or for
    the linked consultation to lose its label (`app/matters/views.py`,
    `_external_position_row`).

    **The headline names how this reached the file and whose it is, and nothing
    else.** «Meile saadetud tagasiside: Metallitööstuse Liit», «Teiste arvamus:
    Rahandusministeerium» — which is what a reader scanning six months is looking
    for, and the distinction the first lawyer test asked for by name. A row
    recorded before `provenance` existed keeps the heading it has always had,
    because nothing about it changed (docs/adr/0084 §6, docs/adr/0091 §3).

    Where the author is a `source_label` rather than an organisation — an
    aggregate answer with no single author — the label stands in the author's
    place, because that is exactly what it is for. Where a record somehow has
    neither, the separator goes with it rather than leaving a headline ending in a
    colon.

    **What the source said, and what this office thinks of it, are two lines.**
    `Seisukoht` is the `sub`; `Juristi märkus` is :attr:`own_note` and is
    rendered under its own label. They are never concatenated — a `sub` carrying
    both would state this office's criticism as part of the position it is
    criticising, which is the defect the column was added to fix
    (docs/adr/0091 §4).

    **A row with no link is an ordinary row.** Since docs/adr/0084's 2026-09-16
    amendment the written `Seisukoht` is a source in its own right, so a
    position recorded from an e-mail renders as a headline, a date and the text
    — no empty link control, no «allikas puudub», and nothing claiming the
    record is incomplete, because it is not.

    **The link is labelled by its host, never printed as an address.** A raw URL
    as a row's own text is a line a reader has to parse instead of read, and it
    is the one shape in which a look-alike address would be believed — the rule
    a published `Ülevaade / uudis` already follows. `link_label` falls back to
    `Ava seisukoht` where the address has no host to name, and the template
    gives every one of these `target="_blank"`, `rel="noopener noreferrer"` and
    a visually hidden «avaneb uues aknas» (docs/adr/0081 §4).
    """
    sub = position.summary
    if position.engagement is not None:
        # The round this answered, where it answered one. After the explanation
        # rather than before it: what they said is what a reader wants first,
        # and «this came back from our consultation» is the context for it.
        related = f"Vastus kaasamisele: {position.engagement.title}"
        sub = f"{sub} · {related}" if sub else related
    links = (ChronologyLink(label=position.link_label, url=position.url),) if position.url else ()
    # `Meile saadetud tagasiside: Metallitööstuse Liit`, `Teiste arvamus: MKM`,
    # or the unchanged `Väline seisukoht: …` for a row recorded before the
    # question existed. The author is the organisation, or the `Allikas` naming a
    # collection of answers that has none — and where a record has neither the
    # separator goes with it, so a headline never ends in a colon
    # (docs/adr/0091 §3.3, §3.4).
    author = position.author_label
    headline = f"{position.kind_label}: {author}" if author else position.kind_label
    return ChronologyMilestone(
        what=headline,
        # The date as it was actually known, or the words «kuupäev teadmata» —
        # never the day the row happens to sit on, and never the anchor of a
        # period (docs/adr/0079 §3).
        display_date=position.display_date or EXTERNAL_POSITION_DATE_UNKNOWN,
        sub=sub,
        links=links,
        # Its own line under its own label, never a clause in `sub`. The
        # position, what it answered and where to read it are one thing; what
        # this office thinks of it is another, and the row says so
        # (docs/adr/0091 §4).
        own_note=position.lawyer_note,
        own_note_label=LAWYER_NOTE_LABEL,
    )


#: What the chronology prints for a development nobody could date.
#:
#: The same sentence an undated `Väline seisukoht` reads, and deliberately the
#: same one: both are records whose date may honestly be unknown, and two
#: spellings of «we do not know when» would be two things to learn.
DEVELOPMENT_DATE_UNKNOWN = EXTERNAL_POSITION_DATE_UNKNOWN


def development_chronology_day(development: MatterProceduralDevelopment) -> date:
    """Where a `Menetluse areng`'s row sits in the chronology.

    Its own date when it has one; the day it was written down when it has not —
    the rule `external_position_chronology_day` states, for the same reason. A row
    that cannot be placed cannot be read, and the day it was recorded is the only
    day this system knows anything about.

    **The fallback places the row and never describes it.**
    :func:`development_milestone` prints :data:`DEVELOPMENT_DATE_UNKNOWN` for
    exactly these rows, because printing `created_at` beside «Ministeerium saatis
    uue eelnõu versiooni» would state that the ministry did it on the day somebody
    typed it in — a fact about another organisation, invented by this application.

    An approximate date places the row on its anchor, which is the first day of
    the period and is exactly what an anchor is for (docs/adr/0079 §2).
    """
    return development.occurred_on or _local_day(development.created_at)


def development_milestone(development: MatterProceduralDevelopment) -> ChronologyMilestone:
    """One `Menetluse areng` as the chronology row a reader sees.

    Built here rather than inline in :func:`projected_milestones` so that one
    function decides what a development row says — a second copy is a second
    place for the note to gain a separator or lose its label.

    Its second caller is `views._development_row`, which is what swaps this row
    back in place after a correction — so a corrected development cannot come
    back worded differently from the way it will read on the next page load.

    That function did not exist until QA-06, and this paragraph said so: a
    `Menetluse areng` was the one milestone with no correction route, and the
    chronology rendered it through the generic block. It has one now, spelled the
    way a `Kaasamine`'s and a `Väline seisukoht`'s are.

    **The headline is the step itself**, which is what the record's `title` holds
    and what Package D will project: «Märge: Ministeerium saatis uue
    eelnõu versiooni». A reader scanning a year of a proceeding is looking for the
    steps, and folding the lawyer's assessment into that line would make one line
    say two things with two authors.

    **What happened and what this office makes of it are two lines.** The `note`
    is :attr:`own_note` and renders under its own label, exactly as a
    `Väline seisukoht`'s does — the same separation, for the same reason
    (docs/adr/0091 §4, §5).
    """
    return ChronologyMilestone(
        what=f"{DEVELOPMENT_HEADLINE}: {development.title}",
        # The date as it was actually known, or the words «kuupäev teadmata» —
        # never the day the row happens to sit on, and never the anchor of a
        # period (docs/adr/0079 §3).
        display_date=development.display_date or DEVELOPMENT_DATE_UNKNOWN,
        own_note=development.note,
        own_note_label=LAWYER_NOTE_LABEL,
    )


#: What the chronology prints for a `Töövõit` whose business period nobody
#: recorded.
#:
#: Its own constant beside the three above, for the reason each of those gives:
#: they happen to be the same four words and they answer different questions —
#: «when did Koda ask», «when did they say it», «when did the procedure move»
#: and «when was this won».
WORK_VICTORY_DATE_UNKNOWN = "Kuupäev teadmata"

#: What the chronology calls a confirmed advocacy win.
WORK_VICTORY_MILESTONE = "Töövõit"


def work_victory_chronology_day(victory: Any) -> date:
    """Where a `Töövõit`'s row sits in the chronology.

    Its own business period's anchor when it has one; the day it was written
    down when it has not — the rule `development_chronology_day` states, for the
    same reason.

    **Never `confirmed_at`, which is what this function exists to stop.** Until
    docs/adr/0092 the chronology placed *and described* a work victory by the
    moment somebody pressed `Kinnita`: a 2019 win reviewed in 2026 sat at the top
    of the file under `12.03.2026`, above the proceeding it belongs to and dated
    to a day on which nothing happened. `confirmed_at` is evidence that the
    record was confirmed; `period_date` + `date_precision` is when the victory
    belongs in the business history, and the model says so in as many words —
    «never `created_at`, never the Matter's reporting year and never a
    commencement date» (`MatterWorkVictory.period_date`, Stage-2G brief 22).

    An approximate period places the row on its **anchor**, which is the first
    day of the period and is exactly what an anchor is for. Here too the
    placement is not the description: :func:`work_victory_milestone` prints
    *2019* or *II kvartal 2026* through `display_period`, never the anchor
    (docs/adr/0079 §2, §3).
    """
    return victory.period_date or _local_day(victory.created_at)


def work_victory_milestone(victory: Any) -> ChronologyMilestone:
    """One confirmed `Töövõit` as the chronology row a reader sees.

    **The date is the business period, at the precision it was recorded to**, or
    the words «kuupäev teadmata» — and never the confirmation timestamp, which
    is a fact about this office's review rather than about the win
    (docs/adr/0092 §4).
    """
    return ChronologyMilestone(
        what=WORK_VICTORY_MILESTONE,
        display_date=victory.display_period or WORK_VICTORY_DATE_UNKNOWN,
        sub=victory.title,
    )


def submission_chronology_day(submission: Any) -> date:
    """Where a sent `Koja arvamus` sits in the chronology.

    `sent_at` is `NOT NULL` on a SENT `Submission` — the database refuses one
    without it, because a send with no date is an unverifiable claim about when
    Koda argued something (`submissions_sent_requires_timestamp_and_evidence`).
    So there is no fallback here and no «kuupäev teadmata» constant beside this
    function: the one record on this page whose business date cannot be missing
    is this one.

    A withdrawn or superseded opinion keeps that timestamp — nothing clears it,
    and the population `projected_milestones` reads is `historically_sent`,
    which requires it — so the row goes on sitting on the day the letter
    actually went rather than moving when the opinion's status later did.
    """
    return _local_day(submission.sent_at)


def submission_milestone(submission: Any, addressees: Sequence[str] = ()) -> ChronologyMilestone:
    """One sent `Koja arvamus` as the chronology row a reader sees.

    **Read off the record, never off the send event.** `SUBMISSION_SENT` said the
    same thing one step further away: its `occurred_at` is the moment somebody
    pressed the button, which for an opinion reconstructed from the historical
    register is a fact about the import, and its addressees were a copy in a
    payload rather than the `SubmissionRecipient` rows that are canonical
    (docs/adr/0092 §3).

    **Several per Matter, each its own row.** A supplementary opinion months
    after the first is a second act, and a file that collapsed them into one
    «final opinion» would lose the act a reader came for. Nothing here elects a
    primary, and each row keeps its own date, its own recipients and its own
    evidence (docs/adr/0061, master specification 6.4).

    **The title is not the headline.** A `Submission` is titled after its Matter
    or after the file that was sent, so printing it here repeats the `<h1>` a few
    hundred pixels up the page or prints `arvamus_final_v3.docx` into the
    chronology. What tells two opinions on one file apart is the `Kokkuvõte`,
    the `Liik` — `Täiendav arvamus`, `Pöördumine Riigikogule` — and who it went
    to, which is what the sub-line carries. The default `Ametlik arvamus` says
    nothing the row does not, and is left off for the reason
    `engagement_milestone` leaves `Muu` off.

    **`Kokkuvõte` first**, as `external_position_milestone` puts `Seisukoht`
    first and for the same reason: what the letter argued is what a reader came
    for, and who it went to is the context for it. A row recorded before the
    column existed has none, prints exactly what it printed before, and gains
    nothing — no summary is derived from the title, from the file or from
    anywhere else (docs/adr/0095 §2).

    Only `ADDRESSEE` recipients. «Teadmiseks» is a copy, and a row that listed
    both would make «who did Koda actually write to» unanswerable — the
    distinction `RecipientRole` exists for.
    """
    from app.submissions.enums import SubmissionKind

    parts: list[str] = []
    if submission.summary:
        parts.append(submission.summary)
    if submission.kind != SubmissionKind.FORMAL_OPINION:
        parts.append(str(submission.get_kind_display()))
    if addressees:
        parts.append(", ".join(addressees))
    return ChronologyMilestone(
        what=SUBMISSION_MILESTONE,
        display_date=format_estonian_date(submission_chronology_day(submission)),
        sub=" · ".join(parts),
    )


def projected_milestones(
    *,
    matter: Matter,
    user: Any,
    intelligence: Any = None,
    today: date | None = None,
) -> list[TimelineItem]:
    """The structured facts, as chronology rows, read from their own records.

    Older ADRs kept `Töövõit`, `Jõustumine`, `Oluline tähtaeg` and `Kaasamine`
    out of the professional timeline because each had a standing section on the
    Matter page showing it. Those sections are gone, so the reasoning is gone
    with them: a fact nobody can see anywhere is not a quieter chronology, it is
    a lost record (docs/adr/0065, superseded by docs/adr/0074 §15).

    **Projected, never duplicated.** Nothing here writes a `ChangeEvent` and
    nothing here reads one. The canonical record already exists and already
    carries the date, the wording and the visibility; this turns it into a row.
    That is also why the corresponding audit clauses left `_CLAUSES` — the
    alternative was rendering one act as a clause and a row (§36 of the brief).

    **Only what has happened.** A deadline in October is where the file is going,
    which is the `.tl-strip`'s question; the chronology answers what has already
    occurred. Projecting a future date here would put tomorrow above yesterday in
    a list that reads newest-first and means *past*.

    Scoped through `matter_intelligence` and `visible_to`, so a restricted child
    changes no row, no count and no ordering (AUTH-003).
    """
    from app.intelligence.selectors import matter_intelligence

    day = today or timezone.localdate()
    facts = intelligence if intelligence is not None else matter_intelligence(matter, user, day)
    rows: list[TimelineItem] = []

    def add(record: Any, when: datetime, milestone: ChronologyMilestone) -> None:
        rows.append(
            TimelineItem(
                occurred_at=when,
                created_at=record.created_at,
                sort_key=str(record.pk),
                item_type=type(record).__name__,
                milestone=milestone,
                record=record,
            )
        )

    for victory in facts.work_victories:
        if victory.confirmed_at is None:
            # A machine's candidate is a proposal, not a professional fact. It
            # reads where candidates are reviewed, and it earns a chronology row
            # once somebody confirms it (Stage-2G).
            #
            # **Whether it is confirmed and when it happened are two questions**,
            # and this reads only the first. `confirmed_at` decides that the row
            # exists; `period_date` decides where it sits and `display_period`
            # what it says, because a 2019 win reviewed in 2026 belongs in 2019
            # (docs/adr/0092 §4, `work_victory_chronology_day`).
            continue
        # **No future filter, unlike every other record below, and that is the
        # rule stated rather than an omission.** `confirmed_at` is the whole
        # existence test: a person has judged this a Chamber win, and the record
        # exists because they did. The period is a *reporting* period — the
        # department records wins by year — so a row labelled 2030 is a label,
        # not a claim that something has not happened yet, and dropping it would
        # take a confirmed judgement off the file altogether. A fact nobody can
        # see anywhere is not a quieter chronology, it is a lost record
        # (docs/adr/0074 §15, docs/adr/0092 §4).
        add(
            victory,
            _end_of_day(work_victory_chronology_day(victory)),
            work_victory_milestone(victory),
        )

    for record in [*facts.past_dates, *facts.upcoming_dates]:
        # **A cancelled expectation is history, and it reads as history.**
        # Nothing is deleted when a plan changes: an expectation somebody called
        # off is part of the file, and quietly dropping it is how a reader
        # concludes nobody ever recorded anything (Stage-2G brief 5, 33). It is
        # marked rather than hidden, and it does not reach the process strip —
        # the strip says where the file is going, and a called-off milestone is
        # not on that path (docs/adr/0074 §12).
        if record.is_cancelled:
            add(
                record,
                _end_of_day(record.period_end),
                ChronologyMilestone(
                    what=record.title,
                    display_date=record.display_date,
                    sub=str(record.get_status_display()),
                ),
            )
            continue
        if not record.has_passed(day):
            continue
        add(
            record,
            _end_of_day(record.period_end),
            ChronologyMilestone(
                what=record.title,
                display_date=record.display_date,
            ),
        )

    for record in facts.effective_dates:
        if record.date_value is None:
            continue
        if record.is_cancelled:
            add(
                record,
                _end_of_day(record.date_value),
                ChronologyMilestone(
                    what=record.description or "Jõustumine",
                    display_date=format_at_precision(record.date_value, record.date_precision),
                    sub=str(record.get_status_display()),
                ),
            )
            continue
        if record.date_value > day:
            continue
        add(
            record,
            _end_of_day(record.date_value),
            ChronologyMilestone(
                what="Jõustus" if record.date_value < day else "Jõustub",
                display_date=format_at_precision(record.date_value, record.date_precision),
                sub=record.description,
            ),
        )

    for engagement in MatterEngagement.objects.filter(matter=matter).visible_to(user):
        when = engagement_chronology_day(engagement)
        if when > day:
            continue
        add(engagement, _end_of_day(when), engagement_milestone(engagement))

    # `Väline seisukoht`: what another organisation said about this file.
    #
    # Projected from the canonical record like every other structured fact, so
    # the four audit events this record writes contribute no row of their own
    # and one act takes one line (docs/adr/0074 §14, docs/adr/0084 §6).
    #
    # `select_related` on both foreign keys the row renders, because the
    # headline is the organisation's name and the sub-line may name the
    # consultation it answered — without it a Matter carrying ten positions
    # would cost twenty queries to draw them.
    for position in (
        MatterExternalPosition.objects.filter(matter=matter)
        .visible_to(user)
        .select_related("organisation", "engagement")
    ):
        when = external_position_chronology_day(position)
        if when > day:
            # A position dated in the future is not history yet, and the
            # chronology reads newest-first and means *past*. The same rule the
            # engagement above it follows.
            continue
        add(position, _end_of_day(when), external_position_milestone(position))

    # `Menetluse areng`: one step the external procedure took.
    #
    # Projected from the canonical record like every other structured fact, so the
    # three audit events this record writes contribute no row of their own and one
    # act takes one line (docs/adr/0074 §14, docs/adr/0091 §5).
    #
    # No `select_related`: the row renders the record's own two columns and
    # nothing across a foreign key — the stage it may have moved is on the Matter
    # and is deliberately not copied here (`MatterProceduralDevelopment`).
    for development in MatterProceduralDevelopment.objects.filter(matter=matter).visible_to(user):
        when = development_chronology_day(development)
        if when > day:
            # A development dated in the future is not history yet, and the
            # chronology reads newest-first and means *past*. The same rule the
            # two records above it follow.
            #
            # **Nothing new reaches this branch.** `record_procedural_development`
            # refuses a period that begins after today, so a development written
            # from now on is never ahead of the list it belongs to. It stays
            # because rows filed before that rule exist, are real, and are not
            # rewritten — a projection quietly showing them under a day they were
            # not recorded to would be the invention docs/adr/0092 §4 refuses,
            # and they arrive here honestly when their date does.
            continue
        add(development, _end_of_day(when), development_milestone(development))

    # `Koja arvamus`: what this office actually sent, read off the record.
    #
    # Projected like every other structured fact since docs/adr/0092, so the send
    # event contributes no row of its own and one act takes one line. Several per
    # Matter is ordinary and each draws its own row: nothing here elects a final
    # opinion (docs/adr/0061, master specification 6.4).
    #
    # **`historically_sent`, never `sent`.** The population is «was this actually
    # sent», answered by the canonical `sent_at`, and not «is this the opinion
    # that currently stands». Sending was a business act on a day; withdrawing
    # the opinion afterwards adds a second act and takes nothing away from the
    # first, and `SUBMISSION_WITHDRAWN` draws its own `Arvamus tagasi võetud`
    # line from the audit vocabulary directly above this one. Reading
    # `status=SENT` here made a sent opinion disappear from the file's own
    # history the moment it was withdrawn or superseded (docs/adr/0092 §3).
    #
    # `prefetch_related` on the addressee rows, so a Matter carrying four
    # opinions costs two queries to name their recipients rather than eight. The
    # prefetch is filtered to `ADDRESSEE` in SQL rather than in Python, because
    # «teadmiseks» is a copy and not somebody Koda wrote to.
    for submission in (
        Submission.objects.filter(matter=matter)
        .visible_to(user)
        .historically_sent()
        .prefetch_related(
            models.Prefetch(
                "recipient_rows",
                queryset=SubmissionRecipient.objects.filter(
                    role=RecipientRole.ADDRESSEE
                ).select_related("organisation"),
                to_attr="chronology_addressees",
            )
        )
    ):
        if submission.sent_at is None:  # pragma: no cover - refused by a CHECK constraint
            continue
        when = submission_chronology_day(submission)
        if when > day:
            continue
        add(
            submission,
            _end_of_day(when),
            submission_milestone(
                submission,
                [row.organisation.name for row in submission.chronology_addressees],
            ),
        )

    # `Ülevaade / uudis`, and **only the two states that are milestones**.
    #
    # A published record and a cancelled plan are things that happened to the
    # file: the page went up, or the write-up was called off. A
    # *planned* one has not happened — it is work the file still owes — and it
    # reads in its own strip above, where it can be acted on. Projecting it here
    # would put an intention in a list that means «what has already occurred»
    # (docs/adr/0081 §4).
    # Which `Koja arvamused` each write-up covers, read once for the whole page
    # and scoped on the `Submission` side: a restricted opinion contributes no
    # name and no count to an overview row a reader may see (docs/adr/0093 §4).
    # Attached to the record the row already carries, so the chronology template
    # reads it off `item.website_overview` without a second context key.
    linked_opinions = linked_submissions_by_overview(matter, user=user)
    for overview in MatterWebsiteOverview.objects.filter(matter=matter).visible_to(user):
        overview.linked_opinions = linked_opinions.get(overview.pk, [])
        if overview.is_published:
            published_on = overview.published_on
            if published_on is not None and published_on > day:
                # A publication date in the future is the same case as a future
                # engagement: it is not history yet, and the chronology reads
                # newest-first and means *past*.
                continue
            # **Where the row sits, and what it says, are two different
            # answers.** Since docs/adr/0089 §8 a published overview may have no
            # publication date, and it must still appear — dropping it would
            # hide a page that exists, which is a worse answer than placing it
            # approximately. So the row is placed on the day it was *recorded*,
            # which is the only day this system knows anything about, and it
            # prints «Kuupäev teadmata» rather than that day. The rule
            # `external_position_chronology_day` states, for the same reason:
            # the fallback places the row and never describes it
            # (docs/adr/0089 §10).
            sits_on = published_on if published_on is not None else _local_day(overview.created_at)
            if sits_on > day:
                continue
            add(
                overview,
                _end_of_day(sits_on),
                ChronologyMilestone(
                    what=WEBSITE_OVERVIEW_MILESTONE,
                    display_date=(
                        format_estonian_date(published_on)
                        if published_on is not None
                        else WEBSITE_OVERVIEW_DATE_UNKNOWN
                    ),
                    sub=str(overview.get_status_display()),
                    # **The label, never the address.** A raw URL as the row's
                    # own text is a line a reader has to parse instead of read,
                    # and it is the one shape in which a look-alike address would
                    # be believed. `Ava ülevaade või uudis` says what the link is
                    # for without claiming which site it is on, which the address
                    # no longer promises (docs/adr/0085 §2); the template gives
                    # it `target="_blank"`, `rel="noopener noreferrer"` and a
                    # visually-hidden «avaneb uues aknas»
                    # (templates/matters/partials/timeline_items.html).
                    links=(ChronologyLink(label=WEBSITE_OVERVIEW_LINK_LABEL, url=overview.url),),
                ),
            )
            continue
        if overview.is_cancelled and overview.cancelled_at is not None:
            cancelled_on = _local_day(overview.cancelled_at)
            if cancelled_on > day:
                continue
            # A cancelled plan is marked, never hidden. Nothing is deleted when
            # a plan changes, and quietly dropping the row is how a reader
            # concludes nobody ever recorded anything — the same rule a cancelled
            # `Oluline tähtaeg` follows directly above (Stage-2G brief 5, 33).
            add(
                overview,
                _end_of_day(cancelled_on),
                ChronologyMilestone(
                    what=WEBSITE_OVERVIEW_MILESTONE,
                    display_date=format_estonian_date(cancelled_on),
                    sub=str(overview.get_status_display()),
                ),
            )

    return rows


#: Sentinel for "this caller has not answered the question", so that `None` can
#: keep meaning «this Matter has no open step» rather than «nobody said».
_UNREAD = object()


def matter_timeline(
    *,
    matter: Matter,
    user: Any,
    limit: int = 50,
    offset: int = 0,
    only: str = TIMELINE_FILTER_ALL,
    intelligence: Any = None,
    today: date | None = None,
    current_action: Any = _UNREAD,
) -> tuple[list[TimelineItem], bool]:
    """Return one page of the timeline, newest first.

    Entries are filtered through their own visibility so a restricted entry
    inside an otherwise visible Matter stays hidden. The change-event stream is
    scoped to this Matter, which the caller has already proven the user may
    read.

    ``only`` filters what is *shown*, never what is grouped: a save that wrote
    a note and set the next step is one action, and the entry filter shows it
    with its facts rather than tearing it in half.

    ``current_action`` is the step this reader may see as open, passed in by the
    Matter page so that the history and `PRAEGUNE TEGEVUS` cannot ask two
    differently scoped questions about one file — the seam ``intelligence``
    already is. It is read here because **the open step is a current-work
    concept and not a history row**: a `Järgmiseks` printed prominently at the
    top of the page and again as «määras järgmise sammu» halfway down reads as
    two instructions, and a reader scrolling for what is owed finds the older
    copy first. The row comes back the moment the step is finished or
    superseded, because then it is history (docs/adr/0092 §8).

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
            | models.Q(event_type__in=RECORD_OPERATION_EVENT_TYPES)
        )
        .select_related("actor")
        .order_by("-occurred_at", "-created_at", "-id")[: window * 3]
    )

    entry_operations: dict[Any, uuid.UUID] = {
        event.object_id: event.operation_id
        for event in events
        if event.event_type == ChangeEventType.ENTRY_ADDED and event.operation_id is not None
    }
    # The same trick an entry uses, for a record that is projected rather than
    # authored: the row saying which operation wrote it is fetched and never
    # rendered. `RECORD_OPERATION_EVENT_TYPES` explains why the record cannot
    # carry the identifier itself.
    record_operations: dict[Any, uuid.UUID] = {
        event.object_id: event.operation_id
        for event in events
        if event.event_type in RECORD_OPERATION_EVENT_TYPES and event.operation_id is not None
    }
    suppressed = frozenset(SUPPRESSED_WHEN_ENTRY_SHOWN) | frozenset(RECORD_OPERATION_EVENT_TYPES)
    renderable = [event for event in events if event.event_type not in suppressed]

    # The structured facts, as their own rows, **before** the events are
    # assembled. Which effects fold onto which row depends on which records this
    # reader may actually see, so the projection — which is where `visible_to`
    # is applied — has to run first (AUTH-003, docs/adr/0092 §7).
    projected: list[TimelineItem] = []
    if only != TIMELINE_FILTER_ENTRIES:
        projected = projected_milestones(
            matter=matter, user=user, intelligence=intelligence, today=today
        )

    # A file that supports a structured fact reads on that fact's own row and
    # nowhere else. Its evidence event would otherwise become a row of its own —
    # the operation that wrote it has no `Entry` to be grouped onto — which is a
    # second line, and a second dot, for one act (brief §25).
    #
    # The sent opinions' final texts are handed over from the projection above
    # rather than read again: those rows are already in hand, and asking the
    # database a second time for a column this function has just been given is a
    # query for an answer it already has.
    shown_on_their_record = _versions_shown_on_their_record(
        matter,
        final_texts={
            item.record.final_version_id
            for item in projected
            if item.submission is not None and item.record.final_version_id
        },
    )
    if shown_on_their_record:
        renderable = [
            event
            for event in renderable
            if not (
                event.event_type == ChangeEventType.EVIDENCE_VERSION_ADDED
                and event.object_id in shown_on_their_record
            )
        ]

    # One operation, one act, one row. `record_operations` says which operation
    # wrote each canonical record; this says which operations wrote a record
    # **that is on this page**, which is the only thing an effect may fold onto.
    folded_operations: dict[uuid.UUID, int] = {}
    for index, item in enumerate(projected):
        if item.record is None:
            continue
        operation = record_operations.get(item.record.pk)
        if operation is not None:
            folded_operations[operation] = index

    effects: dict[uuid.UUID, list[ChangeEvent]] = {}
    if folded_operations:
        kept: list[ChangeEvent] = []
        for event in renderable:
            if (
                event.event_type in OPERATION_EFFECT_EVENT_TYPES
                and event.operation_id is not None
                and event.operation_id in folded_operations
            ):
                effects.setdefault(event.operation_id, []).append(event)
                continue
            kept.append(event)
        renderable = kept

    # **The open step reads once, at the top.** A `NEXT_ACTION_SET` that would
    # otherwise stand alone as its own row, pointing at the action this reader
    # can see is still open, is dropped: `PRAEGUNE TEGEVUS` is where an open
    # instruction is read and acted on. An action set *inside* a save that has a
    # row of its own — a note, a `Menetluse areng` — keeps its `→ …` strip
    # there, because that is the act's own consequence rather than a second copy
    # of the instruction (docs/adr/0092 §8).
    open_action = (
        current_action
        if current_action is not _UNREAD
        else selectors.current_action_of(matter, user)
    )
    open_action_pk = getattr(open_action, "pk", None)
    if open_action_pk is not None:
        acts_with_a_row = set(entry_operations.values())
        renderable = [
            event
            for event in renderable
            if not (
                event.event_type == ChangeEventType.NEXT_ACTION_SET
                and event.object_id == open_action_pk
                and event.operation_id not in acts_with_a_row
            )
        ]

    groups: dict[uuid.UUID, _Group] = {}
    items: list[TimelineItem] = []

    # Milestone events leave the grouping before it starts. A save that wrote a
    # note and changed the stage did two separable things to the record, and the
    # approved target shows two rows for it — not one line with a clause
    # (docs/adr/0074 §14).
    milestone_events = [event for event in renderable if event.event_type in MILESTONE_EVENT_TYPES]
    renderable = [event for event in renderable if event.event_type not in MILESTONE_EVENT_TYPES]
    for event in milestone_events:
        items.append(
            TimelineItem(
                occurred_at=event.occurred_at,
                created_at=event.created_at,
                sort_key=str(event.id),
                item_type=event.event_type,
                event=event,
                events=(event,),
                milestone=_milestone_for_event(event),
            )
        )

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
                    # A lone work event reads as its verb too. «Marko Udras
                    # määras järgmise sammu» is the target's meta line; «Marko
                    # Udras Järgmiseks määratud» is the audit vocabulary set
                    # beside a name it does not agree with. The verbs are the
                    # same ones a grouped save uses, so one act reads the same
                    # whether or not it happened to be part of a composer save
                    # (TEEMA_TARGET_SPEC §E).
                    summary_verbs=_verbs_for(None, [event]),
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

    # What the folded operations decided, attached to the row of the record they
    # were saved with. `stage_effect` is the stage label off the event's own
    # summary — never a stage read off the Matter, which is where the file
    # stands *now* rather than what this act did to it — and the next step is
    # attached by `_with_next_steps` from the same `events` tuple, so it prints
    # at the precision the action was recorded to (docs/adr/0092 §6).
    for operation, index in folded_operations.items():
        folded = effects.get(operation)
        if not folded:
            continue
        stage = next(
            (event for event in folded if event.event_type == ChangeEventType.MATTER_STAGE_CHANGED),
            None,
        )
        projected[index] = replace(
            projected[index],
            events=tuple(sorted(folded, key=lambda event: (event.occurred_at, event.created_at))),
            stage_effect=(stage.summary or "") if stage is not None else "",
        )
    items.extend(projected)

    # Deterministic ordering: the visible time first, then when it was recorded,
    # then the time-sortable id. Without the last two, two things written in the
    # same minute could swap places between page loads and pagination could
    # repeat or skip a line.
    if only == TIMELINE_FILTER_ENTRIES:
        items = [item for item in items if item.is_entry]

    items.sort(key=lambda item: (item.occurred_at, item.created_at, item.sort_key), reverse=True)

    page = items[offset : offset + limit]
    has_more = len(items) > offset + limit
    return _with_linked_files(_with_files(_with_next_steps(page, user), user), user), has_more


def _with_files(page: list[TimelineItem], user: Any) -> list[TimelineItem]:
    """Attach the real filename and a link to the bytes, for every captured file.

    One query for the whole page, like ``_with_next_steps``. The old chronology
    printed the name out of the change event's summary and offered no way to open
    it, which made the commonest reason to scroll a file — finding the version
    somebody attached in June — a trip to another tab.

    **Scoped through `Document.visible_to`.** `DocumentVersion` has no visibility
    of its own; it inherits the document's, which inherits the Matter's. A
    version whose document is restricted below this Matter contributes no link
    and no line, exactly as its evidence event contributes no clause.
    """
    from django.urls import reverse

    from app.documents.models import Document, DocumentVersion

    def versions_of(item: TimelineItem) -> list[Any]:
        # **The exact bytes a sent opinion went out as**, for a row projected
        # from its `Submission`. `final_version` is a column on the record rather
        # than a `DocumentLink`, so `_with_linked_files` below cannot see it —
        # and an opinion row with no way to open the letter it stands for is the
        # commonest reason somebody leaves this page (docs/adr/0092 §5).
        #
        # It goes through the same `Document.visible_to` filter as every other
        # version here, so a final text restricted below its Matter contributes
        # no link and no filename.
        submission = item.submission
        if submission is not None:
            return [submission.final_version_id] if submission.final_version_id else []
        return [
            event.object_id
            for event in item.events
            if event.event_type == ChangeEventType.EVIDENCE_VERSION_ADDED and event.object_id
        ]

    wanted = {key for item in page for key in versions_of(item)}
    if not wanted:
        return page

    found = {
        version.pk: ChronologyFile(
            label=version.original_filename,
            url=reverse("documents:download", kwargs={"pk": version.pk}),
        )
        for version in DocumentVersion.objects.filter(
            pk__in=wanted, document__in=Document.objects.visible_to(user)
        )
    }
    if not found:
        return page

    resolved = []
    for item in page:
        files = tuple(found[key] for key in versions_of(item) if key in found)
        resolved.append(replace(item, files=files) if files else item)
    return resolved


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


def _versions_shown_on_their_record(matter: Matter, *, final_texts: set[Any]) -> set[Any]:
    """Evidence versions that read on a structured fact's own chronology row.

    **A file is not a chronology event.** Attaching two PDFs to a `Töövõit` is
    one act and takes one row — the win's — with the files under it. Without
    this, each of those uploads would *also* produce an
    `EVIDENCE_VERSION_ADDED` row of its own, because the operation it belongs
    to has no `Entry` for the projection to group it onto: two rows for one act,
    one of them a file with its own dot (brief §25).

    So a version whose document is explicitly linked to a record that is **not**
    an `Entry` is dropped from the event stream and rendered by
    :func:`_with_linked_files` on the row that record already draws. Entry links
    are deliberately left alone: a note's attachment has always read as a clause
    on the note's own row and still does.

    **A sent opinion's final text is the same case reached by a different
    column.** `Submission.final_version` is a foreign key rather than a
    `DocumentLink`, so the link query below cannot see it — and since
    docs/adr/0092 the send has a row of its own that renders those exact bytes,
    which made `+ Koja arvamus` draw «Arvamus välja» and a second «lisas
    dokumendi» line for one act. ``final_texts`` is those versions, handed in by
    the caller from the projection it has already made, so naming them here
    costs nothing (docs/adr/0092 §5).

    Unscoped on purpose — this decides *where* a file reads, never *whether*.
    Visibility is applied twice over, by `scope_change_events` on the event
    stream and by `DocumentLink.visible_to` and `Document.visible_to` on the
    files, and this can only ever remove a row.
    """
    from app.documents.links import DocumentLink
    from app.documents.models import DocumentVersion

    documents = set(
        DocumentLink.objects.filter(document__matter=matter, entry__isnull=True).values_list(
            "document_id", flat=True
        )
    )
    if not documents:
        return final_texts
    return final_texts | set(
        DocumentVersion.objects.filter(document_id__in=documents).values_list("id", flat=True)
    )


def _with_linked_files(page: list[TimelineItem], user: Any) -> list[TimelineItem]:
    """Attach the files explicitly associated with each row's own record.

    One query for the whole page, like ``_with_files`` and ``_with_next_steps``,
    and read through ``DocumentLink.visible_to`` — which is the *conjunction* of
    the document's visibility and the record's, so neither end can be repeated
    to somebody who may not see it (app/documents/links.py).

    **This adds no rows and no dots.** A file lands under the line its record
    already draws, using the existing compact document-link language, and the
    chronology count is unchanged. The Documents tab continues to list the
    document normally as well (brief §25).

    Deduplicated against whatever ``_with_files`` already attached from the
    evidence events: a note's attachment is both an event on the note's
    operation and a link to the note, and printing it twice under one line is
    the defect that would look like a double upload.
    """
    from django.urls import reverse

    from app.documents.links import DocumentLink
    from app.documents.services import LINK_FIELD_BY_MODEL

    keyed: list[tuple[int, str, Any]] = []
    wanted: dict[str, set[Any]] = {}
    for index, item in enumerate(page):
        record = item.entry if item.entry is not None else item.record
        if record is None:
            continue
        field = LINK_FIELD_BY_MODEL.get(type(record)._meta.label)
        if field is None:
            continue
        wanted.setdefault(field, set()).add(record.pk)
        keyed.append((index, field, record.pk))
    if not keyed:
        return page

    condition = models.Q()
    for field, keys in wanted.items():
        condition |= models.Q(**{f"{field}__in": keys})

    found: dict[tuple[str, Any], list[ChronologyFile]] = {}
    for link in (
        DocumentLink.objects.filter(condition)
        .visible_to(user)
        .select_related("document", "document__current_version")
        .order_by("created_at", "pk")
    ):
        version = link.document.current_version
        field = link.target_field
        if version is None or not field:
            continue
        found.setdefault((field, getattr(link, f"{field}_id")), []).append(
            ChronologyFile(
                label=version.original_filename,
                url=reverse("documents:download", kwargs={"pk": version.pk}),
            )
        )
    if not found:
        return page

    linked = {index: found.get((field, key), []) for index, field, key in keyed}
    resolved = []
    for index, item in enumerate(page):
        extra = [
            file for file in linked.get(index, []) if file.url not in {f.url for f in item.files}
        ]
        resolved.append(replace(item, files=(*item.files, *extra)) if extra else item)
    return resolved
