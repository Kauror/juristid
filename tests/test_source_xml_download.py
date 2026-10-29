"""`legacy_import:source_xml` — a OneNote page's own XML (audit 2026-10-28 C-05).

A missing storage object is a 404, never a 500; a served page is audited as
`DOCUMENT_DOWNLOADED`, as every other served file is; a reader with no business
read sees nothing (docs/adr/0181); the storage key never reaches the answer.
Synthetic data only.
"""

from __future__ import annotations

import uuid

import pytest
from django.core.files.base import ContentFile
from django.urls import reverse
from django.utils import timezone

from app.audit.enums import SecurityEventType
from app.audit.models import SecurityAuditEvent
from app.core.enums import Visibility
from tests import factories

pytestmark = pytest.mark.django_db

XML = b"<one:Page><one:T>LAHTE-XML-6611</one:T></one:Page>"


def _link(*, visibility: str = Visibility.NORMAL, stored: bool = True):
    from app.legacy_import.historical_apply import legacy_source_storage
    from app.legacy_import.source_pages import LegacySourcePage, MatterSourcePage

    matter = factories.MatterFactory(visibility=visibility)
    key = f"policy/{uuid.uuid4().hex}.xml"
    if stored:
        key = legacy_source_storage().save(key, ContentFile(XML))
    now = timezone.now()
    page = LegacySourcePage.objects.create(
        source_page_id=f"{{page-{uuid.uuid4().hex[:8]}}}",
        page_key=f"page-{uuid.uuid4().hex[:8]}",
        source_notebook="Õigusloome",
        source_section="2019",
        capture_id="c",
        title="Ajalooline leht",
        blocks=[],
        source_xml_storage_key=key,
        source_xml_sha256="a" * 64,
        first_imported_at=now,
        latest_imported_at=now,
    )
    return MatterSourcePage.objects.create(matter=matter, source_page=page, match_method="EXACT")


def _get(client, link):
    return client.get(reverse("legacy_import:source_xml", kwargs={"pk": link.pk}))


def _downloads():
    return SecurityAuditEvent.objects.filter(event_type=SecurityEventType.DOCUMENT_DOWNLOADED)


def test_a_served_page_is_recorded_as_a_download(client):
    link = _link()
    reader = factories.UserFactory()
    client.force_login(reader)
    response = _get(client, link)
    assert response.status_code == 200
    assert b"LAHTE-XML-6611" in b"".join(response.streaming_content)
    assert response["Content-Disposition"].startswith("attachment")
    event = _downloads().get()
    assert event.succeeded and event.actor == reader
    assert event.subject_id == link.source_page.pk
    assert event.detail["source"] == "onenote_source_xml"
    assert event.detail["matter"] == str(link.matter_id)
    assert link.source_page.source_xml_storage_key not in str(event.detail)


def test_a_missing_storage_object_is_a_404_and_a_failed_audit_row(client):
    link = _link(stored=False)
    client.force_login(factories.UserFactory())
    response = _get(client, link)
    assert response.status_code == 404
    assert link.source_page.source_xml_storage_key not in response.content.decode()
    event = _downloads().get()
    assert not event.succeeded and event.detail["outcome"] == "source_object_missing"


def test_a_restricted_teemas_page_stays_closed_to_a_viewer_without_business_read(client):
    link = _link(visibility=Visibility.RESTRICTED)
    client.force_login(factories.AdministratorFactory())
    response = _get(client, link)
    assert response.status_code == 404
    assert b"LAHTE-XML-6611" not in response.content
    assert not _downloads().exists()


def test_a_business_reader_reads_a_restricted_teemas_page(client):
    """ADR 0181: every business-read role reads RESTRICTED content."""
    link = _link(visibility=Visibility.RESTRICTED)
    client.force_login(factories.ReaderFactory())
    response = _get(client, link)
    assert response.status_code == 200
    assert _downloads().filter(succeeded=True).count() == 1
