"""HEAD on a route that serves stored bytes is not a download (QA-07, 2026-11-05).

The independent continuation audit found that a HEAD on every byte route ran the
GET: it opened the file, wrote a ``DOCUMENT_DOWNLOADED`` row and, for a held
OneNote original, hashed the whole file — for a request that delivers nothing.

The rule, the same on every byte route:

* **GET** is unchanged: the bytes, one audit row, every check.
* **HEAD** passes the same door and gets the same status and the same headers
  (``Content-Disposition``, ``Content-Type``, ``nosniff``, the CSP and
  ``Cache-Control`` GET sends, ``Content-Length`` from what is recorded), with
  no body. It reads no file content, hashes nothing and writes no audit row.
* Any other method is a 405 with ``Allow: GET, HEAD`` and no audit row.

Written from that rule, not from the code. Synthetic data only.
"""

from __future__ import annotations

import hashlib
import uuid
from collections.abc import Callable
from types import SimpleNamespace
from typing import Any

import pytest
from django.core.files.base import ContentFile
from django.core.files.storage import FileSystemStorage
from django.test import Client
from django.urls import reverse

from app.audit.enums import SecurityEventType
from app.audit.models import SecurityAuditEvent
from app.legacy_import import source_originals
from tests import factories
from tests.test_download_methods import _source_page_link, _thumbnail
from tests.test_onenote_shared_dossiers import (  # noqa: F401  (fixtures)
    _fresh_package_cache,
    _link,
    _original_url,
    _resource,
    enabled,
    world,
)
from tests.test_opinion_archive_search import hold

pytestmark = pytest.mark.django_db

PDF = b"%PDF-1.4\npa-c-head-7701\n"
LETTER = b"%PDF-1.4 pa-c archive letter 7702"
HELD = b"%PDF-1.4 kiri"  # what `world` puts behind page 001's kiri.pdf

#: The headers a HEAD must send exactly as its GET does, where GET sends them.
COMPARED = (
    "Content-Type",
    "Content-Length",
    "Content-Disposition",
    "X-Content-Type-Options",
    "Content-Security-Policy",
    "Referrer-Policy",
    "Cache-Control",
    "Pragma",
)

#: route -> GET writes one DOCUMENT_DOWNLOADED row (a thumbnail is a regenerable
#: derivative and has never been audited).
AUDITED = {
    "download": True,
    "open": True,
    "thumbnail": False,
    "archive_inline": True,
    "archive_attachment": True,
    "held_original": True,
    "source_xml": True,
}

#: `source_xml` lives in `app/legacy_import/historical_views.py`, which this
#: round's agent C may not edit; the lead integrates the same change there.
SOURCE_XML_PENDING = pytest.mark.xfail(
    strict=True, reason="QA-07: lead applies the source_xml HEAD patch in historical_views.py"
)
#: Every byte route, for the checks that hold on all of them already.
ALL_ROUTES = list(AUDITED)
#: The same, with `source_xml` expected to fail until the lead's patch lands.
ROUTES = [
    pytest.param(name, marks=SOURCE_XML_PENDING) if name == "source_xml" else name
    for name in ALL_ROUTES
]


def _downloads() -> int:
    return SecurityAuditEvent.objects.filter(
        event_type=SecurityEventType.DOCUMENT_DOWNLOADED
    ).count()


def _drain(response: Any) -> bytes:
    if getattr(response, "streaming", False):
        return b"".join(response.streaming_content)
    return bytes(response.content)


@pytest.fixture
def routes(world, capture_evidence, specialist) -> dict[str, dict[str, Any]]:  # noqa: F811
    """Every byte route, with the bytes its GET must serve."""
    matter = factories.MatterFactory(owner=specialist)
    version = capture_evidence(matter, PDF, "kiri õigus.pdf", "application/pdf")
    thumb = _thumbnail(version)
    letter = hold(sha=hashlib.sha256(LETTER).hexdigest())
    from app.documents.services import evidence_storage

    evidence_storage().save(letter.storage_key, ContentFile(LETTER))
    type(letter).objects.filter(pk=letter.pk).update(size_bytes=len(LETTER))
    archive = reverse("legacy_import:opinion_archive_file", kwargs={"pk": letter.pk})
    link = _source_page_link(matter)
    return {
        "download": {
            "url": reverse("documents:download", kwargs={"pk": version.pk}),
            "bytes": PDF,
        },
        "open": {"url": reverse("documents:open", kwargs={"pk": version.pk}), "bytes": PDF},
        "thumbnail": {
            "url": reverse("documents:thumbnail", kwargs={"pk": thumb.pk}),
            "bytes": b"\x89PNG\r\n\x1a\nmethods-6612",
        },
        "archive_inline": {"url": archive, "bytes": LETTER},
        "archive_attachment": {"url": archive + "?laadi=alla", "bytes": LETTER},
        "held_original": {
            "url": _original_url(_link(world["a"], 1), _resource(1, "kiri.pdf")),
            "bytes": HELD,
        },
        "source_xml": {
            "url": reverse("legacy_import:source_xml", kwargs={"pk": link.pk}),
            "bytes": b"<one:Page><one:T>LAHTE-6612</one:T></one:Page>",
        },
    }


@pytest.fixture
def reading(client, specialist) -> Client:
    """A business reader: every route here admits a specialist (archive included)."""
    client.force_login(specialist)
    return client


# -- GET is unchanged ----------------------------------------------------------------


@pytest.mark.parametrize("route", ALL_ROUTES)
def test_get_serves_the_stored_bytes_and_is_audited_once(routes, reading, route):
    before = _downloads()

    response = reading.get(routes[route]["url"])

    assert response.status_code == 200, route
    assert _drain(response) == routes[route]["bytes"]
    assert _downloads() - before == (1 if AUDITED[route] else 0)


# -- HEAD: GET's status and headers, nothing read, nothing audited -------------------


@pytest.mark.parametrize("route", ROUTES)
def test_head_is_not_audited_and_sends_gets_headers_with_no_body(routes, reading, route):
    url = routes[route]["url"]
    before = _downloads()

    head = reading.head(url)

    assert _downloads() == before, "a HEAD wrote a DOCUMENT_DOWNLOADED row"
    assert head.status_code == 200
    assert _drain(head) == b""

    get = reading.get(url)
    assert get.status_code == 200
    for header in COMPARED:
        assert head.get(header) == get.get(header), (route, header)
    assert head["Content-Length"] == str(len(routes[route]["bytes"]))
    assert head["X-Content-Type-Options"] == "nosniff"
    assert head["Content-Disposition"].split(";")[0] in {"attachment", "inline"}


@pytest.mark.parametrize("route", ROUTES)
def test_head_opens_no_file_content_and_hashes_nothing(routes, reading, route, monkeypatch):
    """Every way a byte route reads content is booby-trapped for the HEAD."""

    def refuse(*_args: Any, **_kwargs: Any) -> Any:
        raise AssertionError("HEAD read stored content")

    monkeypatch.setattr(FileSystemStorage, "open", refuse)
    monkeypatch.setattr(FileSystemStorage, "_open", refuse)
    monkeypatch.setattr(source_originals, "open_original", refuse)
    monkeypatch.setattr(source_originals, "find_package", refuse)
    monkeypatch.setattr(source_originals, "_sha256_of", refuse)

    response = reading.head(routes[route]["url"])

    assert response.status_code == 200
    assert _drain(response) == b""


def test_a_held_original_head_does_not_hash_the_file(routes, reading, monkeypatch):
    """The audit's exact finding: a HEAD on a held original hashed it in full."""
    calls: list[str] = []
    real = hashlib.sha256

    def counting(*args: Any, **kwargs: Any) -> Any:
        calls.append("sha256")
        return real(*args, **kwargs)

    # The module's own `hashlib` only: Django's session and CSRF code hash too.
    monkeypatch.setattr(source_originals, "hashlib", SimpleNamespace(sha256=counting))
    assert reading.head(routes["held_original"]["url"]).status_code == 200
    assert calls == []
    assert reading.get(routes["held_original"]["url"]).status_code == 200
    assert calls, "the GET still proves the original before serving it"


def test_the_pinned_evidence_download_head_no_longer_audits(routes, reading):
    """The intentional reversal of the 9eff0e9a pin (see the round report)."""
    reading.head(routes["download"]["url"])
    reading.head(routes["download"]["url"])
    assert _downloads() == 0
    reading.get(routes["download"]["url"])
    assert _downloads() == 1


# -- refusals: the same answer to HEAD as to GET, never a body or a row ---------------


@pytest.mark.parametrize("route", ALL_ROUTES)
@pytest.mark.parametrize("method", ["post", "put", "patch", "delete", "options"])
def test_any_other_method_is_405_with_allow_get_head_and_no_row(routes, reading, route, method):
    before = SecurityAuditEvent.objects.count()

    response = getattr(reading, method)(routes[route]["url"])

    assert response.status_code == 405, (route, method)
    assert set(response["Allow"].replace(" ", "").split(",")) == {"GET", "HEAD"}
    assert routes[route]["bytes"] not in _drain(response)
    assert SecurityAuditEvent.objects.count() == before


@pytest.mark.parametrize("route", ALL_ROUTES)
def test_an_ungranted_administrator_gets_the_same_refusal_to_head_as_to_get(
    routes, client, administrator, route
):
    client.force_login(administrator)
    url = routes[route]["url"]
    before = SecurityAuditEvent.objects.filter(
        event_type=SecurityEventType.DOCUMENT_DOWNLOADED
    ).count()

    get, head = client.get(url), client.head(url)

    assert get.status_code in (403, 404), route
    assert head.status_code == get.status_code, route
    assert _drain(head) == b""
    assert routes[route]["bytes"] not in _drain(get)
    assert _downloads() == before


@pytest.mark.parametrize("route", ALL_ROUTES)
def test_anonymous_head_is_sent_to_sign_in_like_get(routes, route):
    url = routes[route]["url"]
    get, head = Client().get(url), Client().head(url)
    assert get.status_code == head.status_code == 302
    assert head["Location"] == get["Location"]
    assert _drain(head) == b""
    assert _downloads() == 0


def _ids_in(url: str) -> list[str]:
    parts = [p for p in url.split("?")[0].split("/") if p]
    out = []
    for part in parts:
        try:
            uuid.UUID(part)
        except ValueError:
            continue
        out.append(part)
    return out


@pytest.mark.parametrize("route", ALL_ROUTES)
@pytest.mark.parametrize(
    "replace", [lambda _id: str(uuid.uuid4()), lambda _id: "not-a-uuid", lambda _id: _id[:-1]]
)
def test_an_unknown_or_malformed_id_is_404_to_head_and_get(
    routes, reading, route, replace: Callable[[str], str]
):
    url = routes[route]["url"]
    for identifier in _ids_in(url):
        bad = url.replace(identifier, replace(identifier))
        get, head = reading.get(bad), reading.head(bad)
        assert get.status_code == head.status_code == 404, (route, bad)
        assert _drain(head) == b""
    assert _downloads() == 0


@pytest.mark.parametrize(
    "route", ["download", "open", "thumbnail", "archive_inline", "archive_attachment"]
)
def test_a_missing_stored_object_is_404_to_head_too_without_an_audit_row(
    routes, reading, route, settings
):
    """GET answers a vanished object with 404 (and, for evidence, a *failed* row).
    HEAD gives the same 404, asked of the directory entry, and writes nothing."""
    from pathlib import Path

    for root in (settings.EVIDENCE_ROOT, settings.DERIVATIVE_ROOT):
        for path in Path(root).rglob("*"):
            if path.is_file():
                path.unlink()
    before = SecurityAuditEvent.objects.count()

    head = reading.head(routes[route]["url"])

    assert head.status_code == 404
    assert _drain(head) == b""
    assert SecurityAuditEvent.objects.count() == before
    assert reading.get(routes[route]["url"]).status_code == 404
