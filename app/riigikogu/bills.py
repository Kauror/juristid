"""Bills as durable identities, and which Teema follows which bill (ADR 0224 §1–§2).

**Identity.** A bill is its Riigikogu UUID (`Bill.uuid`). The number restarts with
each membership, so «1082 SE» finds a bill and labels it, and is never stored as
the thing a Teema is tied to. A bill's title may change between readings; the
UUID does not, so a confirmed tie survives it.

**Association.** The one register of Teema ↔ bill ties is the register watch's
`WatchTarget` with source RIIGIKOGU and the UUID as identifier — the same row the
nightly watch already reads the bill through (ADR 0217). Two origins make one:

* ``TITLE`` — the Teema's own title names the bill («… (1082 SE)»); the watch has
  always done this, for the current membership only.
* ``LINKED`` — a person tied the bill to the Teema: on the Teema's «Registritest»
  tab, from an invitation, or by linking a «Uued eelnõud» candidate whose documents
  include the bill. A Teema may follow several bills (a withdrawn bill and its
  successor, a package of bills).

Nothing here ties a bill to a Teema by text similarity. Where the evidence is a
shared number or a similar title, the bill is *proposed* (`proposals`), and the
proposal names every Teema it could be, so an ambiguity is shown, not resolved.
"""

from __future__ import annotations

import datetime as dt
import hashlib
import json
import re
import uuid as uuid_module
from dataclasses import dataclass
from typing import Any

from django.db import transaction
from django.db.models import QuerySet
from django.utils import timezone

from app.core.errors import DomainError
from app.riigikogu import client
from app.riigikogu.models import Bill

#: «1082 SE», «(1082 SE)», «1082SE», «1093 OE»: a bill's number and type.
BILL_MARK = re.compile(r"\b(\d{1,4})\s?(SE|OE|AE|RKE)\b")
#: «1-1/15-1082»: a document's register reference ends in the bill's number.
_REFERENCE = re.compile(r"^\d+-\d+/(\d+)-(\d+)")

NOT_A_BILL = "Riigikogust sellist eelnõu ei leitud."
ALREADY_LINKED = "See eelnõu on teemaga juba seotud."


def mark_of(text: str) -> tuple[int, str] | None:
    match = BILL_MARK.search(text or "")
    return (int(match.group(1)), match.group(2)) if match else None


def mark_from_reference(reference: str) -> tuple[int, int] | None:
    """``1-1/15-1082`` → (membership 15, number 1082)."""
    match = _REFERENCE.match(reference or "")
    return (int(match.group(1)), int(match.group(2))) if match else None


def _parse_day(value: Any) -> dt.date | None:
    if not value:
        return None
    try:
        return dt.date.fromisoformat(str(value)[:10])
    except ValueError:
        return None


def _parse_moment(value: Any) -> dt.datetime | None:
    """The API writes local Tallinn time without an offset («2026-12-02T17:15:00»)."""
    if not value:
        return None
    try:
        moment = dt.datetime.fromisoformat(str(value)[:19])
    except ValueError:
        return None
    if moment.tzinfo is None:
        from zoneinfo import ZoneInfo

        moment = moment.replace(tzinfo=ZoneInfo("Europe/Tallinn"))
    return moment


@dataclass(frozen=True)
class BillFacts:
    uuid: uuid_module.UUID
    mark: int | None
    type_code: str
    membership: int | None
    title: str
    stage: str
    status: str
    leading_committee: str
    initiated_on: dt.date | None
    amendments_deadline: dt.datetime | None

    def digest(self) -> str:
        payload = json.dumps(
            [
                self.mark,
                self.type_code,
                self.membership,
                self.title,
                self.stage,
                self.status,
                self.leading_committee,
                self.initiated_on.isoformat() if self.initiated_on else None,
                self.amendments_deadline.isoformat() if self.amendments_deadline else None,
            ],
            ensure_ascii=False,
        )
        return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def facts(row: dict[str, Any]) -> BillFacts:
    """A bill as `/api/volumes/drafts` and `/api/volumes/drafts/{uuid}` both write it."""
    try:
        key = uuid_module.UUID(str(row.get("uuid")))
    except ValueError:
        raise client.FetchError("Riigikogu eelnõul puudub tunnus") from None
    committee = row.get("leadingCommittee") or {}
    raw_mark = row.get("mark")
    mark = (
        raw_mark
        if isinstance(raw_mark, int)
        else int(str(raw_mark))
        if str(raw_mark or "").isdigit()
        else None
    )
    return BillFacts(
        uuid=key,
        mark=mark,
        type_code=str(row.get("draftTypeCode") or "")[:8],
        membership=row.get("membership") if isinstance(row.get("membership"), int) else None,
        title=str(row.get("title") or "").strip()[:1000],
        stage=str(row.get("activeDraftStage") or "")[:64],
        status=str(row.get("activeDraftStatus") or "")[:64],
        leading_committee=str((committee or {}).get("name") or "").strip()[:200],
        initiated_on=_parse_day(row.get("initiated")),
        amendments_deadline=_parse_moment(row.get("amendmentsDeadline")),
    )


@dataclass(frozen=True)
class Upsert:
    bill: Bill
    created: bool
    changed: bool
    #: The amendment deadline before this reading, when it changed.
    previous_deadline: dt.datetime | None = None


def upsert(found: BillFacts) -> Upsert:
    """Store the bill as read now. Unchanged → nothing written but the time it was seen."""
    now = timezone.now()
    with transaction.atomic():
        bill = Bill.objects.select_for_update().filter(uuid=found.uuid).first()
        values = {
            "mark": found.mark,
            "type_code": found.type_code,
            "membership": found.membership,
            "title": found.title,
            "url": client.page_url_for_bill(found.uuid),
            "stage": found.stage,
            "status": found.status,
            "leading_committee": found.leading_committee,
            "initiated_on": found.initiated_on,
            "amendments_deadline": found.amendments_deadline,
            "metadata_hash": found.digest(),
            "fetched_at": now,
        }
        if bill is None:
            return Upsert(Bill.objects.create(uuid=found.uuid, **values), True, True)
        if bill.metadata_hash == values["metadata_hash"]:
            Bill.objects.filter(pk=bill.pk).update(fetched_at=now)
            return Upsert(bill, False, False)
        previous = bill.amendments_deadline
        for name, value in values.items():
            setattr(bill, name, value)
        bill.save()
        return Upsert(
            bill,
            False,
            True,
            previous_deadline=previous if previous != found.amendments_deadline else None,
        )


def read(uuid: Any, *, budget: client.Budget | None = None) -> tuple[Bill, dict[str, Any]]:
    """Read one bill's detail from the API and store it; the bill and the raw detail."""
    detail = client.get_json(f"/api/volumes/drafts/{uuid}", budget=budget)
    if not isinstance(detail, dict) or str(detail.get("uuid")) != str(uuid):
        raise client.FetchError(NOT_A_BILL)
    return upsert(facts(detail)).bill, detail


def find(
    mark: int, type_code: str, *, membership: int | None = None, budget: client.Budget | None = None
) -> Bill | None:
    """The bill with this number: of ``membership`` when given, else the newest membership's."""
    answer = client.get_json("/api/volumes/drafts", {"mark": mark, "size": 20}, budget=budget)
    candidates = [
        facts(row) for row in client.rows(answer) if row.get("draftTypeCode") == type_code
    ]
    if membership is not None:
        candidates = [c for c in candidates if c.membership == membership]
    if not candidates:
        return None
    best = max(candidates, key=lambda c: (c.membership or 0, c.initiated_on or dt.date.min))
    return upsert(best).bill


# -- the Teema side -----------------------------------------------------------------


def linked_targets(matter: Any) -> QuerySet[Any]:
    """The Teema's active Riigikogu ties, whichever origin made them."""
    from app.register_watch.models import WatchSource, WatchTarget

    return WatchTarget.objects.filter(matter=matter, source=WatchSource.RIIGIKOGU, active=True)


def teemad_of(bill: Bill) -> QuerySet[Any]:
    """Every Teema (open or closed, unrestricted or not) tied to the bill. Reader-blind:
    callers that show the result to a person narrow it with ``visible_to`` first."""
    from app.matters.models import Matter
    from app.register_watch.models import WatchSource, WatchTarget

    ids = WatchTarget.objects.filter(
        source=WatchSource.RIIGIKOGU, identifier=str(bill.uuid), active=True
    ).values("matter_id")
    return Matter.objects.filter(pk__in=ids, deleted_at__isnull=True)


@transaction.atomic
def link(matter: Any, bill: Bill, *, actor: Any) -> Any:
    """A person ties ``bill`` to the Teema (``LINKED``). An existing inactive tie is revived."""
    from app.register_watch.models import TargetOrigin, WatchSource, WatchTarget

    if getattr(matter, "deleted_at", None) is not None:
        raise DomainError("Kustutatud teemaga ei saa eelnõu siduda.")
    target, created = WatchTarget.objects.get_or_create(
        matter=matter,
        source=WatchSource.RIIGIKOGU,
        identifier=str(bill.uuid),
        defaults={
            "label": bill.label or bill.title[:120],
            "origin": TargetOrigin.LINKED,
            "url": bill.url,
        },
    )
    if not created:
        if target.active:
            raise DomainError(ALREADY_LINKED)
        target.active = True
        target.origin = TargetOrigin.LINKED
    # Who tied it, kept on the tie itself: a monitoring setting, like a lawyer's
    # search words, not a fact on the Teema's timeline (ADR 0224 §2).
    target.linked_by = actor
    target.linked_at = timezone.now()
    target.save()
    return target


@transaction.atomic
def unlink(target: Any, *, actor: Any) -> None:
    """Stop following a bill on the Teema; the tie stays, inactive, so nothing is offered twice."""
    if not target.active:
        return
    target.active = False
    target.linked_by = actor
    target.linked_at = timezone.now()
    target.save(update_fields=["active", "linked_by", "linked_at", "updated_at"])


@dataclass(frozen=True)
class Proposal:
    """A Teema a bill *may* belong to, and why. Never applied by itself."""

    matter: Any
    reason: str


def proposals(bill: Bill, *, user: Any) -> list[Proposal]:
    """Teemad the reader may see that could be this bill's, for a person to confirm.

    By the bill number in an open Teema's title (any membership: a person decides
    whether it is the same bill), or the bill's distinctive title words. Several
    may be returned: an ambiguity is shown, never resolved here.
    """
    from app.matters.models import Matter
    from app.register_watch.candidates import match_key

    if bill is None:
        return []
    already = set(teemad_of(bill).values_list("pk", flat=True))
    visible = (
        Matter.objects.visible_to(user).filter(is_open=True, deleted_at__isnull=True).real_data()
    )
    found: dict[Any, Proposal] = {}
    if bill.mark and bill.type_code:
        pattern = rf"\m{bill.mark}\s?{bill.type_code}\M"
        for matter in visible.filter(title__iregex=pattern).exclude(pk__in=already)[:5]:
            found[matter.pk] = Proposal(matter, f"teema pealkirjas on number {bill.label}")
    key = match_key(bill.title)
    words = key.split()[:3]
    # One distinctive word is enough when it is a long one («äriseadustiku»): the
    # generic words of a bill's title («muutmise seadus») are already gone from the key.
    if len(words) >= 2 or (words and len(words[0]) >= 8):
        query = visible.exclude(pk__in=already)
        for word in words:
            query = query.filter(title__icontains=word)
        for matter in query[:5]:
            found.setdefault(matter.pk, Proposal(matter, "pealkirjad on sarnased"))
    return list(found.values())
