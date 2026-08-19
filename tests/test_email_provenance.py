"""Email attachments become evidence, and never lose where they came from.

The question this file exists to keep answerable is one sentence long: *which
exact original email did this PDF arrive in?* Free text in a provenance note
answers it for about a year, until the parser that wrote it has been replaced
and nobody trusts the wording. A row answers it forever
(Stage-2B brief 25, 102).
"""

from __future__ import annotations

import pytest

from app.documents.derivatives import AttachmentDisposition, EmailAttachmentLink
from app.documents.email_intake import attachments_of, parent_email_of
from app.documents.enums import DerivativeKind, DerivativeStatus, DocumentRole, MalwareScanState
from app.documents.models import DocumentDerivative, DocumentVersion
from tests import synthetic_corpus as corpus

pytestmark = pytest.mark.django_db

EML = "message/rfc822"
MSG = "application/vnd.ms-outlook"


@pytest.fixture
def email_version(normal_matter, capture_evidence, specialist):
    return capture_evidence(
        normal_matter,
        corpus.consultation_eml(),
        "kooskolastus.eml",
        EML,
        uploaded_by=specialist,
    )


def test_the_original_message_stays_exactly_as_it_arrived(email_version, extract) -> None:
    before = (email_version.sha256, email_version.size_bytes)
    extract(email_version)

    email_version.refresh_from_db()
    assert (email_version.sha256, email_version.size_bytes) == before
    assert email_version.mime_type == EML


def test_each_attachment_becomes_its_own_document(email_version, extract, normal_matter) -> None:
    extract(email_version)

    attachments = normal_matter.documents.filter(role=DocumentRole.EMAIL_ATTACHMENT)
    assert sorted(attachments.values_list("current_version__original_filename", flat=True)) == [
        "lisa-1.pdf",
        "markmed.txt",
    ]


def test_each_attachment_keeps_its_own_bytes_and_checksum(
    email_version, extract, normal_matter
) -> None:
    """Concatenating or zipping them would destroy exactly the per-file
    provenance that makes them evidence."""
    extract(email_version)

    versions = DocumentVersion.objects.filter(
        document__role=DocumentRole.EMAIL_ATTACHMENT, document__matter=normal_matter
    )
    checksums = {version.sha256 for version in versions}
    assert len(checksums) == versions.count() == 2
    assert email_version.sha256 not in checksums


def test_the_link_row_answers_which_message_an_attachment_came_from(email_version, extract) -> None:
    extract(email_version)

    links = list(attachments_of(email_version))
    assert [link.declared_filename for link in links] == ["lisa-1.pdf", "markmed.txt"]
    assert all(link.parent_version_id == email_version.pk for link in links)
    assert all(link.disposition == AttachmentDisposition.ATTACHMENT for link in links)

    attachment = links[0].attachment_version
    assert parent_email_of(attachment).parent_version_id == email_version.pk


def test_an_attachment_ordinal_is_its_position_in_the_message(email_version, extract) -> None:
    extract(email_version)
    assert [link.ordinal for link in attachments_of(email_version)] == [1, 2]


def test_an_inline_resource_does_not_become_a_document(
    email_version, extract, normal_matter
) -> None:
    """Nine signature logos per forwarded thread would bury the two annexes
    that matter."""
    extract(email_version)

    filenames = set(
        normal_matter.documents.values_list("current_version__original_filename", flat=True)
    )
    assert "allkiri-logo.png" not in filenames

    metadata = DocumentDerivative.objects.get(
        version=email_version, kind=DerivativeKind.EMAIL_METADATA, status=DerivativeStatus.ACTIVE
    ).metadata
    assert metadata["attachment_count"] == 2
    assert metadata["inline_resource_count"] == 1


def test_an_attachment_is_never_assumed_to_be_an_official_document(
    email_version, extract, normal_matter
) -> None:
    """Mail arrives from ministries, members and colleagues alike."""
    extract(email_version)

    roles = set(
        normal_matter.documents.exclude(pk=email_version.document_id).values_list("role", flat=True)
    )
    assert roles == {DocumentRole.EMAIL_ATTACHMENT}


def test_an_attachment_does_not_inherit_the_parent_scan_verdict(
    normal_matter, capture_evidence, extract
) -> None:
    """The message having been scanned says nothing about what was inside it."""
    version = capture_evidence(
        normal_matter,
        corpus.consultation_eml(),
        "kiri.eml",
        EML,
        malware_scan_state=MalwareScanState.CLEAN,
    )
    extract(version)

    states = set(
        DocumentVersion.objects.filter(document__role=DocumentRole.EMAIL_ATTACHMENT).values_list(
            "malware_scan_state", flat=True
        )
    )
    assert states == {MalwareScanState.PENDING}


def test_an_attachment_inherits_the_parent_restriction(
    restricted_matter, capture_evidence, extract
) -> None:
    """Not a copied value — the Matter is restricted, so its children are too.

    Asserted here because the intake path creates documents directly and could
    have forgotten to pass the override along, which would make an attachment
    of a restricted email *less* restricted than the email.
    """
    version = capture_evidence(
        restricted_matter,
        corpus.consultation_eml(),
        "kiri.eml",
        EML,
        visibility_override="RESTRICTED",
    )
    extract(version)

    overrides = set(
        restricted_matter.documents.filter(role=DocumentRole.EMAIL_ATTACHMENT).values_list(
            "visibility_override", flat=True
        )
    )
    assert overrides == {"RESTRICTED"}


def test_reprocessing_a_message_does_not_duplicate_its_attachments(
    email_version, extract, normal_matter
) -> None:
    extract(email_version)
    first = normal_matter.documents.count()

    extract(email_version)

    assert normal_matter.documents.count() == first
    assert EmailAttachmentLink.objects.filter(parent_version=email_version).count() == 2


def test_an_attachment_in_a_format_the_store_refuses_is_skipped_not_fatal(
    normal_matter, capture_evidence, extract
) -> None:
    """One odd attachment must not cost the message its extraction."""
    from email.message import EmailMessage

    message = EmailMessage()
    message["Subject"] = "Katse"
    message["From"] = "keegi@naidis.invalid"
    message["To"] = "oigus@koda.invalid"
    message.set_content("Sisu")
    message.add_attachment(
        b"#!/bin/sh\necho tere\n",
        maintype="application",
        subtype="x-sh",
        filename="skript.sh",
    )
    version = capture_evidence(normal_matter, message.as_bytes(), "kiri.eml", EML)

    report = extract(version)

    assert report.state == "DONE"
    assert not normal_matter.documents.filter(role=DocumentRole.EMAIL_ATTACHMENT).exists()


def test_an_outlook_message_records_the_same_provenance(
    normal_matter, capture_evidence, extract
) -> None:
    version = capture_evidence(normal_matter, corpus.outlook_msg(), "kiri.msg", MSG)
    extract(version)

    links = list(attachments_of(version))
    assert [link.declared_filename for link in links] == ["lisa-1.pdf"]
    assert links[0].attachment_version.mime_type == "application/pdf"


def test_the_attachment_type_comes_from_the_extension_not_the_message(
    normal_matter, capture_evidence, extract
) -> None:
    """A message's claim about its attachment is attacker-controlled, exactly
    like a browser's Content-Type, and the upload path already refuses to
    believe that one."""
    from email.message import EmailMessage

    message = EmailMessage()
    message["Subject"] = "Katse"
    message["From"] = "keegi@naidis.invalid"
    message["To"] = "oigus@koda.invalid"
    message.set_content("Sisu")
    message.add_attachment(
        corpus.memo_txt(),
        maintype="application",
        subtype="pdf",  # a lie
        filename="markmed.txt",
    )
    version = capture_evidence(normal_matter, message.as_bytes(), "kiri.eml", EML)
    extract(version)

    stored = DocumentVersion.objects.get(document__role=DocumentRole.EMAIL_ATTACHMENT)
    assert stored.mime_type == "text/plain"


def test_a_nested_message_is_preserved_whole(normal_matter, capture_evidence, extract) -> None:
    """Stage 2B keeps an attached message as evidence and does not crawl it."""
    from email.message import EmailMessage

    inner = corpus.consultation_eml(attachments=False, inline_logo=False)
    message = EmailMessage()
    message["Subject"] = "Edastan"
    message["From"] = "keegi@naidis.invalid"
    message["To"] = "oigus@koda.invalid"
    message.set_content("Vaata lisatud kirja.")
    message.add_attachment(inner, maintype="message", subtype="rfc822", filename="edastatud.eml")
    version = capture_evidence(normal_matter, message.as_bytes(), "valine.eml", EML)

    extract(version)

    nested = DocumentVersion.objects.get(document__role=DocumentRole.EMAIL_ATTACHMENT)
    assert nested.mime_type == EML
    assert nested.original_filename == "edastatud.eml"
    # And it is itself a pending extraction, not something this parse expanded.
    assert nested.extraction_state == "PENDING"


def test_the_email_body_and_headers_are_both_searchable_text(email_version, extract) -> None:
    extract(email_version)

    fragments = {
        fragment.locator_label: fragment.text
        for fragment in DocumentDerivative.objects.get(
            version=email_version,
            kind=DerivativeKind.EXTRACTED_TEXT,
            status=DerivativeStatus.ACTIVE,
        ).fragments.all()
    }
    assert "kirja päis" in fragments
    assert "kirja sisu" in fragments
    assert "Kadri Näidis" in fragments["kirja päis"]
    assert corpus.ONLY_IN_EMAIL_BODY in fragments["kirja sisu"]


# -- the thumbnail route ---------------------------------------------------


def test_a_thumbnail_is_served_inline_and_the_original_never_is(
    normal_matter, capture_evidence, extract, client, specialist
) -> None:
    """The whole point of generating one: the safe copy may be displayed.

    The original keeps its attachment disposition, because it is somebody
    else's bytes and always will be.
    """
    from app.documents.enums import DerivativeKind, DerivativeStatus
    from app.documents.models import DocumentDerivative

    version = capture_evidence(
        normal_matter, corpus.government_pdf(), "kaaskiri.pdf", "application/pdf"
    )
    extract(version)
    client.force_login(specialist)

    thumbnail = DocumentDerivative.objects.get(
        version=version, kind=DerivativeKind.THUMBNAIL, status=DerivativeStatus.ACTIVE
    )
    response = client.get(f"/dokumendid/pisipilt/{thumbnail.pk}/")

    assert response.status_code == 200
    assert response["Content-Type"] == "image/png"
    assert response["X-Content-Type-Options"] == "nosniff"
    assert "attachment" not in response.get("Content-Disposition", "")

    original = client.get(f"/dokumendid/t%C3%B5end/{version.pk}/")
    assert "attachment" in original["Content-Disposition"]


def test_a_restricted_documents_thumbnail_is_not_served(
    restricted_matter, capture_evidence, extract, client, other_specialist
) -> None:
    """A preview that leaked where a download did not would be the more
    embarrassing of the two."""
    from app.documents.enums import DerivativeKind, DerivativeStatus
    from app.documents.models import DocumentDerivative

    version = capture_evidence(
        restricted_matter, corpus.government_pdf(), "salajane.pdf", "application/pdf"
    )
    extract(version)
    client.force_login(other_specialist)

    thumbnail = DocumentDerivative.objects.get(
        version=version, kind=DerivativeKind.THUMBNAIL, status=DerivativeStatus.ACTIVE
    )

    assert client.get(f"/dokumendid/pisipilt/{thumbnail.pk}/").status_code == 404
    assert client.get(f"/dokumendid/{version.document_id}/").status_code == 404
