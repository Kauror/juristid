"""A later sealed capture of the same invented notebook (docs/adr/0205, amendment 2026-11-05).

Every test applies capture 1, then builds capture 2 of the same invented page
and plans/applies it. The contract fixed here is what a later capture may add,
what it must hold for a person, and what it must never do: overwrite an
immutable ``DocumentVersion``, claim that two blocks are one file without
proof, or make a second Document of evidence the Teema already holds.

Invented captures only: invented pages, files and Teemad.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest
from django.forms.models import model_to_dict

from app.documents.models import Document, DocumentVersion
from app.legacy_import import onenote_capture as oc
from app.legacy_import.models import ImportBatch
from app.legacy_import.source_pages import (
    LegacySourcePage,
    LegacySourceResource,
    LegacySourceResourceImport,
    MatterSourcePage,
    ResourceImportState,
    SourceSystem,
)
from tests.test_onenote_capture_import import register_teema, reseal, stored_link

pytestmark = pytest.mark.django_db

T1 = "2021-08-30T08:48:10.000Z"
T2 = "2023-02-01T21:30:00.000Z"
T3 = "2024-05-06T07:00:00.000Z"
T4 = "2025-01-02T03:04:05.000Z"
PAGE_CREATED = "2021-08-01T10:00:00.000Z"
GUID = "0000AA21-0000-4000-8000-0000000000A1"
COPY_GUID = "0000AA22-0000-4000-8000-0000000000A2"
OTHER_GUID = "0000AA23-0000-4000-8000-0000000000A3"


def f(block, name, created, *, data=None, comment="", floating=False):
    """One file on the page: its block object ID, name, block creation time, bytes."""
    return {
        "block": block,
        "name": name,
        "created": None if floating else created,
        "floating": floating,
        "data": data if data is not None else f"sünteetiline {name}".encode(),
        "comment": comment,
    }


def build(root: Path, capture_id: str, pages: list[dict], *, link_pages: bool = True) -> Path:
    """A sealed capture (schema /2) whose block IDs the test chooses."""
    root.mkdir(parents=True)
    (root / "blobs").mkdir()
    manifest_pages, occurrences, blobs = [], [], {}
    for p in pages:
        tag, guid = p["tag"], p["guid"]
        if link_pages:
            for m in p["matters"]:
                state = m.lovable_sync_state
                state.applied = {"onenote_link": stored_link(guid)}
                state.save(update_fields=["applied"])
        (root / "pages" / tag).mkdir(parents=True)
        post = p.get("post", f"<post>{tag} {p.get('text', 'Lehe tekst')}</post>")
        for name, body in (
            ("pre.xml", b"<pre/>"),
            ("post.xml", post.encode()),
            ("receipt.json", b"{}"),
        ):
            (root / "pages" / tag / name).write_bytes(body)
        blocks = [
            {
                "ordinal": 0,
                "kind": "TEXT",
                "text": p.get("text", "Lehe tekst"),
                "object_id": f"t-{capture_id}",
                "created": PAGE_CREATED,
                "modified": PAGE_CREATED,
                "depth": 0,
            }
        ]
        for ordinal, x in enumerate(p["files"], start=1):
            sha = hashlib.sha256(x["data"]).hexdigest()
            blobs[sha] = x["data"]
            blocks.append(
                {
                    "ordinal": ordinal,
                    "kind": "FILE",
                    "text": "",
                    "object_id": x["block"],
                    "created": x["created"] or "",
                    "modified": x["created"] or "",
                    "depth": 1,
                }
            )
            context = (
                [{"basis": "ATTACHED_NOTE", "attributed": True, "text": [x["comment"]]}]
                if x["comment"]
                else []
            )
            occurrences.append(
                {
                    "occurrence_key": f"{guid}|{x['block']}",
                    "page_tag": tag,
                    "page_guid": guid,
                    "ordinal": ordinal,
                    "block_object_id": x["block"],
                    "block_ordinal": ordinal,
                    "floating": x["floating"],
                    "filename": x["name"],
                    "size": len(x["data"]),
                    "sha256": sha,
                    "copied_at": "2026-11-05T09:00:00.000Z",
                    "block_created": x["created"],
                    "block_modified": x["created"],
                    "object_modified": "2025-10-15T12:51:18.000Z" if x["floating"] else None,
                    "context": context,
                }
            )
        manifest_pages.append(
            {
                "tag": tag,
                "page_object_id": p.get("object_id", f"{{OBJ-{capture_id}-{tag}}}{{1}}{{B0}}"),
                "page_guid": guid,
                "title": p.get("title", "Näidisleht"),
                "section": p.get("section", "Sektsioon"),
                "notebook": "Näidismärkmik",
                "section_groups": [],
                "parent_object_id": None,
                "level": 1,
                "archived": False,
                "onenote_hyperlink": f"onenote:https://example.invalid/x.one#p&page-id={{{guid}}}",
                "page_created": PAGE_CREATED,
                "page_modified": "2026-01-01T00:00:00.000Z",
                "matters": [
                    {
                        "matter_id": str(m.pk),
                        "lovable_source_id": str(m.lovable_sync_state.source_id),
                        "reference": m.lovable_sync_state.reference,
                    }
                    for m in p["matters"]
                ],
                "blocks": blocks,
            }
        )
    for sha, content in blobs.items():
        (root / "blobs" / sha).write_bytes(content)
    counts = {
        "pages": len(manifest_pages),
        "occurrences": len(occurrences),
        "distinct_blobs": len(blobs),
        "dated": sum(1 for o in occurrences if o["block_created"]),
        "floating": sum(1 for o in occurrences if o["floating"]),
        "structural_comment_files": sum(1 for o in occurrences if o["context"]),
        "topics": sum(len(p["matters"]) for p in manifest_pages),
        "shared_pages": sum(1 for p in manifest_pages if len(p["matters"]) > 1),
    }
    manifest = {
        "schema": oc.SCHEMA,
        "capture_id": capture_id,
        "package": f"{capture_id}-package",
        "counts": counts,
        "pages": manifest_pages,
        "occurrences": occurrences,
    }
    (root / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False), encoding="utf-8")
    reseal(root)
    return root


def _approve(plan):
    return {
        "operator_intent": oc.OPERATOR_INTENT,
        "expect_manifest_sha256": plan.capture.manifest_sha256,
        "expect_plan_sha256": plan.digest,
        "backup_set": "sets/synthetic",
        "label": "test",
    }


def run(root: Path):
    """Plan and apply every page of a capture; return (plan, report)."""
    capture = oc.load_capture(root)
    plan = oc.build_plan(capture)
    assert plan.problems == [], plan.problems
    pages = oc.select_pages(plan) + oc.select_pages(plan, include_shared=True)
    report = oc.apply(plan, pages, **_approve(plan))
    return plan, report


def snapshot():
    return {
        m.__name__: m.objects.count()
        for m in (
            Document,
            DocumentVersion,
            LegacySourcePage,
            LegacySourceResource,
            LegacySourceResourceImport,
            MatterSourcePage,
            ImportBatch,
        )
    }


def versions():
    return {
        v.pk: (v.sha256, v.size_bytes, v.storage_key, v.document_id)
        for v in DocumentVersion.objects.all()
    }


@pytest.fixture
def enabled(settings):
    settings.ONENOTE_CAPTURE_IMPORT_ENABLED = True
    settings.REAL_DATA_ALLOWED = True
    return settings


@pytest.fixture
def teema():
    return register_teema("2026_21")


FIRST = [
    f("{B1}", "kiri.pdf", T1, comment="Ministeeriumi kiri"),
    f("{B2}", "vastus.docx", T2),
    f("{B3}", "lõuend.pdf", None, floating=True),
]


@pytest.fixture
def applied(tmp_path, teema, enabled):
    """Capture 1 applied: three Documents on the Teema."""
    root = build(
        tmp_path / "c1", "cap-1", [{"tag": "001", "guid": GUID, "matters": [teema], "files": FIRST}]
    )
    run(root)
    assert Document.objects.filter(matter=teema).count() == 3
    return {"teema": teema, "tmp": tmp_path}


def second(applied, files, **page):
    page = {"tag": "001", "guid": GUID, "matters": [applied["teema"]], "files": files, **page}
    return build(applied["tmp"] / "c2", "cap-2", [page])


def items(plan):
    return {(i.resource_key, i.disposition, i.reason) for i in plan.items}


def test_a_first_captures_decisions_and_digest_are_what_they_were(tmp_path, teema):
    """No earlier capture rows: the plan digest is the pre-amendment formula exactly."""
    from app.legacy_import.integrity import canonical_sha256

    root = build(
        tmp_path / "c1", "cap-1", [{"tag": "001", "guid": GUID, "matters": [teema], "files": FIRST}]
    )
    plan = oc.build_plan(oc.load_capture(root))
    assert plan.earlier_blocks == {} and {i.disposition for i in plan.items} == {"PROMOTE"}
    assert plan.digest == canonical_sha256(
        {
            "manifest_sha256": plan.capture.manifest_sha256,
            "sha256sums_sha256": plan.capture.sums_sha256,
            "importer": oc.IMPORTER_VERSION,
            "items": [item.__dict__ for item in plan.items],
            "links": [(str(teema.pk), f"p-{GUID.lower()}")],
            "known_pages": [],
            "problems": [],
        }
    )


# -- case 1: an unchanged capture -------------------------------------------------------


def test_an_unchanged_later_capture_writes_nothing_and_says_so(applied):
    root = second(applied, FIRST)
    before, held_versions = snapshot(), versions()
    capture = oc.load_capture(root)
    plan = oc.build_plan(capture)
    s = plan.summary()
    assert plan.problems == []
    assert s["pages_already_known"] == 1 and s["pages_pending"] == 0 and s["pages_done"] == 1
    assert s["items_already_recorded"] == s["items"] == 3
    assert s["earlier_files_not_in_this_capture"] == 0
    assert oc.select_pages(plan) == []
    report = oc.apply(plan, oc.select_pages(plan), **_approve(plan))
    assert report.batch_id == "" and snapshot() == before and versions() == held_versions
    assert oc.verify(capture)["problems"] == []


# -- case 2: a new attachment on a page an earlier capture completed ---------------------


def test_a_new_attachment_on_a_completed_page_is_imported_not_skipped_forever(applied):
    teema = applied["teema"]
    root = second(applied, [*FIRST, f("{B4}", "uus.pdf", T3)])
    page_row = LegacySourcePage.objects.get()
    frozen = model_to_dict(page_row)
    plan, report = run(root)
    assert plan.summary()["pages_pending"] == 1
    assert ("{B4}", "PROMOTE", "") in items(plan)
    assert report.documents_created == 1 and report.pages_reused == 1
    assert Document.objects.filter(matter=teema).count() == 4
    # The page row stays the earlier capture's own record (0205 §3).
    page_row.refresh_from_db()
    assert model_to_dict(page_row) == frozen and page_row.capture_id == "cap-1"
    assert oc.verify(oc.load_capture(root), require_complete=True)["problems"] == []


# -- case 3: new bytes under an existing attachment identity ------------------------------


def test_changed_bytes_under_the_same_block_are_held_and_never_overwrite(applied):
    teema = applied["teema"]
    changed = [FIRST[0], f("{B2}", "vastus.docx", T2, data=b"muudetud sisu"), FIRST[2]]
    root = second(applied, [*changed, f("{B4}", "uus.pdf", T3)])
    held_versions = versions()
    old = LegacySourceResource.objects.get(resource_key="{B2}")
    frozen = model_to_dict(old)
    capture = oc.load_capture(root)
    plan = oc.build_plan(capture)
    # One changed file no longer refuses the whole capture: it is held, by name.
    assert plan.problems == []
    assert ("{B2}", "HOLD", oc.HOLD_BYTES_CHANGED) in items(plan)
    assert plan.summary()["plan_only_holds"] == {oc.HOLD_BYTES_CHANGED: 1}
    report = oc.apply(plan, oc.select_pages(plan), **_approve(plan))
    assert report.documents_created == 1  # the new attachment still arrives
    # Every earlier version is byte-for-byte the same row; the catalogue row too.
    assert {k: v for k, v in versions().items() if k in held_versions} == held_versions
    old.refresh_from_db()
    assert model_to_dict(old) == frozen
    assert Document.objects.filter(matter=teema).count() == 4
    # verify says plainly that the database does not hold this capture's bytes.
    problems = oc.verify(capture)["problems"]
    assert problems and all("{B2}" in p for p in problems)


@pytest.mark.xfail(
    strict=True,
    reason="NOT SUPPORTED: an edited attachment as version 2 of its Document needs a "
    "per-capture resource revision (ADR 0205 amendment 2026-11-05, proposal P1)",
)
def test_changed_bytes_become_a_second_version_of_the_same_document(applied):
    changed = [FIRST[0], f("{B2}", "vastus.docx", T2, data=b"muudetud sisu"), FIRST[2]]
    run(second(applied, changed))
    document = Document.objects.get(matter=applied["teema"], title="vastus.docx")
    assert document.versions.count() == 2


# -- case 4: a changed structural comment, unchanged bytes -------------------------------


def test_a_changed_comment_is_reported_and_the_documents_note_is_kept(applied):
    note_before = Document.objects.get(title="kiri.pdf").provenance_note
    changed = [f("{B1}", "kiri.pdf", T1, comment="Uus märkus"), *FIRST[1:]]
    root = second(applied, changed)
    before = snapshot()
    plan, report = run(root)
    assert ("{B1}", "HOLD", oc.HOLD_COMMENT_CHANGED) in items(plan)
    assert plan.summary()["plan_only_holds"] == {oc.HOLD_COMMENT_CHANGED: 1}
    assert report.batch_id == "" and snapshot() == before
    assert Document.objects.get(title="kiri.pdf").provenance_note == note_before


def test_an_unchanged_comment_is_not_reported_as_changed(applied):
    plan = oc.build_plan(oc.load_capture(second(applied, FIRST)))
    assert not any(i.reason == oc.HOLD_COMMENT_CHANGED for i in plan.items)


@pytest.mark.xfail(
    strict=True,
    reason="NOT SUPPORTED: recording a later capture's comment beside the earlier one needs "
    "per-capture occurrence context (ADR 0205 amendment 2026-11-05, proposal P2)",
)
def test_a_changed_comment_is_recorded_beside_the_earlier_one(applied):
    changed = [f("{B1}", "kiri.pdf", T1, comment="Uus märkus"), *FIRST[1:]]
    run(second(applied, changed))
    assert "Uus märkus" in Document.objects.get(title="kiri.pdf").provenance_note


# -- case 5: a moved page whose block IDs were all re-issued ------------------------------

MOVED = [
    f("{N1}", "kiri.pdf", T1, comment="Ministeeriumi kiri"),
    f("{N2}", "vastus.docx", T2),
    f("{N3}", "lõuend.pdf", None, floating=True),
]


def test_a_pure_move_continues_the_earlier_files_and_makes_no_second_document(applied):
    teema = applied["teema"]
    root = second(applied, MOVED, section="Arhiiv", title="Teisaldatud leht")
    held_versions = versions()
    capture = oc.load_capture(root)
    plan = oc.build_plan(capture)
    assert plan.problems == []
    got = items(plan)
    assert ("{N1}", "HOLD", oc.SKIP_CONTINUES) in got
    assert ("{N2}", "HOLD", oc.SKIP_CONTINUES) in got
    # A floating file has no creation time: same name and bytes are not proof.
    assert ("{N3}", "HOLD", oc.HOLD_RESEMBLES) in got
    digest = plan.digest
    report = oc.apply(plan, oc.select_pages(plan), **_approve(plan))
    assert report.documents_created == 0
    assert Document.objects.filter(matter=teema).count() == 3 and versions() == held_versions
    row = LegacySourceResourceImport.objects.get(resource__resource_key="{N1}")
    assert row.state == ResourceImportState.SKIPPED and row.error_code == oc.SKIP_CONTINUES
    assert "{B1}" in row.error_detail and row.document_id is None
    # The new block identities are catalogued with their own times.
    assert LegacySourceResource.objects.get(resource_key="{N2}").source_added_at is not None
    again = oc.build_plan(capture)
    assert again.digest == digest and oc.select_pages(again) == []
    assert oc.verify(capture, require_complete=True)["problems"] == []


def test_a_move_with_changed_bytes_is_held_not_continued(applied):
    moved = [MOVED[0], f("{N2}", "vastus.docx", T2, data=b"uued baidid"), MOVED[2]]
    plan, report = run(second(applied, moved))
    assert ("{N2}", "HOLD", oc.HOLD_MOVED_AND_CHANGED) in items(plan)
    assert report.documents_created == 0
    row = LegacySourceResourceImport.objects.get(resource__resource_key="{N2}")
    assert row.error_code == oc.HOLD_MOVED_AND_CHANGED and row.document_id is None


def test_two_earlier_files_with_one_time_and_name_are_ambiguous(tmp_path, teema, enabled):
    twins = [f("{B1}", "lisa.pdf", T3, data=b"esimene"), f("{B2}", "lisa.pdf", T3, data=b"teine")]
    run(
        build(
            tmp_path / "c1",
            "cap-1",
            [{"tag": "001", "guid": GUID, "matters": [teema], "files": twins}],
        )
    )
    moved = [f("{N1}", "lisa.pdf", T3, data=b"esimene"), f("{N2}", "lisa.pdf", T3, data=b"teine")]
    plan, report = run(
        build(
            tmp_path / "c2",
            "cap-2",
            [{"tag": "001", "guid": GUID, "matters": [teema], "files": moved}],
        )
    )
    assert {i.reason for i in plan.items} == {oc.HOLD_AMBIGUOUS}
    assert report.documents_created == 0 and Document.objects.count() == 2


def test_a_floating_file_is_never_matched_by_filename_alone(applied):
    # Same name, other bytes, no creation time: a new file, not the earlier one.
    moved = [*MOVED[:2], f("{N3}", "lõuend.pdf", None, floating=True, data=b"teine pilt")]
    plan, report = run(second(applied, moved))
    assert ("{N3}", "PROMOTE", "") in items(plan)
    assert report.documents_created == 1
    assert not plan.earlier_blocks.get((f"p-{GUID.lower()}", "{N3}"))


def test_a_dated_file_with_the_same_name_at_another_time_is_a_new_file(applied):
    moved = [*MOVED[:2], f("{N4}", "kiri.pdf", T4, data=b"uus kiri")]
    plan, _report = run(second(applied, moved))
    assert ("{N4}", "PROMOTE", "") in items(plan)


# -- case 6: a copied page (new page GUID, the old times kept) ----------------------------


def test_a_copied_page_is_a_new_page_and_its_repeated_files_are_held(applied):
    teema = applied["teema"]
    copied = [
        f("{C1}", "kiri.pdf", T1, comment="Ministeeriumi kiri"),
        f("{C2}", "vastus.docx", T2),
        f("{C3}", "uus.pdf", T3),
    ]
    root = build(
        applied["tmp"] / "c2",
        "cap-2",
        [{"tag": "001", "guid": COPY_GUID, "matters": [teema], "files": copied}],
    )
    plan, report = run(root)
    got = items(plan)
    assert ("{C1}", "HOLD", oc.HOLD_SAME_FILE_ELSEWHERE) in got
    assert ("{C2}", "HOLD", oc.HOLD_SAME_FILE_ELSEWHERE) in got
    assert ("{C3}", "PROMOTE", "") in got  # genuinely new on the copy
    assert report.pages_created == 1 and report.documents_created == 1
    # Two pages, two identities: the copy never continues the original.
    assert MatterSourcePage.objects.filter(matter=teema).count() == 2
    assert not plan.earlier_blocks


# -- case 7: an attachment removed from the page -----------------------------------------


def test_a_removed_attachment_keeps_its_document_and_is_counted(applied):
    teema = applied["teema"]
    root = second(applied, [FIRST[0], FIRST[2]])
    held_versions, before = versions(), snapshot()
    capture = oc.load_capture(root)
    plan = oc.build_plan(capture)
    s = plan.summary()
    assert s["earlier_files_not_in_this_capture"] == 1 and s["pages_pending"] == 0
    report = oc.apply(plan, oc.select_pages(plan), **_approve(plan))
    assert report.batch_id == "" and snapshot() == before and versions() == held_versions
    assert Document.objects.filter(matter=teema, title="vastus.docx").exists()
    result = oc.verify(capture, require_complete=True)
    assert result["problems"] == [] and result["counts"]["earlier_capture_rows"] == 1


def test_a_removed_attachment_with_other_changes_gets_no_new_row(applied):
    root = second(applied, [FIRST[0], FIRST[2], f("{B4}", "uus.pdf", T3)])
    run(root)
    gone = LegacySourceResource.objects.get(resource_key="{B2}")
    assert list(gone.imports.values_list("state", flat=True)) == [ResourceImportState.IMPORTED]


# -- case 8: a Lovable locator change that is only observed ------------------------------


def test_an_observed_but_unapplied_link_change_is_reported_not_followed(applied):
    teema = applied["teema"]
    state = teema.lovable_sync_state
    root = second(applied, FIRST)
    state.refresh_from_db()
    applied_before = dict(state.applied)
    state.observed = {"onenote_link": stored_link(OTHER_GUID)}
    state.save(update_fields=["observed"])
    plan = oc.build_plan(oc.load_capture(root))
    # The plan checks the register's applied link, which still names this page.
    assert plan.problems == [] and plan.summary()["observed_link_differs"] == 1
    state.refresh_from_db()
    assert state.applied == applied_before


def test_a_capture_that_follows_an_unapplied_observed_link_is_refused(applied):
    teema = applied["teema"]
    state = teema.lovable_sync_state
    root = build(
        applied["tmp"] / "c2",
        "cap-2",
        [{"tag": "001", "guid": OTHER_GUID, "matters": [teema], "files": FIRST[:1]}],
        link_pages=False,
    )
    state.refresh_from_db()
    state.observed = {"onenote_link": stored_link(OTHER_GUID)}
    state.save(update_fields=["observed"])
    plan = oc.build_plan(oc.load_capture(root))
    assert any("not name exactly this page" in p and "not applied" in p for p in plan.problems)


def test_the_production_shaped_applied_link_is_read_by_its_halves(applied):
    """``applied["onenote_link"]`` is ``{"source", "target"}`` in production, not a string."""
    teema = applied["teema"]
    root = second(applied, FIRST)
    state = teema.lovable_sync_state
    state.applied = {"onenote_link": {"source": stored_link(GUID), "target": stored_link(GUID)}}
    state.save(update_fields=["applied"])
    assert oc.build_plan(oc.load_capture(root)).problems == []
    state.applied = {
        "onenote_link": {"source": stored_link(GUID), "target": stored_link(OTHER_GUID)}
    }
    state.save(update_fields=["applied"])
    assert any(
        "not name exactly this page" in p for p in oc.build_plan(oc.load_capture(root)).problems
    )


# -- the source-page view after a later capture (task 2) ----------------------------------


def _view_blocks(teema):
    from app.legacy_import.historical_views import _rendered_blocks

    link = MatterSourcePage.objects.get(matter=teema)
    page = link.source_page
    documents = {
        r.resource.resource_key: r
        for r in link.resource_imports.select_related("resource", "document", "document_version")
    }
    resources = {r.resource_key: r for r in page.resources.all()}
    readable = {r.document_id for r in documents.values() if r.document_id}
    return _rendered_blocks(page, resources, documents, readable=readable)


def test_the_page_view_shows_a_later_captures_new_file_and_says_whose_body_it_is(applied):
    teema = applied["teema"]
    run(second(applied, [*FIRST, f("{B4}", "uus.pdf", T3)]))
    blocks = _view_blocks(teema)
    files = [b for b in blocks if b["kind"] == "file"]
    assert [b["filename"] for b in files] == ["kiri.pdf", "vastus.docx", "lõuend.pdf", "uus.pdf"]
    assert files[-1]["document"] is not None and files[-1]["state"] == "imported"
    label = next(b for b in blocks if b["kind"] == "text" and "cap-1" in b["text"])
    assert blocks.index(label) == len(blocks) - 2  # the label sits right before the new file


def test_the_page_view_does_not_repeat_a_continued_file(applied):
    run(second(applied, MOVED))
    files = [b for b in _view_blocks(applied["teema"]) if b["kind"] == "file"]
    # kiri and vastus continue B1/B2; the floating file is held for review and shown once more.
    assert [b["filename"] for b in files] == ["kiri.pdf", "vastus.docx", "lõuend.pdf", "lõuend.pdf"]
    assert files[-1]["document"] is None


def test_a_pilot_page_reused_by_a_capture_shows_the_captures_files(tmp_path, teema, enabled):
    """An October-pilot page row reused unchanged (ADR 0205 §3): pilot body, capture files."""
    from datetime import UTC, datetime

    pilot = LegacySourcePage.objects.create(
        source_system=SourceSystem.ONENOTE_DESKTOP,
        source_page_id="{PILOT}{1}{B0}",
        page_key=f"p-{GUID.lower()}",
        source_notebook="n",
        source_section="vana",
        title="piloodi pealkiri",
        capture_id="pilot-2026-10",
        derived_text="Piloodi tekst",
        blocks=[
            {"ordinal": 0, "kind": "TEXT", "text": "Piloodi tekst", "resource_key": "", "depth": 0},
            {
                "ordinal": 1,
                "kind": "FILE_ATTACHMENT",
                "text": "",
                "resource_key": "r001",
                "depth": 0,
            },
        ],
        first_imported_at=datetime(2026, 10, 20, tzinfo=UTC),
        latest_imported_at=datetime(2026, 10, 20, tzinfo=UTC),
    )
    LegacySourceResource.objects.create(
        source_page=pilot,
        resource_key="r001",
        original_filename="kiri.pdf",
        sha256="a" * 64,
        size_bytes=1,
        archive_relative_path="pilot/r001",
    )
    frozen = model_to_dict(pilot)
    run(
        build(
            tmp_path / "c1",
            "cap-1",
            [{"tag": "001", "guid": GUID, "matters": [teema], "files": FIRST}],
        )
    )
    pilot.refresh_from_db()
    assert model_to_dict(pilot) == frozen  # the pilot's record is never rewritten
    blocks = _view_blocks(teema)
    texts = [b["text"] for b in blocks if b["kind"] == "text"]
    files = [b for b in blocks if b["kind"] == "file"]
    assert texts[0] == "Piloodi tekst" and any("pilot-2026-10" in t for t in texts[1:])
    assert files[0]["filename"] == "kiri.pdf" and files[0]["state"] == "superseded"
    assert [b["filename"] for b in files[1:]] == ["kiri.pdf", "vastus.docx", "lõuend.pdf"]
    assert all(b["state"] == "imported" for b in files[1:])
