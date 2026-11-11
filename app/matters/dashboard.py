"""The open-work populations Osakond and Minu asjad count from.

What remains of the retired *Ülevaade* page's module: the three populations
other surfaces still build on (`app.matters.department_dashboard`,
`app.matters.my_work`).

**Authorization before arithmetic.** Every queryset starts from
``Matter.objects.visible_to(user)`` and is filtered, grouped, counted and sliced
only after that. A restricted Matter the reader may not see contributes nothing
to a total. Hiding it at render time would leave it inside the counts, which is
the disclosure that is easy to miss because nothing on screen looks wrong.
"""

from __future__ import annotations

from typing import Any

from django.db.models import QuerySet

from app.matters.enums import RecordMode
from app.matters.models import Matter
from app.matters.next_step import without_next_step
from app.matters.register_filters import OPINION_DRAFTING, filter_by_opinion_state


def active_matters(user: Any) -> QuerySet[Matter]:
    """Open FULL Matters the reader may see.

    ARCHIVE rows are excluded throughout the dashboard. They are historical
    evidence rather than live work, and counting a decade of imported register
    rows as "active" would make every number meaningless.
    """
    return Matter.objects.visible_to(user).filter(is_open=True, record_mode=RecordMode.FULL)


def without_next_action(user: Any) -> QuerySet[Matter]:
    """Open FULL Matters carrying no instruction *this reader can see*.

    Scoped, because a `NextAction` can be restricted below the Matter it hangs
    off. Read unscoped, a Matter dropped out of Osakond's *järgmine tegevus
    puudub* column the moment somebody filed a restricted step on it, which is
    a restricted record announcing itself through a count (AUTH-003).

    The register's own rule, not a copy of it: an upcoming `Oluline tähtaeg` is
    this Matter's next step when no `Järgmiseks` is set, and the column this
    feeds links to `?tegevus=puudub` (`app.matters.next_step`, docs/adr/0120).
    """
    return without_next_step(active_matters(user), user)


def drafting_matters(user: Any) -> QuerySet[Matter]:
    """Current work whose opinion has no outcome yet — Lovable's «Arvamusi koostamisel».

    Two halves, from different places on purpose.

    The lifecycle half is ``active_matters`` — open FULL records this reader may
    see. A Matter closed today drops out on the next page load.

    The opinion half is the register's own ``?arvamus=koostamisel``
    (:func:`app.matters.register_filters.opinion_state_q`), applied rather than
    restated, so Osakond's column, Minu asjad's figures and the list a lawyer
    lands on are one query. Since docs/adr/0149's amendment of 2026-11-11 it is
    Lovable's definition: no opinion sent and no decision not to send one — read
    from the outcome, never from whether a «Koostan arvamuse» step exists.

    Not a count of Submissions: a Matter is one row however many drafts it has.
    """
    return filter_by_opinion_state(active_matters(user), user, OPINION_DRAFTING)
