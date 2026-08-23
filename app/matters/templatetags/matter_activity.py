"""Reading a Matter's derived last-activity fact inside a shared row partial.

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

register = template.Library()


@register.filter
def last_activity(matter: Any) -> MatterActivityFact | None:
    """The Matter's latest known activity, or ``None`` when nothing is known.

    ``None`` is a real answer and the template renders it as an em dash. It is
    emphatically not the same as "the row was last written on", which is what
    this column used to show for two thousand imported records whose
    ``updated_at`` is the moment the 2026 cutover touched them.
    """
    return activity_of(matter)
