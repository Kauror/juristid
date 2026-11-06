"""The reader's notifications, newest first: what the bell's panel and `/teavitused/` draw (UI-01).

`app.notifications.inbox` is what is *awaiting* the reader — the bell's number
and the Minu asjad block. This module adds what has already been dealt with,
so a notice the reader read, or one the system resolved, stays findable
instead of vanishing (owner's brief of 2026-11-06, UI-01). It reads; it never
writes, so opening the panel or turning a page of it marks nothing.

One state per item, from the rows that already hold it — no new flag:

=================  ==============================  ===================================
Source             Unread (awaiting attention)     Read / resolved (history)
=================  ==============================  ===================================
Deadline notice    `inbox.deadline_rows`: live,    ``read_at`` or ``dismissed_at`` set
(ADR 0198)         not hidden, ``read_at`` empty   (the recipient's), or ``retired_at``
                                                   set (the system's: done, moved,
                                                   handed on, superseded)
Commencement       `inbox`: open, live, the        this reader's own
check (ADR 0188)   reader's desk (or unowned, for  `CommencementReminderPersonalState
                   management), not read or put    .read_at`, whatever has happened to
                   off by this reader              the shared prompt since
«Uus asi»          `unread_assignment_notice_      ``viewed_at`` (opened or marked
(ADR 0051)         queryset`                       read) or ``superseded_at`` (handed
                                                   on) set
=================  ==============================  ===================================

An outstanding deadline row that is unread but hidden — its category switched
off, notifications paused, or its source already gone and the worker not yet
round — is in neither column: it is not awaiting attention and nothing has
happened to it, exactly as before.

**Order.** Unread items first, newest first; then the history, newest first.
"Newest" is when the row was created. Unread first because a prompt can reach
a reader long after its row was made — a Teema handed to them today carries a
commencement check recorded in March — and every item the bell counts must be
at the top of the panel, not on a page nobody loads. Ties break on the source
and the row's id, so the order is total and the cursor exact.

**Pages.** A keyset cursor — group, moment, source, id — rather than an
offset, so an item arriving between two pages neither repeats one nor hides
one. The unread group is small (it is what the Minu asjad block already
reads); the history is read a page at a time from each source.

**Authorization.** Every source starts from the reader's own rows and the
Teemad (or commencements) they may still see, so a Teema restricted or deleted
since takes its history with it, and an administrator without a business role
reads nothing (docs/adr/0181).
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from typing import Any

from django.db.models import Q, QuerySet
from django.utils import timezone

from app.core.authorization import may_read_business_content
from app.notifications import inbox
from app.notifications import selectors as commencement
from app.notifications.inbox import (
    STATE_READ,
    STATE_RESOLVED,
    TYPE_ASSIGNMENT,
    TYPE_COMMENCEMENT,
    TYPE_DEADLINE,
    TYPE_RANK,
    InboxItem,
)
from app.notifications.models import DeadlineNotification

#: How many items one page of the panel, or of the page's history, carries.
PAGE_SIZE = 15

GROUP_UNREAD = 0
GROUP_HISTORY = 1

_EPOCH = datetime(1970, 1, 1, tzinfo=UTC)
_MICROSECOND = timedelta(microseconds=1)


@dataclass(frozen=True)
class Cursor:
    """Where the previous page ended: everything after it, in feed order, comes next."""

    group: int
    at: datetime
    rank: int
    pk: uuid.UUID

    def encode(self) -> str:
        micros = (self.at - _EPOCH) // _MICROSECOND
        return f"{self.group}-{micros}-{self.rank}-{self.pk.hex}"

    @classmethod
    def decode(cls, text: str) -> Cursor | None:
        """The cursor a page link carries, or ``None`` for anything that is not one."""
        parts = str(text or "").split("-")
        if len(parts) != 4:
            return None
        group, micros, rank, hexed = parts
        if group not in ("0", "1") or rank not in {str(value) for value in TYPE_RANK.values()}:
            return None
        if not micros.isdigit() or len(micros) > 18:
            return None
        try:
            pk = uuid.UUID(hex=hexed)
            # A moment past what a datetime can hold is a hand-made link.
            at = _EPOCH + int(micros) * _MICROSECOND
        except (ValueError, OverflowError):
            return None
        return cls(int(group), at, int(rank), pk)

    @classmethod
    def after(cls, group: int, item: InboxItem) -> Cursor:
        return cls(group, _moment(item), item.rank, uuid.UUID(str(item.pk)))


@dataclass(frozen=True)
class Page:
    items: list[InboxItem]
    cursor: Cursor | None

    @property
    def next(self) -> str:
        return self.cursor.encode() if self.cursor is not None else ""


def _moment(item: InboxItem) -> datetime:
    if item.at is not None:
        return item.at
    return datetime.combine(item.date, datetime.min.time(), tzinfo=UTC)  # pragma: no cover


def _key(item: InboxItem) -> tuple[Any, ...]:
    return (_moment(item), item.rank, uuid.UUID(str(item.pk)))


def _before(item: InboxItem, cursor: Cursor) -> bool:
    return _key(item) < (cursor.at, cursor.rank, cursor.pk)


def _after_q(cursor: Cursor | None, rank: int) -> Q:
    """Rows of one source that come after ``cursor`` in the history's order."""
    if cursor is None or cursor.group == GROUP_UNREAD:
        return Q()
    if rank < cursor.rank:
        return Q(created_at__lte=cursor.at)
    if rank > cursor.rank:
        return Q(created_at__lt=cursor.at)
    return Q(created_at__lt=cursor.at) | Q(created_at=cursor.at, pk__lt=cursor.pk)


def _signed_in_reader(user: Any) -> bool:
    return (
        bool(getattr(user, "is_authenticated", False))
        and getattr(user, "pk", None) is not None
        and may_read_business_content(user)
    )


# ---------------------------------------------------------------------------
# The history, one source at a time
# ---------------------------------------------------------------------------


def _visible_matters(user: Any) -> QuerySet[Any]:
    from app.matters.models import Matter

    return Matter.objects.visible_to(user)


def deadline_history(user: Any, unread: set[Any]) -> QuerySet[DeadlineNotification]:
    """The reader's own deadline rows that have been read, hidden or resolved."""
    return (
        DeadlineNotification.objects.filter(recipient=user, matter__in=_visible_matters(user))
        .exclude(pk__in=unread)
        .filter(
            Q(read_at__isnull=False) | Q(dismissed_at__isnull=False) | Q(retired_at__isnull=False)
        )
    )


def commencement_history(user: Any, unread: set[Any]) -> QuerySet[Any]:
    """The prompts this reader has read, on commencements they may still see."""
    return commencement.read_by(user).exclude(pk__in=unread)


def assignment_history(user: Any, unread: set[Any]) -> QuerySet[Any]:
    """The reader's «Uus asi» receipts that are no longer awaiting them."""
    from app.matters.models import MatterAssignmentNotice

    return MatterAssignmentNotice.objects.filter(
        recipient=user, matter__in=_visible_matters(user)
    ).exclude(pk__in=unread)


def _deadline_items(user: Any, rows: list[DeadlineNotification], day: date) -> list[InboxItem]:
    if not rows:
        return []
    live = set(
        inbox.live_deadline_rows(user, day)
        .filter(pk__in=[row.pk for row in rows])
        .values_list("pk", flat=True)
    )
    names = inbox.responsible_names(rows)
    items = []
    for row in rows:
        handled = row.read_at is not None or row.dismissed_at is not None
        items.append(
            inbox.deadline_item(
                row,
                day,
                names,
                state=STATE_READ if handled else STATE_RESOLVED,
                can_unread=row.read_at is not None and row.pk in live,
            )
        )
    return items


def _commencement_items(user: Any, reminders: list[Any], day: date) -> list[InboxItem]:
    if not reminders:
        return []
    ids = [reminder.pk for reminder in reminders]
    rows = commencement.reminder_rows(commencement.read_by(user).filter(pk__in=ids), today=day)
    returnable = set(commencement.inbox_scope(user).filter(pk__in=ids).values_list("pk", flat=True))
    return [
        inbox.commencement_item(row, state=STATE_READ, can_unread=row.pk in returnable)
        for row in rows
    ]


def unreadable_assignment_matters(user: Any, notices: list[Any]) -> set[Any]:
    """The Teemad whose read «Uus asi» may become unread again: still this reader's
    own, open and readable, with no other receipt about them awaiting the reader
    (one active receipt per Teema and person — `matters_one_active_assignment_notice`)."""
    from app.matters.models import MatterAssignmentNotice

    matter_ids = {notice.matter_id for notice in notices}
    if not matter_ids:
        return set()
    owned = set(
        _visible_matters(user)
        .filter(pk__in=matter_ids, owner=user, is_open=True)
        .values_list("pk", flat=True)
    )
    active = set(
        MatterAssignmentNotice.objects.filter(
            recipient=user,
            matter_id__in=owned,
            viewed_at__isnull=True,
            superseded_at__isnull=True,
        ).values_list("matter_id", flat=True)
    )
    return owned - active


def _assignment_items(user: Any, notices: list[Any]) -> list[InboxItem]:
    returnable = unreadable_assignment_matters(
        user, [notice for notice in notices if notice.viewed_at and not notice.superseded_at]
    )
    items = []
    for notice in notices:
        viewed = notice.viewed_at is not None
        items.append(
            inbox.assignment_item(
                notice,
                state=STATE_READ if viewed else STATE_RESOLVED,
                can_unread=viewed
                and notice.superseded_at is None
                and notice.matter_id in returnable,
            )
        )
    return items


def _unread_ids(items: list[InboxItem]) -> dict[str, set[Any]]:
    ids: dict[str, set[Any]] = {
        TYPE_DEADLINE: set(),
        TYPE_COMMENCEMENT: set(),
        TYPE_ASSIGNMENT: set(),
    }
    for item in items:
        ids[item.type].add(item.pk)
    return ids


def _history(
    user: Any, day: date, unread: dict[str, set[Any]], cursor: Cursor | None, limit: int
) -> tuple[list[InboxItem], bool]:
    """Up to ``limit`` history items after ``cursor``, and whether more follow."""
    take = limit + 1
    order = ("-created_at", "-pk")
    deadline_rows = list(
        deadline_history(user, unread[TYPE_DEADLINE])
        .filter(_after_q(cursor, TYPE_RANK[TYPE_DEADLINE]))
        .select_related("matter")
        .order_by(*order)[:take]
    )
    reminders = list(
        commencement_history(user, unread[TYPE_COMMENCEMENT])
        .filter(_after_q(cursor, TYPE_RANK[TYPE_COMMENCEMENT]))
        .order_by(*order)[:take]
    )
    notices = list(
        assignment_history(user, unread[TYPE_ASSIGNMENT])
        .filter(_after_q(cursor, TYPE_RANK[TYPE_ASSIGNMENT]))
        .select_related("matter")
        .order_by(*order)[:take]
    )
    merged = sorted(
        [
            *_deadline_items(user, deadline_rows, day),
            *_commencement_items(user, reminders, day),
            *_assignment_items(user, notices),
        ],
        key=_key,
        reverse=True,
    )
    return merged[:limit], len(merged) > limit


def _unread(user: Any, day: date) -> list[InboxItem]:
    return sorted(inbox.inbox_for(user, day), key=_key, reverse=True)


def panel_page(
    user: Any, *, cursor: Cursor | None = None, today: date | None = None, limit: int = PAGE_SIZE
) -> Page:
    """One page of the panel: the unread items, then the history, after ``cursor``."""
    if not _signed_in_reader(user):
        return Page([], None)
    day = today or timezone.localdate()
    unread = _unread(user, day)
    items: list[InboxItem] = []
    if cursor is None or cursor.group == GROUP_UNREAD:
        rest = unread if cursor is None else [item for item in unread if _before(item, cursor)]
        items = rest[:limit]
        if len(rest) > limit:
            return Page(items, Cursor.after(GROUP_UNREAD, items[-1]))
        cursor = None
    room = limit - len(items)
    ids = _unread_ids(unread)
    if room <= 0:
        # The unread items fill this page exactly: the next one starts the
        # history, if there is any.
        _, more = _history(user, day, ids, None, 0)
        return Page(items, Cursor.after(GROUP_UNREAD, items[-1]) if more else None)
    history, more = _history(user, day, ids, cursor, room)
    items += history
    if more and history:
        return Page(items, Cursor.after(GROUP_HISTORY, history[-1]))
    return Page(items, None)


def history_page(
    user: Any, *, cursor: Cursor | None = None, today: date | None = None, limit: int = PAGE_SIZE
) -> Page:
    """One page of the history alone, for `/teavitused/` below its inbox sections."""
    if not _signed_in_reader(user):
        return Page([], None)
    day = today or timezone.localdate()
    if cursor is not None and cursor.group != GROUP_HISTORY:
        cursor = None
    unread = _unread_ids(inbox.inbox_for(user, day))
    history, more = _history(user, day, unread, cursor, limit)
    if more and history:
        return Page(history, Cursor.after(GROUP_HISTORY, history[-1]))
    return Page(history, None)


def lookup(user: Any, kind: str, pk: Any, *, today: date | None = None) -> InboxItem | None:
    """One item as the panel would draw it now, or ``None`` if it is in neither group.

    What an action from the panel answers with, so the row redraws in its new
    state in place instead of the whole panel reloading.
    """
    if not _signed_in_reader(user) or kind not in TYPE_RANK:
        return None
    day = today or timezone.localdate()
    unread = inbox.inbox_for(user, day)
    for item in unread:
        if item.type == kind and str(item.pk) == str(pk):
            return item
    ids = _unread_ids(unread)
    if kind == TYPE_DEADLINE:
        rows = list(deadline_history(user, ids[kind]).filter(pk=pk).select_related("matter"))
        found = _deadline_items(user, rows, day)
    elif kind == TYPE_COMMENCEMENT:
        found = _commencement_items(
            user, list(commencement_history(user, ids[kind]).filter(pk=pk)), day
        )
    else:
        notices = list(assignment_history(user, ids[kind]).filter(pk=pk).select_related("matter"))
        found = _assignment_items(user, notices)
    return found[0] if found else None


__all__ = [
    "GROUP_HISTORY",
    "GROUP_UNREAD",
    "PAGE_SIZE",
    "Cursor",
    "Page",
    "history_page",
    "lookup",
    "panel_page",
]
