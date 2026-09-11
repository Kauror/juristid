"""Reading a Matter's two derived dates inside a shared row partial.

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


@register.filter
def last_activity(matter: Any) -> MatterActivityFact | None:
    """The Matter's latest known activity, or ``None`` when nothing is known.

    ``None`` is a real answer and the template renders it as an em dash. It is
    emphatically not the same as "the row was last written on", which is what
    this column used to show for two thousand imported records whose
    ``updated_at`` is the moment the 2026 cutover touched them.
    """
    return activity_of(matter)
