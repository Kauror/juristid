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

**Read or put off by one person, still open for the rest.** «Märgi loetuks»
and «Lükka edasi» are a `CommencementReminderPersonalState` of the reader's
own: they take the prompt out of *their* populations here (``_active_for``)
and leave the reminder ``OPEN`` for a new owner and for every other manager
(ADR 0188 amendment 2026-11-02). A deferral recorded before that day on the
reminder itself (``CommencementReminder.deferred_until``) still applies to
everybody, as it did when it was made.

**``status=OPEN`` is not a count of unchecked commencements.** Nobody closes a
prompt any more — reading and postponing are personal and «Kontrollitud» and
«Sulge…» are retired — so open rows accumulate by design. No metric may be
built on that status alone.

**Authorization first.** Every population starts from the commencements the
reader may see (`MatterEffectiveDate.objects.visible_to`), so a restricted
record never reaches a count or a list (ADR 0181, ADR 0158 §3).
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from typing import Any

from django.db.models import Exists, F, OuterRef, Q, QuerySet
from django.urls import reverse
from django.utils import timezone

from app.core.authorization import may_view_department_management
from app.core.dates import format_estonian_date
from app.intelligence.models import MatterEffectiveDate
from app.notifications.enums import ReminderStatus
from app.notifications.models import CommencementReminder, CommencementReminderPersonalState
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


def _states(user: Any) -> QuerySet[CommencementReminderPersonalState]:
    return CommencementReminderPersonalState.objects.filter(
        reminder=OuterRef("pk"), user_id=user.pk
    )


def _active_for(user: Any, today: date) -> Q:
    """Leaves out what this reader has read, or put off past ``today`` — theirs only."""
    return ~Q(Exists(_states(user).filter(Q(read_at__isnull=False) | Q(deferred_until__gt=today))))


def _deferred_by(user: Any, today: date) -> Q:
    """The prompts this reader has put off past ``today`` and not read."""
    return Q(Exists(_states(user).filter(read_at__isnull=True, deferred_until__gt=today)))


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


def _later(first: date | None, second: date | None) -> date | None:
    """The later of two optional dates — when a put-off prompt comes back."""
    dates = [day for day in (first, second) if day is not None]
    return max(dates) if dates else None


def _when(source_date: date, today: date) -> str:
    """How far off the date is, in words. Never what happened on it."""
    days = (source_date - today).days
    if days == 0:
        return "täna"
    if days == 1:
        return "homme"
    if days > 1:
        return f"{days} päeva pärast"
    return "kuupäev on möödunud"


def _rows(
    queryset: QuerySet[CommencementReminder], today: date, *, reader: Any = None
) -> list[ReminderRow]:
    reminders = queryset.select_related(
        "effective_date", "effective_date__matter", "effective_date__matter__owner"
    ).order_by("source_date", "effective_date__matter__title", "pk")
    personal: dict[Any, date | None] = {}
    if reader is not None:
        personal = dict(
            CommencementReminderPersonalState.objects.filter(
                user_id=reader.pk, reminder__in=reminders
            ).values_list("reminder_id", "deferred_until")
        )
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
                deferred_label=format_estonian_date(
                    _later(personal.get(reminder.pk), reminder.deferred_until)
                ),
            )
        )
    return rows


def open_reminders_for(user: Any, *, today: date | None = None) -> list[ReminderRow]:
    """The reminders ``user`` is asked to act on now: their own Matters' open prompts."""
    if not getattr(user, "is_authenticated", False):
        return []
    day = _today(today)
    return _rows(_live_open(user, day).filter(_owned_by(user), _active_for(user, day)), day)


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
    return _live_open(user, day).filter(scope).filter(_active_for(user, day)).count()


def department_unowned_reminders(
    user: Any, *, today: date | None = None, unread_only: bool = False
) -> list[ReminderRow]:
    """Open prompts on Matters with nobody responsible — for the management view only.

    Empty for anybody without `department.view_management`, so the caller does not
    have to remember the check (and a template cannot render what it never got).
    """
    if not may_view_department_management(user):
        return []
    day = _today(today)
    queryset = _live_open(user, day).filter(_unowned())
    if unread_only:
        # The inbox: this manager's own reading or postponing leaves it.
        # Osakond's summary is the department's open work, which one person's
        # notification state does not end.
        queryset = queryset.filter(_active_for(user, day))
    return _rows(queryset, day)


def deferred_reminders_for(user: Any, *, today: date | None = None) -> list[ReminderRow]:
    """The prompts put off for this reader — shown, quietly and uncounted, on the list page.

    Two kinds, both still open and still describing their commencement: the
    reader's own personal deferrals (on their desk, or unowned ones for
    management), and the deferrals recorded on a reminder of their desk before
    deferring became personal (2026-11-02), which still hold for everybody.
    """
    if not getattr(user, "is_authenticated", False):
        return []
    day = _today(today)
    readable = MatterEffectiveDate.objects.visible_to(user).values("pk")
    scope = _owned_by(user)
    if may_view_department_management(user):
        scope = scope | _unowned()
    queryset = (
        CommencementReminder.objects.open()
        .filter(eligibility_q("effective_date__"))
        .filter(source_date=F("effective_date__date_value"))
        .filter(effective_date__in=readable)
        .filter(
            (scope & _deferred_by(user, day))
            | (_owned_by(user) & Q(deferred_until__gt=day) & _active_for(user, day))
        )
    )
    return _rows(queryset, day, reader=user)


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


def inbox_scope(user: Any) -> QuerySet[CommencementReminder]:
    """Every open, live prompt that belongs in this reader's inbox, whatever their own state.

    The reader's own desk, plus the unowned prompts for
    `department.view_management` — the population :func:`readable_inbox_reminder`
    resolves one row from, and the one a «Märgi lugemata» may return a prompt
    to. Empty for a caller who is not signed in.
    """
    if not getattr(user, "is_authenticated", False) or getattr(user, "pk", None) is None:
        return CommencementReminder.objects.none()
    readable = MatterEffectiveDate.objects.visible_to(user).values("pk")
    scope = _owned_by(user)
    if may_view_department_management(user):
        scope = scope | _unowned()
    return (
        CommencementReminder.objects.open()
        .filter(eligibility_q("effective_date__"))
        .filter(source_date=F("effective_date__date_value"))
        .filter(effective_date__in=readable)
        .filter(scope)
    )


def read_by(user: Any) -> QuerySet[CommencementReminder]:
    """The prompts this reader has marked read, on commencements they may still see.

    Their history (`app.notifications.feed`): whatever has happened to the
    prompt since — answered, moved, made obsolete — the reader's own receipt
    is what puts it there, so a colleague's reading never does.
    """
    if not getattr(user, "is_authenticated", False) or getattr(user, "pk", None) is None:
        return CommencementReminder.objects.none()
    readable = MatterEffectiveDate.objects.visible_to(user).values("pk")
    return CommencementReminder.objects.filter(
        Exists(_states(user).filter(read_at__isnull=False)), effective_date__in=readable
    )


def reminder_rows(
    queryset: QuerySet[CommencementReminder], *, today: date | None = None
) -> list[ReminderRow]:
    """:class:`ReminderRow` for any reminder queryset the caller has already scoped."""
    return _rows(queryset, _today(today))


def readable_inbox_reminder(user: Any, pk: Any) -> CommencementReminder | None:
    """The prompt a «Märgi loetuks» or «Lükka edasi» POST names, if it is in this reader's inbox.

    Narrower than :func:`answerable_reminder`: the reader's own desk, plus the
    unowned prompts for `department.view_management` — the populations the
    bell counts. A colleague's prompt is ``None`` (a 404), so viewing somebody's
    Minu asjad never lets anybody clear what that person is shown. Deferred
    prompts count: a receipt for one is harmless and the POST may race the date.
    """
    return inbox_scope(user).filter(pk=pk).first()
