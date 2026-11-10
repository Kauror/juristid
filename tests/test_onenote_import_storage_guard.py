"""STORE-GUARD: an import whose physical files are missing is never reported as done.

Production, 10.11.2026: a OneNote capture import ran in a container that had no
evidence or legacy-source mount. The bytes went into the container's own
directories and vanished; the rows committed and the request read DONE,
because every check read rows only. These tests fix the application side
(`app/legacy_import/import_storage.py`):

* a store that is not a mounted, persistent one is refused before any row;
* a stored object that does not read back rolls its page back, objects too;
* a check of a capture reads back the objects already recorded and fails,
  with counts, when one is missing or different — on the request list too;
* the happy path is unchanged.

Everything is synthetic and isolated: invented captures, temporary stores, and a
synthetic mount table instead of the machine's.
"""

from __future__ import annotations

import io
import json
import os
from pathlib import Path

import pytest
from django.core.files.storage import FileSystemStorage
from django.core.management import call_command
from django.urls import reverse

from app.documents.models import Document, DocumentVersion
from app.legacy_import import import_storage, sync_admin
from app.legacy_import import onenote_capture as oc
from app.legacy_import.lovable_sync_models import LovableSyncState
from app.legacy_import.models import ImportBatch, ReconciliationStatus
from app.legacy_import.source_pages import (
    LegacySourcePage,
    LegacySourceResource,
    LegacySourceResourceImport,
    MatterSourcePage,
)
from app.legacy_import.sync_requests_models import (
    DataSyncRequest,
    SyncRequestLane,
    SyncRequestStatus,
)
from tests.admin_session import apply_admin_gate, technical_admin, verified
from tests.test_onenote_capture_import import (
    T1,
    T2,
    _all,
    _apply,
    _file,
    _guid,
    build_capture,
    register_teema,
)

pytestmark = pytest.mark.django_db

CAPTURE = "onenote-naidis-20261110"


# -- fixtures ---------------------------------------------------------------------------


@pytest.fixture
def enabled(settings, monkeypatch):
    settings.ONENOTE_CAPTURE_IMPORT_ENABLED = True
    settings.REAL_DATA_ALLOWED = True
    monkeypatch.setenv("JURISTID_ONENOTE_BACKUP_SET", "sets/20261110T060000Z")
    return settings


@pytest.fixture
def captures(tmp_path, settings):
    """Two single-Teema pages with two files each, under a capture root."""
    a, b = register_teema("2026_31"), register_teema("2026_32")
    pages = [
        {
            "tag": "001",
            "guid": _guid(31),
            "matters": [a],
            "files": [_file(1, "kiri.pdf", T1), _file(2, "vastus.pdf", T2)],
        },
        {
            "tag": "002",
            "guid": _guid(32),
            "matters": [b],
            "files": [_file(1, "otsus.pdf", T1), _file(2, "lisa.pdf", T2)],
        },
    ]
    root = tmp_path / "captures"
    build_capture(root / CAPTURE, pages)
    settings.ONENOTE_CAPTURE_ROOT = str(root)
    return {"root": root / CAPTURE, "a": a, "b": b}


def _posix(path) -> str:
    return Path(os.path.realpath(path)).as_posix()


def container_without_mounts(monkeypatch) -> None:
    """The incident's container: only its own overlay root, no store mounted."""
    monkeypatch.setattr(import_storage, "_read_mountinfo", lambda: [("/", "overlay")])


def container_with_mounts(monkeypatch, settings) -> None:
    """The deployment's container: each store a bind mount of the host's disk."""
    table = [
        ("/", "overlay"),
        (_posix(settings.EVIDENCE_ROOT), "xfs"),
        (_posix(settings.LEGACY_SOURCE_ROOT), "xfs"),
    ]
    monkeypatch.setattr(import_storage, "_read_mountinfo", lambda: table)


def files_in(root) -> list[Path]:
    return [p for p in Path(root).rglob("*") if p.is_file()]


def nothing_written(settings) -> None:
    assert not ImportBatch.objects.exists()
    assert not LegacySourcePage.objects.exists() and not MatterSourcePage.objects.exists()
    assert not LegacySourceResource.objects.exists()
    assert not LegacySourceResourceImport.objects.exists()
    assert not Document.objects.exists() and not DocumentVersion.objects.exists()
    assert files_in(settings.EVIDENCE_ROOT) == [] and files_in(settings.LEGACY_SOURCE_ROOT) == []


def drop_evidence_bytes(monkeypatch, settings, *, replace_with: bytes | None = None) -> None:
    """The store accepts the write and loses it (or keeps other bytes): silent loss."""
    real_save = FileSystemStorage._save
    evidence = _posix(settings.EVIDENCE_ROOT)

    def lossy(self, name, content):
        saved = real_save(self, name, content)
        if _posix(self.location) == evidence:
            if replace_with is None:
                os.remove(self.path(saved))
            else:
                Path(self.path(saved)).write_bytes(replace_with)
        return saved

    monkeypatch.setattr(FileSystemStorage, "_save", lossy)


# -- the mount table --------------------------------------------------------------------


MOUNTINFO = """\
22 1 0:21 / / rw,relatime - overlay overlay rw,lowerdir=/x
30 22 8:1 /mnt/user/appdata/juristid/evidence /app/evidence rw - xfs /dev/md1 rw
31 22 8:1 /mnt/user/appdata/juristid/legacy\\040source /app/legacy\\040source rw - xfs /dev/md1 rw
32 22 0:40 / /app/scratch rw - tmpfs tmpfs rw
"""


def test_the_mount_table_is_read_with_its_escapes():
    assert import_storage.parse_mountinfo(MOUNTINFO) == [
        ("/", "overlay"),
        ("/app/evidence", "xfs"),
        ("/app/legacy source", "xfs"),
        ("/app/scratch", "tmpfs"),
    ]


@pytest.mark.parametrize(
    ("path", "problem"),
    [
        ("/app/evidence", ""),
        ("/app/evidence/matter/doc", ""),
        ("/app/legacy source", ""),
        ("/app/evidence-copy", "container's own filesystem"),
        ("/app/legacy-source", "container's own filesystem"),
        ("/app/scratch/evidence", "tmpfs mount, which keeps nothing"),
    ],
)
def test_a_store_is_persistent_only_on_a_mount_of_its_own(path, problem, monkeypatch):
    monkeypatch.setattr(os.path, "realpath", lambda p: str(p))
    found = import_storage.persistence_problem(
        Path(path), mounts=import_storage.parse_mountinfo(MOUNTINFO)
    )
    assert (problem in found) if problem else found == ""


def test_without_a_mount_table_the_nearest_mount_must_not_be_the_root(tmp_path, monkeypatch):
    # A developer machine has no mountinfo: `os.path.ismount` is asked instead.
    store = tmp_path / "evidence"
    store.mkdir()
    anchor = Path(os.path.realpath(store)).anchor
    monkeypatch.setattr(os.path, "ismount", lambda p: str(p) == anchor)
    assert "root filesystem" in import_storage.persistence_problem(store, mounts=None)
    mounted = os.path.realpath(store)
    monkeypatch.setattr(os.path, "ismount", lambda p: str(p) in (anchor, mounted))
    assert import_storage.persistence_problem(store, mounts=None) == ""


# -- 1. before any write ----------------------------------------------------------------


def test_an_import_into_a_store_that_is_not_mounted_is_refused_before_any_row(
    captures, enabled, settings, monkeypatch
):
    settings.REQUIRE_PERSISTENT_STORES = True
    container_without_mounts(monkeypatch)
    plan = oc.build_plan(oc.load_capture(captures["root"]))
    with pytest.raises(oc.CaptureStorageError, match="EVIDENCE_ROOT store") as caught:
        _apply(plan, _all(plan))
    assert caught.value.code == import_storage.STORAGE_NOT_PERSISTENT
    assert caught.value.before_writing is True
    nothing_written(settings)


def test_the_legacy_source_store_is_checked_too(captures, enabled, settings, monkeypatch):
    settings.REQUIRE_PERSISTENT_STORES = True
    table = [("/", "overlay"), (_posix(settings.EVIDENCE_ROOT), "xfs")]
    monkeypatch.setattr(import_storage, "_read_mountinfo", lambda: table)
    plan = oc.build_plan(oc.load_capture(captures["root"]))
    with pytest.raises(oc.CaptureStorageError, match="LEGACY_SOURCE_ROOT store") as caught:
        _apply(plan, _all(plan))
    assert caught.value.code == import_storage.STORAGE_NOT_PERSISTENT
    nothing_written(settings)


def test_a_missing_store_is_refused_even_outside_real_data_mode(captures, enabled, settings):
    assert settings.REQUIRE_PERSISTENT_STORES is False
    Path(settings.LEGACY_SOURCE_ROOT).rmdir()
    plan = oc.build_plan(oc.load_capture(captures["root"]))
    with pytest.raises(oc.CaptureStorageError, match="is missing") as caught:
        _apply(plan, _all(plan))
    assert caught.value.code == import_storage.STORAGE_MISSING
    assert not ImportBatch.objects.exists() and not Document.objects.exists()


def test_real_data_mode_requires_persistent_stores_and_cannot_be_switched_off():
    from config import settings as production_settings

    source = Path(production_settings.__file__).read_text(encoding="utf-8")
    assert "REQUIRE_PERSISTENT_STORES = REAL_DATA_ALLOWED" in source


def test_the_import_runs_when_the_stores_are_the_deployments_mounts(
    captures, enabled, settings, monkeypatch
):
    settings.REQUIRE_PERSISTENT_STORES = True
    container_with_mounts(monkeypatch, settings)
    plan = oc.build_plan(oc.load_capture(captures["root"]))
    report = _apply(plan, _all(plan))
    assert report.documents_created == 4 and report.stored_checked == 6  # 4 files + 2 XML
    assert len(files_in(settings.EVIDENCE_ROOT)) == 4
    assert len(files_in(settings.LEGACY_SOURCE_ROOT)) == 2


# -- 2. after the writes, before the commit ---------------------------------------------


def test_bytes_the_store_silently_lost_roll_the_page_back_with_its_objects(
    captures, enabled, settings, monkeypatch
):
    drop_evidence_bytes(monkeypatch, settings)
    plan = oc.build_plan(oc.load_capture(captures["root"]))
    with pytest.raises(oc.CaptureStorageError, match="rolled back whole") as caught:
        _apply(plan, _all(plan))
    error = caught.value
    assert error.code == import_storage.STORED_FILES_MISSING and error.before_writing is False
    assert error.counts == {"stored_checked": 3, "stored_missing": 2, "stored_mismatch": 0}
    # No row claims a file, and the page's XML went with its rows.
    assert not Document.objects.exists() and not DocumentVersion.objects.exists()
    assert not LegacySourcePage.objects.exists() and not LegacySourceResourceImport.objects.exists()
    assert files_in(settings.LEGACY_SOURCE_ROOT) == []
    batch = ImportBatch.objects.get()
    assert batch.reconciliation_status == ReconciliationStatus.FAILED
    notes = json.loads(batch.notes)
    assert notes["storage"]["stored_missing"] == 2
    assert notes["report"]["documents_created"] == 0


def test_bytes_that_read_back_different_roll_the_page_back(
    captures, enabled, settings, monkeypatch
):
    drop_evidence_bytes(monkeypatch, settings, replace_with=b"muu sisu")
    plan = oc.build_plan(oc.load_capture(captures["root"]))
    with pytest.raises(oc.CaptureStorageError) as caught:
        _apply(plan, _all(plan))
    assert caught.value.code == import_storage.STORED_FILES_MISSING
    assert caught.value.counts["stored_mismatch"] == 2
    assert not DocumentVersion.objects.exists()
    assert files_in(settings.EVIDENCE_ROOT) == []  # the wrong bytes were removed too


# -- 3. the requests: refused, failed, verified -----------------------------------------


@pytest.fixture
def admin(settings):
    apply_admin_gate(settings)
    user, secret = technical_admin()
    return {"user": user, "secret": secret}


def _verify_request(admin) -> DataSyncRequest:
    sync_admin.request_onenote_verify(admin["user"], CAPTURE, "kontroll")
    return sync_admin.run_next(SyncRequestLane.ONENOTE).request


def _import_request(admin) -> DataSyncRequest:
    check = _verify_request(admin)
    assert check.status == SyncRequestStatus.DONE, check.outcome_code
    sync_admin.request_onenote_import(admin["user"], CAPTURE, check.result["plan_sha256"], "x")
    return sync_admin.run_next(SyncRequestLane.ONENOTE).request


def _request_list(client, admin, settings) -> str:
    settings.REAL_DATA_ALLOWED = False  # the page as an administrator sees it
    # The capture helper stores a bare page link where the register panel reads
    # field records; the request list does not need it.
    LovableSyncState.objects.update(applied={})
    verified(client, admin["user"], admin["secret"])
    return client.get(reverse("haldus:data_sync")).content.decode()


def test_an_import_request_into_unmounted_stores_is_refused_by_name(
    captures, enabled, admin, settings, monkeypatch, client
):
    settings.REQUIRE_PERSISTENT_STORES = True
    container_without_mounts(monkeypatch)
    item = _import_request(admin)
    assert item.status == SyncRequestStatus.REFUSED
    assert item.outcome_code == "STORAGE_NOT_PERSISTENT"
    assert item.result["error_class"] == "CaptureStorageError"
    nothing_written(settings)
    body = _request_list(client, admin, settings)
    assert sync_admin.FAILURE_REASONS["STORAGE_NOT_PERSISTENT"] in body.replace("&#x27;", "'")


def test_an_import_request_whose_bytes_vanished_fails_with_counts(
    captures, enabled, admin, settings, monkeypatch, client
):
    drop_evidence_bytes(monkeypatch, settings)
    item = _import_request(admin)
    assert item.status == SyncRequestStatus.FAILED
    assert item.outcome_code == "STORED_FILES_MISSING"
    assert item.result["storage"]["stored_missing"] == 2
    assert not DocumentVersion.objects.exists()
    out = io.StringIO()
    call_command("data_sync_requests", "list", "--kind", "onenote", stdout=out)
    newest = json.loads(out.getvalue().splitlines()[0])
    assert newest["code"] == "STORED_FILES_MISSING" and newest["storage"]["stored_missing"] == 2
    body = _request_list(client, admin, settings)
    assert "Talletatud failid: kontrolliti 3 · puudu 2 · räsi ei klapi 0" in body


def test_bytes_lost_after_the_commit_still_fail_the_import_request(
    captures, enabled, admin, settings, monkeypatch
):
    # The page-level read-back is bypassed: the request's own read-back must catch it.
    monkeypatch.setattr(oc, "verify_stored", lambda objects: import_storage.read_back(objects))
    drop_evidence_bytes(monkeypatch, settings)
    item = _import_request(admin)
    assert item.status == SyncRequestStatus.FAILED
    assert item.outcome_code == "STORED_FILES_MISSING"
    assert item.result["storage"]["stored_missing"] == 4


def test_a_check_of_recorded_but_missing_bytes_is_not_verified(
    captures, enabled, admin, settings, client
):
    plan = oc.build_plan(oc.load_capture(captures["root"]))
    _apply(plan, _all(plan))
    lost = DocumentVersion.objects.order_by("pk").first()
    Path(settings.EVIDENCE_ROOT, lost.storage_key).unlink()
    page = LegacySourcePage.objects.order_by("page_key").last()
    Path(settings.LEGACY_SOURCE_ROOT, page.source_xml_storage_key).write_bytes(b"<muu/>")

    check = oc.verify_capture(captures["root"])
    assert check.ok is False
    assert check.storage == {"stored_checked": 6, "stored_missing": 1, "stored_mismatch": 1}
    assert check.page_states[page.page_key] == "FAILED"

    item = _verify_request(admin)
    assert item.status == SyncRequestStatus.FAILED
    assert item.outcome_code == "STORED_FILES_MISSING"
    assert item.result["verified"] is False
    assert item.result["storage"] == check.storage
    assert sync_admin.verified_plan(CAPTURE) is None  # no import may be asked on it
    body = _request_list(client, admin, settings)
    assert "Talletatud failid: kontrolliti 6 · puudu 1 · räsi ei klapi 1" in body


def test_the_command_verify_counts_missing_bytes_instead_of_crashing(captures, enabled, settings):
    plan = oc.build_plan(oc.load_capture(captures["root"]))
    _apply(plan, _all(plan))
    for version in DocumentVersion.objects.all():
        Path(settings.EVIDENCE_ROOT, version.storage_key).unlink()
    result = oc.verify(oc.load_capture(captures["root"]), check_bytes=True)
    assert result["counts"]["stored_missing"] == 4
    assert sum("stored bytes missing" in p for p in result["problems"]) == 4


# -- the happy path ----------------------------------------------------------------------


def test_the_happy_path_is_unchanged_and_says_what_it_read_back(captures, enabled, admin):
    item = _import_request(admin)
    assert item.status == SyncRequestStatus.DONE and item.outcome_code == ""
    assert item.result["counts"]["documents_created"] == 4
    assert item.result["counts"]["stored_checked"] == 6
    assert Document.objects.count() == 4
    check = _verify_request(admin)
    assert check.status == SyncRequestStatus.DONE and check.result["verified"] is True
    assert check.result["storage"] == {
        "stored_checked": 6,
        "stored_missing": 0,
        "stored_mismatch": 0,
    }
