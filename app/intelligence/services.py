"""Named use cases for the three structured Matter facts.

Forms parse and validate; these functions are the only things that write. Every
one records a `ChangeEvent` in the same transaction as the change it describes,
so "who moved this commencement date, and from what" is answerable without a
fourth field-history subsystem (master specification 16.5, Stage-2G brief 32).

Two rules run through all of them.

**Nothing is deleted as ordinary workflow.** A plan that changed is history, not
a mistake: a cancelled deadline records what the department expected, and an
expectation somebody replaced is kept and linked to its replacement. Correction
is an edit with an audit trail; removal is not offered (Stage-2G brief 33).

**Precision is preserved and never invented.** Callers hand in an anchor and its
period end, both produced by ``app.workflow.dates`` from what a person actually
chose. Nothing here derives a day from a quarter, and nothing fills an unknown
commencement date with today, the Matter's year or an epoch
(Stage-2G brief 12, 69).
"""

from __future__ import annotations

from datetime import date
from typing import Any

from django.db import transaction
from django.utils import timezone

from app.audit.enums import ChangeEventType
from app.audit.services import record_change_event
from app.core.errors import DomainError
from app.intelligence.enums import EffectiveDateKind, FactStatus, WorkVictoryStatus
from app.intelligence.models import (
    MatterEffectiveDate,
    MatterImportantDate,
    MatterWorkVictory,
)
from app.workflow.dates import period_bounds
from app.workflow.enums import DatePrecision

#: How much of a free-text field goes into an audit payload. Enough to identify
#: the record in a history view, never enough to become a second copy of it.
SUMMARY_LIMIT = 200


def _iso(value: date | None) -> str | None:
    return value.isoformat() if value is not None else None


def _require_text(value: str, message: str) -> str:
    cleaned = (value or "").strip()
    if not cleaned:
        raise DomainError(message)
    return cleaned


def _check_precision(precision: str) -> str:
    if precision not in DatePrecision.values:
        raise DomainError(f"Tundmatu kuupäeva täpsus {precision!r}.")
    return precision


def _check_bounds(date_value: date, period_end: date, precision: str) -> None:
    """Refuse an anchor and end that do not describe the stated precision.

    The forms compute both through ``app.workflow.dates``; this is the guard for
    every other caller — a future importer, a shell session, a data migration —
    so a QUARTER row cannot end up spanning a single day and then read as an
    exact date on a page that trusts the precision.
    """
    expected_start, expected_end = period_bounds(date_value, precision)
    if (date_value, period_end) != (expected_start, expected_end):
        raise DomainError(
            "Kuupäev ja perioodi lõpp ei vasta valitud täpsusele "
            f"({precision}): oodati {expected_start}–{expected_end}."
        )


# ---------------------------------------------------------------------------
# Olulised tähtajad
# ---------------------------------------------------------------------------


@transaction.atomic
def add_important_date(
    *,
    matter: Any,
    title: str,
    date_value: date,
    period_end: date,
    date_precision: str = DatePrecision.EXACT,
    note: str = "",
    actor: Any = None,
    source_text: str = "",
    legacy_source_page: Any = None,
) -> MatterImportantDate:
    title = _require_text(title, "Olulisel tähtajal peab olema kirjeldus.")
    _check_precision(date_precision)
    _check_bounds(date_value, period_end, date_precision)

    record = MatterImportantDate.objects.create(
        matter=matter,
        title=title,
        date_value=date_value,
        period_end=period_end,
        date_precision=date_precision,
        note=note.strip(),
        created_by=actor,
        source_text=source_text,
        legacy_source_page=legacy_source_page,
    )
    record_change_event(
        event_type=ChangeEventType.IMPORTANT_DATE_ADDED,
        matter=matter,
        actor=actor,
        obj=record,
        summary=title[:SUMMARY_LIMIT],
        payload={
            "date": _iso(date_value),
            "period_end": _iso(period_end),
            "precision": date_precision,
        },
    )
    return record


@transaction.atomic
def update_important_date(
    *,
    record: MatterImportantDate,
    title: str,
    date_value: date,
    period_end: date,
    date_precision: str,
    note: str = "",
    actor: Any = None,
) -> MatterImportantDate:
    """Correct a milestone, keeping what it said before in the audit trail.

    The payload carries the old and the new date. Without that, 27.09.2026 can
    become 01.11.2026 with nothing anywhere recording that it moved, which is
    precisely the failure the department has with a hand-kept list
    (Stage-2G brief 35).
    """
    if record.status != FactStatus.ACTIVE:
        raise DomainError("Ainult kehtivat tähtaega saab muuta.")
    title = _require_text(title, "Olulisel tähtajal peab olema kirjeldus.")
    _check_precision(date_precision)
    _check_bounds(date_value, period_end, date_precision)

    before = {
        "title": record.title,
        "date": _iso(record.date_value),
        "precision": record.date_precision,
    }
    record.title = title
    record.date_value = date_value
    record.period_end = period_end
    record.date_precision = date_precision
    record.note = note.strip()
    record.save(
        update_fields=["title", "date_value", "period_end", "date_precision", "note", "updated_at"]
    )

    record_change_event(
        event_type=ChangeEventType.IMPORTANT_DATE_CHANGED,
        matter=record.matter,
        actor=actor,
        obj=record,
        summary=title[:SUMMARY_LIMIT],
        payload={
            "from": before,
            "to": {"title": title, "date": _iso(date_value), "precision": date_precision},
        },
    )
    return record


@transaction.atomic
def cancel_important_date(
    *, record: MatterImportantDate, actor: Any = None, reason: str = ""
) -> MatterImportantDate:
    """The expected milestone is not going to happen. It stays on the record."""
    if record.status != FactStatus.ACTIVE:
        raise DomainError("Ainult kehtivat tähtaega saab tühistada.")

    record.status = FactStatus.CANCELLED
    record.save(update_fields=["status", "updated_at"])
    record_change_event(
        event_type=ChangeEventType.IMPORTANT_DATE_CANCELLED,
        matter=record.matter,
        actor=actor,
        obj=record,
        summary=record.title[:SUMMARY_LIMIT],
        payload={"reason": reason.strip()[:500], "date": _iso(record.date_value)},
    )
    return record


@transaction.atomic
def supersede_important_date(
    *,
    record: MatterImportantDate,
    title: str,
    date_value: date,
    period_end: date,
    date_precision: str,
    note: str = "",
    actor: Any = None,
) -> MatterImportantDate:
    """Replace a milestone with a new one, keeping both.

    The counterpart to `update_important_date`, and a different fact. An edit
    says *we recorded this wrongly*; superseding says *the plan changed, and
    here is what it changed to*. Both are kept because a lawyer reading the file
    in two years needs to be able to tell those apart (Stage-2G brief 5, 33).
    """
    if record.status != FactStatus.ACTIVE:
        raise DomainError("Ainult kehtivat tähtaega saab asendada.")

    replacement = add_important_date(
        matter=record.matter,
        title=title,
        date_value=date_value,
        period_end=period_end,
        date_precision=date_precision,
        note=note,
        actor=actor,
    )
    record.status = FactStatus.SUPERSEDED
    # Written after the replacement exists, so the chain is navigable in both
    # directions without a nullable placeholder — the same shape `NextAction`
    # already uses for the same reason.
    record.replaced_by = replacement
    record.save(update_fields=["status", "replaced_by", "updated_at"])
    record_change_event(
        event_type=ChangeEventType.IMPORTANT_DATE_CHANGED,
        matter=record.matter,
        actor=actor,
        obj=record,
        summary=record.title[:SUMMARY_LIMIT],
        payload={
            "superseded_by": str(replacement.id),
            "from": {"date": _iso(record.date_value), "precision": record.date_precision},
            "to": {"date": _iso(date_value), "precision": date_precision},
        },
    )
    return replacement


# ---------------------------------------------------------------------------
# Jõustumine
# ---------------------------------------------------------------------------


def _validate_effective_date(
    kind: str, date_value: date | None, period_end: date | None, precision: str
) -> None:
    if kind not in EffectiveDateKind.values:
        raise DomainError(f"Tundmatu jõustumise liik {kind!r}.")
    _check_precision(precision)

    if kind == EffectiveDateKind.KNOWN_DATE:
        if date_value is None or period_end is None:
            raise DomainError("Teadaoleva jõustumise puhul on kuupäev kohustuslik.")
        _check_bounds(date_value, period_end, precision)
        return

    # Everything else says the date is *not* known. Storing one anyway would
    # make a fabricated day indistinguishable from a real one.
    if date_value is not None or period_end is not None:
        raise DomainError("Sellel jõustumise liigil ei saa kuupäeva olla.")


@transaction.atomic
def add_effective_date(
    *,
    matter: Any,
    kind: str = EffectiveDateKind.KNOWN_DATE,
    date_value: date | None = None,
    period_end: date | None = None,
    date_precision: str = DatePrecision.EXACT,
    description: str = "",
    note: str = "",
    source_url: str = "",
    actor: Any = None,
    source_text: str = "",
    legacy_source_page: Any = None,
) -> MatterEffectiveDate:
    _validate_effective_date(kind, date_value, period_end, date_precision)

    record = MatterEffectiveDate.objects.create(
        matter=matter,
        kind=kind,
        date_value=date_value,
        period_end=period_end,
        date_precision=date_precision,
        description=description.strip(),
        note=note.strip(),
        source_url=source_url.strip(),
        created_by=actor,
        source_text=source_text,
        legacy_source_page=legacy_source_page,
    )
    record_change_event(
        event_type=ChangeEventType.EFFECTIVE_DATE_ADDED,
        matter=matter,
        actor=actor,
        obj=record,
        summary=(description.strip() or record.display_when)[:SUMMARY_LIMIT],
        payload={"kind": kind, "date": _iso(date_value), "precision": date_precision},
    )
    return record


@transaction.atomic
def update_effective_date(
    *,
    record: MatterEffectiveDate,
    kind: str,
    date_value: date | None,
    period_end: date | None,
    date_precision: str,
    description: str = "",
    note: str = "",
    source_url: str = "",
    actor: Any = None,
) -> MatterEffectiveDate:
    """Move a commencement date. The central view follows automatically.

    Nothing is copied anywhere: *Jõustuvad aktid* reads this table, so changing
    the date here is the whole change (Stage-2G brief 16).
    """
    if record.status != FactStatus.ACTIVE:
        raise DomainError("Ainult kehtivat jõustumist saab muuta.")
    _validate_effective_date(kind, date_value, period_end, date_precision)

    before = {
        "kind": record.kind,
        "date": _iso(record.date_value),
        "precision": record.date_precision,
        "description": record.description,
    }
    record.kind = kind
    record.date_value = date_value
    record.period_end = period_end
    record.date_precision = date_precision
    record.description = description.strip()
    record.note = note.strip()
    record.source_url = source_url.strip()
    record.save(
        update_fields=[
            "kind",
            "date_value",
            "period_end",
            "date_precision",
            "description",
            "note",
            "source_url",
            "updated_at",
        ]
    )
    record_change_event(
        event_type=ChangeEventType.EFFECTIVE_DATE_CHANGED,
        matter=record.matter,
        actor=actor,
        obj=record,
        summary=(record.description or record.display_when)[:SUMMARY_LIMIT],
        payload={
            "from": before,
            "to": {
                "kind": kind,
                "date": _iso(date_value),
                "precision": date_precision,
                "description": record.description,
            },
        },
    )
    return record


@transaction.atomic
def cancel_effective_date(
    *, record: MatterEffectiveDate, actor: Any = None, reason: str = ""
) -> MatterEffectiveDate:
    if record.status != FactStatus.ACTIVE:
        raise DomainError("Ainult kehtivat jõustumist saab tühistada.")

    record.status = FactStatus.CANCELLED
    record.save(update_fields=["status", "updated_at"])
    record_change_event(
        event_type=ChangeEventType.EFFECTIVE_DATE_CANCELLED,
        matter=record.matter,
        actor=actor,
        obj=record,
        summary=(record.description or record.display_when)[:SUMMARY_LIMIT],
        payload={"reason": reason.strip()[:500], "date": _iso(record.date_value)},
    )
    return record


# ---------------------------------------------------------------------------
# Töövõidud
# ---------------------------------------------------------------------------


def _validate_period(period_date: date | None, period_end: date | None, precision: str) -> None:
    _check_precision(precision)
    if period_date is None and period_end is None:
        # An unknown period is data, not a defect. The department frequently
        # knows that something was a win without being able to say which year
        # it landed in (Stage-2G brief 21, 69).
        return
    if period_date is None or period_end is None:
        raise DomainError("Perioodi algus ja lõpp määratakse koos.")
    _check_bounds(period_date, period_end, precision)


@transaction.atomic
def add_work_victory_candidate(
    *,
    matter: Any,
    title: str,
    detail: str = "",
    period_date: date | None = None,
    period_end: date | None = None,
    date_precision: str = DatePrecision.YEAR,
    source_url: str = "",
    note: str = "",
    actor: Any = None,
    source_text: str = "",
    legacy_source_page: Any = None,
) -> MatterWorkVictory:
    """Record a claim that this was a Koda win. Always as a candidate.

    There is no path that creates a confirmed victory directly. Confirmation is
    a separate, deliberate act by a separate person, because the alternative is
    a department whose count of its own achievements rises whenever somebody
    types confidently (Stage-2G brief 20, 24).
    """
    title = _require_text(title, "Töövõidul peab olema kirjeldus.")
    _validate_period(period_date, period_end, date_precision)

    record = MatterWorkVictory.objects.create(
        matter=matter,
        status=WorkVictoryStatus.CANDIDATE,
        title=title,
        detail=detail.strip(),
        period_date=period_date,
        period_end=period_end,
        date_precision=date_precision,
        source_url=source_url.strip(),
        note=note.strip(),
        created_by=actor,
        source_text=source_text,
        legacy_source_page=legacy_source_page,
        status_changed_at=timezone.now(),
    )
    record_change_event(
        event_type=ChangeEventType.WORK_VICTORY_PROPOSED,
        matter=matter,
        actor=actor,
        obj=record,
        summary=title[:SUMMARY_LIMIT],
        payload={"period": _iso(period_date), "precision": date_precision},
    )
    return record


@transaction.atomic
def update_work_victory(
    *,
    record: MatterWorkVictory,
    title: str,
    detail: str = "",
    period_date: date | None = None,
    period_end: date | None = None,
    date_precision: str = DatePrecision.YEAR,
    source_url: str = "",
    note: str = "",
    actor: Any = None,
) -> MatterWorkVictory:
    """Edit the wording or the period. Never the review state.

    Status transitions have their own functions on purpose: editing a
    candidate's description must not be able to promote it
    (Stage-2G brief 53).
    """
    title = _require_text(title, "Töövõidul peab olema kirjeldus.")
    _validate_period(period_date, period_end, date_precision)

    before = {
        "title": record.title,
        "period": _iso(record.period_date),
        "precision": record.date_precision,
    }
    record.title = title
    record.detail = detail.strip()
    record.period_date = period_date
    record.period_end = period_end
    record.date_precision = date_precision
    record.source_url = source_url.strip()
    record.note = note.strip()
    record.save(
        update_fields=[
            "title",
            "detail",
            "period_date",
            "period_end",
            "date_precision",
            "source_url",
            "note",
            "updated_at",
        ]
    )
    record_change_event(
        event_type=ChangeEventType.WORK_VICTORY_CHANGED,
        matter=record.matter,
        actor=actor,
        obj=record,
        summary=title[:SUMMARY_LIMIT],
        payload={
            "from": before,
            "to": {"title": title, "period": _iso(period_date), "precision": date_precision},
            "status": record.status,
        },
    )
    return record


@transaction.atomic
def confirm_work_victory(*, record: MatterWorkVictory, actor: Any = None) -> MatterWorkVictory:
    """A person decides this is a Chamber work victory.

    The audit row carries both statuses and the approver, because "who decided
    this counted, and when" is the first question anybody will ask of a
    published figure (Stage-2G brief 34).
    """
    if record.status == WorkVictoryStatus.CONFIRMED:
        raise DomainError("Töövõit on juba kinnitatud.")

    previous = record.status
    now = timezone.now()
    record.status = WorkVictoryStatus.CONFIRMED
    record.confirmed_by = actor
    record.confirmed_at = now
    record.status_changed_at = now
    record.save(
        update_fields=["status", "confirmed_by", "confirmed_at", "status_changed_at", "updated_at"]
    )
    record_change_event(
        event_type=ChangeEventType.WORK_VICTORY_CONFIRMED,
        matter=record.matter,
        actor=actor,
        obj=record,
        summary=record.title[:SUMMARY_LIMIT],
        payload={
            "from_status": previous,
            "to_status": WorkVictoryStatus.CONFIRMED.value,
            "confirmed_by": str(getattr(actor, "pk", "") or ""),
            "confirmed_at": now.isoformat(),
            "period": _iso(record.period_date),
        },
    )
    return record


@transaction.atomic
def reject_work_victory(
    *, record: MatterWorkVictory, actor: Any = None, reason: str = ""
) -> MatterWorkVictory:
    """Record that a candidate did not come off. Kept, not deleted."""
    if record.status == WorkVictoryStatus.NOT_REALIZED:
        raise DomainError("Töövõit on juba märgitud mitterealiseerunuks.")

    previous = record.status
    now = timezone.now()
    record.status = WorkVictoryStatus.NOT_REALIZED
    # A rejected claim is not a confirmed one, so the confirmation stamp is
    # cleared rather than left behind to be read as an approval.
    record.confirmed_by = None
    record.confirmed_at = None
    record.status_changed_at = now
    record.save(
        update_fields=["status", "confirmed_by", "confirmed_at", "status_changed_at", "updated_at"]
    )
    record_change_event(
        event_type=ChangeEventType.WORK_VICTORY_REJECTED,
        matter=record.matter,
        actor=actor,
        obj=record,
        summary=record.title[:SUMMARY_LIMIT],
        payload={
            "from_status": previous,
            "to_status": WorkVictoryStatus.NOT_REALIZED.value,
            "reason": reason.strip()[:500],
        },
    )
    return record
