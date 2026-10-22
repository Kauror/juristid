"""Uploads are bounded before they are read, and never held whole in a worker.

csf_35546c06657fae7b9935969d (docs/adr/0182): every upload door read each
accepted file into memory and kept the whole batch there before any aggregate
limit was asked, so one request could put gigabytes into a gunicorn worker.
These tests hold the fix to its three parts:

* **the request boundary** — the first upload handler refuses a request whose
  declared length, file count or byte count is over the policy, and the
  middleware answers 413 before any view runs, with the temporary files gone;
* **one policy for held and new files together** — a save that also files
  what an earlier refusal held counts both (`check_upload_batch`);
* **streaming** — an accepted file is a body read in chunks from where the
  handler wrote it, hashed as it is stored, and a write that fails part-way
  leaves nothing behind.

Every file here is generated in the test and small; the limits are lowered to
meet them.
"""

from __future__ import annotations

import hashlib
import os
from pathlib import Path

import pytest
from django.core.files.uploadedfile import SimpleUploadedFile
from django.core.files.uploadhandler import (
    MemoryFileUploadHandler,
    TemporaryFileUploadHandler,
)
from django.urls import reverse

from app.documents import pending
from app.documents.models import DocumentVersion
from app.documents.services import EVIDENCE_CHANGED, add_evidence_version, evidence_storage
from app.documents.upload_body import BytesBody, StoredBody, UploadBody, store_body
from app.documents.upload_limits import FAILURE_KIND
from app.documents.uploads import (
    AcceptedUpload,
    batch_too_large_refusal,
    read_upload,
    too_many_files_refusal,
)
from app.matters.models import Matter
from tests import factories
from tests.refusals import refused

pytestmark = pytest.mark.django_db

SAABUNUD = reverse("matters:intake")
CREATE = reverse("matters:matter_create")
PDF = b"%PDF-1.4\n1 0 obj<</Type/Catalog>>endobj\ntrailer<</Root 1 0 R>>\n%%EOF\n"


def pdf(name: str, size: int = 0) -> SimpleUploadedFile:
    body = PDF + b"%" * max(0, size - len(PDF))
    return SimpleUploadedFile(name, body, content_type="application/pdf")


@pytest.fixture
def temp_uploads(settings, tmp_path) -> Path:
    """Every uploaded file goes to a temporary file in a directory we can see."""
    directory = tmp_path / "uploads"
    directory.mkdir()
    settings.FILE_UPLOAD_MAX_MEMORY_SIZE = 0
    settings.FILE_UPLOAD_TEMP_DIR = str(directory)
    return directory


@pytest.fixture
def parsed_files(monkeypatch) -> list[int]:
    """The size of every file Django's own temporary-file handler finished."""
    finished: list[int] = []
    original = TemporaryFileUploadHandler.file_complete

    def file_complete(self, file_size):
        finished.append(file_size)
        return original(self, file_size)

    monkeypatch.setattr(TemporaryFileUploadHandler, "file_complete", file_complete)
    return finished


# ---------------------------------------------------------------------------
# The request boundary
# ---------------------------------------------------------------------------


def test_a_request_declaring_more_than_the_policy_is_refused_before_its_body_is_read(
    signed_in, settings, monkeypatch
):
    settings.UPLOAD_MAX_REQUEST_BYTES = 8 * 1024

    def must_not_parse(*args, **kwargs):
        raise AssertionError("a file part was handed to Django's handlers")

    monkeypatch.setattr(MemoryFileUploadHandler, "new_file", must_not_parse)
    monkeypatch.setattr(TemporaryFileUploadHandler, "new_file", must_not_parse)

    response = signed_in.post(
        SAABUNUD, {"title": "Liiga suur pakk", "uploads": pdf("suur.pdf", 20 * 1024)}
    )

    assert response.status_code == 413
    assert batch_too_large_refusal() in response.content.decode()
    assert not Matter.objects.filter(title="Liiga suur pakk").exists()


def test_more_files_than_the_policy_stops_the_parse_and_leaves_no_temporary_file(
    signed_in, settings, temp_uploads, parsed_files
):
    settings.UPLOAD_MAX_FILES_PER_REQUEST = 2

    response = signed_in.post(
        SAABUNUD,
        {"title": "Liiga palju", "uploads": [pdf(f"{n}.pdf", 2048) for n in range(5)]},
    )

    assert response.status_code == 413
    assert too_many_files_refusal() in response.content.decode()
    assert not Matter.objects.filter(title="Liiga palju").exists()
    # The parse stopped at the file past the limit, and what it had written is gone.
    assert len(parsed_files) <= 2
    assert os.listdir(temp_uploads) == []


def test_an_htmx_upload_over_the_policy_gets_the_sentence_and_its_failure_kind(signed_in, settings):
    settings.UPLOAD_MAX_FILES_PER_REQUEST = 1

    response = signed_in.post(
        reverse("matters:intake_stage"),
        {"files": [pdf("a.pdf"), pdf("b.pdf")]},
        HTTP_HX_REQUEST="true",
    )

    assert response.status_code == 413
    assert response["X-Juristid-Failure"] == FAILURE_KIND
    assert response.content.decode() == too_many_files_refusal()


def test_a_request_within_the_policy_is_untouched(signed_in, settings, temp_uploads):
    settings.UPLOAD_MAX_FILES_PER_REQUEST = 3
    settings.UPLOAD_MAX_REQUEST_BYTES = 64 * 1024

    response = signed_in.post(
        SAABUNUD,
        {"title": "Paras pakk", "uploads": [pdf(f"{n}.pdf", 4096) for n in range(3)]},
    )

    assert response.status_code == 302, response.status_code
    matter = Matter.objects.get(title="Paras pakk")
    assert DocumentVersion.objects.filter(document__matter=matter).count() == 3


def test_an_oversized_file_is_not_stored_past_the_ceiling_and_is_refused_by_name(
    signed_in, settings, temp_uploads, parsed_files
):
    """The per-file sentence is unchanged; the bytes past it never reach disk."""
    settings.MAX_EVIDENCE_UPLOAD_BYTES = 1024 * 1024

    response = signed_in.post(
        SAABUNUD,
        {"title": "Üks liiga suur", "uploads": [pdf("hea.pdf"), pdf("suur.pdf", 4 * 1024 * 1024)]},
    )

    assert response.status_code == 400
    assert "Fail on suurem kui lubatud 1 MB." in response.content.decode()
    assert not Matter.objects.filter(title="Üks liiga suur").exists()
    # One 64 KiB parser chunk past the ceiling at most, not two megabytes.
    assert max(parsed_files) <= settings.MAX_EVIDENCE_UPLOAD_BYTES + 64 * 1024


# ---------------------------------------------------------------------------
# One policy for held and new files together
# ---------------------------------------------------------------------------


def _held_after_a_refusal(client, title: str, files: list[SimpleUploadedFile]) -> list[str]:
    refused_save = client.post(
        CREATE, {"title": title, "policy_area_other_selected": "on", "files": files}
    )
    assert refused_save.status_code == 400
    keys = [item.key for item in refused_save.context["held_files"]]
    assert len(keys) == len(files)
    return keys


def test_held_bytes_and_new_bytes_are_counted_together(signed_in, settings):
    keys = _held_after_a_refusal(signed_in, "Koos liiga suur", [pdf("hoitud.pdf", 6000)])
    settings.UPLOAD_MAX_REQUEST_BYTES = 9000

    response = signed_in.post(
        CREATE,
        {"title": "Koos liiga suur", "pending": keys, "files": pdf("uus.pdf", 4000)},
    )

    assert response.status_code == 400
    assert batch_too_large_refusal() in response.content.decode()
    assert not Matter.objects.filter(title="Koos liiga suur").exists()


def test_held_files_and_new_files_are_counted_together(signed_in, settings):
    keys = _held_after_a_refusal(signed_in, "Koos liiga palju", [pdf("a.pdf"), pdf("b.pdf")])
    settings.UPLOAD_MAX_FILES_PER_REQUEST = 2

    response = signed_in.post(
        CREATE, {"title": "Koos liiga palju", "pending": keys, "files": pdf("c.pdf")}
    )

    assert response.status_code == 400
    assert too_many_files_refusal() in response.content.decode()
    assert not Matter.objects.filter(title="Koos liiga palju").exists()


def test_a_resumed_held_file_is_not_read_until_it_is_written(client, monkeypatch, evidence_root):
    session = client.session
    [item] = pending.hold(
        session,
        [AcceptedUpload(content=PDF, filename="a.pdf", mime_type="application/pdf")],
    )
    storage = pending.pending_storage()

    def must_not_open(*args, **kwargs):
        raise AssertionError("resume read the held bytes")

    monkeypatch.setattr(storage, "open", must_not_open)
    [resumed] = pending.resume(session, [item.key])

    assert isinstance(resumed.body, StoredBody)
    assert resumed.size == len(PDF)


# ---------------------------------------------------------------------------
# Streaming
# ---------------------------------------------------------------------------


class NoWholeRead(SimpleUploadedFile):
    """An upload that refuses to be read whole, the way the old door read it."""

    def read(self, size=-1):
        if size is None or size < 0:
            raise AssertionError("the whole file was read into memory")
        return self.file.read(size)


def test_an_accepted_upload_is_validated_and_stored_without_being_read_whole(evidence_root):
    content = PDF + os.urandom(300_000)
    accepted = read_upload(NoWholeRead("kiri.pdf", content, content_type="application/pdf"))
    document = factories.DocumentFactory()

    version = add_evidence_version(
        document=document,
        content=accepted.body,
        original_filename=accepted.filename,
        mime_type=accepted.mime_type,
    )

    assert version.size_bytes == len(content)
    assert version.sha256 == hashlib.sha256(content).hexdigest()
    with evidence_storage().open(version.storage_key, "rb") as handle:
        assert handle.read() == content


class BreaksHalfway(UploadBody):
    """A body whose source fails after its first chunk."""

    def __init__(self) -> None:
        self.size = 1024 * 1024

    def chunks(self):
        yield b"%PDF-" + b"x" * 1000
        raise OSError("source went away")


def test_a_write_that_fails_part_way_leaves_no_object_behind(evidence_root, tmp_path):
    from django.core.files.storage import FileSystemStorage

    storage = FileSystemStorage(location=str(tmp_path / "store"))
    with pytest.raises(OSError, match="source went away"):
        store_body(storage, "a/b", BreaksHalfway())
    assert not storage.exists("a/b")


def test_a_body_shorter_than_it_claims_is_refused_and_removed(tmp_path):
    from django.core.files.storage import FileSystemStorage

    from app.documents.upload_body import BODY_CHANGED

    storage = FileSystemStorage(location=str(tmp_path / "store"))
    body = BytesBody(b"%PDF-1.4 lyhike")
    body.size = 10_000
    with refused(BODY_CHANGED):
        store_body(storage, "lyhike", body)
    assert not storage.exists("lyhike")


def test_an_evidence_write_that_does_not_match_its_recorded_checksum_is_refused(evidence_root):
    document = factories.DocumentFactory()
    before = set(_stored_files(evidence_root))

    with refused(EVIDENCE_CHANGED.format(name="kiri.pdf")):
        add_evidence_version(
            document=document,
            content=BytesBody(PDF),
            original_filename="kiri.pdf",
            mime_type="application/pdf",
            expected_sha256=hashlib.sha256(b"something else").hexdigest(),
        )

    assert set(_stored_files(evidence_root)) == before
    assert not DocumentVersion.objects.filter(document=document).exists()


def _stored_files(root) -> list[str]:
    return [str(path) for path in Path(root).rglob("*") if path.is_file()]


# ---------------------------------------------------------------------------
# The boundary is where the settings say it is
# ---------------------------------------------------------------------------


def test_the_policy_handler_is_first_and_its_middleware_parses_first(settings):
    assert settings.FILE_UPLOAD_HANDLERS[0] == "app.documents.upload_limits.UploadLimitHandler"
    middleware = list(settings.MIDDLEWARE)
    ours = middleware.index("app.documents.upload_limits.UploadLimitMiddleware")
    # Before the first middleware that reads a form field in __call__.
    assert ours < middleware.index("app.core.middleware.RefuseNulMiddleware")
    assert ours < middleware.index("app.core.middleware.UploadTitlesMiddleware")
