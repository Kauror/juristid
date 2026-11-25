"""Committee protocols: who took part, and what was said about koda (ADR 0228 §3).

A committee sitting that has happened lists its minutes (``protocolDocuments``)
in `/api/events`. Each new protocol is read once (its content digest is kept), and
every place it names koda is kept as a `StenogramMention` of source PROTOCOL with
the exact passage. A protocol the source refuses (HTTP 403 on its metadata or its
file) is not an error of the night: it is counted and named in the stage's run
record (``refused``), nothing is stored for it, and it is asked again on the next
night while its sitting is inside `BACK_DAYS` — a refusal today says nothing about
tomorrow. Only a protocol actually read is never read again; one stored unread
(no file, or a file the parsers could not read) is checked again in the same
window. Many refusals in one night (`REFUSED_IS_A_PROBLEM`) are the source's
problem, not one document's, and keep the stage PARTIAL.

A passage in the protocol's attendance block («Kutsutud:», «Osalesid:»,
«Istungist võtsid osa:») marks ``participation``: evidence that koda
took part, which an invitation alone never is.
"""

from __future__ import annotations

import datetime as dt
import re
import uuid as uuid_module
from dataclasses import dataclass, field
from typing import Any

from django.utils import timezone

from app.riigikogu import client, documents
from app.riigikogu.enums import DocumentKind, KodaRole, MentionCategory, TextStatus
from app.riigikogu.models import CommitteeAgendaItem, ParliamentDocument, StenogramMention
from app.riigikogu.stenograms import find

BACK_DAYS = 21
#: The attendance block of a committee protocol.
_ATTENDANCE = re.compile(
    r"(kutsutud|osalesid|istungist\s+võtsid\s+osa|istungil\s+osalesid|kutsutud\s+isikud)\s*:",
    re.IGNORECASE,
)
#: Refusals in one night that make the stage PARTIAL: at least this many, and at
#: least half of the protocols asked for. Below it a refusal is one document's.
REFUSED_IS_A_PROBLEM = 3
_SECTION_END = re.compile(r"\n\s*(\d+\.\s|päevakord|istungi\s+juhataja|protokollis)", re.IGNORECASE)


@dataclass
class Counts:
    sittings: int = 0
    protocols_new: int = 0
    protocols_unreadable: int = 0
    #: Asked for this night (refused ones included); the base of `REFUSED_IS_A_PROBLEM`.
    protocols_asked: int = 0
    #: Refused by the source (HTTP 403): counted apart from unreadable files.
    protocols_refused: int = 0
    mentions_new: int = 0
    participations: int = 0
    errors: list[dict[str, str]] = field(default_factory=list)
    #: One entry per refused protocol: its id, where it was refused, the status, when.
    refused: list[dict[str, Any]] = field(default_factory=list)

    def as_dict(self) -> dict[str, Any]:
        out: dict[str, Any] = {
            k: v for k, v in self.__dict__.items() if k not in ("errors", "refused")
        }
        if self.refused:
            out["refused"] = list(self.refused)
        return out


class Refused(client.FetchError):
    """The source answered 403 for this one protocol (its metadata or its file)."""

    def __init__(self, protocol_uuid: uuid_module.UUID, where: str) -> None:
        super().__init__(f"Riigikogu keeldus protokollist ({where}): 403", status=403)
        self.protocol_uuid = protocol_uuid
        self.where = where


def _get(call: Any, protocol_uuid: uuid_module.UUID, where: str) -> Any:
    """``call()``, with a 403 told apart by its structured status, never by its text."""
    try:
        return call()
    except client.BudgetExceeded:
        raise
    except client.FetchError as error:
        if error.status == 403:
            raise Refused(protocol_uuid, where) from error
        raise


def attendance_spans(text: str) -> list[tuple[int, int]]:
    """Where the protocol's attendance blocks are."""
    spans = []
    for match in _ATTENDANCE.finditer(text or ""):
        end = _SECTION_END.search(text, match.end())
        spans.append((match.start(), end.start() if end else min(len(text), match.end() + 1500)))
    return spans


def _passage(text: str, start: int, end: int, width: int = 500) -> str:
    left = max(0, start - width // 2)
    right = min(len(text), end + width // 2)
    return " ".join(text[left:right].split())


def mentions_in(text: str) -> list[tuple[str, bool, int]]:
    """``(passage, in_attendance, offset)`` for each place the protocol names koda."""
    blocks = attendance_spans(text)
    found = []
    for start, end in find(text):
        inside = any(a <= start < b for a, b in blocks)
        found.append((_passage(text, start, end), inside, start))
    return found


def read_protocol(
    protocol_uuid: uuid_module.UUID,
    *,
    agenda: CommitteeAgendaItem | None,
    budget: client.Budget | None,
    counts: Counts,
) -> None:
    now = timezone.now()
    detail = _get(
        lambda: client.get_json(f"/api/documents/{protocol_uuid}", budget=budget),
        protocol_uuid,
        "metaandmed",
    )
    if not isinstance(detail, dict):
        raise client.FetchError("Riigikogu protokolli vastus on võõra kujuga")
    files = [f for f in detail.get("files") or [] if isinstance(f, dict) and f.get("uuid")]
    chosen = next(
        (
            f
            for f in files
            if str(f.get("fileName") or "").casefold().endswith((".pdf", ".html", ".htm", ".docx"))
        ),
        None,
    )
    text = documents.Text(TextStatus.NO_FILE)
    if chosen is not None:
        raw = _get(
            lambda: client.get_bytes(f"/api/files/{chosen['uuid']}/download", budget=budget),
            protocol_uuid,
            "fail",
        )
        text = documents.read(raw, str(chosen.get("fileName") or ""))
    document, created = ParliamentDocument.objects.get_or_create(
        uuid=protocol_uuid,
        defaults={
            "kind": DocumentKind.PROTOCOL,
            "title": str(detail.get("title") or "Komisjoni protokoll")[:1000],
            "document_type": str(detail.get("documentType") or "")[:64],
            "bill": agenda.bill if agenda is not None else None,
            "registered_on": _day(detail.get("created")),
            "url": client.document_api_url(protocol_uuid),
            "file_uuid": uuid_module.UUID(str(chosen["uuid"])) if chosen else None,
            "file_name": str(chosen.get("fileName") or "")[:300] if chosen else "",
            "text_status": text.status,
            "content_hash": text.digest,
            "first_seen_at": now,
            "last_seen_at": now,
        },
    )
    if not created:
        if document.content_hash == text.digest:
            return
        document.content_hash = text.digest
        document.text_status = text.status
        document.save(update_fields=["content_hash", "text_status", "updated_at"])
    counts.protocols_new += int(created)
    if text.status != TextStatus.READ:
        counts.protocols_unreadable += 1
        return
    found = mentions_in(text.text)
    if found and document.koda_role == KodaRole.NONE:
        document.koda_role = KodaRole.MENTIONED
        document.save(update_fields=["koda_role", "updated_at"])
    for passage, inside, offset in found:
        _row, new = StenogramMention.objects.get_or_create(
            key=f"protocol:{protocol_uuid}:{offset}"[:200],
            defaults={
                "source": "PROTOCOL",
                "participation": inside,
                "sitting_on": (agenda.sitting_at.date() if agenda else document.registered_on)
                or now.date(),
                "sitting_title": f"{agenda.committee} istung" if agenda else document.title[:300],
                "agenda_title": agenda.title if agenda else "",
                "agenda_uuid": agenda.uuid if agenda else None,
                "quote": passage,
                "context": passage,
                "url": client.file_url(chosen["uuid"]) if chosen else document.url,
                "category": MentionCategory.LEGISLATIVE
                if (agenda and agenda.bill_id)
                else MentionCategory.OTHER,
                "bill": agenda.bill if agenda else None,
            },
        )
        if new:
            counts.mentions_new += 1
            counts.participations += int(inside)


def _day(value: Any) -> dt.date | None:
    try:
        return dt.date.fromisoformat(str(value)[:10])
    except (TypeError, ValueError):
        return None


def read_recent(*, today: dt.date, budget: client.Budget | None = None) -> Counts:
    """The protocols of sittings in the last `BACK_DAYS` days that are not read yet."""
    counts = Counts()
    answer = client.get_json(
        "/api/events",
        {
            "startDate": (today - dt.timedelta(days=BACK_DAYS)).isoformat(),
            "endDate": today.isoformat(),
        },
        budget=budget,
    )
    if not isinstance(answer, list):
        raise client.FetchError("Riigikogu päevakorra vastus on võõra kujuga")
    # Only a protocol actually read is done; one stored unread is asked again while
    # its sitting is inside the window (the window is the bound on rechecking).
    known = set(
        ParliamentDocument.objects.filter(
            kind=DocumentKind.PROTOCOL, text_status=TextStatus.READ
        ).values_list("uuid", flat=True)
    )
    for day in answer:
        for group in (day or {}).get("committeeSittings") or []:
            for sitting in (group or {}).get("sittings") or []:
                counts.sittings += 1
                items = [i for i in (sitting or {}).get("agendaItems") or [] if isinstance(i, dict)]
                first = None
                for item in items:
                    try:
                        first = CommitteeAgendaItem.objects.filter(
                            uuid=uuid_module.UUID(str(item.get("uuid")))
                        ).first()
                    except ValueError:
                        first = None
                    if first is not None and first.bill_id:
                        break
                for protocol in (sitting or {}).get("protocolDocuments") or []:
                    try:
                        key = uuid_module.UUID(str((protocol or {}).get("uuid")))
                    except ValueError:
                        continue
                    if key in known:
                        continue
                    known.add(key)
                    counts.protocols_asked += 1
                    try:
                        read_protocol(key, agenda=first, budget=budget, counts=counts)
                    except client.BudgetExceeded:
                        raise
                    except Refused as refused:
                        counts.protocols_refused += 1
                        counts.refused.append(
                            {
                                "protokoll": str(key),
                                "koht": refused.where,
                                "status": 403,
                                "nähtud": timezone.now().isoformat(timespec="seconds"),
                            }
                        )
                    except client.FetchError as error:
                        counts.errors.append({"protokoll": str(key), "viga": str(error)[:200]})
    if counts.protocols_refused >= REFUSED_IS_A_PROBLEM and (
        2 * counts.protocols_refused >= counts.protocols_asked
    ):
        counts.errors.append(
            {
                "viga": f"Riigikogu keeldus {counts.protocols_refused} protokollist "
                f"{counts.protocols_asked}-st: allika juurdepääsu viga, mitte üksiku dokumendi oma"
            }
        )
    return counts
