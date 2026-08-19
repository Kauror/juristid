"""The orchestrator: claiming, publishing, failing, and recovering.

Everything here is about what happens to *state* when a parse succeeds, fails
or is abandoned. The parsers themselves are tested against bytes in
`test_extraction_parsers.py`; this file assumes they work and asks what the
database looks like afterwards.

One theme runs through all of it: **the original evidence survives every path.**
A crashed parser, a killed worker, a failed publish, a rebuild — the bytes and
their checksum are the same afterwards as before, because none of those code
paths can reach them.
"""

from __future__ import annotations

from datetime import timedelta

import pytest
from django.utils import timezone

from app.documents.enums import (
    DerivativeKind,
    DerivativeStatus,
    DocumentRole,
    ExtractionState,
    MalwareScanState,
)
from app.documents.extraction.orchestrator import (
    claim_version,
    discard_derivatives,
    extract_document_version,
    is_eligible_for_extraction,
    pending_versions,
)
from app.documents.models import DocumentDerivative, DocumentTextFragment, DocumentVersion
from tests import synthetic_corpus as corpus

pytestmark = pytest.mark.django_db

PDF = "application/pdf"
DOCX = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"


@pytest.fixture
def pdf_version(normal_matter, capture_evidence):
    return capture_evidence(normal_matter, corpus.government_pdf(), "kaaskiri.pdf", PDF)


# -- the happy path --------------------------------------------------------


def test_a_new_evidence_version_starts_pending(pdf_version) -> None:
    """Intake must not wait for extraction, so it cannot have happened yet."""
    assert pdf_version.extraction_state == ExtractionState.PENDING
    assert pdf_version.pk in {version.pk for version in pending_versions()}


def test_extraction_writes_fragments_and_marks_the_version_done(pdf_version, extract) -> None:
    report = extract(pdf_version)

    pdf_version.refresh_from_db()
    assert report.state == ExtractionState.DONE
    assert pdf_version.extraction_state == ExtractionState.DONE
    assert pdf_version.extraction_claimed_at is None

    derivative = DocumentDerivative.objects.get(version=pdf_version, status=DerivativeStatus.ACTIVE)
    assert derivative.generator == "pdf"
    assert derivative.generator_version
    assert derivative.fragment_count == 6
    assert derivative.built_at is not None
    assert derivative.fragments.count() == 6


def test_every_fragment_knows_where_it_came_from(pdf_version, extract) -> None:
    extract(pdf_version)
    fragments = DocumentTextFragment.objects.filter(derivative__version=pdf_version).order_by(
        "ordinal"
    )

    assert [fragment.locator_label for fragment in fragments] == [
        f"lk {number}" for number in range(1, 7)
    ]
    assert all(fragment.character_count == len(fragment.text) for fragment in fragments)


def test_the_evidence_is_untouched_by_extraction(pdf_version, extract) -> None:
    before = (pdf_version.sha256, pdf_version.size_bytes, pdf_version.storage_key)
    extract(pdf_version)

    pdf_version.refresh_from_db()
    assert (pdf_version.sha256, pdf_version.size_bytes, pdf_version.storage_key) == before


def test_derivative_files_are_not_written_into_the_evidence_store(
    pdf_version, extract, evidence_root
) -> None:
    """A backup that omits derivatives must still be a complete backup."""
    extract(pdf_version)

    evidence_files = list((evidence_root / "evidence").rglob("*"))
    assert evidence_files, "the evidence directory should hold the original"
    assert all("derivative" not in path.name.lower() for path in evidence_files if path.is_file())


# -- idempotency and parser upgrades ---------------------------------------


def test_processing_the_same_bytes_twice_leaves_one_active_derivative(pdf_version, extract) -> None:
    extract(pdf_version)
    extract(pdf_version)

    active = DocumentDerivative.objects.filter(
        version=pdf_version, kind=DerivativeKind.EXTRACTED_TEXT, status=DerivativeStatus.ACTIVE
    )
    assert active.count() == 1
    assert (
        DocumentTextFragment.objects.filter(
            derivative__version=pdf_version, derivative__status=DerivativeStatus.ACTIVE
        ).count()
        == 6
    )


def test_the_previous_derivative_is_superseded_not_deleted(pdf_version, extract) -> None:
    """Kept until a rebuild removes it, so an upgrade is inspectable."""
    extract(pdf_version)
    extract(pdf_version)

    statuses = sorted(
        DocumentDerivative.objects.filter(
            version=pdf_version, kind=DerivativeKind.EXTRACTED_TEXT
        ).values_list("status", flat=True)
    )
    assert statuses == [DerivativeStatus.ACTIVE, DerivativeStatus.SUPERSEDED]


def test_a_failed_reprocess_keeps_the_working_derivative(pdf_version, extract, monkeypatch) -> None:
    """The property that lets a parser be upgraded without risking search.

    Degraded is recoverable; empty is not, and a lawyer whose search stopped
    finding a document has no way to tell which of the two happened.
    """
    extract(pdf_version)
    good = DocumentDerivative.objects.get(version=pdf_version, status=DerivativeStatus.ACTIVE)

    from app.documents.extraction import pdf as pdf_module

    def explode(self, source):
        raise RuntimeError("parser upgrade went wrong")

    monkeypatch.setattr(pdf_module.PdfParser, "parse", explode)
    report = extract(pdf_version)

    assert report.state == ExtractionState.FAILED
    good.refresh_from_db()
    assert good.status == DerivativeStatus.ACTIVE
    assert good.fragments.count() == 6


# -- failure isolation -----------------------------------------------------


def test_a_corrupt_file_fails_without_taking_anything_with_it(
    normal_matter, capture_evidence, extract
) -> None:
    version = capture_evidence(normal_matter, corpus.corrupt_pdf(), "katkine.pdf", PDF)
    report = extract(version)

    version.refresh_from_db()
    assert report.state == ExtractionState.FAILED
    assert report.error_code == "unreadable_pdf"
    assert version.extraction_state == ExtractionState.FAILED
    assert version.extraction_note
    assert version.extraction_claimed_at is None
    # And the bytes are still there to be read by a human.
    assert version.sha256


def test_a_failure_records_a_derivative_an_operator_can_read(
    normal_matter, capture_evidence, extract
) -> None:
    version = capture_evidence(normal_matter, corpus.corrupt_pdf(), "katkine.pdf", PDF)
    extract(version)

    failed = DocumentDerivative.objects.get(version=version, status=DerivativeStatus.FAILED)
    assert failed.error_code == "unreadable_pdf"
    assert failed.error_detail
    # No document content in the error. Errors reach logs, and logs are the one
    # place extracted text must never appear (Stage-2B brief 49).
    assert "Kooskõlastuskiri" not in failed.error_detail


def test_a_parser_that_raises_something_unexpected_is_contained(
    pdf_version, extract, monkeypatch
) -> None:
    from app.documents.extraction import pdf as pdf_module

    def explode(self, source):
        raise ZeroDivisionError("not an ExtractionError at all")

    monkeypatch.setattr(pdf_module.PdfParser, "parse", explode)
    report = extract(pdf_version)

    assert report.state == ExtractionState.FAILED
    assert report.error_code == "parser_error"


def test_one_bad_file_does_not_stop_the_queue(normal_matter, capture_evidence) -> None:
    """The property the whole worker design exists for."""
    from app.documents.extraction.orchestrator import claim_version as claim

    bad = capture_evidence(normal_matter, corpus.corrupt_pdf(), "katkine.pdf", PDF)
    good = capture_evidence(normal_matter, corpus.government_pdf(), "korras.pdf", PDF)

    states = {}
    for version in list(pending_versions()):
        claimed = claim(version.pk)
        assert claimed is not None
        states[claimed.pk] = extract_document_version(claimed).state

    assert states[bad.pk] == ExtractionState.FAILED
    assert states[good.pk] == ExtractionState.DONE


def test_missing_evidence_bytes_are_reported_not_crashed(pdf_version, extract) -> None:
    from app.documents.services import evidence_storage

    evidence_storage().delete(pdf_version.storage_key)
    report = extract(pdf_version)

    assert report.state == ExtractionState.FAILED
    assert report.error_code == "evidence_missing"


# -- unsupported formats ---------------------------------------------------


def test_a_zip_is_stored_and_deliberately_not_unpacked(
    normal_matter, capture_evidence, extract
) -> None:
    version = capture_evidence(normal_matter, corpus.zip_archive(), "arhiiv.zip", "application/zip")
    report = extract(version)

    version.refresh_from_db()
    assert report.state == ExtractionState.NOT_APPLICABLE
    assert "lahti" in version.extraction_note
    assert not DocumentDerivative.objects.filter(version=version).exists()


def test_a_legacy_office_file_is_stored_with_an_honest_reason(
    normal_matter, capture_evidence, extract
) -> None:
    version = capture_evidence(
        normal_matter, b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1vana", "vana.doc", "application/msword"
    )
    report = extract(version)

    version.refresh_from_db()
    assert report.state == ExtractionState.NOT_APPLICABLE
    assert "originaal on alles" in version.extraction_note


# -- claiming and stale recovery -------------------------------------------


def test_a_claim_moves_the_version_to_processing(pdf_version) -> None:
    claimed = claim_version(pdf_version.pk)

    assert claimed is not None
    assert claimed.extraction_state == ExtractionState.PROCESSING
    assert claimed.extraction_claimed_at is not None


def test_a_second_worker_cannot_claim_a_version_already_claimed(pdf_version) -> None:
    assert claim_version(pdf_version.pk) is not None
    assert claim_version(pdf_version.pk) is None


def test_a_stale_claim_is_reclaimed_so_a_killed_worker_loses_nothing(pdf_version, settings) -> None:
    """The only honest difference between a slow parse and a dead worker is time."""
    settings.EXTRACTION_STALE_CLAIM_MINUTES = 30
    claim_version(pdf_version.pk)

    DocumentVersion.objects.filter(pk=pdf_version.pk).update(
        extraction_claimed_at=timezone.now() - timedelta(minutes=31)
    )

    assert pdf_version.pk in {version.pk for version in pending_versions()}
    assert claim_version(pdf_version.pk) is not None


def test_a_fresh_claim_is_left_alone(pdf_version, settings) -> None:
    settings.EXTRACTION_STALE_CLAIM_MINUTES = 30
    claim_version(pdf_version.pk)

    assert pdf_version.pk not in {version.pk for version in pending_versions()}


def test_a_finished_version_is_not_reclaimed_without_force(pdf_version, extract) -> None:
    extract(pdf_version)
    assert claim_version(pdf_version.pk) is None
    assert claim_version(pdf_version.pk, force=True) is not None


# -- the malware gate ------------------------------------------------------


def test_a_clean_file_is_always_eligible(pdf_version) -> None:
    pdf_version.malware_scan_state = MalwareScanState.CLEAN
    assert is_eligible_for_extraction(pdf_version) is True


def test_an_unscanned_file_is_processed_only_in_a_synthetic_environment(
    pdf_version, settings
) -> None:
    settings.REAL_DATA_ALLOWED = False
    assert is_eligible_for_extraction(pdf_version) is True

    settings.REAL_DATA_ALLOWED = True
    assert is_eligible_for_extraction(pdf_version) is False


def test_an_ineligible_file_stays_pending_and_is_never_marked_clean(
    pdf_version, settings, extract
) -> None:
    """Marking PENDING as CLEAN to unblock extraction replaces a missing
    control with a lie about one."""
    settings.REAL_DATA_ALLOWED = True
    report = extract(pdf_version)

    pdf_version.refresh_from_db()
    assert report.state == ExtractionState.PENDING
    assert pdf_version.malware_scan_state == MalwareScanState.PENDING
    assert not DocumentDerivative.objects.filter(version=pdf_version).exists()


def test_an_infected_file_is_never_processed(pdf_version, settings) -> None:
    settings.REAL_DATA_ALLOWED = True
    pdf_version.malware_scan_state = MalwareScanState.INFECTED
    assert is_eligible_for_extraction(pdf_version) is False


# -- rebuilding ------------------------------------------------------------


def test_discarding_derivatives_leaves_the_evidence_alone(pdf_version, extract) -> None:
    extract(pdf_version)
    before = pdf_version.sha256

    discard_derivatives(pdf_version)

    assert not DocumentDerivative.objects.filter(version=pdf_version).exists()
    assert not DocumentTextFragment.objects.filter(derivative__version=pdf_version).exists()
    pdf_version.refresh_from_db()
    assert pdf_version.sha256 == before


def test_a_rebuild_reproduces_the_same_derived_corpus(pdf_version, extract) -> None:
    """The primary correctness property of the whole stage.

    If this holds, derived state is genuinely disposable and the backup only has
    to cover the database and the evidence (Stage-2B brief 68, 81).
    """
    extract(pdf_version)
    original = DocumentDerivative.objects.get(version=pdf_version, status=DerivativeStatus.ACTIVE)
    fingerprint = (
        original.content_sha256,
        original.fragment_count,
        original.character_count,
        sorted(original.fragments.values_list("ordinal", "locator_label", "text_source", "text")),
    )

    discard_derivatives(pdf_version)
    extract(pdf_version)

    rebuilt = DocumentDerivative.objects.get(version=pdf_version, status=DerivativeStatus.ACTIVE)
    assert (
        rebuilt.content_sha256,
        rebuilt.fragment_count,
        rebuilt.character_count,
        sorted(rebuilt.fragments.values_list("ordinal", "locator_label", "text_source", "text")),
    ) == fingerprint


def test_a_rebuild_does_not_duplicate_evidence(normal_matter, pdf_version, extract) -> None:
    extract(pdf_version)
    before = DocumentVersion.objects.filter(document__matter=normal_matter).count()

    discard_derivatives(pdf_version)
    extract(pdf_version)

    assert DocumentVersion.objects.filter(document__matter=normal_matter).count() == before


# -- several formats end to end --------------------------------------------


@pytest.mark.parametrize(
    ("filename", "mime_type", "content_name", "expected_state"),
    [
        ("kaaskiri.pdf", PDF, "government_pdf", ExtractionState.DONE),
        ("markused.docx", DOCX, "draft_docx", ExtractionState.DONE),
        (
            "lisa.xlsx",
            "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            "annex_xlsx",
            ExtractionState.DONE,
        ),
        (
            "ulevaade.pptx",
            "application/vnd.openxmlformats-officedocument.presentationml.presentation",
            "briefing_pptx",
            ExtractionState.DONE,
        ),
        ("markmed.txt", "text/plain", "memo_txt", ExtractionState.DONE),
        ("kogused.csv", "text/csv", "counts_csv", ExtractionState.DONE),
        ("kiri.eml", "message/rfc822", "consultation_eml", ExtractionState.DONE),
        ("kiri.msg", "application/vnd.ms-outlook", "outlook_msg", ExtractionState.DONE),
        ("arhiiv.zip", "application/zip", "zip_archive", ExtractionState.NOT_APPLICABLE),
    ],
)
def test_every_supported_format_reaches_a_terminal_state(
    normal_matter,
    capture_evidence,
    extract,
    filename,
    mime_type,
    content_name,
    expected_state,
) -> None:
    """No path leaves a version stuck in PROCESSING."""
    content = getattr(corpus, content_name)()
    version = capture_evidence(normal_matter, content, filename, mime_type)

    report = extract(version)
    version.refresh_from_db()

    assert version.extraction_state == expected_state
    assert report.state == expected_state
    assert version.extraction_claimed_at is None


def test_an_attachment_document_gets_its_own_role(normal_matter, capture_evidence, extract) -> None:
    version = capture_evidence(
        normal_matter, corpus.consultation_eml(), "kiri.eml", "message/rfc822"
    )
    extract(version)

    roles = set(normal_matter.documents.values_list("role", flat=True))
    assert DocumentRole.EMAIL_ATTACHMENT in roles


# -- thumbnails ------------------------------------------------------------


def test_a_pdf_gets_a_thumbnail_stored_apart_from_its_evidence(
    pdf_version, extract, evidence_root
) -> None:
    extract(pdf_version)

    thumbnail = DocumentDerivative.objects.get(
        version=pdf_version, kind=DerivativeKind.THUMBNAIL, status=DerivativeStatus.ACTIVE
    )
    assert thumbnail.storage_key
    assert thumbnail.metadata["format"] == "PNG"
    # In the derivative store, and nowhere near the evidence.
    assert (evidence_root / "derivatives" / thumbnail.storage_key).exists()
    assert not (evidence_root / "evidence" / thumbnail.storage_key).exists()


def test_a_thumbnail_is_a_generated_png_not_the_uploaded_bytes(pdf_version, extract) -> None:
    """The reason one may be shown inline and the other may not.

    These bytes were produced by Pillow from the original. Whatever was
    interesting about the upload's structure did not survive the round trip.
    """
    from app.documents.extraction.orchestrator import derivative_storage

    extract(pdf_version)
    thumbnail = DocumentDerivative.objects.get(
        version=pdf_version, kind=DerivativeKind.THUMBNAIL, status=DerivativeStatus.ACTIVE
    )

    with derivative_storage().open(thumbnail.storage_key, "rb") as handle:
        content = handle.read()

    assert content.startswith(b"\x89PNG\r\n\x1a\n")
    assert content != pdf_version.storage_key.encode()
    assert not content.startswith(b"%PDF")


def test_discarding_derivatives_removes_the_stored_thumbnail(
    pdf_version, extract, evidence_root
) -> None:
    extract(pdf_version)
    key = DocumentDerivative.objects.get(
        version=pdf_version, kind=DerivativeKind.THUMBNAIL
    ).storage_key

    discard_derivatives(pdf_version)

    assert not (evidence_root / "derivatives" / key).exists()


def test_a_thumbnail_failure_does_not_cost_the_extraction(
    pdf_version, extract, monkeypatch
) -> None:
    """A missing thumbnail costs a preview tile. It must not cost the text."""
    from app.documents.extraction import pdf as pdf_module

    monkeypatch.setattr(pdf_module, "pdf_first_page_thumbnail", lambda content, limits: None)
    report = extract(pdf_version)

    assert report.state == ExtractionState.DONE
    assert not DocumentDerivative.objects.filter(
        version=pdf_version, kind=DerivativeKind.THUMBNAIL
    ).exists()
