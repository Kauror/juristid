"""Reading a Matter's derived dates inside a shared row partial.

``matters/partials/matter_table.html`` is rendered by four surfaces, all of
which come through ``selectors.matter_list_queryset`` and therefore carry the
annotations ``app.matters.activity`` needs. Django's template language cannot
call a function with an argument, hence this filter, which does that and
nothing else.

Deliberately **not** a property on ``Matter``. ``activity_of`` refuses to guess
when the annotations are absent, and a property would turn that refusal into an
exception on every unannotated Matter rendered anywhere else in the product —
or, worse, tempt somebody into making it fall back to six queries per row.
Keeping it a filter keeps the requirement where it belongs: on the queryset
(ADR 0026).
"""

from __future__ import annotations

from typing import Any

from django import template

from app.matters.activity import MatterActivityFact, activity_of
from app.matters.register_dates import RegisterDate
from app.matters.register_dates import register_date as _register_date
from app.matters.work_items import (
    DISCHARGED,
    ResponseObligation,
    secondary_response_obligation,
)

register = template.Library()


@register.filter(name="register_date")
def register_date(matter: Any) -> RegisterDate | None:
    """What the row's **Kuupäev** cell says, decided once.

    Here for the same reason ``last_activity`` is: the rule needs the objects
    the queryset already loaded, the template cannot call a function, and a
    property on ``Matter`` would either query per row or answer without the
    reader-scoped prefetch it depends on.

    The column is sortable, so the same rule is also asked of the database by
    ``register_dates.annotate_display_date``. Two readings, one rule — see that
    module for why the alternative is a table that displays 12.09, 15.09, 13.09
    and calls itself sorted.
    """
    return _register_date(matter)


@register.filter(name="secondary_obligation")
def secondary_obligation(matter: Any, user: Any) -> ResponseObligation | None:
    """The official `Arvamuse tähtaeg` the Kuupäev cell is not already showing.

    The row's primary date is the *plan* — an open `Järgmiseks` outranks the
    response deadline, and a file under an instruction is being worked on rather
    than missed (docs/adr/0050). That reading says nothing about whether Koda has
    answered, which is a different question about the same date, so an
    outstanding obligation is stated under the plan rather than in place of it.

    ``primary_date`` comes from ``register_date`` — the same function the cell
    above renders and the same rule the ORDER BY reads — so when the cell has
    already fallen back to `Arvamuse tähtaeg` the secondary line is silent
    rather than printing the same day twice.

    **The annotation is required, not preferred.** ``secondary_response_
    obligation`` would otherwise answer through a query per row, which on a
    fifty-row register is fifty ``Exists`` pairs for a fact the page already
    bought once in ``selectors.matter_list_queryset``. Refusing is the rule
    ``register_date`` keeps one cell to the left, and for the sharper reason: a
    missing prefetch there raises, while a missing annotation here would only
    ever be *slow* — the failure nobody notices until the register is large
    (Agent-G brief 63, ADR 0026).
    """
    if getattr(matter, DISCHARGED, None) is None:
        raise ValueError(
            "secondary_obligation needs work_items.annotate_response_obligation on "
            "the queryset; selectors.matter_list_queryset applies it."
        )
    shown = _register_date(matter)
    return secondary_response_obligation(
        matter,
        user,
        primary_date=shown.value if shown is not None else None,
        # `RegisterDate` already knows whether the cell is showing a period
        # rather than a day, and the cell is styled on that same answer. An
        # approximate cell's anchor is not what the reader sees, so matching it
        # against the deadline would silence an obligation on the strength of a
        # number the row never printed (docs/adr/0079 §12).
        primary_is_approximate=shown is not None and shown.is_approximate,
    )


@register.filter
def last_activity(matter: Any) -> MatterActivityFact | None:
    """The Matter's latest known activity, or ``None`` when nothing is known.

    ``None`` is a real answer and the template renders it as an em dash. It is
    emphatically not the same as "the row was last written on", which is what
    this column used to show for two thousand imported records whose
    ``updated_at`` is the moment the 2026 cutover touched them.
    """
    return activity_of(matter)
