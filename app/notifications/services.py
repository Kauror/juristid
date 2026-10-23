"""Generating and answering «Kontrolli jõustumist» (docs/adr/0188).

Which commencements are eligible
--------------------------------
Exactly one population, stated once in :func:`eligible_commencements`:

* a `MatterEffectiveDate` of kind ``KNOWN_DATE`` — never ``GENERAL_ORDER`` or
  ``UNKNOWN``, which carry no date by constraint;
* ``ACTIVE`` — not ``CANCELLED``, not ``SUPERSEDED``;
* not removed from its Teema, on a Teema that is not a deletion tombstone;
* at a **day** precision (``EXACT``, or ``INFERRED``, which ADR 0079 §8 says
  behaves as an exact day everywhere). A month, a quarter, a half-year or a year
  has no day to remind anybody on, and inventing its first day is exactly the
  fabricated precision ADR 0079 refuses.

Open and closed Teemad alike: a commencement often lands after the department
has closed its file (ADR 0158 §2), and that is when the check matters.

`Oluline tähtaeg` rows of the informational kinds — a draft's
``PLANNED_COMMENCEMENT`` and a source's ``ESTIMATED_COMMENCEMENT`` — are **not**
eligible. ADR 0120 (amendment of 2026-10-19/20) made those milestones inform and
never generate work: they are never the next step and their passing is never
lateness. A reminder to check them would be the first piece of work they
generate, which is a product decision this round does not take (ADR 0188 §3).

The two stages, on the Europe/Tallinn calendar
----------------------------------------------
For a commencement on day *D*:

* ``SEVEN_DAYS`` is due on *D − 7* and is generated on any day from *D − 7* to
  *D − 1*. A date recorded three days ahead gets it at once.
* ``ON_DATE`` is due on *D* and is generated on any day from *D* to
  *D +* ``ON_DATE_CATCH_UP_DAYS`` — only while nobody has answered for that
  date. It replaces an unanswered seven-day row (``SUPERSEDED``, kept) and
  starts undeferred: a seven-day deferral is capped at *D*. The catch-up window is what makes a
  generator that was down over the day still ask, without a first deployment
  flooding everybody with prompts for every historical commencement.

An answer — ``ACKNOWLEDGED`` or ``DISMISSED`` — for (commencement, date) ends
both stages of that date. A commencement moved to another day is a different
date and is asked about again; one moved back to a day already answered is not.

Concurrency
-----------
Every write here runs under one transaction-scoped advisory lock
(:data:`REMINDER_LOCK`), and the unique constraint on the table backs it: two
workers, a restart in the middle of a run or a person answering while the
generator runs all serialise, and the second one finds the work done.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, timedelta
from typing import Any

from django.db import connection, transaction
from django.db.models import Q, QuerySet
from django.utils import timezone

from app.audit.enums import ChangeEventType
from app.audit.services import record_change_event
from app.core.dates import format_estonian_date
from app.core.errors import DomainError
from app.intelligence.enums import EffectiveDateKind, FactStatus
from app.intelligence.models import MatterEffectiveDate
from app.notifications.enums import (
    RESOLVED_STATUSES,
    ObsoleteReason,
    ReminderStage,
    ReminderStatus,
)
from app.notifications.models import CommencementReminder
from app.workflow.enums import DatePrecision

#: How many calendar days ahead the first prompt comes (brief C2).
LEAD_DAYS = 7

#: How long after the day an unanswered commencement still gets its on-the-day
#: prompt if the generator did not run on the day itself.
ON_DATE_CATCH_UP_DAYS = 7

#: The precisions that name a day (ADR 0079 §8).
DAY_PRECISIONS: tuple[str, ...] = (DatePrecision.EXACT.value, DatePrecision.INFERRED.value)

#: The furthest a reminder may be put off, counted from today.
MAX_DEFER_DAYS = 30

#: Transaction-scoped advisory lock serialising generation and answers. The
#: namespace continues the sequence of this codebase's other locks
#: (search 24601, organisations 24602, titles 24603, storage quota 24604).
REMINDER_LOCK = (24605, 1)

#: The refusals a person can meet. One sentence each, Estonian, said by the view.
REMINDER_NOT_OPEN = "Seda meeldetuletust ei saa enam muuta — see on juba vastatud või aegunud."
DEFER_NOT_AHEAD = "Edasilükkamise kuupäev peab olema tulevikus."
DEFER_TOO_FAR = f"Meeldetuletust saab edasi lükata kuni {MAX_DEFER_DAYS} päeva."
DISMISS_NEEDS_REASON = "Kirjuta, miks meeldetuletus suletakse ilma kontrollita."

#: How much of a free-text reason goes into the audit payload.
REASON_LIMIT = 500


def _hold_lock() -> None:
    if not connection.in_atomic_block:  # pragma: no cover - programming error
        raise RuntimeError("The reminder lock needs a transaction to hold it in.")
    with connection.cursor() as cursor:
        cursor.execute("SELECT pg_advisory_xact_lock(%s, %s)", list(REMINDER_LOCK))


def eligibility_q(prefix: str = "") -> Q:
    """The one statement of «this commencement may be reminded about».

    ``prefix`` lets a reminder query ask the same of its source
    (``effective_date__``), so the generator and every reader agree by
    construction rather than by two copies kept in step.
    """
    return Q(
        **{
            f"{prefix}kind": EffectiveDateKind.KNOWN_DATE,
            f"{prefix}status": FactStatus.ACTIVE,
            f"{prefix}removed_at__isnull": True,
            f"{prefix}date_precision__in": DAY_PRECISIONS,
            f"{prefix}date_value__isnull": False,
            f"{prefix}matter__deleted_at__isnull": True,
        }
    )


def eligible_commencements(today: date) -> QuerySet[MatterEffectiveDate]:
    """Eligible commencements whose day is inside the window any stage can be due in."""
    return (
        MatterEffectiveDate.objects.filter(eligibility_q())
        .filter(
            date_value__gte=today - timedelta(days=ON_DATE_CATCH_UP_DAYS),
            date_value__lte=today + timedelta(days=LEAD_DAYS),
        )
        .order_by("date_value", "pk")
    )


def due_stage(source_date: date, today: date) -> str | None:
    """Which prompt a commencement on ``source_date`` is owed today, if any."""
    if source_date - timedelta(days=LEAD_DAYS) <= today < source_date:
        return ReminderStage.SEVEN_DAYS
    if source_date <= today <= source_date + timedelta(days=ON_DATE_CATCH_UP_DAYS):
        return ReminderStage.ON_DATE
    return None


def due_on_for(stage: str, source_date: date) -> date:
    if stage == ReminderStage.SEVEN_DAYS:
        return source_date - timedelta(days=LEAD_DAYS)
    return source_date


def _obsolete_reason(record: MatterEffectiveDate | None, reminder: CommencementReminder) -> str:
    """Why an open row no longer describes its source, or ``""`` if it still does."""
    if record is None or record.matter.deleted_at is not None:
        return ObsoleteReason.REMOVED
    if record.removed_at is not None:
        return ObsoleteReason.REMOVED
    if record.status != FactStatus.ACTIVE:
        return ObsoleteReason.NOT_ACTIVE
    if (
        record.kind != EffectiveDateKind.KNOWN_DATE
        or record.date_value is None
        or record.date_precision not in DAY_PRECISIONS
    ):
        return ObsoleteReason.NOT_EXACT
    if record.date_value != reminder.source_date:
        return ObsoleteReason.DATE_CHANGED
    return ""


@dataclass(frozen=True)
class ReconcileResult:
    """What one run did. ``summary`` is the operator's one line."""

    today: date
    created: int
    superseded: int
    obsoleted: int
    open_after: int
    dry_run: bool = False

    @property
    def changed(self) -> bool:
        return bool(self.created or self.superseded or self.obsoleted)

    @property
    def summary(self) -> str:
        verb = "Loodaks" if self.dry_run else "Loodud"
        return (
            f"Jõustumise meeldetuletused {self.today.isoformat()}: {verb} {self.created}, "
            f"asendatud {self.superseded}, aegunud {self.obsoleted}, "
            f"avatud {self.open_after}."
        )


def reconcile_commencement_reminders(
    today: date | None = None, *, dry_run: bool = False
) -> ReconcileResult:
    """Bring the reminder table in line with the commencements, for ``today``.

    Idempotent: a second call on the same day with nothing changed creates,
    supersedes and retires nothing. Safe under concurrent callers (the advisory
    lock, then the unique constraint). ``today`` defaults to the Europe/Tallinn
    calendar date — never the UTC one — and is a parameter so a test or an
    operator can ask about a given day without touching the clock.

    ``dry_run`` computes the same plan and rolls it back: what
    ``generate_commencement_reminders --check`` reports.
    """
    day = today or timezone.localdate()
    try:
        with transaction.atomic():
            result = _reconcile(day, dry_run=dry_run)
            if dry_run:
                raise _DryRun(result)
    except _DryRun as finished:
        return finished.result
    return result


class _DryRun(Exception):
    def __init__(self, result: ReconcileResult) -> None:
        super().__init__("dry run")
        self.result = result


def _reconcile(day: date, *, dry_run: bool) -> ReconcileResult:
    _hold_lock()

    # 1. Retire open rows whose commencement moved, ended or lost its day.
    obsoleted = 0
    open_rows = list(
        CommencementReminder.objects.open()
        .select_related("effective_date", "effective_date__matter")
        .select_for_update(of=("self",))
    )
    for reminder in open_rows:
        source = reminder.effective_date
        reason = _obsolete_reason(source, reminder)
        if reason:
            reminder.status = ReminderStatus.OBSOLETE
            reminder.obsolete_reason = reason
            reminder.save(update_fields=["status", "obsolete_reason", "updated_at"])
            obsoleted += 1

    # 2. Owe the prompts that are due today.
    created = 0
    superseded = 0
    for record in eligible_commencements(day):
        if record.date_value is None:  # pragma: no cover - eligibility_q excludes it
            continue
        stage = due_stage(record.date_value, day)
        if stage is None:
            continue
        # OBSOLETE rows are history about a date that stopped being the
        # commencement's. A date edited away and then restored (D1 -> D2 -> D1,
        # or EXACT -> MONTH -> EXACT) is the live date again and is asked about
        # again; the retired rows stay as they were, which is why the unique
        # constraint is partial on non-OBSOLETE rows.
        existing = list(
            CommencementReminder.objects.filter(
                effective_date=record, source_date=record.date_value
            ).exclude(status=ReminderStatus.OBSOLETE)
        )
        if any(row.status in RESOLVED_STATUSES for row in existing):
            continue
        if any(row.stage == stage for row in existing):
            continue
        if stage == ReminderStage.ON_DATE:
            # The on-the-day prompt starts undeferred. A seven-day deferral is
            # capped at the date itself (`defer_reminder`), so nothing it said
            # can reach past the day this prompt exists for.
            for row in existing:
                if row.stage == ReminderStage.SEVEN_DAYS and row.status == ReminderStatus.OPEN:
                    row.status = ReminderStatus.SUPERSEDED
                    row.save(update_fields=["status", "updated_at"])
                    superseded += 1
        CommencementReminder.objects.create(
            effective_date=record,
            source_date=record.date_value,
            stage=stage,
            due_on=due_on_for(stage, record.date_value),
        )
        created += 1

    open_after = CommencementReminder.objects.open().count()
    return ReconcileResult(
        today=day,
        created=created,
        superseded=superseded,
        obsoleted=obsoleted,
        open_after=open_after,
        dry_run=dry_run,
    )


# ---------------------------------------------------------------------------
# A person's answers
# ---------------------------------------------------------------------------


def _lock_open_reminder(reminder: CommencementReminder) -> CommencementReminder:
    """The open prompt for this reminder's commencement date, under the lock.

    An answer is about a *date*, not a row: a person pressing «Kontrollitud» on
    the seven-day prompt a moment after the generator replaced it by the
    on-the-day one is answering the same question, so the open sibling is what
    is answered. Read from locked rows, never from the instance the view
    fetched: a second press, a second tab or a commencement that moved or ended
    meanwhile all meet the same refusal.
    """
    _hold_lock()
    locked = (
        CommencementReminder.objects.select_related("effective_date", "effective_date__matter")
        .select_for_update(of=("self",))
        .get(pk=reminder.pk)
    )
    if _obsolete_reason(locked.effective_date, locked):
        raise DomainError(REMINDER_NOT_OPEN)
    current = (
        _siblings(locked)
        .select_related("effective_date", "effective_date__matter")
        .select_for_update(of=("self",))
        .order_by("-due_on")
        .first()
    )
    if current is None:
        raise DomainError(REMINDER_NOT_OPEN)
    return current


def _siblings(reminder: CommencementReminder) -> QuerySet[CommencementReminder]:
    """Every open stage of the same commencement date — what one answer closes."""
    return CommencementReminder.objects.open().filter(
        effective_date_id=reminder.effective_date_id, source_date=reminder.source_date
    )


def _event_payload(reminder: CommencementReminder, **extra: Any) -> dict[str, Any]:
    return {
        "reminder": str(reminder.pk),
        "stage": reminder.stage,
        "date": reminder.source_date.isoformat(),
        **extra,
    }


def _summary(reminder: CommencementReminder) -> str:
    # The date only: the description belongs to the commencement row, whose own
    # visibility scopes this event (app/audit/visibility.py).
    return f"Jõustumine {format_estonian_date(reminder.source_date)}"


@transaction.atomic
def acknowledge_reminder(*, reminder: CommencementReminder, actor: Any) -> CommencementReminder:
    """«Kontrollitud» — the person checked the commencement. Closes every stage of that date.

    Records what the person *did*, not what is true of the act: nothing here,
    and nothing that reads it, says the act entered into force.
    """
    locked = _lock_open_reminder(reminder)
    now = timezone.now()
    _siblings(locked).update(
        status=ReminderStatus.ACKNOWLEDGED, resolved_at=now, resolved_by=actor, updated_at=now
    )
    record_change_event(
        event_type=ChangeEventType.COMMENCEMENT_CHECK_ACKNOWLEDGED,
        matter=locked.effective_date.matter,
        actor=actor,
        obj=locked.effective_date,
        summary=_summary(locked),
        payload=_event_payload(locked),
    )
    locked.refresh_from_db()
    return locked


@transaction.atomic
def dismiss_reminder(
    *, reminder: CommencementReminder, actor: Any, reason: str
) -> CommencementReminder:
    """Close the prompt without a check, saying why. Closes every stage of that date."""
    reason = (reason or "").strip()
    if not reason:
        raise DomainError(DISMISS_NEEDS_REASON)
    locked = _lock_open_reminder(reminder)
    now = timezone.now()
    _siblings(locked).update(
        status=ReminderStatus.DISMISSED,
        resolved_at=now,
        resolved_by=actor,
        resolution_note=reason[:REASON_LIMIT],
        updated_at=now,
    )
    record_change_event(
        event_type=ChangeEventType.COMMENCEMENT_CHECK_DISMISSED,
        matter=locked.effective_date.matter,
        actor=actor,
        obj=locked.effective_date,
        summary=_summary(locked),
        payload=_event_payload(locked, reason=reason[:REASON_LIMIT]),
    )
    locked.refresh_from_db()
    return locked


@transaction.atomic
def defer_reminder(
    *, reminder: CommencementReminder, actor: Any, until: date, today: date | None = None
) -> CommencementReminder:
    """Hide the prompt until ``until`` (a Tallinn date). It stays open.

    **A seven-day prompt is put off at most until the commencement date.** The
    on-the-day prompt is the second ask the brief requires and must not be
    swallowed by a deferral of the first: a later ``until`` is capped at the
    date, and the caller reads the stored value back to say so.
    """
    day = today or timezone.localdate()
    locked = _lock_open_reminder(reminder)
    if locked.stage == ReminderStage.SEVEN_DAYS and until > locked.source_date:
        until = locked.source_date
    if until <= day:
        raise DomainError(DEFER_NOT_AHEAD)
    if until > day + timedelta(days=MAX_DEFER_DAYS):
        raise DomainError(DEFER_TOO_FAR)
    _siblings(locked).update(deferred_until=until, updated_at=timezone.now())
    record_change_event(
        event_type=ChangeEventType.COMMENCEMENT_CHECK_DEFERRED,
        matter=locked.effective_date.matter,
        actor=actor,
        obj=locked.effective_date,
        summary=_summary(locked),
        payload=_event_payload(locked, until=until.isoformat()),
    )
    locked.refresh_from_db()
    return locked
