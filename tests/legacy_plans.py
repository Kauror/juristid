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

from app.workflow.enums import PlanStepOperation
from app.workflow.models import MatterPlanStep
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
