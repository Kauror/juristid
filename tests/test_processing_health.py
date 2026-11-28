"""Document-processing health and OneNote coverage diagnostics (R98-14, R98-15, docs/adr/0239)."""

from __future__ import annotations

import datetime as dt
import uuid

import pytest
from django.urls import reverse
from django.utils import timezone

from app.documents import processing_health
from app.documents.enums import ExtractionState
from app.documents.models import DocumentVersion
from app.documents.uploads import SIGNED_CONTAINER_MIME_TYPE
from app.legacy_import import onenote_diagnostics
from tests import factories
from tests.admin_session import apply_admin_gate, full_admin, technical_admin, verified

pytestmark = pytest.mark.django_db


@pytest.fixture(autouse=True)
def _gate(settings):
    return apply_admin_gate(settings)


_NUMBER = iter(range(1, 10_000))


def _version(state: str, *, note: str = "", mime: str = "application/pdf", **extra):
    number = next(_NUMBER)
    return DocumentVersion.objects.create(
        document=factories.DocumentFactory(),
        version_number=1,
        storage_key=f"k/{uuid.uuid4()}",
        original_filename="näidis.pdf",
        mime_type=mime,
        size_bytes=10,
        sha256=f"{number:064x}",
        acquired_at=timezone.now(),
        extraction_state=state,
        extraction_note=note,
        **extra,
    )


def test_states_are_kept_apart_and_never_summed():
    _version(ExtractionState.PENDING)
    _version(ExtractionState.DONE)
    _version(ExtractionState.INTAKE_READ)
    _version(ExtractionState.NOT_APPLICABLE, mime=SIGNED_CONTAINER_MIME_TYPE)
    _version(ExtractionState.NOT_APPLICABLE, mime="application/zip")
    health = processing_health.collect()
    assert health.counts["PENDING"] == 1 and health.counts["DONE"] == 1
    assert health.counts["INTAKE_READ"] == 1
    assert health.counts["FAILED"] == 0  # intentionally unprocessed files are not failures
    assert (health.intentionally_unprocessed, health.unsupported_other) == (1, 1)


def test_failures_are_classified_from_the_isolation_notes():
    _version(
        ExtractionState.FAILED, note="Faili lugemine katkestati, sest see kestis üle 240 sekundi. x"
    )
    _version(ExtractionState.FAILED, note="Faili lugemiseks oleks kulunud üle 2048 MB mälu. x")
    _version(ExtractionState.FAILED, note="Faili lugeja lõpetas töö ootamatult. x")
    _version(ExtractionState.FAILED, note="midagi muud")
    assert processing_health.collect().failures == {
        "timeout": 1,
        "memory_limit": 1,
        "crashed": 1,
        "other": 1,
    }


def test_a_stale_claim_is_interrupted_and_a_fresh_one_is_still_running(settings):
    settings.EXTRACTION_STALE_CLAIM_MINUTES = 30
    now = timezone.now()
    _version(ExtractionState.PROCESSING, extraction_claimed_at=now - dt.timedelta(minutes=5))
    _version(ExtractionState.PROCESSING, extraction_claimed_at=now - dt.timedelta(hours=2))
    health = processing_health.collect(now)
    assert health.counts["PROCESSING"] == 2 and health.interrupted == 1


def test_the_oldest_waiting_file_has_an_age():
    _version(ExtractionState.PENDING)
    health = processing_health.collect(timezone.now() + dt.timedelta(days=2))
    assert health.oldest_waiting_age is not None and health.oldest_waiting_age.days == 2


def test_an_unreachable_parser_is_unknown_and_never_shows_a_path(settings):
    settings.DOCUMENT_PARSER_SOCKET = "/run/does-not-exist/parser.sock"
    mark = processing_health._parser_socket("DOCUMENT_PARSER_SOCKET")
    assert mark.state == "unknown" and "/run" not in mark.line


def test_onenote_counts_use_the_owners_distinctions_and_never_claim_the_archive():
    result = onenote_diagnostics.diagnostics()
    assert set(result["rows"]) == {
        "imported",
        "captured_not_applied",
        "awaiting_capture",
        "held",
        "changed_since_capture",
        "failed",
    }
    assert result["archive_complete"] is False and result["never_inventoried"] == "unknown"


def test_the_page_is_for_the_technical_administrator_only(client):
    assert client.get(reverse("haldus:processing")).status_code in (302, 404)
    tech, secret = technical_admin()
    full_admin()
    verified(client, tech, secret)
    _version(ExtractionState.PENDING)
    page = client.get(reverse("haldus:processing"))
    body = page.content.decode()
    assert page.status_code == 200
    assert "Katvus teadmata" in body and "näidis.pdf" not in body  # no file names
