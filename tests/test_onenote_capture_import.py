"""The OneNote capture import (docs/adr/0205), against invented captures only.

Every capture here is built in a temporary directory: invented pages, invented
files, invented Teemad. The tests fix the import's contract — what it may
write, what it must hold, what it must refuse — not anything about a real
notebook.
"""

from __future__ import annotations

import hashlib
import io
import json
import uuid
from datetime import UTC, datetime
from pathlib import Path

import pytest
from django.core.management import CommandError, call_command
from django.forms.models import model_to_dict

from app.documents.models import Document, DocumentVersion
from app.legacy_import import onenote_capture as oc
from app.legacy_import.lovable_sync_models import LovableSyncState
from app.legacy_import.models import ImportBatch, ReconciliationStatus
from app.legacy_import.source_pages import (
    LegacySourcePage,
    LegacySourceResource,
    LegacySourceResourceImport,
    MatterSourcePage,
    ResourceImportState,
    SourceMatchMethod,
    SourceSystem,
)
from app.matters.models import Matter
from app.matters.timeline import HISTORICAL_IMPORT_ITEM, matter_timeline
from tests import factories

pytestmark = pytest.mark.django_db

T1 = "2021-08-30T08:48:10.000Z"  # 30.8.2021 in Tallinn
T2 = "2023-02-01T21:30:00.000Z"  # 1.2.2023 UTC, but 1.2.2023 23:30 in Tallinn
T3 = "2023-02-01T22:30:00.000Z"  # 2.2.2023 00:30 in Tallinn: a different local day
PAGE_CREATED = "2021-08-01T10:00:00.000Z"


def _guid(n: int) -> str:
    return f"{n:08X}-0000-4000-8000-00000000AB{n:02X}"


def _file(
    n: int,
    name: str,
    created: str | None,
    *,
    floating: bool = False,
    data: bytes | None = None,
    modified: str | None = None,
    comment: str = "",
    adjacent: str = "",
) -> dict:
    content = data if data is not None else f"sünteetiline fail {n} {name}".encode()
    context = []
    if comment:
        context.append(
            {
                "basis": "ATTACHED_NOTE",
                "attributed": True,
                "block_ordinals": [n + 1],
                "text": [comment],
            }
        )
    if adjacent:
        context.append(
            {
                "basis": "ADJACENT_SINGLE_FILE",
                "attributed": False,
                "block_ordinals": [n - 1],
                "text": [adjacent],
            }
        )
    return {
        "n": n,
        "name": name,
        "created": created,
        "floating": floating,
        "content": content,
        "modified": modified if modified is not None else created,
        "context": context,
    }


def build_capture(root: Path, pages: list[dict]) -> Path:
    """Write a sealed capture in the package format the sealer produces."""
    root.mkdir(parents=True)
    (root / "blobs").mkdir()
    manifest_pages, occurrences, blobs = [], [], {}
    for p in pages:
        tag = p["tag"]
        (root / "pages" / tag).mkdir(parents=True)
        for name, body in (
            ("pre.xml", b"<pre/>"),
            ("post.xml", f"<post>{tag}</post>".encode()),
            ("receipt.json", b"{}"),
        ):
            (root / "pages" / tag / name).write_bytes(body)
        blocks = [
            {
                "ordinal": 0,
                "kind": "TEXT",
                "text": p.get("text", "Lehe tekst"),
                "object_id": "t0",
                "created": PAGE_CREATED,
                "modified": PAGE_CREATED,
                "outline": 0,
                "depth": 0,
                "parent": None,
            }
        ]
        for f in p["files"]:
            block_id = f"{{{p['guid']}}}{{{f['n']}}}{{B0}}"
            sha = hashlib.sha256(f["content"]).hexdigest()
            blobs[sha] = f["content"]
            blocks.append(
                {
                    "ordinal": f["n"],
                    "kind": "FILE",
                    "text": "",
                    "object_id": block_id,
                    "created": f["created"] or "",
                    "modified": f["modified"] or "",
                    "outline": -1 if f["floating"] else 0,
                    "depth": 1,
                    "parent": None,
                }
            )
            occurrences.append(
                {
                    "occurrence_key": f"{p['guid']}|{block_id}",
                    "page_tag": tag,
                    "page_guid": p["guid"],
                    "ordinal": f["n"],
                    "block_object_id": block_id,
                    "block_ordinal": f["n"],
                    "floating": f["floating"],
                    "filename": f["name"],
                    "extension": Path(f["name"]).suffix,
                    "size": len(f["content"]),
                    "sha256": sha,
                    "copied_at": "2026-11-04T13:10:00.000Z",
                    "cache_kind": "CLIENT_CACHE",
                    "block_created": None if f["floating"] else f["created"],
                    "block_modified": None if f["floating"] else f["modified"],
                    "object_modified": f["modified"] if f["floating"] else None,
                    "context": f["context"],
                }
            )
        manifest_pages.append(
            {
                "tag": tag,
                "batch": "b01",
                "page_object_id": f"{{OBJ-{tag}}}{{1}}{{B0}}",
                "page_guid": p["guid"],
                "title": p.get("title", f"Leht {tag}"),
                "section": p.get("section", "Sektsioon"),
                "archived": False,
                "level": 1,
                "notebook": "Näidismärkmik",
                "section_groups": [],
                "parent_object_id": None,
                "in_recycle_bin": False,
                "onenote_hyperlink": f"onenote:https://example.invalid/x.one#p&page-id={{{p['guid']}}}",
                "page_created": PAGE_CREATED,
                "page_modified": "2024-01-01T00:00:00.000Z",
                "navigate_start": "2026-11-04T13:00:00Z",
                "capture_end": "2026-11-04T13:01:00Z",
                "matters": [
                    {
                        "matter_id": str(m.pk),
                        "lovable_source_id": str(m.lovable_sync_state.source_id),
                        "reference": m.lovable_sync_state.reference,
                        "is_open": True,
                        "raw_locator_sha256": "0" * 64,
                        "notes": [],
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
        "bytes_occurrences": sum(o["size"] for o in occurrences),
        "bytes_blobs": sum(len(c) for c in blobs.values()),
        "dated": sum(1 for o in occurrences if o["block_created"]),
        "floating": sum(1 for o in occurrences if o["floating"]),
        "structural_comment_files": sum(
            1 for o in occurrences if any(c["attributed"] for c in o["context"])
        ),
        "topics": sum(len(p["matters"]) for p in manifest_pages),
        "shared_pages": sum(1 for p in manifest_pages if len(p["matters"]) > 1),
    }
    manifest = {
        "schema": oc.SCHEMA,
        "capture_id": "synthetic-capture",
        "package": "synthetic-capture-v2",
        "reader": "ONENOTE_DESKTOP",
        "counts": counts,
        "pages": manifest_pages,
        "occurrences": occurrences,
    }
    (root / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False), encoding="utf-8")
    reseal(root)
    return root


def reseal(root: Path) -> None:
    lines = []
    for path in sorted(root.rglob("*")):
        if path.is_file() and path.name not in {"SHA256SUMS", "SEAL.json"}:
            digest = hashlib.sha256(path.read_bytes()).hexdigest()
            lines.append(f"{digest}  {path.relative_to(root).as_posix()}")
    (root / "SHA256SUMS").write_text("\n".join(lines) + "\n", encoding="utf-8")
    (root / "SEAL.json").write_text(
        json.dumps(
            {
                "sha256sums_sha256": hashlib.sha256((root / "SHA256SUMS").read_bytes()).hexdigest(),
                "manifest_sha256": hashlib.sha256(
                    (root / "manifest.json").read_bytes()
                ).hexdigest(),
            }
        ),
        encoding="utf-8",
    )


def register_teema(reference: str, *, is_open: bool = True) -> Matter:
    matter = factories.MatterFactory(is_open=is_open)
    LovableSyncState.objects.create(
        matter=matter, source_id=uuid.uuid4(), reference=reference, cohort=oc.REGISTER_COHORT
    )
    return matter


@pytest.fixture
def enabled(settings):
    settings.ONENOTE_CAPTURE_IMPORT_ENABLED = True
    settings.REAL_DATA_ALLOWED = True
    return settings


@pytest.fixture
def world(tmp_path):
    a, b, c, d = (register_teema(f"2026_{n}") for n in (1, 2, 3, 4))
    pages = [
        {
            "tag": "001",
            "guid": _guid(1),
            "matters": [a],
            "title": "Teema A leht",
            "files": [
                _file(
                    1,
                    "kiri.pdf",
                    T1,
                    comment="Ministeeriumi kiri",
                    adjacent="Kõrvallause, mitte märkus",
                ),
                _file(2, "vastus.docx", T2, modified="2023-03-01T00:00:00.000Z"),
                _file(3, "lisa.pdf", T3, data=b"sama sisu"),
                _file(
                    4, "lisa-koopia.pdf", T3, data=b"sama sisu"
                ),  # repeated bytes: two occurrences
                _file(5, "lõuendil.pdf", None, floating=True, modified="2025-10-15T12:51:18.000Z"),
                _file(6, "tühi.txt", T1, data=b""),
            ],
        },
        {"tag": "002", "guid": _guid(2), "matters": [b], "files": [_file(1, "b.pdf", T1)]},
        {"tag": "003", "guid": _guid(3), "matters": [c, d], "files": [_file(1, "jagatud.pdf", T2)]},
    ]
    return {"root": build_capture(tmp_path / "cap", pages), "a": a, "b": b, "c": c, "d": d}


def _apply(plan, pages, **overrides):
    kwargs = {
        "operator_intent": oc.OPERATOR_INTENT,
        "expect_manifest_sha256": plan.capture.manifest_sha256,
        "expect_plan_sha256": plan.digest,
        "backup_set": "sets/20261104T230000Z",
        "label": "test",
    }
    kwargs.update(overrides)
    return oc.apply(plan, pages, **kwargs)


def _all(plan, include_shared=False):
    return oc.select_pages(plan, include_shared=include_shared)


# -- the seal ---------------------------------------------------------------------------


def test_a_sealed_capture_loads_and_reconciles(world):
    capture = oc.load_capture(world["root"], verify_all_bytes=True)
    assert len(capture.pages) == 3 and len(capture.occurrences) == 8
    assert capture.counts["floating"] == 1 and capture.counts["shared_pages"] == 1


def test_a_changed_byte_an_extra_file_or_a_wrong_count_is_refused(world):
    root = world["root"]
    blob = next((root / "blobs").iterdir())
    original = blob.read_bytes()
    blob.write_bytes(original + b"x")
    with pytest.raises(oc.CaptureError, match="does not match SHA256SUMS"):
        oc.load_capture(root, verify_all_bytes=True)
    blob.write_bytes(original)
    (root / "stray.txt").write_text("x")
    with pytest.raises(oc.CaptureError, match="differs from SHA256SUMS"):
        oc.load_capture(root)
    (root / "stray.txt").unlink()
    manifest = json.loads((root / "manifest.json").read_text(encoding="utf-8"))
    manifest["counts"]["dated"] += 1
    (root / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
    with pytest.raises(oc.CaptureError, match="does not match the seal"):
        oc.load_capture(root)
    reseal(root)  # resealed by someone: the seal holds, the counts still do not
    with pytest.raises(oc.CaptureError, match="do not reconcile"):
        oc.load_capture(root)


def test_a_path_outside_the_capture_is_refused(world):
    root = world["root"]
    sums = (root / "SHA256SUMS").read_text(encoding="utf-8") + f"{'0' * 64}  ../outside.txt\n"
    (root / "SHA256SUMS").write_text(sums, encoding="utf-8")
    seal = json.loads((root / "SEAL.json").read_text())
    seal["sha256sums_sha256"] = hashlib.sha256((root / "SHA256SUMS").read_bytes()).hexdigest()
    (root / "SEAL.json").write_text(json.dumps(seal))
    with pytest.raises(oc.CaptureError, match="unsafe path"):
        oc.load_capture(root)


# -- the plan ---------------------------------------------------------------------------


def test_the_plan_promotes_single_pages_and_holds_shared_and_empty_files(world):
    plan = oc.build_plan(oc.load_capture(world["root"]))
    s = plan.summary()
    assert plan.problems == []
    assert s["promote"] == 6  # page 001: 5 non-empty files; page 002: 1
    assert s["hold_by_reason"] == {
        oc.HOLD_EMPTY_FILE: 1,
        oc.HOLD_SHARED_PAGE: 2,
    }  # shared: 1 file x 2 Teemad
    assert s["items"] == s["promote"] + s["hold"] == 9
    assert s["promote_undated"] == 1 and s["promote_with_structural_comment"] == 1


def test_a_topic_mapped_to_another_teema_or_outside_the_register_is_a_problem(world):
    state = world["b"].lovable_sync_state
    state.cohort = "lovable-pilot"
    state.save()
    LovableSyncState.objects.filter(matter=world["a"]).update(matter=factories.MatterFactory())
    plan = oc.build_plan(oc.load_capture(world["root"]))
    assert any("not in the lovable-register cohort" in p for p in plan.problems)
    assert any("maps to another Teema" in p for p in plan.problems)


def test_the_plan_digest_does_not_move_as_an_apply_progresses(world, enabled):
    capture = oc.load_capture(world["root"])
    plan = oc.build_plan(capture)
    before = plan.digest
    _apply(plan, _all(plan)[:1])
    after = oc.build_plan(capture)
    assert after.digest == before
    assert (
        after.progress["recorded_IMPORTED"] + after.progress["recorded_SKIPPED"] == 6
    )  # page 001 done


# -- the gates --------------------------------------------------------------------------


@pytest.mark.parametrize(
    "override, match",
    [
        ({"operator_intent": "nope"}, "operator-intent"),
        ({"expect_manifest_sha256": "0" * 64}, "expect-manifest-sha256"),
        ({"expect_plan_sha256": ""}, "expect-plan-sha256"),
        ({"expect_plan_sha256": "f" * 64}, "changed since it was approved"),
        ({"backup_set": " "}, "backup set"),
    ],
)
def test_apply_refuses_without_each_authorisation(world, enabled, override, match):
    plan = oc.build_plan(oc.load_capture(world["root"]))
    with pytest.raises(oc.CaptureError, match=match):
        _apply(plan, _all(plan), **override)
    assert not Document.objects.exists() and not ImportBatch.objects.exists()


def test_apply_is_off_unless_the_one_off_container_enables_it(world, settings):
    settings.ONENOTE_CAPTURE_IMPORT_ENABLED = False
    settings.REAL_DATA_ALLOWED = True
    plan = oc.build_plan(oc.load_capture(world["root"]))
    with pytest.raises(oc.CaptureError, match="JURISTID_ONENOTE_CAPTURE_IMPORT"):
        _apply(plan, _all(plan))
    settings.ONENOTE_CAPTURE_IMPORT_ENABLED, settings.REAL_DATA_ALLOWED = True, False
    with pytest.raises(oc.CaptureError, match="REAL_DATA_ALLOWED"):
        _apply(plan, _all(plan))


def test_a_plan_with_problems_writes_nothing(world, enabled):
    LovableSyncState.objects.filter(matter=world["b"]).delete()
    plan = oc.build_plan(oc.load_capture(world["root"]))
    with pytest.raises(oc.CaptureError, match="problems"):
        _apply(plan, _all(plan), expect_plan_sha256=plan.digest)
    assert not LegacySourcePage.objects.exists()


# -- what an apply writes ---------------------------------------------------------------


def test_apply_writes_documents_on_the_right_teema_with_the_exact_bytes(world, enabled):
    a = world["a"]
    business_before = oc.business_digest([str(a.pk)])
    matter_before = model_to_dict(Matter.objects.get(pk=a.pk))
    updated_before = Matter.objects.get(pk=a.pk).updated_at
    plan = oc.build_plan(oc.load_capture(world["root"]))
    report = _apply(plan, _all(plan))
    assert report.documents_created == 6 and report.held == {oc.HOLD_EMPTY_FILE: 1}
    docs = Document.objects.filter(matter=a)
    assert docs.count() == 5
    for version in DocumentVersion.objects.filter(document__matter=a):
        assert version.uploaded_by_id is None and version.document.created_by_id is None
        resource = version.legacy_imports.get().resource
        assert version.sha256 == resource.sha256 and version.size_bytes == resource.size_bytes
    # Two files with identical bytes stay two documents.
    assert (
        DocumentVersion.objects.filter(
            document__matter=a, sha256=hashlib.sha256(b"sama sisu").hexdigest()
        ).count()
        == 2
    )
    link = MatterSourcePage.objects.get(matter=a)
    assert link.match_method == SourceMatchMethod.LOVABLE_EXACT_PAGE_GUID
    assert link.source_page.page_key == f"p-{_guid(1).lower()}"
    # Nothing Lovable-derived moved.
    assert oc.business_digest([str(a.pk)]) == business_before
    assert model_to_dict(Matter.objects.get(pk=a.pk)) == matter_before
    assert Matter.objects.get(pk=a.pk).updated_at == updated_before
    batch = ImportBatch.objects.get()
    assert batch.reconciliation_status == ReconciliationStatus.COMPLETED_WITH_GAPS
    assert batch.source_snapshot_sha256 == plan.capture.manifest_sha256


def test_dates_are_the_block_times_and_a_floating_file_stays_undated(world, enabled):
    plan = oc.build_plan(oc.load_capture(world["root"]))
    _apply(plan, _all(plan))
    by_name = {
        r.original_filename: r
        for r in LegacySourceResource.objects.filter(source_page__page_key=f"p-{_guid(1).lower()}")
    }
    assert by_name["kiri.pdf"].source_added_at == datetime(2021, 8, 30, 8, 48, 10, tzinfo=UTC)
    assert by_name["kiri.pdf"].source_added_basis == oc.SOURCE_ADDED_BASIS
    floating = by_name["lõuendil.pdf"]
    assert floating.source_added_at is None and floating.source_added_basis == ""
    note = Document.objects.get(matter=world["a"], title="lõuendil.pdf").provenance_note
    assert "Lisamise kuupäev teadmata" in note
    assert (
        "OneNote'i objekti viimane muutmine: 15.10.2025" in note
    )  # D8: shown apart, never as «lisatud»
    assert "Lisatud OneNote'i" not in note
    drift = Document.objects.get(matter=world["a"], title="vastus.docx").provenance_note
    assert "Faili sisu võib olla hiljem uuendatud" in drift


def test_only_structural_text_becomes_the_files_comment(world, enabled):
    plan = oc.build_plan(oc.load_capture(world["root"]))
    _apply(plan, _all(plan))
    note = Document.objects.get(matter=world["a"], title="kiri.pdf").provenance_note
    assert "Märkus OneNote'is: Ministeeriumi kiri" in note
    assert "Kõrvallause" not in note  # D9: adjacent text stays page text
    page = LegacySourcePage.objects.get(page_key=f"p-{_guid(1).lower()}")
    assert page.derived_text == "Lehe tekst"


def test_the_chronology_files_each_document_on_its_tallinn_day(world, enabled, specialist):
    plan = oc.build_plan(oc.load_capture(world["root"]))
    _apply(plan, _all(plan))
    rows, _more = matter_timeline(matter=world["a"], user=specialist)
    imports = [r for r in rows if r.item_type == HISTORICAL_IMPORT_ITEM]
    days = sorted(r.milestone.display_date for r in imports)
    # 30.8.2021 (kiri), 1.2.2023 (vastus, 23:30 local), 2.2.2023 (two same-byte files), undated.
    assert days == sorted(["30.8.2021", "1.2.2023", "2.2.2023", "kuupäev teadmata"])
    assert sum(r.imported_documents for r in imports) == 5


def test_a_second_apply_writes_nothing_at_all(world, enabled):
    capture = oc.load_capture(world["root"])
    plan = oc.build_plan(capture)
    _apply(plan, _all(plan))
    _apply(plan, _all(plan, include_shared=True))
    counts = {
        m: m.objects.count()
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
    again = oc.build_plan(capture)
    report = _apply(again, _all(again))
    report2 = _apply(again, _all(again, include_shared=True))
    assert report.documents_created == report2.documents_created == 0
    assert report.batch_id == report2.batch_id == ""  # no batch row for a run that writes nothing
    assert {m: m.objects.count() for m in counts} == counts


def test_a_shared_page_is_catalogued_and_linked_but_its_files_are_held(world, enabled, specialist):
    plan = oc.build_plan(oc.load_capture(world["root"]))
    report = _apply(plan, _all(plan, include_shared=True))
    assert report.documents_created == 0 and report.held == {oc.HOLD_SHARED_PAGE: 2}
    for teema in (world["c"], world["d"]):
        assert MatterSourcePage.objects.filter(matter=teema).count() == 1
        assert not Document.objects.filter(matter=teema).exists()
        row = LegacySourceResourceImport.objects.get(matter_source_page__matter=teema)
        assert row.state == ResourceImportState.SKIPPED and row.document_id is None
        rows, _more = matter_timeline(matter=teema, user=specialist)
        assert not [r for r in rows if r.item_type == HISTORICAL_IMPORT_ITEM]
    assert (
        LegacySourceResource.objects.filter(source_page__page_key=f"p-{_guid(3).lower()}").count()
        == 1
    )


def test_an_earlier_captures_page_row_is_reused_unchanged(world, enabled):
    earlier = LegacySourcePage.objects.create(
        source_system=SourceSystem.ONENOTE_DESKTOP,
        source_page_id="{OLD}{1}{B0}",
        page_key=f"p-{_guid(2).lower()}",
        source_notebook="n",
        source_section="vana",
        title="vana pealkiri",
        capture_id="old",
        first_imported_at=datetime(2026, 10, 20, tzinfo=UTC),
        latest_imported_at=datetime(2026, 10, 20, tzinfo=UTC),
    )
    old = LegacySourceResource.objects.create(
        source_page=earlier,
        resource_key="r001",
        original_filename="b.pdf",
        sha256="a" * 64,
        size_bytes=1,
        archive_relative_path="x",
    )
    frozen = model_to_dict(earlier)
    frozen_updated = earlier.updated_at
    plan = oc.build_plan(oc.load_capture(world["root"]))
    assert (
        plan.known_pages == [f"p-{_guid(2).lower()}"]
        and plan.summary()["earlier_capture_resources_on_known_pages"] == 1
    )
    report = _apply(plan, _all(plan))
    earlier.refresh_from_db()
    assert model_to_dict(earlier) == frozen and earlier.updated_at == frozen_updated
    assert report.pages_reused == 1 and report.earlier_capture_rows == 1
    row = LegacySourceResourceImport.objects.get(resource=old)
    assert row.state == ResourceImportState.SKIPPED and row.error_code == oc.HOLD_EARLIER_CAPTURE
    assert Document.objects.filter(matter=world["b"]).count() == 1


def test_a_failure_rolls_its_page_back_whole_and_keeps_the_pages_before(world, enabled):
    root = world["root"]
    capture = oc.load_capture(root)
    plan = oc.build_plan(capture)
    page_b = next(p for p in capture.pages if p.tag == "002")
    capture.blob_path(page_b.occurrences[0].sha256).write_bytes(b"changed after sealing")
    with pytest.raises(oc.CaptureError, match="rolled back whole"):
        _apply(plan, _all(plan))
    assert Document.objects.filter(matter=world["a"]).count() == 5  # page 001 kept
    assert not MatterSourcePage.objects.filter(
        matter=world["b"]
    ).exists()  # page 002: nothing at all
    assert ImportBatch.objects.get().reconciliation_status == ReconciliationStatus.FAILED


def test_verify_reconciles_every_row_and_finds_a_missing_one(world, enabled):
    capture = oc.load_capture(world["root"])
    plan = oc.build_plan(capture)
    _apply(plan, _all(plan))
    _apply(plan, _all(plan, include_shared=True))
    result = oc.verify(capture, check_bytes=True)
    assert result["problems"] == []
    assert result["counts"]["recorded_items"] == result["counts"]["expected_items"] == 9
    assert result["counts"]["documents"] == 6 and result["counts"]["bytes_rehashed"] == 6
    LegacySourceResourceImport.objects.filter(
        state=ResourceImportState.SKIPPED, error_code=oc.HOLD_EMPTY_FILE
    ).delete()
    assert any("has no row" in p for p in oc.verify(capture)["problems"])


def test_the_command_prints_counts_only(world, enabled):
    out = io.StringIO()
    call_command("onenote_capture_import", "plan", "--capture", str(world["root"]), stdout=out)
    shown = json.loads(out.getvalue())
    assert shown["promote"] == 6 and "items" not in shown
    with pytest.raises(CommandError, match="--matter"):
        call_command(
            "onenote_capture_import",
            "apply",
            "--capture",
            str(world["root"]),
            "--label",
            "x",
            stdout=io.StringIO(),
        )
