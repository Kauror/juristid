"""HR-06: an OneNote import request with no proven backup set is REFUSED by code.

The request runner records the backup check's own code (`onenote_capture.
CaptureBackupRefused`) as a refusal before any write, the way STORE-GUARD's
refusals are recorded: never a bare FAILED/IMPORT_FAILED with no reason.

This module deliberately never imports the runner modules by name (see
`lovable_backup_proof_accepted` in tests/conftest.py): here the set's proof is
really checked. Synthetic captures and sets only.
"""

from __future__ import annotations

import pytest

from app.legacy_import.lovable_sync import HOST_BACKUP_DIR_ENV
from app.legacy_import.sync_requests_models import SyncRequestStatus
from tests.test_onenote_import_storage_guard import (  # noqa: F401  (fixtures)
    _import_request,
    admin,
    captures,
    enabled,
    nothing_written,
)

pytestmark = pytest.mark.django_db


def test_an_import_request_with_no_mounted_proof_is_refused_by_its_code(
    captures,  # noqa: F811
    enabled,  # noqa: F811
    admin,  # noqa: F811
    settings,
    monkeypatch,
):
    """Regression: HR-06 follow-up — the refusal used to end FAILED/IMPORT_FAILED
    with ``error_class: CaptureError`` and no code."""
    monkeypatch.delenv(HOST_BACKUP_DIR_ENV)  # the runner's set name, but no mount
    item = _import_request(admin)
    assert item.status == SyncRequestStatus.REFUSED
    assert item.outcome_code == "PROOF_NOT_MOUNTED"
    assert item.result["error_class"] == "CaptureBackupRefused"
    assert item.result["error_code"] == "PROOF_NOT_MOUNTED"
    message = item.result["error_message"]
    assert message.startswith("Refused: PROOF_NOT_MOUNTED. Name the verified backup set")
    # A fixed sentence: never the set's name or a path.
    assert "sets/2" not in message and "\\" not in message and "/backup-set" not in message
    nothing_written(settings)
