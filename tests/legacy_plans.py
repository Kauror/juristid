"""The standard background sequence as version 1 seeded it (docs/adr/0133 §5).

Version 2 (docs/adr/0168 §5) no longer seeds «Koonda tagasiside ja kujunda
Koja seisukoht» (`form-position`), but every Matter filed before it still
holds that step. Tests that need such a Matter seed it through the ordinary
`seed_standard_plan` with this copy of the old template — the same key, the
version and steps it shipped with — so the rows are exactly what production
holds.
"""

from __future__ import annotations

from typing import Any

from django.db import transaction

from app.workflow import plan
from app.workflow.enums import DatePrecision, PlanStepOperation
from app.workflow.models import MatterPlanStep, NextAction
from app.workflow.plan import STANDARD_PLAN, PlanTemplate, TemplateStep, seed_standard_plan

STANDARD_PLAN_V1 = PlanTemplate(
    key=STANDARD_PLAN.key,
    version=1,
    label=STANDARD_PLAN.label,
    steps=(
        TemplateStep("read-material", "Tutvu materjaliga", PlanStepOperation.GENERIC),
        TemplateStep(
            "website-overview", "Koosta kodulehe ülevaade", PlanStepOperation.WEBSITE_OVERVIEW
        ),
        TemplateStep(
            "consult-members", "Kaasa liikmeid / küsi tagasisidet", PlanStepOperation.ENGAGEMENT
        ),
        TemplateStep(
            "form-position",
            "Koonda tagasiside ja kujunda Koja seisukoht",
            PlanStepOperation.GENERIC,
        ),
        TemplateStep("send-opinion", "Saada Koja arvamus", PlanStepOperation.SUBMISSION),
    ),
)


def seed_version_one_plan(*, matter: Any, actor: Any = None) -> list[MatterPlanStep]:
    """A plan as a Matter filed before 2026-10-14 carries it."""
    return seed_standard_plan(matter=matter, actor=actor, template=STANDARD_PLAN_V1)


def start_as_before_adr_0168(
    *, matter: Any, step: Any, actor: Any = None, text: str = ""
) -> NextAction:
    """A **structured** step made the current action, as `Alusta` did before
    docs/adr/0168.

    Since 0168 `Alusta` on a structured step opens its own form, and since
    owner decision D9 (2026-10-17) `activate_plan_step` refuses to start one as
    a generic action. Matters started before 0168 still carry such a current
    step, and the canonical-form, consistency and drafting rules are written
    for them, so the tests that need one build it here — through the same
    lock, startability check and `_start` the service used.
    """
    with transaction.atomic():
        locked = plan._lock(matter)
        target = plan.startable_step(locked, getattr(step, "pk", step))
        return plan._start(
            locked_matter=locked,
            step=target,
            actor=actor,
            text=text,
            target_date=None,
            date_precision=DatePrecision.EXACT,
        )
