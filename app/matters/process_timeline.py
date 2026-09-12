"""`Teema käik` — the horizontal milestone strip above the chronology.

**A sparse procedural story, not a digest of every dated fact.** The chronology
answers *what has happened* and the header answers *where the file stands now*;
this strip answers the third question, which is *which major procedural acts has
this file been through*. Three of them exist today:

    Alustatud · Koja arvamus · Lõpetatud

and a Matter draws only the ones it really has. That is the whole vocabulary.
An earlier version of this module also projected the current `StageVocabulary`,
every `MatterEngagement`, every `MatterImportantDate` and every
`MatterEffectiveDate` — six sources, which made the strip a second, shorter copy
of the chronology with the header's `Hetkeseis` pinned in the middle of it. Each
of those records is still canonical and still reads where it belongs; none of
them is a major procedural act (docs/adr/0074 §12).

**Derived, never stored.** There is no `ProcessTimeline` table and no
`Milestone` model, and adding one to draw a strip would be inventing a second
place for facts the domain already holds — a place that could then disagree with
the records it was built from. Every step below is read off a canonical record
that exists for its own reasons: the Matter's own creation, a sent `Submission`,
the Matter's own closure.

**Historical only.** Every milestone is an act that has already happened, so the
strip has no `praegu`, no future column and no countdown. Where the file stands
*now* is `Hetkeseis` in the header, which is the one place it is stated — a
closed Matter whose last recorded stage was `Riigikogus` must never read
`Riigikogus · praegu` on a strip. The connector already stops at the rightmost
dot because `:last-child` draws none, so the strip still ends somewhere visible
without any milestone claiming to be the current one.

**Scoped before it is derived, not filtered afterwards.** The sent opinions are
read through their own `visible_to`, so a restricted `Submission` cannot change
the number of columns, the connector count, the spacing, the ordering, a label
or a date. Deriving first and hiding afterwards would leave exactly those
observable traces, which is the leak class the 2026-09 audit closed and this
strip must not reopen (AUTH-003, docs/adr/0074 §13).
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from typing import Any

from django.utils import timezone

from app.core.dates import format_estonian_date
from app.matters.enums import MatterOrigin
from app.matters.models import Matter

#: The three labels this strip can draw. All three name an *act*, not a stored
#: title and not an outcome.
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

#: `Lõpetatud` and not `Menetlus lõppes`, `Jõustus` or `Loobuti`. Those are the
#: `Disposition` — *why* it ended — and a strip whose milestone labels varied
#: with the outcome would stop being a comparable rail. The disposition is
#: secondary information and reads as the step's `title`.
CLOSED_LABEL = "Lõpetatud"


@dataclass(frozen=True)
class ProcessStep:
    """One column of the strip.

    ``sort_on`` is how the strip is ordered and is never rendered. ``detail`` is
    secondary information — today only the closure's `Disposition` — and reads
    as the column's `title` rather than as a second visible line, because
    «Vastus esitatud ja järeltegevus tehtud» under a 150 px column is a
    three-line wrap that pushes its neighbours' dates out of alignment.
    """

    label: str
    display: str
    detail: str
    sort_on: date

    @property
    def date_line(self) -> str:
        """``12.5.2026`` — the day the act happened, and nothing appended.

        No `praegu` and no `N p`. Every milestone on this strip is historical,
        so there is neither a current step to mark nor a future one to count
        down to.
        """
        return self.display


def process_steps(*, matter: Matter, user: Any) -> list[ProcessStep]:
    """The strip for one Matter, oldest first, or an empty list."""
    from app.submissions.models import Submission

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
            )
        )

    # Every genuine sent opinion, not just the latest. A supplementary opinion
    # months after the first is a second procedural act and draws its own
    # column; collapsing the two would lose the act this strip exists to show.
    #
    # A DRAFT is not a milestone and neither is an uploaded file: the canonical
    # record of a send is a SENT `Submission` carrying the date somebody
    # supplied (docs/adr/0061, post-QA R2-01).
    for submission in Submission.objects.filter(matter=matter).visible_to(user).sent():
        if submission.sent_at is None:
            continue
        sent_on = timezone.localtime(submission.sent_at).date()
        steps.append(
            ProcessStep(
                label=SENT_LABEL,
                display=format_estonian_date(sent_on),
                detail="",
                sort_on=sent_on,
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
    if not matter.is_open and matter.closed_at is not None:
        closed_on = timezone.localtime(matter.closed_at).date()
        steps.append(
            ProcessStep(
                label=CLOSED_LABEL,
                display=format_estonian_date(closed_on),
                detail=matter.get_disposition_display() if matter.disposition else "",
                sort_on=closed_on,
            )
        )

    # Oldest first, and stable: an opinion sent on the day the file closed keeps
    # the procedural order it was read in, so the strip does not reshuffle
    # between two renders of the same unchanged Matter.
    steps.sort(key=lambda step: step.sort_on)
    return steps
