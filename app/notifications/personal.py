"""«Lisa meeldetuletus» — a person's own reminder on a Teema (owner, 2026-11-16).

`PersonalReminder` is presentation state of the signed-in account, like a read
receipt (`app.notifications.acknowledgement`): set, read, unread and removed
by its own person only, through CSRF POSTs that name a row and take the person
from the session. No business service is called and no `ChangeEvent` written.

* **Upcoming** — set for a later day: listed on `/teavitused/`, where it can be
  removed.
* **Unread** — its day has come and the person has not read it: in the inbox
  (the bell's number, the panel, the Minu asjad block, the row's «!»).
* **Read** — the history, findable like every other notice
  (`app.notifications.feed`).

Every list starts from the person's own rows on Teemad they may still see, so
a Teema deleted or otherwise gone from view takes its reminders out of every list.
"""

from __future__ import annotations

from datetime import date, datetime
from typing import Any

from django.db.models import QuerySet
from django.utils import timezone

from app.core.authorization import may_read_business_content
from app.core.errors import DomainError
from app.notifications.models import PersonalReminder

#: Longest note, the model's own limit.
NOTE_MAX = 200


def _reader(user: Any) -> bool:
    return (
        bool(getattr(user, "is_authenticated", False))
        and getattr(user, "pk", None) is not None
        and may_read_business_content(user)
    )


def _visible_matters(user: Any) -> QuerySet[Any]:
    from app.matters.models import Matter

    return Matter.objects.visible_to(user)


def own(user: Any) -> QuerySet[PersonalReminder]:
    """The person's own reminders on Teemad they may still see."""
    if not _reader(user):
        return PersonalReminder.objects.none()
    return PersonalReminder.objects.filter(recipient=user, matter__in=_visible_matters(user))


def due_unread(user: Any, today: date) -> QuerySet[PersonalReminder]:
    """Awaiting the person now: the day has come and they have not read it."""
    return own(user).filter(remind_on__lte=today, read_at__isnull=True)


def upcoming(user: Any, today: date) -> QuerySet[PersonalReminder]:
    """Set for a later day, soonest first."""
    return own(user).filter(remind_on__gt=today).order_by("remind_on", "id")


def history(user: Any, unread: set[Any]) -> QuerySet[PersonalReminder]:
    """Read ones, for the panel's and the page's history."""
    return own(user).filter(read_at__isnull=False).exclude(pk__in=unread)


def own_row(user: Any, pk: Any) -> PersonalReminder | None:
    """The person's own reminder ``pk`` on a Teema they may see, or ``None`` (a 404)."""
    return own(user).filter(pk=pk).select_related("matter").first()


def arrived_at(row: PersonalReminder) -> datetime:
    """When it last became unread: its day's Tallinn midnight, or a later write."""
    midnight = timezone.make_aware(datetime.combine(row.remind_on, datetime.min.time()))
    return max(midnight, row.updated_at)


def create(*, user: Any, matter: Any, remind_on: date, note: str, today: date) -> PersonalReminder:
    """Set a reminder for ``user`` on ``matter``.

    Refused for a past day, or a Teema the person may not see.
    """
    if not _reader(user) or not _visible_matters(user).filter(pk=matter.pk).exists():
        raise DomainError("Teemat ei leitud.")
    if remind_on < today:
        raise DomainError("Meeldetuletuse kuupäev ei saa olla minevikus.")
    return PersonalReminder.objects.create(
        recipient=user, matter=matter, remind_on=remind_on, note=note.strip()[:NOTE_MAX]
    )


def mark_read(row: PersonalReminder) -> bool:
    """«Märgi loetuks». A repeat is a no-op."""
    if row.read_at is not None:
        return False
    now = timezone.now()
    return bool(
        PersonalReminder.objects.filter(pk=row.pk, read_at__isnull=True).update(
            read_at=now, updated_at=now
        )
    )


def mark_unread(row: PersonalReminder) -> bool:
    """«Märgi lugemata»: back in the inbox. Only a read one whose day has come."""
    if row.read_at is None or row.remind_on > timezone.localdate():
        return False
    now = timezone.now()
    return bool(
        PersonalReminder.objects.filter(pk=row.pk, read_at__isnull=False).update(
            read_at=None, updated_at=now
        )
    )


def remove(row: PersonalReminder) -> None:
    """«Eemalda» an upcoming or read reminder: the person's own row, gone."""
    PersonalReminder.objects.filter(pk=row.pk).delete()
