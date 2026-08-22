"""What survives when two things go wrong at once.

`test_documents.py` asserts that evidence capture is correct and
`test_extraction_worker.py` asserts that one parse reaches one honest state.
This file is about the seams between them — the places where a second actor, a
second process, or a second failure arrives while the first is still running,
and where the answer used to depend on timing:

* a worker that finishes a parse after its claim has been reclaimed;
* an OCR subprocess that never returns;
* a message nested inside a message inside a message;
* a rebuild whose parse fails after the previous representation was deleted;
* an orphan pruner whose deletion, or whose listing, does not work;
* a download of a version whose bytes are no longer there.

Every one of them is written as "the bad thing happens, and here is what must
still be true afterwards", because the failure mode they share is that nothing
errors — the state is simply wrong, and stays wrong quietly.
"""

from __future__ import annotations

import hashlib
import os
from datetime import timedelta
from io import StringIO

import pytest
from django.core.files.base import ContentFile
from django.core.management import call_command
from django.core.management.base import CommandError
from django.urls import reverse
from django.utils import timezone

from app.audit.enums import SecurityEventType
from app.audit.models import SecurityAuditEvent
from app.documents.derivatives import EmailAttachmentLink
from app.documents.enums import (
    DerivativeKind,
    DerivativeStatus,
    DocumentRole,
    ExtractionState,
)
from app.documents.extraction.base import ParsedAttachment
from app.documents.extraction.errors import ExtractionFailed
from app.documents.extraction.orchestrator import (
    CLAIM_LOST,
    claim_version,
    extract_document_version,
)
from app.documents.integrity import (
    FOREIGN_CURRENT_VERSION,
    MISSING_OBJECT,
    ORPHAN_OBJECT,
    SHA_MISMATCH,
    SIZE_MISMATCH,
    STUCK_PROCESSING,
    check_evidence,
)
from app.documents.models import Document, DocumentDerivative, DocumentVersion
from app.documents.services import evidence_prefix, evidence_storage
from tests import synthetic_corpus as corpus

pytestmark = pytest.mark.django_db

PDF = "application/pdf"
EML = "message/rfc822"


@pytest.fixture
def pdf_version(normal_matter, capture_evidence):
    return capture_evidence(normal_matter, corpus.government_pdf(), "kaaskiri.pdf", PDF)


def _steal_the_claim(version, settings) -> DocumentVersion:
    """Age the current claim past the window, then take it as a second worker.

    Exactly what a healthy second worker does to a row whose owner is still
    parsing: there is no OCR job in a test that runs for half an hour, so the
    elapsed time is arranged rather than waited for. Nothing else about the
    situation is simulated — the reclaim goes through the real `claim_version`.
    """
    settings.EXTRACTION_STALE_CLAIM_MINUTES = 30
    DocumentVersion.objects.filter(pk=version.pk).update(
        extraction_claimed_at=timezone.now() - timedelta(minutes=31)
    )
    stolen = claim_version(version.pk)
    assert stolen is not None, "the second worker should have been able to reclaim it"
    return stolen


# --------------------------------------------------------------------------
# A claim that was reclaimed mid-parse
#
# `EXTRACTION_STALE_CLAIM_MINUTES` is 30 and nothing enforces a parse ceiling,
# so a 400-page scan being OCR'd page by page can legitimately outlive its own
# claim. The second worker is not misbehaving; it is doing exactly what the
# stale-claim rule tells it to. What must not happen is both of them writing.
# --------------------------------------------------------------------------


def test_a_pass_that_lost_its_claim_publishes_nothing(pdf_version, settings) -> None:
    slow_worker = claim_version(pdf_version.pk)
    assert slow_worker is not None
    _steal_the_claim(pdf_version, settings)

    report = extract_document_version(slow_worker)

    assert report.state == CLAIM_LOST
    assert report.error_code == "claim_lost"
    assert not DocumentDerivative.objects.filter(version=pdf_version).exists()


def test_a_lost_claim_leaves_the_row_to_the_worker_that_owns_it(pdf_version, settings) -> None:
    """The state must stay the second worker's, not be reset by the first."""
    slow_worker = claim_version(pdf_version.pk)
    assert slow_worker is not None
    stolen = _steal_the_claim(pdf_version, settings)

    extract_document_version(slow_worker)

    pdf_version.refresh_from_db()
    assert pdf_version.extraction_state == ExtractionState.PROCESSING
    assert pdf_version.extraction_claimed_at == stolen.extraction_claimed_at


def test_a_lost_claim_does_not_record_a_failure_on_a_file_that_is_fine(
    normal_matter, capture_evidence, settings
) -> None:
    """The failure path is fenced too, and it is the one that misleads.

    A pass that lost its claim and then hit a parse error would otherwise write
    FAILED over a row a second worker is extracting successfully, leaving an
    operator a failure to investigate that never happened.
    """
    version = capture_evidence(normal_matter, corpus.corrupt_pdf(), "katkine.pdf", PDF)
    slow_worker = claim_version(version.pk)
    assert slow_worker is not None
    _steal_the_claim(version, settings)

    report = extract_document_version(slow_worker)

    version.refresh_from_db()
    assert report.state == CLAIM_LOST
    assert version.extraction_state == ExtractionState.PROCESSING
    assert not DocumentDerivative.objects.filter(
        version=version, status=DerivativeStatus.FAILED
    ).exists()


def test_a_lost_claim_creates_no_duplicate_attachment_documents(
    normal_matter, capture_evidence, settings
) -> None:
    """The expensive half. Attachments are Documents with evidence of their own.

    Two passes publishing the same message would each try to create them, and
    the loser of that race leaves stored bytes behind with no row — which is the
    orphan direction, but only because the unique ordinal happens to catch it.
    Not publishing at all is the answer that does not rely on a constraint.
    """
    version = capture_evidence(normal_matter, corpus.consultation_eml(), "kiri.eml", EML)
    before = DocumentVersion.objects.count()
    slow_worker = claim_version(version.pk)
    assert slow_worker is not None
    _steal_the_claim(version, settings)

    report = extract_document_version(slow_worker)

    assert report.state == CLAIM_LOST
    assert DocumentVersion.objects.count() == before
    assert not EmailAttachmentLink.objects.filter(parent_version=version).exists()


def test_an_unstolen_claim_still_publishes_normally(pdf_version) -> None:
    """The fence must not cost the ordinary case anything."""
    claimed = claim_version(pdf_version.pk)
    assert claimed is not None

    report = extract_document_version(claimed)

    pdf_version.refresh_from_db()
    assert report.state == ExtractionState.DONE
    assert pdf_version.extraction_state == ExtractionState.DONE
    assert pdf_version.extraction_claimed_at is None


# --------------------------------------------------------------------------
# OCR that never returns
# --------------------------------------------------------------------------


def _fake_engine(monkeypatch):
    from app.documents.extraction import ocr as ocr_module

    monkeypatch.setattr(ocr_module, "ocr_engine_version", lambda: "tesseract 5.3.0 (test)")


def test_ocr_is_given_a_deadline(monkeypatch, settings) -> None:
    """Tesseract is a subprocess, and a synchronous parse waits for it forever."""
    settings.EXTRACTION_OCR_TIMEOUT_SECONDS = 42
    _fake_engine(monkeypatch)
    seen: dict[str, object] = {}

    import pytesseract

    def record(image, **kwargs):
        seen.update(kwargs)
        return "loetud tekst"

    monkeypatch.setattr(pytesseract, "image_to_string", record)

    from app.documents.extraction.ocr import recognise_pil_image

    assert recognise_pil_image(object()) == "loetud tekst"
    assert seen["timeout"] == 42


def test_an_ocr_timeout_is_a_named_failure_not_a_wedged_worker(monkeypatch) -> None:
    _fake_engine(monkeypatch)

    import pytesseract

    def hang(image, **kwargs):
        raise RuntimeError("Tesseract process timeout")

    monkeypatch.setattr(pytesseract, "image_to_string", hang)

    from app.documents.extraction.ocr import recognise_pil_image

    with pytest.raises(ExtractionFailed) as raised:
        recognise_pil_image(object())
    assert raised.value.code == "ocr_timeout"


def test_an_unrelated_runtime_error_is_not_reported_as_a_timeout(monkeypatch) -> None:
    """Matching on a message is loose, so it must be loose in the safe direction."""
    _fake_engine(monkeypatch)

    import pytesseract

    def explode(image, **kwargs):
        raise RuntimeError("something else entirely")

    monkeypatch.setattr(pytesseract, "image_to_string", explode)

    from app.documents.extraction.ocr import recognise_pil_image

    with pytest.raises(RuntimeError, match="something else"):
        recognise_pil_image(object())


# --------------------------------------------------------------------------
# A message inside a message inside a message
#
# The parser's own depth limit counts nesting *within one parse*, and a nested
# message is deliberately never expanded within one parse — it is preserved
# whole as its own Document, which the worker then extracts on a later pass that
# starts counting from zero again. So the limit never bound anything, and a file
# of nothing but envelopes could manufacture a Document per level.
# --------------------------------------------------------------------------


def _nest(capture_evidence, matter, depth: int) -> DocumentVersion:
    """A chain of `depth` attachment links ending in one message version."""
    current = capture_evidence(matter, corpus.consultation_eml(), "kiri.eml", EML)
    for level in range(depth):
        child = capture_evidence(matter, corpus.consultation_eml(), f"sees-{level}.eml", EML)
        EmailAttachmentLink.objects.create(
            parent_version=current,
            attachment_version=child,
            ordinal=1,
            declared_filename=f"sees-{level}.eml",
        )
        current = child
    return current


def test_a_nested_message_chain_stops_at_the_configured_depth(
    normal_matter, capture_evidence, settings
) -> None:
    from app.documents.email_intake import register_email_attachments

    settings.EXTRACTION_MAX_EMAIL_DEPTH = 2
    deepest = _nest(capture_evidence, normal_matter, depth=2)

    created = register_email_attachments(
        parent_version=deepest,
        attachments=(
            ParsedAttachment(content=b"nested message", filename="veel.eml", mime_type=EML),
        ),
    )

    assert created == 0
    assert not EmailAttachmentLink.objects.filter(parent_version=deepest).exists()


def test_the_depth_limit_refuses_messages_and_not_the_annexes(
    normal_matter, capture_evidence, settings
) -> None:
    """A PDF three envelopes deep is still somebody's annex.

    It cannot extend the chain — nothing opens a PDF looking for more messages —
    so refusing it would cost real evidence for no bound at all.
    """
    from app.documents.email_intake import register_email_attachments

    settings.EXTRACTION_MAX_EMAIL_DEPTH = 2
    deepest = _nest(capture_evidence, normal_matter, depth=2)

    created = register_email_attachments(
        parent_version=deepest,
        attachments=(
            ParsedAttachment(content=b"nested message", filename="veel.eml", mime_type=EML),
            ParsedAttachment(content=corpus.government_pdf(), filename="lisa.pdf", mime_type=PDF),
        ),
    )

    assert created == 1
    stored = EmailAttachmentLink.objects.get(parent_version=deepest)
    assert stored.declared_filename == "lisa.pdf"


def test_a_message_below_the_depth_limit_is_still_stored(
    normal_matter, capture_evidence, settings
) -> None:
    from app.documents.email_intake import register_email_attachments

    settings.EXTRACTION_MAX_EMAIL_DEPTH = 3
    inner = _nest(capture_evidence, normal_matter, depth=1)

    created = register_email_attachments(
        parent_version=inner,
        attachments=(
            ParsedAttachment(content=b"nested message", filename="veel.eml", mime_type=EML),
        ),
    )

    assert created == 1


# --------------------------------------------------------------------------
# Rebuilding derived content
# --------------------------------------------------------------------------


def test_a_rebuild_whose_parse_fails_keeps_the_previous_representation(
    pdf_version, extract, monkeypatch
) -> None:
    """The command used to delete first, which made a parser regression permanent.

    On `--all` that is the whole archive's searchable text, removed by a run
    that reports how many versions it processed and nothing about what it lost.
    """
    extract(pdf_version)
    good = DocumentDerivative.objects.get(
        version=pdf_version, kind=DerivativeKind.EXTRACTED_TEXT, status=DerivativeStatus.ACTIVE
    )
    fragments_before = good.fragments.count()

    from app.documents.extraction import pdf as pdf_module

    def explode(self, source):
        raise RuntimeError("the new parser is broken")

    monkeypatch.setattr(pdf_module.PdfParser, "parse", explode)
    output = StringIO()
    call_command("rebuild_document_derivatives", "--version-id", str(pdf_version.pk), stdout=output)

    good.refresh_from_db()
    assert good.status == DerivativeStatus.ACTIVE
    assert good.fragments.count() == fragments_before
    assert "jäi kehtima varasem" in output.getvalue()


def test_a_successful_rebuild_leaves_exactly_one_derivative_per_kind(pdf_version, extract) -> None:
    """Extract-then-drop has to reach the same end state destroy-first did."""
    extract(pdf_version)
    call_command(
        "rebuild_document_derivatives", "--version-id", str(pdf_version.pk), stdout=StringIO()
    )

    rows = DocumentDerivative.objects.filter(version=pdf_version)
    assert set(rows.values_list("status", flat=True)) == {DerivativeStatus.ACTIVE}
    assert rows.filter(kind=DerivativeKind.EXTRACTED_TEXT).count() == 1


# --------------------------------------------------------------------------
# The orphan pruner
# --------------------------------------------------------------------------


def _orphan(document, name: str = "0009-orphan") -> str:
    """An unreferenced stored object, backdated past any plausible grace period."""
    storage = evidence_storage()
    key = storage.save(f"{evidence_prefix(document)}/{name}", ContentFile(b"orphaned bytes"))
    stamp = (timezone.now() - timedelta(days=3)).timestamp()
    os.utime(storage.path(key), (stamp, stamp))
    return key


def test_pruning_refuses_to_delete_without_a_grace_period(normal_matter, capture_evidence) -> None:
    """The grace period is the only protection there is, so zero cannot be a flag.

    Reporting under a zero window stays available; it is deleting under one that
    reinstates the race between an uncommitted upload and this command.
    """
    version = capture_evidence(normal_matter, b"%PDF-1.4 evidence", "a.pdf", PDF)
    _orphan(version.document)

    with pytest.raises(CommandError, match="grace period of at least"):
        call_command("prune_orphaned_evidence", "--delete", "--grace-hours", "0", stdout=StringIO())


def test_a_deletion_that_failed_is_not_reported_as_pruned(
    normal_matter, capture_evidence, monkeypatch
) -> None:
    version = capture_evidence(normal_matter, b"%PDF-1.4 evidence", "a.pdf", PDF)
    stubborn = _orphan(version.document, "0009-stubborn")
    removable = _orphan(version.document, "0010-removable")

    storage = evidence_storage()
    original = type(storage).delete

    def refuse(self, name):
        if name == stubborn:
            raise OSError(13, "Permission denied")
        return original(self, name)

    monkeypatch.setattr(type(storage), "delete", refuse)

    output = StringIO()
    with pytest.raises(CommandError, match="could not be deleted"):
        call_command("prune_orphaned_evidence", "--delete", stdout=output, stderr=StringIO())

    assert "Deleted 1 orphaned object(s)." in output.getvalue()
    assert storage.exists(stubborn)
    assert not storage.exists(removable), "one refusal must not abandon the rest of the run"


def test_a_listing_that_failed_is_not_reported_as_an_empty_store(monkeypatch) -> None:
    """The most misleading sentence this command could print.

    "No unreferenced evidence objects found" is what an operator reads as "the
    evidence store is healthy", and an unreadable root used to produce it.
    """
    storage = evidence_storage()

    def refuse(self, name):
        raise PermissionError(13, "Permission denied")

    monkeypatch.setattr(type(storage), "listdir", refuse)

    output = StringIO()
    with pytest.raises(CommandError, match="could not be listed"):
        call_command("prune_orphaned_evidence", stdout=output, stderr=StringIO())
    assert "No unreferenced evidence objects found." not in output.getvalue()


# --------------------------------------------------------------------------
# The integrity checker
# --------------------------------------------------------------------------


def test_a_healthy_store_reports_nothing(normal_matter, capture_evidence) -> None:
    capture_evidence(normal_matter, b"%PDF-1.4 evidence", "a.pdf", PDF)

    report = check_evidence()

    assert report.ok
    assert report.versions_checked == 1
    assert report.bytes_hashed == 0, "the structural pass must not read the objects"


def test_a_row_whose_bytes_are_gone_is_found(normal_matter, capture_evidence) -> None:
    """The one failure the schema is structurally unable to see."""
    version = capture_evidence(normal_matter, b"%PDF-1.4 evidence", "a.pdf", PDF)
    evidence_storage().delete(version.storage_key)

    report = check_evidence()

    assert [f.kind for f in report.findings] == [MISSING_OBJECT]
    assert report.findings[0].subject == str(version.pk)


def test_a_truncated_object_is_found_without_hashing(normal_matter, capture_evidence) -> None:
    version = capture_evidence(normal_matter, b"%PDF-1.4 evidence bytes", "a.pdf", PDF)
    storage = evidence_storage()
    with open(storage.path(version.storage_key), "wb") as handle:
        handle.write(b"%PDF")

    report = check_evidence()

    assert SIZE_MISMATCH in {f.kind for f in report.findings}


def test_changed_bytes_of_the_same_length_need_the_deep_pass(
    normal_matter, capture_evidence
) -> None:
    """Which is exactly why the deep pass exists, and why it is not the default."""
    original = b"%PDF-1.4 evidence bytes"
    version = capture_evidence(normal_matter, original, "a.pdf", PDF)
    storage = evidence_storage()
    tampered = b"%PDF-1.4 EVIDENCE bytes"
    assert len(tampered) == len(original)
    with open(storage.path(version.storage_key), "wb") as handle:
        handle.write(tampered)

    assert check_evidence().ok, "same size, so nothing structural is wrong"

    deep = check_evidence(verify_sha=True)
    assert [f.kind for f in deep.findings] == [SHA_MISMATCH]
    assert deep.bytes_hashed == len(tampered)
    # The recorded digest is not echoed back: printing it invites somebody to
    # "correct" the row to match the bytes, which is the one repair that turns a
    # detected corruption into an undetectable one.
    assert hashlib.sha256(original).hexdigest() not in deep.findings[0].detail


def test_an_unreferenced_object_is_reported_but_never_removed(
    normal_matter, capture_evidence
) -> None:
    version = capture_evidence(normal_matter, b"%PDF-1.4 evidence", "a.pdf", PDF)
    key = _orphan(version.document)

    report = check_evidence()

    assert [f.subject for f in report.findings if f.kind == ORPHAN_OBJECT] == [key]
    assert evidence_storage().exists(key), "the checker reads and does not repair"


def test_a_current_version_belonging_to_another_document_is_found(
    normal_matter, capture_evidence
) -> None:
    """Nothing in the schema forbids it; only `add_evidence_version` does."""
    mine = capture_evidence(normal_matter, b"%PDF-1.4 mine", "a.pdf", PDF)
    theirs = capture_evidence(normal_matter, b"%PDF-1.4 theirs", "b.pdf", PDF)
    Document.objects.filter(pk=mine.document_id).update(current_version=theirs)

    report = check_evidence()

    assert [f.kind for f in report.findings] == [FOREIGN_CURRENT_VERSION]
    assert report.findings[0].subject == str(mine.document_id)


def test_a_claim_that_never_finished_is_countable(
    normal_matter, capture_evidence, settings
) -> None:
    settings.EXTRACTION_STALE_CLAIM_MINUTES = 30
    version = capture_evidence(normal_matter, b"%PDF-1.4 evidence", "a.pdf", PDF)
    DocumentVersion.objects.filter(pk=version.pk).update(
        extraction_state=ExtractionState.PROCESSING,
        extraction_claimed_at=timezone.now() - timedelta(hours=4),
    )

    report = check_evidence()

    assert [f.kind for f in report.findings] == [STUCK_PROCESSING]


def test_the_command_exits_non_zero_when_evidence_is_missing(
    normal_matter, capture_evidence
) -> None:
    version = capture_evidence(normal_matter, b"%PDF-1.4 evidence", "a.pdf", PDF)
    evidence_storage().delete(version.storage_key)

    with pytest.raises(SystemExit) as raised:
        call_command("check_evidence_integrity", stdout=StringIO())
    assert raised.value.code == 1


def test_the_command_says_nothing_confidential(normal_matter, capture_evidence) -> None:
    """This output goes to cron mail. In this corpus the filename is the secret."""
    version = capture_evidence(
        normal_matter,
        b"%PDF-1.4 evidence",
        "Liikme konfidentsiaalne kaebus.pdf",
        PDF,
        title="Liikme konfidentsiaalne kaebus",
    )
    evidence_storage().delete(version.storage_key)

    output = StringIO()
    with pytest.raises(SystemExit):
        call_command("check_evidence_integrity", stdout=output)

    assert "konfidentsiaalne" not in output.getvalue()
    assert str(version.pk) in output.getvalue()


def test_a_clean_store_exits_zero(normal_matter, capture_evidence) -> None:
    capture_evidence(normal_matter, b"%PDF-1.4 evidence", "a.pdf", PDF)

    output = StringIO()
    call_command("check_evidence_integrity", stdout=output)

    assert "No integrity problems found." in output.getvalue()


# --------------------------------------------------------------------------
# Downloading evidence that is no longer there
# --------------------------------------------------------------------------


def test_a_missing_object_does_not_produce_a_download_record(
    client, specialist, normal_matter, capture_evidence
) -> None:
    """A DOCUMENT_DOWNLOADED row is a claim that somebody received a file.

    Written before the object was opened, it recorded deliveries that never
    happened — on top of handing the reader a traceback.
    """
    version = capture_evidence(
        normal_matter, b"%PDF-1.4 evidence", "a.pdf", PDF, role=DocumentRole.INCOMING_AUTHORITY
    )
    evidence_storage().delete(version.storage_key)
    client.force_login(specialist)

    response = client.get(reverse("documents:download", kwargs={"pk": version.pk}))

    assert response.status_code == 404
    events = SecurityAuditEvent.objects.filter(
        event_type=SecurityEventType.DOCUMENT_DOWNLOADED, subject_id=version.pk
    )
    assert events.count() == 1
    assert events.get().succeeded is False


def test_a_present_object_still_downloads_and_is_recorded(
    client, specialist, normal_matter, capture_evidence
) -> None:
    """The fence around the audit row must not have moved the ordinary case."""
    version = capture_evidence(normal_matter, b"%PDF-1.4 evidence", "a.pdf", PDF)
    client.force_login(specialist)

    response = client.get(reverse("documents:download", kwargs={"pk": version.pk}))

    assert response.status_code == 200
    assert SecurityAuditEvent.objects.get(subject_id=version.pk).succeeded is True
    response.close()
