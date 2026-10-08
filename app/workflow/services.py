"""Named use cases for `Järgmiseks`.

One Matter has at most one open action. Every path that changes it goes through
here so that the previous action is ended rather than overwritten, the audit
trail records who decided what, and the "one open action" invariant is
maintained in the same transaction as the change.
"""

from __future__ import annotations

from datetime import date
from typing import Any, cast

from django.apps import apps
from django.db import transaction
from django.utils import timezone

from app.accounts.selectors import is_assignable_business_user
from app.audit.enums import ChangeEventType
from app.audit.services import record_change_event
from app.core.enums import Visibility
from app.core.errors import DomainError
from app.workflow.enums import (
    REVIEW_KINDS,
    ActionKind,
    ActionStatus,
    DatePrecision,
    DateSemantics,
)
from app.workflow.models import NextAction


def current_next_action(matter: Any) -> NextAction | None:
    """The one open action on this Matter, or None. **Reader-blind.**

    A domain question — *which step is open on this file* — asked by the
    services that have to act on the answer whatever anybody may see: closing a
    Matter must cancel its live action, and replacing a step must find the one
    it replaces. Scoping that would mean a Matter whose only open action is
    restricted below it could be closed twice, or acquire a second open step.

    **Not what a page asks.** A reader asks *which step may I see*, and a
    `NextAction` is a `VisibilityInheritingModel` that can be restricted below
    its Matter. That question is `app.matters.selectors.current_action_of`,
    which takes the reader; every rendering surface goes through it. Reading
    this one instead printed a restricted step's text and date onto the Teema
    page for anybody who could open the Matter (AUTH-003, docs/adr/0038).
    """
    return (
        NextAction.objects.filter(matter=matter, status=ActionStatus.OPEN)
        .select_related("responsible")
        .first()
    )


#: Refused when a *new* step would be assigned to somebody the department no
#: longer gives work to. The remedy is on the Teema, not on the step: the step's
#: own Vastutaja is not rendered on either native surface, so the only thing the
#: reader can actually change is who holds the file.
DEPARTED_OWNER_REFUSAL = (
    "Teema vastutaja ei ole enam aktiivne osakonna töötaja. "
    "Määra teemale uus vastutaja, enne kui järgmise sammu salvestad."
)

#: Refused when a POST names a responsible person outside today's department.
#: The forms already narrow their querysets, so reaching this means the value
#: did not come off the page.
INELIGIBLE_RESPONSIBLE_REFUSAL = "Valitud vastutaja ei ole aktiivne osakonna töötaja."


def responsible_for_new_work(*, matter: Any, explicit: Any = None) -> Any:
    """Who a step a person is creating *right now* may be made responsible for.

    `set_next_action` defaults `responsible` to `matter.owner`, and that default
    is correct for the caller it was written for: an importer reconstructing a
    2019 instruction is recording who it belonged to, and the answer is whoever
    owned the file. It is wrong for the native paths, where nobody is recording
    a fact — somebody is handing out work today. A Matter whose owner has left
    would quietly put the new step in a departed colleague's queue, which is the
    one place nobody looks.

    So the rule is not "never a departed person" — the register still says what
    it says — it is *new assignments go to current department workers*, and this
    is where those two meanings are separated. Three answers, and no fourth:

    * an explicit person who is assignable — accepted, unchanged;
    * nobody named, and the owner is assignable — the owner, which is the
      convenience the composer was built around and is left exactly as it was;
    * nobody named, and the owner is not somebody work may be given to —
      refused, in Estonian, naming the thing the reader can fix.

    Refused rather than repaired. Choosing the department head, the first name
    on the list or the person pressing the button would all be the system
    inventing an assignment nobody made, and an invented one is indistinguishable
    from a deliberate one a week later. Clearing it to nobody is the same
    failure with a blank where the name should be.

    An unowned Matter is not this case. It has nobody to fall back *to*, the
    step is stored with no responsible person exactly as it is today, and
    refusing it would retire behaviour this correction was not asked to change.
    """
    if explicit is not None:
        if not is_assignable_business_user(explicit):
            raise DomainError(INELIGIBLE_RESPONSIBLE_REFUSAL)
        return explicit

    owner = getattr(matter, "owner", None)
    if owner is None:
        return None
    if not is_assignable_business_user(owner):
        raise DomainError(DEPARTED_OWNER_REFUSAL)
    return owner


#: What a step with no day but a period precision is told.
UNDATED_ACTION_WITH_A_PRECISION = "Kuupäevata tegevusel ei saa olla kuupäeva täpsust."


def refuse_an_unsupported_precision(target_date: date | None, date_precision: str) -> None:
    """The precision half of a `NextAction`'s date, checked where every writer passes.

    The two services that write `date_precision` — `set_next_action` and
    `acknowledge_review` — call this, and `workflow_nextaction` carries the same
    two rules as `CHECK` constraints underneath (ENG-043). A `DomainError` here
    names what was wrong; an `IntegrityError` out of a composer transaction that
    has already written a note names a constraint.

    * **The vocabulary.** `HALF_YEAR` and `INFERRED` are not offered for new
      input and are still valid values (docs/adr/0079 §7, §8); anything outside
      `DatePrecision` was rendered as an exact day by every surface that met it.
    * **A step with no day is `EXACT`** (docs/adr/0106). `target_date=None` means
      no day has been recorded yet, and a `QUARTER` beside nothing is a period
      of nothing — so the undated step keeps the default the model has always
      given it, and only its approximate twin is refused.
    """
    if date_precision not in DatePrecision.values:
        raise DomainError(f"Tundmatu kuupäeva täpsus {date_precision!r}.")
    if target_date is None and date_precision != DatePrecision.EXACT:
        raise DomainError(UNDATED_ACTION_WITH_A_PRECISION)


#: The refusal every next-step control gives a step with no sentence: a step is
#: its sentence, and a day on its own is not one (docs/adr/0106). Here rather
#: than in the forms because `+ Märge`'s use case gives it too, for a save ticked
#: `Märgi järgmiseks tegevuseks` with nothing written (docs/adr/0124); the forms
#: import it from here.
NEXT_STEP_NEEDS_SENTENCE = "Kirjuta järgmine tegevus."


@transaction.atomic
def set_next_action_for_new_work(
    *,
    matter: Any,
    text: str,
    kind: str = ActionKind.DO,
    date_semantics: str = DateSemantics.DEADLINE,
    target_date: date | None = None,
    date_precision: str = DatePrecision.EXACT,
    source_text: str = "",
    responsible: Any = None,
    actor: Any = None,
    plan_step: Any = None,
    carry_plan_step: bool = False,
    carry_visibility_override: bool = False,
) -> NextAction:
    """`set_next_action`, for the surfaces where a person is creating the step.

    The native boundary, and a separate function rather than a flag on the
    service. A `bypass=` parameter would put the whole distinction in the hands
    of whoever writes the next call site, and the reading it needs — *is this
    somebody assigning work, or something recording what was assigned* — is one
    the caller knows and the service never can.

    Note the signature: no `provenance`. That keyword exists for the callers
    that are not a person, and they are exactly the callers that must not come
    through here. Import, enrichment and the seed commands keep calling
    `set_next_action` and keep preserving whatever the source says, including a
    responsible colleague who left years ago.
    """
    return set_next_action(
        matter=matter,
        text=text,
        kind=kind,
        date_semantics=date_semantics,
        target_date=target_date,
        date_precision=date_precision,
        source_text=source_text,
        responsible=responsible_for_new_work(matter=matter, explicit=responsible),
        actor=actor,
        plan_step=plan_step,
        carry_plan_step=carry_plan_step,
        carry_visibility_override=carry_visibility_override,
    )


#: A plan step named for an action on a different Matter. Unreachable from the
#: page, which fetches steps through the Matter it is on; this is the backstop.
PLAN_STEP_OF_ANOTHER_MATTER = "Tööplaani samm ei kuulu sellele teemale."


@transaction.atomic
def set_next_action(
    *,
    matter: Any,
    text: str,
    kind: str = ActionKind.DO,
    date_semantics: str = DateSemantics.DEADLINE,
    target_date: date | None = None,
    date_precision: str = DatePrecision.EXACT,
    source_text: str = "",
    responsible: Any = None,
    actor: Any = None,
    provenance: dict[str, Any] | None = None,
    plan_step: Any = None,
    carry_plan_step: bool = False,
    carry_visibility_override: bool = False,
) -> NextAction:
    """Set the current action, superseding whatever it replaces.

    **The text and the date are two facts, and only the first is required**
    (docs/adr/0106). Four shapes, of which three are valid:

    ===================  ==============  =========================================
    ``text``             ``target_date``  outcome
    ===================  ==============  =========================================
    present              present          the ordinary dated step
    present              ``None``         a step whose day nobody knows **yet**
    blank                present          refused, on the text
    blank                ``None``         refused, on the text
    ===================  ==============  =========================================

    A ``None`` date is *no deadline recorded yet* and is never filled in: not
    with today, not with the end of the month, not with an approximate period.
    Adding one later is an ordinary replacement through this same function, and
    so is clearing one.

    Responsibility defaults to the Matter owner: in practice the person who
    owns the file is the person who acts on it, and forcing that choice on every
    routine update would slow the composer down for no gain.

    ``provenance`` is for the callers that are not a person: an importer or an
    enrichment run has no ``actor``, and "who set this" would otherwise read as
    a blank. It is recorded under its own key on the existing
    ``NEXT_ACTION_SET`` event rather than as a second event, because only one
    thing happened — an action was set — and a history that raised two rows for
    it would double every imported instruction in the timeline. Manual callers
    pass nothing and are unaffected.

    **``plan_step`` and ``carry_plan_step`` — the `Tööplaan` relation**
    (docs/adr/0133 §4). Both default to nothing, so every existing caller writes
    an action with no plan step exactly as before.

    * ``plan_step`` is the step this action is being *started* from — passed
      only by `app.workflow.plan` when somebody presses `Alusta` on
      `Soovitatud järgmisena` (docs/adr/0141). It must be on the same Matter.
    * ``carry_plan_step`` is `Muuda`: an edit of the step that is open, whose
      replacement row is the same work and keeps the same plan step. Read off
      the row being superseded, under the lock, so a stale editor cannot carry
      a step it never saw. Any other supersession — a `+ Märge` dated ahead, an
      import — passes neither, and the plan step it replaces stays where it was:
      superseding work is never completing it.

    **``carry_visibility_override`` — the same work keeps its restriction**
    (docs/adr/0139). Also `Muuda`, and for the same reason: the replacement is
    the same work, so it is created with the superseded row's own restriction —
    `RESTRICTED` is carried, empty or `NORMAL` carries nothing and the new row
    inherits the Matter as before. Read off the locked row and written in the
    `INSERT`, never patched afterwards, and copied rather than joined. Every
    other caller is new work and passes nothing: superseding a restricted step
    with new work does not restrict the new work (docs/adr/0138 §3–§4).
    """
    text = text.strip()
    if not text:
        raise DomainError("Järgmiseks vajab teksti.")
    if kind not in ActionKind.values:
        raise DomainError(f"Tundmatu tegevuse liik {kind!r}.")
    if date_semantics not in DateSemantics.values:
        raise DomainError(f"Tundmatu kuupäeva tähendus {date_semantics!r}.")
    refuse_an_unsupported_precision(target_date, date_precision)

    # **A next action may have no date at all**, and that is not an incomplete
    # record (docs/adr/0106). «Vaatan ministeeriumi vastuse üle» is a whole
    # instruction; the day it happens is a second fact, and one the lawyer
    # frequently does not have yet. This refused that pair until now, so the
    # only way to record the sentence was to invent a day — and an invented day
    # is a false statement the work queue then reports on.
    #
    # `target_date is None` means **no deadline has been recorded yet**. It is
    # never read as today, as approximate, as waiting or as overdue: `is_overdue`
    # and `days_late` both return early on it, and `overdue_date_q` excludes it
    # in SQL. `text` is still required below, because an action with no text is
    # not a record of anything.

    # Lock the Matter, not just the action row. Closure and next-action changes
    # both depend on the Matter's lifecycle state, so the Matter row is the
    # concurrency boundary that keeps them from interleaving into a closed
    # Matter that still carries an open instruction.
    #
    # **At the strength every Matter writer uses** (`lock_matter_for_write`,
    # `FOR NO KEY UPDATE`), not plain `FOR UPDATE` (SVC-04). Writers still
    # exclude each other — the mode conflicts with itself — but `FOR UPDATE`
    # also blocks the `FOR KEY SHARE` every insert of a row referencing the
    # Matter takes, including the search rebuild's at COMMIT. Upgrading the
    # lock mid-composer is the ENG-027 shape: the next write of an indexed row
    # in the same transaction would deadlock against a rebuild
    # (docs/adr/0110 §1, amended).
    from app.matters.locks import lock_matter_for_write

    locked_matter = lock_matter_for_write(matter.pk)
    if not locked_matter.is_open:
        raise DomainError("Suletud teemale ei saa järgmist tegevust määrata.")

    if plan_step is not None and plan_step.matter_id != locked_matter.pk:
        raise DomainError(PLAN_STEP_OF_ANOTHER_MATTER)

    previous = (
        NextAction.objects.select_for_update(no_key=True)
        .filter(matter=locked_matter, status=ActionStatus.OPEN)
        .first()
    )
    if plan_step is None and carry_plan_step and previous is not None:
        plan_step_id = previous.plan_step_id
    else:
        plan_step_id = getattr(plan_step, "pk", None)
    visibility_override = ""
    if carry_visibility_override and previous is not None:
        carried = previous.visibility_override or ""
        visibility_override = "" if carried == Visibility.NORMAL else carried
    # **A current follow-up check is never superseded by other work**
    # (docs/adr/0146 §4). New work written over it — a `+ Märge` dated ahead,
    # an import — becomes current, and the check goes back to the plan on its
    # own day, still linked to its opinion: replacing it would be exactly the
    # silent disappearance of an unanswered opinion the check exists to prevent.
    returned_to_plan = None
    if previous is not None and previous.follow_up_id is not None:
        previous.status = ActionStatus.PLANNED
        previous.save(update_fields=["status", "updated_at"])
        returned_to_plan, previous = previous, None
    if previous is not None:
        previous.status = ActionStatus.SUPERSEDED
        previous.ended_at = timezone.now()
        previous.ended_by = actor
        previous.save(update_fields=["status", "ended_at", "ended_by", "updated_at"])

    action = NextAction.objects.create(
        matter=locked_matter,
        text=text,
        kind=kind,
        date_semantics=date_semantics,
        target_date=target_date,
        date_precision=date_precision,
        source_text=source_text,
        responsible=responsible or locked_matter.owner,
        created_by=actor,
        plan_step_id=plan_step_id,
        visibility_override=visibility_override,
    )

    if previous is not None:
        # Written after the new row exists so the chain is navigable in both
        # directions without a nullable placeholder.
        previous.replaced_by = action
        previous.save(update_fields=["replaced_by", "updated_at"])

    payload: dict[str, Any] = {
        "kind": kind,
        "date_semantics": date_semantics,
        "target_date": target_date.isoformat() if target_date else None,
        "replaced": str(previous.id) if previous else None,
    }
    if plan_step_id is not None:
        payload["plan_step"] = str(plan_step_id)
    if provenance:
        # Nested rather than merged flat, so a provenance key can never shadow
        # one of the four above and silently change what the event says.
        payload["provenance"] = provenance
    if returned_to_plan is not None:
        payload["returned_to_plan"] = str(returned_to_plan.pk)

    record_change_event(
        event_type=ChangeEventType.NEXT_ACTION_SET,
        matter=locked_matter,
        actor=actor,
        obj=action,
        summary=text[:200],
        payload=payload,
    )
    if returned_to_plan is not None:
        record_change_event(
            event_type=ChangeEventType.NEXT_ACTION_SET,
            matter=locked_matter,
            actor=actor,
            obj=returned_to_plan,
            summary=returned_to_plan.text[:200],
            payload={
                "kind": returned_to_plan.kind,
                "date_semantics": returned_to_plan.date_semantics,
                "target_date": (
                    returned_to_plan.target_date.isoformat()
                    if returned_to_plan.target_date
                    else None
                ),
                "replaced": None,
                "planned": True,
                "returned_to_plan": True,
                "follow_up": _follow_up_block(returned_to_plan),
            },
        )
    return action


#: The sentence a `Koostan arvamuse` step carries, in one place.
#:
#: A constant rather than a string in a view, because three things have to agree
#: about it: the service that writes the step, the idempotency check that
#: recognises one already written, and the page that shows the lawyer what they
#: are about to create. A sentence spelled twice is a sentence that drifts, and
#: here a drift would turn the idempotency check off without anything looking
#: wrong (docs/adr/0091 §1).
#:
#: It is the department's own words for the work — writing Koda's opinion — and
#: the first person, because it is the lawyer's own instruction to themselves,
#: which is what `Järgmiseks` has always held.
OPINION_PREPARATION_TEXT = "Koostan arvamuse"


@transaction.atomic
def establish_opinion_preparation_action(
    *,
    matter: Any,
    prepare_by: date,
    actor: Any = None,
    responsible: Any = None,
) -> NextAction:
    """`Koostan arvamuse` — the first step, from the date somebody typed on `Uus teema`.

    A normal incoming consultation begins the same way every time: the lawyer
    files the Teema and writes Koda's opinion by a day they already know. Before
    this they had to file the Teema, open `Lisa teemale`, choose
    `+ Järgmine tegevus`, and type the sentence «Koostan arvamuse» themselves —
    the same information entered twice, in two places, in a product whose whole
    claim is that routine work is faster than Excel plus OneNote (lawyer
    feedback 9, docs/adr/0091 §1).

    **It creates the step from a date the person supplied, and invents nothing.**
    ``prepare_by`` is required here, which is the point rather than an
    inconvenience: «automatically creates the next action» is not «guess when it
    is due». Not today, not seven days out, not the consultation deadline, not the
    end of the month and not the Matter's creation date. A blank field creates no
    step at all, because a commitment nobody stated is a commitment nobody can be
    held to and is indistinguishable a week later from one somebody made
    (docs/adr/0078 §2, docs/adr/0091 §1.2).

    **Idempotent, and that is what makes it safe to call from a creation flow.**
    A browser that retries a save — a double-pressed button, a resubmitted POST, a
    proxy replaying a request — must not leave two identical instructions on one
    file. Two things guarantee it cannot:

    * `NextAction`'s own `workflow_one_open_action_per_matter` unique constraint
      means a second open step is impossible in the database, whatever any caller
      does;
    * and this function checks for an **equivalent** step under the Matter's lock
      before writing, so a retry does not even supersede-and-replace the first
      one. Equivalent means the same sentence, the same day and the same
      precision — a lawyer who deliberately changes the date is changing the plan
      and gets a real replacement through the ordinary composer, which is a
      different act with a different audit row.

    Returning the existing step rather than raising is deliberate: the caller's
    question is «is this file's first step established», and it is.

    **Ordinary `NextAction` semantics throughout.** `DO` / `DEADLINE` / `EXACT`,
    because the date means *the day this gets done* — which is exactly what that
    combination says and is why `NextActionForm` stopped asking (ADR 0052 §3). The
    step goes through `set_next_action_for_new_work`, so the departed-owner rule
    applies: somebody is assigning work today, not recording what was assigned in
    2019 (ADR 0036 §5). It appears wherever open steps appear — `Minu asjad`,
    `PRAEGUNE TEGEVUS`, `Tähtajad`, the register's `JÄRGMISEKS` — with no special
    case anywhere, because it is not a special kind of step.

    ``responsible`` is handed in by the creation flow, which knows the owner the
    person chose on the same form before the Matter existed. It is a default and
    an explicit choice still wins, exactly as it does for `set_next_action`.
    """
    if prepare_by is None:
        raise DomainError("Koostan arvamuse vajab kuupäeva.")

    # The same lock, at the same strength, as `set_next_action` (SVC-04).
    from app.matters.locks import lock_matter_for_write

    locked_matter = lock_matter_for_write(matter.pk)
    existing = (
        NextAction.objects.select_for_update(no_key=True)
        .filter(
            matter=locked_matter,
            status=ActionStatus.OPEN,
            text=OPINION_PREPARATION_TEXT,
            target_date=prepare_by,
            date_precision=DatePrecision.EXACT,
        )
        .first()
    )
    if existing is not None:
        # The retry case. Nothing is written and nothing is superseded, so a
        # replayed request leaves one step, one audit row and one history.
        return existing

    return set_next_action_for_new_work(
        matter=locked_matter,
        text=OPINION_PREPARATION_TEXT,
        kind=ActionKind.DO,
        date_semantics=DateSemantics.DEADLINE,
        target_date=prepare_by,
        date_precision=DatePrecision.EXACT,
        responsible=responsible,
        actor=actor,
    )


def _lock_for_transition(action: NextAction, refusal: str) -> NextAction:
    """The Matter, then the action, and the action as it is under both locks.

    **A terminal transition is decided on the current row, never on the
    caller's instance** (ENG-072). The view fetched that instance before this
    transaction began; between then and now a replacement may have superseded
    it, a closure cancelled it, or a second click completed it. Deciding on the
    stale copy is how an action ended COMPLETED with `replaced_by` set, a
    completion was written after MATTER_CLOSED, and one task collected two
    completion events.

    Same order as `set_next_action` and `close_matter`: the Matter first, then
    the action (app/matters/locks.py). `FOR NO KEY UPDATE` on both, because the
    transaction goes on to insert a `ChangeEvent` referencing each; the mode
    conflicts with itself, and `set_next_action` takes the same one (SVC-04), so
    every transition on one Matter takes its turn.

    ``refusal`` is the sentence the transition already uses for an action that
    is not open. A row that no longer exists — its Matter deleted meanwhile —
    gets the same one: there is nothing current to act on.
    """
    matter_model = apps.get_model("matters", "Matter")
    try:
        matter = matter_model.objects.select_for_update(no_key=True).get(pk=action.matter_id)
        locked = NextAction.objects.select_for_update(no_key=True).get(pk=action.pk)
    except (matter_model.DoesNotExist, NextAction.DoesNotExist) as error:
        raise DomainError(refusal) from error
    # The Matter row this transaction already holds, rather than a second read
    # of it the first time a caller reaches `action.matter`.
    locked.matter = matter
    return locked


@transaction.atomic
def complete_next_action(
    *, action: NextAction, actor: Any = None, promote: bool = True
) -> NextAction:
    """Mark the current action done. It stays in the history.

    Decided on the locked row (`_lock_for_transition`), so a second click, a
    replacement or a closure that landed first makes this refuse instead of
    writing a second terminal state (ENG-072, docs/adr/0075 §4).
    """
    refusal = "Ainult kehtivat tegevust saab lõpetada."
    action = _lock_for_transition(action, refusal)
    if action.status != ActionStatus.OPEN:
        raise DomainError(refusal)
    # A follow-up check is finished only with what it found (docs/adr/0146 §5):
    # every generic completion — `Mida tegid?`, a ticked `Märgi praegune tegevus
    # tehtuks` — refuses one, and the database refuses a completed check
    # without an outcome underneath this.
    _refuse_a_follow_up_check(action)

    action.status = ActionStatus.COMPLETED
    action.ended_at = timezone.now()
    action.ended_by = actor
    action.save(update_fields=["status", "ended_at", "ended_by", "updated_at"])

    record_change_event(
        event_type=ChangeEventType.NEXT_ACTION_COMPLETED,
        matter=action.matter,
        actor=actor,
        obj=action,
        summary=action.text[:200],
        payload={"kind": action.kind},
    )
    if action.plan_step_id is not None:
        _complete_plan_step(action=action, step_id=action.plan_step_id, actor=actor)
    if promote:
        promote_next_planned_action(matter=action.matter, actor=actor)
    return action


def _complete_plan_step(*, action: NextAction, step_id: Any, actor: Any) -> None:
    """The `Tööplaan` step this action was an occurrence of is done too.

    **Here, inside the one completion service, so no path can disagree.**
    `Mida tegid?`, `✓ Tehtud` on Minu asjad and a ticked `Märgi praegune
    tegevus tehtuks` all complete the action through `complete_next_action`;
    each therefore completes its step — and the next suggestion follows — and
    nothing else does. A superseded or cancelled action — `Muuda` replacing
    it, a `+ Märge` dated ahead, a closure — leaves its step exactly as it was
    (docs/adr/0133 §4).

    Stamped now, like the action. The record the work produced keeps its own
    business date; neither the action's planned day nor anything else is
    written as when the step was done.
    """
    from app.workflow.enums import PlanStepState
    from app.workflow.models import MatterPlanStep

    step = MatterPlanStep.objects.select_for_update(no_key=True).get(pk=step_id)
    if step.state == PlanStepState.COMPLETED:
        return
    step.state = PlanStepState.COMPLETED
    step.completed_at = action.ended_at
    step.completed_by = actor
    step.skipped_at = None
    step.skipped_by = None
    step.save(
        update_fields=[
            "state",
            "completed_at",
            "completed_by",
            "skipped_at",
            "skipped_by",
            "updated_at",
        ]
    )
    record_change_event(
        event_type=ChangeEventType.PLAN_STEP_COMPLETED,
        matter=action.matter,
        actor=actor,
        obj=step,
        summary=step.title[:200],
        payload={"action": str(action.pk)},
    )


@transaction.atomic
def cancel_next_action(
    *,
    action: NextAction,
    actor: Any = None,
    reason: str = "",
    provenance: dict[str, Any] | None = None,
) -> NextAction:
    """Withdraw the current action. It stays in the history.

    ``provenance`` is the same keyword ``set_next_action`` carries and exists
    for the same callers: an enrichment run that withdraws an instruction it
    wrote itself has no ``actor``, and "who cancelled this" would otherwise read
    as a blank on a row somebody may need to account for years later.

    The pairing matters more here than it does on ``set``. A cancellation with a
    null actor is precisely what tells a later run that no person has touched
    this action — the test that decides whether the register may speak about the
    Matter at all — so the reason it was null has to be recorded beside it
    rather than inferred from its absence (brief 19).
    """
    refusal = "Ainult kehtivat tegevust saab tühistada."
    action = _lock_for_transition(action, refusal)
    if action.status not in (ActionStatus.OPEN, ActionStatus.PLANNED):
        raise DomainError(refusal)

    action.status = ActionStatus.CANCELLED
    action.ended_at = timezone.now()
    action.ended_by = actor
    action.save(update_fields=["status", "ended_at", "ended_by", "updated_at"])

    payload: dict[str, Any] = {"reason": reason[:500]}
    if provenance:
        # Nested, so a provenance key can never shadow `reason` and quietly
        # change what the event says — the same rule `set_next_action` follows.
        payload["provenance"] = provenance
    if action.follow_up_id is not None:
        # A check cancelled by closure or by its opinion leaving: the watching
        # of that opinion ends with it, and the event names the opinion
        # (docs/adr/0146 §8).
        from app.workflow.follow_ups import end_follow_up_for_cancelled_check

        end_follow_up_for_cancelled_check(action=action, actor=actor, reason=reason)
        payload["follow_up"] = _follow_up_block(action)

    record_change_event(
        event_type=ChangeEventType.NEXT_ACTION_CANCELLED,
        matter=action.matter,
        actor=actor,
        obj=action,
        summary=action.text[:200],
        payload=payload,
    )
    return action


@transaction.atomic
def end_open_action_for_closure(*, matter: Any, actor: Any = None) -> NextAction | None:
    """Close out the current action **and every planned one** when the Matter closes.

    A closed Matter with a live `Järgmiseks` — or a queued future one — would
    keep appearing in someone's work list forever. Each is cancelled through
    `cancel_next_action`, so each stays in the history (docs/adr/0143).
    """
    for planned in NextAction.objects.filter(matter=matter, status=ActionStatus.PLANNED).order_by(
        "target_date", "created_at", "pk"
    ):
        cancel_next_action(action=planned, actor=actor, reason="Teema suleti")
    action = current_next_action(matter)
    if action is None:
        return None
    return cancel_next_action(action=action, actor=actor, reason="Teema suleti")


@transaction.atomic
def acknowledge_review(
    *,
    action: NextAction,
    actor: Any = None,
    next_review_date: date | None = None,
    date_precision: str = DatePrecision.EXACT,
    note: str = "",
) -> NextAction:
    """Record that a WAIT or MONITOR was looked at, and when to look again.

    Without this, a review date that has passed leaves the row permanently
    "ripe": the only way to clear it is to edit the date, which looks like
    changing the plan rather than doing the work of checking. Reviewing is not
    completing — the Matter is still waiting on the same thing — so the action
    stays open and keeps its identity.

    On the locked row, like completing and cancelling: a review recorded on an
    action that a replacement has just superseded would be a REVIEWED event on a
    step nobody is following any more (ENG-072).

    **A review that would leave the step exactly as it is records nothing.**
    The lock serialises a double submit, but a transition that stays OPEN is
    not refused by the status check the way a second completion is — so the
    second POST of the same `Vaatasin üle` used to find the date it had just
    set, set it again, and write a second NEXT_ACTION_REVIEWED for one look at
    the file. Compared on the locked row, date and precision both, and answered
    with the action rather than a refusal: the person's review did land, once
    (ENG-021).
    """
    refusal = "Ainult kehtivat tegevust saab üle vaadata."
    action = _lock_for_transition(action, refusal)
    if action.status != ActionStatus.OPEN:
        raise DomainError(refusal)
    if action.kind not in REVIEW_KINDS:
        raise DomainError("Üle vaadata saab ainult ootamist või jälgimist.")
    refuse_an_unsupported_precision(next_review_date, date_precision)
    if (action.target_date, action.date_precision) == (next_review_date, date_precision):
        return action

    previous = action.target_date
    action.target_date = next_review_date
    action.date_precision = date_precision
    action.save(update_fields=["target_date", "date_precision", "updated_at"])

    record_change_event(
        event_type=ChangeEventType.NEXT_ACTION_REVIEWED,
        matter=action.matter,
        actor=actor,
        obj=action,
        summary=note[:200] or action.text[:200],
        payload={
            "from": previous.isoformat() if previous else None,
            "to": next_review_date.isoformat() if next_review_date else None,
            "kind": action.kind,
        },
    )
    return action


# ---------------------------------------------------------------------------
# Planned actions (docs/adr/0143)
# ---------------------------------------------------------------------------
#
# A Matter has at most one current action (`OPEN`) and any number of dated
# future ones (`PLANNED`). A planned action is never the current one: it is
# written beside it, changed and cancelled on its own, and becomes current only
# when the current one is completed — the earliest first. Every write locks the
# Matter, the order `set_next_action` and the transitions above already use.

#: Refused when a planned action has no day: planning is planning on days.
PLANNED_ACTION_NEEDS_DATE = "Planeeritud tegevusel peab olema kuupäev."
#: Refused when the planned action a form names is no longer planned.
PLANNED_ACTION_CHANGED = (
    "See planeeritud tegevus on vahepeal muutunud. Värskenda lehte ja vaata uuesti."
)


def _refuse_a_follow_up_check(action: NextAction) -> None:
    """The generic controls do not finish, rewrite or remove a follow-up check.

    `✓ Tehtud`, `Muuda`, `×` and a ticked `Märgi praegune tegevus tehtuks` would
    each end or reshape a check without saying whether the addressee answered.
    The check's own form asks that (`app.workflow.follow_ups`, docs/adr/0146 §5).
    """
    if action.follow_up_id is not None:
        from app.workflow.follow_ups import FOLLOW_UP_HAS_ITS_OWN_FORM

        raise DomainError(FOLLOW_UP_HAS_ITS_OWN_FORM)


def _follow_up_block(action: NextAction) -> dict[str, str]:
    """Which follow-up, and which opinion, an event about a check concerns."""
    from app.workflow.models import OpinionFollowUp

    submission_id = (
        OpinionFollowUp.objects.filter(pk=cast(Any, action.follow_up_id))
        .values_list("submission_id", flat=True)
        .first()
    )
    return {"id": str(action.follow_up_id), "submission": str(submission_id)}


def _locked_planned(locked_matter: Any, action_id: Any) -> NextAction:
    """The named planned action of this Matter, locked — or a refusal (stale tab)."""
    action = (
        NextAction.objects.select_for_update(no_key=True)
        .filter(matter=locked_matter, pk=action_id, status=ActionStatus.PLANNED)
        .first()
    )
    if action is None:
        raise DomainError(PLANNED_ACTION_CHANGED)
    return action


@transaction.atomic
def add_planned_action(
    *,
    matter: Any,
    text: str,
    target_date: date | None,
    actor: Any = None,
    responsible: Any = None,
) -> NextAction:
    """`+ Määra järgmine tegevus` beside a current action: a dated future one.

    Never supersedes the current action or another planned one. `DO` /
    `DEADLINE` / `EXACT`, like every step a person writes; the departed-owner
    rule is the one new work follows. Audited as `NEXT_ACTION_SET` with
    ``planned`` in the payload.
    """
    from app.matters.locks import lock_matter_for_write

    text = (text or "").strip()
    if not text:
        raise DomainError("Järgmiseks vajab teksti.")
    if target_date is None:
        raise DomainError(PLANNED_ACTION_NEEDS_DATE)
    locked_matter = lock_matter_for_write(matter.pk)
    if not locked_matter.is_open:
        raise DomainError("Suletud teemale ei saa järgmist tegevust määrata.")
    action = NextAction.objects.create(
        matter=locked_matter,
        text=text,
        kind=ActionKind.DO,
        date_semantics=DateSemantics.DEADLINE,
        target_date=target_date,
        date_precision=DatePrecision.EXACT,
        status=ActionStatus.PLANNED,
        responsible=responsible_for_new_work(matter=locked_matter, explicit=responsible)
        or locked_matter.owner,
        created_by=actor,
    )
    record_change_event(
        event_type=ChangeEventType.NEXT_ACTION_SET,
        matter=locked_matter,
        actor=actor,
        obj=action,
        summary=text[:200],
        payload={
            "kind": action.kind,
            "date_semantics": action.date_semantics,
            "target_date": target_date.isoformat(),
            "replaced": None,
            "planned": True,
        },
    )
    return action


@transaction.atomic
def change_planned_action(
    *, matter: Any, action_id: Any, text: str, target_date: date | None, actor: Any = None
) -> NextAction:
    """`Muuda` on a planned row — that exact action, said differently.

    Superseded by a new planned row, as `Muuda` on the current action is, so
    the history keeps both. Keeps its responsible person and its restriction;
    the current action is untouched. A stale tab naming an action no longer
    planned refuses with nothing written.
    """
    from app.matters.locks import lock_matter_for_write

    text = (text or "").strip()
    if not text:
        raise DomainError("Järgmiseks vajab teksti.")
    if target_date is None:
        raise DomainError(PLANNED_ACTION_NEEDS_DATE)
    locked_matter = lock_matter_for_write(matter.pk)
    if not locked_matter.is_open:
        raise DomainError("Suletud teemale ei saa järgmist tegevust määrata.")
    previous = _locked_planned(locked_matter, action_id)
    # A check's day moves through `follow_ups.reschedule_check`, in place; its
    # words are not the person's to rewrite (docs/adr/0146 §6).
    _refuse_a_follow_up_check(previous)
    if (previous.text, previous.target_date) == (text, target_date):
        return previous
    action = NextAction.objects.create(
        matter=locked_matter,
        text=text,
        kind=previous.kind,
        date_semantics=previous.date_semantics,
        target_date=target_date,
        date_precision=DatePrecision.EXACT,
        status=ActionStatus.PLANNED,
        responsible=previous.responsible,
        created_by=actor,
        visibility_override=previous.visibility_override,
    )
    previous.status = ActionStatus.SUPERSEDED
    previous.ended_at = timezone.now()
    previous.ended_by = actor
    previous.replaced_by = action
    previous.save(update_fields=["status", "ended_at", "ended_by", "replaced_by", "updated_at"])
    record_change_event(
        event_type=ChangeEventType.NEXT_ACTION_SET,
        matter=locked_matter,
        actor=actor,
        obj=action,
        summary=text[:200],
        payload={
            "kind": action.kind,
            "date_semantics": action.date_semantics,
            "target_date": target_date.isoformat(),
            "replaced": str(previous.pk),
            "planned": True,
        },
    )
    return action


@transaction.atomic
def cancel_planned_action(*, matter: Any, action_id: Any, actor: Any = None) -> NextAction:
    """`×` on a planned row: that exact action leaves the plan. It stays in the history."""
    from app.matters.locks import lock_matter_for_write

    locked_matter = lock_matter_for_write(matter.pk)
    action = _locked_planned(locked_matter, action_id)
    # `×` is not how an unanswered opinion stops being watched: that is
    # `Lõpetan jälgimise`, with a reason (docs/adr/0146 §5).
    _refuse_a_follow_up_check(action)
    return cancel_next_action(action=action, actor=actor, reason="Planeeritud tegevus eemaldati")


def locked_planned_action(locked_matter: Any, action_id: Any) -> NextAction:
    """The named planned action, row-locked under a Matter the caller holds — or a refusal.

    The public face of `_locked_planned` for a use case that writes more than
    the action itself (`✓ Tehtud`'s note and files) and has to ask before it
    writes any of it.
    """
    return _locked_planned(locked_matter, action_id)


@transaction.atomic
def finish_planned_action(*, action: NextAction, actor: Any = None) -> NextAction:
    """`✓ Tehtud` on a planned row — that exact action is done (docs/adr/0144 §1).

    On any day: before its date, on it or after it. The action ends COMPLETED
    with `NEXT_ACTION_COMPLETED`, exactly as the current action does, with
    ``planned`` in the payload so the history tells the two apart.

    **It finishes this action and nothing else.** The current action keeps its
    text and its date, no other planned row is promoted or touched — promotion
    happens when the *current* action is completed, never here (docs/adr/0143
    §A4) — and ``action`` must already be the locked row `locked_planned_action`
    returned, so a stale tab naming a row that is no longer planned was refused
    before anything was written.
    """
    if action.status != ActionStatus.PLANNED:
        raise DomainError(PLANNED_ACTION_CHANGED)
    _refuse_a_follow_up_check(action)
    action.status = ActionStatus.COMPLETED
    action.ended_at = timezone.now()
    action.ended_by = actor
    action.save(update_fields=["status", "ended_at", "ended_by", "updated_at"])
    record_change_event(
        event_type=ChangeEventType.NEXT_ACTION_COMPLETED,
        matter=action.matter,
        actor=actor,
        obj=action,
        summary=action.text[:200],
        payload={"kind": action.kind, "planned": True},
    )
    return action


@transaction.atomic
def promote_next_planned_action(*, matter: Any, actor: Any = None) -> NextAction | None:
    """The earliest planned action becomes current — if nothing is current.

    Called inside the completion's transaction, under the Matter's lock. Order:
    the earliest day, then the order it was planned in; never by its words.
    The row keeps its text, day, responsible person and restriction; only its
    status moves, and `NEXT_ACTION_SET` with ``promoted`` records it.
    """
    if NextAction.objects.filter(matter=matter, status=ActionStatus.OPEN).exists():
        return None
    action = (
        NextAction.objects.select_for_update(no_key=True)
        .filter(matter=matter, status=ActionStatus.PLANNED)
        .order_by("target_date", "created_at", "pk")
        .first()
    )
    if action is None:
        return None
    action.status = ActionStatus.OPEN
    action.save(update_fields=["status", "updated_at"])
    record_change_event(
        event_type=ChangeEventType.NEXT_ACTION_SET,
        matter=action.matter,
        actor=actor,
        obj=action,
        summary=action.text[:200],
        payload={
            "kind": action.kind,
            "date_semantics": action.date_semantics,
            "target_date": action.target_date.isoformat() if action.target_date else None,
            "replaced": None,
            "promoted": True,
        },
    )
    return action
