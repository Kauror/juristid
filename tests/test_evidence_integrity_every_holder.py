"""`check_evidence_integrity` checks the bytes of every holder of evidence (ENG-049).

`EVIDENCE_REFERENCES` names two canonical holders of evidence bytes:
`DocumentVersion` and the opinion archive's `OpinionArchiveBinary`. The orphan
half of the checker read that registry; the half that checks each row's bytes
kept its own `DocumentVersion` query. So a missing, truncated or altered archive
letter reported «No integrity problems found.», even with `--verify-sha`.

The byte check is now driven from the registry, so a holder added to it is
checked the moment it is added, and a test below fails if a holder can be
registered without being checked.
"""

from __future__ import annotations

from io import StringIO

import pytest
from django.core.management import call_command

from app.documents.integrity import MISSING_OBJECT, SHA_MISMATCH, SIZE_MISMATCH, check_evidence
from app.documents.references import EVIDENCE_REFERENCES
from app.legacy_import.opinion_binary import OpinionArchiveBinary
from app.legacy_import.opinion_materialize import materialize
from tests.test_opinion_archive_evidence import catalogued  # noqa: F401 - fixture

pytestmark = pytest.mark.django_db


@pytest.fixture
def archive_binary(catalogued, settings):  # noqa: F811 - the imported fixture
    path, digest, letters = catalogued
    materialize(archive_path=path, expected_archive_sha256=digest)
    binary = OpinionArchiveBinary.objects.get(sha256=letters[0].sha256)
    return binary, settings.EVIDENCE_ROOT / binary.storage_key


def _run(*arguments: str) -> tuple[int, str]:
    out = StringIO()
    try:
        call_command("check_evidence_integrity", *arguments, stdout=out)
    except SystemExit as exit_:
        return int(exit_.code or 0), out.getvalue()
    return 0, out.getvalue()


def _kinds(report) -> dict[str, list[str]]:
    return {kind: [f.subject for f in rows] for kind, rows in report.by_kind().items()}


def test_intact_archive_bytes_are_clean(archive_binary):
    binary, _ = archive_binary

    report = check_evidence(verify_sha=True)

    assert report.ok, report.findings
    assert report.objects_checked["OpinionArchiveBinary"] >= 1
    assert report.bytes_hashed >= binary.size_bytes


def test_a_missing_archive_object_is_found(archive_binary):
    binary, path = archive_binary
    path.unlink()

    report = check_evidence()

    assert _kinds(report) == {MISSING_OBJECT: [str(binary.pk)]}
    assert "OpinionArchiveBinary" in report.findings[0].detail
    code, output = _run()
    assert code == 1
    assert "missing-object: 1" in output


def test_a_truncated_archive_object_is_found(archive_binary):
    binary, path = archive_binary
    path.write_bytes(path.read_bytes()[:3])

    report = check_evidence()

    assert _kinds(report) == {SIZE_MISMATCH: [str(binary.pk)]}


def test_same_size_different_bytes_is_found_with_verify_sha(archive_binary):
    binary, path = archive_binary
    original = path.read_bytes()
    path.write_bytes(bytes(reversed(original)))

    assert check_evidence().ok  # the structural pass cannot see it
    report = check_evidence(verify_sha=True)

    assert _kinds(report) == {SHA_MISMATCH: [str(binary.pk)]}
    code, output = _run("--verify-sha")
    assert code == 1
    assert "sha-mismatch: 1" in output


def test_a_document_version_is_still_checked(archive_binary, normal_matter, capture_evidence):
    """The control: the holder that was always checked still is."""
    version = capture_evidence(
        normal_matter, b"%PDF-1.4 kontroll", "kontroll.pdf", "application/pdf"
    )
    from django.conf import settings

    (settings.EVIDENCE_ROOT / version.storage_key).unlink()

    report = check_evidence()

    assert _kinds(report) == {MISSING_OBJECT: [str(version.pk)]}
    assert report.versions_checked == 1


def test_every_registered_holder_is_checked(archive_binary, normal_matter, capture_evidence):
    """A holder added to the registry is checked without anybody remembering to."""
    capture_evidence(normal_matter, b"%PDF-1.4 kontroll", "kontroll.pdf", "application/pdf")

    report = check_evidence()

    assert set(report.objects_checked) == {reference.label for reference in EVIDENCE_REFERENCES}
    assert all(count >= 1 for count in report.objects_checked.values())
