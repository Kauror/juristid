"""`Teema käik` — the horizontal milestone strip above the chronology.

**A sparse procedural story, not a digest of every dated fact.** The chronology
answers *what has happened* and the header answers *where the file stands now*;
this strip answers the third question, which is *what course is this file on* —
the major acts it has been through, and the next dated point it is known to be
heading for. Five of them exist today:

    Alustatud · Koja arvamus · Arvamuse tähtaeg · Jõustumine · Lõpetatud

and a Matter draws only the ones it really has. That is the whole vocabulary.
An earlier version of this module also projected the current `StageVocabulary`,
every `MatterEngagement` and every `MatterImportantDate` — six sources, which
made the strip a second, shorter copy of the chronology with the header's
`Hetkeseis` pinned in the middle of it. Each of those records is still canonical
and still reads where it belongs; none of them is a major procedural act
(docs/adr/0074 §12.1).

**A known beginning and a known destination.** A newly created Matter with a
response deadline is not a file with only a start: where its first phase is
heading is already recorded, on the Matter's own `response_deadline` column, and
a strip that drew `Alustatud` alone would be withholding it. So the strip
carries two kinds of truthful information — acts that have happened, and
canonical dated points the file is known to be heading for:

    Alustatud · Arvamuse tähtaeg 20.09.2026

A future column claims nothing about the past. It says *this is the next dated
point in the process*, which is exactly what the record says (docs/adr/0074
§12.4).

**Formal, never personal.** `Arvamuse tähtaeg` is `Matter.response_deadline` —
the day an answer is due to whoever asked for it. A lawyer's own
`NextAction.target_date` is a work plan, it reads as `Järgmiseks` on the surfaces
that show a plan, and carrying a date is not what makes something a procedural
milestone. `NextAction` draws no column and this module does not read it.

**Derived, never stored.** There is no `ProcessTimeline` table and no
`Milestone` model, and adding one to draw a strip would be inventing a second
place for facts the domain already holds — a place that could then disagree with
the records it was built from. Every step below is read off a canonical record
that exists for its own reasons: the Matter's own creation, its response
deadline, a sent `Submission`, a recorded commencement, the Matter's own closure.

**Latest, never current.** No column is marked as the one the file is standing
on. Where the file stands *now* is `Hetkeseis` in the header, which is the one
place it is stated — a closed Matter whose last recorded stage was `Riigikogus`
must never read `Riigikogus · praegu` on a strip, and a deadline three weeks out
must never read `praegu` either. The connector already stops at the rightmost dot
because `:last-child` draws none, so the strip ends somewhere visible without any
milestone claiming to be the current one.

**Scoped before it is derived, not filtered afterwards.** The sent opinions are
read through their own `visible_to`, and the commencements arrive already scoped
from `matter_intelligence`, so a restricted child cannot change the number of
columns, the connector count, the spacing, the ordering, a label or a date.
Deriving first and hiding afterwards would leave exactly those observable
traces, which is the leak class the 2026-09 audit closed and this strip must not
reopen (AUTH-003, docs/adr/0074 §13).
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from typing import Any

from django.utils import timezone

from app.core.dates import format_estonian_date
from app.matters.enums import MatterOrigin
from app.matters.models import Matter
from app.workflow.dates import format_at_precision

#: The five labels this strip can draw. Each names an *act* or a *formal dated
#: point*, never a stored title and never an outcome.
#:
#: `Alustatud` and not `Loodud`: the milestone is that the work started, which
#: is what a reader placing the file in a process is looking for. The database
#: row being written is not a procedural act.
STARTED_LABEL = "Alustatud"

#: `Koja arvamus` and not `Arvamus välja`. The chronology keeps `Arvamus välja`
#: — there the sentence is about an event, and «välja» is what happened to the
#: letter. Here the column names the thing itself, which is the Chamber's
#: opinion. `Submission` has no short name of its own and «Koja arvamus
#: pakendiseaduse kohta, 2026-06-02» is not a column heading.
SENT_LABEL = "Koja arvamus"

#: `Arvamuse tähtaeg`, which is `Matter.response_deadline`'s own `verbose_name`
#: and the header band's own word for the same column. Not a bare `Tähtaeg`,
#: which would not say whose; not `Järgmiseks` or `Plaanis`, which name the
#: lawyer's work plan and come from `NextAction`; and not `Lõpp`, because an
#: answer falling due is not the end of the proceeding.
DEADLINE_LABEL = "Arvamuse tähtaeg"

#: `Jõustumine` — the noun, matching `MatterEffectiveDate`'s own
#: `verbose_name`. Not `Jõustub`/`Jõustus`, which the chronology uses because
#: its sentence has a tense; a strip column is a name, and a rail whose label
#: changed on the day the date went past would not be comparable between two
#: files, or between two renders of one.
EFFECTIVE_LABEL = "Jõustumine"

#: `Lõpetatud` and not `Menetlus lõppes`, `Jõustus` or `Loobuti`. Those are the
#: `Disposition` — *why* it ended — and a strip whose milestone labels varied
#: with the outcome would stop being a comparable rail. The disposition is
#: secondary information and reads as the step's `title`.
CLOSED_LABEL = "Lõpetatud"

#: Where each milestone sits in the proceeding, used **only** to break a tie
#: between two milestones recorded on the same day. Dates decide the order; this
#: decides what «the same day» means, so two renders of one unchanged Matter
#: cannot disagree and a same-day pair does not read backwards.
#:
#: The sequence is the procedure's own. A file cannot act before it started; an
#: opinion sent *on* the deadline day was sent by the deadline, so it precedes
#: the deadline rather than appearing to have missed it; an answer falls due
#: before the act it concerns takes effect; and nothing happens to a file after
#: it closes.
#:
#: Two milestones of the *same* kind on one day keep the order their source was
#: read in, which `list.sort` being stable preserves and which each source fixes
#: deterministically: sent opinions by `(sent_at, pk)` below, commencements by
#: `MatterEffectiveDate.Meta.ordering`, which ends in `id`.
PHASE_STARTED = 0
PHASE_SENT = 1
PHASE_DEADLINE = 2
PHASE_EFFECTIVE = 3
PHASE_CLOSED = 4


@dataclass(frozen=True)
class ProcessStep:
    """One column of the strip.

    ``sort_on`` and ``phase`` are how the strip is ordered and neither is ever
    rendered. ``detail`` is secondary information — a closure's `Disposition`,
    a commencement's «mis jõustub» — and reads as the column's `title` rather
    than as a second visible line, because «Vastus esitatud ja järeltegevus
    tehtud» under a 150 px column is a three-line wrap that pushes its
    neighbours' dates out of alignment.
    """

    label: str
    display: str
    detail: str
    sort_on: date
    phase: int

    @property
    def date_line(self) -> str:
        """``12.5.2026`` — the day, and nothing appended.

        No `praegu` and no `N p`. A future milestone is drawn as its date
        exactly like a past one: the strip states the dated point the record
        holds, and a countdown beside it would be this component asserting an
        urgency it was not asked to judge. The countdown is untouched where it
        already has a home — the header band and the work lists.
        """
        return self.display


def process_steps(*, matter: Matter, user: Any, intelligence: Any = None) -> list[ProcessStep]:
    """The strip for one Matter, earliest first, or an empty list.

    ``intelligence`` is the page's single scoped read of the structured facts,
    passed in so the Matter page cannot ask two differently scoped questions
    about one file. It falls back to a read of its own for callers that have
    none — the same seam `matter_timeline` uses.
    """
    from app.intelligence.enums import FactStatus
    from app.intelligence.selectors import matter_intelligence
    from app.submissions.models import Submission

    facts = intelligence if intelligence is not None else matter_intelligence(matter, user)
    steps: list[ProcessStep] = []

    # `Alustatud`, for a Matter this system actually created.
    #
    # **Not for an imported one**, and not from `received_date` either.
    # `created_at` on a register-archive row is the moment the importer wrote it
    # into this database, which for a 2019 file is a fact about a migration;
    # `Saabus` is the day Koda received something, which is a fact about the
    # post and not about when the work started. There is no third field —
    # `created_at`, `updated_at`, `closed_at`, `received_date` and
    # `response_deadline` are every date `Matter` holds — so an imported Matter
    # gets no `Alustatud` at all. An honest gap is better than the strip's one
    # fabricated milestone standing leftmost on the page.
    #
    # `PROMOTED_LEGACY` is imported too — an archive row somebody activated —
    # and its `created_at` is the same import timestamp, so the test is the
    # exact origin rather than "not LEGACY_IMPORT"
    # (app/matters/enums.py `MatterOrigin`).
    if matter.origin == MatterOrigin.NATIVE:
        started = timezone.localtime(matter.created_at).date()
        steps.append(
            ProcessStep(
                label=STARTED_LABEL,
                display=format_estonian_date(started),
                detail="",
                sort_on=started,
                phase=PHASE_STARTED,
            )
        )

    # `Arvamuse tähtaeg` — one Matter-level column whenever the column holds a
    # date. This is the *formal* deadline: the day an answer is due to whoever
    # asked for it, stored on the Matter's own `response_deadline` and shown in
    # the header band under the same name.
    #
    # Drawn whether it is ahead of us or behind us, and at its own chronological
    # position either way. A deadline that has passed was a real procedural
    # point in this file's course; dropping it once the day went by would
    # rewrite the story, and pinning it rightmost would misdate it.
    #
    # **Not `MatterImportantDate`**, which is a watched expectation about what
    # somebody else may do, and **not `NextAction.target_date`**, which is one
    # lawyer's plan for their own next step. Neither becomes a procedural
    # milestone by having a date on it (docs/adr/0074 §12.4).
    #
    # Origin-independent, unlike `Alustatud`: an imported register row's
    # deadline is a date the source actually recorded, not a timestamp the
    # importer happened to write.
    if matter.response_deadline is not None:
        steps.append(
            ProcessStep(
                label=DEADLINE_LABEL,
                display=format_estonian_date(matter.response_deadline),
                detail="",
                sort_on=matter.response_deadline,
                phase=PHASE_DEADLINE,
            )
        )

    # Every genuine sent opinion, not just the latest. A supplementary opinion
    # months after the first is a second procedural act and draws its own
    # column; collapsing the two would lose the act this strip exists to show.
    #
    # A DRAFT is not a milestone and neither is an uploaded file: the canonical
    # record of a send is a SENT `Submission` carrying the date somebody
    # supplied (docs/adr/0061, post-QA R2-01).
    #
    # Ordered here rather than taken from the model's default `-sent_at`: the
    # strip reads earliest-first, and `-sent_at, -created_at` hands two sends
    # made on one day to the stable sort in reverse. `pk` closes the last tie,
    # so the geometry can never be decided by the row order the database
    # happened to return.
    sent_opinions = (
        Submission.objects.filter(matter=matter).visible_to(user).sent().order_by("sent_at", "pk")
    )
    for submission in sent_opinions:
        if submission.sent_at is None:
            continue
        sent_on = timezone.localtime(submission.sent_at).date()
        steps.append(
            ProcessStep(
                label=SENT_LABEL,
                display=format_estonian_date(sent_on),
                detail="",
                sort_on=sent_on,
                phase=PHASE_SENT,
            )
        )

    # `Jõustumine` — one column per canonical commencement that carries a date.
    #
    # **Several per Matter is the model's own point**, not an allowance to be
    # collapsed: one law routinely commences in stages, the main body on one
    # date and particular provisions eighteen months later, and
    # `MatterEffectiveDate` exists to hold exactly that. Nothing in the domain
    # names a *primary* commencement — no flag, no `is_primary`, no ordering
    # that elects one — so this module does not invent one. Each genuine record
    # draws its own column at its own date, and «mis jõustub» reads as that
    # column's `title`, which is what tells two of them apart.
    #
    # `KNOWN_DATE` is the only kind a constraint lets carry a date at all, so
    # testing `date_value` is testing the kind: «Jõustub üldises korras» and
    # «kuupäev täpsustamisel» are statements about what is *not* known and have
    # no position on a rail. `ACTIVE` is the existing vocabulary for a record
    # that still stands — a `CANCELLED` or `SUPERSEDED` commencement is what the
    # department believed at the time, it keeps its fact section and its
    # chronology row, and drawing it here would put two contradicting dates on
    # one strip.
    #
    # An approximate precision renders through `format_at_precision`, the same
    # reading the fact section and the chronology print: «II kvartal 2026» and
    # never a fabricated `01.04.2026`. It sorts on `date_value`, the first day
    # of the period, which is how every other surface orders these records.
    for record in facts.effective_dates:
        if record.date_value is None or record.status != FactStatus.ACTIVE:
            continue
        steps.append(
            ProcessStep(
                label=EFFECTIVE_LABEL,
                display=format_at_precision(record.date_value, record.date_precision),
                detail=record.description,
                sort_on=record.date_value,
                phase=PHASE_EFFECTIVE,
            )
        )

    # `Lõpetatud`, read off the Matter's **current** state rather than off the
    # audit stream. A reopened Matter keeps its `MATTER_CLOSED` event forever —
    # the history is true and stays readable in the chronology — so a strip
    # built by finding any historical closure would show a currently-open file
    # as finished. `reopen_matter` clears `closed_at`, `disposition` and
    # `closed_by`, and the `matters_closure_fields_consistent` constraint makes
    # the database refuse `is_open=True` together with a `closed_at`, so these
    # two columns cannot carry a stale closure between them.
    #
    # A closed ARCHIVE row may genuinely have no `closed_at` — the same
    # constraint exempts it, because an imported register row is not made to
    # invent a day it never had. It draws no closure column: a milestone with no
    # date is not a position in a process, and placing it at either end would
    # assert an ordering nobody recorded.
    #
    # Closing discharges none of the dated facts. A Matter closed before its
    # response deadline still shows that deadline, at the deadline's own place
    # and therefore to the right of `Lõpetatud`: the date was set and was never
    # withdrawn, and deciding here that a closure cancels an external deadline
    # would be inventing a discharge rule the domain has not recorded. That
    # decision is its own product seam (docs/adr/0074 §12.4).
    if not matter.is_open and matter.closed_at is not None:
        closed_on = timezone.localtime(matter.closed_at).date()
        steps.append(
            ProcessStep(
                label=CLOSED_LABEL,
                display=format_estonian_date(closed_on),
                detail=matter.get_disposition_display() if matter.disposition else "",
                sort_on=closed_on,
                phase=PHASE_CLOSED,
            )
        )

    # Earliest first. Two milestones recorded on one day fall back to their
    # place in the proceeding rather than to the order the sources happened to
    # be read in, and two of one kind on one day keep their source's own order,
    # which `list.sort` being stable preserves.
    steps.sort(key=lambda step: (step.sort_on, step.phase))
    return steps
