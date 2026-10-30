"""Generating, re-validating and answering personal deadline notifications (docs/adr/0198).

The schedule, on the Europe/Tallinn calendar
--------------------------------------------
For a source dated *D*, a person's chosen advance intervals ``N₁ > N₂ > … > Nₖ``
and today *t* (``d = D − t`` days to go):

* **Advance.** The interval owed today is the *smallest* ``Nᵢ ≥ d`` — the one
  whose window ``D − Nᵢ … D − Nᵢ₊₁ − 1`` contains *t* (``Nₖ₊₁ = 0``). Only that
  one is generated. A restart, a first deployment, a reassignment or a
  category switched on late therefore delivers the interval that is current,
  never the ones whose windows have passed. With {7, 3, 1}: *d* = 7…4 owes the
  7-day row, 3…2 the 3-day row, 1 the 1-day row; *d* = 8 owes nothing.
* **On the day.** For a category whose day is a delivery and a person who
  keeps it on: one row, generated on any day *D … D + 7* while the source is
  still outstanding — the catch-up is what makes a worker that was down over
  the day still deliver it, without a first deployment flooding everybody with
  history.
* **Escalation** (owner decision D3): for an `Arvamuse tähtaeg` or a DO +
  DEADLINE step that is overdue by the operational definition, one INFO row to
  each holder of `department.view_management`, generated on any day *D + 1 …
  D + 7*. Not to the holder who is themselves responsible for it: they have the
  on-the-day row already.

Every row is unique on ``(recipient, category, source, D, purpose, interval)``,
for good: a rerun or a second worker never delivers an interval twice. When the
interval owed *today* is held by the person's own retired row — the Teema was
handed away and back, the date moved away and back, the step reopened — that
row is brought back as new rather than lost (:func:`_deliver`). A hidden row
stays hidden, a superseded one stays superseded, and an interval whose window
has passed is never owed again, so nothing is replayed.

The first run is not a catch-up
-------------------------------
A day before the feature's first delivery (:func:`feature_start`) owes no
on-the-day row and no escalation: on a first deployment the past week is
history, not a missed delivery. After the start, a worker that was down still
catches up for seven days.

Retirement, and why a list never waits for it
---------------------------------------------
A row whose source is done (``DISCHARGED``), moved (``DATE_CHANGED``), handed to
somebody else (``REASSIGNED``) or no longer a day or a source at all
(``INELIGIBLE``) is retired by the next run, and an advance row is retired
(``SUPERSEDED``) by the next interval or by the day itself. Every list
re-validates each row live with :func:`live_q` — the same population, asked as
the reader — so state is right before the worker has run.

Disabling a category or pausing retires nothing: it stops generation and hides
the category's INFO rows while it lasts (`app.notifications.inbox`).

Presentation, not business
--------------------------
«Märgi loetuks» and «Peida» stamp the recipient's own row and call no business
service. Neither is a business fact, so neither writes a `ChangeEvent` — the
same decision ADR 0194 took for a remembered block. The commencement answers
(ADR 0188) keep theirs.
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from datetime import date, timedelta
from typing import Any

from django.db import connection, transaction
from django.db.models import Exists, F, OuterRef, Q, QuerySet
from django.utils import timezone

from app.notifications.categories import (
    CATCH_UP_DAYS,
    CATEGORIES,
    ESCALATION_KEY,
    Category,
    escalating,
)
from app.notifications.enums import NotificationKind, NotificationPurpose, RetiredReason
from app.notifications.models import DeadlineNotification
from app.notifications.preferences import Choices, Preference, choices_for

#: Transaction-scoped advisory lock for generation and retirement, continuing
#: the sequence (… storage quota 24604, commencement reminders 24605, vocabulary
#: administration 24606, Lovable sync 24607, the daily Muudatused snapshot 24608).
DEADLINE_LOCK = (24609, 1)


def _hold_lock() -> None:
    if not connection.in_atomic_block:  # pragma: no cover - programming error
        raise RuntimeError("The deadline notification lock needs a transaction to hold it in.")
    with connection.cursor() as cursor:
        cursor.execute("SELECT pg_advisory_xact_lock(%s, %s)", list(DEADLINE_LOCK))


# ---------------------------------------------------------------------------
# The schedule — pure arithmetic, tested on its own
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class Owed:
    purpose: str
    offset_days: int
    kind: str
    due_on: date


def owed_advance(source_date: date, today: date, offsets: tuple[int, ...]) -> int | None:
    """The one advance interval owed today, or ``None``: the smallest chosen ``N ≥ D − t``."""
    remaining = (source_date - today).days
    if remaining <= 0:
        return None
    reaching = [offset for offset in offsets if offset >= remaining]
    return min(reaching) if reaching else None


def owed_personal(
    category: Category,
    source_date: date,
    today: date,
    preference: Preference,
    *,
    started: date | None = None,
) -> Owed | None:
    """What the responsible person is owed today for one source date, if anything.

    ``started`` is the feature's first day (:func:`feature_start`): a day that
    passed before it is history, not a missed delivery, so it owes no
    on-the-day row however short ago it was.
    """
    offset = owed_advance(source_date, today, preference.offsets)
    if offset is not None:
        return Owed(
            NotificationPurpose.ADVANCE,
            offset,
            NotificationKind.INFO,
            source_date - timedelta(days=offset),
        )
    late = (today - source_date).days
    if started is not None and source_date < started:
        return None
    if category.supports_on_day and preference.on_day and 0 <= late <= CATCH_UP_DAYS:
        return Owed(NotificationPurpose.ON_DAY, 0, category.on_day_kind, source_date)
    return None


def escalation_is_owed(source_date: date, today: date, *, started: date | None = None) -> bool:
    if started is not None and source_date < started:
        return False
    return 1 <= (today - source_date).days <= CATCH_UP_DAYS


def feature_start(today: date) -> date:
    """The first Tallinn day this feature delivered anything — or ``today`` if never.

    The catch-up windows (the day itself *D … D + 7*, escalation *D + 1 … D + 7*)
    exist so that a worker that was down still delivers. On the first run there
    was no worker to be down: a deadline that passed before the feature existed
    is history, and delivering the past week at once would flood every desk. So
    a day before the feature's first delivery owes neither an on-the-day row nor
    an escalation. The start is read from the table itself — the earliest row
    ever created, never deleted — so it needs no setting and no new state.
    Advance intervals are unaffected: they are always about the future.
    """
    from django.db.models import Min

    first = DeadlineNotification.objects.aggregate(first=Min("created_at"))["first"]
    if first is None:
        return today
    return min(timezone.localdate(first), today)


# ---------------------------------------------------------------------------
# Live validity — one statement, read by the lists and by retirement
# ---------------------------------------------------------------------------


def _matches(category: Category, queryset: QuerySet[Any]) -> Exists:
    return Exists(
        queryset.filter(pk=OuterRef("source_id"), **{category.date_field: OuterRef("source_date")})
    )


def personal_q(reader: Any, category: Category, today: date) -> Q:
    """This category's advance and on-the-day rows that still describe their source for ``reader``.

    An advance row is live only before its day: on the day the day itself, or
    the commencement check, takes over.
    """
    still = _matches(category, category.sources(reader))
    advance = Q(purpose=NotificationPurpose.ADVANCE, source_date__gt=today)
    on_day = Q(purpose=NotificationPurpose.ON_DAY)
    return Q(category=category.key) & (advance | on_day) & Q(still)


def escalation_q(reader: Any, category: Category, today: date) -> Q:
    if category.overdue is None:  # pragma: no cover - `escalating()` gives only these
        return Q(pk__in=[])
    return Q(category=category.key, purpose=NotificationPurpose.ESCALATION) & Q(
        _matches(category, category.overdue(reader, today))
    )


def live_q(
    reader: Any,
    today: date,
    *,
    choices: Choices | None = None,
    holds_management: bool = False,
) -> Q:
    """Rows of ``reader`` that still describe their source today — and, with
    ``choices``, that the reader's preferences currently show.

    Without ``choices`` (retirement) every category counts. With them (a list):
    pausing hides every row; a disabled category hides its INFO rows and keeps
    its on-the-day ACTION rows, which are work that is still true; the
    escalation shows only to a current holder who keeps it on.
    """
    if choices is not None and choices.paused:
        return Q(pk__in=[])
    condition = Q(pk__in=[])
    for category in CATEGORIES.values():
        clause = personal_q(reader, category, today)
        if choices is not None and not choices.of(category.key).enabled:
            clause &= Q(kind=NotificationKind.ACTION)
        condition |= clause
    if holds_management and (choices is None or choices.of(ESCALATION_KEY).enabled):
        for category in escalating():
            condition |= escalation_q(reader, category, today)
    return condition


def outstanding() -> QuerySet[DeadlineNotification]:
    return DeadlineNotification.objects.filter(retired_at__isnull=True)


# ---------------------------------------------------------------------------
# The generator
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class DeadlineReconcileResult:
    today: date
    created: int
    retired: int
    outstanding_after: int
    revived: int = 0
    dry_run: bool = False

    @property
    def changed(self) -> bool:
        return bool(self.created or self.retired or self.revived)

    @property
    def summary(self) -> str:
        verb = "Loodaks" if self.dry_run else "Loodud"
        return (
            f"Tähtaegade teavitused {self.today.isoformat()}: {verb} {self.created}, "
            f"taastatud {self.revived}, aegunud {self.retired}, "
            f"kehtivad {self.outstanding_after}."
        )


class _DryRun(Exception):
    def __init__(self, result: DeadlineReconcileResult) -> None:
        super().__init__("dry run")
        self.result = result


def reconcile_deadline_notifications(
    today: date | None = None, *, dry_run: bool = False
) -> DeadlineReconcileResult:
    """Bring the deadline notifications in line with the canonical dated work, for ``today``.

    Idempotent: a second call on the same day with nothing changed creates and
    retires nothing. Safe under concurrent callers: the advisory lock
    serialises them and the unique constraint backs it (``ON CONFLICT DO
    NOTHING``). ``today`` defaults to the Europe/Tallinn date, never the UTC one.
    ``dry_run`` plans the same run and rolls it back (the ``--check`` healthcheck).
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


def _recipients() -> list[Any]:
    from app.accounts.selectors import department_workers

    return list(department_workers())


def _reconcile(day: date, *, dry_run: bool) -> DeadlineReconcileResult:
    from app.core.authorization import may_view_department_management

    _hold_lock()
    now = timezone.now()
    started = feature_start(day)
    workers = _recipients()
    holders = {worker.pk for worker in workers if may_view_department_management(worker)}

    retired = _retire(day, now, workers, holders)

    rows: list[DeadlineNotification] = []
    low = day - timedelta(days=CATCH_UP_DAYS)
    for worker in workers:
        choices = choices_for(worker)
        if choices.paused:
            continue
        for category in CATEGORIES.values():
            preference = choices.of(category.key)
            if not preference.enabled:
                continue
            high = day + timedelta(days=max(preference.offsets, default=0))
            window = {f"{category.date_field}__gte": low, f"{category.date_field}__lte": high}
            for source_id, matter_id, source_date in (
                category.sources(worker)
                .filter(**window)
                .values_list("pk", category.matter_field, category.date_field)
            ):
                owed = owed_personal(category, source_date, day, preference, started=started)
                if owed is not None:
                    rows.append(_row(worker, category, source_id, matter_id, source_date, owed))
        if worker.pk in holders and choices.of(ESCALATION_KEY).enabled:
            rows.extend(_escalations(worker, day, started=started))

    created, revived = _deliver(rows, now)

    retired += _supersede(day, now)
    return DeadlineReconcileResult(
        today=day,
        created=created,
        revived=revived,
        retired=retired,
        outstanding_after=outstanding().count(),
        dry_run=dry_run,
    )


#: The retirements a source can come back from: handed away and back, moved
#: away and back, a person inactive for a while, a step reopened. Never
#: ``SUPERSEDED`` (a later interval or the day replaced it), and never a row
#: its recipient hid — a hidden row stays hidden.
REVIVABLE: frozenset[str] = frozenset(
    {
        RetiredReason.REASSIGNED.value,
        RetiredReason.DATE_CHANGED.value,
        RetiredReason.INELIGIBLE.value,
        RetiredReason.DISCHARGED.value,
    }
)

_KEY_FIELDS = (
    "recipient_id",
    "category",
    "source_type",
    "source_id",
    "source_date",
    "purpose",
    "offset_days",
)


def _key(row: Any) -> tuple[Any, ...]:
    if isinstance(row, dict):
        return tuple(row[name] for name in _KEY_FIELDS)
    return tuple(getattr(row, name) for name in _KEY_FIELDS)


def _deliver(rows: list[DeadlineNotification], now: Any) -> tuple[int, int]:
    """Insert what is owed today; bring back a retired row that is owed again.

    The unique key holds a slot for good, so a source handed away and back,
    moved away and back, or reopened on the same day would otherwise find its
    *current* interval taken by its own retired row and get nothing. That row is
    un-retired instead, and shown as new (``read_at`` cleared). Only rows owed
    today are ever touched, so an expired interval is never replayed; a hidden
    row and a superseded one stay as they are. Returns (inserted, revived),
    counted from what was written. The advisory lock is held, so the existence
    check and the insert cannot interleave with another run.
    """
    if not rows:
        return 0, 0
    owed = {_key(row): row for row in rows}
    existing = DeadlineNotification.objects.filter(
        recipient_id__in={row.recipient_id for row in rows},
        source_id__in={row.source_id for row in rows},
    ).values("pk", "retired_at", "retired_reason", "dismissed_at", *_KEY_FIELDS)
    revive: list[Any] = []
    for found in existing:
        key = _key(found)
        if key not in owed:
            continue
        del owed[key]
        if (
            found["retired_at"] is not None
            and found["dismissed_at"] is None
            and found["retired_reason"] in REVIVABLE
        ):
            revive.append(found["pk"])
    revived = 0
    if revive:
        revived = DeadlineNotification.objects.filter(pk__in=revive).update(
            retired_at=None, retired_reason="", read_at=None, updated_at=now
        )
    fresh = list(owed.values())
    DeadlineNotification.objects.bulk_create(fresh, ignore_conflicts=True, batch_size=500)
    return len(fresh), revived


def _row(
    recipient: Any,
    category: Category,
    source_id: Any,
    matter_id: Any,
    source_date: date,
    owed: Owed,
) -> DeadlineNotification:
    return DeadlineNotification(
        recipient=recipient,
        matter_id=matter_id,
        category=category.key,
        source_type=category.source_type,
        source_id=source_id,
        source_date=source_date,
        purpose=owed.purpose,
        offset_days=owed.offset_days,
        kind=owed.kind,
        due_on=owed.due_on,
    )


def _escalations(
    holder: Any, day: date, *, started: date | None = None
) -> list[DeadlineNotification]:
    """One INFO row per overdue source to this holder, on *D + 1 … D + 7*."""
    out = []
    owed = Owed(NotificationPurpose.ESCALATION, 0, NotificationKind.INFO, day)
    for category in escalating():
        if category.overdue is None:  # pragma: no cover - `escalating()` gives only these
            continue
        sources = category.overdue(holder, day).filter(
            **{f"{category.date_field}__gte": day - timedelta(days=CATCH_UP_DAYS)}
        )
        values = list(sources.values_list("pk", category.matter_field, category.date_field))
        states = category.state([pk for pk, _, _ in values])
        for source_id, matter_id, source_date in values:
            if not escalation_is_owed(source_date, day, started=started):
                continue
            state = states.get(source_id)
            if state is not None and state.responsible_id == holder.pk:
                # OWNER-DECISION: the responsible holder has the on-the-day row;
                # telling them again as management is noise (docs/adr/0198 §4).
                continue
            row = _row(holder, category, source_id, matter_id, source_date, owed)
            row.due_on = source_date + timedelta(days=1)
            out.append(row)
    return out


def _supersede(day: date, now: Any) -> int:
    """Advance rows a later interval, or the day itself, has replaced."""
    later = DeadlineNotification.objects.filter(
        recipient=OuterRef("recipient"),
        category=OuterRef("category"),
        source_type=OuterRef("source_type"),
        source_id=OuterRef("source_id"),
        source_date=OuterRef("source_date"),
        purpose=NotificationPurpose.ADVANCE,
        offset_days__lt=OuterRef("offset_days"),
    )
    return (
        outstanding()
        .filter(purpose=NotificationPurpose.ADVANCE)
        .filter(Q(source_date__lte=day) | Q(Exists(later)))
        .update(retired_at=now, retired_reason=RetiredReason.SUPERSEDED, updated_at=now)
    )


def _retire(day: date, now: Any, workers: list[Any], holders: set[Any]) -> int:
    """Retire every outstanding row that no longer describes its source, naming why."""
    retired = 0
    worker_ids = {worker.pk for worker in workers}
    # Somebody who no longer does the department's work is owed nothing.
    retired += (
        outstanding()
        .exclude(recipient_id__in=worker_ids)
        .update(retired_at=now, retired_reason=RetiredReason.INELIGIBLE, updated_at=now)
    )
    for worker in workers:
        holds = worker.pk in holders
        if not holds:
            retired += (
                outstanding()
                .filter(recipient=worker, purpose=NotificationPurpose.ESCALATION)
                .update(retired_at=now, retired_reason=RetiredReason.INELIGIBLE, updated_at=now)
            )
        stale = list(
            outstanding()
            .filter(recipient=worker)
            .exclude(purpose=NotificationPurpose.ADVANCE, source_date__lte=day)
            .exclude(live_q(worker, day, holds_management=holds))
            .values("pk", "category", "source_id", "source_date", "purpose")
        )
        if not stale:
            continue
        reasons = _reasons(stale, worker)
        for reason, ids in reasons.items():
            retired += (
                outstanding()
                .filter(pk__in=ids)
                .update(retired_at=now, retired_reason=reason, updated_at=now)
            )
    return retired


def _reasons(stale: list[Any], recipient: Any) -> dict[str, list[Any]]:
    by_category: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in stale:
        by_category[row["category"]].append(row)
    out: dict[str, list[Any]] = defaultdict(list)
    for key, rows in by_category.items():
        states = CATEGORIES[key].state([row["source_id"] for row in rows])
        for row in rows:
            state = states.get(row["source_id"])
            if state is None or state.day is None:
                reason = RetiredReason.INELIGIBLE
            elif state.day != row["source_date"]:
                reason = RetiredReason.DATE_CHANGED
            elif (
                row["purpose"] != NotificationPurpose.ESCALATION
                and state.responsible_id != recipient.pk
            ):
                reason = RetiredReason.REASSIGNED
            else:
                reason = RetiredReason.DISCHARGED
            out[reason].append(row["pk"])
    return out


# ---------------------------------------------------------------------------
# The recipient's own presentation state
# ---------------------------------------------------------------------------


def own_row(user: Any, pk: Any) -> DeadlineNotification | None:
    """The recipient's own outstanding row, or ``None`` — never anybody else's."""
    if user is None or getattr(user, "pk", None) is None:
        return None
    return outstanding().filter(recipient=user, pk=pk).first()


def mark_read(*, notification: DeadlineNotification) -> None:
    """«Märgi loetuks». One conditional UPDATE: a second press changes nothing."""
    now = timezone.now()
    DeadlineNotification.objects.filter(pk=notification.pk, read_at__isnull=True).update(
        read_at=now, updated_at=now
    )


def dismiss(*, notification: DeadlineNotification) -> bool:
    """«Peida» — an INFO row only. Also marks it read. ``False`` for an ACTION row."""
    if notification.kind != NotificationKind.INFO:
        return False
    now = timezone.now()
    DeadlineNotification.objects.filter(pk=notification.pk, dismissed_at__isnull=True).update(
        dismissed_at=now, read_at=F("read_at") if notification.read_at else now, updated_at=now
    )
    return True
