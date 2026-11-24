"""The Riigikogu's amendment deadlines (ADR 0224 §3).

One search a night: every bill whose amendment deadline falls in the coming
weeks (`/api/volumes/drafts?amendmentsDeadlineStartDate=…`). Each is stored as a
`Bill` and its deadline as a structured `ExternalDeadline` (AMENDMENTS,
extraction STRUCTURED). A moved deadline supersedes the old row; both stay.

**What the date means.** It is the last day for the Riigikogu's *members, factions
and the lead committee* to table amendments. koda cannot table one. It is the last
practical moment to get a proposal to someone who can, and every surface says it
in those words — never «koja tähtaeg».
"""

from __future__ import annotations

import datetime as dt
from dataclasses import dataclass, field
from typing import Any
from zoneinfo import ZoneInfo

from django.db import transaction

from app.riigikogu import bills, client
from app.riigikogu.enums import DeadlineKind, ExtractionStatus, FactState
from app.riigikogu.models import Bill, ExternalDeadline

TALLINN = ZoneInfo("Europe/Tallinn")
AHEAD_DAYS = 45
MEANING = (
    "Riigikogu muudatusettepanekute tähtaeg: selleks ajaks saavad Riigikogu liikmed, "
    "fraktsioonid ja juhtivkomisjon muudatusettepanekuid esitada. Koda ise "
    "muudatusettepanekut esitada ei saa."
)


@dataclass
class Counts:
    bills_seen: int = 0
    bills_new: int = 0
    bills_changed: int = 0
    deadlines_new: int = 0
    deadlines_moved: int = 0
    errors: list[dict[str, str]] = field(default_factory=list)

    def as_dict(self) -> dict[str, Any]:
        return {k: v for k, v in self.__dict__.items() if k != "errors"}


def keep_deadline(bill: Bill) -> str:
    """The bill's amendment deadline as an external fact: ``"new"``, ``"moved"`` or ``"same"``."""
    moment = bill.amendments_deadline
    with transaction.atomic():
        current = (
            ExternalDeadline.objects.select_for_update()
            .filter(
                bill=bill,
                kind=DeadlineKind.AMENDMENTS,
                document__isnull=True,
                state=FactState.ACTIVE,
            )
            .first()
        )
        local = moment.astimezone(TALLINN) if moment else None
        day = local.date() if local else None
        time = local.time().replace(second=0, microsecond=0) if local else None
        if current is not None and current.due_on == day and current.due_time == time:
            return "same"
        if day is None and current is None:
            return "same"
        row = ExternalDeadline(
            kind=DeadlineKind.AMENDMENTS,
            bill=bill,
            due_on=day,
            due_time=time,
            extraction=ExtractionStatus.STRUCTURED,
            source_url=bill.url,
        )
        if current is not None:
            current.state = FactState.SUPERSEDED if day is not None else FactState.CANCELLED
            current.save(update_fields=["state", "updated_at"])
            if day is None:
                return "moved"
            row.supersedes = current
        row.save()
        return "moved" if current is not None else "new"


def read_window(*, today: dt.date, budget: client.Budget | None = None) -> Counts:
    counts = Counts()
    params = {
        "amendmentsDeadlineStartDate": today.isoformat(),
        "amendmentsDeadlineEndDate": (today + dt.timedelta(days=AHEAD_DAYS)).isoformat(),
        "sort": "amendmentsDeadline,asc",
    }
    for row in client.pages("/api/volumes/drafts", params, budget=budget):
        counts.bills_seen += 1
        try:
            result = bills.upsert(bills.facts(row))
        except client.FetchError as error:
            counts.errors.append({"eelnõu": str(row.get("uuid"))[:60], "viga": str(error)[:200]})
            continue
        counts.bills_new += int(result.created)
        counts.bills_changed += int(result.changed and not result.created)
        state = keep_deadline(result.bill)
        counts.deadlines_new += int(state == "new")
        counts.deadlines_moved += int(state == "moved")
    return counts


def upcoming(*, today: dt.date) -> Any:
    return (
        ExternalDeadline.objects.filter(
            kind=DeadlineKind.AMENDMENTS, state=FactState.ACTIVE, due_on__gte=today
        )
        .select_related("bill")
        .order_by("due_on", "due_time")
    )
