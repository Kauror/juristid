"""What a step change's audit row says about who chose it (docs/adr/0159 §2, D1).

The workflow services write, in the payload of the `ChangeEvent` they record,
a marker on every step change nobody chose: the follow-up check a send
schedules, a planned step made current, a check put back on the plan, a check's
day recalculated, a check ended because its opinion was withdrawn or replaced,
and every step a closure ends. Two readers tell those rows from a person's
act — Osakond's *Muudatused* (`app.matters.department_changes`) and Minu
asjad's *Viimased tegevused* (`app.matters.recent_activity`) — and both read
the markers here, beside the writers, so that a writer and its readers cannot
drift apart: change the closure reason in one place and every surface reads
the new one (2026-10-28 audit, A-02/A-03).

This module only names markers and the predicates that recognise them. It
writes nothing and reads nothing by itself.
"""

from __future__ import annotations

from django.db.models import Q

from app.audit.enums import ChangeEventType

__all__ = [
    "CLOSED_WITH_MATTER",
    "PROMOTED",
    "RECALCULATED",
    "RETURNED_TO_PLAN",
    "SUPERSEDED_OPINION",
    "WITHDRAWN_OPINION",
    "automatic_step_q",
    "closure_cancelled_q",
    "system_step_change_q",
]

#: The reason written on every step, and on a follow-up's monitoring, that a
#: closure ends (`workflow.services.end_open_action_for_closure`,
#: `workflow.follow_ups`).
CLOSED_WITH_MATTER = "Teema suleti"
#: The reasons written when an opinion's withdrawal or replacement ends its check.
WITHDRAWN_OPINION = "Arvamus võeti tagasi"
SUPERSEDED_OPINION = "Arvamus asendati"

#: Payload keys a service sets on a step change it made on its own.
#: ``returned_to_plan``: ``true`` on the check put back on the plan; the new
#: work's own event carries the check's id as a string and stays a person's act.
RETURNED_TO_PLAN = "returned_to_plan"
#: ``true`` on a planned step made current because the current one was finished.
PROMOTED = "promoted"
#: ``true`` on a check's day recalculated from a corrected send date.
RECALCULATED = "recalculated"


def automatic_step_q() -> Q:
    """Step changes a service made on its own, which nobody chose.

    * the first opinion follow-up check, scheduled by recording the send
      (``follow_up.automatic``, docs/adr/0146 §10) — the send is the act;
    * a current check put back on the plan because new work was written over
      it (``returned_to_plan: true``);
    * a planned step made current because the current one was finished
      (``promoted``) — it was set once, when it was planned, and the finishing
      is its own act;
    * a check's day recalculated from a corrected send date (``recalculated``);
    * a check cancelled because its opinion was withdrawn or replaced (reason
      `WITHDRAWN_OPINION` / `SUPERSEDED_OPINION` with the ``follow_up`` block) —
      the withdrawal or replacement is the person's act, the check ending its
      consequence.
    """
    # JSON containment (`@>`), never a key lookup: a missing key compares NULL,
    # and `exclude()` over a NULL drops the row it should keep.
    return (
        Q(
            event_type=ChangeEventType.NEXT_ACTION_SET,
            payload__contains={"follow_up": {"automatic": True}},
        )
        | Q(
            event_type=ChangeEventType.NEXT_ACTION_SET,
            payload__contains={RETURNED_TO_PLAN: True},
        )
        | Q(event_type=ChangeEventType.NEXT_ACTION_SET, payload__contains={PROMOTED: True})
        | Q(
            event_type=ChangeEventType.NEXT_ACTION_RESCHEDULED,
            payload__contains={RECALCULATED: True},
        )
        | Q(
            event_type=ChangeEventType.NEXT_ACTION_CANCELLED,
            payload__contains={"reason": WITHDRAWN_OPINION},
            payload__has_key="follow_up",
        )
        | Q(
            event_type=ChangeEventType.NEXT_ACTION_CANCELLED,
            payload__contains={"reason": SUPERSEDED_OPINION},
            payload__has_key="follow_up",
        )
    )


def closure_cancelled_q() -> Q:
    """A step ended because its Matter was closed, not cancelled on its own.

    The reason the closure writes (`CLOSED_WITH_MATTER`); a ``closure`` key,
    should a closure writer come to mark its cancellations explicitly, is read
    the same way.
    """
    return Q(event_type=ChangeEventType.NEXT_ACTION_CANCELLED) & (
        Q(payload__contains={"reason": CLOSED_WITH_MATTER}) | Q(payload__has_key="closure")
    )


def system_step_change_q() -> Q:
    """Every step change that is the consequence of another act, not an act itself."""
    return automatic_step_q() | closure_cancelled_q()
