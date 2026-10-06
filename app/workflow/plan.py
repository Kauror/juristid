"""`Soovitatud järgmisena` — the Matter's next suggested step, in the background.

Since docs/adr/0141 `Tööplaan` is no longer a visible or administered feature.
What remains is the ordered sequence behind one line under `PRAEGUNE TEGEVUS`:
when nothing is current, the first step still ahead is suggested, and the
person may **start** it (`activate_plan_step`) or **dismiss** it
(`skip_plan_step`, which stores `SKIPPED` — persistent, and the next step is
suggested instead).

Starting writes the Matter's one canonical `NextAction` through
`set_next_action_for_new_work`, with ``plan_step`` pointing back here; `Muuda`
carries that link to the replacement, and completing the action completes the
step (`app.workflow.services`), so the sequence advances. Work written by hand
is never linked to a step.

The sequence comes from the code-managed `STANDARD_PLAN`, seeded when a person
files real work (`Uus teema`, `Saabunud`). Every change locks the Matter; a
dismissal also names the plan revision the page was drawn from, so a stale tab
cannot skip a different suggestion. Changes are audited as `PLAN_*` events and
are never chronology.
"""

from __future__ import annotations

import hashlib
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date
from typing import Any

from django.db import transaction
from django.utils import timezone

from app.audit.enums import ChangeEventType
from app.audit.services import record_change_event
from app.core.errors import DomainError
from app.matters.enums import RecordMode
from app.workflow.enums import (
    ActionKind,
    ActionStatus,
    DatePrecision,
    DateSemantics,
    PlanStepOperation,
    PlanStepSource,
    PlanStepState,
)
from app.workflow.models import MatterPlanStep, NextAction
from app.workflow.services import set_next_action_for_new_work

# -- the code-managed template ------------------------------------------------


@dataclass(frozen=True)
class TemplateStep:
    """One step of a template: a stable key, its words and its operation.

    The key is what idempotent seeding compares, and it never changes once a
    template version ships. The words may be refined in a new version; a Matter
    already seeded keeps the copy it was given.
    """

    key: str
    title: str
    operation: str


@dataclass(frozen=True)
class PlanTemplate:
    """A versioned, code-managed plan. Not editable from the application.

    There is deliberately no template table and no administration screen: the
    department's standard way of working is reviewed like code and changes with
    a version number, and each seeded step is a durable copy, so editing this
    file reaches no existing Matter (docs/adr/0133 §5).
    """

    key: str
    version: int
    label: str
    steps: tuple[TemplateStep, ...]


#: `Tavapärane õigusloome`, version 1 — the Chamber's ordinary course of policy
#: work on an incoming law, proposal or draft, as the owner described it:
#: read it, write it up for the website, ask the members, form the position,
#: send the opinion (docs/adr/0133 §5).
#:
#: **Behaviour is bound to the operation, never to these words.** Rewording a
#: title in a later version changes nothing about how its step is finished.
STANDARD_PLAN = PlanTemplate(
    key="standard-legislative",
    version=1,
    label="Tavapärane tööplaan",
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

#: Every template the code knows, by key. One today; the schema keeps the key
#: on every seeded step so that a second (`Kiire arvamus`, `ELi teema`) is an
#: addition here rather than a migration (docs/adr/0133 §11).
PLAN_TEMPLATES: dict[str, PlanTemplate] = {STANDARD_PLAN.key: STANDARD_PLAN}

# -- refusals -----------------------------------------------------------------

#: The page was drawn from a sequence somebody has changed since — the
#: suggestion it showed was started, dismissed or replaced in another tab.
STALE_PLAN_REFUSAL = "Soovitus on vahepeal muutunud. Värskenda lehte ja vaata uuesti."
#: An archive register row is offered no plan at all (docs/adr/0133 §5). The
#: page draws no control for one; this is the same rule for a crafted POST.
ARCHIVE_HAS_NO_PLAN = "Arhiivikirjele tööplaani ei koostata."
#: A step id that is not a step of this Matter's sequence.
STEP_NOT_ON_MATTER = "Seda soovitust selle teema juures ei ole."
#: Starting a step while another action is open would replace it silently.
CURRENT_ACTION_EXISTS = (
    "Teemal on juba praegune tegevus. Märgi see tehtuks või muuda seda, "
    "enne kui järgmise sammu alustad."
)
#: Only a step still ahead can be started or dismissed.
STEP_NOT_OPEN = "See soovitus on juba tehtud või kõrvale jäetud."
#: The current step changes through its action, not through the plan.
STEP_IS_CURRENT = (
    "See samm on praegu pooleli. Muuda seda praeguse tegevuse juures või märgi see tehtuks."
)

# -- reading ------------------------------------------------------------------


def plan_steps_of(matter: Any) -> list[MatterPlanStep]:
    """The Matter's whole plan, in order, skipped steps included."""
    return list(MatterPlanStep.objects.filter(matter=matter).order_by("position", "created_at"))


def plan_revision(steps: Sequence[MatterPlanStep]) -> str:
    """Which version of the plan an editor was drawn from.

    The plan has no row of its own to carry an ``updated_at``, so its revision
    is the digest of every step's id and ``updated_at`` — the token each row
    already gives through `revision_token` elsewhere (`MatterWebsiteOverview`),
    folded over the set. Adding, removing, moving, editing, skipping, starting or
    finishing any step moves it; reading does not. A digest rather than the
    concatenation, so the hidden input stays short on a long plan.
    """
    material = "|".join(f"{step.pk}:{step.updated_at.isoformat()}" for step in steps)
    return hashlib.sha256(material.encode()).hexdigest()[:32]


def current_step_id(matter: Any) -> Any:
    """The step the Matter's one open action belongs to, or ``None``. Reader-blind.

    The domain question, asked under the Matter's lock by the services below:
    whether a step is current is decided by the canonical open `NextAction`,
    never by a column on the step (docs/adr/0133 §3). A page asks the scoped
    question through `app.matters.plan_view.recommendation_for` instead.
    """
    return (
        NextAction.objects.filter(matter=matter, status=ActionStatus.OPEN)
        .values_list("plan_step_id", flat=True)
        .first()
    )


# -- the lock and the revision -----------------------------------------------


def _lock(matter: Any) -> Any:
    """The Matter, locked and open, through the one helper every workspace write uses.

    Imported here rather than at the top: `app.matters` imports this app's
    models and services at module level, and the lock helper lives on its side
    of that edge.
    """
    from app.matters.locks import lock_open_matter_for_business_write

    return lock_open_matter_for_business_write(getattr(matter, "pk", matter))


def _lock_for_new_steps(matter: Any) -> Any:
    """`_lock`, for the one act that puts steps on a Matter: seeding.

    Refused for an archive register row under the lock, not only by the page
    hiding the controls: a page is not a
    boundary, and an open ARCHIVE Matter exists (docs/adr/0133 §5).
    """
    locked_matter = _lock(matter)
    if locked_matter.record_mode != RecordMode.FULL:
        raise DomainError(ARCHIVE_HAS_NO_PLAN)
    return locked_matter


def _locked_steps(locked_matter: Any) -> list[MatterPlanStep]:
    return list(
        MatterPlanStep.objects.select_for_update(no_key=True)
        .filter(matter=locked_matter)
        .order_by("position", "created_at")
    )


def _check_revision(steps: Sequence[MatterPlanStep], expected_revision: str | None) -> None:
    """Refuse an editor drawn from an older plan. ``None`` asks no question.

    ``None`` is for the callers that are not an editor — `Uus teema` seeding
    the plan it is creating, a test arranging a world. Every view that renders
    a control passes the token it rendered.
    """
    if expected_revision is not None and plan_revision(steps) != expected_revision:
        raise DomainError(STALE_PLAN_REFUSAL)


def _find(steps: Sequence[MatterPlanStep], step: Any) -> MatterPlanStep:
    wanted = str(getattr(step, "pk", step))
    for candidate in steps:
        if str(candidate.pk) == wanted:
            return candidate
    raise DomainError(STEP_NOT_ON_MATTER)


def _record(event_type: str, *, matter: Any, actor: Any, step: Any, payload: dict) -> None:
    record_change_event(
        event_type=event_type,
        matter=matter,
        actor=actor,
        obj=step,
        summary=getattr(step, "title", "")[:200],
        payload=payload,
    )


# -- the standard plan --------------------------------------------------------


@transaction.atomic
def seed_standard_plan(
    *,
    matter: Any,
    actor: Any = None,
    template: PlanTemplate = STANDARD_PLAN,
    expected_revision: str | None = None,
) -> list[MatterPlanStep]:
    """Copy the template's steps onto a Matter as faint `SUGGESTED` guidance.

    **Called only where a person is starting real work** — `Uus teema` and
    `Saabunud`, inside their creation transaction. Never by `create_matter`,
    an importer, a register refresh, a cutover or a seed command: those record
    what was, and a plan on them would be intent nobody stated
    (docs/adr/0133 §5).

    **Idempotent by step key.** A step of this template already on the plan,
    in any state — suggested, started, done, skipped or edited — is not copied
    again, so seeding twice adds nothing and a retried POST writes nothing.
    `workflow_plan_step_template_once` says the same in the database.

    **Merging is additive.** On a plan that already holds steps a person added,
    the missing template steps are appended after them, in the template's order:
    nothing a person wrote is reordered, renamed or removed. A person repeating
    a step creates a `CUSTOM` occurrence, which this never counts.

    Nothing here creates a `NextAction`, a date, a deadline, a work item or a
    chronology row. One `PLAN_SEEDED` audit event, and only when something was
    added.
    """
    locked_matter = _lock_for_new_steps(matter)
    steps = _locked_steps(locked_matter)
    _check_revision(steps, expected_revision)
    present = {
        step.template_step_key
        for step in steps
        if step.source == PlanStepSource.TEMPLATE and step.template_key == template.key
    }
    missing = [item for item in template.steps if item.key not in present]
    if not missing:
        return []
    created = [
        MatterPlanStep.objects.create(
            matter=locked_matter,
            position=len(steps) + index,
            title=item.title,
            source=PlanStepSource.TEMPLATE,
            operation=item.operation,
            state=PlanStepState.SUGGESTED,
            template_key=template.key,
            template_version=template.version,
            template_step_key=item.key,
            created_by=actor,
        )
        for index, item in enumerate(missing)
    ]
    record_change_event(
        event_type=ChangeEventType.PLAN_SEEDED,
        matter=locked_matter,
        actor=actor,
        summary=template.label,
        payload={
            "template": template.key,
            "version": template.version,
            "steps": [item.key for item in missing],
        },
    )
    return created


def _open_and_not_current(locked_matter: Any, step: MatterPlanStep) -> None:
    if not step.is_open:
        raise DomainError(STEP_NOT_OPEN)
    if current_step_id(locked_matter) == step.pk:
        raise DomainError(STEP_IS_CURRENT)


@transaction.atomic
def skip_plan_step(
    *, step: Any, actor: Any = None, expected_revision: str | None = None
) -> MatterPlanStep:
    """`×` beside `Soovitatud järgmisena` — this suggestion is not wanted here.

    Persistent: the step is stored as `SKIPPED`, so a reload never suggests it
    again and the next step still ahead is suggested instead. Not a deletion —
    the row and its `PLAN_STEP_SKIPPED` audit event stay. Writes no chronology
    row, creates no record and touches no work surface.

    **The exact step the page showed.** The step id and the revision the page
    was drawn from are both checked under the lock: a suggestion somebody else
    started, dismissed or changed in another tab refuses the whole act rather
    than dismissing whatever is suggested now. The current step is never
    dismissed (docs/adr/0141).
    """
    locked_matter = _lock(step.matter_id)
    steps = _locked_steps(locked_matter)
    _check_revision(steps, expected_revision)
    target = _find(steps, step)
    _open_and_not_current(locked_matter, target)
    previous = target.state
    target.state = PlanStepState.SKIPPED
    target.skipped_at = timezone.now()
    target.skipped_by = actor
    target.save(update_fields=["state", "skipped_at", "skipped_by", "updated_at"])
    _record(
        ChangeEventType.PLAN_STEP_SKIPPED,
        matter=locked_matter,
        actor=actor,
        step=target,
        payload={"from": previous},
    )
    return target


# -- starting a step ----------------------------------------------------------


def startable_step(locked_matter: Any, step_id: Any) -> MatterPlanStep:
    """The step ``step_id`` names on this Matter, if it may be started — or a refusal.

    Asked under the Matter's lock, before anything is written, by
    :func:`activate_plan_step`: a step of another Matter, or a finished or
    dismissed one, is refused.
    """
    step = (
        MatterPlanStep.objects.select_for_update(no_key=True)
        .filter(matter=locked_matter, pk=step_id)
        .first()
    )
    if step is None:
        raise DomainError(STEP_NOT_ON_MATTER)
    if not step.is_open:
        raise DomainError(STEP_NOT_OPEN)
    return step


@transaction.atomic
def activate_plan_step(
    *,
    matter: Any,
    step: Any,
    actor: Any = None,
    text: str = "",
    target_date: date | None = None,
    date_precision: str = DatePrecision.EXACT,
    responsible: Any = None,
) -> NextAction:
    """`Alusta` — this step is now the work. It becomes the Matter's `NextAction`.

    **Through the one canonical service.** `set_next_action_for_new_work` writes
    the step, so the departed-owner rule, the precision rules, `DO` /
    `DEADLINE`, the audit row and the one-open-action constraint are exactly
    those of every other step. ``text`` is the step's own words unless the
    person changed them; ``target_date`` is optional and never invented
    (docs/adr/0106).

    **Never over another action.** A Matter with an open step refuses, rather
    than superseding it: the ordinary loop is finish the current thing and then
    choose the next.

    A `SUGGESTED` step started is a step a person accepted: it becomes
    `PLANNED`. Current-ness is not stored — the open action pointing here says
    it (docs/adr/0133 §3).
    """
    locked_matter = _lock(matter)
    target = startable_step(locked_matter, getattr(step, "pk", step))
    if NextAction.objects.filter(matter=locked_matter, status=ActionStatus.OPEN).exists():
        raise DomainError(CURRENT_ACTION_EXISTS)
    return _start(
        locked_matter=locked_matter,
        step=target,
        actor=actor,
        text=text,
        target_date=target_date,
        date_precision=date_precision,
        responsible=responsible,
    )


def _start(
    *,
    locked_matter: Any,
    step: MatterPlanStep,
    actor: Any,
    text: str,
    target_date: date | None,
    date_precision: str,
    responsible: Any = None,
) -> NextAction:
    """Write the action for a step already checked startable, on a Matter with none open."""
    action = set_next_action_for_new_work(
        matter=locked_matter,
        text=(text or "").strip() or step.title,
        kind=ActionKind.DO,
        date_semantics=DateSemantics.DEADLINE,
        target_date=target_date,
        date_precision=date_precision,
        responsible=responsible,
        actor=actor,
        plan_step=step,
    )
    # Saved whether or not the state changes. Starting a step changes which
    # steps the editor may move, so it must move `plan_revision`; a PLANNED step
    # left unsaved kept the old token, and a stale `↑/↓` then swapped a
    # neighbour the editor had never shown (docs/adr/0133 §10).
    if step.state == PlanStepState.SUGGESTED:
        step.state = PlanStepState.PLANNED
    step.save(update_fields=["state", "updated_at"])
    _record(
        ChangeEventType.PLAN_STEP_ACTIVATED,
        matter=locked_matter,
        actor=actor,
        step=step,
        payload={"action": str(action.pk)},
    )
    return action
