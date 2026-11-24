"""HR-06: an OneNote import request with no proven backup set is REFUSED by code.

The request runner records the backup check's own code (`onenote_capture.
CaptureBackupRefused`) as a refusal before any write, the way STORE-GUARD's
refusals are recorded: never a bare FAILED/IMPORT_FAILED with no reason.

This module deliberately never imports the runner modules by name (see
`lovable_backup_proof_accepted` in tests/conftest.py): here the set's proof is
really checked. Synthetic captures and sets only.
"""

from __future__ import annotations

import html
import os
from pathlib import Path

import pytest

from app.legacy_import import onenote_capture
from app.legacy_import.lovable_sync import HOST_BACKUP_DIR_ENV
from app.legacy_import.sync_admin import failure_reason
from app.legacy_import.sync_requests_models import SyncRequestStatus
from tests.test_onenote_import_storage_guard import (  # noqa: F401  (fixtures)
    _import_request,
    _request_list,
    admin,
    captures,
    enabled,
    nothing_written,
)

pytestmark = pytest.mark.django_db


def _no_name(monkeypatch) -> None:
    monkeypatch.delenv("JURISTID_ONENOTE_BACKUP_SET")


def _no_mount(monkeypatch) -> None:
    monkeypatch.delenv(HOST_BACKUP_DIR_ENV)


def _dump_changed(monkeypatch) -> None:
    (Path(os.environ[HOST_BACKUP_DIR_ENV]) / "database.dump").write_bytes(b"PGDMP muudetud")


@pytest.mark.parametrize(
    "break_it, code",
    [(_no_name, "NO_SET"), (_no_mount, "PROOF_NOT_MOUNTED"), (_dump_changed, "PROOF_DUMP")],
    ids=["no set named", "no proof mounted", "dump changed"],
)
def test_an_import_request_with_no_proven_backup_is_refused_by_its_code(
    captures,  # noqa: F811
    enabled,  # noqa: F811
    admin,  # noqa: F811
    settings,
    monkeypatch,
    break_it,
    code,
):
    """Regression: HR-06 follow-up — the refusal used to end FAILED/IMPORT_FAILED
    with ``error_class: CaptureError`` and no code."""
    break_it(monkeypatch)
    probes = []
    real_probe = onenote_capture.require_stores
    monkeypatch.setattr(onenote_capture, "require_stores", lambda: probes.append(1) or real_probe())
    item = _import_request(admin)
    assert item.status == SyncRequestStatus.REFUSED
    assert item.outcome_code == code
    assert item.result["error_class"] == "CaptureBackupRefused"
    assert item.result["error_code"] == code
    message = item.result["error_message"]
    assert message.startswith(f"Refused: {code}. Name the verified backup set")
    # A fixed sentence: never the set's name or a path.
    assert "sets/2" not in message and "\\" not in message and "/backup-set" not in message
    nothing_written(settings)
    # Refused before STORE-GUARD's probe: no store write at all (review of #690).
    assert probes == []


#: Every code the backup check gives (`lovable_sync.backup_set_problem`).
BACKUP_CODES = (
    "NO_SET",
    "SET_NAME",
    "SET_STALE",
    "PROOF_NOT_MOUNTED",
    "PROOF_MISSING",
    "PROOF_OTHER_SET",
    "PROOF_LEVEL",
    "PROOF_TIME",
    "PROOF_DUMP",
)


@pytest.mark.parametrize("code", BACKUP_CODES)
def test_haldus_explains_each_backup_refusal_in_estonian(code):
    """E3: the request list said «Tõrge (kood PROOF_NOT_MOUNTED).»; each backup
    code now has its own sentence, which says nothing was written."""
    text = failure_reason(code)
    assert not text.startswith("Tõrge (kood"), code
    assert "midagi ei kirjutatud" in text, code


def test_the_request_list_shows_the_explanation_not_the_code(
    captures,  # noqa: F811
    enabled,  # noqa: F811
    admin,  # noqa: F811
    settings,
    monkeypatch,
    client,
):
    """E3: what an administrator reads on Haldus → Andmete sünkroonimine."""
    monkeypatch.delenv(HOST_BACKUP_DIR_ENV)
    item = _import_request(admin)
    assert item.outcome_code == "PROOF_NOT_MOUNTED"
    body = _request_list(client, admin, settings)
    assert html.escape(failure_reason("PROOF_NOT_MOUNTED"), quote=True) in body
    assert "Tõrge (kood PROOF_NOT_MOUNTED)" not in body
