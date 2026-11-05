"""The OneNote notes cost a bounded number of queries, however many pages a Teema links.

A synthetic Teema with 1, 3 and 10 linked OneNote pages of 60 blocks each (ten
dated notes, continuation lines, files inside and beside the notes, blanks and
an undated «Järgmised tegevused» section). The chronology, the Teema page and
the chronology fragment are each measured with `CaptureQueriesContext`: the
count must not grow with the pages, the notes or the files (docs/adr/0092 §10).

Self-contained on purpose — it imports nothing this round added — so the same
file measures the tree before the change too. ``TL_B_MEASURE`` names a file to
append the numbers to.

Synthetic data only: invented text, invented filenames.
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
from app.matters.timeline import matter_timeline
from tests import factories

pytestmark = pytest.mark.django_db

NOTES_PER_PAGE = 10


def _block(ordinal, kind, text="", *, depth=1, parent=None, key="", when="2024-01-01"):
    return {
        "kind": kind,
        "text": text,
        "depth": depth,
        "parent": parent,
        "ordinal": ordinal,
        "outline": 0,
        "resource_key": key,
        "created": f"{when}T08:00:00.000Z",
        "modified": f"{when}T08:00:00.000Z",
        "object_id": f"{{o-{ordinal}}}",
        "source_kind": kind,
    }


def _page(matter, index):
    """One page: ten dated notes of six blocks each, files inside and beside them."""
    blocks, files = [], []
    ordinal = 0
    start = datetime(2024, 1, 1, 8, 0, tzinfo=UTC) + timedelta(days=40 * index)
    for note in range(NOTES_PER_PAGE):
        day = start + timedelta(days=3 * note)
        head = ordinal
        blocks.append(
            _block(
                ordinal,
                "TEXT",
                f"{day:%d.%m.%Y} - Näidismärge {index}-{note}: ministeerium saatis "
                "kooskõlastamiseks eelnõu, mille kohta koda kogus liikmete arvamusi",
                when=f"{day:%Y-%m-%d}",
            )
        )
        ordinal += 1
        blocks.append(_block(ordinal, "TEXT", "Teine rida samast märkest."))
        ordinal += 1
        inside = f"in-{note}"
        blocks.append(_block(ordinal, "FILE_ATTACHMENT", depth=2, parent=head, key=inside))
        files.append((inside, f"Kiri {index}-{note}.pdf", day))
        ordinal += 1
        blocks.append(_block(ordinal, "BLANK"))
        ordinal += 1
        beside = f"out-{note}"
        blocks.append(_block(ordinal, "FILE_ATTACHMENT", key=beside))
        files.append((beside, f"Lisa {index}-{note}.pdf", day + timedelta(hours=1)))
        ordinal += 1
        blocks.append(_block(ordinal, "BLANK"))
        ordinal += 1
    heading = ordinal
    blocks.append(_block(ordinal, "TEXT", "Järgmised tegevused:"))
    ordinal += 1
    for step in range(9):
        blocks.append(_block(ordinal, "TEXT", f"samm {step}", depth=2, parent=heading))
        ordinal += 1
    assert len(blocks) >= 50

    now = timezone.now()
    key = f"q-{index}-{now.timestamp()}"
    page = LegacySourcePage.objects.create(
        source_system=SourceSystem.ONENOTE_DESKTOP,
        source_page_id=f"1-{key}",
        page_key=key,
        source_notebook="Näidismärkmik",
        source_section="Näidissektsioon",
        title=f"Näidisleht {index}",
        page_level=1,
        page_order=index,
        page_role=SourcePageRole.MATTER_LIKE,
        role_reason="sünteetiline",
        source_created_at=start - timedelta(days=1),
        source_modified_at=now,
        capture_id="capture-naidis",
        source_xml_sha256="0" * 64,
        derived_text="",
        blocks=blocks,
        links=[],
        reading_order_strategy="VISUAL_THEN_XML",
        text_characters=0,
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
        source_audit_reference="test",
    )
    ordinals = {
        block["resource_key"]: block["ordinal"] for block in blocks if block["resource_key"]
    }
    for resource_key, filename, added in files:
        content = f"%PDF-1.4 {key} {resource_key}".encode()
        resource = LegacySourceResource.objects.create(
            source_page=page,
            resource_key=resource_key,
            original_filename=filename,
            resource_kind="FILE_ATTACHMENT",
            source_block_ordinal=ordinals[resource_key],
            sha256=hashlib.sha256(content).hexdigest(),
            size_bytes=len(content),
            archive_relative_path=f"r/{resource_key}",
            source_added_at=added,
            source_added_basis="ONENOTE_BLOCK_CREATED",
        )
        document = create_document(matter=matter, title=filename, role="LEGACY_MATERIAL")
        version = add_evidence_version(
            document=document,
            content=content,
            original_filename=filename,
            mime_type="application/pdf",
            source_identifier=f"{key}/{resource_key}",
        )
        LegacySourceResourceImport.objects.create(
            matter_source_page=link,
            resource=resource,
            document=document,
            document_version=version,
            state=ResourceImportState.IMPORTED,
        )


def _measure(callable_, runs=3):
    counts, times = [], []
    for _ in range(runs):
        with CaptureQueriesContext(connection) as queries:
            started = time.perf_counter()
            callable_()
            times.append(time.perf_counter() - started)
        counts.append(len(queries))
    return counts[-1], statistics.median(times) * 1000


@pytest.mark.parametrize("pages", [1, 3, 10])
def test_the_notes_cost_no_query_per_page_note_or_file(client, specialist, pages):
    matter = factories.MatterFactory(owner=specialist)
    for index in range(pages):
        _page(matter, index)
    client.force_login(specialist)
    detail = reverse("matters:matter_detail", kwargs={"pk": matter.pk})
    fragment = reverse("matters:timeline_page", kwargs={"pk": matter.pk}) + "?nihe=10"

    def chronology():
        matter_timeline(matter=matter, user=specialist)

    def page():
        assert client.get(detail).status_code == 200

    def more():
        assert client.get(fragment).status_code == 200

    numbers = {
        "pages": pages,
        "blocks": pages * (NOTES_PER_PAGE * 6 + 10),
        "chronology": _measure(chronology),
        "teema_page": _measure(page),
        "fragment": _measure(more),
    }
    out = os.environ.get("TL_B_MEASURE")
    if out:
        with open(out, "a", encoding="utf-8") as handle:
            handle.write(json.dumps(numbers) + "\n")
    # The query count is the same at 1, 3 and 10 pages: recorded by the first
    # parameter, compared by the later ones.
    seen = test_the_notes_cost_no_query_per_page_note_or_file.__dict__.setdefault("seen", {})
    for name in ("chronology", "teema_page", "fragment"):
        count = numbers[name][0]
        first = seen.setdefault(name, count)
        assert count == first, (name, pages, count, first)
