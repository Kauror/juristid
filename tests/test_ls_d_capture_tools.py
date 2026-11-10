# ruff: noqa: E501 - the synthetic OneNote XML is kept one element per line
"""The desktop capture tooling's Python parts (tools/onenote_capture; live-sync round 2026-11).

A synthetic run directory stands in for what ``capture_pages.ps1`` writes on the
workstation: hierarchy reads before and after, each page's pre- and
post-navigation XML, its receipt and the copied files. The sealer must turn it
into a package the importer accepts, take dates from the pre-navigation read,
leave a floating file undated, and refuse an incomplete page; the verifier must
stop on a changed source or a tampered seal. Invented pages and files only.
"""

from __future__ import annotations

import hashlib
import json
import uuid
from pathlib import Path

import pytest

from app.legacy_import import onenote_capture as oc
from tools.onenote_capture.seal import SealError, seal
from tools.onenote_capture.verify import check_raw, check_sealed

NS = "http://schemas.microsoft.com/office/onenote/2013/onenote"
PAGE_ID = "{0000DD01-0000-4000-8000-00000000D001}{1}{B0}"
GUID = "0000DD11-0000-4000-8000-0000000000D1"
OTHER_ID = "{0000DD02-0000-4000-8000-00000000D002}{1}{B0}"
OTHER_GUID = "0000DD12-0000-4000-8000-0000000000D2"
CREATED = "2021-08-30T08:48:10.000Z"
TEXT_CREATED = "2021-08-30T08:00:00.000Z"
TOUCH = "2026-11-09T10:00:01.000Z"

KIRI = b"%PDF synthetic letter"
LOUEND = b"%PDF synthetic canvas"


def page_xml(page_id: str, *, text_modified: str, text: str = "30.08.2021 Kiri saadeti") -> str:
    return f"""<?xml version="1.0"?>
<one:Page xmlns:one="{NS}" ID="{page_id}" name="Näidisleht" lastModifiedTime="{TOUCH}">
 <one:Outline><one:OEChildren>
  <one:OE objectID="{{T1}}" creationTime="{TEXT_CREATED}" lastModifiedTime="{text_modified}"><one:T><![CDATA[{text}]]></one:T></one:OE>
  <one:OE objectID="{{F1}}" creationTime="{CREATED}" lastModifiedTime="{CREATED}">
   <one:InsertedFile pathCache="C:\\\\x\\\\a.pdf" preferredName="kiri.pdf"/>
   <one:OEChildren><one:OE objectID="{{N1}}" creationTime="{CREATED}" lastModifiedTime="{CREATED}"><one:T><![CDATA[Ministeeriumi kiri]]></one:T></one:OE></one:OEChildren>
  </one:OE>
 </one:OEChildren></one:Outline>
 <one:InsertedFile objectID="{{FL}}" pathCache="C:\\\\x\\\\b.pdf" preferredName="lõuend.pdf" lastModifiedTime="2025-10-15T12:51:18.000Z"/>
</one:Page>
"""


def hierarchy_xml(*, page_modified: str = "2026-01-01T00:00:00.000Z") -> str:
    return f"""<?xml version="1.0"?>
<one:Notebooks xmlns:one="{NS}">
 <one:Notebook name="Näidismärkmik" ID="{{NB}}">
  <one:SectionGroup name="Rühm" ID="{{G1}}">
   <one:Section name="Sektsioon" ID="{{S1}}" lastModifiedTime="2026-11-01T00:00:00.000Z">
    <one:Page ID="{PAGE_ID}" name="Näidisleht" dateTime="2021-08-01T10:00:00.000Z" lastModifiedTime="{page_modified}" pageLevel="1"/>
    <one:Page ID="{OTHER_ID}" name="Teine" dateTime="2022-01-01T10:00:00.000Z" lastModifiedTime="2026-01-01T00:00:00.000Z" pageLevel="2"/>
   </one:Section>
  </one:SectionGroup>
 </one:Notebook>
</one:Notebooks>
"""


def receipt(page_id: str, *, unresolved: int = 0, status: str = "CAPTURED") -> dict:
    def r(ordinal, object_id, name, data, floating):
        return {
            "ordinal": ordinal,
            "object_id": object_id,
            "preferred_name": name,
            "floating": floating,
            "oe_created": "" if floating else CREATED,
            "oe_modified": "2025-10-15T12:51:18.000Z" if floating else CREATED,
            "status": status,
            "sha256": hashlib.sha256(data).hexdigest(),
            "bytes": len(data),
            "copy_matches_source": True,
            "source_stable": True,
            "copied_at": "2026-11-09T10:00:05.000Z",
        }

    return {
        "page_id": page_id,
        "navigated": True,
        "unresolved_after": unresolved,
        "navigate_start": "2026-11-09T10:00:00.000Z",
        "capture_end": "2026-11-09T10:00:10.000Z",
        "id_bound": True,
        "title_pre_equals_post": True,
        "receipts": [
            r(1, "{F1}", "kiri.pdf", KIRI, False),
            r(2, "{FL}", "lõuend.pdf", LOUEND, True),
        ],
    }


def build_run(
    root: Path, *, unresolved: int = 0, status: str = "CAPTURED", post_text: str | None = None
) -> Path:
    root.mkdir(parents=True)
    (root / "hierarchy-before.xml").write_text(hierarchy_xml(), encoding="utf-8")
    (root / "hierarchy-after.xml").write_text(hierarchy_xml(), encoding="utf-8")
    rows = ["tag\tpage_id\tpage_guid\tlink"]
    for tag, page_id, guid in (("001", PAGE_ID, GUID), ("002", OTHER_ID, OTHER_GUID)):
        rows.append(
            f"{tag}\t{page_id}\t{guid}\tonenote:https://example.invalid/x.one#p&page-id={{{guid}}}"
        )
        base = root / tag
        (base / "files").mkdir(parents=True)
        (base / "pre.xml").write_text(
            page_xml(page_id, text_modified=TEXT_CREATED), encoding="utf-8"
        )
        post = page_xml(page_id, text_modified=TOUCH, **({"text": post_text} if post_text else {}))
        (base / "post.xml").write_text(post, encoding="utf-8")
        (base / "receipt.json").write_text(
            json.dumps(receipt(page_id, unresolved=unresolved, status=status)), encoding="utf-8"
        )
        (base / "files" / "001.pdf").write_bytes(KIRI)
        (base / "files" / "002.pdf").write_bytes(LOUEND)
    (root / "pages.tsv").write_text("\n".join(rows) + "\n", encoding="utf-8")
    return root


def request_for(teema_ids: list[dict]) -> dict:
    return {
        "schema": "juristid.onenote-capture-request/1",
        "pages": [],
        "register_map": {GUID: teema_ids},
    }


@pytest.fixture
def run_dir(tmp_path):
    return build_run(tmp_path / "run")


@pytest.fixture
def request_file(tmp_path):
    path = tmp_path / "request.json"
    teema = {
        "matter_id": str(uuid.uuid4()),
        "lovable_source_id": str(uuid.uuid4()),
        "reference": "2026_81",
    }
    path.write_text(json.dumps(request_for([teema])), encoding="utf-8")
    return path


def test_the_sealed_package_is_what_the_importer_reads(run_dir, request_file, tmp_path):
    out = tmp_path / "sealed" / "cap-tools"
    result = seal(run_dir, request_file, out, "cap-tools")
    assert result["counts"]["pages"] == 2 and result["counts"]["occurrences"] == 4
    capture = oc.load_capture(out, verify_all_bytes=True)
    page = next(p for p in capture.pages if p.page_guid == GUID)
    assert [m.reference for m in page.matters] == ["2026_81"]
    assert page.section == "Sektsioon" and page.section_groups == ("Rühm",)
    other = next(p for p in capture.pages if p.page_guid == OTHER_GUID)
    assert other.matters == ()  # the register names no Teema for it: inventoried only
    assert other.parent_object_id == PAGE_ID and other.level == 2
    dated, floating = sorted(page.occurrences, key=lambda o: o.ordinal)
    assert dated.block_created.isoformat() == "2021-08-30T08:48:10+00:00"
    assert dated.structural_comment == [("ATTACHED_NOTE", "Ministeeriumi kiri")]
    assert floating.floating and floating.block_created is None  # D8: undated
    assert floating.object_modified is not None
    # The text block's date is its pre-navigation reading, never the navigation touch.
    text = next(b for b in page.blocks if b["kind"] == "TEXT" and b["text"].startswith("30.08"))
    assert text["modified"] == TEXT_CREATED and text["created"] == TEXT_CREATED
    assert check_sealed(out)["stop"] == []


def test_a_page_with_placeholders_left_is_not_sealed(tmp_path, request_file):
    run = build_run(tmp_path / "run", unresolved=2)
    out = tmp_path / "sealed" / "x"
    with pytest.raises(SealError, match="unresolved placeholders"):
        seal(run, request_file, out, "x")
    assert not out.exists()  # nothing written


def test_a_file_not_fetched_is_not_sealed(tmp_path, request_file):
    run = build_run(tmp_path / "run", status="NOT_FETCHED")
    with pytest.raises(SealError, match="is not a verified copy"):
        seal(run, request_file, tmp_path / "sealed" / "x", "x")


def test_a_seal_is_never_overwritten(run_dir, request_file, tmp_path):
    out = tmp_path / "sealed" / "x"
    seal(run_dir, request_file, out, "x")
    with pytest.raises(SealError, match="a seal is immutable"):
        seal(run_dir, request_file, out, "x")


def test_bytes_that_differ_from_the_receipt_are_refused(run_dir, request_file, tmp_path):
    (run_dir / "001" / "files" / "001.pdf").write_bytes(b"other bytes")
    with pytest.raises(SealError, match="bytes differ from the receipt"):
        seal(run_dir, request_file, tmp_path / "sealed" / "x", "x")


def test_the_raw_check_passes_a_navigation_touch_and_stops_on_a_text_change(tmp_path):
    clean = check_raw(build_run(tmp_path / "a"))
    assert clean["stop"] == [] and clean["notes"]["navigation_touch"] == 2
    changed = check_raw(build_run(tmp_path / "b", post_text="Teine tekst"))
    assert "001:TEXT_CHANGED" in changed["stop"]


def test_the_raw_check_stops_when_the_hierarchy_moved_on_a_run_page(tmp_path):
    run = build_run(tmp_path / "a")
    (run / "hierarchy-after.xml").write_text(
        hierarchy_xml(page_modified="2026-11-09T10:00:03.000Z"), encoding="utf-8"
    )
    assert "HIERARCHY_LM_CHANGED_ON_RUN_PAGE" in check_raw(run)["stop"]


def test_the_raw_check_stops_on_placeholders_left(tmp_path):
    assert "001:PLACEHOLDERS_LEFT" in check_raw(build_run(tmp_path / "a", unresolved=1))["stop"]


def test_the_sealed_check_stops_on_a_tampered_blob(run_dir, request_file, tmp_path):
    out = tmp_path / "sealed" / "x"
    seal(run_dir, request_file, out, "x")
    blob = next((out / "blobs").iterdir())
    blob.write_bytes(b"tampered")
    assert "FILE_DIFFERS_FROM_SHA256SUMS" in check_sealed(out)["stop"]
    with pytest.raises(oc.CaptureError):
        oc.load_capture(out, verify_all_bytes=True)


@pytest.mark.django_db
def test_a_sealed_run_imports_through_the_gated_path(tmp_path, settings):
    from app.documents.models import Document
    from tests.test_onenote_capture_import import register_teema, stored_link

    settings.ONENOTE_CAPTURE_IMPORT_ENABLED = True
    settings.REAL_DATA_ALLOWED = True
    teema = register_teema("2026_82")
    state = teema.lovable_sync_state
    state.applied = {"onenote_link": stored_link(GUID)}
    state.save(update_fields=["applied"])
    request = tmp_path / "request.json"
    teemad = [
        {
            "matter_id": str(teema.pk),
            "lovable_source_id": str(state.source_id),
            "reference": "2026_82",
        }
    ]
    request.write_text(json.dumps(request_for(teemad)), encoding="utf-8")
    out = tmp_path / "sealed" / "cap-e2e"
    seal(build_run(tmp_path / "run"), request, out, "cap-e2e")
    check = oc.verify_capture(out)
    assert check.ok, check.problems
    report = oc.apply_capture(
        out, approved_plan_sha256=check.plan_sha256, actor=None, backup_set="sets/t"
    )
    assert report.documents_created == 2
    assert Document.objects.filter(matter=teema).count() == 2
    # The page the register does not name was inventoried, not written.
    assert report.pages_created == 1
