"""`Seotud kirje` — which substantive record a file on Dokumendid belongs to.

A Matter's Dokumendid list is a flat table of files ordered by when each was
uploaded. Six months on, «what is this DOCX» and «which of the two consultations
was this reply to» were questions the table could not answer, although the
answer had been stored all along: every file captured on a Teema panel is tied
to the record it came with by a `DocumentLink`, written in the same transaction
as the record (docs/adr/0075 §6). Nothing read it back here (JUR-CASE-12).

So this is a **read** of those links and nothing else (docs/adr/0129 §10):

* **Only explicit links.** No relationship is derived from a shared upload
  minute, an operation identifier, a filename, a `NextAction` or the order rows
  happen to sit in — a guessed provenance is a fact this application would be
  inventing (docs/adr/0092 §6). A file with no link says nothing.
* **Both ends visible, or nothing.** Read through `DocumentLink.visible_to`, the
  conjunction of the document's visibility and the record's: a normal file
  linked to a restricted round prints no line, so the line cannot disclose that
  the round exists, its title or its day (AUTH-003, docs/adr/0038). A record
  taken off the file prints none either (docs/adr/0102).
* **The upload day and the order are untouched.** `Kuupäev` stays the day the
  file arrived and the list keeps its order; the record's own date is printed
  in the line, never used to move a row. Ordering by it would let a restricted
  record's date move a visible row.
* **One query for the page**, plus one for the addressees when a working
  document of an opinion is on it — never one per row.

The sent letter of a `Koja arvamus` is not here: it is tied to its send by
`Submission.final_version`, not by a link, and its row already says
«Saadetud <day> · <addressee>». A working document of the same opinion reads
«Koja arvamus · <day> · <addressee>», so the two identify one letter while their
`Roll` — `Arvamus` and `Töödokument` — keeps them two different files.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from typing import Any

from django.utils import timezone

from app.core.dates import format_estonian_date

#: What a record with no date reads as — the chronology's own words for it.
DATE_UNKNOWN = "kuupäev teadmata"

#: How long a record's own words may run in the line before it is cut. The line
#: names a record; its full title is one click away on the Teema.
DETAIL_LIMIT = 70

#: What each kind of linked record is called here, in the product's own words.
#: Keyed by `DocumentLink` column. `external_position` is absent on purpose: its
#: name is the record's own `kind_label` («Meile saadetud tagasiside», «Teiste
#: arvamus»), the headline its chronology row carries.
KIND_LABELS: dict[str, str] = {
    "submission": "Koja arvamus",
    "engagement": "Kaasamine",
    "procedural_development": "Märge",
    "important_date": "Oluline tähtaeg",
    "effective_date": "Jõustumine",
    "work_victory": "Töövõit",
    "entry": "Märkus",
}


@dataclass(frozen=True)
class RecordContext:
    """One `Seotud kirje` line: what the record is, when, and which one."""

    kind: str
    when: str = ""
    detail: str = ""

    def __str__(self) -> str:
        return " · ".join(part for part in (self.kind, self.when, self.detail) if part)


def _short(text: str) -> str:
    text = " ".join((text or "").split())
    return text if len(text) <= DETAIL_LIMIT else text[: DETAIL_LIMIT - 1].rstrip() + "…"


def _context_of(field: str, record: Any, addressees: dict[Any, list[str]]) -> RecordContext:
    """The line for one linked record, read off the row already in hand."""
    from app.matters.timeline import submission_chronology_day
    from app.submissions.enums import SubmissionStatus

    if field == "submission":
        if record.sent_at is None or record.status == SubmissionStatus.DRAFT:
            return RecordContext(KIND_LABELS[field], detail="koostamisel")
        parts = [", ".join(addressees.get(record.pk, []))]
        if record.status != SubmissionStatus.SENT:
            parts.append(str(record.get_status_display()).lower())
        return RecordContext(
            KIND_LABELS[field],
            when=format_estonian_date(submission_chronology_day(record)),
            detail=" · ".join(part for part in parts if part),
        )
    if field == "engagement":
        return RecordContext(
            KIND_LABELS[field], record.display_date or DATE_UNKNOWN, _short(record.title)
        )
    if field == "procedural_development":
        return RecordContext(
            KIND_LABELS[field], record.display_date or DATE_UNKNOWN, _short(record.title)
        )
    if field == "external_position":
        return RecordContext(
            record.kind_label, record.display_date or DATE_UNKNOWN, _short(record.author_label)
        )
    if field == "important_date":
        return RecordContext(
            KIND_LABELS[field], record.display_date or DATE_UNKNOWN, _short(record.title)
        )
    if field == "effective_date":
        return RecordContext(KIND_LABELS[field], record.display_date or record.display_when)
    if field == "work_victory":
        return RecordContext(
            KIND_LABELS[field], record.display_period or DATE_UNKNOWN, _short(record.title)
        )
    # `entry`: a note written under `Mida tegid?` — dated by when it says the
    # work happened, on the reader's own calendar.
    return RecordContext(
        KIND_LABELS["entry"],
        format_estonian_date(timezone.localtime(record.occurred_at).date()),
    )


def related_records(documents: Iterable[Any], *, viewer: Any) -> dict[Any, list[RecordContext]]:
    """Each document's `Seotud kirje` lines, keyed by document, for one page.

    Ordered by when each link was written, so a document's own origin — the
    record it was captured with — reads first. Identical lines are printed once.
    A document missing from the result has no visible link and prints nothing.
    """
    from app.documents.links import DocumentLink
    from app.submissions.models import SubmissionRecipient

    ids = [document.pk for document in documents]
    if not ids:
        return {}
    links = list(
        DocumentLink.objects.filter(document_id__in=ids)
        .visible_to(viewer)
        .select_related(
            "entry",
            "engagement",
            "important_date",
            "effective_date",
            "work_victory",
            "external_position__organisation",
            "procedural_development",
            "submission",
        )
        .order_by("created_at", "pk")
    )
    if not links:
        return {}

    # Who each linked opinion went to: the addressees only, the rows that say
    # who Koda formally wrote to, as every other surface prints them (ENG-061).
    submission_ids = {link.submission_id for link in links if link.submission_id is not None}
    addressees: dict[Any, list[str]] = {}
    if submission_ids:
        for row in (
            SubmissionRecipient.objects.addressees()
            .filter(submission_id__in=submission_ids)
            .select_related("organisation")
        ):
            addressees.setdefault(row.submission_id, []).append(row.organisation.name)

    found: dict[Any, list[RecordContext]] = {}
    for link in links:
        field = link.target_field
        if not field:
            continue
        context = _context_of(field, link.record, addressees)
        lines = found.setdefault(link.document_id, [])
        if context not in lines:
            lines.append(context)
    return found
