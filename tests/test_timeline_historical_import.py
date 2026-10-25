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
from datetime import UTC, datetime

import pytest
from django.urls import reverse
from django.utils import timezone

from app.audit.enums import ChangeEventType
from app.audit.models import ChangeEvent
from app.core.dates import format_estonian_date
from app.documents.inline import file_link
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
    HISTORICAL_IMPORT_ITEM,
    ONENOTE_ADDED_ONE,
    ONENOTE_PREDATES_PAGE,
    ONENOTE_UNDATED_DISPLAY,
    ONENOTE_UNDATED_ONE,
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


#: When a synthetic page was created in OneNote: before every file these tests
#: date, so a file reads as added to the page it is on unless a test says not.
PAGE_CREATED = datetime(2015, 1, 5, 9, 0, tzinfo=UTC)


def _import(
    matter,
    files,
    *,
    page_title="Näidisleht",
    batch=None,
    restricted=(),
    added=None,
    page_created=PAGE_CREATED,
):
    """What an importer leaves behind: the page, its files, and the link saying so.

    ``added`` maps a filename to the UTC moment the source says it was added to
    OneNote; a file not named there has no source time, like a floating object.
    ``page_created`` is the page's own OneNote creation time (``None``: unknown).
    """
    added = added or {}
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
        source_created_at=page_created,
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
            source_added_at=added.get(name),
            source_added_basis="ONENOTE_BLOCK_CREATED" if name in added else "",
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


def _utc(*parts) -> datetime:
    return datetime(*parts, tzinfo=UTC)


#: Three files added to OneNote on one Tallinn day, 30.8.2021.
SAME_DAY = {
    "Ministeeriumi kiri 2021.pdf": _utc(2021, 8, 30, 8, 48, 10),
    "Seletuskiri.pdf": _utc(2021, 8, 30, 8, 48, 22),
    "Liikme vastus.pdf": _utc(2021, 8, 30, 10, 10, 24),
}


def _ids(rows) -> list:
    return [str(event.object_id) for row in rows for event in row.events]


# ---------------------------------------------------------------------------
# A. and B. — the day a file was added to OneNote
# ---------------------------------------------------------------------------


def test_one_file_reads_on_the_day_it_was_added_to_onenote(normal_matter, specialist):
    (version,) = _import(
        normal_matter,
        FILES[:1],
        page_title="Näidisseaduse eelnõu",
        added={FILES[0][0]: _utc(2021, 8, 30, 8, 48, 10)},
    )
    (row,) = _imports(_rows(normal_matter, specialist))
    assert row.milestone.what == ONENOTE_ADDED_ONE == "Dokument lisatud OneNote'i"
    assert row.milestone.display_date == "30.8.2021"
    assert row.milestone.sub == f"1 fail · OneNote: {SECTION} → Näidisseaduse eelnõu"
    # Sorted at the moment the source names, not at the import.
    assert row.occurred_at == _utc(2021, 8, 30, 8, 48, 10)
    # The exact bytes, by the one open-or-download rule (app/documents/inline.py).
    assert [file.url for file in row.files] == [file_link(version).href]
    # Never drawn as a person's upload.
    assert row.actor is None and row.is_system and not row.removable


def test_files_added_on_one_day_are_one_row_that_opens_onto_each(normal_matter, specialist):
    versions = _import(normal_matter, FILES, added=SAME_DAY)
    rows = _rows(normal_matter, specialist)
    (row,) = _imports(rows)
    assert row.milestone.what == "OneNote'i lisati 3 dokumenti"
    assert row.milestone.display_date == "30.8.2021"
    assert row.imported_documents == 3
    assert row.occurred_at == min(SAME_DAY.values())
    assert sorted(file.label for file in row.files) == sorted(name for name, _ in FILES)
    assert {file.url for file in row.files} == {file_link(version).href for version in versions}
    assert not [r for r in rows if r.summary_sentence == "lisas dokumendi"]


def test_different_days_on_one_page_are_different_rows(normal_matter, specialist):
    _import(
        normal_matter,
        FILES,
        added={
            FILES[0][0]: _utc(2021, 8, 30, 8, 0),
            FILES[1][0]: _utc(2021, 8, 31, 12, 0),
            FILES[2][0]: _utc(2024, 7, 17, 9, 0),
        },
    )
    rows = _imports(_rows(normal_matter, specialist))
    assert [row.milestone.display_date for row in rows] == ["17.7.2024", "31.8.2021", "30.8.2021"]
    assert all(row.imported_documents == 1 for row in rows)


def test_the_day_is_the_tallinn_day_not_the_utc_one(normal_matter, specialist):
    """22:30 UTC on 30 August is 01:30 on 31 August in Tallinn (EEST, UTC+3)."""
    _import(
        normal_matter,
        FILES[:2],
        added={
            FILES[0][0]: _utc(2021, 8, 30, 22, 30),
            FILES[1][0]: _utc(2021, 8, 31, 9, 0),
        },
    )
    (row,) = _imports(_rows(normal_matter, specialist))
    assert row.milestone.display_date == "31.8.2021"
    assert row.imported_documents == 2


@pytest.mark.parametrize(
    ("moment", "day"),
    [
        # The night summer time ends (31.10.2021 04:00 EEST → 03:00 EET).
        (_utc(2021, 10, 30, 21, 30), "31.10.2021"),  # 00:30 EEST
        (_utc(2021, 10, 31, 21, 30), "31.10.2021"),  # 23:30 EET
        # The night it begins (28.3.2021 03:00 EET → 04:00 EEST).
        (_utc(2021, 3, 27, 22, 30), "28.3.2021"),  # 00:30 EET
        (_utc(2021, 3, 28, 20, 59), "28.3.2021"),  # 23:59 EEST
        (_utc(2021, 3, 28, 21, 0), "29.3.2021"),  # 00:00 EEST
    ],
)
def test_daylight_saving_changes_keep_the_local_calendar_day(
    normal_matter, specialist, moment, day
):
    _import(normal_matter, FILES[:1], added={FILES[0][0]: moment})
    (row,) = _imports(_rows(normal_matter, specialist))
    assert row.milestone.display_date == day


# ---------------------------------------------------------------------------
# D. — no source time, no invented day
# ---------------------------------------------------------------------------


def test_a_file_with_no_source_time_claims_no_day(normal_matter, specialist):
    _import(normal_matter, FILES[:1])
    (row,) = _imports(_rows(normal_matter, specialist))
    assert row.milestone.what == ONENOTE_UNDATED_ONE
    assert row.milestone.display_date == ONENOTE_UNDATED_DISPLAY == "kuupäev teadmata"
    # The import's own day is not printed anywhere on the row.
    assert format_estonian_date(timezone.localdate()) not in (
        row.milestone.display_date + row.milestone.sub
    )


def test_dated_and_undated_files_of_one_page_part_honestly(normal_matter, specialist):
    _import(normal_matter, FILES, added={FILES[0][0]: _utc(2021, 8, 30, 8, 0)})
    rows = _imports(_rows(normal_matter, specialist))
    assert sorted(row.milestone.what for row in rows) == sorted(
        ["Dokument lisatud OneNote'i", "OneNote'i dokumendid (2) — lisamise kuupäev teadmata"]
    )


# ---------------------------------------------------------------------------
# C. — a file that supports a record, and every other row
# ---------------------------------------------------------------------------


def test_a_file_on_a_record_is_not_a_second_row_and_keeps_its_own_time(
    signed_in, normal_matter, specialist
):
    from app.documents.models import DocumentLink
    from app.matters.models import MatterEngagement

    versions = _import(normal_matter, FILES[:2], added=SAME_DAY)
    round_ = MatterEngagement.objects.create(
        matter=normal_matter, title="Liikmete kaasamine", occurred_on=datetime(2021, 8, 30).date()
    )
    DocumentLink.objects.create(document=versions[0].document, engagement=round_)

    (row,) = _imports(_rows(normal_matter, specialist))
    assert row.imported_documents == 1
    assert [file.label for file in row.files] == [FILES[1][0]]
    # The supporting file still states when it reached OneNote, apart from the
    # day of the round it supports.
    html = signed_in.get(
        reverse("documents:document_detail", kwargs={"pk": versions[0].document.pk})
    ).content.decode()
    assert "Lisatud OneNote'i" in html and "30.8.2021 11:48" in html


def test_an_undated_file_says_so_on_its_own_page(signed_in, normal_matter):
    (version,) = _import(normal_matter, FILES[:1])
    html = signed_in.get(
        reverse("documents:document_detail", kwargs={"pk": version.document.pk})
    ).content.decode()
    assert "Lisatud OneNote'i" in html and "kuupäev teadmata" in html


def test_a_persons_upload_keeps_its_own_row(normal_matter, specialist, capture_evidence):
    _import(normal_matter, FILES, added=SAME_DAY)
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


def test_old_and_new_work_sort_together_by_when_it_happened(normal_matter, specialist):
    before = add_entry(
        matter=normal_matter,
        body="<p>Enne.</p>",
        author=None,
        occurred_at=timezone.make_aware(datetime(2021, 8, 29, 12, 0)),
    )
    after = add_entry(
        matter=normal_matter,
        body="<p>Pärast.</p>",
        author=None,
        occurred_at=timezone.make_aware(datetime(2021, 9, 1, 12, 0)),
    )
    _import(normal_matter, FILES, added=SAME_DAY)
    rows = _rows(normal_matter, specialist)
    position = {id(row): index for index, row in enumerate(rows)}
    (imported,) = _imports(rows)
    by_entry = {row.entry: row for row in rows if row.entry is not None}
    # Newest first: 1.9 note, 30.8 OneNote row, 29.8 note — not the import day.
    assert position[id(by_entry[after])] < position[id(imported)] < position[id(by_entry[before])]


# ---------------------------------------------------------------------------
# Duplicates, permissions, paging, idempotency
# ---------------------------------------------------------------------------


def test_each_file_is_on_exactly_one_row_even_with_identical_bytes(normal_matter, specialist):
    twins = [("Koopia A.pdf", b"%PDF-1.4 sama"), ("Koopia B.pdf", b"%PDF-1.4 sama")]
    versions = _import(
        normal_matter,
        twins,
        added={
            "Koopia A.pdf": _utc(2021, 8, 30, 8, 0),
            "Koopia B.pdf": _utc(2021, 8, 30, 9, 0),
        },
    )
    rows = _imports(_rows(normal_matter, specialist))
    assert sorted(_ids(rows)) == sorted(str(v.pk) for v in versions)
    assert len(_ids(rows)) == len(set(_ids(rows)))


def test_a_restricted_file_is_neither_counted_nor_linked_nor_dates_a_row(
    normal_matter, specialist, reader, administrator
):
    # docs/adr/0181: a READER reads the restricted file and gets the owner's rows;
    # an administrator without a grant gets no import row at all.
    _import(
        normal_matter,
        FILES,
        added={**SAME_DAY, "Liikme vastus.pdf": _utc(2019, 1, 2, 10, 0)},
        restricted=("Liikme vastus.pdf",),
    )
    theirs = _imports(_rows(normal_matter, specialist))
    assert sorted(row.milestone.display_date for row in theirs) == ["2.1.2019", "30.8.2021"]

    readers = _imports(_rows(normal_matter, reader))
    assert sorted(row.milestone.display_date for row in readers) == ["2.1.2019", "30.8.2021"]
    assert sorted(row.imported_documents for row in readers) == sorted(
        row.imported_documents for row in theirs
    )
    assert "Liikme vastus.pdf" in [file.label for row in readers for file in row.files]

    outsiders = _imports(_rows(normal_matter, administrator))
    assert outsiders == []


def test_a_page_boundary_never_splits_a_day_and_drops_nothing(normal_matter, specialist):
    _import(
        normal_matter,
        FILES,
        added={
            FILES[0][0]: _utc(2021, 8, 30, 8, 0),
            FILES[1][0]: _utc(2021, 8, 30, 9, 0),
            FILES[2][0]: _utc(2023, 5, 4, 9, 0),
        },
    )
    _import(normal_matter, [("Dateerimata.pdf", b"%PDF-1.4 d")], page_title="Teine leht")
    for day in (1, 2, 3):
        add_entry(
            matter=normal_matter,
            body=f"<p>Märkus {day}</p>",
            author=specialist,
            occurred_at=timezone.make_aware(datetime(2022, 1, day, 9, 0)),
        )
    whole = _rows(normal_matter, specialist)

    seen = []
    offset = 0
    while True:
        page, more = matter_timeline(matter=normal_matter, user=specialist, limit=1, offset=offset)
        seen.extend(page)
        offset += 1
        if not more:
            break
    assert [row.sort_key for row in seen] == [row.sort_key for row in whole]
    imports = _imports(seen)
    assert sorted(row.imported_documents for row in imports) == [1, 1, 2]
    assert len({row.sort_key for row in seen}) == len(seen)


def test_cataloguing_again_fills_only_a_missing_time(normal_matter):
    from app.legacy_import.historical_apply import _catalogue_resources
    from app.legacy_import.onenote_archive import ArchiveResource

    _import(normal_matter, FILES[:2], added={FILES[0][0]: _utc(2021, 8, 30, 8, 0)})
    page = LegacySourcePage.objects.get()

    class _Page:
        resources = [
            ArchiveResource(
                resource_key=f"r{ordinal:03d}",
                original_filename=name,
                resource_kind="FILE_ATTACHMENT",
                source_block_ordinal=ordinal,
                sha256="",
                size_bytes=0,
                relative_path="",
                is_inline=False,
                download_status="CAPTURED",
                source_added_at=_utc(2030, 1, 1, 0, 0),
            )
            for ordinal, (name, _content) in enumerate(FILES[:2], start=1)
        ]

    assert _catalogue_resources(page, _Page()) == 0  # no new rows
    times = dict(LegacySourceResource.objects.values_list("resource_key", "source_added_at"))
    # A recorded time is never corrected by a later reading; a missing one is filled.
    assert times["r001"] == _utc(2021, 8, 30, 8, 0)
    assert times["r002"] == _utc(2030, 1, 1, 0, 0)
    assert _catalogue_resources(page, _Page()) == 0
    assert (
        dict(LegacySourceResource.objects.values_list("resource_key", "source_added_at")) == times
    )


def test_reading_the_chronology_changes_no_stored_fact(normal_matter, specialist):
    versions = _import(normal_matter, FILES, added=SAME_DAY)
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
    # The audit keeps the import's own moment; only the reading is historical.
    assert len(evidence) == 3 and all(row[3] is None for row in evidence)
    assert all(row[2].year == timezone.now().year for row in evidence)
    for version in versions:
        version.refresh_from_db()
        assert version.sha256 == hashlib.sha256(dict(FILES)[version.original_filename]).hexdigest()


def test_the_page_draws_each_day_once_and_counts_rows_not_files(
    signed_in, normal_matter, specialist
):
    _import(normal_matter, FILES, page_title="Näidisseaduse eelnõu", added=SAME_DAY)

    html = signed_in.get(
        reverse("matters:matter_detail", kwargs={"pk": normal_matter.pk})
    ).content.decode()
    assert (
        html.count("OneNote&#x27;i lisati 3 dokumenti") + html.count("OneNote'i lisati 3 dokumenti")
        == 1
    )
    assert "3 faili · OneNote: Näidissektsioon → Näidisseaduse eelnõu" in html
    assert "lisas dokumendi" not in html
    for name, _content in FILES:
        assert name in html
    assert re.search(r'aria-labelledby="kaik-[^"]+-rida"', html)
    rows = html.count('class="uxtl__item ')
    count = re.search(r"·\s*</span>(\d+)\s+kirje", html) or re.search(r"(\d+)\s+kirje", html)
    assert count is not None and int(count.group(1)) == rows


# ---------------------------------------------------------------------------
# A file older than the page it is on now (MOVED_CONTENT)
# ---------------------------------------------------------------------------
#
# OneNote keeps an element's creation time when content is moved or copied in
# from another page, so nine pilot files carry a time older than their page.
# The day is still the day the file reached OneNote; the row must not imply it
# reached *this* page then.


def test_a_file_older_than_its_page_says_so_and_keeps_its_source_day(normal_matter, specialist):
    _import(
        normal_matter,
        FILES[:1],
        page_title="Näidisseaduse eelnõu",
        added={FILES[0][0]: _utc(2021, 8, 30, 8, 48, 10)},
        page_created=_utc(2026, 2, 1, 9, 0),
    )
    (row,) = _imports(_rows(normal_matter, specialist))
    assert row.milestone.what == ONENOTE_ADDED_ONE
    assert row.milestone.display_date == "30.8.2021"
    assert row.occurred_at == _utc(2021, 8, 30, 8, 48, 10)
    assert ONENOTE_PREDATES_PAGE == "lisatud enne praeguse lehe loomist"
    assert row.milestone.sub == (
        f"1 fail · OneNote: {SECTION} → Näidisseaduse eelnõu · lisatud enne praeguse lehe loomist"
    )


def test_a_file_added_after_its_page_was_created_claims_no_move(normal_matter, specialist):
    _import(
        normal_matter,
        FILES[:1],
        added={FILES[0][0]: _utc(2021, 8, 30, 8, 48, 10)},
        page_created=_utc(2021, 8, 30, 8, 0),
    )
    (row,) = _imports(_rows(normal_matter, specialist))
    assert ONENOTE_PREDATES_PAGE not in row.milestone.sub


def test_an_unknown_page_creation_time_claims_nothing(normal_matter, specialist):
    _import(
        normal_matter,
        FILES[:1],
        added={FILES[0][0]: _utc(2021, 8, 30, 8, 48, 10)},
        page_created=None,
    )
    (row,) = _imports(_rows(normal_matter, specialist))
    assert row.milestone.sub == f"1 fail · OneNote: {SECTION} → Näidisleht"


def test_a_day_row_counts_only_the_files_older_than_their_page(normal_matter, specialist):
    _import(
        normal_matter,
        FILES[:2],
        page_title="Vana leht",
        added={name: SAME_DAY[name] for name, _content in FILES[:2]},
        page_created=_utc(2026, 2, 1, 9, 0),
    )
    _import(
        normal_matter,
        FILES[2:],
        page_title="Uus leht",
        added={FILES[2][0]: SAME_DAY[FILES[2][0]]},
        page_created=_utc(2021, 8, 1, 9, 0),
    )
    (row,) = _imports(_rows(normal_matter, specialist))
    assert row.imported_documents == 3
    assert row.milestone.sub == (
        "3 faili · 2 OneNote'i lehelt · 2 neist lisatud enne praeguse lehe loomist"
    )


def test_an_undated_file_never_claims_to_be_older_than_its_page(normal_matter, specialist):
    _import(normal_matter, FILES[:1], page_created=_utc(2026, 2, 1, 9, 0))
    (row,) = _imports(_rows(normal_matter, specialist))
    assert row.milestone.display_date == ONENOTE_UNDATED_DISPLAY
    assert ONENOTE_PREDATES_PAGE not in row.milestone.sub


def test_the_document_page_says_the_file_is_older_than_its_page(signed_in, normal_matter):
    (version,) = _import(
        normal_matter,
        FILES[:1],
        added={FILES[0][0]: _utc(2021, 8, 30, 8, 48, 10)},
        page_created=_utc(2026, 2, 1, 9, 0),
    )
    html = signed_in.get(
        reverse("documents:document_detail", kwargs={"pk": version.document.pk})
    ).content.decode()
    assert "30.8.2021 11:48 · lisatud enne praeguse lehe loomist" in html


def test_the_document_page_of_a_file_added_to_its_page_says_only_the_time(signed_in, normal_matter):
    (version,) = _import(
        normal_matter, FILES[:1], added={FILES[0][0]: _utc(2021, 8, 30, 8, 48, 10)}
    )
    html = signed_in.get(
        reverse("documents:document_detail", kwargs={"pk": version.document.pk})
    ).content.decode()
    assert "30.8.2021 11:48" in html
    assert "enne praeguse lehe loomist" not in html
