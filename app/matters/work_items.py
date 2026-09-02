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
  due, which is the commonest deadline on the whole register.

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

**Authorization before arithmetic.** Every queryset starts from
``visible_to(user)``. A restricted Matter the reader may not see contributes
nothing to a count, a band or a row — so nothing downstream has to remember to
hide it, and no template re-implements a security check.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, timedelta
from typing import Any

from django.db.models import Exists, OuterRef, Q, QuerySet
from django.urls import reverse
from django.utils import timezone

from app.core import dates
from app.core.dates import format_estonian_date
from app.intelligence.enums import FactStatus
from app.intelligence.models import MatterImportantDate
from app.matters.enums import RecordMode
from app.matters.models import Matter
from app.submissions.enums import SubmissionStatus
from app.submissions.models import Submission
from app.workflow.dates import format_at_precision, period_bounds
from app.workflow.enums import (
    REVIEW_KINDS,
    ActionKind,
    ActionStatus,
    DatePrecision,
    DateSemantics,
)
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

#: What the date on a row means, in the words the department agreed.
#:
#: ``OODATAV AEG`` rather than the stored enum's *Oodatav umbes*: the label a
#: lawyer reads is a product decision and the column value is a storage one, and
#: this is the seam between them. Nothing here renames anything stored.
MEANING_DEADLINE = "TÄHTAEG"
MEANING_EXPECTED = "OODATAV AEG"
MEANING_REVIEW = "VAATAN ÜLE"
MEANING_IMPORTANT = "OLULINE TÄHTAEG"
#: The business term, unchanged. The register's column, the Matter header, the
#: old dashboard's *Eesolevad tähtajad* table and this row all say the same two
#: words, because a synonym invented here would be a fourth name for one date.
MEANING_RESPONSE = "ARVAMUSE TÄHTAEG"

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
#: available inline behind «Näita veel N ▾» — nothing leaves the page, and the
#: count in the heading is still the honest total.
BAND_VISIBLE: dict[str, int | None] = {
    BAND_OVERDUE: 10,
    BAND_WEEK: 10,
    BAND_NEXT_30: 5,
    BAND_LATER: 2,
}

#: How far past this week *Järgmised 30 päeva* reaches.
NEXT_30_DAYS = 30

#: How many rows a page may render before it stops being read. The count above
#: each list is the honest total either way.
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
    the month and prints as *september 2026*, because printing ``01.09.2026``
    would manufacture a day nobody named (master specification 3.5).
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
    def short_date(self) -> str:
        """The value the date cell prints — the honest one, not always a day.

        ``10 p üle`` for something genuinely late, a bare ``9 p`` for a review
        that has merely come round, ``täna`` for today, ``26.08`` for an exact
        date this year, and the stored period verbatim for anything recorded to
        a month or a quarter.

        The word *üle* appears only where something was actually missed. A
        ministry that has not replied is not over anything, and one word is the
        whole difference between "you failed" and "have a look at this"
        (master specification 18.8).
        """
        if self.when is None:
            return "—"
        late = self.days_late
        if late:
            return f"{late} p üle" if self.is_overdue else f"{late} p"
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

        ``TÄHTAEG 14.08`` rather than a bare ``TÄHTAEG``, because the cell above
        it is showing *10 p üle* and the reader still needs the day it was.
        """
        if self.when is not None and self.days_late:
            return f"{self.meaning} {self.display_date}"
        return self.meaning


@dataclass(frozen=True)
class WorkBand:
    """One band of the timeline. Rendered only when it holds something.

    ``visible`` is how many rows are on screen; ``rest`` is the remainder of the
    *same* list. One query produces both, so the number in the heading, the rows
    under it and the number on «Näita veel N ▾» are three readings of one answer
    and cannot disagree.
    """

    key: str
    label: str
    items: list[WorkItem]
    #: ``None`` shows everything.
    visible: int | None = None

    @property
    def count(self) -> int:
        return len(self.items)

    @property
    def preview(self) -> list[WorkItem]:
        return self.items if self.visible is None else self.items[: self.visible]

    @property
    def rest(self) -> list[WorkItem]:
        return [] if self.visible is None else self.items[self.visible :]

    @property
    def remaining(self) -> int:
        return len(self.rest)


# ---------------------------------------------------------------------------
# Building items
# ---------------------------------------------------------------------------


def open_matters(user: Any) -> QuerySet[Matter]:
    """Open FULL Matters the reader may see.

    ARCHIVE rows never reach a work surface: a decade of imported register rows
    is historical evidence, not a queue anybody can act on.
    """
    return Matter.objects.visible_to(user).filter(is_open=True, record_mode=RecordMode.FULL)


def action_item(action: NextAction, today: date) -> WorkItem:
    anchor = action.target_date
    end = anchor
    if anchor is not None and action.date_precision != DatePrecision.EXACT:
        # A month or a quarter is behind us only once its *last* day is, so the
        # stored precision decides where the item stops being current.
        try:
            _, end = period_bounds(anchor, action.date_precision)
        except Exception:  # pragma: no cover - a stored precision the parser refuses
            end = anchor
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


def outstanding_response_deadlines(user: Any, *, owner: Any = None) -> QuerySet[Matter]:
    """Open Matters whose ``Arvamuse tähtaeg`` is still the current instruction.

    Authorization first, like every other source here: the population starts
    from :func:`open_matters`, so a restricted Matter contributes nothing to a
    count, a band or a row for a reader who may not see it.

    **Outstanding, not merely stored.** The obligation a response deadline
    describes is discharged by sending the opinion, and the product already has
    one definition of that — a ``SENT`` :class:`~app.submissions.models.Submission`
    on the Matter, which is what the old dashboard's *Tähtaeg möödas, arvamust
    ei ole saadetud* row and `selectors.attention_items` both test. That same
    definition is reused here rather than restated.

    **A `Järgmiseks` outranks it.** ``Arvamuse tähtaeg`` is the date the register
    arrived with: the fallback obligation a file carries until somebody says what
    happens next. The moment a lawyer records an open ``NextAction`` they have
    said it, and their statement is the current work — so the response deadline
    stops being live work and goes back to being what it always was, a recorded
    fact in the Matter's header (docs/adr/0050).

    Three things this rule deliberately is not:

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

    Both subqueries are ``Exists``, so the whole source stays one query however
    many Matters it holds, and both are deliberately **reader-blind** — as the
    fulfilment rule already was. Each can only ever *remove* a row, so neither
    can widen what anybody sees, and a hidden child cannot be read through the
    difference: what changes is whether one date is called work, never whether a
    restricted record is disclosed. Scoping them would be worse than useless
    here — it would make one reader's deadline live and another's suppressed,
    which is two answers to a question about the Matter rather than about the
    reader.

    ``owner`` narrows by ``Matter.owner``, for the reason
    :func:`important_deadlines` does: this deadline belongs to whoever carries
    the file. An ownerless Matter's deadline therefore reaches nobody's Minu
    töö and appears as *vastutajata* on the department surfaces, which is the
    honest place for work nobody has been given.
    """
    sent = Submission.objects.filter(matter=OuterRef("pk"), status=SubmissionStatus.SENT)
    instructed = NextAction.objects.filter(matter=OuterRef("pk"), status=ActionStatus.OPEN)
    queryset = (
        open_matters(user)
        .filter(response_deadline__isnull=False)
        .annotate(has_sent_submission=Exists(sent), has_open_action=Exists(instructed))
        .filter(has_sent_submission=False, has_open_action=False)
        .select_related("stage", "owner")
    )
    if owner is not None:
        queryset = queryset.filter(owner=owner)
    return queryset


def work_items(
    user: Any,
    *,
    today: date | None = None,
    responsible: Any = None,
    latest: date | None = None,
) -> list[WorkItem]:
    """Every dated work item this reader may see, chronologically.

    Three queries, not one per row — one per source, each already narrowed by
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
    if latest is not None:
        actions = actions.filter(target_date__lte=latest)
        deadlines = deadlines.filter(date_value__lte=latest)
        responses = responses.filter(response_deadline__lte=latest)

    items = [action_item(action, today) for action in actions]
    items += [_deadline_item(record, today) for record in deadlines]
    items += [_response_deadline_item(matter, today) for matter in responses]
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
) -> list[WorkBand]:
    """The bands that actually hold something, in reading order.

    An empty band is omitted rather than rendered empty: four headings above
    four "ei ole ühtegi" lines is a page that looks like a data-quality problem
    rather than a quiet morning.
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

    return [
        WorkBand(
            key=key,
            label=BAND_LABELS[key],
            items=grouped[key][:BAND_LIMIT],
            visible=BAND_VISIBLE[key],
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
    "MEANING_DEADLINE",
    "MEANING_EXPECTED",
    "MEANING_IMPORTANT",
    "MEANING_RESPONSE",
    "MEANING_REVIEW",
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
    "WorkBand",
    "WorkItem",
    "action_item",
    "band_items",
    "band_of",
    "dated_actions",
    "deadline_window",
    "end_of_iso_week",
    "important_deadlines",
    "matters_without_action",
    "open_matters",
    "outstanding_response_deadlines",
    "overdue_items",
    "ownerless_matters",
    "quiet_matters",
    "real_deadlines",
    "review_ripe_items",
    "sort_items",
    "start_of_iso_week",
    "undated_actions",
    "week_items",
    "work_items",
    "work_population_ids",
    "work_population_items",
]
