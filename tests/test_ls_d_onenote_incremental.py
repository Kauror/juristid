"""Incremental later captures (live-sync round 2026-11, agent D; ADR 0205 P1–P3).

Capture 1 of an invented page is applied, then a later sealed capture of the same
invented notebook is planned and applied. Each test fixes one classification:
what the later capture reads, what it writes, and what it must never do —
overwrite an evidence version, attribute a shared page's file to every Teema,
invent a date, or import anything twice.

Invented captures, pages, files and Teemad only.
"""

from __future__ import annotations

import hashlib
from datetime import datetime

import pytest
from django.forms.models import model_to_dict

from app.documents.models import Document, DocumentVersion
from app.documents.services import evidence_storage
from app.legacy_import import onenote_capture as oc
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
    SourceMatchMethod,
)
from tests.test_onenote_capture_changes import (
    FIRST,
    GUID,
    OTHER_GUID,
    T1,
    T2,
    T3,
    _approve,
    build,
    f,
    items,
    run,
    snapshot,
    versions,
)
from tests.test_onenote_capture_import import register_teema

pytestmark = pytest.mark.django_db

NEW_BYTES = b"muudetud sisu"


@pytest.fixture
def enabled(settings):
    settings.ONENOTE_CAPTURE_IMPORT_ENABLED = True
    settings.REAL_DATA_ALLOWED = True
    return settings


@pytest.fixture
def teema():
    return register_teema("2026_41")


@pytest.fixture
def applied(tmp_path, teema, enabled):
    root = build(
        tmp_path / "c1", "cap-1", [{"tag": "001", "guid": GUID, "matters": [teema], "files": FIRST}]
    )
    run(root)
    return {"teema": teema, "tmp": tmp_path}


def later(applied, files, capture_id="cap-2", **page):
    page = {"tag": "001", "guid": GUID, "matters": [applied["teema"]], "files": files, **page}
    return build(applied["tmp"] / capture_id, capture_id, [page])


def snapshot_all():
    out = snapshot()
    out["OneNoteResourceRevision"] = OneNoteResourceRevision.objects.count()
    out["OneNotePageRevision"] = OneNotePageRevision.objects.count()
    return out


def changed_b2(data=NEW_BYTES):
    return [FIRST[0], f("{B2}", "vastus.docx", T2, data=data), FIRST[2]]


# -- classification ---------------------------------------------------------------------


def test_every_occurrence_of_a_later_capture_is_classified(applied, tmp_path):
    other = register_teema("2026_42")
    root = build(
        tmp_path / "c2",
        "cap-2",
        [
            # SAME (B1), CHANGED_BYTES (B2), NEW_RESOURCE (B4); B3 is no longer on the page.
            {
                "tag": "001",
                "guid": GUID,
                "matters": [applied["teema"]],
                "files": [*changed_b2()[:2], f("{B4}", "uus.pdf", T3)],
            },
            # A page no capture has read before.
            {
                "tag": "002",
                "guid": OTHER_GUID,
                "matters": [other],
                "files": [f("{P1}", "teine.pdf", T1)],
            },
        ],
    )
    plan = oc.build_plan(oc.load_capture(root))
    assert plan.problems == []
    assert plan.classes == {
        oc.CLASS_SAME: 1,
        oc.CLASS_CHANGED_BYTES: 1,
        oc.CLASS_NEW_RESOURCE: 1,
        oc.CLASS_NEW_PAGE: 1,
        "NEW_PAGE_PAGES": 1,
        "REMOVED": 1,
    }
    assert plan.summary()["changes"] == dict(plan.classes)


def test_a_moved_block_is_matched_by_its_unique_creation_time_and_name(applied):
    moved = [f("{N1}", "kiri.pdf", T1, comment="Ministeeriumi kiri"), *FIRST[1:]]
    plan = oc.build_plan(oc.load_capture(later(applied, moved)))
    assert ("{N1}", oc.HOLD, oc.SKIP_CONTINUES) in items(plan)
    assert plan.classes[oc.CLASS_SAME_BLOCK_NEW_ID] == 1
    assert plan.classes["REMOVED"] == 0  # the earlier block is continued, not gone


# -- idempotency --------------------------------------------------------------------------


def test_applying_the_same_later_capture_twice_imports_nothing_the_second_time(applied):
    root = later(applied, [*changed_b2(), f("{B4}", "uus.pdf", T3)])
    first_plan, first = run(root)
    assert first.versions_created == 1 and first.documents_created == 1
    before, held = snapshot_all(), versions()
    again = oc.build_plan(oc.load_capture(root))
    assert again.digest == first_plan.digest
    assert oc.select_pages(again) == []
    report = oc.apply(again, oc.select_pages(again), **_approve(again))
    assert report.batch_id == "" and snapshot_all() == before and versions() == held
    # Even forced through the page writer, nothing is written twice.
    forced = oc.apply(again, again.capture.pages, **_approve(again))
    assert forced.versions_created == 0 and forced.documents_created == 0
    assert snapshot_all() == before


def test_a_third_capture_equal_to_the_second_adds_no_version(applied):
    run(later(applied, changed_b2()))
    before = snapshot_all()
    plan, report = run(later(applied, changed_b2(), capture_id="cap-3"))
    assert not any(i.disposition in oc.REVISING for i in plan.items)
    assert report.batch_id == "" and snapshot_all() == before


def test_bytes_returning_to_an_earlier_reading_are_the_next_version_not_a_rewind(applied):
    run(later(applied, changed_b2()))
    run(later(applied, FIRST, capture_id="cap-3"))
    document = Document.objects.get(matter=applied["teema"], title="vastus.docx")
    shas = list(document.versions.order_by("version_number").values_list("sha256", flat=True))
    original = hashlib.sha256(FIRST[1]["data"]).hexdigest()
    assert shas == [original, hashlib.sha256(NEW_BYTES).hexdigest(), original]


# -- changed bytes ------------------------------------------------------------------------


def test_changed_bytes_never_overwrite_the_earlier_version(applied):
    document = Document.objects.get(matter=applied["teema"], title="vastus.docx")
    (v1,) = document.versions.all()
    frozen_v1 = model_to_dict(v1)
    resource = LegacySourceResource.objects.get(resource_key="{B2}")
    frozen_resource = model_to_dict(resource)
    import_row = LegacySourceResourceImport.objects.get(resource=resource)
    frozen_row = model_to_dict(import_row)
    root = later(applied, changed_b2())
    run(root)
    v1.refresh_from_db()
    resource.refresh_from_db()
    import_row.refresh_from_db()
    assert model_to_dict(v1) == frozen_v1
    assert model_to_dict(resource) == frozen_resource
    assert model_to_dict(import_row) == frozen_row
    with evidence_storage().open(v1.storage_key, "rb") as handle:
        assert hashlib.sha256(handle.read()).hexdigest() == v1.sha256 == resource.sha256
    revision = OneNoteResourceRevision.objects.get(resource=resource)
    assert revision.state == RevisionState.VERSIONED and revision.kind == RevisionKind.CHANGED_BYTES
    assert revision.document_id == document.pk
    assert revision.document_version.version_number == 2
    assert revision.document_version.sha256 == hashlib.sha256(NEW_BYTES).hexdigest()
    assert revision.document_version.uploaded_by_id is None  # an import, no actor
    assert oc.verify(oc.load_capture(root), check_bytes=True)["problems"] == []


def test_before_the_later_capture_is_applied_verify_reports_the_difference(applied):
    capture = oc.load_capture(later(applied, changed_b2()))
    problems = oc.verify(capture)["problems"]
    assert problems and all("{B2}" in p for p in problems)


def test_changed_bytes_on_a_shared_page_are_held_for_both_teemad(tmp_path, enabled):
    a, b = register_teema("2026_43"), register_teema("2026_44")
    page = {"tag": "001", "guid": GUID, "matters": [a, b], "files": FIRST[:2]}
    run(build(tmp_path / "c1", "cap-1", [page]))
    assert Document.objects.count() == 0  # a shared page's files wait for a person
    page2 = {**page, "files": [FIRST[0], f("{B2}", "vastus.docx", T2, data=NEW_BYTES)]}
    plan, report = run(build(tmp_path / "c2", "cap-2", [page2]))
    held = {i for i in items(plan) if i[0] == "{B2}"}
    assert held == {("{B2}", oc.REVISE, oc.HOLD_SHARED_PAGE)}
    assert report.versions_created == 0 and Document.objects.count() == 0
    revisions = OneNoteResourceRevision.objects.filter(resource__resource_key="{B2}")
    assert sorted(revisions.values_list("state", flat=True)) == [RevisionState.HELD] * 2
    assert not revisions.exclude(document__isnull=True).exists()


def test_changed_bytes_of_a_removed_document_are_held_not_versioned(applied):
    document = Document.objects.get(matter=applied["teema"], title="vastus.docx")
    Document.objects.filter(pk=document.pk).update(removed_at=datetime(2026, 11, 8))
    plan, report = run(later(applied, changed_b2()))
    assert ("{B2}", oc.REVISE, oc.HOLD_DOCUMENT_REMOVED) in items(plan)
    assert report.versions_created == 0 and document.versions.count() == 1


def test_changed_bytes_that_are_empty_are_held(applied):
    plan, report = run(later(applied, changed_b2(data=b"")))
    assert ("{B2}", oc.REVISE, oc.HOLD_EMPTY_FILE) in items(plan)
    assert report.versions_created == 0
    revision = OneNoteResourceRevision.objects.get(resource__resource_key="{B2}")
    assert revision.state == RevisionState.HELD and revision.size_bytes == 0


def test_an_earlier_held_file_with_new_bytes_is_held_with_its_reading(tmp_path, enabled, teema):
    empty = [f("{E1}", "tühi.pdf", T1, data=b"")]
    run(
        build(
            tmp_path / "c1",
            "cap-1",
            [{"tag": "001", "guid": GUID, "matters": [teema], "files": empty}],
        )
    )
    filled = [f("{E1}", "tühi.pdf", T1, data="nüüd on sisu".encode())]
    plan, report = run(
        build(
            tmp_path / "c2",
            "cap-2",
            [{"tag": "001", "guid": GUID, "matters": [teema], "files": filled}],
        )
    )
    assert ("{E1}", oc.REVISE, oc.HOLD_BYTES_CHANGED) in items(plan)
    assert report.versions_created == 0 and Document.objects.count() == 0


def test_a_changed_block_time_is_held_and_the_resource_keeps_its_time(applied):
    resource = LegacySourceResource.objects.get(resource_key="{B2}")
    plan, _ = run(later(applied, [FIRST[0], f("{B2}", "vastus.docx", T3), FIRST[2]]))
    assert ("{B2}", oc.REVISE, oc.HOLD_TIME_CHANGED) in items(plan)
    revision = OneNoteResourceRevision.objects.get(resource=resource)
    assert revision.state == RevisionState.HELD and revision.kind == RevisionKind.CHANGED_TIME
    resource.refresh_from_db()
    assert resource.source_added_at == datetime.fromisoformat(T2.replace("Z", "+00:00"))


# -- new resources and new pages -----------------------------------------------------------


def test_a_new_file_on_a_shared_page_is_attributed_to_no_teema(tmp_path, enabled):
    a, b = register_teema("2026_45"), register_teema("2026_46")
    page = {"tag": "001", "guid": GUID, "matters": [a, b], "files": FIRST[:1]}
    run(build(tmp_path / "c1", "cap-1", [page]))
    plan, report = run(
        build(tmp_path / "c2", "cap-2", [{**page, "files": [FIRST[0], f("{B9}", "uus.pdf", T3)]}])
    )
    assert {i.disposition for i in plan.items if i.resource_key == "{B9}"} == {oc.HOLD}
    assert report.documents_created == 0
    assert not Document.objects.filter(matter__in=[a, b]).exists()


def test_a_new_page_is_linked_only_through_the_exact_register_page_guid(applied):
    teema = applied["teema"]
    # The Teema's stored link names GUID, not OTHER_GUID: a capture naming it for
    # OTHER_GUID is a problem, never a link.
    stray = build(
        applied["tmp"] / "c2",
        "cap-2",
        [{"tag": "002", "guid": OTHER_GUID, "matters": [teema], "files": FIRST[:1]}],
        link_pages=False,
    )
    plan = oc.build_plan(oc.load_capture(stray))
    assert any("does not name exactly this page" in p for p in plan.problems)
    other = register_teema("2026_47")
    root = build(
        applied["tmp"] / "c3",
        "cap-3",
        [{"tag": "002", "guid": OTHER_GUID, "matters": [other], "files": [f("{Q1}", "a.pdf", T1)]}],
    )
    _plan, report = run(root)
    assert report.pages_created == 1
    link = MatterSourcePage.objects.get(matter=other)
    assert link.match_method == SourceMatchMethod.LOVABLE_EXACT_PAGE_GUID
    assert link.source_page.page_key == f"p-{OTHER_GUID.lower()}"


# -- changed text --------------------------------------------------------------------------


def test_changed_page_text_is_a_new_reading_and_the_page_row_is_kept(applied):
    page = LegacySourcePage.objects.get()
    frozen = model_to_dict(page)
    root = later(applied, FIRST, text="Lehe tekst ja uus märge 08.11.2026")
    plan, report = run(root)
    assert plan.page_changes == {f"p-{GUID.lower()}": (True, False)}
    assert report.page_revisions_created == 1 and report.documents_created == 0
    page.refresh_from_db()
    assert model_to_dict(page) == frozen  # the first record stays
    revision = OneNotePageRevision.objects.get(page=page)
    assert "uus märge" in revision.derived_text and revision.text_changed
    assert revision.source_xml_storage_key != page.source_xml_storage_key
    assert revision.capture_id == "cap-2"
    # Applied again: no second reading.
    again = oc.build_plan(oc.load_capture(root))
    assert oc.select_pages(again) == [] and OneNotePageRevision.objects.count() == 1


def test_an_unchanged_text_under_new_modification_times_is_not_a_change(applied):
    # Navigation touches block modification times (Phase 1 §9c); the reading ignores them.
    plan = oc.build_plan(oc.load_capture(later(applied, FIRST)))
    assert plan.page_changes == {}


def test_a_page_moved_to_another_section_is_recorded_as_moved(applied):
    plan, report = run(later(applied, FIRST, section="ARHIIV-näidis", title="Uus pealkiri"))
    assert plan.page_changes == {f"p-{GUID.lower()}": (False, True)}
    revision = OneNotePageRevision.objects.get()
    assert revision.location_changed and not revision.text_changed
    assert revision.source_section == "ARHIIV-näidis"
    assert LegacySourcePage.objects.get().source_section == "Sektsioon"


def test_a_third_capture_compares_with_the_latest_reading(applied):
    run(later(applied, FIRST, text="Teine tekst"))
    plan, _ = run(later(applied, FIRST, capture_id="cap-3", text="Teine tekst"))
    assert plan.page_changes == {}
    plan, _ = run(later(applied, FIRST, capture_id="cap-4", text="Lehe tekst"))
    assert plan.page_changes == {f"p-{GUID.lower()}": (True, False)}
    assert OneNotePageRevision.objects.count() == 2


# -- dates -------------------------------------------------------------------------------


def test_a_new_version_keeps_file_dates_apart_from_event_dates(applied):
    run(later(applied, changed_b2()))
    revision = OneNoteResourceRevision.objects.get(resource__resource_key="{B2}")
    # The block's creation time stays its creation time; the capture moment is
    # only the version's acquisition time; nothing is a new event date.
    assert revision.source_added_at == datetime.fromisoformat(T2.replace("Z", "+00:00"))
    version = revision.document_version
    assert version.acquired_at == datetime.fromisoformat("2026-11-05T09:00:00+00:00")
    resource = LegacySourceResource.objects.get(resource_key="{B2}")
    assert resource.source_added_at == revision.source_added_at


def test_a_floating_files_new_reading_stays_undated(applied):
    floating = [*FIRST[:2], f("{B3}", "lõuend.pdf", None, floating=True, data=b"uus pilt")]
    run(later(applied, floating))
    revision = OneNoteResourceRevision.objects.get(resource__resource_key="{B3}")
    assert revision.source_added_at is None  # D8: never dated from file metadata
    assert revision.state == RevisionState.VERSIONED
    assert DocumentVersion.objects.filter(pk=revision.document_version_id).exists()


def test_a_pilot_page_row_is_never_compared_for_text(tmp_path, teema, enabled):
    from datetime import UTC

    from app.legacy_import.source_pages import SourceSystem

    LegacySourcePage.objects.create(
        source_system=SourceSystem.ONENOTE_DESKTOP,
        source_page_id="{PILOT}{1}{B0}",
        page_key=f"p-{GUID.lower()}",
        source_notebook="n",
        source_section="vana",
        title="piloot",
        capture_id="pilot-2026-10",
        derived_text="Piloodi tekst",
        blocks=[{"ordinal": 0, "kind": "TEXT", "text": "Piloodi tekst", "depth": 0}],
        first_imported_at=datetime(2026, 10, 20, tzinfo=UTC),
        latest_imported_at=datetime(2026, 10, 20, tzinfo=UTC),
    )
    root = build(
        tmp_path / "c1", "cap-1", [{"tag": "001", "guid": GUID, "matters": [teema], "files": FIRST}]
    )
    plan, _ = run(root)
    assert plan.page_changes == {} and OneNotePageRevision.objects.count() == 0
