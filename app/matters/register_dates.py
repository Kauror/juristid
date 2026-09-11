"""What the register's «Kuupäev» column means, as one rule read two ways.

Why this module exists
----------------------
The register row has always chosen its date in the template: the open
``Järgmiseks`` step's own date when the visible step has one, the Matter's
``Arvamuse tähtaeg`` otherwise, and an em dash when neither is known. That was
fine while the column was only ever read.

Making the heading sortable turns the same rule into a second question — *which
row comes first* — and a second question answered in a second place is how a
table ends up displaying 12.09, 15.09, 13.09 while insisting it is sorted by
Kuupäev. The backend would have been sorting `response_deadline`; the rows would
have been showing an action date; both would have been correct about the field
they read, and the page would have been wrong.

So the choice lives here once, and is read two ways:

* :func:`register_date` is the **row's** reading. It returns everything the
  cell renders — the meaning, the value at the precision it was recorded to,
  and the three states the styling depends on — from the objects the register
  has already loaded, with no query of its own.
* :func:`annotate_display_date` is the **database's** reading of the same rule,
  as one correlated subquery the ordering can be taken on.

``tests/test_register_interactive_columns.py`` holds the two against each other
on the cases that distinguish them: a dated action, an action with no date, no
action at all, and a Matter with neither.

This is the shape :mod:`app.matters.activity` already uses for *Viimane tegevus*
— annotations on the queryset, a Python reader that reads only what is already
loaded — and it is used here for the same reason.

Authorization
-------------
**Both readings see the same action, and it is the action this reader may
open.** ``open_actions`` is the reader-scoped prefetch
(``selectors.open_action_prefetch``); the subquery below is
``NextAction.objects.visible_to(user)``. A `Järgmiseks` restricted below its
Matter therefore contributes to neither the displayed date nor the sort key, so
it cannot move a row up the page and announce that it exists to somebody who
may not read it (AUTH-003, and the same rule ``filter_by_next_action`` keeps).

There is no schema behind any of this. The date is an interpretation of two
columns that already exist, in the sense ``CurrentRegisterState`` is an
interpretation of source rows.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from typing import Any

from django.db.models import DateField, OuterRef, QuerySet, Subquery
from django.db.models.functions import Coalesce

from app.core.dates import format_estonian_date
from app.matters.models import Matter
from app.workflow.enums import ActionStatus, DateSemantics
from app.workflow.models import NextAction

#: The annotation :func:`annotate_display_date` writes, and the name
#: ``?jarjestus=kuupaev_asc`` orders on. Prefixed, because it lands on
#: ``Matter`` beside real field names and a collision would be silent — the same
#: precaution :data:`app.matters.activity.ANNOTATIONS` takes.
DISPLAY_DATE = "register_display_date"

#: What the fallback date is called in the cell. The Matter's own column, and
#: the register's own word for it — the same one the Täpsem otsing panel's
#: `?tahtaeg_alates=` pair narrows.
RESPONSE_DEADLINE_LABEL = "Arvamuse tähtaeg"


@dataclass(frozen=True)
class RegisterDate:
    """One register row's Kuupäev cell, decided.

    Read-only and derived. ``value`` is the day the row sorts on; ``display`` is
    how that day reads at the precision it was actually recorded to, which is
    not the same string — an ``EXPECTED_AROUND`` step recorded as *III kvartal
    2026* sorts on a day and prints a quarter (``NextAction.display_date``).
    """

    #: The effective date, exactly as :func:`annotate_display_date` computes it.
    value: date
    #: What kind of date this is, in words. Never blank: a bare "15.08" does not
    #: say whether missing it matters.
    meaning: str
    #: The value at its recorded precision.
    display: str
    #: Whether the product may call this late. Only a DO with a DEADLINE can be
    #: (`NextAction.is_overdue`); a review date that has come round is due for a
    #: look, not missed.
    is_overdue: bool
    #: Whether it is a commitment rather than an expectation.
    is_deadline: bool
    #: Whether the recorded precision is coarser than a day.
    is_approximate: bool


def _open_action(matter: Matter) -> Any:
    """The one open action this reader may see, from the register's prefetch.

    Raises rather than querying when the prefetch is absent, which is the rule
    :func:`app.matters.activity.activity_of` already keeps and for a sharper
    reason here: without it every row would quietly fall through to
    ``response_deadline`` and disagree with an ordering that did not.
    """
    actions = getattr(matter, "open_actions", None)
    if actions is None:
        raise ValueError(
            "register_date needs selectors.open_action_prefetch on the queryset; "
            "open_actions is missing."
        )
    return actions[0] if actions else None


def register_date(matter: Matter, today: date | None = None) -> RegisterDate | None:
    """The Kuupäev cell for one row, or ``None`` when no date is known.

    ``None`` is a real answer and renders an em dash. Neither the created
    timestamp nor the import date is offered in its place: both would be a
    statement about the row rather than about the work, which is the mistake
    :mod:`app.matters.activity` exists to correct one column to the right.
    """
    action = _open_action(matter)
    if action is not None and action.target_date is not None:
        return RegisterDate(
            value=action.target_date,
            meaning=action.date_label,
            display=action.display_date,
            is_overdue=action.is_overdue(today),
            is_deadline=action.date_semantics == DateSemantics.DEADLINE,
            is_approximate=action.is_approximate,
        )
    if matter.response_deadline is not None:
        return RegisterDate(
            value=matter.response_deadline,
            meaning=RESPONSE_DEADLINE_LABEL,
            display=format_estonian_date(matter.response_deadline),
            # An `Arvamuse tähtaeg` on the row is never called late here. The
            # product has one definition of overdue for it — no SENT Submission,
            # no VÄLJA mark, no open Järgmiseks — and the register row does not
            # read any of the three, so it states the deadline and says nothing
            # about whether it was met (ADR 0050, ADR 0059, UX-010).
            is_overdue=False,
            is_deadline=True,
            is_approximate=False,
        )
    return None


def open_action_target_date(user: Any) -> Subquery:
    """The visible open step's own date, as one correlated subquery.

    ``order_by()`` clears ``NextAction.Meta.ordering``: a Matter has at most one
    open action by database constraint, so an ordering column here sorts a set
    of one while dragging itself into the query.
    """
    return Subquery(
        NextAction.objects.visible_to(user)
        .filter(status=ActionStatus.OPEN, matter=OuterRef("pk"))
        .order_by()
        .values("target_date")[:1],
        output_field=DateField(),
    )


def annotate_display_date(queryset: QuerySet[Matter], user: Any) -> QuerySet[Matter]:
    """Attach the date the row displays, so the database can order on it.

    ``Coalesce`` is the rule in :func:`register_date` stated in SQL: the open
    step's date when there is one, ``response_deadline`` otherwise, NULL when
    neither is known. An open step with **no** date falls through to the
    deadline in both readings, which is the case a naive
    ``Case(When(has_action=True, ...))`` would get wrong.

    Applied only by a sort that needs it. The register's ordinary page pays for
    no subquery it does not read (app/matters/views.py `_ordered`).
    """
    return queryset.annotate(
        **{
            DISPLAY_DATE: Coalesce(
                open_action_target_date(user),
                "response_deadline",
                output_field=DateField(),
            )
        }
    )
