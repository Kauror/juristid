"""Who is asked to check which commencement — decided when the page is drawn.

**Reminders follow current responsibility** (brief C4, docs/adr/0188 §5). No
row stores a recipient. A reminder is *for* the active Vastutaja of its Matter
at the moment somebody looks, so a Matter handed to a colleague takes its open
reminders with it on the next request, without a write and without a moment at
which two people — or nobody — hold them. A Matter with no Vastutaja, or whose
Vastutaja's account is no longer active, has nobody to ask: its reminders are
listed for the holders of `department.view_management` instead, and are never
assigned to a guessed person.

**Validity is read live as well.** A reminder whose commencement has since been
moved, cancelled, replaced, removed or made approximate is out of every list
here at once, through :func:`app.notifications.services.eligibility_q` and the
``source_date`` comparison, without waiting for the generator to retire it.

**Authorization first.** Every population starts from the commencements the
reader may see (`MatterEffectiveDate.objects.visible_to`), so a restricted
record never reaches a count or a list (ADR 0181, ADR 0158 §3).
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from typing import Any

from django.db.models import F, Q, QuerySet
from django.urls import reverse
from django.utils import timezone

from app.core.authorization import may_view_department_management
from app.core.dates import format_estonian_date
from app.intelligence.models import MatterEffectiveDate
from app.notifications.enums import ReminderStatus
from app.notifications.models import CommencementReminder
from app.notifications.services import eligibility_q

#: The wording, once. A reminder asks for a check and never states an outcome.
CHECK_LABEL = "Kontrolli jõustumist"


def _today(today: date | None) -> date:
    return today or timezone.localdate()


def _live_open(user: Any, today: date) -> QuerySet[CommencementReminder]:
    """Open, not deferred, still describing its commencement, readable by ``user``."""
    readable = MatterEffectiveDate.objects.visible_to(user).values("pk")
    return (
        CommencementReminder.objects.open()
        .filter(eligibility_q("effective_date__"))
        .filter(source_date=F("effective_date__date_value"))
        .filter(effective_date__in=readable)
        .filter(Q(deferred_until__isnull=True) | Q(deferred_until__lte=today))
    )


def _owned_by(user: Any) -> Q:
    return Q(effective_date__matter__owner=user, effective_date__matter__owner__is_active=True)


def _unowned() -> Q:
    return Q(effective_date__matter__owner__isnull=True) | Q(
        effective_date__matter__owner__is_active=False
    )


@dataclass(frozen=True)
class ReminderRow:
    """One line: what to check, on which Teema, for which date, from which source."""

    reminder: CommencementReminder
    matter_title: str
    matter_url: str
    description: str
    date_label: str
    stage_label: str
    is_due_today: bool
    is_past: bool
    source_url: str
    matter_is_open: bool
    owner_name: str
    deferred_label: str

    @property
    def pk(self) -> Any:
        return self.reminder.pk

    @property
    def label(self) -> str:
        return CHECK_LABEL


def _when(source_date: date, today: date) -> str:
    """How far off the date is, in words. Never what happened on it."""
    days = (source_date - today).days
    if days == 0:
        return "täna"
    if days == 1:
        return "homme"
    if days > 1:
        return f"{days} päeva pärast"
    return "kirjas olev kuupäev on möödunud"


def _rows(queryset: QuerySet[CommencementReminder], today: date) -> list[ReminderRow]:
    reminders = queryset.select_related(
        "effective_date", "effective_date__matter", "effective_date__matter__owner"
    ).order_by("source_date", "effective_date__matter__title", "pk")
    rows = []
    for reminder in reminders:
        record = reminder.effective_date
        matter = record.matter
        owner = matter.owner
        rows.append(
            ReminderRow(
                reminder=reminder,
                matter_title=matter.title,
                # The section anchor of the Teema page's «Jõustumine» block
                # (templates/intelligence/partials/matter_sections.html).
                matter_url=reverse("matters:matter_detail", kwargs={"pk": matter.pk})
                + "#joustumine",
                description=record.description,
                date_label=format_estonian_date(reminder.source_date),
                stage_label=_when(reminder.source_date, today),
                is_due_today=reminder.source_date == today,
                is_past=reminder.source_date < today,
                source_url=record.source_url,
                matter_is_open=matter.is_open,
                owner_name=owner.display_name if owner is not None and owner.is_active else "",
                deferred_label=format_estonian_date(reminder.deferred_until),
            )
        )
    return rows


def open_reminders_for(user: Any, *, today: date | None = None) -> list[ReminderRow]:
    """The reminders ``user`` is asked to act on now: their own Matters' open prompts."""
    if not getattr(user, "is_authenticated", False):
        return []
    day = _today(today)
    return _rows(_live_open(user, day).filter(_owned_by(user)), day)


def unresolved_count(user: Any, *, today: date | None = None) -> int:
    """The bell's number: the same population as :func:`open_reminders_for`, counted.

    For a holder of `department.view_management` it adds the unowned prompts,
    because those are the reminders nobody else will see.
    """
    if not getattr(user, "is_authenticated", False):
        return 0
    day = _today(today)
    scope = _owned_by(user)
    if may_view_department_management(user):
        scope = scope | _unowned()
    return _live_open(user, day).filter(scope).count()


def department_unowned_reminders(user: Any, *, today: date | None = None) -> list[ReminderRow]:
    """Open prompts on Matters with nobody responsible — for the management view only.

    Empty for anybody without `department.view_management`, so the caller does not
    have to remember the check (and a template cannot render what it never got).
    """
    if not may_view_department_management(user):
        return []
    day = _today(today)
    return _rows(_live_open(user, day).filter(_unowned()), day)


def deferred_reminders_for(user: Any, *, today: date | None = None) -> list[ReminderRow]:
    """The user's own prompts that are open but put off — shown, quietly, on the list page."""
    if not getattr(user, "is_authenticated", False):
        return []
    day = _today(today)
    readable = MatterEffectiveDate.objects.visible_to(user).values("pk")
    queryset = (
        CommencementReminder.objects.open()
        .filter(eligibility_q("effective_date__"))
        .filter(source_date=F("effective_date__date_value"))
        .filter(effective_date__in=readable, deferred_until__gt=day)
        .filter(_owned_by(user))
    )
    return _rows(queryset, day)


def answerable_reminder(user: Any, pk: Any) -> CommencementReminder | None:
    """The reminder a POST names, if this reader may see it and it is still live.

    Not narrowed to the reader's own desk: a business writer may answer any
    reminder on a Matter they can read — the department maintains these records
    collaboratively (`may_write_business_content`), and the department head must
    be able to answer the unowned ones. Who answered is recorded.
    """
    readable = MatterEffectiveDate.objects.visible_to(user).values("pk")
    # A superseded seven-day row still resolves: the page that drew it may
    # predate the on-the-day prompt, and its answer is about the same date
    # (`services._lock_open_reminder` answers the open sibling, or refuses).
    return (
        CommencementReminder.objects.filter(
            status__in=[ReminderStatus.OPEN, ReminderStatus.SUPERSEDED]
        )
        .filter(eligibility_q("effective_date__"))
        .filter(source_date=F("effective_date__date_value"))
        .filter(effective_date__in=readable, pk=pk)
        .select_related("effective_date", "effective_date__matter")
        .first()
    )
