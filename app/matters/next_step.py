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

**Among several, the earliest wins, deterministically**: the anchor, then the
period's end, then the primary key — the model's own ordering, so a reader of
the Matter page, a row on `Minu asjad` and the register's `?tegevus=puudub` pick
the same record. Two stated the same way cannot tie.

Three readers, one rule:

* `upcoming_milestone` — records already read (the Matter page has them from
  `matter_intelligence`, so choosing costs no query);
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

from django.db.models import Exists, OuterRef, QuerySet
from django.utils import timezone

from app.intelligence.enums import FactStatus
from app.intelligence.models import MatterImportantDate
from app.workflow.enums import ActionStatus
from app.workflow.models import NextAction


def milestone_is_upcoming(*, status: str, period_end: date | None, today: date) -> bool:
    """Whether a milestone in this state may be a Matter's next step today."""
    return status == FactStatus.ACTIVE and period_end is not None and period_end >= today


def milestone_order(date_value: date, period_end: date, pk: Any) -> tuple[date, date, str]:
    """Earliest first: anchor, then the end of its period, then the key."""
    return (date_value, period_end, str(pk))


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


def without_next_step(
    queryset: QuerySet[Any], user: Any, today: date | None = None
) -> QuerySet[Any]:
    """The Matters in ``queryset`` that have no next step for this reader.

    Neither an open `NextAction` the reader may see — dated or not, since
    docs/adr/0106 an undated step is a step — nor an upcoming `Oluline tähtaeg`
    they may see. Both probes are `visible_to`: a record restricted below its
    Matter must not decide, through a count, whether a visible Matter is listed
    (AUTH-003).
    """
    day = today or timezone.localdate()
    open_action = NextAction.objects.visible_to(user).filter(
        matter=OuterRef("pk"), status=ActionStatus.OPEN
    )
    milestone = upcoming_milestones(user, day).filter(matter=OuterRef("pk"))
    return queryset.annotate(
        next_step_action=Exists(open_action),
        next_step_milestone=Exists(milestone),
    ).filter(next_step_action=False, next_step_milestone=False)
