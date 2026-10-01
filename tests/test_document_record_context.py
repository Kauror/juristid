"""`Seotud kirje` on Dokumendid (JUR-CASE-12, docs/adr/0129 §10).

Every file captured on a Teema panel has had an explicit `DocumentLink` to the
record it came with since docs/adr/0075 §6; Dokumendid now reads it back. What is
pinned here is the read's discipline more than its wording:

* each kind of link names its record, its day and which one it is;
* the line comes **only** from a link — a file uploaded the same minute as a
  `Märge` without one says nothing, whatever its name;
* several files from one save stay several documents that read the same context,
  and the save is still one `Teema käik` row;
* a record the reader may not see, or one taken off the file, prints nothing;
* the upload date, the order and the number of rows are untouched, and the page
  costs the same number of queries for two files as for ten.
"""

from __future__ import annotations

import datetime as dt
import re

import pytest
from django.core.files.uploadedfile import SimpleUploadedFile
from django.db import connection
from django.test.utils import CaptureQueriesContext
from django.urls import reverse
from django.utils import timezone

from app.core.dates import format_estonian_date
from app.core.enums import Visibility
from app.documents.links import DocumentLink
from app.documents.models import Document
from app.documents.services import capture_evidence_on_open_matter
from app.matters.document_context import related_records
from app.matters.enums import ExternalPositionProvenance
from app.matters.models import MatterProceduralDevelopment
from app.matters.removal import remove_matter_record
from app.matters.timeline import matter_timeline
from app.matters.workspace import (
    add_matter_engagement,
    add_matter_external_position,
    add_matter_koda_opinion,
    add_procedural_development,
)
from tests import factories
from tests.synthetic_containers import signed_container

pytestmark = pytest.mark.django_db


def _pdf(name: str) -> SimpleUploadedFile:
    return SimpleUploadedFile(name, f"%PDF-1.4 {name}".encode(), content_type="application/pdf")


def _docx(name: str) -> SimpleUploadedFile:
    return SimpleUploadedFile(
        name,
        b"PK\x03\x04" + name.encode(),
        content_type="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    )


def _asice(name: str = "koda_opinion.asice") -> SimpleUploadedFile:
    return SimpleUploadedFile(
        name, signed_container(), content_type="application/vnd.etsi.asic-e+zip"
    )


def _ago(days: int) -> dt.date:
    return timezone.localdate() - dt.timedelta(days=days)


@pytest.fixture
def matter(specialist):
    return factories.MatterFactory(owner=specialist, stage=None)


def _page(client, matter, **query) -> str:
    url = reverse("matters:matter_documents", kwargs={"pk": matter.pk})
    return client.get(url, query).content.decode()


def _row(body: str, document) -> str:
    start = body.index(f'id="dokument-{document.pk}"')
    return body[start : body.index("</tr>", start)]


def _contexts(row: str) -> list[str]:
    return [
        " ".join(text.split())
        for text in re.findall(r'<span class="doctable__context">(.*?)</span>', row, re.S)
    ]


def _document(matter, title: str) -> Document:
    return Document.objects.get(matter=matter, title=title)


# ---------------------------------------------------------------------------
# Each kind of explicit link
# ---------------------------------------------------------------------------


def test_an_opinions_working_document_names_the_opinion(
    signed_in, matter, specialist, organisation
):
    day = _ago(5)
    add_matter_koda_opinion(
        matter=matter,
        author=specialist,
        upload=_asice(),
        recipients=[organisation],
        sent_on=day,
        working_uploads=[_docx("arvamus.docx")],
    )

    body = _page(signed_in, matter)

    working = _row(body, _document(matter, "arvamus.docx"))
    assert _contexts(working) == [
        f"Seotud kirje: Koja arvamus · {format_estonian_date(day)} · {organisation.name}"
    ]
    assert "Töödokument" in working


def test_the_letter_and_the_working_document_identify_one_opinion_as_two_files(
    signed_in, matter, specialist, organisation
):
    day = _ago(5)
    result = add_matter_koda_opinion(
        matter=matter,
        author=specialist,
        upload=_asice(),
        recipients=[organisation],
        sent_on=day,
        working_uploads=[_docx("arvamus.docx")],
    )
    letter = result.record.final_version.document

    body = _page(signed_in, matter)

    letter_row = _row(body, letter)
    working_row = _row(body, _document(matter, "arvamus.docx"))
    # The letter keeps its own line — tied by `final_version`, not by a link.
    assert "Saadetud" in letter_row and organisation.name in letter_row
    assert _contexts(letter_row) == []
    assert "Arvamus" in letter_row
    # The working document names the same send, under a different role.
    assert format_estonian_date(day) in _contexts(working_row)[0]
    assert "Töödokument" in working_row
    assert "badge--opinion" not in working_row


def test_a_kaasamine_file_names_the_round(signed_in, matter, specialist):
    day = _ago(14)
    add_matter_engagement(
        matter=matter,
        author=specialist,
        audience="liikmed",
        occurred_on=day,
        uploads=[_pdf("kutse.pdf")],
    )

    row = _row(_page(signed_in, matter), _document(matter, "kutse.pdf"))

    assert _contexts(row) == [f"Seotud kirje: Kaasamine · {format_estonian_date(day)} · liikmed"]


def test_a_marge_file_names_the_marge(signed_in, matter, specialist):
    day = _ago(16)
    add_procedural_development(
        matter=matter,
        author=specialist,
        title="Kooskõlastusring – eelnõu",
        occurred_on=day,
        uploads=[_pdf("eelnou.pdf")],
    )

    row = _row(_page(signed_in, matter), _document(matter, "eelnou.pdf"))

    assert _contexts(row) == [
        f"Seotud kirje: Märge · {format_estonian_date(day)} · Kooskõlastusring – eelnõu"
    ]


def test_a_received_position_file_names_its_author(signed_in, matter, specialist, organisation):
    add_matter_external_position(
        matter=matter,
        author=specialist,
        organisation=organisation,
        provenance=ExternalPositionProvenance.RECEIVED.value,
        stated_on=_ago(3),
        uploads=[_pdf("seisukoht.pdf")],
    )

    row = _row(_page(signed_in, matter), _document(matter, "seisukoht.pdf"))

    (line,) = _contexts(row)
    assert line.startswith("Seotud kirje: Meile saadetud tagasiside · ")
    assert line.endswith(organisation.name)


def test_an_undated_record_says_so_rather_than_inventing_a_day(signed_in, matter, specialist):
    add_matter_engagement(
        matter=matter, author=specialist, audience="juhatus", uploads=[_pdf("vastused.pdf")]
    )

    row = _row(_page(signed_in, matter), _document(matter, "vastused.pdf"))

    assert _contexts(row) == ["Seotud kirje: Kaasamine · kuupäev teadmata · juhatus"]


# ---------------------------------------------------------------------------
# Only explicit links, and the list itself untouched
# ---------------------------------------------------------------------------


def test_a_file_without_a_link_says_nothing_however_close_it_looks(signed_in, matter, specialist):
    """Same minute, same words in the name — and no link, so no line (0092 §6)."""
    add_procedural_development(
        matter=matter,
        author=specialist,
        title="Kooskõlastusring",
        occurred_on=_ago(1),
        uploads=[_pdf("kooskolastusring.pdf")],
    )
    plain = capture_evidence_on_open_matter(
        matter=matter,
        title="kooskolastusring lisa.pdf",
        role="OTHER",
        content=b"%PDF-1.4 lisa",
        original_filename="kooskolastusring lisa.pdf",
        mime_type="application/pdf",
        actor=specialist,
    )

    body = _page(signed_in, matter)

    assert _contexts(_row(body, plain)) == []
    assert len(_contexts(_row(body, _document(matter, "kooskolastusring.pdf")))) == 1


def test_files_from_one_save_stay_separate_documents_with_one_context(
    signed_in, matter, specialist
):
    day = _ago(2)
    add_procedural_development(
        matter=matter,
        author=specialist,
        title="Ministeerium saatis eelnõu",
        occurred_on=day,
        uploads=[_pdf("eelnou.pdf"), _pdf("seletuskiri.pdf"), _pdf("lisa.pdf")],
    )

    body = _page(signed_in, matter)

    expected = [f"Seotud kirje: Märge · {format_estonian_date(day)} · Ministeerium saatis eelnõu"]
    for name in ("eelnou.pdf", "seletuskiri.pdf", "lisa.pdf"):
        assert _contexts(_row(body, _document(matter, name))) == expected
    assert Document.objects.filter(matter=matter).count() == 3


def test_the_multi_file_save_is_still_one_teema_kaik_row(matter, specialist):
    add_procedural_development(
        matter=matter,
        author=specialist,
        title="Ministeerium saatis eelnõu",
        occurred_on=_ago(2),
        uploads=[_pdf("eelnou.pdf"), _pdf("seletuskiri.pdf")],
    )

    rows, _ = matter_timeline(matter=matter, user=specialist, limit=100)

    developments = [row for row in rows if row.procedural_development is not None]
    assert len(developments) == 1
    assert sorted(file.label for file in developments[0].files) == ["eelnou.pdf", "seletuskiri.pdf"]
    assert not [row for row in rows if row.record is None and row.files]


def test_the_upload_date_and_the_order_are_untouched(signed_in, matter, specialist):
    """The record's day is printed in the line, never used to move a row."""
    add_procedural_development(
        matter=matter,
        author=specialist,
        title="Vana samm",
        occurred_on=_ago(300),
        uploads=[_pdf("vana.pdf")],
    )
    newer = capture_evidence_on_open_matter(
        matter=matter,
        title="uus.pdf",
        role="OTHER",
        content=b"%PDF-1.4 uus",
        original_filename="uus.pdf",
        mime_type="application/pdf",
        actor=specialist,
    )

    body = _page(signed_in, matter)

    old = _document(matter, "vana.pdf")
    assert body.index(f'id="dokument-{newer.pk}"') < body.index(f'id="dokument-{old.pk}"')
    upload_day = timezone.localtime(old.created_at)
    assert f"{upload_day.day}.{upload_day.month}.{upload_day.year}" in _row(body, old)


# ---------------------------------------------------------------------------
# Visibility: a record the reader may not see is not named
# ---------------------------------------------------------------------------


def test_a_restricted_record_is_not_disclosed(client, matter, specialist, reader):
    add_procedural_development(
        matter=matter,
        author=specialist,
        title="Salajane samm",
        occurred_on=_ago(4),
        uploads=[_pdf("lisa.pdf")],
    )
    MatterProceduralDevelopment.objects.filter(matter=matter).update(
        visibility_override=Visibility.RESTRICTED
    )
    client.force_login(reader)

    body = _page(client, matter)

    # The file itself is at the Matter's visibility (the pinned gap of 0075's
    # links); what must not appear is anything about the record.
    row = _row(body, _document(matter, "lisa.pdf"))
    assert _contexts(row) == []
    assert "Salajane samm" not in body
    assert "Märge ·" not in body


def test_the_same_page_names_it_for_somebody_who_may_see_it(signed_in, matter, specialist):
    add_procedural_development(
        matter=matter,
        author=specialist,
        title="Salajane samm",
        occurred_on=_ago(4),
        uploads=[_pdf("lisa.pdf")],
    )
    MatterProceduralDevelopment.objects.filter(matter=matter).update(
        visibility_override=Visibility.RESTRICTED
    )

    row = _row(_page(signed_in, matter), _document(matter, "lisa.pdf"))

    assert len(_contexts(row)) == 1
    assert "Salajane samm" in _contexts(row)[0]


def test_a_record_taken_off_the_file_is_not_named(signed_in, matter, specialist):
    development = add_procedural_development(
        matter=matter,
        author=specialist,
        title="Ekslik märge",
        occurred_on=_ago(4),
        uploads=[_pdf("lisa.pdf")],
    ).record
    remove_matter_record(
        matter_id=matter.pk, kind_key="marge", record_id=development.pk, actor=specialist
    )

    row = _row(_page(signed_in, matter), _document(matter, "lisa.pdf"))

    assert _contexts(row) == []


def test_an_unrelated_document_reads_no_context(signed_in, matter, specialist):
    plain = capture_evidence_on_open_matter(
        matter=matter,
        title="Märkmed.pdf",
        role="OTHER",
        content=b"%PDF-1.4 m",
        original_filename="Märkmed.pdf",
        mime_type="application/pdf",
        actor=specialist,
    )

    assert related_records([plain], viewer=specialist) == {}
    assert "doctable__context" not in _page(signed_in, matter)


# ---------------------------------------------------------------------------
# One read per page, not per row
# ---------------------------------------------------------------------------


def _queries(matter, user, documents) -> int:
    with CaptureQueriesContext(connection) as captured:
        related_records(documents, viewer=user)
    return len(captured)


def test_the_read_costs_the_same_for_two_files_as_for_ten(matter, specialist, organisation):
    for index in range(5):
        add_procedural_development(
            matter=matter,
            author=specialist,
            title=f"Samm {index}",
            occurred_on=_ago(index + 1),
            uploads=[_pdf(f"a{index}.pdf"), _pdf(f"b{index}.pdf")],
        )
    add_matter_koda_opinion(
        matter=matter,
        author=specialist,
        upload=_asice(),
        recipients=[organisation],
        sent_on=_ago(1),
        working_uploads=[_docx("w.docx")],
    )
    documents = list(Document.objects.filter(matter=matter).order_by("created_at"))

    few = _queries(matter, specialist, documents[:2])
    many = _queries(matter, specialist, documents)

    assert many <= few + 1  # the addressees, read once when an opinion is on the page
    assert many <= 3
    assert DocumentLink.objects.filter(document__matter=matter).count() == 11
