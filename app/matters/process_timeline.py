"""`Teema käik` — the horizontal milestone strip above the chronology.

Where the matter stands, in one line, before anybody scrolls anything. The
chronology answers *what has happened*; this answers *how far along is it*, and
the two are different questions a lawyer asks in that order
(TEEMA_TARGET_SPEC §D).

**Derived, never stored.** There is no `ProcessTimeline` table and no
`Milestone` model, and adding one to draw a strip would be inventing a second
place for facts the domain already holds — a place that could then disagree with
the records it was built from. Every step below is read off a canonical record
that exists for its own reasons: the Matter's creation, a `MatterEngagement`, a
sent `Submission`, the current `StageVocabulary`, a `MatterImportantDate`, a
`MatterEffectiveDate` (docs/adr/0074 §12).

**Only what applies.** `Loodud · Küsitlus · Arvamus välja · Valitsuses ·
I lugemine · Jõustub` is what the design's own demonstration Matter happened to
have, not a six-stage rail every Matter is measured against. A Matter with two
milestones draws two equal columns; one draws one dot and no connector; none
draws no strip at all. Greying out stages a matter never reaches would turn a
progress indicator into a checklist of things nobody was going to do.

**Scoped before it is derived, not filtered afterwards.** Every source below is
read through its own `visible_to`, so a restricted child cannot change the number
of columns, the connector count, the spacing, the ordering, the current/future
classification, a label or a date. Deriving first and hiding afterwards would
leave exactly those observable traces, which is the leak class the 2026-09 audit
closed and this strip must not reopen (AUTH-003, docs/adr/0074 §13).
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from typing import Any

from django.utils import timezone

from app.audit.enums import ChangeEventType
from app.audit.models import ChangeEvent
from app.audit.visibility import scope_change_events
from app.core.dates import format_estonian_date
from app.matters.enums import MatterOrigin
from app.matters.models import Matter, MatterEngagement
from app.workflow.dates import format_at_precision

#: `praegu` on the current step, `N p` on a future one that has a deadline.
#: Both are in the target's locked copy; neither is a sentence this module
#: composes (TEEMA_TARGET_SPEC, EXACT COPY §Zone C).
CURRENT_SUFFIX = "praegu"
DAYS_UNIT = "p"

#: How far ahead a future milestone still earns its day count. The same sixty
#: days the header deadline uses, so one Matter never says «21 p» in the metaline
#: and nothing on the strip for the very same date (app/matters/selectors.py).
DAYS_HORIZON = 60

STATE_DONE = "done"
STATE_CURRENT = "current"
STATE_TODO = "todo"

#: The step a sent opinion draws. The one label on this strip that names an act
#: rather than a stored title, because `Submission` has no short name of its own
#: and «Koja arvamus pakendiseaduse kohta, 2026-06-02» is not a column heading.
SENT_LABEL = "Arvamus välja"
CREATED_LABEL = "Loodud"
EFFECTIVE_LABEL = "Jõustub"

#: A column is about 150px at 1440 and much less at 420. A title longer than this
#: wraps to three lines and pushes its neighbours' dates out of alignment; the
#: full wording is one row down in the chronology, which is where somebody
#: reading rather than scanning already is.
LABEL_LIMIT = 28


def _shorten(text: str) -> str:
    words = " ".join((text or "").split())
    if len(words) <= LABEL_LIMIT:
        return words
    return words[: LABEL_LIMIT - 1].rstrip() + "…"


@dataclass(frozen=True)
class ProcessStep:
    """One column of the strip.

    ``sort_on`` is how the strip is ordered and is never rendered. ``display``
    is what the reader sees and may be empty — a current stage whose transition
    date nobody recorded is shown without one rather than given today's.
    """

    label: str
    display: str
    suffix: str
    state: str
    sort_on: date

    @property
    def is_current(self) -> bool:
        return self.state == STATE_CURRENT

    @property
    def is_todo(self) -> bool:
        return self.state == STATE_TODO

    @property
    def date_line(self) -> str:
        """``1.7.2026 · praegu``, ``30.9.2026 · 21 p``, ``12.5.2026``, or ``''``."""
        if self.display and self.suffix:
            return f"{self.display} · {self.suffix}"
        return self.display or self.suffix


def _stage_changed_on(matter: Matter, user: Any) -> date | None:
    """When this Matter reached the stage it is in now, if anybody recorded it.

    Read from the scoped change-event stream rather than from a column, because
    there is no column: `Matter.stage` holds where the file is and not when it
    got there. A Matter whose stage was set at creation, or corrected by an
    importer that wrote no event, genuinely has no transition date — and the
    strip shows the stage with no day rather than borrowing one
    (TEEMA_TARGET_SPEC §D).
    """
    event = (
        scope_change_events(ChangeEvent.objects.filter(matter=matter), user)
        .filter(event_type=ChangeEventType.MATTER_STAGE_CHANGED)
        .order_by("-occurred_at", "-created_at", "-id")
        .first()
    )
    return timezone.localtime(event.occurred_at).date() if event is not None else None


def _future_suffix(when: date, today: date) -> str:
    ahead = (when - today).days
    if 0 < ahead <= DAYS_HORIZON:
        return f"{ahead} {DAYS_UNIT}"
    return ""


def process_steps(
    *,
    matter: Matter,
    user: Any,
    intelligence: Any = None,
    today: date | None = None,
) -> list[ProcessStep]:
    """The strip for one Matter, oldest first, or an empty list.

    ``intelligence`` is the already-scoped `MatterIntelligence` the page built
    for its own reasons. Passed in rather than re-queried so the strip and the
    chronology cannot ask two differently scoped questions about one Matter —
    the same rule `matter_intelligence` itself was written for.
    """
    from app.intelligence.selectors import matter_intelligence
    from app.submissions.models import Submission

    day = today or timezone.localdate()
    facts = intelligence if intelligence is not None else matter_intelligence(matter, user, day)
    steps: list[ProcessStep] = []

    # `Loodud`, for a Matter this system actually created.
    #
    # **Not for an imported one.** `created_at` on a register-archive row is the
    # moment the importer wrote it into this database, which for a 2019 file is
    # a fact about a migration and not about the proceeding. Printing
    # «Loodud 3.9.2026» as the first milestone of a seven-year-old matter would
    # be the strip's one fabricated date, and it would be the leftmost thing on
    # the page (app/matters/enums.py `MatterOrigin`).
    #
    # It is also why an imported Matter with no stage, no engagement, no opinion
    # and no dated fact draws no strip at all, rather than a single lonely dot
    # standing for its own import.
    if matter.origin == MatterOrigin.NATIVE:
        created = timezone.localtime(matter.created_at).date()
        steps.append(
            ProcessStep(
                label=CREATED_LABEL,
                display=format_estonian_date(created),
                suffix="",
                state=STATE_DONE,
                sort_on=created,
            )
        )

    # Engagements. The kind is the column — `Küsitlus`, `Koosolek` — because that
    # is what a reader scanning the strip is placing in the process; who was
    # engaged is the chronology row underneath.
    for engagement in MatterEngagement.objects.filter(matter=matter).visible_to(user):
        if engagement.occurred_on is None:
            # An undated engagement is a real record and a fact about the file.
            # It is not a position in a process, and putting it at either end of
            # the strip would assert an ordering nobody recorded. It reads in the
            # chronology, where an undated row is honest.
            continue
        steps.append(
            ProcessStep(
                label=_shorten(engagement.get_kind_display()),
                display=format_estonian_date(engagement.occurred_on),
                suffix="",
                state=STATE_DONE,
                sort_on=engagement.occurred_on,
            )
        )

    for submission in Submission.objects.filter(matter=matter).visible_to(user).sent():
        if submission.sent_at is None:
            continue
        sent_on = timezone.localtime(submission.sent_at).date()
        steps.append(
            ProcessStep(
                label=SENT_LABEL,
                display=format_estonian_date(sent_on),
                suffix="",
                state=STATE_DONE,
                sort_on=sent_on,
            )
        )

    # Where the file is now. Exactly one step carries `is-current`, and it is
    # this one — the accent connector stops at its dot, which is the whole
    # grammar of the strip.
    if matter.stage is not None:
        changed = _stage_changed_on(matter, user)
        steps.append(
            ProcessStep(
                label=_shorten(matter.stage.label_et),
                display=format_estonian_date(changed) if changed else "",
                suffix=CURRENT_SUFFIX,
                state=STATE_CURRENT,
                # Undated, it sorts to today: after everything that has happened
                # and before everything that has not, which is what «current»
                # means. Nothing renders this value.
                sort_on=changed or day,
            )
        )

    for record in [*facts.past_dates, *facts.upcoming_dates]:
        if record.is_cancelled:
            # A called-off expectation is history, not a stage the file is
            # heading for. It stays readable where cancelled facts read.
            continue
        passed = record.has_passed(day)
        steps.append(
            ProcessStep(
                label=_shorten(record.title),
                display=record.display_date,
                suffix="" if passed else _future_suffix(record.period_end, day),
                state=STATE_DONE if passed else STATE_TODO,
                sort_on=record.date_value,
            )
        )

    for record in facts.effective_dates:
        if record.date_value is None or record.is_cancelled:
            continue
        passed = record.date_value < day
        steps.append(
            ProcessStep(
                label=_shorten(record.description) or EFFECTIVE_LABEL,
                display=format_at_precision(record.date_value, record.date_precision),
                suffix="" if passed else _future_suffix(record.date_value, day),
                state=STATE_DONE if passed else STATE_TODO,
                sort_on=record.date_value,
            )
        )

    # Oldest first, and stable: two milestones on one day keep the order the
    # sources were read in, so the strip does not reshuffle between two renders
    # of the same unchanged Matter.
    steps.sort(key=lambda step: step.sort_on)
    return steps
