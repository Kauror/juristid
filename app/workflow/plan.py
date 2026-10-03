"""`Tööplaan` — the likely course of a Matter's work, as faint guidance.

**Four questions, four answers, and this module owns only the third**
(docs/adr/0133 §1):

* `Hetkeseis` — where the external process stands;
* `NextAction` — what the lawyer is doing next, at most one open per Matter;
* **`Tööplaan` — what will probably have to be done after that;**
* `Teema käik` — what actually happened.

A plan is guidance, not a queue. A step has no date, no responsible person, no
lateness and no reminder; it reaches no work surface, count or statistic and is
never a chronology row. It becomes work in exactly one way — somebody starts it
(:func:`activate_plan_step`) — and starting it writes the Matter's one canonical
`NextAction` through `set_next_action_for_new_work`, with ``plan_step`` pointing
back here. There is no second task service and no second date model.

**The one-open-action invariant is untouched.** Starting a step while another
action is open is refused rather than silently replacing it: the ordinary loop
is *finish the current thing, then choose the next* (docs/adr/0133 §4).

**Every change is audited and none is chronology.** The events below are
`PLAN_*`, absent from `TIMELINE_EVENT_TYPES` on purpose (docs/adr/0133 §7).

**Every change locks the Matter**, through the same helper every workspace write
uses, so a closed Matter refuses and two writers take turns. Edits to the plan's
*shape* — add, edit, move, skip, restore, repeat — also name the plan revision
the editor was drawn from, and a stale one refuses the whole change rather than
overwriting a colleague's newer order (docs/adr/0133 §10).
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

#: The editor was drawn from a plan somebody has changed since.
STALE_PLAN_REFUSAL = "Tööplaani on vahepeal muudetud. Värskenda lehte ja vaata plaan uuesti üle."
#: A step id that is not a step of this Matter's plan.
STEP_NOT_ON_MATTER = "Seda sammu selle teema tööplaanis ei ole."
#: Starting a step while another action is open would replace it silently.
CURRENT_ACTION_EXISTS = (
    "Teemal on juba praegune tegevus. Märgi see tehtuks või muuda seda, "
    "enne kui järgmise sammu alustad."
)
#: Only a step still ahead can be started, edited, moved or skipped.
STEP_NOT_OPEN = "See samm on juba tehtud või vahele jäetud."
#: The current step changes through its action, not through the plan.
STEP_IS_CURRENT = (
    "See samm on praegu pooleli. Muuda seda praeguse tegevuse juures või märgi see tehtuks."
)
#: A step's words are what it is.
STEP_NEEDS_TITLE = "Kirjuta, mis samm see on."
STEP_TITLE_TOO_LONG = "Sammu kirjeldus on liiga pikk (kuni 300 märki)."
#: Only a finished step is repeated; an open one is already ahead.
ONLY_COMPLETED_REPEATS = "Korrata saab ainult tehtud sammu."
#: Only a skipped step is restored.
ONLY_SKIPPED_RESTORES = "Taastada saab ainult vahele jäetud sammu."
#: A move past either end of the steps still ahead.
STEP_CANNOT_MOVE = "Sammu ei saa sinna liigutada."

TITLE_MAX = 300

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


def has_template(steps: Sequence[MatterPlanStep], template: PlanTemplate = STANDARD_PLAN) -> bool:
    """Whether every step of ``template`` is already on this plan, in any state."""
    present = {
        step.template_step_key
        for step in steps
        if step.source == PlanStepSource.TEMPLATE and step.template_key == template.key
    }
    return all(item.key in present for item in template.steps)


def current_step_id(matter: Any) -> Any:
    """The step the Matter's one open action belongs to, or ``None``. Reader-blind.

    The domain question, asked under the Matter's lock by the services below:
    whether a step is current is decided by the canonical open `NextAction`,
    never by a column on the step (docs/adr/0133 §3). A page asks the scoped
    question through `app.matters.plan_view` instead.
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


def _renumber(steps: Sequence[MatterPlanStep]) -> None:
    """Make the positions dense from zero in the given order, writing only what moved."""
    for index, step in enumerate(steps):
        if step.position != index:
            step.position = index
            step.save(update_fields=["position", "updated_at"])


def _clean_title(title: str) -> str:
    cleaned = " ".join((title or "").split())
    if not cleaned:
        raise DomainError(STEP_NEEDS_TITLE)
    if len(cleaned) > TITLE_MAX:
        raise DomainError(STEP_TITLE_TOO_LONG)
    return cleaned


def _check_operation(operation: str) -> str:
    if operation not in PlanStepOperation.values:
        raise DomainError(f"Tundmatu seotud toiming {operation!r}.")
    return operation


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
    `Saabunud`, inside their creation transaction — or where a writer presses
    `+ Lisa tavapärane tööplaan` on an open Matter. Never by `create_matter`,
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
    locked_matter = _lock(matter)
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


# -- changing the plan's shape ------------------------------------------------


@transaction.atomic
def add_plan_step(
    *,
    matter: Any,
    title: str,
    actor: Any = None,
    operation: str = PlanStepOperation.GENERIC,
    before: Any = None,
    expected_revision: str | None = None,
) -> MatterPlanStep:
    """`+ Lisa samm` — a step a person expects to take. Not work yet.

    `CUSTOM`, `PLANNED`, `GENERIC` unless a linked operation is chosen. No date,
    no responsible person, no `NextAction`: the step becomes work only when it
    is started (docs/adr/0133 §4).

    ``before`` places it ahead of that step; nothing places it at the end.
    """
    cleaned = _clean_title(title)
    _check_operation(operation)
    locked_matter = _lock(matter)
    steps = _locked_steps(locked_matter)
    _check_revision(steps, expected_revision)
    index = len(steps)
    if before is not None:
        anchor = _find(steps, before)
        if not anchor.is_open:
            raise DomainError(STEP_NOT_OPEN)
        index = steps.index(anchor)
    step = MatterPlanStep.objects.create(
        matter=locked_matter,
        position=index,
        title=cleaned,
        source=PlanStepSource.CUSTOM,
        operation=operation,
        state=PlanStepState.PLANNED,
        created_by=actor,
    )
    _renumber([*steps[:index], step, *steps[index:]])
    _record(
        ChangeEventType.PLAN_STEP_ADDED,
        matter=locked_matter,
        actor=actor,
        step=step,
        payload={"operation": operation, "position": index},
    )
    return step


def _open_and_not_current(locked_matter: Any, step: MatterPlanStep) -> None:
    if not step.is_open:
        raise DomainError(STEP_NOT_OPEN)
    if current_step_id(locked_matter) == step.pk:
        raise DomainError(STEP_IS_CURRENT)


@transaction.atomic
def edit_plan_step(
    *,
    step: Any,
    title: str,
    operation: str,
    actor: Any = None,
    expected_revision: str | None = None,
) -> MatterPlanStep:
    """Change what a future step says, and which operation does it.

    **Both answers are explicit.** The editor shows `Seotud toiming` beside the
    words, and whatever it posts is stored — nothing is inferred from the text.
    A typed step renamed to unrelated work keeps its operation only if the
    person left it chosen in view of the new words; the editor switches it to
    `Tavaline tegevus` as they type (docs/adr/0133 §6).

    A finished or skipped step is history and is not edited; the current step is
    edited through its action (`Muuda`). An edit is a person accepting the step,
    so a `SUGGESTED` one becomes `PLANNED`. An edit that changes nothing writes
    nothing.
    """
    cleaned = _clean_title(title)
    _check_operation(operation)
    locked_matter = _lock(step.matter_id)
    steps = _locked_steps(locked_matter)
    _check_revision(steps, expected_revision)
    target = _find(steps, step)
    _open_and_not_current(locked_matter, target)
    if (target.title, target.operation) == (cleaned, operation):
        return target
    before = {"title": target.title, "operation": target.operation}
    target.title = cleaned
    target.operation = operation
    target.state = PlanStepState.PLANNED
    target.save(update_fields=["title", "operation", "state", "updated_at"])
    _record(
        ChangeEventType.PLAN_STEP_CHANGED,
        matter=locked_matter,
        actor=actor,
        step=target,
        payload={"from": before, "to": {"title": cleaned, "operation": operation}},
    )
    return target


@transaction.atomic
def move_plan_step(
    *,
    step: Any,
    direction: str,
    actor: Any = None,
    expected_revision: str | None = None,
) -> MatterPlanStep:
    """`↑` / `↓` — swap a future step with its neighbour among the steps still ahead.

    Only steps still ahead move, and only past each other: a finished step stays
    where it happened, and the current one stays where it is. Buttons rather
    than dragging, so the order is changed the same way with a keyboard, a
    pointer or a screen reader (docs/adr/0133 §10).
    """
    if direction not in ("up", "down"):
        raise DomainError(STEP_CANNOT_MOVE)
    locked_matter = _lock(step.matter_id)
    steps = _locked_steps(locked_matter)
    _check_revision(steps, expected_revision)
    target = _find(steps, step)
    _open_and_not_current(locked_matter, target)
    current = current_step_id(locked_matter)
    movable = [item for item in steps if item.is_open and item.pk != current]
    index = movable.index(target)
    neighbour_index = index - 1 if direction == "up" else index + 1
    if not 0 <= neighbour_index < len(movable):
        raise DomainError(STEP_CANNOT_MOVE)
    neighbour = movable[neighbour_index]
    order = list(steps)
    a, b = order.index(target), order.index(neighbour)
    order[a], order[b] = order[b], order[a]
    _renumber(order)
    _record(
        ChangeEventType.PLAN_STEP_MOVED,
        matter=locked_matter,
        actor=actor,
        step=target,
        payload={"direction": direction, "past": str(neighbour.pk)},
    )
    return target


@transaction.atomic
def skip_plan_step(
    *, step: Any, actor: Any = None, expected_revision: str | None = None
) -> MatterPlanStep:
    """`Jäta vahele` — this step is not needed here. Kept, and restorable.

    Not a deletion: the step stays on the plan as `SKIPPED`, out of the compact
    list and visible in `Muuda plaani`, so the decision can be undone and is on
    the audit trail. Writes no chronology row, creates no record and touches no
    work surface. The current step is finished or replaced first.
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


@transaction.atomic
def restore_plan_step(
    *, step: Any, actor: Any = None, expected_revision: str | None = None
) -> MatterPlanStep:
    """`Taasta` — a skipped step is wanted after all.

    `PLANNED`, not back to `SUGGESTED`: restoring is a person deciding the step
    belongs here, which is what `PLANNED` means. It returns to the place it
    held; the skip's stamps are cleared and the audit trail keeps them.
    """
    locked_matter = _lock(step.matter_id)
    steps = _locked_steps(locked_matter)
    _check_revision(steps, expected_revision)
    target = _find(steps, step)
    if not target.is_skipped:
        raise DomainError(ONLY_SKIPPED_RESTORES)
    target.state = PlanStepState.PLANNED
    target.skipped_at = None
    target.skipped_by = None
    target.save(update_fields=["state", "skipped_at", "skipped_by", "updated_at"])
    _record(
        ChangeEventType.PLAN_STEP_RESTORED,
        matter=locked_matter,
        actor=actor,
        step=target,
        payload={},
    )
    return target


@transaction.atomic
def repeat_plan_step(
    *, step: Any, actor: Any = None, expected_revision: str | None = None
) -> MatterPlanStep:
    """`Korda` — the same work again, as a new occurrence.

    A revised draft arrives and the members have to be asked again. The finished
    step is not reopened — it happened, and its completion stays exactly as it
    was. A new `CUSTOM`, `PLANNED` step with the same words and operation is put
    first among the steps still ahead, where the next thing to do is read.
    There is no uniqueness on an operation: a second `Kaasamine`, `Ülevaade` or
    `Koja arvamus` is ordinary (docs/adr/0133 §9).
    """
    locked_matter = _lock(step.matter_id)
    steps = _locked_steps(locked_matter)
    _check_revision(steps, expected_revision)
    original = _find(steps, step)
    if not original.is_completed:
        raise DomainError(ONLY_COMPLETED_REPEATS)
    current = current_step_id(locked_matter)
    index = next(
        (position for position, item in enumerate(steps) if item.is_open and item.pk != current),
        len(steps),
    )
    repeated = MatterPlanStep.objects.create(
        matter=locked_matter,
        position=index,
        title=original.title,
        source=PlanStepSource.CUSTOM,
        operation=original.operation,
        state=PlanStepState.PLANNED,
        created_by=actor,
    )
    _renumber([*steps[:index], repeated, *steps[index:]])
    _record(
        ChangeEventType.PLAN_STEP_ADDED,
        matter=locked_matter,
        actor=actor,
        step=repeated,
        payload={"operation": original.operation, "repeats": str(original.pk)},
    )
    return repeated


# -- starting a step ----------------------------------------------------------


def startable_step(locked_matter: Any, step_id: Any) -> MatterPlanStep:
    """The step ``step_id`` names on this Matter, if it may be started — or a refusal.

    Asked under the Matter's lock, before anything is written, by
    :func:`activate_plan_step` and by `Mida tegid?`'s `Järgmisena`: a step of
    another Matter, a finished or skipped one, or one somebody else has already
    started is refused, and the whole save with it.
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
    choose the next, and `Mida tegid?`'s `Järgmisena` does both in one save.

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


def start_checked_step(
    *,
    locked_matter: Any,
    step: MatterPlanStep,
    actor: Any,
    text: str = "",
    target_date: date | None = None,
    date_precision: str = DatePrecision.EXACT,
) -> NextAction:
    """Start a step :func:`startable_step` already returned, inside the caller's transaction.

    For `Mida tegid?` → `Järgmisena`, which checks the next step before it writes
    anything and starts it only after the current action is completed — so the
    one-open-action question has already been answered by the completion.
    """
    return _start(
        locked_matter=locked_matter,
        step=step,
        actor=actor,
        text=text,
        target_date=target_date,
        date_precision=date_precision,
    )
