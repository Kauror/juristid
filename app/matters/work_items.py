"""One read model for dated work, shared by Minu töö and Ülevaade.

Why this module exists
----------------------

Two questions were being answered by two different pieces of arithmetic. Minu
töö asked "what do I have to do", Ülevaade asked "where is the department
losing time", and each wrote its own idea of *overdue*, of *this week* and of
who a piece of work belongs to. Two similar definitions in two files is how two
screens start disagreeing about the same Matter — and the person who notices
first is the department head, who is looking at both.

So there is one definition here, and both pages read it.

What a work item is
-------------------

A :class:`WorkItem` is a **rendered answer**, not a stored row. Nothing here
creates a table, and the two sources keep their separate domain objects:

* an open :class:`~app.workflow.models.NextAction` — what Koda does next;
* an active :class:`~app.intelligence.models.MatterImportantDate` — a milestone
  the department watches;
* an outstanding ``Matter.response_deadline`` — the day Koda's own opinion is
  due, which is the commonest deadline on the whole register;
* an open :class:`~app.matters.models.MatterEngagement` feedback wait — a
  consultation round that asked for answers by a named day and has not been
  finished.

The third one is a projection of a column that was already canonical, and it is
here because it was missing: a Matter carrying nothing but an *Arvamuse
tähtaeg* had no NextAction and no milestone, so it contributed nothing to this
model and fell out of every surface built on it — while the question those
surfaces answer is precisely "what deadlines are coming". Nothing is written to
make it appear. No ``Järgmiseks`` is invented from it.

It is also the one source with a **precedence** above it. A response deadline is
the fallback obligation a file carries *until somebody says what happens next*;
an open ``NextAction`` is that statement, and while one exists the response
deadline is not live work. No dates are compared — any open action wins, later
date or none at all — and nothing is written or cleared to express it: the
column stays exactly where it was, in the Matter header, as the fact it is
(`outstanding_response_deadlines`, docs/adr/0050).

The plan and the obligation are two questions
---------------------------------------------

That precedence decides **what a lawyer works on today**, and it is right. What
it must not decide is whether Koda has actually answered: an instruction is a
plan, and a ministry waiting for an opinion is not answered by a note somebody
wrote to themselves. Those two readings were one predicate, so recording a
`Järgmiseks` quietly reported the obligation as met.

`response_obligations` is the second question, asked on its own. It is
discharged only by a ``SENT`` Submission the reader may see or by the register
recording the opinion work as finished, and an open `NextAction` does not
discharge it. The operational population is the same set *minus* the Matters
carrying a visible open step, so every work surface keeps exactly the rows it
had: the separation adds a concept, not a count.

They are unified only in the read layer, and only far enough to be sorted into
one chronological list. Everything that distinguishes them survives the trip:
the mode chip, the meaning of the date, and what may be done to it.

Three rules run through the whole module.

**Only a DO with a DEADLINE can be late.** A WAIT whose review date has passed
is ripe for a look, never missed. Describing an ordinary dependency on a
ministry as a failure is what makes a work queue stop being believed
(master specification 18.8). An ``Oluline tähtaeg`` and an ``Arvamuse tähtaeg``
are independently real deadlines and may therefore be genuinely overdue.

**The date says where, the mode says what.** A ministry's answer expected on
Thursday and an opinion due on Thursday are both Thursday's problem, so they
share one timeline. What they are not is the same obligation, which is why every
row states its meaning in words beside the date.

**A feedback wait is work, and it is not a deadline.** The fourth source is the
one added last and the one most easily misread, so it says what it is in three
sentences. A `Kaasamine` carrying `Tagasisidet ootame kuni` is a round this
office started and has not finished: somebody asked the membership for answers
by the 22nd, and on the 22nd somebody has to read what came back and write it
down. That is a real task with a real day on it, and before this source existed
it was on no list anywhere — the deadline was drawn on the Teema page's process
strip and nowhere else, so a file could sit waiting for three months without
appearing in a single work surface (docs/adr/0086 §3).

What it is *not* is an obligation this office owes anybody outside the building.
It is therefore deliberately **absent from** :func:`real_deadlines`, so it enters
no *Tähtajad* panel, no deadline window population and no register deadline
group: those three name what Koda promised, and «we asked our members by the
22nd» is not one of them. It touches ``Matter.response_deadline`` in no way, it
discharges nothing, it creates no ``NextAction``, and it appears beside an open
one rather than instead of it — two true facts about one file, which is what a
chronological list of work is for (docs/adr/0086 §3, §4).

**Authorization before arithmetic.** Every queryset starts from
``visible_to(user)``. A restricted Matter the reader may not see contributes
nothing to a count, a band or a row — so nothing downstream has to remember to
hide it, and no template re-implements a security check.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, timedelta
from typing import Any

from django.db.models import BooleanField, Exists, ExpressionWrapper, OuterRef, Q, QuerySet
from django.urls import reverse
from django.utils import timezone

from app.core import dates
from app.core.dates import format_estonian_date, short_day_month
from app.intelligence.enums import FactStatus
from app.intelligence.models import MatterImportantDate
from app.legacy_import.current_state import CurrentRegisterState, RegisterCurrency
from app.legacy_import.register_semantics import OPINION_WORK_COMPLETE_STATES
from app.matters.enums import RecordMode
from app.matters.models import Matter, MatterEngagement
from app.matters.register_dates import RESPONSE_DEADLINE_LABEL
from app.submissions.enums import SubmissionStatus
from app.submissions.models import Submission
from app.workflow.dates import format_at_precision
from app.workflow.enums import (
    REVIEW_KINDS,
    ActionKind,
    ActionStatus,
    DatePrecision,
    DateSemantics,
)
from app.workflow.lateness import period_end_for
from app.workflow.models import NextAction

# ---------------------------------------------------------------------------
# Vocabulary
# ---------------------------------------------------------------------------

SOURCE_NEXT_ACTION = "NEXT_ACTION"
SOURCE_IMPORTANT_DEADLINE = "IMPORTANT_DEADLINE"
#: ``Matter.response_deadline``, read as work. Not a stored row of its own, and
#: deliberately not one: the column is canonical and a second copy of it in a
#: deadline table is a second thing to keep in step.
SOURCE_RESPONSE_DEADLINE = "RESPONSE_DEADLINE"
#: An open `Kaasamine` feedback wait, read as work. Like the response deadline
#: it is a projection of columns that were already canonical and deliberately
#: not a stored row of its own: what ends it is `feedback_closed_at` on the
#: consultation, which is where a reader can see it (docs/adr/0086 §3).
SOURCE_FEEDBACK_WAIT = "FEEDBACK_WAIT"

#: The annotation :func:`annotate_response_obligation` writes: whether the
#: official response obligation on this Matter has been **discharged**.
#:
#: Prefixed and spelled out, because it lands on ``Matter`` beside real field
#: names and a collision would be silent — the precaution
#: :data:`app.matters.register_dates.DISPLAY_DATE` already takes.
DISCHARGED = "response_obligation_discharged"

#: What the date on a row means, in the words the department agreed.
#:
#: ``OODATAV AEG`` rather than the stored enum's *Oodatav umbes*: the label a
#: lawyer reads is a product decision and the column value is a storage one, and
#: this is the seam between them. Nothing here renames anything stored.

#: A lawyer's own next step, on the day they chose for it. ``PLAANIS`` and not
#: ``TÄHTAEG``: the two constants below are the deadlines this department
#: actually owes — one to an outside body, one to a watched milestone — and a
#: date somebody set for their own next action is a plan, not a promise made to
#: anybody else. The stored ``DateSemantics.DEADLINE`` is untouched and still
#: decides what can go overdue; this is the seam, and only the seam
#: (docs/adr/0054 §Amendment).
MEANING_DEADLINE = "PLAANIS"
MEANING_EXPECTED = "OODATAV AEG"
MEANING_REVIEW = "VAATAN ÜLE"
MEANING_IMPORTANT = "OLULINE TÄHTAEG"
#: The business term, unchanged. The register's column, the Matter header, the
#: old dashboard's *Eesolevad tähtajad* table and this row all say the same two
#: words, because a synonym invented here would be a fourth name for one date.
MEANING_RESPONSE = "ARVAMUSE TÄHTAEG"
#: What an open consultation round says about its date.
#:
#: «Ootame tagasisidet» and not «TÄHTAEG», in the words the panel asked the
#: question in. The three constants above are obligations — one owed to an
#: outside body, one to a watched milestone, one a lawyer set for themselves —
#: and this is the department waiting for somebody else. The row can still be
#: late, because the *reading* is what is late: after the day it asked for, the
#: round is a thing whose answers are sitting unread (docs/adr/0086 §4).
MEANING_FEEDBACK_WAIT = "OOTAME TAGASISIDET"

_SEMANTICS_MEANING: dict[str, str] = {
    DateSemantics.DEADLINE.value: MEANING_DEADLINE,
    DateSemantics.EXPECTED_AROUND.value: MEANING_EXPECTED,
    DateSemantics.REVIEW_ON.value: MEANING_REVIEW,
}

#: The bands of the timeline, in reading order.
#:
#: Four, not five. *Ülevaatamiseks küps* and *Täna* are gone as blocks: a review
#: that has come round is ordinary dated work and belongs in the week it is
#: being looked at, and today is the first day of this week rather than a
#: heading of its own. What has emphatically **not** gone is the semantics —
#: a WAIT or a MONITOR is still never late and still never red; that is now a
#: mark on the row rather than a block around it (design handoff 03 §1).
BAND_OVERDUE = "ule_tahtaja"
BAND_WEEK = "sel_nadalal"
BAND_NEXT_30 = "jargmised_30_paeva"
BAND_LATER = "hiljem"

BAND_LABELS: dict[str, str] = {
    BAND_OVERDUE: "Üle tähtaja",
    BAND_WEEK: "Sel nädalal",
    BAND_NEXT_30: "Järgmised 30 päeva",
    BAND_LATER: "Hiljem",
}

BAND_ORDER: tuple[str, ...] = (BAND_OVERDUE, BAND_WEEK, BAND_NEXT_30, BAND_LATER)

#: How many rows of each band are on screen before the rest go behind
#: «Näita veel N ▾». The rest are the *same* list, sliced — not a second query —
#: so opening the disclosure cannot show a row the count above it did not
#: include. ``None`` would mean the band shows everything it holds.
#:
#: *Üle tähtaja* is capped like the others, and deliberately. It once was not,
#: on the reasoning that late work is exactly what nobody may have to click to
#: see; a band of two dozen rows then pushed the rest of the timeline off the
#: screen, which is the same failure in the other direction. The approved rule
#: is the one this dictionary now states: overdue work is ordered oldest-first,
#: the ten oldest rows are immediately visible, and the remainder stays
#: available inline behind «Näita veel N ▾».
#:
#: That inline remainder is bounded by ``BAND_LIMIT`` below, so on a big enough
#: band it is not the whole of it. What leaves the page leaves by the link the
#: band renders, and the heading counts the population either way — see
#: ``WorkBand.total``.
BAND_VISIBLE: dict[str, int | None] = {
    BAND_OVERDUE: 10,
    BAND_WEEK: 10,
    BAND_NEXT_30: 5,
    BAND_LATER: 2,
}

#: How far past this week *Järgmised 30 päeva* reaches.
NEXT_30_DAYS = 30

#: How many rows a page may render before it stops being read.
#:
#: This is a *render* bound and nothing else. It used to be applied to the list
#: the band then counted, so it silently became the count as well — the heading
#: read 60 where the population was 64, and the four rows past it were behind no
#: control at all (UX-002). ``band_items`` now records ``WorkBand.total`` before
#: slicing, and a band that overflows says so and links to the register list
#: holding all of it.
BAND_LIMIT = 60


# ---------------------------------------------------------------------------
# The item
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class WorkItem:
    """One dated obligation, ready to render.

    ``when`` is the anchor the list sorts on; ``display_date`` is how it reads
    at the precision it was actually recorded to. Those differ on purpose: a
    MONTH-precision expectation anchored on 1 September sorts with the first of
    the month and never prints as ``01.09.2026``, because that would manufacture
    a day nobody named (master specification 3.5).

    The compact date cell writes a month as ``09.26`` rather than as *september
    2026* — narrower than the column, and still two numbers rather than three,
    so it cannot be read as a day. ``display_date`` keeps the long form for
    every other surface; only this read model's compact accessors shorten it.
    """

    source_type: str
    object_id: Any
    matter: Matter
    responsible: Any | None
    #: ``DO`` / ``WAIT`` / ``MONITOR``, or "" for an important deadline and for
    #: a response deadline, neither of which is a NextAction and neither of
    #: which may ever be dressed as one.
    #:
    #: Read by the code and never by a template. The label that went with it —
    #: *Teen*, *Ootan*, *Jälgin* — was carried here for the chip and went with
    #: it: a display read model holding a value nothing displays is how a
    #: retired component comes back (ADR 0054).
    action_kind: str
    date_semantics: str
    when: date | None
    period_end: date | None
    #: The stored ``DatePrecision``. Carried so the compact cell can ask what
    #: the source actually knew instead of inspecting the rendered string —
    #: a month has to be recognised to be shortened, and «september 2026» is
    #: not something to pattern-match a UI decision out of (01 §3.2).
    date_precision: str
    display_date: str
    meaning: str
    text: str
    is_overdue: bool
    is_review_ripe: bool
    #: The day this item was read against. Carried on the item rather than
    #: passed to each accessor, because a Django template cannot hand an
    #: argument to a property — and a row that had to be told what day it is
    #: would end up being told twice, differently.
    today: date

    @property
    def is_action(self) -> bool:
        return self.source_type == SOURCE_NEXT_ACTION

    @property
    def is_feedback_wait(self) -> bool:
        """Whether this row is an unfinished consultation round.

        Read by the row's overflow menu, which offers `Lõpeta kaasamine…` for
        exactly these and nothing for the rest: an `Oluline tähtaeg` has no
        completion workflow, and offering one would be inventing it here
        (docs/adr/0086 §4).
        """
        return self.source_type == SOURCE_FEEDBACK_WAIT

    @property
    def record_url(self) -> str:
        """Where the record behind this row is read, which is not always the top
        of the Matter page.

        A file may be running three consultations at once, so a link to the
        Matter leaves the reader to find which of them this row was about. The
        engagement's own chronology element carries an id and that is what the
        row points at; every other source has no such element and points at the
        Matter, which is exactly what it did before.
        """
        if self.source_type == SOURCE_FEEDBACK_WAIT:
            return f"{self.matter_url}#kaasamine-{self.object_id}-sisu"
        return self.matter_url

    @property
    def matter_url(self) -> str:
        return reverse("matters:matter_detail", kwargs={"pk": self.matter_id})

    @property
    def matter_id(self) -> Any:
        return self.matter.pk

    @property
    def reference(self) -> str:
        """The technical reference. A **sort key**, not something a row prints.

        Its one caller is the tie-break in `sort_items` below, where it
        makes two items sharing a date order stably. No template reads it: the
        work rows name their topic by title, because `2026_10` told a reader
        which record was written and nothing about which subject
        (human QA §4, §23).
        """
        return self.matter.display_reference

    @property
    def stage_label(self) -> str:
        stage = self.matter.stage
        return stage.label_et if stage is not None else ""

    @property
    def is_restricted(self) -> bool:
        return self.matter.is_restricted

    @property
    def responsible_name(self) -> str:
        return self.responsible.get_short_name() if self.responsible is not None else "vastutajata"

    @property
    def days_late(self) -> int:
        """How many days past its last day this is. Never negative."""
        end = self.period_end or self.when
        if end is None or end >= self.today:
            return 0
        return (self.today - end).days

    @property
    def compact_month(self) -> str:
        """A MONTH-precision period as ``09.26``, or "" for every other one.

        Month and two-digit year, in that order — the shape the compact column
        was already using for a day (``15.09``), reused for the one approximate
        precision that fits it. Two numbers, never three: ``09.26`` says *month
        09 of 2026* and cannot be misread as the first of September the way
        ``01.09.2026`` would be (master specification 3.5).

        Only MONTH. A quarter and a half-year have a Roman numeral and a word
        of their own — *II kvartal 2027* — and there is no two-number spelling
        of those that a reader would arrive at unaided, so this returns nothing
        for them and they keep the long form the rest of the product uses.
        """
        if self.when is None or self.date_precision != DatePrecision.MONTH:
            return ""
        return f"{self.when.month:02d}.{self.when.year % 100:02d}"

    @property
    def short_date(self) -> str:
        """The value the date cell prints — the honest one, not always a day.

        ``10 p üle`` for something genuinely late, a bare ``9 p`` for a review
        that has merely come round, ``täna`` for today, ``26.08`` for an exact
        date this year, ``09.26`` for a month, and the stored period verbatim
        for anything recorded to a quarter or wider.

        The word *üle* appears only where something was actually missed. A
        ministry that has not replied is not over anything, and one word is the
        whole difference between "you failed" and "have a look at this"
        (master specification 18.8).

        The month is answered before ``täna`` on purpose. A month anchors on its
        first day, so on 1 September a September expectation would otherwise
        print *täna* — which names a day as firmly as ``01.09.2026`` does, from
        the other direction.
        """
        if self.when is None:
            return "—"
        late = self.days_late
        if late:
            return f"{late} p üle" if self.is_overdue else f"{late} p"
        month = self.compact_month
        if month:
            return month
        if self.when == self.today:
            return "täna"
        if self.display_date and self.is_approximate:
            return self.display_date
        return f"{self.when.day:02d}.{self.when.month:02d}"

    @property
    def is_approximate(self) -> bool:
        """Whether the source named a period rather than a day.

        Read from the stored precision — a period whose last day is not its
        anchor — rather than from what the printed string happens to look like.
        Banding depends on this now: *Järgmised 30 päeva* takes day-precise
        dates only, and a heuristic over punctuation is not something to put a
        band boundary on (01 §3.2).
        """
        end = self.period_end or self.when
        return end is not None and end != self.when

    # -- what a deadline row prints -------------------------------------
    #
    # Four small properties rather than four `{% if %}` chains, because a
    # Django template cannot pass an argument to a function and the same row is
    # rendered by two pages. They are display only: nothing here decides which
    # window an item is in (design handoff 1a).

    @property
    def is_today(self) -> bool:
        return self.when == self.today

    @property
    def weekday_letter(self) -> str:
        """``R``. Empty for a date recorded to a month or a quarter, where
        naming a weekday would name a day nobody chose."""
        return "" if self.is_approximate else dates.weekday_letter(self.when)

    @property
    def day_month(self) -> str:
        """``28.08``, or the stored period verbatim when that is all there is."""
        if self.when is None:
            return "—"
        return self.display_date if self.is_approximate else dates.short_day_month(self.when)

    @property
    def responsible_initials(self) -> str:
        """Two letters, or the mark that says nobody carries this."""
        return self.responsible.initials if self.responsible is not None else "!"

    @property
    def responsible_title(self) -> str:
        """The full name, for the badge's `title`. The badge shows initials, so
        the name has to be reachable some other way or the row names nobody."""
        return self.responsible.display_name if self.responsible is not None else "Vastutajata"

    @property
    def meaning_line(self) -> str:
        """The meaning, carrying the original date when the value replaced it.

        ``PLAANIS 14.08`` rather than a bare ``PLAANIS``, because the cell above
        it is showing *10 p üle* and the reader still needs the day it was.

        The same compact month as the line above it: the two halves of one cell
        do not get to spell a period two different ways.
        """
        if self.when is not None and self.days_late:
            return f"{self.meaning} {self.compact_month or self.display_date}"
        return self.meaning


@dataclass(frozen=True)
class WorkBand:
    """One band of the timeline. Rendered only when it holds something.

    ``visible`` is how many rows are on screen; ``rest`` is the remainder of the
    *same* list. One query produces both, so the number in the heading, the rows
    under it and the number on «Näita veel N ▾» are three readings of one answer
    and cannot disagree.

    **``total`` is the population; ``items`` is what the page renders.** They are
    two different numbers whenever the band is bigger than ``BAND_LIMIT``, and
    conflating them is what made *Minu asjad* contradict itself: ``count`` used
    to read ``len(items)``, and ``items`` had already been sliced to the cap, so
    a strip figure of 64 sat 40 px above a heading of 60 and four late rows were
    reachable by no control on the page (UX-002). The heading counts the
    obligations that exist; the rows are the ones worth rendering; ``more_url``
    is where the difference can be read in full.
    """

    key: str
    label: str
    #: Capped at ``BAND_LIMIT`` — what this page renders and expands inline.
    items: list[WorkItem]
    #: Everything the band holds, *before* the render cap. The heading's number.
    total: int
    #: ``None`` shows everything.
    visible: int | None = None
    #: The register list holding this band's whole population, for the rows past
    #: the cap. Empty where no single named population names exactly these rows —
    #: an approximate link would be a second definition of the count above it.
    more_url: str = ""

    @property
    def count(self) -> int:
        return self.total

    @property
    def beyond_cap(self) -> int:
        """How many of ``total`` this page will not render even when expanded."""
        return max(0, self.total - len(self.items))

    @property
    def preview(self) -> list[WorkItem]:
        return self.items if self.visible is None else self.items[: self.visible]

    @property
    def rest(self) -> list[WorkItem]:
        return [] if self.visible is None else self.items[self.visible :]

    @property
    def remaining(self) -> int:
        return len(self.rest)


@dataclass(frozen=True)
class ResponseObligation:
    """One Matter's ``Arvamuse tähtaeg``, read as the official obligation it is.

    Read-only and derived, like :class:`WorkItem` and
    :class:`~app.matters.register_dates.RegisterDate`. Nothing here is stored,
    and ``Matter.response_deadline`` is never written to express any of it.

    **This is not the operational plan.** An open ``Järgmiseks`` decides what a
    lawyer does next, and it still outranks this date on every work surface
    (docs/adr/0050). What it does not do is answer the *other* question — has
    Koda actually responded — and this object answers only that one. The two
    were one predicate until now, which meant recording an instruction quietly
    reported the obligation as met.
    """

    #: The stored ``Arvamuse tähtaeg``, or ``None`` when the Matter carries none.
    value: date | None
    #: The date as a reader reads it. Empty when there is no date.
    display: str
    #: What the date is called, in the register's own words. Never blank: a bare
    #: date does not say which obligation it belongs to.
    label: str
    #: Whether the obligation is still undischarged — no visible SENT
    #: ``Submission`` and no ``CURRENT`` register row recording the opinion work
    #: as finished. An open ``NextAction`` deliberately does **not** enter here.
    is_outstanding: bool
    #: Whether the day has gone by. A fact about the calendar only: a discharged
    #: obligation whose deadline has passed is past and is not late.
    is_past: bool
    #: Whether the product may call this late — outstanding *and* past.
    is_overdue: bool
    #: Days since the deadline, and ``0`` unless :attr:`is_overdue`. A number
    #: that counted from a discharged date would be a lateness nobody owes.
    days_late: int

    @property
    def short_display(self) -> str:
        """``20.09`` — the date as a surface that already has a primary date says it.

        The compact, zero-padded form the dense work surfaces already use
        (:func:`app.core.dates.short_day_month`), because that is what this
        reading is *for*: a second date stated beside a primary one, in a table
        cell or under a task, where the full ``20.9.2026`` would compete with
        the date above it.

        The year is carried by the sentence around it rather than lost: an
        overdue obligation prints «N p üle» beside this, and a deadline from a
        previous year reads «382 p üle» rather than «2 p üle». :attr:`display`
        stays the full form, for the header that states this date on its own.
        """
        return short_day_month(self.value)


# ---------------------------------------------------------------------------
# Building items
# ---------------------------------------------------------------------------


def full_matters(user: Any) -> QuerySet[Matter]:
    """FULL Matters the reader may see, open or closed.

    ARCHIVE rows never reach a work surface: a decade of imported register rows
    is historical evidence, not a queue anybody can act on. That exclusion is
    here rather than in :func:`open_matters` because it holds for every reading
    of a Matter as a live record, including the ones that describe a closed file
    honestly rather than queueing it.
    """
    return Matter.objects.visible_to(user).filter(record_mode=RecordMode.FULL)


def open_matters(user: Any) -> QuerySet[Matter]:
    """Open FULL Matters the reader may see — the base of every work surface."""
    return full_matters(user).filter(is_open=True)


def action_item(action: NextAction, today: date) -> WorkItem:
    anchor = action.target_date
    # A month or a quarter is behind us only once its *last* day is, so the
    # stored precision decides where the item stops being current. The same
    # helper `NextAction.is_overdue` reads, so the row's styling and the number
    # beside it cannot be answering two different questions.
    end = None if anchor is None else period_end_for(anchor, action.date_precision)
    overdue = action.is_overdue(today)
    ripe = (
        action.kind in REVIEW_KINDS
        and action.target_date is not None
        and end is not None
        and end < today
    )
    return WorkItem(
        source_type=SOURCE_NEXT_ACTION,
        object_id=action.pk,
        matter=action.matter,
        responsible=action.responsible,
        action_kind=action.kind,
        date_semantics=action.date_semantics,
        when=action.target_date,
        period_end=end,
        date_precision=action.date_precision,
        display_date=action.display_date,
        meaning=_SEMANTICS_MEANING.get(action.date_semantics, MEANING_DEADLINE),
        text=action.text,
        is_overdue=overdue,
        is_review_ripe=ripe,
        today=today,
    )


def _deadline_item(record: MatterImportantDate, today: date) -> WorkItem:
    """An ``Oluline tähtaeg``, whose responsible person is the Matter's owner.

    ``ImportantDeadline`` carries no responsible column of its own, and this
    round does not add one. Reading the Matter's current owner is what makes the
    read model follow a reassignment without anybody editing the deadline: move
    the Matter and the milestone moves with it, which is the behaviour a
    department actually has (§4.2).
    """
    return WorkItem(
        source_type=SOURCE_IMPORTANT_DEADLINE,
        object_id=record.pk,
        matter=record.matter,
        responsible=record.matter.owner,
        action_kind="",
        date_semantics=DateSemantics.DEADLINE.value,
        when=record.date_value,
        period_end=record.period_end,
        date_precision=record.date_precision,
        display_date=record.display_date
        or format_at_precision(record.date_value, record.date_precision),
        meaning=MEANING_IMPORTANT,
        text=record.title,
        # A milestone is a real commitment, so its last day passing is genuine
        # lateness — unlike a review date, which is only a reminder.
        is_overdue=record.period_end < today,
        is_review_ripe=False,
        today=today,
    )


def _response_deadline_item(matter: Matter, today: date) -> WorkItem:
    """A Matter's own ``Arvamuse tähtaeg``, read as one row of work.

    ``object_id`` is the Matter's own primary key, because the Matter *is* the
    record this obligation lives on — there is no deadline row to point at, and
    inventing one would be the duplicate this fix exists to avoid.

    Exact by construction: the column is a ``DateField`` with no companion
    precision, so ``period_end`` is the same day and the row never claims a
    month somebody did not name. And ``action_kind`` stays empty: this is not a
    ``NextAction`` and must never be dressed as one, so the row shows no mode
    chip and the ⋯ menu offers it no completion workflow it does not have.

    The responsible person is the Matter's current owner. Koda's opinion is the
    file's own obligation rather than a task somebody was handed, so a
    reassignment moves the deadline without anybody editing anything — the same
    reading an ``Oluline tähtaeg`` already gets (§4.2).
    """
    deadline = matter.response_deadline
    return WorkItem(
        source_type=SOURCE_RESPONSE_DEADLINE,
        object_id=matter.pk,
        matter=matter,
        responsible=matter.owner,
        action_kind="",
        date_semantics=DateSemantics.DEADLINE.value,
        when=deadline,
        period_end=deadline,
        # Exact by construction — see the docstring. Named rather than left to
        # a default, so the one source with no precision column still says what
        # it knows instead of inheriting an answer.
        date_precision=DatePrecision.EXACT,
        display_date=format_estonian_date(deadline),
        meaning=MEANING_RESPONSE,
        # No text. The row already names the Matter and states its meaning, and
        # a manufactured sentence beside those two would be a third way of
        # saying what the reader has just read (§7).
        text="",
        # A real commitment, so the day passing is genuine lateness. Only
        # reached at all by a Matter the fulfilment filter left outstanding.
        is_overdue=deadline is not None and deadline < today,
        is_review_ripe=False,
        today=today,
    )


def _feedback_wait_item(engagement: MatterEngagement, today: date) -> WorkItem:
    """One open `Kaasamine` feedback wait, as a row of work.

    ``object_id`` is the engagement's own primary key, because the consultation
    *is* the record this wait lives on — `Lõpeta kaasamine` is offered against
    exactly this row and there is no task object to point at instead.

    Exact by construction. `Tagasisidet ootame kuni` stayed on docs/adr/0079
    §11's exact-day list when `Kaasamise kuupäev` left it (docs/adr/0082 §2), so
    ``period_end`` is the same day and the row never claims a month nobody
    named. ``action_kind`` stays empty: this is not a ``NextAction`` and must
    never be dressed as one.

    **The responsible person is the Matter's current owner**, the reading an
    `Oluline tähtaeg` and an `Arvamuse tähtaeg` already get. A consultation
    round belongs to whoever carries the file rather than to whoever happened to
    type it in, so a reassignment moves the wait with the Matter and nobody
    edits anything; and `created_by` is a record of who wrote the row down,
    which is a different question. On a Matter with no owner this is ``None``,
    which puts the row on the department's *vastutajata* surfaces and on nobody's
    personal desk — the honest place for work nobody has been given, and the
    reason no duplicate is created for every lawyer (§4.2, docs/adr/0086 §4).

    ``is_overdue`` is the day having passed. That is a reading of *this office's*
    unread post and not an accusation against the people who were asked: the
    round asked for answers by a day, the day has gone, and what is late is
    looking at them. Nothing about the membership's own timeliness is stated
    anywhere, and a wait still inside its window is simply upcoming
    (docs/adr/0086 §4).
    """
    deadline = engagement.feedback_deadline
    return WorkItem(
        source_type=SOURCE_FEEDBACK_WAIT,
        object_id=engagement.pk,
        matter=engagement.matter,
        responsible=engagement.matter.owner,
        action_kind="",
        date_semantics=DateSemantics.DEADLINE.value,
        when=deadline,
        period_end=deadline,
        date_precision=DatePrecision.EXACT,
        display_date=format_estonian_date(deadline),
        meaning=MEANING_FEEDBACK_WAIT,
        # Who was asked. The row names the Matter and states its meaning, and
        # this is the one thing neither of those says — «liikmed» is what
        # distinguishes two rounds running on one file (docs/adr/0086 §4).
        text=engagement.title,
        is_overdue=deadline is not None and deadline < today,
        is_review_ripe=False,
        today=today,
    )


def open_feedback_waits(user: Any, *, owner: Any = None) -> QuerySet[MatterEngagement]:
    """Unfinished consultation rounds on open Matters, scoped to the reader.

    ``visible_to`` is the engagement's **own** scope, not the Matter's. A
    `Kaasamine` may carry a stricter visibility override than the file it hangs
    off, so a restricted round must contribute nothing at all for a reader who
    may not open it — not a row, not a count, not a band boundary (AUTH-003,
    docs/adr/0038).

    ``owner`` filters by ``Matter.owner``, for the reason
    :func:`important_deadlines` does: the wait belongs to whoever carries the
    file. An ownerless Matter's wait therefore reaches nobody's Minu asjad and
    appears as *vastutajata* on the department surfaces.

    The clauses beyond that are the ones every source here keeps —
    :func:`open_matters` narrows to open ``FULL`` records, so the decade of
    imported ``ARCHIVE`` consultations reaches no work surface however many of
    them carry a reply-by date.
    """
    queryset = (
        MatterEngagement.objects.visible_to(user)
        .filter(
            feedback_deadline__isnull=False,
            feedback_closed_at__isnull=True,
            matter__is_open=True,
            matter__record_mode=RecordMode.FULL,
        )
        .select_related("matter", "matter__stage", "matter__owner")
    )
    if owner is not None:
        queryset = queryset.filter(matter__owner=owner)
    return queryset


def dated_actions(user: Any, *, responsible: Any = None) -> QuerySet[NextAction]:
    """Open actions with a date, scoped to the reader and optionally to a person."""
    queryset = (
        NextAction.objects.visible_to(user)
        .filter(
            status=ActionStatus.OPEN,
            target_date__isnull=False,
            matter__is_open=True,
            matter__record_mode=RecordMode.FULL,
        )
        .select_related("matter", "matter__stage", "matter__owner", "responsible")
    )
    if responsible is not None:
        queryset = queryset.filter(responsible=responsible)
    return queryset


def undated_actions(user: Any, *, responsible: Any = None) -> QuerySet[NextAction]:
    queryset = (
        NextAction.objects.visible_to(user)
        .filter(
            status=ActionStatus.OPEN,
            target_date__isnull=True,
            matter__is_open=True,
            matter__record_mode=RecordMode.FULL,
        )
        .select_related("matter", "matter__stage", "matter__owner", "responsible")
    )
    if responsible is not None:
        queryset = queryset.filter(responsible=responsible)
    return queryset


def important_deadlines(user: Any, *, owner: Any = None) -> QuerySet[MatterImportantDate]:
    """Active milestones on open Matters, scoped to the reader.

    ``owner`` filters by ``Matter.owner`` because that is who the milestone
    belongs to for work purposes. An ownerless Matter's deadline therefore
    reaches nobody's Minu töö — it appears as *vastutajata* on Ülevaade, which
    is the honest place for work nobody has been given (§4.2).
    """
    queryset = (
        MatterImportantDate.objects.visible_to(user)
        .filter(
            status=FactStatus.ACTIVE,
            matter__is_open=True,
            matter__record_mode=RecordMode.FULL,
        )
        .select_related("matter", "matter__stage", "matter__owner")
    )
    if owner is not None:
        queryset = queryset.filter(matter__owner=owner)
    return queryset


def _discharge_exists(user: Any) -> Q:
    """Whether anything has discharged this Matter's official response obligation.

    Exactly two facts do.

    * A ``SENT`` :class:`~app.submissions.models.Submission` — the product's one
      definition of *Koda's opinion went out*, and the only record allowed to
      make a claim about what was sent (ADR 0011).
    * A ``CURRENT``
      :class:`~app.legacy_import.current_state.CurrentRegisterState` whose
      ``VÄLJA`` reads either a date or *ei saatnud*
      (:data:`OPINION_WORK_COMPLETE_STATES`) — the department writing down, in
      its own register, that the opinion step on the file is over (ADR 0059).

    Everything else leaves the obligation standing, and the list of what does not
    discharge it is the point of this function:

    * ``RECORDED_OTHER`` — a cell nobody has read is not an approved completion
      state, and discharging on the strength of prose is the trade ADR 0059 §2
      refused;
    * a blank ``VÄLJA`` — the live drafting queue;
    * a Matter with no register row at all — it has no ``VÄLJA`` to speak for it;
    * a ``RETIRED`` or ``SUPERSEDED`` row — its ``VÄLJA`` describes a finished
      file, not this one;
    * **an open ``NextAction``** — and this is the separation the concept exists
      for. «JÄLGIN, vaatan uuesti üle 09.10» is a lawyer saying what happens
      next. It is the current operational plan and it rightly outranks this date
      on every work surface (docs/adr/0050) — but it is a plan, not a response,
      and it discharges nothing the Chamber owes anybody outside the building.

    No dates are compared here, or anywhere near here.

    **The Submission side is scoped to the reader and the register side is not**,
    and the difference is which table can be restricted. ``Submission`` is a
    :class:`~app.core.models.VisibilityInheritingModel`, so a colleague's
    restricted opinion could otherwise decide what a NORMAL Matter looks like to
    a reader who may not see it — removing a row is observable, which is the
    inference ``visible_to`` exists to prevent (AUTH-003, docs/adr/0038).
    ``CurrentRegisterState`` has no visibility override of its own: it is the
    derived one-row-per-Matter reading of the register, so scoping it would add
    a join and change no answer.
    """
    sent = Submission.objects.visible_to(user).filter(
        matter=OuterRef("pk"), status=SubmissionStatus.SENT
    )
    completed = CurrentRegisterState.objects.filter(
        matter=OuterRef("pk"),
        currency=RegisterCurrency.CURRENT,
        opinion_sent_state__in=OPINION_WORK_COMPLETE_STATES,
    )
    return Q(Exists(sent)) | Q(Exists(completed))


def annotate_response_obligation(queryset: QuerySet[Matter], user: Any) -> QuerySet[Matter]:
    """Attach :data:`DISCHARGED` to each row, as one correlated pair of ``Exists``.

    The database's reading of the rule :func:`response_obligation_of` reads in
    Python — the shape :mod:`app.matters.register_dates` and
    :mod:`app.matters.activity` already use, and for the same reason: two
    readings of one rule written in two places is how a count and the list
    behind it start disagreeing.

    Both subqueries are correlated ``Exists``, so a page pays for one query
    however many Matters it annotates, and never one Submission lookup per row.
    """
    return queryset.annotate(
        **{DISCHARGED: ExpressionWrapper(_discharge_exists(user), output_field=BooleanField())}
    )


def response_obligations(
    user: Any, *, owner: Any = None, open_only: bool = True
) -> QuerySet[Matter]:
    """Matters whose ``Arvamuse tähtaeg`` is still officially unanswered.

    This is the **obligation**, not the plan, and they are two different
    questions about one date:

    ``response_obligations``
        has Koda responded? Discharged only by a visible ``SENT`` Submission or
        by the register recording the opinion work as finished
        (:func:`_discharge_exists`).
    :func:`outstanding_response_deadlines`
        is this date what a lawyer should be working on today? The same
        population, minus the Matters where an open ``Järgmiseks`` has already
        said what happens next (docs/adr/0050).

    They were one predicate until this concept existed, which meant recording an
    instruction reported the obligation as met. It does not: a ministry still
    waiting for Koda's opinion is not answered by Koda writing itself a note,
    however sound the note is. The operational precedence is untouched — a file
    under an instruction still shows the instruction — and this concept puts
    nothing new on any work surface.

    Authorization first, like every other source here: the population starts
    from :func:`full_matters`, so a restricted Matter contributes nothing for a
    reader who may not see it. ``owner`` narrows by ``Matter.owner``, for the
    reason :func:`important_deadlines` does — this obligation belongs to whoever
    carries the file.

    ``open_only=False`` drops **only** the ``is_open`` clause. ARCHIVE rows stay
    out and the reader scope stays on; what changes is that a closed Matter with
    an unanswered deadline can be described honestly, which is a different act
    from putting it in a live queue. Nothing that reads this population for work
    passes ``False``.

    ``Matter.response_deadline`` is never written, cleared or moved by any of
    this. It remains canonical Matter data stating itself in the header, exactly
    as ADR 0050 requires.
    """
    base = full_matters(user)
    if open_only:
        base = base.filter(is_open=True)
    queryset = (
        annotate_response_obligation(base.filter(response_deadline__isnull=False), user)
        .filter(**{DISCHARGED: False})
        .select_related("stage", "owner")
    )
    if owner is not None:
        queryset = queryset.filter(owner=owner)
    return queryset


def response_obligation_of(
    matter: Matter, user: Any, today: date | None = None
) -> ResponseObligation:
    """One Matter's response obligation, described.

    The row-level reading of :func:`annotate_response_obligation`, and it reads
    that annotation when the queryset already carries it — so a page that
    annotates pays one query for the whole list rather than one per row. Without
    it the same question is asked about the one Matter through the same helper,
    so the two readings cannot drift apart.

    A Matter this reader may not see yields an obligation that is not
    outstanding: there is no fact here to describe, and inventing one from a row
    the reader cannot open would be the disclosure the scoping exists to
    prevent.

    ``is_overdue`` is ``is_outstanding and is_past``, and ``days_late`` counts
    only then. A discharged deadline in the past is simply past — the register
    answered it, and a number of days late would be a debt nobody owes.
    """
    today = today or timezone.localdate()
    deadline = matter.response_deadline
    outstanding = False
    if deadline is not None:
        discharged = getattr(matter, DISCHARGED, None)
        if discharged is None:
            discharged = (
                annotate_response_obligation(Matter.objects.visible_to(user), user)
                .filter(pk=matter.pk)
                .values_list(DISCHARGED, flat=True)
                .first()
            )
            # ``None`` here means no such row for this reader, not "undischarged".
            discharged = True if discharged is None else discharged
        outstanding = not discharged
    is_past = deadline is not None and deadline < today
    is_overdue = outstanding and is_past
    return ResponseObligation(
        value=deadline,
        display=format_estonian_date(deadline) if deadline is not None else "",
        label=RESPONSE_DEADLINE_LABEL,
        is_outstanding=outstanding,
        is_past=is_past,
        is_overdue=is_overdue,
        days_late=(today - deadline).days if is_overdue and deadline is not None else 0,
    )


def secondary_response_obligation(
    matter: Matter,
    user: Any,
    *,
    primary_date: date | None,
    primary_is_approximate: bool = False,
    today: date | None = None,
) -> ResponseObligation | None:
    """The official obligation, for a surface whose primary date is the plan.

    ``Järgmiseks`` is the operational plan and it stays primary everywhere
    (docs/adr/0050). What a surface showing *Plaanis 15.10.2026* never said is
    that the Chamber still owes an answer on a day that has already gone by —
    the second question :func:`response_obligations` exists to ask. This is that
    answer, shaped for a surface that is already showing something else::

        Plaanis 15.10.2026
        Arvamuse tähtaeg 20.09 · 25 p üle

    ``None`` means *say nothing here*, for one of three reasons, and the third
    is the one worth naming.

    **Nothing is owed.** The obligation is discharged — a visible ``SENT``
    Submission, or a ``CURRENT`` register row recording the opinion work as
    finished — or the Matter carries no ``Arvamuse tähtaeg`` at all
    (:func:`response_obligation_of`).

    **It is not live work.** A closed Matter and an ``ARCHIVE`` record are not
    things anybody can act on, and an overdue warning on one is a queue entry in
    everything but name. The two clauses here are exactly the ones
    :func:`full_matters` and :func:`response_obligations` already apply, so the
    rows this reading describes are precisely ``response_obligations(user)`` —
    asserted rather than assumed, in
    ``tests/test_response_obligation_display.py``.

    **The surface is already showing this date.** ``primary_date`` is what the
    surface prints as its primary fact, and when that *is* the response deadline
    the secondary line would print the same day twice::

        Arvamuse tähtaeg 20.9.2026
        Arvamuse tähtaeg 20.09 · 25 p üle

    which is not a second fact, it is the first one stuttering. Each surface
    passes its own primary: the register row passes
    :attr:`~app.matters.register_dates.RegisterDate.value`, and the Teema
    workspace passes the open step's date, falling back to the Matter's own
    deadline because the header directly above states that one in full.

    **``primary_is_approximate`` is the exception, and it is not a refinement of
    that rule — it is the rule refusing to be fooled by a number.** A step
    planned for *IV kvartal 2026* is stored against the anchor ``2026-10-01``,
    and a Matter whose ``Arvamuse tähtaeg`` happens to be 1 October then makes
    those two values equal. They are not the same fact and they do not even
    share a day: one says *some time in the last quarter*, the other says *this
    Thursday, and the Chamber owes it*. Suppressing the second because the two
    integers matched would erase an official obligation on the strength of a
    value the reader cannot see and never chose — the anchor is not a
    user-visible fact (docs/adr/0079 §2, §12). So an approximate primary never
    suppresses; exact duplicate suppression is untouched.

    **The reader's scope is the one :func:`response_obligation_of` keeps.** A
    ``SENT`` Submission restricted below a NORMAL Matter discharges the
    obligation only for somebody who may see it; a reader who may not still sees
    the deadline outstanding, which is the direction that discloses nothing
    (AUTH-003, docs/adr/0038). Nothing here adds a query of its own: on a list
    the ``DISCHARGED`` annotation is already on the row.

    Nothing about any work population, count or ordering is touched. This adds a
    sentence to a surface; it moves no Matter into a queue.
    """
    if not matter.is_open or matter.record_mode != RecordMode.FULL:
        return None
    obligation = response_obligation_of(matter, user, today)
    if not obligation.is_outstanding:
        return None
    if obligation.value == primary_date and not primary_is_approximate:
        return None
    return obligation


def outstanding_response_deadlines(user: Any, *, owner: Any = None) -> QuerySet[Matter]:
    """Open Matters whose ``Arvamuse tähtaeg`` is still the current instruction.

    The **operational** population, and unchanged in membership: every dated
    surface reads it, and this is the list Minu asjad, Ülevaade, Osakonna töö
    and the register's ``?too=`` populations are built from.

    It is now stated as what it has always meant — the official obligation, minus
    the files where somebody has said what happens next::

        response_obligations(user)                     the obligation
          minus Matters with a visible open Järgmiseks      the plan

    **A `Järgmiseks` outranks it.** ``Arvamuse tähtaeg`` is the date the register
    arrived with: the fallback obligation a file carries until somebody says what
    happens next. The moment a lawyer records an open ``NextAction`` they have
    said it, and their statement is the current work — so the response deadline
    stops being live work and goes back to being what it always was, a recorded
    fact in the Matter's header (docs/adr/0050).

    Three things that rule deliberately is not:

    * **It is not a comparison of dates.** *Any* open action wins, including one
      dated later than the response deadline. A file whose deadline was in
      January and whose lawyer has said «JÄLGIN, vaata uuesti üle 09.10» is not
      overdue in October; it is being monitored, which is what the person
      carrying it decided.
    * **It is not a judgement about the action.** DO, WAIT and MONITOR all count,
      and so does an action with no date at all: «I do not yet know when» is
      still a decision, and a stronger statement about today's work than a date
      nobody has revisited.
    * **It is not a judgement about who wrote the action.** A ``NextAction``
      materialised by the current-register enrichment carries the register's own
      structured ``JÄRGMISEKS`` value, so it is the department's instruction too.
      There is no second idea of a sufficiently human action here.

    And one thing it is **not** any more, which is the whole of this seam: it is
    not a statement that the obligation has been *met*. An instruction suppresses
    the date as today's work; the Chamber still owes the answer, and
    :func:`response_obligations` is where that is now asked. Nothing this
    function returns changed when that separation was made.

    ``NextAction`` is read through ``visible_to``, as the discharge tests are and
    for the same reason: it is a
    :class:`~app.core.models.VisibilityInheritingModel`, and a restricted step
    silently removing a Matter from a deadline list tells a reader that
    restricted work happened on a named file (AUTH-003).

    Every subquery is an ``Exists``, so the whole source stays one query however
    many Matters it holds.
    """
    instructed = NextAction.objects.visible_to(user).filter(
        matter=OuterRef("pk"), status=ActionStatus.OPEN
    )
    return (
        response_obligations(user, owner=owner)
        .annotate(has_open_action=Exists(instructed))
        .filter(has_open_action=False)
    )


def response_deadline_is_outstanding(matter: Matter, user: Any) -> bool:
    """Whether this one Matter's ``Arvamuse tähtaeg`` is still operational work.

    The same question :func:`outstanding_response_deadlines` answers for a
    population, asked about one row — and answered *by that function*, not by a
    second copy of its clauses. A page that re-derived them would be the
    divergence this module exists to prevent: the Matter header would call a
    deadline late while the work list did not, and both would be right about
    their own arithmetic.

    **Operational, not official.** An open ``Järgmiseks`` makes this ``False``
    while the Chamber still owes the answer — that is the precedence working,
    and :func:`response_obligation_of` is the function that asks the other
    question about the same Matter.

    ``False`` for a Matter with no deadline, for a closed or ``ARCHIVE`` record,
    and for one this reader may not see — each because the population it is
    drawn from already says so.
    """
    if matter.response_deadline is None:
        return False
    return outstanding_response_deadlines(user).filter(pk=matter.pk).exists()


def work_items(
    user: Any,
    *,
    today: date | None = None,
    responsible: Any = None,
    latest: date | None = None,
) -> list[WorkItem]:
    """Every dated work item this reader may see, chronologically.

    Four queries, not one per row — one per source, each already narrowed by
    ``visible_to``. ``latest`` bounds the future so a page that only shows five
    weeks does not drag a decade of milestones through Python. Nothing bounds
    the past: work that is late is exactly what these pages exist to surface.

    One Matter can legitimately produce more than one item: a response deadline,
    a DO deadline and a milestone are three different commitments and a
    chronological list of work says so. What must not double is a figure that
    counts *Matters* — which is why :func:`work_population_ids` reduces to
    Matter primary keys rather than counting rows.
    """
    today = today or timezone.localdate()

    actions = dated_actions(user, responsible=responsible)
    deadlines = important_deadlines(user, owner=responsible)
    responses = outstanding_response_deadlines(user, owner=responsible)
    waits = open_feedback_waits(user, owner=responsible)
    if latest is not None:
        actions = actions.filter(target_date__lte=latest)
        deadlines = deadlines.filter(date_value__lte=latest)
        responses = responses.filter(response_deadline__lte=latest)
        waits = waits.filter(feedback_deadline__lte=latest)

    items = [action_item(action, today) for action in actions]
    items += [_deadline_item(record, today) for record in deadlines]
    items += [_response_deadline_item(matter, today) for matter in responses]
    items += [_feedback_wait_item(engagement, today) for engagement in waits]
    return sort_items(items)


def sort_items(items: list[WorkItem]) -> list[WorkItem]:
    """Oldest first, then by reference so the order is stable between loads."""
    return sorted(items, key=lambda item: (item.when or date.max, item.reference, item.text))


# ---------------------------------------------------------------------------
# Banding
# ---------------------------------------------------------------------------


def start_of_iso_week(today: date) -> date:
    """Monday of the week ``today`` falls in. ISO weeks run Monday–Sunday."""
    return today - timedelta(days=today.weekday())


def end_of_iso_week(today: date) -> date:
    """Sunday of the week ``today`` falls in. ISO weeks run Monday–Sunday."""
    return today + timedelta(days=6 - today.weekday())


def band_of(
    item: WorkItem,
    today: date,
    week_end: date,
    horizon: date | None,
    *,
    next_30_end: date | None = None,
) -> str | None:
    """Which band this item belongs to, or ``None`` if it is beyond the window.

    The last day of a period is what decides whether it is behind us. An
    expectation recorded as *III kvartal 2026* has not passed on 2 July, and
    banding it on its anchor would call a quarter that has barely started late.

    A past item that is not genuinely overdue is *ripe for a look*, and this is
    where the redesign changed shape: it used to have a block of its own headed
    «Ülevaatamiseks küps» with a sentence under it explaining that waiting is
    not lateness. It now sits at the top of **Sel nädalal**, in date order with
    everything else, carrying a neutral ``N p`` rather than ``N p üle``. The
    sentence is gone because the row no longer needs defending; the semantics
    are unchanged and are still enforced by ``is_overdue``
    (design handoff 03 §1, 01 §3.1).

    That also catches the case the old banding lost entirely: a DO whose source
    named a vague month is stored as an expectation rather than a deadline, so
    it can never be overdue and used to fall out of every band and off the page
    (app/legacy_import/register_next_actions.py).

    **Järgmised 30 päeva takes only day-precise dates.** A month or a quarter
    landing inside the next thirty days is not a date somebody can plan a
    Tuesday around, and putting *september 2026* in a band headed by a number of
    days would read as a precision the source never gave (01 §3.2).

    ``?kuni=`` narrows **Hiljem only**. It is that band's own control and it
    must not be able to hide something due next week.
    """
    when = item.when
    if when is None:
        return None
    end = item.period_end or when
    if end < today:
        # Genuinely late, or merely come round. Both are now; only one is red.
        return BAND_OVERDUE if item.is_overdue else BAND_WEEK
    if when <= week_end:
        # Today, a period already running, or a day still inside this week.
        return BAND_WEEK
    next_30_end = next_30_end or today + timedelta(days=NEXT_30_DAYS)
    if when <= next_30_end and not item.is_approximate:
        return BAND_NEXT_30
    if horizon is None or when <= horizon:
        return BAND_LATER
    return None


def band_items(
    items: list[WorkItem],
    today: date,
    *,
    week_end: date | None = None,
    horizon: date | None = None,
    overflow_urls: dict[str, str] | None = None,
) -> list[WorkBand]:
    """The bands that actually hold something, in reading order.

    An empty band is omitted rather than rendered empty: four headings above
    four "ei ole ühtegi" lines is a page that looks like a data-quality problem
    rather than a quiet morning.

    ``overflow_urls`` maps a band key to the register list holding that band's
    whole population, for the rows the cap will not render. It is supplied by
    the caller rather than built here because the URL is *this person's* work
    list, and only the caller knows whose desk is being drawn — which is also
    what makes it the same string the strip figure above already links to,
    rather than a second one that resolves to a similar list
    (``app/matters/my_work.py``).
    """
    week_end = week_end or end_of_iso_week(today)
    next_30_end = today + timedelta(days=NEXT_30_DAYS)
    grouped: dict[str, list[WorkItem]] = {key: [] for key in BAND_ORDER}
    for item in items:
        key = band_of(item, today, week_end, horizon, next_30_end=next_30_end)
        if key is not None:
            grouped[key].append(item)

    # Most overdue first inside the red band. Everything else is left in the
    # chronological order `sort_items` already put it in — which is what puts
    # the ripe reviews, whose dates are the oldest in the band, at the top of
    # *Sel nädalal* (design handoff 03 §1: «vanimad ees»).
    grouped[BAND_OVERDUE].sort(key=lambda item: item.period_end or item.when or date.max)

    overflow = overflow_urls or {}
    return [
        WorkBand(
            key=key,
            label=BAND_LABELS[key],
            items=grouped[key][:BAND_LIMIT],
            # Before the slice, not after it. This is the whole of UX-002.
            total=len(grouped[key]),
            visible=BAND_VISIBLE[key],
            more_url=overflow.get(key, ""),
        )
        for key in BAND_ORDER
        if grouped[key]
    ]


# ---------------------------------------------------------------------------
# Population predicates the three surfaces share
# ---------------------------------------------------------------------------


def overdue_items(items: list[WorkItem]) -> list[WorkItem]:
    """Genuinely late work. Never includes a passed review date."""
    return [item for item in items if item.is_overdue]


def review_ripe_items(items: list[WorkItem]) -> list[WorkItem]:
    return [item for item in items if item.is_review_ripe]


def week_items(items: list[WorkItem], today: date, week_end: date | None = None) -> list[WorkItem]:
    """Dated work falling inside the current ISO week, today included."""
    week_end = week_end or end_of_iso_week(today)
    return [item for item in items if item.when is not None and today <= item.when <= week_end]


def real_deadlines(items: list[WorkItem]) -> list[WorkItem]:
    """Only what a department may honestly call a deadline.

    DO deadlines, ``Oluline tähtaeg`` and ``Arvamuse tähtaeg``. A WAIT's
    expected date and a MONITOR's review date are commitments nobody made — they
    belong in the intervention list, where they read as "look at this again",
    not in a table headed *Tähtajad* (master specification 18.8).

    The response deadline joins the other two rather than softening them: it is
    the day Koda promised its opinion, which is a commitment in exactly the
    sense a review date is not.

    **An open feedback wait is not one of them**, and the omission is a decision
    rather than an oversight. «Vastake 22. septembriks» is a day this office
    named to its own members; putting it in a table headed *Tähtajad* beside
    `Arvamuse tähtaeg` would read as a fourth obligation the Chamber owes, and
    the register's deadline groups would start counting consultations as
    promises. It is still work, still banded and still capable of being late —
    those are readings of the *list*, and this predicate is about the *word*
    (docs/adr/0086 §3).

    Here rather than in :mod:`app.matters.overview` because the register now
    filters on it too: a *Tähtajad* group that opens a list assembled by a
    second, similar predicate is a group whose count and list drift apart.
    """
    return [
        item
        for item in items
        if not item.is_action or item.action_kind == ActionKind.DO.value
        if item.meaning in (MEANING_DEADLINE, MEANING_IMPORTANT, MEANING_RESPONSE)
    ]


# ---------------------------------------------------------------------------
# Named work populations, addressable from a URL
# ---------------------------------------------------------------------------
#
# Why these exist
# ---------------
# Every figure on Ülevaade is a promise that a list exists behind it, and four
# of those figures count *work* rather than Matters: a passed ``Oluline
# tähtaeg`` carries no open NextAction, so the register's ``?tegevus=`` cannot
# express it and a link there opened a list shorter than the number above it.
#
# The fix is not a second query language. It is this: one function turns the
# shared read model into a set of Matter primary keys, Ülevaade counts that set,
# and the register's ``?too=`` filter narrows to the *same* set. The count and
# the list cannot disagree, because there is one selector and both call it
# (master specification 18.9).

WORK_OVERDUE = "hilinenud"
WORK_RIPE = "ulevaatamiseks"
WORK_DEADLINE_THIS_WEEK = "tahtaeg-nadalal"
WORK_DEADLINE_NEXT_WEEK = "tahtaeg-jargmisel"
#: The month ahead, past next week, counted as thirty days from today.
WORK_DEADLINE_30_DAYS = "tahtaeg-30"
#: Everything dated past that thirty-day horizon.
WORK_DEADLINE_BEYOND = "tahtaeg-kaugemal"
WORK_NEEDS_ATTENTION = "sekkumist"
#: Real deadlines inside a window the caller names, or every one of them from
#: today on when it names none. The one population that takes an argument, and
#: what both deadline panels now link through: their windows move with the
#: weekday and with the length of the month, so a fixed name could only ever
#: approximate them, and each is still obliged to open the exact list it counted
#: (`?too_alates=`, `?too_kuni=`; design handoff, Osakond §3; ADR 0046).
WORK_DEADLINE_WINDOW = "tahtaeg-vahemik"
#: Open work nothing has happened on for a month. Not a date population: it is
#: read from the derived last-activity fact, which is why it is resolved as a
#: queryset in `work_population_ids` rather than out of the item list.
WORK_QUIET_30 = "muutusteta-30"

#: How long silence has to last before it is worth a line on a manager's page.
#: A month, which is the review rhythm the department actually keeps.
QUIET_DAYS = 30

#: The thirty-day horizon the third deadline group ends at, counted from today.
DEADLINE_MONTH_DAYS = 30

#: What each value selects, and how it reads in a filter chip.
WORK_POPULATION_LABELS: dict[str, str] = {
    WORK_OVERDUE: "Üle tähtaja",
    WORK_RIPE: "Ülevaatamiseks",
    WORK_DEADLINE_THIS_WEEK: "Tähtaeg sel nädalal",
    WORK_DEADLINE_NEXT_WEEK: "Tähtaeg järgmisel nädalal",
    WORK_DEADLINE_30_DAYS: "Tähtaeg 30 päeva jooksul",
    WORK_DEADLINE_BEYOND: "Tähtaeg kaugemal",
    WORK_DEADLINE_WINDOW: "Tähtaeg ees",
    WORK_NEEDS_ATTENTION: "Vajab sekkumist",
    WORK_QUIET_30: f"Muutusteta {QUIET_DAYS} p",
}

WORK_POPULATIONS: tuple[str, ...] = tuple(WORK_POPULATION_LABELS)


#: The four fixed deadline windows, in order. Consecutive and exhaustive by
#: construction: each starts the day after the previous one ends, and the last
#: has no end, so a future date lands in exactly one of them.
#:
#: These are register populations — a chip, a bookmark, a pasted link. Ülevaade's
#: *Tähtajad* panel no longer reads them: since ADR 0046 it cuts the calendar
#: week and the rest of the calendar month, neither of which a fixed name can
#: express, and it links through `WORK_DEADLINE_WINDOW` instead. Nothing here
#: changed meaning, so nothing anybody saved stopped working.
DEADLINE_WINDOW_KEYS: tuple[str, ...] = (
    WORK_DEADLINE_THIS_WEEK,
    WORK_DEADLINE_NEXT_WEEK,
    WORK_DEADLINE_30_DAYS,
    WORK_DEADLINE_BEYOND,
)


def deadline_window(key: str, today: date) -> tuple[date, date | None]:
    """The closed interval one deadline group holds, both ends inclusive.

    ``None`` as the end means "and everything after", which only the last group
    returns. Days rather than weeks past next week: *30 päeva* is the heading
    the reader sees and the horizon the group is counted to, so it is measured
    from today and not rounded to a week boundary.
    """
    week_end = end_of_iso_week(today)
    next_end = week_end + timedelta(days=7)
    month_end = today + timedelta(days=DEADLINE_MONTH_DAYS)
    if key == WORK_DEADLINE_THIS_WEEK:
        return today, week_end
    if key == WORK_DEADLINE_NEXT_WEEK:
        return week_end + timedelta(days=1), next_end
    if key == WORK_DEADLINE_30_DAYS:
        # `next_end` is at most thirteen days out, so this interval is never
        # inverted and the four windows are always in order.
        return next_end + timedelta(days=1), month_end
    return month_end + timedelta(days=1), None


def _deadlines_between(items: list[WorkItem], start: date, end: date | None) -> list[WorkItem]:
    return [
        item
        for item in real_deadlines(items)
        if item.when is not None and start <= item.when and (end is None or item.when <= end)
    ]


def work_population_items(
    items: list[WorkItem],
    key: str,
    today: date,
    *,
    window: tuple[date, date | None] | None = None,
) -> list[WorkItem]:
    """The rows of one named population, out of an already-read work model.

    Ülevaade passes the list it already holds; the register filter reads its
    own. Same function either way, which is the whole point.

    ``window`` is read by :data:`WORK_DEADLINE_WINDOW` and ignored by every
    other key. Omitted, it means "from today on", so that population is a
    legitimate thing to pick from the register's own control rather than a value
    that selects nothing without two companion parameters.
    """
    if key == WORK_OVERDUE:
        return overdue_items(items)
    if key == WORK_RIPE:
        return review_ripe_items(items)
    if key == WORK_DEADLINE_WINDOW:
        start, end = window or (today, None)
        return _deadlines_between(items, start, end)
    if key in DEADLINE_WINDOW_KEYS:
        start, end = deadline_window(key, today)
        return _deadlines_between(items, start, end)
    if key == WORK_NEEDS_ATTENTION:
        # The dated half. The two undated halves — no next action, no owner —
        # are querysets rather than work items and are added by the caller that
        # has the reader (`work_population_ids`).
        return overdue_items(items) + review_ripe_items(items)
    return []


#: "no person was named", as distinct from "the person named is nobody" — which
#: is a real filter value (`?too_vastutaja=puudub`, the work nobody carries).
ANY_PERSON = object()


def work_population_ids(
    user: Any,
    key: str,
    *,
    today: date | None = None,
    items: list[WorkItem] | None = None,
    responsible: Any = ANY_PERSON,
    quiet: QuerySet[Matter] | None = None,
    ownerless: QuerySet[Matter] | None = None,
    window: tuple[date, date | None] | None = None,
) -> set[Any]:
    """The Matter primary keys one named population holds, for this reader.

    ``items`` lets a page that has already read the work model avoid reading it
    again; omitting it reads the same model with the same authorization. Either
    way the answer is a set of Matters, because that is what the register lists
    and what a figure beside it must therefore count.

    ``responsible`` narrows to one person's work. The caller filters ``items``
    for the dated half — a NextAction names who must do it — and this handles
    the two undated halves of *Vajab sekkumist*, which have no responsible
    column at all: an uninstructed Matter belongs to its owner, and an unowned
    one belongs to nobody, which is precisely why it is on the list. Getting
    that second one wrong would put every unassigned file into every
    colleague's count.
    """
    if key not in WORK_POPULATION_LABELS:
        return set()
    today = today or timezone.localdate()
    if key == WORK_QUIET_30:
        # A Matter-level state, not a dated obligation, so it has no responsible
        # person of its own. Narrowed by *owner* when one is named — the same
        # reading `WORK_NEEDS_ATTENTION` gives its two undated halves below,
        # because a file nobody has touched belongs to whoever carries it.
        #
        # Answered before the work model is read, because it does not consult
        # it: `work_population_items` has no branch for this key. Reading it
        # here cost three queries and a full row materialisation that the next
        # line threw away, on every `?too=muutusteta-30` in the product.
        quiet_ids = quiet_matters(user, today)
        if responsible is ANY_PERSON:
            return set(quiet_ids)
        owned = Matter.objects.filter(pk__in=quiet_ids)
        owned = (
            owned.filter(owner__isnull=True)
            if responsible is None
            else owned.filter(owner=responsible)
        )
        return set(owned.values_list("pk", flat=True))
    if items is None:
        items = work_items(user, today=today)
    ids = {item.matter_id for item in work_population_items(items, key, today, window=window)}
    if key == WORK_NEEDS_ATTENTION:
        # Reused when the caller has them, because `visible_to` resolves the
        # reader's scope on every call and resolving it asks the database
        # whether this person holds a break-glass grant. Ülevaade has already
        # paid for both of these (`overview.Populations`), and a page that
        # re-resolves them here pays twice for one answer.
        quiet = matters_without_action(user) if quiet is None else quiet
        ownerless = ownerless_matters(user) if ownerless is None else ownerless
        if responsible is not ANY_PERSON:
            quiet = quiet.filter(owner=responsible)
            if responsible is not None:
                ownerless = ownerless.none()
        ids |= set(quiet.values_list("pk", flat=True))
        ids |= set(ownerless.values_list("pk", flat=True))
    return ids


def no_next_action_q() -> Q:
    """Matters carrying no open instruction, as a condition rather than a list.

    Reader-blind, and therefore **not** what a page counts with: an action
    restricted below its Matter is invisible to most readers, so this condition
    would call the Matter instructed while the register — which asks the same
    question through ``NextAction.objects.visible_to`` — lists it as having
    none. Kept for the one caller that genuinely wants the reader-blind fact,
    and every count goes through :func:`matters_without_action` instead.
    """
    return ~Q(
        pk__in=NextAction.objects.filter(status=ActionStatus.OPEN).values("matter_id"),
    )


def matters_without_action(user: Any, *, owner: Any = None) -> QuerySet[Matter]:
    """Open Matters with no active NextAction — the one attention state no date can produce.

    Without it a Matter simply stops appearing anywhere and goes quiet, which is
    the failure the whole right rail exists to prevent (design handoff,
    recommendation 1).

    The condition is the register's own ``?tegevus=puudub``, imported rather
    than restated. It was restated once, reader-blind, and the two answers
    differed on exactly the Matters that matter most: one carrying an action
    only its participants may read counted as instructed here and as
    uninstructed in the list the figure linked to.
    """
    from app.matters.selectors import MISSING, filter_by_next_action

    queryset = filter_by_next_action(open_matters(user), user, MISSING)
    if owner is not None:
        queryset = queryset.filter(owner=owner)
    return queryset


def ownerless_matters(user: Any) -> QuerySet[Matter]:
    return open_matters(user).filter(owner__isnull=True)


def quiet_matters(user: Any, today: date | None = None, *, days: int = QUIET_DAYS) -> list[Any]:
    """Open work whose last known activity is older than ``days``, as ids.

    *Muutusteta 30 p*, and the reason it is not a queryset. The last-activity
    fact is a **precedence** over six candidate dates — a closure, a sent
    opinion, an entry, an action, a consultation, an archived page — resolved in
    Python by :func:`app.matters.activity.activity_of` so that two facts on the
    same day pick the more canonical one. Reproducing that ordering as SQL would
    be a second definition of "last activity" beside the one every register row
    already prints, and the two would disagree on the day they were most likely
    to be compared.

    So the annotation is asked for once, in one query, and the comparison is
    done over the rows it returns. A Matter with no known activity at all is
    **not** here: nothing is recorded either way, and a page that called that
    silence would be reporting the absence of a record as the absence of work
    (app/matters/activity.py, ADR 0026).
    """
    from app.matters.activity import activity_of, annotate_last_activity

    today = today or timezone.localdate()
    cutoff = today - timedelta(days=days)
    # No `.only()`. `activity_of` reads six annotations *and* four stored
    # columns — the closure date, the received date, the origin and
    # `updated_at` — so deferring anything here turns one query into one per
    # row, silently, and looks fine on a development database with twelve
    # Matters in it (app/matters/activity.py).
    population = annotate_last_activity(open_matters(user), user)
    quiet = []
    for matter in population:
        fact = activity_of(matter)
        if fact is not None and fact.occurred_on < cutoff:
            quiet.append(matter.pk)
    return quiet


__all__ = [
    "BAND_LABELS",
    "BAND_LATER",
    "BAND_NEXT_30",
    "BAND_ORDER",
    "BAND_OVERDUE",
    "BAND_VISIBLE",
    "BAND_WEEK",
    "DISCHARGED",
    "MEANING_DEADLINE",
    "MEANING_EXPECTED",
    "MEANING_FEEDBACK_WAIT",
    "MEANING_IMPORTANT",
    "MEANING_RESPONSE",
    "MEANING_REVIEW",
    "SOURCE_FEEDBACK_WAIT",
    "SOURCE_IMPORTANT_DEADLINE",
    "SOURCE_NEXT_ACTION",
    "SOURCE_RESPONSE_DEADLINE",
    "WORK_DEADLINE_30_DAYS",
    "WORK_DEADLINE_BEYOND",
    "WORK_DEADLINE_NEXT_WEEK",
    "WORK_DEADLINE_THIS_WEEK",
    "WORK_DEADLINE_WINDOW",
    "WORK_NEEDS_ATTENTION",
    "WORK_OVERDUE",
    "WORK_POPULATIONS",
    "WORK_POPULATION_LABELS",
    "WORK_QUIET_30",
    "WORK_RIPE",
    "ActionKind",
    "ResponseObligation",
    "WorkBand",
    "WorkItem",
    "action_item",
    "annotate_response_obligation",
    "band_items",
    "band_of",
    "dated_actions",
    "deadline_window",
    "end_of_iso_week",
    "full_matters",
    "important_deadlines",
    "matters_without_action",
    "open_feedback_waits",
    "open_matters",
    "outstanding_response_deadlines",
    "overdue_items",
    "ownerless_matters",
    "quiet_matters",
    "real_deadlines",
    "response_deadline_is_outstanding",
    "response_obligation_of",
    "response_obligations",
    "review_ripe_items",
    "secondary_response_obligation",
    "sort_items",
    "start_of_iso_week",
    "undated_actions",
    "week_items",
    "work_items",
    "work_population_ids",
    "work_population_items",
]
