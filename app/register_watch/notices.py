"""«Registris uus dokument» — the bell's notice that a register has something new (ADR 0217 §6).

One notice per Teema for its owner. A run that offers new documents on a
Teema creates it, or adds to the Teema's unread one. It belongs to whoever
owns the Teema *now*, like every other inbox source: a hand-over moves it
with the Teema (audit QA26-DUP-04). It is awaiting the owner
while it is unread *and* the Teema still has a document waiting that the model
did not call unrelated; once the documents are added or skipped it is resolved
history, whether or not it was read. Read and unread by its own person only,
through CSRF POSTs that name a row; no business service is called.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from django.db.models import Exists, OuterRef, QuerySet
from django.utils import timezone

from app.core.authorization import may_read_business_content
from app.register_watch.models import FindingStatus, RegisterNotice, Relevance, WatchFinding


def _reader(user: Any) -> bool:
    return (
        bool(getattr(user, "is_authenticated", False))
        and getattr(user, "pk", None) is not None
        and may_read_business_content(user)
    )


def _waiting() -> Exists:
    return Exists(
        WatchFinding.objects.filter(
            matter=OuterRef("matter"), status=FindingStatus.PENDING
        ).exclude(relevance=Relevance.UNRELATED)
    )


def own(user: Any) -> QuerySet[RegisterNotice]:
    """Notices on the open Teemad the person owns now and may still see."""
    if not _reader(user):
        return RegisterNotice.objects.none()
    from app.matters.models import Matter

    visible = Matter.objects.visible_to(user).filter(
        is_open=True, deleted_at__isnull=True, owner=user
    )
    return RegisterNotice.objects.filter(matter__in=visible)


def due_unread(user: Any) -> QuerySet[RegisterNotice]:
    """Awaiting the person: unread, and something on the Teema still waits."""
    return own(user).filter(read_at__isnull=True).filter(_waiting())


def history(user: Any, unread: set[Any]) -> QuerySet[RegisterNotice]:
    """Read ones, and ones whose documents were all dealt with."""
    return own(user).exclude(pk__in=unread)


def own_row(user: Any, pk: Any) -> RegisterNotice | None:
    return own(user).filter(pk=pk).select_related("matter").first()


def arrived_at(row: RegisterNotice) -> datetime:
    """When the latest documents arrived: set by `notify`, never by reading."""
    return row.updated_at


def notify(matter: Any, documents: int) -> RegisterNotice | None:
    """Tell the Teema's owner; add to the Teema's unread notice if there is one.

    ``recipient`` records who was told last; who sees the notice is the
    current owner (`own`).
    """
    owner = getattr(matter, "owner", None)
    if documents <= 0 or owner is None or not owner.is_active:
        return None
    now = timezone.now()
    row = RegisterNotice.objects.filter(matter=matter, read_at__isnull=True).first()
    if row is None:
        return RegisterNotice.objects.create(recipient=owner, matter=matter, documents=documents)
    RegisterNotice.objects.filter(pk=row.pk).update(
        recipient=owner, documents=row.documents + documents, updated_at=now
    )
    row.refresh_from_db()
    return row


def mark_read(row: RegisterNotice) -> bool:
    if row.read_at is not None:
        return False
    # `updated_at` is when the documents arrived (`arrived_at`); reading leaves it.
    return bool(
        RegisterNotice.objects.filter(pk=row.pk, read_at__isnull=True).update(
            read_at=timezone.now()
        )
    )


def mark_unread(row: RegisterNotice) -> bool:
    if row.read_at is None:
        return False
    return bool(
        RegisterNotice.objects.filter(pk=row.pk, read_at__isnull=False).update(read_at=None)
    )
