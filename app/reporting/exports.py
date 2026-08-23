"""CSV exports of exactly what the page was showing.

Four rules, and each of them is a way the export could otherwise lie.

**Same selector, same filters.** Every export calls the function the page's
numbers came from. An export built from its own query is a second definition,
and the first time the two disagree the export is what somebody has already
pasted into a board paper.

**Authorization is in the queryset, not in the writer.** The rows are already
scoped before this module sees them. Nothing here filters, and nothing here
could accidentally forget to.

**No hidden totals.** An export never carries "and 14 rows you may not see". The
count of what was withheld is itself information about restricted material, and
a file that says how much it is hiding has not hidden it (brief 49).

**It opens in Estonian Excel.** Semicolon-delimited and UTF-8 with a byte-order
mark, because a comma-delimited UTF-8 file opens in Tallinn as one column of
mojibake, and an export nobody can open is an export nobody uses. The encoding
is still plain UTF-8 for any machine reader.
"""

from __future__ import annotations

import codecs
import csv
from collections.abc import Iterator
from typing import Any
from urllib.parse import urlencode

from django.http import StreamingHttpResponse
from django.urls import reverse

from app.core.http import content_disposition
from app.reporting.context import ReportingContext
from app.reporting.selectors import historical, quality
from app.reporting.selectors import submissions as submission_selectors
from app.reporting.selectors.base import in_reporting_year, visible_matters
from app.submissions.enums import RecipientRole

#: Delimiter and byte-order mark, in one place. See the module docstring.
DELIMITER = ";"
BYTE_ORDER_MARK = codecs.BOM_UTF8

#: Rows are streamed rather than assembled, so a full-archive export does not
#: build a multi-megabyte string in memory before the first byte is sent.
CHUNK = 500


class _Echo:
    """A file-like object that returns what it is asked to write."""

    def write(self, value: str) -> str:
        return value


def _stream(rows: Iterator[list[Any]], filename: str) -> StreamingHttpResponse:
    writer = csv.writer(_Echo(), delimiter=DELIMITER, quoting=csv.QUOTE_MINIMAL)

    def encoded() -> Iterator[bytes]:
        yield BYTE_ORDER_MARK
        for row in rows:
            yield writer.writerow(row).encode("utf-8")

    response = StreamingHttpResponse(encoded(), content_type="text/csv; charset=utf-8")
    response["Content-Disposition"] = content_disposition("attachment", filename)
    return response


def _text(value: Any) -> str:
    return "" if value is None else str(value)


def _organisations(organisations: Any) -> str:
    """Several institutions in one cell, in an order that does not drift.

    Sorted by name rather than taken in join order, because a CSV people diff
    between two exports must differ only where the data did. Semicolon-separated
    because organisation names contain commas (``Rahandusministeerium, osakond``)
    and the comma is already the column separator (Agent-E brief 44).
    """
    names = sorted(organisation.name for organisation in organisations)
    return "; ".join(names)


# ---------------------------------------------------------------------------
# Teemad
# ---------------------------------------------------------------------------


def matters_csv(context: ReportingContext) -> StreamingHttpResponse:
    """The Matter population behind the current filters.

    The columns are the ones the register itself shows plus the provenance a
    reader needs to interpret them — record mode, origin and reporting year —
    because a spreadsheet row with no origin cannot be told from a native one.
    """
    queryset = visible_matters(context)
    if not context.period.is_all:
        queryset = in_reporting_year(queryset, context)

    def rows() -> Iterator[list[Any]]:
        yield [
            "viide",
            "pealkiri",
            "kirje_liik",
            "paritolu",
            "aruandlusaasta",
            "vastutaja",
            "hetkeseis",
            "menetlusliik",
            "avatud",
            "saabus",
            "arvamuse_tahtaeg",
            "algataja_voi_saatja",
            "adressaat",
        ]
        selected = (
            queryset.select_related("owner", "stage", "addressee_organisation")
            .prefetch_related("source_organisations")
            .distinct()
            .order_by("-reporting_year", "reference_number")
        )
        for matter in selected.iterator(chunk_size=CHUNK):
            yield [
                matter.display_reference,
                matter.title,
                matter.get_record_mode_display(),
                matter.get_origin_display(),
                _text(matter.reporting_year),
                matter.owner.display_name if matter.owner else "",
                matter.stage.label_et if matter.stage else "",
                matter.get_track_display() if matter.track else "",
                "jah" if matter.is_open else "ei",
                _text(matter.received_date),
                _text(matter.response_deadline),
                _organisations(matter.source_organisations.all()),
                matter.addressee_organisation.name if matter.addressee_organisation else "",
            ]

    return _stream(rows(), "teemad.csv")


# ---------------------------------------------------------------------------
# Arvamused
# ---------------------------------------------------------------------------


def submissions_csv(context: ReportingContext, **filters: Any) -> StreamingHttpResponse:
    """Sent Submissions, addressees and copies kept in separate columns."""
    queryset = submission_selectors.list_rows(context, **filters)

    def rows() -> Iterator[list[Any]]:
        yield [
            "saadetud",
            "pealkiri",
            "liik",
            "teema_viide",
            "teema",
            "adressaadid",
            "teadmiseks",
            "kanal",
            "viide",
        ]
        for submission in queryset.iterator(chunk_size=CHUNK):
            recipients = list(submission.recipient_rows.all())
            addressees = [
                row.organisation.name for row in recipients if row.role == RecipientRole.ADDRESSEE
            ]
            copies = [
                row.organisation.name
                for row in recipients
                if row.role == RecipientRole.FOR_INFORMATION
            ]
            yield [
                submission.sent_at.date().isoformat() if submission.sent_at else "",
                submission.title,
                submission.get_kind_display(),
                submission.matter.display_reference,
                submission.matter.title,
                " | ".join(addressees),
                " | ".join(copies),
                submission.channel,
                submission.reference,
            ]

    return _stream(rows(), "arvamused.csv")


# ---------------------------------------------------------------------------
# Ajalooline materjal
# ---------------------------------------------------------------------------


def materials_csv(context: ReportingContext, **filters: Any) -> StreamingHttpResponse:
    """Historical resource occurrences.

    One row per occurrence, carrying the SHA-256 so a reader can see for
    themselves which rows are the same bytes twice. Collapsing duplicates here
    would answer a different question from the one the page's headline number
    answers (brief 29).
    """
    queryset = historical.list_rows(context, **filters)

    def rows() -> Iterator[list[Any]]:
        yield [
            "failinimi",
            "sektsioon",
            "leht",
            "ploki_jarjekord",
            "suurus_baitides",
            "sha256",
            "lehe_lugemisjarjekord_ebaselge",
        ]
        for resource in queryset.iterator(chunk_size=CHUNK):
            page = resource.source_page
            yield [
                resource.original_filename,
                page.source_section,
                page.title,
                resource.source_block_ordinal,
                resource.size_bytes,
                resource.sha256,
                "jah" if page.reading_order_ambiguous else "ei",
            ]

    return _stream(rows(), "ajalooline-materjal.csv")


# ---------------------------------------------------------------------------
# Andmekvaliteet
# ---------------------------------------------------------------------------


def quality_csv(context: ReportingContext) -> StreamingHttpResponse:
    """The data-quality queues as they stand, coverage notes included.

    Coverage notes are exported alongside the actionable queues, and flagged as
    such in their own column. Leaving them out would produce a file that reads
    as a defect list when part of it is a limitation to understand.
    """
    queues = quality.queues(context)

    def rows() -> Iterator[list[Any]]:
        yield ["jarjekord", "arv", "on_katvuse_markus", "selgitus"]
        for queue in queues:
            yield [
                queue.label,
                queue.count,
                "jah" if queue.is_coverage_note else "ei",
                queue.explanation,
            ]

    return _stream(rows(), "andmekvaliteet.csv")


#: What the export links offer, and what each one calls. Kept as a table so a
#: URL cannot name an export that does not exist and the view has no branching
#: of its own.
EXPORTS: dict[str, str] = {
    "teemad": "Teemade populatsioon",
    "arvamused": "Saadetud arvamused",
    "materjalid": "Ajalooline materjal",
    "andmekvaliteet": "Andmekvaliteedi järjekorrad",
}


def export_url(context: ReportingContext, slug: str, **extra: str) -> str:
    params = {**context.query_params(), **{k: v for k, v in extra.items() if v}}
    return f"{reverse('reporting:export', kwargs={'slug': slug})}?{urlencode(params)}"
