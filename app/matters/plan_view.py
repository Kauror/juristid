"""`Soovitatud järgmisena` as the Teema page reads it — one read, decided in Python.

`Tööplaan` is not drawn any more (docs/adr/0141). What the page still shows is
one suggestion under `PRAEGUNE TEGEVUS`, and only while **this reader** has no
current step: the first step of the background sequence that is still ahead —
suggested or planned, not completed and not dismissed. The template must not
query and must not decide.

**Current-ness is the canonical action's, never the step's.** A step is current
exactly when the open `NextAction` names it, so the suggestion and
`PRAEGUNE TEGEVUS` cannot disagree (docs/adr/0133 §3).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from app.matters.enums import RecordMode
from app.workflow.models import MatterPlanStep
from app.workflow.plan import plan_revision, plan_steps_of


@dataclass(frozen=True)
class Recommendation:
    """The one suggested next step, and the revision `×` must name."""

    step: MatterPlanStep
    revision: str


def recommendation_for(matter: Any, current_action: Any) -> Recommendation | None:
    """The suggestion for this reader's page, or ``None``.

    ``None`` while a current step is visible — work already in hand is never
    shadowed by a suggestion — on a closed Matter, on an archive register row
    and when nothing is left ahead. Otherwise the first open step in order.
    """
    if current_action is not None or not matter.is_open:
        return None
    if matter.record_mode != RecordMode.FULL:
        return None
    steps = plan_steps_of(matter)
    # No reader-blind look at open actions here: a step whose open action is
    # restricted below this reader reads as any other step ahead, so the
    # suggestion cannot be used to learn about an action the reader may not
    # see (docs/adr/0133 §3). Starting it then refuses under the lock.
    ahead = next((step for step in steps if step.is_open), None)
    if ahead is None:
        return None
    return Recommendation(step=ahead, revision=plan_revision(steps))
