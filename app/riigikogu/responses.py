"""Was an invitation answered? «Kutsele vastamine vajab kontrollimist» (ADR 0226 §4).

After an invitation's opinion date has passed, the bill's documents are looked at
for an opinion from koda (`DocumentKind.KODA_OPINION` on the same bill, registered
on or after the letter). Found: «Koja arvamus registris», linked. Not found: the
invitation **needs checking** — never «koda ei vastanud», because a joint letter,
an oral contribution at the sitting, a response filed under another title, a
decision not to answer or a late publication all look the same from here.

A person settles it: links an opinion, records a joint letter, a participation, a
decision not to respond or another activity — or leaves it open. Nothing here
counts towards any person's statistics, and no response rate is computed from it.
"""

from __future__ import annotations

import datetime as dt
from dataclasses import dataclass
from typing import Any

from django.db import transaction
from django.utils import timezone

from app.core.errors import DomainError
from app.riigikogu.enums import DeadlineKind, DocumentKind, FactState, KodaRole, ResponseCheck
from app.riigikogu.models import ExternalDeadline, Invitation, ParliamentDocument

#: Days after the deadline before asking: a response is often registered a few days late.
GRACE_DAYS = 3
SETTLED = frozenset(
    {
        ResponseCheck.FOUND,
        ResponseCheck.OPINION,
        ResponseCheck.JOINT,
        ResponseCheck.PARTICIPATED,
        ResponseCheck.DECIDED_NOT,
        ResponseCheck.OTHER,
    }
)
PERSON_CHOICES = (
    ResponseCheck.OPINION,
    ResponseCheck.JOINT,
    ResponseCheck.PARTICIPATED,
    ResponseCheck.DECIDED_NOT,
    ResponseCheck.OTHER,
)


@dataclass
class Counts:
    checked: int = 0
    found: int = 0
    needs_check: int = 0

    def as_dict(self) -> dict[str, Any]:
        return dict(self.__dict__)


def _deadline(invitation: Invitation) -> ExternalDeadline | None:
    return ExternalDeadline.objects.filter(
        document=invitation.document, kind=DeadlineKind.OPINION_REQUEST, state=FactState.ACTIVE
    ).first()


def koda_opinion_for(invitation: Invitation) -> ParliamentDocument | None:
    document = invitation.document
    if document.bill_id is None:
        return None
    since = document.letter_date or document.registered_on
    rows = ParliamentDocument.objects.filter(
        bill_id=document.bill_id, kind=DocumentKind.KODA_OPINION, koda_role=KodaRole.AUTHOR
    )
    if since is not None:
        rows = rows.filter(registered_on__gte=since)
    return rows.order_by("registered_on").first()


def store_opinions(bill: Any, detail: dict[str, Any], *, now: dt.datetime) -> int:
    """Keep the bill's listed opinions (`opinions[]` of its detail) as documents; how many new.

    koda's own opinion is recognised by its title naming koda as the one giving it
    («Eesti Kaubandus-Tööstuskoja arvamus …»): the opinions list carries no author
    field, so the title is what the Riigikogu states. Others are OTHER_OPINION, the
    material of «Teiste seisukohad» (ADR 0225).
    """
    import re
    import uuid as uuid_module

    from app.riigikogu import client, organisation

    new = 0
    for row in detail.get("opinions") or []:
        if not isinstance(row, dict) or not row.get("uuid"):
            continue
        try:
            key = uuid_module.UUID(str(row["uuid"]))
        except ValueError:
            continue
        title = str(row.get("title") or "").strip()
        lead = re.split(
            r"\s+(arvamus|seisukoh|ettepanek|pöördumi|ühispöördumi)", title, maxsplit=1, flags=re.I
        )[0]
        own = organisation.names_koda(lead)
        is_invitation = title.casefold().startswith("kaasamiskiri")
        kind = (
            DocumentKind.INVITATION
            if is_invitation
            else DocumentKind.KODA_OPINION
            if own
            else DocumentKind.OTHER_OPINION
        )
        files = [f for f in row.get("files") or [] if isinstance(f, dict) and f.get("uuid")]
        _document, created = ParliamentDocument.objects.get_or_create(
            uuid=key,
            defaults={
                "kind": kind,
                "koda_role": KodaRole.AUTHOR if own and not is_invitation else KodaRole.NONE,
                "title": title[:1000] or "(pealkirjata)",
                "document_type": str(row.get("documentType") or "")[:64],
                "bill": bill,
                "reference": str(row.get("reference") or "")[:120],
                "registered_on": _day(row.get("date")),
                "url": client.document_api_url(key),
                "file_uuid": uuid_module.UUID(str(files[0]["uuid"])) if files else None,
                "file_name": str(files[0].get("fileName") or "")[:300] if files else "",
                "first_seen_at": now,
                "last_seen_at": now,
            },
        )
        new += int(created)
    return new


def _day(value: Any) -> dt.date | None:
    try:
        return dt.date.fromisoformat(str(value)[:10])
    except (TypeError, ValueError):
        return None


def check(*, today: dt.date, budget: Any = None) -> Counts:
    """Settle what the register shows; mark the rest as needing a person's check.

    For each bill with an invitation past its date (and grace), the bill's opinion
    list is read once, so a koda opinion registered under the bill is found.
    """
    from app.riigikogu import bills, client

    counts = Counts()
    now = timezone.now()
    due_now = []
    for invitation in Invitation.objects.exclude(response__in=SETTLED).select_related("document"):
        due = _deadline(invitation)
        if due is None or due.due_on is None:
            continue
        if today <= due.due_on + dt.timedelta(days=GRACE_DAYS):
            continue
        due_now.append(invitation)
    for bill_id in {inv.document.bill_id for inv in due_now if inv.document.bill_id}:
        from app.riigikogu.models import Bill

        bill = Bill.objects.get(pk=bill_id)
        try:
            bill, detail = bills.read(bill.uuid, budget=budget)
        except client.BudgetExceeded:
            raise
        except client.FetchError:
            continue
        store_opinions(bill, detail, now=now)
    for invitation in due_now:
        counts.checked += 1
        found = koda_opinion_for(invitation)
        if found is not None:
            invitation.response = ResponseCheck.FOUND
            invitation.response_document = found
            counts.found += 1
        elif invitation.response != ResponseCheck.NEEDS_CHECK:
            invitation.response = ResponseCheck.NEEDS_CHECK
            counts.needs_check += 1
        else:
            continue
        invitation.save(update_fields=["response", "response_document", "updated_at"])
    return counts


@transaction.atomic
def settle(
    invitation: Invitation, *, actor: Any, answer: str, note: str = "", document: Any = None
) -> Invitation:
    """A person's answer to «vajab kontrollimist». The register's own finding is not overwritten."""
    if answer not in PERSON_CHOICES:
        raise DomainError("Tundmatu valik.")
    locked = Invitation.objects.select_for_update().get(pk=invitation.pk)
    if locked.response == ResponseCheck.FOUND:
        raise DomainError("Koja arvamus on registris juba olemas.")
    locked.response = answer
    locked.response_note = (note or "").strip()[:500]
    if document is not None:
        locked.response_document = document
    locked.response_by = actor
    locked.response_at = timezone.now()
    locked.save(
        update_fields=[
            "response",
            "response_note",
            "response_document",
            "response_by",
            "response_at",
            "updated_at",
        ]
    )
    return locked
