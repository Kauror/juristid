"""`Tööplaan` as the Teema page reads it — one read, decided in Python.

The template must not query and must not decide. Whether a step is current,
whether it may be started, moved or skipped, and which step is the next
suggestion are answered here once, from the plan and from the open step **this
reader may see** (`selectors.current_action_of`), and handed to
`matters/partials/work_plan.html` (docs/adr/0133).

**Current-ness is the canonical action's, never the step's.** A step is current
exactly when the visible open `NextAction` names it. A step whose open action is
restricted below the reader reads as an ordinary planned step, so the plan
cannot be used to learn about an action the reader may not see.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from app.workflow.enums import PlanStepOperation
from app.workflow.models import MatterPlanStep
from app.workflow.plan import STANDARD_PLAN, has_template, plan_revision, plan_steps_of

#: What each linked operation's shortcut says beside the current step: the
#: `LISA TEEMALE` chip's own words, so the person recognises the form it opens.
TYPED_ACTION_LABELS: dict[str, str] = {
    PlanStepOperation.WEBSITE_OVERVIEW.value: "+ Ülevaade / uudis",
    PlanStepOperation.ENGAGEMENT.value: "+ Kaasamine",
    PlanStepOperation.SUBMISSION.value: "+ Koja arvamus",
}


@dataclass
class PlanRow:
    """One step as drawn: its state in words as well as in a marker."""

    step: MatterPlanStep
    is_current: bool
    #: May be moved past a neighbour among the steps still ahead.
    can_move_up: bool = False
    can_move_down: bool = False
    #: The step's own `Muuda` form, attached by the view for a step still ahead.
    edit_form: Any = None

    @property
    def marker(self) -> str:
        """The glyph beside the step. Decorative; the words say the same."""
        if self.is_current:
            return "→"
        if self.step.is_completed:
            return "✓"
        if self.step.is_skipped:
            return "–"
        return "○"

    @property
    def state_label(self) -> str:
        """The state a screen reader hears, and the same word a sighted reader sees.

        Never colour alone: «Praegu», «Tehtud», «Soovitus», «Plaanis» and
        «Vahele jäetud» are printed beside the step (docs/adr/0133 §10).
        """
        if self.is_current:
            return "Praegu"
        return self.step.get_state_display()

    @property
    def css_state(self) -> str:
        if self.is_current:
            return "current"
        return self.step.state.lower()

    @property
    def is_ahead(self) -> bool:
        """Still to come, and not the step being worked on now."""
        return self.step.is_open and not self.is_current


@dataclass
class WorkPlanView:
    """Everything the page draws about the plan, for one reader."""

    rows: list[PlanRow] = field(default_factory=list)
    revision: str = ""
    has_standard: bool = False
    #: The step the visible open action belongs to, if any.
    current: MatterPlanStep | None = None
    #: The first step still ahead — the one `PRAEGUNE TEGEVUS` offers to start
    #: when nothing is current. Guidance only: never started for anybody.
    next_suggestion: MatterPlanStep | None = None

    @property
    def is_empty(self) -> bool:
        return not self.rows

    @property
    def compact_rows(self) -> list[PlanRow]:
        """The plan as normally read: skipped steps are out of the way."""
        return [row for row in self.rows if not row.step.is_skipped]

    @property
    def skipped_rows(self) -> list[PlanRow]:
        return [row for row in self.rows if row.step.is_skipped]

    @property
    def ahead(self) -> list[MatterPlanStep]:
        """The steps that could be started next, in order — `Järgmisena`'s choices."""
        return [row.step for row in self.rows if row.is_ahead]

    @property
    def typed_operation(self) -> str:
        """The current step's linked operation, when a canonical record does its work."""
        if self.current is None or not self.current.is_typed:
            return ""
        return self.current.operation

    @property
    def typed_action_label(self) -> str:
        return TYPED_ACTION_LABELS.get(self.typed_operation, "")


def work_plan_for(matter: Any, current_action: Any) -> WorkPlanView:
    """The plan for this Matter, as the reader whose `current_action` this is sees it."""
    steps = plan_steps_of(matter)
    current_id = getattr(current_action, "plan_step_id", None)
    rows = [PlanRow(step=step, is_current=step.pk == current_id) for step in steps]
    ahead = [row for row in rows if row.is_ahead]
    for index, row in enumerate(ahead):
        row.can_move_up = index > 0
        row.can_move_down = index < len(ahead) - 1
    current = next((row.step for row in rows if row.is_current), None)
    return WorkPlanView(
        rows=rows,
        revision=plan_revision(steps),
        has_standard=has_template(steps, STANDARD_PLAN),
        current=current,
        next_suggestion=ahead[0].step if ahead else None,
    )
