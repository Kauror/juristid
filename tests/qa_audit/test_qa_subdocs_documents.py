"""Independent QA 2026-10-14 — subdocs area: documents (ADR 0003, 0014, 0120, 0125, 0137).

Uploads of synthetic tiny files through the real views, the direct-file routes
under every persona, removal and versions. Probes against main 81a7d6e4.
"""

from __future__ import annotations

import io
import uuid
import zipfile

import pytest
from django.core.files.uploadedfile import SimpleUploadedFile
from django.urls import reverse

from app.core.enums import Visibility
from app.documents.enums import DocumentRole, ExtractionState
from app.documents.models import Document, DocumentVersion
from app.matters.services import create_matter
from app.workflow.models import StageVocabulary
from tests.follow_ups import PDF

pytestmark = pytest.mark.django_db


def _matter(owner, **extra):
    return create_matter(
        title="QA dokumendid",
        actor=owner,
        owner=owner,
        stage=StageVocabulary.objects.get(key="consultation"),
        **extra,
    )


def _upload(client, matter, name, content, role=DocumentRole.INCOMING_AUTHORITY, title=""):
    return client.post(
        reverse("documents:upload_evidence", kwargs={"matter_id": matter.pk}),
        {
            "title": title,
            "role": role,
            "upload": SimpleUploadedFile(name, content, content_type="application/octet-stream"),
        },
    )


def _docx_bytes() -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        archive.writestr("[Content_Types].xml", "<Types/>")
        archive.writestr("word/document.xml", "<w:document/>")
    return buffer.getvalue()


# ---------------------------------------------------------------------------
# Supported, unsupported, malformed
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "name,content",
    [
        ("kiri.pdf", PDF),
        ("pilt.png", b"\x89PNG\r\n\x1a\n" + b"\x00" * 16),
        ("markus.txt", "Õiguskantsleri märgukiri šokolaadi ja žürii kohta".encode()),
        ("eelnou.docx", None),
    ],
)
def test_supported_tiny_files_are_stored_byte_exact(signed_in, specialist, name, content):
    matter = _matter(specialist)
    content = content if content is not None else _docx_bytes()

    response = _upload(signed_in, matter, name, content)

    assert response.status_code == 302
    document = Document.objects.get(matter=matter)
    version = document.current_version
    assert version.original_filename == name
    assert version.size_bytes == len(content)
    download = signed_in.get(reverse("documents:download", kwargs={"pk": version.pk}))
    assert download.status_code == 200
    assert b"".join(download.streaming_content) == content


@pytest.mark.parametrize(
    "name,content",
    [
        ("tuhi.pdf", b""),
        ("vale.pdf", b"MZ\x90\x00 not a pdf"),
        ("programm.exe", b"MZ\x90\x00"),
        ("ilma_laiendita", PDF),
        ("skript.html", b"<script>alert(1)</script>"),
        ("vale.docx", b"%PDF-1.4 renamed"),
        ("vale.asice", _docx_bytes()),
        ("pilt.png.exe", b"\x89PNG\r\n\x1a\n"),
    ],
    # The zip bytes carry a write timestamp, so a content-derived id changes on
    # every collection and breaks CI's shard-completeness check.
    ids=lambda value: value if isinstance(value, str) else "content",
)
def test_unsupported_or_malformed_files_are_refused_and_store_nothing(
    signed_in, specialist, name, content
):
    matter = _matter(specialist)

    _upload(signed_in, matter, name, content)

    assert not Document.objects.filter(matter=matter).exists()
    assert not DocumentVersion.objects.filter(document__matter=matter).exists()


def test_duplicate_upload_of_the_same_bytes_is_not_silently_merged(signed_in, specialist):
    """Two uploads of identical bytes: each a file, each its own provenance row."""
    matter = _matter(specialist)

    _upload(signed_in, matter, "a.pdf", PDF + b"same", title="Esimene")
    _upload(signed_in, matter, "b.pdf", PDF + b"same", title="Teine")

    documents = Document.objects.filter(matter=matter)
    assert documents.count() == 2
    names = set(
        DocumentVersion.objects.filter(document__matter=matter).values_list(
            "original_filename", flat=True
        )
    )
    assert names == {"a.pdf", "b.pdf"}


def test_reader_and_administrator_cannot_upload(client, reader, administrator, specialist):
    matter = _matter(specialist)
    for persona in (reader, administrator):
        client.force_login(persona)
        response = _upload(client, matter, "kiri.pdf", PDF)
        assert response.status_code == 404
    assert not Document.objects.filter(matter=matter).exists()


def test_upload_to_a_closed_matter_is_refused(signed_in, specialist):
    matter = _matter(specialist)
    from app.matters.services import close_matter
    from app.workflow.enums import Disposition

    close_matter(matter=matter, disposition=Disposition.COMPLETED, actor=specialist, reason="QA")
    _upload(signed_in, matter, "kiri.pdf", PDF)

    assert not Document.objects.filter(matter=matter).exists()


# ---------------------------------------------------------------------------
# Direct file URLs: other persona, restricted record, guessed ids
# ---------------------------------------------------------------------------


@pytest.fixture
def restricted_file(specialist, capture_evidence):
    matter = _matter(specialist, visibility=Visibility.RESTRICTED)
    return capture_evidence(matter, PDF + b"secret", "salajane.pdf", "application/pdf")


@pytest.fixture
def restricted_child_file(specialist, capture_evidence):
    matter = _matter(specialist)
    version = capture_evidence(matter, PDF + b"child", "piiratud.pdf", "application/pdf")
    Document.objects.filter(pk=version.document_id).update(
        visibility_override=Visibility.RESTRICTED
    )
    return version


def _routes(version):
    return [
        reverse("documents:download", kwargs={"pk": version.pk}),
        reverse("documents:open", kwargs={"pk": version.pk}),
        reverse("documents:document_detail", kwargs={"pk": version.document_id}),
    ]


@pytest.mark.parametrize("fixture_name", ["restricted_file", "restricted_child_file"])
def test_restricted_file_routes_are_404_for_reader_and_admin(
    request, client, reader, administrator, superuser, fixture_name
):
    version = request.getfixturevalue(fixture_name)
    for persona in (reader, administrator, superuser):
        client.force_login(persona)
        for url in _routes(version):
            assert client.get(url).status_code == 404, (persona.role, url)


def _body(response) -> bytes:
    if getattr(response, "streaming", False):
        return b"".join(response.streaming_content)
    return response.content


def test_restricted_file_routes_answer_the_owner(signed_in, restricted_file):
    """The owner gets the file itself on each route — not a redirect, not a refusal page."""
    from app.audit.enums import SecurityEventType
    from app.audit.models import SecurityAuditEvent

    download_url, open_url, detail_url = _routes(restricted_file)

    download = signed_in.get(download_url)
    assert download.status_code == 200
    assert download["Content-Disposition"].startswith("attachment")
    assert _body(download) == PDF + b"secret"

    opened = signed_in.get(open_url)
    assert opened.status_code == 200
    assert opened["Content-Disposition"].startswith("inline")
    assert _body(opened) == PDF + b"secret"

    detail = signed_in.get(detail_url)
    assert detail.status_code == 200
    assert "salajane.pdf" in detail.content.decode()

    served = SecurityAuditEvent.objects.filter(
        event_type=SecurityEventType.DOCUMENT_DOWNLOADED, subject_id=restricted_file.pk
    )
    assert sorted(served.values_list("detail__disposition", flat=True)) == [
        "attachment",
        "inline",
    ]


def test_guessed_ids_are_404_not_500(signed_in):
    for name in (
        "documents:download",
        "documents:open",
        "documents:document_detail",
        "documents:thumbnail",
    ):
        assert signed_in.get(reverse(name, kwargs={"pk": uuid.uuid4()})).status_code == 404


def test_a_document_id_is_not_a_version_id(signed_in, specialist, capture_evidence):
    """The download route is keyed on a version; a document's pk must not resolve."""
    matter = _matter(specialist)
    version = capture_evidence(matter, PDF, "kiri.pdf", "application/pdf")
    response = signed_in.get(reverse("documents:download", kwargs={"pk": version.document_id}))
    assert response.status_code == 404


def test_anonymous_is_redirected_from_every_file_route(client, restricted_file):
    for url in _routes(restricted_file):
        response = client.get(url)
        assert response.status_code == 302
        assert "salajane" not in response.get("Location", "")


# ---------------------------------------------------------------------------
# Removal and versions
# ---------------------------------------------------------------------------


def test_removed_document_is_gone_from_every_route_and_search(
    signed_in, specialist, capture_evidence
):
    from app.search.services import search_documents

    matter = _matter(specialist)
    version = capture_evidence(matter, PDF, "Kustutatavfail.pdf", "application/pdf")

    signed_in.post(reverse("documents:remove", kwargs={"pk": version.document_id}))

    document = Document._base_manager.get(pk=version.document_id)
    assert document.removed_at is not None
    for url in _routes(version):
        assert signed_in.get(url).status_code == 404
    assert not search_documents(query="Kustutatavfail", user=specialist).exists()
    # The bytes and their row stay: removal is not deletion (docs/adr/0120).
    assert DocumentVersion.objects.filter(pk=version.pk).exists()


def test_new_version_keeps_the_old_one_downloadable(signed_in, specialist, capture_evidence):
    matter = _matter(specialist)
    first = capture_evidence(matter, PDF + b"v1", "v1.pdf", "application/pdf")

    signed_in.post(
        reverse("documents:add_version", kwargs={"pk": first.document_id}),
        {"upload": SimpleUploadedFile("v2.pdf", PDF + b"v2", content_type="application/pdf")},
    )

    document = Document.objects.get(pk=first.document_id)
    assert document.versions.count() == 2
    assert document.current_version.original_filename == "v2.pdf"
    old = signed_in.get(reverse("documents:download", kwargs={"pk": first.pk}))
    assert b"".join(old.streaming_content) == PDF + b"v1"


def test_failed_extraction_is_not_presented_as_content(
    signed_in, specialist, capture_evidence, extract
):
    """A PDF the parser cannot read shows a failure, never text, and adds nothing to search."""
    from app.documents.enums import DerivativeStatus
    from app.documents.models import DocumentTextFragment
    from app.search.services import search_documents

    matter = _matter(specialist)
    version = capture_evidence(
        matter, b"%PDF-1.4\n katkinesisu \x00\x01", "katki.pdf", "application/pdf"
    )
    extract(version)

    version.refresh_from_db()
    assert version.extraction_state != ExtractionState.DONE
    assert not DocumentTextFragment.objects.filter(
        derivative__version=version, derivative__status=DerivativeStatus.ACTIVE
    ).exists()
    page = signed_in.get(reverse("documents:document_detail", kwargs={"pk": version.document_id}))
    assert page.status_code == 200
    if version.extraction_state == ExtractionState.FAILED:
        assert "Teksti ei õnnestunud eraldada" in page.content.decode()
    assert not search_documents(query="katkinesisu", user=specialist).exists()
