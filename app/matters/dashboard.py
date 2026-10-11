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
    """Current work with an opinion still being written.

    ``Arvamus koostamisel``, as Matters. Two halves, from different places on
    purpose.

    The lifecycle half is ``active_matters`` — open FULL records this reader may
    see. Leading with it is what makes the number self-correcting: a lawyer who
    closes a Matter today drops out of this count on the next page load, without
    anybody re-running the cutover.

    The opinion half is the register's own ``?arvamus=koostamisel``, which is
    :func:`app.matters.register_filters.opinion_state_q`: a DRAFT Submission
    this reader may see, or a CURRENT register row with a blank ``VÄLJA`` that no
    readable SENT Submission has since overtaken (ENG-019). It is applied rather
    than restated here, which is what lets Osakond's column, this function and
    the list a lawyer lands on be the same query: a second copy of the
    condition, written next door, is how a count and its drill-through start
    disagreeing.

    Not a count of Submissions — a Matter with two drafts is one file being
    worked on — and not «open without a sent opinion», which would count every
    file somebody is merely waiting on or watching.
    """
    return filter_by_opinion_state(active_matters(user), user, OPINION_DRAFTING)
