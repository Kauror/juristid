"""«Teavitused» — one inbox, read by the bell, Minu asjad and `/teavitused/` (docs/adr/0198 §6).

Three sources, each through its own authorization and its own rules, and
nothing else:

1. the reader's live `DeadlineNotification` rows — not retired, not hidden,
   re-validated against their source *as the reader* (`deadlines.live_q`), and
   shown as the reader's preferences currently say;
2. open «Kontrolli jõustumist» checks (ADR 0188 unchanged): the reader's own,
   and — for `department.view_management` — the ones on Teemad nobody is
   responsible for, exactly the population the bell has counted since 0188;
3. unread «Uus asi» receipts (ADR 0051 unchanged: personal, acknowledged only
   by its own POST).

:func:`inbox_for` lists them and :func:`inbox_count` counts the same
populations with three ``COUNT`` statements, so the bell, the Minu asjad block
and the list page agree by construction.

**Order.** By date — the deadline for a deadline notice and a commencement
check, the day of the hand-over for «Uus asi» — so the most urgent (an overdue
day) comes first; on the same date «Vajab tegevust» before «Teadmiseks»; then
the Teema title. The list page shows the two kinds as two sections in that
same order; unowned commencement checks get a section of their own, as before.

**Never a colleague's.** Every function takes the signed-in user and reads
``recipient=user`` / the user's own desk. Minu asjad calls it only in its
``is_self`` branch, so `/inimesed/<id>/asjad/` never queries it.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from typing import Any

from django.db.models import QuerySet
from django.urls import reverse
from django.utils import timezone

from app.core.authorization import may_read_business_content, may_view_department_management
from app.core.dates import format_estonian_date
from app.notifications import selectors as commencement
from app.notifications.categories import CATEGORIES, ESCALATION_KEY
from app.notifications.deadlines import live_q, outstanding
from app.notifications.enums import NotificationKind, NotificationPurpose
from app.notifications.models import DeadlineNotification
from app.notifications.preferences import Choices, choices_for

ACTION = NotificationKind.ACTION.value
INFO = NotificationKind.INFO.value

TYPE_DEADLINE = "deadline"
TYPE_COMMENCEMENT = "commencement"
TYPE_ASSIGNMENT = "assignment"

#: Where an item is listed on `/teavitused/`.
SECTION_ACTION = "action"
SECTION_INFO = "info"
SECTION_UNOWNED = "unowned"

KIND_LABELS = {ACTION: NotificationKind.ACTION.label, INFO: NotificationKind.INFO.label}


@dataclass(frozen=True)
class InboxItem:
    type: str
    kind: str
    title: str
    label: str
    date: date
    date_label: str
    url: str
    is_unread: bool
    pk: Any
    section: str
    can_read: bool = False
    can_hide: bool = False
    #: The commencement row the existing answer forms render (ADR 0188).
    reminder: Any = None
    #: Extra context words: who is responsible, for a management notice.
    note: str = ""
    sort_title: str = field(default="", compare=False)

    @property
    def kind_label(self) -> str:
        return KIND_LABELS[self.kind]

    @property
    def sort_key(self) -> tuple[Any, ...]:
        return (
            self.date,
            0 if self.kind == ACTION else 1,
            self.sort_title or self.title,
            str(self.pk),
        )


def _today(today: date | None) -> date:
    return today or timezone.localdate()


def _signed_in(user: Any) -> bool:
    return bool(getattr(user, "is_authenticated", False)) and getattr(user, "pk", None) is not None


def _when(source_date: date, today: date) -> str:
    """How far off the date is, in words — never a verdict about what happened on it."""
    days = (source_date - today).days
    if days == 0:
        return "täna"
    if days == 1:
        return "homme"
    if days > 1:
        return f"{days} päeva pärast"
    if days == -1:
        return "eile"
    return f"{-days} päeva tagasi"


def deadline_rows(
    user: Any, today: date | None = None, *, choices: Choices | None = None
) -> QuerySet[DeadlineNotification]:
    """The reader's own live deadline rows — the population both the list and the count read."""
    if not _signed_in(user) or not may_read_business_content(user):
        return DeadlineNotification.objects.none()
    day = _today(today)
    chosen = choices if choices is not None else choices_for(user)
    return (
        outstanding()
        .filter(recipient=user, dismissed_at__isnull=True)
        .filter(
            live_q(user, day, choices=chosen, holds_management=may_view_department_management(user))
        )
    )


def _deadline_item(row: DeadlineNotification, today: date) -> InboxItem:
    category = CATEGORIES[row.category]
    matter = row.matter
    if row.purpose == NotificationPurpose.ESCALATION:
        label = "Tähtaeg ületatud"
        owner = matter.owner
        note = (
            f"Vastutaja {owner.display_name}"
            if owner is not None and owner.is_active
            else "Vastutaja puudub"
        )
    elif row.purpose == NotificationPurpose.ADVANCE:
        label = category.advance_label
        note = ""
    else:
        label = category.on_day_label
        note = ""
    date_label = (
        f"{category.date_label} {format_estonian_date(row.source_date)} · "
        f"{_when(row.source_date, today)}"
    )
    return InboxItem(
        type=TYPE_DEADLINE,
        kind=row.kind,
        title=matter.title,
        label=label,
        date=row.source_date,
        date_label=date_label,
        url=reverse("matters:matter_detail", kwargs={"pk": matter.pk}) + category.anchor,
        is_unread=row.read_at is None,
        pk=row.pk,
        section=SECTION_ACTION if row.kind == ACTION else SECTION_INFO,
        can_read=row.read_at is None,
        can_hide=row.kind == INFO,
        note=note,
    )


def _commencement_items(user: Any, today: date) -> list[InboxItem]:
    rows = [(row, SECTION_ACTION) for row in commencement.open_reminders_for(user, today=today)]
    rows += [
        (row, SECTION_UNOWNED)
        for row in commencement.department_unowned_reminders(user, today=today)
    ]
    return [
        InboxItem(
            type=TYPE_COMMENCEMENT,
            kind=ACTION,
            title=row.matter_title,
            label=commencement.CHECK_LABEL,
            date=row.reminder.source_date,
            date_label=f"Kirjas olev jõustumise kuupäev {row.date_label} · {row.stage_label}",
            url=row.matter_url,
            is_unread=False,
            pk=row.pk,
            section=section,
            reminder=row,
        )
        for row, section in rows
    ]


def _assignment_queryset(user: Any) -> QuerySet[Any]:
    from app.matters.person_work import unread_assignment_notice_queryset

    return unread_assignment_notice_queryset(user)


def _assignment_items(user: Any) -> list[InboxItem]:
    items = []
    for notice in _assignment_queryset(user):
        day = timezone.localdate(notice.created_at)
        items.append(
            InboxItem(
                type=TYPE_ASSIGNMENT,
                kind=ACTION,
                title=notice.matter.title,
                label="Uus asi",
                date=day,
                date_label=f"Sulle määratud {format_estonian_date(day)}",
                url=reverse("matters:open_assignment_notice", kwargs={"notice_id": notice.pk}),
                is_unread=True,
                pk=notice.pk,
                section=SECTION_ACTION,
            )
        )
    return items


def inbox_for(user: Any, today: date | None = None) -> list[InboxItem]:
    """Everything the signed-in reader is told about now, in one order."""
    if not _signed_in(user):
        return []
    day = _today(today)
    rows = deadline_rows(user, day).select_related("matter", "matter__owner")
    items = [_deadline_item(row, day) for row in rows]
    items += _commencement_items(user, day)
    items += _assignment_items(user)
    return sorted(items, key=lambda item: item.sort_key)


def inbox_count(user: Any, today: date | None = None) -> int:
    """The bell's number: :func:`inbox_for`'s population, counted without loading it."""
    if not _signed_in(user):
        return 0
    day = _today(today)
    return (
        deadline_rows(user, day).count()
        + commencement.unresolved_count(user, today=day)
        + _assignment_queryset(user).count()
    )


def shows_escalation_preference(user: Any) -> bool:
    """Whether the settings page offers the management escalation switch."""
    return may_view_department_management(user)


__all__ = [
    "ESCALATION_KEY",
    "InboxItem",
    "deadline_rows",
    "inbox_count",
    "inbox_for",
]
