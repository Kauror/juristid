"""Committee agendas, the next fortnight (ADR 0226 §2).

`/api/events` lists each day's committee sittings and their agenda items. An item
carries its own UUID and, when it is about a bill, the bill itself
(``relatedDraft``: UUID, number, title). That structured link is used first; the
«(1095 SE)» in a title is the fallback when an item names a bill without linking
it.

**An item is one row however often it is read.** A sitting moved to another day
updates the row and keeps the old time (`previous_sitting_at`, state MOVED). An
item that stops appearing is not taken as cancelled from one reading: only after
`MISSING_TO_CANCEL` *complete* readings of a window that still covers its date
(a failed or partial reading counts for nothing).

**Early signals.** An item with no bill yet whose title says a bill is being
initiated or its draft presented («eelnõu algatamine», «eelnõu kavandi
tutvustamine», «väljatöötamiskavatsus») is marked as a signal. The words make it a
signal, never relevant: relevance is a person's call (or the existing model's
metadata judgement, mocked here) on Monitooring.
"""

from __future__ import annotations

import datetime as dt
import re
import uuid as uuid_module
from dataclasses import dataclass, field
from typing import Any
from zoneinfo import ZoneInfo

from django.db import transaction
from django.utils import timezone

from app.riigikogu import bills, client
from app.riigikogu.enums import AgendaState
from app.riigikogu.models import Bill, CommitteeAgendaItem

TALLINN = ZoneInfo("Europe/Tallinn")
WINDOW_DAYS = 14
MISSING_TO_CANCEL = 2

_SIGNAL = re.compile(
    r"eelnõu\s+algatami|eelnõu\s+kavandi|kavandi\s+tutvustami|väljatöötamiskavatsus|"
    r"algatamise\s+arutelu|eelnõu\s+väljatöötami",
    re.IGNORECASE,
)
_ROUTINE = re.compile(
    r"^\s*(info\s+ja\s+muud\s+küsimused|muud\s+küsimused|nädala\s+töökava|töökava\s+kinnitami|"
    r"päevakorra\s+kinnitami|jooksvad\s+küsimused|info\b)",
    re.IGNORECASE,
)


@dataclass
class Counts:
    days: int = 0
    items_seen: int = 0
    items_new: int = 0
    items_moved: int = 0
    items_cancelled: int = 0
    linked_to_bill: int = 0
    signals: int = 0
    errors: list[dict[str, str]] = field(default_factory=list)

    def as_dict(self) -> dict[str, Any]:
        return {k: v for k, v in self.__dict__.items() if k != "errors"}


def _moment(value: Any) -> dt.datetime | None:
    try:
        moment = dt.datetime.fromisoformat(str(value)[:19])
    except (TypeError, ValueError):
        return None
    return moment.replace(tzinfo=TALLINN) if moment.tzinfo is None else moment


def _bill(item: dict[str, Any], *, budget: client.Budget | None) -> tuple[Bill | None, str]:
    """The item's bill from its structured link; else the number in its title, if known."""
    draft = item.get("relatedDraft") or {}
    title = str(item.get("title") or "")
    found = bills.mark_of(title)
    label = f"{found[0]} {found[1]}" if found else ""
    if isinstance(draft, dict) and draft.get("uuid"):
        try:
            key = uuid_module.UUID(str(draft["uuid"]))
        except ValueError:
            return None, label
        known = Bill.objects.filter(uuid=key).first()
        if known is None:
            known, _detail = bills.read(key, budget=budget)
        return known, label or known.label
    if found:
        membership = item.get("membership") if isinstance(item.get("membership"), int) else None
        known = (
            Bill.objects.filter(mark=found[0], type_code=found[1], membership=membership).first()
            if membership
            else None
        )
        return known, label
    return None, label


def _store(
    item: dict[str, Any],
    *,
    committee: str,
    sitting: dict[str, Any],
    at: dt.datetime,
    bill: Bill | None,
    label: str,
    now: dt.datetime,
    counts: Counts,
) -> CommitteeAgendaItem | None:
    try:
        key = uuid_module.UUID(str(item.get("uuid")))
    except ValueError:
        return None
    title = str(item.get("title") or "").strip()[:1000]
    signal = bill is None and bool(_SIGNAL.search(title))
    routine = bool(_ROUTINE.search(title))
    protocols = [
        str(p.get("uuid"))
        for p in sitting.get("protocolDocuments") or []
        if isinstance(p, dict) and p.get("uuid")
    ]
    sitting_uuid = sitting.get("uuid")
    with transaction.atomic():
        row = CommitteeAgendaItem.objects.select_for_update().filter(uuid=key).first()
        if row is None:
            counts.items_new += 1
            counts.signals += int(signal)
            return CommitteeAgendaItem.objects.create(
                uuid=key,
                sitting_uuid=uuid_module.UUID(str(sitting_uuid)) if sitting_uuid else None,
                committee=committee[:200],
                sitting_at=at,
                title=title or "(pealkirjata)",
                bill=bill,
                bill_mark=label[:16],
                signal=signal,
                routine=routine,
                protocol_uuids=protocols,
                first_seen_at=now,
                last_seen_at=now,
            )
        changed = []
        if row.sitting_at != at:
            row.previous_sitting_at = row.sitting_at
            row.sitting_at = at
            row.state = AgendaState.MOVED
            counts.items_moved += 1
            changed += ["previous_sitting_at", "sitting_at", "state"]
        elif row.state == AgendaState.CANCELLED:
            row.state = AgendaState.SCHEDULED
            changed.append("state")
        for name, value in (
            ("committee", committee[:200]),
            ("title", title or row.title),
            ("bill", bill),
            ("bill_mark", label[:16]),
            ("signal", signal),
            ("routine", routine),
        ):
            if getattr(row, name) != value:
                setattr(row, name, value)
                changed.append(name)
        if protocols and protocols != row.protocol_uuids:
            row.protocol_uuids = protocols
            changed.append("protocol_uuids")
        row.last_seen_at = now
        row.missing_readings = 0
        row.save(update_fields=[*changed, "last_seen_at", "missing_readings", "updated_at"])
        return row


def read_window(
    *,
    start: dt.date,
    end: dt.date,
    budget: client.Budget | None = None,
    now: dt.datetime | None = None,
) -> Counts:
    """Read every committee sitting from ``start`` to ``end``. Raises on an unreadable answer:
    the stage is then failed, and no item is marked missing from a reading that did not happen."""
    now = now or timezone.now()
    counts = Counts()
    answer = client.get_json(
        "/api/events", {"startDate": start.isoformat(), "endDate": end.isoformat()}, budget=budget
    )
    if not isinstance(answer, list):
        raise client.FetchError("Riigikogu päevakorra vastus on võõra kujuga")
    seen: set[uuid_module.UUID] = set()
    for day in answer:
        if not isinstance(day, dict):
            continue
        counts.days += 1
        for group in day.get("committeeSittings") or []:
            committee = str(((group or {}).get("committee") or {}).get("name") or "").strip()
            for sitting in (group or {}).get("sittings") or []:
                at = _moment((sitting or {}).get("sittingDateTime"))
                if at is None:
                    continue
                for item in (sitting or {}).get("agendaItems") or []:
                    if not isinstance(item, dict):
                        continue
                    counts.items_seen += 1
                    try:
                        bill, label = _bill(item, budget=budget)
                    except client.BudgetExceeded:
                        raise
                    except client.FetchError as error:
                        bill, label = None, ""
                        counts.errors.append(
                            {"punkt": str(item.get("title"))[:80], "viga": str(error)[:200]}
                        )
                    row = _store(
                        item,
                        committee=committee,
                        sitting=sitting,
                        at=at,
                        bill=bill,
                        label=label,
                        now=now,
                        counts=counts,
                    )
                    if row is not None:
                        seen.add(row.uuid)
                        counts.linked_to_bill += int(row.bill_id is not None)
    _missing(start=start, end=end, seen=seen, now=now, counts=counts)
    return counts


def _missing(
    *, start: dt.date, end: dt.date, seen: set[Any], now: dt.datetime, counts: Counts
) -> None:
    """Items of this window not in this complete reading: counted, cancelled only after a second."""
    begin = dt.datetime.combine(start, dt.time.min, tzinfo=TALLINN)
    finish = dt.datetime.combine(end, dt.time.max, tzinfo=TALLINN)
    absent = (
        CommitteeAgendaItem.objects.filter(sitting_at__gte=max(begin, now), sitting_at__lte=finish)
        .exclude(uuid__in=seen)
        .exclude(state=AgendaState.CANCELLED)
    )
    for row in absent:
        row.missing_readings += 1
        fields = ["missing_readings", "updated_at"]
        if row.missing_readings >= MISSING_TO_CANCEL:
            row.state = AgendaState.CANCELLED
            fields.append("state")
            counts.items_cancelled += 1
        row.save(update_fields=fields)
    CommitteeAgendaItem.objects.filter(
        sitting_at__lt=now, state__in=[AgendaState.SCHEDULED, AgendaState.MOVED]
    ).update(state=AgendaState.PAST, updated_at=now)


def upcoming(*, now: dt.datetime | None = None, days: int = WINDOW_DAYS) -> Any:
    now = now or timezone.now()
    return (
        CommitteeAgendaItem.objects.filter(
            sitting_at__gte=now - dt.timedelta(hours=3),
            sitting_at__lte=now + dt.timedelta(days=days),
        )
        .exclude(state=AgendaState.CANCELLED)
        .select_related("bill")
        .order_by("sitting_at", "committee", "title")
    )
