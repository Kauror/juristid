"""A later OneNote capture of a page is what the source page reads (live-sync round 2, B).

Round 1 stored a later capture's reading of a page as a `OneNotePageRevision`
and a changed file as the next `DocumentVersion` (`OneNoteResourceRevision`,
`VERSIONED`), but `/ajalugu/<pk>/` still showed the first import's text. Held
here, against one invented page read twice:

- the latest captured version is the normal reading; every earlier one is one
  `?hoive=` away, and a value naming nothing on this page is a 404;
- which version is shown, whether a later exists, and when each was stored;
- what changed only where the blocks' OneNote object IDs prove it;
- the files of each version, a later file version opening in the later one;
- the first record, its text, its files and its versions are never rewritten;
- «Tegevused»: notes from the latest version, once, on their own dates, linked
  to that version's block; a later file version on its plausible OneNote
  modification day and never under a note;
- the same door as the page itself.

Every page, file, date and Teema is invented.
"""

from __future__ import annotations

import hashlib
import re
from datetime import UTC, datetime

import pytest
from django.urls import reverse
from django.utils import timezone

from app.documents.models import DocumentVersion
from app.documents.services import add_evidence_version, create_document
from app.legacy_import import source_page_versions as versions
from app.legacy_import.models import ImportBatch, ReconciliationStatus
from app.legacy_import.onenote_coverage_models import (
    OneNotePageRevision,
    OneNoteResourceRevision,
    RevisionKind,
    RevisionState,
)
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
from app.matters.timeline import HISTORICAL_IMPORT_ITEM, HISTORICAL_NOTE_ITEM, matter_timeline
from tests import factories

pytestmark = pytest.mark.django_db

T_PAGE = datetime(2024, 3, 1, 9, 0, tzinfo=UTC)
T_FILE = datetime(2024, 3, 12, 9, 15, tzinfo=UTC)
T_FIRST_MODIFIED = datetime(2024, 4, 2, 10, 0, tzinfo=UTC)
T_LATER_MODIFIED = datetime(2026, 11, 7, 12, 0, tzinfo=UTC)
#: When the file's bytes were replaced in OneNote, per the later capture.
T_FILE_MODIFIED = datetime(2026, 11, 6, 8, 30, tzinfo=UTC)
#: When the later note's block was written in OneNote.
T_NOTE_WRITTEN = datetime(2026, 11, 5, 10, 0, tzinfo=UTC)

FIRST_NOTE = "12.03.2024 - Ministeerium saatis eelnõu kooskõlastamiseks."
KEPT = "Koda koostab arvamuse liikmete ettepanekute põhjal."
EDITED_BEFORE = "Tähtaeg on kolm nädalat."
EDITED_AFTER = "Tähtaega pikendati nelja nädalani."
REMOVED = "Ajutine märkus, mis hiljem kustutati."
ADDED_NOTE = "05.11.2026 - Riigikogu menetlusse saadetud eelnõu uus tekst."
FILENAME = "Koja_arvamus.pdf"
V1 = b"%PDF-1.4 esimene versioon"
V2 = b"%PDF-1.4 teine versioon, muudetud OneNote'is"


def _text(ordinal, text, object_id, *, created=T_PAGE):
    return {
        "ordinal": ordinal,
        "kind": "TEXT" if text else "BLANK",
        "source_kind": "TEXT" if text else "BLANK",
        "text": text,
        "object_id": object_id,
        "created": created.isoformat(),
        "modified": created.isoformat(),
        "outline": 0,
        "depth": 0,
        "parent": None,
        "resource_key": "",
    }


def _file(ordinal, key, *, parent):
    return {
        "ordinal": ordinal,
        "kind": "FILE_ATTACHMENT",
        "source_kind": "FILE",
        "text": "",
        "object_id": key,
        "resource_key": key,
        "created": T_FILE.isoformat(),
        "modified": T_FILE.isoformat(),
        "outline": 0,
        "depth": 1,
        "parent": parent,
    }


FILE_KEY = "{F1}{7}{B0}"

FIRST_BLOCKS = [
    _text(0, FIRST_NOTE, "t-note"),
    _file(1, FILE_KEY, parent=0),
    _text(2, KEPT, "t-kept"),
    _text(3, EDITED_BEFORE, "t-edit"),
    _text(4, REMOVED, "t-gone"),
]

LATER_BLOCKS = [
    _text(0, FIRST_NOTE, "t-note"),
    _file(1, FILE_KEY, parent=0),
    _text(2, KEPT, "t-kept"),
    _text(3, EDITED_AFTER, "t-edit"),
    _text(4, "", "t-blank"),
    _text(5, ADDED_NOTE, "t-new", created=T_NOTE_WRITTEN),
]


def _batch(label: str) -> ImportBatch:
    return ImportBatch.objects.create(
        source_system="ONENOTE_DESKTOP_CAPTURE",
        source_file_name=f"{label}-package",
        source_snapshot_sha256=hashlib.sha256(label.encode()).hexdigest(),
        importer_version="test/1",
        contract_version="test/1",
        started_at=timezone.now(),
        reconciliation_status=ReconciliationStatus.COMPLETED,
    )


def _page(matter) -> MatterSourcePage:
    """The first capture: the page row, one imported file (v1), the Teema link."""
    batch = _batch("cap-1")
    now = timezone.now()
    page = LegacySourcePage.objects.create(
        source_system=SourceSystem.ONENOTE_DESKTOP,
        source_page_id="{0000BB01-0000-4000-8000-0000000000B1}",
        page_key="0000BB01-0000-4000-8000-0000000000B1",
        source_notebook="Näidismärkmik",
        source_section="Näidissektsioon",
        title="Näidisseaduse eelnõu",
        page_level=1,
        page_order=1,
        page_role=SourcePageRole.MATTER_LIKE,
        role_reason="sünteetiline",
        source_created_at=T_PAGE,
        source_modified_at=T_FIRST_MODIFIED,
        capture_id="cap-1",
        source_xml_storage_key="",
        source_xml_sha256="1" * 64,
        derived_text="\n".join(b["text"] for b in FIRST_BLOCKS if b["text"]),
        blocks=FIRST_BLOCKS,
        links=[],
        text_characters=100,
        block_count=len(FIRST_BLOCKS),
        file_count=1,
        file_bytes=len(V1),
        first_imported_at=now,
        latest_imported_at=now,
        import_batch=batch,
    )
    link = MatterSourcePage.objects.create(
        matter=matter,
        source_page=page,
        relationship_kind=SourceRelationshipKind.PRIMARY,
        match_method=SourceMatchMethod.LOVABLE_EXACT_PAGE_GUID,
        match_class=SourceMatchClass.EXACT,
        source_audit_reference="test",
    )
    resource = LegacySourceResource.objects.create(
        source_page=page,
        resource_key=FILE_KEY,
        original_filename=FILENAME,
        source_block_ordinal=1,
        sha256=hashlib.sha256(V1).hexdigest(),
        size_bytes=len(V1),
        archive_relative_path="cap-1/blobs/x",
        source_added_at=T_FILE,
        source_added_basis="ONENOTE_BLOCK_CREATED",
    )
    document = create_document(matter=matter, title=FILENAME, role="LEGACY_MATERIAL")
    version = add_evidence_version(
        document=document, content=V1, original_filename=FILENAME, mime_type="application/pdf"
    )
    LegacySourceResourceImport.objects.create(
        matter_source_page=link,
        resource=resource,
        document=document,
        document_version=version,
        state=ResourceImportState.IMPORTED,
        import_batch=batch,
    )
    return link


def _later_capture(link, *, file_modified=T_FILE_MODIFIED, blocks=LATER_BLOCKS, xml_key=""):
    """The second capture: the page's new reading, then the file's new bytes (v2)."""
    batch = _batch("cap-2")
    revision = OneNotePageRevision.objects.create(
        page=link.source_page,
        import_batch=batch,
        capture_id="cap-2",
        manifest_sha256=batch.source_snapshot_sha256,
        source_xml_storage_key=xml_key,
        source_xml_sha256="2" * 64,
        text_signature="sig-2",
        text_changed=True,
        title="Näidisseaduse eelnõu (Riigikogus)",
        source_section="Näidissektsioon",
        source_modified_at=T_LATER_MODIFIED,
        derived_text="\n".join(b["text"] for b in blocks if b["text"]),
        blocks=blocks,
    )
    record = LegacySourceResourceImport.objects.get(matter_source_page=link)
    v2 = add_evidence_version(
        document=record.document,
        content=V2,
        original_filename=FILENAME,
        mime_type="application/pdf",
    )
    OneNoteResourceRevision.objects.create(
        matter_source_page=link,
        resource=record.resource,
        import_batch=batch,
        capture_id="cap-2",
        manifest_sha256=batch.source_snapshot_sha256,
        kind=RevisionKind.CHANGED_BYTES,
        state=RevisionState.VERSIONED,
        sha256=v2.sha256,
        size_bytes=v2.size_bytes,
        source_added_at=T_FILE,
        block_modified_at=file_modified,
        document=record.document,
        document_version=v2,
    )
    return revision, v2


@pytest.fixture
def two_captures(normal_matter):
    link = _page(normal_matter)
    revision, v2 = _later_capture(link)
    return {"link": link, "revision": revision, "v2": v2, "matter": normal_matter}


def _url(link, token=None):
    url = reverse("legacy_import:source_page", kwargs={"pk": link.pk})
    return f"{url}?hoive={token}" if token is not None else url


def _get(client, link, token=None) -> str:
    response = client.get(_url(link, token))
    assert response.status_code == 200, response.status_code
    return response.content.decode()


def _flow(html: str) -> str:
    return html.split("<summary>Tehnilised andmed</summary>", 1)[0]


# -- the source page ----------------------------------------------------------------------


def test_the_latest_captured_version_is_the_normal_reading(two_captures, signed_in):
    html = _get(signed_in, two_captures["link"])
    flow = _flow(html)
    assert ADDED_NOTE.split(" - ", 1)[1] in flow and EDITED_AFTER in flow
    assert EDITED_BEFORE not in flow.split("Eemaldatud plokid", 1)[0]
    assert "<h1>Näidisseaduse eelnõu (Riigikogus)</h1>" in html
    assert "Viimane hõive" in flow and "versioon 2/2" in flow
    assert "Lehest on hilisem hõive" not in flow
    # The header's «muudetud» is the shown version's OneNote day, said once.
    assert "muudetud 07.11.2026" in flow
    # The version line names no OneNote day; the latest is not in «Varasemad».
    assert flow.count("07.11.2026") == 1


def test_an_earlier_version_is_selectable_and_says_a_later_one_exists(two_captures, signed_in):
    html = _get(signed_in, two_captures["link"], versions.ORIGINAL)
    flow = _flow(html)
    assert EDITED_BEFORE in flow and REMOVED in flow
    assert EDITED_AFTER not in flow
    assert "Varasem versioon" in flow and "versioon 1/2" in flow and "esimene hõive" in flow
    assert "Lehest on hilisem hõive" in flow
    assert f'href="{_url(two_captures["link"])}">Ava viimane hõive' in flow
    assert "<h1>Näidisseaduse eelnõu</h1>" in html
    assert "muudetud 02.04.2024" in flow
    # The first record compares with nothing.
    assert "plokk lisandus" not in flow and "Lehe tekst" not in flow


def test_the_latest_selected_by_its_own_id_is_the_same_reading(two_captures, signed_in):
    html = _get(signed_in, two_captures["link"], two_captures["revision"].pk)
    assert "Viimane hõive" in html and EDITED_AFTER in html


@pytest.mark.parametrize("token", ["tundmatu", "00000000-0000-0000-0000-000000000000", ""])
def test_a_version_that_names_nothing_on_this_page_is_not_found(two_captures, signed_in, token):
    response = signed_in.get(_url(two_captures["link"], token))
    assert response.status_code == 404


def test_another_pages_revision_id_is_not_found_here(two_captures, signed_in, normal_matter):
    other = MatterSourcePage.objects.get(pk=two_captures["link"].pk)
    # A second, unrelated page of another Teema with its own revision.
    second_matter = factories.MatterFactory(owner=normal_matter.owner)
    page = other.source_page
    page.pk = None
    page.id = None
    page.page_key = "0000BB02-0000-4000-8000-0000000000B2"
    page.source_page_id = "{0000BB02-0000-4000-8000-0000000000B2}"
    page.save()
    second = MatterSourcePage.objects.create(
        matter=second_matter,
        source_page=page,
        relationship_kind=SourceRelationshipKind.PRIMARY,
        match_method=SourceMatchMethod.LOVABLE_EXACT_PAGE_GUID,
        match_class=SourceMatchClass.EXACT,
        source_audit_reference="test",
    )
    assert signed_in.get(_url(second, two_captures["revision"].pk)).status_code == 404


def test_each_version_says_when_it_was_stored_and_lists_the_others(two_captures, signed_in):
    flow = _flow(_get(signed_in, two_captures["link"]))
    stored = timezone.localdate(two_captures["revision"].created_at).strftime("%d.%m.%Y")
    assert "salvestatud <time" in flow and stored in flow
    versions_list = flow.split("Varasemad versioonid", 1)[1]
    assert f'href="{_url(two_captures["link"], versions.ORIGINAL)}">Versioon 1</a>' in versions_list
    assert "OneNote'is muudetud 02.04.2024" in versions_list
    # The capture's identifiers stay in the technical details.
    assert "cap-2" not in flow and "cap-1" not in flow
    details = _get(signed_in, two_captures["link"]).split("Tehnilised andmed", 1)[1]
    assert "cap-2" in details and "2" * 64 in details


def test_changes_are_said_by_block_identity(two_captures, signed_in):
    flow = _flow(_get(signed_in, two_captures["link"]))
    assert (
        "Võrreldes eelmise versiooniga: 1 plokk lisandus, 1 plokk muutus, 1 plokk eemaldati."
        in flow
    )
    assert "1 faili uus versioon." in flow
    # The added and the changed block carry one quiet word; the kept ones none.
    assert re.search(
        r"uus tekst\.\s*<span class=\"statechip statechip--new\">uus</span>",
        flow,
    )
    assert re.search(
        re.escape(EDITED_AFTER) + r'\s*<span class="statechip statechip--new">muudetud</span>', flow
    )
    assert re.search(re.escape(KEPT) + r"</p>", flow)
    removed = flow.split("Eemaldatud plokid", 1)[1]
    assert REMOVED in removed


def test_no_block_diff_is_invented_when_identity_is_not_proven(normal_matter, signed_in):
    link = _page(normal_matter)
    # A moved page: OneNote re-issued every object ID (D2).
    moved = [{**b, "object_id": f"moved-{b['ordinal']}"} for b in LATER_BLOCKS]
    _later_capture(link, blocks=moved)
    flow = _flow(_get(signed_in, link))
    assert "Plokkide kaupa muudatusi ei saa näidata" in flow
    assert "plokk lisandus" not in flow and 'statechip--new">uus</span>' not in flow
    assert "Eemaldatud plokid" not in flow


def test_block_changes_require_unique_ids_on_both_sides():
    later = [dict(b) for b in LATER_BLOCKS]
    later[2]["object_id"] = later[0]["object_id"]  # a repeated ID
    assert versions.block_changes(FIRST_BLOCKS, later) is None
    without = [{**b, "object_id": ""} for b in FIRST_BLOCKS]  # an earlier shape kept none
    assert versions.block_changes(without, LATER_BLOCKS) is None
    same = versions.block_changes(FIRST_BLOCKS, FIRST_BLOCKS)
    assert same is not None and same.empty


def test_files_are_listed_per_version_and_the_later_one_opens_v2(two_captures, signed_in):
    v1 = LegacySourceResourceImport.objects.get().document_version
    v2 = two_captures["v2"]
    latest = _flow(_get(signed_in, two_captures["link"]))
    assert reverse("documents:open", kwargs={"pk": v2.pk}) in latest
    assert reverse("documents:open", kwargs={"pk": v1.pk}) not in latest
    assert "versioon 2, OneNote'is muudetud" in latest and "06.11.2026" in latest
    assert "Uus versioon selles hõives" in latest
    assert 'lisatud <time datetime="2024-03-12">12.03.2024</time>' in latest
    first = _flow(_get(signed_in, two_captures["link"], versions.ORIGINAL))
    assert reverse("documents:open", kwargs={"pk": v1.pk}) in first
    assert reverse("documents:open", kwargs={"pk": v2.pk}) not in first
    assert "Uus versioon" not in first and "versioon 2" not in first


def test_a_file_a_later_capture_brought_is_not_on_the_earlier_version(two_captures, signed_in):
    link = two_captures["link"]
    LegacySourceResource.objects.create(
        source_page=link.source_page,
        resource_key="{F2}{9}{B0}",
        original_filename="Hilisem_lisa.pdf",
        sha256="3" * 64,
        size_bytes=10,
        archive_relative_path="cap-2/blobs/y",
    )
    latest = _flow(_get(signed_in, link))
    assert "Hilisem_lisa.pdf" in latest and "Uus selles versioonis" in latest
    assert "1 uus fail." in latest
    assert "Hilisem_lisa.pdf" not in _flow(_get(signed_in, link, versions.ORIGINAL))


def test_an_implausible_file_time_claims_no_day(normal_matter, signed_in):
    link = _page(normal_matter)
    _later_capture(link, file_modified=datetime(2030, 1, 1, tzinfo=UTC))  # after the import
    flow = _flow(_get(signed_in, link))
    assert "versioon 2" in flow and "OneNote'is muudetud <time" not in flow


def test_the_first_record_and_its_files_are_never_rewritten(two_captures, signed_in):
    page = LegacySourcePage.objects.get(pk=two_captures["link"].source_page_id)
    assert page.blocks == FIRST_BLOCKS and page.title == "Näidisseaduse eelnõu"
    assert page.source_modified_at == T_FIRST_MODIFIED and page.capture_id == "cap-1"
    record = LegacySourceResourceImport.objects.get()
    before = (record.document_version_id, record.document_version.sha256, record.resource.sha256)
    for token in (None, versions.ORIGINAL, two_captures["revision"].pk):
        _get(signed_in, two_captures["link"], token)
    page.refresh_from_db()
    record.refresh_from_db()
    assert page.blocks == FIRST_BLOCKS and page.source_modified_at == T_FIRST_MODIFIED
    assert (
        record.document_version_id,
        record.document_version.sha256,
        record.resource.sha256,
    ) == before
    assert DocumentVersion.objects.filter(document=record.document).count() == 2
    assert record.document_version.sha256 == hashlib.sha256(V1).hexdigest()


def test_a_page_read_once_renders_as_before(normal_matter, signed_in):
    link = _page(normal_matter)
    flow = _flow(_get(signed_in, link))
    assert "sourceversion" not in flow and "Viimane hõive" not in flow
    assert EDITED_BEFORE in flow
    assert signed_in.get(_url(link, versions.ORIGINAL)).status_code == 200


def test_a_revisions_xml_is_served_through_the_same_door(normal_matter, signed_in):
    from django.core.files.base import ContentFile

    from app.legacy_import.historical_apply import legacy_source_storage

    key = "test/cap-2/page.source.xml"
    legacy_source_storage().save(key, ContentFile(b"<one:Page/>"))
    link = _page(normal_matter)
    revision, _v2 = _later_capture(link, xml_key=key)
    two_captures = {"link": link, "revision": revision}
    url = reverse("legacy_import:source_xml", kwargs={"pk": two_captures["link"].pk})
    response = signed_in.get(f"{url}?hoive={two_captures['revision'].pk}")
    assert response.status_code == 200
    assert b"".join(response.streaming_content) == b"<one:Page/>"
    assert "-v2.xml" in response["Content-Disposition"]
    assert signed_in.get(f"{url}?hoive=tundmatu").status_code == 404
    # The first record has no XML stored here: still a 404, not the revision's.
    assert signed_in.get(f"{url}?hoive={versions.ORIGINAL}").status_code == 404


# -- the door -------------------------------------------------------------------------------


@pytest.mark.parametrize("token", [None, versions.ORIGINAL, "revision"])
def test_a_restricted_teemas_versions_are_refused_to_an_outsider(restricted_matter, client, token):
    link = _page(restricted_matter)
    revision, _v2 = _later_capture(link)
    value = str(revision.pk) if token == "revision" else token  # noqa: S105
    client.force_login(factories.AdministratorFactory())
    assert client.get(_url(link, value)).status_code == 404
    xml = reverse("legacy_import:source_xml", kwargs={"pk": link.pk})
    assert client.get(f"{xml}?hoive={revision.pk}").status_code == 404
    client.force_login(restricted_matter.owner)
    assert client.get(_url(link, value)).status_code == 200


def test_a_deleted_teemas_link_opens_no_version(two_captures, signed_in):
    matter = two_captures["matter"]
    type(matter).objects.filter(pk=matter.pk).update(deleted_at=timezone.now())
    url = _url(two_captures["link"], two_captures["revision"].pk)
    assert signed_in.get(url).status_code == 404


def test_an_anonymous_reader_is_sent_to_sign_in(two_captures, client):
    response = client.get(_url(two_captures["link"], two_captures["revision"].pk))
    assert response.status_code == 302


# -- «Tegevused» --------------------------------------------------------------------------


def _timeline(matter, user):
    items, _more = matter_timeline(matter=matter, user=user)
    return items


def test_notes_come_from_the_latest_version_once_on_their_own_dates(two_captures, specialist):
    rows = [
        r
        for r in _timeline(two_captures["matter"], specialist)
        if r.item_type == HISTORICAL_NOTE_ITEM
    ]
    by_date = {r.historical_note.date_display: r.historical_note for r in rows}
    assert sorted(by_date) == ["05.11.2026", "12.03.2024"], "a note duplicated or lost"
    assert len(rows) == 2
    first = by_date["12.03.2024"]
    # The link opens the version the note was read from, at its block.
    expected = _url(two_captures["link"], two_captures["revision"].pk)
    assert first.source_url == f"{expected}#plokk-0"
    assert by_date["05.11.2026"].source_url == f"{expected}#plokk-5"
    assert first.source_ref.startswith("cap-2")


def test_a_note_anchor_names_a_block_of_the_version_it_links_to(
    two_captures, signed_in, specialist
):
    rows = [
        r
        for r in _timeline(two_captures["matter"], specialist)
        if r.item_type == HISTORICAL_NOTE_ITEM
    ]
    for row in rows:
        url, anchor = row.historical_note.source_url.split("#")
        html = signed_in.get(url).content.decode()
        target = re.search(rf'id="{anchor}"[^>]*>(.*?)</', html, re.S)
        assert target is not None, anchor
        assert row.historical_note.paragraphs[0][:20] in target.group(1)


def test_without_a_revision_a_note_links_to_the_page_as_before(normal_matter, specialist):
    link = _page(normal_matter)
    rows = [r for r in _timeline(normal_matter, specialist) if r.item_type == HISTORICAL_NOTE_ITEM]
    assert [r.historical_note.source_url for r in rows] == [f"{_url(link)}#plokk-0"]


def test_a_later_file_version_reads_on_its_modification_day_never_under_a_note(
    two_captures, specialist
):
    v1 = LegacySourceResourceImport.objects.get().document_version
    rows = _timeline(two_captures["matter"], specialist)
    note = next(
        r
        for r in rows
        if r.item_type == HISTORICAL_NOTE_ITEM and r.historical_note.date_display == "12.03.2024"
    )
    under = [f.version_id for f in note.historical_note.direct_files]
    assert under == [v1.pk], "the note's day dated a later version"
    imports = [r for r in rows if r.item_type == HISTORICAL_IMPORT_ITEM]
    later = [
        f for r in imports for f in r.historical_files if f.version_id == two_captures["v2"].pk
    ]
    assert len(later) == 1
    assert later[0].added_display == "OneNote'is muudetud 06.11.2026"
    row = next(
        r for r in imports if any(f.version_id == two_captures["v2"].pk for f in r.historical_files)
    )
    assert timezone.localdate(row.occurred_at) == datetime(2026, 11, 6).date()


def test_an_implausible_modification_time_never_dates_the_version(normal_matter, specialist):
    link = _page(normal_matter)
    _later, v2 = _later_capture(link, file_modified=datetime(2020, 1, 1, tzinfo=UTC))
    imports = [
        r for r in _timeline(normal_matter, specialist) if r.item_type == HISTORICAL_IMPORT_ITEM
    ]
    later = [f for r in imports for f in r.historical_files if f.version_id == v2.pk]
    assert len(later) == 1 and "muudetud" not in later[0].added_display


def test_an_outsider_gets_no_note_from_a_restricted_teemas_versions(restricted_matter):
    link = _page(restricted_matter)
    _later_capture(link)
    from app.legacy_import.historical_notes import matter_notes

    outsider = factories.AdministratorFactory()
    assert matter_notes(matter=restricted_matter, user=outsider, files={}) == ()
    assert matter_notes(matter=restricted_matter, user=restricted_matter.owner, files={})
