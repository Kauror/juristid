"""Evidence is immutable, checksummed and separable from working documents."""

from __future__ import annotations

import hashlib
import os
import threading
from datetime import timedelta
from io import StringIO

import pytest
from django.core.files.base import ContentFile
from django.core.management import call_command
from django.db import DatabaseError, connection, transaction
from django.utils import timezone

from app.audit.enums import ChangeEventType
from app.audit.models import ChangeEvent
from app.core.errors import DomainError
from app.documents.enums import DocumentRole, MalwareScanState
from app.documents.models import Document, DocumentVersion
from app.documents.services import (
    add_evidence_version,
    create_document,
    evidence_prefix,
    evidence_storage,
    set_legal_hold,
)
from tests import factories

pytestmark = pytest.mark.django_db

CONTENT = b"Sunteetiline toend."
PLAIN_TEXT = "text/plain"


def _stored_files(storage, prefix: str) -> list[str]:
    try:
        _directories, files = storage.listdir(prefix)
    except (FileNotFoundError, NotADirectoryError):
        return []
    return sorted(files)


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


# -- concurrency and rollback safety ----------------------------------------


def test_storage_key_is_unique_per_version(specialist):
    document = _document(created_by=specialist)
    first = add_evidence_version(
        document=document, content=b"a", original_filename="a.txt", mime_type=PLAIN_TEXT
    )
    second = add_evidence_version(
        document=document, content=b"a", original_filename="a.txt", mime_type=PLAIN_TEXT
    )
    # Identical bytes, different stored objects: the same SHA-256 does not make
    # two captures the same business occurrence.
    assert first.sha256 == second.sha256
    assert first.storage_key != second.storage_key


def test_two_versions_cannot_share_a_storage_key(specialist):
    document = _document(created_by=specialist)
    existing = add_evidence_version(
        document=document, content=b"a", original_filename="a.txt", mime_type=PLAIN_TEXT
    )
    with pytest.raises(DatabaseError), transaction.atomic():
        DocumentVersion.objects.create(
            document=document,
            version_number=99,
            storage_key=existing.storage_key,
            original_filename="a.txt",
            mime_type=PLAIN_TEXT,
            size_bytes=1,
            sha256="0" * 64,
            acquired_at=existing.acquired_at,
        )


def test_a_stored_object_is_removed_when_the_record_cannot_be_written(specialist, monkeypatch):
    """Bytes are written before the row; a failure must not leave them behind."""
    document = _document(created_by=specialist)
    storage = evidence_storage()

    def explode(*args, **kwargs):
        raise RuntimeError("database went away")

    monkeypatch.setattr(DocumentVersion.objects, "create", explode)

    with pytest.raises(RuntimeError):
        add_evidence_version(
            document=document,
            content=CONTENT,
            original_filename="kaob.txt",
            mime_type=PLAIN_TEXT,
        )

    assert document.versions.count() == 0
    assert _stored_files(storage, evidence_prefix(document)) == []


def test_a_stored_object_is_removed_when_the_pointer_update_fails(specialist, monkeypatch):
    document = _document(created_by=specialist)
    storage = evidence_storage()

    original_save = Document.save

    def explode(self, *args, **kwargs):
        raise RuntimeError("pointer update failed")

    monkeypatch.setattr(Document, "save", explode)
    with pytest.raises(RuntimeError):
        add_evidence_version(
            document=document,
            content=CONTENT,
            original_filename="kaob.txt",
            mime_type=PLAIN_TEXT,
        )
    monkeypatch.setattr(Document, "save", original_save)

    assert _stored_files(storage, evidence_prefix(document)) == []


def _age_object(storage, key: str, age: timedelta) -> None:
    """Backdate a stored object so the prune command sees it as old."""
    path = storage.path(key)
    stamp = (timezone.now() - age).timestamp()
    os.utime(path, (stamp, stamp))


def test_prune_deletes_an_old_orphan_and_keeps_referenced_objects(specialist):
    """The residual case: an outer transaction that rolls back after the call."""
    document = _document(created_by=specialist)
    kept = add_evidence_version(
        document=document, content=CONTENT, original_filename="a.txt", mime_type=PLAIN_TEXT
    )

    storage = evidence_storage()
    orphan_key = f"{evidence_prefix(document)}/9999-orphan"
    storage.save(orphan_key, ContentFile(b"orphaned bytes"))
    _age_object(storage, orphan_key, timedelta(days=3))
    # An old referenced object must survive too: age alone is not a reason.
    _age_object(storage, kept.storage_key, timedelta(days=30))

    output = StringIO()
    call_command("prune_orphaned_evidence", stdout=output)
    assert "9999-orphan" in output.getvalue()
    assert "eligible" in output.getvalue()
    assert storage.exists(orphan_key), "reporting alone must not delete"

    call_command("prune_orphaned_evidence", "--delete", stdout=StringIO())
    assert not storage.exists(orphan_key)
    assert storage.exists(kept.storage_key)


def test_prune_never_deletes_a_recently_written_object(specialist):
    """A live upload mid-transaction is indistinguishable from an orphan.

    Its row does not exist yet, so it looks unreferenced. Deleting it would
    destroy evidence a committing transaction is about to point at.
    """
    document = _document(created_by=specialist)
    storage = evidence_storage()
    in_flight_key = f"{evidence_prefix(document)}/0001-in-flight"
    storage.save(in_flight_key, ContentFile(b"still committing"))

    output = StringIO()
    call_command("prune_orphaned_evidence", "--delete", stdout=output)

    assert storage.exists(in_flight_key)
    assert "within-grace" in output.getvalue()


def test_prune_respects_an_explicit_grace_period(specialist):
    document = _document(created_by=specialist)
    storage = evidence_storage()
    key = f"{evidence_prefix(document)}/0001-two-hours-old"
    storage.save(key, ContentFile(b"orphan"))
    _age_object(storage, key, timedelta(hours=2))

    call_command("prune_orphaned_evidence", "--delete", "--grace-hours", "6", stdout=StringIO())
    assert storage.exists(key), "younger than the requested grace period"

    call_command("prune_orphaned_evidence", "--delete", "--grace-hours", "1", stdout=StringIO())
    assert not storage.exists(key)


def test_prune_leaves_an_object_alone_when_age_cannot_be_established(specialist, monkeypatch):
    """Unproven age is not a licence to delete."""
    document = _document(created_by=specialist)
    storage = evidence_storage()
    key = f"{evidence_prefix(document)}/0001-timeless"
    storage.save(key, ContentFile(b"orphan"))
    _age_object(storage, key, timedelta(days=30))

    def unsupported(self, name):
        raise NotImplementedError("this backend does not expose timestamps")

    monkeypatch.setattr(type(storage), "get_created_time", unsupported, raising=False)
    monkeypatch.setattr(type(storage), "get_modified_time", unsupported, raising=False)

    output = StringIO()
    call_command("prune_orphaned_evidence", "--delete", stdout=output)

    assert storage.exists(key)
    assert "age-unknown" in output.getvalue()


@pytest.mark.django_db(transaction=True)
def test_concurrent_writers_get_distinct_version_numbers():
    """Two simultaneous captures must not race to the same version number."""
    matter = factories.MatterFactory()
    document = create_document(matter=matter, title="Võistlev dokument")

    ready = threading.Barrier(2, timeout=20)
    failures: list[BaseException] = []

    def capture(payload: bytes) -> None:
        try:
            ready.wait()
            add_evidence_version(
                document=document,
                content=payload,
                original_filename="samaaegne.txt",
                mime_type=PLAIN_TEXT,
            )
        except BaseException as exc:
            failures.append(exc)
        finally:
            connection.close()

    threads = [
        threading.Thread(target=capture, args=(f"sisu-{index}".encode(),)) for index in range(2)
    ]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=30)

    assert failures == []
    versions = list(document.versions.order_by("version_number"))
    assert [version.version_number for version in versions] == [1, 2]
    assert len({version.storage_key for version in versions}) == 2
    assert len({version.id for version in versions}) == 2
