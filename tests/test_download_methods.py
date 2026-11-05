"""The routes that serve stored bytes answer GET and HEAD only.

`documents:download`, `documents:open`, `documents:thumbnail` and
`legacy_import:source_xml` are reads. Before `@require_safe` they answered any
method, so a POST, PUT, PATCH, DELETE or OPTIONS that reached them served the
file and — for the evidence and the page XML — wrote a DOCUMENT_DOWNLOADED row
as if somebody had downloaded it. OPTIONS is not CSRF-guarded, so it reached
them even from a client that enforces CSRF. The held OneNote originals
(`legacy_import:source_original`) already worked this way. Synthetic data only.
"""

from __future__ import annotations

import uuid
from typing import Any

import pytest
from django.core.files.base import ContentFile
from django.test import Client
from django.urls import reverse
from django.utils import timezone

from app.audit.enums import SecurityEventType
from app.audit.models import SecurityAuditEvent
from app.documents.enums import DerivativeKind, DerivativeStatus
from app.documents.models import DocumentDerivative
from tests import factories

pytestmark = pytest.mark.django_db

UNSAFE = ["post", "put", "patch", "delete", "options"]
ROUTES = ["documents:download", "documents:open", "documents:thumbnail", "legacy_import:source_xml"]
#: The routes whose GET writes a DOCUMENT_DOWNLOADED row (a thumbnail is a
#: regenerable derivative and is not audited).
AUDITED = {"documents:download", "documents:open", "legacy_import:source_xml"}
#: Bytes only the stored file carries: a refusal page must not contain them.
STORED = {
    "documents:download": b"meetodid-6612",
    "documents:open": b"meetodid-6612",
    "documents:thumbnail": b"methods-6612",
    "legacy_import:source_xml": b"LAHTE-6612",
}


def _thumbnail(version: Any) -> DocumentDerivative:
    from app.documents.extraction.orchestrator import derivative_storage

    key = derivative_storage().save(
        f"methods/{uuid.uuid4().hex}.png", ContentFile(b"\x89PNG\r\n\x1a\nmethods-6612")
    )
    return DocumentDerivative.objects.create(
        version=version,
        kind=DerivativeKind.THUMBNAIL,
        generator="methods",
        generator_version="1",
        status=DerivativeStatus.ACTIVE,
        storage_key=key,
    )


def _source_page_link(matter: Any) -> Any:
    from app.legacy_import.historical_apply import legacy_source_storage
    from app.legacy_import.source_pages import LegacySourcePage, MatterSourcePage

    key = legacy_source_storage().save(
        f"methods/{uuid.uuid4().hex}.xml",
        ContentFile(b"<one:Page><one:T>LAHTE-6612</one:T></one:Page>"),
    )
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


@pytest.fixture
def urls(capture_evidence, specialist) -> dict[str, str]:
    matter = factories.MatterFactory(owner=specialist)
    version = capture_evidence(matter, b"%PDF-1.4\nmeetodid-6612\n", "kiri.pdf", "application/pdf")
    return {
        "documents:download": reverse("documents:download", kwargs={"pk": version.pk}),
        "documents:open": reverse("documents:open", kwargs={"pk": version.pk}),
        "documents:thumbnail": reverse(
            "documents:thumbnail", kwargs={"pk": _thumbnail(version).pk}
        ),
        "legacy_import:source_xml": reverse(
            "legacy_import:source_xml", kwargs={"pk": _source_page_link(matter).pk}
        ),
    }


def _downloads() -> int:
    return SecurityAuditEvent.objects.filter(
        event_type=SecurityEventType.DOCUMENT_DOWNLOADED
    ).count()


def _drain(response: Any) -> bytes:
    if getattr(response, "streaming", False):
        return b"".join(response.streaming_content)
    return response.content


@pytest.mark.parametrize("route", ROUTES)
@pytest.mark.parametrize("method", UNSAFE)
def test_an_unsafe_method_is_refused_and_serves_and_audits_nothing(
    urls, client, specialist, route, method
):
    """A plain test client does not enforce CSRF, so every method reaches the route."""
    client.force_login(specialist)
    before = SecurityAuditEvent.objects.count()

    response = getattr(client, method)(urls[route])

    assert response.status_code == 405, (route, method, response.status_code)
    assert set(response["Allow"].replace(" ", "").split(",")) == {"GET", "HEAD"}
    assert STORED[route] not in _drain(response)
    assert SecurityAuditEvent.objects.count() == before


@pytest.mark.parametrize("route", ROUTES)
def test_options_is_refused_even_where_csrf_is_enforced(urls, specialist, route):
    """CSRF guards POST, PUT, PATCH and DELETE; OPTIONS passes it and reached the view."""
    client = Client(enforce_csrf_checks=True)
    client.force_login(specialist)

    response = client.options(urls[route])

    assert response.status_code == 405
    assert _downloads() == 0


@pytest.mark.parametrize("route", ROUTES)
def test_an_anonymous_unsafe_request_is_refused_before_sign_in(urls, route):
    response = Client().post(urls[route])

    assert response.status_code == 405
    assert _downloads() == 0


@pytest.mark.parametrize("route", ROUTES)
def test_get_still_serves_and_audits_as_before(urls, client, specialist, route):
    client.force_login(specialist)

    response = client.get(urls[route])

    assert response.status_code == 200
    assert STORED[route] in _drain(response)
    assert _downloads() == (1 if route in AUDITED else 0)


@pytest.mark.parametrize("route", ROUTES)
def test_head_still_answers(urls, client, specialist, route):
    client.force_login(specialist)

    response = client.head(urls[route])

    assert response.status_code == 200
    assert _drain(response) == b""
