"""Imported historical files read as one row per import (docs/adr/0092 §6, amendment 2026-10-19).

An import writes one `EVIDENCE_VERSION_ADDED` per file, with no actor, at the
moment of the import. Read one by one they drew «lisas dokumendi · <import day>»
once per file above a history that runs back years. These tests pin the row
that replaces them: one per import, named as an import, dated by the import,
every file linked to its bytes — and everything else exactly as it was: a
person's upload, the real chronology, the stored events, the permissions.

Synthetic data only: invented page titles, `example.invalid` people.
"""

from __future__ import annotations

import hashlib
import re
from datetime import datetime

import pytest
from django.urls import reverse
from django.utils import timezone

from app.audit.enums import ChangeEventType
from app.audit.models import ChangeEvent
from app.core.dates import format_estonian_date
from app.documents.services import add_evidence_version, create_document
from app.legacy_import.models import ImportBatch, ReconciliationStatus
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
from app.matters.services import add_entry
from app.matters.timeline import (
    HISTORICAL_IMPORT_HEADLINE,
    HISTORICAL_IMPORT_ITEM,
    matter_timeline,
)

pytestmark = pytest.mark.django_db

SECTION = "Näidissektsioon"


def _batch() -> ImportBatch:
    return ImportBatch.objects.create(
        source_system="SYNTHETIC",
        source_file_name="package.json",
        source_snapshot_sha256="0" * 64,
        importer_version="test/1",
        contract_version="test/1",
        started_at=timezone.now(),
        source_row_count=1,
        reconciliation_status=ReconciliationStatus.COMPLETED,
    )


def _import(matter, files, *, page_title="Näidisleht", batch=None, restricted=()):
    """What an importer leaves behind: the page, its files, and the link saying so."""
    now = timezone.now()
    key = f"p-{LegacySourcePage.objects.count() + 1}"
    page = LegacySourcePage.objects.create(
        source_system=SourceSystem.ONENOTE_DESKTOP,
        source_page_id=f"1-{key}",
        page_key=key,
        source_notebook="Näidismärkmik",
        source_section=SECTION,
        title=page_title,
        page_level=1,
        page_order=1,
        page_role=SourcePageRole.MATTER_LIKE,
        role_reason="sünteetiline",
        source_created_at=now,
        source_modified_at=now,
        capture_id="c1",
        source_xml_sha256="0" * 64,
        derived_text=page_title,
        blocks=[],
        links=[],
        reading_order_strategy="VISUAL_THEN_XML",
        text_characters=len(page_title),
        block_count=0,
        file_count=len(files),
        file_bytes=sum(len(content) for _name, content in files),
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
    versions = []
    for ordinal, (name, content) in enumerate(files, start=1):
        resource = LegacySourceResource.objects.create(
            source_page=page,
            resource_key=f"r{ordinal:03d}",
            original_filename=name,
            resource_kind="FILE_ATTACHMENT",
            source_block_ordinal=ordinal,
            sha256=hashlib.sha256(content).hexdigest(),
            size_bytes=len(content),
            archive_relative_path=f"resources/r{ordinal:03d}/original/{name}",
        )
        document = create_document(
            matter=matter,
            title=name,
            role="LEGACY_MATERIAL",
            visibility_override="RESTRICTED" if name in restricted else "",
        )
        version = add_evidence_version(
            document=document,
            content=content,
            original_filename=name,
            mime_type="application/pdf",
            source_identifier=f"{key}/r{ordinal:03d}",
        )
        LegacySourceResourceImport.objects.create(
            matter_source_page=link,
            resource=resource,
            document=document,
            document_version=version,
            state=ResourceImportState.IMPORTED,
            import_batch=batch,
        )
        versions.append(version)
    return versions


FILES = [
    ("Ministeeriumi kiri 2021.pdf", b"%PDF-1.4 kiri"),
    ("Seletuskiri.pdf", b"%PDF-1.4 seletuskiri"),
    ("Liikme vastus.pdf", b"%PDF-1.4 vastus"),
]


def _rows(matter, user, **kwargs):
    items, _more = matter_timeline(matter=matter, user=user, **kwargs)
    return items


def _imports(rows):
    return [row for row in rows if row.item_type == HISTORICAL_IMPORT_ITEM]


def test_one_imports_files_read_as_one_row_named_as_an_import(normal_matter, specialist):
    versions = _import(normal_matter, FILES, page_title="Näidisseaduse eelnõu", batch=_batch())

    rows = _rows(normal_matter, specialist)
    (row,) = _imports(rows)
    assert row.imported_documents == 3
    assert row.milestone.what == HISTORICAL_IMPORT_HEADLINE
    assert row.milestone.sub == f"3 faili · OneNote: {SECTION} → Näidisseaduse eelnõu"
    # Dated by the import, at day precision, like every milestone.
    assert row.milestone.display_date == format_estonian_date(timezone.localdate())
    assert row.is_system and not row.is_primary and not row.removable
    # Every file under it, linked to its exact bytes.
    assert sorted(file.label for file in row.files) == sorted(name for name, _ in FILES)
    assert {file.url for file in row.files} == {
        reverse("documents:download", kwargs={"pk": version.pk}) for version in versions
    }
    # And no «lisas dokumendi» line per file.
    assert not [r for r in rows if r.summary_sentence == "lisas dokumendi"]


def test_a_persons_upload_keeps_its_own_row(normal_matter, specialist, capture_evidence):
    _import(normal_matter, FILES, batch=_batch())
    capture_evidence(
        normal_matter,
        b"%PDF-1.4 uus",
        "Täna lisatud.pdf",
        "application/pdf",
        uploaded_by=specialist,
        created_by=specialist,
    )

    rows = _rows(normal_matter, specialist)
    assert len(_imports(rows)) == 1
    (upload,) = [r for r in rows if r.summary_sentence == "lisas dokumendi"]
    assert upload.actor == specialist
    assert [file.label for file in upload.files] == ["Täna lisatud.pdf"]
    assert upload.imported_documents == 0


def test_two_imports_are_two_rows_and_a_page_stands_in_for_a_missing_batch(
    normal_matter, specialist
):
    _import(normal_matter, FILES[:2], page_title="Esimene leht", batch=_batch())
    _import(normal_matter, FILES[2:], page_title="Teine leht", batch=None)

    rows = _imports(_rows(normal_matter, specialist))
    assert sorted(row.imported_documents for row in rows) == [1, 2]
    assert sorted(row.milestone.sub for row in rows) == [
        f"1 fail · OneNote: {SECTION} → Teine leht",
        f"2 faili · OneNote: {SECTION} → Esimene leht",
    ]


def test_the_real_chronology_keeps_its_own_dates_around_the_import(normal_matter, specialist):
    old = timezone.make_aware(datetime(2021, 8, 31, 12, 0))
    note = add_entry(
        matter=normal_matter, body="<p>Koda saatis arvamuse.</p>", author=None, occurred_at=old
    )
    _import(normal_matter, FILES, batch=_batch())

    rows = _rows(normal_matter, specialist)
    order = [row.item_type for row in rows]
    imported_at = order.index(HISTORICAL_IMPORT_ITEM)
    note_at = next(index for index, row in enumerate(rows) if row.entry == note)
    # Newest first: the import happened today, the note in 2021.
    assert imported_at < note_at
    assert rows[note_at].occurred_at.date().year == 2021


def test_a_page_boundary_never_splits_one_import(normal_matter, specialist):
    batch = _batch()
    _import(normal_matter, FILES, batch=batch)
    for day in (1, 2, 3):
        add_entry(
            matter=normal_matter,
            body=f"<p>Märkus {day}</p>",
            author=specialist,
            occurred_at=timezone.make_aware(datetime(2025, 1, day, 9, 0)),
        )

    seen = []
    offset = 0
    while True:
        page, more = matter_timeline(matter=normal_matter, user=specialist, limit=1, offset=offset)
        seen.extend(page)
        offset += 1
        if not more:
            break
    imports = _imports(seen)
    assert len(imports) == 1 and imports[0].imported_documents == 3
    assert len({row.sort_key for row in seen}) == len(seen)


def test_a_restricted_file_is_neither_counted_nor_linked_for_a_reader_who_may_not_see_it(
    normal_matter, specialist, reader
):
    _import(normal_matter, FILES, batch=_batch(), restricted=("Liikme vastus.pdf",))

    (theirs,) = _imports(_rows(normal_matter, specialist))
    assert theirs.imported_documents == 3 and len(theirs.files) == 3

    (readers,) = _imports(_rows(normal_matter, reader))
    assert readers.imported_documents == 2
    assert readers.milestone.sub.startswith("2 faili")
    assert "Liikme vastus.pdf" not in [file.label for file in readers.files]


def test_reading_the_chronology_changes_no_stored_fact(normal_matter, specialist):
    versions = _import(normal_matter, FILES, batch=_batch())
    before = list(
        ChangeEvent.objects.filter(matter=normal_matter)
        .order_by("id")
        .values_list("id", "event_type", "occurred_at", "actor_id", "object_id")
    )

    _rows(normal_matter, specialist)

    after = list(
        ChangeEvent.objects.filter(matter=normal_matter)
        .order_by("id")
        .values_list("id", "event_type", "occurred_at", "actor_id", "object_id")
    )
    assert before == after
    evidence = [row for row in after if row[1] == ChangeEventType.EVIDENCE_VERSION_ADDED]
    assert len(evidence) == 3 and all(row[3] is None for row in evidence)
    for version in versions:
        version.refresh_from_db()
        assert version.sha256 == hashlib.sha256(dict(FILES)[version.original_filename]).hexdigest()


def test_the_page_draws_one_import_row_and_counts_rows_not_files(
    signed_in, normal_matter, specialist
):
    _import(normal_matter, FILES, page_title="Näidisseaduse eelnõu", batch=_batch())

    html = signed_in.get(
        reverse("matters:matter_detail", kwargs={"pk": normal_matter.pk})
    ).content.decode()
    assert html.count(HISTORICAL_IMPORT_HEADLINE) == 1
    assert "3 faili · OneNote: Näidissektsioon → Näidisseaduse eelnõu" in html
    assert "lisas dokumendi" not in html
    for name, _content in FILES:
        assert name in html
    # The accordion toggle is named by the row's own line.
    assert re.search(r'aria-labelledby="kaik-[^"]+-rida"', html)
    rows = html.count('class="uxtl__item ')
    count = re.search(r"·\s*</span>(\d+)\s+kirje", html) or re.search(r"(\d+)\s+kirje", html)
    assert count is not None and int(count.group(1)) == rows
