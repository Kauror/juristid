"""Evidence is immutable, checksummed and separable from working documents."""

from __future__ import annotations

import hashlib

import pytest
from django.db import DatabaseError, connection, transaction

from app.audit.enums import ChangeEventType
from app.audit.models import ChangeEvent
from app.core.errors import DomainError
from app.documents.enums import DocumentRole, MalwareScanState
from app.documents.models import DocumentVersion
from app.documents.services import add_evidence_version, create_document, set_legal_hold
from tests import factories

pytestmark = pytest.mark.django_db

CONTENT = b"Sunteetiline toend."
PLAIN_TEXT = "text/plain"


def _document(matter=None, **kwargs):
    return create_document(
        matter=matter or factories.MatterFactory(),
        title=kwargs.pop("title", "Saabunud kiri"),
        role=kwargs.pop("role", DocumentRole.INCOMING_AUTHORITY),
        **kwargs,
    )


def test_evidence_version_stores_checksum_size_and_provenance(specialist):
    document = _document(created_by=specialist)
    version = add_evidence_version(
        document=document,
        content=CONTENT,
        original_filename="kiri.txt",
        mime_type=PLAIN_TEXT,
        uploaded_by=specialist,
        source_url="https://example.invalid/kiri.txt",
    )

    assert version.sha256 == hashlib.sha256(CONTENT).hexdigest()
    assert version.size_bytes == len(CONTENT)
    assert version.version_number == 1
    assert version.source_url == "https://example.invalid/kiri.txt"
    assert version.malware_scan_state == MalwareScanState.PENDING

    document.refresh_from_db()
    assert document.current_version_id == version.id
    assert document.has_evidence is True


def test_a_correction_becomes_a_new_version(specialist):
    document = _document(created_by=specialist)
    first = add_evidence_version(
        document=document, content=b"esimene", original_filename="a.txt", mime_type=PLAIN_TEXT
    )
    second = add_evidence_version(
        document=document, content=b"teine", original_filename="a.txt", mime_type=PLAIN_TEXT
    )

    assert (first.version_number, second.version_number) == (1, 2)
    assert document.versions.count() == 2
    document.refresh_from_db()
    assert document.current_version_id == second.id


def test_evidence_bytes_cannot_be_rewritten(specialist):
    document = _document(created_by=specialist)
    version = add_evidence_version(
        document=document, content=CONTENT, original_filename="a.txt", mime_type=PLAIN_TEXT
    )

    with pytest.raises(DatabaseError), transaction.atomic(), connection.cursor() as cursor:
        cursor.execute(
            "UPDATE documents_documentversion SET sha256 = %s WHERE id = %s",
            ["0" * 64, str(version.id)],
        )


def test_operational_state_on_a_version_stays_editable(specialist):
    document = _document(created_by=specialist)
    version = add_evidence_version(
        document=document, content=CONTENT, original_filename="a.txt", mime_type=PLAIN_TEXT
    )
    version.malware_scan_state = MalwareScanState.CLEAN
    version.save(update_fields=["malware_scan_state", "updated_at"])
    version.refresh_from_db()
    assert version.malware_scan_state == MalwareScanState.CLEAN


def test_unaccepted_formats_are_refused(specialist):
    document = _document(created_by=specialist)
    with pytest.raises(DomainError):
        add_evidence_version(
            document=document,
            content=b"<script>alert(1)</script>",
            original_filename="evil.html",
            mime_type="text/html",
        )


def test_empty_and_oversized_files_are_refused(specialist, settings):
    document = _document(created_by=specialist)
    with pytest.raises(DomainError):
        add_evidence_version(
            document=document, content=b"", original_filename="a.txt", mime_type=PLAIN_TEXT
        )

    settings.MAX_EVIDENCE_UPLOAD_BYTES = 4
    with pytest.raises(DomainError):
        add_evidence_version(
            document=document,
            content=b"liiga pikk",
            original_filename="a.txt",
            mime_type=PLAIN_TEXT,
        )


def test_checksum_column_only_accepts_lowercase_hex(specialist):
    document = _document(created_by=specialist)
    with pytest.raises(DatabaseError), transaction.atomic():
        DocumentVersion.objects.create(
            document=document,
            version_number=99,
            storage_key="x",
            original_filename="a.txt",
            mime_type=PLAIN_TEXT,
            size_bytes=1,
            sha256="NOT-A-HASH",
            acquired_at=document.created_at,
        )


def test_adding_evidence_records_a_change_event(specialist):
    document = _document(created_by=specialist)
    add_evidence_version(
        document=document,
        content=CONTENT,
        original_filename="a.txt",
        mime_type=PLAIN_TEXT,
        uploaded_by=specialist,
    )
    assert ChangeEvent.objects.filter(
        event_type=ChangeEventType.EVIDENCE_VERSION_ADDED, matter=document.matter
    ).exists()


def test_working_document_and_evidence_are_distinguishable(specialist):
    document = _document(
        created_by=specialist,
        role=DocumentRole.WORKING_DOCUMENT,
        sharepoint_site_id="site",
        sharepoint_drive_id="drive",
        sharepoint_item_id="item",
        sharepoint_web_url="https://example.invalid/doc.docx",
    )
    assert document.has_working_document is True
    assert document.has_evidence is False

    add_evidence_version(
        document=document,
        content=CONTENT,
        original_filename="lopplik.docx",
        mime_type=("application/vnd.openxmlformats-officedocument.wordprocessingml.document"),
    )
    document.refresh_from_db()
    assert document.has_working_document is True
    assert document.has_evidence is True


def test_legal_hold_requires_a_reason(specialist):
    document = _document(created_by=specialist)
    with pytest.raises(DomainError):
        set_legal_hold(document=document, on=True, reason="", actor=specialist)

    set_legal_hold(document=document, on=True, reason="Kohtuvaidlus 12/2026", actor=specialist)
    document.refresh_from_db()
    assert document.legal_hold is True
    assert document.legal_hold_set_by == specialist
