"""Agent E (independent QA): query count and timing of a Teema with a large OneNote page.

Self-contained (no import from the round's own tests) so the same file runs on
the base tree (ddedb499) and on the candidate. Opt-in: ``TL_E_PERF=1``; writes
its measurements to ``TL_E_PERF_OUT``. Synthetic data only.
"""

from __future__ import annotations

import hashlib
import json
import os
import statistics
import time
from datetime import UTC, datetime, timedelta

import pytest
from django.db import connection
from django.test.utils import CaptureQueriesContext
from django.urls import reverse
from django.utils import timezone

from app.documents.models import Document
from app.documents.services import add_evidence_version, create_document
from app.legacy_import.source_pages import (
    LegacySourcePage,
    LegacySourceResource,
    LegacySourceResourceImport,
    MatterSourcePage,
    ResourceImportState,
    SourceMatchClass,
    SourceMatchMethod,
    SourcePageRole,
    SourceRelationshipKind,
    SourceSystem,
)
from tests import factories

pytestmark = [
    pytest.mark.django_db,
    pytest.mark.skipif(os.environ.get("TL_E_PERF") != "1", reason="opt-in measurement"),
]

START = datetime(2024, 1, 3, 9, 0, tzinfo=UTC)


def _block(ordinal, kind, value="", parent=None, key="", when=START):
    stamp = when.strftime("%Y-%m-%dT%H:%M:%S.000Z")
    return {
        "kind": kind,
        "text": value,
        "depth": 1 if parent is None else 2,
        "parent": parent,
        "ordinal": ordinal,
        "outline": 0,
        "resource_key": key,
        "created": stamp,
        "modified": stamp,
        "object_id": f"{{o-{ordinal}}}",
        "source_kind": kind,
    }


def build_page(matter, *, notes, tag):
    blocks, files = [], []
    o = 0
    for n in range(notes):
        day = START + timedelta(days=7 * n)
        start = o
        blocks.append(
            _block(
                o, "TEXT", f"{day:%d.%m.%Y} - Näidismärge {n} ministeeriumi kirja kohta", when=day
            )
        )
        o += 1
        blocks.append(_block(o, "TEXT", f"Selgitav lõik {n}.", when=day))
        o += 1
        for k, parent in ((f"{tag}-{n}-a", start), (f"{tag}-{n}-b", None)):
            blocks.append(_block(o, "FILE_ATTACHMENT", parent=parent, key=k, when=day))
            files.append((k, f"Näidisfail {tag} {n} {k[-1]}.pdf", day, o))
            o += 1
        blocks.append(_block(o, "TEXT", "Arvamused:", when=day))
        o += 1
        blocks.append(_block(o, "TEXT", "Liit A – kommentaarideta", parent=o - 1, when=day))
        o += 1
        blocks.append(_block(o, "BLANK", when=day))
        o += 1
    now = timezone.now()
    page = LegacySourcePage.objects.create(
        source_system=SourceSystem.ONENOTE_DESKTOP,
        source_page_id=f"perf-{tag}",
        page_key=f"perf-{tag}",
        source_notebook="Näidismärkmik",
        source_section="Näidissektsioon",
        title=f"Näidisleht {tag}",
        page_level=1,
        page_order=1,
        page_role=SourcePageRole.MATTER_LIKE,
        role_reason="sünteetiline",
        source_created_at=START,
        source_modified_at=now,
        capture_id="capture-perf",
        source_xml_sha256="0" * 64,
        derived_text="x",
        blocks=blocks,
        links=[],
        reading_order_strategy="VISUAL_THEN_XML",
        text_characters=1,
        block_count=len(blocks),
        file_count=len(files),
        file_bytes=0,
        first_imported_at=now,
        latest_imported_at=now,
    )
    link = MatterSourcePage.objects.create(
        matter=matter,
        source_page=page,
        relationship_kind=SourceRelationshipKind.PRIMARY,
        match_method=SourceMatchMethod.EXCEL_EXACT_PAGE_ID,
        match_class=SourceMatchClass.EXACT,
        source_audit_reference="perf",
    )
    for key, filename, added, ordinal in files:
        content = f"%PDF-1.4 {tag} {key}".encode()
        resource = LegacySourceResource.objects.create(
            source_page=page,
            resource_key=key,
            original_filename=filename,
            resource_kind="FILE_ATTACHMENT",
            source_block_ordinal=ordinal,
            sha256=hashlib.sha256(content).hexdigest(),
            size_bytes=len(content),
            archive_relative_path=f"resources/{key}",
            source_added_at=added,
            source_added_basis="ONENOTE_BLOCK_CREATED",
        )
        document = create_document(matter=matter, title=filename, role="LEGACY_MATERIAL")
        Document.objects.filter(pk=document.pk).update(
            provenance_note=f"OneNote: x\nAllikas: capture-perf, plokk {ordinal}"
        )
        version = add_evidence_version(
            document=document,
            content=content,
            original_filename=filename,
            mime_type="application/pdf",
            source_identifier=f"perf/{tag}/{key}",
        )
        LegacySourceResourceImport.objects.create(
            matter_source_page=link,
            resource=resource,
            document=document,
            document_version=version,
            state=ResourceImportState.IMPORTED,
        )


def _measure(client, url, runs=7):
    client.get(url)
    times, counts = [], []
    for _ in range(runs):
        with CaptureQueriesContext(connection) as queries:
            began = time.perf_counter()
            response = client.get(url)
            times.append((time.perf_counter() - began) * 1000)
        assert response.status_code == 200
        counts.append(len(queries))
    return {"queries": max(counts), "median_ms": round(statistics.median(times), 1)}


@pytest.mark.parametrize(("pages", "notes"), [(1, 60), (3, 60)])
def test_measure(client, specialist, pages, notes):
    matter = factories.MatterFactory(owner=specialist)
    for p in range(pages):
        build_page(matter, notes=notes, tag=f"p{p}")
    for _ in range(30):
        factories.EntryFactory(matter=matter, author=specialist)
    client.force_login(specialist)
    detail = reverse("matters:matter_detail", kwargs={"pk": matter.pk})
    result = {
        "pages": pages,
        "blocks_per_page": notes * 7,
        "teema": _measure(client, detail),
        "teema_whole": _measure(client, detail + "?kaik=koik"),
        "fragment": _measure(client, reverse("matters:timeline_page", kwargs={"pk": matter.pk})),
    }
    out = os.environ.get("TL_E_PERF_OUT")
    if out:
        with open(out, "a", encoding="utf-8") as handle:
            handle.write(json.dumps(result) + "\n")
