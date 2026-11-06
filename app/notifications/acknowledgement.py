"""Read, unread and «Märgi kõik loetuks»: the reader's own state, one model for all (UI-01).

Every write here is **presentation state of the signed-in account**, stamped on
a row that already exists for exactly that purpose, and nothing else:

* a deadline notice — `DeadlineNotification.read_at` (docs/adr/0198 §7);
* a commencement check — this reader's `CommencementReminderPersonalState.read_at`
  (ADR 0188 amendment 2026-11-02); the shared prompt stays ``OPEN`` for everybody;
* «Uus asi» — `MatterAssignmentNotice.viewed_at` (ADR 0051), through
  `acknowledge_assignment_notice`, the same service its own «Ava» calls.

No business service is called and no `ChangeEvent` written: reading is not
doing the work. A deadline, a current step, a Teema's lifecycle and every other
person's rows are untouched — the tests in `tests/test_ui01_notification_state.py`
fingerprint them. Every function takes the user the request was signed in as;
none accepts anybody else, so a persona under the shared gate can only ever
change its own state.

«Märgi kõik loetuks» and its boundary
------------------------------------
The panel is drawn with a **boundary**: the moment up to which the reader has
been shown their inbox (:func:`boundary`). «Märgi kõik loetuks» marks read
every item still awaiting the reader that *arrived* by then — including the
ones on pages the panel never loaded — and nothing that arrived afterwards, so
a notice delivered while the panel was open stays unread. "Arrived", per
source, is the last moment the item could have become unread for this reader:

* deadline notice and «Uus asi»: ``updated_at``. An unread row is only ever
  written when it is created, brought back (`deadlines._deliver`) or marked
  unread, so that stamp is when it last arrived;
* commencement check: the latest of the prompt's creation, the Teema's last
  change of hands (its newest `MATTER_ASSIGNED` event — `assign_matter`, the
  one path that moves an owner, always writes one), the reader's own state
  row's last change, and the Tallinn midnight on which a postponement ran
  out. A check that becomes a manager's because its owner's account was
  deactivated has no such moment and counts from its creation.

The boundary is never later than the start of any other transaction still
open in this database (:func:`boundary`): whatever such a transaction has
written — a generator's rows, a colleague's hand-over and its «Uus asi» — is
invisible to the panel while carrying a time before its commit, and must not
be swept up by a press made after it appears.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime
from typing import Any

from django.db import connection, transaction
from django.utils import timezone

from app.core.authorization import may_read_business_content
from app.notifications import deadlines, inbox, selectors, services
from app.notifications.feed import unreadable_assignment_matters
from app.notifications.inbox import TYPE_ASSIGNMENT, TYPE_COMMENCEMENT, TYPE_DEADLINE
from app.notifications.models import CommencementReminderPersonalState

#: What a malformed or missing boundary is told. A form drawn by this
#: application always carries one, so this is a stale or hand-made request.
BOUNDARY_REFUSED = "Teavituste seis on aegunud. Ava teavitused uuesti ja proovi siis."


def boundary(now: datetime | None = None) -> datetime:
    """The moment the inbox the reader is being shown is complete up to.

    Normally now. While another transaction in this database is open, the
    start of the oldest one instead: anything it has written so far carries a
    time after its start and is not yet visible, so it is outside the boundary
    (a generator's rows, `assign_matter`'s «Uus asi», a reader's own second
    tab). Conservative: an unrelated long transaction only means a few items
    stay unread, and «Märgi kõik loetuks» says so. Read from
    `pg_stat_activity`; a session the database does not show this role (a
    different role without `pg_read_all_stats`) has a NULL start and is left
    out, which is the behaviour without this refinement.
    """
    moment = now or timezone.now()
    with connection.cursor() as cursor:
        cursor.execute(
            "SELECT min(xact_start) FROM pg_stat_activity "
            "WHERE datname = current_database() AND pid <> pg_backend_pid() "
            "AND xact_start IS NOT NULL"
        )
        started = cursor.fetchone()[0]
    return min(moment, started) if started is not None else moment


def encode_boundary(moment: datetime) -> str:
    return moment.isoformat()


def decode_boundary(text: Any) -> datetime | None:
    """The boundary a form sent back, never later than now; ``None`` if it is not one."""
    try:
        moment = datetime.fromisoformat(str(text or ""))
    except ValueError:
        return None
    if timezone.is_naive(moment):
        return None
    return min(moment, timezone.now())


# ---------------------------------------------------------------------------
# One item
# ---------------------------------------------------------------------------


def _reader(user: Any) -> bool:
    return (
        bool(getattr(user, "is_authenticated", False))
        and getattr(user, "pk", None) is not None
        and may_read_business_content(user)
    )


def _own_notice(user: Any, pk: Any) -> Any:
    from app.matters.models import MatterAssignmentNotice

    return (
        MatterAssignmentNotice.objects.filter(recipient=user, pk=pk, superseded_at__isnull=True)
        .select_related("matter")
        .first()
    )


def resolve(user: Any, kind: str, pk: Any) -> Any:
    """The reader's own row an item names, or ``None`` — never anybody else's.

    A deadline notice: the recipient's outstanding row. A commencement check:
    a prompt in the reader's inbox population (their desk; the unowned ones for
    management). «Uus asi»: the reader's own receipt that has not been handed
    on. Anything else, including another person's id, is ``None`` (a 404).
    """
    if not _reader(user):
        return None
    if kind == TYPE_DEADLINE:
        return deadlines.own_row(user, pk)
    if kind == TYPE_COMMENCEMENT:
        return selectors.readable_inbox_reminder(user, pk)
    if kind == TYPE_ASSIGNMENT:
        return _own_notice(user, pk)
    return None


def mark_read(user: Any, kind: str, row: Any) -> bool:
    """«Märgi loetuks» on a row :func:`resolve` returned. A repeat is a no-op."""
    if kind == TYPE_DEADLINE:
        before = row.read_at
        deadlines.mark_read(notification=row)
        return before is None
    if kind == TYPE_COMMENCEMENT:
        services.mark_reminder_read(reminder=row, reader=user)
        return True
    if kind == TYPE_ASSIGNMENT:
        from app.matters.person_work import unread_assignment_notice_queryset
        from app.matters.services import acknowledge_assignment_notice

        if not unread_assignment_notice_queryset(user).filter(pk=row.pk).exists():
            # Opened or marked read already, or no longer the reader's to
            # acknowledge (closed, restricted, handed on): nothing to stamp.
            return False
        return acknowledge_assignment_notice(notice=row, actor=user)
    return False  # pragma: no cover - `resolve` admits no other kind


def mark_unread(user: Any, kind: str, row: Any) -> bool:
    """«Märgi lugemata»: undo the reader's own receipt, where the item would then be awaiting them.

    A deadline notice the recipient has not hidden; a commencement check still
    in the reader's inbox population; a «Uus asi» receipt about a Teema that is
    still the reader's own, open and readable, with no newer receipt about it.
    Returns whether anything changed.
    """
    if kind == TYPE_DEADLINE:
        return deadlines.mark_unread(notification=row)
    if kind == TYPE_COMMENCEMENT:
        return services.mark_reminder_unread(reminder=row, reader=user)
    if kind == TYPE_ASSIGNMENT:
        return _mark_notice_unread(user, row)
    return False  # pragma: no cover - `resolve` admits no other kind


def _mark_notice_unread(user: Any, notice: Any) -> bool:
    from django.db import IntegrityError

    from app.matters.models import MatterAssignmentNotice

    if notice.viewed_at is None or notice.matter_id not in unreadable_assignment_matters(
        user, [notice]
    ):
        return False
    now = timezone.now()
    try:
        with transaction.atomic():
            return bool(
                MatterAssignmentNotice.objects.filter(
                    pk=notice.pk,
                    recipient=user,
                    viewed_at__isnull=False,
                    superseded_at__isnull=True,
                ).update(viewed_at=None, updated_at=now)
            )
    except IntegrityError:
        # A newer receipt about the same Teema became active meanwhile: that
        # one is the unread item, and this stays read.
        return False


# ---------------------------------------------------------------------------
# Everything at once
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class MarkAllResult:
    marked: int
    #: Still unread afterwards: arrived after the boundary.
    remaining: int

    @property
    def message(self) -> str:
        if self.marked == 0 and self.remaining == 0:
            return "Lugemata teavitusi ei olnud."
        text = f"Loetuks märgitud: {self.marked}."
        if self.remaining:
            text += (
                f" Lugemata jäi {self.remaining}: need võisid lisanduda pärast seda,"
                " kui teavitused avasid."
            )
        return text


def _midnight(day: date | None) -> datetime | None:
    if day is None:
        return None
    return timezone.make_aware(datetime.combine(day, datetime.min.time()))


def _commencement_arrivals(user: Any, rows: list[Any]) -> dict[Any, datetime]:
    """When each awaiting commencement check last arrived for ``user`` (see the module note)."""
    from django.db.models import Max

    from app.audit.enums import ChangeEventType
    from app.audit.models import ChangeEvent

    states = {
        state.reminder_id: state
        for state in CommencementReminderPersonalState.objects.filter(
            user_id=user.pk, reminder_id__in=[row.pk for row in rows]
        )
    }
    handed = dict(
        ChangeEvent.objects.filter(
            event_type=ChangeEventType.MATTER_ASSIGNED,
            matter_id__in={row.reminder.effective_date.matter_id for row in rows},
        )
        .values("matter_id")
        .annotate(last=Max("occurred_at"))
        .values_list("matter_id", "last")
    )
    arrivals = {}
    for row in rows:
        reminder = row.reminder
        state = states.get(reminder.pk)
        moments = [
            reminder.created_at,
            handed.get(reminder.effective_date.matter_id),
            _midnight(reminder.deferred_until),
        ]
        if state is not None:
            moments += [state.updated_at, _midnight(state.deferred_until)]
        arrivals[reminder.pk] = max(moment for moment in moments if moment is not None)
    return arrivals


def mark_all_read(user: Any, *, until: datetime, today: date | None = None) -> MarkAllResult:
    """«Märgi kõik loetuks»: every item awaiting ``user`` that arrived by ``until``.

    All of the reader's populations, not the page the panel showed: a
    deadline notice, a commencement check (the reader's own receipt) and a
    «Uus asi». Nothing that arrived after ``until`` is touched.
    """
    if not _reader(user):
        return MarkAllResult(0, 0)
    from app.matters.person_work import unread_assignment_notice_queryset
    from app.matters.services import acknowledge_assignment_notice

    day = today or timezone.localdate()
    cut = min(until, timezone.now())
    now = timezone.now()
    marked = 0
    with transaction.atomic():
        marked += (
            inbox.deadline_rows(user, day)
            .filter(updated_at__lte=cut)
            .update(read_at=now, updated_at=now)
        )
        rows = selectors.open_reminders_for(user, today=day)
        rows += selectors.department_unowned_reminders(user, today=day, unread_only=True)
        arrivals = _commencement_arrivals(user, rows)
        for row in rows:
            if arrivals[row.pk] <= cut:
                services.mark_reminder_read(reminder=row.reminder, reader=user)
                marked += 1
        for notice in unread_assignment_notice_queryset(user).filter(updated_at__lte=cut):
            marked += int(acknowledge_assignment_notice(notice=notice, actor=user))
    return MarkAllResult(marked, inbox.inbox_count(user, day))


__all__ = [
    "BOUNDARY_REFUSED",
    "MarkAllResult",
    "boundary",
    "decode_boundary",
    "encode_boundary",
    "mark_all_read",
    "mark_read",
    "mark_unread",
    "resolve",
]
