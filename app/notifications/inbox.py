"""«Teavitused» — one inbox, read by the bell, Minu asjad and `/teavitused/` (docs/adr/0198 §6).

Three sources, each through its own authorization and its own rules, and
nothing else:

1. the reader's live `DeadlineNotification` rows — not retired, not hidden,
   not a «Teadmiseks» row the reader has marked read, re-validated against
   their source *as the reader* (`deadlines.live_q`), and shown as the
   reader's preferences currently say;
2. open «Kontrolli jõustumist» checks (ADR 0188): the reader's own, and — for
   `department.view_management` — the ones on Teemad nobody is responsible
   for, less those this reader has marked read (a personal receipt; the check
   stays open for everybody else — ADR 0188 amendment 2026-11-02);
3. unread «Uus asi» receipts (ADR 0051 unchanged: personal, acknowledged only
   by its own POST).

:func:`inbox_for` lists them and :func:`inbox_count` counts the same
populations with three ``COUNT`` statements, so the bell, the Minu asjad block
and the list page agree by construction.

**What "in the inbox" means (UI-01, November 2026).** The inbox is what is
*still awaiting this reader's attention*: an item they have not read, that has
not been resolved by the system, and that is not put off. The bell counts it,
the Minu asjad block lists it, and the panel and `/teavitused/` draw it as
unread. Reading any item — a «Vajab tegevust» deadline notice included — takes
it out of all of them at once; its history stays in the panel and on
`/teavitused/` (`app.notifications.feed`). Reading is never doing the work:
the deadline, the current step and the Teema are untouched, and the Teema's
own lists still show the date (docs/adr/0198 §7).

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
from datetime import date, datetime
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

#: The tie-break between sources at the same moment, for a stable newest-first
#: order and a keyset cursor (`app.notifications.feed`).
TYPE_RANK = {TYPE_DEADLINE: 0, TYPE_COMMENCEMENT: 1, TYPE_ASSIGNMENT: 2}

#: The one state vocabulary the panel, the page and the block draw.
STATE_UNREAD = "unread"
STATE_READ = "read"
#: Done by the system rather than by the reader — the work was done, the date
#: moved, the file handed on — or no longer this reader's to see.
STATE_RESOLVED = "resolved"


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
    #: The commencement row the existing answer forms render (ADR 0188).
    reminder: Any = None
    #: Extra context words: who is responsible, for a management notice.
    note: str = ""
    sort_title: str = field(default="", compare=False)
    #: When the item arrived: the row's creation. Orders the panel newest first.
    at: datetime | None = field(default=None, compare=False)
    state: str = STATE_UNREAD
    #: «Märgi lugemata» is offered: the reader's own read state can be undone
    #: and the item would then be back in their inbox.
    can_unread: bool = False
    #: «Lükka edasi» (a commencement check, ADR 0188 amendment 2026-11-02).
    can_defer: bool = False
    #: «Uus asi» opens through its own acknowledging POST (ADR 0051).
    opens_by_post: bool = False

    @property
    def kind_label(self) -> str:
        return KIND_LABELS[self.kind]

    @property
    def rank(self) -> int:
        return TYPE_RANK[self.type]

    @property
    def sort_key(self) -> tuple[Any, ...]:
        return (
            self.date,
            0 if self.kind == ACTION else 1,
            self.sort_title or self.title,
            str(self.pk),
        )

    @property
    def has_menu(self) -> bool:
        return self.can_read or self.can_unread or self.can_defer

    def _route(self, verb: str) -> str:
        name, key = _ROUTES[(self.type, verb)]
        return reverse(name, kwargs={key: self.pk})

    @property
    def read_url(self) -> str:
        return self._route("read")

    @property
    def unread_url(self) -> str:
        return self._route("unread")

    @property
    def defer_url(self) -> str:
        return self._route("defer")


#: The reader's own POST for each answer on each kind of item.
_ROUTES: dict[tuple[str, str], tuple[str, str]] = {
    (TYPE_DEADLINE, "read"): ("notifications:read", "notification_id"),
    (TYPE_DEADLINE, "unread"): ("notifications:unread", "notification_id"),
    (TYPE_COMMENCEMENT, "read"): ("notifications:reminder_read", "pk"),
    (TYPE_COMMENCEMENT, "unread"): ("notifications:reminder_unread", "pk"),
    (TYPE_COMMENCEMENT, "defer"): ("notifications:defer", "pk"),
    (TYPE_ASSIGNMENT, "read"): ("notifications:assignment_read", "notice_id"),
    (TYPE_ASSIGNMENT, "unread"): ("notifications:assignment_unread", "notice_id"),
}


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


def live_deadline_rows(
    user: Any, today: date | None = None, *, choices: Choices | None = None
) -> QuerySet[DeadlineNotification]:
    """The reader's own deadline rows that still describe their source, read or not.

    Not retired, not hidden, re-validated against the source as the reader and
    shown as their preferences say. The rows a «Märgi lugemata» may return to
    the inbox are exactly these.
    """
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


def deadline_rows(
    user: Any, today: date | None = None, *, choices: Choices | None = None
) -> QuerySet[DeadlineNotification]:
    """The reader's own unread live deadline rows — the population the list and the count read.

    «Märgi loetuks» is the reader saying «seen», on a «Teadmiseks» and a
    «Vajab tegevust» row alike (UI-01): the row leaves the bell, the block and
    the inbox sections, and stays in the history. Each interval is its own row,
    so reading the seven-day notice leaves the three-day one owed.
    """
    return live_deadline_rows(user, today, choices=choices).filter(read_at__isnull=True)


def responsible_names(rows: list[DeadlineNotification]) -> dict[Any, str]:
    """Who is responsible for each escalated source, by the category's own rule.

    For a step that is the step's active `responsible`, else the Matter's owner
    (owner decision D2) — `Category.state`, the rule the generator reads. Two
    statements per category at most and one for the people, however many rows.
    """
    from app.accounts.models import User

    by_category: dict[str, list[Any]] = {}
    for row in rows:
        if row.purpose == NotificationPurpose.ESCALATION:
            by_category.setdefault(row.category, []).append(row.source_id)
    responsible: dict[Any, Any] = {}
    for key, ids in by_category.items():
        for source_id, state in CATEGORIES[key].state(ids).items():
            responsible[source_id] = state.responsible_id
    people = dict(
        User.objects.filter(
            pk__in={pk for pk in responsible.values() if pk}, is_active=True
        ).values_list("pk", "display_name")
    )
    return {
        source_id: people.get(person, "") if person else ""
        for source_id, person in responsible.items()
    }


def deadline_item(
    row: DeadlineNotification,
    today: date,
    names: dict[Any, str],
    *,
    state: str = STATE_UNREAD,
    can_unread: bool = False,
) -> InboxItem:
    """One deadline row as an item, in the state the caller has decided it is in."""
    category = CATEGORIES[row.category]
    matter = row.matter
    if row.purpose == NotificationPurpose.ESCALATION:
        label = category.escalation_label
        name = names.get(row.source_id, "")
        note = f"Vastutaja {name}" if name else "Vastutaja puudub"
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
        is_unread=state == STATE_UNREAD,
        pk=row.pk,
        section=SECTION_ACTION if row.kind == ACTION else SECTION_INFO,
        can_read=state == STATE_UNREAD,
        note=note,
        at=row.created_at,
        state=state,
        can_unread=can_unread,
    )


def commencement_item(
    row: Any, *, section: str = SECTION_ACTION, state: str = STATE_UNREAD, can_unread: bool = False
) -> InboxItem:
    """One «Kontrolli jõustumist» row (`selectors.ReminderRow`) as an item."""
    unread = state == STATE_UNREAD
    return InboxItem(
        type=TYPE_COMMENCEMENT,
        kind=ACTION,
        title=row.matter_title,
        label=commencement.CHECK_LABEL,
        date=row.reminder.source_date,
        date_label=f"Jõustumise kuupäev {row.date_label} · {row.stage_label}",
        url=row.matter_url,
        is_unread=unread,
        pk=row.pk,
        section=section,
        can_read=unread,
        reminder=row,
        at=row.reminder.created_at,
        state=state,
        can_unread=can_unread,
        can_defer=unread,
    )


def _commencement_items(user: Any, today: date) -> list[InboxItem]:
    rows = [(row, SECTION_ACTION) for row in commencement.open_reminders_for(user, today=today)]
    rows += [
        (row, SECTION_UNOWNED)
        for row in commencement.department_unowned_reminders(user, today=today, unread_only=True)
    ]
    return [commencement_item(row, section=section) for row, section in rows]


def _assignment_queryset(user: Any) -> QuerySet[Any]:
    from app.matters.person_work import unread_assignment_notice_queryset

    return unread_assignment_notice_queryset(user)


#: The label of an «Uus asi» item, once.
ASSIGNMENT_LABEL = "Uus asi"


def assignment_item(
    notice: Any, *, state: str = STATE_UNREAD, can_unread: bool = False
) -> InboxItem:
    """One «Uus asi» receipt as an item.

    Unread, its title is the acknowledging POST (ADR 0051: opening it from
    here is the receipt). Read or retired, it is an ordinary link to the
    Teema, because there is nothing left to acknowledge.
    """
    day = timezone.localdate(notice.created_at)
    unread = state == STATE_UNREAD
    if unread:
        url = reverse("matters:open_assignment_notice", kwargs={"notice_id": notice.pk})
    else:
        url = reverse("matters:matter_detail", kwargs={"pk": notice.matter_id})
    return InboxItem(
        type=TYPE_ASSIGNMENT,
        kind=ACTION,
        title=notice.matter.title,
        label=ASSIGNMENT_LABEL,
        date=day,
        date_label=f"Sulle määratud {format_estonian_date(day)}",
        url=url,
        is_unread=unread,
        pk=notice.pk,
        section=SECTION_ACTION,
        can_read=unread,
        at=notice.created_at,
        state=state,
        can_unread=can_unread,
        opens_by_post=unread,
    )


def _assignment_items(user: Any) -> list[InboxItem]:
    return [assignment_item(notice) for notice in _assignment_queryset(user)]


def inbox_for(user: Any, today: date | None = None) -> list[InboxItem]:
    """Everything awaiting the signed-in reader's attention now, in one order."""
    if not _signed_in(user):
        return []
    day = _today(today)
    rows = list(deadline_rows(user, day).select_related("matter"))
    names = responsible_names(rows)
    items = [deadline_item(row, day, names) for row in rows]
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
