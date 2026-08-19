"""Filing incoming material: the file-first way into a Matter.

The property most of these defend is atomicity. A rejected file must leave
nothing behind, because a Matter with half its documents looks like real work
and quietly isn't — and the person who finds it a week later has no way to tell
what was supposed to be there.
"""

from __future__ import annotations

import pytest
from django.core.files.uploadedfile import SimpleUploadedFile
from django.urls import reverse
from django.utils import timezone

from app.core.enums import Visibility
from app.core.errors import DomainError
from app.documents.enums import DocumentRole
from app.documents.models import Document, DocumentVersion
from app.matters.intake import register_incoming, role_for, title_from_filename, validate_uploads
from app.matters.models import Matter
from app.organisations.models import Organisation, OrganisationType
from app.search.services import search_matters
from tests import factories

pytestmark = pytest.mark.django_db

PDF = b"%PDF-1.4 synthetic incoming draft"
XLSX = b"PK\x03\x04synthetic annex"
MSG = b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1synthetic message"


def _file(name: str, content: bytes) -> SimpleUploadedFile:
    return SimpleUploadedFile(name, content)


def _post(client, files, **fields):
    payload = {"visibility": Visibility.NORMAL, **fields}
    payload["uploads"] = files
    return client.post(reverse("matters:intake"), payload)


# -- the service -----------------------------------------------------------


def test_one_pdf_creates_a_matter_and_its_evidence(db, specialist) -> None:
    uploads = validate_uploads([_file("eelnou.pdf", PDF)])
    result = register_incoming(uploads=uploads, actor=specialist, owner=specialist)

    assert result.documents == 1
    document = Document.objects.get(matter=result.matter)
    assert document.role == DocumentRole.INCOMING_AUTHORITY
    version = document.current_version
    assert version.original_filename == "eelnou.pdf"
    assert version.mime_type == "application/pdf"
    assert version.size_bytes == len(PDF)
    assert len(version.sha256) == 64


def test_several_files_become_one_matter_and_several_documents(db, specialist) -> None:
    """A government envelope is a covering letter, a draft and annexes.

    Each keeps its own filename, type and checksum. Concatenating them would
    destroy exactly the per-file provenance that makes this evidence.
    """
    uploads = validate_uploads(
        [_file("kaaskiri.pdf", PDF), _file("eelnou.pdf", PDF), _file("lisa.xlsx", XLSX)]
    )
    result = register_incoming(uploads=uploads, actor=specialist)

    assert Matter.objects.count() == 1
    assert result.documents == 3
    names = set(
        DocumentVersion.objects.filter(document__matter=result.matter).values_list(
            "original_filename", flat=True
        )
    )
    assert names == {"kaaskiri.pdf", "eelnou.pdf", "lisa.xlsx"}


def test_an_email_is_recorded_as_an_email(db, specialist) -> None:
    """Recorded, not parsed. Reading .msg is Stage 2B; the bytes are evidence now."""
    uploads = validate_uploads([_file("kiri.msg", MSG)])
    result = register_incoming(uploads=uploads, actor=specialist)
    assert Document.objects.get(matter=result.matter).role == DocumentRole.ORIGINAL_EMAIL


@pytest.mark.parametrize(
    ("filename", "expected"),
    [
        ("kiri.msg", DocumentRole.ORIGINAL_EMAIL),
        ("kiri.eml", DocumentRole.ORIGINAL_EMAIL),
        ("KIRI.EML", DocumentRole.ORIGINAL_EMAIL),
        ("eelnou.pdf", DocumentRole.INCOMING_AUTHORITY),
        ("lisa.xlsx", DocumentRole.INCOMING_AUTHORITY),
    ],
)
def test_the_role_follows_the_file(filename: str, expected: str) -> None:
    assert role_for(filename) == expected


def test_the_title_falls_back_to_the_filename(db, specialist) -> None:
    uploads = validate_uploads([_file("pakendiseaduse_muutmise_eelnou.pdf", PDF)])
    result = register_incoming(uploads=uploads, actor=specialist)
    assert result.matter.title == "pakendiseaduse muutmise eelnou"


def test_the_derived_title_invents_no_meaning() -> None:
    """Mechanical on purpose: a confident wrong title is worse than an ugly one."""
    assert title_from_filename("eelnou_v3_FINAL.pdf") == "eelnou v3 FINAL"
    assert title_from_filename("") == "Saabunud materjal"


def test_an_explicit_title_wins(db, specialist) -> None:
    uploads = validate_uploads([_file("x.pdf", PDF)])
    result = register_incoming(uploads=uploads, title="Pakendiseaduse eelnõu", actor=specialist)
    assert result.matter.title == "Pakendiseaduse eelnõu"


def test_no_stage_is_invented(db, specialist) -> None:
    """A file arriving says nothing about where the external process stands."""
    uploads = validate_uploads([_file("x.pdf", PDF)])
    result = register_incoming(uploads=uploads, actor=specialist)
    assert result.matter.stage_id is None


def test_uploading_nothing_is_refused(db, specialist) -> None:
    with pytest.raises(DomainError):
        validate_uploads([])


# -- the view --------------------------------------------------------------


def test_the_intake_page_needs_signing_in(client) -> None:
    assert client.get(reverse("matters:intake")).status_code == 302


def test_posting_a_pdf_creates_and_opens_the_matter(client, specialist) -> None:
    client.force_login(specialist)
    response = _post(client, [_file("eelnou.pdf", PDF)], title="Saabunud eelnõu")

    assert response.status_code == 302
    matter = Matter.objects.get(title="Saabunud eelnõu")
    assert response["Location"] == reverse("matters:matter_detail", kwargs={"pk": matter.pk})
    assert Document.objects.filter(matter=matter).count() == 1


def test_an_invalid_file_leaves_no_matter_behind(client, specialist) -> None:
    """The failure this whole module is shaped around.

    A Matter created before the files were checked would survive the rejection
    and look like real work with its documents missing.
    """
    client.force_login(specialist)
    before = Matter.objects.count()

    response = _post(client, [_file("eelnou.pdf", PDF), _file("virus.exe", b"MZ")])

    assert response.status_code == 400
    assert Matter.objects.count() == before
    assert Document.objects.count() == 0


def test_a_rejected_second_file_leaves_no_partial_matter(client, specialist) -> None:
    """Validation happens before any write, so file order cannot matter."""
    client.force_login(specialist)
    response = _post(client, [_file("bad.exe", b"MZ"), _file("ok.pdf", PDF)])
    assert response.status_code == 400
    assert Matter.objects.count() == 0
    assert DocumentVersion.objects.count() == 0


def test_the_received_date_defaults_to_today(client, specialist) -> None:
    client.force_login(specialist)
    _post(client, [_file("x.pdf", PDF)], title="Täna saabunud")
    assert Matter.objects.get(title="Täna saabunud").received_date == timezone.localdate()


def test_a_restricted_intake_stays_restricted(client, specialist, other_specialist) -> None:
    client.force_login(specialist)
    _post(
        client,
        [_file("salajane.pdf", PDF)],
        title="Piiratud saadetis",
        visibility=Visibility.RESTRICTED,
    )

    matter = Matter.objects.get(title="Piiratud saadetis")
    assert matter.visibility == Visibility.RESTRICTED
    assert not Matter.objects.visible_to(other_specialist).filter(pk=matter.pk).exists()


def test_incoming_evidence_downloads_only_for_the_authorized(
    client, specialist, other_specialist
) -> None:
    client.force_login(specialist)
    # An owner, deliberately. A RESTRICTED Matter with nobody on it is invisible
    # even to whoever filed it, because restricted access follows participation
    # rather than authorship — correct, and worth not tripping over here.
    _post(
        client,
        [_file("salajane.pdf", PDF)],
        title="Piiratud saadetis",
        visibility=Visibility.RESTRICTED,
        owner=specialist.pk,
    )
    version = DocumentVersion.objects.get()

    assert client.get(reverse("documents:download", kwargs={"pk": version.pk})).status_code == 200

    client.force_login(other_specialist)
    # 404 rather than 403: a 403 would confirm the file exists.
    assert client.get(reverse("documents:download", kwargs={"pk": version.pk})).status_code == 404


def test_the_sender_can_be_an_organisation_created_moments_ago(client, specialist) -> None:
    client.force_login(specialist)
    ministry = Organisation.objects.create(
        name="Regionaal- ja Põllumajandusministeerium",
        organisation_type=OrganisationType.MINISTRY,
    )
    _post(client, [_file("x.pdf", PDF)], title="Ministeeriumist", source_organisation=ministry.pk)
    assert Matter.objects.get(title="Ministeeriumist").source_organisation == ministry


def test_the_new_matter_is_searchable_at_once(client, specialist) -> None:
    """No reindex step: the projection maintains itself on write."""
    client.force_login(specialist)
    _post(client, [_file("x.pdf", PDF)], title="Pakendiseaduse muutmise eelnõu")

    found = [
        result.matter.title for result in search_matters(query="Pakendiseaduse", user=specialist)
    ]
    assert "Pakendiseaduse muutmise eelnõu" in found


def test_the_dashboard_sees_it_immediately(client, specialist) -> None:
    from app.matters import dashboard

    client.force_login(specialist)
    before = next(c for c in dashboard.summary_cards(specialist) if c.key == "active").count
    _post(client, [_file("x.pdf", PDF)], title="Uus saadetis", owner=specialist.pk)
    after = next(c for c in dashboard.summary_cards(specialist) if c.key == "active").count
    assert after == before + 1


def test_the_matter_page_shows_the_incoming_files(client, specialist) -> None:
    client.force_login(specialist)
    _post(client, [_file("eelnou.pdf", PDF), _file("lisa.xlsx", XLSX)], title="Saadetis")
    matter = Matter.objects.get(title="Saadetis")

    body = client.get(reverse("matters:matter_detail", kwargs={"pk": matter.pk})).content.decode()
    assert "Saabunud materjalid" in body
    assert "eelnou.pdf" in body
    assert "lisa.xlsx" in body


def test_title_only_matter_creation_still_works(client, specialist) -> None:
    """The other entry path must not become file-first by accident."""
    client.force_login(specialist)
    response = client.post(reverse("matters:matter_create"), {"title": "Ainult pealkiri"})
    assert response.status_code == 302
    assert Matter.objects.filter(title="Ainult pealkiri").exists()


def test_saabunud_offers_the_intake_action(client, specialist) -> None:
    client.force_login(specialist)
    body = client.get(reverse("matters:inbox")).content.decode()
    assert "Lisa saabunud materjal" in body
    assert reverse("matters:intake") in body


def test_the_seeded_factories_are_untouched_by_intake(client, specialist) -> None:
    """Intake adds; it never rewrites what is already filed."""
    existing = factories.MatterFactory(owner=specialist)
    client.force_login(specialist)
    _post(client, [_file("x.pdf", PDF)], title="Uus")
    existing.refresh_from_db()
    assert existing.title
