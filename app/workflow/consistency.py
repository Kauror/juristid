"""Workflow-consistency alerts — read-only diagnostics beside the work (docs/adr/0161).

Juristid already guards most of a Matter's workflow where the work happens, and
this module does not repeat any of it:

* a Matter with no next step is «järgmine samm määramata» on `Vajab sekkumist`,
  «järgmise tegevuseta» on Minu asjad and the register, and a queue on
  Andmekvaliteet (`app.matters.next_step.without_next_step`);
* a late step, a ripe review, a late check and a late feedback wait are banded
  red and listed on `Vajab sekkumist` (`app.matters.work_items`);
* a sent opinion schedules its own thirty-day check, and
  `check_domain_invariants` reports a check that went missing (docs/adr/0146);
* closing a Teema confirms every piece of live work it would end
  (docs/adr/0152).

What none of them sees is a **current step that the record already shows was
done**. Since docs/adr/0141 §2 no record fulfils a step: a lawyer who pressed
`Alusta` on «Kaasa liikmeid / küsi tagasisidet» and then filed the `Kaasamine`
through `LISA TEEMALE` still has «Kaasa liikmeid» as `PRAEGUNE TEGEVUS`, and
nothing on the page says so. `Järgmisena?` already knows the step is done — it
skips it (docs/adr/0144 §2) — but only while nothing is current.

**The rule is that one, asked of the current step.** The step is identified by
the explicit `NextAction.plan_step` relation and decided by
`app.matters.plan_view.RecordedWork.proves` — the stable `template_step_key` /
`operation`, never the step's words — narrowed to records entered *after the
current step was set*. No second suggestion engine, no text, no inference.

**Nothing is written.** The alert does not complete, move or rewrite the step,
and it creates nothing. It says why it appears and what can be done with the
controls already beside it: `✓ Tehtud` if it is done, `Muuda` if the work goes
on — and `Muuda` sets the step anew, after the record, so the alert does not
come back about the same records.

**Never more than the reader may see.** The step is the one
`selectors.current_action_of` returns — reader-scoped — and every proof is read
through its record's own `visible_to`. A reader who may not see the Kaasamine
is not told, by an alert, that one exists.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass, replace
from datetime import datetime
from typing import Any

from app.workflow.enums import ActionKind, ActionStatus, PlanStepOperation

#: The one alert this module raises (docs/adr/0161 §2).
STEP_ALREADY_RECORDED = "step-already-recorded"

STEP_ALREADY_RECORDED_TITLE = "Praegune tegevus võib olla juba tehtud"

#: What the controls beside the step can do about it. Both exist already.
STEP_ALREADY_RECORDED_REMEDY = (
    "Kui see on tehtud, vajuta «✓ Tehtud». Kui töö jätkub, "
    "täpsusta tegevust nupuga «Muuda» — siis see teade kaob."
)


@dataclass(frozen=True)
class ConsistencyAlert:
    """One deterministic finding about one Matter's current work, for one reader."""

    key: str
    title: str
    #: Why it appears — the structured fact, in the product's words.
    reason: str
    #: What a person can do about it with an existing control.
    remedy: str


_SINCE = "Pärast selle tegevuse määramist"
PROOF_PUBLISHED_ANY = f"{_SINCE} on teemale lisatud avaldatud kodulehe ülevaade või uudis."
PROOF_OVERVIEW = f"{_SINCE} on teemale lisatud avaldatud kodulehe ülevaade."
PROOF_ENGAGEMENT = f"{_SINCE} on teemale lisatud kaasamine."
PROOF_FEEDBACK = f"{_SINCE} on kaasamise tagasiside kirja pandud."
PROOF_OPINION = f"{_SINCE} on teemale lisatud saadetud Koja arvamus."


def _proof_sentence(step: Any, done: Any) -> str:
    """Which recorded fact proves the step, in the order `RecordedWork.proves` asks."""
    key = step.template_step_key
    if key == "read-material":
        if done.published_any:
            return PROOF_PUBLISHED_ANY
        return PROOF_ENGAGEMENT if done.engagement else PROOF_OPINION
    if key == "form-position":
        return PROOF_FEEDBACK if done.feedback_recorded else PROOF_OPINION
    if step.operation == PlanStepOperation.WEBSITE_OVERVIEW:
        return PROOF_OVERVIEW
    if step.operation == PlanStepOperation.ENGAGEMENT:
        return PROOF_ENGAGEMENT
    return PROOF_OPINION


@dataclass(frozen=True)
class CurrentStep:
    """The facts the rule reads of one current step — from a `NextAction` or a work row.

    A work list holds `WorkItem` rows, not `NextAction` objects, so the rule
    reads these seven facts rather than either shape: the Teema page builds
    one from its `NextAction` (:meth:`of_action`), Minu asjad and Osakond from
    the row they already render (:meth:`of_item`). Nothing else is read.
    """

    matter: Any
    action_id: Any
    is_current: bool
    kind: str
    is_follow_up: bool
    plan_step_id: Any
    set_at: datetime | None

    @classmethod
    def of_action(cls, matter: Any, action: Any) -> CurrentStep:
        return cls(
            matter=matter,
            action_id=action.pk,
            is_current=action.status == ActionStatus.OPEN,
            kind=action.kind,
            is_follow_up=action.follow_up_id is not None,
            plan_step_id=action.plan_step_id,
            set_at=action.created_at,
        )

    @classmethod
    def of_item(cls, item: Any) -> CurrentStep | None:
        """A work row's step, or ``None`` for a row that is not a `NextAction`."""
        if not item.is_action:
            return None
        return cls(
            matter=item.matter,
            action_id=item.object_id,
            # A work row is a live action: `OPEN` — the Matter's current step,
            # of which there is one (`workflow_one_open_action_per_matter`) —
            # or `PLANNED`.
            is_current=not item.is_planned,
            kind=item.action_kind,
            is_follow_up=item.is_follow_up,
            plan_step_id=item.plan_step_id,
            set_at=item.set_at,
        )

    @property
    def may_be_judged(self) -> bool:
        """Every clause of the rule that needs no read (§1)."""
        from app.matters.enums import RecordMode

        return (
            bool(self.matter.is_open)
            and self.matter.record_mode == RecordMode.FULL
            and self.is_current
            and self.kind == ActionKind.DO
            and not self.is_follow_up
            and self.plan_step_id is not None
            and self.set_at is not None
        )


def steps_already_recorded(
    steps: Iterable[CurrentStep | None],
    viewer: Any,
    *,
    plan_steps: Mapping[Any, Any] | None = None,
) -> dict[Any, ConsistencyAlert]:
    """The rule, asked of many current steps at once: action id → alert.

    The predicate, every clause of which is a structured fact:

    * the Matter is open and a full record (an archive row is history, not work);
    * the step is this reader's current step (`OPEN`), a `DO` — a wait or a
      watch is somebody else's move, not a task the record finishes — and not
      an `Arvamuse järelkontroll`, which has its own typed outcome;
    * it was started from a plan step (`plan_step`; imported, legacy and
      hand-written steps never carry one);
    * `RecordedWork.proves` that step, counting only records this reader may
      see that were entered after the step was set (`since=created_at`).

    **A constant number of reads, however many steps** (docs/adr/0161,
    amendment of 2026-11-25): none when no step passes the clauses above;
    otherwise one for the plan steps not handed in as ``plan_steps`` and
    `RecordedWork.for_matters`' grouped reads. The single Teema reading
    (:func:`step_already_recorded`) is this function asked about one step, so
    a work row and the Teema page cannot disagree.
    """
    from app.matters.plan_view import RecordedWork
    from app.workflow.models import MatterPlanStep

    candidates = [step for step in steps if step is not None and step.may_be_judged]
    if not candidates:
        return {}
    known: dict[Any, Any] = dict(plan_steps or {})
    missing = {step.plan_step_id for step in candidates} - set(known)
    if missing:
        known.update(
            (row.pk, row)
            for row in MatterPlanStep.objects.filter(pk__in=missing).only(
                "matter_id", "template_step_key", "operation"
            )
        )
    judged = [
        (step, known[step.plan_step_id])
        for step in candidates
        if step.plan_step_id in known and known[step.plan_step_id].matter_id == step.matter.pk
    ]
    if not judged:
        return {}
    done = RecordedWork.for_matters(
        [step.matter for step, _ in judged],
        viewer,
        since={step.matter.pk: step.set_at for step, _ in judged},
    )
    alerts: dict[Any, ConsistencyAlert] = {}
    for step, plan_step in judged:
        recorded = done[step.matter.pk]
        if recorded.proves(plan_step):
            alerts[step.action_id] = ConsistencyAlert(
                key=STEP_ALREADY_RECORDED,
                title=STEP_ALREADY_RECORDED_TITLE,
                reason=_proof_sentence(plan_step, recorded),
                remedy=STEP_ALREADY_RECORDED_REMEDY,
            )
    return alerts


def step_already_recorded(matter: Any, current_action: Any, viewer: Any) -> ConsistencyAlert | None:
    """The current step is a standard step the record proves done since it was set.

    :func:`steps_already_recorded` asked about this one step — the Teema page's
    `PRAEGUNE TEGEVUS`. A plan step already loaded on the action is handed in,
    so the page pays no read for it.
    """
    from app.workflow.models import NextAction

    if current_action is None:
        return None
    loaded = (
        {current_action.plan_step_id: current_action.plan_step}
        if current_action.plan_step_id is not None
        and NextAction.plan_step.is_cached(current_action)
        else None
    )
    alerts = steps_already_recorded(
        [CurrentStep.of_action(matter, current_action)], viewer, plan_steps=loaded
    )
    return alerts.get(current_action.pk)


def current_work_alerts(matter: Any, current_action: Any, viewer: Any) -> list[ConsistencyAlert]:
    """Every workflow-consistency alert for this Matter's `PRAEGUNE TEGEVUS`, for this reader."""
    alert = step_already_recorded(matter, current_action, viewer)
    return [alert] if alert is not None else []


def with_consistency_alerts(items: list[Any], viewer: Any) -> list[Any]:
    """Work rows with the alert set on the row of each flagged current step.

    For Minu asjad (docs/adr/0161, amendment of 2026-11-25): the rows are the
    ones the page already read, one `WorkItem` per step; the flagged ones are
    copied with ``consistency_alert`` set and every other row is returned as
    it was, in the same order. Nothing is counted, sorted or dropped by it.
    """
    alerts = steps_already_recorded([CurrentStep.of_item(item) for item in items], viewer)
    if not alerts:
        return items
    return [
        replace(item, consistency_alert=alerts[item.object_id])
        if item.is_action and item.object_id in alerts
        else item
        for item in items
    ]
