"""Files that used to break the extraction pipeline quietly, or break it for ever.

Four audit findings, one theme: a hostile or merely unusual file must end in a
state somebody can see and explain, never in a crashed worker, a vanished
attachment or a FAILED row that says nothing.

* **AUD-045, raster budget** — a PDF page's size is a claim the file makes, and
  PDFium allocates whatever it claims the moment ``render`` is called. The scale
  is bounded before rendering, for OCR and for thumbnails alike.
* **AUD-045, crash retry** — a file that kills the worker leaves its row
  PROCESSING; past ``EXTRACTION_MAX_ATTEMPTS`` reclaims it is settled FAILED
  instead of being handed to the next worker to crash again.
* **AUD-046** — an Outlook message attached to an Outlook message is kept as a
  `.msg` Document instead of disappearing into an INFO log line.
* **AUD-047** — an explicit ``Content-Disposition: attachment`` wins over a
  Content-ID, so a real annex is never mistaken for a signature logo.
* **DX-4** — a file the intake reader failed to read keeps the reader's reason
  when it is promoted to evidence.
"""

from __future__ import annotations

import io
import math
from datetime import timedelta
from email.message import EmailMessage
from types import SimpleNamespace

import pytest
from django.core.files.uploadedfile import SimpleUploadedFile
from django.urls import reverse
from django.utils import timezone

from app.documents.derivatives import AttachmentDisposition
from app.documents.email_intake import attachments_of
from app.documents.enums import (
    DerivativeKind,
    DerivativeStatus,
    DocumentRole,
    ExtractionState,
)
from app.documents.extraction import email_msg
from app.documents.extraction import pdf as pdf_module
from app.documents.extraction.base import SourceFile
from app.documents.extraction.email_eml import EmlParser
from app.documents.extraction.limits import current_limits
from app.documents.extraction.orchestrator import (
    claim_version,
    pending_versions,
)
from app.documents.extraction.thumbnails import THUMBNAIL_BOX, pdf_first_page_thumbnail
from app.documents.models import DocumentDerivative, DocumentVersion
from app.matters import intake_extraction
from app.matters.staging import MatterIntakeFile
from tests import synthetic_corpus as corpus

pytestmark = pytest.mark.django_db

PDF = "application/pdf"
EML = "message/rfc822"
MSG = "application/vnd.ms-outlook"

#: The largest page the PDF specification allows, 200 inches a side. At the
#: default OCR resolution that is a 40 000 × 40 000 raster — 1.6 gigapixels.
HUGE_PAGE_POINTS = 14_400


# ---------------------------------------------------------------------------
# AUD-045 — the raster budget is applied before PDFium allocates
# ---------------------------------------------------------------------------


def _blank_pdf(width: float, height: float) -> bytes:
    import pypdfium2

    document = pypdfium2.PdfDocument.new()
    try:
        document.new_page(width, height)
        buffer = io.BytesIO()
        document.save(buffer)
    finally:
        document.close()
    return buffer.getvalue()


@pytest.fixture
def render_spy(monkeypatch):
    """Record every scale PDFium is asked to render at, and allocate nothing.

    The spy stands in for the render itself, so a test of a 1.6-gigapixel page
    costs a ten-pixel image rather than the gigabytes the bug would spend.
    """
    import pypdfium2
    from PIL import Image

    calls: list[tuple[float, float, float]] = []

    def render(page, *, scale=1, **_kwargs):
        width, height = page.get_size()
        calls.append((width, height, scale))
        return SimpleNamespace(to_pil=lambda: Image.new("RGB", (10, 10), "white"))

    monkeypatch.setattr(pypdfium2.PdfPage, "render", render)
    return calls


def _raster_pixels(width: float, height: float, scale: float) -> int:
    return math.ceil(width * scale) * math.ceil(height * scale)


def test_a_huge_page_thumbnail_is_rendered_inside_the_pixel_budget(render_spy) -> None:
    limits = current_limits()
    content = _blank_pdf(HUGE_PAGE_POINTS, HUGE_PAGE_POINTS)

    assert pdf_first_page_thumbnail(content, limits) is not None

    [(width, height, scale)] = render_spy
    assert width == height == HUGE_PAGE_POINTS
    assert _raster_pixels(width, height, scale) <= limits.max_image_pixels
    # Rendered straight to the box rather than at 1.4 and shrunk afterwards.
    assert math.ceil(width * scale) <= THUMBNAIL_BOX[0]


def test_an_ordinary_page_thumbnail_is_never_rendered_larger_than_before(render_spy) -> None:
    pdf_first_page_thumbnail(_blank_pdf(595, 842), current_limits())

    [(_width, height, scale)] = render_spy
    assert scale <= 1.4
    assert math.ceil(height * scale) <= THUMBNAIL_BOX[1]


def test_a_huge_page_is_ocr_rendered_inside_the_pixel_budget_and_says_so(
    render_spy, monkeypatch, settings
) -> None:
    """Still read, more coarsely, and the result does not pretend otherwise."""
    monkeypatch.setattr(pdf_module, "ocr_is_available", lambda: True)
    monkeypatch.setattr(pdf_module, "ocr_engine_version", lambda: "test")
    monkeypatch.setattr(
        pdf_module, "recognise_pil_image", lambda image: "Tuvastatud lehekülje tekst"
    )
    limits = current_limits()
    content = _blank_pdf(HUGE_PAGE_POINTS, HUGE_PAGE_POINTS)

    result = pdf_module.PdfParser().parse(
        SourceFile(content=content, filename="plakat.pdf", mime_type=PDF)
    )

    ocr_scale = render_spy[0][2]
    assert ocr_scale < settings.EXTRACTION_OCR_DPI / 72
    for width, height, scale in render_spy:
        assert _raster_pixels(width, height, scale) <= limits.max_image_pixels
    assert "vähendatud eraldusvõimega" in result.note


def test_an_ordinary_page_is_ocr_rendered_at_full_resolution_without_a_note(
    render_spy, monkeypatch, settings
) -> None:
    monkeypatch.setattr(pdf_module, "recognise_pil_image", lambda image: "tekst")

    _text, reduced = pdf_module._ocr_pages(_blank_pdf(595, 842), [1], current_limits())

    assert render_spy[0][2] == settings.EXTRACTION_OCR_DPI / 72
    assert reduced == []


# ---------------------------------------------------------------------------
# AUD-045 — a file that keeps killing its worker is settled, not retried for ever
# ---------------------------------------------------------------------------


@pytest.fixture
def pdf_version(normal_matter, capture_evidence):
    return capture_evidence(normal_matter, corpus.government_pdf(), "kaaskiri.pdf", PDF)


def _strand_version(version: DocumentVersion, settings) -> None:
    """What a worker killed mid-parse leaves behind: a claim older than the window."""
    DocumentVersion.objects.filter(pk=version.pk).update(
        extraction_claimed_at=timezone.now()
        - timedelta(minutes=settings.EXTRACTION_STALE_CLAIM_MINUTES + 1)
    )


def test_a_version_that_keeps_killing_its_worker_is_settled_failed(pdf_version, settings) -> None:
    settings.EXTRACTION_MAX_ATTEMPTS = 3

    for attempt in (1, 2, 3):
        claimed = claim_version(pdf_version.pk)
        assert claimed is not None
        assert claimed.extraction_attempts == attempt
        _strand_version(pdf_version, settings)

    assert claim_version(pdf_version.pk) is None

    pdf_version.refresh_from_db()
    assert pdf_version.extraction_state == ExtractionState.FAILED
    assert pdf_version.extraction_claimed_at is None
    assert pdf_version.extraction_note == (
        "Faili töötlemine katkes korduvalt; märgitud ebaõnnestunuks pärast 3 katset."
    )
    # The same operator-facing trace any other FAILED extraction leaves.
    failure = DocumentDerivative.objects.get(version=pdf_version, status=DerivativeStatus.FAILED)
    assert failure.error_code == "attempts_exhausted"
    # And it has left the queue, so the worker does not spin on it.
    assert pdf_version.pk not in {version.pk for version in pending_versions()}


def test_a_forced_re_extraction_starts_the_attempt_count_again(pdf_version, settings) -> None:
    settings.EXTRACTION_MAX_ATTEMPTS = 2
    for _ in range(2):
        claim_version(pdf_version.pk)
        _strand_version(pdf_version, settings)
    assert claim_version(pdf_version.pk) is None

    forced = claim_version(pdf_version.pk, force=True)

    assert forced is not None
    assert forced.extraction_attempts == 1
    assert forced.extraction_state == ExtractionState.PROCESSING


def test_a_reclaimed_version_that_then_finishes_is_an_ordinary_success(
    pdf_version, settings
) -> None:
    from app.documents.extraction.orchestrator import extract_document_version

    settings.EXTRACTION_MAX_ATTEMPTS = 3
    claim_version(pdf_version.pk)
    _strand_version(pdf_version, settings)

    claimed = claim_version(pdf_version.pk)
    assert claimed is not None and claimed.extraction_attempts == 2
    report = extract_document_version(claimed)

    assert report.state == ExtractionState.DONE
    pdf_version.refresh_from_db()
    assert pdf_version.extraction_state == ExtractionState.DONE
    assert pdf_version.extraction_note == ""
    assert not DocumentDerivative.objects.filter(
        version=pdf_version, status=DerivativeStatus.FAILED
    ).exists()


def test_a_normally_finishing_extraction_is_counted_once(pdf_version, extract) -> None:
    report = extract(pdf_version, force=False)

    assert report.state == ExtractionState.DONE
    pdf_version.refresh_from_db()
    assert pdf_version.extraction_attempts == 1


# -- the same budget on the intake reader -------------------------------------


@pytest.fixture
def staged_file(signed_in, evidence_root) -> MatterIntakeFile:
    upload = SimpleUploadedFile(
        "kaaskiri.pdf", corpus.text_pdf(["Näidisministeerium\n\nKiri"]), content_type=PDF
    )
    response = signed_in.post(reverse("matters:intake_stage"), {"files": [upload]})
    assert response.status_code in (200, 400), response.status_code
    return MatterIntakeFile.objects.get(session=response.context["intake_session"])


def _strand_staged(staged: MatterIntakeFile, settings) -> None:
    MatterIntakeFile.objects.filter(pk=staged.pk).update(
        extraction_claimed_at=timezone.now()
        - timedelta(minutes=settings.INTAKE_READER_STALE_CLAIM_MINUTES + 1)
    )


def test_a_staged_file_that_keeps_killing_the_reader_is_settled_failed(
    staged_file, settings
) -> None:
    settings.EXTRACTION_MAX_ATTEMPTS = 3

    for attempt in (1, 2, 3):
        claimed = intake_extraction.claim_intake_file(staged_file.pk)
        assert claimed is not None
        assert claimed.extraction_attempts == attempt
        _strand_staged(staged_file, settings)

    assert intake_extraction.claim_intake_file(staged_file.pk) is None

    staged_file.refresh_from_db()
    assert staged_file.extraction_state == ExtractionState.FAILED
    assert staged_file.extraction_claimed_at is None
    assert staged_file.extraction_note == (
        "Faili töötlemine katkes korduvalt; märgitud ebaõnnestunuks pärast 3 katset."
    )
    assert staged_file.pk not in {row.pk for row in intake_extraction.pending_intake_files()}
    assert intake_extraction.drain() == []


def test_a_forced_intake_read_starts_the_attempt_count_again(staged_file, settings) -> None:
    settings.EXTRACTION_MAX_ATTEMPTS = 2
    for _ in range(2):
        intake_extraction.claim_intake_file(staged_file.pk)
        _strand_staged(staged_file, settings)
    assert intake_extraction.claim_intake_file(staged_file.pk) is None

    forced = intake_extraction.claim_intake_file(staged_file.pk, force=True)

    assert forced is not None
    assert forced.extraction_attempts == 1


def test_a_normally_read_staged_file_is_counted_once(staged_file, settings) -> None:
    settings.EXTRACTION_MAX_ATTEMPTS = 3
    intake_extraction.claim_intake_file(staged_file.pk)
    _strand_staged(staged_file, settings)

    [report] = intake_extraction.drain()

    assert report.state == ExtractionState.DONE
    staged_file.refresh_from_db()
    assert staged_file.extraction_state == ExtractionState.DONE
    assert staged_file.extraction_attempts == 2
    assert staged_file.text


# ---------------------------------------------------------------------------
# AUD-046 — a forwarded Outlook message survives as its own `.msg` Document
# ---------------------------------------------------------------------------


def test_an_outlook_message_inside_an_outlook_message_becomes_a_document(
    normal_matter, capture_evidence, extract, specialist
) -> None:
    version = capture_evidence(
        normal_matter, corpus.outlook_msg(embedded_message=True), "edastus.msg", MSG
    )

    report = extract(version)

    assert report.state == ExtractionState.DONE
    links = list(attachments_of(version, viewer=specialist))
    assert [link.declared_filename for link in links] == [
        "lisa-1.pdf",
        f"{corpus.EMBEDDED_MSG_SUBJECT}.msg",
    ]
    nested = links[1].attachment_version
    assert nested.mime_type == MSG
    assert nested.document.role == DocumentRole.EMAIL_ATTACHMENT
    # Preserved whole and itself still to be read, not expanded by this parse.
    assert nested.extraction_state == ExtractionState.PENDING


def test_the_forwarded_message_is_a_readable_outlook_file(normal_matter, capture_evidence) -> None:
    content = corpus.outlook_msg(embedded_message=True)
    result = email_msg.MsgParser().parse(
        SourceFile(content=content, filename="a.msg", mime_type=MSG)
    )

    nested = result.attachments[-1]
    inner = email_msg.MsgParser().parse(
        SourceFile(content=nested.content, filename=nested.filename, mime_type=MSG)
    )
    metadata = next(p for p in inner.derivatives if p.kind == DerivativeKind.EMAIL_METADATA)
    assert metadata.metadata["subject"] == corpus.EMBEDDED_MSG_SUBJECT


def test_a_nested_message_that_cannot_be_exported_is_said_out_loud() -> None:
    """Kept the except, lost the silence: the message says an attachment is missing."""

    class Unexportable:
        def exportBytes(self) -> bytes:
            raise RuntimeError("katki")

    message = SimpleNamespace(
        attachments=[SimpleNamespace(data=Unexportable(), longFilename="edasi.msg")]
    )

    found, notes = email_msg._attachments(message)

    assert found == []
    assert notes == ["1 manust ei õnnestunud kirjast eraldada; originaalkiri on alles."]


# ---------------------------------------------------------------------------
# AUD-047 — `Content-Disposition: attachment` wins over a Content-ID
# ---------------------------------------------------------------------------


def _eml_with_parts(*, attachment_cid: bool, inline_cid: bool) -> bytes:
    message = EmailMessage()
    message["Subject"] = "Lisad"
    message["From"] = "keegi@naidis.invalid"
    message["To"] = "oigus@koda.invalid"
    message.set_content("Manused on lisatud.")
    if attachment_cid:
        message.add_attachment(
            corpus.text_pdf(["Lisa 1"]),
            maintype="application",
            subtype="pdf",
            filename="lisa-1.pdf",
            cid="<lisa-1@naidis.invalid>",
        )
    if inline_cid:
        message.add_attachment(
            corpus.scanned_png("LOGO"),
            maintype="image",
            subtype="png",
            filename="logo.png",
            disposition="inline",
            cid="<logo@naidis.invalid>",
        )
    return message.as_bytes()


def test_an_attachment_with_a_content_id_still_becomes_a_document(
    normal_matter, capture_evidence, extract, specialist
) -> None:
    raw = _eml_with_parts(attachment_cid=True, inline_cid=True)
    assert b"Content-Disposition: attachment" in raw and b"<lisa-1@naidis.invalid>" in raw
    version = capture_evidence(normal_matter, raw, "kiri.eml", EML)

    extract(version)

    links = list(attachments_of(version, viewer=specialist))
    assert [link.declared_filename for link in links] == ["lisa-1.pdf"]
    assert links[0].disposition == AttachmentDisposition.ATTACHMENT
    assert links[0].content_id == "lisa-1@naidis.invalid"
    filenames = set(
        normal_matter.documents.values_list("current_version__original_filename", flat=True)
    )
    assert "logo.png" not in filenames


def test_a_part_with_no_disposition_and_a_content_id_is_still_inline() -> None:
    """The case the Content-ID rule exists for: an image the HTML body draws."""
    raw = (
        b"MIME-Version: 1.0\r\n"
        b"Subject: Logo\r\n"
        b"From: keegi@naidis.invalid\r\n"
        b'Content-Type: multipart/mixed; boundary="b"\r\n\r\n'
        b"--b\r\nContent-Type: text/plain; charset=utf-8\r\n\r\nSisu\r\n"
        b"--b\r\nContent-Type: image/png\r\nContent-ID: <logo@naidis.invalid>\r\n"
        b"Content-Transfer-Encoding: base64\r\n\r\niVBORw0KGgo=\r\n"
        b"--b--\r\n"
    )

    result = EmlParser().parse(SourceFile(content=raw, filename="kiri.eml", mime_type=EML))

    [part] = result.attachments
    assert part.inline is True


def test_an_outlook_attachment_with_a_content_id_is_inline_only_when_hidden() -> None:
    visible = SimpleNamespace(
        data=b"%PDF-1.4 lisa", longFilename="lisa-1.pdf", cid="lisa@x", hidden=False
    )
    hidden = SimpleNamespace(
        data=b"\x89PNG logo", longFilename="logo.png", cid="logo@x", hidden=True
    )

    found, _notes = email_msg._attachments(SimpleNamespace(attachments=[visible, hidden]))

    assert [(part.filename, part.inline) for part in found] == [
        ("lisa-1.pdf", False),
        ("logo.png", True),
    ]


# ---------------------------------------------------------------------------
# DX-4 — a staged failure keeps its reason when it becomes evidence
# ---------------------------------------------------------------------------


def test_a_promoted_staged_failure_carries_the_readers_reason(
    signed_in, staged_file, settings
) -> None:
    reason = "PDF-i ei õnnestunud avada; fail võib olla rikutud."
    MatterIntakeFile.objects.filter(pk=staged_file.pk).update(
        extraction_state=ExtractionState.FAILED, extraction_note=reason
    )

    response = signed_in.post(
        reverse("matters:matter_create"),
        {"title": "Loetamatu fail", "intake": str(staged_file.session_id)},
    )

    assert response.status_code == 302
    version = DocumentVersion.objects.get(original_filename="kaaskiri.pdf")
    assert version.extraction_state == ExtractionState.FAILED
    assert version.extraction_note == reason


def test_a_promoted_staged_success_carries_no_note(signed_in, staged_file) -> None:
    MatterIntakeFile.objects.filter(pk=staged_file.pk).update(
        extraction_state=ExtractionState.DONE, extraction_note="2 lehekülge 2-st loeti OCR-iga."
    )

    signed_in.post(
        reverse("matters:matter_create"),
        {"title": "Loetud fail", "intake": str(staged_file.session_id)},
    )

    version = DocumentVersion.objects.get(original_filename="kaaskiri.pdf")
    assert version.extraction_state == ExtractionState.INTAKE_READ
    assert version.extraction_note == ""
