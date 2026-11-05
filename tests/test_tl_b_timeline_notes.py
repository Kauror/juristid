"""Historical OneNote notes on the Teema chronology (`HISTORICAL_NOTE_ITEM`).

The chronology read «OneNote'i lisati 4 dokumenti» — what the importer did. It
now reads the linked page's own dated notes, verbatim, and names the files on
each document row. These tests pin that projection end to end: the acceptance
page's shape, which files are direct, that no imported file stops being
reachable, who may read a note, sorting and paging, and that reading writes
nothing.

Synthetic data only: invented text, invented filenames, `.invalid` addresses.
"""

from __future__ import annotations

import hashlib
import re
from datetime import UTC, date, datetime

import pytest
from django.urls import reverse
from django.utils import timezone

from app.audit.models import ChangeEvent
from app.documents.models import Document, DocumentVersion
from app.documents.services import add_evidence_version, create_document
from app.legacy_import.historical_notes import NEXT_ACTIONS, NOTE, UNDATED_FILE_DISPLAY
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
from app.matters.models import Entry
from app.matters.timeline import (
    HISTORICAL_IMPORT_ITEM,
    HISTORICAL_NOTE_ITEM,
    ONENOTE_UNDATED_DISPLAY,
    _end_of_day,
    matter_timeline,
)
from app.workflow.models import NextAction
from tests import factories

pytestmark = pytest.mark.django_db

SECTION = "Näidissektsioon"
CAPTURE = "capture-naidis"
PAGE_CREATED = datetime(2026, 7, 29, 6, 2, 46, tzinfo=UTC)


def _utc(*parts) -> datetime:
    return datetime(*parts, tzinfo=UTC)


def text(ordinal, value, *, depth=1, parent=None, outline=0):
    return {
        "kind": "TEXT",
        "text": value,
        "depth": depth,
        "parent": parent,
        "ordinal": ordinal,
        "outline": outline,
        "resource_key": "",
        # A block's own creation time — never an event date (the tests below
        # make it disagree with every note's date on purpose).
        "created": "2026-07-29T06:02:53.000Z",
        "modified": "2026-08-10T07:41:24.000Z",
        "object_id": f"{{obj-{ordinal}}}",
        "source_kind": "TEXT",
    }


def blank(ordinal, *, depth=1, parent=None, outline=0):
    return {**text(ordinal, "", depth=depth, parent=parent, outline=outline), "kind": "BLANK"}


def file_block(ordinal, key, *, depth=1, parent=None, outline=0):
    return {
        **text(ordinal, "", depth=depth, parent=parent, outline=outline),
        "kind": "FILE_ATTACHMENT",
        "resource_key": key,
        "source_kind": "FILE",
    }


def make_page(
    matters,
    blocks,
    files=(),
    *,
    title="Näidismääruse muutmise eelnõu",
    links=(),
    held=False,
    restricted=(),
    states=None,
    capture_id=CAPTURE,
):
    """A captured page linked to ``matters``, with its files imported for each.

    ``files`` are ``(resource_key, filename, added_at | None)``. With ``held``
    every file is recorded as held for review on every link (a shared page),
    as the capture import records them; ``states`` maps a key to the
    ``(state, error_code)`` of a row that made no Document.
    """
    now = timezone.now()
    key = f"p-{LegacySourcePage.objects.count() + 1}-{now.timestamp()}"
    page = LegacySourcePage.objects.create(
        source_system=SourceSystem.ONENOTE_DESKTOP,
        source_page_id=f"1-{key}",
        page_key=key,
        source_notebook="Näidismärkmik",
        source_section=SECTION,
        title=title,
        page_level=1,
        page_order=1,
        page_role=SourcePageRole.MATTER_LIKE,
        role_reason="sünteetiline",
        source_created_at=PAGE_CREATED,
        source_modified_at=now,
        capture_id=capture_id,
        source_xml_sha256="0" * 64,
        derived_text=title,
        blocks=list(blocks),
        links=list(links),
        reading_order_strategy="VISUAL_THEN_XML",
        text_characters=len(title),
        block_count=len(blocks),
        file_count=len(files),
        file_bytes=0,
        first_imported_at=now,
        latest_imported_at=now,
    )
    ordinals = {
        block.get("resource_key"): block["ordinal"] for block in blocks if block.get("resource_key")
    }
    resources = {}
    for resource_key, filename, added in files:
        content = f"%PDF-1.4 {key} {resource_key}".encode()
        resources[resource_key] = (
            LegacySourceResource.objects.create(
                source_page=page,
                resource_key=resource_key,
                original_filename=filename,
                resource_kind="FILE_ATTACHMENT",
                source_block_ordinal=ordinals.get(resource_key, 0),
                sha256=hashlib.sha256(content).hexdigest(),
                size_bytes=len(content),
                archive_relative_path=f"resources/{resource_key}/{filename}",
                source_added_at=added,
                source_added_basis="ONENOTE_BLOCK_CREATED" if added else "",
            ),
            content,
        )
    made = {}
    for matter in matters:
        link = MatterSourcePage.objects.create(
            matter=matter,
            source_page=page,
            relationship_kind=SourceRelationshipKind.PRIMARY,
            match_method=SourceMatchMethod.EXCEL_EXACT_PAGE_ID,
            match_class=SourceMatchClass.EXACT,
            source_audit_reference="test",
        )
        versions = {}
        for resource_key, filename, _added in files:
            resource, content = resources[resource_key]
            state = (states or {}).get(resource_key)
            if held or state:
                LegacySourceResourceImport.objects.create(
                    matter_source_page=link,
                    resource=resource,
                    state=state[0] if state else ResourceImportState.SKIPPED,
                    error_code=state[1] if state else "SHARED_PAGE_AWAITING_REVIEW",
                )
                continue
            document = create_document(
                matter=matter,
                title=filename,
                role="LEGACY_MATERIAL",
                visibility_override="RESTRICTED" if resource_key in restricted else "",
            )
            Document.objects.filter(pk=document.pk).update(
                provenance_note=(
                    f"OneNote: {SECTION} → {title}\nAllikas: {capture_id}, plokk "
                    f"{ordinals.get(resource_key, 0)}"
                )
            )
            version = add_evidence_version(
                document=document,
                content=content,
                original_filename=filename,
                mime_type="application/pdf",
                source_identifier=f"{key}/{resource_key}/{matter.pk}",
            )
            LegacySourceResourceImport.objects.create(
                matter_source_page=link,
                resource=resource,
                document=document,
                document_version=version,
                state=ResourceImportState.IMPORTED,
            )
            versions[resource_key] = version
        made[matter.pk] = (link, versions)
    return page, made


#: The acceptance page's shape (2026_184): a dated note, four files, blanks, a
#: second dated note, an undated paragraph with an address, and a «Järgmised
#: tegevused» section whose prose holds a date. Every word is invented.
FIRST_NOTE = (
    "27.07.2026 - Ministeerium saatis kooskõlastusringile eelnõu, millega täpsustatakse "
    "näidismääruses kasutatavaid mõisteid ning kehtestatakse vastavusmärgi vorm"
)
SECOND_NOTE = "10.08.2026 - kojal ei olnud märkusi ega ettepanekuid eelnõu kohta"
ACCEPTANCE_BLOCKS = [
    text(0, FIRST_NOTE),
    file_block(1, "k92"),
    file_block(2, "k76"),
    file_block(3, "k71"),
    file_block(4, "k84"),
    blank(5),
    blank(6, outline=1),
    text(7, SECOND_NOTE, outline=1),
    blank(8, outline=1),
    blank(9),
    blank(10),
    text(11, "Eelnõu menetlus infosüsteemis: "),
    text(12, "https://eelnoud.example.invalid/doc/5019"),
    blank(13),
    text(14, "Järgmised tegevused: "),
    text(15, "Ministeerium ootab vastust 10.08. ", depth=2, parent=14),
    text(16, "Ootan eelnõu jõustumist. ", depth=2, parent=14),
    blank(17),
]
ADDED = _utc(2026, 7, 29, 6, 3, 6)
ACCEPTANCE_FILES = [
    ("k92", "EELNÕU.pdf", _utc(2026, 7, 29, 6, 3, 7)),
    ("k76", "Seletuskiri.pdf", ADDED),
    ("k71", "Kaaskiri ministeeriumile.pdf", ADDED),
    ("k84", "Vastavustabel.pdf", ADDED),
]


def rows_of(matter, user, **kwargs):
    items, _more = matter_timeline(matter=matter, user=user, **kwargs)
    return items


def notes_of(rows):
    return [row for row in rows if row.item_type == HISTORICAL_NOTE_ITEM]


def documents_of(rows):
    return [row for row in rows if row.item_type == HISTORICAL_IMPORT_ITEM]


def all_rows(matter, user, limit=200):
    out, offset = [], 0
    while True:
        items, more = matter_timeline(matter=matter, user=user, limit=limit, offset=offset)
        out.extend(items)
        if not more:
            return out
        offset += limit


# ---------------------------------------------------------------------------
# The acceptance page
# ---------------------------------------------------------------------------


def test_the_acceptance_page_reads_as_its_notes_and_one_document_row(normal_matter, specialist):
    _page, made = make_page([normal_matter], ACCEPTANCE_BLOCKS, ACCEPTANCE_FILES)
    link, versions = made[normal_matter.pk]
    rows = rows_of(normal_matter, specialist)

    notes = notes_of(rows)
    assert [note.historical_note.date_display for note in notes] == ["10.08.2026", "27.07.2026"]
    second, first = (note.historical_note for note in notes)
    assert first.headline == "Ministeerium saatis kooskõlastusringile eelnõu, millega…" or (
        first.headline.startswith("Ministeerium saatis kooskõlastusringile eelnõu, millega")
        and first.headline.endswith("…")
        and len(first.headline) <= 65
    )
    assert first.paragraphs == (FIRST_NOTE.split(" - ", 1)[1],)
    assert second.headline == "kojal ei olnud märkusi ega ettepanekuid eelnõu kohta"
    assert second.paragraphs == ("kojal ei olnud märkusi ega ettepanekuid eelnõu kohta",)
    for note in (first, second):
        assert note.kind == NOTE
        assert note.precision == "DAY"
        # The four files sit beside the first note, not inside it: page-level.
        assert note.direct_files == ()
        assert note.page_files_count == 4
        assert note.page_url == f"/ajalugu/{link.pk}/"
        assert note.page_title == "Näidismääruse muutmise eelnõu"
        assert note.section == SECTION
    # The first note's run holds its four files (R5.3), page-level all the same.
    assert first.source_ref == f"{CAPTURE} · plokid 0–4"
    assert second.source_ref == f"{CAPTURE} · plokk 7"
    assert all(note.historical_note.direct_files == () for note in notes)

    (document_row,) = documents_of(rows)
    assert document_row.milestone.display_date == "29.07.2026"
    assert document_row.milestone.what == "EELNÕU.pdf ja 3 muud faili"
    assert document_row.imported_documents == 4
    assert [file.original_filename for file in document_row.historical_files] == [
        "EELNÕU.pdf",
        "Seletuskiri.pdf",
        "Kaaskiri ministeeriumile.pdf",
        "Vastavustabel.pdf",
    ]
    assert {file.added_display for file in document_row.historical_files} == {"29.07.2026"}
    assert {file.version_id for file in document_row.historical_files} == {
        version.pk for version in versions.values()
    }
    for row in rows:
        if row.milestone is not None:
            assert "OneNote'i lisati" not in row.milestone.what
    # Nothing undated, and «Järgmised tegevused» is no row at all.
    assert len(rows) == 3


def test_the_rows_sort_by_their_dates_not_by_any_capture_time(normal_matter, specialist):
    make_page([normal_matter], ACCEPTANCE_BLOCKS, ACCEPTANCE_FILES)
    rows = rows_of(normal_matter, specialist)
    assert [row.item_type for row in rows] == [
        HISTORICAL_NOTE_ITEM,
        HISTORICAL_IMPORT_ITEM,
        HISTORICAL_NOTE_ITEM,
    ]
    assert rows[0].occurred_at == _end_of_day(date(2026, 8, 10))
    assert rows[2].occurred_at == _end_of_day(date(2026, 7, 27))


def test_the_teema_page_draws_the_notes_and_never_the_generic_headline(signed_in, normal_matter):
    make_page([normal_matter], ACCEPTANCE_BLOCKS, ACCEPTANCE_FILES)
    html = signed_in.get(
        reverse("matters:matter_detail", kwargs={"pk": normal_matter.pk})
    ).content.decode()
    assert "OneNote'i lisati" not in html and "OneNote&#x27;i lisati" not in html
    assert "kojal ei olnud märkusi ega ettepanekuid eelnõu kohta" in html
    assert "EELNÕU.pdf ja 3 muud faili" in html
    # The prose date in «Järgmised tegevused» is not drawn as anything.
    assert "Ootan eelnõu jõustumist" not in html


# ---------------------------------------------------------------------------
# Direct files
# ---------------------------------------------------------------------------

DIRECT_BLOCKS = [
    text(0, "12.03.2024 - Koda saatis arvamuse ministeeriumile"),
    file_block(1, "kiri", depth=2, parent=0),
    file_block(2, "lisa", depth=2, parent=0),
    blank(3),
    file_block(4, "muu"),
]
DIRECT_FILES = [
    ("kiri", "Arvamus.pdf", _utc(2024, 3, 12, 10, 0)),
    ("lisa", "Lisa.pdf", _utc(2024, 3, 12, 10, 1)),
    ("muu", "Muu.pdf", _utc(2024, 3, 12, 10, 2)),
]


def test_a_file_inside_a_note_reads_under_it_and_draws_no_document_row(normal_matter, specialist):
    _page, made = make_page([normal_matter], DIRECT_BLOCKS, DIRECT_FILES)
    _link, versions = made[normal_matter.pk]
    rows = rows_of(normal_matter, specialist)
    (note,) = notes_of(rows)
    assert [file.original_filename for file in note.historical_note.direct_files] == [
        "Arvamus.pdf",
        "Lisa.pdf",
    ]
    assert note.historical_note.page_files_count == 1
    first = note.historical_note.direct_files[0]
    assert first.document_url == f"/dokumendid/{versions['kiri'].document_id}/"
    assert str(versions["kiri"].pk) in first.open_url
    assert first.added_display == "12.03.2024"
    assert note.historical_note.source_ref == f"{CAPTURE} · plokid 0–2"
    (document_row,) = documents_of(rows)
    assert document_row.milestone.what == "Muu.pdf"
    assert len(document_row.historical_files) == 1
    assert [str(event.object_id) for event in document_row.events] == [str(versions["muu"].pk)]


def test_a_direct_files_structural_comment_is_shown_with_it(normal_matter, specialist):
    _page, made = make_page([normal_matter], DIRECT_BLOCKS, DIRECT_FILES)
    _link, versions = made[normal_matter.pk]
    Document.objects.filter(pk=versions["kiri"].document_id).update(
        provenance_note=(
            "OneNote: x\nPealkiri OneNote'is: 12.03.2024 - Koda saatis arvamuse "
            "ministeeriumile\nAllikas: c, plokk 1"
        )
    )
    (note,) = notes_of(rows_of(normal_matter, specialist))
    assert note.historical_note.direct_files[0].comment == (
        "12.03.2024 - Koda saatis arvamuse ministeeriumile"
    )
    assert note.historical_note.direct_files[1].comment == ""


def test_an_attached_note_next_to_a_dated_note_does_not_make_the_file_direct(
    normal_matter, specialist
):
    """The lead's binding finding (agent D, 2026-11-05): «Märkus OneNote'is» under
    file N is usually the label of file N+1. Neither the comment nor the dated
    text nested under a file ties that file to the note; it stays page-level and
    reads on its own document row, its comment shown as Dokumendid shows it."""
    _page, made = make_page(
        [normal_matter],
        [
            file_block(0, "kiri"),
            text(1, "12.03.2024 - Ministeerium vastas kojale", depth=2, parent=0),
            file_block(2, "jargmine"),
            text(3, "Koja arvamus", depth=2, parent=2),
        ],
        [
            ("kiri", "Vastus.pdf", _utc(2024, 3, 12, 10, 0)),
            ("jargmine", "Arvamus.pdf", _utc(2024, 3, 12, 10, 5)),
        ],
    )
    _link, versions = made[normal_matter.pk]
    Document.objects.filter(pk=versions["kiri"].document_id).update(
        provenance_note=(
            "OneNote: x\nMärkus OneNote'is: 12.03.2024 - Ministeerium vastas kojale\n"
            "Allikas: c, plokk 0"
        )
    )
    rows = rows_of(normal_matter, specialist)
    (note,) = notes_of(rows)
    assert note.historical_note.headline == "Ministeerium vastas kojale"
    assert note.historical_note.direct_files == ()
    assert note.historical_note.page_files_count == 2
    (document_row,) = documents_of(rows)
    assert [file.original_filename for file in document_row.historical_files] == [
        "Vastus.pdf",
        "Arvamus.pdf",
    ]
    # The comment is shown with its file, as it is today, and not promoted.
    assert document_row.historical_files[0].comment == "12.03.2024 - Ministeerium vastas kojale"


def test_a_note_with_only_a_date_and_files_is_named_by_its_files(normal_matter, specialist):
    make_page(
        [normal_matter],
        [text(0, "12.03.2024"), file_block(1, "kiri", depth=2, parent=0)],
        [("kiri", "Arvamus.pdf", _utc(2024, 3, 12, 10, 0))],
    )
    (note,) = notes_of(rows_of(normal_matter, specialist))
    assert note.historical_note.headline == "Arvamus.pdf"
    assert note.historical_note.paragraphs == ()


# ---------------------------------------------------------------------------
# Every imported file stays reachable, once
# ---------------------------------------------------------------------------


def test_every_imported_file_stays_reachable_and_on_exactly_one_row(normal_matter, specialist):
    make_page([normal_matter], ACCEPTANCE_BLOCKS, ACCEPTANCE_FILES)
    make_page([normal_matter], DIRECT_BLOCKS, DIRECT_FILES, title="Teine leht")
    make_page(
        [normal_matter],
        [text(0, "Dateerimata märkus"), file_block(1, "a"), file_block(2, "b")],
        [("a", "Ujuv.pdf", None), ("b", "Dateeritud.pdf", _utc(2025, 1, 2, 9, 0))],
        title="Kolmas leht",
    )
    # On ddedb499 every imported, visible version drew under exactly one row.
    reachable_then = {
        str(pk)
        for pk in LegacySourceResourceImport.objects.filter(
            matter_source_page__matter=normal_matter,
            state=ResourceImportState.IMPORTED,
        ).values_list("document_version_id", flat=True)
    }
    for limit in (1, 2, 50):
        rows = all_rows(normal_matter, specialist, limit=limit)
        seen: list[str] = []
        for row in rows:
            if row.item_type == HISTORICAL_IMPORT_ITEM:
                seen.extend(str(event.object_id) for event in row.events)
                assert len(row.historical_files) == row.imported_documents
            if row.item_type == HISTORICAL_NOTE_ITEM:
                seen.extend(str(file.version_id) for file in row.historical_note.direct_files)
        assert sorted(seen) == sorted(reachable_then), limit
    undated = [
        row
        for row in documents_of(all_rows(normal_matter, specialist))
        if row.milestone.display_date == ONENOTE_UNDATED_DISPLAY
    ]
    (row,) = undated
    assert row.milestone.what == "Ujuv.pdf"
    assert row.historical_files[0].added_display == UNDATED_FILE_DISPLAY


def test_the_page_walk_neither_repeats_nor_skips_a_row(normal_matter, specialist):
    for index in range(4):
        make_page(
            [normal_matter],
            [
                text(0, f"0{index + 1}.03.2024 - Esimene märge {index}"),
                text(1, f"0{index + 1}.03.2024 - Sama päeva teine märge {index}"),
                blank(2),
                file_block(3, "f"),
            ],
            [("f", f"Fail {index}.pdf", _utc(2024, 3, index + 1, 8, 0))],
            title=f"Leht {index}",
        )
    whole = [row.row_key for row in all_rows(normal_matter, specialist, limit=500)]
    assert len(whole) == len(set(whole)) == 12
    for limit in (1, 2, 3, 5):
        assert [row.row_key for row in all_rows(normal_matter, specialist, limit=limit)] == whole
    # Twice the same: the order is stable between loads.
    assert [row.row_key for row in all_rows(normal_matter, specialist, limit=500)] == whole


# ---------------------------------------------------------------------------
# Who may read a note
# ---------------------------------------------------------------------------


def test_an_outsider_reads_no_note(normal_matter, administrator):
    make_page([normal_matter], ACCEPTANCE_BLOCKS, ACCEPTANCE_FILES)
    rows = rows_of(normal_matter, administrator)
    assert notes_of(rows) == []
    assert documents_of(rows) == []


@pytest.fixture
def narrow_reader(monkeypatch):
    """A reader of NORMAL work only: the hand-built scope docs/adr/0181 still honours."""
    from app.core.authorization import Scope
    from app.core.authorization import scope_for_user as real

    reader = factories.UserFactory(display_name="Kitsendatud lugeja")

    def scope(user):
        if getattr(user, "pk", None) == reader.pk:
            return Scope(
                user=user,
                is_authenticated=True,
                sees_restricted_by_role=False,
                break_glass_grant_id=None,
            )
        return real(user)

    for module in (
        "app.matters.models",
        "app.documents.models",
        "app.audit.visibility",
        "app.workflow.models",
        "app.submissions.models",
        "app.legacy_import.historical_views",
    ):
        monkeypatch.setattr(f"{module}.scope_for_user", scope)
    return reader


def test_a_restricted_teema_outside_a_narrow_scope_shows_no_note(
    restricted_matter, specialist, narrow_reader
):
    make_page([restricted_matter], DIRECT_BLOCKS, DIRECT_FILES)
    assert notes_of(rows_of(restricted_matter, specialist)) != []
    rows = rows_of(restricted_matter, narrow_reader)
    assert notes_of(rows) == []
    assert documents_of(rows) == []


def test_a_deleted_teemas_link_is_no_way_in(normal_matter, specialist):
    make_page([normal_matter], ACCEPTANCE_BLOCKS, ACCEPTANCE_FILES)
    type(normal_matter).objects.filter(pk=normal_matter.pk).update(deleted_at=timezone.now())
    normal_matter.refresh_from_db()
    assert notes_of(rows_of(normal_matter, specialist)) == []


def test_a_restricted_direct_file_is_neither_named_nor_counted(normal_matter, narrow_reader):
    make_page([normal_matter], DIRECT_BLOCKS, DIRECT_FILES, restricted=("kiri",))
    rows = rows_of(normal_matter, narrow_reader)
    (note,) = notes_of(rows)
    assert [file.original_filename for file in note.historical_note.direct_files] == ["Lisa.pdf"]
    assert len(note.historical_note.direct_files) == 1
    for row in rows:
        assert "Arvamus.pdf" not in (row.milestone.what if row.milestone else "")
        assert all(file.original_filename != "Arvamus.pdf" for file in row.historical_files)


def test_a_page_shared_by_two_teemad_projects_once_on_each_through_its_own_link(specialist):
    first = factories.MatterFactory(owner=specialist)
    second = factories.MatterFactory(owner=specialist)
    _page, made = make_page([first, second], DIRECT_BLOCKS, DIRECT_FILES, held=True)
    for matter in (first, second):
        link, _versions = made[matter.pk]
        notes = notes_of(rows_of(matter, specialist))
        assert len(notes) == 1
        note = notes[0].historical_note
        assert note.page_url == f"/ajalugu/{link.pk}/"
        # Held for review on a shared page: no Document, so nothing is named.
        assert note.direct_files == ()
        assert note.page_files_count == 0
        assert documents_of(rows_of(matter, specialist)) == []


def test_a_shared_pages_files_are_each_teemas_own(specialist):
    first = factories.MatterFactory(owner=specialist)
    second = factories.MatterFactory(owner=specialist)
    _page, made = make_page([first, second], DIRECT_BLOCKS, DIRECT_FILES)
    for matter in (first, second):
        _link, versions = made[matter.pk]
        (note,) = notes_of(rows_of(matter, specialist))
        assert {file.version_id for file in note.historical_note.direct_files} == {
            versions["kiri"].pk,
            versions["lisa"].pk,
        }


def test_an_earlier_captures_row_is_not_a_document(normal_matter, specialist):
    make_page(
        [normal_matter],
        DIRECT_BLOCKS,
        DIRECT_FILES,
        states={"kiri": (ResourceImportState.SKIPPED, "EARLIER_CAPTURE_NOT_IMPORTED")},
    )
    (note,) = notes_of(rows_of(normal_matter, specialist))
    assert [file.original_filename for file in note.historical_note.direct_files] == ["Lisa.pdf"]
    assert note.historical_note.page_files_count == 1


# ---------------------------------------------------------------------------
# Undated text, next actions, and nothing written
# ---------------------------------------------------------------------------


def test_undated_text_is_no_row_and_its_page_stays_one_link_away(normal_matter, specialist):
    _page, made = make_page(
        [normal_matter],
        [
            text(0, "Ministeerium ootab vastust 10.08.2026 ja otsustab siis"),
            blank(1),
            file_block(2, "a"),
        ],
        [("a", "Fail.pdf", _utc(2026, 8, 1, 9, 0))],
    )
    link, _versions = made[normal_matter.pk]
    rows = rows_of(normal_matter, specialist)
    assert notes_of(rows) == []
    (row,) = documents_of(rows)
    assert row.milestone.what == "Fail.pdf"
    # The Teema page's «Ajalooline OneNote» lists the page itself.
    html_link = reverse("legacy_import:source_page", kwargs={"pk": link.pk})
    assert html_link == f"/ajalugu/{link.pk}/"


def test_a_dated_next_actions_section_is_labelled_and_is_never_a_task(normal_matter, specialist):
    before = NextAction.objects.count()
    make_page(
        [normal_matter],
        [
            text(0, "Mis saab edasi?"),
            text(1, "13.08.2026 seisuga ootab teema otsust", depth=2, parent=0),
            text(2, "helistada ministeeriumi", depth=2, parent=0),
        ],
    )
    # The undated heading is no row; its dated line is its own NEXT_ACTIONS row (R4, U6).
    (note,) = notes_of(rows_of(normal_matter, specialist))
    assert note.historical_note.kind == NEXT_ACTIONS
    assert note.historical_note.date_display == "13.08.2026"
    assert note.historical_note.date_iso == "2026-08-13"
    assert note.historical_note.paragraphs == (
        "seisuga ootab teema otsust",
        "helistada ministeeriumi",
    )
    assert NextAction.objects.count() == before


def test_reading_the_notes_writes_nothing(normal_matter, specialist, signed_in):
    make_page([normal_matter], ACCEPTANCE_BLOCKS, ACCEPTANCE_FILES)
    make_page([normal_matter], DIRECT_BLOCKS, DIRECT_FILES, title="Teine leht")
    counts = lambda: (  # noqa: E731
        Entry.objects.count(),
        NextAction.objects.count(),
        Document.objects.count(),
        DocumentVersion.objects.count(),
        ChangeEvent.objects.count(),
        LegacySourcePage.objects.count(),
        LegacySourceResourceImport.objects.count(),
    )
    before = counts()
    pages = list(LegacySourcePage.objects.values_list("blocks", flat=True))
    all_rows(normal_matter, specialist, limit=1)
    signed_in.get(reverse("matters:matter_detail", kwargs={"pk": normal_matter.pk}))
    assert counts() == before
    assert list(LegacySourcePage.objects.values_list("blocks", flat=True)) == pages


def test_a_note_dated_ahead_says_so(normal_matter, specialist):
    # Written 10.08.2026 for 20.09.2026: within R7's 45 days, so it stays dated (Q4).
    make_page([normal_matter], [text(0, "20.09.2026 - Kavandatud istung")])
    (note,) = notes_of(rows_of(normal_matter, specialist, today=date(2026, 9, 1)))
    assert note.milestone.ahead is True


def test_a_date_written_far_ahead_is_no_row(normal_matter, specialist):
    # Written 10.08.2026 for 2028: a forecast or a typo (R7), never an event date.
    make_page([normal_matter], [text(0, "01.07.2028 - jõustuvad uued normid")])
    assert notes_of(rows_of(normal_matter, specialist)) == []


def test_a_trailing_date_heading_is_a_dated_note_shown_verbatim(normal_matter, specialist):
    make_page(
        [normal_matter],
        [text(0, "Koja arvamus 12.03.2024:"), text(1, "Koda toetas eelnõu.", depth=2, parent=0)],
    )
    (note,) = notes_of(rows_of(normal_matter, specialist))
    assert note.historical_note.date_display == "12.03.2024"
    assert note.historical_note.paragraphs == ("Koja arvamus 12.03.2024:", "Koda toetas eelnõu.")


def test_entities_in_the_source_are_decoded(normal_matter, specialist):
    make_page([normal_matter], [text(0, "12.03.2024&nbsp;-&nbsp;Koda &quot;toetas&quot;")])
    (note,) = notes_of(rows_of(normal_matter, specialist))
    assert note.historical_note.paragraphs == ('Koda "toetas"',)


def test_month_and_year_notes_keep_their_precision_and_sort_on_their_anchor(
    normal_matter, specialist
):
    make_page(
        [normal_matter],
        [
            text(0, "07.2025 - Kuu jooksul kogunes töörühm"),
            blank(1),
            text(2, "2024 - Aasta ülevaade"),
        ],
    )
    month, year = notes_of(rows_of(normal_matter, specialist))
    assert (month.historical_note.date_display, month.historical_note.precision) == (
        "07.2025",
        "MONTH",
    )
    assert month.occurred_at == _end_of_day(date(2025, 7, 1))
    assert (year.historical_note.date_display, year.historical_note.precision) == ("2024", "YEAR")
    assert (month.historical_note.date_iso, year.historical_note.date_iso) == ("2025-07", "2024")
    assert year.occurred_at == _end_of_day(date(2024, 1, 1))


def test_the_entries_filter_draws_no_note(normal_matter, specialist):
    make_page([normal_matter], ACCEPTANCE_BLOCKS, ACCEPTANCE_FILES)
    assert notes_of(rows_of(normal_matter, specialist, only="sissekanded")) == []
    assert len(notes_of(rows_of(normal_matter, specialist, only="sundmused"))) == 2


def test_the_source_ref_is_inspectable(normal_matter, specialist):
    make_page([normal_matter], ACCEPTANCE_BLOCKS, ACCEPTANCE_FILES)
    for note in notes_of(rows_of(normal_matter, specialist)):
        assert re.fullmatch(
            rf"{CAPTURE} · (?:plokk \d+|plokid \d+–\d+)", note.historical_note.source_ref
        )
