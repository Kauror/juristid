"""Who sees «Uued eelnõud», and its notice in the bell (ADR 0218 §6–§7).

The page is the department head's: always open to whoever holds the head's
management view (`may_view_department_management`). The head can open it to
every business reader (`CandidateAudience.everyone`), and close it again.

Whoever may decide on candidates gets «Uued eelnõud registrites» in the bell
after a sweep that put candidates on the list: one unread notice per person,
which a later sweep adds to. It is awaiting the person while it is unread and
something is still on the list; once the list is empty it is history.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from django.contrib.auth import get_user_model
from django.db.models import Exists, Q, QuerySet
from django.utils import timezone

from app.core.authorization import (
    may_read_business_content,
    may_view_department_management,
    may_write_business_content,
)
from app.register_watch.models import CandidateAudience, CandidateNotice


def audience() -> CandidateAudience:
    row = CandidateAudience.objects.order_by("created_at").first()
    return row if row is not None else CandidateAudience(everyone=False)


def is_head(user: Any) -> bool:
    return bool(getattr(user, "is_authenticated", False)) and may_view_department_management(user)


def may_see(user: Any) -> bool:
    """The head always; everyone who reads business content once the head opened it."""
    if not getattr(user, "is_authenticated", False) or not may_read_business_content(user):
        return False
    return is_head(user) or audience().everyone


def may_decide(user: Any) -> bool:
    """Dismiss, add or link: who sees the page and may write business content."""
    return may_see(user) and may_write_business_content(user)


def set_everyone(everyone: bool) -> CandidateAudience:
    row = CandidateAudience.objects.order_by("created_at").first()
    if row is None:
        return CandidateAudience.objects.create(everyone=everyone)
    if row.everyone != everyone:
        row.everyone = everyone
        row.save(update_fields=["everyone", "updated_at"])
    return row


def _recipients() -> list[Any]:
    """Every active person who may decide: the heads, or every writer once opened."""
    users = get_user_model().objects.filter(is_active=True)
    return [user for user in users if may_decide(user)]


def _tell(*, candidates: int = 0, agendas: int = 0, invitations: int = 0) -> int:
    """Add to each recipient's unread notice, or create one. How many were told."""
    if candidates <= 0 and agendas <= 0 and invitations <= 0:
        return 0
    now = timezone.now()
    told = 0
    for user in _recipients():
        row = CandidateNotice.objects.filter(recipient=user, read_at__isnull=True).first()
        if row is None:
            CandidateNotice.objects.create(
                recipient=user, candidates=candidates, agendas=agendas, invitations=invitations
            )
        else:
            CandidateNotice.objects.filter(pk=row.pk).update(
                candidates=row.candidates + candidates,
                agendas=row.agendas + agendas,
                invitations=row.invitations + invitations,
                updated_at=now,
            )
        told += 1
    return told


def notify(candidates: int) -> int:
    """A sweep listed ``candidates`` new drafts (ADR 0218 §7)."""
    return _tell(candidates=candidates)


def notify_agendas(agendas: int) -> int:
    """A run stored ``agendas`` new Government agendas worth a look (ADR 0219)."""
    return _tell(agendas=agendas)


def notify_invitations(invitations: int) -> int:
    """Riigikogu invitations to koda with no Teema owner to tell (ADR 0223 §7): a bill no
    Teema follows yet, or a Teema without an active owner. The notice is a count only; it
    names no Teema, so a restricted one is never revealed through it."""
    return _tell(invitations=invitations)


def own(user: Any) -> QuerySet[CandidateNotice]:
    """The person's notices, while the page is theirs to see. Lazy: no query here.

    Whether the head opened the page to everyone is a condition inside the same
    query rather than a read of its own, so the bell, Minu asjad and the inbox
    pay one query for this source, whatever it holds (query budgets in
    tests/test_work_pages_at_scale.py).
    """
    if (
        not getattr(user, "is_authenticated", False)
        or getattr(user, "pk", None) is None
        or not may_read_business_content(user)
    ):
        return CandidateNotice.objects.none()
    rows = CandidateNotice.objects.filter(recipient=user)
    if not is_head(user):
        rows = rows.filter(Exists(CandidateAudience.objects.filter(everyone=True)))
    return rows


def due_unread(user: Any) -> QuerySet[CandidateNotice]:
    """Unread, and it carries an agenda or a draft still waits on the list: one lazy query."""
    from app.register_watch.candidates import worth_telling

    return (
        own(user)
        .filter(read_at__isnull=True)
        .filter(Q(agendas__gt=0) | Q(invitations__gt=0) | Exists(worth_telling()))
    )


def history(user: Any, unread: set[Any]) -> QuerySet[CandidateNotice]:
    return own(user).exclude(pk__in=unread)


def own_row(user: Any, pk: Any) -> CandidateNotice | None:
    return own(user).filter(pk=pk).first()


def arrived_at(row: CandidateNotice) -> datetime:
    return row.updated_at


def mark_read(row: CandidateNotice) -> bool:
    if row.read_at is not None:
        return False
    return bool(
        CandidateNotice.objects.filter(pk=row.pk, read_at__isnull=True).update(
            read_at=timezone.now()
        )
    )


def mark_unread(row: CandidateNotice) -> bool:
    if row.read_at is None:
        return False
    return bool(
        CandidateNotice.objects.filter(pk=row.pk, read_at__isnull=False).update(read_at=None)
    )
