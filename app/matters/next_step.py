"""A Matter's next step, answered in one place (docs/adr/0120).

**An open `Järgmiseks` is the next step whenever there is one.** It is the
explicit instruction a person wrote, and nothing here second-guesses it: a
Matter carrying one is never asked the question below.

**Otherwise the nearest `Oluline tähtaeg` still ahead of us is.** A lawyer who
records «Ootan ministeeriumi tagasisidet, 1.10.2026» has said what this file is
waiting for; a page that went on reading «Järgmine samm on määramata» above it,
and a desk listing the file under «Järgmise tegevuseta», contradicted what they
had just written. The milestone is *surfaced*, never converted: no `NextAction`
is created, nothing is completed through it, and it keeps its own record and
its own wording («Oluline tähtaeg») wherever it is shown.

What makes a milestone eligible is the reading every other surface already
gives it:

* **active** — a cancelled or superseded expectation is history, not a plan;
* **not removed** — `MatterImportantDate.objects.visible_to` already drops a
  row taken off the file (docs/adr/0102), and every reader here goes through it;
* **its period has not ended** — the period's *last* day, the rule
  `MatterImportantDate.has_passed` states and `intelligence.selectors` splits
  «upcoming» from «past» by, so «oktoober 2026» stays ahead of us until
  31 October and a milestone dated today is still today's.

A past milestone was valid to record and stays in `Teema käik`; it is simply not
what happens *next*.

**Among several, the one due first wins, deterministically**: the period's
end, then the anchor, then the primary key — so a reader of the Matter page and
a row on `Minu asjad` pick the same record, and two stated the same way cannot
tie. It is the *end* first since docs/adr/0121 §1: ranked by the anchor, «2026»
(stored as 1 January) outranked a deadline due tomorrow, which is the stored
first day of a period standing in for a day nobody named — the reading §7 of
the same record removes from `Minu asjad`.

Three readers, one rule:

* `upcoming_milestone` — records already read (the Matter page has them from
  `matter_intelligence`, so choosing costs no query; a register row has them
  from `upcoming_milestone_prefetch`, one query for the page);
* `milestone_is_upcoming` / `milestone_order` — the same test and the same order
  over values, for `Minu asjad`'s work items, which carry no record;
* `without_next_step` — the SQL form, for every «järgmine tegevus puudub»
  population: the register filter, Ülevaade, Osakond, `Minu asjad` and
  Statistika all count Matters through it, so a figure and the list it opens
  cannot disagree about a file with an upcoming milestone.
"""

from __future__ import annotations

from collections.abc import Iterable
from datetime import date
from typing import Any

from django.db.models import Exists, OuterRef, Prefetch, QuerySet
from django.utils import timezone

from app.intelligence.enums import FactStatus
from app.intelligence.models import MatterImportantDate
from app.workflow.enums import ActionStatus
from app.workflow.models import NextAction


def milestone_is_upcoming(*, status: str, period_end: date | None, today: date) -> bool:
    """Whether a milestone in this state may be a Matter's next step today."""
    return status == FactStatus.ACTIVE and period_end is not None and period_end >= today


def milestone_order(date_value: date, period_end: date, pk: Any) -> tuple[date, date, str]:
    """Due first: the end of its period, then its anchor, then the key."""
    return (period_end, date_value, str(pk))


def upcoming_milestone(
    records: Iterable[MatterImportantDate], today: date | None = None
) -> MatterImportantDate | None:
    """The milestone that stands in for a missing `Järgmiseks`, or ``None``.

    ``records`` must already be scoped to the reader — the Matter page passes
    `matter_intelligence`'s upcoming list, which is `visible_to` and therefore
    free of removed rows. Cancelled records ride along there (they are history
    the page prints) and are skipped here.
    """
    day = today or timezone.localdate()
    candidates = [
        record
        for record in records
        if record.removed_at is None
        and milestone_is_upcoming(status=record.status, period_end=record.period_end, today=day)
    ]
    return min(
        candidates,
        key=lambda record: milestone_order(record.date_value, record.period_end, record.pk),
        default=None,
    )


def upcoming_milestones(user: Any, today: date) -> QuerySet[MatterImportantDate]:
    """`milestone_is_upcoming`, as a reader-scoped queryset."""
    return MatterImportantDate.objects.visible_to(user).filter(
        status=FactStatus.ACTIVE, period_end__gte=today
    )


#: Where `upcoming_milestone_prefetch` leaves each Matter's upcoming milestones.
UPCOMING_MILESTONES = "upcoming_important_dates"


def upcoming_milestone_prefetch(user: Any, today: date | None = None) -> Prefetch:
    """`upcoming_milestones`, attached to every Matter of a list in one query.

    For the register row, whose `Järgmiseks` cell must name the same milestone
    the Matter page does when no step is open — and must not say «Järgmine samm
    puudub» about a file `without_next_step` keeps out of `?tegevus=puudub`
    (RULE-01). Reader-scoped through `upcoming_milestones`, so a milestone
    restricted below its visible Matter is never attached, and therefore never
    named, dated or counted on the row.
    """
    day = today or timezone.localdate()
    return Prefetch(
        "important_dates", queryset=upcoming_milestones(user, day), to_attr=UPCOMING_MILESTONES
    )


def prefetched_upcoming_milestone(matter: Any) -> MatterImportantDate | None:
    """`upcoming_milestone` over what `upcoming_milestone_prefetch` attached.

    **The prefetch is required, not preferred**, like the other derived values a
    register row reads: falling back to a query here would be one per row, and
    a list that forgot the prefetch would quietly pay it on every page.
    """
    records = getattr(matter, UPCOMING_MILESTONES, None)
    if records is None:
        raise ValueError(
            "prefetched_upcoming_milestone needs next_step.upcoming_milestone_prefetch "
            "on the queryset; selectors.matter_list_queryset applies it."
        )
    return upcoming_milestone(records)


def without_next_step(
    queryset: QuerySet[Any], user: Any, today: date | None = None
) -> QuerySet[Any]:
    """The Matters in ``queryset`` that have no next step for this reader.

    Neither an open `NextAction` the reader may see — dated or not, since
    docs/adr/0106 an undated step is a step — nor an upcoming `Oluline tähtaeg`
    they may see, nor work already scheduled in the Matter's own lifecycle
    (docs/adr/0144 §3): a `PLANNED` action, or a `Kaasamine` still waiting for
    feedback. «Ootame tagasisidet 15.10» is the next step of a file whose round
    is open, and reporting that file as having none was the page contradicting
    itself. Every probe is `visible_to`: a record restricted below its Matter
    must not decide, through a count, whether a visible Matter is listed
    (AUTH-003).

    A current `Arvamuse tähtaeg` is not a step: it is what the next step is
    *for*, and it stays in the header.
    """
    from app.matters.models import MatterEngagement

    day = today or timezone.localdate()
    open_action = NextAction.objects.visible_to(user).filter(
        matter=OuterRef("pk"), status__in=(ActionStatus.OPEN, ActionStatus.PLANNED)
    )
    milestone = upcoming_milestones(user, day).filter(matter=OuterRef("pk"))
    waiting = MatterEngagement.objects.visible_to(user).filter(
        matter=OuterRef("pk"), lifecycle_tracked=True, feedback_closed_at__isnull=True
    )
    return queryset.annotate(
        next_step_action=Exists(open_action),
        next_step_milestone=Exists(milestone),
        next_step_waiting=Exists(waiting),
    ).filter(next_step_action=False, next_step_milestone=False, next_step_waiting=False)
