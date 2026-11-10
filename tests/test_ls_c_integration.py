"""Andmete sünkroonimine against the integrated A, B and D modules (LS-C, review F).

The page's numbers come from the real `lovable_inventory` ledger and the real
`onenote_coverage.coverage_summary()`, and a decision on a continuation goes
through the real engine. Every reference, page and capture is invented.
"""

from __future__ import annotations

import datetime as dt

import psycopg
import pytest
from django.db import IntegrityError, connection
from django.urls import reverse

from app.audit.enums import SecurityEventType
from app.audit.models import SecurityAuditEvent
from app.legacy_import import lovable_inventory, lovable_sync, onenote_coverage, sync_admin
from app.legacy_import.lovable_inventory import Category, Ledger, LedgerRow
from app.legacy_import.lovable_sync_models import SyncMode
from app.legacy_import.onenote_coverage_models import PageState
from app.legacy_import.sync_requests_models import (
    DataSyncRequest,
    SyncRequestKind,
    SyncRequestLane,
    SyncRequestStatus,
)
from tests import factories
from tests.admin_session import apply_admin_gate, choose, technical_admin, verified
from tests.test_ls_d_onenote_coverage import G_A, G_B, G_S, enabled, linked  # noqa: F401
from tests.test_ls_d_onenote_coverage import world as d_world  # noqa: F401
from tests.test_onenote_capture_changes import FIRST, build, run

pytestmark = pytest.mark.django_db


@pytest.fixture
def onenote_world(d_world):  # noqa: F811
    """D's world, with each stored link in the engine's shape (``{"source": link}``):
    D's fixture stores the bare string, which the engine's `held_by_import`, read
    by the page through `lovable_observability.status`, does not take."""
    from app.legacy_import.lovable_sync_models import LovableSyncState

    for state in LovableSyncState.objects.all():
        link = (state.applied or {}).get("onenote_link")
        if isinstance(link, str):
            state.applied = {**state.applied, "onenote_link": {"source": link}}
            state.save(update_fields=["applied"])
    return d_world


def _audits(action: str) -> list[SecurityAuditEvent]:
    return [
        e
        for e in SecurityAuditEvent.objects.filter(event_type=SecurityEventType.IMPORT_RUN)
        if (e.detail or {}).get("action") == action
    ]


def _row(n: int, category: str, *, operational: bool) -> LedgerRow:
    return LedgerRow(
        source_id=f"00000000-0000-0000-0000-{n:012d}",
        reference=f"2020_{n}",
        year=2020,
        operational=operational,
        activity="ONGOING" if operational else "NOT_OPERATIONAL",
        category=category,
        reason="TEST",
    )


# -- 1. the inventory ------------------------------------------------------------------


def test_the_coverage_panel_shows_the_real_ledgers_categories(db):
    rows = [
        *(_row(n, Category.EXACT, operational=True) for n in range(1, 5)),
        _row(5, Category.ABSENT, operational=True),
        *(_row(n, Category.ABSENT, operational=False) for n in range(6, 9)),
        _row(9, Category.POSSIBLE_DUPLICATE, operational=False),
        _row(10, Category.ARCHIVE_UNLINKED, operational=False),
    ]
    run_ = lovable_inventory.store_run(
        Ledger(taken_at="2026-11-09T10:00:00+00:00", source_sha256="0" * 64, rows=rows)
    )
    cov = sync_admin.inventory_coverage()
    assert cov.available
    assert (cov.total, cov.operational, cov.historical) == (10, 5, 5)
    assert cov.taken_at == run_.taken_at
    by_label = {r.label: r for r in cov.rows}
    assert [r.label for r in cov.rows] == [str(label) for _v, label in Category.choices]
    for value, label in Category.choices:
        row = by_label[str(label)]
        assert row.total == run_.counts["by_category"][value]
        assert row.operational == run_.counts["by_category_operational"][value]
        assert row.historical == run_.counts["by_category_historical"][value]
    assert by_label[Category.ABSENT.label].operational == 1
    assert by_label[Category.ABSENT.label].historical == 3


def test_no_inventory_says_so(db):
    assert sync_admin.inventory_coverage().note == sync_admin.INVENTORY_MISSING


# -- 2. OneNote ------------------------------------------------------------------------


def test_the_onenote_panel_equals_the_real_coverage_summary(onenote_world):
    summary = onenote_coverage.coverage_summary()
    op = summary["operational"]
    panel = sync_admin.onenote_panel()
    assert panel.available
    counts = dict(panel.counts)
    for key, label in sync_admin.ONENOTE_COUNTS:
        assert counts[label] == op[key], key
    assert counts["Operatiivseid lehti"] == 3 and counts["Imporditud manuseid"] == 3
    states = dict(panel.per_state)
    for value, label in PageState.choices:
        assert states[str(label)] == op["states"][value]
    assert sum(states.values()) == op["pages"]
    held = dict(panel.held)
    assert held[sync_admin.ONENOTE_HOLDS["SHARED_PAGE_AWAITING_REVIEW"]] == 2
    assert panel.last_capture["name"] == summary["last_capture"]["capture_id"] == "cap-1"
    assert panel.other_pages == summary["other_pages"]["pages"]
    assert panel.sections_with_pages == summary["sections"]["with_operational_pages"]
    assert panel.sections_not_covered == summary["sections"]["not_covered"]


def test_the_page_renders_the_real_onenote_numbers(onenote_world, client, settings):
    apply_admin_gate(settings)
    settings.REAL_DATA_ALLOWED = False
    admin, secret = technical_admin()
    verified(client, admin, secret)
    body = client.get(reverse("haldus:data_sync")).content.decode()
    assert "cap-1" in body
    assert "Operatiivseid lehti" in body and "Ühisleht, ootab seose otsust" in body
    assert sync_admin.ONENOTE_MISSING not in body


# -- 3. a continuation is decidable ----------------------------------------------------


@pytest.fixture
def admin_client(client, settings):
    apply_admin_gate(settings)
    settings.REAL_DATA_ALLOWED = False
    admin, secret = technical_admin()
    verified(client, admin, secret)
    return client, admin


def test_a_continuation_difference_can_be_decided(onenote_world, admin_client):
    client, admin = admin_client
    teema = onenote_world["a"]
    state = teema.lovable_sync_state
    run_ = lovable_sync.LovableSyncRun.objects.create(
        run_key="t-continuation",
        trigger="MANUAL",
        mode=SyncMode.COMPARE,
        status="SUCCEEDED",
        started_at=dt.datetime(2026, 11, 9, tzinfo=dt.UTC),
        mapping_version=lovable_sync.MAPPING_VERSION,
    )
    state.observed = {**(state.observed or {}), lovable_sync.CONTINUATION: ["x-1"]}
    state.observed_run = run_
    state.save(update_fields=["observed", "observed_run"])
    assert lovable_sync.CONTINUATION in lovable_sync.RESOLVABLE_FIELDS
    url = reverse("haldus:data_sync_resolve")
    query = {"viide": state.reference, "vali": lovable_sync.CONTINUATION}
    # The technical administrator alone reads no Teema; a business persona does.
    assert sync_admin.resolvable(admin, state.reference, lovable_sync.CONTINUATION) is None
    reader = factories.UserFactory()
    choose(client, reader)
    assert sync_admin.resolvable(reader, state.reference, lovable_sync.CONTINUATION) == state
    persona = client.get(url, query)
    assert persona.status_code == 200
    response = client.post(url, {**query, "note": "jätk vaadatud"})
    assert response.status_code == 302
    assert _audits("LOVABLE_FIELD_RESOLVED")[0].detail["field"] == lovable_sync.CONTINUATION


# -- 4. «Kontrolli muudatusi» is audited -----------------------------------------------


def test_the_check_names_its_actor(admin_client):
    client, admin = admin_client
    lovable_sync.set_mode(SyncMode.COMPARE, reason="t")
    assert client.post(reverse("haldus:data_sync_check")).status_code == 302
    rows = _audits("LOVABLE_CHECK_REQUESTED")
    assert len(rows) == 1 and rows[0].actor == admin


# -- 5. a live run is never declared lost ----------------------------------------------


def _hold_the_run_lock() -> psycopg.Connection:
    settings = connection.settings_dict
    other = psycopg.connect(
        host=settings["HOST"] or None,
        port=settings["PORT"] or None,
        user=settings["USER"],
        password=settings["PASSWORD"],
        dbname=settings["NAME"],
        autocommit=True,
    )
    held = other.execute(
        "SELECT pg_try_advisory_lock(%s, %s)", [lovable_sync.LOCK_NAMESPACE, lovable_sync.LOCK_KEY]
    ).fetchone()
    assert held is not None and held[0]
    return other


def test_a_running_request_is_not_lost_while_the_engine_holds_its_lock(db):
    user, _ = technical_admin()
    lovable_sync.set_mode(SyncMode.COMPARE, reason="t")
    start = dt.datetime(2026, 11, 9, 1, 0, tzinfo=dt.UTC)
    sync_admin.request_lovable_sync(user, "x", now=start)
    item = sync_admin.claim_next(SyncRequestLane.LOVABLE, now=start)
    later = start + sync_admin.RUNNING_LOST_AFTER + dt.timedelta(minutes=1)
    other = _hold_the_run_lock()
    try:
        assert sync_admin.recover_lost(SyncRequestLane.LOVABLE, now=later) == 0
        item.refresh_from_db()
        assert item.status == SyncRequestStatus.RUNNING
    finally:
        other.close()
    assert sync_admin.recover_lost(SyncRequestLane.LOVABLE, now=later) == 1


def test_a_late_finish_after_recovery_is_recorded_not_an_error(db):
    user, _ = technical_admin()
    start = dt.datetime(2026, 11, 9, 1, 0, tzinfo=dt.UTC)
    sync_admin.request_onenote_verify(user, "kogum-1", "x", now=start)
    item = sync_admin.claim_next(SyncRequestLane.ONENOTE, now=start)
    later = start + sync_admin.RUNNING_LOST_AFTER + dt.timedelta(minutes=1)
    assert sync_admin.recover_lost(SyncRequestLane.ONENOTE, now=later) == 1
    done = sync_admin._finish(item, SyncRequestStatus.DONE, "", {}, now=later)
    assert done.status == SyncRequestStatus.FAILED and done.outcome_code == "RUNNER_LOST"
    late = _audits("DATA_SYNC_FINISHED_LATE")
    assert len(late) == 1 and late[0].detail["status"] == SyncRequestStatus.DONE


# -- 6. only the lane index is «busy» --------------------------------------------------


def test_another_integrity_error_is_not_reported_as_a_busy_lane(db):
    user, _ = technical_admin()
    with pytest.raises(IntegrityError):
        sync_admin._record(
            user,
            kind=SyncRequestKind.ONENOTE_IMPORT,
            reason="x",
            now=dt.datetime(2026, 11, 9, tzinfo=dt.UTC),
            client=None,
            capture_name="kogum-1",
            approved_plan_sha256="",  # breaks data_sync_import_names_its_plan
        )
    assert not _audits("DATA_SYNC_REQUEST_REFUSED")
    assert DataSyncRequest.objects.count() == 0


def test_the_lane_index_is_still_reported_as_busy(db):
    user, _ = technical_admin()
    sync_admin.request_onenote_verify(user, "kogum-1", "x")
    with pytest.raises(sync_admin.SyncRequestRefused) as refusal:
        sync_admin.request_onenote_verify(user, "kogum-2", "y")
    assert refusal.value.code == "LANE_BUSY"


# Keep the D helpers referenced: the fixture module builds captures with them.
_ = (FIRST, build, run, G_A, G_B, G_S)
