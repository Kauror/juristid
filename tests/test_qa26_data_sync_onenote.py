"""The on-demand data-sync runner and held OneNote readings (quality audit 2026-11-18, package F).

QA26-BUG-01  A RUNNING request whose runner died is marked RUNNER_LOST by the host
             script's own `poll`, so the lane takes new requests (ADR 0213).
QA26-BUG-02  «Kontrolli muudatusi» whose run FAILED says so and is audited as failed.
QA26-BUG-03  A held OneNote reading a person accepted or rejected is not held again
             when a later capture reads the same bytes.
"""

from __future__ import annotations

import datetime as dt
import io

import pytest
from django.core.management import call_command
from django.utils import timezone

from app.audit.enums import SecurityEventType
from app.audit.models import SecurityAuditEvent
from app.legacy_import import lovable_sync, sync_admin
from app.legacy_import import onenote_coverage as cov
from app.legacy_import.lovable_sync_models import LovableSyncRun, SyncMode, SyncRunStatus
from app.legacy_import.onenote_coverage_models import (
    HoldDecisionKind,
    HoldScope,
    OneNoteResourceRevision,
    RevisionState,
)
from app.legacy_import.sync_requests_models import (
    DataSyncRequest,
    SyncRequestKind,
    SyncRequestLane,
    SyncRequestStatus,
)
from tests.admin_session import apply_admin_gate, technical_admin, verified
from tests.test_ls2_c_onenote_holds import (  # noqa: F401
    NEW_BYTES,
    _decide,
    _item,
    _later,
    _link_of,
    people,
    world,
)

pytestmark = pytest.mark.django_db


@pytest.fixture
def gate(settings):
    apply_admin_gate(settings)
    settings.REAL_DATA_ALLOWED = False
    return settings


@pytest.fixture
def admin(gate):
    user, secret = technical_admin()
    return {"user": user, "secret": secret}


def _poll(lane: str, *problem: str) -> str:
    out = io.StringIO()
    args = ["data_sync_requests", "poll", "--kind", lane]
    if problem:
        args += ["--problem", problem[0]]
    call_command(*args, stdout=out)
    return out.getvalue().strip().splitlines()[-1]


# -- QA26-BUG-01 ------------------------------------------------------------------------------


@pytest.mark.parametrize("problem", ["", "HOST_PAUSED"])
def test_a_lost_lovable_runner_is_recovered_by_the_host_scripts_poll(admin, problem):
    lovable_sync.set_mode(SyncMode.COMPARE, reason="t")
    seven_ago = timezone.now() - dt.timedelta(hours=7)
    item = sync_admin.request_lovable_sync(admin["user"], "x", now=seven_ago)
    claimed = sync_admin.claim_next(SyncRequestLane.LOVABLE, now=seven_ago)
    assert claimed is not None and claimed.pk == item.pk  # runner died after this

    # Exactly what juristid-lovable-sync.sh does every firing: poll; on NOT_DUE exit.
    answer = _poll("lovable", *([problem] if problem else []))
    item.refresh_from_db()
    # ADR 0213: "a RUNNING one fails after 6 h with no runner".
    assert item.status == SyncRequestStatus.FAILED and item.outcome_code == "RUNNER_LOST"
    assert answer == "NOT_DUE NO_REQUEST"


def test_the_lovable_lane_takes_a_new_request_once_the_lost_one_is_failed(admin):
    lovable_sync.set_mode(SyncMode.COMPARE, reason="t")
    seven_ago = timezone.now() - dt.timedelta(hours=7)
    sync_admin.request_lovable_sync(admin["user"], "x", now=seven_ago)
    sync_admin.claim_next(SyncRequestLane.LOVABLE, now=seven_ago)
    for _ in range(3):  # three host firings
        assert _poll("lovable") == "NOT_DUE NO_REQUEST"
    # A new «Sünkrooni nüüd» must be possible once the lost one is past 6 h.
    try:
        sync_admin.request_lovable_sync(admin["user"], "uus")
    except sync_admin.SyncRequestRefused as error:
        pytest.fail(f"lane still refuses new requests: {error.code}")


def test_a_lost_onenote_runner_is_recovered_too(admin):
    seven_ago = timezone.now() - dt.timedelta(hours=7)
    item = DataSyncRequest.objects.create(
        kind=SyncRequestKind.ONENOTE_VERIFY,
        requested_by=admin["user"],
        reason="a",
        requested_at=seven_ago,
        capture_name="onenote-naidis-20261109",
    )
    sync_admin.claim_next(SyncRequestLane.ONENOTE, now=seven_ago)
    assert _poll("onenote") == "NOT_DUE NO_REQUEST"
    item.refresh_from_db()
    assert item.status == SyncRequestStatus.FAILED, item.status


def test_run_next_still_recovers_a_lost_runner(admin):
    """The recovery `run_next` always made, unchanged."""
    lovable_sync.set_mode(SyncMode.COMPARE, reason="t")
    seven_ago = timezone.now() - dt.timedelta(hours=7)
    item = sync_admin.request_lovable_sync(admin["user"], "x", now=seven_ago)
    sync_admin.claim_next(SyncRequestLane.LOVABLE, now=seven_ago)
    sync_admin.run_next(SyncRequestLane.LOVABLE)
    item.refresh_from_db()
    assert item.status == SyncRequestStatus.FAILED and item.outcome_code == "RUNNER_LOST"


# -- QA26-BUG-02 ------------------------------------------------------------------------------


def test_a_failed_check_is_not_reported_as_done(client, admin):
    verified(client, admin["user"], admin["secret"])
    # No stored snapshot: the CHECK run is created and then FAILS (NO_SNAPSHOT).
    response = client.post(sync_admin_check_url(), follow=True)
    run = LovableSyncRun.objects.get()
    assert run.status == SyncRunStatus.FAILED
    page = response.content.decode()
    audit = [
        e
        for e in SecurityAuditEvent.objects.filter(event_type=SecurityEventType.IMPORT_RUN)
        if (e.detail or {}).get("action") == "LOVABLE_CHECK_REQUESTED"
    ]
    assert "Kontroll tehtud" not in page
    assert [a.succeeded for a in audit] == [False]


def sync_admin_check_url() -> str:
    from django.urls import reverse

    return reverse("haldus:data_sync_check")


# -- QA26-BUG-03 ------------------------------------------------------------------------------


def _held_revisions():
    return OneNoteResourceRevision.objects.filter(state=RevisionState.HELD)


def _open_revision_items(viewer):
    items, _hidden, _counts = cov.held_items(viewer=viewer)
    return [i for i in items if i.scope == HoldScope.HELD_REVISION]


@pytest.mark.parametrize("decision_a", [HoldDecisionKind.ACCEPT_REVISION, HoldDecisionKind.REJECT])
def test_a_decided_reading_is_not_held_again(world, people, decision_a):  # noqa: F811
    shared = _item("shared:", people["reader"])
    _decide(shared, people, HoldDecisionKind.ASSOCIATE, link_id=_link_of(shared, world["a"]))
    _later(world, "cap-2", NEW_BYTES)
    readings = {i.teemad[0].matter_id: i for i in _open_revision_items(people["reader"])}
    on_a = cov.held_item(readings[str(world["a"].pk)].key, viewer=people["reader"])
    on_b = cov.held_item(readings[str(world["b"].pk)].key, viewer=people["reader"])
    _decide(on_a, people, decision_a)
    _decide(on_b, people, HoldDecisionKind.REJECT)
    assert _open_revision_items(people["reader"]) == []
    held_before = _held_revisions().count()

    # The next capture: the very same bytes again.
    _later(world, "cap-3", NEW_BYTES)
    open_after = _open_revision_items(people["reader"])
    assert open_after == [], "same bytes, already decided, held again"
    assert _held_revisions().count() == held_before
