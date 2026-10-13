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

from dataclasses import dataclass
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


def step_already_recorded(matter: Any, current_action: Any, viewer: Any) -> ConsistencyAlert | None:
    """The current step is a standard step the record proves done since it was set.

    The predicate, every clause of which is a structured fact:

    * the Matter is open and a full record (an archive row is history, not work);
    * ``current_action`` is this reader's current step (`OPEN`), a `DO` — a
      wait or a watch is somebody else's move, not a task the record finishes —
      and not an `Arvamuse järelkontroll`, which has its own typed outcome;
    * it was started from a plan step (`plan_step`; imported, legacy and
      hand-written steps never carry one);
    * `RecordedWork.proves` that step, counting only records this reader may
      see that were entered after the step was set (`since=created_at`).
    """
    from app.matters.enums import RecordMode
    from app.matters.plan_view import RecordedWork

    if current_action is None or not matter.is_open or matter.record_mode != RecordMode.FULL:
        return None
    if current_action.status != ActionStatus.OPEN or current_action.kind != ActionKind.DO:
        return None
    if current_action.follow_up_id is not None or current_action.plan_step_id is None:
        return None
    step = current_action.plan_step
    if step.matter_id != matter.pk:
        return None
    done = RecordedWork.of(matter, viewer, since=current_action.created_at)
    if not done.proves(step):
        return None
    return ConsistencyAlert(
        key=STEP_ALREADY_RECORDED,
        title=STEP_ALREADY_RECORDED_TITLE,
        reason=_proof_sentence(step, done),
        remedy=STEP_ALREADY_RECORDED_REMEDY,
    )


def current_work_alerts(matter: Any, current_action: Any, viewer: Any) -> list[ConsistencyAlert]:
    """Every workflow-consistency alert for this Matter's `PRAEGUNE TEGEVUS`, for this reader."""
    alert = step_already_recorded(matter, current_action, viewer)
    return [alert] if alert is not None else []
