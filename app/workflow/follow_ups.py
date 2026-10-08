"""`Arvamuse järelkontroll` — checking on a sent opinion (docs/adr/0146).

When the Chamber records an opinion as sent, its work on that opinion is not
over: somebody has to find out whether the addressee answered. Before this, a
sent opinion left the department's active work the moment it went out, and
whether anybody looked again depended on memory.

**One canonical place.** Every interactive act that records a send —
`Märgi saadetuks`, `Registreeri saatmine`, `+ Koja arvamus` — reaches
`schedule_first_check` through the two open-Matter wrappers in
`app.submissions.services`, and nothing else calls it. The archive apply and the
register importers write their sends below those wrappers and schedule nothing:
a letter from 2019 does not become a task (docs/adr/0146 §9).

**Not a second task engine.** A check is an ordinary dated `NextAction` —
`PLANNED` beside whatever is current, promoted by the established rule, moved
with the file, shown by every work surface — that points at the
`OpinionFollowUp` row of its opinion. What this module adds is the rules no
generic action has:

* the first check is due **30 calendar days** after the recorded sending date,
  never moved for a weekend or a holiday;
* its day can always be moved, in place — the same check, its opinion, its
  responsible person and its restriction kept, the automatic day still on the
  follow-up row and the move in the audit trail;
* finishing it needs a **typed outcome**: an answer arrived, no answer yet and
  the next check's day (chosen by the lawyer, never another automatic 30 days),
  or monitoring ends with a written reason. Nothing makes an unanswered opinion
  disappear silently: the generic `✓ Tehtud`, `Muuda` and `×` refuse a check.

Every write here locks the Matter first, then the submission or the action, the
one order every other service follows (`app/matters/locks.py`).
"""

from __future__ import annotations

from datetime import date, timedelta
from typing import Any, cast

from django.db import transaction
from django.db.models import QuerySet
from django.utils import timezone

from app.audit.enums import ChangeEventType
from app.audit.services import record_change_event
from app.core.enums import Visibility
from app.core.errors import DomainError
from app.workflow.enums import (
    ActionKind,
    ActionStatus,
    DatePrecision,
    DateSemantics,
    FollowUpOutcome,
    FollowUpState,
)
from app.workflow.models import NextAction, OpinionFollowUp

#: Calendar days from the recorded sending date to the first check. Not
#: business days: the owner's rule is «30 days after», and a check that moves
#: with the calendar is one a lawyer can predict (docs/adr/0146 §2).
FOLLOW_UP_DAYS = 30

#: The check's own words. The addressees are shown beside it, never folded in:
#: a sentence that named one ministry would be wrong for a letter to three.
FOLLOW_UP_TEXT_ONE = "Kontrolli, kas adressaat on Koja arvamusele vastanud"
FOLLOW_UP_TEXT_MANY = "Kontrolli, kas adressaadid on Koja arvamusele vastanud"

#: A form named a check that is no longer planned or current (stale tab).
FOLLOW_UP_CHANGED = "See järelkontroll on vahepeal muutunud. Värskenda lehte ja vaata uuesti."
FOLLOW_UP_NEEDS_DATE = "Vali järelkontrolli kuupäev."
FOLLOW_UP_DATE_IN_THE_PAST = "Järelkontrolli kuupäev ei saa olla minevikus."
FOLLOW_UP_NEEDS_OUTCOME = "Vali, mis selgus."
NEXT_CHECK_NEEDS_DATE = "Vali järgmise kontrolli kuupäev."
MONITORING_END_NEEDS_REASON = "Kirjuta, miks jälgimine lõpeb."
#: The generic controls — `✓ Tehtud`, `Muuda`, `×`, `Märgi praegune tegevus
#: tehtuks` — on a check. Each refuses with this, before anything is written.
FOLLOW_UP_HAS_ITS_OWN_FORM = (
    "See on Koja arvamuse järelkontroll. Märgi see tehtuks järelkontrolli real "
    "ja vali, kas vastus saabus."
)
#: What closing a Matter with a planned or current check is told, until the
#: person confirms it (docs/adr/0146 §8). The owner's own sentence.
FOLLOW_UP_CLOSURE_WARNING = (
    "Sellel teemal on pooleli Koja arvamuse järelkontroll. "
    "Teema sulgemisel lõpetatakse ka planeeritud järelkontroll."
)
#: The reasons written when something other than a check ends the monitoring.
CLOSED_WITH_MATTER = "Teema suleti"
WITHDRAWN_OPINION = "Arvamus võeti tagasi"
SUPERSEDED_OPINION = "Arvamus asendati"

ACTIVE = (ActionStatus.OPEN, ActionStatus.PLANNED)


class FollowUpClosureUnconfirmed(DomainError):
    """A closure that would end a check nobody confirmed ending.

    Its own class so a view can tell it from every other refusal and draw the
    confirmation box beside the warning, rather than a bare error.
    """

    def __init__(self) -> None:
        super().__init__(FOLLOW_UP_CLOSURE_WARNING)


def follow_up_text(addressee_count: int) -> str:
    """The check's sentence: one addressee, or several."""
    return FOLLOW_UP_TEXT_MANY if addressee_count > 1 else FOLLOW_UP_TEXT_ONE


def sent_on_of(sent_at: Any) -> date:
    """The business day a recorded send fell on, in Europe/Tallinn.

    The same reading every surface makes of `Submission.sent_at`: a day-only
    send is stored as Tallinn midnight and reads back as that day, and a
    timestamped one (`Märgi saadetuks`) as the Tallinn day it was pressed on.
    """
    return timezone.localdate(sent_at)


def first_check_day(sent_on: date) -> date:
    """`sent_on` + 30 calendar days. 8 October → 7 November."""
    return sent_on + timedelta(days=FOLLOW_UP_DAYS)


def _restriction_of(submission: Any) -> str:
    """The check is never less restricted than its opinion (docs/adr/0146 §10).

    Copied at creation, like every restriction a derived record carries
    (docs/adr/0138): an opinion restricted below its Matter gives its check the
    same restriction, so the check's words, its date and every event about it
    reach exactly the readers the opinion does.
    """
    return Visibility.RESTRICTED if submission.visibility_override == Visibility.RESTRICTED else ""


def _addressee_count(submission: Any) -> int:
    from app.submissions.enums import RecipientRole

    return submission.recipient_rows.filter(role=RecipientRole.ADDRESSEE).count()


def _block(follow_up: OpinionFollowUp, **extra: Any) -> dict[str, Any]:
    """The `follow_up` part of an audit payload: which opinion, and what else."""
    return {"id": str(follow_up.pk), "submission": str(follow_up.submission_id), **extra}


def _completed_checks(follow_up: OpinionFollowUp) -> int:
    return NextAction.objects.filter(follow_up=follow_up, status=ActionStatus.COMPLETED).count()


def pending_checks(matter: Any) -> QuerySet[NextAction]:
    """The planned or current checks on this Matter. **Reader-blind.**

    A domain question asked by closure, which has to end every one of them
    whoever may see them; a page asks `NextAction.objects.visible_to` instead.
    """
    return NextAction.objects.filter(
        matter_id=getattr(matter, "pk", matter), follow_up__isnull=False, status__in=ACTIVE
    )


# ---------------------------------------------------------------------------
# Scheduling the first check
# ---------------------------------------------------------------------------


@transaction.atomic
def schedule_first_check(*, submission: Any, actor: Any = None) -> NextAction | None:
    """The first check of a just-sent opinion, 30 calendar days after its sending date.

    Called by the interactive send wrappers, inside the send's own transaction,
    after `mark_submission_sent` has stamped it — so a refused send schedules
    nothing and a refusal here rolls the send back with it (docs/adr/0146 §1).

    * **A `PLANNED` action**, beside whatever is current. It never supersedes
      the current action and touches no other planned one; it becomes current
      only by the established promotion (docs/adr/0143 §A4).
    * **Due on the recorded sending date + 30**, from the date the send was
      recorded with — a letter registered today as sent two months ago gets a
      check that is already overdue, on purpose.
    * **The Matter's owner is responsible** — not whoever pressed the button —
      so the check follows the file when it changes hands.
    * **Restricted with the opinion** when the opinion is restricted.
    * **Once per opinion.** The one-to-one `OpinionFollowUp.submission` refuses
      a second in the database; under the locks taken here a repeated call
      finds the first and writes nothing.

    Refuses a closed Matter: no live check is ever created on one, whatever the
    path (docs/adr/0146 §10).
    """
    from app.matters.locks import (
        lock_open_matter_for_business_write,
        lock_submission_for_evidence_integrity,
    )
    from app.submissions.enums import SubmissionStatus

    matter = lock_open_matter_for_business_write(submission.matter_id)
    locked = lock_submission_for_evidence_integrity(submission.pk)
    if locked.status != SubmissionStatus.SENT or locked.sent_at is None:
        raise DomainError("Järelkontrolli saab määrata ainult saadetud arvamusele.")
    existing = OpinionFollowUp.objects.filter(submission=locked).first()
    if existing is not None:
        return NextAction.objects.filter(follow_up=existing, status__in=ACTIVE).first()

    sent_on = sent_on_of(locked.sent_at)
    due = first_check_day(sent_on)
    follow_up = OpinionFollowUp.objects.create(
        submission=locked, sent_on=sent_on, first_due_on=due, created_by=actor
    )
    text = follow_up_text(_addressee_count(locked))
    action = NextAction.objects.create(
        matter=matter,
        text=text,
        kind=ActionKind.DO,
        date_semantics=DateSemantics.DEADLINE,
        target_date=due,
        date_precision=DatePrecision.EXACT,
        status=ActionStatus.PLANNED,
        responsible=matter.owner,
        created_by=actor,
        visibility_override=_restriction_of(locked),
        follow_up=follow_up,
    )
    record_change_event(
        event_type=ChangeEventType.NEXT_ACTION_SET,
        matter=matter,
        actor=actor,
        obj=action,
        summary=text[:200],
        payload={
            "kind": action.kind,
            "date_semantics": action.date_semantics,
            "target_date": due.isoformat(),
            "replaced": None,
            "planned": True,
            # **Scheduled by the system, not assigned by a person**: the actor is
            # whoever recorded the send, and this says the check was not their
            # choice of day (docs/adr/0146 §10).
            "follow_up": _block(follow_up, automatic=True, check=1, sent_on=sent_on.isoformat()),
        },
    )
    return action


# ---------------------------------------------------------------------------
# Moving and finishing a check
# ---------------------------------------------------------------------------


def locked_check(locked_matter: Any, action_id: Any) -> NextAction:
    """The named planned or current check on this Matter, row-locked — or a refusal.

    The caller holds the Matter's lock. A tab that still shows a check somebody
    has since finished, moved past or cancelled is refused with nothing written.
    """
    action = (
        NextAction.objects.select_for_update(no_key=True)
        .filter(
            matter=locked_matter,
            pk=action_id,
            follow_up__isnull=False,
            status__in=ACTIVE,
        )
        .first()
    )
    if action is None:
        raise DomainError(FOLLOW_UP_CHANGED)
    # The Matter row this transaction already holds.
    action.matter = locked_matter
    return action


def _refuse_a_past_day(day: date | None, missing: str) -> date:
    if day is None:
        raise DomainError(missing)
    if day < timezone.localdate():
        raise DomainError(FOLLOW_UP_DATE_IN_THE_PAST)
    return day


@transaction.atomic
def reschedule_check(
    *, matter: Any, action_id: Any, target_date: date | None, actor: Any = None
) -> NextAction:
    """Move a check to another day — earlier or later, before it is due or long after.

    **The same check, in place** (docs/adr/0146 §6). Its identity, its opinion,
    its responsible person and its restriction stay; only `target_date` moves,
    and `NEXT_ACTION_RESCHEDULED` keeps where it was. The automatic first day
    stays on the follow-up row. Planned or current alike. A day already
    behind us is refused: a check moved into the past is overdue by choice.
    A day equal to the one it has writes nothing.
    """
    from app.matters.locks import lock_open_matter_for_business_write

    day = _refuse_a_past_day(target_date, FOLLOW_UP_NEEDS_DATE)
    locked_matter = lock_open_matter_for_business_write(matter.pk)
    action = locked_check(locked_matter, action_id)
    if action.target_date == day:
        return action
    previous = action.target_date
    action.target_date = day
    action.save(update_fields=["target_date", "updated_at"])
    follow_up = OpinionFollowUp.objects.get(pk=cast(Any, action.follow_up_id))
    record_change_event(
        event_type=ChangeEventType.NEXT_ACTION_RESCHEDULED,
        matter=locked_matter,
        actor=actor,
        obj=action,
        summary=action.text[:200],
        payload={
            "from": previous.isoformat() if previous else None,
            "to": day.isoformat(),
            "follow_up": _block(follow_up, first_due_on=follow_up.first_due_on.isoformat()),
        },
    )
    return action


def validate_outcome(*, outcome: str, next_check_on: date | None = None, reason: str = "") -> None:
    """The outcome's own questions, asked before anything is written.

    Public so the workspace operation can ask them before it stores a file.
    """
    if outcome not in FollowUpOutcome.values:
        raise DomainError(FOLLOW_UP_NEEDS_OUTCOME)
    if outcome == FollowUpOutcome.NO_RESPONSE:
        _refuse_a_past_day(next_check_on, NEXT_CHECK_NEEDS_DATE)
    if outcome == FollowUpOutcome.MONITORING_ENDED and not (reason or "").strip():
        raise DomainError(MONITORING_END_NEEDS_REASON)


@transaction.atomic
def complete_check(
    *,
    action: NextAction,
    outcome: str,
    actor: Any = None,
    next_check_on: date | None = None,
    reason: str = "",
) -> NextAction | None:
    """Finish a check with what it found. Returns the next check, if one was scheduled.

    ``action`` is the row `locked_check` returned, under the Matter's lock.

    * `Vastus saabunud` — the check is done and the monitoring ends answered.
      Nothing new is created and the Matter stays open.
    * `Vastust ei ole — kontrollin uuesti` — the check is done and the **next
      one** is planned on ``next_check_on``, the day the lawyer chose: same
      opinion, same text, the Matter's owner, the same restriction. Never an
      automatic 30 days, and never an endless recurrence.
    * `Lõpetan jälgimise` — the check is done and the monitoring ends, with
      ``reason`` (required) on the follow-up row; nothing is scheduled.

    All in one transaction: a check is never left done with the next one
    missing. A check that was the **current** action promotes the earliest
    planned one when it is finished, exactly as finishing any current action
    does (docs/adr/0143 §A4); a planned check promotes nothing (docs/adr/0144 §1).
    """
    from app.workflow.services import promote_next_planned_action

    validate_outcome(outcome=outcome, next_check_on=next_check_on, reason=reason)
    if action.follow_up_id is None or action.status not in ACTIVE:
        raise DomainError(FOLLOW_UP_CHANGED)
    follow_up = OpinionFollowUp.objects.select_for_update(no_key=True).get(
        pk=cast(Any, action.follow_up_id)
    )
    was_current = action.status == ActionStatus.OPEN
    now = timezone.now()

    action.status = ActionStatus.COMPLETED
    action.ended_at = now
    action.ended_by = actor
    action.follow_up_outcome = outcome
    action.save(update_fields=["status", "ended_at", "ended_by", "follow_up_outcome", "updated_at"])
    record_change_event(
        event_type=ChangeEventType.NEXT_ACTION_COMPLETED,
        matter=action.matter,
        actor=actor,
        obj=action,
        summary=action.text[:200],
        payload={
            "kind": action.kind,
            "planned": not was_current,
            "follow_up": _block(follow_up, outcome=outcome),
        },
    )

    next_check = None
    if outcome == FollowUpOutcome.NO_RESPONSE:
        if next_check_on is None:  # refused by validate_outcome; for the type checker
            raise DomainError(NEXT_CHECK_NEEDS_DATE)
        next_check = NextAction.objects.create(
            matter=action.matter,
            text=action.text,
            kind=ActionKind.DO,
            date_semantics=DateSemantics.DEADLINE,
            target_date=next_check_on,
            date_precision=DatePrecision.EXACT,
            status=ActionStatus.PLANNED,
            responsible=action.matter.owner,
            created_by=actor,
            visibility_override=action.visibility_override,
            follow_up=follow_up,
        )
        record_change_event(
            event_type=ChangeEventType.NEXT_ACTION_SET,
            matter=action.matter,
            actor=actor,
            obj=next_check,
            summary=next_check.text[:200],
            payload={
                "kind": next_check.kind,
                "date_semantics": next_check.date_semantics,
                "target_date": next_check_on.isoformat(),
                "replaced": None,
                "planned": True,
                "follow_up": _block(
                    follow_up,
                    automatic=False,
                    check=_completed_checks(follow_up) + 1,
                    after=str(action.pk),
                ),
            },
        )
    else:
        follow_up.state = (
            FollowUpState.RESPONSE_RECEIVED
            if outcome == FollowUpOutcome.RESPONSE_RECEIVED
            else FollowUpState.ENDED
        )
        follow_up.ended_at = now
        follow_up.ended_by = actor
        follow_up.end_reason = (reason or "").strip()
        follow_up.save(update_fields=["state", "ended_at", "ended_by", "end_reason", "updated_at"])

    if was_current:
        promote_next_planned_action(matter=action.matter, actor=actor)
    return next_check


# ---------------------------------------------------------------------------
# What else ends a check
# ---------------------------------------------------------------------------


def end_follow_up_for_cancelled_check(*, action: NextAction, actor: Any, reason: str) -> None:
    """A check was cancelled — by closure, or by its opinion leaving — so the watching ends.

    Called by `cancel_next_action` for a check, under its locks. The follow-up
    row keeps why; the cancelled check keeps its opinion. Nothing reopens it:
    reopening the Matter later does not recreate the check (docs/adr/0146 §8).
    """
    follow_up = OpinionFollowUp.objects.select_for_update(no_key=True).get(
        pk=cast(Any, action.follow_up_id)
    )
    if not follow_up.is_monitoring:
        return
    follow_up.state = FollowUpState.CANCELLED
    follow_up.ended_at = action.ended_at or timezone.now()
    follow_up.ended_by = actor
    follow_up.end_reason = (reason or "").strip() or "Tühistatud"
    follow_up.save(update_fields=["state", "ended_at", "ended_by", "end_reason", "updated_at"])


def refuse_unconfirmed_closure(locked_matter: Any, *, confirmed: bool) -> None:
    """Closing a Matter with a planned or current check needs the person's confirmation.

    Asked under the Matter's lock by `close_matter`, and earlier by every
    operation that stores a file before its stage can close the Matter, so a
    refusal never strands bytes. The answer is the database's at that moment:
    a tab drawn before a check existed, posting no confirmation, is refused
    (docs/adr/0146 §8). Allowed once confirmed — closure is never forbidden.
    """
    if not confirmed and pending_checks(locked_matter).exists():
        raise FollowUpClosureUnconfirmed()


@transaction.atomic
def cancel_checks_of_submission(*, submission: Any, actor: Any = None, reason: str) -> int:
    """An opinion withdrawn or superseded is no longer something to check on.

    Its planned or current check is cancelled through `cancel_next_action` —
    kept in the history with the reason, its opinion link kept — and the
    follow-up ends `CANCELLED`. Completed checks stay exactly as they were. The
    caller holds the Matter's and the submission's locks.
    """
    from app.workflow.services import cancel_next_action

    follow_up = OpinionFollowUp.objects.filter(submission=submission).first()
    if follow_up is None or not follow_up.is_monitoring:
        return 0
    cancelled = 0
    for check in NextAction.objects.filter(follow_up=follow_up, status__in=ACTIVE).order_by("pk"):
        cancel_next_action(action=check, actor=actor, reason=reason)
        cancelled += 1
    return cancelled


@transaction.atomic
def follow_sent_date_correction(*, submission: Any, actor: Any = None) -> NextAction | None:
    """A corrected sending date moves the first check — unless a person already moved it.

    Only while the opinion is still being watched, only before any check was
    done, and only when the check still stands on the automatic day: a day a
    lawyer chose is never silently overwritten (docs/adr/0146 §6). Then the
    check moves to the corrected date + 30, in place, and the follow-up row
    records the new automatic day; `NEXT_ACTION_RESCHEDULED` with
    ``recalculated`` keeps the old one. The caller holds the Matter's and the
    submission's locks.
    """
    follow_up = (
        OpinionFollowUp.objects.select_for_update(no_key=True).filter(submission=submission).first()
    )
    if follow_up is None or not follow_up.is_monitoring or submission.sent_at is None:
        return None
    sent_on = sent_on_of(submission.sent_at)
    if sent_on == follow_up.sent_on:
        return None
    if NextAction.objects.filter(follow_up=follow_up, status=ActionStatus.COMPLETED).exists():
        return None
    check = (
        NextAction.objects.select_for_update(no_key=True)
        .filter(follow_up=follow_up, status__in=ACTIVE)
        .first()
    )
    if check is None or check.target_date != follow_up.first_due_on:
        return None
    previous = check.target_date
    due = first_check_day(sent_on)
    check.target_date = due
    check.save(update_fields=["target_date", "updated_at"])
    follow_up.sent_on = sent_on
    follow_up.first_due_on = due
    follow_up.save(update_fields=["sent_on", "first_due_on", "updated_at"])
    record_change_event(
        event_type=ChangeEventType.NEXT_ACTION_RESCHEDULED,
        matter=check.matter,
        actor=actor,
        obj=check,
        summary=check.text[:200],
        payload={
            "from": previous.isoformat() if previous else None,
            "to": due.isoformat(),
            "recalculated": True,
            "follow_up": _block(follow_up, sent_on=sent_on.isoformat()),
        },
    )
    return check
