"""Invitations to koda: found, verified, read, kept, routed (ADR 0225).

The nightly stage (`discover`) asks the document register for everything naming
«Tööstuskoda» registered in a window that overlaps the previous one, reads each
new or changed document's metadata, and keeps those whose **addressee list**
names koda. Each such letter's file is read and its dates extracted
(`app.riigikogu.deadlines`). Then:

* the letter's bill already has a Teema (`WatchTarget`): the invitation waits on
  the Teema's «Registritest» tab and in Monitooring → Riigikogu, and the Teema's
  owner is told once (`RegisterNotice.invitations`);
* it has none: the bill's «Uued eelnõud» candidate is enriched, or one is made,
  and marked «Riigikogu kutsus koda arvamust avaldama» — shown whatever the
  model's verdict and whether the model is on at all — and the department head is
  told (`CandidateNotice.invitations`).

**Second line of defence.** The register is not a complete record of what was
sent to koda; e-mail stays the first line, and nothing here says otherwise.

**Rediscovery is idempotent.** A document already known with the same metadata
digest is only marked seen. A changed one records a `DocumentChange`; a changed
file re-reads the dates and, where a date moved, supersedes the old
`ExternalDeadline` with a new one (both kept) and tells the person again.
"""

from __future__ import annotations

import datetime as dt
import hashlib
import json
import uuid as uuid_module
from dataclasses import dataclass, field
from typing import Any

from django.db import transaction
from django.db.models import CharField, QuerySet
from django.db.models.functions import Cast
from django.utils import timezone

from app.riigikogu import bills, client, deadlines, documents, organisation
from app.riigikogu.enums import (
    DeadlineKind,
    DocumentKind,
    ExtractionStatus,
    FactState,
    InvitationReview,
    KodaRole,
    TextStatus,
)
from app.riigikogu.models import (
    Bill,
    DocumentChange,
    ExternalDeadline,
    Invitation,
    ParliamentDocument,
)

#: What the register is searched for. The search matches the author/addressee field.
SEARCH = "Tööstuskoda"
#: The previous window is read again by this much: a letter registered late is still found.
OVERLAP_DAYS = 7
#: The first window, when no stage has succeeded yet. Older letters are not offered
#: as new work (ADR 0228 §5: no historical backfill into the queues).
FIRST_WINDOW_DAYS = 30
CANDIDATE_LABEL = "Riigikogu kutsus koda arvamust avaldama"


@dataclass
class Counts:
    seen: int = 0
    new_documents: int = 0
    changed: int = 0
    invitations_new: int = 0
    deadlines_changed: int = 0
    extraction_failed: int = 0
    not_addressed: int = 0
    unlinked: int = 0
    errors: list[dict[str, str]] = field(default_factory=list)

    def as_dict(self) -> dict[str, Any]:
        return {k: v for k, v in self.__dict__.items() if k != "errors"}


def _day(value: Any) -> dt.date | None:
    if not value:
        return None
    try:
        return dt.date.fromisoformat(str(value)[:10])
    except ValueError:
        return None


def _metadata(detail: dict[str, Any]) -> dict[str, Any]:
    """The fields whose change is a change of the document (not of when it was read)."""
    volume = detail.get("volume") or {}
    files = detail.get("files") or []
    return {
        "title": str(detail.get("title") or "").strip(),
        "type": str(detail.get("documentType") or ""),
        "direction": str((detail.get("direction") or {}).get("code") or ""),
        "parties": str(detail.get("author") or "").strip(),
        "reference": str(detail.get("reference") or volume.get("reference") or ""),
        "volume": str(volume.get("uuid") or ""),
        "volume_type": str(volume.get("volumeType") or ""),
        "author_date": str(detail.get("authorDate") or "")[:10],
        "created": str(detail.get("created") or "")[:10],
        "files": sorted(
            f"{f.get('uuid')}:{f.get('fileName') or ''}"
            for f in files
            if isinstance(f, dict) and f.get("uuid")
        ),
    }


def _digest(meta: dict[str, Any]) -> str:
    return hashlib.sha256(json.dumps(meta, sort_keys=True, ensure_ascii=False).encode()).hexdigest()


def _letter_file(detail: dict[str, Any]) -> dict[str, Any] | None:
    """The file to read: HTML first (the letter itself), then PDF, then Word."""
    files = [f for f in detail.get("files") or [] if isinstance(f, dict) and f.get("uuid")]
    for suffixes in ((".html", ".htm"), (".pdf",), (".docx",), (".txt",)):
        for one in files:
            if str(one.get("fileName") or "").casefold().endswith(suffixes):
                return one
    return None


def _bill_for(meta: dict[str, Any], *, budget: client.Budget | None) -> Bill | None:
    """The bill a document belongs to: its volume when the volume is a bill.

    The volume UUID of a bill's document is the bill's own UUID (`/api/volumes/{uuid}`
    and `/api/volumes/drafts/{uuid}` are the same record). A stored bill is reused;
    an unknown one is read once.
    """
    if meta["volume_type"] != "eelnou" or not meta["volume"]:
        return None
    try:
        key = uuid_module.UUID(meta["volume"])
    except ValueError:
        return None
    known = Bill.objects.filter(uuid=key).first()
    if known is not None:
        return known
    bill, _detail = bills.read(key, budget=budget)
    return bill


def _store_document(
    uuid: uuid_module.UUID, detail: dict[str, Any], meta: dict[str, Any], *, now: dt.datetime
) -> tuple[ParliamentDocument, str]:
    """Create or update the row; ``"new"``, ``"changed"`` or ``"same"``."""
    digest = _digest(meta)
    role = organisation.koda_role(
        direction=meta["direction"], parties=meta["parties"], title=meta["title"]
    )
    values = {
        "title": meta["title"][:1000] or "(pealkirjata)",
        "document_type": meta["type"][:64],
        "direction": meta["direction"][:8],
        "parties": meta["parties"],
        "reference": meta["reference"][:120],
        "volume_uuid": uuid_module.UUID(meta["volume"]) if meta["volume"] else None,
        "letter_date": _day(meta["author_date"]),
        "registered_on": _day(meta["created"]),
        "koda_role": role,
        "url": client.document_api_url(uuid),
        "metadata_hash": digest,
        "last_seen_at": now,
    }
    document = ParliamentDocument.objects.select_for_update().filter(uuid=uuid).first()
    if document is None:
        kind = organisation.classify(role=role, title=meta["title"], document_type=meta["type"])
        return (
            ParliamentDocument.objects.create(uuid=uuid, kind=kind, first_seen_at=now, **values),
            "new",
        )
    if document.metadata_hash == digest:
        ParliamentDocument.objects.filter(pk=document.pk).update(last_seen_at=now)
        return document, "same"
    before = {
        name: str(getattr(document, name))
        for name in ("title", "parties", "direction", "reference")
    }
    for name, value in values.items():
        setattr(document, name, value)
    document.save()
    DocumentChange.objects.create(
        document=document,
        what="metadata",
        before=before,
        after={name: str(values[name]) for name in before},
    )
    return document, "changed"


def _read_text(
    document: ParliamentDocument, detail: dict[str, Any], *, budget: client.Budget | None
) -> documents.Text:
    one = _letter_file(detail)
    if one is None:
        return documents.Text(TextStatus.NO_FILE, why="dokumendil pole faili")
    raw = client.get_bytes(f"/api/files/{one['uuid']}/download", budget=budget)
    text = documents.read(raw, str(one.get("fileName") or ""))
    document.file_uuid = uuid_module.UUID(str(one["uuid"]))
    document.file_name = str(one.get("fileName") or "")[:300]
    return text


def _active(document: ParliamentDocument, kind: str) -> ExternalDeadline | None:
    return ExternalDeadline.objects.filter(
        document=document, kind=kind, state=FactState.ACTIVE
    ).first()


def _previous_round(
    bill: Bill | None, document: ParliamentDocument, kind: str
) -> ExternalDeadline | None:
    """The latest current date of this kind on an *earlier* letter: what an extension moves."""
    if bill is None:
        return None
    return (
        ExternalDeadline.objects.filter(
            document__bill=bill,
            kind=kind,
            state=FactState.ACTIVE,
            document__kind=DocumentKind.INVITATION,
        )
        .exclude(document=document)
        .order_by("-document__registered_on", "-created_at")
        .first()
    )


def _same(one: ExternalDeadline, found: deadlines.FoundDate) -> bool:
    return (
        one.due_on == found.day
        and one.due_time == found.time
        and one.extraction == found.status
        and one.evidence == found.evidence[:2000]
    )


def record_dates(
    document: ParliamentDocument, extraction: deadlines.Extraction, *, text_status: str
) -> int:
    """Keep the letter's dates as `ExternalDeadline`s; how many *changed* an earlier one.

    A letter that could not be read still gets an opinion-date row saying so, so the
    invitation is never shown as if it had no deadline at all.
    """
    changed = 0
    found_dates = list(extraction.dates)
    if not any(d.kind == DeadlineKind.OPINION_REQUEST for d in found_dates):
        status = (
            ExtractionStatus.UNREADABLE
            if text_status in (TextStatus.UNREADABLE, TextStatus.NO_FILE)
            else ExtractionStatus.NOT_FOUND
        )
        found_dates.append(
            deadlines.FoundDate(
                kind=DeadlineKind.OPINION_REQUEST, day=None, time=None, status=status, evidence=""
            )
        )
    for found in found_dates:
        current = _active(document, found.kind)
        if current is not None and _same(current, found):
            continue
        previous = current
        if previous is None and found.extension:
            previous = _previous_round(document.bill, document, found.kind)
        row = ExternalDeadline(
            kind=found.kind,
            bill=document.bill,
            document=document,
            due_on=found.day,
            due_time=found.time,
            extraction=found.status,
            evidence=found.evidence[:2000],
            doubt=found.doubt[:300],
            source_url=client.file_url(document.file_uuid) if document.file_uuid else document.url,
        )
        if previous is not None:
            previous.state = FactState.SUPERSEDED
            previous.save(update_fields=["state", "updated_at"])
            row.supersedes = previous
            if previous.due_on != found.day or previous.due_time != found.time:
                changed += 1
        row.save()
    if extraction.cancelled:
        ExternalDeadline.objects.filter(document=document, state=FactState.ACTIVE).update(
            state=FactState.CANCELLED, updated_at=timezone.now()
        )
        changed += 1
    return changed


def committee_of(document: ParliamentDocument, bill: Bill | None) -> str:
    return (bill.leading_committee if bill is not None else "")[:200]


def waiting_on_teema(matter: Any) -> QuerySet[Invitation]:
    """Invitations nobody has decided, on a bill tied to the Teema.

    ``matter`` is a Teema, or an ``OuterRef`` to one when this is the inside of an
    ``Exists`` (the bell's «something still waits» test): the tie is looked up in
    a correlated subquery, one level deeper, so the reference is lifted once more.
    """
    from django.db.models import Exists, OuterRef

    from app.register_watch.models import WatchSource, WatchTarget

    owner = OuterRef(matter) if isinstance(matter, OuterRef) else matter
    tied = WatchTarget.objects.filter(
        matter=owner,
        source=WatchSource.RIIGIKOGU,
        active=True,
        identifier=Cast(OuterRef("document__bill__uuid"), CharField()),
    )
    return Invitation.objects.filter(review=InvitationReview.NEW).filter(Exists(tied))


def _route(invitation: Invitation, *, told: dict[str, Any]) -> None:
    """Tell the right person once: the Teema's owner, else the department head."""
    bill = invitation.document.bill
    matters = list(bills.teemad_of(bill).select_related("owner")) if bill is not None else []
    if matters:
        for matter in matters:
            owner = getattr(matter, "owner", None)
            if matter.is_open and owner is not None and owner.is_active:
                told["teemad"][matter.pk] = (
                    matter,
                    told["teemad"].get(matter.pk, (matter, 0))[1] + 1,
                )
            else:
                told["head"] += 1
        return
    invitation.candidate = _candidate_for(invitation, bill)
    invitation.save(update_fields=["candidate", "updated_at"])
    told["head"] += 1


def _candidate_for(invitation: Invitation, bill: Bill | None) -> Any:
    """The bill's «Uued eelnõud» candidate, enriched; or a new one. Never a Teema."""
    from app.register_watch.candidates import match_key
    from app.register_watch.models import CandidateItem, TopicCandidate, WatchSource

    document = invitation.document
    if bill is not None:
        item = (
            CandidateItem.objects.filter(
                source=WatchSource.RIIGIKOGU, external_id=f"draft:{bill.uuid}"
            )
            .select_related("candidate")
            .first()
        )
        if item is not None:
            return item.candidate
    title = (bill.title if bill is not None and bill.title else document.title)[:500]
    candidate = TopicCandidate.objects.create(title=title, match_key=match_key(title))
    if bill is not None:
        CandidateItem.objects.get_or_create(
            source=WatchSource.RIIGIKOGU,
            external_id=f"draft:{bill.uuid}",
            defaults={
                "candidate": candidate,
                "title": f"{bill.title} ({bill.label})"[:500] if bill.label else bill.title[:500],
                "occurred_on": bill.initiated_on,
                "url": bill.url,
                "detail": " · ".join(p for p in (bill.label, bill.leading_committee) if p)[:300],
            },
        )
    return candidate


def _one(
    row: dict[str, Any],
    counts: Counts,
    told: dict[str, Any],
    *,
    budget: client.Budget | None,
    now: dt.datetime,
) -> None:
    try:
        key = uuid_module.UUID(str(row.get("uuid")))
    except ValueError:
        counts.errors.append({"dokument": str(row.get("uuid"))[:60], "viga": "tunnus puudub"})
        return
    counts.seen += 1
    known = ParliamentDocument.objects.filter(uuid=key).only("pk", "metadata_hash").first()
    detail = client.get_json(f"/api/documents/{key}", budget=budget)
    if not isinstance(detail, dict) or str(detail.get("uuid")) != str(key):
        raise client.FetchError("Riigikogu dokumendi vastus on võõra kujuga")
    meta = _metadata(detail)
    if known is not None and known.metadata_hash == _digest(meta):
        ParliamentDocument.objects.filter(pk=known.pk).update(last_seen_at=now)
        return
    bill = _bill_for(meta, budget=budget)
    with transaction.atomic():
        document, state = _store_document(key, detail, meta, now=now)
        if bill is not None and document.bill_id != bill.pk:
            document.bill = bill
            document.save(update_fields=["bill", "updated_at"])
    if state == "new":
        counts.new_documents += 1
    elif state == "changed":
        counts.changed += 1
    if document.koda_role != KodaRole.ADDRESSEE:
        if document.koda_role != KodaRole.AUTHOR:
            counts.not_addressed += 1
        return
    text = _read_text(document, detail, budget=budget)
    content_changed = bool(text.digest) and text.digest != document.content_hash
    with transaction.atomic():
        document = ParliamentDocument.objects.select_for_update().get(pk=document.pk)
        kind = organisation.classify(
            role=document.koda_role,
            title=document.title,
            document_type=document.document_type,
            text=text.text,
        )
        if content_changed and document.content_hash:
            DocumentChange.objects.create(
                document=document,
                what="content",
                before={"hash": document.content_hash},
                after={"hash": text.digest},
            )
        document.kind = kind
        document.text_status = text.status
        document.content_hash = text.digest or document.content_hash
        document.save()
        if kind != DocumentKind.INVITATION:
            return
        invitation, created = Invitation.objects.get_or_create(
            document=document, defaults={"committee": committee_of(document, bill)}
        )
        if not created and not content_changed and state == "same":
            return
        extraction = (
            deadlines.extract(text.text, letter_date=document.letter_date or document.registered_on)
            if text.status == TextStatus.READ
            else deadlines.Extraction(dates=(), status=ExtractionStatus.UNREADABLE)
        )
        if extraction.status in (
            ExtractionStatus.UNREADABLE,
            ExtractionStatus.NOT_FOUND,
            ExtractionStatus.CHECK,
        ):
            counts.extraction_failed += 1
        moved = record_dates(document, extraction, text_status=text.status)
        if extraction.hearing_on and invitation.hearing_on != extraction.hearing_on:
            invitation.hearing_on = extraction.hearing_on
            invitation.save(update_fields=["hearing_on", "updated_at"])
        if created:
            counts.invitations_new += 1
            _route(invitation, told=told)
            if invitation.candidate_id:
                counts.unlinked += 1
        elif moved:
            counts.deadlines_changed += 1
            _route(invitation, told=told)


def _tell(told: dict[str, Any]) -> None:
    from app.register_watch import candidate_notices, notices

    for matter, count in told["teemad"].values():
        notices.notify(matter, 0, invitations=count)
    if told["head"]:
        candidate_notices.notify_invitations(told["head"])


def discover(
    *,
    since: dt.date,
    until: dt.date,
    budget: client.Budget | None = None,
    now: dt.datetime | None = None,
) -> Counts:
    """Read the window's documents naming koda. One document's failure is that document's."""
    now = now or timezone.now()
    counts = Counts()
    told: dict[str, Any] = {"teemad": {}, "head": 0}
    params = {
        "letterAuthor": SEARCH,
        "createdStart": since.isoformat(),
        "createdEnd": until.isoformat(),
        "sort": "created,asc",
    }
    try:
        for row in client.pages("/api/documents", params, budget=budget):
            try:
                _one(row, counts, told, budget=budget, now=now)
            except client.BudgetExceeded:
                raise
            except client.FetchError as error:
                counts.errors.append(
                    {"dokument": str(row.get("uuid"))[:60], "viga": str(error)[:200]}
                )
    finally:
        _tell(told)
    return counts


# -- a person's decisions ------------------------------------------------------------


ALREADY_DECIDED = "Selle kutse kohta on juba otsustatud."
NOT_CONFIRMED = "Tähtaeg pole kinnitatud: vaata kutse lause üle ja kinnita tähtaeg enne lisamist."


def _lock(invitation: Invitation) -> Invitation:
    from app.core.errors import DomainError

    locked = (
        Invitation.objects.select_for_update(of=("self",))
        .select_related("document__bill")
        .get(pk=invitation.pk)
    )
    if locked.review != InvitationReview.NEW:
        raise DomainError(ALREADY_DECIDED)
    return locked


def _decide(invitation: Invitation, review: str, actor: Any, **extra: Any) -> None:
    invitation.review = review
    invitation.decided_by = actor
    invitation.decided_at = timezone.now()
    fields = ["review", "decided_by", "decided_at", "updated_at"]
    for name, value in extra.items():
        setattr(invitation, name, value)
        fields.append(name)
    invitation.save(update_fields=fields)


def current_opinion_date(invitation: Invitation) -> ExternalDeadline | None:
    return _active(invitation.document, DeadlineKind.OPINION_REQUEST)


@transaction.atomic
def add_as_date(
    invitation: Invitation, matter: Any, *, actor: Any, day: dt.date | None = None
) -> Any:
    """«Lisa tähtajana»: an informational `Oluline tähtaeg` on the Teema (ADR 0225 §3).

    Only a verified date. ``day`` is the date the person chose beside the button
    (prefilled with the date read from the letter, changeable from the calendar):
    when it differs from the verified date, or there is none yet, it is recorded
    first as the person's confirmed date (`confirm_date`: the read row superseded,
    both kept) and that is the date added. Never `Arvamuse tähtaeg`, which is
    Lovable's; the kind keeps it out of next steps, lateness, work counts and
    closure holds.
    """
    from app.core.errors import DomainError
    from app.intelligence.enums import ImportantDateKind
    from app.intelligence.services import add_important_date
    from app.workflow.enums import DatePrecision

    locked = _lock(invitation)
    due = current_opinion_date(locked)
    if day is not None and (due is None or not due.verified or due.due_on != day):
        confirm_date(locked, actor=actor, day=day)
        due = current_opinion_date(locked)
    if due is None or due.due_on is None or not due.verified:
        raise DomainError(NOT_CONFIRMED)
    bill = locked.document.bill
    tied = set(bills.teemad_of(bill).values_list("pk", flat=True)) if bill is not None else set()
    if matter.pk not in tied:
        raise DomainError("See kutse ei käi selle teema eelnõu kohta.")
    committee = locked.committee or "Riigikogu komisjon"
    time_part = f" kell {due.due_time.strftime('%H.%M')}" if due.due_time else ""
    record = add_important_date(
        matter=matter,
        title=f"Arvamuse tähtaeg: {committee}"[:300],
        date_value=due.due_on,
        period_end=due.due_on,
        date_precision=DatePrecision.EXACT,
        kind=ImportantDateKind.RK_OPINION_REQUEST,
        note=f"Riigikogu kutse kojale{time_part}. Allikas: {due.source_url}",
        actor=actor,
        source_text=due.evidence[:2000],
    )
    _decide(locked, InvitationReview.DATE_ADDED, actor, important_date=record)
    return record


@transaction.atomic
def link_to_date(invitation: Invitation, important_date: Any, *, actor: Any) -> Invitation:
    """«Seo olemasoleva tähtajaga»: the request is already on the Teema; no second date."""
    locked = _lock(invitation)
    _decide(locked, InvitationReview.LINKED, actor, important_date=important_date)
    return locked


@transaction.atomic
def add_reminder(
    invitation: Invitation, matter: Any, *, actor: Any, remind_on: dt.date, note: str = ""
) -> Any:
    """«Lisa meeldetuletus»: the person's own reminder — no Teema field, no workload (ADR 0225 §6).

    `Järgmiseks` is Lovable's (ADR 0213), so an invitation never writes it; the
    person's own reminder is the internal follow-up this surface may create.
    """
    from app.notifications import personal

    locked = _lock(invitation)
    reminder = personal.create(
        user=actor,
        matter=matter,
        remind_on=remind_on,
        note=(note or "Riigikogu kutse").strip(),
        today=timezone.localdate(),
    )
    _decide(locked, InvitationReview.REMINDER, actor)
    return reminder


@transaction.atomic
def no_action(invitation: Invitation, *, actor: Any) -> Invitation:
    """«Edasist tööd pole vaja»: recorded, nothing written on any Teema."""
    locked = _lock(invitation)
    _decide(locked, InvitationReview.NO_ACTION, actor)
    return locked


@transaction.atomic
def confirm_date(
    invitation: Invitation, *, actor: Any, day: dt.date, time: dt.time | None = None
) -> ExternalDeadline:
    """«Kontrolli tähtaega»: a person read the sentence and states the date.

    A new row supersedes the doubtful one (both kept); ``extraction`` FOUND, with
    the person's confirmation as its doubt-free evidence.
    """
    locked = _lock(invitation)
    current = current_opinion_date(locked)
    row = ExternalDeadline(
        kind=DeadlineKind.OPINION_REQUEST,
        bill=locked.document.bill,
        document=locked.document,
        due_on=day,
        due_time=time,
        extraction=ExtractionStatus.FOUND,
        evidence=(current.evidence if current else "")[:2000],
        doubt=f"kinnitas {getattr(actor, 'get_full_name', lambda: '')() or actor}"[:300],
        source_url=current.source_url if current else locked.document.url,
    )
    if current is not None:
        current.state = FactState.SUPERSEDED
        current.save(update_fields=["state", "updated_at"])
        row.supersedes = current
    row.save()
    return row
